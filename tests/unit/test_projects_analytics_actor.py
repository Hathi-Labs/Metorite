"""WS-27bn RA-1 — who did it: people, agents and agent-assisted. Hermetic.

Spec: ``project-docs/specs/projects_reports.md`` §6.8 F and §8 RA-1.

The rule lives in ``analytics.py``, as two twins. This file pins the PYTHON
twin (``classify_finish``, ``classify_holder``) to the table of cases below,
and the shape of the SQL twin. ``test_projects_analytics_actor_r8.py`` pins
the SQL twin to the same cases on a real database.

* A done task is ``agent_finished`` when its move to done came from an agent:
  ``meta.via`` = ``mcp:…``, or the actor is ``agent:<name>``.
* It is ``agent_assisted`` when it held an agent run and a person moved it.
* It is ``person`` in every other case. ``system:workflow:<id>`` is not an
  agent.
* A reopened task takes the label of its LATEST move into done.
* An open task's live holder is ``agent`` (a live run, or an ``agent:``
  assignee), ``person`` (a person assignee), or None.

**The mutations (R7).** Each one turned the named test red. Each ran after a
commit, and ``git checkout`` restored the file after it.

* A1. ``is_agent_act`` reads ``created_by`` only and ignores ``via``
  -> ``test_an_act_through_mcp_finishes_as_an_agent``.
* A2. ``is_agent_act`` takes the ``system:`` prefix as an agent
  -> ``test_a_workflow_is_not_an_agent``.
* A3. ``done_move`` takes the EARLIEST move into done (``min``)
  -> ``test_a_reopened_task_takes_its_latest_finish``.
* A4. ``classify_holder`` drops the ``run_holds`` arm
  -> ``test_a_live_run_holds_the_task_for_an_agent``.
* A5. ``split_counts`` leaves out a key with no row
  -> ``test_the_split_has_every_key_and_zero_for_no_row``.
* A6. a second ``->>'via'`` read in ``analytics.py``
  -> ``test_one_module_reads_via_for_the_label``.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.projects import analytics as ana

_ROOT = Path(__file__).resolve().parents[2]
_PROJECTS = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "projects"
_T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
_VIA = "mcp:claude.ai:claude-code"


def _move(
    minutes: int, by: str, *, via: str | None = None,
    frm: str = "in_progress", to: str = "done",
) -> ana.StatusMove:
    return ana.StatusMove(_T0 + timedelta(minutes=minutes), by, via, frm, to)


# ── The finish label ─────────────────────────────────────────────────────────


def test_the_labels_are_the_three_of_the_spec() -> None:
    assert ana.FINISH_LABELS == ("agent_finished", "agent_assisted", "person")
    assert ana.HOLDER_KEYS == ("agent", "person", "none")


def test_an_act_through_mcp_finishes_as_an_agent() -> None:
    """The member's address with the MCP stamp: the agent made the move."""
    moves = [_move(0, "ana@x.test", via=_VIA)]
    assert ana.classify_finish(moves, had_run=False) == ana.FINISH_AGENT
    assert ana.classify_finish(moves, had_run=True) == ana.FINISH_AGENT


def test_an_in_app_agent_finishes_as_an_agent() -> None:
    moves = [_move(0, "agent:coder")]
    assert ana.classify_finish(moves, had_run=False) == ana.FINISH_AGENT


def test_the_stamp_is_case_blind() -> None:
    assert ana.is_agent_act("Agent:Coder", None)
    assert ana.is_agent_act("ana@x.test", "MCP:claude.ai")


def test_a_person_after_an_agent_run_is_agent_assisted() -> None:
    """``meta.via`` absent, a person's address, and the task held a run."""
    moves = [_move(0, "ana@x.test")]
    assert ana.classify_finish(moves, had_run=True) == ana.FINISH_ASSISTED


def test_a_person_with_no_run_is_a_person() -> None:
    moves = [_move(0, "ana@x.test")]
    assert ana.classify_finish(moves, had_run=False) == ana.FINISH_PERSON


def test_a_workflow_is_not_an_agent() -> None:
    """``system:workflow:<id>`` is an automation (D-PM-4), not an agent."""
    moves = [_move(0, "system:workflow:7f2c")]
    assert ana.classify_finish(moves, had_run=False) == ana.FINISH_PERSON
    assert ana.classify_finish(moves, had_run=True) == ana.FINISH_ASSISTED
    assert not ana.is_agent_act("system:workflow:7f2c", None)


