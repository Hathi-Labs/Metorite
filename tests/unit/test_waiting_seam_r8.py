"""WS-AP AP-1a: the read seam of "Waiting" on a real database (R8).

Spec: ``project-docs/specs/waiting_on_me.md`` §7.3, §4.4 (V1, V2, V3 and V6)
and the row AP-1 of §8.

The real ``/shell`` router runs as the non-privileged role of the phase-4
rehearsal, on a database with the whole ladder and two organizations. Each
provider calls its app's own read, so each case seeds the app's own store:
``pm_decisions``, ``chat_pending_ask``, a Studio app request and
``pending_actions``.

* V1: two members of one org each see their own rows, and the count holds
  the caller's rows only.
* V2: no row for a task that the reader cannot open.
* V3: two orgs, and no row crosses. The org B rows name the SAME address as
  the org A member.
* The flag ``WAITING_ORGS`` (default empty): off, both routes answer 404.
* A provider that raises reads ``failed``, never empty.
* A member who is not an admin gets no ``held_write`` row.
* A ``chat_ask`` row holds no question text.

**Mutations this suite catches.** Commit first, and restore with
``git checkout -- <file>``.

* M1. ``waiting_allowed``: answer True always.
* M2. ``_held_writes``: register the provider with ``admin=False``.
* M3. ``_chat_asks``: read a ``None`` answer of ``_my_waiting_asks`` as an
  empty list. (The ``actor_email == me`` check is defence in depth: the read
  already filters on the member, so no case here can see it go.)
* M4. ``_chat_asks``: use the question text as the title.
* M5. ``_caller``: drop the agent check.

Run::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_waiting_seam_r8.py -v -rs
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.shell", reason="gateway not installed")

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

#: Every bound on a wait. Broken code goes red and never hangs.
_WAIT = 60

ONE = "one@waiting-r8.test"
TWO = "two@waiting-r8.test"
ADMIN = "admin@waiting-r8.test"
ASKER = "asker@waiting-r8.test"
BEE = "bee@waiting-r8-b.test"

#: Text that a model wrote. No answer may hold it.
SECRET = "Model text that must not show"

YES_NO = [{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No"}]
FEATURES = ("feature:projects", "feature:chat", "feature:approvals")
STUDIO_PKG = "11111111-2222-4333-8444-555555555555"


# ── The helpers ─────────────────────────────────────────────────────────────


def _principal(email: str, org: str, *, admin: bool = False, agent: Any = None,
               features: tuple[str, ...] = FEATURES) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    perms = [*features, *(["admin:members:read"] if admin else [])]
    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
                       organization_id=org, agent=agent)


def _app(users: dict[str, Any]) -> Any:
    """The real ``/shell`` router. The caller is the principal that the test
    header names, and the override binds the tenant from it. The tenant never
    comes from the request."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes.shell import router

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _caller
    return app


class _Client:
    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client = client
        self.users = users

    async def __call__(self, user: Any, path: str) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await asyncio.wait_for(
            self.client.get(path, headers={"x-test-as": key}), _WAIT)

    async def waiting(self, user: Any) -> dict[str, Any]:
        got = await self(user, "/shell/waiting?direction=for_you")
        assert got.status_code == 200, got.text
        return got.json()

    async def total(self, user: Any) -> dict[str, Any]:
        got = await self(user, "/shell/waiting/count")
        assert got.status_code == 200, got.text
        return got.json()


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(users)),
                              base_url="http://shell.test") as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


@contextlib.contextmanager
def _bound(org: str) -> Iterator[None]:
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _seed_member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": email, "o": org})


def _seed_task(p: Any, org: str, creator: str, *, grants: tuple[str, ...] = ()) -> str:
    project, status, task = (str(uuid.uuid4()) for _ in range(3))
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'waiting', :e, true)"), {"id": project, "o": org, "e": creator})
        for who in grants:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (:o, :p, :s, :e)"),
                {"o": org, "p": project, "s": who, "e": creator})
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, "
            "position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"),
            {"id": status, "o": org, "p": project})
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :p, 1, :s, 'Fix the extruder', :e)"),
            {"id": task, "o": org, "p": project, "s": status, "e": creator})
    return task


