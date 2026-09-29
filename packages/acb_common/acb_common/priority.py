"""The priority matrix — the ONE server-side source (D78, 2026-09-24).

Projects and My Tasks show one priority system: the matrix of three inputs,
Important x Urgent x Leveraged, which gives one of seven levels. The client's
twin is ``workbench/control_plane/src/app/tasks/lib/priority.ts``, and
``tests/unit/test_priority_shared.py`` holds the two equal (R7).

It lives in ``acb_common`` because two kinds of caller need it and neither can
import the other. The gateway (``gateway/routes/tasks/priority.py`` re-exports
this module) and the chat skills (``skill_projects``, ``skill_my_tasks``) both
read it. A second copy in a skill would be a second priority vocabulary, which
is the defect D78 removes (CLAUDE.md §4, H-173).

Where each input lives:

* Important is ``pm_tasks.importance >= IMPORTANT_AT``. The column keeps its
  0-3 values. NULL means that nobody has judged the task yet.
* Leveraged is ``pm_tasks.leveraged`` (migration 218). NULL reads as false.
* Urgent derives from ``due_at``. Nobody sets it and nothing stores it.

Design (agreed with the user):
  * important = downside (something stalls if skipped); leveraged = upside
    (asymmetric 100x). Separate axes on purpose.
  * The 8 cells are a projection of the three booleans (the user's Notion
    formula, verbatim). Never persisted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

DEFAULT_URGENT_WINDOW_HOURS = 48

#: The shared priority at which a task counts as Important (D78, 2026-09-24).
#: The client's twin is `IMPORTANT_AT` in `lib/priority.ts`, and
#: `test_priority_shared.py` holds the two equal.
IMPORTANT_AT = 2


def important_from_importance(importance: int | None) -> bool | None:
    """The matrix's Important input, read from the task's shared priority.

    ``None`` stays ``None``: nobody has judged the task yet. The matrix reads
    that as not important. Any value is important when it is 2 or more.
    """
    if importance is None:
        return None
    return int(importance) >= IMPORTANT_AT


def importance_for(important: bool) -> int:
    """The matrix's Important → the value to store in ``pm_tasks.importance``.

    0 means "judged, and not important", which is not the same as NULL. The
    client's twin is ``importanceFor`` in ``lib/priority.ts``.
    """
    return IMPORTANT_AT if important else 0


def importance_write(important: bool, current: Any) -> int | None:
    """The ``importance`` value an Important answer writes, or None for no write.

    True raises the task to ``IMPORTANT_AT`` and leaves a higher stored value
    (a 3 from before D78) as it is. False lowers it to 0. A task that already
    holds the answer is not written. An unjudged task (NULL) is always written.
    """
    level = int(current) if current is not None else None
    if important:
        return None if level is not None and level >= IMPORTANT_AT else importance_for(True)
    return None if level == 0 else importance_for(False)


def is_urgent(
    due_at: datetime | date | str | None,
    window_hours: int = DEFAULT_URGENT_WINDOW_HOURS,
    now: datetime | None = None,
) -> bool:
    """Overdue OR due within ``window_hours``. No due date → never urgent.

    ``due_at`` may be the ISO text the gateway sends. A text that does not
    parse reads as no due date, the way the client reads an invalid date.
    """
    due = _as_datetime(due_at)
    if due is None:
        return False
    now = now or datetime.now(tz=UTC)
    delta_hours = (due - now).total_seconds() / 3600.0
    return delta_hours <= window_hours  # overdue (<=0) included


def _as_datetime(value: datetime | date | str | None) -> datetime | None:
    """A due date as an aware datetime. A bare date is midnight UTC, which is
    what the browser's ``new Date("2026-10-01")`` gives the client."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        out = value
    elif isinstance(value, date):
        out = datetime(value.year, value.month, value.day)
    else:
        try:
            out = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return out if out.tzinfo is not None else out.replace(tzinfo=UTC)


# ── The 7 priority levels ────────────────────────────────────────────────────

# cell key → (order 1..7, emoji, label, mode). Order 1 = act first; the sequence
# INTERLEAVES leveraged and non-leveraged levels by true priority. LABELS carry
# only the priority CHARACTER (no action-words) — the action to take
# (delegate/schedule/eliminate) is the `mode`, surfaced as a competing card
# badge, never in the label. The two "not important to you" cases (urgent-only
# and neither) fold into one Low Priority level → 7 levels, not 8. Parity with
# lib/priority.ts CELL_META is held by test_priority_shared.py.
CELL_META: dict[str, tuple[int, str, str, str]] = {
    "critical": (1, "🔥", "Critical", "do"),
    "urgent": (2, "🚨", "Urgent", "delegate"),
    "high-leverage": (3, "📈", "High-Leverage", "do"),
    "important": (4, "❗", "Important", "schedule"),
    "quick-leverage": (5, "📤", "Quick Leverage Win", "do"),
    "speculative-bet": (6, "🧪", "Speculative Bet", "do"),
    "low-priority": (7, "🗑", "Low Priority", "drop"),
}

CELLS_IN_ORDER: list[str] = sorted(CELL_META, key=lambda c: CELL_META[c][0])

