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

⚠️ **The retired 0-4 ``importance`` number is GONE from every tool (H-196).**
The model no longer sees it in a schema. A tool that took it keeps a hidden
``importance: Removed = None`` parameter and hands it to ``priority_fields``,
which answers a call that still sends it with the replacement arguments. It is not dropped
without a word: the agent framework drops an unknown argument silently, so a
parameter that simply vanished would let an old call write a task with no
priority and say nothing.
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
    level_names,
    task_cell,
)
from pydantic.json_schema import SkipJsonSchema

#: The annotation of a REMOVED tool argument. ``SkipJsonSchema`` keeps it out
#: of the JSON schema the model reads, and validation still passes a sent
#: value through, so the tool can refuse it by name.
Removed = SkipJsonSchema[Any]


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
#: The words a tool description uses for the priority arguments.
PRIORITY_ARGS_DOC = (
    "priority is a level name: " + level_names() + ". It sets that level's "
    "Important and Leveraged flags. The due date sets Urgent, so the task "
    f"reads {_labels(_URGENT_CELLS)} only while it is due within "
    f"{DEFAULT_URGENT_WINDOW_HOURS} hours. Or set important and leveraged "
    "directly (true or false)."
)

#: The answer to a call that still sends the removed ``importance`` number.
IMPORTANCE_REMOVED = (
    "importance (a number) was removed, and nothing was written. Pass "
    f"priority, a level name ({level_names()}), or important and leveraged, "
    "each true or false."
)


def takes_priority(fn, text: str = PRIORITY_ARGS_DOC):
    """Append the priority arguments' text to a tool's description.

    The model reads the docstring as the tool's description. Built from the
    one source, the level names in it cannot drift from the app's labels.
    """
    fn.__doc__ = f"{(fn.__doc__ or '').rstrip()} {text}"
    return fn


#: `clear` words for the priority → the stored fields each one empties.
CLEAR_PRIORITY: dict[str, dict[str, Any]] = {
    "importance": {"importance": None},
    "important": {"importance": None},
    "priority": {"importance": None, "leveraged": False},
    "leveraged": {"leveraged": False},
}

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


def priority_fields(
    *,
    priority: str = "",
    important: Any = "",
    leveraged: Any = "",
    importance: Any = None,
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any] | str:
    """The ``pm_tasks`` fields the priority arguments write, or a refusal.

    ``current`` is the task's row for an update. Only what CHANGES is
    written, and an Important task keeps a stored 3. With no ``current`` (a
    new task, or a bulk edit over a mixed selection) each passed flag is
    written as it stands. A sent ``importance`` (H-196: removed) is refused
    with ``IMPORTANCE_REMOVED``, before anything else is read.

    ⚠️ **So a bulk ``important=true`` writes 2 over a stored 3** (review of
    PR #509). Both read as Important, so the level does not change. The app's
    bulk bar does the same (``BULK_FLAG_OPTIONS``), and the chat keeps that
    rule rather than read every task in the selection first.
    ``test_chat_priority_levels.py`` pins it.
    """
    if importance is not None:
        return IMPORTANCE_REMOVED
    imp = flag(important)
    lev = flag(leveraged)
    for name, parsed in (("important", imp), ("leveraged", lev)):
        if isinstance(parsed, str):
            return f"{name}: {parsed}"
    level = str(priority or "").strip()

    if level:
        if imp is not None or lev is not None:
            return "Pass priority, or important and leveraged. Not both."
        cell = cell_for_name(level)
        if cell is None:
            return f"priority is one of: {level_names()}. Not {level!r}."
        imp, lev = CELL_FLAGS[cell]

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
    return fields


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


def level_note(priority: str, task: Mapping[str, Any]) -> list[str]:
    """The answer's note when the level asked for is not the level the task
    reads, or an empty list (review of PR #509).

    ``priority`` sets Important and Leveraged only. The due date sets Urgent,
    so ``priority=Critical`` on a task with no due date reads High-Leverage.
    Without this note the model reports the level it asked for.
    """
    asked = cell_for_name(str(priority or ""))
    if asked is None:
        return []
    reads = task_cell(task)
    if reads == asked:
        return []
    if asked in _URGENT_CELLS:
        why = (
            "has no due date"
            if not task.get("due_at")
            else f"is not due within {DEFAULT_URGENT_WINDOW_HOURS} hours"
        )
    else:
        why = f"is due within {DEFAULT_URGENT_WINDOW_HOURS} hours, or overdue"
    return [
        f"Note: you asked for {cell_label(asked)}, and the task reads "
        f"{cell_label(reads)}. The due date sets Urgent, and this task {why}."
    ]


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
