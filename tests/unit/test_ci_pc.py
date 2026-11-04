"""Local CI on this PC reads pr-check.yml, so the two cannot drift (2026-10-19).

R7 — the rule names its fence. `scripts/ci_pc/steps.py` renders a pr-check
job as bash, and `scripts/ci_pc/run_suite.sh` runs those jobs in the preset
`metorite-ci` distro. Each failure here is silent in use:

* a job that no longer renders makes local CI fail for a reason that is not
  the branch, and people stop trusting it;
* an expression rendered as an empty string turns a check into a no-op;
* a skip rule that matches a TEST step makes local CI green while it tests
  nothing;
* a job that run_suite.sh names and pr-check.yml lost runs nothing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts" / "ci_pc"))
import steps  # noqa: E402

_CTX = {
    "github.workspace": "/w",
    "github.event_name": "pull_request",
    "github.token": "",
    "github.event.pull_request.number": "",
    "runner.os": "Linux",
    "matrix.shard": "1",
    "needs.test.result": "success",
    # CIV-3: run_suite.sh gives the `changes` job a FULL result.
    "needs.changes.result": "success",
    "needs.changes.outputs.full": "true",
    "needs.changes.outputs.files": "",
    "steps.pick.outputs.mode": "full",
    "steps.pick.outputs.paths": "",
    "github.event.pull_request.base.sha": "abc",
}

_RUN_SUITE = (_ROOT / "scripts/ci_pc/run_suite.sh").read_text(encoding="utf-8")


def _named_jobs() -> set[str]:
    # `job <label> <pod> <pr-check job> ...` in run_suite.sh
    return set(re.findall(r"^\s*job \S+ \S+ ([a-z][\w-]*)", _RUN_SUITE, re.M))


def test_run_suite_names_only_jobs_that_exist():
    jobs = steps.load_jobs()
    named = _named_jobs()
    assert named, "the regex found no job lines; it is wrong, not the file"
    assert named <= set(jobs), named - set(jobs)


def test_every_pr_check_job_is_run_locally_or_named_as_left_out():
    # `changes` decides what GitHub skips. Locally every job runs.
    # `e2e` runs as one process in run_suite.sh, not through steps.py.
    left_out = {"changes", "e2e"}
    assert set(steps.load_jobs()) - left_out == _named_jobs()


@pytest.mark.parametrize("job", sorted(steps.load_jobs()))
def test_every_job_renders(job):
    if job in {"changes"}:
        pytest.skip("not run locally")
    out = steps.render(job, _CTX, {})
    assert "${{" not in out
    assert out.rstrip().endswith('echo "== job ok"')


def test_an_unknown_expression_is_an_error_not_a_blank():
    with pytest.raises(SystemExit, match="unknown expression"):
        steps.render("test", {k: v for k, v in _CTX.items() if k != "matrix.shard"}, {})


def test_the_skip_list_never_drops_a_test_or_a_check():
    for name, job in steps.load_jobs().items():
        for step in job.get("steps", []):
            label = step.get("name", "")
            if label.startswith(steps.SKIP_STEPS):
                assert not re.search(r"pytest|vitest|playwright test|tsc|ruff|xenon|guard|fence|Rehearse|Replay",
                                     step.get("run", "")), f"{name}: {label!r} is skipped but checks something"


def test_an_override_beats_the_job_env():
    out = steps.render("test", _CTX, {"TENANT_LADDER_DATABASE_URL": "postgresql://x@127.0.0.1:6701/t"})
    assert "6701" in out
    assert "5434" not in out.split("== Run unit tests")[1]


def test_the_local_shard_count_rewrites_the_split_that_pr_check_names():
    # run_suite.sh splits finer than pr-check on a PC with more cores. It
    # swaps the literal `--splits 6`, so that text must stay in pr-check.
    out = steps.render("test", _CTX, {}, {"--splits 6": "--splits 12"})
    assert "--splits 12 --group 1" in out
    assert '"--replace=--splits 6=>--splits $SHARDS"' in _RUN_SUITE


def test_a_replacement_that_matches_nothing_is_an_error():
    # A silent miss would run 12 groups of a 6-way split: half the suite twice.
    with pytest.raises(SystemExit, match="not found"):
        steps.render("test", _CTX, {}, {"--splits 9": "--splits 12"})


def test_no_job_can_reach_the_dev_stack_on_this_pc():
    # Every WSL2 distro shares one network, and Docker Desktop publishes the
    # dev stack's Postgres on *:5432 there. The first local run reached it.
    # So no service publishes a port, and every job runs in a container.
    assert not re.search(r"docker run[^\n]*\s-p\s", _RUN_SUITE)
    assert '"$IMAGE" bash "$RUN/$label.sh"' in _RUN_SUITE
    assert "--network \"container:$pod\"" in _RUN_SUITE


def test_no_container_can_run_the_vm_out_of_memory():
    # 2026-10-20: 12 uncapped shards ran the shared WSL2 VM out of memory, and
    # WSL, git and Docker Desktop hung for every session on the PC.
    # Join `\`-continued lines first, so a cap on the next line counts.
    joined = re.sub(r"\\\r?\n", " ", _RUN_SUITE)
    runs = [line for line in joined.splitlines() if "docker run " in line]
    assert runs, "the regex found no docker run; it is wrong, not the file"
    for cmd in runs:
        assert "--memory " in cmd, f"an uncapped container: {cmd[:80]}"
    assert "--tmpfs /var/lib/postgresql/data:size=" in _RUN_SUITE
    # The shard count follows free memory, never the core count alone.
    assert "MemAvailable" in _RUN_SUITE


def test_a_bare_ci_local_call_passes_no_empty_argument():
    # `printf ' %q'` with no arguments prints `''`, and run_suite.sh refused
    # it: every `bash scripts/ci_local.sh` with no options failed at once.
    text = (_ROOT / "scripts/ci_local.sh").read_text(encoding="utf-8")
    assert '[ ${#pass[@]} -gt 0 ] && args="$(printf' in text


def test_the_browser_suite_is_opt_in_and_runs_in_three_fresh_parts():
    # 2026-10-20: one `next dev` for the whole suite was OOM-killed at its
    # cap, and 409 of 482 tests "failed" on runs GitHub passed.
    assert re.search(r"^E2E=no\b", _RUN_SUITE, re.M)
    assert "--shard=$p/3" in _RUN_SUITE
    # It starts only after the unit phase has finished.
    e2e = _RUN_SUITE.index('if [ "$E2E" = yes ]; then')
    guard = _RUN_SUITE.index('job unit-tests "" unit-tests')
    assert guard < e2e


def test_the_summary_reads_the_verdict_not_a_test_name():
    # A parameter id like "pg_restore FAILED in the verify container" is a
    # PASSED test. The verdict sits at the end of the line.
    pattern = re.search(r'grep -hE "( \(FAILED\|ERROR\)[^"]*)"', _RUN_SUITE).group(1)
    rx = re.compile(pattern.replace("\\|", "|"))
    assert rx.search("tests/unit/t.py::test_x FAILED [ 40%]")
    assert not rx.search("tests/unit/t.py::test_y[pg_restore FAILED in the verify] PASSED [  6%]")


def test_run_suite_refuses_a_starved_vm():
    # Run by hand (no slot grant), a VM with under 8 GB free is refused.
    assert re.search(r'\[ "\$avail_gb" -lt 8 \]; then', _RUN_SUITE)


_DOCTOR = (_ROOT / "scripts/ci_pc/doctor.sh").read_text(encoding="utf-8")


def test_every_doctor_probe_has_a_timeout():
    # A doctor that hangs on the thing it checks is no use on the day it
    # matters. Every wsl.exe and docker call goes through `timeout`.
    for line in _DOCTOR.splitlines():
        code = line.split("#", 1)[0]
        for tool in ("wsl.exe ", "docker info", "docker ps", "docker start"):
            if tool in code:
                assert "timeout " in code, f"no timeout: {line.strip()}"


def test_the_doctor_repairs_in_the_order_that_worked():
    # 2026-10-20: `wsl --shutdown` did not stop the stuck VM. The WSL service
    # restart (UAC) did. Docker Desktop then needed its OWN distro
    # terminated, and the containers without a restart policy needed a start.
    body = _DOCTOR.split("restart_wsl() {")[1]
    assert body.index("wsl.exe --shutdown") < body.index("Start-Service WSLService")
    assert "wsl.exe --terminate docker-desktop" in _DOCTOR
    assert "restore_containers" in _DOCTOR.split('if restart_docker_desktop; then')[1].split("\n")[0]
    # It changes nothing without --fix.
    assert 'FIX=false; [ "${1:-}" = "--fix" ] && FIX=true' in _DOCTOR


def test_the_distro_is_locked_down():
    prov = (_ROOT / "scripts/ci_pc/provision.sh").read_text(encoding="utf-8")
    conf = prov.split("cat > /etc/wsl.conf <<'CONF'")[1].split("CONF")[0]
    assert re.search(r"\[automount\]\s*\nenabled = false", conf)
    assert re.search(r"\[interop\]\s*\nenabled = false", conf)


def test_the_local_run_is_full_for_the_selection_jobs() -> None:
    """CIV-3: pr-check's `test` and `unit-tests` read the `changes` job.
    The local CI tests the whole commit, so run_suite.sh passes a FULL
    result to both jobs, and steps.py renders them with it."""
    text = (_ROOT / "scripts" / "ci_pc" / "run_suite.sh").read_text(encoding="utf-8")
    assert "--ctx needs.changes.outputs.full=true" in text
    assert re.search(r'job "shard-\$i" .* test .*"\$\{FULL_RUN\[@\]\}"', text)
    assert re.search(r'job unit-tests .*"\$\{FULL_RUN\[@\]\}"', text)
    assert "--ctx steps.pick.outputs.mode=full" in text
    for job in ("test", "unit-tests"):
        out = steps.render(job, _CTX, {})
        # The pick step appends to $GITHUB_OUTPUT. Unset, `tee -a ""` fails.
        assert 'export GITHUB_OUTPUT="${GITHUB_OUTPUT:-/dev/null}"' in out


# ── CIV-6: several runs at once on one lane (task 1634) ─────────────────────
_CI_LOCAL = (_ROOT / "scripts/ci_local.sh").read_text(encoding="utf-8")
_REMOTE = (_ROOT / "scripts/ci_pc/remote_run.sh").read_text(encoding="utf-8")


def test_each_run_gets_its_own_worktree():
    # Two runs in one working tree overwrite each other's checkout, `.next`
    # and `junit/`. The run's worktree is named by its pid.
    assert 'git -C "$CI_BASE" worktree add -q --detach "$wt" "$sha"' in _REMOTE
    assert 'wt="$CI_WT_DIR/${sha:0:12}-$$-$(date +%s)"' in _REMOTE
    # No run checks out into the shared clone any more.
    assert "checkout -q -f" not in _CI_LOCAL
    assert "git clean -fdxq" not in _CI_LOCAL


def test_ci_local_no_longer_holds_the_lane_exclusively():
    assert "flock -w 3600 9" not in _CI_LOCAL
    # A new run holds the old lane lock SHARED, so an older ci_local.sh (which
    # holds it exclusive and works in ~/metorite) cannot overlap with it.
    assert 'flock -s -w 3600 9' in _REMOTE
    assert "ci_slot_acquire" in _REMOTE


def test_ci_local_ships_the_slot_code_with_the_run():
    assert "scripts/ci_pc/slots.sh scripts/ci_pc/remote_run.sh" in _CI_LOCAL
    assert "ci_remote_run $sha$args" in _CI_LOCAL


def test_the_shared_clone_has_one_git_writer_at_a_time():
    assert _CI_LOCAL.count("$GITLOCK") >= 2
    assert "flock -w 300 5" in _REMOTE


def test_the_default_run_is_the_civ2_selection_and_full_is_opt_in():
    assert "affected_tests.py --gate --base" in _REMOTE and "origin/main" in _REMOTE
    assert "--full) full=yes" in _REMOTE
    assert "--select-file" in _RUN_SUITE
    # A selector that fails, or says full, runs every test (it fails open).
    assert 'sel_args=(--select-file' in _REMOTE
    assert "the selector says full" in _REMOTE


def test_a_selected_run_swaps_the_folder_for_the_files_and_skips_the_whole_suite_guard():
    out = steps.render("test", _CTX, {}, {"tests/unit/ -v": "tests/unit/test_a.py -v"})
    assert "tests/unit/test_a.py -v -rs" in out
    # The guard wants every must-run file in the reports. A selected run has
    # not run them, so the guard job is inside the "not selected" branch.
    guard = _RUN_SUITE.index('job unit-tests "" unit-tests')
    assert _RUN_SUITE.rindex('if [ -z "$SELECT_FILE" ]; then', 0, guard) > _RUN_SUITE.index("shards_ok=success")


def test_two_runs_started_in_one_second_do_not_share_a_name():
    # Container names and the log folder carry the pid.
    assert re.search(r'^TAG="\$\(date \+%H%M%S\)-\$\$"', _RUN_SUITE, re.M)
    assert re.search(r'^RUN="\$HOME/runs/.*-\$SHA-\$\$"', _RUN_SUITE, re.M)


def test_a_run_under_slots_does_not_use_the_one_run_memory_refusal():
    # Another run lowers MemAvailable on purpose; slots.sh has reserved room.
    assert 'if [ -z "${CI_SLOT_GRANTED:-}" ] && [ "$avail_gb" -lt 8 ]; then' in _RUN_SUITE


def test_node_modules_come_from_a_store_keyed_by_the_lockfile():
    assert 'store="$CI_DEPS_DIR/$app-${lock:0:16}"' in _RUN_SUITE
    # A hard-linked tool cache must not be written through the link.
    assert 'rm -rf "$store/node_modules/.cache"' in _RUN_SUITE


def test_the_lane_choice_asks_for_room_not_for_an_idle_lane():
    assert "has_room" in _CI_LOCAL
    assert "lock_free" not in _CI_LOCAL
