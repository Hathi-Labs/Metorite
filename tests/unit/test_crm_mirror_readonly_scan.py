"""CIV-33d: the source-tree scans of ``test_crm_mirror_readonly.py``.

These fences walk the files of ``routes/crm`` and of the sync, so
``affected_tests`` runs this file on every Python change. They live apart
from the large R8 file so that file runs only when its own imports change.
The assertions are unchanged. The shared helpers stay in the old file.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit.test_crm_mirror_readonly import (
    _CRM,
    _EXEMPT,
    _GATEWAY,
    _GUARD_NAMES,
    _OVERLAY_WORDS,
    _STUDIO_COLUMNS,
    _calls,
    _reach,
    _sql_strings,
    _unguarded_raw_writes,
)


def _write_routes() -> list[tuple[str, str, set[str]]]:
    found = []
    for path in sorted(_CRM.glob("*.py")):
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
                ):
                    found.append((path.name, fn.name, _reach(fn, funcs)))
    return found
def test_every_write_route_reaches_the_guard() -> None:
    routes = _write_routes()
    assert len(routes) >= 20, "the scan found too few write routes"
    bare = [
        f"{mod}::{fn}" for mod, fn, calls in routes
        if mod not in _EXEMPT and not (calls & _GUARD_NAMES)
    ]
    assert not bare, (
        "write routes that skip the mirror guard (call assert_writable, "
        f"insert_row or update_row): {bare}"
    )
def test_raw_deletes_in_routes_call_the_guard() -> None:
    """A DELETE is raw SQL and never meets ``update_row``. Its function must guard."""
    missing = []
    for path in sorted(_CRM.glob("*.py")):
        if path.name in _EXEMPT | {"core.py", "sync_zoho.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            src = ast.get_source_segment(path.read_text(encoding="utf-8"), fn) or ""
            if "DELETE FROM" in src and "assert_writable" not in _calls(fn):
                missing.append(f"{path.name}::{fn.name}")
    # The deal-contact unlink guards its deal; the cleanup helpers are not routes.
    assert not missing, f"raw DELETE without assert_writable: {missing}"
def test_every_raw_write_to_a_guarded_table_is_guarded() -> None:
    bad = []
    for path in sorted(_CRM.glob("*.py")):
        bad += _unguarded_raw_writes(path.read_text(encoding="utf-8"), path.name)
    assert not bad, f"raw writes with no guard and no allowlist reason: {bad}"
def _overlay_sql(roots: tuple[Path, ...]) -> list[str]:
    """``path:line: word`` for each SQL string under *roots* that names an
    overlay column or ``color``."""
    bad: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            for line, value in _sql_strings(path.read_text(encoding="utf-8-sig")):
                for match in _OVERLAY_WORDS.finditer(value):
                    bad.append(f"{path.name}:{line}: {match.group(1)}")
    return bad
def test_no_sql_of_the_sync_names_an_overlay_column() -> None:
    """CRM-C3 done when 15: the sync and the connector never write the overlay."""
    roots = (_GATEWAY / "crm_sync", _GATEWAY / "crm_sources")
    assert all(r.is_dir() for r in roots)
    assert _overlay_sql(roots) == []
def test_the_scan_reads_the_routes_too() -> None:
    """The same scan finds the overlay in ``routes/crm/``, so it reads SQL."""
    found = _overlay_sql((_CRM,))
    assert any(f.startswith("core.py:") and "ui_position" in f for f in found), found
    assert any(f.startswith("admin.py:") and "ui_position" in f for f in found), found
def test_the_overlay_scan_proves_itself(tmp_path: Path) -> None:
    """CRM-C3 done when 16: a planted literal that names ``ui_position``."""
    nl = chr(10)
    (tmp_path / "planted.py").write_text(
        "_SQL = 'UPDATE crm_deal_statuses SET ui_position = :p'" + nl,
        encoding="utf-8",
    )
    assert _overlay_sql((tmp_path,)) == ["planted.py:1: ui_position"]
    (tmp_path / "planted.py").write_text(
        "async def go(db):" + nl
        + "    await db.execute(text(f'UPDATE {t} SET ui_color = :c'))" + nl,
        encoding="utf-8",
    )
    assert _overlay_sql((tmp_path,)) == ["planted.py:2: ui_color"]
    # A Python name is not SQL.
    (tmp_path / "planted.py").write_text(
        "def go():" + nl + "    hidden = True" + nl + "    return hidden" + nl,
        encoding="utf-8",
    )
    assert _overlay_sql((tmp_path,)) == []
def _studio_sql(roots: tuple[Path, ...]) -> list[str]:
    """``path:line: word`` for each SQL string under *roots* that names one of
    the seven studio columns."""
    bad: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            for line, value in _sql_strings(path.read_text(encoding="utf-8-sig")):
                for match in _STUDIO_COLUMNS.finditer(value):
                    bad.append(f"{path.name}:{line}: {match.group(1)}")
    return bad
def test_no_sql_of_the_sync_names_a_studio_column() -> None:
    """CRM-C4a done when 14: a sync leaves the seven new columns unchanged."""
    roots = (_GATEWAY / "crm_sync", _GATEWAY / "crm_sources")
    assert _studio_sql(roots) == []
def test_the_studio_scan_proves_itself(tmp_path: Path) -> None:
    nl = chr(10)
    (tmp_path / "planted.py").write_text(
        "_SQL = 'UPDATE crm_field_defs SET retired_at = NULL'" + nl, encoding="utf-8",
    )
    assert _studio_sql((tmp_path,)) == ["planted.py:1: retired_at"]
    found = _studio_sql((_CRM,))
    assert any(f.startswith("field_studio.py:") and "is_system" in f for f in found), found
