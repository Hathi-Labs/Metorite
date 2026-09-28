"""H-142 — the deploy rides out an ssh blip, and a pull-path delivery counts
only on served evidence.

Measured 2026-09-20 to 2026-09-28, over 200 deploy runs: 9 runs went red on
``ssh exited 255`` / ``Connection timed out``, and each lost ALL THREE rounds,
27 of 27 connects. The 163 green runs hold zero 255 rounds. The box served the
commit anyway in all 9 cases, mostly through ``acb-pull.timer``. So the red
said "the release failed" when only the CONNECTION had failed.

Two halves, and both have a structural and a behavioural side:

  * ``wait_for_ssh`` probes with backoff inside ONE connect budget, and a
    connect failure never spends an apply round.
  * ``await_pull_delivery`` goes green only when ``/version`` shows the gateway
    on this commit AND the box's deploy marker (``applied_sha``) names it, and
    the workbench answers. It goes red when the box does not serve it.

The behavioural cases run the real shell against a fake ``ssh``, ``curl`` and
``sleep`` on PATH. They run the deploy STEP itself, lifted out of the YAML,
under ``bash -e -o pipefail`` as GitHub runs it.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
LIB = ROOT / "scripts" / "ci_deploy_reach.sh"
APPLY = ROOT / "scripts" / "vps_apply.sh"


def _read(p: Path) -> str:
    return io.open(p, encoding="utf-8").read()


@pytest.fixture(scope="module")
def raw() -> str:
    return _read(WORKFLOW)


@pytest.fixture(scope="module")
def jobs(raw: str) -> dict:
    return yaml.safe_load(raw)["jobs"]


def _deploy_step(jobs: dict) -> str:
    return next(s for s in jobs["deploy"]["steps"] if s.get("id") == "deploy")["run"]


# ── Structural ──────────────────────────────────────────────────────────────


class TestTheConnectRetry:
    def test_the_lib_is_sourced_by_the_deploy_step(self, jobs: dict) -> None:
        assert ". scripts/ci_deploy_reach.sh" in _deploy_step(jobs)

    def test_a_probe_runs_before_every_round(self, jobs: dict) -> None:
        """The probe comes BEFORE the round counter moves, so a connect that
        fails spends connect budget and never a round."""
        step = _deploy_step(jobs)
        loop = step[step.index('while [ "$round" -lt 3 ]'):]
        probe = loop.index("wait_for_ssh || wrc=$?")
        bump = loop.index("round=$((round + 1))")
        assert probe < bump, "wait_for_ssh must run before the round counter moves"

    def test_the_connect_budget_is_bounded(self) -> None:
        """A few minutes, not an hour. The job's 120-minute backstop is sized
        from 3 rounds plus this budget."""
        body = _read(LIB)
        assert ': "${CONNECT_BUDGET:=300}"' in body
        assert ': "${CONNECT_BACKOFF_MAX:=60}"' in body
        assert "connect_budget_left" in body

    def test_the_probe_is_a_no_op_with_the_same_options(self) -> None:
        body = _read(LIB)
        assert 'ssh "${CI_SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" true' in body
        assert "ConnectTimeout=" in body

    def test_the_apply_uses_the_same_options(self, jobs: dict) -> None:
        """One option set. A probe that connects with other options proves
        nothing about the apply."""
        step = _deploy_step(jobs)
        assert 'timeout -k 30 1800 ssh "${CI_SSH_OPTS[@]}"' in step
        assert "-o ConnectTimeout=30" not in step

    def test_a_dropped_connect_is_refunded_not_counted(self, jobs: dict) -> None:
        step = _deploy_step(jobs)
        refund = step[step.index("! apply_started /tmp/apply.log"):]
        refund = refund[: refund.index("continue")]
        assert "ssh_network_fault < /tmp/apply.log" in refund
        assert "round=$((round - 1))" in refund
        assert "charge_connect" in refund, (
            "a refunded round must be charged to the connect budget, or a "
            "probe that works and an apply that cannot connect loop for ever"
        )
        assert "connect_budget_left || no_connect" in refund

    def test_a_non_network_ssh_failure_goes_red_at_once(self, jobs: dict) -> None:
        step = _deploy_step(jobs)
        assert 'if [ "$wrc" = 2 ]; then' in step
        assert "ssh_network_fault" in _read(LIB)
        assert "permission denied" not in _read(LIB).lower(), (
            "an auth failure must NOT be classed as a network fault"
        )

    def test_the_apply_lock_is_unchanged(self, jobs: dict) -> None:
        """#484: the CI apply still runs vps_apply.sh, which takes the lock."""
        step = _deploy_step(jobs)
        assert "cp scripts/vps_apply.sh /tmp/deploy_remote.sh" in step
        assert "bash -s\" < /tmp/deploy_remote.sh" in step
        assert 'deploy_lock_acquire "${DEPLOY_LOCK_MODE:-wait}"' in _read(APPLY)


