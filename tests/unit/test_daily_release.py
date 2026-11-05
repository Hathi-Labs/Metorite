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
   after 02:00 UTC waits a day, and says so (spec §4.1a). A plain dispatch
   never waits.
6. CIV-13 (D178): the home box dispatches with ``nightly=true`` at 21:30 UTC.
   A nightly dispatch is a nightly run: ``RELEASE_HOLD`` holds it, the window
   holds it, and it cannot skip the tests. A night ships once. A nightly run
   does not publish when another nightly run published since 21:00 UTC.

And in ``.github/workflows/vps-health.yml``: the hourly delivery check
compares the box with ``release``, not with ``main``. Under the daily clock a
comparison with ``main`` fails every day by design.

The behaviour of ``vps_apply.sh`` with a named commit is fenced next door,
in ``test_deploy_target_ref.py``.
"""

from __future__ import annotations

import os
import pathlib
import re
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
    skips on every plain dispatch, and ``test`` skips on a ``skip_tests`` dispatch.
    So each job after them starts its ``if`` with ``always()`` and tests the
    result that it needs. A bare ``success()`` skipped the smoke test on the
    dispatch of 2026-10-29 (run 40888027282)."""
    jobs = deploy["jobs"]
    after = _after(jobs, "release-window") | _after(jobs, "test")
    assert {"publish-release", "deploy", "pull-delivery", "chat-smoke", "smoke"} <= after
    bare = sorted(n for n in after if not _if(jobs[n]).startswith("${{ always()"))
    assert not bare, f"these jobs skip when an earlier job skips: {bare}"
    # `always()` alone also runs after a FAILED job. So each job reads the
    # result that it needs. `release-window` only reads the clock, and
    # `publish-release` reads `needs.test.result` for it.
    blind = sorted(
        n for n in after - {"release-window"}
        if not re.search(r"needs\.[a-z-]+\.result ==", _if(jobs[n]))
    )
    assert not blind, f"these jobs run after a failed job: {blind}"


# ── 2. The schedule and the dispatch publish release, behind the tests ───────


def test_the_daily_schedule_exists(deploy: dict) -> None:
    crons = [c["cron"] for c in _on(deploy)["schedule"]]
    assert crons == ["30 21 * * *"], "21:30 UTC is 03:00 IST (spec §4.1)"


def test_a_dispatch_can_release_outside_the_clock(deploy: dict) -> None:
    inputs = _on(deploy)["workflow_dispatch"]["inputs"]
    assert {"reason", "skip_tests", "force", "reinstall", "nightly"} <= set(inputs)


def test_the_nightly_input_is_a_boolean_off_by_default(deploy: dict) -> None:
    """CIV-13: a person's dispatch stays a plain dispatch unless it asks."""
    nightly = _on(deploy)["workflow_dispatch"]["inputs"]["nightly"]
    assert nightly["type"] == "boolean"
    assert nightly["default"] is False


def test_the_tests_gate_the_release(deploy: dict) -> None:
    job = deploy["jobs"]["publish-release"]
    assert "test" in _needs(job)
    cond = _if(job)
    assert "needs.lint.result == 'success'" in cond
    assert "needs.test.result == 'success'" in cond
    # The one way past: a person's PLAIN dispatch with skip_tests. A nightly
    # dispatch with skip_tests does not publish (CIV-13).
    assert (
        "(github.event_name == 'workflow_dispatch' && inputs.skip_tests "
        "&& !inputs.nightly && needs.test.result == 'skipped')"
    ) in cond
    assert "github.ref == 'refs/heads/main'" in cond


def test_release_is_fast_forward_only(deploy: dict) -> None:
    body = "\n".join(s.get("run", "") for s in deploy["jobs"]["publish-release"]["steps"])
    assert 'git push origin "${GITHUB_SHA}:refs/heads/release"' in body
    assert "--force" not in body and " -f " not in body


# ── 3. The hold switch ───────────────────────────────────────────────────────


def test_a_hold_stops_a_nightly_run_and_not_a_plain_dispatch(deploy: dict) -> None:
    cond = _if(deploy["jobs"]["publish-release"])
    assert (
        "!((github.event_name == 'schedule' || inputs.nightly) && vars.RELEASE_HOLD == '1')"
    ) in cond