def _decision(p: Any, org: str, task: str, decider: str, question: str) -> str:
    """A decision row, written past the service guards on purpose: V2 needs a
    row for a decider who cannot open the task, which the ask refuses."""
    did = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_decisions (organization_id, id, subject_id, decider, options, "
            "question, asked_by, source) VALUES (CAST(:o AS uuid), CAST(:i AS uuid), "
            "CAST(:t AS uuid), :d, CAST(:opts AS jsonb), :q, 'system:align', 'align')"),
            {"o": org, "i": did, "t": task, "d": decider, "opts": json.dumps(YES_NO),
             "q": question})
    return did


def _chat_ask(p: Any, org: str, actor: str) -> str:
    """One waiting chat card in a thread with no chat row. The room layer
    treats such a thread as the caller's own."""
    thread = f"th-{uuid.uuid4().hex[:12]}"
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_pending_ask (organization_id, request_id, thread_id, "
            "actor_email, agent_name, kind, question) VALUES (CAST(:o AS uuid), :r, :t, "
            ":a, 'mira', 'ask_user', :q)"),
            {"o": org, "r": uuid.uuid4().hex, "t": thread, "a": actor, "q": SECRET})
    return thread


def _held_write(org: str, tag: str) -> str:
    from action_broker import AuthorityTier, enqueue, propose

    proposal = propose("agent:email-assistant", "crm.zoho_update", f"lead:{tag}",
                       {"args": {}}, authority=AuthorityTier.SUGGEST)
    with _bound(org):
        row_id = enqueue(proposal)
    assert row_id == str(proposal.id), "the enqueue wrote nothing"
    return row_id


async def _app_request(org: str, member: str) -> str:
    """One Studio app request, through the store's own open."""
    from acb_common.db import tenant_session
    from gateway import app_requests

    call = {"method": "POST", "template": "/studio/packages/{package}/actions/{action}",
            "path": f"/studio/packages/{STUDIO_PKG}/actions/approve_order",
            "query": [], "body": {"order": "PO-7"}, "if_match": None}
    with _bound(org):
        async with tenant_session(org) as db:
            opened = await app_requests.open_request(
                db, org=org, member=member, source="studio", app="studio",
                subject=STUDIO_PKG, op="approve_order", cls="C", call=call,
                idempotency_key=uuid.uuid4().hex, connection_id=None,
                via="studio:approve-order", title="Approve an order.", app_name="Orders")
    return opened.request_id


def _ids(answer: dict[str, Any], kind: str | None = None) -> set[str]:
    return {i["id"] for i in answer["items"] if kind is None or i["kind"] == kind}


# ── The world ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    p = promoted
    for email in (ONE, TWO, ADMIN, ASKER):
        _seed_member(p, p.org_a, email)
    with p.admin_engine.begin() as c:
        # Migration 222 grants the room check to `acb_app` only. The rehearsal
        # role has a suite-private name, so it gets the same grant here.
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    ns = SimpleNamespace(p=p)
    ns.task = _seed_task(p, p.org_a, ASKER, grants=(ASKER, ONE, TWO))
    ns.hidden = _seed_task(p, p.org_a, ASKER, grants=(ASKER,))
    _seed_member(p, p.org_b, BEE)
    ns.task_b = _seed_task(p, p.org_b, BEE, grants=(BEE, ONE))
    ns.one = _principal(ONE, p.org_a)
    ns.two = _principal(TWO, p.org_a)
    ns.admin = _principal(ADMIN, p.org_a, admin=True)
    # Org B: a member and admin of its own, and a session with the SAME
    # address as member one.
    ns.bee = _principal(BEE, p.org_b, admin=True)
    ns.one_b = _principal(ONE, p.org_b)
    ns.agent = _principal(ONE, p.org_a, agent=SimpleNamespace(connection_id="c"))
    return ns


