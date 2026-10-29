"""Production moves once a day. A merge moves `staging` and deploys nothing.

WS-38, ``project-docs/specs/staging_and_daily_release.md`` §4 and §5.6 phase 1.

Measured 2026-10-16: 36 gateway starts on production in 24 hours, because
every merge to ``main`` deployed. Each start ended every live agent run. The
owner asked for one production release a day.

What this file fences, in ``.github/workflows/deploy.yml``:

1. A push publishes ``staging`` and never ``release``, and never deploys.
2. The daily schedule and a manual dispatch publish ``release``, and the
   unit tests gate them. ``skip_tests`` on a dispatch is the only way past.
3. ``RELEASE_HOLD=1`` stops the schedule and says so. It does not stop a
   dispatch, which is a person's deliberate act.
4. The deploy waits for ``release`` to move, and sends that exact commit.
5. A schedule run that GitHub starts so late that it reaches the release
   after 02:00 UTC waits a day, and says so (spec §4.1a). A dispatch never
   waits.

And in ``.github/workflows/vps-health.yml``: the hourly delivery check
compares the box with ``release``, not with ``main``. Under the daily clock a
comparison with ``main`` fails every day by design.

The behaviour of ``vps_apply.sh`` with a named commit is fenced next door,
in ``test_deploy_target_ref.py``.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEPLOY = _ROOT / ".github/workflows/deploy.yml"
_HEALTH = _ROOT / ".github/workflows/vps-health.yml"


def _load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def deploy() -> dict:
    return _load(_DEPLOY)


def _on(doc: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return doc.get("on", doc.get(True))


def _if(job: dict) -> str:
    return " ".join(str(job.get("if", "")).split())


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


# ── 1. A push moves staging only ─────────────────────────────────────────────


def test_a_push_still_runs_the_workflow(deploy: dict) -> None:
    assert _on(deploy)["push"]["branches"] == ["main"]


def test_a_push_publishes_staging(deploy: dict) -> None:
    job = deploy["jobs"]["publish-staging"]
    cond = _if(job)
    assert "github.event_name == 'push'" in cond
    assert "github.ref == 'refs/heads/main'" in cond
    body = "\n".join(s.get("run", "") for s in job["steps"])
    assert '"${GITHUB_SHA}:refs/heads/staging"' in body
    assert "--force" not in body and " -f " not in body, "staging is fast-forward only"


def test_a_push_never_publishes_release(deploy: dict) -> None:
    assert "github.event_name != 'push'" in _if(deploy["jobs"]["publish-release"])


def test_the_deploy_runs_only_when_release_moved(deploy: dict) -> None:
    job = deploy["jobs"]["deploy"]
    assert "publish-release" in _needs(job), (
        "the deploy must wait for `release`. If it shipped a sha that `release` "
        "then failed to reach, the pull timer would roll the box back to "
        "`release` five minutes later."
    )
    assert _if(job) == "${{ always() && needs.publish-release.result == 'success' }}"


@pytest.mark.parametrize("name", ["pull-delivery", "chat-smoke", "smoke"])
def test_every_job_that_touches_the_box_hangs_off_the_deploy(deploy: dict, name: str) -> None:
    job = deploy["jobs"][name]
    assert "deploy" in _needs(job)
    assert "needs.deploy.result == 'success'" in _if(job)


def _after(jobs: dict, root: str) -> set[str]:
    """Every job that needs ``root``, directly or through another job."""
    out: set[str] = set()
    grew = True
    while grew:
        grew = False
        for name, job in jobs.items():
            if name not in out and ({root} | out) & set(_needs(job)):
                out.add(name)
                grew = True
    return out


def test_no_job_after_a_skipped_job_reads_a_bare_success(deploy: dict) -> None:
    """``success()`` is FALSE when any ancestor job SKIPPED. ``release-window``
    skips on every dispatch, and ``test`` skips on a ``skip_tests`` dispatch.
    So each job after them starts its ``if`` with ``always()`` and tests the
    result that it needs. A bare ``success()`` skipped the smoke test on the
    dispatch of 2026-10-29 (run 40888027282)."""
    jobs = deploy["jobs"]
    after = _after(jobs, "release-window") | _after(jobs, "test")
    assert {"publish-release", "deploy", "pull-delivery", "chat-smoke", "smoke"} <= after
    bare = sorted(n for n in after if not _if(jobs[n]).startswith("${{ always()"))
    assert not bare, f"these jobs skip when an earlier job skips: {bare}"


# ── 2. The schedule and the dispatch publish release, behind the tests ───────


def test_the_daily_schedule_exists(deploy: dict) -> None:
    crons = [c["cron"] for c in _on(deploy)["schedule"]]
    assert crons == ["30 21 * * *"], "21:30 UTC is 03:00 IST (spec §4.1)"


def test_a_dispatch_can_release_outside_the_clock(deploy: dict) -> None:
    inputs = _on(deploy)["workflow_dispatch"]["inputs"]
    assert {"reason", "skip_tests", "force", "reinstall"} <= set(inputs)


def test_the_tests_gate_the_release(deploy: dict) -> None:
    job = deploy["jobs"]["publish-release"]
    assert "test" in _needs(job)
    cond = _if(job)
    assert "needs.lint.result == 'success'" in cond
    assert "needs.test.result == 'success'" in cond
    # The one way past: a person's dispatch with skip_tests.
    assert (
        "(github.event_name == 'workflow_dispatch' && inputs.skip_tests "
        "&& needs.test.result == 'skipped')"
    ) in cond
    assert "github.ref == 'refs/heads/main'" in cond


def test_release_is_fast_forward_only(deploy: dict) -> None:
    body = "\n".join(s.get("run", "") for s in deploy["jobs"]["publish-release"]["steps"])
    assert 'git push origin "${GITHUB_SHA}:refs/heads/release"' in body
    assert "--force" not in body and " -f " not in body


# ── 3. The hold switch ───────────────────────────────────────────────────────


def test_a_hold_stops_the_schedule_and_not_a_dispatch(deploy: dict) -> None:
    cond = _if(deploy["jobs"]["publish-release"])
    assert "!(github.event_name == 'schedule' && vars.RELEASE_HOLD == '1')" in cond


def test_a_held_day_says_so(deploy: dict) -> None:
    job = deploy["jobs"]["release-hold"]
    assert _if(job) == "${{ github.event_name == 'schedule' && vars.RELEASE_HOLD == '1' }}"
    body = "\n".join(s.get("run", "") for s in job["steps"])
    assert "::warning" in body and "GITHUB_STEP_SUMMARY" in body


# ── 4. The deploy names its commit ───────────────────────────────────────────


def test_the_deploy_sends_its_own_sha(deploy: dict) -> None:
    raw = _DEPLOY.read_text(encoding="utf-8")
    assert '"DEPLOY_REF=${GITHUB_SHA} DEPLOY_FORCE=' in raw


# ── 5. A late schedule run waits a day ──────────────────────────────────────


def test_a_late_schedule_run_waits_a_day(deploy: dict) -> None:
    job = deploy["jobs"]["release-window"]
    assert _if(job) == (
        "${{ always() && github.event_name == 'schedule' && vars.RELEASE_HOLD != '1' }}"
    ), "the schedule only, and not on a held day, which release-hold reports"
    # After the tests, so the clock it reads is the clock of the restart.
    assert {"lint", "test"} <= set(_needs(job))
    assert job["outputs"]["open"] == "${{ steps.clock.outputs.open }}"
    # A renamed step id would leave `open` empty, and hold every night.
    assert [s.get("id") for s in job["steps"]] == ["clock"]
    publish = deploy["jobs"]["publish-release"]
    assert "release-window" in _needs(publish)
    # Empty reads as closed, so a failed window step holds the release.
    assert (
        "(github.event_name != 'schedule' || needs.release-window.outputs.open == 'true')"
    ) in _if(publish), "a dispatch never waits, and a schedule run waits outside the window"


def test_the_window_holds_the_planned_hour(deploy: dict) -> None:
    env = deploy["jobs"]["release-window"]["steps"][0]["env"]
    open_min, close_min = int(env["OPEN_MIN"]), int(env["CLOSE_MIN"])
    assert (open_min, close_min) == (21 * 60, 2 * 60), "21:00 to 02:00 UTC (spec §4.1a)"
    (cron,) = [c["cron"] for c in _on(deploy)["schedule"]]
    minute, hour = (int(p) for p in cron.split()[:2])
    planned = hour * 60 + minute
    # The window spans midnight. Move OPEN_MIN with the cron, or an on-time
    # run is held every night.
    assert planned >= open_min or planned < close_min, (
        "an on-time run must find the window open"
    )


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_gateway_drain.py.
        git_bash = pathlib.Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()


@pytest.mark.skipif(_BASH is None, reason="no bash on this machine")
@pytest.mark.parametrize(("clock", "attempt", "is_open"), [
    ("2059", "1", False), ("2100", "1", True), ("2130", "1", True), ("2359", "1", True),
    ("0000", "1", True), ("0159", "1", True), ("0200", "1", False),
    # 08 and 09 are not octal. 10# reads them as base 10.
    ("0806", "1", False), ("0959", "1", False), ("1200", "1", False),
    # A person's rerun of a held run releases, as a dispatch does.
    ("1000", "2", True), ("0806", "3", True),
])
def test_the_window_reads_the_clock(deploy: dict, tmp_path: pathlib.Path,
                                    clock: str, attempt: str, is_open: bool) -> None:
    step = deploy["jobs"]["release-window"]["steps"][0]
    assert 'hm="$(date -u +%H%M)"' in step["run"], "it reads the real clock"
    out, summary = tmp_path / "out", tmp_path / "summary"
    # A shell function shadows `date`, so the step's own text runs unchanged.
    script = f"date() {{ echo {clock}; }}\n" + step["run"]
    # GITHUB_RUN_ATTEMPT is pinned: a rerun of CI itself sets it to 2.
    env = {**os.environ, **step["env"], "GITHUB_RUN_ATTEMPT": attempt,
           "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}
    done = subprocess.run([_BASH, "-c", script], env=env, capture_output=True,
                          text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert out.read_text(encoding="utf-8").strip() == f"open={'true' if is_open else 'false'}"
    if is_open:
        assert not summary.exists()
    else:
        assert "::warning title=Release waits a day::" in done.stdout
        assert "Release WAITS" in summary.read_text(encoding="utf-8")


# ── The hourly delivery check reads release ──────────────────────────────────


def test_the_health_check_compares_with_release_not_main() -> None:
    job = _load(_HEALTH)["jobs"]["delivery"]
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert 'want="$(git rev-parse origin/release)"' in run
    assert 'want="$(git rev-parse HEAD)"' not in run, (
        "HEAD is main. Under the daily clock main is ahead by design, and the "
        "check would fail every day."
    )
    # It also notices a daily release that did not run.
    assert "origin/release..origin/main" in run
    assert "-ge 30" in run
    assert job["permissions"]["actions"] == "read"


def test_a_stale_rerun_does_not_roll_production_back(deploy: dict) -> None:
    steps = deploy["jobs"]["deploy"]["steps"]
    ids = [s.get("id") for s in steps]
    assert ids.index("stale") < ids.index("deploy"), "check before the ssh opens"
    stale = steps[ids.index("stale")]["run"]
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" "$rel"' in stale
    assert steps[ids.index("deploy")]["if"] == "${{ steps.stale.outputs.stale != 'true' }}"
