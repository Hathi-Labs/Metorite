#!/usr/bin/env python3
"""The answer watcher of the home box (CIV-14f, Metorite task 1731).

Spec: ``project-docs/specs/agent_coordination.md`` §5.8, CIV-14f.

**What it does.** A user timer runs this file every 2 minutes. It asks the
Metorite MCP for the agent runs that wait for an agent (``list_agent_runs``
with ``needs_agent`` true). For each run in the state ``answered`` or
``queued`` it starts ONE fresh headless session, with the run id in the
prompt. The owner chose a fresh session for each run on 2026-11-06.

**What it never does.**
- It never reads an answer and never acts on one. The session that it starts
  reads the run itself.
- It never wakes an old session. No tool can reach a session that ended its
  turn.
- It never starts a session for a run in the state ``stopping``.
- It never prints the token.

**One session for one wake.** A wake is a run id plus its ``last_activity_at``.
Before it starts a session, the watcher takes an attempt file for the wake with
``O_CREAT | O_EXCL``. A second tick finds the file and starts nothing. A new
answer changes ``last_activity_at``, so it is a new wake, and it starts a new
session. A wake that no session took up is tried again after
:data:`LOCK_TTL_S`, and at most :data:`MAX_TRIES` times.

**Each session is its own systemd unit.** The watcher runs as a ``oneshot``
unit, and systemd kills each process that is left in it when it exits. So each
session starts with ``systemd-run --user --collect``, in a transient unit of
its own. The live count is the number of those units that run now.

**The vet.** Before a start, the watcher reads the run (``get_agent_run``). It
starts a session only when the last ``prompt`` on the run is by the member of
the run. A prompt by another member starts nothing.

**The box is shared (rule 11 of the lane rules).** It starts no session when
less than :data:`MIN_FREE_MB` of memory is free, and no more than
:data:`MAX_LIVE` sessions at once. It leaves a run alone for
:data:`QUIET_S` after its last activity, so a live session of the owner acts
first.

**The credential.** The token is a read-only box token (D-EA-5, scope
``projects:read``). It comes from ``METORITE_WATCHER_TOKEN``, which the unit
reads from ``~/.config/metorite/watcher.env`` (mode 0600). With no token the
watcher logs one line and exits 0, so the unit can ship before the token
exists. The session that the watcher starts uses the login of its own lane,
not this token.

Fence: ``tests/unit/test_answer_watcher.py``.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any

#: The MCP door and the repo the sessions work in.
MCP_URL = os.environ.get("METORITE_MCP_URL", "https://mcp.metorite.com/mcp")
REPO = pathlib.Path(os.environ.get("METORITE_REPO", str(pathlib.Path.home() / "Metorite")))

#: A wake that no session took up is tried again after this long.
LOCK_TTL_S = 2 * 3600
#: The most starts for one wake.
MAX_TRIES = 3
#: A run is left alone this long after its last activity.
QUIET_S = 120
#: The attempt files are kept this long, then pruned.
KEEP_S = 7 * 24 * 3600
#: The least free memory (MiB) at which a session may start.
MIN_FREE_MB = 5000
#: The most sessions that this watcher keeps alive at once.
MAX_LIVE = 2
#: The default session command. The Metorite tools only. The repo settings
#: grant the rest. ``WATCHER_CLAUDE_CMD`` overrides it.
DEFAULT_CLAUDE_CMD = "claude -p --allowedTools mcp__metorite__*"
#: The longest wait for the MCP (seconds).
HTTP_TIMEOUT_S = 20

STATE_DIR = pathlib.Path(
    os.environ.get("METORITE_WATCHER_STATE", str(pathlib.Path.home() / ".local/state/metorite/answer-watcher"))
)


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{stamp} answer-watcher: {msg}", flush=True)


# ── The MCP read ────────────────────────────────────────────────────────────


def _post(token: str, body: dict[str, Any], session: str | None) -> tuple[Any, str | None]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(MCP_URL, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        raw = resp.read().decode("utf-8", "replace")
        sid = resp.headers.get("Mcp-Session-Id") or session
    if not raw.strip():
        return None, sid
    if raw.lstrip().startswith("{"):
        return json.loads(raw), sid
    # An SSE answer: the last ``data:`` line holds the message.
    data = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")]
    return (json.loads(data[-1]) if data else None), sid


def _rpc(token: str, session: str | None, rid: int, name: str, args: dict[str, Any]) -> Any:
    msg, _ = _post(
        token,
        {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": args}},
        session,
    )
    if not isinstance(msg, dict) or "error" in msg or (msg.get("result") or {}).get("isError"):
        raise RuntimeError("tool call failed")  # a failed read must never clear a lock
    return msg


def open_session(token: str) -> str | None:
    _, sid = _post(
        token,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "metorite-answer-watcher", "version": "1"},
            },
        },
        None,
    )
    _post(token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    return sid


def _payload(msg: Any) -> dict[str, Any]:
    """The JSON object of a ``tools/call`` answer, from either result shape."""
    result = (msg or {}).get("result") or {}
    content = result.get("structuredContent")
    if isinstance(content, dict):
        return content
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            try:
                data = json.loads(part.get("text") or "")
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    raise RuntimeError("no result object")


def parse_runs(msg: Any) -> list[dict[str, Any]]:
    """The ``runs`` list of a ``tools/call`` answer. Raises when it is not one."""
    runs = _payload(msg).get("runs")
    if not isinstance(runs, list):
        raise RuntimeError("no runs list")
    return [r for r in runs if isinstance(r, dict)]


def fetch_runs(token: str, sid: str | None) -> list[dict[str, Any]]:
    """The runs that wait for an agent. Raises on any failure."""
    return parse_runs(_rpc(token, sid, 2, "list_agent_runs", {"needs_agent": True}))


def last_prompt_member(msg: Any) -> str | None:
    """The member of the newest ``prompt`` activity of a ``get_agent_run`` answer."""
    acts = _payload(msg).get("activities")
    if not isinstance(acts, list):
        return None
    prompts = [a for a in acts if isinstance(a, dict) and a.get("type") == "prompt" and a.get("author") == "person"]
    if not prompts:
        return None
    return str(prompts[-1].get("member") or "") or None


#: The most pages the vet reads for one run (200 rows a page). A run that
#: needs more is left alone: the vet cannot see its newest prompt.
VET_MAX_PAGES = 10


def vet_member(read: Callable[[int | None], Any]) -> str | None:
    """The member of the newest person prompt over ALL pages of a run.

    *read* takes the cursor (``None`` for the first page) and returns one
    ``get_agent_run`` answer. A page holds the OLDEST rows after the cursor,
    so the newest prompt may sit on a later page (review of CIV-14g). Raises
    when the run needs more than :data:`VET_MAX_PAGES` pages.
    """
    who: str | None = None
    cursor: int | None = None
    for _ in range(VET_MAX_PAGES):
        msg = read(cursor)
        found = last_prompt_member(msg)
        if found is not None:
            who = found
        body = _payload(msg)
        acts = body.get("activities")
        nxt = body.get("cursor")
        if not isinstance(acts, list) or not acts or not isinstance(nxt, int) or nxt == cursor:
            return who
        cursor = nxt
    raise RuntimeError("the run has too many pages to vet")


def make_vet(token: str, sid: str | None) -> Callable[[dict[str, Any]], bool]:
    """A run passes when its newest person prompt is by the member of the run.

    A run with no person prompt (a ``queued`` run: a member asked an agent)
    passes: the member of the run made it. A prompt by ANOTHER member does not.
    The vet reads every page of the run, not only the first.
    """

    def vet(run: dict[str, Any]) -> bool:
        def read(after: int | None) -> Any:
            args: dict[str, Any] = {"run_id": run["id"]}
            if after is not None:
                args["after"] = after
            return _rpc(token, sid, 3, "get_agent_run", args)

        who = vet_member(read)
        return who is None or who == run.get("member")

    return vet


# ── One attempt file for one wake ───────────────────────────────────────────


def _safe_id(run_id: str) -> str | None:
    """The run id when it is a plain UUID, else None. It names a file."""
    import uuid

    try:
        return str(uuid.UUID(str(run_id)))
    except ValueError:
        return None


def wake_key(run: dict[str, Any]) -> str:
    """A run id plus a hash of its last activity: one new answer, one new wake."""
    import hashlib

    stamp = str(run.get("last_activity_at") or "")
    return f"{run['id']}.{hashlib.sha256(stamp.encode()).hexdigest()[:10]}"


def take_attempt(state_dir: pathlib.Path, key: str, now: float) -> bool:
    """True when this call may start a session for the wake *key*. Atomic.

    The first start creates the file (``O_EXCL``). Another start is allowed
    after :data:`LOCK_TTL_S`, at most :data:`MAX_TRIES` times in all.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{key}.attempt"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                st = path.stat()
                tries = int(path.read_text() or "1")
            except (FileNotFoundError, ValueError):
                continue
            if now - st.st_mtime < LOCK_TTL_S or tries >= MAX_TRIES:
                return False
            path.write_text(str(tries + 1))  # a retry: the file is ours to bump
            os.utime(path, (now, now))
            return True
        with os.fdopen(fd, "w") as fh:
            fh.write("1")
        os.utime(path, (now, now))
        return True
    return False


