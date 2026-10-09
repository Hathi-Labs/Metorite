"""Text that Postgres stores: no NUL, no lone surrogate.

Postgres refuses two kinds of character that a Python ``str`` can hold:

* **NUL** (``\\x00``). A ``text`` column refuses it, and a ``jsonb`` column
  refuses the ``\\u0000`` escape that ``json.dumps`` writes for it
  (``UntranslatableCharacter``).
* **A lone surrogate** (``\\ud800`` to ``\\udfff``). It does not encode to
  UTF-8, so the driver fails before the row reaches the server, and ``jsonb``
  refuses its escape too.

On 2026-10-09 a chat agent read a ``.docx`` as raw zip bytes. The bytes held
NUL, and they reached a tool result. The ``chat_message`` upsert then failed
14 times, and ``run_trace`` and ``chat_fold`` failed too, so the turn was
lost. This module is the ONE place that cleans such text before a write.

:func:`pg_safe` cleans a value. :func:`pg_json` cleans a value and dumps it
for a ``CAST(... AS JSONB)`` parameter. Both remove the two kinds and keep
every other character. A valid emoji is one code point in a Python ``str``,
not a surrogate pair, so it stays.

Callers: ``gateway.routes.chat._upsert_messages`` (the chat route and the
chat fold both write through it) and ``gateway.run_trace._persist_row``.
Fence: ``tests/unit/test_pg_text.py`` (R8, a real database).
"""
from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["pg_json", "pg_safe"]

_REFUSED = re.compile("[\x00\ud800-\udfff]")


def pg_safe(value: Any) -> Any:
    """*value* with every NUL and lone surrogate removed from its strings.

    It walks dicts (keys and values), lists and tuples, and returns new
    containers. A value of another type comes back unchanged.
    """
    if isinstance(value, str):
        return _REFUSED.sub("", value) if _REFUSED.search(value) else value
    if isinstance(value, dict):
        return {pg_safe(k): pg_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [pg_safe(v) for v in value]
    return value


def pg_json(value: Any) -> str:
    """``json.dumps`` of :func:`pg_safe` of *value*, for a JSONB parameter."""
    return json.dumps(pg_safe(value))
