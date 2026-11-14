"""Union merge for the pure append files (CIV-33, task 3284, docs/merge_hotspots.md).

Two PRs that each append a flag paragraph or a flag test must merge with no
conflict. The union driver keeps both sides. Its one failure is a duplicate,
so a test name cannot appear twice in test_flag_reload.py.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNION_FILES = ("docs/flag_reload.md", "tests/unit/test_flag_reload.py")
_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.x"}


def _git(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          env=_ENV, check=True).stdout


def test_the_attribute_names_each_append_file():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for f in UNION_FILES:
        assert f"{f} merge=union" in text, f


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_two_appends_merge_clean_under_the_attribute(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".gitattributes").write_text("flags.md merge=union\n", encoding="utf-8")
    (tmp_path / "flags.md").write_text("`A_ORGS` one.\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "b1")
    with open(tmp_path / "flags.md", "a", encoding="utf-8") as fh:
        fh.write("\n`B_ORGS` two.\n")
    _git(tmp_path, "commit", "-qam", "b1")
    _git(tmp_path, "checkout", "-q", "main")
    with open(tmp_path / "flags.md", "a", encoding="utf-8") as fh:
        fh.write("\n`C_ORGS` three.\n")
    _git(tmp_path, "commit", "-qam", "c")
    _git(tmp_path, "merge", "-q", "--no-edit", "b1")
    body = (tmp_path / "flags.md").read_text(encoding="utf-8")
    assert "`B_ORGS` two." in body and "`C_ORGS` three." in body


def test_no_test_name_repeats_in_the_flag_reload_tests():
    tree = ast.parse((ROOT / "tests/unit/test_flag_reload.py").read_text(encoding="utf-8"))
    names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    dup = sorted({n for n in names if names.count(n) > 1})
    assert not dup, f"a union merge left a duplicate test: {dup}"
