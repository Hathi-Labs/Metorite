"""WS-ALN ALN-5a — the rules of a project on a real Postgres (R8).

Spec: ``project-docs/specs/project_alignment.md`` §4.2 and §7.2. Code:
``apps/services/gateway/gateway/routes/project_rules.py`` (the one writer of
a rule row) and two helpers of ``gateway/routes/knowledge/core.py``
(``page_version_text`` and ``quote_occurs``). Migration: the file that adds
the rule CHECKs to ``pm_agent_guidance``, found by content (R1).

The real ``/project-rules`` router, the non-privileged role of the phase-4
rehearsal (NOBYPASSRLS), two organizations, real Knowledge pages. A hermetic
fake agrees with any SQL it gets, so these cases run here:

1. A non-lead write gets 403 and no row. A lead gets 201.
2. Only the accept route makes a proposal active.
3. Org B reads none of org A, and FORCE RLS holds.
4. A quote that is not in the page version gets 422 and no row.
5. A true quote on version N stays valid after the page changes.
6. A reader who cannot open the page gets no quote.
7. Each change adds a row, and the old row stays (an UPDATE raises).
8. The 41st active rule of a project gets 409.
10. A personal tree gets 403.
11. With the flag off, each route answers 404.

Case 9 (a guidance read is byte-identical with rule rows present) is in
``test_agent_guidance_r8.py``. The static fences are in
``test_project_rules_static.py``.

Run, on a PRIVATE database (one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_project_rules.py -v -rs

Without the database each test SKIPS, and the run reads green. A skip is not
a pass.

Mutations (R7), each turns the named test red. ``R`` is
``apps/services/gateway/gateway/routes/project_rules.py``.

* R1. ``R`` ``_assert_writable``: skip ``assert_may_write_guidance``.
  ``test_a_lead_writes_a_rule_and_a_member_gets_403``.
* R2. ``R`` ``_checked_source``: skip the ``quote_occurs`` check.
  ``test_a_quote_that_is_not_in_the_version_is_refused``.
* R3. ``R`` ``_rule_out``: ``hidden = False``.
  ``test_a_reader_who_cannot_open_the_page_gets_no_quote``.
* R4. ``R`` ``create_rule``: skip ``_refuse_over_cap``. ``test_the_41st_active_rule_gets_409``.
* R5. ``R`` ``_change``: drop the ``_lock_project`` call, and ``create_rule``
  too. ``test_concurrent_creates_hold_the_cap``.
* R6. ``R`` ``require_project_rules``: never raise. ``test_flag_off_answers_404_for_each_route``.
* R7. The migration: drop the ``OLD.kind = 'rule'`` arm of the guard.
  ``test_each_change_adds_a_row_and_the_old_row_stays``.
* R8. The migration: drop the CHECK ``pm_agent_guidance_quote_source``.
  ``test_the_database_refuses_a_rule_row_of_a_bad_shape``.
* R9. ``generated/04_policies.sql``: the policy of ``pm_agent_guidance``
  ``USING (true)``. ``test_org_b_reads_none_of_org_a``.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from fastapi import Request
from gateway.routes import project_rules
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401 — fixtures
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

#: The members. One domain, so each address is plainly a test address.
LEAD = "lead@rules.test"
LEAD_O = "lead-o@rules.test"
MEMBER = "member@rules.test"
ADMIN = "admin@rules.test"
B_LEAD = "lead-b@rules.test"

SEE_ALL = ("feature:projects", "data:org:read")
PROJECTS_ADMIN = (*SEE_ALL, "projects:settings:write")

#: The words of the open page. Version 1 has a CRLF, so the check must make
#: the line ends one form. Version 2 has other words.
V1 = "# Billing\r\nEach billing change needs a migration plan.\r\nKeep the old column."
V2 = "# Billing\nBilling changes need a review by the lead."
TRUE_QUOTE = "Each billing change needs a migration plan.\nKeep the old column."
CLOSED_TEXT = "Closed rule: the vendor list stays private."
SECRET_QUOTE = "the vendor list stays private"


# ── The members and the app ─────────────────────────────────────────────────


def _principal(email: str, org: str, *, perms: tuple[str, ...], agent: bool = False) -> Any:
    from acb_auth import AgentScope, UserContext, UserRole, build_access

    scope = None
    if agent:
        scope = AgentScope(
            connection_id=str(uuid.uuid4()), scopes=("projects:read", "projects:write"),
            include_personal=False, client_origin="https://claude.ai",
            actor_via="mcp:claude.ai:claude-code",
        )
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org, agent=scope,
    )


def _app(users: dict[str, Any]) -> Any:
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(project_rules.router)
    app.dependency_overrides[get_current_user] = _caller
    return app


class _Client:
    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client, self.users = client, users

    async def __call__(self, user: Any, method: str, path: str,
                       body: dict | None = None) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await self.client.request(method, path, json=body, headers={"x-test-as": key})


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)), base_url="http://rules.test",
            ) as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


# ── The seed ────────────────────────────────────────────────────────────────


def _project(c: Any, org: str, name: str, *, parent: str | None = None, lead: str | None = None,
             personal_owner: str | None = None, archived: bool = False) -> str:
    pid = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
        "parent_project_id, lead, personal_owner, status, archived_at) "
        "VALUES (:id, :o, :n, 'seed@rules.test', :own, CAST(:par AS uuid), :lead, :po, "
        ":st, CASE WHEN :arch THEN now() END)"),
        {"id": pid, "o": org, "n": name, "own": parent is None, "par": parent, "lead": lead,
         "po": personal_owner, "st": "archived" if archived else "active", "arch": archived})
    return pid


def _user(c: Any, org: str, email: str) -> None:
    c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
        {"e": email, "o": org})


def _space(c: Any, org: str, slug: str, grants: list[tuple[str, str]], *,
           kind: str = "team", owner: str | None = None) -> str:
    sid = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO kb_spaces (id, organization_id, slug, name, kind, owner_email) "
        "VALUES (:id, :o, :s, :s, :k, :ow)"),
        {"id": sid, "o": org, "s": slug, "k": kind, "ow": owner})
    for subject, level in grants:
        c.execute(text(
            "INSERT INTO kb_space_grants (organization_id, space_id, subject, level, created_by) "
            "VALUES (:o, :sp, :su, :l, 'seed@rules.test')"),
            {"o": org, "sp": sid, "su": subject, "l": level})
    return sid


def _page(c: Any, org: str, space: str, title: str, versions: list[str], *,
          state: str = "published", author: str = LEAD, history_from: int = 1) -> str:
    pid = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO kb_pages (id, organization_id, space_id, title, body_md, state, "
        "author_email, published_at, history_from) VALUES (:id, :o, :sp, :t, :b, :st, :au, "
        "CASE WHEN CAST(:st AS text) = 'published' THEN now() END, :hf)"),
        {"id": pid, "o": org, "sp": space, "t": title, "b": versions[-1], "st": state,
         "au": author, "hf": history_from})
    for n, body in enumerate(versions, start=1):
        _version(c, org, pid, n, body, title=title, author=author)
    return pid


def _version(c: Any, org: str, page: str, n: int, body: str, *, title: str = "Page",
             author: str = LEAD) -> None:
    c.execute(text(
        "INSERT INTO kb_page_versions (organization_id, page_id, version, body_md, title, "
        "author_email, reason) VALUES (:o, :p, :v, :b, :t, :a, :r)"),
        {"o": org, "p": page, "v": n, "b": body, "t": title, "a": author,
         "r": "create" if n == 1 else "edit"})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Org A: a space S led by LEAD, P1 and P_CAP below it, an archived
    project, the personal tree of MEMBER, and a space O led by LEAD_O. Four
    Knowledge spaces: OPEN (an org grant), CLOSED (LEAD only), the `me` space
    of LEAD, and the pages in them. Org B: one space with a lead and a page."""
    a, b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, a=a, b=b)
    with promoted.admin_engine.begin() as c:
        for email in (LEAD, LEAD_O, MEMBER, ADMIN):
            _user(c, a, email)
        _user(c, b, B_LEAD)
        ns.space = _project(c, a, "SECRET SPACE NAME", lead=LEAD)
        ns.p1 = _project(c, a, "Project one", parent=ns.space)
        ns.p_cap = _project(c, a, "Project at the cap", parent=ns.space)
        ns.p_race = _project(c, a, "Project of the race", parent=ns.space)
        ns.archived = _project(c, a, "Old", parent=ns.space, archived=True)
        ns.personal = _project(c, a, "Mine", personal_owner=MEMBER)
        ns.other = _project(c, a, "Other space", lead=LEAD_O)
        ns.b_space = _project(c, b, "B space", lead=B_LEAD)

        ns.k_open = _space(c, a, "rules-open", [("org", "read")])
        ns.k_closed = _space(c, a, "rules-closed", [(LEAD, "read")])
        ns.k_me = _space(c, a, "me", [], kind="me", owner=LEAD)
        ns.open = _page(c, a, ns.k_open, "Billing rules", [V1, V2])
        ns.closed = _page(c, a, ns.k_closed, "Vendor rules", [CLOSED_TEXT])
        ns.draft = _page(c, a, ns.k_open, "Draft rules", ["Draft words here."], state="draft")
        ns.mine = _page(c, a, ns.k_me, "My rules", ["My own words here."])
        ns.moved = _page(c, a, ns.k_open, "Moved rules",
                         ["Private draft words.", "Public words now."], history_from=2)
        ns.k_b = _space(c, b, "rules-b", [("org", "read")])
        ns.page_b = _page(c, b, ns.k_b, "B rules", ["Org B words."], author=B_LEAD)

    ns.lead = _principal(LEAD, a, perms=SEE_ALL)
    ns.lead_o = _principal(LEAD_O, a, perms=SEE_ALL)
    ns.member = _principal(MEMBER, a, perms=SEE_ALL)
    ns.admin = _principal(ADMIN, a, perms=PROJECTS_ADMIN)
    ns.agent = _principal(LEAD, a, perms=PROJECTS_ADMIN, agent=True)
    ns.b_lead = _principal(B_LEAD, b, perms=SEE_ALL)
    return ns


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "project_rules_orgs", "*")


