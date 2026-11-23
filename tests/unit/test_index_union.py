"""CIV-20 slice 3: INDEX.md merges with the union driver, and no row repeats.

The union driver keeps both sides of a clash, so a second row for one spec
could slip in. The first test is the fence for that.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "project-docs" / "INDEX.md"
ROW = re.compile(r"^\|\s*\*{0,2}`?([\w./-]+\.md)`?\*{0,2}\s*\|")


def test_no_spec_is_listed_twice_in_a_table_section():
    seen: dict[str, list[int]] = {}
    section = ""
    for n, line in enumerate(INDEX.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("## "):
            section = line
        m = ROW.match(line)
        if m:
            seen.setdefault(f"{section}|{m.group(1)}", []).append(n)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dups, f"a spec row repeats in one section (union merge?): {dups}"


def test_the_attribute_names_the_index():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "project-docs/INDEX.md merge=union" in text


def _git(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, check=check, capture_output=True, text=True)


def test_two_branches_that_append_a_row_merge_without_a_conflict(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@x.io"), ("user.name", "t")):
        _git(tmp_path, "config", k, v)
    idx = tmp_path / "project-docs" / "INDEX.md"
    idx.parent.mkdir()
    (tmp_path / ".gitattributes").write_text("project-docs/INDEX.md merge=union\n", encoding="utf-8")
    idx.write_text("| a.md |\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    for name in ("b.md", "c.md"):
        _git(tmp_path, "checkout", "-q", "-b", name[0], "main")
        idx.write_text(f"| a.md |\n| {name} |\n", encoding="utf-8")
        _git(tmp_path, "commit", "-qam", name)
    r = _git(tmp_path, "merge", "-q", "--no-edit", "b", check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = idx.read_text(encoding="utf-8").split("\n")
    assert "| b.md |" in rows and "| c.md |" in rows
