"""Task 4113: the bot token of the meeting-bot worker fails closed.

Three routes of ``/notes`` take no member. The meeting-bot worker calls each
one with the shared ``MEETING_BOT_TOKEN``:

* ``POST /notes/meetings/{id}/live/segment`` posts a live caption.
* ``GET /notes/meetings/{id}/live/wanted`` asks if the worker may stream.
* ``POST /notes/stt/bot-live-token`` mints a streaming ASR credential.

``live_transcript._check_bot_auth`` is the one check of all three. Before this
task, an unset token let every caller through. Two of the routes are in
``main.PUBLIC_ROUTES``, so any caller with no member could then post captions
into the live bus of a meeting of any tenant, or mint a provider credential.

The rule now:

* token unset: 503, the detail names the setting, and one log line names it
  too. The value is never in a line.
* bearer missing or wrong: 401.
* bearer right: the route runs.

The source half pins that each ``/notes`` route with an ``authorization``
header calls the check first, and that the check compares in constant time.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from gateway.routes.notes import live
from gateway.routes.notes import live_speakers as ls
from gateway.routes.notes import live_transcript as lt

TOKEN = "s3cret-bot-token"
UNSET_DETAIL = "MEETING_BOT_TOKEN is not configured"
WRONG = (None, "", "Bearer", "Bearer ", "Bearer wrong", TOKEN, f"bearer {TOKEN}",
         f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", "Bearer s3cret-bot-tokén")

_NOTES = Path(__file__).resolve().parents[2] / (
    "apps/services/gateway/gateway/routes/notes"
)


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kw: Any) -> None:
        self.lines.append((event, kw))


@pytest.fixture
def calls(monkeypatch) -> dict[str, list[str]]:
    """Each route body, played. A list fills only when the check let it pass."""
    seen: dict[str, list[str]] = {"publish": [], "wanted": [], "minted": []}

    def publish(meeting_id: str, data: dict) -> None:
        seen["publish"].append(meeting_id)

    async def wanted(meeting_id: str, db: Any = None) -> tuple[bool, str]:
        seen["wanted"].append(meeting_id)
        return True, "wanted"

    async def mint() -> live.LiveToken:
        seen["minted"].append("t")
        return live.LiveToken(provider="assemblyai", token="t", model="", expires_in=60)

    monkeypatch.setattr(lt, "publish_segment", publish)
    monkeypatch.setattr(live, "live_wanted", wanted)
    monkeypatch.setattr(live, "_issue_live_token", mint)
    monkeypatch.setattr(lt, "_unset_logged", False, raising=False)
    return seen


def _segment(authorization: str | None) -> Any:
    seg = lt.LiveSegment(text="hello", start_s=0.0, end_s=1.0)
    return lt.ingest_live_segment("m-4113", seg, authorization=authorization)


def _wanted(authorization: str | None) -> Any:
    return live.read_live_wanted("m-4113", authorization=authorization)


def _mint(authorization: str | None) -> Any:
    return live.bot_live_token(authorization=authorization)


ROUTES = {
    "segment": (_segment, "publish"),
    "wanted": (_wanted, "wanted"),
    "bot-live-token": (_mint, "minted"),
}


def _call(route: str, authorization: str | None) -> Any:
    fn, _ = ROUTES[route]
    ls.reset("m-4113")
    return asyncio.run(fn(authorization))


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("unset", ["", "   "], ids=["absent-or-empty", "blank"])
def test_an_unset_token_refuses_every_caller(route, unset, calls, monkeypatch) -> None:
    if unset:
        monkeypatch.setenv("MEETING_BOT_TOKEN", unset)
    else:
        monkeypatch.delenv("MEETING_BOT_TOKEN", raising=False)
    for auth in (*WRONG, f"Bearer {TOKEN}", "Bearer   "):
        with pytest.raises(HTTPException) as ei:
            _call(route, auth)
        assert ei.value.status_code == 503, (route, auth)
        assert ei.value.detail == UNSET_DETAIL
    assert calls[ROUTES[route][1]] == [], f"{route} ran with no token set"


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_wrong_bearer_is_401(route, calls, monkeypatch) -> None:
    monkeypatch.setenv("MEETING_BOT_TOKEN", TOKEN)
    for auth in WRONG:
        with pytest.raises(HTTPException) as ei:
            _call(route, auth)
        assert ei.value.status_code == 401, (route, auth)
    assert calls[ROUTES[route][1]] == [], f"{route} ran on a wrong bearer"


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_the_right_bearer_runs_the_route(route, calls, monkeypatch) -> None:
    monkeypatch.setenv("MEETING_BOT_TOKEN", f"  {TOKEN}  ")
    out = _call(route, f"Bearer {TOKEN}")
    assert calls[ROUTES[route][1]], f"{route} did not run"
    if route == "segment":
        assert out == {"ok": True}
    elif route == "wanted":
        assert out.wanted is True
    else:
        assert out.token == "t"


def test_the_unset_token_logs_one_line_with_no_value(calls, monkeypatch) -> None:
    log = _Log()
    monkeypatch.setattr(lt, "_log", log)
    monkeypatch.delenv("MEETING_BOT_TOKEN", raising=False)
    for route in ROUTES:
        for _ in range(3):
            with pytest.raises(HTTPException):
                _call(route, f"Bearer {TOKEN}")
    assert log.lines == [
        ("notes.bot_token.not_configured", {"setting": "MEETING_BOT_TOKEN"})
    ]


def test_a_wrong_bearer_logs_no_token(calls, monkeypatch) -> None:
    log = _Log()
    monkeypatch.setattr(lt, "_log", log)
    monkeypatch.setenv("MEETING_BOT_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as ei:
        _call("segment", "Bearer wrong")
    assert TOKEN not in str(ei.value.detail)
    assert all(TOKEN not in repr(line) for line in log.lines)


#: The routes in ``core.router``'s ``exempt`` list. Each route keeps the
#: feature gate of the router, so this HTTP test reaches the bot check only on
#: these two. ``/live/wanted`` is not exempt, and the gate answers 401 first
#: (filed in the gateway AGENTS.md). The direct calls above cover its check.
HTTP_ROUTES = ("bot-live-token", "segment")


def _client() -> TestClient:
    """The two exempt machine routes over HTTP, with no member at all."""
    app = FastAPI()
    sub = APIRouter()
    paths = {
        "/notes/meetings/{meeting_id}/live/segment",
        "/notes/stt/bot-live-token",
    }
    for route in lt.router.routes:
        if getattr(route, "path", "") in paths:
            sub.routes.append(route)
    assert len(sub.routes) == len(paths)
    app.include_router(sub)
    return TestClient(app)


def _http(c: TestClient, route: str, headers: dict[str, str]) -> int:
    ls.reset("m-http")
    if route == "segment":
        body = {"text": "x", "start_s": 0.0, "end_s": 1.0, "is_final": True}
        return c.post("/notes/meetings/m-http/live/segment", json=body,
                      headers=headers).status_code
    return c.post("/notes/stt/bot-live-token", headers=headers).status_code


@pytest.mark.parametrize("route", HTTP_ROUTES)
def test_over_http_unset_wrong_and_right(route, calls, monkeypatch) -> None:
    c = _client()
    right = {"Authorization": f"Bearer {TOKEN}"}
    monkeypatch.delenv("MEETING_BOT_TOKEN", raising=False)
    assert _http(c, route, {}) == 503
    assert _http(c, route, right) == 503
    monkeypatch.setenv("MEETING_BOT_TOKEN", TOKEN)
    assert _http(c, route, {}) == 401
    assert _http(c, route, {"Authorization": "Bearer wrong"}) == 401
    ok = 202 if route == "segment" else 200
    assert _http(c, route, right) == ok


# ── the source half ──────────────────────────────────────────────────────────

def _routes_with_authorization() -> dict[str, ast.AsyncFunctionDef]:
    found: dict[str, ast.AsyncFunctionDef] = {}
    for path in sorted(_NOTES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                       and isinstance(d.func.value, ast.Name)
                       and d.func.value.id == "router" for d in node.decorator_list):
                continue
            args = [a.arg for a in (*node.args.args, *node.args.kwonlyargs)]
            if "authorization" in args:
                found[f"{path.name}:{node.name}"] = node
    return found


def test_each_route_with_an_authorization_header_checks_the_bot_token_first() -> None:
    routes = _routes_with_authorization()
    assert set(routes) == {
        "live_transcript.py:ingest_live_segment",
        "live.py:read_live_wanted",
        "live.py:bot_live_token",
    }, "a new /notes route reads authorization: name it here and check it"
    for name, fn in routes.items():
        body = [s for s in fn.body
                if not isinstance(s, (ast.Import, ast.ImportFrom))
                and not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        first = body[0]
        assert (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
                and getattr(first.value.func, "id", "") == "_check_bot_auth"), (
            f"{name} must call _check_bot_auth(authorization) first")


def test_there_is_one_check_and_it_compares_in_constant_time() -> None:
    defs = []
    for path in sorted(_NOTES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defs += [path.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == "_check_bot_auth"]
    assert defs == ["live_transcript.py"], defs
    src = inspect.getsource(lt._check_bot_auth)
    assert "hmac.compare_digest" in src
    tree = ast.parse(src)
    compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
    assert not any(isinstance(op, (ast.Eq, ast.NotEq))
                   for n in compares for op in n.ops), "compare the token in constant time"
