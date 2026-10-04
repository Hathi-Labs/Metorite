"""Fences for the env-write hardening of the Integrations routes (security, 2026-10-05).

The defect. ``POST /integrations/configure`` took ANY key of the shape
``[A-Z][A-Z0-9_]+``. It set ``os.environ``, appended to the env file of the box
(``/opt/acb/app/.env``, the ``EnvironmentFile`` of ``acb-gateway.service``) and
cleared the settings cache. ``acb_auth`` promotes an org admin with
``admin:settings:manage`` to EXECUTIVE, so a customer admin could set
``GATEWAY_SESSION_SECRET`` or ``DATABASE_URL`` for every organization, and it
lasted past a restart. ``_upsert_env_var`` wrote ``f"{key}={value}"`` as it
came, so a ``\\n`` in a value added any line it liked, and systemd reads the
last line of a key. ``PUT /integrations/keys`` had no role check and wrote the
same way.

The fix, in three layers. The rule lives in ``acb_common/env_guard.py``.

* **A** — a control character or a line separator in a key or a value is
  400, and so is a value that ``bash`` reads as code under
  ``deploy/hostinger/deploy.sh``'s ``source``. ``_upsert_env_var`` refuses
  too, and the startup load in ``acb_llm/key_store.py`` skips such a value.
* **B** — a platform name (``env_guard.is_platform_env``) is 403 on every
  Integrations write.
* **C** — configure takes only a key that a setup guide, the LLM key list or a
  registered custom integration declares, else 422.

Every case points the env file at a temp file and fakes the credential store.
No test reads or writes a real ``.env`` and none needs a database.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole
from acb_common import env_guard
from acb_common.settings import Settings, get_settings
from fastapi import HTTPException
from gateway.routes import integrations
from gateway.routes import oauth as oauth_routes
from gateway.routes import settings as settings_routes

USER = UserContext(email="admin@example.test", role=UserRole.EXECUTIVE)

#: Values that add a line to the env file, now or at the next rewrite.
CONTROL_VALUES = [
    pytest.param("x\nMSFT_OAUTH_CLIENT_ID=evil", id="LF"),
    pytest.param("x\rDATABASE_URL=postgres://evil", id="CR"),
    pytest.param("x\x00y", id="NUL"),
    pytest.param("x\u2028DATABASE_URL=evil", id="U+2028"),
    pytest.param("x\x0bDATABASE_URL=evil", id="VT"),
    pytest.param("x\x0cDATABASE_URL=evil", id="FF"),
    pytest.param("x\x85DATABASE_URL=evil", id="NEL"),
    pytest.param("x\x1cDATABASE_URL=evil", id="FS"),
    pytest.param("x\tDATABASE_URL=evil", id="TAB"),
]

#: Values that ``source`` runs, or that two readers read differently.
SHELL_VALUES = [
    pytest.param("x $(id)", id="space-and-substitution"),
    pytest.param("$(touch /tmp/p)", id="substitution"),
    pytest.param("`id`", id="backtick"),
    pytest.param("x;touch /tmp/p", id="semicolon"),
    pytest.param("x&&id", id="ampersand"),
    pytest.param("x|id", id="pipe"),
    pytest.param("x>/tmp/p", id="redirect"),
    pytest.param("a b", id="space"),
    pytest.param("${HOME}", id="dollar"),
    pytest.param("it's", id="quote"),
    pytest.param('a"b', id="double-quote"),
    pytest.param("C:\\Users\\x", id="backslash"),
    pytest.param("~root", id="leading-tilde"),
    pytest.param("a:~/b", id="tilde-after-colon"),
    pytest.param("#x", id="leading-hash"),
    pytest.param("caf\u00e9", id="non-ascii"),
]

#: Real shapes of the credentials that the guides take. Each must pass.
REAL_VALUES = [
    "1000.0f3b9c.4d2e8a1b",                    # Zoho refresh token
    "https://www.zohoapis.in",                 # Zoho API domain
    "ghp_AbCdEf0123456789",                    # GitHub token
    "Iv1.8a61f9b3a7aba766",                    # GitHub OAuth client id
    "sk-ant-api03-x_Y-z",                      # Anthropic key
    "AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY",  # Google API key
    "abc8Q~xyz.-_",                            # Azure-style secret
    "dGVzdA==/+",                              # base64
    "p@ss!w*rd%^[]{}?,=+/:#",                  # a password with no shell character
    "2026-10-05T10:00:00.123456+00:00",        # an OAuth expiry
    "/var/lib/acb/sa.json",                    # a service-account path
    "587",                                     # SMTP port
]

#: The mail-app keys that a setup guide declares and layer B refuses.
#: WS-17 EM-G7 removes the `gmail-oauth` guide, so a test reads this as a
#: ceiling and never as an exact set.
MAIL_APP_GUIDE_KEYS = frozenset({
    "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
    "MSFT_OAUTH_CLIENT_ID", "MSFT_OAUTH_CLIENT_SECRET", "MICROSOFT_TENANT_ID",
})

#: The `Settings` fields that are integration credentials, not platform
#: settings. Every OTHER field must be on the deny list. A new field fails
#: `test_every_settings_field_is_sorted` until somebody puts it in one place.
INTEGRATION_SETTINGS_FIELDS = frozenset({
    "anthropic_api_key", "anymailfinder_api_key", "apify_api_token",
    "apollo_api_key", "deepseek_api_key", "gemini_api_key", "github_client_id",
    "github_token", "gmail_default_user", "gmail_sa_json_path",
    "gmail_workspace_domain", "google_access_token", "google_maps_api_key",
    "google_refresh_token", "google_sheets_sa_json_path", "google_token_expiry",
    "groq_api_key", "instantly_api_key", "mistral_api_key", "openai_api_key",
    "openrouter_api_key", "serpapi_api_key", "smtp_host", "smtp_password",
    "smtp_port", "smtp_use_tls", "smtp_username", "together_api_key",
    "zoho_access_token", "zoho_accounts_url", "zoho_api_domain",
    "zoho_client_id", "zoho_client_secret", "zoho_refresh_token", "zoho_region",
    "zoho_token_expiry",
})

ENV_BEFORE = b"EXISTING=1\nZOHO_REGION=in\n"


# ── Fakes and fixtures ─────────────────────────────────────────────────────


class FakeStore:
    """Records every credential-store write. It never opens a database."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, str]] = []
        self.deletes: list[str] = []

    async def put(self, provider: str, api_key: str, **_: Any) -> None:
        self.puts.append((provider, api_key))

    async def delete(self, provider: str, **_: Any) -> None:
        self.deletes.append(provider)

    async def get_by_type(self, *_: Any, **__: Any) -> dict[str, str]:
        return {}


