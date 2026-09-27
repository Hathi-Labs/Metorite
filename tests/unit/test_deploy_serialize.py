"""Both deploy paths take ONE lock, and a CI round cannot leave a build behind.

🔴 **The box ran two deploy paths on one checkout, and nothing made them take
turns.** `deploy.yml` runs `scripts/vps_apply.sh` over ssh as the app user.
`acb-pull.timer` ran the same apply every five minutes as ROOT. Measured
2026-09-21 to 2026-09-26:

* **EACCES** on `.next.staging/trace`. Root left paths the app-user build could
  not write. Run 36177499439 failed all three rounds, and root's pull applied
  the same commit three minutes later.
* **A missing module.** One path's `npm ci` removed `node_modules/next` while
  the other built (`Cannot find module 'next/server.js'`, run 36166015861) or
  started (`next: not found`, H-164, run 35855523275).
* **An orphan build.** A failed CI round left `next build` running as the app
  user for 41 minutes after the run ended.
* **A double build.** CI built a commit at 06:34 and root's pull built the same
  commit again at 06:47.

The fix has four parts, and this file fences each one:

1. one `flock` for the whole apply. CI waits with a bound, and the pull does
   not wait;
2. the pull runs as the checkout's owner, never as root;
3. install, then build, then restart, and never restart without `next`;
4. the build runs under `timeout`, and a CI apply dies with its ssh session.

Two kinds of test. The STRUCTURAL ones read non-comment lines and run on every
platform. The BEHAVIOURAL ones source the real helper block out of
`vps_apply.sh`, or run the real `vps_pull.sh`, against a real lock file. They
need Linux (`flock`, `/proc`, `ps`), so they skip on the Windows dev box and
run in CI.

⚠️ Idiom from `test_deploy_next_build_swap`: structural assertions read
NON-COMMENT lines, because every block under test carries a comment that names
the strings checked here.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"
_PULL = _ROOT / "scripts/vps_pull.sh"
_UNIT = _ROOT / "deploy/hostinger/acb-pull.service"

_BEGIN = "# >>> deploy-serialize helpers"
_END = "# <<< deploy-serialize helpers"


def _executable_lines(path: pathlib.Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _first(lines: list[str], needle: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i
    raise AssertionError(f"{needle!r} not found")


# ── Structural: every platform ───────────────────────────────────────────────


class TestOneLockForBothPaths:
    def test_both_files_default_to_the_SAME_lock_file(self) -> None:
        """Two lock files are no lock at all. The old pull lock was
        `/tmp/acb-vps-pull.lock`, and the CI path never took it."""
        want = 'DEPLOY_LOCK="${DEPLOY_LOCK:-$(dirname "$APP_DIR")/acb-deploy.lock}"'
        assert any(ln.strip() == want for ln in _executable_lines(_APPLY))
        assert any(ln.strip() == want for ln in _executable_lines(_PULL))
        assert not any("/tmp/acb-vps-pull.lock" in ln for ln in _executable_lines(_PULL))

    def test_the_lock_is_not_in_a_sticky_world_writable_dir(self) -> None:
        """`fs.protected_regular=2` on the box refuses an O_CREAT open of a
        file another user owns in /tmp or /run/lock, root included. A lock one
        user created there would lock the other user OUT, not in."""
        for path in (_APPLY, _PULL):
            locks = [ln for ln in _executable_lines(path) if "DEPLOY_LOCK=" in ln]
            assert locks, f"{path.name} names no deploy lock"
            for ln in locks:
                assert "/tmp/" not in ln and "/run/lock" not in ln, ln

    def test_the_apply_takes_the_lock_BEFORE_it_fetches(self) -> None:
        lines = _executable_lines(_APPLY)
        take = _first(lines, 'deploy_lock_acquire "${DEPLOY_LOCK_MODE:-wait}"')
        fetch = _first(lines, "git fetch origin main")
        assert take < fetch, "the lock must cover the fetch and the reset"

    def test_the_pull_takes_the_lock_without_waiting_BEFORE_it_fetches(self) -> None:
        lines = _executable_lines(_PULL)
        take = _first(lines, "flock -n 8")
        fetch = _first(lines, "git fetch")
        assert take < fetch
        assert not any("flock -w" in ln for ln in lines), (
            "the pull path must never wait: the timer's next tick is its retry"
        )

    def test_a_busy_lock_is_a_clean_exit_on_the_pull_path(self) -> None:
        lines = _executable_lines(_PULL)
        at = _first(lines, "if ! flock -n 8; then")
        window = lines[at : at + 12]
        assert any(ln.strip() == "exit 0" for ln in window), (
            "a busy lock is a normal state. Exit 0, or systemd marks the unit "
            "failed every time the CI path is deploying"
        )

    def test_the_pull_hands_its_lock_to_the_apply(self) -> None:
        """Without DEPLOY_LOCK_HELD the apply opens a NEW file description on
        the same file and waits on its own parent until the bound expires."""
        body = "\n".join(_executable_lines(_PULL))
        assert "DEPLOY_LOCK_HELD=1" in body
        apply_lines = _executable_lines(_APPLY)
        assert any('"${DEPLOY_LOCK_HELD:-0}" = "1"' in ln for ln in apply_lines)

    def test_the_CI_wait_is_bounded_and_says_who_holds_it(self) -> None:
        lines = _executable_lines(_APPLY)
        assert any(ln.strip().startswith('DEPLOY_LOCK_WAIT="${DEPLOY_LOCK_WAIT:-') for ln in lines)
        assert any('flock -w "$DEPLOY_LOCK_WAIT" 8' in ln for ln in lines)
        assert any("another deploy holds the lock $(deploy_lock_holder)" in ln for ln in lines)


class TestTheSecondPathDoesNotRebuild:
    def test_the_apply_checks_the_marker_between_fetch_and_reset(self) -> None:
        lines = _executable_lines(_APPLY)
        fetch = _first(lines, "git fetch origin main")
        skip = _first(lines, 'deploy_already_applied "$DEPLOY_TARGET_SHA"')
        reset = _first(lines, "git reset --hard origin/main")
        assert fetch < skip < reset

    def test_a_skip_still_prints_the_final_line(self) -> None:
        """deploy.yml greps for it (H-137). A skip means a complete apply of
        this sha already finished, which is the claim that line makes."""
        lines = _executable_lines(_APPLY)
        skip = _first(lines, 'deploy_already_applied "$DEPLOY_TARGET_SHA"')
        window = lines[skip : skip + 4]
        assert any('echo "==> Deployment complete"' in ln for ln in window)
        assert any("already at" in ln and "skipping" in ln for ln in window)

    def test_the_marker_is_written_only_just_before_the_final_line(self) -> None:
        """Written anywhere earlier, it records an attempt, not a delivery, and
        the skip then latches a half-applied box out of every retry."""
        lines = _executable_lines(_APPLY)
        calls = [
            i for i, ln in enumerate(lines)
            if "record_applied_sha" in ln and "()" not in ln
        ]
        assert len(calls) == 1, calls
        assert lines[calls[0] + 1].strip() == 'echo "==> Deployment complete"'
        assert calls[0] + 2 == len(lines), "the marker write must be the end of the file"

    def test_force_bypasses_the_skip(self) -> None:
        """`vps_pull.sh --force` is the runbook for an .env edit. A skip that
        --force cannot bypass would silently ignore the new secret."""
        pull = "\n".join(_executable_lines(_PULL))
        assert 'DEPLOY_FORCE="$FORCE_FLAG"' in pull
        apply_lines = _executable_lines(_APPLY)
        assert any('"${DEPLOY_FORCE:-0}" != "1"' in ln and "deploy_already_applied" in ln
                   for ln in apply_lines)

    def test_the_pull_gate_accepts_either_paths_marker(self) -> None:
        lines = _executable_lines(_PULL)
        gate = [ln for ln in lines if '"$LOCAL" = "$TARGET"' in ln]
        assert gate and all("APPLIED_SHA" in ln for ln in gate)
        assert any("APPLIED_SHA=" in ln and "DEPLOY_MARKER" in ln for ln in lines)


class TestThePullPathIsNotRoot:
    def test_the_unit_runs_as_the_app_user(self) -> None:
        lines = _executable_lines(_UNIT)
        assert "User=acb" in lines
        assert "User=root" not in lines, "root on this path is H-89's writer"

    def test_the_unit_owns_its_state_directory(self) -> None:
        """/var/lib/acb was root-owned. StateDirectory= hands it to User=, or
        every marker write fails silently and the breaker stops counting."""
        assert "StateDirectory=acb" in _executable_lines(_UNIT)

    def test_the_unit_finds_uv(self) -> None:
        env = [ln for ln in _executable_lines(_UNIT) if ln.startswith("Environment=PATH=")]
        assert env and "/home/acb/.local/bin" in env[0]

    def test_a_root_run_drops_to_the_owner_BEFORE_the_lock(self) -> None:
        """The runbook says `sudo … vps_pull.sh`. The first tick after the
        merge still runs the old unit as root. Both must not build as root."""
        lines = _executable_lines(_PULL)
        drop = _first(lines, "exec runuser -u")
        lock = _first(lines, "flock -n 8")
        assert drop < lock

    def test_safe_directory_is_added_once_not_every_tick(self) -> None:
        lines = _executable_lines(_PULL)
        adds = [i for i, ln in enumerate(lines) if "safe.directory" in ln and "--add" in ln]
        assert adds
        for i in adds:
            assert any("--get-all safe.directory" in ln for ln in lines[max(0, i - 3) : i]), (
                "guard the add: root's ~/.gitconfig held 8375 copies of it"
            )


class TestInstallThenBuildThenRestart:
    def test_the_workbench_restart_is_gated_on_the_next_binary(self) -> None:
        lines = _executable_lines(_APPLY)
        restart = _first(lines, "sudo systemctl restart acb-workbench")
        gate = _first(lines, 'require_next_bin "$APP_DIR/workbench/control_plane"')
        build = _first(lines, 'build_next_staged "workbench"')
        install = _first(lines, 'npm_install_here "workbench"')
        assert install < build < gate < restart

    def test_the_operator_console_restart_is_gated_too(self) -> None:
        lines = _executable_lines(_APPLY)
        restart = _first(lines, 'sudo systemctl restart "$OC_UNIT"')
        gate = _first(lines, 'require_next_bin "$OC_DIR"')
        assert gate < restart

    def test_a_missing_binary_stops_the_apply_before_the_restart(self) -> None:
        lines = _executable_lines(_APPLY)
        gates = [ln for ln in lines if ln.strip().startswith("require_next_bin ")]
        assert len(gates) >= 2
        assert all(ln.rstrip().endswith("|| exit 1") for ln in gates)

    def test_the_build_is_bounded(self) -> None:
        body = _APPLY.read_text(encoding="utf-8")
        runner = body[body.index("run_tethered_build() {"):]
        runner = runner[: runner.index("\n}\n")]
        assert 'timeout -k 60 "$NEXT_BUILD_TIMEOUT_S" "$@" &' in runner, (
            "an unbounded build is how the 41-minute orphan happened"
        )
        fn = body[body.index("build_next_staged() {"):]
        fn = fn[: fn.index("\n}\n")]
        code = "\n".join(ln for ln in fn.splitlines() if not ln.strip().startswith("#"))
        assert "run_tethered_build env NEXT_DIST_DIR=" in code
        assert code.count("npm run build") == 1, "one build, and it runs through the runner"

    def test_the_reclaim_measures_node_modules(self) -> None:
        body = _APPLY.read_text(encoding="utf-8")
        fn = body[body.index("reclaim_build_tree() {"):]
        fn = fn[: fn.index("\n}\n")]
        assert "node_modules" in fn
        assert "reclaimed $total path(s)" in fn, (
            "print the total every time, so a zero is visible as a zero"
        )


class TestNoOrphanBuild:
    def test_the_apply_tethers_itself_after_the_lock(self) -> None:
        lines = _executable_lines(_APPLY)
        tether = next(i for i, ln in enumerate(lines) if ln.strip() == "tether_to_session")
        fetch = _first(lines, "git fetch origin main")
        lock = _first(lines, 'deploy_lock_acquire "${DEPLOY_LOCK_MODE:-wait}"')
        assert lock < tether < fetch

    def test_the_session_is_found_BEFORE_the_lock_wait(self) -> None:
        """A round cancelled during the wait leaves the apply under init, and
        a search after the wait then finds no sshd and starts no watcher."""
        lines = _executable_lines(_APPLY)
        anchor = _first(lines, 'DEPLOY_SESSION_ANCHOR="$(session_anchor)"')
        lock = _first(lines, 'deploy_lock_acquire "${DEPLOY_LOCK_MODE:-wait}"')
        assert anchor < lock

    def test_the_watcher_kills_only_the_build_group(self) -> None:
        """P2 of the PR #484 review: a kill of the WHOLE apply can stop a
        migration halfway or leave no `.next`. So no kill names the apply."""
        body = _APPLY.read_text(encoding="utf-8")
        fn = body[body.index("tether_to_session() {"):]
        fn = fn[: fn.index("\n}\n")]
        kills = [ln.strip() for ln in fn.splitlines() if ln.strip().startswith("kill ")]
        assert kills, "the watcher kills nothing"
        assert all('-- "-$bpg"' in k for k in kills), kills
        assert "apply_pid" not in " ".join(kills)

    def test_the_watcher_never_holds_the_lock(self) -> None:
        body = _APPLY.read_text(encoding="utf-8")
        fn = body[body.index("tether_to_session() {"):]
        fn = fn[: fn.index("\n}\n")]
        assert "exec 8<&-" in fn


class TestReapplyTheSameSha:
    """A skip is right for a second path. It is wrong when an operator edits
    `.env` and asks for the same sha again (PR #484 review, P3)."""

    _WF = _ROOT / ".github/workflows/deploy.yml"

    def test_a_CI_rerun_or_the_force_input_forces_the_apply(self) -> None:
        wf = self._WF.read_text(encoding="utf-8")
        assert "DEPLOY_FORCE: ${{ (github.run_attempt > 1 || inputs.force) && '1' || '0' }}" in wf
        assert "\"DEPLOY_FORCE=${DEPLOY_FORCE:-0} bash -s\" < /tmp/deploy_remote.sh" in wf
        assert "      force:\n" in wf, "the workflow_dispatch input is missing"

    def test_MODE_from_the_environment_is_honoured(self) -> None:
        """The give-up message said `sudo MODE=force bash vps_pull.sh`, and a
        hard `MODE="apply"` made that advice a no-op."""
        lines = _executable_lines(_PULL)
        assert any(ln.strip() == 'MODE="${MODE:-apply}"' for ln in lines)
        assert not any(ln.strip() == 'MODE="apply"' for ln in lines)
        body = _PULL.read_text(encoding="utf-8")
        drop = body[body.index("exec runuser"):]
        drop = drop[: drop.index('bash "$0" "$@"')]
        assert 'MODE="$MODE"' in drop, "the root drop loses MODE"


# ── Behavioural: Linux only ──────────────────────────────────────────────────

_LINUX = sys.platform.startswith("linux") and all(
    shutil.which(t) for t in ("bash", "flock", "ps", "timeout", "git")
)
linux_only = pytest.mark.skipif(not _LINUX, reason="needs Linux with flock, ps and /proc")


def _helpers(tmp: pathlib.Path, app_dir: pathlib.Path) -> pathlib.Path:
    """The REAL helper block out of vps_apply.sh, as a file to source."""
    body = _APPLY.read_text(encoding="utf-8")
    block = body[body.index(_BEGIN) : body.index(_END)]
    out = tmp / "helpers.sh"
    out.write_text(f'APP_DIR="{app_dir}"\n{block}', encoding="utf-8")
    return out


def _env(tmp: pathlib.Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        DEPLOY_LOCK=str(tmp / "acb-deploy.lock"),
        DEPLOY_MARKER=str(tmp / "acb-deploy.applied"),
        DEPLOY_TETHER="0",
    )
    env.update(extra)
    return env


def _wait_for(pred, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _hold_lock(tmp: pathlib.Path, helpers: pathlib.Path, seconds: int = 30) -> subprocess.Popen:
    """A process that holds the lock through the real helper, like a CI apply."""
    ready = tmp / "held"
    proc = subprocess.Popen(
        ["bash", "-c", f'. "{helpers}"; deploy_lock_acquire wait || exit 9; : > "{ready}"; sleep {seconds}'],
        env=_env(tmp),
    )
    assert _wait_for(ready.exists), "the holder never took the lock"
    return proc


def _alive(pid: int) -> bool:
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@linux_only
class TestTheLockReallySerialises:
    def test_two_CI_applies_run_one_after_the_other(self, tmp_path: pathlib.Path) -> None:
        helpers = _helpers(tmp_path, tmp_path / "app")
        log = tmp_path / "log"
        script = (
            f'. "{helpers}"; deploy_lock_acquire wait || exit 9; '
            f'echo "start $1" >> "{log}"; sleep 1; echo "end $1" >> "{log}"'
        )
        procs = [
            subprocess.Popen(["bash", "-c", script, "x", tag], env=_env(tmp_path))
            for tag in ("a", "b")
        ]
        assert [p.wait(timeout=30) for p in procs] == [0, 0]
        # "start a, end a, start b, end b" in either order. Never two starts
        # in a row, which is what two applies on one checkout look like.
        events = [ln.split() for ln in log.read_text().splitlines()]
        assert len(events) == 4, events
        assert [e[0] for e in events] == ["start", "end", "start", "end"], (
            f"the two critical sections overlapped: {events}"
        )
        assert events[0][1] == events[1][1] and events[2][1] == events[3][1]

    def test_a_CI_apply_gives_up_after_its_bound_and_names_the_holder(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The REAL vps_apply.sh, stopped at its first step. It must fetch,
        build and restart nothing, and it must say who holds the lock."""
        app = tmp_path / "app"
        app.mkdir()
        holder = _hold_lock(tmp_path, _helpers(tmp_path, app))
        try:
            res = subprocess.run(
                ["bash", str(_APPLY)],
                env=_env(tmp_path, APP_DIR=str(app), DEPLOY_LOCK_WAIT="1"),
                capture_output=True, text=True, timeout=30,
            )
        finally:
            holder.kill()
            holder.wait()
        out = res.stdout + res.stderr
        assert res.returncode == 1, out
        assert "another deploy holds the lock since" in out, out
        assert f"pid {holder.pid}" in out, out
        assert "Pulling latest" not in out, "it went past the lock"

    def test_the_pull_path_exits_0_at_once_when_the_lock_is_held(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The REAL vps_pull.sh against a lock the apply's helper holds. So
        this also proves the two files lock the SAME thing."""
        app = tmp_path / "app"
        app.mkdir()
        holder = _hold_lock(tmp_path, _helpers(tmp_path, app))
        try:
            t0 = time.monotonic()
            res = subprocess.run(
                ["bash", str(_PULL)],
                env=_env(tmp_path, APP_DIR=str(app), STATE_DIR=str(tmp_path / "state")),
                capture_output=True, text=True, timeout=30,
            )
            took = time.monotonic() - t0
        finally:
            holder.kill()
            holder.wait()
        out = res.stdout + res.stderr
        assert res.returncode == 0, out
        assert "another deploy holds the lock since" in out, out
        assert "Fetching" not in out, "it went past the lock"
        assert took < 5, f"the pull path waited {took:.1f}s. It must not wait at all"

    def test_a_lock_the_pull_holds_stops_the_apply_too(self, tmp_path: pathlib.Path) -> None:
        """The other direction: `flock -n` on fd 8, exactly as vps_pull.sh
        takes it, must keep a CI apply out."""
        app = tmp_path / "app"
        app.mkdir()
        lock = tmp_path / "acb-deploy.lock"
        lock.touch()
        ready = tmp_path / "held"
        holder = subprocess.Popen(
            ["bash", "-c", f'exec 8<"{lock}"; flock -n 8 || exit 9; : > "{ready}"; sleep 30'],
        )
        assert _wait_for(ready.exists)
        try:
            res = subprocess.run(
                ["bash", str(_APPLY)],
                env=_env(tmp_path, APP_DIR=str(app), DEPLOY_LOCK_WAIT="1"),
                capture_output=True, text=True, timeout=30,
            )
        finally:
            holder.kill()
            holder.wait()
        assert res.returncode == 1, res.stdout + res.stderr
        assert "Pulling latest" not in res.stdout

    def test_a_lock_file_another_user_made_read_only_still_works(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Root may create the file on the first run after the merge. The app
        user then cannot open it for writing, and must not need to."""
        helpers = _helpers(tmp_path, tmp_path / "app")
        lock = tmp_path / "acb-deploy.lock"
        lock.touch()
        lock.chmod(0o444)
        res = subprocess.run(
            ["bash", "-c", f'. "{helpers}"; deploy_lock_acquire nowait; echo "rc=$?"'],
            env=_env(tmp_path), capture_output=True, text=True, timeout=30,
        )
        assert "rc=0" in res.stdout, res.stdout + res.stderr


@linux_only
class TestTheNextBinaryGate:
    def _run(self, tmp_path: pathlib.Path, app: pathlib.Path) -> subprocess.CompletedProcess:
        helpers = _helpers(tmp_path, tmp_path)
        return subprocess.run(
            ["bash", "-c", f'. "{helpers}"; require_next_bin "{app}" acb-workbench; echo "rc=$?"'],
            env=_env(tmp_path), capture_output=True, text=True, timeout=30,
        )

    def test_a_missing_binary_refuses_the_restart(self, tmp_path: pathlib.Path) -> None:
        app = tmp_path / "wb"
        (app / "node_modules").mkdir(parents=True)
        res = self._run(tmp_path, app)
        assert "rc=1" in res.stdout
        assert "MISSING" in res.stdout and "H-164" in res.stdout

    def test_a_dangling_link_counts_as_missing(self, tmp_path: pathlib.Path) -> None:
        """`.bin/next` is a symlink into `node_modules/next`. Mid-`npm ci` the
        link can survive while its target is gone, which is H-164 exactly."""
        app = tmp_path / "wb"
        (app / "node_modules/.bin").mkdir(parents=True)
        (app / "node_modules/.bin/next").symlink_to("../next/dist/bin/next")
        assert "rc=1" in self._run(tmp_path, app).stdout

    def test_a_present_binary_passes(self, tmp_path: pathlib.Path) -> None:
        app = tmp_path / "wb"
        target = app / "node_modules/next/dist/bin/next"
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\n")
        target.chmod(0o755)
        (app / "node_modules/.bin").mkdir()
        (app / "node_modules/.bin/next").symlink_to("../next/dist/bin/next")
        assert "rc=0" in self._run(tmp_path, app).stdout


@linux_only
class TestTheSkipNeedsACompleteApply:
    def _repo(self, tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
        app = tmp_path / "app"
        app.mkdir()
        git = ["git", "-C", str(app), "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", str(app)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        sha = subprocess.run(
            ["git", "-C", str(app), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        wb = app / "workbench/control_plane"
        (wb / ".next").mkdir(parents=True)
        (wb / ".next/BUILD_ID").write_text("b")
        (wb / "node_modules/.bin").mkdir(parents=True)
        nxt = wb / "node_modules/.bin/next"
        nxt.write_text("#!/bin/sh\n")
        nxt.chmod(0o755)
        return app, sha

    def _skip(self, tmp_path: pathlib.Path, app: pathlib.Path, sha: str) -> bool:
        helpers = _helpers(tmp_path, app)
        res = subprocess.run(
            ["bash", "-c", f'. "{helpers}"; deploy_already_applied "{sha}" && echo SKIP'],
            env=_env(tmp_path), capture_output=True, text=True, timeout=30,
        )
        return "SKIP" in res.stdout

    def test_a_marker_for_HEAD_with_both_builds_skips(self, tmp_path: pathlib.Path) -> None:
        app, sha = self._repo(tmp_path)
        (tmp_path / "acb-deploy.applied").write_text(f"{sha} 2026-09-26T06:34:00Z\n")
        assert self._skip(tmp_path, app, sha)

    def test_no_marker_means_no_skip(self, tmp_path: pathlib.Path) -> None:
        app, sha = self._repo(tmp_path)
        assert not self._skip(tmp_path, app, sha)

    def test_a_marker_for_another_sha_means_no_skip(self, tmp_path: pathlib.Path) -> None:
        app, sha = self._repo(tmp_path)
        (tmp_path / "acb-deploy.applied").write_text("0" * 40 + " t\n")
        assert not self._skip(tmp_path, app, sha)

    def test_a_missing_build_means_no_skip(self, tmp_path: pathlib.Path) -> None:
        app, sha = self._repo(tmp_path)
        (tmp_path / "acb-deploy.applied").write_text(f"{sha} t\n")
        (app / "workbench/control_plane/.next/BUILD_ID").unlink()
        assert not self._skip(tmp_path, app, sha)

    def test_the_marker_round_trips(self, tmp_path: pathlib.Path) -> None:
        app, sha = self._repo(tmp_path)
        helpers = _helpers(tmp_path, app)
        subprocess.run(
            ["bash", "-c", f'. "{helpers}"; record_applied_sha "{sha}"'],
            env=_env(tmp_path), check=True, timeout=30,
        )
        assert (tmp_path / "acb-deploy.applied").read_text().split()[0] == sha
        assert self._skip(tmp_path, app, sha)


def _tethered(tmp: pathlib.Path, helpers: pathlib.Path, session_pid: int, body: str,
              **extra: str) -> subprocess.Popen:
    """A stand-in apply: the real helpers, a watcher on `session_pid`, then
    `body`. It prints to files under `tmp`, so the test reads what ran."""
    return subprocess.Popen(
        ["bash", "-c", f'set -e; . "{helpers}"; DEPLOY_SESSION_ANCHOR={session_pid}; '
                       f"tether_to_session; {body}"],
        env=_env(
            tmp,
            DEPLOY_TETHER="1",
            DEPLOY_TETHER_POLL="0.2",
            DEPLOY_TETHER_GRACE="1",
            **extra,
        ),
    )


def _stop(*procs: subprocess.Popen) -> None:
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()


@linux_only
class TestAnEndedSessionEndsTheBuild:
    def test_the_build_dies_when_the_session_does(self, tmp_path: pathlib.Path) -> None:
        """A stand-in for sshd, an apply tethered to it, and a long "build"
        that starts a grandchild of its own. Kill the stand-in: the build AND
        the grandchild must go, the apply must fail, and nothing may swap.
        This is the 41-minute orphan of 2026-09-25, in miniature.

        The build and its child IGNORE TERM, and the child inherits that. So
        only the KILL can stop them, and it must reach the same GROUP that
        got the TERM (review item 3)."""
        session = subprocess.Popen(["sleep", "300"])
        helpers = _helpers(tmp_path, tmp_path)
        pids = tmp_path / "build.pids"
        swapped = tmp_path / "swapped"
        build = f'bash -c \'trap "" TERM; sleep 300 & echo "$$ $!" > "{pids}"; wait\''
        apply = _tethered(
            tmp_path, helpers, session.pid,
            f'run_tethered_build {build} || exit 7; : > "{swapped}"',
        )
        try:
            assert _wait_for(lambda: pids.exists() and len(pids.read_text().split()) == 2)
            builder, grandchild = (int(x) for x in pids.read_text().split())
            assert _alive(builder) and _alive(grandchild)
            session.kill()
            session.wait()
            assert _wait_for(lambda: apply.poll() is not None, 15), "the apply outlived its build"
            assert apply.returncode == 7, "a killed build must fail the apply"
            assert not swapped.exists(), "a killed build must not swap"
            assert _wait_for(lambda: not _alive(builder), 15), "the build outlived its session"
            assert _wait_for(lambda: not _alive(grandchild), 15), (
                "the build's child outlived its session. Kill the GROUP, and "
                "KILL the same group that got TERM"
            )
        finally:
            _stop(session, apply)

    def test_a_session_end_during_a_migration_lets_the_migration_finish(
        self, tmp_path: pathlib.Path
    ) -> None:
        """P2 of the PR #484 review. The session ends while a stand-in
        migration runs. The migration must finish and write its ledger row,
        and the next step (a stand-in for the gateway restart) must run too.
        The build that follows must not start, and the apply must fail."""
        session = subprocess.Popen(["sleep", "300"])
        helpers = _helpers(tmp_path, tmp_path)
        started = tmp_path / "migration.started"
        ledger = tmp_path / "ledger"
        built = tmp_path / "built"
        restarted = tmp_path / "restarted"
        apply = _tethered(
            tmp_path, helpers, session.pid,
            f': > "{started}"; sleep 3; echo 999 > "{ledger}"; : > "{restarted}"; '
            f'run_tethered_build touch "{built}" || exit 7; echo APPLIED',
        )
        try:
            assert _wait_for(started.exists)
            session.kill()
            session.wait()
            assert _wait_for(lambda: apply.poll() is not None, 20)
            assert ledger.exists() and ledger.read_text().strip() == "999", (
                "the watcher interrupted the migration"
            )
            assert restarted.exists(), "the step after the migration did not run"
            assert not built.exists(), "a build started after the session ended"
            assert apply.returncode == 7
        finally:
            _stop(session, apply)

    def test_a_live_session_leaves_the_apply_alone(self, tmp_path: pathlib.Path) -> None:
        session = subprocess.Popen(["sleep", "300"])
        helpers = _helpers(tmp_path, tmp_path)
        try:
            res = subprocess.run(
                ["bash", "-c",
                 f'. "{helpers}"; DEPLOY_SESSION_ANCHOR={session.pid}; tether_to_session; '
                 f'run_tethered_build sleep 1 && echo DONE'],
                env=_env(
                    tmp_path,
                    DEPLOY_TETHER="1",
                    DEPLOY_TETHER_POLL="0.2",
                ),
                capture_output=True, text=True, timeout=30,
            )
            assert "DONE" in res.stdout, res.stdout + res.stderr
            assert "tethered to deploy session" in res.stdout
            assert not (tmp_path / "acb-deploy.lock.build").exists(), "the build pid file is left"
        finally:
            _stop(session)

    def test_a_session_that_ends_during_the_lock_wait_applies_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The REAL vps_apply.sh waits for the lock. Its session ends during
        the wait, then the lock frees. The apply must stop before the fetch."""
        app = tmp_path / "app"
        app.mkdir()
        session = subprocess.Popen(["sleep", "300"])
        holder = _hold_lock(tmp_path, _helpers(tmp_path, app), seconds=3)
        apply = subprocess.Popen(
            ["bash", str(_APPLY)],
            env=_env(
                tmp_path, APP_DIR=str(app), DEPLOY_LOCK_WAIT="30",
                DEPLOY_TETHER="1", DEPLOY_TETHER_ANCHOR=str(session.pid),
            ),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            time.sleep(1)
            session.kill()
            session.wait()
            out, _ = apply.communicate(timeout=30)
        finally:
            _stop(session, holder, apply)
        assert apply.returncode == 1, out
        assert "ended while this apply waited" in out, out
        assert "Pulling latest" not in out, "it went past the lock"


# ── H-60: a deploy gives live users no 502 window ────────────────────────────
#
# Measured 2026-09-27, run 36320367460. The gateway restart ALSO restarted the
# workbench (`Requires=`), and browsers got a 502 from 127.0.0.1:3001. `npm ci`
# deleted `node_modules` under the running workbench for 38 s, and it answered
# 500. The Caddy step restarted Caddy on every deploy, because `admin off`
# makes a reload fail, and that cut every open connection. The tests below
# fence the four fixes: Caddy waits for a restarting upstream, a gateway
# restart leaves the workbench alone, an unchanged dependency tree is kept,
# and each restart waits until the service answers.

_CADDYFILE = _ROOT / "deploy/hostinger/caddy/Caddyfile"
_WB_UNIT = _ROOT / "deploy/hostinger/acb-workbench.service"


def _caddy_proxy_block(upstream: str) -> str:
    """The body of the `reverse_proxy <upstream> { ... }` block."""
    lines = [ln.split("#", 1)[0] for ln in _CADDYFILE.read_text(encoding="utf-8").splitlines()]
    start = next(
        (i for i, ln in enumerate(lines) if ln.split()[:2] == ["reverse_proxy", upstream]),
        None,
    )
    assert start is not None, f"no reverse_proxy to {upstream}"
    assert lines[start].rstrip().endswith("{"), f"reverse_proxy {upstream} has no block"
    body: list[str] = []
    for ln in lines[start + 1 :]:
        if ln.strip() == "}":
            return "\n".join(body)
        body.append(ln.strip())
    raise AssertionError(f"reverse_proxy {upstream} block is not closed")


def _seconds(value: str) -> float:
    for unit, mult in (("ms", 0.001), ("s", 1.0), ("m", 60.0)):
        if value.endswith(unit):
            return float(value[: -len(unit)]) * mult
    raise AssertionError(f"not a duration: {value}")


def _apply_function(name: str) -> str:
    body = _APPLY.read_text(encoding="utf-8")
    fn = body[body.index(f"{name}() {{"):]
    fn = fn[: fn.index("\n}\n")]
    return "\n".join(ln for ln in fn.splitlines() if not ln.strip().startswith("#"))


class TestCaddyHoldsTheRequest:
    @pytest.mark.parametrize("upstream", ["127.0.0.1:3001", "127.0.0.1:8080", "127.0.0.1:3002"])
    def test_every_upstream_is_retried_through_a_restart(self, upstream: str) -> None:
        block = _caddy_proxy_block(upstream)
        opts = {ln.split()[0]: ln.split()[1] for ln in block.splitlines() if ln.split()}
        assert "lb_try_duration" in opts, f"{upstream}: a restart answers 502 without it"
        assert "lb_try_interval" in opts, f"{upstream}: no retry interval"
        # Longer than the gateway's measured cold start (15 to 23 s).
        assert _seconds(opts["lb_try_duration"]) >= 25, opts
        assert _seconds(opts["lb_try_interval"]) <= 1, opts

    def test_the_repo_file_serves_every_hostname_the_box_serves(self) -> None:
        """The apply installs this file. A hostname missing here goes DOWN on
        the next deploy. The repo copy had lost three of them by 2026-09-28."""
        text = _CADDYFILE.read_text(encoding="utf-8")
        sites = {
            ln.split()[0]
            for ln in text.splitlines()
            if ln and not ln[0].isspace() and ln.rstrip().endswith("{") and "." in ln
        }
        assert sites >= {
            "api.metorite.com", "app.metorite.com", "metorite.com",
            "www.metorite.com", "operator.metorite.com",
        }, sites

    def test_the_apply_validates_the_file_before_it_installs_it(self) -> None:
        lines = _executable_lines(_APPLY)
        validate = _first(lines, 'caddy validate --config "$CADDY_REPO"')
        install = _first(lines, 'sudo install -m 0644 "$CADDY_REPO" "$CADDY_LIVE"')
        assert validate < install
        assert lines[validate].lstrip().startswith("if ! "), "a bad file must stop the apply"
        assert "exit 1" in "\n".join(lines[validate : validate + 6])

    def test_the_config_goes_in_before_the_first_restart(self) -> None:
        lines = _executable_lines(_APPLY)
        install = _first(lines, 'sudo install -m 0644 "$CADDY_REPO" "$CADDY_LIVE"')
        assert install < _first(lines, "sudo systemctl restart acb-gateway")

    def test_caddy_is_never_reloaded_and_restarts_only_on_a_change(self) -> None:
        """`admin off`: a reload always fails, and `reload || restart` then
        restarted Caddy on every deploy and cut every open stream."""
        lines = _executable_lines(_APPLY)
        assert not any("systemctl reload caddy" in ln for ln in lines)
        assert any('cmp -s "$CADDY_REPO" "$CADDY_LIVE"' in ln for ln in lines)
        restarts = [ln for ln in lines if "systemctl restart caddy" in ln]
        assert restarts
        for ln in restarts:
            assert ln.startswith((" ", "\t")), f"an unconditional Caddy restart: {ln!r}"

    def test_a_changed_config_restarts_caddy_even_without_a_backup(self) -> None:
        """The restart must follow the INSTALL, not the backup. A backup that
        fails must never leave a new file on disk and the old one running."""
        lines = _executable_lines(_APPLY)
        install = _first(lines, 'sudo install -m 0644 "$CADDY_REPO" "$CADDY_LIVE"')
        assert lines[install + 1].strip() == "CADDY_CHANGED=1"
        restart = _first(lines, "systemctl restart caddy", install)
        cond = lines[restart - 2 : restart]
        assert any('[ "$CADDY_CHANGED" = "1" ]' in ln for ln in cond), cond

    def test_a_config_that_does_not_start_is_rolled_back(self) -> None:
        lines = _executable_lines(_APPLY)
        backup = _first(lines, 'sudo cp -a "$CADDY_LIVE" "$CADDY_BAK"')
        restore = _first(lines, 'sudo install -m 0644 "$CADDY_BAK" "$CADDY_LIVE"')
        assert backup < restore


class TestAGatewayRestartLeavesTheWorkbenchAlone:
    def _unit(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for ln in _WB_UNIT.read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.lstrip().startswith(("#", ";")):
                k, v = ln.split("=", 1)
                out.setdefault(k.strip(), []).extend(v.split())
        return out

    def test_no_hard_dependency_on_the_gateway(self) -> None:
        unit = self._unit()
        for key in ("Requires", "BindsTo", "PartOf", "Requisite"):
            assert "acb-gateway.service" not in unit.get(key, []), (
                f"{key}= makes a gateway restart take the workbench down (H-60)"
            )

    def test_the_gateway_still_starts_first(self) -> None:
        unit = self._unit()
        assert "acb-gateway.service" in unit.get("Wants", [])
        assert "acb-gateway.service" in unit.get("After", [])

    def test_the_new_unit_is_on_the_box_before_the_gateway_restart(self) -> None:
        lines = _executable_lines(_APPLY)
        cp = _first(lines, "deploy/hostinger/acb-workbench.service")
        reload_ = _first(lines, "sudo systemctl daemon-reload", cp)
        assert cp < reload_ < _first(lines, "sudo systemctl restart acb-gateway")

    def test_the_workbench_restarts_once(self) -> None:
        lines = _executable_lines(_APPLY)
        assert sum("systemctl restart acb-workbench" in ln for ln in lines) == 1


class TestTheRestartOrder:
    def test_each_restart_waits_for_the_one_before_it(self) -> None:
        lines = _executable_lines(_APPLY)
        order = [
            _first(lines, "apply_migrations.sh"),
            _first(lines, 'sudo install -m 0644 "$CADDY_REPO" "$CADDY_LIVE"'),
            _first(lines, "sudo systemctl restart acb-gateway"),
            _first(lines, 'wait_ready "gateway" "http://127.0.0.1:8080/health"'),
            _first(lines, "sudo systemctl restart acb-customer-console"),
            _first(lines, 'wait_ready "Customer Console"'),
            _first(lines, 'npm_install_here "workbench"'),
            _first(lines, 'build_next_staged "workbench"'),
            _first(lines, "sudo systemctl restart acb-workbench"),
            _first(lines, 'wait_ready "workbench" "http://127.0.0.1:3001/"'),
            _first(lines, 'sudo systemctl restart "$OC_UNIT"'),
            _first(lines, 'wait_ready "$OC_UNIT"'),
        ]
        assert order == sorted(order), order

    def test_a_service_that_never_answers_fails_the_apply(self) -> None:
        lines = _executable_lines(_APPLY)
        for needle in ('wait_ready "gateway"', 'wait_ready "workbench"'):
            i = _first(lines, needle)
            tail = "\n".join(lines[i : i + 2])
            assert "exit 1" in tail, f"{needle}: a cold service must not pass"


class TestTheInstallKeepsAnUnchangedTree:
    def test_the_check_runs_before_the_first_npm_ci(self) -> None:
        fn = _apply_function("npm_install_here")
        assert fn.index("deps_unchanged") < fn.index("npm ci")

    def test_every_successful_install_records_the_stamp(self) -> None:
        fn = _apply_function("npm_install_here")
        installs = [
            ln for ln in fn.splitlines() if ln.strip().startswith(("npm ci", "npm install"))
        ]
        assert len(installs) == 3, installs
        for ln in installs:
            assert "record_deps_stamp" in ln, f"an install that leaves no stamp: {ln!r}"

    def test_a_failed_install_removes_the_stamp(self) -> None:
        fn = _apply_function("npm_install_here")
        last = [ln for ln in fn.splitlines() if ln.strip().startswith("npm install")][-1]
        assert "rm -f node_modules/.acb-deps-stamp" in fn[fn.index(last) :]


@linux_only
class TestTheReadinessWait:
    def _wait(
        self, tmp_path: pathlib.Path, url: str, seconds: int
    ) -> subprocess.CompletedProcess:
        helpers = _helpers(tmp_path, tmp_path)
        return subprocess.run(
            ["bash", "-c", f'. "{helpers}"; wait_ready svc "{url}" {seconds}; echo "rc=$?"'],
            env=_env(tmp_path, WAIT_READY_POLL="0.2"),
            capture_output=True, text=True, timeout=60,
        )

    def _server(self, status: int, delay: float) -> tuple[subprocess.Popen, int]:
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code = (
            "import http.server, time\n"
            f"time.sleep({delay})\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            f"        self.send_response({status}); self.end_headers()\n"
            "    def log_message(self, *a): pass\n"
            f"http.server.HTTPServer(('127.0.0.1', {port}), H).serve_forever()\n"
        )
        return subprocess.Popen([sys.executable, "-c", code]), port

    def test_it_waits_for_a_cold_service(self, tmp_path: pathlib.Path) -> None:
        srv, port = self._server(200, delay=1.5)
        try:
            t0 = time.monotonic()
            res = self._wait(tmp_path, f"http://127.0.0.1:{port}/health", 20)
            took = time.monotonic() - t0
        finally:
            _stop(srv)
        assert "rc=0" in res.stdout, res.stdout + res.stderr
        assert "answers HTTP 200" in res.stdout
        assert took >= 1.0, f"it returned after {took:.1f}s, before the service listened"

    def test_a_service_that_never_listens_times_out(self, tmp_path: pathlib.Path) -> None:
        srv, port = self._server(200, delay=600)
        try:
            res = self._wait(tmp_path, f"http://127.0.0.1:{port}/", 2)
        finally:
            _stop(srv)
        assert "rc=1" in res.stdout, res.stdout
        assert "did not answer" in res.stdout

    def test_a_5xx_is_not_ready(self, tmp_path: pathlib.Path) -> None:
        srv, port = self._server(500, delay=0)
        try:
            time.sleep(0.5)
            res = self._wait(tmp_path, f"http://127.0.0.1:{port}/", 2)
        finally:
            _stop(srv)
        assert "rc=1" in res.stdout, res.stdout
        assert "last answer: 500" in res.stdout

    def test_a_redirect_is_ready(self, tmp_path: pathlib.Path) -> None:
        """The workbench answers `/` with 307 to the login page."""
        srv, port = self._server(307, delay=0)
        try:
            res = self._wait(tmp_path, f"http://127.0.0.1:{port}/", 10)
        finally:
            _stop(srv)
        assert "rc=0" in res.stdout, res.stdout + res.stderr


@linux_only
class TestTheDependencyStamp:
    def _app(self, tmp_path: pathlib.Path) -> pathlib.Path:
        app = tmp_path / "wb"
        (app / "node_modules/.bin").mkdir(parents=True)
        nxt = app / "node_modules/.bin/next"
        nxt.write_text("#!/bin/sh\n")
        nxt.chmod(0o755)
        (app / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
        return app

    def _run(self, tmp_path: pathlib.Path, app: pathlib.Path, cmd: str, **extra: str) -> str:
        helpers = _helpers(tmp_path, tmp_path)
        res = subprocess.run(
            ["bash", "-c", f'. "{helpers}"; cd "{app}"; {cmd}'],
            env=_env(tmp_path, **extra), capture_output=True, text=True, timeout=30,
        )
        return res.stdout + res.stderr

    def _kept(self, tmp_path: pathlib.Path, app: pathlib.Path, **extra: str) -> bool:
        return "KEEP" in self._run(tmp_path, app, "deps_unchanged && echo KEEP", **extra)

    def test_no_stamp_means_install(self, tmp_path: pathlib.Path) -> None:
        assert not self._kept(tmp_path, self._app(tmp_path))

    def test_a_recorded_install_is_kept(self, tmp_path: pathlib.Path) -> None:
        app = self._app(tmp_path)
        self._run(tmp_path, app, "record_deps_stamp")
        assert self._kept(tmp_path, app)

    def test_a_changed_lock_file_means_install(self, tmp_path: pathlib.Path) -> None:
        app = self._app(tmp_path)
        self._run(tmp_path, app, "record_deps_stamp")
        (app / "package-lock.json").write_text('{"lockfileVersion": 3, "x": 1}\n')
        assert not self._kept(tmp_path, app)

    def test_force_means_install(self, tmp_path: pathlib.Path) -> None:
        app = self._app(tmp_path)
        self._run(tmp_path, app, "record_deps_stamp")
        assert not self._kept(tmp_path, app, DEPLOY_FORCE="1")

    def test_a_tree_without_next_means_install(self, tmp_path: pathlib.Path) -> None:
        app = self._app(tmp_path)
        self._run(tmp_path, app, "record_deps_stamp")
        (app / "node_modules/.bin/next").unlink()
        assert not self._kept(tmp_path, app)