def _rows(w: Any, sql: str, **params: Any) -> list[Any]:
    with w.p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params))


def _rule_rows(w: Any) -> list[Any]:
    return _rows(w, "SELECT * FROM pm_agent_guidance WHERE kind = 'rule' "
                    "ORDER BY rule_key, version")


def _reset(w: Any) -> None:
    """Each test starts with no rule and no guidance."""
    with w.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_guidance"))


def _projects(project: str) -> str:
    return f"/project-rules/projects/{project}"


def _rule(key: str, tail: str = "") -> str:
    return f"/project-rules/rules/{key}{tail}"


async def _create(call: _Client, user: Any, project: str, body: str = "Each change has a plan.",
                  **extra: Any) -> httpx.Response:
    return await call(user, "POST", _projects(project), {"body": body, **extra})


async def _list(call: _Client, user: Any, project: str, state: str | None = None) -> list[dict]:
    path = _projects(project) + (f"?state={state}" if state else "")
    r = await call(user, "GET", path)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


def _seed_rule(w: Any, project: str, *, state: str = "active", org: str | None = None) -> str:
    key = str(uuid.uuid4())
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_guidance (organization_id, project_id, kind, rule_key, "
            "version, body, written_by, level, state, proposed_by) VALUES (:o, :p, 'rule', "
            ":k, 1, 'A seeded rule.', 'seed@rules.test', 'soft', :s, 'seed@rules.test')"),
            {"o": org or w.a, "p": project, "k": key, "s": state})
    return key