def test_another_via_is_not_an_agent() -> None:
    """A chat write and a GitHub merge stamp ``via`` too. Neither is MCP."""
    assert not ana.is_agent_act("ana@x.test", "chat:projects-assistant")
    assert not ana.is_agent_act("system:github", "github")


def test_a_reopened_task_takes_its_latest_finish() -> None:
    """Agent finished, a person reopened, a person finished again."""
    moves = [
        _move(0, "ana@x.test", via=_VIA),
        _move(10, "ana@x.test", frm="done", to="todo"),
        _move(20, "bo@x.test", frm="todo"),
    ]
    assert ana.classify_finish(moves, had_run=False) == ana.FINISH_PERSON
    # And the reverse: a person first, the agent last.
    flipped = [
        _move(0, "bo@x.test"),
        _move(10, "bo@x.test", frm="done", to="todo"),
        _move(20, "ana@x.test", via=_VIA, frm="todo"),
    ]
    assert ana.classify_finish(flipped, had_run=False) == ana.FINISH_AGENT


def test_the_order_of_the_list_does_not_decide() -> None:
    moves = [
        _move(20, "bo@x.test", frm="todo"),
        _move(0, "ana@x.test", via=_VIA),
    ]
    assert ana.classify_finish(moves, had_run=False) == ana.FINISH_PERSON


def test_a_move_between_two_done_lanes_is_not_a_finish() -> None:
    """The agent finished. A person tidied the task into another done lane."""
    moves = [
        _move(0, "ana@x.test", via=_VIA),
        _move(10, "bo@x.test", frm="done", to="done"),
    ]
    assert ana.done_move(moves) == moves[0]
    assert ana.classify_finish(moves, had_run=False) == ana.FINISH_AGENT


def test_no_move_into_done_reads_the_runs_alone() -> None:
    """A task born in a done lane has no move. No actor is no agent."""
    assert ana.done_move([]) is None
    assert ana.classify_finish([], had_run=False) == ana.FINISH_PERSON
    assert ana.classify_finish([], had_run=True) == ana.FINISH_ASSISTED


def test_a_cancel_is_not_a_finish() -> None:
    moves = [_move(0, "ana@x.test", via=_VIA, to="cancelled")]
    assert ana.done_move(moves) is None


# ── The live holder ──────────────────────────────────────────────────────────


def test_a_live_run_holds_the_task_for_an_agent() -> None:
    assert ana.classify_holder([], run_holds=True) == ana.HOLDER_AGENT
    assert ana.classify_holder(["ana@x.test"], run_holds=True) == ana.HOLDER_AGENT


def test_an_agent_assignee_holds_the_task() -> None:
    assert ana.classify_holder(["agent:coder"], run_holds=False) == ana.HOLDER_AGENT
    assert ana.classify_holder(
        ["ana@x.test", "agent:coder"], run_holds=False,
    ) == ana.HOLDER_AGENT


def test_a_person_assignee_holds_the_task() -> None:
    assert ana.classify_holder(["ana@x.test"], run_holds=False) == ana.HOLDER_PERSON


def test_nobody_holds_an_unassigned_task_with_no_live_run() -> None:
    assert ana.classify_holder([], run_holds=False) is None
    assert ana.classify_holder(["", None], run_holds=False) is None  # type: ignore[list-item]


# ── The split ────────────────────────────────────────────────────────────────


def test_the_split_has_every_key_and_zero_for_no_row() -> None:
    rows = [SimpleNamespace(label="person", n=3)]
    assert ana.split_counts(rows, ana.FINISH_LABELS) == {
        "agent_finished": 0, "agent_assisted": 0, "person": 3,
    }
    assert ana.split_counts([], ana.HOLDER_KEYS) == {"agent": 0, "person": 0, "none": 0}


_SPLIT_ROWS = [
    SimpleNamespace(label="agent_finished", n=2),
    SimpleNamespace(label="agent_assisted", n=1),
    SimpleNamespace(label="person", n=4),
]
_HOLDER_ROWS = [SimpleNamespace(label="agent", n=1), SimpleNamespace(label="none", n=5)]


