"""CIV-33d: the source-tree scans of ``test_crm_change_history.py``.

These fences walk every file of ``routes/crm`` and of the gateway, so
``affected_tests`` runs this file on every Python change. They live apart
from the large R8 file so that file runs only when its own imports change.
The assertions are unchanged. The lists of routes stay in the old file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from tests.unit.test_crm_change_history import HISTORY, LOGGED, OUT, PENDING, SEAM

_ROOT = Path(__file__).resolve().parents[2]
_CRM = _ROOT / "apps/services/gateway/gateway/routes/crm"


def _calls(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return names
def _reach(start: ast.AST, funcs: dict[str, ast.AST]) -> set[str]:
    """Every call name reachable from *start* through same-module helpers."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        for name in _calls(todo.pop()):
            if name not in seen:
                seen.add(name)
                if name in funcs:
                    todo.append(funcs[name])
    return seen
def write_routes(root: Path = _CRM) -> dict[str, tuple[str, set[str]]]:
    """``"VERB /crm/path"`` → (``file::function``, the calls it reaches)."""
    found: dict[str, tuple[str, set[str]]] = {}
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = {
            n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for fn in funcs.values():
            for dec in fn.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in {"post", "patch", "put", "delete"}
                    and dec.args and isinstance(dec.args[0], ast.Constant)
                ):
                    key = f"{dec.func.attr.upper()} /crm{dec.args[0].value}"
                    assert key not in found, f"two handlers for {key}"
                    found[key] = (f"{path.name}::{fn.name}", _reach(fn, funcs))
    return found
def unclassified(routes: dict[str, Any]) -> list[str]:
    lists = (LOGGED, HISTORY, PENDING, OUT)
    return sorted(k for k in routes if sum(k in lst for lst in lists) != 1)
def unlogged(routes: dict[str, tuple[str, set[str]]]) -> list[str]:
    return sorted(
        f"{k} ({routes[k][0]})" for k in LOGGED
        if k in routes and SEAM not in routes[k][1]
    )
def test_the_scan_finds_the_write_routes() -> None:
    assert len(write_routes()) >= 70, "the scan found too few write routes"
def test_every_write_route_is_in_exactly_one_list() -> None:
    """Done when 1. A new write route of ``routes/crm/`` fails here until
    somebody decides: logged, pending for PR 2, or out with a reason."""
    routes = write_routes()
    assert not unclassified(routes), (
        "write routes in no list, or in two (LOGGED, HISTORY, PENDING, OUT): "
        f"{unclassified(routes)}"
    )
    stale = sorted(
        k for lst in (LOGGED, HISTORY, PENDING, OUT) for k in lst if k not in routes
    )
    assert not stale, f"list entries that name no route: {stale}"
    for lst in (LOGGED, PENDING, OUT):
        assert all(reason.strip() for reason in lst.values())
def test_every_logged_route_reaches_the_seam() -> None:
    """Done when 1: each LOGGED route reaches ``log_change`` in its own body
    or in a helper of its module."""
    assert not unlogged(write_routes()), (
        f"LOGGED routes that never call {SEAM}: {unlogged(write_routes())}"
    )
def test_the_fence_fails_on_a_new_route_and_on_a_dropped_call(tmp_path: Path) -> None:
    """Mutation: a new write route in no list, and a LOGGED route that lost
    its call, each fail by name."""
    for path in _CRM.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        if path.name == "views.py":
            body = body.replace(
                "        await log_change(db, user, Change(\n"
                "            area=\"view\", action=change_log.CREATE,",
                "        await _gone(db, user, Change(\n"
                "            area=\"view\", action=change_log.CREATE,",
            )
            body += (
                "\n\n@router.post(\"/zz-new\")\nasync def zz_new() -> None:\n"
                "    return None\n"
            )
        (tmp_path / path.name).write_text(body, encoding="utf-8")
    routes = write_routes(tmp_path)
    assert unclassified(routes) == ["POST /crm/zz-new"]
    assert unlogged(routes) == ["POST /crm/views (views.py::create_view)"]
def test_one_writer_of_the_table() -> None:
    """Only ``change_log.py`` writes ``crm_change_log`` in the gateway, and
    ``log_change`` holds its one INSERT. It opens no session of its own, so
    the row joins the transaction of the change."""
    gateway = _ROOT / "apps/services/gateway/gateway"
    write = re.compile(r"(INSERT INTO|UPDATE|DELETE FROM)\s+crm_change_log\b")
    writers = sorted(
        str(p.relative_to(gateway)).replace("\\", "/")
        for p in gateway.rglob("*.py")
        if write.search(p.read_text(encoding="utf-8"))
    )
    assert writers == ["routes/crm/change_log.py"], writers
    log = (_CRM / "change_log.py").read_text(encoding="utf-8")
    assert log.count("INSERT INTO crm_change_log") == 1
    tree = ast.parse(log)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == SEAM)
    assert "_INSERT_SQL" in ast.unparse(fn)
    assert "_tenant_session" not in ast.unparse(fn)
#: What locks the thing of an Undo: a read ``FOR UPDATE``, the lock seam of
#: ``core.load_row``, the lock of the settings row, or ``admin._current``.
_LOCKS = ("FOR UPDATE", "lock=True", "_LOCK_SETTINGS_SQL", "_current(")
def _undo_functions(root: Path = _CRM) -> dict[str, tuple[str, dict[str, ast.AST]]]:
    """``file::function`` → (its source, the functions of its module), for
    each function that ``@change_log.undoes(...)`` registers."""
    found: dict[str, tuple[str, dict[str, ast.AST]]] = {}
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = {
            n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for fn in funcs.values():
            if any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr == "undoes" for d in fn.decorator_list
            ):
                found[f"{path.name}::{fn.name}"] = (_code(fn), funcs)
    return found
def _code(fn: Any) -> str:
    """The statements of *fn*, with no docstring: its words are no code."""
    body = fn.body[1:] if ast.get_docstring(fn) is not None else fn.body
    return "\n".join(ast.unparse(s) for s in body)
def test_each_undo_function_locks_its_thing_before_the_check() -> None:
    """P3a (fix round 1). Each Undo reads its thing ``FOR UPDATE`` before it
    checks the values, so a write between the check and the Undo waits.
    Mutation: drop the lock of one Undo function → red by name."""
    found = _undo_functions()
    assert len(found) == 8, sorted(found)
    late = []
    for name, (source, funcs) in sorted(found.items()):
        first_lock = min((source.find(m) for m in _LOCKS if m in source), default=-1)
        first_check = source.find("changed_since")
        if first_lock < 0 or (0 <= first_check < first_lock):
            late.append(name)
        if "_current(" in source:
            assert "lock=True" in _code(funcs["_current"]), name
    assert not late, f"Undo functions that check before a lock: {late}"
    # Fix round 2 (P3-4): EACH read of a row by `load_row` in an Undo locks,
    # in every branch, not only the first read of the function. Mutation:
    # drop `lock=True` from the stage read of the DELETE branch of
    # `pipelines._undo_stage` → red by name.
    unlocked = sorted(
        f"{name}: {ast.unparse(call)}"
        for name, (_source, funcs) in found.items()
        for call in ast.walk(funcs[name.split("::", 1)[1]])
        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "load_row"
        and not any(k.arg == "lock" and getattr(k.value, "value", None) is True
                    for k in call.keywords)
    )
    assert not unlocked, f"Undo reads with no lock: {unlocked}"
