"""CIV-20 slice 1: scripts/home_server/lane_handover.sh.

A temporary git repo with a bare origin and a fake `gh` serve every case.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "home_server" / "lane_handover.sh"
BASH = shutil.which("bash")

FAKE_GH = """#!/usr/bin/env bash
case "$*" in
  *headRefOid*) printf '%s\\n' "$FAKE_HEAD" ;;
  *"pr list"*) printf '%s' "$FAKE_OTHERS" ;;
  *body*) printf '%s\\n' "$FAKE_BODY" ;;
esac
"""


def git(cwd, *a):
    return subprocess.run(
        ["git", *a], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    wt = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(wt)], check=True, capture_output=True)
    for k, v in (("user.email", "t@x.io"), ("user.name", "t")):
        git(wt, "config", k, v)
    (wt / "infra" / "postgres").mkdir(parents=True)
    (wt / "infra" / "postgres" / "010_a.sql").write_text("select 1;\n", encoding="utf-8")
    (wt / "notes.md").write_text("one\n", encoding="utf-8")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "base")
    git(wt, "push", "-q", "origin", "main")
    git(wt, "checkout", "-qb", "feat")
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    return tmp_path, wt


def _run(tmp, wt, body="Visual review: no UI change", others="", head=None, verdicts=None):
    sha = git(wt, "rev-parse", "HEAD")
    (tmp / "verdicts.txt").write_text(
        f"GREEN {sha} selection expected=0\n" if verdicts is None else verdicts, encoding="utf-8"
    )
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HANDOVER_QUEUE": str(tmp / "queue.tsv"),
        "HANDOVER_VERDICTS_FILE": str(tmp / "verdicts.txt"),
        "FAKE_HEAD": head or git(wt, "rev-parse", "HEAD"),
        "FAKE_BODY": body,
        "FAKE_OTHERS": others,
    }
    return subprocess.run(
        [BASH, str(SCRIPT), "77", "2207", str(wt), "lane-ci", "note one"],
        env=env,
        capture_output=True,
        text=True,
    )


def _commit(wt, path, text):
    p = wt / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", f"edit {path}")


def _queue(tmp):
    q = tmp / "queue.tsv"
    return q.read_text(encoding="utf-8") if q.exists() else ""


def test_a_clean_hand_over_appends_one_tsv_line(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    r = _run(tmp, wt)
    assert r.returncode == 0, r.stderr
    assert _queue(tmp) == f"77\t2207\t{wt}\tlane-ci\tnote one\n"


def test_a_dirty_worktree_or_an_unpushed_head_is_refused(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    (wt / "a.txt").write_text("dirty\n", encoding="utf-8")
    assert "uncommitted" in _run(tmp, wt).stderr
    git(wt, "checkout", "--", "a.txt")
    r = _run(tmp, wt, head="0" * 40)
    assert r.returncode == 1 and "Push first" in r.stderr
    assert _queue(tmp) == ""


def test_a_frontend_diff_needs_the_visual_review_line(repo):
    tmp, wt = repo
    _commit(wt, "workbench/control_plane/src/x.ts", "export {}\n")
    r = _run(tmp, wt, body="no line here")
    assert r.returncode == 1 and "Visual review:" in r.stderr
    assert _queue(tmp) == ""
    assert _run(tmp, wt, body="Visual review: light, compact, accent, 390 px").returncode == 0


def test_a_backend_diff_needs_no_visual_review_line(repo):
    tmp, wt = repo
    _commit(wt, "scripts/x.py", "x = 1\n")
    assert _run(tmp, wt, body="").returncode == 0


def test_a_conflict_with_main_is_refused_and_names_the_file(repo):
    tmp, wt = repo
    _commit(wt, "notes.md", "mine\n")
    git(wt, "checkout", "-q", "main")
    _commit(wt, "notes.md", "theirs\n")
    git(wt, "push", "-q", "origin", "main")
    git(wt, "checkout", "-q", "feat")
    r = _run(tmp, wt)
    assert r.returncode == 1 and "conflicts in" in r.stderr and "notes.md" in r.stderr
    assert _queue(tmp) == ""


def test_a_migration_below_mains_highest_is_refused(repo):
    tmp, wt = repo
    _commit(wt, "infra/postgres/005_low.sql", "select 1;\n")
    r = _run(tmp, wt)
    assert r.returncode == 1 and "not above main's highest number (10)" in r.stderr


def test_a_migration_number_taken_by_another_open_pr_is_refused(repo):
    tmp, wt = repo
    _commit(wt, "infra/postgres/011_mine.sql", "select 1;\n")
    r = _run(tmp, wt, others="55 infra/postgres/011_theirs.sql\n")
    assert r.returncode == 1 and "also taken by PR 55" in r.stderr
    assert _run(tmp, wt, others="55 infra/postgres/012_theirs.sql\n").returncode == 0


def test_a_note_with_a_tab_is_refused(repo):
    tmp, wt = repo
    r = subprocess.run(
        [BASH, str(SCRIPT), "77", "2207", str(wt), "lane-ci", "a\tb"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1 and "tab" in r.stderr


def test_an_empty_pr_number_is_refused(repo):
    tmp, wt = repo
    r = subprocess.run(
        [BASH, str(SCRIPT), "", "2207", str(wt), "lane-ci", "n"], capture_output=True, text=True
    )
    assert r.returncode == 1 and "are numbers" in r.stderr


def test_a_relative_worktree_is_written_as_an_absolute_path(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    (tmp / "verdicts.txt").write_text(
        f"GREEN {git(wt, 'rev-parse', 'HEAD')} full expected=0\n", encoding="utf-8"
    )
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HANDOVER_QUEUE": str(tmp / "queue.tsv"),
        "HANDOVER_VERDICTS_FILE": str(tmp / "verdicts.txt"),
        "FAKE_HEAD": git(wt, "rev-parse", "HEAD"),
        "FAKE_BODY": "",
        "FAKE_OTHERS": "",
    }
    r = subprocess.run(
        [BASH, str(SCRIPT), "77", "2207", "wt", "lane-ci", "n"],
        cwd=tmp,
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert _queue(tmp).split("\t")[2] == str(wt.resolve())


def test_a_repeated_index_row_after_the_merge_is_refused(repo):
    tmp, wt = repo
    git(wt, "checkout", "-q", "main")
    _commit(wt, "project-docs/INDEX.md", "## ACTIVE\n| a.md | one |\n")
    git(wt, "push", "-q", "origin", "main")
    (wt / ".gitattributes").write_text("project-docs/INDEX.md merge=union\n", encoding="utf-8")
    _commit(wt, ".gitattributes", "project-docs/INDEX.md merge=union\n")
    git(wt, "push", "-q", "origin", "main")
    git(wt, "checkout", "-q", "-b", "two", "main")
    _commit(wt, "project-docs/INDEX.md", "## ACTIVE\n| a.md | mine |\n")
    git(wt, "checkout", "-q", "main")
    _commit(wt, "project-docs/INDEX.md", "## ACTIVE\n| a.md | theirs |\n")
    git(wt, "push", "-q", "origin", "main")
    git(wt, "checkout", "-q", "two")
    r = _run(tmp, wt)
    assert r.returncode == 1 and "lists a spec twice: a.md" in r.stderr, r.stderr
    assert _queue(tmp) == ""


def test_a_hand_over_needs_a_green_box_run_of_the_head_commit(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    sha = git(wt, "rev-parse", "HEAD")
    r = _run(tmp, wt, verdicts="")
    assert r.returncode == 1 and "no GREEN box run" in r.stderr and _queue(tmp) == ""
    r = _run(tmp, wt, verdicts=f"GREEN {'0' * 40} full expected=0\n")
    assert r.returncode == 1 and "no GREEN box run" in r.stderr  # a GREEN run of another commit
    r = _run(tmp, wt, verdicts=f"RED {sha} selection expected=0 unexpected=shard-2\n")
    assert r.returncode == 1 and "is RED" in r.stderr and _queue(tmp) == ""
    r = _run(tmp, wt, verdicts=f"RED {sha} selection expected=0\nGREEN {sha} full expected=2\n")
    assert r.returncode == 0, r.stderr  # a later GREEN run of the same commit passes
