"""CRM · the overlay of an empty core slot, for the readers (WS-53 CRM-Q20-3a).

Spec: ``project-docs/specs/crm_platform.md`` §18.13.5.

An approved ``role`` item of the plan says that a generic module plays an
empty core slot: Commercials plays Deals. CRM-Q20-2 serves the rail. This
module serves two more readers, ``GET /crm/aggregate`` and the AI tools: a
total over Deals then reads the records of the module.

**One read.** :func:`overlay_of` returns the approved ``role`` of one slot,
with its ``target`` and ``stage`` items. The catalogue entry of the module
reaches the aggregate through ``field_sql.module_scope`` only, so one total
reads it once (fix round, item 5). It returns ``None`` with no approved role. As
``mapping_plans.approved_overlays`` does, it puts the org in the predicate,
reads through RLS, and names only a module that the bound org mirrors. A
Native CRM has no mirrored module, so it has no overlay.

**Who calls it.** ``aggregate.py``, for a core entity. The SQL of the
projection lives in ``field_sql.py``. Each other core reader stays as it is
(§18.13.5, "Readers that wait").

**Tenancy (R5).** The caller passes the bound tenant, never request input.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The API slug of each core entity → its slot.
SLOT_OF: Mapping[str, str] = {
    "organizations": "company", "contacts": "contact", "leads": "lead", "deals": "deal",
}


@dataclass(frozen=True)
class Overlay:
    """The approved overlay of one slot.

    ``targets`` maps a target column to the field API names of its item.
    ``stage`` is the API name of the stage field, and ``values`` maps each
    stage value to ``won``, ``lost`` or ``open``.
    """

    slot: str
    module: str
    targets: dict[str, list[str]] = field(default_factory=dict)
    stage: str | None = None
    values: dict[str, str] = field(default_factory=dict)

    def wire(self) -> dict[str, str]:
        """What the answer carries: the slot and the module."""
        return {"slot": self.slot, "module": self.module}


#: The order of the lane types (§18.13.5, "The lanes of an overlaid slot").
LANE_TYPES: tuple[str, ...] = ("open", "won", "lost")
#: The one lane that holds each value with no entry in the stage map, a
#: value that is not an option, and a blank value. It comes last.
UNSORTED = "unsorted"
UNSORTED_LABEL = "Unsorted"


def lanes(options: Mapping[str, str], values: Mapping[str, str]) -> list[tuple[str, str]]:
    """The lanes of an overlaid slot, in order: each value that maps to
    ``open``, then ``won``, then ``lost``, each in the order of the options
    of the field in Zoho. A mapped value that is not an option goes to the
    Unsorted lane. With no options known, the values keep their name order.
    Returns ``(value, type)``. The Unsorted lane is not in the list."""
    known = list(options) if options else sorted(values)
    return [(value, word) for word in LANE_TYPES for value in known
            if values.get(value) == word]


async def overlay_of(db: Any, org: str | None, slot: str | None) -> Overlay | None:
    """The approved overlay of *slot* for *org*, or ``None``, in the
    caller's tenant session."""
    from gateway.routes.crm.mapping_plans import (
        KIND_STAGE,
        KIND_TARGET,
        approved_others,
        roles_of,
    )
    from gateway.routes.crm.modules import mirrored_modules

    if not org or not slot:
        return None
    others = await approved_others(db, str(org))
    module = roles_of(others).get(slot)
    if not module:
        return None
    if module not in await mirrored_modules(db):
        return None
    mine = [o for o in others if o.get("slot") == slot and o.get("module") == module]
    targets = {
        str(o["target"]): [str(f) for f in o.get("fields") or [] if isinstance(f, str)]
        for o in mine
        if o.get("kind") == KIND_TARGET and isinstance(o.get("target"), str)
    }
    stage = next((o for o in mine if o.get("kind") == KIND_STAGE), None)
    values = stage.get("values") if stage is not None else None
    return Overlay(
        slot=slot, module=module, targets=targets,
        stage=str(stage["field_api_name"]) if stage and stage.get("field_api_name") else None,
        values={str(k): str(v) for k, v in values.items()} if isinstance(values, Mapping) else {},
    )


__all__ = ["LANE_TYPES", "SLOT_OF", "UNSORTED", "UNSORTED_LABEL", "Overlay", "lanes",
           "overlay_of"]
