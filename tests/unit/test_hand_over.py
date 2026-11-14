"""CIV-28c: scripts/hand_over.sh, the hand-over of a PR from any machine, and the wait.

A session on the PC has no worktree on the box. So the wrapper runs origin/main's
`lane_handover.sh --pr` in the bot's own checkout on the box, over ssh. A fake
`ssh` runs the remote command here, with HOME set to a fake box home. The box
home holds `lanes/merge-bot-src`, a clone of an origin whose main holds the real
lane_handover.sh and merge_bot.sh. A fake `gh` serves the PR. Spec: the CIV-28
row of the CI velocity spec, §8.5. (The spec path is not written here: a test
that names a doc is picked when that doc changes, and test_affected_tests.py
keeps a docs-only change lean.)
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "hand_over.sh"
HANDOVER = ROOT / "scripts" / "home_server" / "lane_handover.sh"
BOT = ROOT / "scripts" / "home_server" / "merge_bot.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

FAKE_GH = """#!/usr/bin/env bash
case "$*" in
  *isCrossRepository*) printf '%s\\tfalse\\tmain\\tfeat\\n' "$FAKE_HEAD" ;;
  *headRefOid*) printf '%s\\n' "$FAKE_HEAD" ;;
  *"pr list"*) printf '%s' "$FAKE_OTHERS" ;;
  *body*) printf '%s\\n' "$FAKE_BODY" ;;
esac
"""

# Real ssh joins its arguments into one string for the remote shell. The wrapper
# gives that string as the last argument, so this runs the last argument.
FAKE_SSH = """#!/usr/bin/env bash
# One log line per call: the remote script has many lines, so keep its first one.
printf '%s\\n' "${*%%$'\\n'*}" >> "$FAKE_SSH_LOG"
if [ -n "${FAKE_SSH_DOWN:-}" ]; then
  echo "ssh: connect to host metorite-home port 22: Connection timed out" >&2
  exit 255
fi
cmd="${@: -1}"
if [ -n "${FAKE_SSH_LOSE_CALL:-}" ] && [ "$(wc -l < "$FAKE_SSH_LOG")" = "$FAKE_SSH_LOSE_CALL" ]; then
  HOME="$FAKE_REMOTE_HOME" bash -c "$cmd" > /dev/null 2>&1
  echo "Connection to metorite-home closed by remote host." >&2
  exit 255
fi
HOME="$FAKE_REMOTE_HOME" exec bash -c "$cmd"
"""

BODY = "VERIFY: PASS (run 3)\nVisual review: no UI change"
SHA = "a" * 40
HEADER = "# PR\ttask\tworktree\tlane\thead_sha\ttier\tnote\n"


def git(cwd, *a):
    return subprocess.run(
        ["git", *a], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _script(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _main_commit(seed: Path, path: str, text: str):
    """Put a file on origin's main, as a merged PR would."""
    git(seed, "checkout", "-q", "main")
    _script(seed / path, text)
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", f"main: {path}")
    git(seed, "push", "-q", "origin", "main")


@pytest.fixture
def box(tmp_path):
    """origin with the real scripts on main, a session worktree with a PR head, a box home."""
    origin = tmp_path / "origin.git"
    wt = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(wt)], check=True, capture_output=True)
    for k, v in (("user.email", "t@x.io"), ("user.name", "t")):
        git(wt, "config", k, v)
    (wt / "notes.md").write_text("one\n", encoding="utf-8")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "base")
    git(wt, "push", "-q", "origin", "main")
    _main_commit(wt, "scripts/home_server/lane_handover.sh", HANDOVER.read_text(encoding="utf-8"))
    _main_commit(wt, "scripts/home_server/merge_bot.sh", BOT.read_text(encoding="utf-8"))
    git(wt, "checkout", "-qb", "feat")
    (wt / "a.txt").write_text("x\n", encoding="utf-8")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "feature")
    git(wt, "push", "-q", "origin", "HEAD:refs/pull/77/head")

    remote_home = tmp_path / "box_home"
    src = remote_home / "lanes" / "merge-bot-src"
    subprocess.run(["git", "clone", "-q", str(origin), str(src)], check=True, capture_output=True)
    _script(tmp_path / "bin" / "gh", FAKE_GH)
    _script(tmp_path / "bin" / "ssh", FAKE_SSH)
    (tmp_path / "pc_home").mkdir()
    (tmp_path / "t").mkdir()
    return tmp_path, wt, remote_home


