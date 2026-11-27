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

9. CIV-ORCH (task 4058). ``orch_watch.py`` writes events to
   ``~/lanes/orch-events.log``. lane-orch moves its cursor
   (``~/.local/state/metorite/orch_events.cursor``, a count of lines) past the
   lines it handled. When the log has more lines than the cursor, and
   ``lane-orch`` is listed and not running (no busy session, and no session
   inside a turn, as in point 3), the keeper wakes it (reason
   ``orch-events``). It resumes the newest session, or starts one from its
   start note. It wakes it at most once for each new batch: the line count of
   the last wake is ``_orch_batch`` in the state file. The 10-minute age and the
   30-minute cap do not apply, because a new batch is new work. Only events
   wake lane-orch: the plain wake of points 3 to 8 leaves it out.

10. CIV-LIVE (task 4060). :func:`stop_cause` reads the tail of the transcript of
    the newest session, and names why the last turn ended:

    - ``network``: an API error record of a lost connection or a 5xx status
      (ENOTFOUND, ECONNRESET, ETIMEDOUT, socket hang up, fetch failed). The
      keeper wakes it at the first pass after a backoff of 2, 5, 15 and 30
      minutes since the last transcript write, one step for each network wake. The backoff takes
      the place of the 10-minute age and the 30-minute cap. A turn that ends
      for another cause sets the step back to the first.
    - ``usage``: an API error record of a usage or rate limit. No wake before
      its reset time plus 2 minutes. With no reset time, no wake for 60
      minutes after the last transcript write. An account change ignores it.
    - ``signout``: a sign-in error of Claude, or an error result of a
      ``mcp__metorite__*`` tool in the last turn that says the sign-in is gone.
      No wake at all. When the stop row opens, one NOTE line goes to
      ``~/lanes/merge-queue.log``. It names the kind. Nothing repeats while the
      cause stays.
    - ``memory``: no end record, and the session has no live process.
    - ``normal``: any other end. ``memory`` and ``normal`` keep points 3 to 6.

    ``~/lanes/lane-stops.tsv`` holds one row for each stop of a fault cause:
    ``<iso start>\t<iso end or ->\t<lane>\t<cause>``. A row opens when the
    keeper first sees the stop, and it closes when the lane runs again, or
    when the lane leaves the list. A closed row older than 30 days goes. The
    weekly review reads it (``delivery_review.sh``, ``--time-lost``).

Every wake and every skip with a reason goes to ``~/lanes/keeper.log``, with
the cause, one line for each decision.
The keeper stops only the ended session of a lane that it is about to resume.
It starts a new session only for a listed lane that has none and has a start note.
Fence: tests/unit/test_lane_keeper.py.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

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
    "Your turn ended. Read ~/lanes/merge-bot.log, fix a BACK line on your PRs first, "
    "act on an owner answer that you asked for, then take your next free task. "
    "When you wait on CI or a merge, run one `sleep 600` and look again. "
    "When you hold an open owner question and have no other free work, check it "
    "with get_agent_run, and stop only if it is still open. Do not end the turn "
    "after a hand-over."
)
# CIV-ORCH (task 4058): the lane that reads ~/lanes/orch-events.log.
ORCH_LANE = "lane-orch"
ORCH_CURSOR = Path(
    os.environ.get(
        "KEEPER_ORCH_CURSOR", HOME / ".local" / "state" / "metorite" / "orch_events.cursor"
    )
)
ORCH_NOTE = (
    "New events wait in ~/lanes/orch-events.log. Read the lines after your cursor "
    "(~/.local/state/metorite/orch_events.cursor) and note the line count that you "
    "read. Act on each line as lane-orch.md says. Then write THAT count to the "
    "cursor, never a new `wc -l`: a line that came while you worked stays new. "
    "Never merge, deploy or flip a flag."
)

