"""CIV-33a: the plugin keeps the listed files and the conftests, and drops the rest."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _run(tmp: Path, listed: list[str] | None, *extra: str) -> str:
    sel = tmp / "sel.txt"
    import os

    env = {**os.environ, "PYTHONPATH": str(_ROOT / "scripts")}
    env.pop("SHARD_SELECTED_FILES", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    (tmp / "pytest.ini").write_text("[pytest]" + chr(10), encoding="utf-8")
    if listed is not None:
        sel.write_text("\n".join(listed), encoding="utf-8")
        env["SHARD_SELECTED_FILES"] = str(sel)
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "--collect-only", "-q",
         "-p", "pytest_only_selected", "-p", "no:cacheprovider", *extra],
        cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    return out.stdout


def _tree(tmp: Path) -> None:
    unit = tmp / "tests" / "unit"
    unit.mkdir(parents=True)
    (unit / "conftest.py").write_text(
        "def pytest_collection_modifyitems(items):\n    pass\n", encoding="utf-8")
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        (unit / name).write_text("def test_x():\n    pass\n", encoding="utf-8")
    sub = unit / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "test_d.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (tmp / "tests" / "other.py").write_text("", encoding="utf-8")


def test_only_listed_files_are_collected(tmp_path: Path) -> None:
    _tree(tmp_path)
    out = _run(tmp_path, ["tests/unit/test_a.py", "tests/unit/sub/test_d.py"])
    assert "test_a.py::test_x" in out
    assert "test_d.py::test_x" in out
    assert "test_b.py" not in out
    assert "test_c.py" not in out
    assert "2 tests collected" in out


def test_no_list_collects_everything(tmp_path: Path) -> None:
    _tree(tmp_path)
    out = _run(tmp_path, None)
    assert "4 tests collected" in out


def test_conftest_and_package_init_are_never_ignored(tmp_path: Path) -> None:
    """A list that names one file still loads the conftest and the package."""
    _tree(tmp_path)
    (tmp_path / "tests/unit/conftest.py").write_text(
        "def pytest_collection_modifyitems(items):\n"
        "    print('CONFTEST_LOADED')\n", encoding="utf-8")
    out = _run(tmp_path, ["tests/unit/sub/test_d.py"], "-s")
    assert "CONFTEST_LOADED" in out
    assert "1 test collected" in out


def test_plugin_source_never_ignores_directories() -> None:
    src = (_ROOT / "scripts" / "pytest_only_selected.py").read_text(encoding="utf-8")
    assert 'collection_path.suffix != ".py"' in src


def test_the_plugin_takes_its_variables_out_of_the_environment(tmp_path):
    """A subprocess that a test starts must not inherit the shard-only variables
    (the SHARD_-only rule of PR 1749, and the Opus review of PR 2192)."""
    import os

    unit = tmp_path / "tests" / "unit"
    unit.mkdir(parents=True)
    scripts = str(_ROOT / "scripts")
    (unit / "test_env.py").write_text(
        "import os\n"
        "def test_env():\n"
        "    assert 'SHARD_SELECTED_FILES' not in os.environ\n"
        f"    assert {scripts!r} not in os.environ.get('PYTHONPATH', '')\n",
        encoding="utf-8",
    )
    sel = tmp_path / "sel.txt"
    sel.write_text("tests/unit/test_env.py", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]" + chr(10), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": scripts, "SHARD_SELECTED_FILES": str(sel),
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "-q",
         "-p", "pytest_only_selected", "-p", "no:cacheprovider"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert out.returncode == 0, out.stdout + out.stderr
