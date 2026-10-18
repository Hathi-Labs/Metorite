"""WS-55 EA-13e — oversight: the project strip and the agent-worked flag.

Spec: ``project-docs/specs/external_agent_access.md`` §12.2 item 4 and row
EA-13e of §15 (EA3-43, EA3-49), acceptance S1 to S5.

**R8**, a real Postgres, through the real ``/projects`` router as the
non-privileged role of the phase-4 rehearsal, on a database with the whole
ladder and two organizations:

* **S1** each of the five counts holds on a planted project, and a task in a
  subproject counts. ``working`` reads three states, ``waiting_on_you`` reads
  both arms of the rule (the run's member and an assignee), ``in_review`` reads
  an open task only, and ``merged_this_week`` stops at 7 days.
* **S2** a task that the caller cannot see counts nowhere.
* **S3** organization B reads none of the counts of A.
* **S4** ``agent_worked`` on the rows equals ``agent_worked.reason`` for each
  planted task.
* **S5** the manifest, chat coverage and field parity fences pass (the three
  files that ``test_s5_*`` runs by name).
* **S6** the runs flag off: the three state counts read 0, the two pull
  request counts do not.

R7, the mutations. Each one turns a test here red (run 2026-10-18, each after a
commit, because ``git checkout`` restores HEAD):

* **M1** the ``sub`` CTE keeps the project alone, with no descendants
  -> ``test_s1_the_five_counts_hold_and_a_subproject_task_counts``.
* **M2** ``WORKING_STATES`` drops ``stale`` -> the same test.
* **M3** ``waiting_on_you`` drops the assignee arm
  -> ``test_s1_waiting_on_you_reads_both_arms_of_the_rule``.
* **M4** ``waiting_on_you`` drops the member arm -> the same test.
* **M5** ``in_review`` drops the closed-category predicate
  -> ``test_s1_in_review_counts_an_open_task_with_an_open_or_draft_pull_request``.
* **M6** ``merged_this_week`` drops the 7-day window
  -> ``test_s1_merged_this_week_counts_references_in_the_last_7_days``.
* **M7** ``task_visibility_clause`` is left out of ``scope``
  -> ``test_s2_a_task_the_caller_cannot_see_counts_nowhere`` and S3.
* **M8** the pick rule of ``agent_state_sql`` loses the ``awaiting`` priority
  -> ``test_s1_the_strip_and_the_card_read_one_state``.
* **M9** ``attach_agent_worked`` fills ``True`` for every row
  -> ``test_s4_agent_worked_on_the_rows_equals_the_reason``.
* **M10** the flag check dropped from the route
  -> ``test_s6_with_the_runs_flag_off_the_state_counts_read_zero``.
* **M11** the route reads ``me`` from a query parameter (a ``me`` argument)
  -> ``test_the_caller_is_never_named_by_the_request``.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

A_EMAIL = "ea13e-a@summary-r8.test"
B_EMAIL = "ea13e-b@summary-r8.test"
OTHER = "ea13e-other@summary-r8.test"
M_EMAIL = "ea13e-m@summary-r8.test"
_ROOT = Path(__file__).resolve().parents[2]
_ZERO = {"queued": 0, "working": 0, "waiting_on_you": 0, "in_review": 0,
         "merged_this_week": 0}


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = b._principal(A_EMAIL, promoted.org_a)
    ns.b = b._principal(B_EMAIL, promoted.org_b)
    ns.m = b._principal(M_EMAIL, promoted.org_a, unrestricted=False)
    b.member(promoted, promoted.org_a, OTHER)
    b.member(promoted, promoted.org_a, M_EMAIL)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    b.clear(world.p)
    # A new tree for each test: a root project and a subproject of it.
    world.root = b.project(world.p, world.p.org_a, A_EMAIL)
    world.sub = b.project(world.p, world.p.org_a, A_EMAIL, parent=world.root)
    return world


def _t(w: Any, proj: dict | None = None, **kwargs: Any) -> str:
    return b.task(w.p, w.p.org_a, A_EMAIL, proj or w.root, **kwargs)


async def _summary(w: Any, user: Any, project: str) -> tuple[int, Any]:
    async with b._session(w.p) as call:
        got = await call(user, "GET", f"/projects/{project}/agent-summary")
    return got.status_code, got.json()


async def _counts(w: Any, user: Any = None, project: str | None = None) -> dict[str, int]:
    status, body = await _summary(w, user or w.a, project or w.root["id"])
    assert status == 200, body
    return body


@_DB_GATE
class TestTheFiveCounts:
    async def test_s1_the_five_counts_hold_and_a_subproject_task_counts(self, w):
        org = w.p.org_a
        b.run(w.p, org, _t(w), A_EMAIL, "created")  # queued
        b.run(w.p, org, _t(w), A_EMAIL, "in_progress")  # working
        # answered: the member wrote after the agent
        b.run(w.p, org, _t(w), A_EMAIL, "in_progress", last_agent="NULL")
        b.run(w.p, org, _t(w), A_EMAIL, "in_progress",  # stale
              last_activity="now() - interval '10 minutes'")
        b.run(w.p, org, _t(w, w.sub), A_EMAIL, "in_progress")  # a subproject task
        b.run(w.p, org, _t(w), A_EMAIL, "failed")  # no count
        b.run(w.p, org, _t(w), A_EMAIL, "completed")  # no count
        b.run(w.p, org, _t(w), A_EMAIL, "stopping")  # no count
        awaiting = _t(w)
        b.run(w.p, org, awaiting, A_EMAIL, "awaiting")
        got = await _counts(w)
        assert got == {"queued": 1, "working": 4, "waiting_on_you": 1, "in_review": 0,
                       "merged_this_week": 0}

    async def test_s1_the_strip_and_the_card_read_one_state(self, w):
        """An open `awaiting` run of the member and a newer run of another
        member: the card reads the question, and so does the strip."""
        org = w.p.org_a
        t = _t(w)
        b.run(w.p, org, t, A_EMAIL, "awaiting", created="now() - interval '1 hour'")
        b.run(w.p, org, t, OTHER, "in_progress", created="now()")
        got = await _counts(w)
        assert got["waiting_on_you"] == 1 and got["working"] == 0
        async with b._session(w.p) as call:
            row = (await call(w.a, "GET", f"/projects/tasks/{t}")).json()
        assert row["agent_state"] == "awaiting"

    async def test_s1_waiting_on_you_reads_both_arms_of_the_rule(self, w):
        org = w.p.org_a
        mine = _t(w)
        b.run(w.p, org, mine, A_EMAIL, "awaiting")
        assigned = _t(w, assignee=A_EMAIL)
        b.run(w.p, org, assigned, OTHER, "awaiting")
        not_mine = _t(w)
        b.run(w.p, org, not_mine, OTHER, "awaiting")
        assert (await _counts(w))["waiting_on_you"] == 2
        # The other member reads the other half of the same planted data.
        other = b._principal(OTHER, org)
        assert (await _counts(w, other))["waiting_on_you"] == 2

    async def test_s1_in_review_counts_an_open_task_with_an_open_or_draft_pull_request(self, w):
        org = w.p.org_a
        opened, drafted, done, merged_only = _t(w), _t(w, w.sub), _t(w, category="done"), _t(w)
        b.ref(w.p, org, opened, 1, "open")
        b.ref(w.p, org, opened, 2, "open")  # two references, one task
        b.ref(w.p, org, drafted, 3, "draft")
        b.ref(w.p, org, done, 4, "open")  # the task is done
        b.ref(w.p, org, merged_only, 5, "merged", merged="now() - interval '30 days'")
        got = await _counts(w)
        assert got["in_review"] == 2
        assert got["merged_this_week"] == 0

    async def test_s1_merged_this_week_counts_references_in_the_last_7_days(self, w):
        org = w.p.org_a
        one, two = _t(w), _t(w, w.sub)
        b.ref(w.p, org, one, 1, "merged", merged="now() - interval '2 days'")
        b.ref(w.p, org, one, 2, "merged", merged="now() - interval '6 days'")
        b.ref(w.p, org, two, 3, "merged", merged="now() - interval '1 hour'")
        b.ref(w.p, org, two, 4, "merged", merged="now() - interval '8 days'")
        b.ref(w.p, org, two, 5, "closed", merged="now() - interval '1 day'")
        assert (await _counts(w))["merged_this_week"] == 3

    async def test_s1_the_summary_of_a_subproject_counts_only_its_own_tasks(self, w):
        org = w.p.org_a
        b.run(w.p, org, _t(w), A_EMAIL, "created")
        b.run(w.p, org, _t(w, w.sub), A_EMAIL, "created")
        assert (await _counts(w, project=w.sub["id"]))["queued"] == 1
        assert (await _counts(w))["queued"] == 2

    async def test_s1_an_archived_task_counts_nowhere(self, w):
        org = w.p.org_a
        t = _t(w)
        b.run(w.p, org, t, A_EMAIL, "created")
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET archived_at = now() "
                             "WHERE id = CAST(:t AS uuid)"), {"t": t})
        assert await _counts(w) == _ZERO


@_DB_GATE
class TestWhoSeesWhat:
    async def test_s2_a_task_the_caller_cannot_see_counts_nowhere(self, w):
        """The member holds a grant on the root. A sealed subproject is out of
        the grant closure, so its task is out of the count. An unsealed
        subproject counts."""
        org = w.p.org_a
        b.member(w.p, org, M_EMAIL)
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :s, :e)"),
                {"o": org, "p": w.root["id"], "s": M_EMAIL, "e": A_EMAIL})
        sealed = b.project(w.p, org, A_EMAIL, parent=w.root, sealed=True)
        b.run(w.p, org, _t(w, sealed), A_EMAIL, "created")
        b.run(w.p, org, _t(w, w.sub), A_EMAIL, "created")
        assert (await _counts(w, w.m))["queued"] == 1
        # A task that the member holds as an assignee stays visible.
        b.run(w.p, org, _t(w, sealed, assignee=M_EMAIL), A_EMAIL, "created")
        assert (await _counts(w, w.m))["queued"] == 2

    async def test_s2_a_project_the_caller_cannot_see_answers_404(self, w):
        b.run(w.p, w.p.org_a, _t(w), A_EMAIL, "created")
        status, _body = await _summary(w, w.m, w.root["id"])
        assert status == 404

    async def test_s3_organization_b_reads_none_of_the_counts_of_a(self, w):
        org = w.p.org_a
        t = _t(w)
        b.run(w.p, org, t, A_EMAIL, "created")
        b.ref(w.p, org, t, 1, "open")
        # B names the project of A: the same answer as no project.
        status, _ = await _summary(w, w.b, w.root["id"])
        assert status == 404
        # A row of B that points at the task of A never counts for A.
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, "
                "client_via, status, started_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), "
                ":e, :v, 'created', :e)"),
                {"o": w.p.org_b, "t": t, "e": B_EMAIL, "v": b.VIA})
        got = await _counts(w)
        assert got["queued"] == 1 and got["in_review"] == 1
        # B reads its own project: all zeros.
        own = b.project(w.p, w.p.org_b, B_EMAIL)
        assert await _counts(w, w.b, own["id"]) == _ZERO


@_DB_GATE
class TestTheFlag:
    async def test_s6_with_the_runs_flag_off_the_state_counts_read_zero(self, w, monkeypatch):
        from acb_common import get_settings

        org = w.p.org_a
        b.run(w.p, org, _t(w), A_EMAIL, "created")
        b.run(w.p, org, _t(w), A_EMAIL, "awaiting")
        pr = _t(w)
        b.ref(w.p, org, pr, 1, "open")
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        assert await _counts(w) == {**_ZERO, "in_review": 1}


@_DB_GATE
class TestAgentWorkedOnTheRows:
    async def test_s4_agent_worked_on_the_rows_equals_the_reason(self, w):
        from acb_common.db import bind_tenant
        from gateway.db import tenant_session
        from gateway.routes.projects import agent_worked

        org = w.p.org_a
        by_run, by_act, by_other, neither = _t(w), _t(w), _t(w), _t(w)
        b.run(w.p, org, by_run, A_EMAIL, "completed")
        b.mcp_comment(w.p, org, by_act, A_EMAIL)
        b.mcp_comment(w.p, org, by_other, A_EMAIL, via="chat:projects-assistant")
        planted = {by_run: True, by_act: True, by_other: False, neither: False}
        async with b._session(w.p) as call:
            got = await call(w.a, "GET", f"/projects/tasks?project_id={w.root['id']}"
                                         "&include_subtree=true&page_size=100")
        assert got.status_code == 200, got.text
        rows = {r["id"]: r for r in got.json()["rows"]}
        async with b._session(w.p):
            bind_tenant(w.p.org_a)
            async with tenant_session() as db:
                for task, expected in planted.items():
                    why = await agent_worked.reason(
                        db, task, datetime(9999, 1, 1, tzinfo=UTC))
                    assert (why is not None) is expected, task
                    assert rows[task]["agent_worked"] is expected, task
                    assert rows[task]["agent_worked"] is (why is not None), task

    async def test_s4_the_batched_read_is_one_query(self, w):
        from acb_common.db import bind_tenant
        from gateway.db import tenant_session
        from gateway.routes.projects import agent_worked

        tasks = [_t(w) for _ in range(4)]
        b.run(w.p, w.p.org_a, tasks[0], A_EMAIL, "completed")

        class Counting:
            def __init__(self, db: Any) -> None:
                self.db, self.reads = db, 0

            async def execute(self, statement: Any, params: Any = None) -> Any:
                self.reads += 1
                return await self.db.execute(statement, params)

        async with b._session(w.p):
            bind_tenant(w.p.org_a)
            async with tenant_session() as db:
                counting = Counting(db)
                worked = await agent_worked.worked_among(
                    counting, tasks, datetime(9999, 1, 1, tzinfo=UTC))
        assert counting.reads == 1 and worked == {tasks[0]}


# ── The fences that need no database ────────────────────────────────────────


def test_the_caller_is_never_named_by_the_request() -> None:
    """R5 and R11. The route takes the project from the path and the caller
    from the session. No parameter names a member or an organization."""
    from gateway.routes.projects import agent_summary

    names = list(inspect.signature(agent_summary.agent_summary).parameters)
    assert names == ["project_id", "user"]


def test_the_route_is_mounted_and_is_a_class_a_read() -> None:
    from gateway.routes.projects import router
    from skill_projects import manifest

    paths = {(m, r.path) for r in router.routes for m in getattr(r, "methods", ())}
    assert ("GET", "/projects/{project_id}/agent-summary") in paths
    found = [r for r in manifest.MANIFEST if r.path == "/projects/{project_id}/agent-summary"]
    assert [(r.method, r.tool, r.cls) for r in found] == [("GET", "agent_summary", "A")]
    assert manifest.PLANNED["agent_summary"]


def test_the_state_counts_read_the_pill_query() -> None:
    """The strip and the card cannot disagree: the route builds its states from
    ``filters.agent_state_sql``, and the list pill is built from the same text."""
    from gateway.routes.projects import agent_summary, filters

    assert "agent_state_sql" in inspect.getsource(agent_summary)
    assert filters.agent_state_sql(
        "r.task_id = ANY(CAST(:ids AS uuid[]))") == filters._AGENT_STATE_SQL
    assert agent_summary.WORKING_STATES == ("working", "answered", "stale")


def test_the_batched_flag_is_the_one_predicate() -> None:
    """EA3-49: the list read is built from ``AGENT_WORKED_SQL``, not from a copy."""
    from gateway.routes.projects import agent_worked

    batch = agent_worked._BATCH_SQL
    assert agent_worked.AGENT_WORKED_SQL.replace(agent_worked._TASK_PARAM, "t.id") in batch
    assert "aw_task" not in batch


def test_s5_the_three_fences_name_the_route() -> None:
    """S5. The three fences read the manifest, the chat tools and the row
    fields. This test makes sure that they exist, so that a run of the file
    list in the spec cannot pass by reading no file."""
    for name in ("test_projects_chat_coverage.py", "test_projects_field_parity.py",
                 "test_projects_ui_actions.py"):
        assert (_ROOT / "tests" / "unit" / name).is_file(), name