def _env(tmp, wt, remote_home, home=None, **extra):
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HOME": (home or tmp / "pc_home").as_posix(),
        "FAKE_REMOTE_HOME": remote_home.as_posix(),
        "FAKE_SSH_LOG": (tmp / "ssh.log").as_posix(),
        "FAKE_HEAD": git(wt, "rev-parse", "HEAD"),
        "FAKE_BODY": BODY,
        "FAKE_OTHERS": "",
        "TMPDIR": (tmp / "t").as_posix(),
        # The fake box has no systemd. The real check runs on the box.
        "HANDOVER_TIMER_CHECK": "true",
        "HAND_OVER_POLL": "1",
        **extra,
    }
    for k in (
        "HAND_OVER_HOST",
        "HAND_OVER_SRC",
        "HAND_OVER_LANE",
        "HANDOVER_QUEUE",
        "HANDOVER_REPO",
    ):
        if k not in extra:
            env.pop(k, None)
    return env


def _run(tmp, wt, remote_home, *args, home=None, **extra):
    return subprocess.run(
        [BASH, str(WRAPPER), *args],
        cwd=wt,
        env=_env(tmp, wt, remote_home, home=home, **extra),
        capture_output=True,
        text=True,
    )


def _queue(remote_home):
    q = remote_home / "lanes" / "merge-bot-queue.tsv"
    text = q.read_text(encoding="utf-8") if q.exists() else ""
    return [line.split("\t") for line in text.splitlines() if not line.startswith("#")]


def _ssh_calls(tmp):
    p = tmp / "ssh.log"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _box_lines(remote_home, queue=(), log=()):
    """Write hand-over lines to the box queue, and result lines to the box log."""
    lanes = remote_home / "lanes"
    lanes.mkdir(parents=True, exist_ok=True)
    q = lanes / "merge-bot-queue.tsv"
    with q.open("a", encoding="utf-8", newline="\n") as f:
        if q.stat().st_size == 0:
            f.write(HEADER)
        for pr in queue:
            f.write(f"{pr}\t3229\t-\tsession@pc\t{SHA}\tSTANDARD\tn\n")
    with (lanes / "merge-bot.log").open("a", encoding="utf-8", newline="\n") as f:
        for line in log:
            f.write(line + "\n")


# ── The hand-over ───────────────────────────────────────────────────────────


def test_a_pc_session_hands_over_over_ssh(box):
    tmp, wt, remote_home = box
    note = """CIV-28c: it's "quoted", $HOME; and `spaced`"""
    r = _run(tmp, wt, remote_home, "77", "3229", "STANDARD", note)
    assert r.returncode == 0, r.stderr
    assert "handed over: PR 77 (task 3229)" in r.stdout
    calls = _ssh_calls(tmp)
    # Two calls: one read of the queue for the number of this hand-over, then the hand-over.
    assert len(calls) == 2
    assert all("-o BatchMode=yes" in c and " metorite-home " in c for c in calls)
    assert "this is hand-over 1 of PR 77" in r.stdout
    (row,) = _queue(remote_home)
    head = git(wt, "rev-parse", "HEAD")
    assert row[0:3] == ["77", "3229", "-"] and row[4:6] == [head, "STANDARD"]
    assert row[3].startswith("session@")
    assert row[6] == note  # the note reaches the box as written
    assert list((tmp / "t").glob("lane_handover.*")) == []  # the temp copy of the script is gone


