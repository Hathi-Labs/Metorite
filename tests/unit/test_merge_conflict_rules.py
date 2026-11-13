"""CIV-34a (D218): the conflict rules of scripts/merge_conflict_rules.py.

Each test gives the three versions of one file (base, ours, theirs) to
`resolve_file` and reads the result. The bot drives the same rules in
tests/unit/test_merge_bot.py.
"""

import importlib.util
import json
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


def test_union_keeps_ours_then_new_lines_of_theirs():
    base = "# H\n\n### H-1\n"
    text, used = rules.resolve_file(
        "project-docs/HANDOFF.md", base, base + "### H-2\n", base + "### H-3\n"
    )
    assert used == ["union"] and text.index("H-2") < text.index("H-3")
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
    assert not rules.blocked("infra/postgres/generated/01_add_columns.sql", high)
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