# ── Case 1: only a lead writes ──────────────────────────────────────────────


async def test_a_lead_writes_a_rule_and_a_member_gets_403(world) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        member = await _create(call, w.member, w.p1)
        other_lead = await _create(call, w.lead_o, w.p1)
        agent = await _create(call, w.agent, w.p1)
        assert _rule_rows(w) == []
        hand = await _create(call, w.lead, w.p1, "Each billing change needs a plan.")
        proposed = await _create(call, w.lead, w.p1, "A proposal.", state="proposed")
        by_admin = await _create(call, w.admin, w.p1, "An admin rule.", level="hard")
        on_space = await _create(call, w.lead, w.space, "A rule of the space.")
        unknown = await _create(call, w.lead, str(uuid.uuid4()))
        spoofed = await _create(call, w.lead, w.p1, written_by="mira",
                                organization_id=w.b)
    assert member.status_code == 403, member.text
    assert other_lead.status_code == 403 and agent.status_code == 403
    assert agent.json()["detail"] == project_rules.AGENT_WRITE_REFUSED
    assert hand.status_code == 201, hand.text
    got = hand.json()
    assert (got["state"], got["level"], got["version"]) == ("active", "soft", 1)
    assert got["proposed_by"] == LEAD and got["written_by"] == LEAD
    assert got["quote"] is None and got["quote_hidden"] is False
    assert proposed.status_code == 201 and proposed.json()["state"] == "proposed"
    assert by_admin.status_code == 201 and by_admin.json()["level"] == "hard"
    assert on_space.status_code == 201
    assert unknown.status_code == 404
    # A body key for the actor or the tenant is refused, never used (R11).
    assert spoofed.status_code == 422
    rows = _rule_rows(w)
    assert len(rows) == 4
    assert {str(r.organization_id) for r in rows} == {w.a}
    assert {r.written_by for r in rows} == {LEAD, ADMIN}