def test_a_held_day_says_so(deploy: dict) -> None:
    job = deploy["jobs"]["release-hold"]
    assert _if(job) == (
        "${{ (github.event_name == 'schedule' || inputs.nightly) && vars.RELEASE_HOLD == '1' }}"
    )
    body = "\n".join(s.get("run", "") for s in job["steps"])
    assert "::warning" in body and "GITHUB_STEP_SUMMARY" in body


# ── 4. The deploy names its commit ───────────────────────────────────────────


def test_the_deploy_sends_its_own_sha(deploy: dict) -> None:
    raw = _DEPLOY.read_text(encoding="utf-8")
    assert '"DEPLOY_REF=${GITHUB_SHA} DEPLOY_FORCE=' in raw


# ── 5. A late schedule run waits a day ──────────────────────────────────────


def test_a_late_nightly_run_waits_a_day(deploy: dict) -> None:
    job = deploy["jobs"]["release-window"]
    assert _if(job) == (
        "${{ always() && (github.event_name == 'schedule' || inputs.nightly) "
        "&& vars.RELEASE_HOLD != '1' }}"
    ), "nightly runs only, and not on a held day, which release-hold reports"
    # After the tests, so the clock it reads is the clock of the restart.
    assert {"lint", "test"} <= set(_needs(job))
    # `once` writes `open` when the clock is open. A crash of `once` before
    # it writes falls back to the clock: the release ships (fail open, CIV-13).
    assert job["outputs"]["open"] == "${{ steps.once.outputs.open || steps.clock.outputs.open }}"
    # A renamed step id would leave `open` empty, and hold every night.
    assert [s.get("id") for s in job["steps"]] == ["clock", "once"]
    publish = deploy["jobs"]["publish-release"]
    assert "release-window" in _needs(publish)
    # Empty reads as closed, so a failed clock step holds the release.
    assert (
        "(!(github.event_name == 'schedule' || inputs.nightly) "
        "|| needs.release-window.outputs.open == 'true')"
    ) in _if(publish), "a plain dispatch never waits, and a nightly run waits outside the window"


def test_only_the_window_job_reads_the_actions_api_for_the_dedupe(deploy: dict) -> None:
    job = deploy["jobs"]["release-window"]
    assert job["permissions"] == {"actions": "read"}, "read only, and nothing else"
    once = job["steps"][1]
    assert once["if"] == "${{ steps.clock.outputs.open == 'true' }}"
    assert once["env"]["GH_TOKEN"] == "${{ github.token }}"
    # The jobs API names a job by its `name:`. A renamed job would never match.
    assert once["env"]["PUBLISH_JOB"] == deploy["jobs"]["publish-release"]["name"]


def test_the_run_name_marks_a_nightly_dispatch(deploy: dict) -> None:
    """The runs API does not return the inputs. So a nightly dispatch says
    "nightly" in its run name, and the dedupe and the box script read that.
    A push keeps its commit message, and every other run keeps "deploy"."""
    assert deploy["run-name"] == (
        "${{ inputs.nightly && 'deploy (nightly)' "
        "|| github.event_name == 'push' && github.event.head_commit.message "
        "|| 'deploy' }}"
    )


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


# ── 6. Each kind of run, through the release conditions (CIV-13) ─────────────
#
# A small evaluator for the three `if` lines. It reads only `&&`, `||`, `!`,
# brackets, `==`, `!=`, names and quoted strings. That is all those lines use.
# It follows GitHub's rules: `&&` and `||` give an operand, not a boolean,
# and a missing input is null, which is falsy.

_TOKEN = re.compile(r"\s*(\(|\)|&&|\|\||!=|==|!|'[^']*'|[A-Za-z_][\w.\-]*(?:\(\))?)")


def _tokens(expr: str) -> list[str]:
    out, pos = [], 0
    expr = expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        assert m, f"cannot read {expr[pos:]!r}"
        out.append(m.group(1))
        pos = m.end()
    return out