class TestThreeFailuresReadDifferently:
    """H-142's own Check: a distinct message when ssh never connects."""

    def test_the_start_line_is_the_apply_scripts_first_output(self) -> None:
        lib = _read(LIB)
        assert 'APPLY_START_LINE="==> Taking the deploy lock"' in lib
        first = next(
            ln.strip() for ln in _read(APPLY).splitlines()
            if ln.startswith("echo ")
        )
        assert first.startswith('echo "==> Taking the deploy lock'), (
            f"vps_apply.sh's first top-level echo is {first!r}. The workflow "
            "reads the absence of the lock line as 'the apply never started'."
        )

    def test_the_lock_busy_line_matches_the_apply_script(self) -> None:
        lib = _read(LIB)
        line = "and it is still busy. This round did NOTHING"
        assert f'APPLY_LOCK_BUSY_LINE="{line}"' in lib
        assert line in _read(APPLY)

    def test_run_deploy_names_each_case(self, jobs: dict) -> None:
        step = _deploy_step(jobs)
        never = step.index("THE APPLY NEVER STARTED")
        busy = step.index("NOTHING WAS APPLIED")
        half = step.index("THE APPLY DID NOT REACH ITS FINAL LINE")
        assert never < half and busy < half, (
            "the two nothing-applied cases must be tested BEFORE the "
            "half-applied message, or they still print it"
        )


class TestThePullPathFallback:
    def test_the_deploy_job_exports_its_reach(self, jobs: dict) -> None:
        assert jobs["deploy"]["outputs"]["reach"] == "${{ steps.deploy.outputs.reach }}"
        assert 'echo "reach=unreachable" >> "$GITHUB_OUTPUT"' in _deploy_step(jobs)

    def test_a_forced_apply_is_never_handed_off(self, jobs: dict) -> None:
        """The pull timer skips a sha it already applied. A forced rerun after
        an .env edit would then read green on a delivery from BEFORE the edit."""
        step = _deploy_step(jobs)
        fn = step[step.index("no_connect() {"):]
        fn = fn[: fn.index("hand_off_to_pull_path\n")]
        assert '"${DEPLOY_FORCE:-0}" = "1"' in fn
        assert "exit 1" in fn

    def test_the_fallback_runs_on_a_fresh_runner(self, jobs: dict) -> None:
        job = jobs["pull-delivery"]
        assert job["needs"] == "deploy"
        cond = " ".join(job["if"].split())
        assert "needs.deploy.outputs.reach == 'unreachable'" in cond
        assert "needs.deploy.result == 'success'" in cond
        assert cond.startswith("${{ always()"), (
            "without always(), a skipped `test` job (the skip_tests dispatch) "
            "skips this job too, and a hand-off ends the run GREEN unproved"
        )
        assert job["steps"][0]["with"]["fetch-depth"] == 0, (
            "the ancestry test needs full history"
        )
        assert job["timeout-minutes"] <= 30

    def test_the_fallback_goes_red_unless_proved(self, jobs: dict) -> None:
        run = jobs["pull-delivery"]["steps"][1]["run"]
        assert 'await_pull_delivery "$GITHUB_SHA" || rc=$?' in run
        green = run[run.index("0)"):run.index("3)")]
        assert "exit 0" in green and "::warning" in green
        rest = run[run.index("3)"):]
        assert rest.count("exit 1") == 2, "every other result must go red"

    def test_the_proof_needs_the_gateway_and_the_marker_and_the_web(self) -> None:
        body = _read(LIB)
        fn = body[body.index("await_pull_delivery() {"):]
        cond = fn[fn.index("if commit_contains"):fn.index("; then")]
        assert 'commit_contains "$sha" "$want"' in cond
        assert 'commit_contains "$applied" "$want"' in cond, (
            "without applied_sha the check proves the GATEWAY only. The gateway "
            "restarts before the workbench builds, so a failed build would pass."
        )
        assert "^[23]" in cond

    def test_the_gateway_reads_the_marker_the_apply_writes(self) -> None:
        apply = _read(APPLY)
        assert 'DEPLOY_MARKER="${DEPLOY_MARKER:-$(dirname "$APP_DIR")/acb-deploy.applied}"' in apply
        info = _read(ROOT / "apps" / "services" / "gateway" / "gateway" / "build_info.py")
        assert 'root.parent / "acb-deploy.applied"' in info
        main = _read(ROOT / "apps" / "services" / "gateway" / "gateway" / "main.py")
        assert "applied_sha=applied_sha" in main

    def test_no_literal_backslash_n_is_left_in_the_step(self, raw: str) -> None:
        """deployed_sha() held two, from a quoting trap. Bash read each as an
        extra argument "n"."""
        assert "2>/dev/null \\n" not in raw
        assert "\\n              |" not in raw


