"""How the Projects chat tools read and write a task's priority (D78, H-173).

A task has ONE priority: a level of the matrix, Important x Urgent x
Leveraged. The member states two of the inputs, Important and Leveraged, and
the due date sets Urgent. This module adapts the tools' arguments to those
two stored fields and prints the level the app draws.

⚠️ **It computes nothing of its own.** Every rule, label and threshold comes
from ``acb_common.priority``, the one server-side source, which
``test_priority_shared.py`` holds equal to the client's ``lib/priority.ts``.
A second formula or a second label list here would be a second priority
vocabulary (CLAUDE.md §4).

What a tool accepts:

* ``priority`` — a level NAME, with no regard to case ("critical",
  "high leverage", "speculative bet"). It sets the two flags that level
  implies (``CELL_FLAGS``). Urgent is the due date's, so the answer names
  the level the task reads NOW.
* ``important`` and ``leveraged`` — "true" or "false", the stored fields.
* ``importance`` — ⚠️ DEPRECATED, the retired 0-4 number, for one release.
  It maps EXPLICITLY through ``LEGACY_IMPORTANCE``: 2 or more is Important,
  0 and 1 are not. It never sets Leveraged. The answer says how it was read.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acb_common.priority import (
    CELL_FLAGS,
    CELLS_IN_ORDER,
    DEFAULT_URGENT_WINDOW_HOURS,
    PriorityInputs,
    cell_for_inputs,
    cell_for_name,
    cell_label,
    importance_for,
    importance_write,
    important_from_importance,
    important_from_legacy,
    level_names,
    task_cell,
)


def _labels(cells: list[str]) -> str:
    """The labels of ``cells``, in rank order, as "A, B or C"."""
    names = [cell_label(c) for c in CELLS_IN_ORDER if c in cells]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


#: The levels that need a close due date. Derived from the matrix, so a
#: description never spells a level by hand.
_URGENT_CELLS = sorted(
    {
        cell_for_inputs(PriorityInputs(important=i, urgent=True, leveraged=lev))
        for i, lev in CELL_FLAGS.values()
    }
    - {"low-priority"}
)
#: The levels a capture can take: the intake route stores no Leveraged.
_UNLEVERAGED_CELLS = [c for c, (_imp, lev) in CELL_FLAGS.items() if not lev]

#: The words a tool description uses for the priority arguments.
PRIORITY_ARGS_DOC = (
    "priority is a level name: " + level_names() + ". It sets that level's "
    "Important and Leveraged flags. The due date sets Urgent, so the task "
    f"reads {_labels(_URGENT_CELLS)} only while it is due within "
    f"{DEFAULT_URGENT_WINDOW_HOURS} hours. Or set important and leveraged "
    "directly (true or false). importance (a number) is deprecated: 2 or more "
    "reads as Important."
)

#: `capture_intake`'s version: it takes Important only (H-196).
INTAKE_PRIORITY_DOC = (
    f"priority is a level name that is not leveraged: {_labels(_UNLEVERAGED_CELLS)}. "
    "Or set important directly (true or false). importance (a number) is "
    "deprecated: 2 or more reads as Important."
)


def takes_priority(fn, text: str = PRIORITY_ARGS_DOC):
    """Append the priority arguments' text to a tool's description.

    The model reads the docstring as the tool's description. Built from the
    one source, the level names in it cannot drift from the app's labels.
    """
    fn.__doc__ = f"{(fn.__doc__ or '').rstrip()} {text}"
    return fn


def takes_important_only(fn):
    """``takes_priority`` for a tool that cannot store Leveraged."""
    return takes_priority(fn, INTAKE_PRIORITY_DOC)


#: `clear` words for the priority → the stored fields each one empties.
CLEAR_PRIORITY: dict[str, dict[str, Any]] = {
    "importance": {"importance": None},
    "important": {"importance": None},
    "priority": {"importance": None, "leveraged": False},
    "leveraged": {"leveraged": False},
}

_TRUE = frozenset({"true", "yes", "y", "1", "on"})
_FALSE = frozenset({"false", "no", "n", "0", "off"})


def flag(value: Any) -> bool | str | None:
    """"true"/"false" → a bool. Empty → None (not passed). Anything else → a
    refusal string, because a flag the tool cannot read is not a guess."""
    if isinstance(value, bool):
        return value
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return f"{value!r} is not true or false."


def legacy_number(value: Any) -> int | None:
    """The deprecated ``importance`` argument: -1 (the default), empty or
    None means "not passed". 0 is a real value."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return -2  # passed, and not a number: refused by the caller
    return None if parsed == -1 else parsed


