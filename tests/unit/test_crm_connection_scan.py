"""CIV-33d: the source-tree scans of ``test_crm_connection.py``.

These fences walk every Python file of ``apps`` and ``packages``, so
``affected_tests`` runs this file on every Python change. They live apart
from the large file so that file runs only when its own imports change.
The assertions are unchanged.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit.test_crm_connection import _BLOB, _REPO, _STORE


def _names(roots: list[Path], needle: str, *, exempt: Path | None = None) -> list[str]:
    """Each python file that names *needle* as an identifier, attribute or string."""
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            if exempt is not None and path.resolve() == exempt.resolve():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                text = None
                if isinstance(node, ast.Name):
                    text = node.id
                elif isinstance(node, ast.Attribute):
                    text = node.attr
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    text = node.value
                elif isinstance(node, ast.keyword):
                    text = node.arg
                if text and needle in text:
                    hits.append(path.resolve().as_posix())
                    break
    return hits
def test_only_the_store_names_the_connections_table() -> None:
    assert _STORE.exists()
    readers = _names([_REPO / "apps", _REPO / "packages"], _BLOB, exempt=_STORE)
    assert readers == [], f"Only connections/store.py may name the connections table: {readers}"
    assert _names([_STORE.parent], _BLOB) == [_STORE.resolve().as_posix()], (
        "the fence is vacuous if the store no longer names the table"
    )
def test_the_table_fence_sees_a_second_reader(tmp_path: Path) -> None:
    for i, source in enumerate((
        f"def f(r):\n    return r.{_BLOB}\n",
        f"SQL = 'SELECT {_BLOB} FROM t'\n",
        f"def g(**kw):\n    return h({_BLOB}=1)\n",
    )):
        plant = tmp_path / f"second{i}.py"
        plant.write_text(source, encoding="utf-8")
        assert _names([tmp_path], _BLOB) != [], source
        plant.unlink()
def _attr_readers(names: set[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for root in (_REPO / "apps", _REPO / "packages"):
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            seen: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in names:
                    seen.add(node.attr)
                elif isinstance(node, ast.Constant) and node.value in names:
                    # getattr(settings, "crm_zoho_client_id") also counts.
                    seen.add(node.value)
            for name in seen:
                out.setdefault(name, []).append(path.resolve().as_posix())
    return out
def test_one_module_reads_the_zoho_client_settings() -> None:
    reader = (_REPO / "apps/services/gateway/gateway/routes/crm/connection.py").resolve().as_posix()
    found = _attr_readers({"crm_zoho_client_id", "crm_zoho_client_secret"})
    for name, files in found.items():
        assert files == [reader], (name, files)
    assert set(found) == {"crm_zoho_client_id", "crm_zoho_client_secret"}