def _eval(expr: str, ctx: dict) -> object:
    expr = expr.strip()
    assert expr.startswith("${{") and expr.endswith("}}")
    toks = _tokens(expr[3:-2])
    pos = 0

    def peek() -> str | None:
        return toks[pos] if pos < len(toks) else None

    def take() -> str:
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def primary() -> object:
        tok = take()
        if tok == "(":
            val = or_()
            assert take() == ")"
            return val
        if tok == "!":
            return not primary()
        if tok.startswith("'"):
            return tok[1:-1]
        if tok in ("always()", "success()"):
            return True
        if tok in ("true", "false"):
            return tok == "true"
        return ctx.get(tok)

    def cmp() -> object:
        left = primary()
        while peek() in ("==", "!="):
            op = take()
            right = primary()
            left = (left == right) if op == "==" else (left != right)
        return left

    def and_() -> object:
        left = cmp()
        while peek() == "&&":
            take()
            right = cmp()
            left = right if left else left
        return left

    def or_() -> object:
        left = and_()
        while peek() == "||":
            take()
            right = and_()
            left = left if left else right
        return left

    val = or_()
    assert pos == len(toks), f"unread tokens: {toks[pos:]}"
    return val


def _ctx(event: str, *, nightly: bool | None = None, skip_tests: bool | None = None,
         hold: str = "", test: str = "success", window_open: str = "") -> dict:
    return {
        "github.event_name": event,
        "github.ref": "refs/heads/main",
        "inputs.nightly": nightly,
        "inputs.skip_tests": skip_tests,
        "vars.RELEASE_HOLD": hold,
        "needs.lint.result": "success",
        "needs.test.result": test,
        "needs.release-window.outputs.open": window_open,
    }


def _runs(deploy: dict, job: str, ctx: dict) -> bool:
    return bool(_eval(str(deploy["jobs"][job]["if"]), ctx))


def test_a_plain_dispatch_is_not_held(deploy: dict) -> None:
    """A plain dispatch behaves as before CIV-13: no hold, no window."""
    ctx = _ctx("workflow_dispatch", nightly=False, skip_tests=False, hold="1")
    assert not _runs(deploy, "release-hold", ctx)
    assert not _runs(deploy, "release-window", ctx)
    assert _runs(deploy, "publish-release", ctx), "RELEASE_HOLD does not stop a person"
    # With skip_tests the test job skips, and a plain dispatch still publishes.
    ctx = _ctx("workflow_dispatch", nightly=False, skip_tests=True, test="skipped")
    assert _runs(deploy, "publish-release", ctx)


def test_a_nightly_dispatch_is_held_by_release_hold(deploy: dict) -> None:
    ctx = _ctx("workflow_dispatch", nightly=True, hold="1", window_open="true")
    assert _runs(deploy, "release-hold", ctx)
    assert not _runs(deploy, "release-window", ctx)
    assert not _runs(deploy, "publish-release", ctx)


def test_a_nightly_dispatch_obeys_the_window(deploy: dict) -> None:
    ctx = _ctx("workflow_dispatch", nightly=True)
    assert _runs(deploy, "release-window", ctx)
    for shut in ("", "false"):
        assert not _runs(deploy, "publish-release", {**ctx, "needs.release-window.outputs.open": shut})
    assert _runs(deploy, "publish-release", {**ctx, "needs.release-window.outputs.open": "true"})


def test_a_nightly_dispatch_cannot_skip_the_tests(deploy: dict) -> None:
    ctx = _ctx("workflow_dispatch", nightly=True, skip_tests=True, test="skipped",
               window_open="true")
    assert not _runs(deploy, "publish-release", ctx)


def test_the_schedule_still_releases_as_before(deploy: dict) -> None:
    ctx = _ctx("schedule", window_open="true")
    assert _runs(deploy, "release-window", ctx)
    assert _runs(deploy, "publish-release", ctx)
    assert not _runs(deploy, "publish-release", {**ctx, "needs.release-window.outputs.open": "false"})
    held = _ctx("schedule", hold="1", window_open="true")
    assert _runs(deploy, "release-hold", held)
    assert not _runs(deploy, "publish-release", held)


def test_a_push_never_releases_or_reads_the_window(deploy: dict) -> None:
    ctx = _ctx("push", window_open="true")
    for job in ("release-hold", "release-window", "publish-release"):
        assert not _runs(deploy, job, ctx), job