def priority_fields(
    *,
    priority: str = "",
    important: Any = "",
    leveraged: Any = "",
    importance: Any = -1,
    current: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]] | str:
    """The ``pm_tasks`` fields the priority arguments write, and the notes the
    answer must carry, or a refusal.

    ``current`` is the task's row for an update. Only what CHANGES is
    written, and an Important task keeps a stored 3. With no ``current`` (a
    new task, or a bulk edit over a mixed selection) each passed flag is
    written as it stands.
    """
    notes: list[str] = []
    imp = flag(important)
    lev = flag(leveraged)
    for name, parsed in (("important", imp), ("leveraged", lev)):
        if isinstance(parsed, str):
            return f"{name}: {parsed}"
    number = legacy_number(importance)
    level = str(priority or "").strip()

    if level:
        if imp is not None or lev is not None or number is not None:
            return "Pass priority, or important and leveraged. Not both."
        cell = cell_for_name(level)
        if cell is None:
            return f"priority is one of: {level_names()}. Not {level!r}."
        imp, lev = CELL_FLAGS[cell]
    elif number is not None:
        if imp is not None:
            return "importance is deprecated. Pass important, not both."
        read = important_from_legacy(number)
        if read is None:
            return (
                "importance is deprecated, and takes 0 to 4. Pass priority "
                f"({level_names()}), or important true or false."
            )
        imp = read
        notes.append(
            f"importance {number} is deprecated. It was read as "
            + ("Important" if read else "not Important")
            + ". Next time pass priority, or important true or false."
        )

    fields: dict[str, Any] = {}
    if imp is not None:
        if current is None:
            fields["importance"] = importance_for(bool(imp))
        else:
            write = importance_write(bool(imp), current.get("importance"))
            if write is not None:
                fields["importance"] = write
    if lev is not None and (current is None or bool(current.get("leveraged")) != lev):
        fields["leveraged"] = bool(lev)
    return fields, notes


def level_label(task: Mapping[str, Any]) -> str:
    """The label the app draws for this task's level, now."""
    return cell_label(task_cell(task))


def level_fact(task: Mapping[str, Any]) -> str:
    """``priority Critical`` for a list line, or "" for Low Priority.

    The Projects card draws no chip for Low Priority, so a list line prints
    none either. A detail read prints every level (``level_detail``).
    """
    cell = task_cell(task)
    return "" if cell == "low-priority" else f"priority {cell_label(cell)}"


def level_detail(task: Mapping[str, Any]) -> str:
    """The detail read's line: the level, and the two stated flags."""
    judged = important_from_importance(task.get("importance"))
    important = "not judged" if judged is None else ("yes" if judged else "no")
    leveraged = "yes" if task.get("leveraged") else "no"
    return f"{level_label(task)} (important: {important}, leveraged: {leveraged})"


def card_view(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The priority fields as a member reads them on a card.

    The card shows the flags and not the stored number: ``importance: 2``
    is the retired scale's language.
    """
    out: dict[str, Any] = {}
    if "importance" in fields:
        out["important"] = _judged(fields["importance"])
    if "leveraged" in fields:
        out["leveraged"] = "yes" if fields["leveraged"] else "no"
    return out


def _judged(importance: Any) -> str:
    """A stored ``importance`` as the Important answer: yes, no or not judged."""
    try:
        judged = important_from_importance(
            None if importance in (None, "") else int(str(importance))
        )
    except (TypeError, ValueError):
        return str(importance)
    return "not judged" if judged is None else ("yes" if judged else "no")


def change_view(field: str, before: Any, after: Any) -> tuple[str, Any, Any]:
    """A timeline field change as a member reads it.

    A change of ``importance`` prints as ``important no → yes``, never as the
    retired 0-3 number. Every other field passes through unchanged.
    """
    if field == "importance":
        return "important", _judged(before), _judged(after)
    return field, before, after
