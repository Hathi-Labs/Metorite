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
    monkeypatch.setattr(ps, "named_tests", lambda *_a, **_k: (set(), False))
    monkeypatch.setattr(ps.at, "FENCE_GUARD_TESTS", ("tests/unit/test_fence_lists.py",), raising=False)
    got = ps.pick(["tests/fences.json"], ["tests/fences.json"])
    assert got == ["tests/unit/test_fence_lists.py"]


def test_a_data_file_that_tests_read_as_text_selects_the_tests_that_name_it():
    """2026-11-15: PR 2248 re-indented a line of tests/fences.json and
    test_merge_conflict_rules (which reads the file) went red on main. No import links
    them, so the picker finds the tests by the name of the data file."""
    named, cut = ps.named_tests(["tests/fences.json"])
    assert "tests/unit/test_merge_conflict_rules.py" in named
    assert "tests/unit/test_fence_lists.py" in named
    assert not cut


def test_the_row_files_and_the_flag_doc_select_the_tests_that_name_them():
    named, _ = ps.named_tests(["project-docs/specs/ai_studio_rows/B6a.md", "docs/flag_reload.md"])
    assert "tests/unit/test_studio_run_as.py" in named
    assert "tests/unit/test_agent_questions_r8.py" in named


def test_code_and_generic_names_select_nothing():
    named, cut = ps.named_tests(["scripts/premerge.sh", "tests/unit/test_premerge.py", "README.md", "package.json",
                                 "workbench/control_plane/src/x.json"])
    assert named == set() and not cut


def test_a_name_that_too_many_tests_mention_is_cut_and_said(monkeypatch):
    named, cut = ps.named_tests(["tests/fences.json"], limit=2)
    assert len(named) == 2 and cut


def test_the_named_tests_run_in_the_first_tier(monkeypatch):
    _patch_reach(monkeypatch, {})
    got, first = ps.pick_tiers(["tests/fences.json"], ["scripts/x.py"])
    assert first >= 1 and "tests/unit/test_merge_conflict_rules.py" in got[:first]


def test_the_tests_of_the_prs_own_data_files_run_first_and_the_fast_ones_before_the_slow(monkeypatch, tmp_path):
    """The replay of PR 2248 (2026-11-15): the test that reads tests/fences.json sat at
    position 67 of 488 and the 240 s cap hit before it. Tier 0 is the tests that name a data
    file of the PR, and each tier runs the files with the smallest recorded run time first."""
    (tmp_path / "tests/unit").mkdir(parents=True)
    for n in ("test_slow.py", "test_fast.py", "test_other.py"):
        (tmp_path / "tests/unit" / n).write_text("x\n", encoding="utf-8")
    (tmp_path / ".test_durations").write_text(
        '{"tests/unit/test_slow.py::t": 90.0, "tests/unit/test_fast.py::t": 0.5, "tests/unit/test_other.py::t": 0.1}',
        encoding="utf-8")
    assert ps._by_size({"tests/unit/test_slow.py", "tests/unit/test_fast.py", "tests/unit/test_other.py"}, tmp_path) == [
        "tests/unit/test_other.py", "tests/unit/test_fast.py", "tests/unit/test_slow.py"]
    _patch_reach(monkeypatch, {})
    monkeypatch.setattr(ps, "named_tests", lambda files, root=None, **_k: (
        ({"tests/unit/test_fence_lists.py"} if "tests/fences.json" in files else {"tests/unit/test_studio_run_as.py"}), False))
    got, first = ps.pick_tiers(["tests/fences.json"], ["docs/x.md"])
    assert got[0] == "tests/unit/test_fence_lists.py", "the PR's own data file tests come first"
    assert first == 2 and "tests/unit/test_studio_run_as.py" in got[:first]
