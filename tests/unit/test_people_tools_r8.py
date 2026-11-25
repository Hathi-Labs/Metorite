"""P-5b-1 (task 3114) and P-5b-2 (task 3943) — the people chat tools on a real
database (R8).

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

**Acceptance (P-5b-2).** Section d, at the end of the file: each self write
changes only the caller's own row, ``email`` never changes, and a member
cannot change another member's record through any tool.

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


def _app(w: _World) -> Any:
    """The real People routers, in the order of ``main.py``. The caller is the
    principal of ``X-User-Email``, and the wrapper binds that caller's org, as
    the auth layer does."""
    from gateway.routes.people import router, self_router

    app = FastAPI()
    app.include_router(self_router)
    app.include_router(router)

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


# ── d. The self writes (P-5b-2, task 3943, owner answer OD1 B) ──────────────
#
# Each write runs through the REAL self routes of ``selfservice.py`` as the
# member of the run, on FORCE RLS. The card answers approved. Acceptance:
#
# d1. Each write changes only the caller's own row.
# d2. A member cannot change the record of another member through any tool.
#     The tools send the self routes (``/people/me``) and take no person id.
# d3. ``email`` is unchanged after ``update_my_profile``.
# d4. A removal of another person's absence changes nothing.
# d5. A member with no ``feature:people`` still saves their own record.
# d6. An admin with ``admin:members:manage`` changes only their own row.


@pytest.fixture
def approve(monkeypatch) -> list[str]:
    """The card of the run answers approved, and records each title."""
    from acb_skills import ask_tools

    titles: list[str] = []

    async def card(title: str, *_a: Any, **_k: Any) -> bool:
        titles.append(title)
        return True

    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    return titles


def _seed_children(w: _World) -> dict[str, str]:
    """A skill, a credential and an absence for Priya (org A) and for Bob
    (org B), as the superuser. ``{name: absence id}``."""
    p = w.p
    out: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for email, org, name in ((w.priya, p.org_a, "priya"), (w.bob, p.org_b, "bob")):
            pid, o = w.ids[email], org
            c.execute(text(
                "INSERT INTO people_skills (organization_id, person_id, skill, level, evidence) "
                "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'modbus', 'expert', 'manual')"),
                {"o": o, "p": pid})
            c.execute(text(
                "INSERT INTO people_credentials (organization_id, person_id, kind, title) "
                "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'education', 'BEng')"),
                {"o": o, "p": pid})
            out[name] = str(c.execute(text(
                "INSERT INTO people_absences (organization_id, person_id, starts_on, ends_on, "
                "kind, created_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), "
                "DATE '2027-02-01', DATE '2027-02-03', 'away', 'test') RETURNING id"),
                {"o": o, "p": pid}).scalar())
    return out


def _state(w: _World) -> dict[str, Any]:
    """Each seeded person: the row, the skills, the credentials and the
    absences, read as the superuser."""
    out: dict[str, Any] = {}
    with w.p.admin_engine.begin() as c:
        for email, pid in w.ids.items():
            row = c.execute(text(
                "SELECT email, pronouns, phone, bio, languages, birthday, status, title "
                "FROM people WHERE id = CAST(:p AS uuid)"), {"p": pid}).mappings().one()
            skills = sorted(r[0] for r in c.execute(text(
                "SELECT skill || '/' || coalesce(level, '-') FROM people_skills "
                "WHERE person_id = CAST(:p AS uuid)"), {"p": pid}))
            creds = sorted(r[0] for r in c.execute(text(
                "SELECT kind || '/' || title FROM people_credentials "
                "WHERE person_id = CAST(:p AS uuid)"), {"p": pid}))
            away = sorted(str(r[0]) for r in c.execute(text(
                "SELECT id FROM people_absences WHERE person_id = CAST(:p AS uuid)"),
                {"p": pid}))
            out[email] = {"row": dict(row), "skills": skills, "credentials": creds,
                          "absences": away}
    return out


def _others(state: dict[str, Any], w: _World) -> dict[str, Any]:
    return {k: v for k, v in state.items() if k != w.mel}


def test_d1_d3_the_profile_write_changes_only_my_row_and_never_email(world, approve) -> None:
    w = world
    _seed_children(w)
    before = _state(w)
    out = w.act(w.mel, pt.update_my_profile, pronouns="she/her", phone="+44 20 7946 0000",
                languages=["English", "Hindi"], birthday="02-29", bio="Builds PLCs.")
    assert out["ok"] is True, out
    assert approve == ["Save these changes to your People record?"]
    after = _state(w)
    mine = after[w.mel]["row"]
    assert mine["pronouns"] == "she/her" and mine["phone"] == "+44 20 7946 0000"
    assert mine["languages"] == ["English", "Hindi"] and mine["birthday"] == "02-29"
    assert mine["email"] == w.mel, "d3: the email is unchanged"
    assert mine["status"] == before[w.mel]["row"]["status"]
    assert mine["title"] == before[w.mel]["row"]["title"]
    assert _others(after, w) == _others(before, w), "d1: no other row changed"


def test_d1_skills_credentials_and_absences_change_only_my_rows(world, approve) -> None:
    w = world
    _seed_children(w)
    before = _state(w)
    out = w.act(w.mel, pt.set_my_skills, ["python", {"skill": "rust", "level": "working"}])
    assert out["ok"] is True and out["count"] == 2, out
    out = w.act(w.mel, pt.set_my_credentials,
                [{"kind": "certification", "title": "AWS SA", "issuer": "Amazon"}])
    assert out["ok"] is True and out["count"] == 1, out
    made = w.act(w.mel, pt.add_my_absence, "2027-03-01", "2027-03-02", "holiday",
                 note="Family")
    assert made["ok"] is True, made
    after = _state(w)
    assert after[w.mel]["skills"] == ["python/-", "rust/working"]
    assert after[w.mel]["credentials"] == ["certification/AWS SA"]
    assert after[w.mel]["absences"] == [made["absence_id"]]
    assert _others(after, w) == _others(before, w), "no other person changed"
    gone = w.act(w.mel, pt.remove_my_absence, made["absence_id"])
    assert gone["ok"] is True, gone
    assert _state(w)[w.mel]["absences"] == []
    assert len(approve) == 4


def test_d2_d4_an_absence_of_another_person_is_refused_and_nothing_changes(
        world, approve) -> None:
    w = world
    ids = _seed_children(w)
    before = _state(w)
    for other in (ids["priya"], ids["bob"]):
        out = w.act(w.mel, pt.remove_my_absence, other)
        assert out == {"ok": False, "error": pt._W_NOT_MINE, "status": 404}, out
    assert approve == [], "no card for an absence that is not mine"
    assert _state(w) == before
    # Non-vacuity: the absence of Priya is still there, and Mel's own read sees none.
    assert before[w.priya]["absences"] == [ids["priya"]]


def test_d2_no_write_names_a_person_so_a_wrong_card_row_still_writes_only_mine(
        world, approve, monkeypatch) -> None:
    """Each write sends a self route (``/people/me``), and the server finds the
    row of the caller when it writes. So even a defect that reads Priya's row
    for the card lands the write on Mel's own row. Mutation: send an id route
    with the id of the row that the card read -> Priya's row changes -> red."""
    w = world
    _seed_children(w)
    before = _state(w)

    async def priya() -> dict[str, Any]:
        return {"id": w.ids[w.priya], "email": w.priya, "pronouns": None}

    monkeypatch.setattr(pt, "_own_row", priya)
    out = w.act(w.mel, pt.update_my_profile, pronouns="x/y")
    assert out["ok"] is True, out
    after = _state(w)
    assert after[w.mel]["row"]["pronouns"] == "x/y"
    assert _others(after, w) == _others(before, w), "Priya and org B are unchanged"