def test_fix1_the_box_runs_origin_mains_script_not_a_frozen_copy(box):
    # The copy in the bot's checkout is old. origin/main has the real script.
    tmp, wt, remote_home = box
    frozen = (
        remote_home / "lanes" / "merge-bot-src" / "scripts" / "home_server" / "lane_handover.sh"
    )
    _script(frozen, "#!/usr/bin/env bash\necho FROZEN COPY RAN\nexit 0\n")
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n")
    assert r.returncode == 0, r.stderr
    assert "FROZEN COPY RAN" not in r.stdout + r.stderr
    assert "handed over: PR 77" in r.stdout and len(_queue(remote_home)) == 1


def test_fix1_a_main_with_no_pr_mode_is_named(box):
    tmp, wt, remote_home = box
    _main_commit(wt, "scripts/home_server/lane_handover.sh", "#!/usr/bin/env bash\necho old\n")
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n")
    assert r.returncode == 3 and "has no --pr mode" in r.stderr
    assert "old" not in r.stdout and _queue(remote_home) == []


def test_a_refusal_exits_1_and_prints_the_reason(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n", FAKE_BODY="no verify line")
    assert r.returncode == 1
    assert "HANDOVER REFUSED" in r.stderr and "VERIFY: PASS" in r.stderr
    assert "hand_over: refused" in r.stderr
    assert _queue(remote_home) == []


def test_fix1_a_stopped_bot_timer_exits_3_through_the_wrapper(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n", HANDOVER_TIMER_CHECK="false")
    assert r.returncode == 3 and "not active" in r.stderr
    assert _queue(remote_home) == []


def test_on_the_box_it_runs_with_no_ssh(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "on the box", home=remote_home)
    assert r.returncode == 0, r.stderr
    assert _ssh_calls(tmp) == []
    (row,) = _queue(remote_home)
    assert row[0] == "77" and row[2] == "-" and row[6] == "on the box"


def test_the_lane_field_can_be_named(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n", HAND_OVER_LANE="lane-ci")
    assert r.returncode == 0, r.stderr
    assert _queue(remote_home)[0][3] == "lane-ci"


def test_fix1_a_lost_ssh_says_the_result_is_unknown(box):
    # ssh exit 255 can come after the box appended the line, so it never says "nothing".
    # Fix round 2: --status then reads the truth. Here the box queued it.
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n", FAKE_SSH_LOSE_CALL="2")
    assert r.returncode == 3
    assert "The result is unknown" in r.stderr and "--status 77" in r.stderr
    assert "Nothing is queued" not in r.stderr and len(_queue(remote_home)) == 1
    s = _run(tmp, wt, remote_home, "--status", "77")
    assert s.returncode == 5 and "WAITING: hand-over 1 of PR 77" in s.stdout


def test_fix2_a_box_that_cannot_be_read_first_hands_over_nothing(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n", FAKE_SSH_DOWN="1")
    assert r.returncode == 3 and "Nothing was handed over" in r.stderr
    assert _queue(remote_home) == [] and len(_ssh_calls(tmp)) == 1


def test_too_few_arguments_print_the_usage(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW")
    assert r.returncode == 2 and "usage: hand_over.sh" in r.stderr
    assert _ssh_calls(tmp) == []


# ── Fix round 1: the wait for the bot's line ────────────────────────────────


def test_fix1_the_wait_prints_a_done_line_and_exits_0(box):
    tmp, wt, remote_home = box
    _box_lines(remote_home, queue=[77], log=["DONE 76 1111", "DONE 77 abc123"])
    r = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "DONE 77 abc123"


def test_fix1_the_wait_prints_a_back_line_and_exits_1(box):
    tmp, wt, remote_home = box
    _box_lines(remote_home, queue=[77], log=["BACK 77 red: Lint"])
    r = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0")
    assert r.returncode == 1 and r.stdout.strip() == "BACK 77 red: Lint"
    assert "comment" in r.stderr


def test_fix1_a_line_of_an_older_hand_over_is_skipped(box):
    # Hand-over 1 got a BACK. Hand-over 2 waits for the second result line.
    tmp, wt, remote_home = box
    _box_lines(remote_home, queue=[77], log=["BACK 77 red: Lint"])
    _box_lines(remote_home, queue=[77])
    r = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0")
    assert r.returncode == 3 and "no DONE or BACK line for PR 77" in r.stderr
    assert r.stdout == ""
    _box_lines(remote_home, log=["BACK 77 premerge: boom"])
    r = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0")
    assert r.returncode == 1 and r.stdout.strip() == "BACK 77 premerge: boom"


def test_fix1_the_wait_sees_a_line_that_comes_later(box):
    tmp, wt, remote_home = box
    _box_lines(remote_home, queue=[77], log=["RESOLVED 77 table-rows"])
    p = subprocess.Popen(
        [BASH, str(WRAPPER), "--wait", "77"],
        cwd=wt,
        env=_env(tmp, wt, remote_home, HAND_OVER_WAIT="1"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(3)
    assert p.poll() is None, "the wait ended before the bot wrote a line"
    _box_lines(remote_home, log=["DONE 77 feed123"])
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0 and out.strip() == "DONE 77 feed123", err
    # The RESOLVED line is a note, once, on stderr.
    assert err.count("RESOLVED 77 table-rows") == 1 and "git pull --ff-only" in err


def test_fix1_the_wait_times_out_with_exit_3(box):
    tmp, wt, remote_home = box
    _box_lines(remote_home, queue=[77])
    r = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0")
    assert r.returncode == 3 and "in 0 min" in r.stderr and r.stdout == ""


def test_fix1_the_wait_needs_a_hand_over_and_a_box(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0")
    assert r.returncode == 3 and "no hand-over 1 of PR 77" in r.stderr
    _box_lines(remote_home, queue=[77], log=["DONE 77 abc"])
    r = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0", FAKE_SSH_DOWN="1")
    assert r.returncode == 3 and "cannot read" in r.stderr and r.stdout == ""


def test_fix1_the_wait_runs_on_the_box_with_no_ssh(box):
    tmp, wt, remote_home = box
    _box_lines(remote_home, queue=[77], log=["DONE 77 abc"])
    r = _run(tmp, wt, remote_home, "--wait", "77", home=remote_home, HAND_OVER_WAIT="0")
    assert r.returncode == 0 and r.stdout.strip() == "DONE 77 abc"
    assert _ssh_calls(tmp) == []


def test_fix1_the_wait_after_a_real_hand_over(box):
    tmp, wt, remote_home = box
    assert _run(tmp, wt, remote_home, "77", "3229", "LOW", "n").returncode == 0
    _box_lines(remote_home, log=["BACK 77 red: Unit tests"])
    r = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0")
    assert r.returncode == 1 and r.stdout.strip() == "BACK 77 red: Unit tests"


# ── Fix round 2 (PR 2294 re-review) ─────────────────────────────────────────


def _pr78_with(wt, path):
    """A second PR head, 78, that touches one more path."""
    git(wt, "checkout", "-q", "feat")
    _script(wt / path, "x\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", f"touch {path}")
    git(wt, "push", "-q", "origin", "HEAD:refs/pull/78/head")
    return git(wt, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("args", "path", "kind"),
    [
        (("77", "3229", "HIGH", "n"), None, "HIGH tier"),
        (("78", "3229", "LOW", "n"), "deploy/run.sh", "HIGH path"),
    ],
)
def test_fix2_a_final_refusal_exits_4_and_says_never_change_the_diff(box, args, path, kind):
    tmp, wt, remote_home = box
    head = _pr78_with(wt, path) if path else git(wt, "rev-parse", "HEAD")
    r = _run(tmp, wt, remote_home, *args, FAKE_HEAD=head)
    assert r.returncode == 4, r.stderr
    assert f"{kind}: merge it yourself with the full loop" in r.stderr
    assert (
        "Do not change the diff to pass" in r.stderr and "the bot never takes this PR" in r.stderr
    )
    assert _queue(remote_home) == []


def test_fix2_status_names_each_state_with_its_own_code(box):
    tmp, wt, remote_home = box
    s = _run(tmp, wt, remote_home, "--status", "77")
    assert s.returncode == 6 and "NOT QUEUED" in s.stdout
    _box_lines(remote_home, queue=[77])
    assert _run(tmp, wt, remote_home, "--status", "77").returncode == 5
    _box_lines(remote_home, log=["BACK 77 red: Lint"])
    s = _run(tmp, wt, remote_home, "--status", "77")
    assert s.returncode == 1 and s.stdout.strip() == "BACK 77 red: Lint"
    _box_lines(remote_home, queue=[77], log=["DONE 77 abc123"])
    s = _run(tmp, wt, remote_home, "--status", "77")
    assert s.returncode == 0 and s.stdout.strip() == "DONE 77 abc123"
    s = _run(tmp, wt, remote_home, "--status", "77", FAKE_SSH_DOWN="1")
    assert s.returncode == 3 and "cannot read" in s.stderr


def test_fix2_an_old_back_never_reads_as_the_result_of_a_new_hand_over(box):
    tmp, wt, remote_home = box
    # Hand-over 1 goes BACK.
    assert _run(tmp, wt, remote_home, "77", "3229", "LOW", "n").returncode == 0
    _box_lines(remote_home, log=["BACK 77 red: Lint"])
    s = _run(tmp, wt, remote_home, "--status", "77")
    assert s.returncode == 1 and "red: Lint" in s.stdout
    # Hand-over 2 is refused, so it is not in the queue. The old BACK is not its result.
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n", FAKE_BODY="no verify line")
    assert r.returncode == 1
    s = _run(tmp, wt, remote_home, "--status", "77")
    assert s.returncode == 6 and "hand-over 2 is not in it" in s.stdout, s.stdout
    w = _run(tmp, wt, remote_home, "--wait", "77", HAND_OVER_WAIT="0")
    assert w.returncode == 3 and w.stdout == ""
    # Hand-over 2 lands. It waits, and the old BACK is skipped.
    assert _run(tmp, wt, remote_home, "77", "3229", "LOW", "n").returncode == 0
    assert _run(tmp, wt, remote_home, "--status", "77").returncode == 5
    _box_lines(remote_home, log=["BACK 77 premerge: boom"])
    s = _run(tmp, wt, remote_home, "--status", "77")
    assert s.returncode == 1 and s.stdout.strip() == "BACK 77 premerge: boom"


def test_fix2_the_unit_names_the_one_bot_checkout_and_the_doc_moves_it():
    """The bot runs from ~/lanes/merge-bot-src, and the early refusals read its lists there."""
    unit = (ROOT / "scripts" / "home_server" / "systemd" / "metorite-merge-bot.service").read_text(
        encoding="utf-8"
    )
    assert "WorkingDirectory=%h/lanes/merge-bot-src\n" in unit
    assert "ExecStart=%h/lanes/merge-bot-src/scripts/home_server/merge_bot.sh\n" in unit
    assert "%h/Metorite/scripts" not in unit
    assert 'SRC_DEFAULT="$HOME/lanes/merge-bot-src"' in WRAPPER.read_text(encoding="utf-8")
    doc = " ".join((ROOT / "docs" / "home_box.md").read_text(encoding="utf-8").split())
    for step in (
        "systemctl --user stop metorite-merge-bot.timer",
        "systemctl --user is-active --quiet metorite-merge-bot.service",
        "git -c core.fileMode=false checkout -q --detach origin/main",
        "systemctl --user start metorite-merge-bot.timer",
    ):
        assert step in doc, step