# ── Case 2: only accept makes a proposal active ─────────────────────────────


async def test_only_accept_makes_a_proposal_active(world) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        made = await _create(call, w.lead, w.p1, "Proposal one.", state="proposed")
        key = made.json()["rule_key"]
        active_before = await _list(call, w.member, w.p1)
        proposed = await _list(call, w.member, w.p1, "proposed")
        by_member = await call(w.member, "POST", _rule(key, "/accept"))
        edited = await call(w.lead, "PATCH", _rule(key), {"body": "Proposal one, edited."})
        hard = await call(w.lead, "POST", _rule(key, "/level"), {"level": "hard"})
        still = await _list(call, w.member, w.p1)
        accepted = await call(w.lead, "POST", _rule(key, "/accept"))
        again = await call(w.lead, "POST", _rule(key, "/accept"))
        active_after = await _list(call, w.member, w.p1)
        history = await call(w.member, "GET", _rule(key, "/history"))
        bad_state = await _create(call, w.lead, w.p1, "Born retired.", state="retired")
    assert made.status_code == 201
    assert active_before == [] and [r["rule_key"] for r in proposed] == [key]
    assert by_member.status_code == 403
    assert edited.json()["state"] == "proposed" and edited.json()["version"] == 2
    assert hard.json()["state"] == "proposed" and hard.json()["version"] == 3
    assert still == []
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "active" and accepted.json()["version"] == 4
    assert accepted.json()["body"] == "Proposal one, edited." and accepted.json()["level"] == "hard"
    assert accepted.json()["proposed_by"] == LEAD
    # A second accept is the same rule, with no new row.
    assert again.status_code == 200 and again.json()["version"] == 4
    assert [r["rule_key"] for r in active_after] == [key]
    assert [(r["version"], r["state"]) for r in history.json()["rows"]] == [
        (4, "active"), (3, "proposed"), (2, "proposed"), (1, "proposed")]
    assert bad_state.status_code == 422
    assert len(_rule_rows(w)) == 4


# ── Case 3: org B reads none of org A ───────────────────────────────────────


async def test_org_b_reads_none_of_org_a(world) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        a_key = (await _create(call, w.lead, w.p1, "A rule of org A.")).json()["rule_key"]
        b_made = await _create(call, w.b_lead, w.b_space, "A rule of org B.")
        b_key = b_made.json()["rule_key"]
        rs = [
            await call(w.b_lead, "GET", _projects(w.p1)),
            await call(w.b_lead, "GET", _rule(a_key, "/history")),
            await call(w.b_lead, "POST", _rule(a_key, "/accept")),
            await call(w.b_lead, "PATCH", _rule(a_key), {"body": "B writes A."}),
            await call(w.b_lead, "POST", _rule(a_key, "/retire")),
            await call(w.b_lead, "POST", _rule(a_key, "/level"), {"level": "hard"}),
            await _create(call, w.b_lead, w.p1, "B writes into A."),
        ]
        b_list = await _list(call, w.b_lead, w.b_space)
        # A page of org B cannot be the source of a rule of org A.
        cited = await _create(call, w.lead, w.p1, "Cites B.", source_page_id=w.page_b,
                              source_page_version=1, quote="Org B words.")
    assert b_made.status_code == 201
    assert [r.status_code for r in rs] == [404] * len(rs), [r.text for r in rs]
    assert "A rule of org A" not in "".join(r.text for r in rs)
    assert [r["rule_key"] for r in b_list] == [b_key]
    assert cited.status_code == 404
    # The database, as the non-privileged role, bound to org B and to nobody.
    eng = create_engine(w.p.app_url)
    try:
        with eng.begin() as c:
            role = c.execute(text(
                "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user")).first()
            assert not role.rolbypassrls and not role.rolsuper
            flags = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'pm_agent_guidance'")).first()
            assert tuple(flags) == (True, True)
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": w.b})
            keys = {str(r.rule_key) for r in c.execute(text(
                "SELECT rule_key FROM pm_agent_guidance WHERE kind = 'rule'"))}
            assert keys == {b_key}, "org B read a rule of org A"
            by_key = c.execute(text(
                "SELECT 1 FROM pm_agent_guidance WHERE rule_key = CAST(:k AS uuid)"),
                {"k": a_key}).first()
            assert by_key is None, "org B read a rule of org A by its key"
    finally:
        eng.dispose()
    # A fresh connection, so no setting of the bound session remains.
    fresh = create_engine(w.p.app_url)
    try:
        with fresh.begin() as c:
            unbound = c.execute(text("SELECT count(*) FROM pm_agent_guidance")).scalar()
            assert unbound == 0, "an unbound session reads nothing"
    finally:
        fresh.dispose()
    assert {str(r.organization_id) for r in _rule_rows(w)} == {w.a, w.b}


