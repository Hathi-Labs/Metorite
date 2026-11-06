"""WS-55 EA-18a: Questions for me, the read route, the answer rule, the bell.

Spec: ``project-docs/specs/external_agent_access.md`` §12.8 (D-EA-14), and row
EA-18a of §15 (Metorite task 1806).

Two halves. The hermetic tests need no database: the shape of the route, the
one text, and the flag. The R8 tests run the real ``/projects`` router on the
phase-4-promoted catalog as the NON-privileged app role, so FORCE RLS holds. A
run and its question come from the real routes, through a signed agent.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_agent_questions_r8.py -v -rs

**The mutations (R7).** Each one turns the named test red. Commit first, and
restore with ``git checkout -- <file>``. ``Q`` is
``routes/projects/agent_questions.py``, ``R`` is ``agent_runs.py`` and ``C`` is
``core.py``.

* M1. ``Q`` ``question_row``: ``can_answer`` is always true.
  ``test_m1_a_reader_outside_the_set_reads_can_answer_false``.
* M2. ``R`` ``post_agent_activity``: no ``_refuse_an_outside_answer``.
  ``test_m2_a_viewer_outside_the_set_gets_403_on_an_answer``.
* M3. ``Q`` ``list_agent_questions``: no role check on ``scope=everyone``.
  ``test_m3_everyone_refuses_a_reader_without_the_role``.
* M4. ``Q`` ``questions_sql``: no ``task_visibility_clause``.
  ``test_m4_a_question_of_another_organization_never_shows``.
* M5. ``R`` ``_comment``: a second ``notify`` for the creator.
  ``test_m5_a_creator_who_is_an_assignee_gets_one_bell``.
* M6. ``R`` ``may_answer_run``: a text of its own with no creator arm. And ``Q``
  ``questions_sql``: a text of its own with no creator arm.
  ``test_m6_the_list_and_the_route_read_one_answer_set`` and
  ``test_the_list_and_the_route_read_one_text``.
* M7. ``R`` ``_refuse_an_outside_answer``: no check of ``signal == "stop"``.
  ``test_m7_a_stop_stays_open_to_a_viewer``.
* M8. ``C`` ``visible_project_paths``: no ``Visibility.project_clause``.
  ``test_m8_the_path_stops_at_the_first_hidden_parent``.

Fix round 1 (the review of 2026-11-06), each run on a committed tree:

* M9. ``filters.py`` ``agent_focus_clause``: the flag-on branch drops the
  ``listed_question_sql`` test. ``test_waiting_on_me_and_for_me_agree_with_the_flag_on``,
  ``test_a_typed_request_waits_on_its_member_only`` and
  ``test_a_question_on_a_done_task_counts_in_neither``.
* M10. ``R`` ``listed_question_sql``: the typed test passes each reader, so the
  creator sees a typed request. ``test_a_typed_request_waits_on_its_member_only``,
  ``test_a4_a_typed_archive_request_shows_to_its_member_only`` and the agree test.
* M11. ``R`` ``listed_question_sql``: no ``task_closed_sql``. The closed tests of
  the list and of the focus.
* M12. ``Q`` ``questions_sql``: no ``r.status = 'awaiting'``.
  ``test_the_list_names_the_status_for_the_partial_index``.

Fix round 2 (the coordinator's rule of 2026-11-06), on a committed tree:

* M13. ``R`` ``listed_question_sql``: no member arm, so a typed request hides
  from the run's member. ``test_a_typed_request_waits_on_its_member_only``,
  ``test_a4_a_typed_archive_request_shows_to_its_member_only`` and the agree test.

Fix round 3, on a committed tree:

* M14. ``Q`` ``question_row``: ``kind`` is always ``"question"``.
  ``test_a_typed_request_waits_on_its_member_only``.
* M15. ``Q`` ``question_row``: ``kind`` is always ``"archive_request"``.
  ``test_a1_the_creator_sees_the_question_with_why_and_the_path`` and
  ``test_a_free_question_has_no_options``.

The cases that the spec names with no M number, each red under its own mutation:

* ``Q``: the route writes a row. ``test_a_read_writes_nothing``.
* ``Q``: no flag check. ``test_a7_with_the_flag_off_the_route_answers_404``.
* ``R``: the rule reads no flag. ``test_a7_with_the_flag_off_any_viewer_may_answer``.
* ``R``: the bell reads no flag. ``test_a7_with_the_flag_off_the_audience_has_no_creator``.
* ``filters.py``: the focus reads no flag.
  ``test_waiting_on_me_and_for_me_agree_with_the_flag_on``.
* ``Q``: a typed request is listed. ``test_a4_a_typed_archive_request_does_not_show``.
* ``Q``: a closed task is listed. ``test_a_question_on_a_closed_task_does_not_show``.
* ``R``: the rule also binds a run that is not awaiting.
  ``test_a_reply_to_a_finished_run_stays_open_with_its_question_open``.
* ``R``: the rule also binds a typed request.
  ``test_the_typed_archive_rule_does_not_change``.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_auth import AgentScope, UserContext, UserRole, build_access
from acb_common import env_guard, get_settings
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import Settings
from gateway.routes.projects import agent_questions, agent_runs, core, filters
from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_agent_runs_r8 import (
    VIA,
    _act,
    _key,
    _principal,
    _seed_connection,
    _select,
    _session,
    _start,
    _status,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]

CREATOR = "ea18-creator@questions.test"
#: ``created_by`` holds the actor as written, so the fence seeds it mixed case.
CREATOR_WRITTEN = "EA18-Creator@Questions.test"
ASSIGNEE = "ea18-assignee@questions.test"
MEMBER = "ea18-member@questions.test"
VIEWER = "ea18-viewer@questions.test"
OWNER = "ea18-owner@questions.test"
ADMIN = "ea18-admin@questions.test"
MANAGER = "ea18-manager@questions.test"
#: Holds the role admin, and no grant shows the project to them.
STRANGER = "ea18-stranger@questions.test"
#: Sees every project of the tenant through ``data:org:read``, with no role.
PORTFOLIO = "ea18-portfolio@questions.test"
#: Sees one task of a hidden project, as its assignee.
ELSEWHERE = "ea18-elsewhere@questions.test"
#: The creator of a task whose project they cannot see.
LOST = "ea18-lost@questions.test"
B_MEMBER = "ea18-b-member@questions.test"
B_ADMIN = "ea18-b-admin@questions.test"

FORBIDDEN_ANSWER = (
    "Only {name}, the creator of the task, its assignees, or an owner, admin or "
    "manager can answer this question. Ask one of them, or open the task and add "
    "a comment."
)
FORBIDDEN_EVERYONE = (
    "Only an owner, an admin or a manager can see every question. Open For me to "
    "see yours."
)
ROW_KEYS = {
    "run_id", "activity_id", "task_id", "task_number", "task_title", "agent_name",
    "client_via", "asked_at", "run_started_at", "project_path", "question",
    "can_answer", "why",
}


def _person(email: str, org: str, *, roles: tuple[str, ...] = (),
            unrestricted: bool = False) -> UserContext:
    """A person from the session: the address, the roles and the org. The fence
    gives ``data:org:read`` only to :data:`PORTFOLIO`, so a role never widens
    what a reader sees."""
    perms = ["feature:projects", "data:org:read"] if unrestricted else ["feature:projects"]
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms, roles=roles),
        organization_id=org,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the shape, the one text and the flag
# ═══════════════════════════════════════════════════════════════════════════


class TestTheShape:

    def test_the_route_is_mounted(self) -> None:
        from gateway.routes.projects import router

        mounted = {(m, r.path) for r in router.routes for m in getattr(r, "methods", set())}
        assert ("GET", "/projects/agent-questions") in mounted

    def test_the_route_takes_no_tenant_and_no_person(self) -> None:
        """R5, R11. The address, the roles and the org come from the session."""
        names = set(inspect.signature(agent_questions.list_agent_questions).parameters)
        assert names == {"scope", "project", "sort", "offset", "limit", "user"}

    def test_the_flag_ships_empty_and_a_tenant_cannot_write_it(self) -> None:
        assert Settings.model_fields["agent_questions_orgs"].default == ""
        assert "AGENT_QUESTIONS_ORGS" in set(env_guard.PLATFORM_ENV_NAMES)
        env = (_REPO / ".env.example").read_text(encoding="utf-8")
        assert re.search(r"^AGENT_QUESTIONS_ORGS=$", env, re.MULTILINE)
        doc = (_REPO / "docs" / "flag_reload.md").read_text(encoding="utf-8")
        assert "AGENT_QUESTIONS_ORGS" in doc

    def test_the_flag_reads_the_org_or_the_star(self, monkeypatch) -> None:
        settings = get_settings()
        org = "6f1d0b8a-1c2e-4a4b-9d3e-2f6a1b0c9d8e"
        monkeypatch.setattr(settings, "agent_questions_orgs", "")
        assert not filters.agent_questions_allowed(org)
        monkeypatch.setattr(settings, "agent_questions_orgs", f"x, {org.upper()}")
        assert filters.agent_questions_allowed(org)
        assert not filters.agent_questions_allowed("another")
        assert not filters.agent_questions_allowed(None)
        monkeypatch.setattr(settings, "agent_questions_orgs", "*")
        assert filters.agent_questions_allowed(org)
        assert not filters.agent_questions_allowed(None), "no org never opens it"


class TestTheOneText:

    def test_the_list_and_the_route_read_one_text(self) -> None:
        """M6, the text half. Each reader names the one function, and the SQL of
        the list holds its text."""
        vis = SimpleNamespace(unrestricted=True, agent=None, params={})
        listed = agent_questions.questions_sql("TRUE", vis)
        assert agent_runs.person_may_answer_sql("r") in listed
        assert agent_runs.answer_reason_sql("r") in listed
        source = inspect.getsource(agent_runs.may_answer_run)
        assert "person_may_answer_sql('r')" in source
        assert "holds_answer_role(user)" in source
        assert "person_may_answer_sql" in inspect.getsource(filters.agent_focus_clause)

    def test_the_list_names_the_status_for_the_partial_index(self) -> None:
        """Fix round 1 (P3). A plain status test, ahead of the CASE, lets the
        partial index ``pm_agent_runs_one_open_per_task`` serve the list."""
        vis = SimpleNamespace(unrestricted=True, agent=None, params={})
        listed = agent_questions.questions_sql("TRUE", vis)
        assert f"r.status = 'awaiting' AND ({filters.AGENT_RUN_STATE_SQL}) = 'awaiting'" in listed

    def test_the_list_and_the_focus_read_one_text_of_a_listed_question(self, monkeypatch):
        """Fix round 1 (P2). The list and "Waiting on me" read
        ``listed_question_sql``, and it holds the closed test and the typed test."""
        vis = SimpleNamespace(unrestricted=True, agent=None, params={})
        assert agent_runs.listed_question_sql("r") in agent_questions.questions_sql("TRUE", vis)
        text_ = agent_runs.listed_question_sql("qr", me=filters.AGENT_FOCUS_ME)
        assert filters.task_closed_sql("qr") in text_
        assert agent_runs.answerable_question_sql("qr.id") in text_
        assert f"OR lower(qr.member_email) = :{filters.AGENT_FOCUS_ME})" in text_
        typed = agent_runs.answerable_question_sql("qr.id")
        assert f"<> '{agent_runs.ARCHIVE_REQUEST_KIND}'" in typed
        assert "-> 'request' ->> 'kind'" in typed and "ORDER BY oq.seq DESC LIMIT 1" in typed
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        org = "6f1d0b8a-1c2e-4a4b-9d3e-2f6a1b0c9d8e"
        monkeypatch.setattr(get_settings(), "agent_questions_orgs", org)
        scope = bind_tenant(org)
        try:
            on = filters.agent_focus_clause("waiting")
        finally:
            release_tenant(scope)
        assert agent_runs.listed_question_sql("qr", me=filters.AGENT_FOCUS_ME) in on

    def test_the_answer_set_is_the_reason_set(self) -> None:
        reason = agent_runs.answer_reason_sql("r")
        assert agent_runs.person_may_answer_sql("r") == f"({reason} IS NOT NULL)"

    def test_the_role_test_stays_out_of_the_sql(self) -> None:
        """§12.8. The role comes from the session, so the SQL cannot hold it."""
        text_ = agent_runs.person_may_answer_sql("r").lower()
        for role in ("owner", "admin", "manager", "roles"):
            assert role not in text_, role

    def test_each_address_test_folds_the_case(self) -> None:
        text_ = agent_runs.answer_reason_sql("r")
        assert "lower(qt.created_by) = :me" in text_
        assert "lower(qa.assignee) = :me" in text_
        assert "lower(r.member_email) = :me" in text_
        assert text_.index("'created'") < text_.index("'assigned'") < text_.index("'my_agent'")

    def test_the_focus_names_its_own_bind(self, monkeypatch) -> None:
        """With the flag on, the focus reads the one text over its pill row and
        its own bind. With it off, it stays as it was."""
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        monkeypatch.setattr(get_settings(), "agent_questions_orgs", "")
        org = "6f1d0b8a-1c2e-4a4b-9d3e-2f6a1b0c9d8e"
        scope = bind_tenant(org)
        try:
            off = filters.agent_focus_clause("waiting")
            assert "created_by" not in off and ":af_me" in off
            monkeypatch.setattr(get_settings(), "agent_questions_orgs", org)
            on = filters.agent_focus_clause("waiting")
        finally:
            release_tenant(scope)
        assert agent_runs.person_may_answer_sql("s", me=filters.AGENT_FOCUS_ME) in on
        assert ":me" not in on

    def test_a_connection_does_not_carry_its_members_role(self) -> None:
        """A signed agent of an admin is no reviewer. A prompt is a person's
        word, and the list is a person's list."""
        person = _person(ADMIN, "o", roles=("admin",))
        agent = UserContext(
            email=ADMIN, role=UserRole.EMPLOYEE,
            access=build_access(["feature:projects"], roles=("admin",)), organization_id="o",
            agent=AgentScope(connection_id="c", actor_via=VIA),
        )
        assert agent_runs.holds_answer_role(person)
        assert not agent_runs.holds_answer_role(agent)
        assert not agent_runs.holds_answer_role(_person(VIEWER, "o", roles=("member",)))
        for role in ("owner", "admin", "manager"):
            assert agent_runs.holds_answer_role(_person(MANAGER, "o", roles=(role,))), role


