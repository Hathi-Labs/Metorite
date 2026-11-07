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


def stale(procs, sessions=None, self_pid=9000, uid=ME):
    rows = [*list(procs), P(self_pid, 8999, 0, "python3 scripts/reaper.py"), P(8999, 1, 0, "bash -c python3 scripts/reaper.py")]
    env = reaper.Env(now=NOW, procs=rows, uid=uid, self_pid=self_pid, sessions=sessions or {})
    return [a["pid"] for a in reaper.stale_processes(env)]


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
    # The quoted grep above is a shell whose text holds a loop. It counts as a
    # loop only because it has the full `while ... do sleep ... done` shape.
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
    assert stale([P(10, 1, 2.1, LOOP)]) == [10]
    assert stale([P(10, 1, 1.9, LOOP)]) == []
    # The Bash tool wrapper orphaned, the loop under it: a chain of shells only.
    wrapper = "/bin/bash -c source snap.sh && eval 'timeout 590 bash -c \"until x; do sleep 5; done\"'"
    assert stale([P(20, 1, 7, wrapper), P(21, 20, 7, "timeout 590 bash -c until x; do sleep 5; done"), P(22, 21, 7, LOOP)]) == [20, 22]


def test_loop_under_an_idle_session_is_listed_after_6h():
    procs, sess = session(idle_h=7)
    assert stale([*procs, P(10, 100, 7, LOOP)], sess) == [10]


def test_pytest_4h_under_no_session_is_listed():
    chain = [P(30, 1, 4, "/bin/bash -c eval 'timeout 9000 uv run pytest x'"), P(31, 30, 4, "timeout 9000 uv run pytest x"),
             P(32, 31, 4, "uv run pytest x"), P(33, 32, 4, PYTEST)]
    assert stale(chain) == [33]
    assert stale([P(33, 1, 2.9, PYTEST)]) == []


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


def _spawn_orphan(shell_text: str) -> int:
    """Start `shell_text` in the background of a shell that exits, so init adopts it."""
    r = subprocess.run(["bash", "-c", f"{shell_text} >/dev/null 2>&1 </dev/null & echo $!"],
                       capture_output=True, text=True, timeout=10)
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
    orphan = _spawn_orphan(f"bash -c '{loop}'")
    root = _spawn_orphan(f"exec -a claude bash {script}")
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
        reaper.apply(acts, Path("."), log=log.append)
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
    assert reaper.session_info(5, 77, tmp_path, NOW) == reaper.Session(8 * H, False)
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
