#!/usr/bin/env python3
"""CIV-ORCH: the orchestrator watch of the home box (Metorite task 4058).

**Why.** The orchestrator and its watch loop (``~/lanes/orch_watch.sh``) ran on
the owner's PC. When the PC shut down, nothing watched the lanes. This file
is ONE pass of the same rules. ``metorite-orch-watch.timer`` runs it every 5
minutes on the box, so it has no loop and no sleep.

**The rules.** One pass looks for three events:

- (a) ``idle``: a listed lane is idle, and its transcript is older than
  :data:`IDLE_MIN` minutes. Or it is active, and its transcript is older than
  :data:`ACTIVE_MIN` minutes. A lane on ``~/lanes/orch_resting.txt`` and the
  lane ``lane-orch`` itself are left out. The lanes come from
  ``~/lanes/keeper-lanes.txt`` and ``claude agents --json``, never from a
  hard-coded list.
- (b) ``back``, ``hold`` or ``red``: a new line since the last pass in one of
  two logs. Lane-merge writes ``HOLD``, ``RED`` and ``DONE`` lines to
  ``~/lanes/merge-queue.log``: ``BACK``, ``HOLD`` and ``RED`` there are events.
  The merge bot writes ``BACK``, ``RESOLVED`` and ``RENUMBERED`` lines to
  ``~/lanes/merge-bot.log``: ``BACK`` and ``RED`` there are events. Each log has
  its own position file. The first pass starts at the end of each log, so old
  lines make no event.
- (c) ``merger-silent``: no activity of a lane whose name starts with
  ``lane-merge`` (``lane-merge-1126``) for :data:`MERGER_MIN`
  minutes. Activity is a new NOTE line of lane-merge in the queue log, or a
  write to the transcript of lane-merge. A NOTE line is of lane-merge when its
  second field starts with ``lane-merge`` (``lane-merge``, ``lane-merge-1126``).

**The output.** One line for each new event goes to ``~/lanes/orch-events.log``::

    EVENT\\t<kind>\\t<HH:MM> UTC\\t<subject>\\t<detail>

**No repeat.** An event of rule (a) or (c) is a condition with a signature.
The watch writes it once, and keeps the signature in
``~/.local/state/metorite/orch_watch.json``. While the signature stays the
same, it writes nothing. When the condition clears, the key goes. When the
signature changes, it writes a new event.

**The idle state** mirrors ``~/lanes/idle_check.sh`` (the PC loop's helper):
the last meaningful transcript record ``turn_duration`` or
``stop_hook_summary`` means the turn ended. A meaningful record has the type
user, assistant or system. The read walks back past bookkeeping records. Then a ``bash -c`` child of the session process means
``waiting`` (it wakes by itself), and no child means ``idle``. Any other last
record means ``active``. A ``waiting`` lane makes no event.

**It never prints a token**, and it reads no credential. Each subject and detail
passes through the ONE credential list, ``acb_common/secret_shapes.py``
(task 1272), loaded by file path. A match becomes ``<redacted>``. With no copy
of the list, the watch withholds the whole detail.

The lane ``lane-orch`` reads the events log (``lanes/lane-orch.md``). The lane
keeper wakes it when the log has lines past its cursor.

Fence: ``tests/unit/test_orch_watch.py``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# Reuse the pure readers of the lane keeper. The timer runs an installed copy,
# and orch_watch.sh installs lane_keeper.py beside this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_keeper import newest_per_lane

HOME = Path(os.environ.get("HOME", "~")).expanduser()
LANES_DIR = Path(os.environ.get("KEEPER_LANES_DIR", HOME / "lanes"))
STATE_DIR = Path(os.environ.get("ORCH_STATE_DIR", HOME / ".local" / "state" / "metorite"))
PROJECTS = HOME / ".claude" / "projects"

IDLE_MIN = 30
ACTIVE_MIN = 60
MERGER_MIN = 90
ORCH_LANE = "lane-orch"
MERGE_LANE = "lane-merge"
ENDED_RECORDS = ("turn_duration", "stop_hook_summary")
QUEUE_RE = re.compile(r"^(BACK|HOLD|RED)\b\s*(.*)$")
NOTE_RE = re.compile(r"^NOTE\s+lane-merge\S*(\s|$)")
# The kinds that are events in each log (rule b).
QUEUE_KINDS = frozenset({"BACK", "HOLD", "RED"})
BOT_KINDS = frozenset({"BACK", "RED"})
DETAIL_MAX = 300
# The record types that say what a turn did. Any other type is bookkeeping
# (tools-used, cost-state, queue-operation, bridge-session, last-prompt, ...).
MEANINGFUL_TYPES = frozenset({"user", "assistant", "system"})
TAIL_BYTES = 262144
WITHHELD = "<detail withheld: no secret_shapes.py to scrub it>"
# The ONE credential list (task 1272): packages/acb_common/acb_common/secret_shapes.py.
# The timer runs /usr/bin/python3 outside the venv, so it loads the file by path.
# orch_watch.sh installs a copy beside this file. In a checkout, the repo copy serves.
_SHAPES_REL = Path("packages") / "acb_common" / "acb_common" / "secret_shapes.py"


def _shape_files() -> list[Path]:
    here = Path(__file__).resolve().parent
    return [
        here / "secret_shapes.py",
        here.parents[1] / _SHAPES_REL,
        HOME / "Metorite" / _SHAPES_REL,
    ]


def load_shapes(files: list[Path] | None = None) -> tuple[re.Pattern[str], ...] | None:
    """Every scrub pattern of the one list, or None when no copy loads."""
    for f in _shape_files() if files is None else files:
        if not f.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("orch_secret_shapes", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return (
                *(rx for _, rx in mod.SECRET_SHAPES),
                *mod.REDACTION_SHAPES,
                mod.REDACTION_BEARER,
            )
        except Exception:  # a broken copy: try the next one
            continue
    return None


SHAPES = load_shapes()


def queue_log() -> Path:
    return LANES_DIR / "merge-queue.log"


def bot_log() -> Path:
    return LANES_DIR / "merge-bot.log"


def events_log() -> Path:
    return LANES_DIR / "orch-events.log"


def state_path() -> Path:
    return STATE_DIR / "orch_watch.json"


def pos_path() -> Path:
    return STATE_DIR / "orch_watch.pos"


def bot_pos_path() -> Path:
    return STATE_DIR / "orch_watch_bot.pos"


def scrub(text: str) -> str:
    """One line with no tab and no credential, cut to :data:`DETAIL_MAX` characters.

    It fails closed: with no copy of the one list, the whole text is withheld."""
    if not text:
        return ""
    if SHAPES is None:
        return WITHHELD
    for rx in SHAPES:
        text = rx.sub("<redacted>", text)
    return re.sub(r"[\t\r\n]+", " ", text).strip()[:DETAIL_MAX]


def listed_lanes() -> set[str]:
    """The names in keeper-lanes.txt. Mirrors ``lane_keeper.lane_names``, which reads
    a module path, so this reads the same file by its own path."""
    try:
        text = (LANES_DIR / "keeper-lanes.txt").read_text(encoding="utf-8")
    except OSError:
        return set()
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}


def resting() -> set[str]:
    """The first word of each line of orch_resting.txt: a lane name or a session id start."""
    try:
        text = (LANES_DIR / "orch_resting.txt").read_text(encoding="utf-8")
    except OSError:
        return set()
    out = set()
    for ln in text.splitlines():
        words = ln.split()
        if words and not words[0].startswith("#"):
            out.add(words[0])
    return out


def rests(name: str, sid: str, rest: set[str]) -> bool:
    return name in rest or any(len(r) >= 8 and sid.startswith(r) for r in rest)


def transcript(sid: str) -> Path | None:
    for p in PROJECTS.glob(f"*/{sid}.jsonl"):
        return p
    return None


def last_record(path: Path) -> str:
    """The subtype or type of the last meaningful record of a transcript.

    It walks back past bookkeeping records, whose type is not user, assistant or
    system. A real tail ends ``turn_duration, tools-used, tools-used``. Empty when
    no meaningful record is in the tail."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    for ln in reversed(tail.splitlines()):
        try:
            rec = json.loads(ln)
        except ValueError:
            continue  # a blank line, or the cut first line of the tail
        if isinstance(rec, dict) and rec.get("type") in MEANINGFUL_TYPES:
            return str(rec.get("subtype") or rec.get("type"))
    return ""


