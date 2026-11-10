"""Each decision number in `work_plan.md` §3 is defined once.

Spec: `project-docs/work_plan.md` §3 · Metorite task LD-3.

On 2026-10-25 a docs PR wrote D113 while it waited in CI, and #1318 merged its
own D113 first. Both merged. Nothing failed, because no fence read D-numbers,
and a decision cited by number then meant two things.

A definition is a `### D<n>` heading or a `- **D<n> —` bullet (the older
form). A sub-item such as `- **D75.6.**` is not a definition.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

WORK_PLAN = Path(__file__).resolve().parents[2] / "project-docs" / "work_plan.md"
DEFINITION = re.compile(r"^(?:### |- \*\*)D(\d+[a-z]?)(?![.\d])", re.MULTILINE)


def _ids() -> list[str]:
    return DEFINITION.findall(WORK_PLAN.read_text(encoding="utf-8"))


def test_the_fence_still_sees_decisions():
    """A pattern that matches nothing passes forever."""
    assert len(_ids()) >= 50, "the decision heading shape changed. This fence went blind."


def test_each_decision_number_is_defined_once():
    dupes = sorted(n for n, c in Counter(_ids()).items() if c > 1)
    assert not dupes, (
        f"decision number(s) defined twice: {', '.join('D' + d for d in dupes)}. "
        "The one that merged second takes the next free number. Keep main's own "
        "references to the taken number."
    )
