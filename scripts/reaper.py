"""The reaper: stop orphan dev stacks, remove finished worktrees, and stop left loops (CIV-14j, CIV-16).

Three jobs, one pass:

1. STACKS. A `metorite-dev-*` container group stops when its worktree is gone,
   or when no process uses the worktree and its last activity is older than
   --stack-hours (default 4).
2. WORKTREES. A box worktree goes when its pull request is merged, its HEAD
   equals the merged head, it has no change, and no process uses it. The
   removal is `scripts/worktree.sh remove <slug>`, never `git worktree remove`.
3. PROCESSES (CIV-16). A wait loop (`until`/`while ... do ... sleep ... done`
   in a shell) or a `pytest` run that an agent left behind. It must belong to
   the user of the reaper and be older than its limit. Its session must have
   ended or be idle. `stale_processes` holds the full rule.

    python scripts/reaper.py --dry-run     # list what it would do
    python scripts/reaper.py               # do jobs 1 and 2. List job 3 only
    python scripts/reaper.py --apply       # do jobs 1 and 2, and stop job 3
    python scripts/reaper.py --count       # count bash, sleep, node, claude, pytest

Job 3 stops a process only with --apply. It never stops a process of a session
that works. When it cannot tell, it keeps the process.

It needs Linux: `ps -eo pid=,ppid=,etimes=,uid=,args=` and `/proc`. On a host
with no `/proc` (Windows, Git Bash) it does nothing and exits 0.

It never touches a worktree with a change, an unmerged branch, or a live
process. Fence: tests/unit/test_reaper.py (fake docker, gh, clock, ps, kill).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

STACK_PREFIX = "metorite-dev-"
DEFAULT_STACK_HOURS = 4.0
WT_GLOB = "wt-*"

# CIV-16: what an agent leaves behind. A wait loop that runs this long waits
# for something that never comes. An orphan loop (its parent is init) goes
# sooner. A test run of the repo ends in minutes, so 3 h means it hangs.
LOOP_HOURS = 6.0
ORPHAN_LOOP_HOURS = 2.0
PYTEST_HOURS = 3.0
# A session is ACTIVE when a process under it is younger than this...
YOUNG_CHILD_SECONDS = 10 * 60
# ...or when it wrote its transcript inside this window (or inside the limit
# of the process, when that is longer).
TRANSCRIPT_ACTIVE_SECONDS = 30 * 60
# After SIGTERM, the reaper waits this long before SIGKILL.
TERM_WAIT_SECONDS = 10.0

SHELLS = {"bash", "sh", "dash", "zsh"}
# A chain of these alone, up to init, is a session that ended. Anything else
# in the chain (tmux, sshd, systemd, a node host) makes the state unknown.
WRAPPERS = SHELLS | {"timeout", "env", "nohup", "setsid", "nice", "uv", "xargs"}
# A `claude` process with one of these subcommands is not a session. The
# daemon, a pty host and remote control sit above sessions.
CLAUDE_INFRA = {"daemon", "bg-pty-host", "remote-control"}
# The `until`/`while` must stand where a command starts: after `-c`, `eval '`,
# `;`, `&&`, `|`, `(`, `{`, a new line or `do`. So `grep 'while ...'` is no loop.
# The match is linear: one start regex with no nested quantifier, then one
# plain search for each of `do`, `sleep` and `done`, in order. A chain of lazy
# `.*?` took 74 s on a 13 KB heredoc. The text is cut to LOOP_CAP first.
LOOP_START_RE = re.compile(
    r"(?:^|[;&|(){}\n]|(?:\beval|\s-c|\bdo|\bthen|\belse)(?:\s+['\"]|\s))\s*(?:until|while)\s"
)
LOOP_TOKENS = (re.compile(r"\bdo\b"), re.compile(r"\bsleep\b"), re.compile(r"\bdone\b"))
LOOP_CAP = 4096
# command_label masks secrets in this much text only. Its label is 100 characters.
LABEL_CAP = 1000
PYTEST_NAMES = {"pytest", "py.test"}
PYTHON_RE = re.compile(r"^python[\d.]*$")


@dataclass
class Worktree:
    path: Path
    branch: str
    head: str
    dirty: bool
    stack_name: str | None
    last_activity: float


@dataclass(frozen=True)
class Proc:
    """One row of `ps`. `start` is the start time from /proc (clock ticks)."""

    pid: int
    ppid: int
    age: int  # seconds
    uid: int
    cmd: str
    start: int | None = None


@dataclass(frozen=True)
class Session:
    """What the reaper knows about a live `claude` session root.

    `idle_s` is the time since its last write (transcript, subagent
    transcript, session file). None means the reaper could not tell.
    """

    idle_s: float | None
    busy: bool = False
    cwd: str | None = None  # the cwd of its session file


def _no_read(pid: int) -> None:
    return None


@dataclass(frozen=True)
class Limits:
    loop_s: float = LOOP_HOURS * 3600
    orphan_loop_s: float = ORPHAN_LOOP_HOURS * 3600
    pytest_s: float = PYTEST_HOURS * 3600
    young_child_s: float = YOUNG_CHILD_SECONDS
    transcript_active_s: float = TRANSCRIPT_ACTIVE_SECONDS


@dataclass
class Env:
    """Everything that touches the machine. The test swaps it for fakes."""

    now: float = field(default_factory=time.time)
    # (container name, value of its `metorite.dev_up` label = the repo path)
    containers: list[tuple[str, str]] = field(default_factory=list)
    existing: set[str] = field(default_factory=set)  # label paths that still exist on disk
    worktrees: list[Worktree] = field(default_factory=list)
    busy_paths: set[str] = field(default_factory=set)  # a process runs here
    merged_heads: dict[str, set[str]] = field(default_factory=dict)  # branch -> merged head SHAs
    procs: list[Proc] = field(default_factory=list)  # every row of `ps`, all users
    uid: int | None = None  # the user of the reaper. None: job 3 does nothing
    self_pid: int = 0  # the reaper. It, its parent and all its ancestors stay
    sessions: dict[int, Session] = field(default_factory=dict)  # claude root pid -> state
    # pid -> its environment (dict) or None when the reaper cannot read it.
    environ_of: Callable[[int], dict[str, str] | None] = _no_read
    # pid -> its cwd or None when the reaper cannot read it.
    cwd_of: Callable[[int], str | None] = _no_read


def _busy(env: Env, wt: Worktree) -> bool:
    p = str(wt.path)
    return any(b == p or b.startswith(p + "/") for b in env.busy_paths)


# --- job 3: processes -------------------------------------------------------


def _argv(cmd: str) -> list[str]:
    return cmd.strip().split()


def _base(tok: str) -> str:
    return re.split(r"[\\/]", tok)[-1].removesuffix(".exe")


def _argv0(cmd: str) -> str:
    return _base((_argv(cmd) or [""])[0])


def is_claude(cmd: str) -> bool:
    """True when `cmd` is the Claude Code CLI (the isClaude of stop-children.mjs)."""
    argv0 = (_argv(cmd) or [""])[0]
    if re.search(r"(^|[\\/])claude(\.exe)?$", argv0, re.I):
        return True
    if re.search(r"[\\/]claude[\\/]versions[\\/][^\\/]+$", argv0, re.I):
        return True
    return bool(re.search(r"@anthropic-ai[\\/]claude-code[\\/]", cmd, re.I))


def is_claude_session(cmd: str) -> bool:
    """A `claude` process that is a session, not the daemon or a pty host."""
    argv = _argv(cmd)
    return is_claude(cmd) and not (len(argv) > 1 and argv[1] in CLAUDE_INFRA)


def is_wait_loop(cmd: str) -> bool:
    """A shell whose command is an `until`/`while` loop with `sleep` in it."""
    if _argv0(cmd) not in SHELLS:
        return False
    text = cmd[:LOOP_CAP]
    m = LOOP_START_RE.search(text)
    if m is None:
        return False
    # `.+?` of the old form: at least one character between the keyword and `do`.
    pos = m.end() + 1
    for tok in LOOP_TOKENS:
        found = tok.search(text, pos)
        if found is None:
            return False
        pos = found.end()
    return True


def is_pytest(cmd: str) -> bool:
    """`pytest ...`, `python .../pytest ...` or `python -m pytest ...`."""
    argv = _argv(cmd)
    if not argv:
        return False
    if _base(argv[0]) in PYTEST_NAMES:
        return True
    if not PYTHON_RE.match(_base(argv[0])):
        return False
    if len(argv) > 1 and _base(argv[1]) in PYTEST_NAMES:
        return True
    head = argv[1:5]
    return any(head[i] == "-m" and head[i + 1] in PYTEST_NAMES for i in range(len(head) - 1))


SECRET_PATTERNS = [
    (re.compile(r"\b(\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD)\w*)=(\"[^\"]*\"|'[^']*'|\S+)", re.I), r"\1=***"),
    (re.compile(r"\bBearer\s+\S+", re.I), "Bearer ***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"), "sk-***"),
    (re.compile(r"(://[^\s:/@]+):[^\s@/]+@"), r"\1:***@"),
    # A secret as the next word of a known long flag: `--token abc`, `--api-key abc`.
    (re.compile(r"(?<!\S)(--(?:token|password|passwd|api-key|apikey|secret|client-secret|auth-token|access-token))"
                r"(\s+|=)(\"[^\"]*\"|'[^']*'|\S+)", re.I), r"\1\2***"),
]


def command_label(cmd: str) -> str:
    """A short label, at most 100 characters, with obvious secrets masked.

    The commandLabel of stop-children.mjs: for a job of the Bash tool, the
    label is the text inside its `eval '...'`.
    """
    text = str(cmd)
    m = re.search(r"\beval (['\"])", text)
    if m:
        q = m.group(1)
        body = text[m.end():]
        cuts = [i for i in (body.find(f"{q} < /dev/null"), body.find(f"{q} && pwd -P")) if i >= 0]
        if cuts:
            body = body[: min(cuts)]
        elif body.endswith(q):
            body = body[:-1]
        text = body.replace("'\\''", "'").replace("'\"'\"'", "'") if q == "'" else body
    text = re.sub(r"\s+", " ", text[:LABEL_CAP]).strip()
    for rx, to in SECRET_PATTERNS:
        text = rx.sub(to, text)
    return text[:100]


def _ancestors(by_pid: dict[int, Proc], pid: int) -> list[Proc] | None:
    """The chain above `pid`, nearest first, init left out. None: a gap."""
    out: list[Proc] = []
    seen = {pid}
    cur = by_pid.get(pid)
    while cur is not None and cur.ppid > 1:
        parent = by_pid.get(cur.ppid)
        if parent is None or parent.pid in seen:
            return None  # the parent is not in the list: we cannot tell
        seen.add(parent.pid)
        out.append(parent)
        cur = parent
    return out


def _has_young_process(root: int, kids: dict[int, list[Proc]], young_s: float) -> bool:
    """True when a process under `root` is younger than `young_s`.

    The walk does not go under a wait loop. Its `sleep` child is always young,
    so it says nothing about the session.
    """
    stack = [root]
    seen = {root}
    while stack:
        for c in kids.get(stack.pop(), []):
            if c.pid in seen:
                continue
            seen.add(c.pid)
            if c.age < young_s:
                return True
            if not is_wait_loop(c.cmd):
                stack.append(c.pid)
    return False


def _root_state(env: Env, r: Proc, kids: dict[int, list[Proc]], idle_need: float, limits: Limits) -> tuple[str, str]:
    """The state of one live claude session root: 'idle', 'active' or 'unknown'."""
    if _has_young_process(r.pid, kids, limits.young_child_s):
        return "active", f"session {r.pid} has a young process"
    info = env.sessions.get(r.pid)
    if info is None or info.idle_s is None:
        return "unknown", f"session {r.pid}: no transcript"
    if info.busy:
        return "active", f"session {r.pid} is busy"
    if info.idle_s < idle_need:
        return "active", f"session {r.pid} wrote {info.idle_s / 60:.0f} min ago"
    return "idle", f"session {r.pid} idle {info.idle_s / 3600:.1f} h"


def _descendants(pid: int, kids: dict[int, list[Proc]], cap: int = 200) -> list[Proc]:
    """The processes under `pid`, nearest first, at most `cap`."""
    out: list[Proc] = []
    queue = [pid]
    seen = {pid}
    while queue and len(out) < cap:
        for c in kids.get(queue.pop(0), []):
            if c.pid not in seen:
                seen.add(c.pid)
                out.append(c)
                queue.append(c.pid)
    return out[:cap]


def _claude_pid(env: Env, p: Proc, chain: list[Proc], kids: dict[int, list[Proc]]) -> int | None:
    """CLAUDE_PID from the environment of `p`, else of the nearest ancestor, else of a descendant."""
    for q in [p, *chain, *_descendants(p.pid, kids, cap=50)]:
        value = (env.environ_of(q.pid) or {}).get("CLAUDE_PID", "")
        if value.isdigit():
            return int(value)
    return None


_WORKTREE_RE = re.compile(r"^(.*?/wt-[^/]+)")


def _tree(path: str) -> str:
    """The worktree root of `path` (the `.../wt-<slug>` part), or `path` itself."""
    m = _WORKTREE_RE.match(path)
    return m.group(1) if m else path


def _contains(outer: str, inner: str) -> bool:
    outer = outer.rstrip("/") or "/"
    return inner == outer or inner.startswith(outer if outer == "/" else outer + "/")


def _works_in(env: Env, s: Proc, kids: dict[int, list[Proc]], cwd: str) -> bool:
    """True when session `s` has a cwd that holds `cwd`, or a process in the same worktree."""
    info = env.sessions.get(s.pid)
    own = [d for d in ((info.cwd if info else None), env.cwd_of(s.pid)) if d]
    if any(_contains(d, cwd) for d in own):
        return True
    return any((d := env.cwd_of(c.pid)) and _tree(d) == _tree(cwd) for c in _descendants(s.pid, kids))


def _orphan_state(env: Env, p: Proc, chain: list[Proc], limit_s: float, limits: Limits,
                  by_pid: dict[int, Proc], kids: dict[int, list[Proc]]) -> tuple[str, str]:
    """The state of a process with only shells up to init.

    Init adopts a background job when its shell exits. So the session that
    started it can still be live. CLAUDE_PID in its environment names it.
    """
    idle_need = max(limits.transcript_active_s, limit_s)
    sessions = [x for x in env.procs if x.uid == env.uid and is_claude_session(x.cmd)]
    cwd = env.cwd_of(p.pid)
    if cwd:
        for s in sessions:
            if _root_state(env, s, kids, idle_need, limits)[0] != "idle" and _works_in(env, s, kids, cwd):
                return "active", f"orphan in {cwd}, where session {s.pid} works"
    cpid = _claude_pid(env, p, chain, kids)
    if cpid is not None:
        root = by_pid.get(cpid)
        # A session cannot start a process older than itself: an older one means a reused pid.
        if root is None or root.uid != env.uid or not is_claude_session(root.cmd) or root.age + 5 < p.age:
            return "ended", f"orphan, its session {cpid} ended"
        state, why = _root_state(env, root, kids, idle_need, limits)
        return state, f"orphan of {why}"
    if p.age <= 2 * limit_s:
        return "unknown", "orphan, no CLAUDE_PID, under twice its limit"
    for s in sessions:
        info = env.sessions.get(s.pid)
        if info is None or info.idle_s is None or info.idle_s < limit_s or _has_young_process(s.pid, kids, limit_s):
            return "unknown", f"orphan, no CLAUDE_PID, session {s.pid} is not quiet"
    return "ended", "orphan, no CLAUDE_PID, over twice its limit, no session works"


def session_state(env: Env, p: Proc, limit_s: float, limits: Limits, by_pid=None, kids=None) -> tuple[str, str]:
    """('ended' | 'idle' | 'active' | 'unknown', why). Only 'ended' and 'idle' let a stop go on."""
    by_pid = by_pid if by_pid is not None else {x.pid: x for x in env.procs}
    if kids is None:
        kids = {}
        for x in env.procs:
            kids.setdefault(x.ppid, []).append(x)
    chain = _ancestors(by_pid, p.pid)
    if chain is None:
        return "unknown", "a parent is missing from ps"
    claudes = [a for a in chain if is_claude(a.cmd)]
    if not claudes:
        if all(_argv0(a.cmd) in WRAPPERS for a in chain):
            return _orphan_state(env, p, chain, limit_s, limits, by_pid, kids)
        return "unknown", "no claude session, but a parent that is not a shell"
    roots = [a for a in claudes if is_claude_session(a.cmd)]
    if not roots:
        return "unknown", "only claude infrastructure above it"
    idle_need = max(limits.transcript_active_s, limit_s)
    whys = []
    for r in roots:
        state, why = _root_state(env, r, kids, idle_need, limits)
        if state != "idle":
            return state, why
        whys.append(why)
    return "idle", whys[0]


def stale_processes(env: Env, limits: Limits | None = None) -> list[dict]:
    """Wait loops and hung test runs that an agent left behind. Pure.

    A process is listed only when ALL of these hold:
    - it belongs to the user of the reaper (env.uid),
    - it is not the reaper, its parent or any ancestor of the reaper,
    - it is a shell wait loop or a pytest run,
    - it is older than its limit (orphan loop 2 h, loop 6 h, pytest 3 h),
    - its start time is known, so the stop can guard against pid reuse,
    - its session ENDED or is IDLE. IDLE: each claude session above it has no
      young process, is not busy, and wrote nothing inside the limit.
      A process with only shells up to init is an orphan. Its session is the
      one that CLAUDE_PID in its environment names. That session must be gone,
      or idle. With no CLAUDE_PID, the orphan must be over twice its limit,
      and no session may be busy. A busy session whose cwd holds the cwd of
      the orphan keeps it. `_orphan_state` holds this rule.
    Any doubt keeps the process. A listed test run carries the pids of its
    children ("kids"). The stop leaves them, and the log names them.
    """
    limits = limits or Limits()
    if env.uid is None or not env.procs:
        return []
    by_pid = {p.pid: p for p in env.procs}
    kids: dict[int, list[Proc]] = {}
    for p in env.procs:
        kids.setdefault(p.ppid, []).append(p)
    protected = {env.self_pid}
    for a in _ancestors(by_pid, env.self_pid) or []:
        protected.add(a.pid)
    me = by_pid.get(env.self_pid)
    if me is not None:
        protected.add(me.ppid)
    out = []
    for p in env.procs:
        if p.uid != env.uid or p.pid <= 1 or p.pid in protected:
            continue
        if is_wait_loop(p.cmd):
            kind, limit_s = ("orphan wait loop", limits.orphan_loop_s) if p.ppid == 1 else ("wait loop", limits.loop_s)
        elif is_pytest(p.cmd):
            kind, limit_s = "test run", limits.pytest_s
        else:
            continue
        if p.age < limit_s or p.start is None:
            continue
        state, why = session_state(env, p, limit_s, limits, by_pid, kids)
        if state not in ("ended", "idle"):
            continue
        act = {
            "act": "stop-process", "pid": p.pid, "start": p.start, "age": p.age,
            "cmd": command_label(p.cmd), "why": f"{kind}, {p.age / 3600:.1f} h, {why}",
        }
        if kind == "test run":
            act["kids"] = sorted(c.pid for c in kids.get(p.pid, []))
        out.append(act)
    return out


def recheck_process(act: dict, claude_dir: Path, limits: Limits | None = None,
                    collect=None) -> tuple[dict | None, str]:
    """Read the machine again for one stop-process act, just before its signal.

    Returns (the fresh act, "listed") when the process is still listed, with
    the same pid and start time. Else (None, the new state).
    """
    env = Env()
    (collect or collect_procs)(env, claude_dir)
    for a in stale_processes(env, limits):
        if a["pid"] == act["pid"] and a["start"] == act["start"]:
            return a, "listed"
    p = next((x for x in env.procs if x.pid == act["pid"]), None)
    if p is None or p.start != act["start"]:
        return None, "gone"
    limits = limits or Limits()
    limit_s = limits.pytest_s if is_pytest(p.cmd) else limits.orphan_loop_s if p.ppid == 1 else limits.loop_s
    if p.age < limit_s:
        return None, "young"
    return None, session_state(env, p, limit_s, limits)[0]


def plan(env: Env, stack_hours: float = DEFAULT_STACK_HOURS, limits: Limits | None = None) -> list[dict]:
    """Return the acts: {"act": "stop-stack"|"remove-worktree"|"stop-process", ...}. Pure."""
    acts: list[dict] = []
    by_path = {str(w.path): w for w in env.worktrees}
    for repo in sorted({label for _, label in env.containers if label}):
        if repo not in env.existing:
            acts.append({"act": "stop-stack", "repo": repo, "why": "its worktree is gone"})
            continue
        wt = by_path.get(repo)
        # A stack of a path we do not track (the primary checkout, /tmp) stays.
        if wt is not None and not _busy(env, wt) and env.now - wt.last_activity > stack_hours * 3600:
            hours = (env.now - wt.last_activity) / 3600
            acts.append({"act": "stop-stack", "repo": repo, "why": f"idle {hours:.1f} h, no process"})
    for wt in env.worktrees:
        if wt.dirty or _busy(env, wt):
            continue
        if wt.head and wt.head in env.merged_heads.get(wt.branch, set()):
            acts.append({"act": "remove-worktree", "slug": wt.path.name.removeprefix("wt-"), "path": str(wt.path), "why": f"PR merged at {wt.head[:9]}"})
    return acts + stale_processes(env, limits)


def proc_start(pid: int) -> int | None:
    """The start time of `pid` in clock ticks, from /proc. None: gone, a zombie, or no /proc."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rest = raw[raw.rfind(")") + 2:].split()
    if len(rest) < 20 or rest[0] == "Z":
        return None
    return int(rest[19])