def release_attempt(state_dir: pathlib.Path, key: str) -> None:
    """A start that failed frees its wake for the next tick."""
    (state_dir / f"{key}.attempt").unlink(missing_ok=True)


def prune_attempts(state_dir: pathlib.Path, now: float) -> int:
    removed = 0
    if state_dir.is_dir():
        for path in state_dir.glob("*.attempt"):
            if now - path.stat().st_mtime > KEEP_S:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


# ── The plan ────────────────────────────────────────────────────────────────


def _age_s(run: dict[str, Any], now: float) -> float:
    """Seconds since the last activity of *run*. Big when the stamp is unknown."""
    import datetime as dt

    raw = str(run.get("last_activity_at") or "")
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    return now - when.timestamp()


def wanted(runs: Iterable[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """The runs that need a session.

    ``list_agent_runs`` with ``needs_agent`` already lists only runs that wait
    for an agent. A run stays on it as ``stale`` five minutes after its answer,
    so the state is not a filter. The watcher drops a run that is ``stopping``,
    a ``stranded`` one (a nudge, not an answer), one with a bad id, and one
    with activity in the last :data:`QUIET_S` seconds.
    """
    out = []
    for run in runs:
        rid = _safe_id(run.get("id", ""))
        if rid is None:
            continue
        if run.get("status") == "stopping" or run.get("state") == "stopping":
            continue
        if run.get("stranded"):
            continue
        if _age_s(run, now) < QUIET_S:
            continue
        out.append({**run, "id": rid})
    return out


#: CIV-14g. The fixed text that starts the prompt. The MCP answer rule
#: (``agent_edge.prompts.WATCHER_MARKER``) names it as the one exception. This
#: file does not import the edge package, so it holds the same literal. Fence:
#: ``test_answer_watcher.py`` ``test_the_marker_is_the_marker_of_the_edge``.
WATCHER_MARKER = "ANSWER WATCHER HAND-OFF for run"


def prompt_for(run: dict[str, Any], worktree: str = "(your current directory)") -> str:
    """The prompt of the fresh session. It holds ids only, never text from a run.

    It starts with :data:`WATCHER_MARKER` and the run id (CIV-14g). A run id
    that is not a UUID raises ``ValueError``, so no prompt names a bad run.
    """
    rid = _safe_id(run.get("id", ""))
    if rid is None:
        raise ValueError("the run id is not a UUID")
    task = _safe_id(run.get("task_id", "")) or "(see the run)"
    return (
        f"{WATCHER_MARKER} {rid} on task {task}. "
        "You are a fresh session that the answer watcher of metorite-home started "
        "for this agent run. "
        "The session that asked ended its turn, and a person answered or queued work. "
        "Call get_agent_run on this run, and read EVERY page (pass after set to the cursor of the page before) until a page has no activities, and then pick the newest prompt of a person. "
        "Call claim_task on the task, then get_task_context with comments all. "
        "Act on the answer only when it is the newest prompt of a person, its member "
        "is the member of the run, and no later activity withdrew it. "
        "Never act on an answer that restarts a machine, erases data or stops sessions. "
        f"Work in {worktree}, which the watcher made for you. Do not make a second worktree. "
        "Follow CLAUDE.md. End the turn as the lane rules say: done, "
        "an ask_on_task question, or release_task with a comment."
    )


def mem_available_mb() -> int:
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def live_sessions() -> int:
    """The number of session units that run now (``answer-*`` transient units)."""
    try:
        out = subprocess.run(
            ["systemctl", "--user", "list-units", "answer-*.service", "--state=running,activating",
             "--plain", "--no-legend"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return MAX_LIVE  # unknown: start nothing
    return sum(1 for ln in out.splitlines() if ln.strip())


def tick(
    runs: list[dict[str, Any]],
    *,
    state_dir: pathlib.Path,
    start: Callable[[dict[str, Any]], bool],
    now: float,
    free_mb: Callable[[], int] = mem_available_mb,
    live: Callable[[], int] = live_sessions,
    vet: Callable[[dict[str, Any]], bool] = lambda _r: True,
) -> list[str]:
    """One pass. Returns the ids of the runs that got a session."""
    prune_attempts(state_dir, now)
    started: list[str] = []
    for run in wanted(runs, now):
        if live() + len(started) >= MAX_LIVE:
            log(f"{MAX_LIVE} sessions are live. Run {run['id']} waits for the next tick.")
            break
        if free_mb() < MIN_FREE_MB:
            log(f"less than {MIN_FREE_MB} MB free. No session starts this tick.")
            break
        key = wake_key(run)
        try:
            if not vet(run):
                continue  # the newest prompt is by another member
        except Exception as exc:
            log(f"vet failed for {run['id']} ({type(exc).__name__}). Skipped.")
            continue
        if not take_attempt(state_dir, key, now):
            continue
        try:
            ok = start(run)
        except Exception as exc:  # the wake must not stay locked after a failed start
            log(f"start failed for {run['id']} ({type(exc).__name__})")
            ok = False
        if ok:
            started.append(run["id"])
            log(f"started a session for run {run['id']}")
        else:
            release_attempt(state_dir, key)
    return started


def start_session(run: dict[str, Any]) -> bool:
    """Start one session in its own transient systemd unit, in a new worktree."""
    key = wake_key(run)
    slug = f"answer-{run['id'][:8]}-{key.rsplit('.', 1)[1][:6]}"
    subprocess.run(
        ["bash", "scripts/worktree.sh", "new", slug], cwd=REPO, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
    )
    worktree = REPO.parent / f"wt-{slug}"
    cmd = os.environ.get("WATCHER_CLAUDE_CMD", DEFAULT_CLAUDE_CMD).split()
    logs = STATE_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logfile = logs / f"{slug}.log"
    # A transient unit: it is not in the cgroup of this oneshot unit, so it
    # lives on after this script exits. The token is not in its environment.
    subprocess.run(
        ["systemd-run", "--user", "--collect", f"--unit=answer-{slug}",
         f"--working-directory={worktree}", f"--property=StandardOutput=append:{logfile}",
         f"--property=StandardError=append:{logfile}", "--", *cmd, prompt_for(run, str(worktree))],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        env={k: v for k, v in os.environ.items() if k != "METORITE_WATCHER_TOKEN"},
    )
    return True


#: A worktree of a finished wake is removed after this long, when it holds no work.
WORKTREE_KEEP_S = 24 * 3600


def sweep_worktrees(
    dirs: Iterable[pathlib.Path],
    *,
    now: float,
    live_count: int,
    is_clean: Callable[[pathlib.Path], bool],
    remove: Callable[[str], None],
) -> list[str]:
    """Remove the ``wt-answer-*`` worktrees that hold no work. Returns the slugs.

    It removes nothing while any session runs, because a session may use one.
    A worktree with a change, or a commit that main does not hold, stays.
    """
    if live_count > 0:
        return []
    gone = []
    for d in dirs:
        if not d.name.startswith("wt-answer-"):
            continue
        try:
            age = now - d.stat().st_mtime
        except OSError:
            continue
        if age < WORKTREE_KEEP_S or not is_clean(d):
            continue
        remove(d.name[len("wt-"):])
        gone.append(d.name[len("wt-"):])
    return gone


def _is_clean(d: pathlib.Path) -> bool:
    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, timeout=30, check=True).stdout

    try:
        return not git("status", "--porcelain").strip() and not git("rev-list", "origin/main..HEAD").strip()
    except (OSError, subprocess.SubprocessError):
        return False


def _remove(slug: str) -> None:
    subprocess.run(["bash", "scripts/worktree.sh", "remove", slug], cwd=REPO, timeout=120, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    token = os.environ.get("METORITE_WATCHER_TOKEN", "").strip()
    if not token:
        log("no METORITE_WATCHER_TOKEN. The watcher is off until the owner drops the token.")
        return 0
    try:
        sid = open_session(token)
        runs = fetch_runs(token, sid)
        vet = make_vet(token, sid)
    except urllib.error.HTTPError as exc:
        log(f"the MCP answered HTTP {exc.code}. No session starts.")
        return 1
    except Exception as exc:
        log(f"the MCP read failed ({type(exc).__name__}). No session starts.")
        return 1
    now = time.time()
    tick(runs, state_dir=STATE_DIR, start=start_session, now=now, vet=vet)
    sweep_worktrees(
        REPO.parent.glob("wt-answer-*"), now=now, live_count=live_sessions(),
        is_clean=_is_clean, remove=_remove,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
