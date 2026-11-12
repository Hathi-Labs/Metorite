"""CIV-20 slice 1 (CIV-28: no box run): scripts/home_server/lane_handover.sh.

A temporary git repo with a bare origin and a fake `gh` serve every case.
The queue is the merge bot's queue: PR, task, worktree, lane, head SHA, tier,
note, under one header comment line.
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


BODY = "VERIFY: PASS (run 3)\nVisual review: no UI change"
HEADER = "# PR\ttask\tworktree\tlane\thead_sha\ttier\tnote\n"


def _run(tmp, wt, body=BODY, others="", head=None, tier="STANDARD"):
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HANDOVER_QUEUE": str(tmp / "queue.tsv"),
        "FAKE_HEAD": head or git(wt, "rev-parse", "HEAD"),
        "FAKE_BODY": body,
        "FAKE_OTHERS": others,
    }
    return subprocess.run(
        [BASH, str(SCRIPT), "77", "2207", str(wt), "lane-ci", tier, "note one"],
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


def _shell_path(p: Path) -> str:
    """The path as the script writes it: `pwd -P` in Git Bash gives /c/x on Windows."""
    text = str(p)
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text


def _queue(tmp):
    """The data lines of the queue, without the header comment."""
    q = tmp / "queue.tsv"
    text = q.read_text(encoding="utf-8") if q.exists() else ""
    return "".join(line for line in text.splitlines(keepends=True) if not line.startswith("#"))


def test_a_clean_hand_over_appends_one_tsv_line(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    r = _run(tmp, wt)
    assert r.returncode == 0, r.stderr
    head = git(wt, "rev-parse", "HEAD")
    text = (tmp / "queue.tsv").read_text(encoding="utf-8")
    assert text == f"{HEADER}77\t2207\t{_shell_path(wt)}\tlane-ci\t{head}\tSTANDARD\tnote one\n"


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
    r = _run(tmp, wt, body="VERIFY: PASS\nno line here")
    assert r.returncode == 1 and "Visual review:" in r.stderr
    assert _queue(tmp) == ""
    body = "VERIFY: PASS\nVisual review: light, compact, accent, 390 px"
    assert _run(tmp, wt, body=body).returncode == 0


def test_a_backend_diff_needs_no_visual_review_line(repo):
    tmp, wt = repo
    _commit(wt, "scripts/x.py", "x = 1\n")
    assert _run(tmp, wt, body="VERIFY: PASS").returncode == 0


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
    _tmp, wt = repo
    r = subprocess.run(
        [BASH, str(SCRIPT), "77", "2207", str(wt), "lane-ci", "LOW", "a\tb"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1 and "tab" in r.stderr


def test_an_empty_pr_number_is_refused(repo):
    _tmp, wt = repo
    r = subprocess.run(
        [BASH, str(SCRIPT), "", "2207", str(wt), "lane-ci", "LOW", "n"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1 and "are numbers" in r.stderr


def test_a_relative_worktree_is_written_as_an_absolute_path(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HANDOVER_QUEUE": str(tmp / "queue.tsv"),
        "FAKE_HEAD": git(wt, "rev-parse", "HEAD"),
        "FAKE_BODY": BODY,
        "FAKE_OTHERS": "",
    }
    r = subprocess.run(
        [BASH, str(SCRIPT), "77", "2207", "wt", "lane-ci", "LOW", "n"],
        cwd=tmp,
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert _queue(tmp).split("\t")[2] == _shell_path(wt.resolve())


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


def test_a_hand_over_needs_no_box_run(repo):
    # D218 (2026-11-12): GitHub pr-check is the gate, so no box verdict is read.
    # The other checks still run. A fresh HEAD with no verdict file passes.
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    r = _run(tmp, wt)
    assert r.returncode == 0, r.stderr
    assert "box run" not in r.stderr and not (tmp / "verdicts.txt").exists()
    fields = _queue(tmp).rstrip("\n").split("\t")
    head = git(wt, "rev-parse", "HEAD")
    assert [fields[0], fields[1], *fields[3:]] == [
        "77",
        "2207",
        "lane-ci",
        head,
        "STANDARD",
        "note one",
    ]


def test_a_machine_with_no_box_can_hand_over(repo):
    # A shell where `id ci` fails: nothing asks for user ci, so the hand-over passes.
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    shim = tmp / "shim"
    shim.mkdir()
    (shim / "id").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    (shim / "id").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{shim}{os.pathsep}{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HANDOVER_QUEUE": str(tmp / "queue.tsv"),
        "FAKE_HEAD": git(wt, "rev-parse", "HEAD"),
        "FAKE_BODY": BODY,
        "FAKE_OTHERS": "",
    }
    r = subprocess.run(
        [BASH, str(SCRIPT), "77", "2288", str(wt), "lane-ci", "LOW", "n"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert _queue(tmp) != ""


# ── Repair round 1 (PR 2183 review) ─────────────────────────────────────────


def test_a_body_with_no_verify_pass_line_is_refused(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    for body in ("Visual review: no UI change", "VERIFY: FAIL", "  VERIFY: PASS indented"):
        r = _run(tmp, wt, body=body)
        assert r.returncode == 1 and "VERIFY: PASS" in r.stderr, body
    assert _queue(tmp) == ""


def test_the_tier_is_one_of_three_words(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    r = _run(tmp, wt, tier="medium")
    assert r.returncode == 1 and "LOW, STANDARD or HIGH" in r.stderr
    assert _queue(tmp) == ""
    assert _run(tmp, wt, tier="HIGH").returncode == 0
    assert _queue(tmp).split("\t")[5] == "HIGH"


def test_the_queue_records_the_pr_head_and_one_header(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    assert _run(tmp, wt).returncode == 0
    _commit(wt, "b.txt", "y\n")
    assert _run(tmp, wt).returncode == 0
    text = (tmp / "queue.tsv").read_text(encoding="utf-8")
    assert text.startswith(HEADER) and text.count("#") == 1
    heads = [line.split("\t")[4] for line in _queue(tmp).splitlines()]
    assert heads == [git(wt, "rev-parse", "HEAD~1"), git(wt, "rev-parse", "HEAD")]


def test_the_default_queue_is_the_bots_new_queue(repo):
    tmp, wt = repo
    _commit(wt, "a.txt", "x\n")
    home = tmp / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": home.as_posix(),
        "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "FAKE_HEAD": git(wt, "rev-parse", "HEAD"),
        "FAKE_BODY": BODY,
        "FAKE_OTHERS": "",
    }
    env.pop("HANDOVER_QUEUE", None)
    r = subprocess.run(
        [BASH, str(SCRIPT), "77", "2207", str(wt), "lane-ci", "LOW", "n"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (home / "lanes" / "merge-bot-queue.tsv").exists()
    assert not (home / "lanes" / "merge-queue.tsv").exists()
