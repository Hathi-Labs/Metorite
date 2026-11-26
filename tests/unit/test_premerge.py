"""`scripts/premerge.sh` tests the merge with today's main, before `gh pr merge`.

Spec: HANDOFF H-10 (Metorite #639) · `.claude/commands/next-ticket.md` step 7 ·
Metorite task LD-3.

pr-check tests a PR against main as it was when the run started. Without a
merge queue (owner decision, 2026-10-16), a PR can then merge onto a main that
took the same migration number in between. These tests build a throwaway git
repo with a bare `origin`, so they need git and bash, and no network.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "premerge.sh"


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_daily_release.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()
pytestmark = pytest.mark.skipif(_BASH is None or shutil.which("git") is None,
                                reason="needs bash and git")

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                         encoding="utf-8", env=_ENV, check=True)
    return res.stdout.strip()


def _commit(repo: Path, path: str, text: str = "x\n") -> None:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"add {path}")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone of a bare origin. main holds migration 315. Branch `feat` is cut."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    _commit(work, "infra/postgres/315_base.sql")
    _commit(work, "shared.md", "a\nb\nc\nd\ne\n")
    _commit(work, "tests/t.py", "def test_a():\n    pass\n")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-b", "feat")
    return work


def _advance_main(repo: Path, path: str, text: str = "x\n") -> None:
    """Another PR merges: main moves on while `feat` waits."""
    _git(repo, "checkout", "-q", "main")
    _commit(repo, path, text)
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feat")


def _run(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    # The throwaway repo has no scripts/affected_tests.py: stub the test run unless a case sets it.
    env = {"PREMERGE_AFFECTED_CMD": "true", "PREMERGE_RERUN_CMD": "true", **env}
    return subprocess.run([_BASH, SCRIPT.as_posix(), *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", env={**_ENV, **env}, timeout=120)


def _both_sides_change_shared(repo: Path) -> None:
    """Both sides edit shared.md on different lines: a clean merge with overlap."""
    _commit(repo, "shared.md", "A\nb\nc\nd\ne\n")
    _advance_main(repo, "shared.md", "a\nb\nc\nd\nE\n")


_FAILS = 'echo "FAILED tests/t.py::test_a - boom"; exit 1'


def test_a_test_that_fails_only_when_merged_blocks(repo):
    """Green alone, red together: the case the overlap step exists for."""
    _both_sides_change_shared(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD=_FAILS, PREMERGE_RERUN_CMD="true")
    assert res.returncode == 1
    assert "tests/t.py::test_a" in res.stderr and "pass on the branch alone" in res.stderr
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_no_tests_flag_runs_no_affected_tests(repo):
    """--no-tests (or PREMERGE_MERGE_TEST=0) keeps the collision fences only."""
    _both_sides_change_shared(repo)
    marker = repo.parent / "affected-ran"
    cmd = f"touch {marker.as_posix()}; {_FAILS}"
    for res in (_run(repo, "--no-tests", PREMERGE_AFFECTED_CMD=cmd, PREMERGE_RERUN_CMD="true"),
                _run(repo, PREMERGE_MERGE_TEST="0", PREMERGE_AFFECTED_CMD=cmd, PREMERGE_RERUN_CMD="true")):
        assert res.returncode == 0, res.stderr
        assert "PASS" in res.stdout
        assert not marker.exists(), "the affected tests ran with the merge test off"


def test_the_default_runs_the_affected_tests_and_prints_minutes(repo):
    """CIV-MT (task 3277): the merge test is on by default."""
    _both_sides_change_shared(repo)
    marker = repo.parent / "affected-ran"
    res = _run(repo, PREMERGE_AFFECTED_CMD=f"touch {marker.as_posix()}", PREMERGE_RERUN_CMD="true")
    assert res.returncode == 0, res.stderr
    assert marker.exists()
    assert "stage tests:" in res.stdout


def test_files_no_side_shares_still_run_the_tests(repo):
    """The 2026-11-13/14 cases: the two PRs touched different files. The union
    list holds the branch's file AND main's file."""
    _commit(repo, "a_feat.py")
    _advance_main(repo, "b_main.py")
    res = _run(repo, PREMERGE_AFFECTED_CMD="true", PREMERGE_RERUN_CMD="true")
    assert res.returncode == 0, res.stderr
    assert "2 files in the union" in res.stdout


def test_a_timeout_is_unproven_and_not_a_red(repo):
    _both_sides_change_shared(repo)
    res = _run(repo, PREMERGE_TEST_TIMEOUT="1", PREMERGE_AFFECTED_CMD="sleep 30", PREMERGE_RERUN_CMD="true")
    assert res.returncode == 0, res.stderr
    assert "UNPROVEN" in res.stdout


def test_a_test_that_is_red_on_main_alone_does_not_block(repo):
    """Main is red (2026-11-14, four times). The PR did not cause it. The re-run
    on main alone (a detached head) fails too, so the merge is not blamed."""
    _both_sides_change_shared(repo)
    rerun = "f() { git symbolic-ref -q HEAD >/dev/null || { echo 'FAILED tests/t.py::test_a - boom'; return 1; }; }; f"
    res = _run(repo, PREMERGE_AFFECTED_CMD=_FAILS, PREMERGE_RERUN_CMD=rerun)
    assert res.returncode == 0, res.stderr
    assert "did not cause them" in res.stdout


def test_a_test_file_that_only_main_has_blocks_when_it_passes_on_main_alone(repo):
    """2026-11-14: main added a fence (new file), the PR added a source. The fence
    fails on the merge, passes on main alone, and has no file on the branch.
    No file is no evidence, so the merge is blamed."""
    _both_sides_change_shared(repo)
    cmd = 'echo "FAILED tests/new_on_main.py::test_x - boom"; exit 1'
    # main (a detached head) has the file. Make the re-run pass there.
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "tests/new_on_main.py", "def test_x():\n    pass\n")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feat")
    res = _run(repo, PREMERGE_AFFECTED_CMD=cmd, PREMERGE_RERUN_CMD="true")
    assert res.returncode == 1, res.stdout
    assert "tests/new_on_main.py::test_x" in res.stderr


def test_a_test_file_that_only_main_has_and_is_red_on_main_does_not_block(repo):
    _both_sides_change_shared(repo)
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "tests/new_on_main.py", "def test_x():\n    pass\n")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feat")
    cmd = 'echo "FAILED tests/new_on_main.py::test_x - boom"; exit 1'
    rerun = "f() { git symbolic-ref -q HEAD >/dev/null || { echo 'FAILED tests/new_on_main.py::test_x - boom'; return 1; }; }; f"
    res = _run(repo, PREMERGE_AFFECTED_CMD=cmd, PREMERGE_RERUN_CMD=rerun)
    assert res.returncode == 0, res.stderr
    assert "did not cause them" in res.stdout


def test_a_rename_keeps_the_old_path_in_the_lists(repo):
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "mod_old.py", "x = 1\nprint(1)\nprint(2)\nprint(3)\n")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feat")
    _git(repo, "merge", "-q", "--ff-only", "main")
    _git(repo, "mv", "mod_old.py", "mod_new.py")
    _git(repo, "commit", "-q", "-m", "rename")
    _advance_main(repo, "other.py")
    res = _run(repo, PREMERGE_AFFECTED_CMD="true")
    assert res.returncode == 0, res.stderr
    body = SCRIPT.read_text(encoding="utf-8")
    assert body.count("--no-renames --name-only") >= 2


def _stub_picker(repo: Path, body: str) -> None:
    """A scripts/premerge_select.py that writes what a Windows Python would: CRLF."""
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "premerge_select.py").write_text(body, encoding="utf-8")
    _git(repo, "add", "scripts/premerge_select.py")
    _git(repo, "commit", "-q", "-m", "picker stub")


_PICKER_CRLF = (
    "import sys\n"
    "a = sys.argv\n"
    "out = lambda flag: a[a.index(flag) + 1]\n"
    "open(out('--first-out'), 'wb').write(b'1\\r\\n')\n"
    "open(out('--note-out'), 'wb').write(b'\\r\\n')\n"
    "sys.stdout.buffer.write(b'tests/a.py\\r\\ntests/b.py\\r\\n')\n"
)


def _fake_pytest(repo: Path) -> Path:
    path = repo.parent / "fake_pytest.sh"
    path.write_text('printf "%s|" "$@" >> "$(dirname "$0")/pytest-args"\n', encoding="utf-8")
    return path


def test_a_cr_in_the_picker_output_never_reaches_pytest(repo):
    """Review of 2026-11-15, P1: a Windows Python wrote CRLF, Git Bash kept the CR on
    each path but the last, and pytest stopped with 'file not found'."""
    _stub_picker(repo, _PICKER_CRLF)
    _advance_main(repo, "other.py")
    fake = _fake_pytest(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD="", PREMERGE_PY="python3",
               PREMERGE_PYTEST_CMD=f"bash {fake.as_posix()}")
    assert res.returncode == 0, res.stderr
    args = (repo.parent / "pytest-args").read_text(encoding="utf-8")
    assert "\r" not in args and "tests/a.py|" in args and "tests/b.py|" in args


def test_a_picker_that_does_not_finish_is_unproven_not_a_hang(repo):
    """CIV-MT-3: on the merger PC the default ran past 1000 s. The picker has its own cap."""
    _stub_picker(repo, "import time\ntime.sleep(60)\n")
    _advance_main(repo, "other.py")
    fake = _fake_pytest(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD="", PREMERGE_PY="python3", PREMERGE_PICKER_TIMEOUT="1",
               PREMERGE_PYTEST_CMD=f"bash {fake.as_posix()}")
    assert res.returncode == 0, res.stderr
    assert "UNPROVEN. The picker had no verdict within 1s" in res.stdout
    assert not (repo.parent / "pytest-args").exists(), "no test may run after a picker timeout"


def test_a_short_budget_still_runs_the_picked_tests(repo):
    """Review of PR 2407 at e349723573, P1: the tests always get at least 60 s, as on main.
    At 250 s, what is left after the picker minus two re-runs is under 60 s."""
    _stub_picker(repo, _PICKER_CRLF)
    _advance_main(repo, "other.py")
    fake = _fake_pytest(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD="", PREMERGE_PY="python3", PREMERGE_TOTAL_BUDGET="250",
               PREMERGE_PYTEST_CMD=f"bash {fake.as_posix()}")
    assert res.returncode == 0, res.stderr
    args = (repo.parent / "pytest-args").read_text(encoding="utf-8")
    assert "tests/a.py|" in args and "timeout 60s" in res.stdout


def test_a_short_budget_still_runs_the_affected_tests(repo):
    """The same floor for a caller that sets PREMERGE_AFFECTED_CMD: the tests run, a red blocks."""
    _both_sides_change_shared(repo)
    marker = repo.parent / "affected-ran"
    res = _run(repo, PREMERGE_TOTAL_BUDGET="250", PREMERGE_AFFECTED_CMD=f"touch {marker.as_posix()}; {_FAILS}",
               PREMERGE_RERUN_CMD="true")
    assert marker.exists(), res.stdout
    assert res.returncode == 1, res.stdout
    assert "tests/t.py::test_a" in res.stderr


def test_a_picker_that_needs_the_kill_is_unproven_too(repo):
    """timeout -k ends with 137 when the child ignores TERM. That is a timeout, not a failure."""
    _stub_picker(repo, "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n")
    _advance_main(repo, "other.py")
    res = _run(repo, PREMERGE_AFFECTED_CMD="", PREMERGE_PY="python3", PREMERGE_PICKER_TIMEOUT="1",
               PREMERGE_PYTEST_CMD="false")
    assert res.returncode == 0, res.stderr
    assert "UNPROVEN. The picker had no verdict" in res.stdout


def test_a_short_budget_skips_the_type_check_and_still_runs_the_tests(repo):
    """Finding 1 and 2 of the review of PR 2407: the picker and the baseline run took their full cap.
    At 150 s the type check cannot keep PREMERGE_RESERVE, so it does not start. The picker and
    the tests still run (the tests get at least 60 s)."""
    _frontend_pair(repo)
    _stub_picker(repo, _PICKER_CRLF)
    marker = repo.parent / "tsc-ran"
    fake = _fake_pytest(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD="", PREMERGE_PY="python3", PREMERGE_TOTAL_BUDGET="150",
               PREMERGE_TSC_CMD=f"touch {marker.as_posix()}", PREMERGE_PYTEST_CMD=f"bash {fake.as_posix()}")
    assert res.returncode == 0, res.stderr
    assert not marker.exists()
    assert "UNPROVEN. The type check did not run" in res.stdout
    assert (repo.parent / "pytest-args").exists()


def test_each_stage_says_how_long_it_took(repo):
    _stub_picker(repo, _PICKER_CRLF)
    _advance_main(repo, "other.py")
    fake = _fake_pytest(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD="", PREMERGE_PY="python3", PREMERGE_PYTEST_CMD=f"bash {fake.as_posix()}")
    assert res.returncode == 0, res.stderr
    for stage in ("collision fences", "picker", "tests"):
        assert f"stage {stage}:" in res.stdout, stage


def test_an_empty_selection_runs_no_pytest(repo):
    """Review of 2026-11-15, P2: an empty list made pytest run the whole tree."""
    _stub_picker(repo, "import sys\na = sys.argv\nopen(a[a.index('--first-out') + 1], 'w').write('0\\n')\n")
    _advance_main(repo, "other.py")
    fake = _fake_pytest(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD="", PREMERGE_PY="python3",
               PREMERGE_PYTEST_CMD=f"bash {fake.as_posix()}")
    assert res.returncode == 0, res.stderr
    assert not (repo.parent / "pytest-args").exists()


def test_any_value_that_is_not_an_off_word_leaves_the_tests_on(repo):
    _both_sides_change_shared(repo)
    marker = repo.parent / "affected-ran"
    _run(repo, PREMERGE_MERGE_TEST="yes", PREMERGE_AFFECTED_CMD=f"touch {marker.as_posix()}")
    assert marker.exists()
    marker.unlink()
    res = _run(repo, PREMERGE_MERGE_TEST="false", PREMERGE_AFFECTED_CMD=f"touch {marker.as_posix()}")
    assert res.returncode == 0 and not marker.exists() and "did not run" in res.stdout


def test_a_function_that_one_side_lacks_gives_no_evidence_there(repo):
    """Review of 2026-11-15, P2: pytest exits 4 on a missing id and runs nothing, so a red
    that main caused was blamed on the PR. The id of a function that is not in the file of
    a side is neither run nor counted there. The fake pytest returns 4 on main (a detached head) for the id main lacks."""
    _both_sides_change_shared(repo)
    # The branch adds test_g to tests/t.py. Main does not have it, and main is red on test_a.
    _commit(repo, "tests/t.py", "def test_a():\n    pass\n\ndef test_g():\n    pass\n")
    cmd = 'echo "FAILED tests/t.py::test_a - boom"; echo "FAILED tests/t.py::test_g - boom"; exit 1'
    rerun = (
        'f() { for a in "$@"; do case "$a" in *test_g) git symbolic-ref -q HEAD >/dev/null || { echo "ERROR: not found: $a"; return 4; };; esac; done;'
        " git symbolic-ref -q HEAD >/dev/null || { echo 'FAILED tests/t.py::test_a - boom'; return 1; }; }; f"
    )
    res = _run(repo, PREMERGE_AFFECTED_CMD=cmd, PREMERGE_RERUN_CMD=rerun)
    # test_a is red on main alone: not the merge's. test_g is not on main (no evidence there) and
    # passes on the branch alone here: the merge is to blame, and only for test_g.
    assert res.returncode == 1, res.stdout + res.stderr
    assert "tests/t.py::test_g" in res.stderr and "tests/t.py::test_a" not in res.stderr


def test_a_collection_error_red_on_both_sides_does_not_block(repo):
    """Review of 2026-11-15, finding 1: an id with no `::` is a whole file. The file alone decides."""
    _both_sides_change_shared(repo)
    cmd = 'echo "ERROR tests/t.py - ImportError"; exit 1'
    rerun = 'echo "ERROR tests/t.py - ImportError"; exit 1'
    res = _run(repo, PREMERGE_AFFECTED_CMD=cmd, PREMERGE_RERUN_CMD=rerun)
    assert res.returncode == 0, res.stderr
    assert "did not cause them" in res.stdout


def test_a_cr_in_the_pytest_log_does_not_hide_a_red_on_main(repo):
    """A Windows pytest ends each summary line with CRLF and an id with no ' - message'
    keeps the CR through awk, so the re-run cannot match it. failed_ids strips CR."""
    _both_sides_change_shared(repo)
    cmd = "printf 'FAILED tests/t.py::test_a\\r\\n'; exit 1"
    rerun = "f() { git symbolic-ref -q HEAD >/dev/null || { printf 'FAILED tests/t.py::test_a\\r\\n'; return 1; }; }; f"
    res = _run(repo, PREMERGE_AFFECTED_CMD=cmd, PREMERGE_RERUN_CMD=rerun)
    assert res.returncode == 0, res.stderr
    assert "did not cause them" in res.stdout


def test_an_empty_selection_says_that_nothing_is_proven(repo):
    _stub_picker(repo, "import sys\na = sys.argv\nopen(a[a.index('--first-out') + 1], 'w').write('0\\n')\n")
    _advance_main(repo, "other.py")
    res = _run(repo, PREMERGE_AFFECTED_CMD="", PREMERGE_PY="python3",
               PREMERGE_PYTEST_CMD="false")
    assert res.returncode == 0, res.stderr
    assert "UNPROVEN. The selector picked no test" in res.stdout


def _frontend_pair(repo: Path) -> None:
    """The branch adds one front-end file, main adds another: two PRs that each pass alone."""
    (repo / "workbench/control_plane/node_modules").mkdir(parents=True, exist_ok=True)
    _commit(repo, "workbench/control_plane/src/a.ts", "export const a = 1\n")
    _advance_main(repo, "workbench/control_plane/src/b.ts", "export const b = 2\n")


def test_tsc_runs_on_the_merge_result_for_a_front_end_diff(repo):
    """CIV-MT-2 (task 3369): 2354 and 2295 each passed tsc alone, and main's tsc went red."""
    _frontend_pair(repo)
    marker = repo.parent / "tsc-ran"
    res = _run(repo, PREMERGE_TSC_CMD=f"touch {marker.as_posix()}")
    assert res.returncode == 0, res.stderr
    assert marker.exists() and "type check passed" in res.stdout