# ── Behavioural ─────────────────────────────────────────────────────────────

# Linux only, like the process tests in test_deploy_serialize.py. On Windows a
# fake on PATH loses to Git Bash's own ssh and sleep, and the real ones wait.
# CI runs these on ubuntu-latest.
_TOOLS = sys.platform.startswith("linux") and all(
    shutil.which(t) for t in ("bash", "git", "timeout")
)
needs_shell = pytest.mark.skipif(not _TOOLS, reason="needs Linux with bash, git and timeout")

FAKE_SSH = r"""#!/usr/bin/env bash
# Probe (`... true`) and apply (`... bash -s`) are told apart by the last arg.
n=$(cat "$FAKE/ssh_n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE/ssh_n"
last="${!#}"
if [ "$last" = "true" ]; then
  echo probe >> "$FAKE/calls"
  p=$(cat "$FAKE/probe_n" 2>/dev/null || echo 0); p=$((p + 1)); echo "$p" > "$FAKE/probe_n"
  mode="${PROBE_MODE:-ok}"
  [ "$mode" = stall ] && exit 124   # what `timeout` returns when it kills a stalled probe
  if [ "$mode" = auth ]; then echo "acb@host: Permission denied (publickey)." >&2; exit 255; fi
  if [ "$mode" = down ] || [ "$p" -le "${PROBE_FAILS:-0}" ]; then
    echo "ssh: connect to host 10.0.0.1 port 22: Connection timed out" >&2; exit 255
  fi
  exit 0
fi
echo apply >> "$FAKE/calls"
cat > /dev/null
if [ "${APPLY_MODE:-ok}" = drop ]; then
  echo "ssh: connect to host 10.0.0.1 port 22: Connection timed out" >&2; exit 255
fi
echo "==> Taking the deploy lock (/opt/acb/acb-deploy.lock)"
echo "==> Deployment complete"
exit 0
"""

FAKE_CURL = r"""#!/usr/bin/env bash
[ "${CURL_MODE:-ok}" = down ] && { case "$*" in *http_code*) printf 000;; esac; exit 7; }
case "$*" in
  *http_code*) printf '%s' "${WB_CODE:-200}"; exit 0 ;;
  */health*) echo '{"status":"ok"}'; exit 0 ;;
  */version*)
    n=$(cat "$FAKE/v_n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE/v_n"
    i=$n; while [ ! -f "$FAKE/version.$i" ] && [ "$i" -gt 1 ]; do i=$((i - 1)); done
    cat "$FAKE/version.$i"; exit 0 ;;
esac
exit 0
"""

FAKE_SLEEP = """#!/usr/bin/env bash
echo "$1" >> "$FAKE/slept"
"""


