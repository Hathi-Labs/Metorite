"""WS-55 SW-11a — Needs attention: three kinds of stopped work, and two acts.

Spec: ``project-docs/specs/external_agent_access.md`` §12.7 (Metorite task
#342).

Two halves. The hermetic tests need no database: the shape of the route and
of the resume body, and that the list joins the rules that exist. The R8
tests run the real ``/projects`` router on the phase-4-promoted catalog as
the NON-privileged app role, so FORCE RLS holds. A stalled run comes from the
REAL sweep of SW-10a (``agent_run_sweep.run_org``), twice, with the strand
aged one lease between the two ticks.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_sw11 \\
        uv run pytest tests/unit/test_needs_attention_r8.py -v -rs

**The mutations (R7).** Each one turns the named test red. ``N`` is
``routes/projects/needs_attention.py``, ``F`` is ``filters.py`` and ``R`` is
``agent_runs.py``.

* A1. ``F`` ``open_recovery_sql``: no ``recovered_at IS NOT NULL`` (a
  fingerprint is a stalled row). ``test_k1_near_misses_of_a_stalled_run``.
* A2. ``N`` ``stalled_sql``: no ``NOT task_closed_sql`` (a closed task shows
  twice). ``test_k1_near_misses_of_a_stalled_run``.
* A3. ``N`` ``dangling_sql``: no ``task_closed_sql`` (every open run is
  dangling). ``test_k2_near_misses_of_a_dangling_run``.
* A4. ``N`` ``blocked_sql``: no holder test (an owned blocker is parked).
  ``test_k3_near_misses_of_a_parked_blocker``.
* A5. ``N`` ``blocked_sql``: no ``bs.category = :parked``.
  ``test_k3_near_misses_of_a_parked_blocker``.
* A6. ``N`` ``blocked_sql``: the blocker subquery drops ``blocking_where``
  (a hidden blocker gives a row). ``test_k3_a_hidden_blocker_gives_no_row``.
* A7. ``N`` ``_run_where``: no ``task_visibility_clause``.
  ``test_v1_a_restricted_member_sees_only_what_they_can_open``.
* A8. ``N`` ``needs_attention``: no ``agent_runs_enabled`` check.
  ``test_v5_with_the_runs_flag_off_no_run_kind_reads``.
* A9. ``R`` ``resume_agent_run``: no ``_OPEN_RECOVERY_SQL`` check.
  ``test_r2_resume_refuses_what_it_may_not_take``.
* A10. ``R`` ``resume_agent_run``: no ``may_end_run`` check.
  ``test_r2_resume_refuses_what_it_may_not_take``.
* A11. ``R`` ``resume_agent_run``: write no prompt (only the answer).
  ``test_r1_resume_hands_the_run_back_to_an_agent``.
* A12. ``R`` ``person_may_end_sql``: no assignee arm.
  ``test_v7_can_act_is_the_rule_of_release`` and
  ``test_r3_an_assignee_and_an_admin_may_resume``.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from gateway import agent_run_sweep as sweep
from gateway.routes.projects import agent_runs, filters, needs_attention
from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_agent_run_sweep_r8 import _age_strand, _insert_run, _sweep_org
from tests.unit.test_agent_runs_r8 import _key, _rows
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ALICE = "sw11-alice@attention.test"
CAROL = "sw11-carol@attention.test"
DAVE = "sw11-dave@attention.test"
ERIN = "sw11-erin@attention.test"
MALLORY = "sw11-mallory@attention.test"
BOB = "sw11-bob@attention.test"
LEASE = filters.AGENT_RUN_LEASE_DEFAULT_S


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the shape and the reuse
# ═══════════════════════════════════════════════════════════════════════════


class TestTheShape:

    def test_the_route_takes_no_tenant_and_no_person(self) -> None:
        """R5, R11: the caller and the tenant come from the session. No query
        parameter names an organization or a member."""
        names = set(inspect.signature(needs_attention.needs_attention).parameters)
        assert names == {"project_id", "mine", "offset", "limit", "user"}

    def test_resume_takes_the_key_only(self) -> None:
        assert set(agent_runs.ResumeIn.model_fields) == {"idempotency_key"}
        assert agent_runs.ResumeIn.model_config.get("extra") == "forbid"

    def test_the_list_joins_the_rules_that_exist(self) -> None:
        """No second copy: each kind reads the one text of its rule."""
        stalled = needs_attention.stalled_sql("TRUE", _Vis())
        assert filters.open_recovery_sql("s2", "r") in stalled
        assert filters.task_closed_sql("r") in stalled
        assert agent_runs.person_may_end_sql("r") in stalled
        dangling = needs_attention.dangling_sql("TRUE", _Vis())
        assert filters.task_closed_sql("r") in dangling
        blocked = needs_attention.blocked_sql("OPEN", "BLOCKING")
        assert filters.open_block_sql("l", "bs") in blocked
        from gateway.routes.projects.analytics import holder_label_sql

        assert holder_label_sql() in blocked
        assert filters.open_block_sql("l", "s") in filters._BLOCKER_COUNTS_SQL
        assert filters.open_recovery_sql("s", "r") in agent_runs._OPEN_RECOVERY_SQL


def _Vis() -> Any:
    """The smallest stand-in for ``Visibility`` that the clause reads."""
    return SimpleNamespace(unrestricted=True, agent=None, params={})


_TASK = "7f1d0b8a-1c2e-4a4b-9d3e-2f6a1b0c9d8e"
_RUN = "0a3c5e7f-9b1d-4f2a-8c6e-4d2b0a8f6c4e"
_BLOCKER = "5e4d3c2b-1a09-4f8e-b7d6-c5b4a3928170"


def _chat_rows() -> list[dict[str, Any]]:
    """One row of each kind, in the route's shape."""
    base = {"task_id": _TASK, "title": "Ship «it»\nnow", "project_name": "Ops",
            "can_act": True, "yours": False, "owner": "alice@x.io", "owner_name": "Alice"}
    return [
        {**base, "kind": "stalled", "task_number": 7, "run_id": _RUN, "reason": "agent_quiet",
         "client_name": "Claude Code", "age_hours": 5.2, "action": "resume", "yours": True},
        {**base, "kind": "dangling", "task_number": 8, "run_id": _RUN, "reason": "done",
         "run_status": "awaiting", "age_hours": 50, "action": "close_run", "can_act": False},
        {**base, "kind": "blocked", "task_number": 9, "blocker_id": _BLOCKER,
         "blocker_number": 3, "blocker_title": "Design", "age_hours": 0.5,
         "action": "open_blocker", "owner": None, "owner_name": None},
    ]


