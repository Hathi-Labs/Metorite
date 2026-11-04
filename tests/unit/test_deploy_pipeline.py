"""The deploy pipeline's safety properties, pinned.

Added 2026-08-26 after two merges three minutes apart put two deploys on the
same box concurrently and the run overran its usual 5.5 minutes by an order of
magnitude.

⚠️ **The properties here are each one line of YAML, and each one is easy to
remove for a plausible-sounding reason.** ``cancel-in-progress: false`` in
particular reads like a mistake — it is the opposite of what that field is
normally set to, and the next person tidying this workflow will want to flip it.
So every test states the failure it prevents, because the argument for the
setting is not visible from the setting.

These are STRUCTURAL. Whether the pipeline works is answered by running it; what
cannot be answered that way is whether a future edit quietly removed a guard,
since a pipeline with no guards passes just as green as one with them — right up
until the deploy it was supposed to catch.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
)


@pytest.fixture(scope="module")
def raw() -> str:
    return io.open(WORKFLOW, encoding="utf-8").read()


@pytest.fixture(scope="module")
def parsed(raw: str) -> dict:
    return yaml.safe_load(raw)


def test_the_workflow_exists_and_parses(parsed: dict) -> None:
    # Guards the guard: an unparseable workflow makes every test below vacuous,
    # and GitHub reports a broken workflow as "no runs" rather than as a failure.
    assert parsed["name"] == "deploy"
    assert "deploy" in parsed["jobs"]


def test_the_file_holds_no_control_bytes(raw: str) -> None:
    """Measured, not theoretical — this file shipped a 0x01 once.

    Building the SHA check with a ``sed`` backreference put a literal
    backslash-one through three quoting layers (Python source -> shell heredoc
    -> YAML) and wrote ``chr(1)`` into the workflow. YAML then refused the whole
    file, which GitHub surfaces as no runs queued rather than as an error — the
    same "nothing happened looks like nothing to do" failure the `paths-ignore`
    comment in this workflow already records once.
    """
    body = io.open(WORKFLOW, "rb").read()
    bad = [i for i, c in enumerate(body) if c < 9 or 13 < c < 32]
    assert not bad, (
        f"control byte(s) at offset(s) {bad[:5]} — the workflow will not parse, "
        "and GitHub renders an unparseable workflow as silence"
    )


# ── One deploy at a time ────────────────────────────────────────────────────

def test_deploys_are_serialised(parsed: dict) -> None:
    concurrency = parsed["jobs"]["deploy"].get("concurrency")
    assert concurrency, (
        "the deploy JOB has no `concurrency:` group. Two merges minutes apart "
        "then run two deploys against ONE box simultaneously — both executing "
        "vps_apply.sh, both racing the migration ladder and the workbench "
        "rebuild on 4GB of RAM. Measured 2026-08-26."
    )
    assert concurrency.get("group"), "the concurrency group needs a name"


def test_the_group_is_on_the_JOB_and_not_the_workflow(parsed: dict) -> None:
    """⚠️ The correction that cost a delivery outage, so it is pinned.

    A workflow-level group cancels the whole RUN when a newer one supersedes it
    while pending — including `publish-release`, which fast-forwards the
    `release` ref the box's pull path polls. Measured 2026-08-26: four
    consecutive runs cancelled with ZERO jobs started, `release` frozen at
    00fb8db0 while `main` moved four commits past it.

    The serialisation meant to protect delivery had silently stopped it, and
    "cancelled" reads like tidy supersession rather than like a fault.

    CIV-4 gives `test` a group of its own, and only that one group. The next
    section pins it. `lint` and `publish-release` still have none.
    """
    assert parsed.get("concurrency") is None, (
        "deploy.yml declares concurrency at WORKFLOW level. That queues — and "
        "then cancels — `lint`, `test` and `publish-release` too. None of them "
        "touch the box, and `publish-release` is how a box that cannot be "
        "reached inbound still learns what to converge on. Put the group on the "
        "`deploy` job."
    )
    for job in ("lint", "publish-release"):
        assert "concurrency" not in parsed["jobs"][job], (
            f"`{job}` must not be serialised behind a deploy — it never touches "
            "the box"
        )


def test_a_superseded_deploy_is_queued_and_never_cancelled(parsed: dict) -> None:
    """⚠️ The one that will look like a bug to whoever reads it next.

    `cancel-in-progress: true` is the usual setting and is right for a test job,
    whose result stops mattering the moment it is superseded. It is wrong here.
    Cancelling kills the SSH session mid-`vps_apply.sh` — the "killed mid-apply"
    state the deploy step's own 1800s timeout exists to prevent, leaving
    migrations half-applied on a ladder that CANNOT ROLL BACK (R6).

    A queued deploy costs minutes. A half-applied one costs a restore.
    """
    assert parsed["jobs"]["deploy"]["concurrency"].get("cancel-in-progress") is False, (
        "cancel-in-progress must be FALSE for production deploys — see this "
        "test's docstring before changing it"
    )


def test_the_deploy_job_is_bounded(parsed: dict) -> None:
    """Otherwise a wedged job holds the concurrency slot for GitHub's 6h default.

    Which is worse than the hang itself: every later deploy queues behind it, so
    one stuck run stops delivery entirely rather than just failing.
    """
    timeout = parsed["jobs"]["deploy"].get("timeout-minutes")
    assert timeout, "the deploy job needs a timeout-minutes backstop"
    assert timeout >= 105, (
        f"timeout-minutes={timeout} is BELOW the deploy step's own worst case "
        "(3 rounds x (1800s ssh + 240s verify) + backoff ~= 105 min). A "
        "backstop that fires during normal operation would kill a legitimate "
        "slow deploy mid-apply — the exact state it is meant to prevent."
    )
    assert timeout <= 180, (
        f"timeout-minutes={timeout} is so high it is not a backstop"
    )


# ── CIV-4: main runs the full suite in batches ──────────────────────────────
#
# `project-docs/specs/ci_velocity.md` §5, CIV-4. A push run of `test` joins
# ONE group, so one full suite runs at a time. GitHub keeps one running job
# and one pending job in a group. A newer pending job cancels the older one.
# So the merges that wait fold into the next run, and a folded run shows as
# a cancelled `test` job. That is the design.
#
# ⚠️ THE TRAP. If the daily release shared the push group, the next merge
# would cancel its pending `test` job. `publish-release` would then read
# `cancelled` and skip, and that day's release would be lost. So a schedule
# run or a dispatch gets a group of its own, named by its run id. It never
# queues, and nothing cancels it.

#: The ONE concurrency group that `test` may have, character for character.
SUITE_GROUP = (
    "${{ github.event_name == 'push' && 'main-full-suite' "
    "|| format('release-{0}', github.run_id) }}"
)

#: The step that names the tested range of a push batch.
RANGE_STEP = "Name the tested range"


def _steps(parsed: dict, job: str) -> list[dict]:
    return parsed["jobs"][job]["steps"]


def _range_step(parsed: dict) -> dict:
    found = [s for s in _steps(parsed, "test") if s.get("name") == RANGE_STEP]
    assert len(found) == 1, f"the `test` job needs exactly one step named {RANGE_STEP!r}"
    return found[0]


def test_a_push_batch_shares_one_group_and_a_release_gets_its_own(parsed: dict) -> None:
    """Only this expression. A plain `main-full-suite` loses the release."""
    concurrency = parsed["jobs"]["test"].get("concurrency")
    assert concurrency == {"group": SUITE_GROUP, "cancel-in-progress": False}, (
        "the `test` job's group must be EXACTLY the CIV-4 expression, with "
        "cancel-in-progress false. A group that a schedule or a dispatch "
        "shares with a push lets the next merge cancel the release's tests. "
        "cancel-in-progress true would kill a running batch at every merge, "
        "and then no batch would ever finish."
    )


def test_no_other_group_in_any_workflow_can_meet_the_suite_group() -> None:
    """A group name is repository-wide, so it spans every workflow file."""
    groups: list[str] = []
    for path in sorted(WORKFLOW.parent.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        holders = [doc, *(doc.get("jobs") or {}).values()]
        for holder in holders:
            conc = holder.get("concurrency")
            if isinstance(conc, dict):
                groups.append(str(conc.get("group", "")))
            elif conc:
                groups.append(str(conc))
    assert groups.count(SUITE_GROUP) == 1, "the suite group is the `test` job's alone"
    others = [g for g in groups if g != SUITE_GROUP]
    clash = [g for g in others if "main-full-suite" in g or "release-" in g]
    assert not clash, f"these groups can meet the suite group: {clash}"


def test_the_suite_job_token_reads_and_never_writes(parsed: dict) -> None:
    """The range step reads the Actions API. It needs `actions: read`, no more.

    A job-level block REPLACES the workflow's `contents: read`, so the job
    names `contents: read` again for its checkout.
    """
    assert parsed["jobs"]["test"].get("permissions") == {
        "contents": "read", "actions": "read",
    }


def test_the_range_step_runs_on_a_push_and_cannot_fail_the_suite(parsed: dict) -> None:
    step = _range_step(parsed)
    assert step.get("if") == "${{ github.event_name == 'push' }}", (
        "a push only. A release run tests the tip, and needs no range."
    )
    assert step.get("continue-on-error") is True, (
        "a failure of the range step must never fail `test`. On a release, "
        "`test` is the gate."
    )
    assert 0 < int(step.get("timeout-minutes", 0)) <= 5, "a hung API call must not hold the batch group"
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    # The jobs API names a job by its `name:`. A rename that forgets this
    # value would find no green run, and every range would read "unknown".
    assert step["env"]["SUITE_JOB"] == parsed["jobs"]["test"]["name"]
    # GitHub runs the step under `bash -e`. A failed API call must reach the
    # summary line, so the script turns -e off.
    assert step["run"].lstrip().startswith("set +e")
    names = [s.get("name") for s in _steps(parsed, "test")]
    assert names.index(RANGE_STEP) < names.index("Run unit tests"), (
        "the range goes first, so a red batch still names its PRs"
    )


# The range step, run for real in bash. Shell functions shadow `gh` and
# `python3`, so the step's own text runs unchanged, as in
# test_daily_release.py. `gh api <path>` prints the fixture file for <path>.

def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_daily_release.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()

_REPO = "Hathi-Labs/Metorite"
_RUNS = f"repos/{_REPO}/actions/workflows/deploy.yml/runs?branch=main&event=push&per_page=40"
_OWN, _FOLDED, _GREEN, _OLDER = 300, 299, 298, 297
_SHA = {_OWN: "c" * 40, _FOLDED: "b" * 40, _GREEN: "a" * 40, _OLDER: "9" * 40}


def _jobs_path(run_id: int) -> str:
    return f"repos/{_REPO}/actions/runs/{run_id}/jobs?per_page=100"


def _compare_path(base: str) -> str:
    return f"repos/{_REPO}/compare/{base}...{_SHA[_OWN]}"


def _runs(conclusions: dict[int, str | None]) -> dict:
    # Newest first, as the API lists them. This run is in the list too.
    return {"workflow_runs": [
        {"id": rid, "head_sha": _SHA[rid], "conclusion": c}
        for rid, c in sorted(conclusions.items(), reverse=True)
    ]}


def _jobs(suite_conclusion: str | None, name: str) -> dict:
    return {"jobs": [
        {"name": "Lint & type-check", "conclusion": "success"},
        {"name": name, "conclusion": suite_conclusion},
    ]}


def _compare(status: str, *subjects: str) -> dict:
    commits = [
        {"sha": f"{i:x}" * 40, "commit": {"message": f"{s}\n\nThe body text (#999)."}}
        for i, s in enumerate(subjects, start=1)
    ]
    return {"status": status, "total_commits": len(commits), "commits": commits}


def _run_range(
    parsed: dict, tmp_path: Path, fixtures: dict[str, dict], clock: int = -600,
) -> tuple[str, list[str]]:
    step = _range_step(parsed)
    fix = tmp_path / "fix"
    fix.mkdir()
    for api_path, body in fixtures.items():
        (fix / re.sub(r"[^A-Za-z0-9]", "_", api_path)).write_text(json.dumps(body), encoding="utf-8")
    summary = tmp_path / "summary.md"
    # The fake `gh` uses bash builtins only. On a Windows PC each process
    # start costs seconds. `SECONDS=<clock>` sets the bash clock that the
    # step's 90 s budget reads, so a slow PC does not trip it by accident.
    # Python on Windows prints CRLF, and the Linux runner prints LF. So the
    # `python3` shim removes each CR, and keeps the exit code.
    shims = (
        f"SECONDS={clock}\n"
        "python3() {\n"
        f'  local out rc; out="$("{Path(sys.executable).as_posix()}" "$@")"; rc=$?\n'
        "  out=${out//$'\\r'/}\n"
        '  [ -z "$out" ] || printf \'%s\\n\' "$out"\n'
        "  return $rc\n"
        "}\n"
        "gh() {\n"
        '  [ "$1" = api ] || return 9\n'
        '  printf \'%s\\n\' "$2" >> "$FIX/calls"\n'
        '  local f="$FIX/${2//[!A-Za-z0-9]/_}" line\n'
        '  [ -f "$f" ] || return 1\n'
        '  while IFS= read -r line || [ -n "$line" ]; do printf \'%s\\n\' "$line"; done < "$f"\n'
        "}\n"
    )
    env = {
        **os.environ, **step["env"], "FIX": fix.as_posix(),
        "GITHUB_REPOSITORY": _REPO, "GITHUB_RUN_ID": str(_OWN),
        "GITHUB_SHA": _SHA[_OWN], "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    # The flags GitHub gives a `run:` step: bash --noprofile --norc -eo pipefail.
    done = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", shims + step["run"]],
        env=env, capture_output=True, text=True, timeout=300, cwd=tmp_path,
    )
    calls_file = fix / "calls"
    calls = calls_file.read_text(encoding="utf-8").split() if calls_file.exists() else []
    text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    assert done.returncode == 0, done.stdout + done.stderr
    assert "### Tested range" in text, done.stdout + done.stderr
    assert f"`{_SHA[_OWN][:12]}`" in text, "the range ends at this run's commit"
    return text, calls


needs_bash = pytest.mark.skipif(_BASH is None, reason="no bash on this machine")


@needs_bash
def test_the_range_starts_at_the_last_green_suite_and_names_each_pr(parsed: dict, tmp_path: Path) -> None:
    name = parsed["jobs"]["test"]["name"]
    text, calls = _run_range(parsed, tmp_path, {
        _RUNS: _runs({_OWN: None, _FOLDED: "cancelled", _GREEN: "success", _OLDER: "success"}),
        # A folded run: its suite never started.
        _jobs_path(_FOLDED): _jobs("cancelled", name),
        _jobs_path(_GREEN): _jobs("success", name),
        _compare_path(_SHA[_GREEN]): _compare(
            "ahead", "Chat: one fix (#11)", "Merge pull request #12 from Hathi-Labs/x"),
    })
    assert f"`{_SHA[_GREEN][:12]}`" in text
    assert f"actions/runs/{_GREEN}" in text
    assert "2 new commit(s)" in text
    assert "PRs: #11, #12." in text, "the subject names the PR, and the body does not"
    assert "#999" not in text
    assert _jobs_path(_OWN) not in calls, "this run is not its own last green run"
    assert _jobs_path(_OLDER) not in calls, "the search stops at the newest green run"
    assert calls[0] == _RUNS, "push runs on main only"


@needs_bash
def test_a_green_suite_in_a_red_run_still_counts(parsed: dict, tmp_path: Path) -> None:
    """It reads the JOB. A run is red when only `publish-staging` failed."""
    name = parsed["jobs"]["test"]["name"]
    text, _ = _run_range(parsed, tmp_path, {
        _RUNS: _runs({_OWN: None, _FOLDED: "failure", _GREEN: "success"}),
        _jobs_path(_FOLDED): _jobs("success", name),
        _compare_path(_SHA[_FOLDED]): _compare("ahead", "Docs: a line (#13)"),
    })
    assert f"actions/runs/{_FOLDED}" in text
    assert "PRs: #13." in text


@needs_bash
@pytest.mark.parametrize(("case", "why"), [
    ("api-down", "the Actions API gave no list of runs"),
    ("no-green-run", "no push run of this job passed in the last 40 runs"),
    ("not-an-ancestor", " is not an ancestor of this commit"),
    ("compare-down", "the compare API gave no answer"),
    ("slow-api", "the search for a green run took over 90 s"),
])
def test_the_range_reads_unknown_and_the_step_still_ends_clean(
    parsed: dict, tmp_path: Path, case: str, why: str,
) -> None:
    name = parsed["jobs"]["test"]["name"]
    fixtures: dict[str, dict] = {}
    if case != "api-down":
        fixtures[_RUNS] = _runs({_OWN: None, _FOLDED: "cancelled", _GREEN: "success"})
        fixtures[_jobs_path(_FOLDED)] = _jobs("cancelled", name)
        fixtures[_jobs_path(_GREEN)] = _jobs(
            "failure" if case == "no-green-run" else "success", name)
    if case == "not-an-ancestor":
        fixtures[_compare_path(_SHA[_GREEN])] = _compare("diverged", "Other (#14)")
    # 1000 s on the bash clock: the budget is spent before the first call.
    text, calls = _run_range(parsed, tmp_path, fixtures, clock=1000 if case == "slow-api" else -600)
    assert "- From: unknown (" in text, text
    assert why in text, text
    assert "PRs:" not in text
    if case == "slow-api":
        assert not any("/jobs" in c for c in calls), "a spent budget makes no more calls"


# ── Verify by evidence, never by a green tick ───────────────────────────────

def test_the_deploy_is_verified_by_the_SERVING_COMMIT(raw: str) -> None:
    """The pipeline's oldest hole, closed 2026-08-26.

    Every gate here used to ask only "is the app up?" — and an app running
    YESTERDAY'S code answers yes. That is CLAUDE.md rule 8 in one line: "four
    deploys once reported success while shipping nothing."
    """
    deploy_step = raw[raw.index("verify() {"):raw.index("hand_off_to_pull_path() {")]
    assert "/version" in deploy_step, (
        "verify() no longer reads GET /version. Health alone cannot distinguish "
        "a successful deploy from a timeout-killed one that left the OLD build "
        "running and healthy."
    )
    assert "GITHUB_SHA" in deploy_step, (
        "verify() must compare the serving SHA against THIS RUN's commit — "
        "reading /version without comparing it proves nothing"
    )


def test_a_wrong_commit_fails_rather_than_warns(raw: str) -> None:
    """A mismatch must end the round, not print a note and pass."""
    verify = raw[raw.index("verify() {"):raw.index("hand_off_to_pull_path() {")]
    ok_line = [
        line for line in verify.splitlines()
        if 'got_sha" = "$GITHUB_SHA' in line
    ]
    assert ok_line, (
        "verify()'s success condition must include the SHA equality test — if "
        "the comparison is only logged, the gate is decorative"
    )


def test_the_ssh_timeout_cannot_be_ignored(raw: str) -> None:
    """`timeout` sends SIGTERM, and a wedged ssh can ignore it.

    Observed 2026-08-26: run 32937837653 sat in this step at 35 minutes, past a
    30-minute limit that had already fired. `-k` follows with SIGKILL.

    A timeout that CAN be ignored is worse than no timeout at all, because
    everything downstream is sized from a number that turns out not to hold —
    the 3-round loop, and the job's 120-minute backstop, are both computed from
    this bound.
    """
    assert "timeout -k" in raw, (
        "the deploy ssh must use `timeout -k <grace> <limit>` so a hung session "
        "is SIGKILLed after refusing SIGTERM"
    )


def test_the_version_endpoint_the_pipeline_depends_on_still_exists() -> None:
    """The deploy gate now has a runtime dependency, so name it.

    `GET /version` shipped for WS-39 and, until this change, nothing consumed
    it. Now the pipeline does — so deleting it stops being a local decision
    about an unused endpoint and starts being a change to how delivery is
    verified.
    """
    main = (
        Path(__file__).resolve().parents[2]
        / "apps" / "services" / "gateway" / "gateway" / "main.py"
    )
    body = io.open(main, encoding="utf-8").read()
    assert '"/version"' in body or "'/version'" in body, (
        "GET /version is gone, and deploy.yml's verify() depends on it. "
        "Removing it makes every deploy fail verification."
    )


# ── H-137: a half-finished apply must not report success ──────────────────
#
# 🔴 **Measured 2026-09-20, on three runs all marked SUCCESS.** `vps_apply.sh`
# died at `==> Rebuilding + restarting workbench` when `npm ci` hit EACCES and
# the Next build then failed. `set -e` exited, ssh returned non-zero, the
# workflow shrugged, and `verify()` passed — because a failed build
# DELIBERATELY leaves the previous `.next` serving and the gateway restart
# happens further up the script. Every step after the failure silently did not
# run: the health watchdog timer, and the systemd unit sync.
#
# ⚠️ These are STRUCTURAL, like the rest of this file. The contract is a
# STRING printed by one file and grepped by another, which is exactly the
# shape that rots without a test — rewording either side breaks the gate and
# nothing goes red.

APPLY = Path(__file__).resolve().parents[2] / "scripts" / "vps_apply.sh"

#: The apply script's final line. The workflow proves the script finished by
#: finding it. Both sides must agree, character for character.
APPLY_DONE = "==> Deployment complete"


class TestTheApplyMustReachItsEnd:
    def test_the_apply_script_prints_the_marker_as_its_LAST_echo(self):
        body = APPLY.read_text(encoding="utf-8")
        assert APPLY_DONE in body, (
            "vps_apply.sh no longer prints the line deploy.yml greps for. "
            "A deploy that dies half way would report success again.")
        # It must be the LAST echo in the file, or a script that dies after it
        # would still look finished.
        echoes = [
            ln for ln in body.splitlines()
            if ln.strip().startswith("echo ") and "$" not in ln.split("echo ", 1)[1][:2]
        ]
        assert APPLY_DONE in echoes[-1], (
            f"the marker must be the script's final echo, found: {echoes[-1]!r}")

    def test_the_workflow_greps_for_that_exact_marker(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        assert f'APPLY_DONE="{APPLY_DONE}"' in wf, (
            "deploy.yml must pin the apply script's final line verbatim")
        assert 'grep -qF "$APPLY_DONE"' in wf, (
            "deploy.yml must TEST for the marker. Printing it is not a gate.")

    def test_the_ssh_status_is_read_from_PIPESTATUS_not_dollar_question(self):
        """🔴 The subtle way this gate turns itself off.

        The apply output goes through `tee`, and `tee` always succeeds. Reading
        `$?` after the pipe therefore reports every apply as clean.
        """
        wf = WORKFLOW.read_text(encoding="utf-8")
        assert "PIPESTATUS[0]" in wf
        assert "ssh_deploy 2>&1 | tee" in wf

    def test_a_round_requires_BOTH_the_apply_and_the_verify(self):
        """Either alone has shipped a green run that delivered nothing.

        `verify()` alone is H-137. A finished apply alone is the hole that
        `verify()` was added in 2026-08-26 to close.
        """
        wf = WORKFLOW.read_text(encoding="utf-8")
        assert 'if run_deploy; then applied=1; fi' in wf
        assert '[ "$applied" = 1 ] && verify' in wf
        # And the old shape must be gone: run_deploy called for its side
        # effects, with only verify gating the exit.
        assert "\n            run_deploy\n            if verify; then" not in wf

    def test_run_deploy_reports_a_failure_instead_of_swallowing_it(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        assert "verifying by health regardless" not in wf, (
            "that line WAS the bug: it announced the failure and continued")