def test_tsc_is_skipped_for_a_python_only_diff(repo):
    _commit(repo, "scripts/x.py")
    _advance_main(repo, "scripts/y.py")
    marker = repo.parent / "tsc-ran"
    res = _run(repo, PREMERGE_TSC_CMD=f"touch {marker.as_posix()}")
    assert res.returncode == 0, res.stderr
    assert not marker.exists()


def test_a_type_error_that_only_the_merge_has_blocks_and_is_named(repo):
    _frontend_pair(repo)
    both = ('if [ -f src/a.ts ] && [ -f src/b.ts ]; then '
            'echo "src/a.ts(3,5): error TS2739: Type is missing the property layout"; exit 1; fi')
    res = _run(repo, PREMERGE_TSC_CMD=both)
    assert res.returncode == 1
    assert "error TS2739" in res.stderr and "src/a.ts" in res.stderr
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "feat"


def test_a_type_error_that_main_has_alone_does_not_block(repo):
    """Main's tsc was red for 50 minutes. That must not block every PR behind it."""
    _frontend_pair(repo)
    on_main = 'if [ -f src/b.ts ]; then echo "src/b.ts(1,1): error TS2322: boom"; exit 1; fi'
    res = _run(repo, PREMERGE_TSC_CMD=on_main)
    assert res.returncode == 0, res.stderr
    assert "did not cause them" in res.stdout


