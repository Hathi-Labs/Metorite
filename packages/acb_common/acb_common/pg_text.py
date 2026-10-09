"""Make a value that Postgres can store in a ``text`` or ``jsonb`` column.

🔴 **Postgres refuses two kinds of character that Python strings can hold.**

1. **U+0000 (NUL).** A ``text`` value cannot hold it, and a ``jsonb`` value
   refuses the escape ``\\u0000`` with ``UntranslatableCharacter``.
2. **A lone surrogate (U+D800 to U+DFFF).** psycopg cannot encode it as
   UTF-8, and ``jsonb`` refuses an unpaired ``\\udXXX`` escape.

Either one fails the whole statement. On 2026-10-09 a tool result held the
raw bytes of a ZIP file (``PK\\x03\\x04\\x14\\x00``), and
``POST /chat/sessions/{id}/messages`` answered 500 for 40 seconds. The reply
of that run was never saved.

:func:`storable` is the ONE answer. A writer of model output, tool output or
client text calls it on the Python value BEFORE ``json.dumps`` and before the
bind. Do not write a second copy of the rule in a caller.

**Each such character becomes U+FFFD (REPLACEMENT CHARACTER).** It is not
removed, for three reasons. The reader sees that the text changed at that
place. Every offset in the text stays valid, because the length does not
change. And it is the character that Python's own decoder writes for a byte it
cannot read, and that ``orchestrator/sandbox/data_engine.py`` already writes
for a NUL in a CSV file.

Callers: ``gateway/routes/chat.py`` (``_upsert_messages``, the session upsert
and the session patch), ``gateway/run_trace.py`` (``_persist_row``) and
``orchestrator/native_session_store.py`` (``_session_body``).
Fence: ``tests/unit/test_chat_nul_persist.py`` (hermetic and R8).
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["REPLACEMENT", "storable"]

#: The character that takes the place of each character Postgres refuses.
REPLACEMENT = "�"

#: NUL, and every surrogate code point. A Python ``str`` holds an astral
#: character as ONE code point, so a surrogate in a ``str`` is always unpaired.
_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")


def storable(value: Any) -> Any:
    """*value*, with each NUL and each lone surrogate replaced by U+FFFD.

    It walks a ``dict`` (keys and values), a ``list`` and a ``tuple`` to any
    depth, and it returns a new container. A ``str`` with nothing to replace
    comes back as the same object. Any other value comes back unchanged.
    """
    if isinstance(value, str):
        if _UNSTORABLE.search(value) is None:
            return value
        return _UNSTORABLE.sub(REPLACEMENT, value)
    if isinstance(value, dict):
        return {storable(k): storable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [storable(v) for v in value]
    if isinstance(value, tuple):
        return tuple(storable(v) for v in value)
    return value