class TestTheChatRead:
    """The class A read ``needs_attention`` (spec §12.7, "Chat first")."""

    async def test_it_sends_the_scope_and_the_page_and_prints_each_kind(self, monkeypatch):
        import skill_projects

        from tests.unit._projects_agent_fakes import fake_gateway

        payload = {"rows": _chat_rows(), "counts": {"stalled": 1, "dangling": 1, "blocked": 28},
                   "total": 30, "listed": 30, "offset": 0, "limit": 25, "capped": False}
        calls = fake_gateway(monkeypatch, payload)
        out = await skill_projects.needs_attention(project_id=_TASK, mine=True)
        [call] = calls
        assert (call["method"], call["path"]) == ("GET", "/projects/needs-attention")
        assert call["params"] == {"offset": 0, "limit": 25, "project_id": _TASK, "mine": True}
        assert "30 the member's own items (stalled 1, open runs of closed tasks 1," in out
        assert "«Claude Code» sent nothing for 5 h" in out
        assert "the task is done, and the run is awaiting, quiet for 2 d" in out
        assert "blocked by #3 «Design»" in out
        assert out.count(f"full_id: {_TASK}") == 3
        assert f"run_id: {_RUN}" in out and f"blocker_id: {_BLOCKER}" in out
        assert "(only the run's member, an assignee of the task or an admin can do it)" in out
        assert "no owner" in out and "· yours" in out
        # A title is member text: it is fenced, and its newline forges no row.
        assert "\nnow" not in out
        assert "Showing 1-3 of 30. Call again with offset=3 for the rest." in out

    async def test_an_empty_list_says_so(self, monkeypatch):
        import skill_projects

        from tests.unit._projects_agent_fakes import fake_gateway

        calls = fake_gateway(monkeypatch, {"rows": [], "counts": {}, "total": 0, "listed": 0})
        out = await skill_projects.needs_attention()
        assert "project_id" not in calls[0]["params"] and "mine" not in calls[0]["params"]
        assert "(nothing needs attention)" in out


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════