class FakeCustomTable:
    """Stands in for `custom_api_definitions` behind `integrations._db_query`."""

    def __init__(self, keys: tuple[str, ...] = (), *, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        if keys:
            self.rows.append({
                "service_id": "notion", "label": "Notion",
                "env_vars": [{"key": k, "label": k, "sensitive": True} for k in keys],
            })
        self.fail = fail
        self.statements: list[str] = []

    async def __call__(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        self.statements.append(" ".join(sql.split()))
        if self.fail:
            raise RuntimeError("database down")
        verb = sql.strip().split()[0].upper()
        if verb == "SELECT":
            return list(self.rows)
        if verb == "INSERT":
            self.rows.append({
                "service_id": params["service_id"], "label": params["label"],
                "env_vars": json.loads(params["env_vars"]),
            })
        return []

    @property
    def inserts(self) -> list[str]:
        return [s for s in self.statements if s.upper().startswith("INSERT")]


@pytest.fixture(autouse=True)
def _restore_environ():
    """Every route here writes `os.environ` directly. Put it back."""
    saved = dict(os.environ)
    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(saved)
    get_settings.cache_clear()


@pytest.fixture()
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_bytes(ENV_BEFORE)
    monkeypatch.setattr(integrations, "_find_env_file", lambda: path)
    monkeypatch.setattr(oauth_routes, "_find_env_file", lambda: path)
    return path


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    import acb_llm.key_store as key_store_module

    fake = FakeStore()
    monkeypatch.setattr(key_store_module, "get_key_store", lambda: fake)
    return fake


@pytest.fixture()
def custom(monkeypatch: pytest.MonkeyPatch) -> FakeCustomTable:
    table = FakeCustomTable()
    monkeypatch.setattr(integrations, "_db_query", table)
    return table


@pytest.fixture()
def byok_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYOK_ENABLED", "true")
    get_settings.cache_clear()


def _snapshot(env_path: Path) -> tuple[bytes, dict[str, str]]:
    return env_path.read_bytes(), dict(os.environ)


def _assert_untouched(env_path: Path, before: tuple[bytes, dict[str, str]]) -> None:
    assert env_path.read_bytes() == before[0], "the env file changed"
    assert dict(os.environ) == before[1], "os.environ changed"


async def _configure(*pairs: tuple[str, str]) -> dict[str, Any]:
    req = integrations.ConfigureRequest(
        vars=[integrations.IntegrationVar(key=k, value=v) for k, v in pairs],
    )
    return await integrations.configure_integrations(req, user=USER)


async def _put(service: str, key_name: str, value: str) -> dict[str, Any]:
    req = integrations.IntegrationKeyRequest(service=service, key_name=key_name, value=value)
    return await integrations.put_integration_key(req, user=USER)


async def _expect(status_code: int, awaitable: Any) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await awaitable
    assert caught.value.status_code == status_code, caught.value.detail
    return caught.value


def _fake_httpx(monkeypatch: pytest.MonkeyPatch, module: Any, post_payload: dict[str, Any]) -> None:
    class _Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload
            self.status_code = 200
            self.headers: dict[str, str] = {}

        def json(self) -> dict[str, Any]:
            return self._payload

        @property
        def text(self) -> str:
            return json.dumps(self._payload)

    class _Client:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: Any) -> bool:
            return False

        async def post(self, *_: Any, **__: Any) -> _Response:
            return _Response(post_payload)

        async def get(self, *_: Any, **__: Any) -> _Response:
            return _Response({"login": "octocat"})

    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)


