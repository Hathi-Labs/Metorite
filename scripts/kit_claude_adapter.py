#!/usr/bin/env python3
"""Turn a Claude Code stream into a KIT-6 transcript (scripts/kit_conformance.py).

Stdlib only, no network. Claude Code prints one JSON object per line with
``claude -p --output-format stream-json --verbose``. The stream has no clock.
This script stamps each line with the time it arrives, so run it live in a pipe:

  claude -p "..." --output-format stream-json --verbose \
    | kit_claude_adapter.py record --client-version 2.1.322 --kit-version 2 \
        --scenario single > transcript.jsonl
  kit_conformance.py score transcript.jsonl

``convert`` takes lines that already hold ``_ts`` (seconds), for a test or a replay.

Mapping. A tool_use of an MCP tool becomes kind "tool" with the bare tool name
(``mcp__metorite__my_work`` gives ``my_work``). A Bash call becomes kind "shell",
named "worktree" when it makes a git worktree or runs scripts/worktree.sh, with
the branch in ``args.branch``. Edit, Write and NotebookEdit become "edit". A tool_use
with a ``parent_tool_use_id`` belongs to a sub-agent: its actor is
``subagent:<id>``. A post_agent_activity call that sends ``waiting`` adds a
"wait" note and a "park" note that ends ``until_minutes`` later.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date

EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})
WORKTREE_CMD = re.compile(r"git\s+worktree\s+add|worktree\.sh\s+new")
BRANCH_FLAG = re.compile(r"(?:\s-b\s+|\s-B\s+)(\S+)")
SHORT_ID = re.compile(r"mt-[0-9a-f]{8}[\w./-]*")
MCP_NAME = re.compile(r"^mcp__[^_]+(?:_[^_]+)*?__(.+)$")


def tool_name(raw: str) -> str:
    """``mcp__metorite__my_work`` -> ``my_work``. A name with no MCP prefix stays."""
    found = MCP_NAME.match(raw)
    return found.group(1) if found else raw


def _branch(cmd: str) -> str:
    """The branch of a worktree command: the -b value, else the first short-id token."""
    flag = BRANCH_FLAG.search(cmd)
    if flag:
        return flag.group(1).strip("'\"")
    found = SHORT_ID.search(cmd)
    return found.group(0) if found else ""


def _events(block: dict, t: float, actor: str) -> list[dict]:
    raw = str(block.get("name", ""))
    args = block.get("input") if isinstance(block.get("input"), dict) else {}
    if raw in EDIT_TOOLS:
        return [{"t": t, "kind": "tool", "name": "edit", "args": {}, "actor": actor}]
    if raw == "Bash":
        cmd = str(args.get("command", ""))
        if WORKTREE_CMD.search(cmd):
            return [{"t": t, "kind": "shell", "name": "worktree",
                     "args": {"cmd": cmd, "branch": _branch(cmd)}, "actor": actor}]
        return [{"t": t, "kind": "shell", "name": "bash", "args": {"cmd": cmd}, "actor": actor}]
    if not raw.startswith("mcp__"):
        return []
    name = tool_name(raw)
    out = [{"t": t, "kind": "tool", "name": name, "args": args, "actor": actor}]
    waiting = args.get("waiting") if name == "post_agent_activity" else None
    if isinstance(waiting, dict) and isinstance(waiting.get("until_minutes"), (int, float)):
        out.append({"t": t, "kind": "note", "name": "wait", "args": {}, "actor": actor})
        out.append({"t": t, "kind": "note", "name": "park",
                    "args": {"until": t + 60 * float(waiting["until_minutes"])}, "actor": actor})
    return out


def convert(lines: list[str], client_version: str, kit_version: int, scenario: str,
            client: str = "Claude Code", day: str | None = None) -> list[dict]:
    """The transcript: a header, then the events, with t counted from the first line."""
    header = {"client": client, "client_version": client_version, "scenario": scenario,
              "kit_version": kit_version, "date": day or date.today().isoformat()}
    events: list[dict] = []
    origin: float | None = None
    for line in lines:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict) or msg.get("type") != "assistant" or "_ts" not in msg:
            continue
        stamp = float(msg["_ts"])
        origin = stamp if origin is None else origin
        parent = msg.get("parent_tool_use_id")
        actor = f"subagent:{parent}" if parent else "coordinator"
        content = (msg.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                events += _events(block, round(stamp - origin, 3), actor)
    return [header, *events]


def record(stream, client_version: str, kit_version: int, scenario: str) -> list[dict]:
    """Read a live stream and stamp each line with its arrival time."""
    lines = []
    for raw in stream:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict):
            msg["_ts"] = time.time()
            lines.append(json.dumps(msg))
    return convert(lines, client_version, kit_version, scenario)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("record", "convert"):
        print(__doc__)
        return 2
    opts = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1, 2) if argv[i].startswith("--")}
    for need in ("--client-version", "--kit-version", "--scenario"):
        if need not in opts:
            print(f"error: {need} is required", file=sys.stderr)
            return 2
    args = (opts["--client-version"], int(opts["--kit-version"]), opts["--scenario"])
    rows = record(sys.stdin, *args) if argv[0] == "record" else convert(sys.stdin.read().splitlines(), *args)
    for row in rows:
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
