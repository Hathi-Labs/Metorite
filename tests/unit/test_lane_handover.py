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
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


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


def _run(tmp, wt, body="Visual review: no UI change", others="", head=None):
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HANDOVER_QUEUE": str(tmp / "queue.tsv"),
        "FAKE_HEAD": head or git(wt, "rev-parse", "HEAD"),
        "FAKE_BODY": body,
        "FAKE_OTHERS": others,
    }
    return subprocess.run(
        [BASH, str(SCRIPT), "77", "2207", str(wt), "lane-ci", "note one"],
        env=env, capture_output=True, text=True,
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
        [BASH, str(SCRIPT), "77", "2207", str(wt), "lane-ci", "a\tb"], capture_output=True, text=True
    )
    assert r.returncode == 1 and "tab" in r.stderr
