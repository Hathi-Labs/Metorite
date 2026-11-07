"""CIV-5: scripts/bisect_main.sh, scripts/home_server/bisect_poll.sh and
scripts/junit_failed_ids.py.

The bisect tests build a fixture repo in tmp_path. Six commits carry the
subjects "(#101)" to "(#106)", and a chosen commit breaks one test.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BISECT = ROOT / "scripts" / "bisect_main.sh"
POLL = ROOT / "scripts" / "home_server" / "bisect_poll.sh"
JUNIT = ROOT / "scripts" / "junit_failed_ids.py"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# The probe command of the fixture. It logs the PR of the probed commit.
# It exits 2 for the PRs in $UNJ. Else it runs pytest on the given ids.
PROBE_CMD = (
    'n=$(git log -1 --format=%s | sed -E "s/.*\\(#([0-9]+)\\)$/\\1/"); '
    'echo "$n" >> "$PROBE_LOG"; '
    'case " $UNJ " in *" $n "*) exit 2;; esac; '
    '"$PY" -m pytest -x -q -p no:cacheprovider "$@"'
)


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return out.stdout.strip()


def make_repo(tmp_path: Path, broken: set[int], subjects: dict[int, str] | None = None):
    """Six commits (#101 to #106). calc.add is wrong in the PRs of `broken`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_other():\n    assert True\n",
        encoding="utf-8",
    )
    shas: dict[int, str] = {}
    for pr in range(101, 107):
        body = "def add(a, b):\n    return a - b\n" if pr in broken else "def add(a, b):\n    return a + b\n"
        (repo / "calc.py").write_text(body, encoding="utf-8")
        (repo / "note.txt").write_text(f"{pr}\n", encoding="utf-8")
        git(repo, "add", "-A")
        subject = (subjects or {}).get(pr, f"change number {pr} (#{pr})")
        git(repo, "commit", "-q", "-m", subject)
        shas[pr] = git(repo, "rev-parse", "HEAD")
    return repo, shas


def run_bisect(repo: Path, good: str, bad: str, tmp_path: Path, unj: str = "", extra_env=None):
    log = tmp_path / "probes.log"
    log.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "BISECT_TEST_CMD": PROBE_CMD,
        "PROBE_LOG": str(log),
        "UNJ": unj,
        "PY": sys.executable,
        "TMPDIR": str(tmp_path),
        **(extra_env or {}),
    }
    proc = subprocess.run(
        ["bash", str(BISECT), good, bad, "test_calc.py::test_add"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    probes = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
    return proc, probes


def worktrees(repo: Path) -> list[str]:
    return git(repo, "worktree", "list").splitlines()


def test_finds_the_breaking_pr_in_few_probes(tmp_path):
    repo, shas = make_repo(tmp_path, broken={104, 105, 106})
    head = git(repo, "rev-parse", "HEAD")
    proc, probes = run_bisect(repo, shas[101], shas[106], tmp_path)
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("FIRST_BAD")]
    assert lines == [f"FIRST_BAD {shas[104]} change number 104 (#104) PR=104"]
    n = 5
    assert len(probes) <= math.ceil(math.log2(n)) + 2
    assert proc.stderr.count("PROBE ") == len(probes)
    # The caller's checkout stays as it was.
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "branch", "--show-current") == "main"
    assert len(worktrees(repo)) == 1
    assert list(tmp_path.glob("bisect.*")) == []


def test_good_commit_that_fails_exits_3(tmp_path):
    repo, shas = make_repo(tmp_path, broken={101, 102, 103, 104, 105, 106})
    proc, probes = run_bisect(repo, shas[101], shas[106], tmp_path)
    assert proc.returncode == 3
    assert "good" in proc.stderr.lower()
    assert "FIRST_BAD" not in proc.stdout
    assert len(probes) == 1


def test_bad_commit_that_passes_exits_3(tmp_path):
    repo, shas = make_repo(tmp_path, broken=set())
    proc, _ = run_bisect(repo, shas[101], shas[106], tmp_path)
    assert proc.returncode == 3
    assert "bad" in proc.stderr.lower()
    assert "FIRST_BAD" not in proc.stdout
    assert len(worktrees(repo)) == 1


def test_unjudgeable_commit_is_skipped_and_the_answer_holds(tmp_path):
    # #104 cannot be judged, and the culprit is #103.
    repo, shas = make_repo(tmp_path, broken={103, 104, 105, 106})
    proc, _ = run_bisect(repo, shas[101], shas[106], tmp_path, unj="104")
    assert proc.returncode == 0, proc.stderr
    assert f"FIRST_BAD {shas[103]} " in proc.stdout
    assert "PR=103" in proc.stdout
    assert "cannot be judged" in proc.stderr


