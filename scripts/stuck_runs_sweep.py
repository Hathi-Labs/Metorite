"""Flag an agent run that waits on the owner with no open question (CIV-14e).

The owner cannot answer a thought. A run whose newest line says it waits on
the owner, with no open question for 30 minutes, is stuck. This script reads
the JSON that `list_agent_runs` returns, and prints each stuck run. The caller
holds the Metorite credential and posts the flag comment. This script holds no
credential.

    python scripts/stuck_runs_sweep.py runs.json [--minutes 30] [--now ISO] [--json]

The input is a list of runs, or an object with a `runs` list.
Exit code: 0 when no run is stuck, 1 when one or more are.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

DEFAULT_MINUTES = 30
OPEN_STATUSES = {"created", "in_progress", "awaiting", "working"}
WAIT_RE = re.compile(
    r"AWAITING-OWNER"
    r"|\bwait(?:s|ing|ed)?\s+(?:on|for)\s+(?:the\s+)?(?:owner|your\s+(?:answer|approval|review|preview)|a\s+decision)"
    r"|\bawait(?:s|ing)?\s+(?:the\s+)?owner"
    r"|\b(?:owner|your)\s+(?:preview|approval)\b.{0,40}\bwait",
    re.IGNORECASE,
)
FLAG_TEXT = (
    "This run says it waits on the owner, and it has no open question for "
    "{minutes} minutes. The owner cannot answer a thought. Call `ask_on_task` "
    "now with the PR, what changed, where to click and the review result. Then "
    "take your next task."
)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _text(value) -> str:
    if isinstance(value, dict):
        value = value.get("untrusted_text", "")
    return value if isinstance(value, str) else ""


def find_stuck(runs: list[dict], now: datetime, minutes: int = DEFAULT_MINUTES) -> list[dict]:
    """Return the stuck runs: open, newest line waits on the owner, no open question."""
    limit = timedelta(minutes=minutes)
    stuck = []
    for run in runs:
        if run.get("status") not in OPEN_STATUSES and run.get("state") not in OPEN_STATUSES:
            continue
        if run.get("open_question"):
            continue
        line = run.get("latest_line") or {}
        body = _text(line.get("body"))
        if not WAIT_RE.search(body):
            continue
        since = _parse(line.get("created_at"))
        if since is None or now - since < limit:
            continue
        stuck.append(
            {
                "run_id": run.get("id") or run.get("run_id"),
                "task_id": run.get("task_id"),
                "waiting_minutes": int((now - since).total_seconds() // 60),
                "line": body[:200],
                "flag": FLAG_TEXT.format(minutes=minutes),
            }
        )
    return stuck


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs_file", help="JSON from list_agent_runs, or - for stdin")
    ap.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    ap.add_argument("--now", help="ISO time, for a test")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raw = sys.stdin.read() if args.runs_file == "-" else open(args.runs_file, encoding="utf-8").read()
    data = json.loads(raw)
    runs = data.get("runs", []) if isinstance(data, dict) else data
    now = _parse(args.now) if args.now else datetime.now(timezone.utc)
    stuck = find_stuck(runs, now, args.minutes)
    if args.json:
        print(json.dumps(stuck, indent=2))
    else:
        for s in stuck:
            print(f"stuck, no question: run {s['run_id']} task {s['task_id']} ({s['waiting_minutes']} min): {s['line']}")
        print(f"{len(stuck)} stuck run(s)")
    return 1 if stuck else 0


if __name__ == "__main__":
    sys.exit(main())