# ═══════════════════════════════════════════════════════════════════════════
# R8: the world
# ═══════════════════════════════════════════════════════════════════════════


def _name(p: Any, project: dict[str, Any], name: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_projects SET name = :n WHERE id = CAST(:p AS uuid)"),
                  {"n": name, "p": project["id"]})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Two organizations, one project tree, and the people of D-EA-14.

    The tree of organization A is ``space`` (no grant) > ``mid`` (a grant to the
    org) > ``leaf``. So a member sees ``mid`` and ``leaf`` and never ``space``.
    ``top`` (a grant to the org) > ``sealed`` > ``deep`` (a grant to the org) has
    a hidden parent under a visible grandparent. ``secret`` has no grant.
    """
    org, org_b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, org=org, org_b=org_b)
    for who in (CREATOR_WRITTEN, ASSIGNEE, MEMBER, VIEWER, OWNER, ADMIN, MANAGER, STRANGER,
                PORTFOLIO, ELSEWHERE, LOST):
        b.member(promoted, org, who)
    for who in (B_MEMBER, B_ADMIN):
        b.member(promoted, org_b, who)

    ns.space = b.project(promoted, org, CREATOR_WRITTEN)
    _name(promoted, ns.space, "Metorite")
    ns.mid = b.project(promoted, org, CREATOR_WRITTEN, parent=ns.space, grant="org")
    _name(promoted, ns.mid, "Application")
    ns.leaf = b.project(promoted, org, CREATOR_WRITTEN, parent=ns.mid)
    _name(promoted, ns.leaf, "AI Studio")
    ns.other = b.project(promoted, org, CREATOR_WRITTEN, grant="org")
    _name(promoted, ns.other, "Other")
    ns.secret = b.project(promoted, org, CREATOR_WRITTEN)
    _name(promoted, ns.secret, "Secret name")
    ns.top = b.project(promoted, org, CREATOR_WRITTEN, grant="org")
    _name(promoted, ns.top, "Top")
    ns.sealed = b.project(promoted, org, CREATOR_WRITTEN, parent=ns.top, sealed=True)
    _name(promoted, ns.sealed, "Sealed name")
    ns.deep = b.project(promoted, org, CREATOR_WRITTEN, parent=ns.sealed, grant="org")
    _name(promoted, ns.deep, "Deep")

    # The task of the creator, with one assignee. No watcher row, so the creator
    # hears of it only through the rule of this slice.
    ns.task = b.task(promoted, org, CREATOR_WRITTEN, ns.leaf, assignee=ASSIGNEE)
    # The same person is the creator and an assignee, in two spellings.
    ns.task_both = b.task(promoted, org, CREATOR_WRITTEN, ns.other, assignee=CREATOR.upper())
    # The agent's member is an assignee too.
    ns.task_held = b.task(promoted, org, CREATOR_WRITTEN, ns.other, assignee=MEMBER)
    ns.task_other = b.task(promoted, org, CREATOR_WRITTEN, ns.other, title="Second")
    ns.task_third = b.task(promoted, org, CREATOR_WRITTEN, ns.other, title="Third")
    ns.task_deep = b.task(promoted, org, CREATOR_WRITTEN, ns.deep, title="Deep work")
    ns.task_secret = b.task(promoted, org, LOST, ns.secret, assignee=ELSEWHERE, title="Hidden")
    ns.task_system = b.task(promoted, org, "", ns.other, title="No creator")

    ns.conn = _seed_connection(promoted, org, MEMBER)
    ns.agent = _principal(MEMBER, org, agent_connection=ns.conn)
    ns.conn_viewer = _seed_connection(promoted, org, VIEWER)
    ns.agent_viewer = _principal(VIEWER, org, agent_connection=ns.conn_viewer)

    ns.creator = _person(CREATOR, org)
    ns.assignee = _person(ASSIGNEE, org)
    ns.member = _person(MEMBER, org)
    ns.viewer = _person(VIEWER, org, roles=("member",))
    ns.owner = _person(OWNER, org, roles=("owner",))
    ns.admin = _person(ADMIN, org, roles=("admin",))
    ns.manager = _person(MANAGER, org, roles=("manager",))
    ns.stranger = _person(STRANGER, org, roles=("admin",))
    ns.portfolio = _person(PORTFOLIO, org, unrestricted=True)
    ns.elsewhere = _person(ELSEWHERE, org)

    ns.proj_b = b.project(promoted, org_b, B_MEMBER, grant="org")
    ns.task_b = b.task(promoted, org_b, B_MEMBER, ns.proj_b, title="Of the other org")
    ns.conn_b = _seed_connection(promoted, org_b, B_MEMBER)
    ns.agent_b = _principal(B_MEMBER, org_b, agent_connection=ns.conn_b)
    ns.b_member = _person(B_MEMBER, org_b)
    ns.b_admin = _person(B_ADMIN, org_b, roles=("admin",))
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """Both flags on, and no run, comment or notification from the last test."""
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    monkeypatch.setattr(get_settings(), "agent_questions_orgs", "*")
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        c.execute(text("DELETE FROM pm_notifications"))
        c.execute(text("DELETE FROM pm_activities WHERE type = 'comment'"))
        # A comment subscribes its author, and a watcher joins the audience.
        c.execute(text("DELETE FROM pm_task_watchers"))
    return world


def _flag_off(monkeypatch: Any) -> None:
    monkeypatch.setattr(get_settings(), "agent_questions_orgs", "")


async def _ask(w: Any, call: Any, task: str | None = None, *, agent: Any = None,
               question: str = "Which database?") -> tuple[str, str]:
    """A run and an open question, from the real routes: ``(run id, question id)``."""
    who = agent or w.agent
    run = await _start(w, call, user=who, task=task or w.task)
    asked = await _act(call, who, run["id"], **_select(question))
    assert asked.status_code == 201, asked.text
    assert asked.json()["run"]["status"] == "awaiting"
    return run["id"], asked.json()["activity"]["id"]


async def _ask_again(w: Any, call: Any, run: str) -> str:
    asked = await _act(call, w.agent, run, **_select("And then?"))
    assert asked.status_code == 201, asked.text
    return asked.json()["activity"]["id"]


async def _list(call: Any, user: Any, **query: Any) -> Any:
    path = "/projects/agent-questions"
    return await call(user, "GET", f"{path}?{urlencode(query)}" if query else path)


async def _rows(call: Any, user: Any, **query: Any) -> list[dict[str, Any]]:
    got = await _list(call, user, **query)
    assert got.status_code == 200, got.text
    return got.json()["rows"]


async def _read_every(user: Any) -> list[dict[str, Any]]:
    """What the read gives a reader BEFORE the scope narrows it, with no gate of
    the route. A reader outside the set gets no row from the route, so this is
    where their ``can_answer`` shows. Use it inside ``_session``."""
    scope = bind_tenant(user.organization_id)
    try:
        async with core._tenant_session() as db:
            vis = await core.resolve_visibility(db, user)
            found = await agent_questions.read_questions(
                db, vis, agent_runs._member(user), "TRUE", {},
            )
            return await agent_questions.question_rows(
                db, vis, found, review=agent_runs.holds_answer_role(user),
            )
    finally:
        release_tenant(scope)


def _bells(w: Any, kind: str = "agent_question") -> list[str]:
    with w.p.admin_engine.connect() as c:
        return [r[0] for r in c.execute(text(
            "SELECT recipient FROM pm_notifications WHERE kind = :k ORDER BY recipient"),
            {"k": kind})]


def _path_names(row: dict[str, Any]) -> list[str]:
    return [step["name"] for step in row["project_path"]]


# ═══════════════════════════════════════════════════════════════════════════
# R8: the read
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheRead:

    async def test_a1_the_creator_sees_the_question_with_why_and_the_path(self, w):
        async with _session(w.p) as call:
            run, question = await _ask(w, call)
            got = await _list(call, w.creator)
            assert got.status_code == 200, got.text
            body = got.json()
            assert set(body) == {"rows", "counts", "total", "offset", "limit", "capped"}
            assert body["counts"] == {"mine": 1, "everyone": None}
            assert (body["total"], body["offset"], body["limit"]) == (1, 0, 100)
            assert body["capped"] is False
            [row] = body["rows"]
            assert set(row) == ROW_KEYS
            assert (row["run_id"], row["activity_id"], row["task_id"]) == (run, question, w.task)
            assert row["task_title"] == "Fix the extruder"
            assert isinstance(row["task_number"], int)
            assert row["why"] == "created" and row["can_answer"] is True
            assert row["client_via"] == VIA and row["agent_name"] == "Claude Code"
            assert row["asked_at"] and row["run_started_at"]
            assert row["project_path"] == [
                {"id": w.mid["id"], "name": "Application"},
                {"id": w.leaf["id"], "name": "AI Studio"},
            ]
            assert row["question"]["body"] == "Which database?"
            assert [o["id"] for o in row["question"]["options"]] == ["pg", "my", "sq"]
            assert row["question"]["options"][0]["recommended"] is True
            assert row["question"]["allow_multiple"] is False
            assert row["question"]["kind"] == "question"

    async def test_why_takes_the_first_true_test(self, w):
        async with _session(w.p) as call:
            await _ask(w, call, w.task)
            assert [r["why"] for r in await _rows(call, w.creator)] == ["created"]
            assert [r["why"] for r in await _rows(call, w.assignee)] == ["assigned"]
            assert [r["why"] for r in await _rows(call, w.member)] == ["my_agent"]

    async def test_why_prefers_created_then_assigned(self, w):
        async with _session(w.p) as call:
            await _ask(w, call, w.task_both)
            await _ask(w, call, w.task_held)
            why = {r["task_id"]: r["why"] for r in await _rows(call, w.creator)}
            assert why == {w.task_both: "created", w.task_held: "created"}
            why = {r["task_id"]: r["why"] for r in await _rows(call, w.member)}
            assert why == {w.task_both: "my_agent", w.task_held: "assigned"}

    async def test_an_answer_takes_the_row_off_the_list(self, w):
        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
            answered = await _act(call, w.creator, run, type="prompt", selected=["pg"])
            assert answered.status_code == 201, answered.text
            got = await _list(call, w.creator)
            assert got.json()["rows"] == [] and got.json()["counts"]["mine"] == 0

    async def test_a_free_question_has_no_options(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call, user=w.agent, task=w.task)
            asked = await _act(call, w.agent, run["id"], type="elicitation", body="Free?")
            assert asked.status_code == 201, asked.text
            [row] = await _rows(call, w.creator)
            assert row["question"] == {"kind": "question", "body": "Free?", "options": [],
                                       "allow_multiple": False}

    async def test_a4_a_typed_archive_request_shows_to_its_member_only(self, w):
        """Only the run's member approves it, through ``archive-approval`` on
        the task card (§12.1). So it waits on that member, and on nobody else."""
        async with _session(w.p) as call:
            asked = await call(w.agent, "POST", f"/projects/tasks/{w.task}/archive-request",
                               {"idempotency_key": _key()})
            assert asked.status_code == 201, asked.text
            got = await _list(call, w.member)
            assert got.json()["counts"] == {"mine": 1, "everyone": None}
            [row] = got.json()["rows"]
            assert row["why"] == "my_agent" and row["can_answer"] is True
            assert [o["id"] for o in row["question"]["options"]] == ["approve", "decline"]
            for reader in (w.creator, w.assignee, w.owner, w.admin, w.manager, w.viewer):
                got = await _list(call, reader)
                assert got.json()["rows"] == [], reader.email
                assert got.json()["counts"]["mine"] == 0, reader.email
            for reader in (w.owner, w.admin, w.manager):
                got = await _list(call, reader, scope="everyone")
                assert got.json()["rows"] == [], reader.email
                assert got.json()["counts"]["everyone"] == 0, reader.email

    async def test_a_question_on_a_closed_task_does_not_show(self, w):
        """It is a ``dangling`` row of Needs attention, and the sweep closes it."""
        async with _session(w.p) as call:
            await _ask(w, call, w.task_other)
            assert len(await _rows(call, w.creator)) == 1
            with w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE pm_tasks SET archived_at = now() "
                               "WHERE id = CAST(:t AS uuid)"), {"t": w.task_other})
            try:
                assert await _rows(call, w.creator) == []
            finally:
                with w.p.admin_engine.begin() as c:
                    c.execute(text("UPDATE pm_tasks SET archived_at = NULL "
                                   "WHERE id = CAST(:t AS uuid)"), {"t": w.task_other})

    async def test_a_read_writes_nothing(self, w):
        """Each read, in every shape. No bell, no row, no moved stamp."""
        def _state() -> list[Any]:
            with w.p.admin_engine.connect() as c:
                return [
                    c.execute(text(
                        f"SELECT count(*), md5(coalesce(string_agg(x::text, ',' ORDER BY x::text),"
                        f" '')) FROM {table} x")).first()
                    for table in ("pm_agent_runs", "pm_agent_run_activities", "pm_notifications",
                                  "pm_activities", "pm_agent_run_strands", "pm_tasks")
                ]

        async with _session(w.p) as call:
            await _ask(w, call)
            before = _state()
            await _rows(call, w.creator)
            await _rows(call, w.creator, sort="newest", limit=1)
            await _rows(call, w.creator, limit=0)
            await _rows(call, w.creator, project=w.mid["id"])
            await _rows(call, w.admin, scope="everyone")
            assert (await _list(call, w.viewer, scope="everyone")).status_code == 403
            assert _state() == before


@_DB_GATE
class TestWhoSeesWhat:

    async def test_m1_a_reader_outside_the_set_reads_can_answer_false(self, w):
        """Acceptance 2, the read half. "For me" gives them no row, and the read
        under it says ``can_answer`` false with ``why`` null."""
        async with _session(w.p) as call:
            await _ask(w, call)
            got = await _list(call, w.viewer)
            assert got.status_code == 200, got.text
            assert got.json()["rows"] == []
            assert got.json()["counts"] == {"mine": 0, "everyone": None}
            [row] = await _read_every(w.viewer)
            assert row["can_answer"] is False and row["why"] is None
            # The set reads true, from the same two functions.
            for reader, why in ((w.creator, "created"), (w.assignee, "assigned"),
                                (w.member, "my_agent")):
                [row] = await _read_every(reader)
                assert row["can_answer"] is True and row["why"] == why

    async def test_a3_each_role_sees_everyone_and_answers_any_question(self, w):
        async with _session(w.p) as call:
            for reader in (w.owner, w.admin, w.manager):
                run, _ = await _ask(w, call)
                got = await _list(call, reader, scope="everyone")
                assert got.status_code == 200, got.text
                body = got.json()
                assert body["counts"] == {"mine": 0, "everyone": 1}
                assert body["total"] == 1
                [row] = body["rows"]
                assert row["can_answer"] is True and row["why"] is None
                # "For me" stays their own: none of the three groups.
                assert await _rows(call, reader) == []
                answered = await _act(call, reader, run, type="prompt", selected=["pg"])
                assert answered.status_code == 201, answered.text
                assert answered.json()["activity"]["actor"] == reader.email
                with w.p.admin_engine.begin() as c:
                    c.execute(text("DELETE FROM pm_agent_runs"))

    async def test_m3_everyone_refuses_a_reader_without_the_role(self, w):
        """Acceptance 4. Each reader without the role, in the set or outside it."""
        async with _session(w.p) as call:
            await _ask(w, call)
            for reader in (w.viewer, w.creator, w.assignee, w.member, w.portfolio):
                got = await _list(call, reader, scope="everyone")
                assert got.status_code == 403, reader.email
                assert got.json()["detail"] == FORBIDDEN_EVERYONE
                # No "Everyone" tab: the count is null.
                assert (await _list(call, reader)).json()["counts"]["everyone"] is None

    async def test_a_role_does_not_widen_what_a_reader_sees(self, w):
        """D-EA-14 point 2, D87 rule 4. A reader with the role who cannot see the
        task gets no row and no answer, and 404 and not 403 on the write."""
        async with _session(w.p) as call:
            run, _ = await _ask(w, call, w.task_secret)
            got = await _list(call, w.stranger, scope="everyone")
            assert got.status_code == 200, got.text
            assert got.json()["rows"] == [] and got.json()["counts"]["everyone"] == 0
            answered = await _act(call, w.stranger, run, type="prompt", selected=["pg"])
            assert answered.status_code == 404, answered.text

    async def test_m4_a_question_of_another_organization_never_shows(self, w):
        async with _session(w.p) as call:
            run_a, _ = await _ask(w, call)
            run_b, _ = await _ask(w, call, w.task_b, agent=w.agent_b)
            # Through the route, under RLS, each org reads its own.
            assert [r["run_id"] for r in await _rows(call, w.admin, scope="everyone")] == [run_a]
            assert [r["run_id"] for r in await _rows(call, w.b_admin, scope="everyone")] == [run_b]
            assert [r["run_id"] for r in await _rows(call, w.b_member)] == [run_b]
            assert (await _list(call, w.admin, project=w.proj_b["id"])).status_code == 404
            # The SQL itself names the tenant, with no RLS to lean on: the admin
            # engine bypasses RLS, and the read of org A still holds only org A.
            vis = core.Visibility(unrestricted=True, email=ADMIN, groups=(),
                                  organization_id=w.org)
            with w.p.admin_engine.connect() as c:
                found = c.execute(text(agent_questions.questions_sql("TRUE", vis)),
                                  {**vis.params, "me": ADMIN}).fetchall()
            assert [str(r.run_id) for r in found] == [run_a]

    async def test_a_reader_with_a_grant_only_reads_their_own_project(self, w):
        """§7 rule 1. The creator of a task in a project they cannot open gets no
        row of it: ``task_visibility_clause`` holds for them too."""
        lost = _person(LOST, w.org)
        async with _session(w.p) as call:
            await _ask(w, call, w.task_secret)
            assert await _rows(call, lost) == []


@_DB_GATE
class TestThePath:

    async def test_m8_the_path_stops_at_the_first_hidden_parent(self, w):
        async with _session(w.p) as call:
            await _ask(w, call)
            [row] = await _rows(call, w.creator)
            assert _path_names(row) == ["Application", "AI Studio"]
            assert "Metorite" not in str(row)
            # A reader who sees the whole tree gets the whole path.
            portfolio = _person(PORTFOLIO, w.org, roles=("admin",), unrestricted=True)
            [row] = await _rows(call, portfolio, scope="everyone")
            assert _path_names(row) == ["Metorite", "Application", "AI Studio"]

    async def test_a_visible_grandparent_above_a_hidden_parent_does_not_show(self, w):
        async with _session(w.p) as call:
            await _ask(w, call, w.task_deep)
            [row] = await _rows(call, w.creator)
            assert _path_names(row) == ["Deep"]
            assert "Sealed name" not in str(row)

    async def test_a_project_the_reader_cannot_see_gives_an_empty_path(self, w):
        """The reader holds this task as its assignee, and the project stays
        hidden, so no name shows."""
        async with _session(w.p) as call:
            await _ask(w, call, w.task_secret)
            [row] = await _rows(call, w.elsewhere)
            assert row["task_title"] == "Hidden" and row["why"] == "assigned"
            assert row["project_path"] == []
            assert "Secret name" not in str(row)

    async def test_the_walk_is_the_one_text_of_core(self):
        """§12.8, "it adds no third walk". The verdict of a tree and the path
        read one CTE, and the route writes no walk of its own."""
        chain = core._ancestor_chain_sql("id = CAST(:pid AS uuid)")
        assert core._ANCESTOR_RUNNABLE_SQL.startswith(chain)
        source = inspect.getsource(core.visible_project_paths)
        assert "_ancestor_chain_sql(" in source and "project_clause" in source
        assert "WITH RECURSIVE" not in inspect.getsource(agent_questions)


@_DB_GATE
class TestTheQuery:

    async def test_the_project_filter_keeps_the_project_and_its_subprojects(self, w):
        async with _session(w.p) as call:
            run, _ = await _ask(w, call, w.task)
            run2, _ = await _ask(w, call, w.task_other)
            assert {r["run_id"] for r in await _rows(call, w.creator)} == {run, run2}
            assert [r["run_id"] for r in await _rows(call, w.creator, project=w.mid["id"])] == [run]
            assert [r["run_id"] for r in await _rows(call, w.creator, project=w.leaf["id"])] == [run]
            kept = await _list(call, w.creator, project=w.other["id"])
            assert [r["run_id"] for r in kept.json()["rows"]] == [run2]
            assert kept.json()["counts"]["mine"] == 1 and kept.json()["total"] == 1

    async def test_a_project_the_reader_cannot_see_answers_404(self, w):
        async with _session(w.p) as call:
            await _ask(w, call)
            for pid in (w.space["id"], w.secret["id"], "not-a-uuid",
                        "7f1d0b8a-1c2e-4a4b-9d3e-2f6a1b0c9d8e"):
                got = await _list(call, w.creator, project=pid)
                assert got.status_code == 404, (pid, got.text)
            assert (await _list(call, w.portfolio, project=w.space["id"])).status_code == 200

    async def test_sort_offset_limit_and_capped(self, w):
        async with _session(w.p) as call:
            first, _ = await _ask(w, call, w.task, question="First")
            second, _ = await _ask(w, call, w.task_other, question="Second")
            third, _ = await _ask(w, call, w.task_third, question="Third")
            order = [first, second, third]
            got = await _list(call, w.creator)
            assert [r["run_id"] for r in got.json()["rows"]] == order
            assert got.json()["total"] == 3 and got.json()["capped"] is False
            got = await _list(call, w.creator, sort="newest")
            assert [r["run_id"] for r in got.json()["rows"]] == order[::-1]
            got = await _list(call, w.creator, limit=1)
            assert [r["run_id"] for r in got.json()["rows"]] == [first]
            assert got.json()["capped"] is True and got.json()["total"] == 3
            got = await _list(call, w.creator, offset=1, limit=1)
            assert [r["run_id"] for r in got.json()["rows"]] == [second]
            assert (got.json()["offset"], got.json()["limit"]) == (1, 1)
            assert got.json()["capped"] is True
            got = await _list(call, w.creator, offset=2, limit=1)
            assert got.json()["capped"] is False
            # A count with no page: what the sidebar badge reads.
            got = await _list(call, w.creator, limit=0)
            assert got.json()["rows"] == [] and got.json()["counts"]["mine"] == 3
            assert got.json()["capped"] is True

    @pytest.mark.parametrize("query", [
        {"limit": 201}, {"limit": -1}, {"offset": -1}, {"sort": "sideways"}, {"scope": "all"},
    ])
    async def test_a_bad_query_is_a_422(self, w, query):
        async with _session(w.p) as call:
            assert (await _list(call, w.creator, **query)).status_code == 422

    async def test_the_largest_page_is_200(self, w):
        async with _session(w.p) as call:
            got = await _list(call, w.creator, limit=200)
            assert got.status_code == 200 and got.json()["limit"] == 200


# ═══════════════════════════════════════════════════════════════════════════
# R8: the write rule
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheWriteRule:

    async def test_m2_a_viewer_outside_the_set_gets_403_on_an_answer(self, w):
        """Acceptance 2, the write half. Options and free text, and nothing moves."""
        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
            for body in ({"selected": ["pg"]}, {"body": "Use Postgres"}):
                got = await _act(call, w.viewer, run, type="prompt", **body)
                assert got.status_code == 403, got.text
                assert got.json()["detail"] == FORBIDDEN_ANSWER.format(name=MEMBER)
            assert await _status(call, w.creator, run) == ("awaiting", "awaiting")
            with w.p.admin_engine.connect() as c:
                written = c.execute(text(
                    "SELECT count(*) FROM pm_agent_run_activities WHERE type = 'prompt'")).scalar()
            assert written == 0

    async def test_the_refusal_names_the_member_by_name(self, w):
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE app_user SET display_name = 'Priya Member' WHERE email = :e"),
                      {"e": MEMBER})
        try:
            async with _session(w.p) as call:
                run, _ = await _ask(w, call)
                got = await _act(call, w.viewer, run, type="prompt", selected=["pg"])
                assert got.json()["detail"] == FORBIDDEN_ANSWER.format(name="Priya Member")
        finally:
            with w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE app_user SET display_name = :e WHERE email = :e"),
                          {"e": MEMBER})

    @pytest.mark.parametrize("who", ["creator", "assignee", "member", "owner", "admin", "manager"])
    async def test_each_of_the_set_answers(self, w, who):
        reader = getattr(w, who)
        async with _session(w.p) as call:
            run, question = await _ask(w, call)
            got = await _act(call, reader, run, type="prompt", selected=["pg"])
            assert got.status_code == 201, got.text
            assert got.json()["activity"]["in_reply_to"] == question
            assert got.json()["run"]["status"] == "in_progress"

    async def test_a_creator_written_in_another_case_answers(self, w):
        """``created_by`` holds the actor as written. The address test folds it,
        and the session's spelling does not matter either."""
        loud = _person(CREATOR.upper(), w.org)
        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
            got = await _act(call, loud, run, type="prompt", selected=["pg"])
            assert got.status_code == 201, got.text

    async def test_m6_the_list_and_the_route_read_one_answer_set(self, w):
        """The list never offers a button that the route refuses, and it never
        hides one that the route takes. One reader at a time, on a new question."""
        readers = (w.creator, w.assignee, w.member, w.owner, w.admin, w.manager, w.viewer)
        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
            for reader in readers:
                [row] = await _read_every(reader)
                got = await _act(call, reader, run, type="prompt", selected=["pg"])
                assert (got.status_code == 201) is row["can_answer"], reader.email
                assert got.status_code in (201, 403), got.text
                if got.status_code == 201:
                    await _ask_again(w, call, run)

    async def test_m7_a_stop_stays_open_to_a_viewer(self, w):
        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
            got = await _act(call, w.viewer, run, type="prompt", signal="stop")
            assert got.status_code == 201, got.text
            assert got.json()["run"]["status"] == "stopping"
            assert got.json()["run"]["stopped_by"] == VIEWER

    async def test_a_reply_to_a_run_that_is_not_awaiting_stays_open(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call, user=w.agent, task=w.task)
            rid = run["id"]
            assert run["status"] == "in_progress"
            got = await _act(call, w.viewer, rid, type="prompt", body="Also add an index")
            assert got.status_code == 201, got.text
            assert got.json()["run"]["status"] == "in_progress"
            # An answered question: the run moved on, so a second reply answers
            # nothing and stays open too.
            await _ask_again(w, call, rid)
            await _act(call, w.creator, rid, type="prompt", selected=["pg"])
            got = await _act(call, w.viewer, rid, type="prompt", body="One more thing")
            assert got.status_code == 201, got.text
            # A finished run opens again for any viewer.
            done = await call(w.agent, "POST", f"/projects/agent-runs/{rid}/finish", {})
            assert done.json()["run"]["status"] == "completed"
            got = await _act(call, w.viewer, rid, type="prompt", body="Please look again")
            assert got.status_code == 201, got.text
            assert got.json()["run"]["status"] == "in_progress"

    async def test_a_reply_to_a_finished_run_stays_open_with_its_question_open(self, w):
        """The one case that needs the status test of the rule. An agent may
        finish from ``awaiting``, so the run is ``completed`` and its question
        is still open. The run does not wait, so the reply stays open to a
        viewer (D-EA-10 point 1). Narrowing it would be a new decision."""
        async with _session(w.p) as call:
            run, question = await _ask(w, call)
            done = await call(w.agent, "POST", f"/projects/agent-runs/{run}/finish", {})
            assert done.json()["run"]["status"] == "completed"
            assert done.json()["run"]["open_elicitation"] is None
            with w.p.admin_engine.connect() as c:
                still_open = c.execute(text(
                    "SELECT count(*) FROM pm_agent_run_activities a "
                    "WHERE a.id = CAST(:q AS uuid) AND NOT EXISTS ("
                    "SELECT 1 FROM pm_agent_run_activities p WHERE p.in_reply_to = a.id)"),
                    {"q": question}).scalar()
            assert still_open == 1
            assert await _rows(call, w.creator) == []
            got = await _act(call, w.viewer, run, type="prompt", body="Please look again")
            assert got.status_code == 201, got.text
            assert got.json()["run"]["status"] == "in_progress"

    async def test_a_comment_stays_open_and_answers_nothing(self, w):
        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
            got = await call(w.viewer, "POST", f"/projects/tasks/{w.task}/comments",
                             {"body": "A note for the team"})
            assert got.status_code == 201, got.text
            assert await _status(call, w.creator, run) == ("awaiting", "awaiting")
            assert len(await _rows(call, w.creator)) == 1

    async def test_a_viewer_who_cannot_see_the_task_still_gets_404(self, w):
        restricted = _person(VIEWER, w.org)
        async with _session(w.p) as call:
            run, _ = await _ask(w, call, w.task_secret)
            got = await _act(call, restricted, run, type="prompt", selected=["pg"])
            assert got.status_code == 404, got.text

    async def test_the_typed_archive_rule_does_not_change(self, w):
        """The generic route answers as it did, for each person. The approval
        stays with the run's member, and a role does not open it."""
        async with _session(w.p) as call:
            asked = await call(w.agent, "POST", f"/projects/tasks/{w.task}/archive-request",
                               {"idempotency_key": _key()})
            run = asked.json()["run"]["id"]
            for reader in (w.viewer, w.creator, w.admin):
                got = await _act(call, reader, run, type="prompt", selected=["approve"])
                assert got.status_code == 422, got.text
                got = await _act(call, reader, run, type="prompt", body="Yes")
                assert got.status_code == 409, got.text
            for reader in (w.creator, w.admin, w.viewer):
                got = await call(reader, "POST", f"/projects/agent-runs/{run}/archive-approval",
                                 {"decision": "decline", "idempotency_key": _key()})
                assert got.status_code == 403, got.text
            got = await call(w.member, "POST", f"/projects/agent-runs/{run}/archive-approval",
                             {"decision": "decline", "idempotency_key": _key()})
            assert got.status_code == 200, got.text

    async def test_a_replay_returns_its_first_row(self, w, monkeypatch):
        """R5 holds through the rule. A viewer answered while the flag was off,
        the agent asked again, and the flag is now on. A retry of that key writes
        nothing. A new key from the same viewer is an answer, and gets 403."""
        key = _key()
        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
            _flag_off(monkeypatch)
            first = await _act(call, w.viewer, run, type="prompt", selected=["pg"],
                               idempotency_key=key)
            assert first.status_code == 201, first.text
            await _ask_again(w, call, run)
            monkeypatch.setattr(get_settings(), "agent_questions_orgs", "*")
            again = await _act(call, w.viewer, run, type="prompt", selected=["pg"],
                               idempotency_key=key)
            assert again.status_code == 200, again.text
            assert again.json()["created"] is False
            assert again.json()["activity"]["id"] == first.json()["activity"]["id"]
            fresh = await _act(call, w.viewer, run, type="prompt", selected=["pg"])
            assert fresh.status_code == 403, fresh.text

    async def test_a_marked_run_still_gets_the_no_person_refusal_first(self, w):
        """AUTO-7a stays as it is: the mark wins over this rule."""
        from acb_common.no_person import NO_PERSON_PRESENT, no_person_scope

        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
        payload = agent_runs.ActivityIn(type="prompt", selected=["pg"], idempotency_key=_key())
        async with _session(w.p):
            scope = bind_tenant(w.org)
            try:
                async with core._tenant_session() as db:
                    locked = await agent_runs._lock_run(db, run)
                    with no_person_scope(), pytest.raises(agent_runs.HTTPException) as refused:
                        await agent_runs._refuse_an_outside_answer(db, locked, w.viewer, payload)
            finally:
                release_tenant(scope)
        assert refused.value.status_code == 403
        assert refused.value.detail["error"] == NO_PERSON_PRESENT

    async def test_a_start_only_prompts_the_callers_own_run(self, w):
        """The audit's pin. ``start_agent_run`` writes a member ``prompt`` on an
        open run with no check of this rule. It may, because the gate hands it
        only the caller's OWN run, so the caller is the run's member and sits in
        the set. Any other caller gets 409, and no prompt is written."""
        async with _session(w.p) as call:
            run, question = await _ask(w, call, w.task_other, agent=w.agent_viewer)
            mine = await call(w.viewer, "POST", f"/projects/tasks/{w.task_other}/agent-runs",
                              {"connection_id": w.conn_viewer, "body": "Use Postgres",
                               "idempotency_key": _key()})
            assert mine.status_code == 200, mine.text
            assert mine.json()["run"]["id"] == run
            assert mine.json()["run"]["member_email"] == VIEWER
            assert mine.json()["run"]["status"] == "in_progress"
            with w.p.admin_engine.connect() as c:
                reply = c.execute(text(
                    "SELECT in_reply_to, actor FROM pm_agent_run_activities "
                    "WHERE type = 'prompt' AND run_id = CAST(:r AS uuid)"), {"r": run}).one()
            assert str(reply.in_reply_to) == question and reply.actor == VIEWER

            run2, _ = await _ask(w, call, w.task)
            held = await call(w.viewer, "POST", f"/projects/tasks/{w.task}/agent-runs",
                              {"connection_id": w.conn_viewer, "body": "Let me answer",
                               "idempotency_key": _key()})
            assert held.status_code == 409, held.text
            assert await _status(call, w.creator, run2) == ("awaiting", "awaiting")

    async def test_a6_an_answer_names_its_author(self, w):
        async with _session(w.p) as call:
            run, _ = await _ask(w, call)
            got = await _act(call, w.admin, run, type="prompt", selected=["pg"])
            assert got.status_code == 201, got.text
            activity = got.json()["activity"]
            assert activity["actor"] == ADMIN
            read = await call(w.creator, "GET", f"/projects/agent-runs/{run}")
            prompts = [a for a in read.json()["activities"] if a["type"] == "prompt"]
            assert [a["actor"] for a in prompts] == [ADMIN]
            timeline = await call(w.creator, "GET", f"/projects/tasks/{w.task}/timeline")
            assert timeline.status_code == 200, timeline.text
            with w.p.admin_engine.connect() as c:
                author = c.execute(text(
                    "SELECT created_by FROM pm_activities WHERE id = CAST(:a AS uuid)"),
                    {"a": activity["pm_activity_id"]}).scalar()
            assert author == ADMIN