def _name(c: Any, org: str, email: str, name: str) -> None:
    c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :n, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
        {"e": email, "n": name, "o": org})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    p = promoted
    ns = SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        for email, name in ((ALICE, "Alice Archer"), (CAROL, "Carol Chen"),
                            (DAVE, "Dave Doe"), (ERIN, "Erin Evans"),
                            (MALLORY, "Mallory M")):
            _name(c, ns.a, email, name)
        _name(c, ns.b, BOB, "Bob Baker")
    ns.alice = b._principal(ALICE, ns.a)
    ns.alice_agent = b._principal(
        ALICE, ns.a, agent_connection=b._seed_connection(p, ns.a, ALICE))
    ns.dave = b._principal(DAVE, ns.a)
    ns.erin = b._principal(ERIN, ns.a)
    ns.mallory = b._principal(MALLORY, ns.a, unrestricted=False)
    ns.bob = b._principal(BOB, ns.b)
    from acb_auth import UserContext, UserRole, build_access

    ns.carol_admin = UserContext(
        email=CAROL, role=UserRole.EMPLOYEE, organization_id=ns.a,
        access=build_access(["feature:projects", "data:org:read", "admin:members:read"]))
    return ns


@pytest.fixture
def w(world, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_runs_enabled", True)
    monkeypatch.delenv(filters.AGENT_RUN_LEASE_ENV, raising=False)
    sweep._stopping.clear()
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        c.execute(text("DELETE FROM pm_task_links"))
        c.execute(text("DELETE FROM pm_notifications"))
    return world


def _project(w: Any, *, parent: dict | None = None, grant: str | None = None,
             org: str | None = None, owner: str = ALICE) -> dict[str, Any]:
    """A project with the lanes of every category the kinds read: `todo` and
    `done` from the shared seed, and `backlog` and `cancelled` here."""
    proj = b.project(w.p, org or w.a, owner, parent=parent, grant=grant)
    if parent is None:
        with w.p.admin_engine.begin() as c:
            for pos, category in ((5, "backlog"), (90, "cancelled")):
                sid = str(uuid.uuid4())
                c.execute(text(
                    "INSERT INTO pm_task_statuses (id, organization_id, project_id, name,"
                    " category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                    {"id": sid, "o": org or w.a, "p": proj["id"], "n": category.title(),
                     "c": category, "pos": pos})
                proj["lanes"][category] = sid
    return proj


def _task(w: Any, proj: dict, *, category: str = "todo", assignee: str | None = None,
          title: str = "Fix the extruder", org: str | None = None) -> str:
    return b.task(w.p, org or w.a, ALICE, proj, category=category, assignee=assignee,
                  title=title)


def _link(w: Any, blocker: str, blocked: str, kind: str = "blocks",
          org: str | None = None) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_links (organization_id, source_task_id, target_task_id,"
            " link_type, created_by) VALUES (CAST(:o AS uuid), CAST(:s AS uuid),"
            " CAST(:t AS uuid), :k, :e)"),
            {"o": org or w.a, "s": blocker, "t": blocked, "k": kind, "e": ALICE})


def _move(w: Any, task: str, proj: dict, category: str) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET status_id = CAST(:s AS uuid)"
                       " WHERE id = CAST(:t AS uuid)"),
                  {"s": proj["lanes"][category], "t": task})


def _archive(w: Any, task: str) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET archived_at = now() WHERE id = CAST(:t AS uuid)"),
                  {"t": task})


