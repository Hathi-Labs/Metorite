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

import ast
import io
import json
import os
import re
import shlex
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
#
# A person's rerun of an old push run (attempt 2 or more) also gets a group
# of its own. In the push group, its pending job would cancel a newer
# pending batch, and that batch holds the newer merges.
#
# ⚠️ GitHub does not promise the order of pending jobs: "ordering is not
# guaranteed" (docs, `actions-group-concurrency`). So the pending job that
# survives can hold an OLDER merge than the tip. A batch job therefore tests
# the tip of main when it starts, not its own GITHUB_SHA. The release, a
# dispatch and a rerun test GITHUB_SHA, because that is what they ship or
# check again.

#: The ONE concurrency group that `test` may have, character for character.
SUITE_GROUP = (
    "${{ github.event_name == 'push' && github.run_attempt == 1 "
    "&& 'main-full-suite' || format('release-{0}', github.run_id) }}"
)

#: The step that names the tested range of a push batch.
RANGE_STEP = "Name the tested range"

#: The step that moves a push batch to the tip of main, and its condition.
TIP_STEP = "Test the tip of main"
TIP_IF = "${{ github.event_name == 'push' && github.run_attempt == 1 }}"
#: The one fetch that the tip step makes. It keeps `origin/main` equal to HEAD.
TIP_FETCH = "git fetch --no-tags --depth=1 origin +refs/heads/main:refs/remotes/origin/main"
#: The state that actions/checkout leaves on a push: local `main` at origin/main.
TIP_CHECKOUT = "git checkout -q -B main refs/remotes/origin/main"


def _steps(parsed: dict, job: str) -> list[dict]:
    return parsed["jobs"][job]["steps"]


def _named_step(parsed: dict, name: str) -> dict:
    found = [s for s in _steps(parsed, "test") if s.get("name") == name]
    assert len(found) == 1, f"the `test` job needs exactly one step named {name!r}"
    return found[0]


def _range_step(parsed: dict) -> dict:
    return _named_step(parsed, RANGE_STEP)


def _tip_step(parsed: dict) -> dict:
    return _named_step(parsed, TIP_STEP)


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


def _gh_eval(
    expr: str, github: dict[str, object], inputs: dict[str, object] | None = None,
) -> object:
    """Evaluate a GitHub Actions expression of the shape the group uses.

    It models the GitHub rules that the group depends on:
    - `&&` and `||` return an operand, not a boolean. `&&` returns the first
      falsy operand, or the last one. `||` returns the first truthy operand,
      or the last one.
    - `==` on two strings ignores case. On other types, GitHub casts both
      sides to a number. `!=` is the opposite of `==`.
    - `!x` is true when `x` is falsy. A schedule run has no inputs, so
      `inputs.skip_tests` is null there, and `!inputs.skip_tests` is true.
    - `format()` puts each argument in its `{N}` place. `always()` is true.
    Any other node fails the test, so a new shape cannot pass by accident.
    """
    body = expr.strip()
    assert body.startswith("${{") and body.endswith("}}"), body
    inner = body[3:-2].replace("&&", " and ").replace("||", " or ")
    inner = inner.replace("!=", "@NE@").replace("!", " not ").replace("@NE@", "!=").strip()
    return _gh_node(ast.parse(inner, mode="eval"), {"github": github, "inputs": inputs or {}})