@pytest.fixture
def w(world, app_engine, monkeypatch):  # noqa: F811
    """Every flag on, the sync sessions as the app role, and no row left from
    the last test."""
    from acb_common import get_settings
    from acb_graph import db as graph_db

    settings = get_settings()
    for name, value in (("waiting_orgs", "*"), ("decisions_orgs", "*"),
                        ("app_requests_orgs", "*"), ("chat_durable_asks", True),
                        ("agent_access_enabled", True), ("agent_runs_enabled", True)):
        monkeypatch.setattr(settings, name, value)
    factory = sessionmaker(bind=app_engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    with world.p.admin_engine.begin() as c:
        for table in ("pm_decisions", "chat_pending_ask", "pending_actions", "pm_agent_runs"):
            c.execute(text(f"DELETE FROM {table}"))
    return world


# ── The cases ───────────────────────────────────────────────────────────────


class TestTheWaitingSeam:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
        assert role is not None and not role[0] and not role[1]

    async def test_v1_each_member_sees_only_their_own_rows(self, w):
        p = w.p
        d_one = _decision(p, p.org_a, w.task, ONE, "Ship it today?")
        d_two = _decision(p, p.org_a, w.task, TWO, "Ship it tomorrow?")
        c_one = _chat_ask(p, p.org_a, ONE)
        c_two = _chat_ask(p, p.org_a, TWO)
        async with _session(p) as call:
            r_one = await _app_request(p.org_a, ONE)
            r_two = await _app_request(p.org_a, TWO)
            one = await call.waiting(w.one)
            two = await call.waiting(w.two)
            one_total = await call.total(w.one)
        mine = {f"decision:{d_one}", f"chat_ask:{c_one}", f"app_request:{r_one}"}
        theirs = {f"decision:{d_two}", f"chat_ask:{c_two}", f"app_request:{r_two}"}
        assert one["sources"] == {"decision": "ok", "chat_ask": "ok",
                                  "app_request": "ok", "held_write": "absent"}
        assert _ids(one) == mine
        assert _ids(two) == theirs
        # The count holds the caller's rows only.
        assert one_total["count"] == 3
        assert one_total["sources"] == one["sources"]
        # Each row has the shape of §8 AP-1, and each act is a link.
        for row in one["items"]:
            assert set(row) == {"id", "kind", "title", "waiter", "href", "since", "acts"}
            assert row["since"] and row["title"] and row["waiter"]
            assert row["acts"] and all(a["way"] == "open" and a["href"] for a in row["acts"])
        by_kind = {r["kind"]: r for r in one["items"]}
        assert by_kind["decision"]["title"] == "Ship it today?"
        assert by_kind["decision"]["href"] == f"/projects?task={w.task}"
        assert by_kind["app_request"]["title"] == "Approve an order."
        assert by_kind["app_request"]["waiter"] == "Studio"

    async def test_a_chat_ask_row_holds_no_question_text(self, w):
        p = w.p
        _chat_ask(p, p.org_a, ONE)
        async with _session(p) as call:
            answer = await call(w.one, "/shell/waiting")
        assert answer.status_code == 200
        assert SECRET not in answer.text
        (row,) = [r for r in answer.json()["items"] if r["kind"] == "chat_ask"]
        assert row["title"] == "An agent asked you a question in a chat"
        assert row["waiter"] == "mira"

    async def test_v2_no_row_for_a_task_the_reader_cannot_open(self, w):
        p = w.p
        seen = _decision(p, p.org_a, w.task, ONE, "Can open")
        hidden = _decision(p, p.org_a, w.hidden, ONE, "Cannot open")
        async with _session(p) as call:
            answer = await call.waiting(w.one)
            total = await call.total(w.one)
        assert _ids(answer, "decision") == {f"decision:{seen}"}
        assert f"decision:{hidden}" not in _ids(answer)
        assert "Cannot open" not in json.dumps(answer)
        assert total["count"] == 1

    async def test_v3_no_row_crosses_two_orgs(self, w):
        p = w.p
        a_decision = _decision(p, p.org_a, w.task, ONE, "Org A")
        a_chat = _chat_ask(p, p.org_a, ONE)
        a_write = _held_write(p.org_a, "a")
        # Org B rows that name the SAME address as member one of org A.
        b_same = {f"decision:{_decision(p, p.org_b, w.task_b, ONE, 'Org B same')}",
                  f"chat_ask:{_chat_ask(p, p.org_b, ONE)}"}
        # Org B rows of its own member, so the negative case is not empty.
        b_own = {f"decision:{_decision(p, p.org_b, w.task_b, BEE, 'Org B own')}",
                 f"chat_ask:{_chat_ask(p, p.org_b, BEE)}",
                 f"held_write:{_held_write(p.org_b, 'b')}"}
        async with _session(p) as call:
            a_request = await _app_request(p.org_a, ONE)
            b_same.add(f"app_request:{await _app_request(p.org_b, ONE)}")
            b_own.add(f"app_request:{await _app_request(p.org_b, BEE)}")
            in_a = await call.waiting(w.one)
            admin_a = await call.waiting(w.admin)
            in_b = await call.waiting(w.bee)
            one_in_b = await call.waiting(w.one_b)
        mine_a = {f"decision:{a_decision}", f"chat_ask:{a_chat}", f"app_request:{a_request}"}
        assert _ids(in_a) == mine_a
        assert _ids(admin_a, "held_write") == {f"held_write:{a_write}"}
        assert _ids(in_b) == b_own
        # The same address in an org B session reads org B rows only.
        assert not _ids(one_in_b) & (mine_a | {f"held_write:{a_write}"} | b_own)
        assert "Org A" not in json.dumps(in_b) + json.dumps(one_in_b)

    async def test_a_member_who_is_not_an_admin_gets_no_held_write(self, w):
        p = w.p
        row = _held_write(p.org_a, "admin-only")
        async with _session(p) as call:
            member = await call.waiting(w.one)
            member_total = await call.total(w.one)
            admin = await call.waiting(w.admin)
        assert member["sources"]["held_write"] == "absent"
        assert not _ids(member, "held_write")
        assert member_total["count"] == 0
        assert admin["sources"]["held_write"] == "ok"
        (got,) = [r for r in admin["items"] if r["kind"] == "held_write"]
        assert got["id"] == f"held_write:{row}"
        assert got["href"] == f"/approvals?action={row}"
        assert got["title"] == "Change a record in Zoho CRM"
        assert [a["class"] for a in got["acts"]] == ["C"]

    async def test_with_the_flag_off_both_routes_answer_404(self, w, monkeypatch):
        from acb_common import get_settings

        p = w.p
        _decision(p, p.org_a, w.task, ONE, "Hidden by the flag")
        async with _session(p) as call:
            for raw in ("", p.org_b):
                monkeypatch.setattr(get_settings(), "waiting_orgs", raw)
                for path in ("/shell/waiting", "/shell/waiting/count"):
                    got = await call(w.one, path)
                    assert got.status_code == 404, (raw, path)
                    assert "Hidden by the flag" not in got.text
                assert (await call(w.one, "/shell/waiting?direction=on_others")).status_code == 404, raw
            monkeypatch.setattr(get_settings(), "waiting_orgs", p.org_a.upper())
            assert (await call(w.one, "/shell/waiting")).status_code == 200

    async def test_a_signed_agent_and_another_direction_are_refused(self, w):
        async with _session(w.p) as call:
            for path in ("/shell/waiting", "/shell/waiting/count"):
                assert (await call(w.agent, path)).status_code == 404, path
            assert (await call(w.one, "/shell/waiting?direction=on_others")).status_code == 422

    async def test_a_provider_that_fails_reads_failed_never_empty(self, w, monkeypatch):
        from gateway.routes import chat
        from gateway.routes.projects import decisions

        p = w.p
        _decision(p, p.org_a, w.task, ONE, "Kept")
        _chat_ask(p, p.org_a, ONE)

        async def _broken_asks(org: str, me: str) -> None:
            return None  # the read's own answer for a database error

        async def _broken_list(**_kw: Any) -> dict[str, Any]:
            raise RuntimeError("the decisions read failed")

        monkeypatch.setattr(chat, "_my_waiting_asks", _broken_asks)
        async with _session(p) as call:
            answer = await call.waiting(w.one)
            assert answer["sources"]["chat_ask"] == "failed"
            assert answer["sources"]["decision"] == "ok"
            assert "chat_ask" not in answer["totals"]
            monkeypatch.setattr(decisions, "list_decisions", _broken_list)
            answer = await call.waiting(w.one)
            total = await call.total(w.one)
        assert answer["sources"]["decision"] == "failed"
        assert not _ids(answer, "decision")
        assert total["sources"]["decision"] == "failed"

    async def test_a_flag_that_is_off_reads_absent(self, w, monkeypatch):
        from acb_common import get_settings

        settings = get_settings()
        for name, value in (("decisions_orgs", ""), ("app_requests_orgs", ""),
                            ("chat_durable_asks", False)):
            monkeypatch.setattr(settings, name, value)
        async with _session(w.p) as call:
            answer = await call.waiting(w.one)
        assert answer["sources"] == {"decision": "absent", "chat_ask": "absent",
                                     "app_request": "absent", "held_write": "absent"}
        assert answer["items"] == []