# ── Case 4: the quote check ─────────────────────────────────────────────────


async def test_a_quote_that_is_not_in_the_version_is_refused(world) -> None:
    w = world
    _reset(w)

    def source(page: str, version: int | None, quote: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if page is not None:
            out["source_page_id"] = page
        if version is not None:
            out["source_page_version"] = version
        if quote is not None:
            out["quote"] = quote
        return out

    async with _session(w.p) as call:
        async def cite(page: Any, version: Any, quote: Any, who: Any = None) -> httpx.Response:
            return await _create(call, who or w.lead, w.p1, "A quoted rule.",
                                 **source(page, version, quote))

        invented = await cite(w.open, 2, "Billing changes never need a review.")
        wrong_version = await cite(w.open, 2, "Each billing change needs a migration plan.")
        case_folded = await cite(w.open, 1, "each billing change needs a migration plan.")
        empty = await cite(w.open, 1, "")
        blank = await cite(w.open, 1, "   ")
        too_long = await cite(w.open, 1, "x" * 401)
        no_page = await cite(None, None, "Each billing change")
        no_quote = await cite(w.open, 1, None)
        no_version = await cite(w.open, None, "Each billing change")
        unknown_version = await cite(w.open, 99, "Each billing change")
        under_floor = await cite(w.moved, 1, "Private draft words.")
        hidden = await cite(w.closed, 1, SECRET_QUOTE, who=w.admin)
        unknown_page = await cite(str(uuid.uuid4()), 1, SECRET_QUOTE, who=w.admin)
        draft = await cite(w.draft, 1, "Draft words here.")
        me_page = await cite(w.mine, 1, "My own words here.")
        nul = await cite(w.open, 1, "Each\x00billing")
    for name, r in (("invented", invented), ("wrong_version", wrong_version),
                    ("case_folded", case_folded), ("unknown_version", unknown_version),
                    ("under_floor", under_floor)):
        assert r.status_code == 422, (name, r.text)
        assert r.json()["detail"] == project_rules.QUOTE_NOT_FOUND, name
    for name, r in (("empty", empty), ("blank", blank), ("too_long", too_long), ("nul", nul)):
        assert r.status_code == 422, (name, r.text)
    for name, r in (("no_page", no_page), ("no_quote", no_quote), ("no_version", no_version)):
        assert r.status_code == 422, (name, r.text)
        assert r.json()["detail"] == project_rules.SOURCE_INCOMPLETE, name
    # A page the caller cannot read and an unknown page read the same.
    assert hidden.status_code == 404 and unknown_page.status_code == 404
    assert hidden.json() == unknown_page.json()
    assert draft.status_code == 409 and draft.json()["detail"] == project_rules.NOT_PUBLISHED_REFUSED
    assert me_page.status_code == 409
    assert me_page.json()["detail"] == project_rules.PERSONAL_PAGE_REFUSED
    assert _rule_rows(w) == []


# ── Case 5: a true quote stays valid when the page changes ──────────────────


async def test_a_true_quote_stays_valid_after_the_page_changes(world) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        made = await _create(call, w.lead, w.p1, "Plan each billing change.",
                             source_page_id=w.open, source_page_version=1,
                             quote=TRUE_QUOTE.replace("\n", "\r\n"))
        assert made.status_code == 201, made.text
        key = made.json()["rule_key"]
        # The page changes: a third version with other words.
        with w.p.admin_engine.begin() as c:
            _version(c, w.a, w.open, 3, "# Billing\nNo plan is needed now.")
            c.execute(text("UPDATE kb_pages SET body_md = :b WHERE id = :p"),
                      {"b": "# Billing\nNo plan is needed now.", "p": w.open})
        rows = await _list(call, w.member, w.p1)
        again_v1 = await _create(call, w.lead, w.p1, "Same quote, version 1.",
                                 source_page_id=w.open, source_page_version=1, quote=TRUE_QUOTE)
        on_v3 = await _create(call, w.lead, w.p1, "Same quote, version 3.",
                              source_page_id=w.open, source_page_version=3, quote=TRUE_QUOTE)
        with w.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM kb_page_versions WHERE page_id = :p AND version = 3"),
                      {"p": w.open})
            c.execute(text("UPDATE kb_pages SET body_md = :b WHERE id = :p"),
                      {"b": V2, "p": w.open})
    got = made.json()
    assert got["quote"] == TRUE_QUOTE, "the stored quote has LF line ends"
    assert (got["source_page_id"], got["source_page_version"]) == (w.open, 1)
    rule = next(r for r in rows if r["rule_key"] == key)
    assert rule["state"] == "active" and rule["quote"] == TRUE_QUOTE
    assert rule["source_page_version"] == 1 and rule["quote_hidden"] is False
    assert again_v1.status_code == 201, again_v1.text
    assert on_v3.status_code == 422