async def _stalled(w: Any, task: str, *, member: str = ALICE) -> str:
    """A run of *task* with an open recovery record, made by the REAL sweep:
    the first miss, one lease, then the second miss."""
    rid = _insert_run(w, task, member=member, idle_s=3 * 3600)
    first = await _sweep_org(w)
    assert first.nudged >= 1
    _age_strand(w, rid, LEASE + 60)
    second = await _sweep_org(w)
    assert second.recovered >= 1
    [strand] = _strands(w, rid)
    assert strand.recovered_at is not None and strand.cleared_at is None
    return rid


def _strands(w: Any, run: str) -> list[Any]:
    return _rows(w.p, "SELECT * FROM pm_agent_run_strands WHERE run_id = CAST(:r AS uuid)"
                      " ORDER BY nudged_at", r=run)


def _task_state(w: Any, task: str) -> tuple[Any, ...]:
    [row] = _rows(w.p, "SELECT status_id, archived_at FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                  t=task)
    people = sorted(r.assignee for r in _rows(
        w.p, "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)", t=task))
    return (row.status_id, row.archived_at, tuple(people))


async def _list(w: Any, user: Any, project: dict | None = None, **query: Any) -> dict[str, Any]:
    status, body = await _get(w, user, project, **query)
    assert status == 200, body
    return body


async def _get(w: Any, user: Any, project: dict | None = None,
               **query: Any) -> tuple[int, Any]:
    params = dict(query)
    if project is not None:
        params["project_id"] = project["id"]
    qs = "&".join(f"{k}={str(v).lower() if isinstance(v, bool) else v}"
                  for k, v in params.items())
    async with b._session(w.p) as call:
        got = await call(user, "GET", "/projects/needs-attention" + (f"?{qs}" if qs else ""))
    return got.status_code, got.json()


def _kinds(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(r["kind"], r["task_id"]) for r in body["rows"]]


async def _post(w: Any, user: Any, path: str, body: dict | None = None) -> Any:
    async with b._session(w.p) as call:
        return await call(user, "POST", path, body if body is not None else {})


@_DB_GATE
class TestTheKinds:

    async def test_k1_a_stalled_run_is_listed_with_its_owner_and_actions(self, w):
        proj = _project(w)
        task = _task(w, proj)
        rid = await _stalled(w, task)
        body = await _list(w, w.alice, proj)
        [row] = body["rows"]
        assert (row["kind"], row["task_id"], row["run_id"]) == ("stalled", task, rid)
        assert (row["owner"], row["owner_name"]) == (ALICE, "Alice Archer")
        assert (row["action"], row["actions"]) == ("resume", ["resume", "close_run"])
        assert row["reason"] == "agent_quiet" and row["client_name"] == "Claude Code"
        assert row["can_act"] is True and row["yours"] is True
        assert row["age_hours"] >= 3 and row["run_status"] == "in_progress"
        assert body["counts"] == {"stalled": 1, "dangling": 0, "blocked": 0}
        assert body["total"] == 1 and body["capped"] is False

    async def test_k1_near_misses_of_a_stalled_run(self, w):
        """A fingerprint alone, a recovery record that a new activity ended, a
        released run and a run of a closed task are not stalled. The closed
        task's run shows once, as dangling."""
        proj = _project(w)
        nudged, moved, released, closed, kept = (_task(w, proj) for _ in range(5))
        nudged_run = _insert_run(w, nudged, member=ALICE, idle_s=3 * 3600)
        await _sweep_org(w)  # the first miss only
        assert _strands(w, nudged_run)[0].recovered_at is None
        moved_run = await _stalled(w, moved)
        released_run = await _stalled(w, released)
        closed_run = await _stalled(w, closed)
        kept_run = await _stalled(w, kept)
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_agent_runs SET last_activity_at = now()"
                           " WHERE id = CAST(:r AS uuid)"), {"r": moved_run})
        got = await _post(w, w.alice, f"/projects/agent-runs/{released_run}/release")
        assert got.status_code == 200, got.text
        _move(w, closed, proj, "done")
        body = await _list(w, w.alice, proj)
        assert _kinds(body) == [("stalled", kept), ("dangling", closed)]
        assert body["rows"][0]["run_id"] == kept_run
        assert body["rows"][1]["run_id"] == closed_run

    async def test_k2_a_dangling_run_is_listed_for_each_closed_reason(self, w):
        proj = _project(w)
        done, cancelled, archived = (_task(w, proj) for _ in range(3))
        _move(w, done, proj, "done")
        _move(w, cancelled, proj, "cancelled")
        _archive(w, archived)
        runs = {
            done: b.run(w.p, w.a, done, ALICE, "in_progress",
                        last_activity="now() - interval '3 hours'"),
            cancelled: b.run(w.p, w.a, cancelled, ALICE, "awaiting",
                             last_activity="now() - interval '2 hours'"),
            archived: b.run(w.p, w.a, archived, ALICE, "created",
                            last_activity="now() - interval '1 hour'"),
        }
        body = await _list(w, w.alice, proj)
        assert [(r["kind"], r["task_id"], r["reason"]) for r in body["rows"]] == [
            ("dangling", done, "done"), ("dangling", cancelled, "cancelled"),
            ("dangling", archived, "archived")]
        for row in body["rows"]:
            assert row["run_id"] == runs[row["task_id"]]
            assert (row["action"], row["actions"]) == ("close_run", ["close_run"])
            assert row["owner"] == ALICE and row["can_act"] is True
        assert body["rows"][2]["archived"] is True

    async def test_k2_near_misses_of_a_dangling_run(self, w):
        """A finished run of a done task, a released run of an archived task
        and an open run of an open task are not dangling."""
        proj = _project(w)
        done, archived, open_ = (_task(w, proj) for _ in range(3))
        _move(w, done, proj, "done")
        _archive(w, archived)
        b.run(w.p, w.a, done, ALICE, "completed")
        b.run(w.p, w.a, archived, ALICE, "released")
        b.run(w.p, w.a, open_, ALICE, "in_progress")
        body = await _list(w, w.alice, proj)
        assert body["rows"] == [] and body["total"] == 0

    async def test_k3_a_task_blocked_by_parked_work(self, w):
        proj = _project(w)
        blocked = _task(w, proj, assignee=ERIN, title="Ship the widget")
        blocker = _task(w, proj, category="backlog", title="Design the bracket")
        _link(w, blocker, blocked)
        body = await _list(w, w.alice, proj)
        [row] = body["rows"]
        assert (row["kind"], row["task_id"], row["blocker_id"]) == ("blocked", blocked, blocker)
        assert row["blocker_title"] == "Design the bracket"
        assert row["blocker_number"] is not None
        assert (row["owner"], row["owner_name"]) == (ERIN, "Erin Evans")
        assert (row["action"], row["actions"]) == ("open_blocker", ["open_blocker"])
        assert row["can_act"] is True and row["yours"] is False
        assert row["run_id"] is None and row["category"] == "todo"
        mine = await _list(w, w.erin, proj)
        assert mine["rows"][0]["yours"] is True

    async def test_k3_near_misses_of_a_parked_blocker(self, w):
        """Each blocker below is NOT parked, or the link is not an open
        `blocks` link, or the blocked task is closed. Only the last pair, a
        blocker whose run lost its lease, counts: RA-1's holder rule reads a
        run past its lease as holding nothing."""
        proj = _project(w)

        def pair(**blocker: Any) -> tuple[str, str]:
            blocked = _task(w, proj)
            return _task(w, proj, **blocker), blocked

        owned, b1 = pair(category="backlog", assignee=ERIN)
        live, b2 = pair(category="backlog")
        b.run(w.p, w.a, live, ALICE, "in_progress")
        todo, b3 = pair(category="todo")
        done, b4 = pair(category="done")
        related, b5 = pair(category="backlog")
        parked, b6 = pair(category="backlog")
        archived, b7 = pair(category="backlog")
        old, b8 = pair(category="backlog")
        b.run(w.p, w.a, old, ALICE, "in_progress",
              last_activity="now() - interval '3 hours'",
              last_agent="now() - interval '3 hours'")
        for blocker, blocked in ((owned, b1), (live, b2), (todo, b3), (done, b4),
                                 (parked, b6), (archived, b7), (old, b8)):
            _link(w, blocker, blocked)
        _link(w, related, b5, kind="relates_to")
        _move(w, b6, proj, "done")  # the blocked task itself is closed
        _archive(w, archived)
        body = await _list(w, w.alice, proj)
        assert _kinds(body) == [("blocked", b8)]
        assert body["rows"][0]["blocker_id"] == old

    async def test_k3_a_hidden_blocker_gives_no_row(self, w):
        """§7 rule 1 on the blocker. A blocked row prints the blocker's title,
        so a reader who cannot open the blocker gets no row."""
        mine = _project(w, grant=MALLORY)
        other = _project(w)
        blocked = _task(w, mine)
        blocker = _task(w, other, category="backlog")
        _link(w, blocker, blocked)
        assert _kinds(await _list(w, w.alice, mine)) == [("blocked", blocked)]
        assert (await _list(w, w.mallory, mine))["rows"] == []
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject,"
                " created_by) VALUES (:o, :p, :s, :e)"),
                {"o": w.a, "p": other["id"], "s": MALLORY, "e": ALICE})
        assert _kinds(await _list(w, w.mallory, mine)) == [("blocked", blocked)]


