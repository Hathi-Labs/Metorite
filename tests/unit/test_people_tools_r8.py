"""P-5b-1 (task 3114) — the people chat tools on a real database (R8).

Spec: ``project-docs/specs/chat_parity.md`` section 6.4 (the P-5b slices) and
``people_center_app.md`` section 6.5 (D-PC-10). Code: the chat tools of
``acb_skills.people_tools``, and the REAL People routers
(``gateway/routes/people/``: ``self_router`` first, then the gated directory
router, as ``main.py`` includes them). The hermetic twin is
``tests/unit/test_people_tools.py``.

The tool's HTTP calls reach the real routers through ``httpx.ASGITransport``.
The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds each statement. A small ASGI
wrapper binds the org of the caller, as the auth layer does. The caller comes
from ``X-User-Email``, the header that the tool sends for the run's member.
No tool takes an org or a caller.

Org A has Ada (an admin, with ``admin:members:read``) and Mel (a member with
``feature:*`` only), and a person, Priya, with skills, an employee id and a
phone. Org B has Zed (a member) and a person, Bob.

**Acceptance (P-5b-1).**

a. Mel reads the directory and gets no HR field and no private field. Ada
   gets Priya's skills and employee id, so the check is not vacuous.
b. A person id of org B gives Mel the plain not-found words. Zed lists no
   person of org A.
c. ``people_me`` returns only the row of the caller.

**Acceptance (P-5b-5, task 3946).** The REAL Reach router
(``gateway.reach.routes``) answers the status, the preview and the send.

d. Mel of org A approves the card, and her brief goes to her own link only.
   Zed of org B has no link in org B. A link row for his address in org A does
   not reach him: the tool shows no card and sends no request, and a direct
   ``POST /reach/brief`` as Zed answers ``no_link``. A member that Reach does
   not allow gets 404, and the tool says "Reach is off for you."

**No test here reaches WhatsApp.** The fixture ``reach_world`` stubs the
provider factory, the thread record and the bot credential read, and it sets
no flag or credential of the box. The allow rule and the channel switch are
stubbed as functions, so the denial of Zed comes from the data of his org.
``test_people_tools.py`` holds that by its structural test.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_people_tools_r8.py -v -rs -p no:randomly

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.people", reason="gateway not installed")

import httpx
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common.db import bind_tenant, release_tenant
from acb_skills import people_tools as pt
from fastapi import FastAPI, Request
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

#: An admin who may read the HR half (``admin:members:read``).
ADMIN_PERMS = ["feature:*", "admin:members:read"]
#: A member: ``feature:*`` only (migration 207).
MEMBER_PERMS = ["feature:*"]
#: The HR tier and the private tier (``routes/tasks/people.py``).
HR_KEYS = ("skills", "employee_id", "capacity_hours_per_week", "resume_summary")
PRIVATE_KEYS = ("phone", "emergency_contact", "personal_email", "birthday")
_REAL_CLIENT = httpx.AsyncClient


def _rows(block: str) -> Any:
    """The JSON inside one framed block of a tool result."""
    return json.loads(block.split("\n", 1)[1].rsplit("\n", 1)[0])


class _World:
    """Two orgs of people, and the caller of the next call."""

    def __init__(self, promoted: Any) -> None:  # noqa: F811
        self.p = promoted
        self.tag = uuid.uuid4().hex[:8]
        self.ada = f"ada.{self.tag}@p5b-a.example.test"
        self.mel = f"mel.{self.tag}@p5b-a.example.test"
        self.priya = f"priya.{self.tag}@p5b-a.example.test"
        self.zed = f"zed.{self.tag}@p5b-b.example.test"
        self.bob = f"bob.{self.tag}@p5b-b.example.test"
        self.users: dict[str, UserContext] = {}
        self.ids: dict[str, str] = {}
        self.member = self.mel

    @property
    def url(self) -> str:
        return self.p.app_url.render_as_string(hide_password=False)

    def principal(self, email: str, org: str, perms: list[str]) -> None:
        self.users[email] = UserContext(email=email, role=UserRole.EMPLOYEE,
                                        organization_id=org, access=build_access(perms))

    def act(self, who: str, tool: Callable[..., Awaitable[dict[str, Any]]],
            *args: Any, **kw: Any) -> dict[str, Any]:
        """The chat tool, as the member *who* of the run."""
        self.member = who

        async def go() -> dict[str, Any]:
            async with tenant_engine_scope(self.url):
                return await tool(*args, **kw)
        return asyncio.run(go())


def _app(w: _World, *, reach: bool = False) -> Any:
    """The real People routers, in the order of ``main.py``. The caller is the
    principal of ``X-User-Email``, and the wrapper binds that caller's org, as
    the auth layer does. *reach* adds the real Reach router (P-5b-5)."""
    from gateway.routes.people import router, self_router

    app = FastAPI()
    app.include_router(self_router)
    app.include_router(router)
    if reach:
        from gateway.reach.routes import router as reach_router

        app.include_router(reach_router)

    def who(request: Request) -> UserContext:
        return w.users[request.headers.get("x-user-email", "")]

    app.dependency_overrides[get_current_user] = who

    async def bound(scope: Any, receive: Any, send: Any) -> None:
        caller = dict(scope.get("headers") or []).get(b"x-user-email", b"").decode()
        user = w.users.get(caller)
        token = bind_tenant(user.organization_id) if user is not None else None
        try:
            await app(scope, receive, send)
        finally:
            if token is not None:
                release_tenant(token)

    return bound


def _seed(w: _World) -> None:
    """The people of both orgs, as the superuser (it escapes RLS)."""
    p = w.p
    rows = (
        (w.ada, p.org_a, "Ada", None, None, None),
        (w.mel, p.org_a, "Mel", None, None, None),
        (w.priya, p.org_a, "Priya", ["modbus", "plc"], "E-17", "+1 555 0100"),
        (w.zed, p.org_b, "Zed", None, None, None),
        (w.bob, p.org_b, "Bob of org B", ["welding"], "B-9", None),
    )
    with p.admin_engine.begin() as c:
        for email, org, name, skills, emp, phone in rows:
            pid = c.execute(text(
                "INSERT INTO people (id, name, email, title, status, skills, employee_id, "
                "phone, capacity_hours_per_week, source, source_key, organization_id, "
                "updated_by, updated_at) VALUES (gen_random_uuid(), :n, :e, 'Engineer', "
                "'active', CAST(:s AS text[]), :emp, :ph, 40, 'manual', :k, "
                "CAST(:o AS uuid), 'test', now()) RETURNING id"),
                {"n": name, "e": email, "s": skills or [], "emp": emp, "ph": phone,
                 "k": f"manual:{email}:{org}", "o": org}).scalar()
            w.ids[email] = str(pid)
    w.principal(w.ada, p.org_a, ADMIN_PERMS)
    w.principal(w.mel, p.org_a, MEMBER_PERMS)
    w.principal(w.zed, p.org_b, MEMBER_PERMS)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    w = _World(promoted)
    _seed(w)
    transport = httpx.ASGITransport(app=_app(w))
    monkeypatch.setattr(pt.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=transport, **kw))
    monkeypatch.setattr(pt, "_current_user_email", lambda: w.member)
    yield w
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM people WHERE lower(email) LIKE :t"),
                  {"t": f"%.{w.tag}@p5b-%"})


def _ours(out: dict[str, Any], w: _World) -> dict[str, dict[str, Any]]:
    """The rows of the directory result that this test seeded, by address."""
    return {r["email"]: r for r in _rows(out["rows"])
            if isinstance(r.get("email"), str) and w.tag in r["email"]}


# ── a. The HR tier follows the right of the caller ──────────────────────────


def test_a_a_member_without_the_hr_right_gets_no_hr_field(world) -> None:
    w = world
    out = w.act(w.mel, pt.people_directory, limit=50)
    assert out["ok"] is True and out["hr_visible"] is False, out
    rows = _ours(out, w)
    assert set(rows) == {w.ada, w.mel, w.priya}, sorted(rows)
    for email, row in rows.items():
        for key in HR_KEYS + PRIVATE_KEYS:
            assert key not in row, (email, key, row.get(key))
    assert "modbus" not in out["rows"] and "E-17" not in out["rows"]
    assert "555 0100" not in out["rows"]


def test_a_the_skill_filter_is_not_an_oracle_for_a_member(world) -> None:
    """The server drops the HR filter for Mel, and the tool says so."""
    w = world
    out = w.act(w.mel, pt.people_directory, skill="modbus", limit=50)
    assert out["ok"] is True and "admin:members:read" in out["note"]
    assert set(_ours(out, w)) == {w.ada, w.mel, w.priya}


def test_a_the_admin_gets_the_hr_fields_non_vacuity(world) -> None:
    w = world
    out = w.act(w.ada, pt.people_directory, query="Priya")
    assert out["ok"] is True and out["hr_visible"] is True, out
    rows = _ours(out, w)
    assert set(rows) == {w.priya}
    assert rows[w.priya]["skills"] == ["modbus", "plc"]
    assert rows[w.priya]["employee_id"] == "E-17"
    assert "phone" not in rows[w.priya], "the private tier needs the manage right"


# ── b. Another org ──────────────────────────────────────────────────────────


def test_b_a_person_of_another_org_gives_plain_not_found(world) -> None:
    w = world
    out = w.act(w.mel, pt.people_work, w.ids[w.bob])
    assert out == {"ok": False, "error": pt._NOT_FOUND, "status": 404}, out
    # Non-vacuity: a person of the caller's own org is found.
    mine = w.act(w.mel, pt.people_work, w.ids[w.priya])
    assert mine["ok"] is True, mine


def test_b_org_b_lists_no_person_of_org_a(world) -> None:
    w = world
    out = w.act(w.zed, pt.people_directory, limit=50)
    assert out["ok"] is True
    assert set(_ours(out, w)) == {w.zed, w.bob}
    assert "Priya" not in out["rows"] and w.mel not in out["rows"]


# ── c. My own row ───────────────────────────────────────────────────────────


def test_c_people_me_returns_only_the_callers_row(world) -> None:
    w = world
    out = w.act(w.mel, pt.people_me)
    assert out["ok"] is True and out["state"] == "resolved", out
    person = _rows(out["person"])
    assert person["email"] == w.mel and person["name"] == "Mel"
    assert person["id"] == w.ids[w.mel]
    for other in (w.ada, w.priya, w.zed, w.bob):
        assert other not in out["person"]
    zed = w.act(w.zed, pt.people_me)
    assert _rows(zed["person"])["email"] == w.zed and w.mel not in zed["person"]


# ── d. The send of my brief (P-5b-5, task 3946) ─────────────────────────────


class _Provider:
    """A WhatsApp provider that records each send and sends nothing."""

    def __init__(self) -> None:
        self.sends: list[tuple[str, str, str]] = []

    async def send_text(self, wa_id: str, body: str) -> str:
        self.sends.append(("text", wa_id, body))
        return "wamid.p5b5.stub"

    async def send_template(self, wa_id: str, name: str, language: str,
                            components: Any = None) -> str:
        self.sends.append(("template", wa_id, name))
        return "wamid.p5b5.stub"


class _ReachWorld:
    """The world of the send: the provider, the cards and the thread records."""

    def __init__(self, w: _World, transport: httpx.ASGITransport) -> None:
        self.w = w
        self.transport = transport
        self.provider = _Provider()
        self.cards: list[dict[str, Any]] = []
        self.records: list[tuple[str, str]] = []
        self.phone_a = "91" + str(uuid.uuid4().int)[:10]
        self.phone_z = "92" + str(uuid.uuid4().int)[:10]
        self.allowed: Callable[[str, str], bool] = lambda org, email: True

    def send(self, who: str) -> dict[str, Any]:
        """``reach_send_brief`` as the VERIFIED member *who* of the run."""
        from acb_common import bind_run_context, run_context_scope
        from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

        self.w.member = who

        async def go() -> dict[str, Any]:
            async with tenant_engine_scope(self.w.url):
                with run_context_scope(), artifact_context_scope():
                    bind_run_context(run_id="r-p5b5-r8", user=who, member_verified=True)
                    bind_artifact_context(session_id="s-p5b5-r8", member=who,
                                          agent_name="orchestrator", run_id="r-p5b5-r8")
                    return await pt.reach_send_brief()
        return asyncio.run(go())

    def post(self, who: str) -> httpx.Response:
        """A direct ``POST /reach/brief`` as *who*, with no tool and no card."""

        async def go() -> httpx.Response:
            async with tenant_engine_scope(self.w.url), _REAL_CLIENT(
                    transport=self.transport, base_url="http://gateway.test") as client:
                return await client.post("/reach/brief", headers={"X-User-Email": who})
        return asyncio.run(go())


def _outbox(p: Any, member: str) -> list[dict[str, Any]]:
    with p.admin_engine.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT organization_id::text AS org, state, reason, regime FROM reach_outbox "
            "WHERE member_email = :m ORDER BY created_at"), {"m": member}).mappings()]


def _wipe_reach(p: Any, members: tuple[str, ...]) -> None:
    with p.admin_engine.begin() as c:
        for table in ("reach_outbox", "reach_signal_log", "whatsapp_bot_messages",
                      "whatsapp_member_links"):
            c.execute(text(f"DELETE FROM {table} WHERE member_email = ANY(:m)"),
                      {"m": list(members)})


@pytest.fixture
def reach_world(world, monkeypatch):
    """The real Reach router over the real database, with each send stubbed.

    The stubs: the provider factory, the thread record and the bot credential
    read. The allow rule and the channel switch are stubbed as functions, so
    no flag of the box is set. Mel of org A has a link with an open window.
    Zed of org B has no link in org B, and a link row for his address sits in
    org A."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from acb_skills import ask_tools
    from gateway.reach import flags as reach_flags
    from gateway.reach import loop
    from gateway.reach import whatsapp as channel
    from gateway.routes.shell import state
    from gateway.routes.whatsapp_channel import flags as wa_flags
    from whatsapp_ingestion.providers import factory

    w = world
    p = w.p
    transport = httpx.ASGITransport(app=_app(w, reach=True))
    rw = _ReachWorld(w, transport)
    monkeypatch.setattr(pt.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=transport, **kw))
    # The stubs of the send. Nothing below them can reach a phone.
    monkeypatch.setattr(factory, "build_provider", lambda kind, creds: rw.provider)

    async def record(org: str, member: str, wa_id: str, out_id: Any, body: str,
                     outbox_id: str) -> None:
        rw.records.append((org, member))

    monkeypatch.setattr(channel, "_record", record)
    monkeypatch.setattr(wa_flags, "bot_credentials", lambda: {"stub": "no credential"})
    monkeypatch.setattr(wa_flags, "org_allowed", lambda org: True)
    monkeypatch.setattr(channel, "channel_ready", lambda org: True)
    monkeypatch.setattr(reach_flags, "member_allowed",
                        lambda org, email: rw.allowed(str(org or ""), str(email or "")))
    # My Day is not the subject here: one overdue task card.
    day = state.Day(ZoneInfo("Asia/Kolkata"), 9, 18)
    card = {"signal_id": "tasks:p5b5", "app": "tasks", "kind": "overdue",
            "group": "needs_you", "score": 50, "title": "Ship the quote", "facts": {},
            "fingerprint": "fp0p5b05", "state": "new", "at": None, "why": "Overdue"}

    async def snapshot(org: str, user: Any, now: Any = None) -> Any:
        return loop.Snapshot([dict(card)], now or datetime.now(UTC), day, True, True)

    monkeypatch.setattr(loop, "snapshot", snapshot)

    async def confirm(title: str, detail: str = "", context: str = "", **kw: Any) -> bool:
        rw.cards.append({"title": title, "context": context, **kw})
        return True

    monkeypatch.setattr(ask_tools, "request_confirmation", confirm)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    members = (w.mel, w.zed)
    _wipe_reach(p, members)
    with p.admin_engine.begin() as c:
        for org, member, phone in ((p.org_a, w.mel, rw.phone_a),
                                   (p.org_a, w.zed, rw.phone_z)):
            c.execute(text(
                "INSERT INTO whatsapp_member_links (organization_id, member_email, wa_id, "
                "status, is_current, linked_at) VALUES (CAST(:o AS uuid), :m, :wa, "
                "'active', true, now())"), {"o": org, "m": member, "wa": phone})
            c.execute(text(
                "INSERT INTO whatsapp_bot_messages (organization_id, member_email, wa_id, "
                "wamid, direction, state) VALUES (CAST(:o AS uuid), :m, :wa, :wid, 'in', "
                "'replied')"), {"o": org, "m": member, "wa": phone,
                                "wid": f"wamid.p5b5.{w.tag}.{phone}"})
    yield rw
    _wipe_reach(p, members)