# ═══════════════════════════════════════════════════════════════════════════
# R8: the notification
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheBell:

    async def test_the_creator_joins_the_audience(self, w):
        async with _session(w.p) as call:
            await _ask(w, call)
        assert _bells(w) == sorted([CREATOR, ASSIGNEE, MEMBER])

    async def test_m5_a_creator_who_is_an_assignee_gets_one_bell(self, w):
        """Acceptance 5. Two roles in one person, in two spellings, one row."""
        async with _session(w.p) as call:
            await _ask(w, call, w.task_both)
        assert _bells(w) == sorted([CREATOR, MEMBER])

    async def test_a_task_with_no_creator_address_adds_nobody(self, w):
        async with _session(w.p) as call:
            await _ask(w, call, w.task_system)
        assert _bells(w) == [MEMBER]

    async def test_a_creator_who_lost_the_grant_gets_no_bell(self, w):
        """``notify`` drops each person who cannot see the task."""
        async with _session(w.p) as call:
            await _ask(w, call, w.task_secret)
        assert LOST not in _bells(w)
        assert ELSEWHERE in _bells(w)

    async def test_a_typed_archive_request_still_notifies_its_member_only(self, w):
        async with _session(w.p) as call:
            asked = await call(w.agent, "POST", f"/projects/tasks/{w.task}/archive-request",
                               {"idempotency_key": _key()})
            assert asked.status_code == 201, asked.text
        assert _bells(w) == [MEMBER]

    async def test_a_question_open_at_the_flip_gets_no_bell(self, w, monkeypatch):
        """The notification stays prospective: no backfill."""
        _flag_off(monkeypatch)
        async with _session(w.p) as call:
            await _ask(w, call)
            assert _bells(w) == sorted([ASSIGNEE, MEMBER])
            monkeypatch.setattr(get_settings(), "agent_questions_orgs", "*")
            assert len(await _rows(call, w.creator)) == 1
        assert _bells(w) == sorted([ASSIGNEE, MEMBER])


