"""The repair of a saved skill (WS-TOF TOF-3a, F12).

Spec: ``project-docs/specs/ai_studio_rows/F12.md``. F12c adds
``check_contract``, the repeat check and the call of ``run_suite`` here.

**F12b holds only the repair key.** :func:`body_key` is the key of the body
that ran. Each writer of the store of skill runs (``gateway/studio/skill_runs.py``)
stores it in ``body_key``, so the repeat check of F12c can find a body that
failed before. It is a pure function, and it reads no flag and no database.
Fence: ``tests/unit/test_skill_repair.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gateway.studio.skill_runs import fold_space
from gateway.studio.skills_store import content_hash

__all__ = ["body_key"]


def _folded(value: Any) -> Any:
    """*value* with each string folded (``skill_runs.fold_space``), in each
    list and each mapping. A key of a mapping stays as it is."""
    if isinstance(value, str):
        return fold_space(value)
    if isinstance(value, Mapping):
        return {k: _folded(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_folded(v) for v in value]
    return value


def _sort_key(item: Any) -> tuple[int, str]:
    """A string sorts by its text. Any other item sorts after, by its type."""
    return (0, item) if isinstance(item, str) else (1, type(item).__name__)


def body_key(body: Mapping[str, Any]) -> str:
    """The repair key of *body*, in 64 lower-case hex digits (F12.md "The
    repair key").

    Each run of white space in each string becomes one space, and each
    string loses the white space at each end (``skill_runs.fold_space``).
    The list ``actions`` is sorted. Then the key is
    ``skills_store.content_hash`` of the result. So two bodies that differ
    only in white space, or in the order of their actions, have one key.
    Raises ``TypeError`` or ``ValueError`` for a body that is no JSON
    mapping, as ``content_hash`` does."""
    if not isinstance(body, Mapping):
        raise TypeError("A body is a mapping.")
    folded = _folded(body)
    actions = folded.get("actions")
    if isinstance(actions, list):
        folded["actions"] = sorted(actions, key=_sort_key)
    return content_hash(folded)
