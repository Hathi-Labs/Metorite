"""No NEW test that reads the clock in the two shapes that failed CI.

**Why this exists.** In two days (2026-10-12 and 2026-10-13), unrelated PRs
failed CI on tests that read the clock, and a rerun passed each one. The
fixes are #932, #947, #951 and the clock sweep that added this file. The
same two shapes keep coming back, so this file refuses a new one:

* **A module instant.** ``NOW = datetime.now(UTC)`` at import, used to
  build rows, while the code under test reads its own clock at run time.
  CI imports at collection and runs a file up to 30 minutes later, so
  "due later today" became overdue (``test_shell_needs.py``, #951). Fix
  it with a fixed instant and a ``_now()`` seam in the product module.
* **A wall-clock budget.** ``took = time.monotonic() - started`` and then
  ``assert took < 2.5``. A stalled runner measured 5.4 s outside any
  window (#947), so a budget measures the runner as much as the code.
  Fix it with an event or an order (#947 ``_Clock``), a count of equal
  work (``_linear_time.assert_linear_count``), or a hang guard of 30 s
  that only catches a hang.

**Scope, so the next reader knows what this does NOT cover.** It reads
``tests/unit`` only. Rule A matches an assignment at module level or in a
class body whose value calls ``datetime.now``, ``datetime.utcnow``,
``datetime.today`` or ``date.today``. Rule B matches ``assert NAME < N``
(or ``<=``, or a chain that ends so) where ``N`` is a number and ``NAME``
was bound in the same function from an expression that calls
``time.monotonic`` or ``time.perf_counter``. An inline
``time.monotonic() - x < N``, a helper that returns the seconds, and a
budget read from a constant are left out on purpose: each of those has
fixes in flight, and a wider rule would churn on them. Widen the rule
rather than add an entry.

**The allowlist only shrinks.** Each entry is what remained after the
sweep, with the reason it stays. An entry that no longer matches fails
the second test, so a fix must delete its entry.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

_UNIT = Path(__file__).resolve().parent
_REPO = _UNIT.parents[1]

_NOW_CALLS = frozenset({"now", "utcnow", "today"})
_NOW_ROOTS = frozenset({"datetime", "date", "dt"})
_TIMERS = frozenset({"monotonic", "perf_counter", "monotonic_ns", "perf_counter_ns"})

#: Rule A: a module instant that stays, by ``file::name``, and why.
_MODULE_NOW_ALLOWED: dict[str, str] = {
    "test_auth_sql_asyncpg.py::SOON":
        "a bind value only: every statement runs for its SQL shape, never its "
        "effect, so the instant never meets the code's clock",
    "test_email_ai_context.py::_T0":
        "two days back, inside the 7-day window the suite reads: the drift of "
        "one CI run cannot move a row across it",
    "test_email_html_hot_only.py::COLD":
        "200 days back, past HTML_HOT_DAYS (90): no CI run drifts 110 days",
    "test_email_html_hot_only.py::HOT":
        "10 days back, inside HTML_HOT_DAYS (90): no CI run drifts 80 days",
    "test_email_html_tier.py::COLD":
        "120 days back, past HTML_HOT_DAYS (90): no CI run drifts 30 days",
    "test_email_html_tier.py::HOT":
        "10 days back, inside HTML_HOT_DAYS (90): no CI run drifts 80 days",
    "test_projects_assignees.py::TODAY":
        "the dates sit 3, 10 and 20 days on, and no assertion compares them "
        "with the code's today",
    "test_shell_needs_r8.py::NOW":
        "#951: the R8 seeds read this one instant, and an autouse fixture "
        "freezes the feed's _now and the lens read's today to it",
}

#: Rule B: a wall-clock budget that stays, by ``file::function::name``, and why.
_BUDGET_ALLOWED: dict[str, str] = {
    "test_attachment_formats.py::test_a_zip_bomb_slide_stops_at_the_part_cap_and_keeps_"
    "the_slides_before::took":
        "a hang guard of 15 s. The cap is fenced by stopped=True and the peak "
        "memory, and a read with no cap reads the whole bomb",
    "test_bh2_rollback.py::test_status_takes_no_lock::took":
        "a real subprocess against a lock wait of 30 s. The bound of 20 s "
        "tells a wait from none, and no call of flock is the event fence",
    "test_deploy_serialize.py::test_no_answer_fails_at_the_deadline::took":
        "a real subprocess, in a file that open PR #961 edits, so the sweep "
        "left it alone",
    "test_deploy_serialize.py::test_the_pull_path_exits_0_at_once_when_the_lock_is_held::took":
        "a real subprocess, in a file that open PR #961 edits, so the sweep "
        "left it alone",
    "test_email_disconnect_order.py::test_a_held_key_share_lock_gives_409_in_seconds_and_"
    "the_loop_runs::waited":
        "an R8 suite on a real database. The 3 s is the route's own "
        "lock_timeout, and 15 s tells it from the 20 s hang guard",
    "test_pdf_render.py::test_f2_the_same_member_still_gets_429_at_once::refused_in":
        "a fake child that sleeps 2 s. A held child and a held org wait are "
        "the follow-up for the five pdf_render budgets",
    "test_pdf_render.py::test_f2_the_wait_runs_out_to_429::waited":
        "the real ORG_WAIT_S of 0.5 s is the behaviour. A held clock in "
        "pdf_render is the follow-up",
    "test_pdf_render.py::test_f2_waiting_colleagues_hold_no_pool_thread::probe":
        "a to_thread probe against 40 waiters that sleep. A held child is the "
        "follow-up",
    "test_pdf_render.py::test_r3_a_flood_holds_no_pool_thread::probe":
        "a to_thread probe against 40 real child processes. A held child is "
        "the follow-up",
    "test_pdf_render.py::test_r4_one_render_per_member_at_a_time::refused_in":
        "a real child process that sleeps 2 s. A held child is the follow-up",
}


def _dotted(node: ast.expr) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return parts[::-1]


def _calls(node: ast.AST, names: frozenset[str], roots: frozenset[str] | None) -> bool:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        parts = _dotted(sub.func)
        if not parts or parts[-1] not in names:
            continue
        if roots is None or any(p in roots for p in parts[:-1]):
            return True
    return False


def _is_number(node: ast.expr) -> bool:
    """A literal number, or arithmetic on literal numbers only."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int | float) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp):
        return _is_number(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_number(node.left) and _is_number(node.right)
    return False


def _files() -> list[Path]:
    return sorted(p for p in _UNIT.glob("*.py") if p.name != Path(__file__).name)


def _module_instants(path: Path, tree: ast.Module) -> set[str]:
    found: set[str] = set()
    bodies = [tree.body] + [n.body for n in tree.body if isinstance(n, ast.ClassDef)]
    for body in bodies:
        for node in body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if not _calls(value, _NOW_CALLS, _NOW_ROOTS):
                continue
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        found.add(f"{path.name}::{name.id}")
    return found


def _budgets(path: Path, tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        timed: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and _calls(node.value, _TIMERS, None):
                timed.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if not timed:
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assert):
                continue
            for cmp in ast.walk(node.test):
                if not isinstance(cmp, ast.Compare):
                    continue
                operands = [cmp.left, *cmp.comparators]
                for i, op in enumerate(cmp.ops):
                    left, right = operands[i], operands[i + 1]
                    if (isinstance(op, ast.Lt | ast.LtE) and isinstance(left, ast.Name)
                            and left.id in timed and _is_number(right)):
                        found.add(f"{path.name}::{fn.name}::{left.id}")
    return found


#: A file that holds none of these words cannot match either rule, so it is
#: not parsed. tests/unit holds about 900 files, and parsing all of them took
#: 20 s on a loaded box.
_WORDS = ("now(", "today(", "monotonic(", "perf_counter(")


@functools.cache
def _scan() -> tuple[frozenset[str], frozenset[str]]:
    instants: set[str] = set()
    budgets: set[str] = set()
    for path in _files():
        source = path.read_text(encoding="utf-8-sig")  # two files carry a BOM
        if not any(word in source for word in _WORDS):
            continue
        tree = ast.parse(source, filename=str(path))
        instants |= _module_instants(path, tree)
        budgets |= _budgets(path, tree)
    return frozenset(instants), frozenset(budgets)


def test_no_new_module_instant_or_wall_clock_budget() -> None:
    instants, budgets = _scan()
    new_instants = sorted(instants - set(_MODULE_NOW_ALLOWED))
    new_budgets = sorted(budgets - set(_BUDGET_ALLOWED))
    assert not new_instants, (
        "A module-level datetime.now/date.today used as a test's 'now'. CI runs "
        "a file up to 30 minutes after import. Use a fixed instant and freeze the "
        f"code's _now() seam to it (#951): {new_instants}"
    )
    assert not new_budgets, (
        "A wall-clock budget: assert <seconds> < N on time.monotonic or "
        "perf_counter. Assert an event or an order (#947), count equal work "
        f"(_linear_time.assert_linear_count), or use a 30 s hang guard: {new_budgets}"
    )


def test_the_allowlists_only_shrink() -> None:
    """An entry that no longer matches must go, so a fixed test is never
    allowed back in by a stale line."""
    instants, budgets = _scan()
    assert sorted(set(_MODULE_NOW_ALLOWED) - instants) == []
    assert sorted(set(_BUDGET_ALLOWED) - budgets) == []
    assert all(reason.strip() for reason in (*_MODULE_NOW_ALLOWED.values(),
                                             *_BUDGET_ALLOWED.values()))


def test_the_rules_find_the_shapes_they_name() -> None:
    """Non-vacuity: each rule fires on the shape it names, and on no other."""
    bad = ast.parse(
        "import time\n"
        "from datetime import UTC, date, datetime\n"
        "NOW = datetime.now(UTC)\n"
        "TODAY = date.today()\n"
        "FIXED = datetime(2026, 10, 13, tzinfo=UTC)\n"
        "class T:\n"
        "    LATER = datetime.now(UTC)\n"
        "def test_x():\n"
        "    started = time.monotonic()\n"
        "    took = time.monotonic() - started\n"
        "    assert took < 2.5\n"
        "    assert 0.1 <= took < 1.0 + 0.5\n"
        "    assert took >= 0.2\n"
        "    assert took < LIMIT\n"
    )
    path = _UNIT / "test_shape.py"
    assert _module_instants(path, bad) == {
        "test_shape.py::NOW", "test_shape.py::TODAY", "test_shape.py::LATER"}
    assert _budgets(path, bad) == {"test_shape.py::test_x::took"}
