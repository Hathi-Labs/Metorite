"""CIV-33d (task 3074): no scanner test file costs more than 30 s in total.

``affected_tests.scanning_tests`` adds every scanner file to each selection
that holds a Python change. A scanner file is any file with one scan fence
that lists Python files. A large file with one slow scan fence therefore ran
on every pull request. The fix is to keep each scan fence in a small file
beside the large file. This test blocks a new slow scanner.

The cost of a file is the sum of its entries in ``.test_durations``.
``OVER_BUDGET`` lists the files that exceed the budget today and that no
slice has fixed yet. A file that is in the list and is no longer over budget
fails this test, so the list only shrinks.
"""

from __future__ import annotations

import functools
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import affected_tests  # noqa: E402

BUDGET_S = 30.0

#: Scanner files over the budget, dated 2026-11-13. Each names its task.
#: ``test_affected_tests.py`` waits for CIV-33c-1 (PR 2205) to merge.
OVER_BUDGET: dict[str, str] = {
    "tests/unit/test_affected_tests.py": "task 3074, follow-up after PR 2205 (CIV-33c-1)",
    "tests/unit/test_data_engine.py": "task 3074, tmp-dir listings in many tests",
    "tests/unit/test_agent_run_exclusive.py": "task 3074, f-string migration globs",
    "tests/unit/test_no_person_present.py": "task 3074, every test is a scan",
    "tests/unit/test_flag_reload.py": "task 3074, every test is a scan",
    "tests/unit/test_email_self_reader_tenant_fence.py": "task 3074, every test is a scan",
}


def file_costs(durations: dict[str, float]) -> dict[str, float]:
    cost: dict[str, float] = defaultdict(float)
    for key, seconds in durations.items():
        cost[key.partition("::")[0]] += seconds
    return cost


@functools.cache
def _scanner_files_cached(root: Path) -> frozenset[str]:
    tests = affected_tests._texts(sorted((root / "tests" / "unit").glob("test_*.py")), root)
    return frozenset(affected_tests.scanning_tests(tests))


def scanner_files(root: Path = ROOT) -> set[str]:
    return set(_scanner_files_cached(root))


def over_budget(durations: dict[str, float], scanners: set[str]) -> set[str]:
    cost = file_costs(durations)
    return {f for f in scanners if cost[f] > BUDGET_S}


def _durations() -> dict[str, float]:
    return json.loads((ROOT / ".test_durations").read_text(encoding="utf-8"))


def test_no_new_scanner_file_costs_more_than_the_budget() -> None:
    over = over_budget(_durations(), scanner_files()) - set(OVER_BUDGET)
    assert not over, (
        f"Scanner files over {BUDGET_S:.0f} s. Move the scan fence into a small file "
        f"beside it, or list the file in OVER_BUDGET with a task: {sorted(over)}"
    )


def test_a_listed_file_that_is_back_under_the_budget_leaves_the_list() -> None:
    over = over_budget(_durations(), scanner_files())
    stale = sorted(set(OVER_BUDGET) - over)
    assert not stale, f"Remove these files from OVER_BUDGET, they are under budget: {stale}"


def test_every_listed_file_exists_and_names_a_task() -> None:
    for name, why in OVER_BUDGET.items():
        assert (ROOT / name).is_file(), name
        assert "task " in why, name


def test_the_check_counts_a_file_over_the_budget() -> None:
    durations = {"tests/unit/a.py::t1": 20.0, "tests/unit/a.py::t2": 11.0, "tests/unit/b.py::t": 5.0}
    scanners = {"tests/unit/a.py", "tests/unit/b.py"}
    assert over_budget(durations, scanners) == {"tests/unit/a.py"}
    assert over_budget(durations, {"tests/unit/b.py"}) == set()