def test_a_baseline_that_gives_no_verdict_does_not_block(repo):
    """Review of PR 2394, F1: a baseline run on main that times out or crashes cannot
    clear or blame the merge. It is UNPROVEN, not a block."""
    _frontend_pair(repo)
    cmd = ('if [ -f src/a.ts ] && [ -f src/b.ts ]; then echo "src/a.ts(1,1): error TS2322: boom"; exit 1; fi; '
           'if [ -f src/b.ts ]; then exit 134; fi')
    res = _run(repo, PREMERGE_TSC_CMD=cmd)
    assert res.returncode == 0, res.stderr
    assert "UNPROVEN. The type check of" in res.stdout


@pytest.mark.timeout(300)
def test_a_long_merged_type_check_leaves_the_baseline_enough_time(repo):
    """Review of PR 2407 at e349723573, P1: the merged tsc spent the budget down to
    PREMERGE_RESERVE, the baseline got the same cap minus RESERVE, said UNPROVEN, and a type
    error that only the merge has passed with exit 0. A FAIL needs no later stage, so the
    baseline keeps only 20 s. Budget 270 and reserve 200 give the merged run about 70 s. It
    takes 52 s, so the old baseline cap is under 20 s and the new one is about 190 s."""
    _frontend_pair(repo)
    cmd = ('if [ -f src/a.ts ] && [ -f src/b.ts ]; then sleep 52; '
           'echo "src/a.ts(3,5): error TS2739: Type is missing the property layout"; exit 1; fi')
    res = _run(repo, PREMERGE_TOTAL_BUDGET="270", PREMERGE_RESERVE="200", PREMERGE_TSC_CMD=cmd)
    assert "type check on the merge result" in res.stdout, res.stdout
    assert res.returncode == 1, res.stdout
    assert "error TS2739" in res.stderr


