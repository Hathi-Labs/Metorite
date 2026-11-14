"""The connector list (WS-54 IN-11).

``connector_list.json`` is the one curated file of the providers that a member
can connect. This module is its Python reader. The console, the "Use data
from" step of Studio (I8), the result card (IN-9) and the chat read it.
``scripts/gen_connectors.py`` writes the TypeScript twin from the same file.
Spec: ``project-docs/specs/integrations_console.md`` §3.7 rule 3.
The file holds no credential. Fence: ``tests/unit/test_connector_list.py``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("connector_list.json")
SIGNIN_KINDS = ("one_click", "admin_consent", "key", "mcp_address")
SCOPES = ("org", "member")


@lru_cache(maxsize=1)
def connector_rows() -> tuple[dict[str, Any], ...]:
    """Every row of the list, in file order."""
    doc = json.loads(PATH.read_text(encoding="utf-8"))
    return tuple(doc["rows"])


def connector(provider_id: str) -> dict[str, Any] | None:
    """The row of one provider id, or ``None``."""
    return next((r for r in connector_rows() if r["id"] == provider_id), None)
