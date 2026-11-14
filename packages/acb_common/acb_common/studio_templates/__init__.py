"""The Studio app templates: plain manifests in a code registry (STU-K1a).

Spec: ``project-docs/specs/ai_studio.md`` section 14, row K1. A template is a
Studio manifest (``studio_manifest.py``) in a ``<id>.json`` file beside this
module. It is NOT a table and NOT a second manifest shape (D205). The gateway
creates, publishes and installs a copy for a member in one step.

A template holds no colour, no size in px and no credential (D12, the manifest
conformance fence). ``tests/unit/test_studio_templates.py`` holds the registry.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

__all__ = ["TEMPLATE_IDS", "TemplateInfo", "get_template", "list_templates"]

_DIR = Path(__file__).resolve().parent

#: The ids of the templates, in the order of the gallery. A file with no id
#: here is not served. ``test_studio_templates`` holds the files equal to it.
TEMPLATE_IDS: tuple[str, ...] = (
    "field-visits",
    "support-desk",
    "purchase-requests",
    "expense-claims",
    "hiring",
    "spares",
)


class TemplateInfo(dict[str, Any]):
    """The gallery header of one template: ``id``, ``name`` and ``purpose``."""


def _load(template_id: str) -> dict[str, Any]:
    path = _DIR / f"{template_id.replace('-', '_')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def get_template(template_id: object) -> dict[str, Any] | None:
    """A fresh copy of the manifest of *template_id*, or None when the registry
    has no such id. Any value that is not a string answers None."""
    if not isinstance(template_id, str) or template_id not in TEMPLATE_IDS:
        return None
    return copy.deepcopy(_load(template_id))


def list_templates() -> list[TemplateInfo]:
    """The gallery headers, in registry order."""
    out: list[TemplateInfo] = []
    for tid in TEMPLATE_IDS:
        doc = _load(tid)
        out.append(TemplateInfo(id=doc["id"], name=doc["name"], purpose=doc["purpose"]))
    return out
