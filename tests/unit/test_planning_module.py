"""PJP-6a — the planning engine, ``acb_common.planning``.

Spec: ``project-docs/specs/project_planning.md`` §3.4 and §10.

No database. The module is pure, so each test feeds it a fixed graph and checks
each number. Three fences hold the home of the maths (R7):

* ``test_the_module_imports_no_io_library``
* ``test_no_other_module_sorts_a_graph``

Mutations (R7). ``P`` is ``packages/acb_common/acb_common/planning.py``.

* M1. ``P``: ``dur`` loses ``REVIEW_WAIT_MINS``. ``test_a_chain``, ``test_sp12_*``.
* M2. ``P``: an epic link expands to nothing. ``test_an_epic_link_expands_to_its_slices``.
* M3. ``P``: the cycle branch does not return. ``test_a_cycle_*``.
* M4. ``P``: ``add_working_days`` counts a weekend. ``test_the_finish_date_skips_weekends``.
* M5. ``P``: ``next_slice`` drops the priority key. ``test_next_slice_breaks_a_tie_by_priority_then_id``.
* M6. ``P``: a done outside blocker holds its task. ``test_an_outside_blocker_*``.
"""

from __future__ import annotations

import ast
import re
from datetime import date
from pathlib import Path

from acb_common import planning
from acb_common.planning import (
    NOT_ENOUGH_TEXT,
    REVIEW_WAIT_MINS,
    PlanTask,
    add_working_days,
    analyze_plan,
)

ROOT = Path(__file__).resolve().parents[2]
W = REVIEW_WAIT_MINS


def T(id_, mins=None, *, cat="todo", owner="o", parent=None, rank=None, type_=None):
    return PlanTask(id=id_, category=cat, estimate_mins=mins, owner=owner,
                    parent=parent, priority_rank=rank, type=type_)


