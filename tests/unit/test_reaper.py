"""Fence for scripts/reaper.py (CIV-14j, CIV-16): fake containers, worktrees, clock, ps and kill.

One live test starts real loops with a tiny limit and stops only its own pids.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import contextlib

import reaper

NOW = 1_000_000.0
H = 3600


def wt(slug, head="abc", dirty=False, stack=None, age_h=0.0, branch=None):
    return reaper.Worktree(Path(f"/home/dev/wt-{slug}"), branch or slug, head, dirty, stack, NOW - age_h * H)


def acts(**kw):
    return reaper.plan(reaper.Env(now=NOW, **kw))


def _c(slug):
    return [(f"metorite-dev-{slug}-pg", f"/home/dev/wt-{slug}"), (f"metorite-dev-{slug}-redis", f"/home/dev/wt-{slug}")]


def test_stack_with_no_worktree_stops():
    out = acts(containers=_c("gone"), existing=set())
    assert [(a["act"], a["repo"]) for a in out] == [("stop-stack", "/home/dev/wt-gone")]


def test_idle_stack_stops_and_live_one_stays():
    out = acts(
        containers=_c("old") + _c("live") + _c("fresh"),
        existing={"/home/dev/wt-old", "/home/dev/wt-live", "/home/dev/wt-fresh"},
        worktrees=[wt("old", age_h=17), wt("live", age_h=17), wt("fresh", age_h=1)],
        busy_paths={"/home/dev/wt-live/apps"},
    )
    assert [a["repo"] for a in out] == ["/home/dev/wt-old"]


def test_stack_of_the_primary_checkout_or_an_untracked_path_stays():
    """The primary checkout is no wt-* dir. Its live stack must not read as 'worktree gone'."""
    cs = [("metorite-dev-metorite-pg", "/home/dev/Metorite"), ("metorite-dev-preview-pg", "/tmp/x")]
    assert acts(containers=cs, existing={"/home/dev/Metorite", "/tmp/x"}) == []


def test_container_without_label_is_ignored_by_collect_contract():
    assert acts(containers=[("metorite-dev-x-pg", "")]) == []


def test_merged_clean_idle_worktree_is_removed():
    w = wt("done", head="sha1")
    out = acts(worktrees=[w], merged_heads={"done": {"sha1"}})
    assert out == [{"act": "remove-worktree", "slug": "done", "path": "/home/dev/wt-done", "why": "PR merged at sha1"}]


def test_worktree_is_kept_when_dirty_busy_head_differs_or_unmerged():
    cases = [
        (wt("a", head="s1", dirty=True), {"a": {"s1"}}, set()),
        (wt("b", head="s1"), {"b": {"s1"}}, {"/home/dev/wt-b"}),
        (wt("c", head="s2"), {"c": {"s1"}}, set()),
        (wt("d", head="s1"), {}, set()),
    ]
    for w, merged, busy in cases:
        assert acts(worktrees=[w], merged_heads=merged, busy_paths=busy) == [], w.path


# --- job 3: wait loops and hung test runs (CIV-16) -----------------------------

ME = 1001
LOOP = "bash -c until [ -f /tmp/x ]; do sleep 5; done"
PYTEST = "/home/dev/wt-a/.venv/bin/python /home/dev/wt-a/.venv/bin/pytest tests/unit/test_x.py -q"
CLAUDE = "claude bg-spare --bg-spare /tmp/cc/x.claim.sock"
L = reaper.Limits()


def P(pid, ppid, hours, cmd, uid=ME, start=None):
    return reaper.Proc(pid, ppid, int(hours * H), uid, cmd, pid * 10 if start is None else start)


DEAD = 4_194_304  # above the default pid_max: no process has this pid


def stale(procs, sessions=None, self_pid=9000, uid=ME, environ=None, cwds=None):
    """`environ` and `cwds` are the fake /proc: pid -> its environment, pid -> its cwd."""
    rows = [*list(procs), P(self_pid, 8999, 0, "python3 scripts/reaper.py"), P(8999, 1, 0, "bash -c python3 scripts/reaper.py")]
    env = reaper.Env(now=NOW, procs=rows, uid=uid, self_pid=self_pid, sessions=sessions or {},
                     environ_of=(environ or {}).get, cwd_of=(cwds or {}).get)
    return [a["pid"] for a in reaper.stale_processes(env)]


def of(cpid):
    return {"CLAUDE_PID": str(cpid), "PATH": "/usr/bin"}


def session(root_pid=100, idle_h=8.0, child_h=8.0, busy=False):
    """A live claude root (pid 100) with an MCP child, and its session state."""
    procs = [P(root_pid, 1, 9, CLAUDE), P(101, root_pid, child_h, "node /home/dev/.local/bin/codegraph serve --mcp")]
    return procs, {root_pid: reaper.Session(idle_h * H, busy)}


def test_matchers_name_loops_and_pytest_and_nothing_else():
    assert reaper.is_wait_loop(LOOP)
    assert reaper.is_wait_loop("/bin/bash -c source snap.sh && eval 'while ! curl -s x; do sleep 2; done' < /dev/null")
    assert reaper.is_pytest(PYTEST)
    assert reaper.is_pytest("python3 -m pytest tests/unit -q")
    assert reaper.is_pytest("/usr/bin/pytest -x")
    for cmd in [
        "grep -r 'until.*sleep' scripts", "ugrep -E while.*sleep.*done x", "vim scripts/reaper.py",
        "bash -c grep 'while x; do sleep 1; done' f", "node server.js", "uv run pytest tests/unit/test_x.py",
        "timeout 300 uv run pytest tests/unit/test_x.py", "python scripts/affected_tests.py --run",
        "bash -c echo hi; sleep 5", CLAUDE,
    ]:
        assert not (reaper.is_wait_loop(cmd) or reaper.is_pytest(cmd)), cmd
    # The quoted grep above holds the loop as data: the shell does not run it.
    assert not reaper.is_wait_loop("bash -c grep -c 'while' f; sleep 1")


def test_young_loop_is_not_listed():
    assert stale([P(10, 1, 1.9, LOOP), P(11, 1, 0.1, LOOP)]) == []


def test_loop_7h_under_an_active_session_is_not_listed():
    procs, sess = session(idle_h=0.1)  # transcript written 6 min ago
    assert stale([*procs, P(10, 100, 7, LOOP)], sess) == []
    procs, sess = session(busy=True)
    assert stale([*procs, P(10, 100, 7, LOOP)], sess) == []
    procs, sess = session(idle_h=5)  # idle, but not past the 6 h limit of the loop
    assert stale([*procs, P(10, 100, 7, LOOP)], sess) == []


def test_loop_under_an_ended_session_is_listed_after_2h():
    assert stale([P(10, 1, 2.1, LOOP)], environ={10: of(DEAD)}) == [10]
    assert stale([P(10, 1, 1.9, LOOP)], environ={10: of(DEAD)}) == []
    # The Bash tool wrapper orphaned, the loop under it: a chain of shells only.
    # Only the wrapper at the top has a readable environment. The loop takes it.
    wrapper = "/bin/bash -c source snap.sh && eval 'timeout 590 bash -c \"until x; do sleep 5; done\"'"
    chain = [P(20, 1, 7, wrapper), P(21, 20, 7, "timeout 590 bash -c until x; do sleep 5; done"), P(22, 21, 7, LOOP)]
    # The wrapper (20) only passes the loop text to `bash -c` as a quoted word.
    # It does not run the loop itself, so the loop (22) alone is listed.
    assert stale(chain, environ={20: of(DEAD)}) == [22]


def test_loop_under_an_idle_session_is_listed_after_6h():
    procs, sess = session(idle_h=7)
    assert stale([*procs, P(10, 100, 7, LOOP)], sess) == [10]


def test_pytest_4h_under_no_session_is_listed():
    chain = [P(30, 1, 4, "/bin/bash -c eval 'timeout 9000 uv run pytest x'"), P(31, 30, 4, "timeout 9000 uv run pytest x"),
             P(32, 31, 4, "uv run pytest x"), P(33, 32, 4, PYTEST)]
    assert stale(chain, environ={30: of(DEAD)}) == [33]
    assert stale([P(33, 1, 2.9, PYTEST)], environ={33: of(DEAD)}) == []


def test_pytest_4h_whose_claude_root_has_a_2_minute_old_child_is_not_listed():
    procs, sess = session(idle_h=9)
    procs.append(P(102, 100, 2 / 60, "/bin/bash -c eval 'git status'"))
    assert stale([*procs, P(33, 100, 4, PYTEST)], sess) == []


def test_sleep_child_of_a_loop_does_not_make_the_session_look_active():
    procs, sess = session(idle_h=7)
    procs += [P(10, 100, 7, LOOP), P(11, 10, 1 / 3600, "sleep 5")]
    assert stale(procs, sess) == [10]


def test_reaper_its_parent_and_ancestors_are_never_listed():
    # The reaper runs inside an old orphan loop, under an old orphan pytest.
    procs = [P(40, 1, 9, PYTEST), P(41, 40, 9, LOOP), P(9000, 41, 9, "python3 scripts/reaper.py")]
    env = reaper.Env(now=NOW, procs=procs, uid=ME, self_pid=9000)
    assert reaper.stale_processes(env) == []


def test_another_users_process_is_never_listed():
    assert stale([P(10, 1, 9, LOOP, uid=0), P(11, 1, 9, PYTEST, uid=1002)]) == []


def test_unknown_session_state_is_not_listed():
    # A live claude root with no session file: unknown.
    procs, _ = session()
    assert stale([*procs, P(10, 100, 7, LOOP)], {100: reaper.Session(None)}) == []
    assert stale([*procs, P(10, 100, 7, LOOP)], {}) == []
    # Only the claude daemon above it: unknown.
    assert stale([P(50, 1, 9, "claude daemon run --json-path x"), P(10, 50, 7, LOOP)]) == []
    # A parent that is not in ps: unknown.
    assert stale([P(10, 77, 7, LOOP)]) == []
    # A parent that is not a shell (tmux, systemd, a node host): unknown.
    assert stale([P(60, 1, 9, "tmux new -d"), P(10, 60, 7, LOOP)]) == []
    # No start time: the stop could not guard against pid reuse.
    rows = [reaper.Proc(10, 1, 7 * H, ME, LOOP, None)]
    assert reaper.stale_processes(reaper.Env(now=NOW, procs=rows, uid=ME, self_pid=9000)) == []
    # No uid (Windows): nothing.
    assert stale([P(10, 1, 9, LOOP)], uid=None) == []


def test_any_active_claude_session_in_the_chain_keeps_it():
    inner = "claude -p 'run the job'"
    procs = [P(100, 1, 9, CLAUDE), P(101, 100, 0.05, "bash -c eval 'ls'"), P(110, 100, 8, inner), P(10, 110, 7, LOOP)]
    sess = {100: reaper.Session(9 * H), 110: reaper.Session(9 * H)}
    assert stale(procs, sess) == []


def test_parse_ps_and_count():
    text = "  10     1  7200  1001 bash -c until x; do sleep 5; done\n bad line\n  11 10 3 1001 sleep 5\n  12 1 9 0 bash\n"
    rows = reaper.parse_ps(text)
    assert [r.pid for r in rows] == [10, 11, 12]
    assert reaper.counts([*rows, P(13, 1, 1, CLAUDE), P(14, 1, 1, PYTEST), P(15, 1, 1, "node x.js")], ME) == {
        "bash": 1, "sleep": 1, "node": 1, "claude": 1, "pytest": 1}


def test_command_label_masks_secrets_and_reads_the_eval():
    raw = "/bin/bash -c source s.sh && eval 'API_TOKEN=abc123 until curl -H \"Bearer xyz\" x; do sleep 5; done' < /dev/null && pwd -P"
    label = reaper.command_label(raw)
    assert label.startswith("API_TOKEN=*** until curl")
    assert "abc123" not in label and "xyz" not in label
    assert len(reaper.command_label("x" * 500)) == 100


class FakeProcs:
    """A fake kill and a fake /proc. `dies_on` is the signal the process obeys."""

    def __init__(self, start=111, dies_on=None):
        self.start, self.dies_on, self.sent = start, dies_on, []

    def kill(self, pid, sig):
        self.sent.append((pid, sig))
        if sig == self.dies_on:
            self.start = None

    def start_of(self, pid):
        return self.start


def _stop(fp, start=111):
    return reaper.stop_process(42, start, kill=fp.kill, start_of=fp.start_of, sleep=lambda s: None, log=lambda m: None)


def test_sigterm_first_then_sigkill_only_if_still_the_same_process():
    fp = FakeProcs(dies_on=reaper.signal.SIGTERM)
    assert _stop(fp) == "term" and fp.sent == [(42, reaper.signal.SIGTERM)]
    fp = FakeProcs(dies_on=reaper.signal.SIGKILL)
    assert _stop(fp) == "kill" and fp.sent == [(42, reaper.signal.SIGTERM), (42, reaper.signal.SIGKILL)]


def test_pid_reuse_guard_start_time_mismatch_sends_nothing():
    fp = FakeProcs(start=999)
    assert _stop(fp, start=111) == "gone" and fp.sent == []

    class Reused(FakeProcs):
        def kill(self, pid, sig):  # SIGTERM ends it, and a new process takes the pid
            super().kill(pid, sig)
            self.start = 555

    fp = Reused()
    assert _stop(fp) == "term" and fp.sent == [(42, reaper.signal.SIGTERM)]


def test_dry_run_and_no_apply_kill_nothing(monkeypatch, capsys):
    act = {"act": "stop-process", "pid": 42, "start": 1, "age": 9 * H, "cmd": LOOP, "why": "orphan wait loop"}
    monkeypatch.setattr(reaper, "collect", lambda home, repo: reaper.Env())
    monkeypatch.setattr(reaper, "plan", lambda env, hours: [act])
    calls = []
    monkeypatch.setattr(reaper, "stop_process", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(reaper.os, "kill", lambda *a: calls.append(a))
    assert reaper.main(["--dry-run", "--apply"]) == 0
    assert reaper.main([]) == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "would stop-process: pid 42" in out and "would stop pid 42" in out


def test_no_proc_host_does_nothing(monkeypatch, capsys):
    monkeypatch.setattr(reaper.Path, "is_dir", lambda self: str(self) != "/proc")
    monkeypatch.setattr(reaper, "collect", lambda *a: (_ for _ in ()).throw(AssertionError("must not run")))
    assert reaper.main(["--apply"]) == 0
    assert "nothing to do" in capsys.readouterr().out


# --- one live proof: real processes, a tiny limit ------------------------------

LIVE = pytest.mark.skipif(not Path("/proc").is_dir() or shutil.which("ps") is None, reason="needs Linux ps and /proc")


def _spawn_orphan(shell_text: str, cwd: Path) -> int:
    """Start `shell_text` in the background of a shell that exits, so init adopts it.

    Its CLAUDE_PID names a pid that no process has: its session ended. Its cwd
    is a temp dir, so no live session on this box works there.
    """
    env = {**os.environ, "CLAUDE_PID": str(DEAD)}
    r = subprocess.run(["bash", "-c", f"{shell_text} >/dev/null 2>&1 </dev/null & echo $!"],
                       capture_output=True, text=True, timeout=10, env=env, cwd=cwd)
    return int(r.stdout.strip())


def _kids(pid: int) -> list[int]:
    return [p.pid for p in reaper._ps() if p.ppid == pid]


@LIVE
def test_live_orphan_loop_dies_and_working_look_alike_survives(tmp_path):
    mark = f"reaper-live-{uuid.uuid4().hex[:10]}"
    loop = f"until false; do sleep 1; done; : {mark}"
    script = tmp_path / "lookalike.sh"
    # The fake session: a loop at once, and a fresh `sleep 600` 3 s later.
    script.write_text(f"bash -c '{loop}' &\nsleep 3\nsleep 600 &\nwait\n", encoding="utf-8")
    orphan = _spawn_orphan(f"bash -c '{loop}'", tmp_path)
    # The fake session works in another dir. In the dir of the orphan, the cwd rule would keep it.
    (tmp_path / "session").mkdir()
    root = _spawn_orphan(f"exec -a claude bash {script}", tmp_path / "session")
    young = []
    try:
        time.sleep(4.5)
        twin = [k for k in _kids(root) if reaper.is_wait_loop(next(p.cmd for p in reaper._ps() if p.pid == k))]
        young = [k for k in _kids(root) if k not in twin]
        assert len(twin) == 1 and young, (twin, young)
        env = reaper.Env()
        reaper.collect_procs(env, tmp_path / "claude")  # an empty claude dir: no session file
        assert env.uid is not None
        limits = reaper.Limits(loop_s=2, orphan_loop_s=2, pytest_s=2, young_child_s=3)
        ours = {orphan, root, twin[0], *young}
        acts = [a for a in reaper.stale_processes(env, limits) if a["pid"] in ours]  # never touch another lane
        assert [a["pid"] for a in acts] == [orphan]
        by_pid = {p.pid: p for p in env.procs}
        state, why = reaper.session_state(env, by_pid[twin[0]], 2, limits)
        assert state == "active" and "young process" in why
        log = []
        claude_dir = tmp_path / "claude"
        reaper.apply(acts, Path("."), log=log.append, recheck=lambda a: reaper.recheck_process(a, claude_dir, limits))
        assert reaper.proc_start(orphan) is None
        assert reaper.proc_start(twin[0]) is not None
        assert any(f"stop pid {orphan}" in m for m in log) and any("ended after SIGTERM" in m for m in log)
    finally:
        for pid in [orphan, root, *_kids(root), *young]:
            with contextlib.suppress(OSError):
                os.kill(pid, reaper.signal.SIGKILL)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", mark.replace("reaper-live", "reaper-liv[e]")], capture_output=True, text=True)
        assert left.stdout.strip() == ""
        assert all(reaper.proc_start(p) is None for p in young)


def test_session_info_reads_the_session_file_and_the_transcript(tmp_path):
    (tmp_path / "sessions").mkdir()
    proj = tmp_path / "projects" / "-home-dev-Metorite"
    (proj / "s1" / "subagents").mkdir(parents=True)

    def write(status="idle", start=77, sid="s1"):
        body = {"pid": 5, "sessionId": sid, "cwd": "/home/dev/Metorite", "procStart": str(start), "status": status}
        f = tmp_path / "sessions" / "5.json"
        f.write_text(json.dumps(body), encoding="utf-8")
        os.utime(f, (NOW - 9 * H, NOW - 9 * H))

    t = proj / "s1.jsonl"
    t.write_text("{}\n", encoding="utf-8")
    os.utime(t, (NOW - 8 * H, NOW - 8 * H))
    write()
    assert reaper.session_info(5, 77, tmp_path, NOW) == reaper.Session(8 * H, False, "/home/dev/Metorite")
    sub = proj / "s1" / "subagents" / "a.jsonl"
    sub.write_text("{}\n", encoding="utf-8")
    os.utime(sub, (NOW - 60, NOW - 60))
    assert reaper.session_info(5, 77, tmp_path, NOW).idle_s == 60  # a subagent works: recent
    write(status="shell")
    assert reaper.session_info(5, 77, tmp_path, NOW).busy
    write(start=78)  # the file names an older process with this pid
    assert reaper.session_info(5, 77, tmp_path, NOW).idle_s is None
    t.write_text("", encoding="utf-8")  # an empty transcript: a remote session
    write()
    assert reaper.session_info(5, 77, tmp_path, NOW).idle_s is None
    assert reaper.session_info(6, 77, tmp_path, NOW) == reaper.Session(None)  # no session file


# --- review round: orphans of a live session, a linear matcher, a re-check -------


def test_orphan_with_claude_pid_of_a_live_busy_session_is_not_listed():
    """A busy session ran `( ... ) &`. Its shell exited and init adopted the job."""
    procs, sess = session(busy=True)
    assert stale([*procs, P(10, 1, 3, LOOP)], sess, environ={10: of(100)}) == []
    assert stale([*procs, P(33, 1, 5, PYTEST)], sess, environ={33: of(100)}) == []
    procs, sess = session(idle_h=0.2)  # it wrote its transcript 12 min ago
    assert stale([*procs, P(10, 1, 3, LOOP)], sess, environ={10: of(100)}) == []


def test_orphan_with_claude_pid_of_a_live_idle_session_is_listed():
    procs, sess = session(idle_h=8)
    assert stale([*procs, P(10, 1, 3, LOOP)], sess, environ={10: of(100)}) == [10]


def test_orphan_with_claude_pid_of_a_dead_or_reused_pid_is_listed():
    assert stale([P(10, 1, 3, LOOP)], environ={10: of(DEAD)}) == [10]
    # Pid 100 is a claude now, but it is younger than the orphan: the pid was reused.
    procs, sess = session(busy=True)
    assert stale([*procs, P(10, 1, 10, LOOP)], sess, environ={10: of(100)}) == [10]
    # Pid 100 is no claude session: the session that started the loop is gone.
    assert stale([P(100, 1, 9, "node x.js"), P(10, 1, 3, LOOP)], environ={10: of(100)}) == [10]


def test_claude_pid_comes_from_an_ancestor_or_a_child_when_the_process_has_none():
    procs, sess = session(busy=True)
    test_run = [P(33, 1, 5, PYTEST), P(34, 33, 5, "python -c from xdist import worker")]
    assert stale([*procs, *test_run], sess, environ={34: of(100)}) == []
    assert stale([*procs, *test_run], sess, environ={33: {"PATH": "/usr/bin"}, 34: of(DEAD)}) == [33]


def test_orphan_with_no_readable_claude_pid_needs_twice_its_limit():
    # Under 2x the limit (4 h loop, 6 h test run): kept, with no session at all.
    assert stale([P(10, 1, 3.9, LOOP)]) == []
    assert stale([P(33, 1, 5.9, PYTEST)], environ={33: {"PATH": "/usr/bin"}}) == []
    # Over 2x and no session on the box: listed.
    assert stale([P(10, 1, 4.1, LOOP), P(33, 1, 6.1, PYTEST)]) == [10, 33]
    # Over 2x, and every session is quiet past the limit: listed.
    procs, sess = session(idle_h=8)
    assert stale([*procs, P(10, 1, 4.1, LOOP)], sess) == [10]
    # Over 2x, but a session wrote inside the limit, or has a young child: kept.
    procs, sess = session(idle_h=1)
    assert stale([*procs, P(10, 1, 4.1, LOOP)], sess) == []
    procs, sess = session(idle_h=8, child_h=1)
    assert stale([*procs, P(10, 1, 4.1, LOOP)], sess) == []
    # Over 2x, and a session with no transcript: we cannot tell, so kept.
    procs, _ = session()
    assert stale([*procs, P(10, 1, 4.1, LOOP)], {100: reaper.Session(None)}) == []


def test_orphan_in_the_cwd_of_a_busy_session_is_not_listed():
    procs, _ = session()
    busy = {100: reaper.Session(8 * H, True, "/home/dev/wt-a")}
    # No readable CLAUDE_PID, over 2x: the cwd match keeps it.
    assert stale([*procs, P(10, 1, 5, LOOP)], busy, cwds={10: "/home/dev/wt-a/apps"}) == []
    # Even a dead CLAUDE_PID: a busy session works in that cwd.
    assert stale([*procs, P(10, 1, 5, LOOP)], busy, environ={10: of(DEAD)}, cwds={10: "/home/dev/wt-a"}) == []
    # The session sits in the main checkout, but a child of it works in the worktree.
    main = {100: reaper.Session(8 * H, True, "/home/dev/Metorite")}
    cwds = {10: "/home/dev/wt-a/apps", 101: "/home/dev/wt-a"}
    assert stale([*procs, P(10, 1, 5, LOOP)], main, environ={10: of(DEAD)}, cwds=cwds) == []
    # Another cwd, or an idle session in the same cwd: the dead CLAUDE_PID decides.
    assert stale([*procs, P(10, 1, 5, LOOP)], main, environ={10: of(DEAD)}, cwds={10: "/home/dev/wt-b"}) == [10]
    idle = {100: reaper.Session(8 * H, False, "/home/dev/wt-a")}
    assert stale([*procs, P(10, 1, 5, LOOP)], idle, environ={10: of(DEAD)}, cwds={10: "/home/dev/wt-a"}) == [10]


def test_loop_match_is_linear_and_still_finds_loops():
    hostile = "bash -c " + "; while x do sleep 1 " * 1200
    assert len(hostile) > 24_000
    t0 = time.perf_counter()
    assert not reaper.is_wait_loop(hostile)
    assert not reaper.is_wait_loop("bash -c " + "eval " * 5000 + "x")
    assert not reaper.is_wait_loop("bash -c eval" + " " * 20_000 + "x")
    assert time.perf_counter() - t0 < 1.0
    t0 = time.perf_counter()
    assert reaper.command_label("bash -c " + "a" * 13_000)
    assert time.perf_counter() - t0 < 1.0
    for cmd in [
        LOOP, "bash -c while true; do sleep 60; done",
        "bash -c cd /x; (until [ -f done.txt ]; do sleep 60; done) &",
        "/bin/bash -c source snap.sh && eval 'while ! curl -s x; do sleep 2; done' < /dev/null",
        "bash -c eval \"until x; do sleep 2; done\"",
        "bash -c set -e\nwhile pgrep x\ndo\n  sleep 5\ndone",
    ]:
        assert reaper.is_wait_loop(cmd), cmd
    # A loop past the cap is not seen: the reaper keeps it.
    assert not reaper.is_wait_loop("bash -c " + "x" * 5000 + "; while x; do sleep 1; done")


def test_command_label_masks_a_secret_after_a_known_flag():
    for flag in ("--token", "--password", "--api-key", "--access-token"):
        label = reaper.command_label(f"curl {flag} s3cr3t https://x {flag}=t0p")
        assert "s3cr3t" not in label and "t0p" not in label, label
        assert f"{flag} ***" in label
    # A short flag stays: `-p` is a prompt in claude and a plugin in pytest.
    assert "-p no:cacheprovider" in reaper.command_label("pytest -p no:cacheprovider")


def test_apply_reads_the_state_again_and_skips_a_process_whose_session_woke():
    procs, sess = session(idle_h=8)
    rows = [*procs, P(10, 100, 7, LOOP)]
    env = reaper.Env(now=NOW, procs=rows, uid=ME, self_pid=9000, sessions=sess)
    planned = reaper.stale_processes(env)
    assert [a["pid"] for a in planned] == [10]

    def woke(env2, claude_dir):  # the session writes again while docker stops
        env2.now, env2.procs, env2.uid, env2.self_pid = NOW + 600, rows, ME, 9000
        env2.sessions = {100: reaper.Session(30, False)}

    sent, log = [], []
    reaper.apply(planned, Path("."), log=log.append, stop=lambda *a, **k: sent.append(a),
                 recheck=lambda a: reaper.recheck_process(a, Path("x"), collect=woke))
    assert sent == [] and log == ["reaper: skip pid 10 (state changed to active)"]

    def gone(env2, claude_dir):
        env2.procs, env2.uid, env2.self_pid = [r for r in rows if r.pid != 10], ME, 9000
        env2.sessions = sess

    log.clear()
    reaper.apply(planned, Path("."), log=log.append, stop=lambda *a, **k: sent.append(a),
                 recheck=lambda a: reaper.recheck_process(a, Path("x"), collect=gone))
    assert sent == [] and log == ["reaper: skip pid 10 (state changed to gone)"]


def test_apply_stops_a_process_still_listed_and_names_the_children_it_leaves():
    rows = [P(33, 1, 7, PYTEST), P(34, 33, 7, "python -c xdist worker"), P(35, 33, 7, "python -c xdist worker")]

    def same(env2, claude_dir):
        env2.now, env2.procs, env2.uid, env2.self_pid = NOW, rows, ME, 9000
        env2.environ_of = {33: of(DEAD)}.get

    env = reaper.Env(now=NOW, procs=rows, uid=ME, self_pid=9000, environ_of={33: of(DEAD)}.get)
    planned = reaper.stale_processes(env)
    assert planned[0]["kids"] == [34, 35]
    sent, log = [], []
    reaper.apply(planned, Path("."), log=log.append, stop=lambda pid, start, log: sent.append((pid, start)),
                 recheck=lambda a: reaper.recheck_process(a, Path("x"), collect=same))
    assert sent == [(33, 330)]
    assert log[0].startswith("reaper: stop pid 33 (test run") and log[0].endswith("[children left: 34 35]")


# The real shape of a Bash-tool job, from /proc/<pid>/cmdline of a Bash-tool shell.
_BASH_TOOL = (
    "/bin/bash -c source /home/dev/.claude/shell-snapshots/snapshot-bash-1794020937185-48mf2w.sh 2>/dev/null || true"
    " && shopt -u extglob 2>/dev/null || true && { \\builtin unalias -- 'which'; \\builtin unset -f -- 'which'; }"
    " >/dev/null 2>&1 || true && { \\builtin unalias -- 'unsetenv'; \\builtin unset -f -- 'unsetenv'; }"
    " >/dev/null 2>&1 || true && eval "
)


def bash_tool(command: str) -> str:
    """`command` as the Bash tool runs it: single-quoted inside `eval`."""
    return _BASH_TOOL + "'" + command.replace("'", "'\"'\"'") + "' < /dev/null && pwd -P >| /tmp/claude-5975-cwd"


_COMMIT = "git" + " commit"  # split, so a commit hook does not read this file as a command

DATA_LOOPS = [
    "cat > /tmp/w.sh <<'EOF'\nuntil curl -s x; do sleep 5; done\nEOF\nuv run long_server",
    'echo "a; while x; do sleep 1; done"',
    _COMMIT + " -m 'fix x\n\nwhile the gate runs, do not sleep, we are done'",
    "gh pr create --body 'Steps:\nwhile CI runs, do nothing. sleep. done.'",
    "cat <<EOF\nwhile true; do sleep 1; done\nEOF",
    "cat <<-EOF\n\twhile true; do sleep 1; done\n\tEOF\necho ok",
    "uv run pytest tests -q  # while x; do sleep 1; done",
]


def test_a_shell_that_holds_a_loop_as_data_is_no_wait_loop():
    for cmd in DATA_LOOPS:
        assert not reaper.is_wait_loop(bash_tool(cmd)), cmd
        assert not reaper.is_wait_loop("bash -c " + cmd), cmd
        # `ps` shows a new line as a space.
        assert not reaper.is_wait_loop(bash_tool(cmd.replace("\n", " "))), cmd


def test_a_loop_that_the_shell_runs_is_still_a_wait_loop():
    for cmd in [
        "until gh pr checks 1; do sleep 30; done",
        "while true; do sleep 60; done",
        "cd /x; (until [ -f done.txt ]; do sleep 60; done) &",
        "set -e\nwhile pgrep x\ndo\n  sleep 5\ndone",
        "echo 'start'; until curl -s \"http://x/a b\"; do echo .; sleep 2; done",
        "cat > f <<'EOF'\ndata\nEOF\nuntil [ -f f ]; do sleep 1; done",
        "x && until y; do sleep 1; done",
    ]:
        assert reaper.is_wait_loop("bash -c " + cmd), cmd
        assert reaper.is_wait_loop(bash_tool(cmd)), cmd
    assert reaper.is_wait_loop('bash -c eval "until x; do sleep 2; done"')
    assert reaper.is_wait_loop("sh -lc until x; do sleep 2; done")
    # Not after `-c`: a script file, or a shell with no script text.
    assert not reaper.is_wait_loop("bash /tmp/until.sh while do sleep done")


def test_wait_loop_match_stays_linear_on_hostile_text():
    t0 = time.perf_counter()
    assert not reaper.is_wait_loop("bash -c " + "; while x do sleep 1 " * 1200)
    assert not reaper.is_wait_loop("bash -c " + " " * 20_000 + "x")
    assert not reaper.is_wait_loop(bash_tool("cat <<'EOF'\n" + "while the gate runs do sleep then done\n" * 340 + "EOF"))
    assert not reaper.is_wait_loop("bash -c echo '" + "until x; do sleep 1; done " * 800)
    assert not reaper.is_wait_loop('bash -c echo "' + "a\\\" " * 5000)
    assert not reaper.is_wait_loop("bash -c cat <<EOF\n" + "while x; do sleep 1; done\n" * 800)
    assert not reaper.is_wait_loop("bash -c " + "cat <<EOF " * 2000)
    assert time.perf_counter() - t0 < 1.0


def test_young_child_of_a_false_loop_keeps_its_session_active():
    procs, sess = session(idle_h=9)
    wrapper = bash_tool(DATA_LOOPS[0])
    procs += [P(102, 100, 8, wrapper), P(103, 102, 2 / 60, "uv run long_server")]
    assert not reaper.is_wait_loop(wrapper)
    assert stale([*procs, P(33, 100, 4, PYTEST)], sess) == []
    # A true loop: its young `sleep` child does not count, a young other child does.
    procs, sess = session(idle_h=9)
    procs += [P(10, 100, 7, LOOP), P(11, 10, 1 / 3600, "sleep 5")]
    assert stale([*procs, P(33, 100, 4, PYTEST)], sess) == [10, 33]
    procs.append(P(12, 10, 1 / 3600, "curl -s x"))
    assert stale([*procs, P(33, 100, 4, PYTEST)], sess) == []


def test_orphan_with_no_claude_pid_is_kept_while_any_session_is_busy():
    sess_procs = [P(200, 1, 10, "claude --resume"), P(201, 200, 3, "bash -c uv run long_thing")]
    loop = P(500, 1, 5, "bash -c until curl -s x; do sleep 5; done")
    for busy, want in ((True, []), (False, [500])):
        sessions = {200: reaper.Session(3 * H, busy, "/home/dev/Metorite")}
        assert stale([loop, *sess_procs], sessions) == want, busy


def test_command_label_masks_tokens_headers_and_user_passwords():
    cases = {
        "curl -H 'Authorization: token ghp_ABCDEFGHIJKLMNOP1234' x": ("ghp_ABCD", "Authorization: token ***'"),
        "curl -H 'Authorization: Bearer abc.def' x": ("abc.def", "Authorization: Bearer ***'"),
        "curl -H 'Authorization: Basic dXNlcjpwYXNz' x": ("dXNlcjpwYXNz", "Authorization: Basic ***'"),
        "gh api x --raw ghp_" + "A" * 36: ("A" * 20, "ghp_***"),
        "echo gho_" + "b" * 30 + " ghs_" + "c" * 30 + " ghu_" + "d" * 30: ("bbbb", "ghu_***"),
        "echo github_pat_11ABCDEFG0123456789_abcdefghij": ("11ABCDEFG", "github_pat_***"),
        "curl -d token=x https://slack.com xoxb-1234-5678-abcdefgh": ("abcdefgh", "xoxb-***"),
        "curl -u admin:hunter2pass https://x": ("hunter2pass", "-u admin:***"),
        "curl --user 'admin:hunter2pass' https://x": ("hunter2pass", "--user 'admin:***'"),
        "curl --user=admin:hunter2pass https://x": ("hunter2pass", "--user=admin:***"),
        "curl -H 'X-Api-Key: abcdef123456' x": ("abcdef123456", "X-Api-Key: ***'"),
        "curl -H 'X-Auth-Token: qwerty98765' x": ("qwerty98765", "X-Auth-Token: ***'"),
        "curl -H 'Private-Token: zzz111222' x": ("zzz111222", "Private-Token: ***"),
        "OPENAI=1 x sk-abcdef1234567": ("abcdef1234567", "sk-***"),
    }
    for cmd, (secret, shown) in cases.items():
        for raw in (cmd, bash_tool(cmd)):
            label = reaper.command_label(raw)
            assert secret not in label and shown in label, (raw, label)
    for plain in ["uv run pytest tests/unit/test_x.py -q", "git push -u origin main",
                  "curl -H 'Accept: application/json' https://x", "python -u scripts/x.py --key-file k.pem"]:
        assert reaper.command_label(plain) == plain
    t0 = time.perf_counter()
    reaper.command_label("curl -H 'X-" + "a-" * 5000 + "' -u " + "b" * 5000)
    assert time.perf_counter() - t0 < 1.0


def test_a_worktree_is_not_inside_a_worktree_whose_name_it_starts_with():
    assert not reaper._contains("/home/dev/wt-a", "/home/dev/wt-ab")
    assert reaper._contains("/home/dev/wt-a", "/home/dev/wt-a/apps")
    procs, _ = session()
    env = reaper.Env(now=NOW, procs=procs, uid=ME, sessions={100: reaper.Session(H, True, "/home/dev/wt-a")},
                     cwd_of={101: "/home/dev/wt-a/x"}.get)
    kids = {}
    for p in procs:
        kids.setdefault(p.ppid, []).append(p)
    assert not reaper._works_in(env, procs[0], kids, "/home/dev/wt-ab/apps")
    assert reaper._works_in(env, procs[0], kids, "/home/dev/wt-a/apps")