@pytest.mark.parametrize(("ctx", "title"), [
    ({"inputs.nightly": True, "github.event_name": "workflow_dispatch"}, "deploy (nightly)"),
    ({"inputs.nightly": False, "github.event_name": "workflow_dispatch"}, "deploy"),
    ({"github.event_name": "schedule"}, "deploy"),
    ({"github.event_name": "push", "github.event.head_commit.message": "WS-1: a fix (#9)"},
     "WS-1: a fix (#9)"),
])
def test_the_run_name_of_each_kind_of_run(deploy: dict, ctx: dict, title: str) -> None:
    assert _eval(deploy["run-name"], ctx) == title


# ── 7. A night ships once (CIV-13) ──────────────────────────────────────────

_REPO_ENV = {"GITHUB_REPOSITORY": "Hathi-Labs/Metorite", "GITHUB_RUN_ID": "500",
             "GITHUB_SERVER_URL": "https://github.com"}

# A fake `gh`. It writes each call to calls.log, and it answers from files in
# FAKE_DIR. FAKE_FAIL=1 makes every call fail, as a down API does.
_FAKE_GH = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/calls.log"
[ "${FAKE_FAIL:-0}" = 1 ] && { echo '{"message":"Server Error"}'; exit 1; }
url="$2"
case "$url" in
  *event=schedule*) f="$FAKE_DIR/schedule.json" ;;
  *event=workflow_dispatch*) f="$FAKE_DIR/dispatch.json" ;;
  */jobs*) id="${url#*/runs/}"; f="$FAKE_DIR/jobs_${id%%/*}.json" ;;
  *) exit 1 ;;