# ═══════════════════════════════════════════════════════════════════════════
# R8: "Waiting on me" reads the one text
# ═══════════════════════════════════════════════════════════════════════════


async def _waiting_tasks(call: Any, user: Any, w: Any, *, project: str | None = None) -> set[str]:
    got = await call(
        user, "GET",
        f"/projects/tasks?project_id={project or w.other['id']}"
        "&include_subtree=true&agent_focus=waiting",
    )
    assert got.status_code == 200, got.text
    return {row["id"] for row in got.json()["rows"]}


def _move(w: Any, task: str, category: str) -> None:
    """Put *task* of the project ``other`` in its lane of *category*."""
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET status_id = CAST(:s AS uuid) "
                       "WHERE id = CAST(:t AS uuid)"),
                  {"s": w.other["lanes"][category], "t": task})


async def _waiting_count(call: Any, user: Any, w: Any, *, project: str | None = None) -> int:
    got = await call(user, "GET", f"/projects/{project or w.other['id']}/agent-summary")
    assert got.status_code == 200, got.text
    return got.json()["waiting_on_you"]


@_DB_GATE
class TestTheFocus:

    async def _agree(self, w: Any, call: Any, want: dict[str, int]) -> None:
        """For each reader, "For me", the "Waiting on me" filter and the strip
        count read one number."""
        for name, count in want.items():
            reader = getattr(w, name)
            listed = await _rows(call, reader, project=w.other["id"])
            assert len(listed) == count, name
            assert await _waiting_tasks(call, reader, w) == (
                {w.task_other} if count else set()), name
            assert await _waiting_count(call, reader, w) == count, name

    async def test_waiting_on_me_and_for_me_agree_with_the_flag_on(self, w):
        """A free question, a typed archive request, and a question on a done
        task. The fix round of the review adds the second and the third."""
        async with _session(w.p) as call:
            run, _ = await _ask(w, call, w.task_other)
            await self._agree(w, call, {"creator": 1, "member": 1, "viewer": 0})
            with w.p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM pm_agent_runs WHERE id = CAST(:r AS uuid)"),
                          {"r": run})
            asked = await call(w.agent, "POST", f"/projects/tasks/{w.task_other}/archive-request",
                               {"idempotency_key": _key()})
            assert asked.status_code == 201, asked.text
            # Fix round 2: a typed request waits on the run's member only.
            await self._agree(w, call, {"creator": 0, "member": 1, "viewer": 0})
            with w.p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM pm_agent_runs"))
            await _ask(w, call, w.task_other)
            _move(w, w.task_other, "done")
            try:
                await self._agree(w, call, {"creator": 0, "member": 0, "viewer": 0})
            finally:
                _move(w, w.task_other, "todo")

    async def test_a_typed_request_waits_on_its_member_only(self, w):
        """The first proof of the review, turned into a fence, with the rule of
        fix round 2. Before round 1 the creator read ``for_me=0 waiting_on_you=1``.
        Only the run's member approves a typed request (§12.1). So it is in
        "For me" and "Waiting on me" for that member, and for nobody else.

        The task has a creator, an assignee and a run member who are three
        people, so each one of them reads it once."""
        leaf = w.leaf["id"]
        async with _session(w.p) as call:
            asked = await call(w.agent, "POST", f"/projects/tasks/{w.task}/archive-request",
                               {"idempotency_key": _key()})
            assert asked.status_code == 201, asked.text
            [row] = await _rows(call, w.member, project=leaf)
            assert row["task_id"] == w.task
            assert row["why"] == "my_agent" and row["can_answer"] is True
            # Fix round 3: the server types the row, so EA-18b sends the answer
            # to `archive-approval` and never derives the kind itself.
            assert row["question"]["kind"] == "archive_request"
            assert await _waiting_tasks(call, w.member, w, project=leaf) == {w.task}
            assert await _waiting_count(call, w.member, w, project=leaf) == 1
            for reader in (w.creator, w.assignee, w.owner, w.admin, w.manager):
                assert await _rows(call, reader, project=leaf) == [], reader.email
                assert await _waiting_tasks(call, reader, w, project=leaf) == set(), reader.email
                assert await _waiting_count(call, reader, w, project=leaf) == 0, reader.email
            assert await _rows(call, w.admin, scope="everyone", project=leaf) == []
            # A reviewer who is the run's member reads it on both tabs, because
            # "For me" is a part of "Everyone".
            reviewer = _person(MEMBER, w.org, roles=("admin",))
            got = await _list(call, reviewer, scope="everyone", project=leaf)
            assert [r["task_id"] for r in got.json()["rows"]] == [w.task]
            assert got.json()["counts"] == {"mine": 1, "everyone": 1}
        assert _bells(w) == [MEMBER]

    async def test_a_question_on_a_done_task_counts_in_neither(self, w):
        """The second proof of the review, turned into a fence. Before the fix
        the creator read ``for_me=0 waiting_on_you=1``."""
        async with _session(w.p) as call:
            await _ask(w, call, w.task_other)
            _move(w, w.task_other, "done")
            try:
                listed = await _rows(call, w.creator, project=w.other["id"])
                assert len(listed) == 0
                assert await _waiting_count(call, w.creator, w) == 0
                assert await _waiting_tasks(call, w.creator, w) == set()
            finally:
                _move(w, w.task_other, "todo")

    async def test_waiting_on_me_stays_as_it_was_with_the_flag_off(self, w, monkeypatch):
        _flag_off(monkeypatch)
        async with _session(w.p) as call:
            await _ask(w, call, w.task_held)
            assert await _waiting_tasks(call, w.creator, w) == set()
            assert await _waiting_count(call, w.creator, w) == 0
            assert await _waiting_tasks(call, w.member, w) == {w.task_held}
            assert await _waiting_count(call, w.member, w) == 1
            # With the flag off, the member still counts a typed request, as before.
            with w.p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM pm_agent_runs"))
            asked = await call(w.agent, "POST", f"/projects/tasks/{w.task_held}/archive-request",
                               {"idempotency_key": _key()})
            assert asked.status_code == 201, asked.text
            assert await _waiting_tasks(call, w.member, w) == {w.task_held}
            assert await _waiting_count(call, w.member, w) == 1


