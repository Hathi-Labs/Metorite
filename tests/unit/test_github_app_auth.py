"""WS-54 GH-1 — the GitHub App auth core and the registration script.

Spec: ``project-docs/specs/github_integration.md`` §6.1, §6.2, §13 and §15.
No database: every rule here is pure, or it talks to a fake GitHub on
``httpx.MockTransport``. The private key is one that this module makes.

What each test holds (the acceptance items of GH-1):

* **A1** the JWT decodes with the public key, ``iat`` is 60 seconds back,
  ``exp`` is at most 600 seconds ahead, and ``iss`` is the client id.
* **A2** a second call within the window makes no second token request. A call
  less than five minutes before ``expires_at`` makes one. Two concurrent
  misses make ONE request.
* **A3** a sentinel token appears in no log line, and in no answer of the door.
* **A4** a missing key file raises ``GitHubAppNotConfigured``, and the message
  holds no path and no byte of the file.
* **A5** the script builds the manifest of §13.2 and §14 for each ``--env``,
  refuses a wrong ``state``, and refuses to overwrite a file.
* **The door.** A call names a path on a fixed host. A body over the cap is
  refused. Redirects are off.

R7, the mutations. Each one turns at least one test here red (run 2026-10-18):

* M1 ``"iat": issued`` (no back-date) -> ``test_the_jwt_has_the_shape_of_the_docs``.
* M2 ``"iss": _setting("github_app_id")`` -> ``test_the_jwt_has_the_shape_of_the_docs``.
* M3 ``JWT_LIFETIME_S = 900`` -> ``test_the_jwt_has_the_shape_of_the_docs``.
* M4 ``_fresh`` returns False -> ``test_a_second_call_in_the_window_reuses_the_token``.
* M5 ``TOKEN_REUSE_MARGIN = timedelta(0)`` -> ``test_a_token_near_its_expiry_is_made_again``.
* M6 no second cache read under the lock ->
  ``test_two_concurrent_misses_make_one_request``.
* M7 ``_log.info(..., token=token)`` in ``installation_token`` ->
  ``test_a_sentinel_token_reaches_no_log_line``.
* M8 the key error names the path ->
  ``test_a_missing_key_raises_not_configured_with_no_path``.
* M9 ``"pull_requests": "write"`` in ``PERMISSIONS`` ->
  ``test_the_manifest_asks_for_read_only``.
* M10 ``check_state`` returns at once -> ``test_a_wrong_state_is_refused``.
* M11 ``refuse_existing`` returns at once ->
  ``test_the_script_never_overwrites_a_file``.
* M12 the size check in ``call`` removed -> ``test_a_body_over_the_cap_is_refused``.
* M13 ``follow_redirects=True`` in ``call`` -> ``test_a_redirect_is_not_followed``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from gateway.connections import github_app

REAL_CLIENT = httpx.AsyncClient

CLIENT_ID = "Iv23.gh1-test-client"
SENTINEL = "ghs_SENTINEL_INSTALLATION_TOKEN_must_never_leak"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def keypair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any]:
    """An RSA key that THIS module makes. No real key is ever read."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    path = tmp_path_factory.mktemp("gh1") / "app.pem"
    path.write_bytes(pem)
    return path, key.public_key()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, keypair: tuple[Path, Any]) -> Any:
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "github_app_client_id", CLIENT_ID, raising=False)
    monkeypatch.setattr(settings, "github_app_id", "424242", raising=False)
    monkeypatch.setattr(settings, "github_app_private_key_path", str(keypair[0]), raising=False)
    github_app.reset_for_tests()
    yield settings
    github_app.reset_for_tests()


class FakeGitHub:
    """A fake api.github.com. It counts the token requests it answers."""

    def __init__(self, *, ttl: timedelta = timedelta(hours=1), delay: float = 0.0) -> None:
        self.ttl = ttl
        self.delay = delay
        self.token_requests = 0
        self.requests: list[httpx.Request] = []
        self.token = SENTINEL

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/access_tokens") and request.method == "POST":
            self.token_requests += 1
            if self.delay:
                await asyncio.sleep(self.delay)
            expires = datetime.now(UTC) + self.ttl
            return httpx.Response(201, json={
                "token": self.token, "expires_at": expires.isoformat().replace("+00:00", "Z"),
            })
        if path == "/installation/repositories":
            return httpx.Response(200, json={"total_count": 1, "repositories": []})
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    gh = FakeGitHub()

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_CLIENT(*args, transport=httpx.MockTransport(gh.handle), **kwargs)

    monkeypatch.setattr(github_app.httpx, "AsyncClient", _client)
    return gh