@pytest.fixture()
def box(tmp_path: Path):
    """A fake world: bin/ with ssh, curl, sleep and python3, and a git repo
    with three commits OLD -> WANT -> NEWER."""
    fake = tmp_path / "fake"
    binr = tmp_path / "bin"
    fake.mkdir()
    binr.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("curl", FAKE_CURL), ("sleep", FAKE_SLEEP)):
        p = binr / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    py = binr / "python3"
    py.write_text(
        f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
        encoding="utf-8", newline="\n",
    )
    py.chmod(0o755)

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(LIB, repo / "scripts" / LIB.name)
    shutil.copy(APPLY, repo / "scripts" / APPLY.name)
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    shas = {}
    for name in ("old", "want", "newer"):
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", name], check=True)
        shas[name] = subprocess.run(
            [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    home = tmp_path / "home"
    home.mkdir()
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8")

    def env(**extra: str) -> dict:
        e = dict(os.environ)
        e.update(
            PATH=f"{binr}{os.pathsep}{e.get('PATH', '')}",
            FAKE=fake.as_posix(), HOME=home.as_posix(),
            GITHUB_SHA=shas["want"], GITHUB_OUTPUT=out.as_posix(),
            GITHUB_STEP_SUMMARY=(tmp_path / "summary").as_posix(),
            GATEWAY_URL="https://gw.test", WORKBENCH_URL="https://wb.test",
            SSH_HOST="10.0.0.1", SSH_USER="acb", SSH_PORT="22", SSH_KEY="k",
            DEPLOY_FORCE="0", DEPLOY_REINSTALL="0",
            CONNECT_BUDGET="120", PULL_WAIT="60", PULL_POLL="30",
        )
        e.update(extra)
        return e

    def version(n: int, sha: str | None, applied: str | None) -> None:
        body = '{"sha": %s, "env": "prod", "applied_sha": %s}' % (
            f'"{sha}"' if sha else "null", f'"{applied}"' if applied else "null")
        (fake / f"version.{n}").write_text(body, encoding="utf-8", newline="\n")

    class Box:
        pass

    b = Box()
    b.fake, b.repo, b.shas, b.env, b.version, b.out = fake, repo, shas, env, version, out
    return b


def _count(box, name: str) -> list[str]:
    p = box.fake / name
    return p.read_text(encoding="utf-8").split() if p.exists() else []


def _lib(box, script: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f". scripts/ci_deploy_reach.sh\n{script}"],
        cwd=box.repo, env=box.env(**env), capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )


def _step(box, jobs: dict, **env: str) -> subprocess.CompletedProcess:
    step = box.repo / "step.sh"
    step.write_text(_deploy_step(jobs), encoding="utf-8", newline="\n")
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(step.name)],
        cwd=box.repo, env=box.env(**env), capture_output=True, text=True,
        encoding="utf-8", timeout=180,
    )


@needs_shell
class TestWaitForSshBehaviour:
    def test_a_blip_that_ends_is_ridden_out(self, box) -> None:
        r = _lib(box, 'wait_for_ssh; echo "rc=$?"', PROBE_FAILS="3")
        assert "rc=0" in r.stdout, r.stdout + r.stderr
        assert _count(box, "calls") == ["probe"] * 4
        assert _count(box, "slept") == ["10", "20", "40"], "backoff must double"

    def test_a_blip_that_does_not_end_is_bounded(self, box) -> None:
        r = _lib(box, 'wait_for_ssh; echo "rc=$?"', PROBE_MODE="down", CONNECT_BUDGET="400")
        assert "rc=1" in r.stdout, r.stdout + r.stderr
        slept = [int(s) for s in _count(box, "slept")]
        assert sum(slept) <= 400, f"slept {slept} past a 400 s budget"
        assert max(slept) <= 60, "the backoff must be capped"

    def test_a_stalled_probe_is_a_network_fault(self, box) -> None:
        """`timeout` kills a probe stuck in the key exchange, and ssh prints
        nothing. That is the network, so it must retry, not go red at once."""
        r = _lib(box, 'wait_for_ssh; echo "rc=$?"', PROBE_MODE="stall")
        assert "rc=1" in r.stdout, r.stdout + r.stderr
        assert len(_count(box, "calls")) > 1

    def test_an_auth_failure_is_not_retried(self, box) -> None:
        r = _lib(box, 'wait_for_ssh; echo "rc=$?"', PROBE_MODE="auth")
        assert "rc=2" in r.stdout, r.stdout + r.stderr
        assert _count(box, "calls") == ["probe"], "waiting cannot fix a bad key"


@needs_shell
class TestTheDeployStepBehaviour:
    def test_connect_failures_do_not_spend_rounds(self, box, jobs) -> None:
        box.version(1, box.shas["want"], box.shas["want"])
        r = _step(box, jobs, PROBE_FAILS="3")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Deploy round 1/3" in r.stdout
        assert "Deploy round 2/3" not in r.stdout
        assert _count(box, "calls").count("apply") == 1

    def test_no_connect_hands_off_and_applies_nothing(self, box, jobs) -> None:
        r = _step(box, jobs, PROBE_MODE="down")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "reach=unreachable" in box.out.read_text(encoding="utf-8")
        assert "Deploy round" not in r.stdout
        assert "apply" not in _count(box, "calls")
        assert "::warning" in r.stdout

    def test_a_dropped_apply_connect_is_refunded_and_bounded(self, box, jobs) -> None:
        r = _step(box, jobs, APPLY_MODE="drop")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Deploy round 2/3" not in r.stdout, "a dropped connect spent a round"
        assert "THE APPLY NEVER STARTED" in r.stdout
        assert "THE APPLY DID NOT REACH ITS FINAL LINE" not in r.stdout
        assert "reach=unreachable" in box.out.read_text(encoding="utf-8")

    def test_a_forced_run_goes_red_instead_of_handing_off(self, box, jobs) -> None:
        r = _step(box, jobs, PROBE_MODE="down", DEPLOY_FORCE="1")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "reach=unreachable" not in box.out.read_text(encoding="utf-8")

    def test_a_bad_key_goes_red_without_a_hand_off(self, box, jobs) -> None:
        r = _step(box, jobs, PROBE_MODE="auth")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "reach=unreachable" not in box.out.read_text(encoding="utf-8")