def has_shell_child(pid: int | None) -> bool:
    """True when a ``bash -c`` process is a child of ``pid``. Reads /proc."""
    if not pid:
        return False
    for d in Path("/proc").glob("[0-9]*"):
        try:
            stat = (d / "stat").read_text(encoding="utf-8", errors="replace")
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            if ppid != int(pid):
                continue
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except (OSError, ValueError, IndexError):
            continue
        if "bash -c" in cmd:
            return True
    return False


def idle_conditions(
    agents: list[dict],
    lanes: set[str],
    rest: set[str],
    now: float,
    child: Callable[[int | None], bool] = has_shell_child,
) -> dict[str, tuple[str, str, str]]:
    """Rule (a). key -> (signature, subject, detail)."""
    out: dict[str, tuple[str, str, str]] = {}
    for name, a in sorted(newest_per_lane(agents).items()):
        sid = a.get("sessionId") or ""
        if name not in lanes or name == ORCH_LANE or not sid or rests(name, sid, rest):
            continue
        path = transcript(sid)
        if path is None:
            continue
        mtime = path.stat().st_mtime
        age_min = int((now - mtime) // 60)
        if last_record(path) in ENDED_RECORDS:
            if child(a.get("pid")):
                continue  # waiting: a shell child wakes it
            state, limit = "idle", IDLE_MIN
        else:
            state, limit = "active", ACTIVE_MIN
        if age_min <= limit:
            continue
        detail = f"{state} {age_min} min, session {sid[:8]}"
        out[f"idle:{name}"] = (f"{sid}:{int(mtime)}", name, detail)
    return out


def _mark(f, offset: int) -> str:
    """A short hash of the up to 64 bytes before ``offset``."""
    f.seek(max(0, offset - 64))
    return hashlib.sha256(f.read(min(offset, 64))).hexdigest()[:16]


def read_queue(path: Path, pos: dict | None) -> tuple[list[str], dict]:
    """The complete new lines since ``pos`` and the new position.

    With no position, the place is the end of the log and no line is new. The
    log is read from its start when it has a new inode, when it is shorter than
    the offset, or when the bytes before the offset changed. The last case is a
    log truncated in place that then grew past the old offset."""
    try:
        with open(path, "rb") as f:
            return _read_from(f, pos)
    except OSError:
        return [], pos or {"inode": 0, "offset": 0, "mark": ""}


def _read_from(f, pos: dict | None) -> tuple[list[str], dict]:
    """Read ``f`` from ``pos``. See :func:`read_queue`."""
    st = os.fstat(f.fileno())
    if pos is None:
        return [], {"inode": st.st_ino, "offset": st.st_size, "mark": _mark(f, st.st_size)}
    offset = int(pos.get("offset", 0))
    if (
        pos.get("inode") != st.st_ino
        or st.st_size < offset
        or _mark(f, offset) != pos.get("mark", _mark(f, offset))
    ):
        offset = 0
    f.seek(offset)
    data = f.read()
    end = data.rfind(b"\n") + 1  # a half-written last line waits for the next pass
    new = offset + end
    lines = data[:end].decode("utf-8", errors="replace").splitlines()
    return lines, {"inode": st.st_ino, "offset": new, "mark": _mark(f, new)}


def queue_events(
    lines: list[str], kinds: frozenset[str] = QUEUE_KINDS
) -> list[tuple[str, str, str]]:
    """Rule (b). (kind, subject, detail) for each line whose first word is in ``kinds``."""
    out = []
    for ln in lines:
        m = QUEUE_RE.match(ln)
        if not m or m.group(1) not in kinds:
            continue
        rest = m.group(2).split(None, 1)
        subject = rest[0] if rest else "-"
        detail = rest[1] if len(rest) > 1 else ""
        out.append((m.group(1).lower(), scrub(subject), scrub(detail)))
    return out


def merger_note(lines: list[str]) -> bool:
    """True when a new line is a NOTE line whose second field starts with lane-merge."""
    return any(NOTE_RE.match(ln) for ln in lines)


def merger_condition(
    agents: list[dict], state: dict, now: float, noted: bool
) -> dict[str, tuple[str, str, str]]:
    """Rule (c). Updates ``state['merge_seen']``. key -> (signature, subject, detail)."""
    seen = float(state.get("merge_seen") or 0) or now  # the first pass starts the clock
    if noted:
        seen = now
    # The newest session of any lane whose name starts with lane-merge (lane-merge-1126).
    merges = [a for n, a in newest_per_lane(agents).items() if n.startswith(MERGE_LANE)]
    a = max(merges, key=lambda x: x.get("startedAt", 0), default=None)
    path = transcript(a["sessionId"]) if a and a.get("sessionId") else None
    if path is not None:
        seen = max(seen, path.stat().st_mtime)
    state["merge_seen"] = seen
    silent_min = int((now - seen) // 60)
    if silent_min < MERGER_MIN:
        return {}
    detail = f"no lane-merge NOTE line and no lane-merge transcript write for {silent_min} min"
    return {"merger-silent": (str(int(seen)), MERGE_LANE, detail)}


def _load_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)  # atomic: a reader never sees half a file


def read_agents() -> list[dict] | None:
    """``claude agents --json``, or None when it fails."""
    try:
        r = subprocess.run(
            ["claude", "agents", "--json"], capture_output=True, text=True, timeout=60
        )
        if r.returncode:
            return None
        data = json.loads(r.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def run_pass(
    agents: list[dict] | None = None,
    now: float | None = None,
    child: Callable[[int | None], bool] = has_shell_child,
) -> list[tuple[str, str, str]]:
    """One pass. Returns the events it wrote: (kind, subject, detail)."""
    now = dt.datetime.now(dt.UTC).timestamp() if now is None else now
    if agents is None:
        agents = read_agents()
    state = _load_json(state_path()) or {}
    active = state.get("active") if isinstance(state.get("active"), dict) else {}
    lines, pos = read_queue(queue_log(), _load_json(pos_path()))
    bot_lines, bot_pos = read_queue(bot_log(), _load_json(bot_pos_path()))
    events = queue_events(lines, QUEUE_KINDS) + queue_events(bot_lines, BOT_KINDS)
    if agents is None:
        # No list of sessions: keep the conditions of (a) and (c) as they are.
        print("orch-watch: claude agents --json failed, rules a and c skipped", file=sys.stderr)
        conds = {k: (v, "", "") for k, v in active.items()}
        new_keys: set[str] = set()
        if merger_note(lines):
            state["merge_seen"] = now
    else:
        conds = idle_conditions(agents, listed_lanes(), resting(), now, child)
        conds |= merger_condition(agents, state, now, merger_note(lines))
        new_keys = {k for k, (sig, _, _) in conds.items() if active.get(k) != sig}
    for key in sorted(new_keys):
        kind = "idle" if key.startswith("idle:") else key
        _, subject, detail = conds[key]
        events.append((kind, scrub(subject), scrub(detail)))
    if events:
        stamp = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%H:%M")
        events_log().parent.mkdir(parents=True, exist_ok=True)
        with open(events_log(), "a", encoding="utf-8") as f:
            for kind, subject, detail in events:
                f.write(f"EVENT\t{kind}\t{stamp} UTC\t{subject}\t{detail}\n")
    state["active"] = {k: sig for k, (sig, _, _) in conds.items()}
    _save_json(state_path(), state)
    _save_json(pos_path(), pos)
    _save_json(bot_pos_path(), bot_pos)
    return events


def main() -> int:
    try:
        events = run_pass()
    except Exception as exc:  # a timer job reports and exits
        print(f"orch-watch: error {type(exc).__name__}", file=sys.stderr)
        return 1
    kinds = ",".join(sorted({e[0] for e in events})) or "none"
    print(f"orch-watch: {len(events)} new event(s): {kinds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
