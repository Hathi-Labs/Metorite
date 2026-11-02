"""WS-ALN ALN-2a: one person decides, the backend.

Spec: ``project-docs/specs/project_alignment.md`` §3.2, §7.2, §13 and the row
ALN-2a of §10.

A decision is a question with two to four named options for one named person
about one task. This suite proves the rules on a real Postgres (R8) and the
pure parts without one.

**Hermetic** (no database). The options check, the flag reader, the migration
(found by CONTENT, R1), the six places a new bell kind must be named, the
"no score for any person" scan (ALN-Q5) and the member purge.

**R8** (the class carries ``_DB_GATE``). The real ``/projects`` router runs as
the non-privileged role of the phase-4 rehearsal, on a database with the whole
ladder and two organizations:

* only the named decider answers; the author never comes from the body;
* a second answer is refused, and two concurrent answers end as one;
* one answer writes exactly one activity row;
* a decider who cannot open the task gets no row and no bell;
* an expired decision takes no answer;
* an answer must be one of the option keys, and the DB holds that too;
* org isolation;
* the flag ``DECISIONS_ORGS`` (default empty) gates the ask and each route;
* a run with no person present answers nothing (AUTO-7a).

**Mutations this suite catches.** Commit first, and restore with
``git checkout -- <file>``.

* M1. ``answer_decision``: drop ``lower(decider) = :me`` from the UPDATE and the
  check of ``row.decider`` before it.
* M2. ``answer_decision``: drop ``answered_at IS NULL`` from the UPDATE.
* M3. ``answer_decision``: drop ``expires_at > now()`` from the UPDATE.
* M4. ``answer_decision``: drop the ``answer_key not in labels`` check.
* M5. ``ask_decision``: drop the ``deliverable`` check.
* M6. ``decisions_allowed``: answer True always.
* M7. ``_refuse_no_person``: do nothing.

Run::

    uv run pytest tests/unit/test_decisions.py -v -rs
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException, Request
from gateway.routes.projects import decisions
from pydantic import ValidationError
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"
_SERVICE = _REPO / "apps/services/gateway/gateway/routes/projects/decisions.py"

#: Every bound on a wait. Broken code goes red and never hangs.
_WAIT = 60

YES_NO = [{"key": "yes", "label": "Yes, go ahead"}, {"key": "no", "label": "No, stop"}]


def _migration() -> Path:
    """The migration that creates ``pm_decisions``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS pm_decisions" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


# ── Hermetic ────────────────────────────────────────────────────────────────


def test_the_options_check_takes_two_to_four_unique_keys() -> None:
    ok = decisions.clean_options(YES_NO)
    assert ok == YES_NO
    four = [{"key": k, "label": k.upper()} for k in ("a", "b", "c", "d")]
    assert decisions.clean_options(four) == four
    bad: list[Any] = [
        YES_NO[:1],                                               # one option
        [*four, {"key": "e", "label": "E"}],                      # five options
        [YES_NO[0], YES_NO[0]],                                   # a repeated key
        [{"key": "Yes", "label": "x"}, YES_NO[1]],                # key not a token
        [{"key": "yes", "label": "  "}, YES_NO[1]],               # blank label
        [{"key": "yes", "label": "x" * 81}, YES_NO[1]],           # label too long
        [{"key": "yes", "label": "x", "extra": 1}, YES_NO[1]],    # a third field
        "yes,no", None, {"yes": "no"},
    ]
    for options in bad:
        with pytest.raises(decisions.DecisionRefused) as refused:
            decisions.clean_options(options)
        assert refused.value.reason == "bad_options", options


