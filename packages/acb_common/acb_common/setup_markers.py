"""The ``<<<SETUP:service:KEY=value>>>`` markers of a model answer, removed
before anything is stored (WS-54 fix round 2, item 5).

A chat agent that helps a member set up an integration writes the key into
its answer as ``<<<SETUP:github:GITHUB_TOKEN=ghp_…>>>``. The executor and the
chat BFF read the marker and save the key through its own writer. The answer
TEXT is stored as well, in two places: the native session of the run
(``orchestrator.native_session_store``) and the chat transcript
(``gateway.routes.chat._upsert_messages``). Before this module both stored the
raw marker, so the secret sat in plain text in a session row and in a chat row.

:func:`redact` replaces each marker with :data:`REPLACEMENT`, which names
the service and the key and holds no value. :func:`redact_tree` does it in
every string of a JSON-like value. Both stores call them on their write path,
so every path (the batch run, the stream, the server fold, a browser save)
stores no value.

The replacement is not a marker, so a model that reads it back writes no
new key from it. Fence: ``tests/unit/test_setup_marker_redaction.py``.
"""

from __future__ import annotations

import re
from typing import Any

#: One marker, up to the first ``>>>`` on its line. It is wider than the
#: shape that the callers read, on purpose: a value that holds a ``>`` is not
#: saved by them, and it must not be stored either.
MARKER_RE = re.compile(r"<<<SETUP:([^:>\n]{0,100}):?([A-Za-z0-9_]{0,100})[^\n]*?>>>")
#: A marker that never closes (a stream cut short): removed to the end of its line.
OPEN_MARKER_RE = re.compile(r"<<<SETUP:([^:>\n]{0,100}):?([A-Za-z0-9_]{0,100})[^\n]*")

#: What a marker becomes. It holds no value and is not a marker.
REPLACEMENT = "[setup value removed: {service} {key}]"


def redact(text: str) -> str:
    """*text* with every SETUP marker replaced. Pure."""
    if "<<<SETUP:" not in text:
        return text

    def _one(m: re.Match[str]) -> str:
        return REPLACEMENT.format(service=m.group(1) or "?", key=m.group(2) or "?")

    return OPEN_MARKER_RE.sub(_one, MARKER_RE.sub(_one, text))


def redact_tree(value: Any) -> Any:
    """*value* with :func:`redact` applied to every string inside it. Pure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_tree(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_tree(v) for v in value)
    return value


__all__ = ["MARKER_RE", "OPEN_MARKER_RE", "REPLACEMENT", "redact", "redact_tree"]