@_DB_GATE
class TestTheScope:

    async def test_v1_a_restricted_member_sees_only_what_they_can_open(self, w):
        granted = _project(w, grant=MALLORY)
        hidden = _project(w)
        seen_task, hidden_task = _task(w, granted), _task(w, hidden)
        _move(w, seen_task, granted, "done")
        _move(w, hidden_task, hidden, "done")
        b.run(w.p, w.a, seen_task, ALICE, "in_progress")
        b.run(w.p, w.a, hidden_task, ALICE, "in_progress")
        assert _kinds(await _list(w, w.mallory, granted)) == [("dangling", seen_task)]
        status, _ = await _get(w, w.mallory, hidden)
        assert status == 404
        portfolio = await _list(w, w.mallory)
        assert _kinds(portfolio) == [("dangling", seen_task)]
        everything = await _list(w, w.alice)
        assert {t for _, t in _kinds(everything)} == {seen_task, hidden_task}

    async def test_v2_organization_b_sees_nothing_of_a(self, w):
        proj = _project(w)
        task = _task(w, proj)
        _move(w, task, proj, "done")
        b.run(w.p, w.a, task, ALICE, "in_progress")
        assert (await _list(w, w.bob))["rows"] == []
        status, _ = await _get(w, w.bob, proj)
        assert status == 404

    async def test_v3_a_project_reads_its_subprojects_and_no_other(self, w):
        root = _project(w)
        sub = _project(w, parent=root)
        sibling = _project(w)
        in_root, in_sub, elsewhere = _task(w, root), _task(w, sub), _task(w, sibling)
        for task, proj in ((in_root, root), (in_sub, root), (elsewhere, sibling)):
            _move(w, task, proj, "done")
            b.run(w.p, w.a, task, ALICE, "in_progress")
        assert {t for _, t in _kinds(await _list(w, w.alice, root))} == {in_root, in_sub}

    async def test_v4_mine_keeps_the_callers_rows(self, w):
        proj = _project(w)
        hers, his = _task(w, proj), _task(w, proj)
        for task in (hers, his):
            _move(w, task, proj, "done")
        b.run(w.p, w.a, hers, ALICE, "in_progress")
        b.run(w.p, w.a, his, DAVE, "in_progress")
        assert _kinds(await _list(w, w.alice, proj, mine=True)) == [("dangling", hers)]
        body = await _list(w, w.alice, proj, mine=True)
        assert body["counts"]["dangling"] == 1
        assert len((await _list(w, w.alice, proj))["rows"]) == 2

    async def test_v5_with_the_runs_flag_off_no_run_kind_reads(self, w, monkeypatch):
        proj = _project(w)
        done, blocked = _task(w, proj), _task(w, proj)
        _move(w, done, proj, "done")
        b.run(w.p, w.a, done, ALICE, "in_progress")
        _link(w, _task(w, proj, category="backlog"), blocked)
        assert len((await _list(w, w.alice, proj))["rows"]) == 2
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        body = await _list(w, w.alice, proj)
        assert _kinds(body) == [("blocked", blocked)]
        assert body["counts"] == {"stalled": 0, "dangling": 0, "blocked": 1}

    async def test_v6_the_order_the_counts_and_the_page(self, w):
        proj = _project(w)
        stalled = _task(w, proj)
        await _stalled(w, stalled)
        old, new = _task(w, proj), _task(w, proj)
        for task in (old, new):
            _move(w, task, proj, "done")
        b.run(w.p, w.a, new, ALICE, "in_progress", last_activity="now() - interval '1 hour'")
        b.run(w.p, w.a, old, ALICE, "in_progress", last_activity="now() - interval '2 hours'")
        blocked = _task(w, proj)
        _link(w, _task(w, proj, category="backlog"), blocked)
        body = await _list(w, w.alice, proj)
        assert _kinds(body) == [("stalled", stalled), ("dangling", old), ("dangling", new),
                                ("blocked", blocked)]
        assert body["counts"] == {"stalled": 1, "dangling": 2, "blocked": 1}
        assert (body["total"], body["listed"]) == (4, 4)
        page = await _list(w, w.alice, proj, offset=1, limit=2)
        assert _kinds(page) == [("dangling", old), ("dangling", new)]
        assert (page["total"], page["offset"], page["limit"]) == (4, 1, 2)
        counts_only = await _list(w, w.alice, proj, limit=0)
        assert counts_only["rows"] == [] and counts_only["total"] == 4

    async def test_v7_can_act_is_the_rule_of_release(self, w):
        """The run's member, an assignee of the task and an admin may act. A
        member who only sees the task may not, and the row says so."""
        proj = _project(w)
        task = _task(w, proj, assignee=ERIN)
        _move(w, task, proj, "done")
        b.run(w.p, w.a, task, ALICE, "in_progress")
        for user, want in ((w.alice, True), (w.erin, True), (w.carol_admin, True),
                           (w.dave, False)):
            [row] = (await _list(w, user, proj))["rows"]
            assert row["can_act"] is want, user.email