def test_the_flag_is_off_by_default_and_reads_a_list_or_a_star(monkeypatch) -> None:
    from acb_common import get_settings

    org, other = str(uuid.uuid4()), str(uuid.uuid4())
    settings = get_settings()
    assert type(settings).model_fields["decisions_orgs"].default == ""
    for raw, org_on, other_on in (
        ("", False, False),
        (f" {org.upper()} , x", True, False),
        ("*", True, True),
        (other, False, True),
    ):
        monkeypatch.setattr(settings, "decisions_orgs", raw)
        assert decisions.decisions_allowed(org) is org_on, raw
        assert decisions.decisions_allowed(other) is other_on, raw
    for nobody in (None, "", "  "):
        monkeypatch.setattr(settings, "decisions_orgs", "*")
        assert decisions.decisions_allowed(nobody) is False


def test_the_flag_is_named_in_the_three_places_of_a_flag() -> None:
    env = (_REPO / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^DECISIONS_ORGS=$", env, re.MULTILINE)
    from acb_common import env_guard

    guarded = (
        set(getattr(env_guard, "PLATFORM_ENV_NAMES", ()))
        | set(getattr(env_guard, "PLATFORM_ENV_VARS", ()))
    )
    text_ = Path(env_guard.__file__).read_text(encoding="utf-8")
    assert '"DECISIONS_ORGS"' in text_ or "DECISIONS_ORGS" in guarded


def test_the_migration_widens_the_bell_and_guards_the_table() -> None:
    sql = _migration().read_text(encoding="utf-8")
    assert "'decision'" in sql and "pm_notifications_kind_check" in sql
    assert "FORCE  ROW LEVEL SECURITY" in sql
    assert "answered_at IS NULL" not in sql.split("CREATE TABLE", 1)[0]
    for name in ("pm_decisions_answered_by_decider", "pm_decisions_answer_is_an_option",
                 "pm_decisions_guard", "pm_decisions_options"):
        assert name in sql, name
    # REFERENCES before DEFAULT, so the tenancy ratchet matches the pair.
    assert re.search(
        r"REFERENCES organization \(id\) ON DELETE CASCADE\s+DEFAULT current_setting",
        sql,
    )
    assert not re.search(r"\bGRANT\b", re.sub(r"--.*", "", sql))


def test_a_decision_is_named_in_each_place_a_bell_kind_must_be() -> None:
    from gateway.reach import classes
    from gateway.routes.projects.notifications import NOTIFICATION_KINDS
    from gateway.routes.shell import feed, needs

    assert "decision" in NOTIFICATION_KINDS
    assert set(NOTIFICATION_KINDS) <= set(needs._VERB)
    assert needs._VERB["decision"]
    assert classes.REACH_CLASS["decision"] == "interrupt"
    assert "decision" in feed._NOTE_WORDS


def test_the_table_keeps_no_score_for_any_person() -> None:
    """ALN-Q5. The only columns that name a person are ``decider`` and
    ``answered_by`` (and ``asked_by``, for the record). No raised-for column,
    no counter, no index on a person, and no query that groups or counts by
    one."""
    sql = re.sub(r"--.*", "", _migration().read_text(encoding="utf-8"))
    body = sql.split("CREATE TABLE IF NOT EXISTS pm_decisions", 1)[1].split(");", 1)[0]
    columns = set(re.findall(r"^\s{4}([a-z_]+)\s+[A-Z]", body, re.MULTILINE))
    assert columns == {
        "organization_id", "id", "subject_kind", "subject_id", "decider", "options",
        "question", "asked_by", "source", "payload", "created_at", "expires_at",
        "answered_by", "answer_key", "answered_at", "answer_reason",
    }, columns
    for index in re.findall(r"CREATE\s+(?:UNIQUE\s+)?INDEX[^;]*;", sql, re.IGNORECASE):
        assert not re.search(r"decider|answered_by|asked_by", index), index
    code = re.sub(r"#.*", "", _SERVICE.read_text(encoding="utf-8"))
    code = re.sub(r'""".*?"""', "", code, flags=re.DOTALL)
    for forbidden in (r"group\s+by", r"count\s*\(", r"\brank\b", r"\bscore\b",
                      r"\bsum\s*\(", r"\bavg\s*\("):
        assert not re.search(forbidden, code, re.IGNORECASE), forbidden


def test_the_member_purge_keeps_decisions_as_it_keeps_every_pm_row() -> None:
    """R5 and D63. A decision is a record on a task, so the purge deletes no
    ``pm_decisions`` row, and it counts none by person (ALN-Q5). An open
    decision of a purged decider ends at ``expires_at``."""
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    for rows in (*_PURGE_DELETES, *_PURGE_KEEPS):
        assert rows.table != "pm_decisions", rows
        assert "pm_decisions" not in rows.where
    members = Path(
        _REPO / "apps/services/gateway/gateway/routes/admin/members.py"
    ).read_text(encoding="utf-8")
    assert "DELETE FROM pm_decisions" not in members


def test_the_routes_are_mounted_and_there_is_no_ask_route() -> None:
    from gateway.routes.projects import router

    mounted = {(m, r.path) for r in router.routes for m in getattr(r, "methods", set())}
    assert ("GET", "/projects/decisions") in mounted
    assert ("POST", "/projects/decisions/{decision_id}/answer") in mounted
    ours = {(m, p) for (m, p) in mounted if "/decisions" in p}
    assert ours == {("GET", "/projects/decisions"),
                    ("POST", "/projects/decisions/{decision_id}/answer")}, ours


def test_the_answer_body_names_no_person() -> None:
    fields = set(decisions.AnswerIn.model_fields)
    assert fields == {"answer_key", "reason"}
    with pytest.raises(ValidationError):
        decisions.AnswerIn(answer_key="yes", answered_by="someone@x.test")  # type: ignore[call-arg]


async def test_a_run_with_no_person_present_is_refused_before_any_read() -> None:
    """AUTO-7a. The stub fails on any call, so a read or a write goes red."""
    from acb_common.no_person import NO_PERSON_PRESENT, no_person_scope

    class _Untouchable:
        calls = 0

        async def execute(self, *_a: Any, **_k: Any) -> Any:
            type(self).calls += 1
            raise AssertionError("a marked run read or wrote")

    user = SimpleNamespace(email="d@x.test", agent=None, organization_id=str(uuid.uuid4()))
    with no_person_scope(), pytest.raises(HTTPException) as refused:
        await asyncio.wait_for(
            decisions.answer_decision(_Untouchable(), user, str(uuid.uuid4()), "yes", None),
            _WAIT,
        )
    assert refused.value.status_code == 403
    assert refused.value.detail["error"] == NO_PERSON_PRESENT
    assert _Untouchable.calls == 0


async def test_an_agent_connection_does_not_answer() -> None:
    class _Untouchable:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("an agent read or wrote")

    agent = SimpleNamespace(email="d@x.test", agent=object(), organization_id="o")
    with pytest.raises(HTTPException) as refused:
        await asyncio.wait_for(
            decisions.answer_decision(_Untouchable(), agent, str(uuid.uuid4()), "yes", None),
            _WAIT,
        )
    assert refused.value.status_code == 403


def test_the_no_person_fence_lists_the_answer_path() -> None:
    """``test_no_person_present.py`` scans for a tool that names a route that
    answers. The answer route of a decision must be in that list, so a tool
    cannot call it as the member."""
    src = (_REPO / "tests/unit/test_no_person_present.py").read_text(encoding="utf-8")
    assert '"/decisions/{}/answer"' in src


# ── R8: the helpers ─────────────────────────────────────────────────────────


def _principal(email: str, org: str, *, unrestricted: bool = False, agent: Any = None) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:projects", "data:org:read"] if unrestricted else ["feature:projects"]
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
        organization_id=org, agent=agent,
    )