class _FakeDb:
    """Answers the two split queries with the rows above, and every other
    query with no rows and a summary row of zeros."""

    async def execute(self, sql: Any, _params: Any) -> Any:
        body = str(sql)
        rows: list[Any] = []
        if "AS label, count(*) AS n" in body:
            rows = _SPLIT_ROWS if "fh.had_run" in body else _HOLDER_ROWS
        return SimpleNamespace(
            fetchall=lambda: rows,
            scalar=lambda: 0,
            one=lambda: SimpleNamespace(
                completed=7, cancelled=0, measured=0, no_start=0,
                median_hours=None, p90_hours=None, mins=0, estimated=0,
                tasks=0, period_start=_T0.date(), period_end=_T0.date(),
            ),
        )


@pytest.fixture
def fake_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        yield _FakeDb()

    async def _vis(_db: Any, _user: Any) -> Any:
        return SimpleNamespace(params={}, unrestricted=True, agent=None)

    async def _scope(*_a: Any) -> str:
        return "TRUE"

    monkeypatch.setattr(ana, "_tenant_session", _session)
    monkeypatch.setattr(ana, "resolve_visibility", _vis)
    monkeypatch.setattr(ana, "scope_clause", _scope)
    monkeypatch.setattr(ana, "task_visibility_clause", lambda *_a: "TRUE")


_WANT = {"agent_finished": 2, "agent_assisted": 1, "person": 4}


def test_throughput_returns_the_actor_split(fake_routes: None) -> None:
    out = asyncio.run(ana.throughput(
        project_id=None, include_subtree=True, weeks=4, user=SimpleNamespace(),
    ))
    assert out["actor_split"] == _WANT
    assert out["summary"]["completed"] == 7  # an old key stays


def test_finished_returns_the_actor_split(fake_routes: None) -> None:
    out = asyncio.run(ana.finished(
        project_id=None, include_subtree=True, weeks=4, skip_current_week=False,
        user=SimpleNamespace(),
    ))
    assert out["actor_split"] == _WANT
    assert out["total_completed"] == 7


def test_load_body_returns_the_holder_split(fake_routes: None) -> None:
    body = asyncio.run(ana.load_body(
        _FakeDb(), SimpleNamespace(params={}), project_id=None, include_subtree=True,
    ))
    assert body["holder_split"] == {"agent": 1, "person": 0, "none": 5}
    assert body["total_tasks"] == 0


# ── The SQL twin, by shape ───────────────────────────────────────────────────


def test_the_sql_twin_names_the_same_prefixes_and_labels() -> None:
    sql = ana.finish_label_sql()
    assert f"LIKE '{ana.MCP_VIA_PREFIX}%'" in sql
    assert f"LIKE '{ana.AGENT_PREFIX}%'" in sql
    for label in ana.FINISH_LABELS:
        assert f"'{label}'" in sql
    holder = ana.holder_label_sql()
    assert f"'{ana.HOLDER_AGENT}'" in holder and f"'{ana.HOLDER_PERSON}'" in holder
    # The run arm is the claim gate's lease rule, not a copy of it.
    assert ana.unheld_clause() in holder


def test_the_join_takes_the_latest_crossing_into_done() -> None:
    join = ana.finish_actor_join("c.task_id")
    assert "ORDER BY fa.created_at DESC, fa.id DESC" in join
    assert "IS DISTINCT FROM :done_cat" in join
    assert "fa.deleted_at IS NULL" in join


def test_the_labels_read_the_scope_of_the_counts() -> None:
    """§7 rule 1. The done labels sit on Throughput's `cyc`, and the holder
    labels on Load's open predicate. Neither adds a scope of its own."""
    assert ana.cycle_cte_sql("SCOPE") in ana.finish_labels_sql("SCOPE")
    assert "WHERE OPEN" in ana.holder_labels_sql("OPEN")


def test_one_module_reads_via_for_the_label() -> None:
    """Nothing reads ``meta.via`` twice. In the analytics modules and the
    report render, ``->>'via'`` appears once, in ``finish_actor_join``."""
    hits: list[str] = []
    for path in [*_PROJECTS.glob("analytics*.py"), _PROJECTS / "reports.py"]:
        text = path.read_text(encoding="utf-8")
        hits += [path.name] * len(re.findall(r"->>\s*'via'", text))
    assert hits == ["analytics.py"], hits