@_DB_GATE
class TestResume:

    async def test_r1_resume_hands_the_run_back_to_an_agent(self, w):
        proj = _project(w)
        task = _task(w, proj)
        rid = await _stalled(w, task)
        before = _task_state(w, task)
        key = _key()
        got = await _post(w, w.alice, f"/projects/agent-runs/{rid}/resume",
                          {"idempotency_key": key})
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["resumed"] is True and body["created"] is True
        assert body["run"]["needs_agent"] == "answered" and body["run"]["state"] == "answered"
        assert body["run"]["status"] == "in_progress" and body["run"]["stranded"] is None
        assert body["activity"]["type"] == "prompt"
        assert body["activity"]["content"]["resume"] is True
        assert body["activity"]["content"]["body"] == agent_runs.RESUME_BODY
        # The row leaves the list at once, before any tick.
        assert (await _list(w, w.alice, proj))["rows"] == []
        # The member's next session finds it with needs_agent=true.
        async with b._session(w.p) as call:
            listed = await call(w.alice_agent, "GET", "/projects/agent-runs?needs_agent=true")
        assert [(r["id"], r["needs_agent"]) for r in listed.json()["rows"]] == [
            (rid, "answered")]
        # The task does not change, and the next tick clears the strand with
        # SW-10a's own path.
        assert _task_state(w, task) == before
        await _sweep_org(w)
        [strand] = _strands(w, rid)
        assert strand.cleared_reason == "new_activity"
        # A replay of the key writes nothing.
        again = await _post(w, w.alice, f"/projects/agent-runs/{rid}/resume",
                            {"idempotency_key": key})
        assert again.status_code == 200 and again.json()["created"] is False
        prompts = _rows(w.p, "SELECT id FROM pm_agent_run_activities WHERE run_id ="
                             " CAST(:r AS uuid) AND type = 'prompt'", r=rid)
        assert len(prompts) == 1

    async def test_r2_resume_refuses_what_it_may_not_take(self, w):
        proj = _project(w)
        task = _task(w, proj)
        rid = await _stalled(w, task)
        path = f"/projects/agent-runs/{rid}/resume"
        for user, want in ((w.dave, 403), (w.alice_agent, 403), (w.mallory, 404),
                           (w.bob, 404)):
            got = await _post(w, user, path, {"idempotency_key": _key()})
            assert got.status_code == want, (user.email, got.text)
        # A fingerprint with no recovery record has nothing to resume.
        nudged = _task(w, proj)
        nudged_run = _insert_run(w, nudged, member=ALICE, idle_s=3 * 3600)
        await _sweep_org(w)
        got = await _post(w, w.alice, f"/projects/agent-runs/{nudged_run}/resume",
                          {"idempotency_key": _key()})
        assert got.status_code == 409, got.text
        # A closed task: close the run instead.
        _move(w, task, proj, "done")
        got = await _post(w, w.alice, path, {"idempotency_key": _key()})
        assert got.status_code == 409 and "Close the run" in got.json()["detail"]
        assert _rows(w.p, "SELECT id FROM pm_agent_run_activities WHERE run_id ="
                          " CAST(:r AS uuid) AND type = 'prompt'", r=rid) == []

    async def test_r2_a_key_of_another_activity_is_refused(self, w):
        proj = _project(w)
        task = _task(w, proj)
        rid = await _stalled(w, task)
        key = _key()
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id, type,"
                " content, actor, idempotency_key) VALUES (CAST(:o AS uuid), CAST(:r AS uuid),"
                " CAST(:t AS uuid), 'thought', CAST('{\"body\": \"x\"}' AS jsonb), :m, :k)"),
                {"o": w.a, "r": rid, "t": task, "m": ALICE, "k": key})
        got = await _post(w, w.alice, f"/projects/agent-runs/{rid}/resume",
                          {"idempotency_key": key})
        assert got.status_code == 409, got.text

    async def test_r3_an_assignee_and_an_admin_may_resume(self, w):
        proj = _project(w)
        for user in (w.erin, w.carol_admin):
            task = _task(w, proj, assignee=ERIN)
            rid = await _stalled(w, task)
            got = await _post(w, user, f"/projects/agent-runs/{rid}/resume",
                              {"idempotency_key": _key()})
            assert got.status_code == 200, (user.email, got.text)


