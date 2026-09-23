"""D76 — the shared Projects priority seeds ``important``, on the SERVER too.

Owner decision 2026-09-23. While a member has not stated ``important``, a
shared ``pm_tasks.importance`` of 2 (High) or 3 (Highest) counts as important.
The client applies it in ``priorityInputs`` (``tasks/lib/priority.ts``).

🔴 **Why this file exists.** The first build of D76 seeded the client only.
The day planner ranks on the server, from ``_candidate_brief``, and read
``bool(m.important)``. So a High task the member had not judged was
"Important" in the Calendar list and not important to "Plan my day" — two
answers about one task on one page. Caught in review on 2026-09-23.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from gateway.routes.projects import planning
from gateway.routes.tasks import calendar
from gateway.routes.tasks.priority import (
    ORG_PRIORITY_SEED,
    priority_cell,
    seeded_important,
)

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "workbench/control_plane/src/app/tasks/lib/priority.ts"


def test_the_seed_threshold_matches_the_client() -> None:
    """R7: one number, two languages. If they drift, the list and the planner
    disagree again — which is the defect this file was written for."""
    src = CLIENT.read_text(encoding="utf-8")
    match = re.search(r"export const ORG_PRIORITY_SEED = (\d+);", src)
    assert match, "ORG_PRIORITY_SEED is gone from priority.ts"
    assert int(match.group(1)) == ORG_PRIORITY_SEED == 2


@pytest.mark.parametrize(
    ("important", "org_priority", "expected"),
    [
        (None, 3, True),    # Highest, never judged -> seeded
        (None, 2, True),    # High, never judged -> seeded
        (None, 1, False),   # Normal never seeds
        (None, 0, False),
        (None, None, False),
        (True, None, True),   # the member's yes
        (False, 3, False),    # the member's NO beats Highest
        (True, 0, True),      # and their yes beats Low
    ],
)
def test_seeded_important(important, org_priority, expected) -> None:
    assert seeded_important(important, org_priority) is expected


def test_a_plain_bool_caller_gets_what_it_got_before() -> None:
    """The mirror's old callers pass `important` as a bool and no priority."""
    far = datetime.now(tz=UTC) + timedelta(days=30)
    assert priority_cell(important=True, leveraged=False, due_at=far) == "important"
    assert priority_cell(important=False, leveraged=False, due_at=far) == "low-priority"


def test_the_seed_moves_a_high_task_out_of_low_priority() -> None:
    far = datetime.now(tz=UTC) + timedelta(days=30)
    assert priority_cell(
        important=None, leveraged=False, due_at=far, org_priority=2,
    ) == "important"


def test_the_planner_brief_applies_the_seed() -> None:
    """The row `_candidate_brief` reads is `_pm_row`'s namespace."""
    now = datetime.now(tz=UTC)

    def row(**kw):
        base = dict(
            id="t", title="t", time_estimate_mins=30, energy=None,
            important=None, leveraged=None, deep_work=None,
            due_at=None, context=None, org_priority=None,
        )
        return SimpleNamespace(**{**base, **kw})

    assert calendar._candidate_brief(row(org_priority=2), now)["important"] is True
    assert calendar._candidate_brief(row(org_priority=1), now)["important"] is False
    assert calendar._candidate_brief(
        row(important=False, org_priority=3), now)["important"] is False
    # A legacy row with no `org_priority` attribute at all still works.
    legacy = SimpleNamespace(
        id="t", title="t", time_estimate_mins=30, energy=None, important=True,
        leveraged=False, deep_work=False, due_at=None, context=None,
    )
    assert calendar._candidate_brief(legacy, now)["important"] is True


def test_the_planner_select_carries_the_shared_priority() -> None:
    """The seed is only as good as the column reaching it."""
    assert "t.importance" in planning._PM_SELECT
    assert "AS org_priority" in planning._PM_SELECT
    assert "org_priority" in planning._PM_PASSTHROUGH


def test_the_seeded_task_outranks_an_unjudged_normal_one_in_the_fallback() -> None:
    """`_rank_fallback` is what orders the day when the LLM is off."""
    now = datetime.now(tz=UTC)

    def brief(tid, **kw):
        base = dict(
            id=tid, title=tid, time_estimate_mins=30, energy=None,
            important=None, leveraged=None, deep_work=None, due_at=None,
            context=None, org_priority=None,
        )
        return calendar._candidate_brief(SimpleNamespace(**{**base, **kw}), now)

    ranked = calendar._rank_fallback(
        [brief("normal", org_priority=1), brief("high", org_priority=2)]
    )
    assert [c["id"] for c in ranked] == ["high", "normal"]
