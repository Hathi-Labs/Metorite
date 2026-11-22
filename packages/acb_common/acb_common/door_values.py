"""The AD-10 rule that wraps each value of an answer for an agent.

Spec ``project-docs/specs/external_agent_access.md`` §9.4 and §19 (AD-10).
:func:`door_value` maps one value of an app door answer. Each string becomes
``{"untrusted_text": ...}``, at any depth. A string under a key that the row
names as plain stays plain only in a plain shape (:func:`plain_shape`). A key
whose name holds a word of :data:`SECRET_WORDS` leaves no answer.

The rule lives here, once. ``agent_edge.tools`` imports each name, and it
stays the one edge module that maps an answer (EA3-9). ``acb_skills`` cannot
import the edge, so a Studio chat tool imports the rule from here.

Fence: ``tests/unit/test_door_values_single_copy.py``.
"""
from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "ISO_TIME_RE",
    "KEY_RE",
    "SECRET_WORDS",
    "compact_json",
    "door_value",
    "is_number",
    "plain_shape",
    "untrusted",
]

#: A key-shaped string: a field name, a category, a link type.
KEY_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")

#: An ISO-8601 date or time, one of the plain shapes of AD-10.
ISO_TIME_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,9})?)?"
    r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?")

#: AD-10, the second guard. A key whose name holds one of these words leaves
#: no answer, at any depth. The first guard is that no X route is a door op.
SECRET_WORDS: tuple[str, ...] = ("token", "secret", "password", "credential", "api_key")


def untrusted(value: Any) -> dict[str, str] | None:
    """``{"untrusted_text": value}``, or ``None`` for a null (§9.4)."""
    if value is None:
        return None
    return {"untrusted_text": value if isinstance(value, str) else str(value)}


def is_number(value: Any) -> bool:
    """True for an int or a float. A bool is not a number."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compact_json(value: Any) -> str:
    """*value* as JSON with sorted keys and no spaces."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plain_shape(value: str) -> bool:
    """True for a plain shape of AD-10: an id, an ISO-8601 time, or a token
    of ``[A-Za-z0-9_.:-]`` with at most 64 characters."""
    return bool(KEY_RE.fullmatch(value) or ISO_TIME_RE.fullmatch(value))


def door_value(value: Any, plain: frozenset[str], key: str | None = None) -> Any:
    """One value of a door answer, mapped by AD-10.

    Each string becomes ``{"untrusted_text": ...}``, at any depth. A string
    under a key that *plain* names stays plain only in a plain shape. A
    number, a boolean and null stay. A dict keeps each key of a key shape
    (:data:`KEY_RE`) whose name holds no word of :data:`SECRET_WORDS`, and
    drops the others with their values. A list maps each item under the key
    of the list.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for name, item in value.items():
            if not isinstance(name, str) or not KEY_RE.fullmatch(name):
                continue
            if any(word in name.lower() for word in SECRET_WORDS):
                continue
            out[name] = door_value(item, plain, name)
        return out
    if isinstance(value, list):
        return [door_value(item, plain, key) for item in value]
    if isinstance(value, str):
        if key in plain and plain_shape(value):
            return value
        return untrusted(value)
    if value is None or isinstance(value, bool) or is_number(value):
        return value
    return untrusted(compact_json(value))
