"""Task 4120: the bot token of the meeting-bot worker fails closed.

Before, ``app/main.py::_auth`` let every caller through when
``MEETING_BOT_TOKEN`` was unset, and it compared the bearer with ``!=``. So a
worker that started with no token was an open API: anybody who reached the
port could send a bot into a meeting, read a recording or sign the browser
into a Google account.

Now each route except ``/health`` answers:

* 503 ``MEETING_BOT_TOKEN is not configured`` when the token is unset or blank,
* 401 when the bearer is missing or wrong,
* the normal answer of the route when the bearer is right.

This is the status meaning of the gateway side (task 4113,
``live_transcript._check_bot_auth``). ``/health`` stays open, because
``scripts/vps_apply.sh`` reads it with a bare ``curl``.

The cases run over HTTP, through the real FastAPI app. The route table is
read from the app, so a new route with no row here fails
``test_every_route_is_in_the_table``.

The worker lives outside the uv workspace, so it loads by path, with stubs for
``.meet`` and ``.google_login`` (no Playwright, no browser).
"""
from __future__ import annotations

import importlib.util
import inspect
import pathlib
import sys
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient

_APP = pathlib.Path(__file__).resolve().parents[2] / "apps/services/meeting_bot/app"
_PKG = "mbapp_auth"
TOKEN = "worker-test-token-4120"


def _load() -> Any:
    """The worker's ``main`` module, with no browser behind it."""
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_APP)]
    sys.modules[_PKG] = pkg

    meet = types.ModuleType(f"{_PKG}.meet")
    meet.PROFILE_DIR = ""
    meet.DIAG_DIR = "/tmp"

    class _Err(Exception):
        def __init__(self, message, status="failed", diagnostics=None):
            super().__init__(message)
            self.status = status
            self.diagnostics = diagnostics or {}

    meet.MeetingBotError = _Err
    meet.join_and_record = lambda *a, **k: None
    sys.modules[f"{_PKG}.meet"] = meet

    gl = types.ModuleType(f"{_PKG}.google_login")

    class _LoginError(Exception):
        def __init__(self, message: str = "") -> None:
            super().__init__(message)
            self.diagnostics: dict = {}

    async def _account() -> dict:
        return {"signed_in": False}

    async def _sign_in(email: str, password: str) -> dict:
        return {"signed_in": True}

    gl.LoginError = _LoginError
    gl.current_account = _account
    gl.sign_in = _sign_in
    gl.interactive_status = lambda: {"running": False}
    gl.configured_credentials = lambda: ("", "")
    gl.start_interactive = lambda: {"running": True}
    sys.modules[f"{_PKG}.google_login"] = gl

    spec = importlib.util.spec_from_file_location(f"{_PKG}.main", _APP / "main.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.main"] = mod
    spec.loader.exec_module(mod)
    return mod


main = _load()

#: Every route of the worker with the request that reaches its handler.
#: ``(method, path)`` -> ``(url, json body or None)``.
ROUTES: dict[tuple[str, str], tuple[str, dict | None]] = {
    ("POST", "/bots"): ("/bots", {"meeting_url": "https://meet.google.com/a-b-c"}),
    ("GET", "/bots/{bot_id}"): ("/bots/nope", None),
    ("POST", "/bots/{bot_id}/say"): ("/bots/nope/say", {"text": "hello"}),
    ("POST", "/bots/{bot_id}/leave"): ("/bots/nope/leave", None),
    ("GET", "/bots/{bot_id}/recording"): ("/bots/nope/recording", None),
    ("GET", "/bots/{bot_id}/diagnostics"): ("/bots/nope/diagnostics", None),
    ("GET", "/bots/{bot_id}/screenshot"): ("/bots/nope/screenshot", None),
    ("GET", "/google-login"): ("/google-login", None),
    ("POST", "/google-login"): ("/google-login", None),
    ("POST", "/google-login/interactive"): ("/google-login/interactive", None),
}

