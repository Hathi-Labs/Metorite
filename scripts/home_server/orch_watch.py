#!/usr/bin/env python3
"""CIV-ORCH: the watcher of the box orchestrator (lane-orch). One pass a run.

metorite-orch-watch.timer runs this every 5 minutes as user dev. Before it,
the orchestrator ran on the owner's PC, with a watch loop in its own turn. The
PC shut down on 2026-11-26, and the watch stopped with it. Now the watch is a
timer on the box, and the lane keeper wakes lane-orch on a new event.

One pass:

1. Read the new lines of ``~/lanes/merge-queue.log`` and ``~/lanes/merge-bot.log``
   since the last pass (a byte offset per file in the state file).
   - ``BACK``, ``HOLD``, ``RED`` and ``STOP`` lines are events of kind ``back``.
   - ``REQUEUE`` lines are events of kind ``requeue``: a HIGH PR waits for lane-merge.
2. Read ``claude agents --json``. For each lane of ``~/lanes/keeper-lanes.txt``
   (lane-orch itself left out), take its newest session.
   - Ended (done, blocked or idle), transcript older than 30 minutes, and not
     in ``~/lanes/orch_resting.txt``: kind ``idle-lane``. For a lane-merge lane
     the limit is 15 minutes and the kind is ``merger-idle``.
   - Working, but the transcript is older than 60 minutes: kind ``stall``.
   A lane event goes out again only after 60 minutes, so one idle lane does
   not fill the log.
3. Append each event to ``~/lanes/orch-events.log``, one line each:
   ``<UTC time>\\t<kind>\\t<subject>\\t<detail>``. Only this file writes it.

It never wakes or stops a session. The keeper (lane_keeper.py, ``plan_orch``)
wakes lane-orch when this log grew. Fence: tests/unit/test_orch_watch.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("HOME", "~")).expanduser()
LANES_DIR = Path(os.environ.get("ORCH_LANES_DIR", HOME / "lanes"))
ORCH_LANE = "lane-orch"
ENDED = {"done", "blocked", "idle"}
IDLE_S = 1800
MERGER_IDLE_S = 900
STALL_S = 3600
REPEAT_S = 3600
BACK_WORDS = ("BACK", "HOLD", "RED", "STOP")
LOGS = ("merge-queue.log", "merge-bot.log")


def log_events(lines: list[str], source: str) -> list[tuple[str, str, str]]:
    """The events in new log lines: (kind, subject, detail). Pure."""
    out = []
    for line in lines:
        word = line.split("\t", 1)[0].split(" ", 1)[0]
        if word not in BACK_WORDS and word != "REQUEUE":
            continue
        rest = line[len(word):].strip()
        subject = rest.replace("\t", " ").split(" ", 1)[0] if rest else "-"
        kind = "requeue" if word == "REQUEUE" else "back"
        out.append((kind, f"pr-{subject}" if subject.isdigit() else subject,
                    f"{source}: {line.strip()[:300]}"))
    return out


def newest_per_lane(agents: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for a in agents:
        name = a.get("name") or ""
        if name.startswith("lane-") and (
            name not in out or a.get("startedAt", 0) > out[name].get("startedAt", 0)
        ):
            out[name] = a
    return out


def lane_events(
    agents: list[dict],
    ages: dict[str, float | None],
    lanes: set[str],
    resting: set[str],
) -> list[tuple[str, str, str]]:
    """Idle, idle-merger and stalled lanes: (kind, lane, detail). Pure."""
    out = []
    for name, a in sorted(newest_per_lane(agents).items()):
        if name not in lanes or name == ORCH_LANE or name in resting:
            continue
        age = ages.get(a.get("sessionId", ""))
        if age is None:
            continue
        merger = name.startswith("lane-merge")
        if a.get("state") in ENDED:
            limit = MERGER_IDLE_S if merger else IDLE_S
            if age >= limit:
                kind = "merger-idle" if merger else "idle-lane"
                out.append((kind, name, f"state {a.get('state')}, transcript {int(age // 60)} min old"))
        elif age >= STALL_S:
            out.append(("stall", name, f"state {a.get('state')}, no transcript write for {int(age // 60)} min"))
    return out


def due(events: list[tuple[str, str, str]], seen: dict[str, float], now: float) -> list[tuple[str, str, str]]:
    """The lane events that may go out now: new, or last sent REPEAT_S ago. Pure.
    It updates ``seen``. A lane that is no longer idle drops out of ``seen``."""
    keys = {f"{k}:{s}" for k, s, _ in events}
    for key in list(seen):
        if key not in keys:
            del seen[key]
    out = []
    for kind, subject, detail in events:
        key = f"{kind}:{subject}"
        if now - seen.get(key, 0) >= REPEAT_S:
            seen[key] = now
            out.append((kind, subject, detail))
    return out


def read_new(path: Path, pos: int) -> tuple[list[str], int]:
    """The lines after byte ``pos``. A shorter file (rotated) reads from the start."""
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    if size < pos:
        pos = 0
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read()
    # Keep a partial last line for the next pass.
    cut = data.rfind(b"\n") + 1
    return data[:cut].decode("utf-8", "replace").splitlines(), pos + cut


def _set(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}


def _ages(agents: list[dict], now: float) -> dict[str, float | None]:
    projects = HOME / ".claude" / "projects"
    out: dict[str, float | None] = {}
    for a in agents:
        sid = a.get("sessionId")
        if not sid:
            continue
        out[sid] = None
        for p in projects.glob(f"*/{sid}.jsonl"):
            out[sid] = now - p.stat().st_mtime
            break
    return out


def run_pass(agents_json: str | None = None, now: float | None = None) -> list[tuple[str, str, str]]:
    now = time.time() if now is None else now
    state_path = LANES_DIR / "orch-watch-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    pos = state.setdefault("pos", {})
    seen = state.setdefault("seen", {})
    events: list[tuple[str, str, str]] = []
    for name in LOGS:
        path = LANES_DIR / name
        if name not in pos:
            # The first pass starts at the end: old lines are not news.
            try:
                pos[name] = path.stat().st_size
            except OSError:
                pos[name] = 0
            continue
        lines, pos[name] = read_new(path, int(pos[name]))
        events += log_events(lines, name)
    raw = agents_json
    if raw is None:
        try:
            raw = subprocess.run(
                ["claude", "agents", "--json"], capture_output=True, text=True, timeout=60, check=True
            ).stdout
        except (subprocess.SubprocessError, OSError):
            raw = None
    if raw is not None:
        agents = json.loads(raw)
        lanes = _set(LANES_DIR / "keeper-lanes.txt")
        resting = _set(LANES_DIR / "orch_resting.txt")
        events += due(lane_events(agents, _ages(agents, now), lanes, resting), seen, now)
    if events:
        stamp = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(now))
        with open(LANES_DIR / "orch-events.log", "a", encoding="utf-8") as f:
            for kind, subject, detail in events:
                f.write(f"{stamp}\t{kind}\t{subject}\t{detail.replace(chr(9), ' ')}\n")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return events


if __name__ == "__main__":
    try:
        run_pass()
    except Exception as exc:  # the timer must not crash-loop
        print(f"orch_watch error: {exc!r}", file=sys.stderr)
        sys.exit(1)
