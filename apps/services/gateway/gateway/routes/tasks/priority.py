"""Tasks · the prioritization engine, as the gateway imports it.

⚠️ **A re-export, not a copy.** The matrix lives in ``acb_common.priority``
since H-173 (2026-09-28), because the chat skills need it too and a skill
cannot import the gateway. Every gateway caller keeps this import path. Add a
rule to ``acb_common/priority.py``, never here.

The client's twin is ``lib/priority.ts``, and ``test_priority_shared.py``
holds the two equal (R7).
"""

from __future__ import annotations

from acb_common.priority import (
    CELL_FLAGS,
    CELL_META,
    CELLS_IN_ORDER,
    DEFAULT_URGENT_WINDOW_HOURS,
    IMPORTANT_AT,
    PriorityInputs,
    action_mode,
    cell_for_inputs,
    cell_label,
    importance_for,
    important_from_importance,
    is_urgent,
    priority_cell,
    priority_inputs,
    priority_rank,
    task_cell,
    task_level_label,
)

__all__ = [
    "CELLS_IN_ORDER",
    "CELL_FLAGS",
    "CELL_META",
    "DEFAULT_URGENT_WINDOW_HOURS",
    "IMPORTANT_AT",
    "PriorityInputs",
    "action_mode",
    "cell_for_inputs",
    "cell_label",
    "importance_for",
    "important_from_importance",
    "is_urgent",
    "priority_cell",
    "priority_inputs",
    "priority_rank",
    "task_cell",
    "task_level_label",
]