#: Routes with no token on purpose, with the reason.
OPEN_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/health"): "scripts/vps_apply.sh reads it with a bare curl. "
    "Two counts, no meeting data",
}


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """The real app. Dispatch starts no browser and writes under tmp_path."""
    main._JOBS.clear()
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "_unset_logged", False, raising=False)

    async def _no_run(_job: Any) -> None:
        return None

    monkeypatch.setattr(main, "_run", _no_run)
    return TestClient(main.app)


def _send(c: TestClient, key: tuple[str, str], headers: dict[str, str]) -> Any:
    url, body = ROUTES[key]
    return c.request(key[0], url, json=body, headers=headers)


def _app_routes() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in main.app.routes:
        for method in getattr(route, "methods", None) or ():
            if method in ("HEAD", "OPTIONS"):
                continue
            out.add((method, route.path))
    return out


def test_every_route_is_in_the_table() -> None:
    """A new route must say if it needs the token. No row means red."""
    assert _app_routes() == set(ROUTES) | set(OPEN_ROUTES)


@pytest.mark.parametrize("key", sorted(ROUTES))
@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_an_unset_token_answers_503(client, monkeypatch, key, value) -> None:
    """The old code let this call through. It is the case that was open."""
    if value is None:
        monkeypatch.delenv("MEETING_BOT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MEETING_BOT_TOKEN", value)
    for headers in ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer x"}):
        r = _send(client, key, headers)
        assert r.status_code == 503, (key, headers, r.status_code, r.text)
        assert r.json()["detail"] == "MEETING_BOT_TOKEN is not configured"
    assert main._JOBS == {}, "an unset token dispatched a bot"


@pytest.mark.parametrize("key", sorted(ROUTES))
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": TOKEN},
        {"Authorization": f"Token {TOKEN}"},
    ],
    ids=["missing", "wrong", "longer", "no-scheme", "other-scheme"],
)
def test_a_wrong_bearer_answers_401(client, monkeypatch, key, headers) -> None:
    monkeypatch.setenv("MEETING_BOT_TOKEN", TOKEN)
    r = _send(client, key, headers)
    assert r.status_code == 401, (key, headers, r.status_code, r.text)
    assert TOKEN not in r.text
    assert main._JOBS == {}, "a wrong bearer dispatched a bot"


@pytest.mark.parametrize("key", sorted(ROUTES))
def test_the_right_bearer_reaches_the_route(client, monkeypatch, key) -> None:
    """The right bearer gets the normal answer: 201 for a dispatch, 200 for
    the login routes, 404 for a bot id that does not exist."""
    monkeypatch.setenv("MEETING_BOT_TOKEN", f"  {TOKEN}  ")
    r = _send(client, key, {"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code not in (401, 503), (key, r.status_code, r.text)
    expected = {"/bots": 201, "/google-login": 200, "/google-login/interactive": 202}
    assert r.status_code == expected.get(key[1], 404), (key, r.status_code, r.text)


@pytest.mark.parametrize("value", [None, "", TOKEN], ids=["unset", "empty", "set"])
def test_health_stays_open(client, monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("MEETING_BOT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MEETING_BOT_TOKEN", value)
    r = client.get("/health")
    assert r.status_code == 200
    assert set(r.json()) == {"ok", "active", "profile"}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_schema_pages_are_off(client, path) -> None:
    assert client.get(path).status_code == 404


def test_the_first_unset_refusal_logs_one_line(client, monkeypatch, caplog) -> None:
    monkeypatch.delenv("MEETING_BOT_TOKEN", raising=False)
    with caplog.at_level("WARNING", logger="meeting_bot"):
        for _ in range(3):
            assert client.get("/bots/nope").status_code == 503
    lines = [r for r in caplog.records if "bot_token.not_configured" in r.getMessage()]
    assert len(lines) == 1
    assert "MEETING_BOT_TOKEN" in lines[0].getMessage()


def test_the_check_is_constant_time() -> None:
    """Source fence: the one check uses ``hmac.compare_digest``."""
    src = inspect.getsource(main._auth)
    body = src.split('"""')[-1]  # the code after the docstring
    assert "hmac.compare_digest" in body
    assert "!=" not in body and "==" not in body