def test_d5_a_member_with_no_people_feature_saves_their_own_record(world, approve) -> None:
    """D-PC-15: your own row is not behind the directory's gate. A member who
    holds no ``feature:people`` saves the profile and an absence through the
    tools. Mutation: send the id routes behind ``feature:people`` -> 403 -> red."""
    w = world
    w.principal(w.mel, w.p.org_a, ["feature:tasks"])
    before = _state(w)
    # Non-vacuity: the gated directory refuses this member.
    gated = w.act(w.mel, pt.people_directory)
    assert gated["ok"] is False and gated["status"] == 403, gated
    out = w.act(w.mel, pt.update_my_profile, pronouns="she/her", phone="+44 1")
    assert out["ok"] is True, out
    made = w.act(w.mel, pt.add_my_absence, "2027-05-03", "2027-05-04", "away")
    assert made["ok"] is True, made
    after = _state(w)
    assert after[w.mel]["row"]["pronouns"] == "she/her"
    assert after[w.mel]["row"]["phone"] == "+44 1"
    assert after[w.mel]["row"]["email"] == w.mel
    assert after[w.mel]["absences"] == [made["absence_id"]]
    assert _others(after, w) == _others(before, w)


def test_d6_an_admin_who_may_manage_people_changes_only_their_own_row(
        world, approve) -> None:
    """An admin with ``admin:members:manage`` may edit any row on the page.
    Through the chat tools the admin still changes only their own row.
    Mutation: let a tool take a person id -> red."""
    w = world
    ids = _seed_children(w)
    w.principal(w.ada, w.p.org_a, ["feature:*", "admin:members:read", "admin:members:manage"])
    before = _state(w)
    for tool, args, kw in (
        (pt.update_my_profile, (), {"pronouns": "he/him", "bio": "Admin."}),
        (pt.set_my_skills, (["sql"],), {}),
        (pt.set_my_credentials, ([{"kind": "education", "title": "MBA"}],), {}),
        (pt.add_my_absence, ("2027-06-01", "2027-06-01"), {}),
    ):
        out = w.act(w.ada, tool, *args, **kw)
        assert out["ok"] is True, (tool.__name__, out)
    # The admin may read Priya's absences on the page, but the chat removal
    # reads the admin's own list, so Priya's id is not found.
    out = w.act(w.ada, pt.remove_my_absence, ids["priya"])
    assert out == {"ok": False, "error": pt._W_NOT_MINE, "status": 404}, out
    after = _state(w)
    assert after[w.ada]["row"]["pronouns"] == "he/him"
    assert after[w.ada]["skills"] == ["sql/-"]
    assert after[w.ada]["credentials"] == ["education/MBA"]
    assert len(after[w.ada]["absences"]) == 1
    assert {k: v for k, v in after.items() if k != w.ada} == \
        {k: v for k, v in before.items() if k != w.ada}, "no other row changed"
