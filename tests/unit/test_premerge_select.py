"""`scripts/premerge_select.py` picks the tests a merge can break (CIV-MT, task 3277).

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


def _patch_reach(monkeypatch, table):
    """`table` maps the first changed file to (the tests it reaches, full)."""
    def fake(files, root=None, *, src_only=False):
        files = [f for f in files if not (src_only and f.startswith("tests/"))]
        if not files:
            return set(), False
        got, full = table.get(files[0], (set(), False))
        return set(got), full
    ps._CACHE.clear()
    monkeypatch.setattr(ps, "_reach", fake)


def test_a_changed_test_file_on_either_side_is_kept(monkeypatch):
    _patch_reach(monkeypatch, {})
    got = ps.pick(["tests/unit/test_premerge_select.py"], ["tests/unit/test_premerge.py"])
    assert set(got) == {"tests/unit/test_premerge.py", "tests/unit/test_premerge_select.py"}


def test_changed_tests_come_first(monkeypatch):
    _patch_reach(monkeypatch, {"x.py": ({"tests/unit/test_a.py", "tests/unit/test_z.py"}, False),
                               "y.py": ({"tests/unit/test_a.py", "tests/unit/test_z.py"}, False)})
    got = ps.pick(["x.py", "tests/unit/test_premerge_select.py"], ["y.py", "tests/unit/test_premerge.py"])
    assert set(got[:2]) == {"tests/unit/test_premerge.py", "tests/unit/test_premerge_select.py"}


def test_only_tests_both_sides_reach_are_kept(monkeypatch):
    _patch_reach(monkeypatch, {"a.py": ({"t1", "t2"}, False), "b.py": ({"t2", "t3"}, False)})
    assert ps.pick(["a.py"], ["b.py"]) == ["t2"]


def test_one_full_side_leaves_the_other_side_alone(monkeypatch):
    _patch_reach(monkeypatch, {"a.py": ({"t1", "t2"}, False), "pyproject.toml": ({"t9"}, True)})
    assert ps.pick(["a.py"], ["pyproject.toml"]) == ["t1", "t2"]
    assert ps.full_sides(["a.py"], ["pyproject.toml"]) == ["main"]


def test_both_sides_full_is_none(monkeypatch):
    _patch_reach(monkeypatch, {"pyproject.toml": ({"t"}, True)})
    assert ps.pick(["pyproject.toml"], ["pyproject.toml"]) is None


def test_a_changed_test_that_the_other_sides_source_reaches_runs_first(monkeypatch):
    """Tier 1: main changed test_premerge.py. The PR changed x.py, which that test imports."""
    _patch_reach(monkeypatch, {"x.py": ({"tests/unit/test_premerge.py", "tests/unit/test_z.py"}, False),
                               "y.py": ({"tests/unit/test_z.py"}, False)})
    got, first = ps.pick_tiers(["x.py"], ["y.py", "tests/unit/test_premerge.py", "tests/unit/test_premerge_select.py"])
    assert first == 1 and got[0] == "tests/unit/test_premerge.py"
    assert got[1] == "tests/unit/test_premerge_select.py", "the other changed test is tier 2"


def test_main_writes_lf_bytes_only(monkeypatch, tmp_path, capfdbinary):
    """Review of 2026-11-15, P1: a CR in the output broke pytest in Git Bash."""
    pr, main = tmp_path / "pr.txt", tmp_path / "main.txt"
    pr.write_text("a.py\r\n", encoding="utf-8")
    main.write_text("b.py\r\n", encoding="utf-8")
    monkeypatch.setattr(ps, "pick_tiers", lambda *_a, **_k: (["tests/unit/test_a.py", "tests/unit/test_b.py"], 1))
    monkeypatch.setattr(ps, "full_sides", lambda *_a, **_k: [])
    first, note = tmp_path / "first.txt", tmp_path / "note.txt"
    assert ps.main(["--pr", str(pr), "--main", str(main), "--first-out", str(first), "--note-out", str(note)]) == 0
    out = capfdbinary.readouterr().out
    assert out == b"tests/unit/test_a.py\ntests/unit/test_b.py\n"
    assert b"\r" not in first.read_bytes() and b"\r" not in note.read_bytes()


def test_an_empty_selection_prints_nothing(monkeypatch, tmp_path, capfdbinary):
    pr, main = tmp_path / "pr.txt", tmp_path / "main.txt"
    pr.write_text("a.py\n", encoding="utf-8")
    main.write_text("b.py\n", encoding="utf-8")
    monkeypatch.setattr(ps, "pick_tiers", lambda *_a, **_k: ([], 0))
    monkeypatch.setattr(ps, "full_sides", lambda *_a, **_k: [])
    assert ps.main(["--pr", str(pr), "--main", str(main)]) == 0
    assert capfdbinary.readouterr().out == b""


def test_a_data_file_of_main_reaches_the_tests_that_read_it(monkeypatch):
    """Review of 2026-11-15, P2: select() missed the readers of a data file. The picker
    asks the selector that pr-check uses, `affected_tests.gate`."""
    calls = []

    def fake_gate(files, root=None, **_k):
        calls.append(list(files))
        return {"full": False, "files": ["tests/unit/test_shell_manifests.py"] if "crm.json" in files[0] else [], "reasons": []}

    monkeypatch.setattr(ps.at, "gate", fake_gate, raising=False)
    ps._CACHE.clear()
    reached, full = ps._reach(["crm.json"], ps.at.ROOT)
    assert reached == {"tests/unit/test_shell_manifests.py"} and not full


def test_a_changed_fence_list_adds_the_tests_that_read_it(monkeypatch):
    """gate() cannot read tests/fences.json without the diff text, so the picker adds
    the guards by name (finding F2 of the review of 2026-11-15)."""
    _patch_reach(monkeypatch, {})
    monkeypatch.setattr(ps.at, "FENCE_GUARD_TESTS", ("tests/unit/test_fence_lists.py",), raising=False)
    got = ps.pick(["tests/fences.json"], ["tests/fences.json"])
    assert got == ["tests/unit/test_fence_lists.py"]