# ═══════════════════════════════════════════════════════════════════════════
# R8: the flags
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheFlags:

    async def test_a7_with_the_flag_off_the_route_answers_404(self, w, monkeypatch):
        _flag_off(monkeypatch)
        async with _session(w.p) as call:
            await _ask(w, call)
            for reader in (w.creator, w.admin, w.viewer):
                assert (await _list(call, reader)).status_code == 404, reader.email
            assert (await _list(call, w.admin, scope="everyone")).status_code == 404

    async def test_a7_with_the_flag_off_any_viewer_may_answer(self, w, monkeypatch):
        """D-EA-10 point (1) stays the rule of an org that the flag does not hold."""
        _flag_off(monkeypatch)
        async with _session(w.p) as call:
            run, question = await _ask(w, call)
            got = await _act(call, w.viewer, run, type="prompt", selected=["pg"])
            assert got.status_code == 201, got.text
            assert got.json()["activity"]["in_reply_to"] == question

    async def test_a7_with_the_flag_off_the_audience_has_no_creator(self, w, monkeypatch):
        _flag_off(monkeypatch)
        async with _session(w.p) as call:
            await _ask(w, call)
        assert _bells(w) == sorted([ASSIGNEE, MEMBER])

    async def test_the_flag_reads_the_org_of_the_session(self, w, monkeypatch):
        """One org on the list opens its own list, rule and audience, and no
        other. No parameter can name another org."""
        monkeypatch.setattr(get_settings(), "agent_questions_orgs", w.org)
        async with _session(w.p) as call:
            await _ask(w, call)
            run_b, _ = await _ask(w, call, w.task_b, agent=w.agent_b)
            assert (await _list(call, w.creator)).status_code == 200
            assert (await _list(call, w.b_member)).status_code == 404
            other = _person("ea18-b-viewer@questions.test", w.org_b, unrestricted=True)
            b.member(w.p, w.org_b, other.email)
            got = await _act(call, other, run_b, type="prompt", selected=["pg"])
            assert got.status_code == 201, got.text
        assert CREATOR in _bells(w)
        monkeypatch.setattr(get_settings(), "agent_questions_orgs", w.org_b)
        async with _session(w.p) as call:
            assert (await _list(call, w.creator)).status_code == 404
            assert (await _list(call, w.b_member)).status_code == 200

    async def test_with_the_runs_flag_off_the_list_is_empty(self, w, monkeypatch):
        async with _session(w.p) as call:
            await _ask(w, call)
            monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
            got = await _list(call, w.creator)
            assert got.status_code == 200, got.text
            body = got.json()
            assert body["rows"] == [] and body["counts"] == {"mine": 0, "everyone": None}
            assert body["total"] == 0 and body["capped"] is False
            got = await _list(call, w.admin, scope="everyone")
            assert got.json()["counts"] == {"mine": 0, "everyone": 0}
            # The gates hold with the runs flag off too.
            assert (await _list(call, w.viewer, scope="everyone")).status_code == 403
            assert (await _list(call, w.creator, project=w.space["id"])).status_code == 404
