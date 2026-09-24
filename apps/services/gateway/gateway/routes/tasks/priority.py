"""Tasks · prioritization engine (server mirror of lib/priority.ts).

The single source of truth on the SERVER for how a task's three inputs
(important x urgent x leveraged) become a priority CELL, an action MODE and a
rank. Kept byte-for-byte behaviourally identical to the frontend
``lib/priority.ts`` so a task's cell is the same whether the client or the
gateway computes it (used for server-side Priority ordering + the AI's reasoning
about what to surface).

Design (agreed with the user):
  * urgent is DERIVED from due_at (overdue or due within a window), never
    stored — so it can't go stale.
  * important = downside (something stalls if skipped); leveraged = upside
    (asymmetric 100x). Separate axes on purpose.
  * D78: important and leveraged are ONE shared answer on the task, not per
    member. Important is `pm_tasks.importance >= 2` and leveraged is
    `pm_tasks.leveraged`.
  * The 8 cells are a projection of the three booleans (the user's Notion
    formula, verbatim). Never persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_URGENT_WINDOW_HOURS = 48

#: The shared priority at which a task counts as Important (D78, 2026-09-24).
#: Important is `pm_tasks.importance >= 2`: 2 is High and 3 is Highest. The
#: client's twin is `IMPORTANT_AT` in `lib/priority.ts`, and
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


def is_urgent(
    due_at: datetime | None,
    window_hours: int = DEFAULT_URGENT_WINDOW_HOURS,
    now: datetime | None = None,
) -> bool:
    """Overdue OR due within ``window_hours``. No due date → never urgent."""
    if due_at is None:
        return False
    now = now or datetime.now(tz=UTC)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    delta_hours = (due_at - now).total_seconds() / 3600.0
    return delta_hours <= window_hours  # overdue (<=0) included


# ── The 7 priority levels ────────────────────────────────────────────────────

# cell key → (order 1..7, emoji, label, mode). Order 1 = act first; the sequence
# INTERLEAVES leveraged and non-leveraged levels by true priority. LABELS carry
# only the priority CHARACTER (no action-words) — the action to take
# (delegate/schedule/eliminate) is the `mode`, surfaced as a competing card
# badge, never in the label. The two "not important to you" cases (urgent-only
# and neither) fold into one Low Priority level → 7 levels, not 8. Byte-parity
# with lib/priority.ts CELL_META.
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
    due_at: datetime | None,
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
    due_at: datetime | None,
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
