#!/usr/bin/env python3
"""CIV-19: the lane keeper. Wake each lane whose turn ended, from the box.

metorite-lane-keeper.timer runs this every 10 minutes as user dev. It needs
no PC and no person. One pass:

1. Read ``~/lanes/keeper-lanes.txt``, one lane name per line. It is the
   whole list of lanes that may wake. With no file, nothing wakes.
2. Read ``claude agents --json``. Keep the newest session of each listed lane.
3. A lane is ended when its state is done, blocked or idle. A lane that
   sleeps inside a ``sleep 600`` has state working, so it is not woken, unless
   its status is idle and its transcript is older than 30 minutes (reason
   ``stale-working``, task 2466).
4. A lane with a busy session is never woken. That is a busy session that
   has the lane's name, or a busy session of any name whose transcript header
   names the lane. A fork copies the header of its parent, so a fork that an old
   resume gave an automatic name still starts with the lane's name.
5. Wake it when its transcript is older than 10 minutes, and no wake went out
   in 30 minutes: ``claude stop <old id>`` (only while the old session still has
   a process), then ``claude --bg --resume <session id> -n <lane>
   --remote-control=<lane> <note>``. A resume of a session that still runs starts
   a copy, so a failed stop means no resume in that pass. ``-n`` gives the new
   session the lane's name, so the next pass finds it and not the old one
   (task 2206, 2026-11-08).
6. After an account change, wake every ended lane once, at the next pass,
   and ignore the 30-minute cap that time.
7. Run the account switch when ``KEEPER_SWITCH_CMD`` names one. The skip list
   goes to it as ``KEEPER_SKIP_ACCOUNTS``, and the keeper does not run it
   while the box sits on a skip-list account.

8. A listed lane with no session of its name is started fresh from
   ``~/lanes/start/<lane>.txt`` (reason ``fresh start``, task 2466). With no such
   file it logs and starts nothing. The 30-minute cap applies.

Every wake and every skip with a reason goes to ``~/lanes/keeper.log``.
The keeper stops only the ended session of a lane that it is about to resume.
It starts a new session only for a listed lane that has none and has a start note.
Fence: tests/unit/test_lane_keeper.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("HOME", "~")).expanduser()
LANES_DIR = Path(os.environ.get("KEEPER_LANES_DIR", HOME / "lanes"))
IDLE_S = 600
CAP_S = 1800
ENDED = {"done", "blocked", "idle"}
# CIV-19b (task 2466). A turn can end with status idle and state still "working"
# (lane-merge, 2026-11-08, cost about 5 hours). Such a lane wakes when its
# transcript is older than this.
STALE_S = 1800
# Lanes that wait on the owner. Owner directive 2026-11-07: never in a loop.
DEFAULT_SKIP_LANES = "lane-chat,lane-bill"
NOTE = (
    "Your turn ended. Read ~/lanes/merge-queue.log, fix a BACK line on your PRs first, "
    "act on an owner answer that you asked for, then take your next free task. "
    "When you wait on CI or a merge, run one `sleep 600` and look again. "
    "When you hold an open owner question and have no other free work, check it "
    "with get_agent_run, and stop only if it is still open. Do not end the turn "
    "after a hand-over."
)


def _csv(name: str, default: str = "") -> set[str]:
    return {x.strip() for x in os.environ.get(name, default).split(",") if x.strip()}


def newest_per_lane(agents: list[dict]) -> dict[str, dict]:
    """The newest session of each lane. An old, stopped session of a name loses."""
    out: dict[str, dict] = {}
    for a in agents:
        name = a.get("name") or ""
        if not name.startswith("lane-"):
            continue
        if name not in out or a.get("startedAt", 0) > out[name].get("startedAt", 0):
            out[name] = a
    return out


def transcript_age(session_id: str, now: float, projects: Path) -> float | None:
    """Seconds since the transcript of a session changed, or None when none exists."""
    for p in projects.glob(f"*/{session_id}.jsonl"):
        return now - p.stat().st_mtime
    return None


def lane_names() -> set[str]:
    """The lanes that may wake: one name per line of keeper-lanes.txt. None: empty."""
    try:
        text = (LANES_DIR / "keeper-lanes.txt").read_text(encoding="utf-8")
    except OSError:
        return set()
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}


def _header_names(path: Path, lines: int = 20) -> set[str]:
    """The names in the first records of a transcript. A fork copies the header of
    its parent, so a fork with an automatic name still starts with the lane's name."""
    names: set[str] = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _ in range(lines):
                line = f.readline()
                if not line:
                    break
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("type") in ("custom-title", "agent-name"):
                    n = rec.get("customTitle") or rec.get("agentName")
                    if isinstance(n, str):
                        names.add(n)
    except OSError:
        pass
    return names