@_DB_GATE
class TestClose:

    async def test_c1_close_ends_the_run_and_leaves_the_task(self, w):
        proj = _project(w)
        dangling, stalled = _task(w, proj, assignee=ERIN), _task(w, proj)
        stalled_run = await _stalled(w, stalled)
        _move(w, dangling, proj, "done")
        dangling_run = b.run(w.p, w.a, dangling, ALICE, "awaiting")
        before = {t: _task_state(w, t) for t in (dangling, stalled)}
        assert len((await _list(w, w.alice, proj))["rows"]) == 2
        for rid in (dangling_run, stalled_run):
            got = await _post(w, w.alice, f"/projects/agent-runs/{rid}/release")
            assert got.status_code == 200, got.text
            assert got.json()["run"]["status"] == "released"
        assert (await _list(w, w.alice, proj))["rows"] == []
        assert {t: _task_state(w, t) for t in (dangling, stalled)} == before
        lines = _rows(w.p, "SELECT content->>'body' AS body FROM pm_agent_run_activities"
                           " WHERE run_id = CAST(:r AS uuid) AND content->>'system' = 'true'",
                      r=dangling_run)
        assert [r.body for r in lines] == ["Released by Alice Archer/the app"]

    async def test_c2_close_refuses_a_member_without_the_right(self, w):
        proj = _project(w)
        task = _task(w, proj)
        _move(w, task, proj, "done")
        rid = b.run(w.p, w.a, task, ALICE, "in_progress")
        got = await _post(w, w.dave, f"/projects/agent-runs/{rid}/release")
        assert got.status_code == 403, got.text
        [row] = (await _list(w, w.dave, proj))["rows"]
        assert row["can_act"] is False