# CIV-LIVE (task 4060): why the last turn of a lane ended.
NETWORK_BACKOFF_S = (120, 300, 900, 1800)
USAGE_GRACE_S = 120
USAGE_DEFAULT_S = 3600
FAULT_CAUSES = ("network", "usage", "signout", "memory")
END_SUBTYPES = ("turn_duration", "stop_hook_summary")
MEANINGFUL_TYPES = ("user", "assistant", "system")
TAIL_BYTES = 262144
# Only an API error record (isApiErrorMessage) or a tool result is read. Tool
# output such as a `git fetch` log holds "fetch failed" in a normal turn.
NETWORK_RE = re.compile(
    r"ENOTFOUND|ECONNRESET|ETIMEDOUT|ECONNREFUSED|EAI_AGAIN|socket hang up|fetch failed"
    r"|connection (?:error|lost|failed|refused)|request timed out|overloaded",
    re.I,
)
USAGE_RE = re.compile(r"hit your (?:[\w-]+ )?limit|usage limit|rate limit|limit reached", re.I)
SIGNOUT_RE = re.compile(
    r"please run /login|oauth token|invalid api key|not logged in|authentication[_ ](?:error|failed)",
    re.I,
)
# The tool result of a Metorite tool when the MCP sign-in is gone (real shapes,
# 2026-11): 'MCP server "metorite" needs you to sign in again (run /mcp ...)'
# and 'MCP server "metorite" needs authentication'.
MCP_SIGNOUT_RE = re.compile(r'MCP server "metorite" needs (?:you to sign in|authentication)', re.I)
RESET_RE = re.compile(
    r"resets(?: at)? (?:(?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}),? )?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)\s*\(UTC\)",
    re.I,
)
SIGNOUT_NOTE = {
    "claude": (
        "NOTE\tlane-keeper\t{hhmm} UTC: {lane} stopped on a sign-out of its Claude account. "
        "See docs/home_box.md §5.1: run hub-add-accounts, then start fresh lanes.\n"
    ),
    "metorite": (
        "NOTE\tlane-keeper\t{hhmm} UTC: {lane} stopped on a sign-out of the Metorite MCP. "
        "See the sign-in alarm (docs/home_box.md §5.2 and §5.3).\n"
    ),
}
# A reset further ahead than this is a wrong parse: the 60-minute wait serves.
RESET_MAX_S = 8 * 86400
# lane-stops.tsv keeps a closed row for this long.
STOPS_KEEP_S = 30 * 86400


class Stop(NamedTuple):
    """Why the last turn of a lane ended. ``reset_at`` is a usage reset (epoch s).
    ``kind`` names the sign-in of a ``signout``: ``claude`` or ``metorite``."""

    cause: str
    reset_at: float | None = None
    kind: str = ""


def _epoch(ts: object) -> float | None:
    if not isinstance(ts, str):
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(rec: dict) -> str:
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return ""


def _reset_at(rec: dict, text: str) -> float | None:
    """The reset time of a usage limit. The record field first, then the message."""
    base = _epoch(rec.get("timestamp"))
    t = _reset_raw(rec, text, base)
    if t is None or (base is not None and not base - 60 <= t <= base + RESET_MAX_S):
        return None
    return t


def _reset_raw(rec: dict, text: str, base: float | None) -> float | None:
    params = rec.get("apiErrorParams")
    for d in (
        rec.get("quotaLimits"),
        params.get("rate_limit_info") if isinstance(params, dict) else None,
    ):
        v = d.get("resetsAt") if isinstance(d, dict) else None
        if isinstance(v, (int, float)) and v > 0:
            return v / 1000 if v > 1e12 else float(v)
    m = RESET_RE.search(text)
    if not m or base is None:
        return None
    b = dt.datetime.fromtimestamp(base, dt.UTC)
    hour = int(m["h"]) % 12 + (12 if m["ap"].lower() == "pm" else 0)
    minute = int(m["m"] or 0)
    if m["mon"]:
        # The nearest date at or after the record. "Nov 23" on a Nov 25 record
        # is Nov 23 of next year, and the 8-day cap then drops it.
        try:
            month = list(calendar.month_abbr).index(m["mon"].title())
            t = b.replace(month=month, day=int(m["day"]), hour=hour, minute=minute, second=0, microsecond=0)
            if t < b:
                t = t.replace(year=t.year + 1)
        except ValueError:
            return None
    else:
        t = b.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if t <= b:
            t += dt.timedelta(days=1)
    return t.timestamp()


