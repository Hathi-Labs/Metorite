#!/usr/bin/env python3
"""Score a recorded agent run against the kit protocol (KIT-6).

Stdlib only, no network. A transcript is a JSONL file. Line 1 is a header:
  {"client", "client_version", "scenario": "single"|"coordinator", "kit_version", "date"}
Each later line is one event:
  {"t": seconds, "kind": "tool"|"shell"|"note", "name": str, "args": {...}, "actor": str}
A "note" event named "park" holds {"until": t}. It declares a park from its own t to "until".
A "note" event named "wait" marks the point where the run waits for something.

Usage:
  kit_conformance.py score TRANSCRIPT.jsonl
  kit_conformance.py matrix
  kit_conformance.py gate OLD.json NEW.json
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The tool names this script reads. The unit test asserts that each one is a real tool.
KNOWN_TOOLS = frozenset({
    "my_work", "get_plan", "get_task_context", "claim_task", "post_agent_activity",
    "add_comment", "set_task_status", "ask_on_task", "release_task", "finish_agent_run",
    "start_agent_run", "create_task", "add_task_reference",
})
# Tools that change task state or post to a run.
WRITE_TOOLS = frozenset({
    "claim_task", "post_agent_activity", "add_comment", "set_task_status", "ask_on_task",
    "release_task", "finish_agent_run", "start_agent_run", "create_task", "add_task_reference",
})
HEARTBEAT_TOOLS = frozenset({"post_agent_activity", "add_comment"})
DISPOSITION_STATUS = re.compile(r"review|done", re.I)
BRANCH_ID = re.compile(r"mt-[0-9a-f]{8}")
MAX_GAP_SECONDS = 60
MAX_SLEEP_SECONDS = 30
#: A declared park excuses at most this many seconds, and only right after a thought or a question.
MAX_PARK_SECONDS = 900

SINGLE_STEPS = [
    ("connect_reads", "Reads my_work, then get_plan or get_task_context, before the first write."),
    ("claims_slice", "Calls claim_task once for the slice, before any post_agent_activity."),
    ("worktree", "Makes a worktree with the task short id in the branch, before the first edit."),
    ("heartbeat", "Posts activity or a comment at least once a minute, apart from a declared park."),
    ("parks_at_wait", "Parks with a thought or a question before a wait. Never sleeps in the session."),
    ("one_disposition", "Ends with exactly one disposition: review or done, a question, or a release."),
    ("finishes_run", "Calls finish_agent_run after the disposition."),
]
COORDINATOR_STEPS = [
    *SINGLE_STEPS,
    ("two_subagents", "Two different sub-agents appear in the run."),
    ("subagents_silent", "No sub-agent writes or posts."),
    ("coordinator_posts", "The coordinator posts activity while the sub-agents run."),
]
SCENARIOS = {"single": SINGLE_STEPS, "coordinator": COORDINATOR_STEPS}

CLIENTS = [
    "Claude Code",
    "OpenAI Codex",
    "GitHub Copilot editor",
    "GitHub Copilot coding agent",
    "Gemini CLI",
    "Google Antigravity",
]
HEADER_KEYS = ("client", "client_version", "scenario", "kit_version", "date")


@dataclass
class Step:
    id: str
    passed: bool
    reason: str


@dataclass
class Result:
    header: dict
    steps: list[Step] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.steps if s.passed)

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def score(self) -> str:
        return f"{self.passed}/{self.total}"

    @property
    def failed_ids(self) -> list[str]:
        return [s.id for s in self.steps if not s.passed]


def _is_sub(e: dict) -> bool:
    return str(e.get("actor", "coordinator")).startswith("subagent:")


def _tool(e: dict, *names: str) -> bool:
    return e.get("kind") == "tool" and e.get("name") in names


def _first(events: list[dict], pred) -> int | None:
    for i, e in enumerate(events):
        if pred(e):
            return i
    return None


def _is_write(e: dict) -> bool:
    return _tool(e, *WRITE_TOOLS) or e.get("name") == "edit"


def _is_disposition(e: dict) -> bool:
    if _tool(e, "ask_on_task", "release_task"):
        return True
    status = str((e.get("args") or {}).get("status", ""))
    return _tool(e, "set_task_status") and bool(DISPOSITION_STATUS.search(status))


def _sleep_seconds(e: dict) -> float | None:
    """Seconds of a sleeping shell event, or None when the event does not sleep."""
    if e.get("kind") != "shell":
        return None
    name = str(e.get("name", ""))
    args = e.get("args") or {}
    text = name + " " + str(args.get("cmd", args.get("command", "")))
    if name in ("until", "while") or re.search(r"\b(until|while)\b.*\bsleep\b", text):
        return float("inf")
    if re.search(r"\bsleep\b", text):
        if "seconds" in args:
            return float(args["seconds"])
        m = re.search(r"\bsleep\s+(\d+(?:\.\d+)?)", text)
        return float(m.group(1)) if m else float("inf")
    return None


def _step_connect(ev):
    i = _first(ev, lambda e: _tool(e, "my_work"))
    if i is None:
        return False, "my_work was never called"
    ctx = _first(ev, lambda e: _tool(e, "get_plan", "get_task_context"))
    if ctx is None or ctx < i:
        return False, "neither get_plan nor get_task_context came after my_work"
    w = _first(ev, _is_write)
    if w is not None and w < ctx:
        return False, "a write came before the plan was read"
    return True, "my_work, then the plan, then the first write"


def _step_claim(ev):
    claims = [e for e in ev if _tool(e, "claim_task")]
    if not claims:
        return False, "claim_task was never called"
    ids = [(e.get("args") or {}).get("task_id") for e in claims]
    if any(not i for i in ids):
        return False, "claim_task had no task_id"
    if len(ids) != len(set(ids)):
        return False, "the same slice was claimed more than once"
    first_claim = _first(ev, lambda e: _tool(e, "claim_task"))
    post = _first(ev, lambda e: _tool(e, "post_agent_activity"))
    if post is not None and post < first_claim:
        return False, "activity was posted before the claim"
    return True, "one claim per slice, before any activity"


def _step_worktree(ev):
    def is_wt(e):
        if e.get("kind") != "shell":
            return False
        text = (str(e.get("name", "")) + " " + json.dumps(e.get("args") or {})).lower()
        if "worktree" not in text:
            return False
        return bool(BRANCH_ID.search(str((e.get("args") or {}).get("branch", ""))))

    i = _first(ev, is_wt)
    if i is None:
        return False, "no worktree command with a branch that holds the task short id"
    edit = _first(ev, lambda e: e.get("name") == "edit")
    if edit is not None and edit < i:
        return False, "a file was edited before the worktree"
    return True, "worktree made before the first edit"


def _parks(ev):
    """The windows that a declared park excuses. A park counts only when a thought
    or a question came at most MAX_GAP_SECONDS before it, and it excuses at most
    MAX_PARK_SECONDS. A longer wait means the agent ends the session and a later
    session resumes the run."""
    found = []
    for e in ev:
        if e.get("kind") != "note" or e.get("name") != "park":
            continue
        said = [p for p in ev if p.get("kind") == "tool" and p["t"] <= e["t"]
                and e["t"] - p["t"] <= MAX_GAP_SECONDS
                and (p["name"] == "ask_on_task" or (p["name"] == "post_agent_activity"
                     and (p.get("args") or {}).get("type") == "thought"))]
        if not said:
            continue
        until = float((e.get("args") or {}).get("until", e["t"]))
        found.append((e["t"], min(until, e["t"] + MAX_PARK_SECONDS)))
    return found


def _step_heartbeat(ev):
    claim = next((e for e in ev if _tool(e, "claim_task")), None)
    if claim is None:
        return False, "no claim, so no heartbeat to measure"
    start = claim["t"]
    end = max(e["t"] for e in ev)
    pts = sorted(e["t"] for e in ev if _tool(e, *HEARTBEAT_TOOLS) and e["t"] >= start)
    parks = _parks(ev)
    marks = [start, *pts, end]
    for a, b in itertools.pairwise(marks):
        gap = b - a
        for ps, pe in parks:
            gap -= max(0.0, min(b, pe) - max(a, ps))
        if gap > MAX_GAP_SECONDS:
            return False, f"{gap:.0f} seconds with no activity after t={a:g}"
    return True, f"no gap longer than {MAX_GAP_SECONDS} seconds"


def _step_parks(ev):
    for e in ev:
        s = _sleep_seconds(e)
        if s is not None and s > MAX_SLEEP_SECONDS:
            return False, f"the session slept at t={e['t']:g}"
    for i, e in enumerate(ev):
        if e.get("kind") == "note" and e.get("name") == "wait":
            prev = next((p for p in reversed(ev[:i]) if p.get("kind") == "tool"), None)
            ok = prev is not None and (
                prev["name"] == "ask_on_task"
                or (prev["name"] == "post_agent_activity"
                    and (prev.get("args") or {}).get("type") == "thought"))
            if not ok:
                return False, f"a wait at t={e['t']:g} had no thought or question before it"
    return True, "no long sleep and each wait was parked"


def _step_disposition(ev):
    d = [e for e in ev if _is_disposition(e)]
    if not d:
        return False, "no disposition"
    kinds = {"status" if e["name"] == "set_task_status" else e["name"] for e in d}
    if len(kinds) > 1:
        return False, "two different dispositions: " + ", ".join(sorted(kinds))
    if len(d) > 1:
        return False, "the same disposition came more than once"
    return True, "one disposition: " + next(iter(kinds))


def _step_finish(ev):
    d = _first(ev, _is_disposition)
    f = _first(ev, lambda e: _tool(e, "finish_agent_run"))
    if f is None:
        return False, "finish_agent_run was never called"
    if d is None or f < d:
        return False, "finish_agent_run came before the disposition"
    return True, "finish_agent_run after the disposition"


def score(events: list[dict], header: dict) -> Result:
    if not isinstance(header, dict) or any(k not in header for k in HEADER_KEYS):
        raise ValueError("transcript header must hold: " + ", ".join(HEADER_KEYS))
    scenario = header["scenario"]
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}, use one of {sorted(SCENARIOS)}")
    for e in events:
        actor = str(e.get("actor", "coordinator"))
        if actor != "coordinator" and not actor.startswith("subagent:"):
            raise ValueError(f"unknown actor {actor!r}: use 'coordinator' or 'subagent:<id>'")
    ordered = sorted(events, key=lambda e: e["t"])
    main = [e for e in ordered if not _is_sub(e)]
    subs = [e for e in ordered if _is_sub(e)]
    fns = {
        "connect_reads": _step_connect, "claims_slice": _step_claim, "worktree": _step_worktree,
        "heartbeat": _step_heartbeat, "parks_at_wait": _step_parks,
        "one_disposition": _step_disposition, "finishes_run": _step_finish,
    }
    res = Result(header=header)
    for sid, _desc in SCENARIOS[scenario]:
        if sid in fns:
            ok, why = fns[sid](main)
        elif sid == "two_subagents":
            n = len({e["actor"] for e in subs})
            ok, why = n >= 2, f"{n} sub-agent actor(s) seen"
        elif sid == "subagents_silent":
            bad = [e for e in subs if _tool(e, *WRITE_TOOLS)]
            ok = not bad
            why = "no sub-agent write" if ok else f"{bad[0]['actor']} called {bad[0]['name']}"
        else:  # coordinator_posts
            lo = min((e["t"] for e in subs), default=0)
            hi = max((e["t"] for e in subs), default=0)
            ok = bool(subs) and any(
                _tool(e, "post_agent_activity") and lo <= e["t"] <= hi for e in main)
            why = ("coordinator posted while sub-agents ran" if ok
                   else "no coordinator post while sub-agents ran")
        res.steps.append(Step(sid, ok, why))
    return res


def load(path: Path) -> tuple[dict, list[dict]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty transcript")
    return json.loads(lines[0]), [json.loads(ln) for ln in lines[1:]]


def row(res: Result) -> str:
    h = res.header
    fails = ", ".join(res.failed_ids) or "none"
    return (f"| {h['client']} | {h['client_version']} | {h['scenario']} | {h['kit_version']} "
            f"| {h['date']} | {res.score} | {fails} |")


RESULTS_FILE = Path(__file__).resolve().parents[1] / "docs" / "kit_conformance_results.jsonl"


def load_results(path: Path = RESULTS_FILE) -> list[dict]:
    """The stored runs: one JSON object per line, as ``score --json`` prints it, plus ``score``."""
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def matrix(results: list[dict] | None = None) -> str:
    """One row for each client. A client with a stored run shows its newest run."""
    newest: dict[str, dict] = {}
    for r in sorted(results or [], key=lambda r: str(r.get("date", ""))):
        newest[r.get("client")] = r
    out = ["| Client | Client version | Scenario | Kit version | Date | Score | Failed steps |",
           "|---|---|---|---|---|---|---|"]
    for c in CLIENTS:
        r = newest.get(c)
        if r is None:
            out.append(f"| {c} | not run | not run | not run | not run | not run | not run |")
            continue
        steps = SCENARIOS[r["scenario"]]
        failed = [sid for sid, _ in steps if sid not in r.get("passed_steps", [])]
        out.append(f"| {c} | {r['client_version']} | {r['scenario']} | {r['kit_version']} | {r['date']} "
                   f"| {len(steps) - len(failed)}/{len(steps)} | {', '.join(failed) or 'none'} |")
    return "\n".join(out)


def gate(previous_rows: list[dict], new_row: dict) -> list[str]:
    """Return the steps that passed before and fail now. A non-empty list blocks the release."""
    before: set[str] = set()
    for r in previous_rows:
        if r.get("client") == new_row.get("client") and r.get("scenario") == new_row.get("scenario"):
            before |= set(r.get("passed_steps", []))
    return sorted(before - set(new_row.get("passed_steps", [])))


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("score", "matrix", "gate"):
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "matrix":
        print(matrix(load_results()))
        return 0
    if cmd == "score":
        header, events = load(Path(argv[1]))
        res = score(events, header)
        print(row(res))
        if "--json" in argv:
            print(json.dumps({**res.header,
                              "passed_steps": [s.id for s in res.steps if s.passed]}))
        return 1 if res.failed_ids else 0
    old = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    new = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    dropped = gate(old if isinstance(old, list) else [old], new)
    for s in dropped:
        print(f"dropped: {s}")
    return 1 if dropped else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError, IndexError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
