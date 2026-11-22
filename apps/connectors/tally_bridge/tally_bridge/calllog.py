"""The call log and the call limit (brief section 6.5, rules 5 and 6).

**Rule 6, the call log.** One JSON line for each tool call: the time, the
tool, the inputs, the row count, the duration and a result code. The tools
never receive the token, and the line holds only the tool inputs, so no
token can reach the log. With ``LOG_DIR`` set, the lines go to
``calls.log`` in that folder. The file rotates at midnight and keeps 90 days.

**Rule 5, the call limit.** ``CallLimiter`` allows ``CALLS_PER_MINUTE`` calls
(60 by default) in any 60 seconds. The bridge makes one limiter for each MCP
server, and each token has its own server: ``/mcp`` for the read token now,
and ``/mcp-write`` for the write token in rung 3. So the limit holds for
each token. The queue of one in ``reader/client.py`` holds the other half of
rule 5: one Tally request at a time.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

CALL_LOG = "tally_bridge.calls"
KEEP_DAYS = 90
WINDOW_S = 60.0
_MAX_VALUE = 100

log = logging.getLogger(CALL_LOG)

# The time of the newest tool call, for step 4 of the setup screen.
_last_call: datetime | None = None


def last_call_at() -> datetime | None:
    return _last_call


def _value(v: Any) -> Any:
    """A logged input: a short scalar, never a whole object."""
    if v is None or isinstance(v, bool | int | float):
        return v
    text = str(v)
    return text if len(text) <= _MAX_VALUE else text[:_MAX_VALUE] + "..."


def record(
    tool: str,
    inputs: Mapping[str, Any] | None,
    code: str,
    duration_ms: float,
    row_count: int | None,
) -> None:
    global _last_call
    now = datetime.now(UTC)
    _last_call = now
    line = {
        "time": now.isoformat(timespec="seconds"),
        "tool": tool,
        "inputs": {str(k): _value(v) for k, v in (inputs or {}).items()},
        "row_count": row_count,
        "duration_ms": round(duration_ms),
        "result": code,
    }
    log.info(json.dumps(line, ensure_ascii=False, separators=(",", ":")))


def open_call_log(log_dir: str) -> Path | None:
    """Send the call lines to LOG_DIR/calls.log, rotated daily, kept 90 days."""
    if not log_dir:
        return None
    folder = Path(log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "calls.log"
    if not any(getattr(h, "baseFilename", None) == str(path) for h in log.handlers):
        handler = TimedRotatingFileHandler(
            path, when="midnight", backupCount=KEEP_DAYS, encoding="utf-8", utc=True
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    return path


class CallLimiter:
    """At most ``limit`` calls in any ``WINDOW_S`` seconds."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self._clock = clock
        self._calls: deque[float] = deque()

    def take(self) -> int:
        """Count one call. Returns 0 when it may run, or the seconds to wait."""
        now = self._clock()
        while self._calls and now - self._calls[0] >= WINDOW_S:
            self._calls.popleft()
        if len(self._calls) >= self.limit:
            return max(1, math.ceil(WINDOW_S - (now - self._calls[0])))
        self._calls.append(now)
        return 0
