"""`scripts/premerge_select.py` picks the tests a merge can break (CIV-32, task 3277).

Cases of 2026-11-13/14: PR A changed a function, PR B called it, and no file
overlapped. PR A added an action source, PR B added a fence that needs a row for
each source. The picker keeps the tests both sides reach, and every test file
that either side changed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("premerge_select", ROOT / "scripts" / "premerge_select.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = _load()


def test_a_changed_test_file_on_either_side_is_kept(monkeypatch):
    monkeypatch.setattr(ps.at, "select", lambda changed, **k: ([], [], []))
    got = ps.pick(["tests/unit/test_premerge_select.py"], ["tests/unit/test_premerge.py"])
    assert set(got) == {"tests/unit/test_premerge.py", "tests/unit/test_premerge_select.py"}


def test_changed_tests_come_first(monkeypatch):
    monkeypatch.setattr(ps.at, "select", lambda changed, **k: (["tests/unit/test_a.py", "tests/unit/test_z.py"], [], []))
    got = ps.pick(["x.py", "tests/unit/test_premerge_select.py"], ["x.py", "tests/unit/test_premerge.py"])
    assert set(got[:2]) == {"tests/unit/test_premerge.py", "tests/unit/test_premerge_select.py"}


def test_only_tests_both_sides_reach_are_kept(monkeypatch):
    sets = {"a.py": ["t1", "t2"], "b.py": ["t2", "t3"]}
    monkeypatch.setattr(ps.at, "select", lambda changed, **k: (sets[changed[0]], [], []))
    assert ps.pick(["a.py"], ["b.py"]) == ["t2"]


def test_one_full_side_leaves_the_other_side_alone(monkeypatch):
    def fake(changed, **k):
        return (["t1", "t2"], [], []) if changed[0] == "a.py" else (["t9"], ["pyproject.toml"], [])
    monkeypatch.setattr(ps.at, "select", fake)
    assert ps.pick(["a.py"], ["pyproject.toml"]) == ["t1", "t2"]


def test_both_sides_full_is_none(monkeypatch):
    monkeypatch.setattr(ps.at, "select", lambda changed, **k: (["t"], ["pyproject.toml"], []))
    assert ps.pick(["pyproject.toml"], ["pyproject.toml"]) is None


def test_a_changed_test_that_the_other_sides_source_reaches_runs_first(monkeypatch):
    """Tier 1: main changed test_premerge.py. The PR changed x.py, which that test imports."""
    def fake(changed, **k):
        if changed == ["x.py"]:
            return (["tests/unit/test_premerge.py", "tests/unit/test_z.py"], [], [])
        return (["tests/unit/test_z.py"], [], [])
    monkeypatch.setattr(ps.at, "select", fake)
    got, first = ps.pick_tiers(["x.py"], ["y.py", "tests/unit/test_premerge.py", "tests/unit/test_premerge_select.py"])
    assert first == 1 and got[0] == "tests/unit/test_premerge.py"
    assert got[1] == "tests/unit/test_premerge_select.py", "the other changed test is tier 2"
