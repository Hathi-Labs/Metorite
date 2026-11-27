"""Task 4117 (PAR-4e-1): R8 of the four notes write tools, through the real routes.

Spec: ``project-docs/specs/chat_parity.md`` section 6.4. Module:
``acb_skills.notes_write_tools``.

The tools call the real Notes routers over ASGI, on the phase-4 promoted
catalog, as the app role under FORCE row level security. What is locked:

1. The glossary add and removal and the instruction change touch only the rows
   of the member of the run, in that member's org.
2. A member cannot remove the term of a colleague or of another org.
3. The settings write never erases a field that the member did not name. This
   holds with the settings read of today and with the bound read of task 3966:
   the tool saves one field and keeps the rest, or it saves nothing.

Run (a ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_notes_write_tools_r8.py -v -rs

Without the database every test here SKIPS. A skip is not a pass.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.notes", reason="gateway not installed")

import httpx
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common.db import bind_tenant, release_tenant
from acb_skills import ask_tools
from acb_skills import notes_read_tools as nt
from acb_skills import notes_write_tools as nw
from fastapi import FastAPI, Request
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

_REAL_CLIENT = httpx.AsyncClient


class _World:
    def __init__(self, promoted: Any) -> None:  # noqa: F811
        self.p = promoted
        self.tag = uuid.uuid4().hex[:8]
        self.mel = f"mel.{self.tag}@t4117-a.example.test"
        self.priya = f"priya.{self.tag}@t4117-a.example.test"
        self.zed = f"zed.{self.tag}@t4117-b.example.test"
        self.orgs = {self.mel: promoted.org_a, self.priya: promoted.org_a,
                     self.zed: promoted.org_b}
        self.member = self.mel
        self.terms: dict[str, str] = {}

    def user(self, email: str) -> UserContext:
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           organization_id=self.orgs[email],
                           access=build_access(["feature:*"]))

    def act(self, who: str, tool: Callable[..., Awaitable[dict[str, Any]]],
            *args: Any, **kw: Any) -> dict[str, Any]:
        self.member = who
        url = self.p.app_url.render_as_string(hide_password=False)

        async def go() -> dict[str, Any]:
            async with tenant_engine_scope(url):
                return await tool(*args, **kw)
        return asyncio.run(go())

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        with self.p.admin_engine.begin() as c:
            return [tuple(r) for r in c.execute(text(sql), {"t": f"%.{self.tag}@%"})]

    def glossary(self) -> list[tuple[Any, ...]]:
        return self.rows("SELECT user_id, term, organization_id::text FROM notes_glossary "
                         "WHERE user_id LIKE :t ORDER BY user_id, term")

    def config(self) -> list[tuple[Any, ...]]:
        return self.rows("SELECT owner_email, instructions, copilot_default_on, "
                         "copilot_sensitivity, bot_name, live_transcription, "
                         "auto_dispatch_emails, organization_id::text FROM copilot_config "
                         "WHERE owner_email LIKE :t ORDER BY owner_email")


def _app(w: _World) -> Any:
    from gateway.routes.notes import router

    app = FastAPI()
    app.include_router(router)

    def who(request: Request) -> UserContext:
        return w.user(request.headers.get("x-user-email", ""))

    app.dependency_overrides[get_current_user] = who

    async def bound(scope: Any, receive: Any, send: Any) -> None:
        caller = dict(scope.get("headers") or []).get(b"x-user-email", b"").decode()
        token = bind_tenant(w.orgs[caller])
        try:
            await app(scope, receive, send)
        finally:
            release_tenant(token)

    return bound


def _seed(w: _World) -> None:
    with w.p.admin_engine.begin() as c:
        for email, org in w.orgs.items():
            w.terms[email] = str(c.execute(text(
                "INSERT INTO notes_glossary (user_id, term, organization_id) VALUES "
                "(:u, :term, CAST(:o AS uuid)) RETURNING id"),
                {"u": email, "term": "Seeded", "o": org}).scalar())
            c.execute(text(
                "INSERT INTO copilot_config (owner_email, instructions, copilot_default_on, "
                "copilot_sensitivity, bot_name, live_transcription, auto_dispatch_emails, "
                "organization_id) VALUES (:e, 'Seeded text', true, 'high', 'Bot', 'never', "
                "false, CAST(:o AS uuid))"), {"e": email, "o": org})


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    w = _World(promoted)
    _seed(w)
    transport = httpx.ASGITransport(app=_app(w))
    monkeypatch.setattr(nw.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=transport, **kw))
    monkeypatch.setattr(nt, "_current_user_email", lambda: w.member)

    async def approve(*_a: Any, **_k: Any) -> bool:
        return True

    monkeypatch.setattr(ask_tools, "request_confirmation", approve)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    yield w
    with promoted.admin_engine.begin() as c:
        for table, col in (("notes_glossary", "user_id"), ("copilot_config", "owner_email")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} LIKE :t"),
                      {"t": f"%.{w.tag}@%"})


def test_the_glossary_writes_change_only_my_rows(world) -> None:
    w = world
    added = w.act(w.mel, nw.notes_add_glossary_term, "Modbus")
    assert added["ok"] is True, added
    assert (w.mel, "Modbus", w.orgs[w.mel]) in w.glossary()
    before = w.glossary()
    for other in (w.priya, w.zed):
        out = w.act(w.mel, nw.notes_delete_glossary_term, w.terms[other])
        assert out["ok"] is False and out["status"] == 404, out
    assert w.glossary() == before
    removed = w.act(w.mel, nw.notes_delete_glossary_term, w.terms[w.mel])
    assert removed["ok"] is True, removed
    assert w.glossary() == [r for r in before if r[:2] != (w.mel, "Seeded")]


def test_the_instruction_change_touches_only_my_row_and_only_that_field(world) -> None:
    w = world
    before = {r[0]: r for r in w.config()}
    out = w.act(w.mel, nw.notes_set_copilot_instructions, "Line one.\nLine two.")
    assert out["ok"] is True, out
    after = {r[0]: r for r in w.config()}
    assert after[w.mel] == (w.mel, "Line one.\nLine two.", *before[w.mel][2:])
    assert after[w.priya] == before[w.priya] and after[w.zed] == before[w.zed]


def test_the_settings_write_never_erases_a_saved_field(world) -> None:
    """Mutation: drop the all-default check while the settings read is not
    bound -> the row loses its saved values -> red."""
    w = world
    before = {r[0]: r for r in w.config()}
    out = w.act(w.mel, nw.notes_update_settings, copilot_sensitivity="low")
    after = {r[0]: r for r in w.config()}
    assert after[w.priya] == before[w.priya] and after[w.zed] == before[w.zed]
    if out["ok"]:
        # The bound read (task 3966): one field changed, the rest stayed.
        assert after[w.mel] == (*before[w.mel][:3], "low", *before[w.mel][4:]), after
    else:
        # The read of today sees no row and answers with the defaults.
        assert out["error"] == nw._UNPROVEN, out
        assert after[w.mel] == before[w.mel]
