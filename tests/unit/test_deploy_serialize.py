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
        lines = _executable_lines(_APPLY)
        assert any("timeout -k" in ln and "npm run build" in ln for ln in lines)
        assert not any(
            ln.strip().startswith("NODE_OPTIONS=") and "npm run build" in ln
            and "timeout" not in ln
            for ln in lines
        ), "an unbounded build is how the 41-minute orphan happened"

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

    def test_the_watcher_never_holds_the_lock(self) -> None:
        body = _APPLY.read_text(encoding="utf-8")
        fn = body[body.index("tether_to_session() {"):]
        fn = fn[: fn.index("\n}\n")]
        assert "exec 8<&-" in fn


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


@linux_only
class TestAnEndedSessionEndsTheBuild:
    def test_the_build_dies_when_the_session_does(self, tmp_path: pathlib.Path) -> None:
        """A stand-in for sshd, an apply tethered to it, and a long "build"
        under the apply. Kill the stand-in: the build and the apply must go.
        This is the 41-minute orphan of 2026-09-25, in miniature."""
        session = subprocess.Popen(["sleep", "300"])
        helpers = _helpers(tmp_path, tmp_path)
        child_file = tmp_path / "build.pid"
        apply = subprocess.Popen(
            [
                "bash", "-c",
                f'. "{helpers}"; tether_to_session; '
                f'sleep 300 & echo $! > "{child_file}"; wait',
            ],
            env=_env(
                tmp_path,
                DEPLOY_TETHER="1",
                DEPLOY_TETHER_ANCHOR=str(session.pid),
                DEPLOY_TETHER_POLL="0.2",
                DEPLOY_TETHER_GRACE="1",
            ),
        )
        try:
            assert _wait_for(lambda: child_file.exists() and child_file.read_text().strip())
            build = int(child_file.read_text().strip())
            assert _alive(build)
            session.kill()
            session.wait()
            assert _wait_for(lambda: apply.poll() is not None, 15), "the apply outlived its session"
            assert _wait_for(lambda: not _alive(build), 15), "the build outlived its session"
        finally:
            for p in (session, apply):
                if p.poll() is None:
                    p.kill()
                    p.wait()

    def test_a_live_session_leaves_the_apply_alone(self, tmp_path: pathlib.Path) -> None:
        session = subprocess.Popen(["sleep", "300"])
        helpers = _helpers(tmp_path, tmp_path)
        try:
            res = subprocess.run(
                ["bash", "-c", f'. "{helpers}"; tether_to_session; sleep 1; echo DONE'],
                env=_env(
                    tmp_path,
                    DEPLOY_TETHER="1",
                    DEPLOY_TETHER_ANCHOR=str(session.pid),
                    DEPLOY_TETHER_POLL="0.2",
                ),
                capture_output=True, text=True, timeout=30,
            )
            assert "DONE" in res.stdout
            assert "tethered to deploy session" in res.stdout
        finally:
            session.kill()
            session.wait()