def test_a_lockfile_change_runs_npm_ci_even_with_node_modules(repo):
    _commit(repo, "workbench/control_plane/package-lock.json", "{}\n")
    _advance_main(repo, "workbench/control_plane/src/b.ts", "export const b = 2\n")
    (repo / "workbench/control_plane/node_modules").mkdir(parents=True, exist_ok=True)
    res = _run(repo, PREMERGE_TSC_CMD="true", PREMERGE_NPM_TIMEOUT="5")
    assert "a lockfile changed: running npm ci" in res.stdout


def test_a_front_end_config_file_triggers_the_type_check(repo):
    (repo / "workbench/control_plane/node_modules").mkdir(parents=True, exist_ok=True)
    _commit(repo, "workbench/control_plane/vitest.config.ts", "export default {}\n")
    _advance_main(repo, "scripts/y.py")
    marker = repo.parent / "tsc-ran"
    _run(repo, PREMERGE_TSC_CMD=f"touch {marker.as_posix()}")
    assert marker.exists()


def test_tsc_off_switch(repo):
    _frontend_pair(repo)
    marker = repo.parent / "tsc-ran"
    res = _run(repo, PREMERGE_TSC="0", PREMERGE_TSC_CMD=f"touch {marker.as_posix()}")
    assert res.returncode == 0, res.stderr
    assert not marker.exists()