def _fake_gh_cli(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    class _Proc:
        def __init__(self, stdout: str) -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def _run(argv: list[str], **_: Any) -> _Proc:
        if argv[:3] == ["gh", "auth", "token"]:
            return _Proc(token)
        return _Proc("Logged in to github.com account octocat (keyring)\n"
                     "Token scopes: 'repo', 'copilot'\n")

    monkeypatch.setattr(integrations.subprocess, "run", _run)


# ── Layer A: control characters and line separators ────────────────────────


class TestLayerARefusesControlCharacters:
    @pytest.mark.parametrize("value", CONTROL_VALUES)
    async def test_configure_refuses_and_writes_nothing(self, value, env_file, store, custom):
        before = _snapshot(env_file)
        await _expect(400, _configure(("APOLLO_API_KEY", value)))
        _assert_untouched(env_file, before)
        assert store.puts == []

    async def test_configure_refuses_a_newline_in_a_key(self, env_file, store, custom):
        # `re.match` with `$` accepts "KEY\n", so the old shape check let a
        # key break the line too.
        before = _snapshot(env_file)
        await _expect(400, _configure(("APOLLO_API_KEY\nDATABASE_URL", "evil")))
        _assert_untouched(env_file, before)
        assert store.puts == []

    @pytest.mark.parametrize("value", CONTROL_VALUES)
    async def test_put_keys_refuses_and_writes_nothing(self, value, env_file, store):
        before = _snapshot(env_file)
        await _expect(400, _put("apollo", "api_key", value))
        _assert_untouched(env_file, before)
        assert store.puts == []

    @pytest.mark.parametrize("value", CONTROL_VALUES)
    async def test_github_device_poll_refuses_a_token_from_github(
        self, value, env_file, monkeypatch,
    ):
        monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.8a61f9b3a7aba766")
        get_settings.cache_clear()
        _fake_httpx(monkeypatch, integrations, {"access_token": value})
        before = _snapshot(env_file)
        await _expect(400, integrations.github_device_poll(
            integrations.DevicePollRequest(device_code="d"), user=USER,
        ))
        _assert_untouched(env_file, before)

    @pytest.mark.parametrize("value", CONTROL_VALUES)
    async def test_github_connect_cli_refuses_a_token_from_the_cli(
        self, value, env_file, monkeypatch,
    ):
        _fake_gh_cli(monkeypatch, f"gho_{value}x")
        before = _snapshot(env_file)
        await _expect(400, integrations.github_connect_cli(user=USER))
        _assert_untouched(env_file, before)

    async def test_custom_registration_refuses_a_key_with_a_newline(self, custom):
        req = integrations.CustomApiDef(
            service_id="notion", label="Notion",
            env_vars=[{"key": "NOTION_API_TOKEN\nDATABASE_URL", "label": "Token"}],
        )
        await _expect(400, integrations.create_custom_api(req, user=USER))
        assert custom.inserts == []

    async def test_oauth_persist_is_all_or_nothing(self, env_file):
        # The refresh token is bad. The access token comes first in the
        # write order, and it must not be written either.
        before = _snapshot(env_file)
        with pytest.raises(env_guard.EnvWriteRefused):
            oauth_routes._persist_tokens(
                oauth_routes._PROVIDERS["zoho-crm"],
                {"access_token": "1000.good", "refresh_token": "1000.x\nDATABASE_URL=evil",
                 "expires_in": 3600},
            )
        _assert_untouched(env_file, before)

    async def test_oauth_callback_answers_an_error_page_and_writes_nothing(
        self, env_file, monkeypatch,
    ):
        monkeypatch.setattr(oauth_routes, "_verify_state", lambda *_: True)
        _fake_httpx(monkeypatch, oauth_routes, {"access_token": "1000.x\nDATABASE_URL=evil"})
        before = _snapshot(env_file)
        page = await oauth_routes.oauth_callback(service="zoho-crm", code="c", state="s", error="")
        assert page.status_code == 400
        _assert_untouched(env_file, before)

    @pytest.mark.parametrize("value", CONTROL_VALUES)
    def test_upsert_env_var_itself_refuses(self, value, tmp_path):
        path = tmp_path / ".env"
        path.write_bytes(ENV_BEFORE)
        with pytest.raises(env_guard.EnvWriteRefused) as caught:
            integrations._upsert_env_var(path, "APOLLO_API_KEY", value)
        assert caught.value.platform is False
        assert path.read_bytes() == ENV_BEFORE

    def test_upsert_env_var_refuses_a_bad_key_shape(self, tmp_path):
        path = tmp_path / ".env"
        path.write_bytes(ENV_BEFORE)
        for key in ("APOLLO_API_KEY\n", "A=B", "lower", "A B"):
            with pytest.raises(env_guard.EnvWriteRefused):
                integrations._upsert_env_var(path, key, "v")
        assert path.read_bytes() == ENV_BEFORE

    @pytest.mark.parametrize("separator", ["\u2028", "\x0b", "\x0c", "\x85", "\x1c", "\r"])
    def test_a_rewrite_never_splits_an_old_line(self, separator, tmp_path):
        # A line that already holds a separator stays ONE line. With
        # `splitlines()`, the next rewrite of ANY key made it two.
        path = tmp_path / ".env"
        old = f"OLD=a{separator}DATABASE_URL=evil\nOTHER=1\n".encode()
        path.write_bytes(old)
        integrations._upsert_env_var(path, "APOLLO_API_KEY", "k1")
        assert path.read_bytes() == old + b"APOLLO_API_KEY=k1\n"
        assert b"\nDATABASE_URL=evil" not in path.read_bytes()

    def test_upsert_env_var_still_replaces_and_appends(self, tmp_path):
        path = tmp_path / ".env"
        path.write_bytes(b"A=1\nZOHO_REGION=in\n\n# note\n")
        integrations._upsert_env_var(path, "ZOHO_REGION", "eu")
        integrations._upsert_env_var(path, "APOLLO_API_KEY", "k1")
        assert path.read_bytes() == b"A=1\nZOHO_REGION=eu\n\n# note\nAPOLLO_API_KEY=k1\n"
        empty = tmp_path / "fresh.env"
        integrations._upsert_env_var(empty, "APOLLO_API_KEY", "k1")
        assert empty.read_bytes() == b"APOLLO_API_KEY=k1\n"

    async def test_a_trailing_newline_is_trimmed_not_refused(self, env_file, store, custom):
        # Recorded decision: a value is written with its surrounding space
        # removed, which is what systemd reads from the file anyway.
        await _configure(("APOLLO_API_KEY", "key123\n"))
        assert env_file.read_bytes() == ENV_BEFORE + b"APOLLO_API_KEY=key123\n"
        assert os.environ["APOLLO_API_KEY"] == "key123"
        assert store.puts == [("apollo:api_key", "key123")]

    async def test_key_store_skips_a_stored_integration_value_with_a_control_character(
        self, monkeypatch,
    ):
        from acb_llm.key_store import ProviderKeyStore

        stored = {"zoho-crm:client_id": "1000.x\nDATABASE_URL=evil", "apollo:api_key": "good"}
        ks = ProviderKeyStore()

        async def _get(provider: str, organization_id: str | None = None) -> str:
            return stored.get(provider, "")

        migrated: list[str] = []

        async def _put(provider: str, *_: Any, **__: Any) -> None:
            migrated.append(provider)

        monkeypatch.setattr(ks, "get", _get)
        monkeypatch.setattr(ks, "put", _put)
        monkeypatch.setenv("ZOHO_CLIENT_ID", "from-the-env-file")
        await ks.configure_integrations()
        assert os.environ["ZOHO_CLIENT_ID"] == "from-the-env-file"
        assert os.environ["APOLLO_API_KEY"] == "good"
        assert "zoho-crm:client_id" not in migrated

    async def test_key_store_skips_a_stored_llm_key_with_a_control_character(self, monkeypatch):
        import litellm
        from acb_llm.key_store import ProviderKeyStore

        for attr in ("api_key", "anthropic_api_key"):
            monkeypatch.setattr(litellm, attr, getattr(litellm, attr, None), raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        ks = ProviderKeyStore()

        async def _get_all(organization_id: str | None = None) -> dict[str, str]:
            return {"openai": "sk-x\x00y", "anthropic": "sk-ant-good"}

        monkeypatch.setattr(ks, "get_all", _get_all)
        await ks.configure_litellm()
        assert "OPENAI_API_KEY" not in os.environ
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-good"


# ── Layer A: the file form that `source` and systemd read alike ─────────────


class TestLayerAKeepsTheFileSafeToSource:
    @pytest.mark.parametrize("value", SHELL_VALUES)
    async def test_configure_refuses_a_value_the_shell_reads_as_code(
        self, value, env_file, store, custom,
    ):
        before = _snapshot(env_file)
        await _expect(400, _configure(("SMTP_PASSWORD", value)))
        _assert_untouched(env_file, before)
        assert store.puts == []

    @pytest.mark.parametrize("value", SHELL_VALUES)
    async def test_put_keys_refuses_a_value_the_shell_reads_as_code(self, value, env_file, store):
        before = _snapshot(env_file)
        await _expect(400, _put("smtp", "password", value))
        _assert_untouched(env_file, before)
        assert store.puts == []

    @pytest.mark.parametrize("value", REAL_VALUES)
    def test_real_credential_shapes_pass(self, value):
        assert env_guard.value_problem(value) is None

    async def test_the_refusal_never_echoes_the_value(self, env_file, store, custom):
        err = await _expect(400, _configure(("SMTP_PASSWORD", "s3cret value")))
        assert "s3cret" not in str(err.detail)


# ── Layer B: platform names ────────────────────────────────────────────────


class TestLayerBRefusesPlatformKeys:
    @pytest.mark.parametrize("key", [
        "GATEWAY_SESSION_SECRET", "DATABASE_URL", "EMAIL_GMAIL_CONNECT",
        "MSFT_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "LITELLM_MASTER_KEY",
        "ACB_MASTER_KEY", "REDIS_URL", "AUTH_MICROSOFT_ENTRA_ID_SECRET",
        "database_url",
    ])
    async def test_configure_refuses_and_writes_nothing(self, key, env_file, store, custom):
        before = _snapshot(env_file)
        await _expect(403, _configure((key, "value1")))
        _assert_untouched(env_file, before)
        assert store.puts == []

    async def test_configure_refuses_a_mixed_request_as_a_whole(self, env_file, store, custom):
        before = _snapshot(env_file)
        await _expect(403, _configure(
            ("APOLLO_API_KEY", "good1"), ("GATEWAY_SESSION_SECRET", "evil"),
        ))
        _assert_untouched(env_file, before)
        assert store.puts == []

    async def test_configure_refuses_a_mixed_request_with_an_undeclared_key(
        self, env_file, store, custom,
    ):
        before = _snapshot(env_file)
        await _expect(422, _configure(("APOLLO_API_KEY", "good1"), ("NOTION_API_TOKEN", "t1")))
        _assert_untouched(env_file, before)
        assert store.puts == []

    async def test_put_keys_refuses_a_mail_app_key(self, env_file, store):
        before = _snapshot(env_file)
        await _expect(403, _put("microsoft-oauth", "msft_oauth_client_id", "evil-app"))
        _assert_untouched(env_file, before)
        assert store.puts == []

    async def test_delete_keys_refuses_a_mail_app_key_before_the_store_delete(self, store):
        os.environ["MSFT_OAUTH_CLIENT_ID"] = "the-platform-app"
        req = integrations.IntegrationKeyDelete(
            service="microsoft-oauth", key_name="msft_oauth_client_id",
        )
        await _expect(403, integrations.delete_integration_key(req, user=USER))
        assert os.environ["MSFT_OAUTH_CLIENT_ID"] == "the-platform-app"
        assert store.deletes == []

    @pytest.mark.parametrize("keys", [
        ["DATABASE_URL"], ["NOTION_API_TOKEN", "GATEWAY_SESSION_SECRET"], ["https_proxy"],
    ])
    async def test_custom_registration_refuses_a_platform_key(self, keys, custom):
        req = integrations.CustomApiDef(
            service_id="notion", label="Notion",
            env_vars=[{"key": k, "label": k} for k in keys],
        )
        await _expect(403, integrations.create_custom_api(req, user=USER))
        assert custom.inserts == []

    def test_upsert_env_var_itself_refuses_a_platform_key(self, tmp_path):
        path = tmp_path / ".env"
        path.write_bytes(ENV_BEFORE)
        with pytest.raises(env_guard.EnvWriteRefused) as caught:
            integrations._upsert_env_var(path, "GATEWAY_SESSION_SECRET", "x")
        assert caught.value.platform is True
        assert path.read_bytes() == ENV_BEFORE

    @pytest.mark.parametrize("name", [
        # The names this fix was asked for.
        "DATABASE_URL", "TENANT_LADDER_DATABASE_URL", "PGPASSWORD", "POSTGRES_USER",
        "REDIS_URL", "GATEWAY_INTERNAL_TOKEN", "GATEWAY_SESSION_SECRET",
        "CUSTOMER_CONSOLE_ENCRYPTION_KEY", "FERNET_KEY", "ACB_MASTER_KEY",
        "SUPABASE_URL", "AUTH_SECRET", "OPERATOR_STAFF_DOMAINS",
        "ROUTER_SERVING_ENABLED", "CONSOLE_BOOTSTRAP_ENABLED",
        "WORKBENCH_PUBLIC_URL", "GATEWAY_PUBLIC_URL", "MSFT_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET", "AUTH_MICROSOFT_ENTRA_ID_ID",
        "EMAIL_GMAIL_CONNECT", "SELF_SERVE_SIGNUP_ENABLED", "DECIDE_ENABLED",
        "DECIDE_FEATURE_ORGS", "MEM0_ENABLED", "LITELLM_MASTER_KEY",
        "DEPLOY_TARGET_SHA", "SKIP_PRE_MIGRATION_BACKUP",
        # Names the box reads that no setting declares.
        "EXECUTIVE_EMAILS", "ALLOWED_EMAIL_DOMAIN", "SELF_MUTATION_DISABLED",
        "IDENTITY_CUTOVER", "ACTION_BROKER_ENFORCE", "INGESTION_CONSUMER",
        "CRM_ZOHO_SYNC", "WHATSAPP_APP_SECRET", "OPENAI_BASE_URL",
        "OPENAI_API_BASE", "MICROSOFT_TENANT_ID",
        # The process environment.
        "PATH", "LD_PRELOAD", "PYTHONPATH", "HTTPS_PROXY", "https_proxy",
        "GIT_SSH_COMMAND", "NODE_OPTIONS", "SSL_CERT_FILE", "DOCKER_HOST",
        # Case and space do not matter.
        " database_url ", "Gateway_Session_Secret",
    ])
    def test_the_deny_list_holds(self, name):
        assert env_guard.is_platform_env(name), name

    @pytest.mark.parametrize("name", [
        "NOTION_API_TOKEN", "APOLLO_API_KEY", "ZOHO_CLIENT_ID", "SMTP_PASSWORD",
        "GITHUB_TOKEN", "OPENAI_API_KEY", "ZOHO_ACCESS_TOKEN", "GOOGLE_REFRESH_TOKEN",
    ])
    def test_the_deny_list_can_fail(self, name):
        # The companion: a list that matched every name would pass the test
        # above and break every integration on the box.
        assert not env_guard.is_platform_env(name), name

    def test_every_settings_field_is_sorted(self):
        """A new `Settings` field is a platform setting until somebody says not."""
        fields = set(Settings.model_fields)
        unsorted = sorted(
            f for f in fields
            if f not in INTEGRATION_SETTINGS_FIELDS and not env_guard.is_platform_env(f)
        )
        assert unsorted == [], (
            "these Settings fields are on neither list. Add a platform name to "
            f"acb_common/env_guard.py or an integration field here: {unsorted}"
        )
        refused = sorted(f for f in INTEGRATION_SETTINGS_FIELDS if env_guard.is_platform_env(f))
        assert refused == [], f"integration fields that the deny list refuses: {refused}"
        stale = sorted(INTEGRATION_SETTINGS_FIELDS - fields)
        assert stale == [], f"integration fields that Settings no longer has: {stale}"

    def test_a_guide_loses_only_its_mail_app_keys(self):
        guide_keys = {v["key"] for g in integrations._SETUP_GUIDES.values() for v in g["env_vars"]}
        refused = {k for k in guide_keys if env_guard.is_platform_env(k)}
        assert refused <= MAIL_APP_GUIDE_KEYS, sorted(refused - MAIL_APP_GUIDE_KEYS)
        assert "MSFT_OAUTH_CLIENT_ID" in refused

    def test_the_mail_app_prefixes_are_refused(self):
        # WS-17 EM-G7 names these three prefixes for its own refusal.
        for prefix in ("GMAIL_OAUTH_", "MSFT_OAUTH_", "AUTH_MICROSOFT_ENTRA_ID_"):
            assert env_guard.is_platform_env(prefix + "ANYTHING"), prefix


# ── Layer C: configure takes declared keys only ─────────────────────────────


class TestLayerCDeclaredKeysOnly:
    async def test_configure_refuses_an_undeclared_key(self, env_file, store, custom):
        before = _snapshot(env_file)
        await _expect(422, _configure(("NOTION_API_TOKEN", "secret1")))
        _assert_untouched(env_file, before)

    async def test_a_failed_custom_read_refuses_rather_than_guesses(
        self, env_file, store, monkeypatch,
    ):
        monkeypatch.setattr(integrations, "_db_query", FakeCustomTable(fail=True))
        before = _snapshot(env_file)
        await _expect(422, _configure(("NOTION_API_TOKEN", "secret1")))
        _assert_untouched(env_file, before)

    async def test_a_declared_key_needs_no_custom_read(self, env_file, store, custom):
        await _configure(("APOLLO_API_KEY", "key123"))
        assert custom.statements == []

    async def test_a_custom_key_reads_the_same_rows_the_page_shows(self, env_file, store, custom):
        custom.rows.extend(FakeCustomTable(("NOTION_API_TOKEN",)).rows)
        await _configure(("NOTION_API_TOKEN", "secret_abc123"))
        assert custom.statements == ["SELECT * FROM custom_api_definitions ORDER BY created_at"]


# ── The callers still work, one case per caller path ────────────────────────


class TestEveryCallerStillWorks:
    async def test_integrations_page_credential_form_saves_a_guide_key(self, env_file, store, custom):
        # src/app/integrations/page.tsx CredentialForm (about :217).
        out = await _configure(("APOLLO_API_KEY", "apollo_key_123"))
        assert out["written"] == ["APOLLO_API_KEY"]
        assert env_file.read_bytes() == ENV_BEFORE + b"APOLLO_API_KEY=apollo_key_123\n"
        assert os.environ["APOLLO_API_KEY"] == "apollo_key_123"
        assert store.puts == [("apollo:api_key", "apollo_key_123")]

    async def test_integrations_page_discovery_registers_then_saves_a_custom_key(
        self, env_file, store, custom,
    ):
        # src/app/integrations/page.tsx AI discovery (about :580 and :597).
        await integrations.create_custom_api(integrations.CustomApiDef(
            service_id="notion", label="Notion",
            env_vars=[{"key": "NOTION_API_TOKEN", "label": "Token", "sensitive": True}],
        ), user=USER)
        assert len(custom.inserts) == 1
        out = await _configure(("NOTION_API_TOKEN", "secret_abc123"))
        assert out["written"] == ["NOTION_API_TOKEN"]
        assert os.environ["NOTION_API_TOKEN"] == "secret_abc123"

    async def test_agent_wizard_and_setup_card_save_the_zoho_keys(self, env_file, store, custom):
        # src/components/AddAgentWizard.tsx (about :331) and
        # src/components/IntegrationSetup.tsx (about :276) send the guide keys
        # of the agent's integrations.
        out = await _configure(
            ("ZOHO_CLIENT_ID", "1000.ABCDEF123456"),
            ("ZOHO_CLIENT_SECRET", "0f3b9c4d2e8a1b"),
            ("ZOHO_REFRESH_TOKEN", "1000.0f3b9c.4d2e8a1b"),
        )
        assert out["written"] == ["ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"]
        assert b"ZOHO_REFRESH_TOKEN=1000.0f3b9c.4d2e8a1b\n" in env_file.read_bytes()

    async def test_github_device_connect_saves_the_client_id(self, env_file, store, custom):
        # src/components/GitHubDeviceConnect.tsx (about :126), Option B.
        out = await _configure(("GITHUB_CLIENT_ID", "Iv1.8a61f9b3a7aba766"))
        assert out["written"] == ["GITHUB_CLIENT_ID"]

    async def test_github_device_connect_saves_a_pat_when_byok_is_on(
        self, env_file, store, custom, byok_on,
    ):
        # Option A. GITHUB_TOKEN is in `_PROVIDER_ENV_MAP`, so BYOK gates it
        # (pre-existing, `test_byok_disabled.py`). With BYOK on it saves.
        out = await _configure(("GITHUB_TOKEN", "ghp_AbCdEf0123456789"))
        assert out["written"] == ["GITHUB_TOKEN"]

    @pytest.mark.parametrize("caller", ["agent-chat-route", "orchestrator-executor"])
    async def test_a_setup_token_saves_a_declared_key(self, caller, env_file, store, custom):
        # src/app/api/agent/chat/route.ts (about :807) and
        # orchestrator/executor.py (about :5139) post the model's
        # <<<SETUP:service:KEY=value>>> tokens as `{vars: [{key, value}]}`.
        out = await _configure(("SERPAPI_API_KEY", "abc123def456"))
        assert out["written"] == ["SERPAPI_API_KEY"]

    @pytest.mark.parametrize("key, value, code", [
        ("GATEWAY_SESSION_SECRET", "evil", 403),
        ("PATH", "/tmp/evil", 403),
        ("SERPAPI_API_KEY", "abc\nDATABASE_URL=evil", 400),
        ("SOME_RANDOM_KEY", "x1", 422),
    ])
    async def test_a_setup_token_cannot_reach_past_the_layers(
        self, key, value, code, env_file, store, custom,
    ):
        # The token text is model output, so a prompt injection writes it.
        before = _snapshot(env_file)
        await _expect(code, _configure((key, value)))
        _assert_untouched(env_file, before)

    async def test_put_keys_saves_a_guide_key(self, env_file, store):
        out = await _put("apollo", "api_key", "apollo_key_123")
        assert out["env_var"] == "APOLLO_API_KEY"
        assert os.environ["APOLLO_API_KEY"] == "apollo_key_123"
        assert env_file.read_bytes() == ENV_BEFORE + b"APOLLO_API_KEY=apollo_key_123\n"

    async def test_delete_keys_removes_a_guide_key(self, store):
        os.environ["APOLLO_API_KEY"] = "apollo_key_123"
        await integrations.delete_integration_key(
            integrations.IntegrationKeyDelete(service="apollo", key_name="api_key"), user=USER,
        )
        assert "APOLLO_API_KEY" not in os.environ
        assert store.deletes == ["apollo:api_key"]

    async def test_github_device_poll_saves_a_token(self, env_file, monkeypatch):
        monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.8a61f9b3a7aba766")
        get_settings.cache_clear()
        _fake_httpx(monkeypatch, integrations, {"access_token": "gho_AbCdEf0123456789"})
        out = await integrations.github_device_poll(
            integrations.DevicePollRequest(device_code="d"), user=USER,
        )
        assert out == {"status": "authorized", "login": "octocat"}
        assert env_file.read_bytes() == ENV_BEFORE + b"GITHUB_TOKEN=gho_AbCdEf0123456789\n"

    async def test_github_connect_cli_saves_a_token(self, env_file, monkeypatch):
        # src/components/GitHubAccountBadge.tsx (about :103).
        _fake_gh_cli(monkeypatch, "gho_AbCdEf0123456789\n")
        out = await integrations.github_connect_cli(user=USER)
        assert out["ok"] is True
        assert os.environ["GITHUB_TOKEN"] == "gho_AbCdEf0123456789"

    async def test_oauth_callback_saves_the_zoho_tokens(self, env_file, monkeypatch):
        monkeypatch.setattr(oauth_routes, "_verify_state", lambda *_: True)
        _fake_httpx(monkeypatch, oauth_routes, {
            "access_token": "1000.access", "refresh_token": "1000.refresh", "expires_in": 3600,
        })
        page = await oauth_routes.oauth_callback(service="zoho-crm", code="c", state="s", error="")
        assert page.status_code == 200
        text = env_file.read_bytes().decode()
        assert "ZOHO_ACCESS_TOKEN=1000.access\n" in text
        assert "ZOHO_REFRESH_TOKEN=1000.refresh\n" in text
        assert "ZOHO_TOKEN_EXPIRY=" in text


# ── The Models routes: the same guard on settings.py::_write_env_key ──────
#
# `POST /settings/llm/key` and `POST /settings/llm/copilot-model` write the
# same env file through a second writer. No BFF route reaches either one
# today (measured 2026-10-05), and a direct gateway call still can.


@pytest.fixture()
def models_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_bytes(ENV_BEFORE)
    monkeypatch.setattr(settings_routes, "_env_file_path", lambda: path)
    return path


async def _set_key(provider: str, api_key: str) -> dict[str, str]:
    req = settings_routes.ProviderKeyRequest(provider=provider, api_key=api_key)
    out = await settings_routes.set_provider_key(req, _user=USER)
    await asyncio.sleep(0)  # let the scheduled store write run
    return out


async def _set_model(model: str) -> dict[str, str]:
    req = settings_routes.CopilotModelRequest(model=model)
    return await settings_routes.set_copilot_model(req, _user=USER)


MODELS_BAD_VALUES = [
    pytest.param("sk-x\nDATABASE_URL=postgres://evil", id="LF"),
    pytest.param("sk-$(touch /tmp/p)", id="substitution"),
    pytest.param("sk-x\x00y", id="NUL"),
    pytest.param("sk-x\u2028GATEWAY_SESSION_SECRET=evil", id="U+2028"),
    pytest.param("sk x", id="space"),
]


class TestTheModelsRoutesUseTheSameGuard:
    @pytest.mark.parametrize("value", MODELS_BAD_VALUES)
    async def test_the_key_route_refuses_and_writes_nothing(
        self, value, models_env_file, store, byok_on,
    ):
        before = _snapshot(models_env_file)
        await _expect(400, _set_key("openai", value))
        _assert_untouched(models_env_file, before)
        assert store.puts == []

    @pytest.mark.parametrize("value", MODELS_BAD_VALUES)
    async def test_the_copilot_model_route_refuses_and_writes_nothing(self, value, models_env_file):
        before = _snapshot(models_env_file)
        await _expect(400, _set_model(value))
        _assert_untouched(models_env_file, before)

    async def test_the_key_route_refuses_vllm_base_url(self, models_env_file, store, byok_on):
        # A URL, and a vLLM client sends its key to the host it names.
        before = _snapshot(models_env_file)
        await _expect(403, _set_key("vllm", "http://evil.example/v1"))
        _assert_untouched(models_env_file, before)
        assert store.puts == []

    async def test_the_model_setting_still_saves(self, models_env_file):
        out = await _set_model("claude-sonnet-4-5")
        assert out == {"ok": "true", "model": "claude-sonnet-4-5"}
        assert models_env_file.read_bytes() == ENV_BEFORE + b"COPILOT_CHAT_MODEL=claude-sonnet-4-5\n"

    async def test_a_provider_key_still_saves(self, models_env_file, store, byok_on):
        out = await _set_key("openai", "sk-proj-AbC123_xyz")
        assert out["env_var"] == "OPENAI_API_KEY"
        assert models_env_file.read_bytes() == ENV_BEFORE + b"OPENAI_API_KEY=sk-proj-AbC123_xyz\n"
        assert os.environ["OPENAI_API_KEY"] == "sk-proj-AbC123_xyz"
        assert store.puts == [("openai", "sk-proj-AbC123_xyz")]

    def test_only_vllm_base_url_of_the_provider_map_is_refused(self):
        names = {v for v in settings_routes._PROVIDER_ENV_MAP.values() if v}
        assert {n for n in names if env_guard.is_platform_env(n)} == {"VLLM_BASE_URL"}

    def test_the_models_page_owns_exactly_one_platform_key(self):
        # Growing this set lets a route write a platform key. It must be a
        # model choice, never a secret and never a URL, so it is pinned.
        assert frozenset({"COPILOT_CHAT_MODEL"}) == settings_routes._MODELS_PAGE_ENV_KEYS
        assert env_guard.is_platform_env("COPILOT_CHAT_MODEL")

    def test_owned_is_an_exact_name_exemption(self):
        owned = settings_routes._MODELS_PAGE_ENV_KEYS
        env_guard.check_env_write("COPILOT_CHAT_MODEL", "gpt-4o", owned=owned)
        for key in ("COPILOT_SANDBOX_IMAGE", "COPILOT_LLM_BASE_URL", "VLLM_BASE_URL"):
            with pytest.raises(env_guard.EnvWriteRefused) as caught:
                env_guard.check_env_write(key, "x", owned=owned)
            assert caught.value.platform is True, key
        with pytest.raises(env_guard.EnvWriteRefused):
            env_guard.check_env_write("COPILOT_CHAT_MODEL", "x\ny", owned=owned)

    @pytest.mark.parametrize("value", MODELS_BAD_VALUES)
    def test_write_env_key_itself_refuses(self, value, models_env_file):
        with pytest.raises(env_guard.EnvWriteRefused) as caught:
            settings_routes._write_env_key("OPENAI_API_KEY", value)
        assert caught.value.platform is False
        assert models_env_file.read_bytes() == ENV_BEFORE

    def test_write_env_key_itself_refuses_a_platform_key(self, models_env_file):
        for key in ("GATEWAY_SESSION_SECRET", "VLLM_BASE_URL", "DATABASE_URL"):
            with pytest.raises(env_guard.EnvWriteRefused) as caught:
                settings_routes._write_env_key(key, "x")
            assert caught.value.platform is True, key
        assert models_env_file.read_bytes() == ENV_BEFORE

    @pytest.mark.parametrize("separator", ["\u2028", "\x0b", "\x85", "\r"])
    def test_write_env_key_never_splits_an_old_line(self, separator, models_env_file):
        old = f"OLD=a{separator}DATABASE_URL=evil\nOTHER=1\n".encode()
        models_env_file.write_bytes(old)
        settings_routes._write_env_key("OPENAI_API_KEY", "sk-1")
        assert models_env_file.read_bytes() == old + b"OPENAI_API_KEY=sk-1\n"

    def test_write_env_key_still_replaces_and_appends(self, models_env_file):
        models_env_file.write_bytes(b"A=1\nCOPILOT_CHAT_MODEL=gpt-4o\n\n# note")
        settings_routes._write_env_key("COPILOT_CHAT_MODEL", "o3-mini")
        settings_routes._write_env_key("OPENAI_API_KEY", "sk-1")
        assert models_env_file.read_bytes() == (
            b"A=1\nCOPILOT_CHAT_MODEL=o3-mini\n\n# note\nOPENAI_API_KEY=sk-1\n"
        )
