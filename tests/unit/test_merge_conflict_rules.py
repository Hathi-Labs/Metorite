"""CIV-34a (D218): the conflict rules of scripts/merge_conflict_rules.py.

Each test gives the three versions of one file (base, ours, theirs) to
`resolve_file` and reads the result. The bot drives the same rules in
tests/unit/test_merge_bot.py.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "merge_conflict_rules", ROOT / "scripts" / "merge_conflict_rules.py"
)
rules = importlib.util.module_from_spec(_spec)
sys.modules["merge_conflict_rules"] = rules
_spec.loader.exec_module(rules)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

TABLE = (
    "| Slice | What |\n|---|---|\n| CIV-27 | a |\n| CIV-28 | b |\n| CIV-29 | c |\n| CIV-30 | d |\n"
)


def test_trivial_takes_the_side_that_changed():
    hunk = rules.Hunk(["same\n"], ["old\n"], ["same\n"])
    assert rules.rule_trivial(hunk, "x.md") == ["same\n"]
    assert rules.rule_trivial(rules.Hunk(["old\n"], ["old\n"], ["new\n"]), "x.md") == ["new\n"]
    assert rules.rule_trivial(rules.Hunk(["a\n"], ["old\n"], ["b\n"]), "x.md") is None


def test_table_rows_keeps_each_sides_own_row():
    ours = TABLE.replace("| CIV-28 | b |", "| CIV-28 | b built |")
    theirs = TABLE.replace("| CIV-29 | c |", "| CIV-29 | c built |")
    text, used = rules.resolve_file("spec.md", TABLE, ours, theirs)
    assert used == ["table-rows"]
    assert text == TABLE.replace("| CIV-28 | b |", "| CIV-28 | b built |").replace(
        "| CIV-29 | c |", "| CIV-29 | c built |"
    )


def test_table_rows_refuses_one_row_changed_on_both_sides():
    ours = TABLE.replace("| CIV-28 | b |", "| CIV-28 | mine |")
    theirs = TABLE.replace("| CIV-28 | b |", "| CIV-28 | theirs |")
    assert rules.resolve_file("spec.md", TABLE, ours, theirs) is None


def test_table_rows_takes_a_new_row_from_each_side():
    ours = TABLE + "| CIV-31 | mine |\n"
    theirs = TABLE.replace("| CIV-30 | d |", "| CIV-30 | d built |")
    text, used = rules.resolve_file("spec.md", TABLE, ours, theirs)
    assert (
        used == ["table-rows"]
        and "| CIV-30 | d built |" in text
        and text.endswith("| CIV-31 | mine |\n")
    )


def test_table_rows_refuses_a_hunk_that_is_not_a_table():
    assert rules.resolve_file("spec.md", "one\n", "mine\n", "theirs\n") is None


def test_an_append_only_markdown_list_is_left_to_gitattributes():
    """INDEX.md and HANDOFF.md have merge=union in .gitattributes, so git never stops on them."""
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for path in ("project-docs/INDEX.md", "project-docs/HANDOFF.md"):
        assert f"{path} merge=union" in attrs
    base = "# H\n\n### H-1\n"
    assert rules.resolve_file("notes.md", base, base + "### H-2\n", base + "### H-3\n") is None


def test_fences_json_lists_are_united_sorted_and_not_repeated():
    base = {"python": {"safety": ["a.py", "c.py"], "process": ["p.py"]}}
    ours = {"python": {"safety": ["a.py", "b.py", "c.py"], "process": ["p.py"]}}
    theirs = {"python": {"safety": ["a.py", "c.py", "d.py", "b.py"], "process": ["p.py"]}}
    text, used = rules.resolve_file(
        "tests/fences.json", json.dumps(base), json.dumps(ours), json.dumps(theirs)
    )
    got = json.loads(text)
    assert used == ["union"] and got["python"]["safety"] == ["a.py", "b.py", "c.py", "d.py"]
    assert text.endswith("\n")


def test_fences_json_honours_a_removal_and_refuses_a_scalar_clash():
    base = {"k": ["a", "b"], "n": 1}
    ours = {"k": ["a"], "n": 1}
    theirs = {"k": ["a", "b", "c"], "n": 1}
    text, _ = rules.resolve_file(
        "tests/fences.json", json.dumps(base), json.dumps(ours), json.dumps(theirs)
    )
    assert json.loads(text)["k"] == ["a", "c"]
    clash = rules.resolve_file(
        "tests/fences.json",
        json.dumps(base),
        json.dumps({**base, "n": 2}),
        json.dumps({**base, "n": 3}),
    )
    assert clash is None


def test_the_real_fences_file_survives_a_round_trip():
    raw = (ROOT / "tests" / "fences.json").read_text(encoding="utf-8").replace("\r\n", "\n")
    out = rules.resolve_json(raw, raw, raw)
    assert out == raw


TEST_BASE = "import os\n\n\ndef test_a():\n    assert os.sep\n"


def test_additions_keeps_both_added_tests_in_a_test_file():
    ours = TEST_BASE + "\n\ndef test_mine():\n    assert True\n"
    theirs = TEST_BASE + "\n\ndef test_theirs():\n    assert True\n"
    text, used = rules.resolve_file("tests/unit/test_x.py", TEST_BASE, ours, theirs)
    assert used == ["additions"] and text.index("test_mine") < text.index("test_theirs")


def test_additions_keeps_both_added_imports():
    base = "import os\n\n\nX = 1\n"
    text, used = rules.resolve_file(
        "apps/x/mod.py",
        base,
        "import os\nimport json\n\n\nX = 1\n",
        "import os\nimport re\n\n\nX = 1\n",
    )
    assert used == ["additions"] and "import json" in text and "import re" in text


def test_additions_refuses_a_non_test_file_with_code_added():
    ours = "X = 1\n\n\ndef mine():\n    pass\n"
    theirs = "X = 1\n\n\ndef theirs():\n    pass\n"
    assert rules.resolve_file("apps/x/mod.py", "X = 1\n", ours, theirs) is None


def test_additions_refuses_a_change_of_a_base_line():
    ours = TEST_BASE.replace("os.sep", "os.name")
    theirs = TEST_BASE + "\n\ndef test_theirs():\n    assert True\n"
    assert rules.resolve_file("tests/unit/test_x.py", TEST_BASE, ours, theirs) is None


def test_additions_refuses_text_that_does_not_compile():
    ours = TEST_BASE + "\n\ndef test_mine(:\n    pass\n"
    theirs = TEST_BASE + "\n\ndef test_theirs():\n    assert True\n"
    assert rules.resolve_file("tests/unit/test_x.py", TEST_BASE, ours, theirs) is None


def test_add_add_resolves_only_when_both_files_are_equal():
    assert rules.resolve_file(
        "tests/unit/test_n.py", "", "def test_a():\n    pass\n", "def test_a():\n    pass\n"
    )
    assert (
        rules.resolve_file(
            "tests/unit/test_n.py", "", "def test_a():\n    pass\n", "def test_b():\n    pass\n"
        )
        is None
    )


def test_blocked_paths_never_get_a_rule():
    high = r"^(infra/(postgres|customer_console)/|deploy/)"
    assert rules.blocked(".github/workflows/pr-check.yml", high)
    assert rules.blocked("infra/postgres/001_x.sql", high)
    assert rules.blocked("deploy/run.sh", high)
    assert rules.blocked("infra/postgres/generated/01_add_columns.sql", high)
    assert not rules.blocked("project-docs/specs/ci_velocity.md", high)


def _repo(tmp_path):
    def git(*a):
        return subprocess.run(
            ["git", *a], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8"
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@x.io")
    git("config", "user.name", "t")
    return git


def test_resolve_merge_resolves_a_real_merge_and_names_each_file(tmp_path, monkeypatch):
    git = _repo(tmp_path)
    (tmp_path / "spec.md").write_text(TABLE, encoding="utf-8", newline="\n")
    (tmp_path / "other.md").write_text("one\n", encoding="utf-8", newline="\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-q", "-b", "pr")
    (tmp_path / "spec.md").write_text(
        TABLE.replace("| CIV-28 | b |", "| CIV-28 | x |"), encoding="utf-8", newline="\n"
    )
    (tmp_path / "other.md").write_text("mine\n", encoding="utf-8", newline="\n")
    git("commit", "-qam", "pr")
    git("checkout", "-q", "main")
    (tmp_path / "spec.md").write_text(
        TABLE.replace("| CIV-29 | c |", "| CIV-29 | y |"), encoding="utf-8", newline="\n"
    )
    (tmp_path / "other.md").write_text("theirs\n", encoding="utf-8", newline="\n")
    git("commit", "-qam", "main")
    git("checkout", "-q", "pr")
    git("-c", "merge.conflictstyle=diff3", "merge", "--no-commit", "--no-ff", "main")
    monkeypatch.chdir(tmp_path)
    done, bad = rules.resolve_merge(None)
    assert done == [("table-rows", "spec.md")] and bad == ["other.md"]


# ── Fix round (PR 2266 review) ───────────────────────────────────────────────


def test_p0_the_selection_of_a_one_file_resolution_is_small():
    """The tests that a resolution reaches, not the scanners and the safety floor."""
    for path in ("project-docs/specs/ci_velocity.md", "tests/unit/test_merge_bot.py"):
        sel = rules.select_tests([path], ROOT)
        assert sel["full"] is False
        assert len(sel["files"]) <= 20, (path, len(sel["files"]))
        assert sel["seconds"] < 900, (path, sel["seconds"])
        # A scanner (a test that lists files) is never in it.
        assert "tests/unit/test_scanner_budget.py" not in sel["files"]
    assert (
        "tests/unit/test_merge_bot.py"
        in rules.select_tests(["tests/unit/test_merge_bot.py"], ROOT)["files"]
    )


def test_p0_the_bot_command_on_the_real_tree_picks_a_small_set():
    """The exact command that merge_bot.sh builds, run in a checkout of this repo."""
    bot = (ROOT / "scripts" / "home_server" / "merge_bot.sh").read_text(encoding="utf-8")
    assert 'python3 "$ROOT/scripts/merge_conflict_rules.py" tests "${resolved[@]}"' in bot
    assert "affected_tests.py --gate" not in bot
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "merge_conflict_rules.py"),
            "tests",
            "tests/unit/test_merge_bot.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    seconds = [int(ln.split()[1]) for ln in lines if ln.startswith("SECONDS ")]
    tests = [ln[5:] for ln in lines if ln.startswith("TEST ")]
    assert len(seconds) == 1 and seconds[0] < 900
    assert "tests/unit/test_merge_bot.py" in tests and len(tests) <= 20, tests


def test_p0_a_conflicted_test_file_is_always_selected(tmp_path):
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_x.py").write_text("def test_a():\n    pass\n")
    # The scan call is split, so this file is not itself a scanner for
    # affected_tests (its text detector would read the literal).
    scan_call = "Path('.')." + "iter" + "dir()"
    (tmp_path / "tests" / "unit" / "test_scan.py").write_text(
        f"from pathlib import Path\n\n\ndef test_s():\n    assert list({scan_call})\n"
    )
    (tmp_path / ".test_durations").write_text(json.dumps({"tests/unit/test_x.py::test_a": 7.5}))
    sel = rules.select_tests(["tests/unit/test_x.py"], tmp_path)
    assert sel == {"full": False, "files": ["tests/unit/test_x.py"], "seconds": 7.5}


def test_p0_ruff_comes_from_the_workdir_venv_and_fails_closed_without_one(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFLICT_RULES_RUFF", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.chdir(tmp_path)
    assert rules._ruff_cmd() is None
    sub = "Scripts" if os.name == "nt" else "bin"
    exe = tmp_path / ".venv" / sub / ("ruff.exe" if os.name == "nt" else "ruff")
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    cmd = rules._ruff_cmd()
    assert cmd is not None and len(cmd) == 1 and os.path.samefile(cmd[0], exe)


def _conflict(tmp_path, files_base, files_pr, files_main, attrs=None):
    git = _repo(tmp_path)
    if attrs:
        (tmp_path / ".gitattributes").write_text(attrs, encoding="utf-8", newline="\n")
    for name, data in files_base.items():
        (tmp_path / name).write_bytes(data)
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-q", "-b", "pr")
    for name, data in files_pr.items():
        (tmp_path / name).write_bytes(data)
    git("commit", "-qam", "pr")
    git("checkout", "-q", "main")
    for name, data in files_main.items():
        (tmp_path / name).write_bytes(data)
    git("commit", "-qam", "main")
    git("checkout", "-q", "pr")
    git("-c", "rerere.enabled=false", "merge", "--no-commit", "--no-ff", "main")
    return git


def test_p1_a_binary_or_no_merge_conflict_is_never_resolved(tmp_path, monkeypatch):
    """A conflict with no markers is not a stored resolution: it keeps one side whole."""
    _conflict(
        tmp_path,
        {"logo.png": b"\x89PNG\0base", "data.txt": b"one\n", "blob.dat": b"one\n"},
        {"logo.png": b"\x89PNG\0mine", "data.txt": b"mine\n", "blob.dat": b"mine\n"},
        {"logo.png": b"\x89PNG\0theirs", "data.txt": b"theirs\n", "blob.dat": b"theirs\n"},
        attrs="data.txt -merge\nblob.dat binary\n",
    )
    monkeypatch.chdir(tmp_path)
    done, bad = rules.resolve_merge(None)
    assert done == [] and sorted(bad) == ["blob.dat", "data.txt", "logo.png"]


def test_p1_a_nul_byte_in_one_side_is_refused(tmp_path, monkeypatch):
    _conflict(
        tmp_path,
        {"notes.md": b"| a | b |\n|---|---|\n| 1 | x |\n| 2 | y |\n"},
        {"notes.md": b"| a | b |\n|---|---|\n| 1 | x\0 |\n| 2 | y |\n"},
        {"notes.md": b"| a | b |\n|---|---|\n| 1 | x |\n| 2 | z |\n"},
    )
    monkeypatch.chdir(tmp_path)
    assert rules.resolve_merge(None) == ([], ["notes.md"])


def test_p1_rerere_is_not_a_rule():
    assert "rerere" not in [name for name, _ in rules.RULES]
    assert '"rerere"' not in Path(rules.__file__).read_text(encoding="utf-8")


PY_BASE = "import pytest\n\n\ndef test_a():\n    assert True\n"


def test_p1_two_tests_with_one_name_are_refused():
    ours = PY_BASE + "\n\ndef test_new():\n    assert 1\n"
    theirs = PY_BASE + "\n\ndef test_new():\n    assert 2\n"
    assert rules.resolve_file("tests/unit/test_x.py", PY_BASE, ours, theirs) is None


def test_p1_two_fixtures_with_one_name_are_refused():
    ours = PY_BASE + "\n\n@pytest.fixture\ndef client():\n    return 'A'\n"
    theirs = PY_BASE + "\n\n@pytest.fixture\ndef client():\n    return 'B'\n"
    assert rules.resolve_file("tests/unit/conftest.py", PY_BASE, ours, theirs) is None


def test_p1_two_methods_with_one_name_in_a_class_are_refused():
    base = "class TestX:\n    def test_a(self):\n        assert True\n"
    ours = base + "\n    def test_b(self):\n        assert 1\n"
    theirs = base + "\n    def test_b(self):\n        assert 2\n"
    assert rules.resolve_file("tests/unit/test_x.py", base, ours, theirs) is None


def test_p1_a_name_that_one_side_already_repeats_still_resolves():
    """A property setter repeats a name on purpose. The merge did not add the repeat."""
    base = (
        "class C:\n    @property\n    def x(self):\n        return 1\n\n"
        "    @x.setter\n    def x(self, v):\n        pass\n"
    )
    ours = base + "\n\ndef test_mine():\n    assert C\n"
    theirs = base + "\n\ndef test_theirs():\n    assert C\n"
    text, used = rules.resolve_file("tests/unit/test_c.py", base, ours, theirs)
    assert used == ["additions"] and "test_mine" in text and "test_theirs" in text


def test_p2_table_rows_never_touches_a_typescript_union():
    head = 'export type Status =\n  | "open"\n'
    base = head + '  | "done";\n\nexport const x = 1;\n'
    ours = head + '  | "done"\n  | "blocked";\n\nexport const x = 1;\n'
    theirs = head + '  | "done"\n  | "waiting";\n\nexport const x = 1;\n'
    assert (
        rules.resolve_file("workbench/control_plane/src/lib/status.ts", base, ours, theirs) is None
    )


def test_p2_a_new_column_against_a_new_row_is_refused():
    base = "# T\n\n| id | name |\n|---|---|\n| a | 1 |\n| b | 2 |\n"
    ours = "# T\n\n| id | name | owner |\n|---|---|---|\n| a | 1 | x |\n| b | 2 | y |\n"
    theirs = base + "| c | 3 |\n"
    assert rules.resolve_file("docs/t.md", base, ours, theirs) is None


def test_p2_a_row_with_the_wrong_cell_count_is_refused():
    ours = TABLE.replace("| CIV-28 | b |", "| CIV-28 | b | extra |")
    theirs = TABLE.replace("| CIV-29 | c |", "| CIV-29 | c built |")
    assert rules.resolve_file("spec.md", TABLE, ours, theirs) is None


def test_p2_a_changed_header_is_refused():
    """The header changed in a clean hunk, and a row conflict sits below it."""
    big = TABLE + "".join(f"| CIV-{n} | r |\n" for n in range(40, 60))
    ours = big.replace("| Slice | What |", "| Slice | Notes |").replace(
        "| CIV-50 | r |", "| CIV-50 | mine |"
    )
    theirs = big.replace("| CIV-51 | r |", "| CIV-51 | theirs |")
    assert rules.resolve_file("spec.md", big, ours, theirs) is None


def test_p2_an_rst_border_is_not_read_as_a_conflict_marker():
    head = '"""Doc.\n\n=========  =====\nState      Why\n'
    tail = 'a          1\n=========  =====\n"""\n\n\ndef test_a():\n    assert True\n'
    base = head + "---------  -----\n" + tail
    ours = head + "=========  =====\n" + tail
    theirs = head + "--------- ------\n" + tail
    # Both sides changed one line in two ways: a real conflict, and no rule fits it.
    assert rules.resolve_file("tests/unit/test_doc.py", base, ours, theirs) is None


def test_p2_a_marker_line_in_the_result_is_refused():
    base = '"""Doc.\n=======\n"""\n\n\ndef test_a():\n    pass\n'
    ours = base + "\n\ndef test_b():\n    pass\n"
    theirs = base + "\n\ndef test_c():\n    pass\n"
    assert rules.resolve_file("tests/unit/test_m.py", base, ours, theirs) is None


def test_also_union_and_rebuild_are_gone():
    base = "# H\n\n### H-1\n"
    ours, theirs = base + "### H-2\n", base + "### H-3\n"
    assert rules.resolve_file("project-docs/HANDOFF.md", base, ours, theirs) is None
    assert rules.blocked("infra/postgres/generated/01_add_columns.sql", None)
    assert [name for name, _ in rules.RULES] == ["trivial", "table-rows", "additions"]
