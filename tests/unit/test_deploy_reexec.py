"""The apply runs the steps of the commit it records, and of no other commit.

🔴 **Measured on production, 2026-10-08.** A deploy of 90fc39e3 started its
own copy of ``scripts/vps_apply.sh``. PR #756 (WS-49 BH-7) merged as 469f5081
during that apply and added a step. The pull step of the old copy reset the
checkout to 469f5081. The old copy then ran its OLD steps, and at its end it
recorded 469f5081 as applied. Each later deploy of 469f5081 printed "already
at 469f508134f3, skipping", so the BH-7 drop-ins never went in. Every deploy
job was green. This is a sibling of H-137.

The fix has two parts, and this file fences both:

1. After the reset, the apply compares its running copy with the target's
   copy. When they differ, or when the running copy came from stdin, it
   ``exec``s the target's copy one time. ``VPS_APPLY_REEXECED=1`` stops a
   second exec. The lock on fd 8 stays held through the exec.
2. ``record_applied_sha`` writes the marker only when HEAD's own copy of the
   script is the copy that ran the steps.

The BEHAVIOURAL tests build an OLD and a NEW script out of the REAL file:
everything up to the end of the pull step, one stand-in step, and the real
final lines. A real git repo holds them as two commits. They need Linux
(``flock``, ``stat -c``), so they skip on the Windows dev box and run in CI.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"

# The first step after the pull. Everything before it is the real prologue:
# the helpers, the lock, the pull and (with the fix) the re-exec.
_FIRST_STEP = 'echo "==> Skipping deprecated LiteLLM proxy cleanup'
# The real final lines: the marker write and the line deploy.yml greps for.
_RECORD_CALL = 'record_applied_sha "$(git -C "$APP_DIR" rev-parse HEAD)"'


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


class TestTheReexecIsWired:
    def test_the_self_hash_is_taken_before_the_lock_and_the_pull(self) -> None:
        lines = _executable_lines(_APPLY)
        take = _first(lines, 'VPS_APPLY_SELF_SUM="$(sha256sum < "${BASH_SOURCE[0]}"')
        assert take < _first(lines, 'echo "==> Taking the deploy lock')
        assert take < _first(lines, "git fetch origin main")

    def test_the_reexec_follows_the_reset_and_precedes_the_first_step(self) -> None:
        lines = _executable_lines(_APPLY)
        reset = _first(lines, "git reset --hard origin/main")
        reexec = _first(lines, "exec env VPS_APPLY_REEXECED=1")
        step = _first(lines, _FIRST_STEP)
        assert reset < reexec < step

    def test_the_reexec_keeps_the_lock_and_drops_stdin(self) -> None:
        body = _APPLY.read_text(encoding="utf-8")
        call = body[body.index("exec env VPS_APPLY_REEXECED=1") :]
        call = call[: call.index("< /dev/null") + len("< /dev/null")]
        assert "DEPLOY_LOCK_HELD=1" in call, "the new copy would wait on its own lock"
        assert 'DEPLOY_TARGET_SHA="$DEPLOY_TARGET_SHA"' in call
        assert 'DEPLOY_TETHER_ANCHOR="$DEPLOY_SESSION_ANCHOR"' in call
        assert 'git show "$DEPLOY_TARGET_SHA:scripts/vps_apply.sh"' in body

    def test_the_reexeced_copy_skips_the_fetch_the_skip_and_the_watcher(self) -> None:
        lines = _executable_lines(_APPLY)
        pull = _first(lines, 'echo "==> Pulling latest')
        branch = _first(lines, 'if [ "${VPS_APPLY_REEXECED:-0}" = "1" ]; then', pull)
        other = next(i for i in range(branch, len(lines)) if lines[i].strip() == "else")
        assert branch == pull + 1
        for needle in (
            "git fetch origin main",
            'deploy_already_applied "$DEPLOY_TARGET_SHA"',
            "git reset --hard origin/main",
            "exec env VPS_APPLY_REEXECED=1",
        ):
            assert _first(lines, needle) > other, f"{needle} must sit in the first copy's branch"
        tether = next(i for i, ln in enumerate(lines) if ln.strip() == "tether_to_session")
        assert 'VPS_APPLY_REEXECED:-0}" != "1"' in lines[tether - 1]

    def test_the_temp_copy_is_removed_before_any_step_can_exit(self) -> None:
        lines = _executable_lines(_APPLY)
        rm = _first(lines, 'acb-vps-apply-reexec.*) rm -f "$VPS_APPLY_REEXEC_FILE"')
        assert _first(lines, 'VPS_APPLY_SELF_SUM="$(sha256sum') < rm, "hash first, then remove"
        assert rm < _first(lines, "if deploy_session_ended; then")
        assert rm < _first(lines, 'echo "==> Taking the deploy lock')

    def test_the_marker_names_the_script_that_ran(self) -> None:
        lines = _executable_lines(_APPLY)
        call = [ln for ln in lines if _RECORD_CALL in ln]
        assert len(call) == 1
        assert call[0].strip() == f'{_RECORD_CALL} "$VPS_APPLY_SELF_SUM"'


# ── Behavioural: Linux only ──────────────────────────────────────────────────

_LINUX = sys.platform.startswith("linux") and all(
    shutil.which(t) for t in ("bash", "git", "flock", "sha256sum", "mv")
)
linux_only = pytest.mark.skipif(not _LINUX, reason="needs Linux with git, flock and stat -c")


def _step(tag: str) -> str:
    """A stand-in step. It logs its tag, and whether the deploy lock is held."""
    return (
        f'echo "==> {tag} step"\n'
        f'echo {tag} >> "$STEP_LOG"\n'
        f'if flock -n "$DEPLOY_LOCK" true; then echo "{tag} free" >> "$LOCK_LOG"; '
        f'else echo "{tag} held" >> "$LOCK_LOG"; fi\n'
    )


def _script(tag: str, extra: str = "") -> str:
    """The REAL prologue and final lines of vps_apply.sh, with one stand-in step."""
    body = _APPLY.read_text(encoding="utf-8")
    head = body[: body.index(_FIRST_STEP)]
    tail = body[body.index(_RECORD_CALL) :]
    return head + _step(tag) + extra + tail


def _git(*args: str, cwd: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(seed: pathlib.Path, script: str) -> str:
    target = seed / "scripts/vps_apply.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8", newline="\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "x", cwd=seed)
    _git("push", "-q", "origin", "HEAD:main", cwd=seed)
    return _git("rev-parse", "HEAD", cwd=seed)


class _Box:
    """A checkout at commit A, and an origin/main that moved to commit B."""

    def __init__(self, tmp: pathlib.Path, old: str, new: str) -> None:
        self.tmp = tmp
        origin = tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        seed = tmp / "seed"
        subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True,
                       capture_output=True)
        # The prologue restores agents.json into this directory when a backup
        # exists in /tmp. Keep the directory, so that a stray backup is harmless.
        (seed / "apps/services/gateway").mkdir(parents=True)
        (seed / "apps/services/gateway/.keep").write_text("")
        self.sha_a = _commit(seed, old)
        self.app = tmp / "app"
        subprocess.run(["git", "clone", "-q", str(origin), str(self.app)], check=True,
                       capture_output=True)
        self.sha_b = _commit(seed, new)   # merged while the apply runs
        self.stubs = tmp / "stubs"
        self.stubs.mkdir()
        real_mv = shutil.which("mv")
        (self.stubs / "sudo").write_text("#!/bin/sh\nexit 0\n")
        # Count each write of the marker. record_applied_sha moves a temp file
        # onto DEPLOY_MARKER, so a move to that path is one write.
        (self.stubs / "mv").write_text(
            "#!/bin/bash\n"
            'for a in "$@"; do [ "$a" = "$DEPLOY_MARKER" ] && echo w >> "$MARKER_WRITES"; done\n'
            f'exec "{real_mv}" "$@"\n'
        )
        for stub in self.stubs.iterdir():
            stub.chmod(0o755)
        self.tmpdir = tmp / "t"
        self.tmpdir.mkdir()
        self.marker = tmp / "acb-deploy.applied"
        self.lock = tmp / "acb-deploy.lock"
        self.lock.touch()   # vps_pull.sh creates it before it opens fd 8
        self.steps = tmp / "steps.log"
        self.locks = tmp / "locks.log"
        self.writes = tmp / "marker-writes.log"

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            APP_DIR=str(self.app),
            DEPLOY_LOCK=str(self.lock),
            DEPLOY_MARKER=str(self.marker),
            DEPLOY_LOCK_WAIT="3",
            DEPLOY_TETHER="0",
            TMPDIR=str(self.tmpdir),
            STEP_LOG=str(self.steps),
            LOCK_LOG=str(self.locks),
            MARKER_WRITES=str(self.writes),
            PATH=f"{self.stubs}{os.pathsep}{env.get('PATH', '')}",
        )
        env.update(extra)
        return env

    def read(self, path: pathlib.Path) -> list[str]:
        return path.read_text().splitlines() if path.exists() else []


def _run_push_path(box: _Box, script: str) -> subprocess.CompletedProcess:
    """deploy.yml: `ssh … bash -s < /tmp/deploy_remote.sh`. No file holds it."""
    return subprocess.run(
        ["bash", "-s"], input=script, env=box.env(), cwd=box.tmp,
        capture_output=True, text=True, timeout=120,
    )


def _run_pull_path(box: _Box, script: str) -> subprocess.CompletedProcess:
    """vps_pull.sh: it holds the lock on fd 8, then runs a temp copy with
    DEPLOY_LOCK_HELD=1. A re-exec that took the lock again would wait on this
    holder for DEPLOY_LOCK_WAIT seconds and then exit 1."""
    copy = box.tmp / "pulled-copy.sh"
    copy.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run(
        ["bash", "-c",
         'exec 8<"$DEPLOY_LOCK"; flock -n 8 || exit 9; '
         f'DEPLOY_LOCK_HELD=1 bash "{copy}"'],
        env=box.env(), cwd=box.tmp, capture_output=True, text=True, timeout=120,
    )


def _run_by_hand(box: _Box, _script: str) -> subprocess.CompletedProcess:
    """The runbook: `bash scripts/vps_apply.sh` from the checkout. The reset
    rewrites the very file that bash reads."""
    return subprocess.run(
        ["bash", "scripts/vps_apply.sh"], env=box.env(), cwd=box.app,
        capture_output=True, text=True, timeout=120,
    )


@linux_only
class TestAMergeDuringTheApply:
    @pytest.mark.parametrize("run", [_run_push_path, _run_pull_path, _run_by_hand],
                             ids=["push-path-stdin", "pull-path-file", "by-hand-checkout"])
    def test_the_new_step_runs_once_and_the_marker_is_written_once(
        self, tmp_path: pathlib.Path, run
    ) -> None:
        old, new = _script("OLD"), _script("NEW")
        box = _Box(tmp_path, old, new)
        res = run(box, old)
        out = res.stdout + res.stderr
        assert res.returncode == 0, out
        assert box.read(box.steps) == ["NEW"], (
            f"the steps of the pulled commit must run, once, and no others:\n{out}"
        )
        assert box.read(box.writes) == ["w"], f"one marker write, no more:\n{out}"
        assert box.marker.read_text().split()[0] == box.sha_b, out
        assert out.count("==> Deployment complete") == 1, out
        assert out.count("(re-exec)") == 1, out
        assert "NOT recording" not in out, out
        assert not list(box.tmpdir.glob("acb-vps-apply-reexec.*")), "the temp copy is left"

    @pytest.mark.parametrize("run", [_run_push_path, _run_pull_path],
                             ids=["push-path-stdin", "pull-path-file"])
    def test_the_lock_is_held_through_the_reexec(self, tmp_path: pathlib.Path, run) -> None:
        box = _Box(tmp_path, _script("OLD"), _script("NEW"))
        res = run(box, _script("OLD"))
        out = res.stdout + res.stderr
        assert res.returncode == 0, f"the re-exec waited on its own lock:\n{out}"
        assert box.read(box.locks) == ["NEW held"], out
        assert "held by the caller (vps_pull.sh, or this script before its re-exec)" in out

    def test_a_copy_equal_to_the_target_runs_without_a_reexec(
        self, tmp_path: pathlib.Path
    ) -> None:
        new = _script("NEW")
        box = _Box(tmp_path, new, new + "\n# an unrelated change to the file\n")
        res = _run_pull_path(box, new + "\n# an unrelated change to the file\n")
        out = res.stdout + res.stderr
        assert res.returncode == 0, out
        assert "(re-exec)" not in out, out
        assert box.read(box.steps) == ["NEW"], out
        assert box.marker.read_text().split()[0] == box.sha_b, out


@linux_only
class TestNoLoopAndNoFalseMarker:
    def test_a_reexeced_copy_that_still_differs_runs_once_and_records_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Impossible in practice: the first copy reads the target's copy out
        of git. So set the stage by hand. HEAD is B, and B's copy is NEW. The
        copy that runs is NEW2, and it says that it was re-executed already.
        It must not exec again, and it must not record B."""
        box = _Box(tmp_path, _script("OLD"), _script("NEW"))
        _git("fetch", "-q", "origin", cwd=box.app)
        _git("reset", "-q", "--hard", "origin/main", cwd=box.app)
        copy = tmp_path / "differs.sh"
        copy.write_text(_script("NEW2"), encoding="utf-8", newline="\n")
        res = subprocess.run(
            ["bash", "-c",
             'exec 8<"$DEPLOY_LOCK"; flock -n 8 || exit 9; '
             f'bash "{copy}"'],
            env=box.env(
                VPS_APPLY_REEXECED="1", DEPLOY_LOCK_HELD="1", DEPLOY_TARGET_SHA=box.sha_b,
                VPS_APPLY_REEXEC_FILE=str(copy),
            ),
            cwd=tmp_path, capture_output=True, text=True, timeout=120,
        )
        out = res.stdout + res.stderr
        assert res.returncode == 0, out
        assert box.read(box.steps) == ["NEW2"], out
        assert "(re-exec)" not in out, f"it exec'd again:\n{out}"
        assert "NOT recording" in out, out
        assert not box.marker.exists(), "a marker for steps that B does not hold"
        assert box.read(box.writes) == [], out
        assert out.count("==> Deployment complete") == 1, out
        assert copy.exists(), "a file without the temp name must never be removed"

    def test_the_helper_alone_still_records_without_a_hash(self, tmp_path: pathlib.Path) -> None:
        """`record_applied_sha <sha>` with one argument keeps its old meaning.
        `test_deploy_serialize.py` round-trips the marker through it."""
        box = _Box(tmp_path, _script("OLD"), _script("NEW"))
        body = _APPLY.read_text(encoding="utf-8")
        block = body[body.index("# >>> deploy-serialize helpers") :
                     body.index("# <<< deploy-serialize helpers")]
        helpers = tmp_path / "helpers.sh"
        helpers.write_text(f'APP_DIR="{box.app}"\n{block}', encoding="utf-8")
        subprocess.run(
            ["bash", "-c", f'. "{helpers}"; record_applied_sha "{box.sha_a}"'],
            env=box.env(), check=True, timeout=30,
        )
        assert box.marker.read_text().split()[0] == box.sha_a