def busy_lanes(agents: list[dict], lanes: set[str], projects: Path) -> set[str]:
    """Lanes that have a busy session: one with the lane's name, or a busy session
    of any name whose transcript header names the lane (a fork with an automatic name)."""
    out: set[str] = set()
    for a in agents:
        if a.get("status") != "busy":
            continue
        if a.get("name") in lanes:
            out.add(a["name"])
        sid = a.get("sessionId")
        if not sid:
            continue
        for f in projects.glob(f"*/{sid}.jsonl"):
            out |= _header_names(f) & lanes
    return out


def plan(
    agents: list[dict],
    state: dict[str, float],
    now: float,
    *,
    ages: dict[str, float | None],
    skip_lanes: set[str],
    only_lanes: set[str],
    force_all: bool,
    busy: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[tuple[str, str, str]]:
    """The wakes of one pass: (lane, session id, reason). Pure, so a test can call it."""
    wakes = []
    for name, a in sorted(newest_per_lane(agents).items()):
        # An empty `only_lanes` wakes nothing: the list file is the whole list.
        if name in skip_lanes or name not in only_lanes or name in busy:
            continue
        age = ages.get(a["sessionId"])
        stale = a.get("status") == "idle" and age is not None and age >= STALE_S
        if a.get("state") not in ENDED and not stale:
            continue
        if age is None or age < IDLE_S:
            continue
        if not force_all and now - state.get(name, 0) < CAP_S:
            continue
        reason = "account change" if force_all else (a["state"] if a.get("state") in ENDED else "stale-working")
        wakes.append((name, a["sessionId"], reason))
    return wakes


def plan_fresh(
    agents: list[dict],
    state: dict[str, float],
    now: float,
    *,
    skip_lanes: set[str],
    only_lanes: set[str],
    busy: set[str],
    notes: dict[str, str],
) -> list[tuple[str, str]]:
    """Listed lanes with no session at all: (lane, start note). Pure."""
    have = set(newest_per_lane(agents))
    out = []
    for name in sorted(only_lanes):
        if name in skip_lanes or name in have or name in busy:
            continue
        if now - state.get(name, 0) < CAP_S:
            continue
        if name in notes:
            out.append((name, notes[name]))
    return out


def start_notes(lanes: set[str]) -> dict[str, str]:
    """The start note of each lane: ``~/lanes/start/<lane>.txt``, when it exists."""
    out = {}
    for n in lanes:
        try:
            text = (LANES_DIR / "start" / f"{n}.txt").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            out[n] = text
    return out


def _start(name: str, note: str) -> tuple[int, str]:
    """Start a lane fresh. Never raises."""
    cwd = os.environ.get("KEEPER_START_CWD") or str(HOME / "Metorite")
    where = cwd if Path(cwd).is_dir() else None
    try:
        r = subprocess.run(
            ["claude", "--bg", "-n", name, f"--remote-control={name}", note],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=where,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, f" {type(exc).__name__}"
    return r.returncode, (f" {r.stderr.strip()[:120]!r}" if r.returncode and r.stderr else "")


def account_email() -> str:
    for base in (os.environ.get("CLAUDE_CONFIG_DIR"), str(HOME)):
        if not base:
            continue
        p = Path(base) / ".claude.json"
        try:
            return (
                json.loads(p.read_text(encoding="utf-8"))
                .get("oauthAccount", {})
                .get("emailAddress", "")
            )
        except (OSError, ValueError):
            continue
    return ""


def log(line: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    LANES_DIR.mkdir(parents=True, exist_ok=True)
    with open(LANES_DIR / "keeper.log", "a", encoding="utf-8") as f:
        f.write(f"{stamp}Z {line}\n")


def _wake(name: str, sid: str, agent: dict) -> tuple[int, str]:
    """Stop the ended session when it still has a process, then resume it under the
    lane's name. Returns (exit code, a short reason). Never raises."""
    # Claude refuses to resume from an untrusted directory such as the home
    # directory ("Workspace not trusted", exit 1). Resume from the cwd of the lane.
    cwd = agent.get("cwd")
    where = cwd if cwd and Path(cwd).is_dir() else None
    try:
        if agent.get("pid"):
            # A resume of a session that still runs starts a copy, and then the lane
            # has two live sessions. So no resume until the stop worked.
            short = agent.get("id")
            if not short:
                return 1, " the session runs and has no short id to stop"
            r = subprocess.run(
                ["claude", "stop", short], capture_output=True, text=True, timeout=60, cwd=where
            )
            if r.returncode:
                return r.returncode, f" stop failed: {r.stderr.strip()[:100]!r}"
        r = subprocess.run(
            ["claude", "--bg", "--resume", sid, "-n", name, f"--remote-control={name}", NOTE],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=where,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, f" {type(exc).__name__}"
    return r.returncode, (f" {r.stderr.strip()[:120]!r}" if r.returncode and r.stderr else "")


def run_pass(
    agents_json: str | None = None, now: float | None = None
) -> list[tuple[str, str, str]]:
    now = time.time() if now is None else now
    state_path = LANES_DIR / "keeper-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    skip_accounts = _csv("KEEPER_SKIP_ACCOUNTS", "vjvarada@hathilabs.com")
    email = account_email()
    cmd = os.environ.get("KEEPER_SWITCH_CMD", "").strip()
    if cmd and email not in skip_accounts:
        r = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "KEEPER_SKIP_ACCOUNTS": ",".join(sorted(skip_accounts))},
        )
        log(f"switch cmd exit={r.returncode} {r.stdout.strip()[-200:]}")
        email = account_email()
    elif cmd:
        log(f"switch skipped: box account {email} is on the skip list")
    force_all = bool(email) and state.get("_account") not in (None, email)
    raw = (
        agents_json
        if agents_json is not None
        else subprocess.run(
            ["claude", "agents", "--json"], capture_output=True, text=True, timeout=60, check=True
        ).stdout
    )
    agents = json.loads(raw)
    projects = HOME / ".claude" / "projects"
    ages = {
        a["sessionId"]: transcript_age(a["sessionId"], now, projects)
        for a in agents
        if a.get("sessionId")
    }
    lanes = lane_names()
    if not lanes:
        log("no lane named in keeper-lanes.txt, so no wake")
    busy = busy_lanes(agents, lanes, projects)
    wakes = plan(
        agents,
        state,
        now,
        ages=ages,
        skip_lanes=_csv("KEEPER_SKIP_LANES", DEFAULT_SKIP_LANES),
        only_lanes=lanes,
        force_all=force_all,
        busy=busy,
    )
    failed_forced = False
    by_sid = {a["sessionId"]: a for a in agents if a.get("sessionId")}
    for name, sid, reason in wakes:
        rc, why = _wake(name, sid, by_sid.get(sid, {}))
        log(f"wake {name} session={sid[:8]} reason={reason} exit={rc}{why}")
        if rc == 0:
            state[name] = now
        elif reason == "account change":
            failed_forced = True
    skip_lanes = _csv("KEEPER_SKIP_LANES", DEFAULT_SKIP_LANES)
    notes = start_notes(lanes)
    for name in sorted(lanes - set(newest_per_lane(agents)) - skip_lanes - busy):
        if name not in notes:
            log(f"no session of {name} and no start note in lanes/start, so no start")
    for name, note in plan_fresh(
        agents, state, now, skip_lanes=skip_lanes, only_lanes=lanes, busy=busy, notes=notes
    ):
        rc, why = _start(name, note)
        log(f"start {name} reason=fresh start exit={rc}{why}")
        if rc == 0:
            state[name] = now
            wakes.append((name, "", "fresh start"))
    if not failed_forced:
        state["_account"] = email
    LANES_DIR.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return wakes


if __name__ == "__main__":
    try:
        run_pass()
    except Exception as exc:  # the timer must not crash-loop
        log(f"keeper error: {exc!r}")
        sys.exit(1)