# ── Case 6: the quote is hidden from a reader who cannot open the page ──────


async def test_a_reader_who_cannot_open_the_page_gets_no_quote(world) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        made = await _create(call, w.lead, w.p1, "Keep the vendors private.", level="hard",
                             source_page_id=w.closed, source_page_version=1, quote=SECRET_QUOTE)
        assert made.status_code == 201, made.text
        key = made.json()["rule_key"]
        member_list = await call(w.member, "GET", _projects(w.p1))
        member_history = await call(w.member, "GET", _rule(key, "/history"))
        lead_list = await _list(call, w.lead, w.p1)
        # A Projects admin who cannot read the page edits the sentence. The
        # answer hides the quote, and the new row keeps it.
        edited = await call(w.admin, "PATCH", _rule(key), {"body": "Keep vendors private."})
    assert made.json()["quote"] == SECRET_QUOTE and made.json()["quote_hidden"] is False
    for r in (member_list, member_history):
        assert r.status_code == 200, r.text
        row = r.json()["rows"][0]
        assert row["body"] == "Keep the vendors private."
        assert (row["level"], row["state"]) == ("hard", "active")
        assert row["quote"] is None and row["source_page_id"] is None
        assert row["source_page_version"] is None and row["quote_hidden"] is True
        assert SECRET_QUOTE not in r.text and w.closed not in r.text
    assert lead_list[0]["quote"] == SECRET_QUOTE and lead_list[0]["source_page_id"] == w.closed
    assert edited.status_code == 200, edited.text
    assert edited.json()["quote_hidden"] is True and SECRET_QUOTE not in edited.text
    newest = _rows(w, "SELECT quote, source_page_id, version FROM pm_agent_guidance "
                      "WHERE rule_key = :k ORDER BY version DESC LIMIT 1", k=key)[0]
    assert (newest.quote, str(newest.source_page_id), newest.version) == (
        SECRET_QUOTE, w.closed, 2)


# ── Case 7: each change adds a row ──────────────────────────────────────────


