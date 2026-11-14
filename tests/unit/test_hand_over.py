"""CIV-28c: scripts/hand_over.sh, the hand-over of a PR from any machine.

A session on the PC has no worktree on the box. So the wrapper runs
`lane_handover.sh --pr` in the bot's own checkout on the box, over ssh. A fake
`ssh` runs the remote command here, with HOME set to a fake box home. The box
home holds `lanes/merge-bot-src`, a clone with the real lane_handover.sh in it.
A fake `gh` serves the PR. Spec: the CIV-28 row of the CI velocity spec, §8.5.
(The spec path is not written here: a test that names a doc is picked when
that doc changes, and test_affected_tests.py keeps a docs-only change lean.)
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "hand_over.sh"
HANDOVER = ROOT / "scripts" / "home_server" / "lane_handover.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

FAKE_GH = """#!/usr/bin/env bash
case "$*" in
  *headRefOid*) printf '%s\\n' "$FAKE_HEAD" ;;
  *"pr list"*) printf '%s' "$FAKE_OTHERS" ;;
  *body*) printf '%s\\n' "$FAKE_BODY" ;;
esac
"""

# Real ssh joins its arguments into one string for the remote shell. The wrapper
# gives that string as the last argument, so this runs the last argument.
FAKE_SSH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_SSH_LOG"
if [ -n "${FAKE_SSH_DOWN:-}" ]; then
  echo "ssh: connect to host metorite-home port 22: Connection timed out" >&2
  exit 255
fi
cmd="${@: -1}"
HOME="$FAKE_REMOTE_HOME" exec bash -c "$cmd"
"""

BODY = "VERIFY: PASS (run 3)\nVisual review: no UI change"


def git(cwd, *a):
    return subprocess.run(
        ["git", *a], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _script(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.fixture
def box(tmp_path):
    """origin, a session worktree with a PR head, and a fake box home."""
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
    git(wt, "checkout", "-qb", "feat")
    (wt / "a.txt").write_text("x\n", encoding="utf-8")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "feature")
    git(wt, "push", "-q", "origin", "HEAD:refs/pull/77/head")

    remote_home = tmp_path / "box_home"
    src = remote_home / "lanes" / "merge-bot-src"
    subprocess.run(["git", "clone", "-q", str(origin), str(src)], check=True, capture_output=True)
    _script(
        src / "scripts" / "home_server" / "lane_handover.sh", HANDOVER.read_text(encoding="utf-8")
    )
    _script(tmp_path / "bin" / "gh", FAKE_GH)
    _script(tmp_path / "bin" / "ssh", FAKE_SSH)
    (tmp_path / "pc_home").mkdir()
    (tmp_path / "t").mkdir()
    return tmp_path, wt, remote_home


def _run(tmp, wt, remote_home, *args, home=None, **extra):
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
    return subprocess.run(
        [BASH, str(WRAPPER), *args], cwd=wt, env=env, capture_output=True, text=True
    )


def _queue(remote_home):
    q = remote_home / "lanes" / "merge-bot-queue.tsv"
    text = q.read_text(encoding="utf-8") if q.exists() else ""
    return [line.split("\t") for line in text.splitlines() if not line.startswith("#")]


def _ssh_calls(tmp):
    p = tmp / "ssh.log"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_a_pc_session_hands_over_over_ssh(box):
    tmp, wt, remote_home = box
    note = """CIV-28c: it's "quoted", $HOME; and `spaced`"""
    r = _run(tmp, wt, remote_home, "77", "3229", "STANDARD", note)
    assert r.returncode == 0, r.stderr
    assert "handed over: PR 77 (task 3229)" in r.stdout
    calls = _ssh_calls(tmp)
    assert len(calls) == 1 and "-o BatchMode=yes" in calls[0] and " metorite-home " in calls[0]
    (row,) = _queue(remote_home)
    head = git(wt, "rev-parse", "HEAD")
    assert row[0:3] == ["77", "3229", "-"] and row[4:6] == [head, "STANDARD"]
    assert row[3].startswith("session@")
    assert row[6] == note  # the note reaches the box as written
    assert list((tmp / "t").glob("handover-pr*")) == []


def test_a_refusal_exits_1_and_prints_the_reason(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n", FAKE_BODY="no verify line")
    assert r.returncode == 1
    assert "HANDOVER REFUSED" in r.stderr and "VERIFY: PASS" in r.stderr
    assert "hand_over: refused" in r.stderr
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


def test_an_unreachable_box_exits_3_and_queues_nothing(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n", FAKE_SSH_DOWN="1")
    assert r.returncode == 3 and "ssh cannot reach metorite-home" in r.stderr
    assert _queue(remote_home) == []


def test_an_old_box_copy_with_no_pr_mode_is_named(box):
    tmp, wt, remote_home = box
    old = remote_home / "lanes" / "merge-bot-src" / "scripts" / "home_server" / "lane_handover.sh"
    _script(old, "#!/usr/bin/env bash\necho old copy\nexit 0\n")
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW", "n")
    assert r.returncode == 3 and "has no --pr mode" in r.stderr
    assert "old copy" not in r.stdout and _queue(remote_home) == []


def test_too_few_arguments_print_the_usage(box):
    tmp, wt, remote_home = box
    r = _run(tmp, wt, remote_home, "77", "3229", "LOW")
    assert r.returncode == 2 and "usage: hand_over.sh" in r.stderr
    assert _ssh_calls(tmp) == []