def _app(users: dict[str, Any]) -> Any:
    """The real ``/projects`` router. The caller is the principal that the
    test header names, and the override binds the tenant from it. The tenant
    never comes from the request."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes import projects as projects_package

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(projects_package.router)
    app.dependency_overrides[get_current_user] = _caller
    return app


class _Client:
    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client = client
        self.users = users

    async def __call__(
        self, user: Any, method: str, path: str, body: dict | None = None,
    ) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await asyncio.wait_for(
            self.client.request(method, path, json=body, headers={"x-test-as": key}), _WAIT,
        )


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)), base_url="http://projects.test",
            ) as client,
        ):
            yield _Client(client, users), p
    finally:
        release_tenant(scope)


async def _ask(p: Any, org: str, task: str, decider: str, **kw: Any) -> dict:
    """The ask, as server code makes it: a bound tenant and a session of its
    own, on the same engine scope as the routes."""
    from acb_common.db import bind_tenant
    from gateway.db import tenant_session

    bind_tenant(org)
    kw.setdefault("question", "Ship the extruder fix today?")
    kw.setdefault("options", YES_NO)
    kw.setdefault("asked_by", "system:align")
    async with tenant_session() as db:
        return await decisions.ask_decision(db, task_id=task, decider=decider, **kw)


def _seed_member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": email, "o": org})


def _seed_task(p: Any, org: str, creator: str, *, grants: tuple[str, ...] = (),
               assignee: str | None = None) -> str:
    project, status, task = (str(uuid.uuid4()) for _ in range(3))
    _seed_member(p, org, creator)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'decisions', :e, true)"), {"id": project, "o": org, "e": creator})
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
    if assignee is not None:
        _seed_member(p, org, assignee)
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, "
                "assigned_by) VALUES (:o, :t, :a, :e)"),
                {"o": org, "t": task, "a": assignee, "e": creator})
    return task


def _count(p: Any, sql: str, **params: Any) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(sql), params).scalar())


def _row(p: Any, decision: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(
            text("SELECT * FROM pm_decisions WHERE id = CAST(:i AS uuid)"), {"i": decision},
        ).fetchone()


def _activities(p: Any, task: str) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT * FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
            "AND meta ? 'decision_id'"), {"t": task}))


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.asker = "asker@decisions-r8.test"
    ns.d = "decider@decisions-r8.test"
    ns.o = "other@decisions-r8.test"      # opens the task, is not the decider
    ns.c = "assignee@decisions-r8.test"   # opens it through the assignee arm only
    ns.x = "outsider@decisions-r8.test"   # a member with no grant at all
    ns.b = "decider@decisions-r8-b.test"  # a member of the other org
    for email in (ns.x, ns.d, ns.o):
        _seed_member(promoted, promoted.org_a, email)
    ns.task = _seed_task(promoted, promoted.org_a, ns.asker,
                         grants=(ns.asker, ns.d, ns.o), assignee=ns.c)
    ns.hidden = _seed_task(promoted, promoted.org_a, ns.asker, grants=(ns.asker,))
    ns.task_b = _seed_task(promoted, promoted.org_b, ns.b, grants=(ns.b,))
    ns.u_d = _principal(ns.d, promoted.org_a)
    ns.u_o = _principal(ns.o, promoted.org_a)
    ns.u_x = _principal(ns.x, promoted.org_a)
    ns.u_asker = _principal(ns.asker, promoted.org_a)
    ns.u_b = _principal(ns.b, promoted.org_b)
    ns.u_agent = _principal(ns.d, promoted.org_a, agent=SimpleNamespace(connection_id="c"))
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The world with the flag on for org A only, and with no decision, no
    decision activity and no notification left from the last test."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "decisions_orgs", world.p.org_a)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_decisions"))
        c.execute(text("DELETE FROM pm_notifications"))
        c.execute(text("DELETE FROM pm_activities WHERE type = 'comment'"))
    return world


@_DB_GATE
class TestDecisionsOnARealDatabase:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
            forced = c.execute(text(
                "SELECT count(*) FROM pg_class WHERE relname = 'pm_decisions' "
                "AND relrowsecurity AND relforcerowsecurity")).scalar()
        assert role is not None and not role[0] and not role[1]
        assert forced == 1

    # ── The ask ─────────────────────────────────────────────────────────

    async def test_an_ask_reaches_its_named_person_and_nobody_else(self, w):
        async with _session(w.p):
            made = await _ask(w.p, w.p.org_a, w.task, w.d.upper())
        assert made["heard"] is True and made["state"] == "open"
        assert made["decider"] == w.d and made["options"] == YES_NO
        assert "payload" not in made
        row = _row(w.p, made["id"])
        assert row.organization_id is not None and str(row.organization_id) == w.p.org_a
        with w.p.admin_engine.connect() as c:
            bells = list(c.execute(text("SELECT recipient, kind, actor FROM pm_notifications")))
        assert [(b.recipient, b.kind) for b in bells] == [(w.d, "decision")]
        # The default life is 7 days.
        life = _count(w.p, "SELECT extract(epoch FROM expires_at - created_at)::int "
                           "FROM pm_decisions")
        assert abs(life - 7 * 86400) < 5

    async def test_a_decider_who_cannot_open_the_task_gets_no_row_and_no_bell(self, w):
        async with _session(w.p):
            for who in (w.x, w.asker.replace("asker", "nobody"), w.b):
                with pytest.raises(decisions.DecisionRefused) as refused:
                    await _ask(w.p, w.p.org_a, w.task, who)
                assert refused.value.reason == "decider_cannot_open", who
            # w.d holds a grant on `task` and none on `hidden`.
            with pytest.raises(decisions.DecisionRefused) as refused:
                await _ask(w.p, w.p.org_a, w.hidden, w.d)
            assert refused.value.reason == "decider_cannot_open"
        assert _count(w.p, "SELECT count(*) FROM pm_decisions") == 0
        assert _count(w.p, "SELECT count(*) FROM pm_notifications") == 0

    async def test_the_assignee_arm_opens_the_task_as_it_does_everywhere(self, w):
        async with _session(w.p):
            made = await _ask(w.p, w.p.org_a, w.task, w.c)
        assert made["heard"] is True

    async def test_an_asker_who_is_the_decider_and_an_agent_decider_write_nothing(self, w):
        async with _session(w.p):
            with pytest.raises(decisions.DecisionRefused) as same:
                await _ask(w.p, w.p.org_a, w.task, w.d, asked_by=w.d.upper())
            assert same.value.reason == "self_decision"
            with pytest.raises(decisions.DecisionRefused) as agent:
                await _ask(w.p, w.p.org_a, w.task, "agent:bot@x.test")
            assert agent.value.reason == "bad_decider"
        assert _count(w.p, "SELECT count(*) FROM pm_decisions") == 0
        assert _count(w.p, "SELECT count(*) FROM pm_notifications") == 0

    async def test_a_page_subject_a_long_question_and_bad_options_write_nothing(self, w):
        async with _session(w.p):
            for kw, reason in (
                ({"subject_kind": "page"}, "subject_kind"),
                ({"question": "q" * 141}, "bad_question"),
                ({"question": "   "}, "bad_question"),
                ({"options": YES_NO[:1]}, "bad_options"),
                ({"source": "human"}, "source"),
                ({"expires_in": timedelta(seconds=5)}, "bad_life"),
            ):
                with pytest.raises(decisions.DecisionRefused) as refused:
                    await _ask(w.p, w.p.org_a, w.task, w.d, **kw)
                assert refused.value.reason == reason, kw
            ok = await _ask(w.p, w.p.org_a, w.task, w.d, question="q" * 140)
        assert ok["state"] == "open"
        assert _count(w.p, "SELECT count(*) FROM pm_decisions") == 1

    async def test_org_b_cannot_ask_about_a_task_of_org_a(self, w, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "decisions_orgs", "*")
        async with _session(w.p):
            with pytest.raises(decisions.DecisionRefused) as refused:
                await _ask(w.p, w.p.org_b, w.task, w.b)
            assert refused.value.reason == "decider_cannot_open"

    # ── The flag ────────────────────────────────────────────────────────

    async def test_with_the_flag_off_nothing_is_asked_and_nothing_is_heard(self, w, monkeypatch):
        from acb_common import get_settings

        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
            monkeypatch.setattr(get_settings(), "decisions_orgs", "")
            with pytest.raises(decisions.DecisionRefused) as refused:
                await _ask(w.p, w.p.org_a, w.task, w.d)
            assert refused.value.reason == "off"
            # The routes answer 404 for an existing decision.
            assert (await call(w.u_d, "GET", "/projects/decisions")).status_code == 404
            gone = await call(w.u_d, "POST", f"/projects/decisions/{made['id']}/answer",
                              {"answer_key": "yes"})
            assert gone.status_code == 404
            # The flag of another org does not open this one.
            monkeypatch.setattr(get_settings(), "decisions_orgs", w.p.org_b)
            with pytest.raises(decisions.DecisionRefused):
                await _ask(w.p, w.p.org_a, w.task, w.d)
        assert _count(w.p, "SELECT count(*) FROM pm_decisions") == 1
        assert _count(w.p, "SELECT count(*) FROM pm_notifications") == 1
        assert _row(w.p, made["id"]).answered_at is None

    # ── The answer ──────────────────────────────────────────────────────

    async def test_only_the_decider_answers(self, w, monkeypatch):
        from acb_common import get_settings

        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
            path = f"/projects/decisions/{made['id']}/answer"
            # The flag is on for both orgs, so org B meets the data and not the flag.
            monkeypatch.setattr(get_settings(), "decisions_orgs", "*")
            # Another member who can open the task, the asker, a member with no
            # access, and a member of another org: each gets the same 404.
            for user in (w.u_o, w.u_asker, w.u_x, w.u_b):
                refused = await call(user, "POST", path, {"answer_key": "yes"})
                assert refused.status_code == 404, user.email
                assert refused.json()["detail"] == "Decision not found"
            assert _row(w.p, made["id"]).answered_at is None
            assert _activities(w.p, w.task) == []
            done = await call(w.u_d, "POST", path, {"answer_key": "yes", "reason": " go "})
            assert done.status_code == 200, done.text
        out = done.json()["decision"]
        assert out["state"] == "answered" and out["answer_key"] == "yes"
        row = _row(w.p, made["id"])
        assert (row.answered_by, row.answer_key, row.answer_reason) == (w.d, "yes", "go")

    async def test_the_answerer_never_comes_from_the_body(self, w):
        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
            path = f"/projects/decisions/{made['id']}/answer"
            sneaky = await call(w.u_o, "POST", path,
                                {"answer_key": "yes", "answered_by": w.d})
            assert sneaky.status_code == 422
            assert _row(w.p, made["id"]).answered_at is None

    async def test_an_agent_connection_does_not_answer_for_its_member(self, w):
        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
            refused = await call(w.u_agent, "POST",
                                 f"/projects/decisions/{made['id']}/answer", {"answer_key": "yes"})
            assert refused.status_code == 403
        assert _row(w.p, made["id"]).answered_at is None

    async def test_one_answer_writes_one_activity_and_a_second_is_refused(self, w):
        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
            path = f"/projects/decisions/{made['id']}/answer"
            first = await call(w.u_d, "POST", path, {"answer_key": "yes", "reason": "ship it"})
            assert first.status_code == 200
            second = await call(w.u_d, "POST", path, {"answer_key": "no"})
            assert second.status_code == 409 and second.json()["detail"] == "decision_answered"
        row = _row(w.p, made["id"])
        assert row.answer_key == "yes"
        acts = _activities(w.p, w.task)
        assert len(acts) == 1
        assert acts[0].type == "comment" and acts[0].created_by == w.d
        assert "Yes, go ahead" in acts[0].body and "ship it" in acts[0].body
        assert acts[0].meta["decision_id"] == made["id"]

    async def test_two_concurrent_answers_end_as_one_answer_and_one_refusal(self, w):
        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
            path = f"/projects/decisions/{made['id']}/answer"
            results = await asyncio.wait_for(asyncio.gather(
                call(w.u_d, "POST", path, {"answer_key": "yes"}),
                call(w.u_d, "POST", path, {"answer_key": "no"}),
            ), _WAIT)
        codes = sorted(r.status_code for r in results)
        assert codes == [200, 409], [r.text for r in results]
        winner = next(r for r in results if r.status_code == 200).json()["decision"]
        assert _row(w.p, made["id"]).answer_key == winner["answer_key"]
        assert len(_activities(w.p, w.task)) == 1

    async def test_an_expired_decision_takes_no_answer(self, w):
        with w.p.admin_engine.begin() as c:
            expired = c.execute(text(
                "INSERT INTO pm_decisions (organization_id, subject_id, decider, options,"
                " question, asked_by, created_at, expires_at) "
                "VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :d, CAST(:opts AS jsonb),"
                " 'Late?', 'system:align', now() - interval '2 days',"
                " now() - interval '1 day') RETURNING id"),
                {"o": w.p.org_a, "t": w.task, "d": w.d,
                 "opts": '[{"key":"yes","label":"Yes"},{"key":"no","label":"No"}]'},
            ).scalar()
        async with _session(w.p) as (call, _):
            late = await call(w.u_d, "POST", f"/projects/decisions/{expired}/answer",
                              {"answer_key": "yes"})
            assert late.status_code == 409 and late.json()["detail"] == "decision_expired"
            mine = (await call(w.u_d, "GET", "/projects/decisions")).json()["decisions"]
            assert mine == []
            on_task = (await call(w.u_o, "GET", f"/projects/decisions?task_id={w.task}")).json()
            assert [d["state"] for d in on_task["decisions"]] == ["expired"]
        assert _row(w.p, str(expired)).answered_at is None
        assert _activities(w.p, w.task) == []

    async def test_a_decider_who_lost_the_task_can_no_longer_answer(self, w):
        lost = _seed_task(w.p, w.p.org_a, w.asker, grants=(w.asker, w.d))
        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, lost, w.d)
            with w.p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM pm_project_grants WHERE subject = :s AND project_id "
                               "= (SELECT project_id FROM pm_tasks WHERE id = CAST(:t AS uuid))"),
                          {"s": w.d, "t": lost})
            refused = await call(w.u_d, "POST", f"/projects/decisions/{made['id']}/answer",
                                 {"answer_key": "yes"})
            assert refused.status_code == 404
        assert _row(w.p, made["id"]).answered_at is None
        assert _activities(w.p, lost) == []

    async def test_an_answer_must_be_one_of_the_option_keys(self, w):
        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
            path = f"/projects/decisions/{made['id']}/answer"
            wrong = await call(w.u_d, "POST", path, {"answer_key": "maybe"})
            assert wrong.status_code == 422
            assert _row(w.p, made["id"]).answered_at is None
            assert _activities(w.p, w.task) == []
            assert (await call(w.u_d, "POST", path, {"answer_key": "no"})).status_code == 200

    async def test_the_database_holds_the_rules_the_service_holds(self, w):
        from sqlalchemy.exc import DBAPIError, IntegrityError

        async with _session(w.p):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
        did = made["id"]
        wall = (IntegrityError, DBAPIError)
        # An answer that is not an option, or from a person who is not the decider.
        for sets in ("answer_key = 'maybe', answered_by = decider, answered_at = now()",
                     "answer_key = 'yes', answered_by = 'other@x.test', answered_at = now()",
                     "answer_key = 'yes', answered_at = now()"):
            with pytest.raises(wall), w.p.admin_engine.begin() as c:
                c.execute(text(f"UPDATE pm_decisions SET {sets} WHERE id = CAST(:i AS uuid)"),
                          {"i": did})
        # What was asked cannot change.
        with pytest.raises(wall), w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_decisions SET question = 'Else?' "
                           "WHERE id = CAST(:i AS uuid)"), {"i": did})
        # Two to four options, a short question.
        for column, value in (("options", '[{"key":"a","label":"A"}]'),
                              ("question", "'" + "q" * 141)):
            with pytest.raises(wall), w.p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO pm_decisions (organization_id, subject_id, decider, options,"
                    " question, asked_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :d,"
                    " CAST(:opts AS jsonb), :q, 'x')"),
                    {"o": w.p.org_a, "t": w.task, "d": w.d, "q": "Q?",
                     "opts": '[{"key":"yes","label":"Y"},{"key":"no","label":"N"}]'}
                    | ({"opts": value} if column == "options" else {"q": value[1:]}))
        # An answer, then a second UPDATE of any kind, by any role.
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_decisions SET answered_by = decider, answer_key = 'yes',"
                           " answered_at = now() WHERE id = CAST(:i AS uuid)"), {"i": did})
        with pytest.raises(wall), w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_decisions SET answer_key = 'no' "
                           "WHERE id = CAST(:i AS uuid)"), {"i": did})

    async def test_a_run_with_no_person_present_answers_nothing(self, w):
        from acb_common.no_person import NO_PERSON_PRESENT, no_person_scope

        async with _session(w.p) as (call, _):
            made = await _ask(w.p, w.p.org_a, w.task, w.d)
            path = f"/projects/decisions/{made['id']}/answer"
            with no_person_scope():
                refused = await call(w.u_d, "POST", path, {"answer_key": "yes"})
            assert refused.status_code == 403
            assert refused.json()["detail"]["error"] == NO_PERSON_PRESENT
            assert _row(w.p, made["id"]).answered_at is None
            assert _activities(w.p, w.task) == []
            # A person still answers, once the mark is gone.
            assert (await call(w.u_d, "POST", path, {"answer_key": "yes"})).status_code == 200

    # ── The reads ───────────────────────────────────────────────────────

    async def test_the_decider_lists_their_open_decisions_and_a_task_lists_its_own(
        self, w, monkeypatch,
    ):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "decisions_orgs", "*")
        async with _session(w.p) as (call, _):
            mine = await _ask(w.p, w.p.org_a, w.task, w.d)
            await _ask(w.p, w.p.org_a, w.task, w.c)
            listed = (await call(w.u_d, "GET", "/projects/decisions")).json()["decisions"]
            assert [d["id"] for d in listed] == [mine["id"]]
            assert all("payload" not in d for d in listed)
            # O opens the task and is not the decider: O reads no decision of D.
            by_task = (await call(w.u_o, "GET", f"/projects/decisions?task_id={w.task}")).json()
            assert by_task == {"decisions": []}
            assert mine["question"] not in str(by_task) and w.d not in str(by_task)
            # D reads their own decision on the task, and not the one of C.
            own = (await call(w.u_d, "GET", f"/projects/decisions?task_id={w.task}")).json()
            assert [d["id"] for d in own["decisions"]] == [mine["id"]]
            # A member who cannot open the task cannot list its decisions.
            assert (await call(w.u_x, "GET",
                               f"/projects/decisions?task_id={w.task}")).status_code == 404
            # Org B sees nothing of org A.
            assert (await call(w.u_b, "GET", "/projects/decisions")).json() == {"decisions": []}
            assert (await call(w.u_b, "GET",
                               f"/projects/decisions?task_id={w.task}")).status_code == 404

    async def test_org_isolation_holds_in_the_table_itself(self, w, app_engine):  # noqa: F811
        async with _session(w.p):
            await _ask(w.p, w.p.org_a, w.task, w.d)
        with app_engine.connect() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, false)"), {"o": w.p.org_b})
            assert c.execute(text("SELECT count(*) FROM pm_decisions")).scalar() == 0
            c.execute(text("SELECT set_config('app.tenant_id', :o, false)"), {"o": w.p.org_a})
            assert c.execute(text("SELECT count(*) FROM pm_decisions")).scalar() == 1