async def test_each_change_adds_a_row_and_the_old_row_stays(world) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        key = (await _create(call, w.lead, w.p1, "Version one.")).json()["rule_key"]
        first = _rows(w, "SELECT * FROM pm_agent_guidance WHERE rule_key = :k", k=key)[0]
        edited = await call(w.lead, "PATCH", _rule(key), {"body": "Version two."})
        quoted = await call(w.lead, "PATCH", _rule(key), {
            "source_page_id": w.open, "source_page_version": 2,
            "quote": "Billing changes need a review"})
        unquoted = await call(w.lead, "PATCH", _rule(key), {
            "source_page_id": None, "source_page_version": None, "quote": None})
        hard = await call(w.lead, "POST", _rule(key, "/level"), {"level": "hard"})
        same = await call(w.lead, "POST", _rule(key, "/level"), {"level": "hard"})
        retired = await call(w.lead, "POST", _rule(key, "/retire"))
        retired_again = await call(w.lead, "POST", _rule(key, "/retire"))
        after_retire = [
            await call(w.lead, "PATCH", _rule(key), {"body": "Too late."}),
            await call(w.lead, "POST", _rule(key, "/level"), {"level": "soft"}),
            await call(w.lead, "POST", _rule(key, "/accept")),
        ]
        nothing = await call(w.lead, "PATCH", _rule(key), {})
        history = await call(w.member, "GET", _rule(key, "/history"))
        by_member = await call(w.member, "POST", _rule(key, "/retire"))
    assert [r.status_code for r in (edited, quoted, unquoted, hard, same, retired)] == [200] * 6
    assert [r.json()["version"] for r in (edited, quoted, unquoted, hard, same, retired)] == [
        2, 3, 4, 5, 5, 6]
    assert quoted.json()["quote"] == "Billing changes need a review"
    assert unquoted.json()["quote"] is None and unquoted.json()["source_page_id"] is None
    assert retired.json()["state"] == "retired" and retired.json()["level"] == "hard"
    assert retired.json()["written_by"] == LEAD and retired.json()["proposed_by"] == LEAD
    assert retired_again.status_code == 200 and retired_again.json()["version"] == 6
    assert [r.status_code for r in after_retire] == [409] * 3
    assert all(r.json()["detail"] == project_rules.RETIRED_REFUSED for r in after_retire)
    assert nothing.status_code == 422
    assert by_member.status_code == 403
    rows = history.json()["rows"]
    assert [r["version"] for r in rows] == [6, 5, 4, 3, 2, 1]
    assert [r["body"] for r in rows][-2:] == ["Version two.", "Version one."]
    # The first row is as it was.
    again = _rows(w, "SELECT * FROM pm_agent_guidance WHERE rule_key = :k AND version = 1",
                  k=key)[0]
    assert tuple(again) == tuple(first)
    # An UPDATE of a rule row raises, for the owner and for the app role.
    with pytest.raises(DBAPIError, match="cannot change"), w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_agent_guidance SET state = 'active' WHERE rule_key = :k"),
                  {"k": key})
    eng = create_engine(w.p.app_url)
    try:
        with pytest.raises(DBAPIError, match="cannot change"), eng.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": w.a})
            c.execute(text("UPDATE pm_agent_guidance SET body = 'x' WHERE rule_key = :k"),
                      {"k": key})
    finally:
        eng.dispose()


async def test_the_database_refuses_a_rule_row_of_a_bad_shape(world) -> None:
    """The CHECKs of the rule migration, on the real catalog."""
    w = world
    _reset(w)
    base = {"o": w.a, "p": w.p1, "kind": "rule", "k": str(uuid.uuid4()), "body": "A rule.",
            "level": "soft", "state": "active", "by": LEAD, "page": None, "ver": None,
            "quote": None}
    sql = ("INSERT INTO pm_agent_guidance (organization_id, project_id, kind, rule_key, "
           "version, body, written_by, level, state, proposed_by, source_page_id, "
           "source_page_version, quote) VALUES (:o, :p, :kind, CAST(:k AS uuid), 1, :body, "
           ":by, :level, :state, :by, CAST(:page AS uuid), CAST(:ver AS integer), :quote)")
    bad = {
        "level": {"level": "medium"},
        "state": {"state": "needs_review"},
        "no key": {"k": None},
        "no level": {"level": None},
        "no state": {"state": None},
        "long body": {"body": "x" * 301},
        "empty body": {"body": ""},
        "long quote": {"quote": "x" * 401, "page": w.open, "ver": 1},
        "quote, no page": {"quote": "words"},
        "page, no quote": {"page": w.open, "ver": 1},
        "version, no page": {"ver": 1},
    }
    for name, change in bad.items():
        with pytest.raises(IntegrityError), w.p.admin_engine.begin() as c:
            c.execute(text(sql), {**base, **change, "k": change.get("k", str(uuid.uuid4()))})
        assert _rule_rows(w) == [], name
    # A good row and a guidance row with NULL rule columns pass.
    with w.p.admin_engine.begin() as c:
        c.execute(text(sql), {**base, "body": "x" * 300, "quote": "q" * 400, "page": w.open,
                              "ver": 1})
        c.execute(text(sql), {**base, "kind": "guidance", "k": None, "level": None,
                              "state": None, "body": "g" * 3000})


# ── Case 8: the cap of active rules ─────────────────────────────────────────