def test_d_an_approved_card_sends_my_brief_to_my_own_link(reach_world) -> None:
    """Non-vacuity of the case below: the stubbed provider gets Mel's brief."""
    rw = reach_world
    w = rw.w
    out = rw.send(w.mel)
    assert out["sent"] is True and out["answer"] == "Sent. Check your WhatsApp.", out
    assert len(rw.cards) == 1 and rw.cards[0]["responder"] == w.mel
    assert pt.SEND_CARD_OPEN in rw.cards[0]["context"]
    assert [(kind, wa) for kind, wa, _body in rw.provider.sends] == [("text", rw.phone_a)]
    assert rw.records == [(w.p.org_a, w.mel)]
    assert _outbox(w.p, w.mel) == [{"org": w.p.org_a, "state": "sent", "reason": None,
                                    "regime": "free_text"}]


def test_d_a_member_of_org_b_cannot_trigger_a_send(reach_world) -> None:
    """Zed of org B has a link row in org A only. The tool shows no card and
    sends no request. A direct POST answers ``no_link`` and sends nothing."""
    rw = reach_world
    w = rw.w
    out = rw.send(w.zed)
    assert out == {"ok": False, "error": pt._NOT_LINKED, "sent": False}, out
    assert rw.cards == [] and rw.provider.sends == []
    assert _outbox(w.p, w.zed) == []
    got = rw.post(w.zed)
    assert got.status_code == 200 and got.json() == {"state": "skipped",
                                                     "reason": "no_link"}
    assert rw.provider.sends == [] and rw.records == []
    # The row of the direct POST is in Zed's own org, and it says no_link.
    assert _outbox(w.p, w.zed) == [{"org": w.p.org_b, "state": "skipped",
                                    "reason": "no_link", "regime": None}]


def test_d_reach_off_for_a_member_is_404_and_plain_words(reach_world) -> None:
    rw = reach_world
    w = rw.w
    rw.allowed = lambda org, email: org == w.p.org_a
    out = rw.send(w.zed)
    assert out == {"ok": False, "error": pt._REACH_OFF, "sent": False}, out
    assert rw.post(w.zed).status_code == 404
    assert rw.cards == [] and rw.provider.sends == [] and _outbox(w.p, w.zed) == []