#: The two STATED inputs each level implies, as ``(important, leveraged)``.
#: Urgent is not here, because the due date sets it. The client's twin is
#: ``flagsForCell`` in ``projects/lib/matrix.ts``.
CELL_FLAGS: dict[str, tuple[bool, bool]] = {
    "critical": (True, True),
    "urgent": (True, False),
    "high-leverage": (True, True),
    "important": (True, False),
    "quick-leverage": (False, True),
    "speculative-bet": (False, True),
    "low-priority": (False, False),
}


@dataclass(frozen=True)
class PriorityInputs:
    important: bool
    urgent: bool
    leveraged: bool


def cell_for_inputs(inp: PriorityInputs) -> str:
    """The user's Notion formula, verbatim, as a pure function of the 3 bools."""
    if inp.leveraged:
        if inp.important and inp.urgent:
            return "critical"            # order 1
        if inp.important and not inp.urgent:
            return "high-leverage"       # order 3
        if not inp.important and inp.urgent:
            return "quick-leverage"      # order 5
        return "speculative-bet"         # order 6
    if inp.important and inp.urgent:
        return "urgent"                  # order 2
    if inp.important and not inp.urgent:
        return "important"               # order 4
    # Not important to you — urgent-only OR neither → one Low Priority level.
    return "low-priority"                # order 7


def priority_inputs(
    *,
    important: bool | None,
    leveraged: bool,
    due_at: datetime | date | str | None,
    window_hours: int = DEFAULT_URGENT_WINDOW_HOURS,
    now: datetime | None = None,
) -> PriorityInputs:
    # D78: Important and Leveraged are shared facts on the task. The caller
    # reads them from `pm_tasks` (see `important_from_importance`).
    return PriorityInputs(
        important=bool(important),
        leveraged=bool(leveraged),
        urgent=is_urgent(due_at, window_hours, now),
    )


def priority_cell(
    *,
    important: bool | None,
    leveraged: bool,
    due_at: datetime | date | str | None,
    window_hours: int = DEFAULT_URGENT_WINDOW_HOURS,
    now: datetime | None = None,
) -> str:
    return cell_for_inputs(priority_inputs(
        important=important, leveraged=leveraged, due_at=due_at,
        window_hours=window_hours, now=now))


def priority_rank(**kwargs) -> int:
    """The matrix rank (1 = highest). Lower sorts first."""
    return CELL_META[priority_cell(**kwargs)][0]


def action_mode(**kwargs) -> str:
    """do / delegate / schedule / drop for a task."""
    return CELL_META[priority_cell(**kwargs)][3]


# ── A task row → its level, and a spoken level → its flags ───────────────────


def cell_label(cell: str) -> str:
    """The level's label, exactly as the UI draws it (``cellLabel``)."""
    return CELL_META[cell][2]


def task_cell(task: Mapping[str, Any], now: datetime | None = None) -> str:
    """A ``pm_tasks`` row (as the gateway sends it) → its level.

    The twin of ``taskCell`` in ``projects/lib/matrix.ts``. It reads
    ``importance``, ``leveraged`` and ``due_at``.
    """
    return priority_cell(
        important=important_from_importance(task.get("importance")),
        leveraged=bool(task.get("leveraged")),
        due_at=task.get("due_at"),
        now=now,
    )


def task_level_label(task: Mapping[str, Any], now: datetime | None = None) -> str:
    """The label the UI draws for this task's level."""
    return cell_label(task_cell(task, now))


#: A spoken level → its cell key. The label and the key both match, with no
#: regard to case, spaces or dashes: "high leverage", "High-Leverage" and
#: "HIGH_LEVERAGE" are one level.
def _norm(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


_LEVEL_BY_NAME: dict[str, str] = {
    **{_norm(cell): cell for cell in CELL_META},
    **{_norm(meta[2]): cell for cell, meta in CELL_META.items()},
}


def cell_for_name(name: str) -> str | None:
    """A level NAME (case-insensitive) → its cell key, or None."""
    return _LEVEL_BY_NAME.get(_norm(name)) if str(name or "").strip() else None


def level_names() -> str:
    """The seven labels in rank order, for a refusal or a tool description."""
    return ", ".join(CELL_META[c][2] for c in CELLS_IN_ORDER)


#: The words a flag reads as. ⚠️ "1" and "0" are NOT here, on purpose
#: (review of PR #509). The retired ``importance`` scale read 1 as NOT
#: important, and a flag would read "1" as true. One string must not mean two
#: things, so a digit is refused and the model is told to say true or false.
_TRUE = frozenset({"true", "yes", "y", "on"})
_FALSE = frozenset({"false", "no", "n", "off"})


def flag(value: Any) -> bool | str | None:
    """"true"/"false" → a bool. Empty or None → None (not passed). Anything
    else → a refusal string, because a flag the tool cannot read is not a
    guess. A number, "1" and "0" included, is refused (see ``_TRUE``)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, int | float):
        return f"{value!r} is a number. Pass true or false."
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    if raw.lstrip("-").replace(".", "", 1).isdigit():
        return f"{value!r} is a number. Pass true or false."
    return f"{value!r} is not true or false."