def test_an_unknown_flag_is_refused(repo):
    res = _run(repo, "--bogus")
    assert res.returncode == 1 and "--bogus" in res.stderr


def test_a_test_that_fails_alone_too_does_not_block(repo):
    """Six tests fail on the Windows PC and pass in CI. They must not block a
    merge they did not come from (LD-3b, 2026-10-30)."""
    _both_sides_change_shared(repo)
    head = _git(repo, "rev-parse", "HEAD")
    res = _run(repo, "--with-tests", PREMERGE_AFFECTED_CMD=_FAILS, PREMERGE_RERUN_CMD=_FAILS)
    assert res.returncode == 0, res.stderr
    assert "did not cause them" in res.stdout and "PASS" in res.stdout
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "feat"


def test_a_run_that_names_no_test_blocks(repo):
    _both_sides_change_shared(repo)
    res = _run(repo, "--with-tests", PREMERGE_AFFECTED_CMD="echo boom; exit 2", PREMERGE_RERUN_CMD="true")
    assert res.returncode == 1
    assert "no test is named" in res.stderr


def test_a_branch_that_contains_main_passes(repo):
    _commit(repo, "infra/postgres/316_feat.sql")
    res = _run(repo)
    assert res.returncode == 0, res.stderr
    assert "Merge now" in res.stdout