def _gh_number(value: object) -> float:
    # GitHub casts null, false and the empty string to 0, and true to 1.
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def _gh_equal(left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.lower() == right.lower()
    return _gh_number(left) == _gh_number(right)


def _gh_bool_op(node: ast.BoolOp, ctx: dict[str, dict[str, object]]) -> object:
    # `&&` stops at the first falsy operand, `||` at the first truthy one.
    value: object = None
    for part in node.values:
        value = _gh_node(part, ctx)
        if (not value) if isinstance(node.op, ast.And) else bool(value):
            return value
    return value


def _gh_node(node: ast.AST, ctx: dict[str, dict[str, object]]) -> object:
    if isinstance(node, ast.Expression):
        return _gh_node(node.body, ctx)
    if isinstance(node, ast.BoolOp):
        return _gh_bool_op(node, ctx)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _gh_node(node.operand, ctx)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        same = _gh_equal(_gh_node(node.left, ctx), _gh_node(node.comparators[0], ctx))
        return same if isinstance(node.ops[0], ast.Eq) else not same
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "github":
        return ctx["github"][node.attr]
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "inputs":
        return ctx["inputs"].get(node.attr)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "always" and not node.args:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "format":
        template, *args = (_gh_node(a, ctx) for a in node.args)
        return str(template).format(*args)
    raise AssertionError(f"the expression uses a shape this model does not know: {ast.dump(node)}")


_RUN_ID = "41884338138"


@pytest.mark.parametrize(("event", "attempt", "group"), [
    # The first attempt of a push joins the batch.
    ("push", "1", "main-full-suite"),
    # A person's rerun of an old push run tests its old commit at once. In
    # the push group it would cancel a newer pending batch.
    ("push", "2", f"release-{_RUN_ID}"),
    ("push", "3", f"release-{_RUN_ID}"),
    # The release and a dispatch never queue behind a push.
    ("schedule", "1", f"release-{_RUN_ID}"),
    ("schedule", "2", f"release-{_RUN_ID}"),
    ("workflow_dispatch", "1", f"release-{_RUN_ID}"),
    ("workflow_dispatch", "2", f"release-{_RUN_ID}"),
])
@pytest.mark.parametrize("as_number", [False, True], ids=["attempt-string", "attempt-number"])
def test_each_trigger_and_attempt_gets_the_planned_group(
    parsed: dict, event: str, attempt: str, group: str, as_number: bool,
) -> None:
    """The context gives `run_attempt` and `run_id` as strings. `== 1` casts
    the string to a number. Both forms must give a string group, never a
    boolean, because a boolean group would put every run in one group."""
    expr = parsed["jobs"]["test"]["concurrency"]["group"]
    github = {
        "event_name": event,
        "run_attempt": int(attempt) if as_number else attempt,
        "run_id": int(_RUN_ID) if as_number else _RUN_ID,
    }
    got = _gh_eval(expr, github)
    assert isinstance(got, str) and got, f"the group evaluated to {got!r}, not a name"
    assert got == group, f"{event} attempt {attempt}: group {got!r}, want {group!r}"


@pytest.mark.parametrize(("event", "attempt"), [
    ("push", "1"), ("push", "2"), ("schedule", "1"), ("schedule", "2"),
    ("workflow_dispatch", "1"), ("workflow_dispatch", "2"),
])
def test_a_job_tests_the_tip_exactly_when_it_is_in_the_batch_group(
    parsed: dict, event: str, attempt: str,
) -> None:
    """A batch job tests the tip, so a fold cannot leave the tip untested.

    Every job outside the batch group tests GITHUB_SHA. The release ships
    GITHUB_SHA, so its gate must test that commit and no other.
    """
    github = {"event_name": event, "run_attempt": attempt, "run_id": _RUN_ID}
    in_batch = _gh_eval(parsed["jobs"]["test"]["concurrency"]["group"], github) == "main-full-suite"
    moves_to_tip = bool(_gh_eval(_tip_step(parsed)["if"], github))
    assert moves_to_tip == in_batch, f"{event} attempt {attempt}: tip={moves_to_tip}, batch={in_batch}"


def test_a_push_batch_tests_the_tip_and_a_release_tests_its_own_commit(parsed: dict) -> None:
    steps = _steps(parsed, "test")
    names = [s.get("name") for s in steps]
    tip = _tip_step(parsed)
    assert tip.get("id") == "tip"
    assert tip.get("if") == TIP_IF
    assert TIP_IF.removeprefix("${{ ").removesuffix(" }}") in SUITE_GROUP, (
        "the tip step and the batch group must read the same condition"
    )
    # A half-moved tree must fail the job, never test in silence.
    assert "continue-on-error" not in tip
    assert TIP_FETCH in tip["run"] and TIP_CHECKOUT in tip["run"]
    # The checkout itself takes GITHUB_SHA. That is the release path.
    assert steps[0]["uses"].startswith("actions/checkout@") and not steps[0].get("with"), (
        "the checkout must take GITHUB_SHA, with no `ref:`. The release ships that commit."
    )
    assert names[1] == TIP_STEP, "the tip step comes straight after the checkout"
    assert names.index(TIP_STEP) < names.index(RANGE_STEP) < names.index("Sync deps") < names.index(
        "Run unit tests"), "move the tree first, then name it, then install and test it"
    movers = [
        s.get("name") or s.get("uses") for s in steps
        if s is not tip and re.search(r"\bgit\s+(fetch|checkout|switch|reset|pull)\b", s.get("run", ""))
    ]
    assert not movers, f"only the tip step may move the tree: {movers}"
    assert _range_step(parsed)["env"]["TESTED_SHA"] == "${{ steps.tip.outputs.sha || github.sha }}", (
        "the range must end at the commit that the job tested"
    )


def _run_tip(parsed: dict, tmp_path: Path, fetch_ok: bool) -> tuple[str, str, list[str]]:
    """Run the tip step in bash. A shell function stands in for `git`."""
    step = _tip_step(parsed)
    log, out = tmp_path / "git.log", tmp_path / "output"
    shims = (
        "git() {\n"
        '  printf \'%s\\n\' "git $*" >> "$GIT_LOG"\n'
        '  case "$1" in\n'
        '    fetch) [ "$FETCH_OK" = 1 ] ;;\n'
        "    rev-parse) printf '%s\\n' \"$TIP\" ;;\n"
        "    *) return 0 ;;\n"
        "  esac\n"
        "}\n"
    )
    env = {
        **os.environ, "GIT_LOG": log.as_posix(), "FETCH_OK": "1" if fetch_ok else "0",
        "TIP": _TIP, "GITHUB_SHA": _SHA[_OWN], "GITHUB_OUTPUT": str(out),
    }
    done = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", shims + step["run"]],
        env=env, capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return out.read_text(encoding="utf-8"), done.stdout, calls


def test_the_suite_job_token_reads_and_never_writes(parsed: dict) -> None:
    """The range step reads the Actions API and the check-run annotations.

    It needs `actions: read` and `checks: read`, and no write. A job-level
    block REPLACES the workflow's `contents: read`, so the job names
    `contents: read` again for its checkout.
    """
    assert parsed["jobs"]["test"].get("permissions") == {
        "contents": "read", "actions": "read", "checks": "read",
    }


def test_a_hung_batch_cannot_hold_the_group_for_six_hours(parsed: dict) -> None:
    """Without a limit, GitHub stops a job after 360 minutes.

    A hung push batch would hold `main-full-suite` that long, and every later
    merge would wait. The last four batches took 35 to 37 minutes (measured
    2026-11-04). 90 minutes is about 2.4 times that. On a release, the same
    limit ends a hung gate, and a red gate holds that day's release. A lower
    limit can stop a slow but sound release, so change it only after a new
    measurement.
    """
    assert parsed["jobs"]["test"].get("timeout-minutes") == 90, (
        "the `test` job needs `timeout-minutes: 90`. Read this test's "
        "docstring before you change the number."
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
#: The tip of main when this run's job started. It holds this run's push.
_TIP = "e" * 40
#: What the green batch really tested: the tip at ITS start, per its notice.
_GREEN_TESTED = "d" * 40


def _job_id(run_id: int) -> int:
    return run_id * 10


def _jobs_path(run_id: int) -> str:
    return f"repos/{_REPO}/actions/runs/{run_id}/jobs?per_page=100"


def _notes_path(run_id: int) -> str:
    return f"repos/{_REPO}/check-runs/{_job_id(run_id)}/annotations?per_page=100"


def _notes(tested: str) -> list[dict]:
    # The shape GitHub gives a `::notice title=...::` line (read 2026-11-05).
    return [
        {"annotation_level": "notice", "path": ".github", "title": "", "message": '"The ubuntu-latest label moves."'},
        {"annotation_level": "notice", "path": ".github", "title": "Tested commit", "message": tested},
    ]


def _compare_path(base: str, head: str = _TIP) -> str:
    return f"repos/{_REPO}/compare/{base}...{head}"


def _runs(conclusions: dict[int, str | None]) -> dict:
    # Newest first, as the API lists them. This run is in the list too.
    return {"workflow_runs": [
        {"id": rid, "head_sha": _SHA[rid], "conclusion": c}
        for rid, c in sorted(conclusions.items(), reverse=True)
    ]}


def _jobs(run_id: int, suite_conclusion: str | None, name: str) -> dict:
    return {"jobs": [
        {"id": _job_id(run_id) + 1, "name": "Lint & type-check", "conclusion": "success"},
        {"id": _job_id(run_id), "name": name, "conclusion": suite_conclusion},
    ]}


def _compare(status: str, *subjects: str) -> dict:
    commits = [
        {"sha": f"{i:x}" * 40, "commit": {"message": f"{s}\n\nThe body text (#999)."}}
        for i, s in enumerate(subjects, start=1)
    ]
    return {"status": status, "total_commits": len(commits), "commits": commits}


def _run_range(
    parsed: dict, tmp_path: Path, fixtures: dict[str, object], clock: int = -600,
    tested: str = _TIP,
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
        "GITHUB_STEP_SUMMARY": str(summary), "TESTED_SHA": tested,
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
    assert f"- To: `{tested[:12]}`, the commit that this job tested." in text, text
    if tested != _SHA[_OWN]:
        assert f"This run's push was `{_SHA[_OWN][:12]}`" in text, text
    return text, calls


needs_bash = pytest.mark.skipif(_BASH is None, reason="no bash on this machine")


@needs_bash
def test_the_tip_step_moves_the_tree_to_the_tip_and_names_it(parsed: dict, tmp_path: Path) -> None:
    out, stdout, calls = _run_tip(parsed, tmp_path, fetch_ok=True)
    assert out.strip() == f"sha={_TIP}"
    assert f"::notice title=Tested commit::{_TIP}" in stdout
    assert calls[0] == TIP_FETCH and TIP_CHECKOUT in calls, calls


@needs_bash
def test_a_failed_fetch_tests_the_run_commit_and_says_so(parsed: dict, tmp_path: Path) -> None:
    out, stdout, calls = _run_tip(parsed, tmp_path, fetch_ok=False)
    assert out.strip() == f"sha={_SHA[_OWN]}"
    assert "::warning title=Tip of main unknown::" in stdout
    assert f"::notice title=Tested commit::{_SHA[_OWN]}" in stdout
    assert not any(c.startswith("git checkout") for c in calls), "no fetch, so the tree stays"


@needs_bash
def test_the_range_starts_at_what_the_last_green_batch_tested(parsed: dict, tmp_path: Path) -> None:
    """The green batch tested the tip at ITS start. Its notice names it."""
    name = parsed["jobs"]["test"]["name"]
    text, calls = _run_range(parsed, tmp_path, {
        _RUNS: _runs({_OWN: None, _FOLDED: "cancelled", _GREEN: "success", _OLDER: "success"}),
        # A folded run: its suite never started.
        _jobs_path(_FOLDED): _jobs(_FOLDED, "cancelled", name),
        _jobs_path(_GREEN): _jobs(_GREEN, "success", name),
        _notes_path(_GREEN): _notes(_GREEN_TESTED),
        _compare_path(_GREEN_TESTED): _compare(
            "ahead", "Chat: one fix (#11)", "Merge pull request #12 from Hathi-Labs/x"),
    })
    assert f"- From: `{_GREEN_TESTED[:12]}`" in text, text
    assert f"actions/runs/{_GREEN}" in text
    assert "2 new commit(s)" in text
    assert "PRs: #11, #12." in text, "the subject names the PR, and the body does not"
    assert "#999" not in text
    assert _compare_path(_GREEN_TESTED) in calls, "the range ends at the tested tip"
    assert _jobs_path(_OWN) not in calls, "this run is not its own last green run"
    assert _jobs_path(_OLDER) not in calls, "the search stops at the newest green run"
    assert calls[0] == _RUNS, "push runs on main only"


@needs_bash
def test_a_green_batch_with_no_notice_starts_at_its_own_commit(parsed: dict, tmp_path: Path) -> None:
    """An older batch, or a failed notice read. Its push commit is an ancestor
    of what it tested, so the range can hold more commits, never fewer.
    It reads the JOB: a run is red when only `publish-staging` failed."""
    name = parsed["jobs"]["test"]["name"]
    text, _ = _run_range(parsed, tmp_path, {
        _RUNS: _runs({_OWN: None, _FOLDED: "failure", _GREEN: "success"}),
        _jobs_path(_FOLDED): _jobs(_FOLDED, "success", name),
        _compare_path(_SHA[_FOLDED]): _compare("ahead", "Docs: a line (#13)"),
    })
    assert f"- From: `{_SHA[_FOLDED][:12]}`" in text, text
    assert f"actions/runs/{_FOLDED}" in text
    assert "PRs: #13." in text


@needs_bash
def test_a_rerun_names_its_own_commit(parsed: dict, tmp_path: Path) -> None:
    """A rerun skips the tip step, so it tests and names GITHUB_SHA."""
    name = parsed["jobs"]["test"]["name"]
    text, _ = _run_range(parsed, tmp_path, {
        _RUNS: _runs({_OWN: None, _GREEN: "success"}),
        _jobs_path(_GREEN): _jobs(_GREEN, "success", name),
        _compare_path(_SHA[_GREEN], _SHA[_OWN]): _compare("ahead", "Fix (#15)"),
    }, tested=_SHA[_OWN])
    assert "This run's push was" not in text
    assert "PRs: #15." in text


@needs_bash
@pytest.mark.parametrize(("case", "why"), [
    ("api-down", "the Actions API gave no list of runs"),
    ("no-green-run", "no push run of this job passed in the last 40 runs"),
    ("base-newer", f"the last green batch tested `{_SHA[_GREEN][:12]}`, which is newer than this commit"),
    ("diverged", f"`{_SHA[_GREEN][:12]}` and this commit have diverged"),
    ("compare-down", "the compare API gave no answer"),
    ("slow-api", "the search for a green run took over 90 s"),
])
def test_the_range_reads_unknown_and_the_step_still_ends_clean(
    parsed: dict, tmp_path: Path, case: str, why: str,
) -> None:
    name = parsed["jobs"]["test"]["name"]
    fixtures: dict[str, object] = {}
    if case != "api-down":
        fixtures[_RUNS] = _runs({_OWN: None, _FOLDED: "cancelled", _GREEN: "success"})
        fixtures[_jobs_path(_FOLDED)] = _jobs(_FOLDED, "cancelled", name)
        fixtures[_jobs_path(_GREEN)] = _jobs(
            _GREEN, "failure" if case == "no-green-run" else "success", name)
    if case == "base-newer":
        fixtures[_compare_path(_SHA[_GREEN])] = _compare("behind")
    if case == "diverged":
        fixtures[_compare_path(_SHA[_GREEN])] = _compare("diverged", "Other (#14)")
    # 1000 s on the bash clock: the budget is spent before the first call.
    text, calls = _run_range(parsed, tmp_path, fixtures, clock=1000 if case == "slow-api" else -600)
    assert "- From: unknown (" in text, text
    assert why in text, text
    assert "PRs:" not in text
    assert "is not an ancestor" not in text
    if case == "slow-api":
        assert not any("/jobs" in c for c in calls), "a spent budget makes no more calls"


# ── CIV-4b: the R8 gate of a release ────────────────────────────────────────
#
# `project-docs/specs/ci_velocity.md` §5, CIV-4b. The `test` job has no
# database, so each R8 suite skips there. A skip is not a pass. So a release
# also runs the R8 files in six shards, with pr-check's two Postgres
# services, and `publish-release` needs `r8-gate` green.
#
# Measured 2026-11-05: with both databases, the full suite needs 128 to 132
# min of test time on one runner, and `test` stops at 90 min. So the R8
# files get jobs of their own. CIV-9 makes them fast, and then R8 moves into
# the push batch.
#
# ⚠️ The services, the env, the two assert steps and the guard's lists are
# COPIES of pr-check.yml's. The 35 R8 suites that check for their own name
# read pr-check.yml only, so these tests stop the copies from drifting.

_ROOT = WORKFLOW.parents[2]
PR_CHECK = WORKFLOW.parent / "pr-check.yml"
R8_SUITE, R8_GATE = "r8-suite", "r8-gate"
R8_RUN = "Run the R8 suites (this shard)"
R8_GUARD = "The R8 suites ran, and no fence skipped"
ASSERT_STEPS = (
    "Assert the Customer Console database is reachable",
    "Assert the tenant ladder database is reachable and has pgvector",
)
#: The one way past a release gate: a person's dispatch with `skip_tests`.
SKIP_PAST = (
    "(github.event_name == 'workflow_dispatch' && inputs.skip_tests "
    "&& needs.{job}.result == 'skipped')"
)

sys.path.insert(0, str(_ROOT / "scripts"))
import affected_tests  # noqa: E402  scripts/affected_tests.py, the --r8 rule


@pytest.fixture(scope="module")
def pr_check() -> dict:
    return yaml.safe_load(PR_CHECK.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def r8_gate_files() -> list[str]:
    return affected_tests.r8_gate_files()


def _job_step(doc: dict, job: str, name: str) -> dict:
    found = [s for s in doc["jobs"][job]["steps"] if s.get("name") == name]
    assert len(found) == 1, f"`{job}` needs exactly one step named {name!r}"
    return found[0]


def _guard_args(run: str) -> dict[str, list[str]]:
    """The flags of the `ci_unit_guard.py check` command, with their values."""
    words = shlex.split(run.replace("\\\n", " "))
    out: dict[str, list[str]] = {}
    flag = ""
    for word in words[words.index("check") + 1:]:
        if word.startswith("--"):
            flag = word
            out.setdefault(flag, [])
        else:
            out[flag].append(word)
    return out


def _guard_also(parsed: dict) -> list[str]:
    return _job_step(parsed, R8_SUITE, R8_RUN)["env"]["GUARD_ALSO"].split()


#: The release events, by name. A trigger that someone adds later must not
#: start six shards by accident.
R8_RELEASE = "github.event_name == 'schedule' || (github.event_name == 'workflow_dispatch' && !inputs.skip_tests)"


@pytest.mark.parametrize(("event", "inputs", "runs"), [
    ("push", {}, False),
    ("schedule", {}, True),
    ("workflow_dispatch", {"skip_tests": False}, True),
    ("workflow_dispatch", {"skip_tests": True}, False),
    # CIV-13 adds a `nightly` input. Every dispatch without skip_tests runs.
    ("workflow_dispatch", {"skip_tests": False, "nightly": True}, True),
    ("workflow_dispatch", {"skip_tests": True, "nightly": True}, False),
    # Not a release event.
    ("workflow_run", {}, False),
])
def test_the_r8_gate_runs_on_a_release_and_never_on_a_push(
    parsed: dict, event: str, inputs: dict, runs: bool,
) -> None:
    """A push batch does not run it. Six shards on every merge cost about
    120 runner-min, and CIV-9 moves R8 into the batch instead. A dispatch
    with `skip_tests` skips it, as it skips `test`."""
    github = {"event_name": event}
    for job in (R8_SUITE, R8_GATE):
        got = bool(_gh_eval(parsed["jobs"][job]["if"], github, inputs))
        assert got is runs, f"{job} on {event} with {inputs}: runs={got}"
    if event in ("schedule", "workflow_dispatch"):
        # On a release run, the R8 gate runs exactly when `test` runs.
        assert bool(_gh_eval(parsed["jobs"]["test"]["if"], github, inputs)) is runs


def test_the_r8_jobs_read_the_planned_conditions(parsed: dict) -> None:
    jobs = parsed["jobs"]
    assert jobs[R8_SUITE]["if"] == f"${{{{ {R8_RELEASE} }}}}"
    assert jobs[R8_SUITE]["needs"] == "lint"
    # `always()`: a failed or skipped shard must turn the gate red. A gate
    # that skipped would read as a pass.
    assert jobs[R8_GATE]["if"] == f"${{{{ always() && ({R8_RELEASE}) }}}}"
    assert jobs[R8_GATE]["needs"] == R8_SUITE


def test_the_r8_jobs_join_no_group(parsed: dict) -> None:
    """A release job in a shared group can be cancelled by the next run.
    `publish-release` would then skip, and that day's release is lost."""
    for job in (R8_SUITE, R8_GATE):
        assert "concurrency" not in parsed["jobs"][job], job


def test_the_release_needs_the_r8_gate_with_the_one_way_past(parsed: dict) -> None:
    job = parsed["jobs"]["publish-release"]
    assert R8_GATE in job["needs"]
    cond = " ".join(str(job["if"]).split())
    assert f"(needs.{R8_GATE}.result == 'success' || {SKIP_PAST.format(job=R8_GATE)})" in cond, (
        "the release needs `r8-gate` green, or skipped on a `skip_tests` dispatch only"
    )
    assert f"(needs.test.result == 'success' || {SKIP_PAST.format(job='test')})" in cond
    assert cond.count("inputs.skip_tests") == 2, "no other way past a gate"
    # The clock of the release is read after the last gate.
    assert R8_GATE in parsed["jobs"]["release-window"]["needs"]


def test_the_r8_shards_have_pr_checks_two_databases(parsed: dict, pr_check: dict) -> None:
    services = parsed["jobs"][R8_SUITE]["services"]
    assert services == pr_check["jobs"]["test"]["services"], (
        "the R8 shards must start exactly pr-check's two Postgres services"
    )
    assert services["platform-postgres"]["image"] == "postgres:16"
    assert services["platform-postgres"]["ports"] == ["5433:5432"]
    # The stock image has no pgvector, and 01_schema.sql cannot build there.
    assert services["tenant-postgres"]["image"] == "pgvector/pgvector:pg16"
    assert services["tenant-postgres"]["ports"] == ["5434:5432"]


def test_the_r8_shards_set_the_two_variables_and_never_database_url(
    parsed: dict, pr_check: dict, raw: str,
) -> None:
    """DATABASE_URL would arm the two DB-gated tests of
    test_tenant_coverage.py. They fail by design against a fresh ladder.
    pr-check.yml's comment on TENANT_LADDER_DATABASE_URL gives the reason."""
    env = parsed["jobs"][R8_SUITE]["env"]
    assert set(env) == {"CUSTOMER_CONSOLE_DATABASE_URL", "TENANT_LADDER_DATABASE_URL"}
    assert env == pr_check["jobs"]["test"]["env"]
    holders = [parsed, *parsed["jobs"].values()]
    holders += [s for j in parsed["jobs"].values() for s in j.get("steps", [])]
    assert not any("DATABASE_URL" in (h.get("env") or {}) for h in holders)
    assert not re.search(r"\bDATABASE_URL\s*=", raw), "no step may export DATABASE_URL"


def test_the_r8_shards_prove_both_databases_before_the_suite(parsed: dict, pr_check: dict) -> None:
    """Without these, a service that did not come up puts the gate back to
    silent skips. The second also proves pgvector, the way the wrong image
    fails."""
    names = [s.get("name") for s in parsed["jobs"][R8_SUITE]["steps"]]
    for name in ASSERT_STEPS:
        assert _job_step(parsed, R8_SUITE, name) == _job_step(pr_check, "test", name), (
            f"{name!r} must be pr-check's step, character for character"
        )
        assert names.index("Sync deps") < names.index(name) < names.index(R8_RUN)
    assert "'vector'" in _job_step(parsed, R8_SUITE, ASSERT_STEPS[1])["run"]


def test_each_r8_shard_runs_the_r8_files_in_pr_checks_split(parsed: dict, pr_check: dict) -> None:
    job = parsed["jobs"][R8_SUITE]
    assert job["strategy"] == {"fail-fast": False, "matrix": {"shard": [1, 2, 3, 4, 5, 6]}}
    run = _job_step(parsed, R8_SUITE, R8_RUN)["run"]
    assert "python3 scripts/affected_tests.py --r8 > r8_files.txt" in run
    assert "--splits 6 --group ${{ matrix.shard }}" in run
    assert "--splitting-algorithm duration_based_chunks" in run
    assert "-o junit_family=xunit1 --junitxml=junit/r8-shard-${{ matrix.shard }}.xml" in run
    assert "--reruns 1" in run
    assert " -x " not in f" {run} ", "no -x: one run must report every failure"
    pr_run = _job_step(pr_check, "test", "Run unit tests (this shard)")["run"]
    assert "--splits 6" in pr_run and "--splitting-algorithm duration_based_chunks" in pr_run
    keep = _job_step(parsed, R8_SUITE, "Keep this shard's report")
    assert keep["if"] == "always()", "a red shard keeps its report too"
    assert keep["with"]["name"] == "r8-junit-shard-${{ matrix.shard }}"
    assert keep["with"]["path"] == "junit/"


def test_a_hung_r8_shard_cannot_hold_the_release_for_hours(parsed: dict) -> None:
    """A shard took about 20 min in the measurement. 60 min is three times
    that. A hung shard ends red in one hour, and a re-run gets the
    release out the same night."""
    assert parsed["jobs"][R8_SUITE].get("timeout-minutes") == 60


def test_the_r8_gate_reads_every_shard_and_runs_the_guard(parsed: dict) -> None:
    steps = parsed["jobs"][R8_GATE]["steps"]
    assert '[ "${{ needs.r8-suite.result }}" = "success" ] || exit 1' in steps[0]["run"]
    downloads = [s for s in steps if str(s.get("uses", "")).startswith("actions/download-artifact@")]
    assert len(downloads) == 1
    assert downloads[0]["with"] == {"pattern": "r8-junit-shard-*", "path": "junit", "merge-multiple": True}
    guard = _job_step(parsed, R8_GATE, R8_GUARD)["run"]
    assert guard.lstrip().startswith("python3 scripts/ci_unit_guard.py check --junit junit")


def test_the_guard_lists_are_pr_checks_lists(parsed: dict, pr_check: dict) -> None:
    """Each R8 suite checks for its own name in pr-check.yml only. So a list
    here that drifts from pr-check's would go unseen. Add a new R8 suite to
    both files."""
    ours = _guard_args(_job_step(parsed, R8_GATE, R8_GUARD)["run"])
    theirs = _guard_args(_job_step(pr_check, "unit-tests", R8_GUARD)["run"])
    for flag in ("--disarmed", "--never-skip", "--must-run"):
        missing = sorted(set(theirs.get(flag, [])) - set(ours.get(flag, [])))
        extra = sorted(set(ours.get(flag, [])) - set(theirs.get(flag, [])))
        assert ours.get(flag) == theirs.get(flag), (
            f"deploy.yml's {flag} list is not pr-check's: missing {missing}, extra {extra}. "
            "Copy pr-check's list."
        )
    assert len(ours["--must-run"]) > 50
    # A release runs every R8 suite. A filter (CIV-3) belongs to pr-check.
    assert set(ours) == {"--junit", "--disarmed", "--never-skip", "--must-run"}


def test_every_file_that_the_guard_names_runs_in_an_r8_shard(
    parsed: dict, r8_gate_files: list[str],
) -> None:
    """Else the guard reports "no shard ran" for it, and every release is red.

    GUARD_ALSO holds the guard's files that need no database. It holds no
    other file, so it never turns into a second list of R8 suites."""
    guard = _guard_args(_job_step(parsed, R8_GATE, R8_GUARD)["run"])
    named = set(guard["--must-run"]) | set(guard["--never-skip"])
    also = _guard_also(parsed)
    missing = sorted(named - set(r8_gate_files) - set(also))
    assert not missing, f"the guard names these, and no R8 shard runs them: {missing}"
    assert set(also) <= named, "GUARD_ALSO holds only files of the guard's lists"
    needless = sorted(set(also) & set(r8_gate_files))
    assert not needless, f"`affected_tests.py --r8` already lists these: {needless}"
    assert all((_ROOT / p).is_file() for p in also)


def test_the_r8_list_holds_the_measured_suites(r8_gate_files: list[str]) -> None:
    """2026-11-05: 454 files skipped for an unset R8 variable in a deploy
    run. A rule that finds far fewer has broken."""
    assert len(r8_gate_files) > 400
    # A helper-gated file and a file that names the variable.
    assert "tests/unit/test_auth_me_shell_r8.py" in r8_gate_files
    assert "tests/unit/test_customer_console_hold_settle.py" in r8_gate_files


def _run_r8_step(parsed: dict, tmp_path: Path, files: list[str]) -> tuple[int, str, list[str]]:
    """Run the R8 step in bash. Shell functions stand in for python3 and uv."""
    step = _job_step(parsed, R8_SUITE, R8_RUN)
    run = step["run"].replace("${{ matrix.shard }}", "3")
    assert "${{" not in run
    log = tmp_path / "uv.log"
    shims = (
        "python3() {\n"
        '  [ "$1 $2" = "scripts/affected_tests.py --r8" ] || return 9\n'
        "  [ -z \"$R8_LIST\" ] || printf '%s\\n' $R8_LIST\n"
        "}\n"
        "uv() {\n"
        "  printf '%s\\n' \"$@\" > \"$UV_LOG\"\n"
        "}\n"
    )
    env = {**os.environ, **step["env"], "R8_LIST": " ".join(files), "UV_LOG": log.as_posix()}
    done = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", shims + run],
        env=env, capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    args = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done.returncode, done.stdout + done.stderr, args


@needs_bash
@pytest.mark.parametrize("count", [0, 99])
def test_a_short_r8_list_never_reaches_pytest(parsed: dict, tmp_path: Path, count: int) -> None:
    """pytest with no path runs the whole tree, so an empty list stops the
    shard. A list far below the measured 454 files means the rule broke."""
    code, out, args = _run_r8_step(parsed, tmp_path, [f"tests/unit/test_r{i}.py" for i in range(count)])
    assert code == 1, out
    assert "R8 list too short" in out
    assert args == [], "pytest must not start"


@needs_bash
def test_the_r8_step_gives_pytest_the_list_the_guard_files_and_the_split(
    parsed: dict, tmp_path: Path,
) -> None:
    files = [f"tests/unit/test_r{i}.py" for i in range(120)]
    code, out, args = _run_r8_step(parsed, tmp_path, files)
    assert code == 0, out
    assert args[:4] == ["run", "python", "-m", "pytest"]
    also = _guard_also(parsed)
    assert args[4:4 + len(files) + len(also)] == files + also
    rest = args[4 + len(files) + len(also):]
    assert rest[rest.index("--group") + 1] == "3"
    assert "--junitxml=junit/r8-shard-3.xml" in rest
    assert "-x" not in rest


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
