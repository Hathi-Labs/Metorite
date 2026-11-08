#!/usr/bin/env python3
"""CIV-19: the lane keeper. Wake each lane whose turn ended, from the box.

metorite-lane-keeper.timer runs this every 10 minutes as user dev. It needs
no PC and no person. One pass:

1. Read ``claude agents --json``. Keep the newest session of each lane name.
2. A lane is ended when its state is done, blocked or idle. A lane that
   sleeps inside a ``sleep 600`` has state working, so it is never woken.
3. Wake it with ``claude --bg --resume <session id> <note>`` when its
   transcript is older than 10 minutes, and no wake went out in 30 minutes.
4. After an account change, wake every ended lane once, at the next pass,
   and ignore the 30-minute cap that time.
5. Run the account switch when ``KEEPER_SWITCH_CMD`` names one. The skip list
   goes to it as ``KEEPER_SKIP_ACCOUNTS``, and the keeper does not run it
   while the box sits on a skip-list account.

Every wake and every skip with a reason goes to ``~/lanes/keeper.log``.
The keeper never stops a process and never starts a new lane.
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


def plan(
    agents: list[dict],
    state: dict[str, float],
    now: float,
    *,
    ages: dict[str, float | None],
    skip_lanes: set[str],
    only_lanes: set[str],
    force_all: bool,
) -> list[tuple[str, str, str]]:
    """The wakes of one pass: (lane, session id, reason). Pure, so a test can call it."""
    wakes = []
    for name, a in sorted(newest_per_lane(agents).items()):
        if name in skip_lanes or (only_lanes and name not in only_lanes):
            continue
        if a.get("state") not in ENDED:
            continue
        age = ages.get(a["sessionId"])
        if age is None or age < IDLE_S:
            continue
        if not force_all and now - state.get(name, 0) < CAP_S:
            continue
        wakes.append((name, a["sessionId"], "account change" if force_all else a["state"]))
    return wakes


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
    wakes = plan(
        agents,
        state,
        now,
        ages=ages,
        skip_lanes=_csv("KEEPER_SKIP_LANES", DEFAULT_SKIP_LANES),
        only_lanes=_csv("KEEPER_LANES"),
        force_all=force_all,
    )
    failed_forced = False
    cwds = {a["sessionId"]: a.get("cwd") for a in agents if a.get("sessionId")}
    for name, sid, reason in wakes:
        # Claude refuses to resume from an untrusted directory such as the home
        # directory ("Workspace not trusted", exit 1). Resume from the cwd of the lane.
        cwd = cwds.get(sid)
        r = subprocess.run(
            ["claude", "--bg", "--resume", sid, NOTE],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd if cwd and Path(cwd).is_dir() else None,
        )
        why = f" {r.stderr.strip()[:120]!r}" if r.returncode and r.stderr else ""
        log(f"wake {name} session={sid[:8]} reason={reason} exit={r.returncode}{why}")
        if r.returncode == 0:
            state[name] = now
        elif reason == "account change":
            failed_forced = True
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