def test_a_migration_number_main_took_meanwhile_fails_by_name(repo):
    """The R1 collision: both PRs took 316, and the other merged first."""
    _commit(repo, "infra/postgres/316_feat.sql")
    _advance_main(repo, "infra/postgres/316_other.sql")
    res = _run(repo)
    assert res.returncode == 1
    assert "316_feat.sql" in res.stderr and "Take 317" in res.stderr


def test_a_migration_below_mains_highest_fails(repo):
    """Main reached 317. A late 316 would apply after 317 in production."""
    _commit(repo, "infra/postgres/316_feat.sql")
    _advance_main(repo, "infra/postgres/317_other.sql")
    res = _run(repo)
    assert res.returncode == 1
    assert "Take 318" in res.stderr


def test_a_conflict_fails_and_leaves_the_worktree_as_it_was(repo):
    _commit(repo, "notes.md", "feat\n")
    _advance_main(repo, "notes.md")
    head = _git(repo, "rev-parse", "HEAD")
    res = _run(repo)
    assert res.returncode == 1
    assert "conflicts in: notes.md" in res.stderr
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "feat"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_an_untracked_file_in_the_way_is_named_and_kept(repo):
    """Main adds a file that is untracked here. git refuses the merge. The
    message must say why, and the untracked file must survive."""
    _commit(repo, "feat.md")
    _advance_main(repo, "zone.md")
    (repo / "zone.md").write_text("mine\n", encoding="utf-8")
    res = _run(repo)
    assert res.returncode == 1
    assert "zone.md" in res.stderr and "untracked" in res.stderr
    assert (repo / "zone.md").read_text(encoding="utf-8") == "mine\n"


def test_a_clean_behind_branch_passes_and_the_trial_merge_is_undone(repo):
    _commit(repo, "infra/postgres/317_feat.sql")
    _advance_main(repo, "infra/postgres/316_other.sql")
    head = _git(repo, "rev-parse", "HEAD")
    res = _run(repo)
    assert res.returncode == 0, res.stderr
    assert "PASS" in res.stdout
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "feat"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_uncommitted_work_is_refused(repo):
    (repo / "wip.md").write_text("wip\n", encoding="utf-8")
    _git(repo, "add", "wip.md")
    res = _run(repo)
    assert res.returncode == 1
    assert "uncommitted" in res.stderr


def test_the_collision_fences_are_named():
    body = SCRIPT.read_text(encoding="utf-8")
    for fence in ("test_migration_prefixes.py", "test_handoff_queue.py", "test_decision_ids.py"):
        assert fence in body
        assert (ROOT / "tests" / "unit" / fence).is_file(), fence