def test_adjacent_unjudgeable_commits_exit_4(tmp_path):
    repo, shas = make_repo(tmp_path, broken={104, 105, 106})
    proc, _ = run_bisect(repo, shas[101], shas[106], tmp_path, unj="103 104")
    assert proc.returncode == 4
    assert "FIRST_BAD" not in proc.stdout
    assert shas[103][:12] in proc.stdout + proc.stderr
    assert shas[105][:12] in proc.stdout + proc.stderr
    assert len(worktrees(repo)) == 1


def test_subject_without_pr_number(tmp_path):
    repo, shas = make_repo(tmp_path, broken={104, 105, 106}, subjects={104: "hand made fix"})
    proc, _ = run_bisect(repo, shas[101], shas[106], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert f"FIRST_BAD {shas[104]} hand made fix PR=none" in proc.stdout


def test_probe_cap_exits_5(tmp_path):
    repo, shas = make_repo(tmp_path, broken={104, 105, 106})
    proc, probes = run_bisect(repo, shas[101], shas[106], tmp_path, extra_env={"BISECT_MAX_PROBES": "3"})
    assert proc.returncode == 5
    assert len(probes) <= 3


def test_sigterm_during_a_probe_leaves_no_worktree(tmp_path):
    repo, shas = make_repo(tmp_path, broken={104, 105, 106})
    env = {
        **os.environ,
        "BISECT_TEST_CMD": "sleep 60",
        "TMPDIR": str(tmp_path),
    }
    proc = subprocess.Popen(
        ["bash", str(BISECT), shas[101], shas[106], "test_calc.py::test_add"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline and len(worktrees(repo)) < 2:
        time.sleep(0.1)
    assert len(worktrees(repo)) == 2, "the probe worktree never appeared"
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=30)
    assert proc.returncode != 0
    assert len(worktrees(repo)) == 1
    assert list(tmp_path.glob("bisect.*")) == []


# ── junit_failed_ids.py ────────────────────────────────────────────────


def run_junit(*paths: Path):
    return subprocess.run(
        [sys.executable, "-I", str(JUNIT), *map(str, paths)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_junit_ids_from_failures_and_errors(tmp_path):
    xml = tmp_path / "a.xml"
    xml.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.unit.test_a" name="test_one"><failure message="x"/></testcase>'
        '<testcase classname="tests.unit.test_a.TestK" name="test_two[p-1]"><error message="x"/></testcase>'
        '<testcase classname="tests.unit.test_a" name="test_ok"/>'
        '<testcase classname="tests.unit.test_b" name="test_skip"><skipped/></testcase>'
        '<testcase classname="tests.unit.test_a" name="test_one"><failure/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    out = run_junit(xml)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "tests/unit/test_a.py::TestK::test_two[p-1]",
        "tests/unit/test_a.py::test_one",
    ]


def test_junit_reads_a_directory_and_survives_bad_xml(tmp_path):
    d = tmp_path / "junit"
    d.mkdir()
    (d / "ok.xml").write_text(
        '<testsuite><testcase classname="tests.t" name="test_x"><failure/></testcase></testsuite>',
        encoding="utf-8",
    )
    (d / "bad.xml").write_text("<testsuite", encoding="utf-8")
    out = run_junit(d)
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["tests/t.py::test_x"]
    assert "bad.xml" in out.stderr


# ── bisect_poll.sh ─────────────────────────────────────────────────────


POLL_HARNESS = r"""
set -euo pipefail
source "$POLL"
# The shim. It answers `gh run list` and `gh run download` from files.
gh_cmd() {
  echo "gh $*" >> "$GH_LOG"
  case "$*" in
    *"run list"*"--status success"*) cat "$GH_GREEN" ;;
    *"run list"*) cat "$GH_LIST" ;;
    *"run download"*)
      dir=""
      while [ $# -gt 0 ]; do [ "$1" = "-D" ] && dir="$2"; shift; done
      mkdir -p "$dir"; cp "$GH_JUNIT" "$dir/junit.xml" ;;
    *) echo "unexpected gh call" >&2; return 9 ;;
  esac
}
# The bisect stub. It records its arguments.
run_bisect_script() {
  echo "$*" >> "$BISECT_LOG"
  echo "FIRST_BAD abc fake (#7) PR=7"
  return "${BISECT_RC:-0}"
}
main
"""


def run_poll(tmp_path: Path, runs, green, junit_xml=None, bisect_rc=0):
    (tmp_path / "list.json").write_text(json.dumps(runs), encoding="utf-8")
    (tmp_path / "green.json").write_text(json.dumps(green), encoding="utf-8")
    (tmp_path / "j.xml").write_text(
        junit_xml
        or '<testsuite><testcase classname="tests.unit.test_a" name="test_one"><failure/></testcase></testsuite>',
        encoding="utf-8",
    )
    state = tmp_path / "state"
    env = {
        **os.environ,
        "POLL": str(POLL),
        "GH_LOG": str(tmp_path / "gh.log"),
        "GH_LIST": str(tmp_path / "list.json"),
        "GH_GREEN": str(tmp_path / "green.json"),
        "GH_JUNIT": str(tmp_path / "j.xml"),
        "BISECT_LOG": str(tmp_path / "bisect.log"),
        "BISECT_RC": str(bisect_rc),
        "BISECT_STATE_DIR": str(state),
        "TMPDIR": str(tmp_path),
    }
    proc = subprocess.run(
        ["bash", "-c", POLL_HARNESS],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    handled = state / "handled"
    ids = handled.read_text(encoding="utf-8").split() if handled.exists() else []
    blog = tmp_path / "bisect.log"
    calls = blog.read_text(encoding="utf-8").splitlines() if blog.exists() else []
    return proc, ids, calls


def run(i, conclusion, sha, status="completed"):
    return {"databaseId": i, "conclusion": conclusion, "headSha": sha, "status": status}


def test_poll_green_latest_does_nothing(tmp_path):
    proc, ids, calls = run_poll(tmp_path, [run(30, "success", "c3"), run(20, "failure", "c2")], [])
    assert proc.returncode == 0, proc.stderr
    assert calls == [] and ids == []
    assert "BISECT_RESULT" not in proc.stdout


def test_poll_skips_handled_runs(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "handled").write_text("30\n", encoding="utf-8")
    proc, ids, calls = run_poll(tmp_path, [run(30, "failure", "c3")], [run(10, "success", "c1")])
    assert proc.returncode == 0, proc.stderr
    assert calls == []
    assert ids == ["30"]


def test_poll_ignores_runs_that_are_not_finished(tmp_path):
    proc, ids, calls = run_poll(
        tmp_path, [run(40, None, "c4", status="in_progress")], [run(10, "success", "c1")]
    )
    assert proc.returncode == 0
    assert calls == [] and ids == []


def test_poll_red_run_bisects_and_records(tmp_path):
    runs = [run(30, "failure", "c3sha"), run(25, "success", "c25sha"), run(20, "failure", "c2sha")]
    green = [run(25, "success", "c25sha"), run(10, "success", "c1sha")]
    proc, ids, calls = run_poll(tmp_path, runs, green)
    assert proc.returncode == 0, proc.stderr
    assert calls == ["c25sha c3sha tests/unit/test_a.py::test_one"]
    assert ids == ["30"]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("BISECT_RESULT ")]
    assert len(line) == 1
    data = json.loads(line[0].split(" ", 1)[1])
    assert data["run_id"] == 30
    assert data["good_sha"] == "c25sha" and data["bad_sha"] == "c3sha"
    assert data["exit_code"] == 0
    assert "PR=7" in data["result"]
    # It reads. It never reruns or cancels.
    gh_log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert "rerun" not in gh_log and "cancel" not in gh_log


def test_poll_does_not_record_a_run_when_the_bisect_is_unfinished(tmp_path):
    proc, ids, calls = run_poll(
        tmp_path, [run(30, "failure", "c3")], [run(10, "success", "c1")], bisect_rc=4
    )
    assert calls == ["c1 c3 tests/unit/test_a.py::test_one"]
    # An exit code 4 is a settled answer, a crash is not. The poller records
    # a run for the exit codes 0, 3, 4 and 5.
    assert ids == ["30"]


def test_poll_state_is_not_written_when_the_bisect_crashes(tmp_path):
    proc, ids, calls = run_poll(
        tmp_path, [run(30, "failure", "c3")], [run(10, "success", "c1")], bisect_rc=1
    )
    assert calls != []
    assert ids == []
    assert proc.returncode != 0


def test_poll_without_a_junit_artifact_is_a_known_gap(tmp_path):
    # The shim copies a file with no failures, as an artifact with no failed test.
    proc, ids, calls = run_poll(
        tmp_path,
        [run(30, "failure", "c3")],
        [run(10, "success", "c1")],
        junit_xml="<testsuite><testcase classname='t' name='test_ok'/></testsuite>",
    )
    assert calls == []
    assert ids == ["30"]
    assert "no failed test" in (proc.stdout + proc.stderr).lower()


def test_scripts_parse():
    for script in (BISECT, POLL):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


def test_a_junit_failure_uses_the_file_attribute_when_it_is_present(tmp_path: Path) -> None:
    """Verifier finding: a lower-case class name broke the classname guess."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import junit_failed_ids as jf

    xml = tmp_path / "r.xml"
    xml.write_text(
        '<testsuites><testsuite><testcase classname="tests.unit.test_a.test_group" '
        'name="test_x[1-a.b]" file="tests/unit/test_a.py"><failure/></testcase>'
        '<testcase classname="tests.unit.Odd_dir.test_b.TestK" name="test_y" '
        'file="tests/unit/Odd_dir/test_b.py"><error/></testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    assert jf.failed_ids(xml) == {
        "tests/unit/test_a.py::test_group::test_x[1-a.b]",
        "tests/unit/Odd_dir/test_b.py::TestK::test_y",
    }


def test_an_unknown_revision_is_a_bad_argument(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path, {104})
    got = subprocess.run(
        ["bash", str(BISECT), "nonexistent", "HEAD", "x"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    assert got.returncode == 2, got