esac
[ -f "$f" ] || { echo '{"message":"Not Found"}'; exit 1; }
cat "$f"
"""


def _write_json(path: pathlib.Path, doc: object) -> None:
    import json
    path.write_text(json.dumps(doc), encoding="utf-8")


def _run_once(deploy: dict, tmp_path: pathlib.Path, *, now: str = "2026-11-05T22:40:00Z",
              attempt: str = "1", schedule: list | None = None, dispatch: list | None = None,
              jobs: dict | None = None, fail: bool = False) -> tuple[str, str, str, list[str]]:
    step = deploy["jobs"]["release-window"]["steps"][1]
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    gh = fake / "bin" / "gh"
    gh.write_text(_FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    _write_json(fake / "schedule.json", {"workflow_runs": schedule or []})
    _write_json(fake / "dispatch.json", {"workflow_runs": dispatch or []})
    for run_id, run_jobs in (jobs or {}).items():
        _write_json(fake / f"jobs_{run_id}.json", {"jobs": run_jobs})
    out, summary = tmp_path / "out", tmp_path / "summary"
    script = f"date() {{ echo {now}; }}\n" + step["run"]
    env = {**os.environ, **_REPO_ENV, "PUBLISH_JOB": step["env"]["PUBLISH_JOB"],
           "GITHUB_RUN_ATTEMPT": attempt, "FAKE_DIR": str(fake),
           "FAKE_FAIL": "1" if fail else "0",
           "PATH": f"{fake / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
           "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}
    done = subprocess.run([_BASH, "-c", script], env=env, capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    log = fake / "calls.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return out.read_text(encoding="utf-8").strip(), done.stdout, text, calls


def _publish(conclusion: str | None) -> list[dict]:
    return [{"name": "Lint & type-check", "conclusion": "success"},
            {"name": "Publish release ref", "conclusion": conclusion}]


_SCHEDULE_RUN = {"id": 401, "event": "schedule", "display_title": "deploy"}
_NIGHTLY_RUN = {"id": 402, "event": "workflow_dispatch", "display_title": "deploy (nightly)"}
# Run 41969146897: the owner's plain dispatch of 2026-11-05. It published,
# and it must never count as a nightly release.
_PLAIN_RUN = {"id": 41969146897, "event": "workflow_dispatch", "display_title": "deploy"}
_SELF_RUN = {"id": 500, "event": "workflow_dispatch", "display_title": "deploy (nightly)"}

needs_bash = pytest.mark.skipif(_BASH is None, reason="no bash on this machine")


@needs_bash
def test_once_ships_when_no_nightly_run_shipped(deploy: dict, tmp_path: pathlib.Path) -> None:
    open_, stdout, summary, calls = _run_once(
        deploy, tmp_path, schedule=[_SCHEDULE_RUN], dispatch=[_SELF_RUN],
        jobs={"401": _publish("skipped")})
    assert open_ == "open=true"
    assert "::warning" not in stdout and not summary
    # It reads the window of tonight, and never its own run.
    assert any("created=>=2026-11-05T21:00:00Z" in c for c in calls)
    assert not any("/runs/500/" in c for c in calls)


@needs_bash
def test_once_holds_when_a_nightly_dispatch_shipped(deploy: dict, tmp_path: pathlib.Path) -> None:
    open_, stdout, summary, _ = _run_once(
        deploy, tmp_path, dispatch=[_SELF_RUN, _NIGHTLY_RUN],
        jobs={"402": _publish("success")})
    assert open_ == "open=false"
    assert "::notice title=Release shipped tonight::" in stdout
    assert "Release SKIPPED" in summary and "/actions/runs/402" in summary


@needs_bash
def test_once_holds_when_the_schedule_run_shipped(deploy: dict, tmp_path: pathlib.Path) -> None:
    open_, _, _, _ = _run_once(deploy, tmp_path, schedule=[_SCHEDULE_RUN],
                               jobs={"401": _publish("success")})
    assert open_ == "open=false"


@needs_bash
def test_once_ships_after_an_earlier_failure(deploy: dict, tmp_path: pathlib.Path) -> None:
    open_, stdout, _, _ = _run_once(
        deploy, tmp_path, schedule=[_SCHEDULE_RUN], dispatch=[_NIGHTLY_RUN],
        jobs={"401": _publish("failure"), "402": _publish("cancelled")})
    assert open_ == "open=true"
    assert "::warning" not in stdout


@needs_bash
def test_a_plain_dispatch_never_counts(deploy: dict, tmp_path: pathlib.Path) -> None:
    open_, _, _, calls = _run_once(deploy, tmp_path, dispatch=[_PLAIN_RUN],
                                   jobs={"41969146897": _publish("success")})
    assert open_ == "open=true"
    assert not any("/runs/41969146897/" in c for c in calls), "it never reads its jobs"


@needs_bash
def test_once_ships_with_a_warning_when_the_api_fails(deploy: dict, tmp_path: pathlib.Path) -> None:
    """FAIL OPEN, on purpose. A second ship of one night is one more apply of
    the same or a newer commit. A missed night is a day of merges held back."""
    open_, stdout, _, calls = _run_once(deploy, tmp_path, fail=True)
    assert open_ == "open=true"
    assert "::warning title=Release may ship twice::" in stdout
    assert calls, "it tried the API"


@needs_bash
def test_once_ships_with_a_warning_when_the_jobs_api_fails(deploy: dict, tmp_path: pathlib.Path) -> None:
    # No jobs file for run 402, so the fake answers 404.
    open_, stdout, _, _ = _run_once(deploy, tmp_path, dispatch=[_NIGHTLY_RUN])
    assert open_ == "open=true"
    assert "::warning title=Release may ship twice::" in stdout


@needs_bash
def test_a_rerun_skips_the_check(deploy: dict, tmp_path: pathlib.Path) -> None:
    open_, _, _, calls = _run_once(deploy, tmp_path, attempt="2", schedule=[_SCHEDULE_RUN],
                                   jobs={"401": _publish("success")})
    assert open_ == "open=true"
    assert calls == [], "a person's rerun does not ask the API"


@needs_bash
@pytest.mark.parametrize(("now", "since"), [
    ("2026-11-05T21:00:00Z", "2026-11-05T21:00:00Z"),
    ("2026-11-05T23:59:59Z", "2026-11-05T21:00:00Z"),
    # After midnight the window opened on the day before.
    ("2026-11-06T00:00:00Z", "2026-11-05T21:00:00Z"),
    ("2026-11-06T01:59:00Z", "2026-11-05T21:00:00Z"),
    ("2026-12-01T01:30:00Z", "2026-11-30T21:00:00Z"),
    ("2027-01-01T00:10:00Z", "2026-12-31T21:00:00Z"),
])
def test_once_looks_back_to_the_window_open(deploy: dict, tmp_path: pathlib.Path,
                                            now: str, since: str) -> None:
    _, _, _, calls = _run_once(deploy, tmp_path, now=now)
    runs = [c for c in calls if "/workflows/deploy.yml/runs?" in c]
    assert len(runs) == 2, "one list for the schedule, one for the dispatch"
    assert all(f"created=>={since}&" in c for c in runs), runs


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