# ── A1: the App JWT ─────────────────────────────────────────────────────────


def test_the_jwt_has_the_shape_of_the_docs(app: Any, keypair: tuple[Path, Any]) -> None:
    now = 1_760_000_000
    token = github_app.app_jwt(now=now)
    claims = jwt.decode(
        token, keypair[1], algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    assert claims["iat"] == now - 60
    assert 0 < claims["exp"] - now <= 600
    assert claims["exp"] - claims["iat"] <= 600
    assert claims["iss"] == CLIENT_ID


def test_the_jwt_verifies_now_with_the_public_key(app: Any, keypair: tuple[Path, Any]) -> None:
    claims = jwt.decode(github_app.app_jwt(), keypair[1], algorithms=["RS256"])
    assert claims["iss"] == CLIENT_ID


def test_the_key_is_read_once_for_each_process(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str] = []
    real = Path.read_bytes

    def counting(self: Path) -> bytes:
        reads.append(self.name)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", counting)
    github_app.app_jwt()
    github_app.app_jwt()
    assert reads.count("app.pem") == 1


# ── A2: the cache ───────────────────────────────────────────────────────────


async def test_a_second_call_in_the_window_reuses_the_token(app: Any, fake: FakeGitHub) -> None:
    assert await github_app.installation_token(77) == SENTINEL
    assert await github_app.installation_token(77) == SENTINEL
    assert fake.token_requests == 1


async def test_a_token_near_its_expiry_is_made_again(app: Any, fake: FakeGitHub) -> None:
    await github_app.installation_token(78)
    assert fake.token_requests == 1
    later = datetime.now(UTC) + timedelta(minutes=56)  # four minutes before the end
    await github_app.installation_token(78, now=later)
    assert fake.token_requests == 2


async def test_a_token_with_more_than_five_minutes_left_is_reused(
    app: Any, fake: FakeGitHub,
) -> None:
    await github_app.installation_token(79)
    soon = datetime.now(UTC) + timedelta(minutes=50)
    await github_app.installation_token(79, now=soon)
    assert fake.token_requests == 1


async def test_two_concurrent_misses_make_one_request(app: Any, fake: FakeGitHub) -> None:
    fake.delay = 0.05
    got = await asyncio.gather(*(github_app.installation_token(80) for _ in range(5)))
    assert got == [SENTINEL] * 5
    assert fake.token_requests == 1


async def test_drop_installation_forgets_the_token(app: Any, fake: FakeGitHub) -> None:
    await github_app.installation_token(81)
    github_app.drop_installation(81)
    await github_app.installation_token(81)
    assert fake.token_requests == 2


async def test_a_401_drops_the_token_and_tries_once_more(
    app: Any, fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = fake.handle

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/installation/repositories":
            calls.append(request.headers.get("authorization", ""))
            if len(calls) == 1:
                return httpx.Response(401, json={"message": "Bad credentials"})
        return await original(request)

    monkeypatch.setattr(fake, "handle", handle)
    answer = await github_app.installation_call(82, "GET", "/installation/repositories")
    assert answer.status == 200
    assert fake.token_requests == 2 and len(calls) == 2


async def test_a_refused_token_request_raises_installation_gone(
    app: Any, fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def gone(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    monkeypatch.setattr(fake, "handle", gone)
    with pytest.raises(github_app.GitHubCallFailed) as caught:
        await github_app.installation_token(83)
    assert caught.value.code == "installation_gone"


# ── A3: no token out ────────────────────────────────────────────────────────


async def test_a_sentinel_token_reaches_no_log_line(
    app: Any, fake: FakeGitHub, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as logs:
        await github_app.installation_token(90)
        answer = await github_app.installation_call(90, "GET", "/installation/repositories")
        await github_app.installation_call(90, "GET", "/nothing-here")
        github_app.drop_installation(90)
    seen = json.dumps(logs, default=str) + caplog.text
    assert SENTINEL not in seen
    assert SENTINEL not in repr(answer) and SENTINEL not in json.dumps(answer.body)
    assert SENTINEL not in repr(github_app._TOKENS)


async def test_the_token_request_carries_the_jwt_and_no_token(app: Any, fake: FakeGitHub) -> None:
    await github_app.installation_token(91)
    (request,) = [r for r in fake.requests if r.url.path.endswith("/access_tokens")]
    assert request.url.host == "api.github.com"
    assert request.url.path == "/app/installations/91/access_tokens"
    assert request.headers["authorization"].startswith("Bearer ey")
    assert request.content in (b"", b"null")  # no `repositories` narrowing (§6.2)


# ── A4: a missing key ───────────────────────────────────────────────────────


def test_a_missing_key_raises_not_configured_with_no_path(
    app: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    missing = tmp_path / "secret-dir-name" / "nowhere.pem"
    monkeypatch.setattr(app, "github_app_private_key_path", str(missing))
    github_app.reset_for_tests()
    with pytest.raises(github_app.GitHubAppNotConfigured) as caught:
        github_app.app_jwt()
    message = str(caught.value)
    assert "secret-dir-name" not in message and "nowhere" not in message
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_a_file_that_is_not_a_key_holds_no_byte_in_the_message(
    app: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.pem"
    bad.write_text("-----BEGIN RSA PRIVATE KEY-----\nNOTAKEYBYTES\n-----END RSA PRIVATE KEY-----\n")
    monkeypatch.setattr(app, "github_app_private_key_path", str(bad))
    github_app.reset_for_tests()
    with pytest.raises(github_app.GitHubAppNotConfigured) as caught:
        github_app.app_jwt()
    assert "NOTAKEYBYTES" not in str(caught.value) and "bad.pem" not in str(caught.value)


def test_no_client_id_raises_not_configured(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "github_app_client_id", "")
    with pytest.raises(github_app.GitHubAppNotConfigured):
        github_app.app_jwt()


def test_the_feature_ships_off() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["github_app_enabled"].default is False
    assert Settings.model_fields["app_public_url"].default == "https://app.metorite.com"


# ── The door ────────────────────────────────────────────────────────────────


async def test_a_call_cannot_name_another_host(app: Any, fake: FakeGitHub) -> None:
    for path in ("https://evil.example/x", "//evil.example/x", "x", "/a\\b", "/@evil.example"):
        with pytest.raises(github_app.GitHubCallFailed):
            await github_app.call("GET", path)
    with pytest.raises(github_app.GitHubCallFailed):
        await github_app.call("GET", "/user", base="https://evil.example")
    assert fake.requests == []


async def test_a_body_over_the_cap_is_refused(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    big = b"[" + b"1," * (github_app.MAX_RESPONSE_BYTES // 2 + 10) + b"1]"

    async def handle(request: httpx.Request) -> httpx.Response:
        async def stream():
            for i in range(0, len(big), 65536):
                yield big[i:i + 65536]
        return httpx.Response(200, content=stream())

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_CLIENT(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(github_app.httpx, "AsyncClient", _client)
    with pytest.raises(github_app.GitHubCallFailed) as caught:
        await github_app.call("GET", "/big")
    assert caught.value.code == "too_large"


async def test_a_redirect_is_not_followed(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://evil.example/steal"})
        return httpx.Response(200, json={"stolen": True})

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_CLIENT(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(github_app.httpx, "AsyncClient", _client)
    answer = await github_app.call("GET", "/repos/a/b")
    assert answer.status == 302 and seen == ["https://api.github.com/repos/a/b"]


async def test_an_unreachable_github_is_a_short_code(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_CLIENT(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(github_app.httpx, "AsyncClient", _client)
    with pytest.raises(github_app.GitHubCallFailed) as caught:
        await github_app.call("GET", "/x")
    assert caught.value.code == "unreachable"


def test_the_rate_limit_answer_is_read() -> None:
    assert github_app.Answer(429, None).rate_limited()
    assert github_app.Answer(403, None, {"x-ratelimit-remaining": "0"}).rate_limited()
    assert not github_app.Answer(403, None, {"x-ratelimit-remaining": "12"}).rate_limited()
    assert not github_app.Answer(404, None).rate_limited()


def test_the_install_url_carries_the_state(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "github_app_slug", "metorite-staging")
    url = github_app.install_url("abc.def")
    assert url == "https://github.com/apps/metorite-staging/installations/new?state=abc.def"
    monkeypatch.setattr(app, "github_app_slug", "bad/slug")
    with pytest.raises(github_app.GitHubAppNotConfigured):
        github_app.install_url("x")


# ── A5: the registration script ─────────────────────────────────────────────


def _script() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "github_app_manifest.py"
    import sys

    if "github_app_manifest" in sys.modules:
        return sys.modules["github_app_manifest"]
    spec = importlib.util.spec_from_file_location("github_app_manifest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["github_app_manifest"] = module  # a dataclass reads it
    spec.loader.exec_module(module)
    return module


CONVERSION = {
    "id": 123456, "slug": "metorite-staging", "client_id": "Iv23.client",
    "client_secret": "secret-value-x", "webhook_secret": "hook-secret-y",
    "pem": "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n",
}


@pytest.mark.parametrize("env_key", ["staging", "production"])
def test_the_manifest_matches_section_14(env_key: str) -> None:
    script = _script()
    env = script.ENVIRONMENTS[env_key]
    manifest = script.build_manifest(env, "http://127.0.0.1:5555/redirect")
    expected = {
        "staging": ("Metorite (staging)", "https://dev.metorite.com",
                    "https://api.dev.metorite.com/webhooks/github",
                    "https://dev.metorite.com/api/integrations/github/callback", False),
        "production": ("Metorite", "https://metorite.com",
                       "https://api.metorite.com/webhooks/github",
                       "https://app.metorite.com/api/integrations/github/callback", True),
    }[env_key]
    assert manifest["name"] == expected[0]
    assert manifest["url"] == expected[1]
    assert manifest["hook_attributes"] == {"url": expected[2], "active": True}
    assert manifest["callback_urls"] == [expected[3]]
    assert manifest["public"] is expected[4]
    assert manifest["redirect_url"] == "http://127.0.0.1:5555/redirect"
    assert manifest["request_oauth_on_install"] is True
    assert "setup_url" not in manifest  # §4.2 fact 6: OAuth on install has none
    assert set(manifest["default_events"]) == {"pull_request", "check_suite", "check_run", "status"}


def test_the_manifest_asks_for_read_only() -> None:
    script = _script()
    manifest = script.build_manifest(script.ENVIRONMENTS["production"], "http://127.0.0.1:1/r")
    assert manifest["default_permissions"] == {
        "metadata": "read", "pull_requests": "read", "checks": "read",
        "statuses": "read", "members": "read",
    }
    assert "write" not in json.dumps(manifest["default_permissions"])
    assert "contents" not in manifest["default_permissions"]


def test_the_form_posts_to_the_organization_with_the_state() -> None:
    script = _script()
    assert script.form_action("Hathi-Labs", "s t") == (
        "https://github.com/organizations/Hathi-Labs/settings/apps/new?state=s%20t"
    )
    assert script.form_action(None, "x") == "https://github.com/settings/apps/new?state=x"
    with pytest.raises(script.ManifestError):
        script.form_action("evil/../x", "s")


def test_a_wrong_state_is_refused() -> None:
    script = _script()
    script.check_state("right", "right")
    for bad in ("wrong", "", None, "righ"):
        with pytest.raises(script.ManifestError):
            script.check_state("right", bad)


def test_the_env_file_holds_the_eight_settings_with_the_flag_off(tmp_path: Path) -> None:
    script = _script()
    env_path, pem_path = script.write_outputs(tmp_path, script.ENVIRONMENTS["staging"], CONVERSION)
    assert (env_path.name, pem_path.name) == ("github-app-staging.env", "github-app-staging.pem")
    lines = dict(
        ln.split("=", 1) for ln in env_path.read_text(encoding="utf-8").splitlines()
        if ln and not ln.startswith("#")
    )
    assert lines == {
        "GITHUB_APP_ENABLED": "false", "GITHUB_APP_ID": "123456",
        "GITHUB_APP_SLUG": "metorite-staging", "GITHUB_APP_CLIENT_ID": "Iv23.client",
        "GITHUB_APP_CLIENT_SECRET": "secret-value-x", "GITHUB_APP_WEBHOOK_SECRET": "hook-secret-y",
        "GITHUB_APP_PRIVATE_KEY_PATH": "/opt/acb/github-app.pem",
        "APP_PUBLIC_URL": "https://dev.metorite.com",
    }
    assert pem_path.read_text(encoding="utf-8") == CONVERSION["pem"]


def test_the_script_never_overwrites_a_file(tmp_path: Path) -> None:
    script = _script()
    env = script.ENVIRONMENTS["production"]
    (tmp_path / "github-app.pem").write_text("OLD KEY\n", encoding="utf-8")
    with pytest.raises(script.ManifestError):
        script.write_outputs(tmp_path, env, CONVERSION)
    with pytest.raises(script.ManifestError):
        script.refuse_existing(tmp_path, env)
    assert (tmp_path / "github-app.pem").read_text(encoding="utf-8") == "OLD KEY\n"
    assert not (tmp_path / "github-app.env").exists()

    other = tmp_path / "second"
    other.mkdir()
    (other / "github-app.env").write_text("GITHUB_APP_ID=999\n", encoding="utf-8")
    with pytest.raises(script.ManifestError):
        script.write_outputs(other, env, CONVERSION)
    assert (other / "github-app.env").read_text(encoding="utf-8") == "GITHUB_APP_ID=999\n"


def test_an_empty_template_of_secrets_init_is_replaced(tmp_path: Path) -> None:
    script = _script()
    env = script.ENVIRONMENTS["production"]
    (tmp_path / "github-app.env").write_text(
        "# The push refuses a key that is not in this template.\nGITHUB_APP_ID=\nAPP_PUBLIC_URL=\n",
        encoding="utf-8",
    )
    env_path, _ = script.write_outputs(tmp_path, env, CONVERSION)
    assert "GITHUB_APP_ID=123456" in env_path.read_text(encoding="utf-8")


def test_a_conversion_with_a_missing_value_writes_nothing(tmp_path: Path) -> None:
    script = _script()
    with pytest.raises(script.ManifestError):
        script.write_outputs(tmp_path, script.ENVIRONMENTS["staging"],
                             {**CONVERSION, "webhook_secret": ""})
    assert list(tmp_path.iterdir()) == []


def test_the_script_writes_only_to_the_drop_folder() -> None:
    script = _script()
    assert Path.home() / ".metorite" / "secrets" == script.DROP_DIR
    source = (Path(__file__).resolve().parents[2] / "scripts" / "github_app_manifest.py").read_text(
        encoding="utf-8")
    assert "--drop" not in source  # no option moves the output


def test_the_secrets_manifest_carries_the_four_app_entries() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "deploy/secrets/manifest.json").read_text(encoding="utf-8"))
    by_name = {e["name"]: e for e in manifest["entries"]}
    script = _script()
    for env_key, stem in (("staging", "github-app-staging"), ("production", "github-app")):
        env_entry, key_entry = by_name[stem], by_name[f"{stem}-key"]
        assert env_entry["local_file"] == f"{stem}.env"
        assert env_entry["kind"] == "env-merge"
        assert env_entry["remote_path"] == "/opt/acb/app/.env"
        assert env_entry["allowed_keys"] == list(script.ENV_KEYS)
        assert env_entry["required_keys"] == list(script.ENV_KEYS)
        assert env_entry["restart"] == ["acb-gateway"]
        assert key_entry["local_file"] == f"{stem}.pem"
        assert (key_entry["kind"], key_entry["remote_path"]) == ("file", script.BOX_KEY_PATH)
        assert (key_entry["owner"], key_entry["group"], key_entry["mode"]) == ("root", "acb", "0640")
        # The key goes first, so the restart of the env push finds it.
        names = [e["name"] for e in manifest["entries"]]
        assert names.index(f"{stem}-key") < names.index(stem)
        assert script.ENVIRONMENTS[env_key].file_stem == stem