def _api_cause(rec: dict) -> Stop:
    err = str(rec.get("error") or "")
    api = str(rec.get("apiError") or "")
    status = rec.get("apiErrorStatus")
    status = status if isinstance(status, int) else 0
    text = _text(rec)
    if err == "authentication_failed" or status == 401 or SIGNOUT_RE.search(text):
        return Stop("signout", kind="claude")
    if err == "rate_limit" or api == "usage_limit_reached" or status == 429 or USAGE_RE.search(text):
        return Stop("usage", _reset_at(rec, text))
    if err == "server_error" or api == "connection_failed" or status >= 500 or NETWORK_RE.search(text):
        return Stop("network")
    return Stop("normal")


def _parts(rec: dict) -> list[dict]:
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [x for x in content if isinstance(x, dict)] if isinstance(content, list) else []


def _metorite_signout(turn: list[dict]) -> bool:
    """True when an error result of a ``mcp__metorite__*`` tool in ``turn`` says
    the sign-in is gone. The result of any other tool never counts: a Bash
    result can quote the text."""
    ids = {
        x.get("id")
        for rec in turn
        for x in _parts(rec)
        if x.get("type") == "tool_use" and str(x.get("name", "")).startswith("mcp__metorite__")
    }
    for rec in turn:
        for x in _parts(rec):
            if x.get("type") != "tool_result" or not x.get("is_error"):
                continue
            if x.get("tool_use_id") not in ids:
                continue
            c = x.get("content")
            if MCP_SIGNOUT_RE.search(c if isinstance(c, str) else json.dumps(c)):
                return True
    return False


def _is_prompt(rec: dict) -> bool:
    """A user record that starts a turn: text, not a tool result."""
    if rec.get("type") != "user":
        return False
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(
        isinstance(x, dict) and x.get("type") == "tool_result" for x in content
    )


def stop_cause(tail: str, *, alive: bool | None = None) -> Stop:
    """Why the last turn of a transcript tail ended. Pure.

    It walks back past bookkeeping records and past system records. An API
    error record (``isApiErrorMessage``) as the last user or assistant record
    names the cause. Else a Metorite sign-in error in a tool result of the last
    turn is ``signout``. Else no end record (``turn_duration`` or
    ``stop_hook_summary``) and ``alive`` False is ``memory``. Else ``normal``.
    ``alive`` None means the process state is not known."""
    ended = False
    last: dict | None = None
    turn: list[dict] = []
    for ln in reversed(tail.splitlines()):
        try:
            rec = json.loads(ln)
        except ValueError:
            continue  # a blank line, or the cut first line of the tail
        if not isinstance(rec, dict) or rec.get("type") not in MEANINGFUL_TYPES:
            continue
        if rec.get("type") == "system":
            if last is None and rec.get("subtype") in END_SUBTYPES:
                ended = True
            continue
        if last is None:
            last = rec
            if rec.get("type") == "assistant" and rec.get("isApiErrorMessage"):
                return _api_cause(rec)
        turn.append(rec)
        if _is_prompt(rec):
            break
    if _metorite_signout(turn):
        return Stop("signout", kind="metorite")
    if last is not None and not ended and alive is False:
        return Stop("memory")
    return Stop("normal")