class TestTheFixtures:
    def test_a_chain(self):
        r = analyze_plan([T("a", 60), T("b", 120), T("c", 30)], [("a", "b"), ("b", "c")])
        assert r.ready == ["a"]
        assert r.waves == [["a"], ["b"], ["c"]]
        assert r.critical_path == ["a", "b", "c"]
        assert r.slack == {"a": 0, "b": 0, "c": 0}
        assert r.next_slice == "a"
        assert r.finish == {"mins": 60 + 120 + 30 + 3 * W, "working_days": 2, "date": None}
        assert r.cycle is None and r.not_enough is None

    def test_a_diamond(self):
        tasks = [T("a", 60), T("b", 120), T("c", 30), T("d", 60)]
        r = analyze_plan(tasks, [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
        assert r.ready == ["a"]
        assert r.waves == [["a"], ["b", "c"], ["d"]]
        assert r.critical_path == ["a", "b", "d"]
        assert r.slack["c"] == 120 - 30  # b is 90 minutes longer than c
        assert r.slack["b"] == 0
        assert r.finish["mins"] == (60 + W) + (120 + W) + (60 + W)

    def test_a_cycle_reports_the_tasks_and_no_path(self):
        tasks = [T("a", 10), T("b", 10), T("c", 10), T("d", 10)]
        r = analyze_plan(tasks, [("a", "b"), ("b", "c"), ("c", "b"), ("c", "d")])
        assert r.cycle == {"task_ids": ["b", "c"], "epic_ids": []}
        assert r.critical_path == [] and r.waves == [] and r.slack == {}
        assert r.next_slice is None and r.finish is None
        assert r.ready == ["a"]

    def test_a_cycle_across_epics_names_the_epics(self):
        tasks = [T("e1", cat="todo"), T("e2", cat="todo"),
                 T("a", 10, parent="e1"), T("b", 10, parent="e2")]
        r = analyze_plan(tasks, [("e1", "e2"), ("e2", "e1")])
        assert r.cycle == {"task_ids": ["a", "b"], "epic_ids": ["e1", "e2"]}
        assert r.critical_path == [] and r.waves == []

    def test_a_task_with_no_estimate_counts_zero_and_is_listed(self):
        r = analyze_plan([T("a", None, owner=None), T("b", 60)], [("a", "b")])
        assert r.finish["mins"] == (0 + W) + (60 + W)
        assert r.missing["no_estimate"] == 1 and r.missing["no_estimate_ids"] == ["a"]
        assert r.missing["no_owner"] == 1 and r.missing["no_owner_ids"] == ["a"]
        assert r.missing["words"] == ["1 task has no estimate", "1 task has no owner"]
        assert r.critical_path == ["a", "b"]

    def test_missing_words_are_plain(self):
        r = analyze_plan([T("a", None, owner=None), T("b", None, owner=None), T("c", 5)],
                         [("a", "b")])
        assert r.missing["words"] == ["2 tasks have no estimate", "2 tasks have no owner"]

    def test_a_graph_with_no_links_has_no_path(self):
        r = analyze_plan([T("a", 10), T("b", 10)], [])
        assert r.not_enough == NOT_ENOUGH_TEXT == "need 2 linked tasks"
        assert r.critical_path == [] and r.finish is None and r.next_slice is None
        assert r.ready == ["a", "b"] and r.waves == [["a", "b"]]

    def test_one_link_is_enough(self):
        r = analyze_plan([T("a", 10), T("b", 10)], [("a", "b")])
        assert r.not_enough is None and r.critical_path == ["a", "b"]

    def test_sp12_an_epic_of_six_slices_and_five_links(self):
        tasks = [T("epic"),
                 T("a", 60, parent="epic"), T("b", 120, parent="epic"),
                 T("c", 30, parent="epic"), T("d", 60, parent="epic"),
                 T("e", 60, parent="epic"), T("f", 30, parent="epic")]
        links = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("d", "e")]
        r = analyze_plan(tasks, links, start=date(2026, 11, 2))
        assert r.slices == 6
        assert r.ready == ["a", "f"]
        assert r.waves == [["a", "f"], ["b", "c"], ["d"], ["e"]]
        assert r.critical_path == ["a", "b", "d", "e"]
        assert r.slack["a"] == r.slack["b"] == r.slack["d"] == r.slack["e"] == 0
        assert r.slack["c"] == 90
        assert r.slack["f"] == 540 - (30 + W)
        assert r.next_slice == "a"
        assert r.finish == {"mins": 540, "working_days": 2, "date": "2026-11-03"}


class TestTheRules:
    def test_an_epic_link_expands_to_its_slices(self):
        tasks = [T("e1"), T("e2"),
                 T("a1", 10, parent="e1"), T("a2", 10, parent="e1"),
                 T("b1", 10, parent="e2"), T("b2", 10, parent="e2")]
        r = analyze_plan(tasks, [("e1", "e2")])
        assert r.ready == ["a1", "a2"]
        assert r.waves == [["a1", "a2"], ["b1", "b2"]]

    def test_a_done_slice_of_a_blocker_epic_blocks_nothing(self):
        tasks = [T("e1"), T("e2"), T("a1", 10, parent="e1", cat="done"),
                 T("a2", 10, parent="e1"), T("b1", 10, parent="e2")]
        r = analyze_plan(tasks, [("e1", "e2")])
        assert r.waves == [["a2"], ["b1"]]

    def test_a_done_blocker_leaves_its_task_ready(self):
        r = analyze_plan([T("a", 10, cat="done"), T("b", 10)], [("a", "b")])
        assert r.ready == ["b"] and r.slices == 1

    def test_an_outside_blocker_holds_its_task(self):
        tasks = [T("a", 10), T("b", 10), T("c", 10)]
        r = analyze_plan(tasks, [("x", "a"), ("a", "b")], outside={"x": "in_progress"})
        assert r.ready == ["c"]
        assert r.outside_blockers == [{"task_id": "a", "blocked_by": ["x"]}]
        assert r.held == ["a", "b"] and r.waves == [["c"]]

    def test_a_done_outside_blocker_is_ignored(self):
        r = analyze_plan([T("a", 10)], [("x", "a")], outside={"x": "done"})
        assert r.ready == ["a"] and r.outside_blockers == []

    def test_a_link_to_an_unknown_id_is_ignored(self):
        r = analyze_plan([T("a", 10)], [("ghost", "a"), ("a", "ghost")])
        assert r.ready == ["a"] and r.outside_blockers == []

    def test_next_slice_breaks_a_tie_by_priority_then_id(self):
        tasks = [T("a", 10, rank=5), T("b", 10, rank=2), T("c", 10, rank=2), T("z", 10)]
        r = analyze_plan(tasks, [("a", "z"), ("b", "z"), ("c", "z")])
        assert r.slack["a"] == r.slack["b"] == r.slack["c"] == 0
        assert r.next_slice == "b"
        r2 = analyze_plan(
            [T("a", 10, rank=2), T("b", 10, rank=2), T("z", 10)], [("a", "z"), ("b", "z")])
        assert r2.next_slice == "a"

    def test_next_slice_prefers_the_path_then_least_slack(self):
        tasks = [T("a", 600), T("b", 10), T("z", 10)]
        r = analyze_plan(tasks, [("a", "z"), ("b", "z")])
        assert r.critical_path == ["a", "z"] and r.next_slice == "a"

    def test_the_finish_date_skips_weekends(self):
        assert add_working_days(date(2026, 11, 6), 1) == date(2026, 11, 9)  # Fri to Mon
        assert add_working_days(date(2026, 11, 7), 0) == date(2026, 11, 9)  # Sat to Mon
        assert add_working_days(date(2026, 11, 2), 4) == date(2026, 11, 6)
        # 3 days of work from a Friday ends on Tuesday.
        r = analyze_plan([T("a", 600), T("b", 600)], [("a", "b")], start=date(2026, 11, 6))
        assert r.finish["working_days"] == 4
        assert r.finish["date"] == "2026-11-11"

    def test_the_result_is_plain_data(self):
        import json

        r = analyze_plan([T("a", 10), T("b", 10)], [("a", "b")], start=date(2026, 11, 2))
        assert json.loads(json.dumps(r.to_dict()))["critical_path"] == ["a", "b"]

    def test_tasks_from_rows(self):
        got = planning.tasks_from_rows([{"id": 1, "category": "todo", "parent": 2}])
        assert got == [PlanTask(id="1", category="todo", parent="2")]


# ── Fences: where the maths lives ───────────────────────────────────────────

_IO_MODULES = {
    "httpx", "sqlalchemy", "asyncpg", "psycopg", "psycopg2", "os", "socket",
    "requests", "subprocess", "redis", "gateway", "skill_projects", "aiohttp",
    "urllib", "pathlib", "shutil",
}


def test_the_module_imports_no_io_library():
    source = (ROOT / "packages/acb_common/acb_common/planning.py").read_text(encoding="utf-8")
    seen: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            seen |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            seen.add(node.module.split(".")[0])
    assert not (seen & _IO_MODULES), seen & _IO_MODULES


_SORT_DEF = re.compile(
    r"^\s*(?:async\s+)?def\s+\w*(?:topological|toposort|critical_path|kahn|forward_pass|"
    r"backward_pass|longest_path)\w*", re.IGNORECASE | re.MULTILINE)
_SORT_TOKEN = re.compile(r"\b(?:indegree|in_degree|indeg)\b")


def test_no_other_module_sorts_a_graph():
    """The one home is ``packages/acb_common/acb_common/planning.py`` (D195 Q8).

    The gateway and ``skill_projects`` call ``analyze_plan``. A topological sort
    or a critical-path pass in either is a second engine.
    """
    roots = [ROOT / "apps/services/gateway", ROOT / "apps/skills/skill-projects"]
    found: list[str] = []
    for base in roots:
        for path in base.rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if _SORT_DEF.search(text) or _SORT_TOKEN.search(text):
                found.append(str(path.relative_to(ROOT)))
    assert not found, (
        "A graph sort lives only in packages/acb_common/acb_common/planning.py. "
        f"Call analyze_plan instead. Found in: {found}"
    )