@linux_only
class TestTheSessionAcrossTheReexec:
    def test_one_watcher_before_and_after_the_exec(self, tmp_path: pathlib.Path) -> None:
        """The watcher of the first copy watches the pid, and `exec` keeps the
        pid. So the re-executed copy starts no second watcher, and the one
        watcher is still there when the new steps run."""
        if shutil.which("ps") is None:
            pytest.skip("needs ps")
        children = tmp_path / "children.log"
        # `ps` runs as a direct child of the apply, so it lists itself too.
        extra = f'ps -o comm= --ppid $$ > "{children}"\n'
        old, new = _script("OLD"), _script("NEW", extra)
        box = _Box(tmp_path, old, new)
        anchor = subprocess.Popen(["sleep", "300"])
        try:
            res = subprocess.run(
                ["bash", "-s"], input=old, cwd=tmp_path,
                env=box.env(DEPLOY_TETHER="1", DEPLOY_TETHER_ANCHOR=str(anchor.pid),
                            DEPLOY_TETHER_POLL="0.2"),
                capture_output=True, text=True, timeout=120,
            )
        finally:
            anchor.kill()
            anchor.wait()
        out = res.stdout + res.stderr
        assert res.returncode == 0, out
        assert out.count("(re-exec)") == 1, out
        assert out.count("tethered to deploy session") == 1, out
        assert box.read(box.steps) == ["NEW"], out
        watchers = [c.strip() for c in box.read(children) if c.strip() != "ps"]
        assert watchers == ["bash"], f"one watcher child after the exec, found {watchers}"

    def test_a_session_end_before_the_new_steps_says_what_is_true(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The first copy already reset the checkout. So the message must not
        say "did NOTHING", and the temp copy must not stay behind."""
        box = _Box(tmp_path, _script("OLD"), _script("NEW"))
        gone = subprocess.Popen(["true"])
        gone.wait()
        copy = box.tmpdir / "acb-vps-apply-reexec.TEST01"
        copy.write_text(_script("NEW"), encoding="utf-8", newline="\n")
        res = subprocess.run(
            ["bash", "-c",
             'exec 8<"$DEPLOY_LOCK"; flock -n 8 || exit 9; '
             f'bash "{copy}"'],
            env=box.env(
                VPS_APPLY_REEXECED="1", VPS_APPLY_REEXEC_FILE=str(copy),
                DEPLOY_LOCK_HELD="1", DEPLOY_TARGET_SHA=box.sha_b,
                DEPLOY_TETHER="1", DEPLOY_TETHER_ANCHOR=str(gone.pid),
            ),
            cwd=tmp_path, capture_output=True, text=True, timeout=120,
        )
        out = res.stdout + res.stderr
        assert res.returncode == 1, out
        assert "ended before the re-executed copy began its steps" in out, out
        assert "already reset to" in out and "No marker was written" in out, out
        assert "did NOTHING" not in out, out
        assert box.read(box.steps) == [], out
        assert not box.marker.exists(), out
        assert not copy.exists(), "an early exit left the temp copy behind"