def stop_process(pid: int, start: int, *, kill=os.kill, start_of=proc_start, sleep=time.sleep,
                 wait_s: float = TERM_WAIT_SECONDS, log=print) -> str:
    """SIGTERM `pid`, wait up to `wait_s`, then SIGKILL it if it is still the same process.

    "The same process" means the same pid AND the same start time. Each
    signal goes to this one pid. It never signals a group or a tree.
    Returns "gone" (no signal), "term" (SIGTERM was enough) or "kill".
    """
    if start_of(pid) != start:
        log(f"reaper: pid {pid} kept: it ended, or the pid now names another process")
        return "gone"
    try:
        kill(pid, signal.SIGTERM)
    except OSError:
        return "gone"
    waited = 0.0
    while waited < wait_s:
        sleep(0.5)
        waited += 0.5
        if start_of(pid) != start:
            log(f"reaper: pid {pid} ended after SIGTERM")
            return "term"
    try:
        kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        return "term"
    log(f"reaper: pid {pid} did not end in {wait_s:.0f} s: SIGKILL")
    return "kill"


def _run(*cmd: str, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return ""  # a missing docker or gh keeps everything: no act
    return r.stdout if r.returncode == 0 else ""


def _state_name(path: Path) -> str | None:
    state = path / ".dev_up" / "state"
    if not state.is_file():
        return None
    for line in state.read_text(encoding="utf-8").splitlines():
        if line.startswith("NAME="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def _last_activity(path: Path) -> float:
    marks = [path / ".dev_up" / "state", path / ".git"]
    return max((m.stat().st_mtime for m in marks if m.exists()), default=0.0)


def _busy_paths() -> set[str]:
    out = set()
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            with contextlib.suppress(OSError):
                out.add(os.readlink(f"/proc/{pid}/cwd"))
    return out


def parse_ps(text: str) -> list[Proc]:
    """Rows of `ps -eo pid=,ppid=,etimes=,uid=,args=`. A row that does not parse is left out."""
    out = []
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5 and all(x.isdigit() for x in parts[:4]):
            out.append(Proc(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), parts[4]))
    return out


def _ps() -> list[Proc]:
    return parse_ps(_run("ps", "-eo", "pid=,ppid=,etimes=,uid=,args="))


def _mtime(path: Path) -> float | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime if st.st_size > 0 else None


def session_info(pid: int, start: int | None, claude_dir: Path, now: float) -> Session:
    """Read ~/.claude/sessions/<pid>.json and the transcript it names. Any doubt: idle_s None."""
    try:
        data = json.loads((claude_dir / "sessions" / f"{pid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Session(None)
    if not isinstance(data, dict) or start is None or str(data.get("procStart")) != str(start):
        return Session(None)  # the file names another process with this pid
    # Claude Code writes "busy", "shell" or "idle". Each value but "idle" counts as work.
    busy = data.get("status") not in (None, "idle")
    sid, cwd = data.get("sessionId"), data.get("cwd")
    if not sid or not cwd:
        return Session(None, busy, str(cwd) if cwd else None)
    proj = claude_dir / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    main = _mtime(proj / f"{sid}.jsonl")
    if main is None:
        return Session(None, busy, str(cwd))  # no transcript, or an empty one (a remote session)
    marks = [main, _mtime(claude_dir / "sessions" / f"{pid}.json") or 0.0]
    sub = proj / str(sid) / "subagents"
    if sub.is_dir():
        marks += [m for m in (_mtime(f) for f in sub.glob("*.jsonl")) if m]
    return Session(max(0.0, now - max(marks)), busy, str(cwd))


def read_environ(pid: int) -> dict[str, str] | None:
    """The environment of `pid` from /proc. None: gone, another user, or no /proc."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    out = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if sep:
            out[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return out


def read_cwd(pid: int) -> str | None:
    """The cwd of `pid` from /proc. None when the reaper cannot read it."""
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def collect_procs(env: Env, claude_dir: Path) -> None:
    """Fill the job-3 fields of `env`. On a host with no uid or no /proc it leaves them empty."""
    if not hasattr(os, "getuid") or not Path("/proc").is_dir():
        return
    rows = _ps()
    if not rows:
        return
    uid = os.getuid()
    full = []
    for p in rows:
        if p.uid == uid and (is_wait_loop(p.cmd) or is_pytest(p.cmd) or is_claude_session(p.cmd)):
            p = Proc(p.pid, p.ppid, p.age, p.uid, p.cmd, proc_start(p.pid))
        full.append(p)
    env.procs, env.uid, env.self_pid = full, uid, os.getpid()
    env.environ_of, env.cwd_of = read_environ, read_cwd
    for p in full:
        if p.uid == uid and is_claude_session(p.cmd):
            env.sessions[p.pid] = session_info(p.pid, p.start, claude_dir, env.now)


def collect(home: Path, repo: Path, claude_dir: Path | None = None) -> Env:
    env = Env(busy_paths=_busy_paths())
    collect_procs(env, claude_dir or home / ".claude")
    for line in _run("docker", "ps", "--format", '{{.Names}}|{{.Label "metorite.dev_up"}}').splitlines():
        name, _, label = line.partition("|")
        if name.startswith(STACK_PREFIX) and label.strip():
            env.containers.append((name, label.strip()))
            if Path(label.strip()).is_dir():
                env.existing.add(label.strip())
    known = _run("git", "-C", str(repo), "worktree", "list", "--porcelain")
    ours = {ln.split(" ", 1)[1].strip() for ln in known.splitlines() if ln.startswith("worktree ")}
    for p in sorted(home.glob(WT_GLOB)):
        if str(p) not in ours:
            continue  # not a worktree of this repo
        branch = _run("git", "-C", str(p), "rev-parse", "--abbrev-ref", "HEAD").strip()
        head = _run("git", "-C", str(p), "rev-parse", "HEAD").strip()
        dirty = bool(_run("git", "-C", str(p), "status", "--porcelain").strip())
        env.worktrees.append(Worktree(p, branch, head, dirty, _state_name(p), _last_activity(p)))
    for wt in env.worktrees:
        if wt.branch in ("HEAD", "main") or wt.branch in env.merged_heads:
            continue
        out = _run("gh", "pr", "list", "--head", wt.branch, "--state", "merged", "--json", "headRefOid", "--limit", "5", cwd=str(repo))
        env.merged_heads[wt.branch] = {x["headRefOid"] for x in json.loads(out or "[]")}
    return env


def counts(procs: list[Proc], uid: int) -> dict[str, int]:
    """Count the processes of `uid` by kind: bash, sleep, node, claude, pytest."""
    out = {"bash": 0, "sleep": 0, "node": 0, "claude": 0, "pytest": 0}
    for p in procs:
        if p.uid != uid:
            continue
        argv0 = _argv0(p.cmd)
        if is_claude(p.cmd):
            out["claude"] += 1
        elif is_pytest(p.cmd):
            out["pytest"] += 1
        elif argv0 in ("bash", "sleep", "node"):
            out[argv0] += 1
    return out


def _describe(a: dict) -> str:
    if a["act"] == "stop-process":
        kids = f" [children left: {' '.join(map(str, a['kids']))}]" if a.get("kids") else ""
        return f"pid {a['pid']} ({a['why']}): {a['cmd']}{kids}"
    return f"{a.get('repo') or a.get('path')} ({a['why']})"


def apply(acts: list[dict], repo: Path, log=print, stop=stop_process, recheck=None) -> None:
    """Do the acts in order.

    A docker stop or a worktree removal can take minutes, and a session can
    start work in that time. So each stop-process act reads the machine again
    (`recheck`) just before its signal. It stops the process only when the
    process is still listed.
    """
    if recheck is None:
        def recheck(a: dict) -> tuple[dict | None, str]:
            return recheck_process(a, Path.home() / ".claude")
    for a in acts:
        if a["act"] == "stop-stack":
            names = _run("docker", "ps", "-a", "--filter", f"label=metorite.dev_up={a['repo']}", "--format", "{{.Names}}").split()
            mine = [n for n in names if n.startswith(STACK_PREFIX)]
            log(f"reaper: stop {a['repo']} ({a['why']}): {' '.join(mine)}")
            if mine:
                subprocess.run(["docker", "stop", *mine], capture_output=True, timeout=120)
        elif a["act"] == "stop-process":
            fresh, state = recheck(a)
            if fresh is None:
                log(f"reaper: skip pid {a['pid']} (state changed to {state})")
                continue
            log(f"reaper: stop {_describe(fresh)}")
            stop(fresh["pid"], fresh["start"], log=log)
        else:
            log(f"reaper: remove worktree {a['path']} ({a['why']})")
            r = subprocess.run(["bash", str(repo / "scripts" / "worktree.sh"), "remove", a["slug"]], cwd=repo, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                log(f"reaper: worktree.sh refused {a['slug']}: {r.stdout.strip()[-200:]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="list every act, do none")
    ap.add_argument("--apply", action="store_true", help="also stop the left processes (job 3)")
    ap.add_argument("--count", action="store_true", help="print the process counts of this user, do nothing")
    ap.add_argument("--stack-hours", type=float, default=DEFAULT_STACK_HOURS)
    ap.add_argument("--home", default=str(Path.home()))
    ap.add_argument("--repo", default=str(Path.home() / "Metorite"))
    args = ap.parse_args(argv)
    if not Path("/proc").is_dir() or not hasattr(os, "getuid"):
        print("reaper: no /proc on this host: nothing to do")
        return 0
    if args.count:
        c = counts(_ps(), os.getuid())
        print(" ".join(f"{k}={v}" for k, v in c.items()))
        return 0
    repo = Path(args.repo)
    acts = plan(collect(Path(args.home), repo), args.stack_hours)
    if args.dry_run:
        for a in acts:
            print(f"would {a['act']}: {_describe(a)}")
        print(f"{len(acts)} act(s), dry run")
        return 0
    if not args.apply:
        for a in acts:
            if a["act"] == "stop-process":
                print(f"reaper: would stop {_describe(a)} (add --apply)")
        acts = [a for a in acts if a["act"] != "stop-process"]
    claude_dir = Path(args.home) / ".claude"
    apply(acts, repo, recheck=lambda a: recheck_process(a, claude_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