def read_tail(path: Path, size: int = TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - size))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def pid_alive(pid: object) -> bool:
    """True when the process ``pid`` exists. A missing pid is no process."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


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
    causes: dict[str, Stop] | None = None,
    skips: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str, str]]:
    """The wakes of one pass: (lane, session id, reason). Pure, so a test can call it.

    ``causes`` holds the :class:`Stop` of each lane (CIV-LIVE). A lane with no
    entry is ``normal``. Each ended lane that does not wake gets one
    (lane, why) entry in ``skips``. The backoff step of a network lane is
    ``state["_net"][lane]``."""
    wakes = []
    causes = causes or {}
    skips = [] if skips is None else skips
    net = state.get("_net") if isinstance(state.get("_net"), dict) else {}
    for name, a in sorted(newest_per_lane(agents).items()):
        # An empty `only_lanes` wakes nothing: the list file is the whole list.
        # CIV-ORCH: only events wake lane-orch, through plan_orch.
        if name in skip_lanes or name not in only_lanes or name in busy or name == ORCH_LANE:
            continue
        age = ages.get(a["sessionId"])
        stale = a.get("status") == "idle" and age is not None and age >= STALE_S
        if a.get("state") not in ENDED and not stale:
            continue
        if age is None:
            continue
        stop = causes.get(name, Stop("normal"))
        reason = "account change" if force_all else (a["state"] if a.get("state") in ENDED else "stale-working")
        if stop.cause == "signout":
            skips.append((name, "no wake on a sign-out, a person must sign in"))
            continue
        if stop.cause == "network" and not force_all:
            step = int(net.get(name, 0))
            wait = NETWORK_BACKOFF_S[min(step, len(NETWORK_BACKOFF_S) - 1)]
            if age < wait:
                skips.append((name, f"network backoff {wait // 60} min, transcript {int(age // 60)} min old"))
                continue
            # The backoff takes the place of the 10-minute age and the 30-minute cap.
            wakes.append((name, a["sessionId"], reason))
            continue
        if stop.cause == "usage" and not force_all:
            until = usage_until(stop, now, age)
            if now < until:
                skips.append((name, f"usage limit, no wake before {_hhmm(until)} UTC"))
                continue
        if age < IDLE_S:
            skips.append((name, f"transcript {int(age // 60)} min old, under 10 min"))
            continue
        if not force_all and now - state.get(name, 0) < CAP_S:
            skips.append((name, "woken in the last 30 min"))
            continue
        wakes.append((name, a["sessionId"], reason))
    return wakes


def usage_until(stop: Stop, now: float, age: float) -> float:
    """No wake of a usage stop before this time: the reset plus 2 minutes, or
    60 minutes after the last transcript write."""
    if stop.reset_at:
        return stop.reset_at + USAGE_GRACE_S
    return now - age + USAGE_DEFAULT_S


def _hhmm(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%H:%M")


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def update_stops(
    open_stops: dict[str, dict], seen: dict[str, tuple[bool, str]], now: float
) -> list[tuple[str, str, str, str]]:
    """The stop log changes of one pass. Pure, and it updates ``open_stops``.

    ``seen`` maps a lane to (running, cause). Returns (action, lane, cause,
    start) for each change, where the action is ``open`` or ``close``. A row of a
    fault cause opens when the keeper first sees the stop. It closes when the
    lane runs, or when its last turn ended for another cause."""
    out: list[tuple[str, str, str, str]] = []
    # A lane that left the list, or lost its session or transcript: its open
    # row closes now and keeps its cause.
    for lane in sorted(set(open_stops) - set(seen)):
        row = open_stops.pop(lane)
        out.append(("close", lane, str(row.get("cause")), str(row.get("start"))))
    for lane, (running, cause) in sorted(seen.items()):
        row = open_stops.get(lane)
        if row and (running or row.get("cause") != cause):
            out.append(("close", lane, str(row.get("cause")), str(row.get("start"))))
            del open_stops[lane]
            row = None
        if not running and cause in FAULT_CAUSES and row is None:
            start = _iso(now)
            open_stops[lane] = {"cause": cause, "start": start}
            out.append(("open", lane, cause, start))
    return out


def write_stops(path: Path, changes: list[tuple[str, str, str, str]], now: float) -> None:
    """Apply :func:`update_stops` changes to ``lane-stops.tsv``. A close sets the
    end of the open row of the lane with that start."""
    if not changes:
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    lines = [ln for ln in lines if not _expired(ln, now)]
    end = _iso(now)
    for action, lane, cause, start in changes:
        if action == "open":
            lines.append(f"{start}\t-\t{lane}\t{cause}")
            continue
        for i, ln in enumerate(lines):
            f = ln.split("\t")
            if len(f) == 4 and f[0] == start and f[1] == "-" and f[2] == lane:
                lines[i] = f"{start}\t{end}\t{lane}\t{f[3]}"
                break
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    tmp.replace(path)


def _expired(line: str, now: float) -> bool:
    """A closed row that ended more than 30 days ago. An open row stays."""
    f = line.split("\t")
    if len(f) != 4 or f[1] == "-":
        return False
    end = _epoch(f[1])
    return end is not None and end < now - STOPS_KEEP_S


def time_lost(text: str, now: float, days: int = 7) -> str:
    """The "time lost by lane" table of the weekly review, from ``lane-stops.tsv``.

    Hours by cause for each lane, plus a total, inside the last ``days`` days.
    An open row counts until ``now``. Pure."""
    since = now - days * 86400
    hours: dict[str, dict[str, float]] = {}
    for ln in text.splitlines():
        f = ln.split("\t")
        if len(f) != 4:
            continue
        start = _epoch(f[0])
        end = now if f[1] == "-" else _epoch(f[1])
        if start is None or end is None:
            continue
        secs = min(end, now) - max(start, since)
        if secs <= 0:
            continue
        hours.setdefault(f[2], {}).setdefault(f[3], 0.0)
        hours[f[2]][f[3]] += secs / 3600
    title = f"### Time lost by lane (last {days} days, hours)\n\n"
    if not hours:
        return title + "No lane stopped on a fault in the window.\n"
    head = ["Lane", *FAULT_CAUSES, "Total"]
    rows = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    col = dict.fromkeys(FAULT_CAUSES, 0.0)
    for lane in sorted(hours):
        h = hours[lane]
        for c in FAULT_CAUSES:
            col[c] += h.get(c, 0.0)
        cells = [f"{h.get(c, 0.0):.1f}" for c in FAULT_CAUSES]
        total = sum(h.get(c, 0.0) for c in FAULT_CAUSES)
        rows.append(f"| {lane} | " + " | ".join(cells) + f" | {total:.1f} |")
    cells = [f"{col[c]:.1f}" for c in FAULT_CAUSES]
    rows.append("| Total | " + " | ".join(cells) + f" | {sum(col.values()):.1f} |")
    return title + "\n".join(rows) + "\n"


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
        if name in skip_lanes or name in have or name in busy or name == ORCH_LANE:
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
    cwd = str(HOME / "Metorite")
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


def _wake(name: str, sid: str, agent: dict, note: str = NOTE) -> tuple[int, str]:
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
            ["claude", "--bg", "--resume", sid, "-n", name, f"--remote-control={name}", note],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=where,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, f" {type(exc).__name__}"
    return r.returncode, (f" {r.stderr.strip()[:120]!r}" if r.returncode and r.stderr else "")


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _cursor(path: Path) -> int:
    """The line count that lane-orch handled. A missing or damaged file reads 0."""
    try:
        return int(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return 0


def plan_orch(
    *, lanes: set[str], skip_lanes: set[str], busy: set[str], total: int, cursor: int, batch: int
) -> bool:
    """True when lane-orch must wake for a new batch of events. Pure."""
    if ORCH_LANE not in lanes or ORCH_LANE in skip_lanes or ORCH_LANE in busy:
        return False
    return total > max(cursor, batch)


def _orch_wake(
    agents: list[dict],
    state: dict,
    now: float,
    wakes: list[tuple[str, str, str]],
    *,
    lanes: set[str],
    skip_lanes: set[str],
    busy: set[str],
    notes: dict[str, str],
    causes: dict[str, Stop] | None = None,
) -> None:
    """CIV-ORCH: wake lane-orch once for each new batch of events.

    CIV-LIVE: no wake while its last turn ended on a sign-out, and none before
    the reset of a usage limit. A network stop wakes it, because events are new work."""
    total = _count_lines(LANES_DIR / "orch-events.log")
    cursor = _cursor(ORCH_CURSOR)
    batch = state.get("_orch_batch", 0)
    batch = batch if isinstance(batch, int) else 0
    if not plan_orch(
        lanes=lanes, skip_lanes=skip_lanes, busy=busy, total=total, cursor=cursor, batch=batch
    ):
        return
    why_new = f"orch-events lines {max(cursor, batch) + 1}-{total}"
    a = newest_per_lane(agents).get(ORCH_LANE)
    if a and a.get("state") not in ENDED:
        # Inside a turn (a `sleep`, say): it is running. Same rule as plan().
        age = transcript_age(a["sessionId"], now, HOME / ".claude" / "projects")
        if not (a.get("status") == "idle" and age is not None and age >= STALE_S):
            log(f"skip {ORCH_LANE}: its session is inside a turn ({why_new})")
            return
    stop = (causes or {}).get(ORCH_LANE, Stop("normal"))
    if a and stop.cause == "signout":
        log(f"skip {ORCH_LANE} cause=signout: no wake on a sign-out ({why_new})")
        return
    if a and stop.cause == "usage":
        age = transcript_age(a["sessionId"], now, HOME / ".claude" / "projects") or 0.0
        until = usage_until(stop, now, age)
        if now < until:
            log(f"skip {ORCH_LANE} cause=usage: no wake before {_hhmm(until)} UTC ({why_new})")
            return
    if a:
        sid = a["sessionId"]
        rc, why = _wake(ORCH_LANE, sid, a, note=ORCH_NOTE)
        log(f"wake {ORCH_LANE} session={sid[:8]} reason=orch-events ({why_new}) exit={rc}{why}")
    elif ORCH_LANE in notes:
        sid = ""
        rc, why = _start(ORCH_LANE, notes[ORCH_LANE])
        log(f"start {ORCH_LANE} reason=orch-events ({why_new}) exit={rc}{why}")
    else:
        log(f"no session of {ORCH_LANE} and no start note, so no wake ({why_new})")
        return
    if rc == 0:
        state[ORCH_LANE] = now
        state["_orch_batch"] = total
        wakes.append((ORCH_LANE, sid, "orch-events"))


def _causes(
    agents: list[dict],
    lanes: set[str],
    busy: set[str],
    ages: dict[str, float | None],
    projects: Path,
) -> tuple[dict[str, Stop], dict[str, tuple[bool, str]]]:
    """The :class:`Stop` of each listed lane, and (running, cause) for the stop log."""
    causes: dict[str, Stop] = {}
    seen: dict[str, tuple[bool, str]] = {}
    for name, a in newest_per_lane(agents).items():
        sid = a.get("sessionId")
        if name not in lanes or not sid:
            continue
        path = next(iter(projects.glob(f"*/{sid}.jsonl")), None)
        if path is None:
            continue
        age = ages.get(sid)
        stale = a.get("status") == "idle" and age is not None and age >= STALE_S
        running = name in busy or (a.get("state") not in ENDED and not stale)
        try:
            stop = stop_cause(read_tail(path), alive=pid_alive(a.get("pid")))
        except Exception as exc:  # one bad tail must not stop the pass for all lanes
            log(f"cause {name}: read failed ({type(exc).__name__}), so cause=normal")
            stop = Stop("normal")
        causes[name] = stop
        seen[name] = (running, stop.cause)
    return causes, seen


def _wake_all(
    agents: list[dict],
    wakes: list[tuple[str, str, str]],
    causes: dict[str, Stop],
    state: dict,
    net: dict,
    now: float,
) -> bool:
    """Run the wakes of :func:`plan`. True when a wake of an account change failed."""
    failed_forced = False
    by_sid = {a["sessionId"]: a for a in agents if a.get("sessionId")}
    for name, sid, reason in wakes:
        cause = causes.get(name, Stop("normal")).cause
        rc, why = _wake(name, sid, by_sid.get(sid, {}))
        log(f"wake {name} session={sid[:8]} reason={reason} cause={cause} exit={rc}{why}")
        if rc == 0:
            state[name] = now
            if cause == "network":
                net[name] = int(net.get(name, 0)) + 1  # the next network stop waits longer
        elif reason == "account change":
            failed_forced = True
    return failed_forced


def _stop_log(
    state: dict, seen: dict[str, tuple[bool, str]], now: float, causes: dict[str, Stop]
) -> None:
    """Open and close rows of ``lane-stops.tsv``, and write one NOTE for a sign-out."""
    open_stops = state.get("_stops") if isinstance(state.get("_stops"), dict) else {}
    changes = update_stops(open_stops, seen, now)
    state["_stops"] = open_stops
    write_stops(LANES_DIR / "lane-stops.tsv", changes, now)
    for action, lane, cause, _ in changes:
        log(f"stop-log {action} {lane} cause={cause}")
        if action == "open" and cause == "signout":
            with open(LANES_DIR / "merge-queue.log", "a", encoding="utf-8") as f:
                kind = causes.get(lane, Stop("signout")).kind or "metorite"
                f.write(SIGNOUT_NOTE[kind].format(hhmm=_hhmm(now), lane=lane))


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
    causes, seen = _causes(agents, lanes, busy, ages, projects)
    net = state.get("_net") if isinstance(state.get("_net"), dict) else {}
    for name, (running, cause) in seen.items():
        if not running and cause != "network":
            net.pop(name, None)  # a turn that ended for another cause resets the backoff
    state["_net"] = net
    skips: list[tuple[str, str]] = []
    wakes = plan(
        agents,
        state,
        now,
        ages=ages,
        skip_lanes=_csv("KEEPER_SKIP_LANES", DEFAULT_SKIP_LANES),
        only_lanes=lanes,
        force_all=force_all,
        busy=busy,
        causes=causes,
        skips=skips,
    )
    for name, why in skips:
        log(f"skip {name} cause={causes.get(name, Stop('normal')).cause}: {why}")
    failed_forced = _wake_all(agents, wakes, causes, state, net, now)
    _stop_log(state, seen, now, causes)
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
    _orch_wake(
        agents, state, now, wakes, lanes=lanes, skip_lanes=skip_lanes, busy=busy, notes=notes,
        causes=causes,
    )
    if not failed_forced:
        state["_account"] = email
    LANES_DIR.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return wakes


def main_time_lost(argv: list[str]) -> int:
    """``--time-lost``: print the table of :func:`time_lost`. Writes nothing."""
    ap = argparse.ArgumentParser(prog="lane_keeper.py --time-lost")
    ap.add_argument("--time-lost", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--stops", default=str(LANES_DIR / "lane-stops.tsv"))
    ap.add_argument("--now", type=float, default=None)
    args = ap.parse_args(argv)
    try:
        text = Path(args.stops).read_text(encoding="utf-8")
    except OSError:
        text = ""
    now = time.time() if args.now is None else args.now
    sys.stdout.write(time_lost(text, now, args.days))
    return 0


if __name__ == "__main__":
    if "--time-lost" in sys.argv[1:]:
        sys.exit(main_time_lost(sys.argv[1:]))
    try:
        run_pass()
    except Exception as exc:  # the timer must not crash-loop
        log(f"keeper error: {exc!r}")
        sys.exit(1)
