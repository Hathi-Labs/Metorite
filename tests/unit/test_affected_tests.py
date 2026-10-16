"""Fence for scripts/affected_tests.py, the inner-loop test selector.

It builds a small repo in tmp_path, so the rules are checked without the
real tree's 1,000 test files.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import affected_tests as sel  # noqa: E402


def _write(root: Path, rel: str, body: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    _write(tmp_path, "packages/pkg/pkg/__init__.py")
    _write(tmp_path, "packages/pkg/pkg/leaf.py", "X = 1\n")
    _write(tmp_path, "packages/pkg/pkg/user.py", "from pkg import leaf\n")
    _write(tmp_path, "packages/pkg/pkg/hub.py", "import pkg.user\n")
    _write(tmp_path, "tests/unit/test_leaf.py", "from pkg.leaf import X\n")
    _write(tmp_path, "tests/unit/test_user.py", "import pkg.user\n")
    _write(tmp_path, "tests/unit/test_patch.py", 'patch("pkg.leaf.X")\n')
    _write(tmp_path, "tests/unit/test_doc.py", 'read("docs/guide_to_things.md")\n')
    _write(tmp_path, "tests/unit/test_other.py", "import os\n")
    return tmp_path


def test_the_module_name_follows_init_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert sel.module_name("packages/pkg/pkg/leaf.py", root) == "pkg.leaf"
    assert sel.module_name("packages/pkg/pkg/__init__.py", root) == "pkg"


def test_a_leaf_change_picks_direct_and_one_level_up(tmp_path: Path) -> None:
    picked, full, _ = sel.select(["packages/pkg/pkg/leaf.py"], _repo(tmp_path))
    assert "tests/unit/test_leaf.py" in picked  # from pkg.leaf import X
    assert "tests/unit/test_patch.py" in picked  # a dotted string
    assert "tests/unit/test_user.py" in picked  # imports a module that imports leaf
    assert "tests/unit/test_other.py" not in picked
    assert full == []


def test_a_test_that_names_a_doc_path_is_picked(tmp_path: Path) -> None:
    picked, _, _ = sel.select(["docs/guide_to_things.md"], _repo(tmp_path))
    assert picked == ["tests/unit/test_doc.py"]


def test_a_hub_is_not_expanded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sel, "HUB_LIMIT", 0)
    picked, _, _ = sel.select(["packages/pkg/pkg/leaf.py"], _repo(tmp_path))
    assert "tests/unit/test_user.py" not in picked


def test_a_shared_config_change_asks_for_the_full_suite(tmp_path: Path) -> None:
    _, full, _ = sel.select(["pyproject.toml", "tests/unit/conftest.py"], _repo(tmp_path))
    assert full == ["pyproject.toml", "tests/unit/conftest.py"]


def test_frontend_files_go_to_vitest(tmp_path: Path) -> None:
    f = "workbench/control_plane/src/lib/nav.ts"
    picked, _, frontend = sel.select([f], _repo(tmp_path))
    assert frontend == [f]
    assert picked == []


def test_a_migration_change_runs_the_prefix_guard(tmp_path: Path) -> None:
    picked, _, _ = sel.select(["infra/postgres/300_new.sql"], _repo(tmp_path))
    assert "tests/unit/test_migration_prefixes.py" in picked