async def test_the_41st_active_rule_gets_409(world) -> None:
    w = world
    _reset(w)
    seeded = [_seed_rule(w, w.p_cap) for _ in range(project_rules.ACTIVE_MAX)]
    _seed_rule(w, w.p_cap, state="retired")
    async with _session(w.p) as call:
        over = await _create(call, w.lead, w.p_cap, "The 41st rule.")
        proposal = await _create(call, w.lead, w.p_cap, "A proposal at the cap.", state="proposed")
        key = proposal.json()["rule_key"]
        accept_over = await call(w.lead, "POST", _rule(key, "/accept"))
        retire_one = await call(w.lead, "POST", _rule(seeded[0], "/retire"))
        accept_now = await call(w.lead, "POST", _rule(key, "/accept"))
        active = await _list(call, w.lead, w.p_cap)
        elsewhere = await _create(call, w.lead, w.p1, "Another project has room.")
    assert over.status_code == 409 and over.json()["detail"] == project_rules.CAP_REFUSED
    assert proposal.status_code == 201
    assert accept_over.status_code == 409
    assert retire_one.status_code == 200 and accept_now.status_code == 200
    assert len(active) == project_rules.ACTIVE_MAX
    assert elsewhere.status_code == 201


#: The writes of one round of the race.
RACE_PARTIES = 4


async def test_concurrent_creates_hold_the_cap(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """The advisory lock serializes the writes of one project, so the count of
    active rules cannot race. The writes wait at a barrier after the write
    check, just before the lock. With the lock, two get 201 and two get 409
    at a cap of 2. With no lock, each write counts 0 and each gets 201."""
    w = world
    _reset(w)
    monkeypatch.setattr(project_rules, "ACTIVE_MAX", 2)
    real_check = project_rules.assert_may_write_guidance
    gate = asyncio.Barrier(RACE_PARTIES)

    async def _check_then_wait(db: Any, user: Any, project: Any) -> None:
        await real_check(db, user, project)
        await asyncio.wait_for(gate.wait(), timeout=20)

    monkeypatch.setattr(project_rules, "assert_may_write_guidance", _check_then_wait)
    async with _session(w.p) as call:
        rs = await asyncio.wait_for(asyncio.gather(
            *(_create(call, w.lead, w.p_race, f"Race rule {n}.") for n in range(RACE_PARTIES))),
            timeout=60)
    assert sorted(r.status_code for r in rs) == [201, 201, 409, 409], [r.text for r in rs]
    active = _rows(w, "SELECT 1 FROM pm_agent_guidance WHERE kind = 'rule' "
                      "AND project_id = :p AND state = 'active'", p=w.p_race)
    assert len(active) == 2


# ── Case 10: a personal tree, and an archived project ───────────────────────


async def test_a_personal_tree_takes_no_rule(world) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        owner = await _create(call, w.member, w.personal, "My own rule.")
        admin = await _create(call, w.admin, w.personal, "An admin rule.")
        archived = await _create(call, w.lead, w.archived, "An old rule.")
    assert owner.status_code == 403 and "personal" in owner.json()["detail"].lower()
    # A personal tree is private, so the admin cannot even see it.
    assert admin.status_code == 404
    assert archived.status_code == 409
    assert archived.json()["detail"] == project_rules.ARCHIVED_REFUSED
    assert _rule_rows(w) == []


# ── Case 11: the flag ───────────────────────────────────────────────────────


async def test_flag_off_answers_404_for_each_route(world, monkeypatch: pytest.MonkeyPatch) -> None:
    w = world
    _reset(w)
    key = _seed_rule(w, w.p1)
    for value in ("", w.b):
        monkeypatch.setattr(get_settings(), "project_rules_orgs", value)
        async with _session(w.p) as call:
            rs = [
                await call(w.lead, "GET", _projects(w.p1)),
                await _create(call, w.lead, w.p1, "Dark."),
                await call(w.lead, "GET", _rule(key, "/history")),
                await call(w.lead, "POST", _rule(key, "/accept")),
                await call(w.lead, "PATCH", _rule(key), {"body": "Dark."}),
                await call(w.lead, "POST", _rule(key, "/retire")),
                await call(w.lead, "POST", _rule(key, "/level"), {"level": "hard"}),
            ]
        assert [r.status_code for r in rs] == [404] * 7, (value, [r.text for r in rs])
    assert len(_rule_rows(w)) == 1
