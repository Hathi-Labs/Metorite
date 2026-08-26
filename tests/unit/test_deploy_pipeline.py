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
    concurrency = parsed.get("concurrency")
    assert concurrency, (
        "deploy.yml has no `concurrency:` group. Two merges minutes apart then "
        "run two deploys against ONE box simultaneously — both executing "
        "vps_apply.sh, both racing the migration ladder and the workbench "
        "rebuild on 4GB of RAM. Measured 2026-08-26."
    )
    assert concurrency.get("group"), "the concurrency group needs a name"


def test_a_superseded_deploy_is_queued_and_never_cancelled(parsed: dict) -> None:
    """⚠️ The one that will look like a bug to whoever reads it next.

    `cancel-in-progress: true` is the usual setting and is right for a test job,
    whose result stops mattering the moment it is superseded. It is wrong here.
    Cancelling kills the SSH session mid-`vps_apply.sh` — the "killed mid-apply"
    state the deploy step's own 1800s timeout exists to prevent, leaving
    migrations half-applied on a ladder that CANNOT ROLL BACK (R6).

    A queued deploy costs minutes. A half-applied one costs a restore.
    """
    assert parsed["concurrency"].get("cancel-in-progress") is False, (
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


# ── Verify by evidence, never by a green tick ───────────────────────────────

def test_the_deploy_is_verified_by_the_SERVING_COMMIT(raw: str) -> None:
    """The pipeline's oldest hole, closed 2026-08-26.

    Every gate here used to ask only "is the app up?" — and an app running
    YESTERDAY'S code answers yes. That is CLAUDE.md rule 8 in one line: "four
    deploys once reported success while shipping nothing."
    """
    deploy_step = raw[raw.index("verify() {"):raw.index("for round in 1 2 3")]
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
    verify = raw[raw.index("verify() {"):raw.index("for round in 1 2 3")]
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