@needs_shell
class TestAwaitPullDeliveryBehaviour:
    def _await(self, box, **env) -> subprocess.CompletedProcess:
        return _lib(box, 'await_pull_delivery "$GITHUB_SHA"; echo "rc=$?"', **env)

    def test_green_when_the_box_serves_a_complete_apply(self, box) -> None:
        box.version(1, box.shas["want"], box.shas["want"])
        r = self._await(box, WB_CODE="307")
        assert "rc=0" in r.stdout, r.stdout + r.stderr

    def test_green_when_a_descendant_is_served(self, box) -> None:
        box.version(1, box.shas["newer"], box.shas["newer"])
        assert "rc=0" in self._await(box).stdout

    def test_green_once_the_pull_catches_up(self, box) -> None:
        box.version(1, box.shas["old"], box.shas["old"])
        box.version(2, box.shas["want"], box.shas["old"])
        box.version(3, box.shas["want"], box.shas["want"])
        r = self._await(box, PULL_WAIT="120")
        assert "rc=0" in r.stdout, r.stdout + r.stderr
        assert _count(box, "slept") == ["30", "30"]

    def test_red_when_the_box_serves_an_older_commit(self, box) -> None:
        box.version(1, box.shas["old"], box.shas["old"])
        r = self._await(box)
        assert "rc=1" in r.stdout, r.stdout + r.stderr

    def test_red_when_only_the_gateway_moved(self, box) -> None:
        """The web build failed: the gateway restarted on WANT, and the marker
        still names the last complete apply."""
        box.version(1, box.shas["want"], box.shas["old"])
        assert "rc=1" in self._await(box).stdout

    def test_red_when_the_marker_is_absent(self, box) -> None:
        box.version(1, box.shas["want"], None)
        assert "rc=1" in self._await(box).stdout

    def test_red_when_the_web_app_is_down(self, box) -> None:
        box.version(1, box.shas["want"], box.shas["want"])
        assert "rc=1" in self._await(box, WB_CODE="502").stdout

    def test_no_answer_is_its_own_result(self, box) -> None:
        box.version(1, box.shas["want"], box.shas["want"])
        assert "rc=3" in self._await(box, CURL_MODE="down").stdout

    def test_the_wait_is_bounded(self, box) -> None:
        box.version(1, box.shas["old"], box.shas["old"])
        self._await(box, PULL_WAIT="90", PULL_POLL="30")
        assert _count(box, "slept") == ["30", "30", "30"]


def _pull_step(box, jobs: dict, **env: str) -> subprocess.CompletedProcess:
    step = box.repo / "pull_step.sh"
    step.write_text(jobs["pull-delivery"]["steps"][1]["run"], encoding="utf-8", newline="\n")
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", step.name],
        cwd=box.repo, env=box.env(SSH_HOST="127.0.0.1", SSH_PORT="1", **env),
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


@needs_shell
class TestThePullDeliveryJobBehaviour:
    """The job's own exit code, which is what colours the run."""

    def test_green_with_a_warning_when_delivered(self, box, jobs) -> None:
        box.version(1, box.shas["want"], box.shas["want"])
        r = _pull_step(box, jobs)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "::warning title=Delivered by the pull path::" in r.stdout

    def test_red_when_the_box_did_not_self_deliver(self, box, jobs) -> None:
        box.version(1, box.shas["old"], box.shas["old"])
        r = _pull_step(box, jobs)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "::error title=Not delivered::" in r.stdout

    def test_red_when_the_web_build_did_not_finish(self, box, jobs) -> None:
        box.version(1, box.shas["want"], box.shas["old"])
        assert _pull_step(box, jobs).returncode == 1

    def test_red_when_the_box_never_answers(self, box, jobs) -> None:
        r = _pull_step(box, jobs, CURL_MODE="down")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "::error title=Box unreachable from GitHub::" in r.stdout


def test_the_pull_wait_also_counts_wall_time() -> None:
    """Each curl can take 10 s when packets drop. Counting naps alone let the
    wait run past the job's timeout-minutes, which kills the red message."""
    fn = _read(LIB)
    fn = fn[fn.index("await_pull_delivery() {"):]
    assert "t_start=$(_now)" in fn
    assert '[ $(( $(_now) - t_start )) -ge "$PULL_WAIT" ] && break' in fn
