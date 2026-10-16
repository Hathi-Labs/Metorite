"""CRM · the fill rank, the read side (WS-53 CRM-Q17a-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.3 and the CRM-Q17a-1
done-when list of §18.9.6.

The sync writes ``crm_field_fill`` (``crm_sync/fill.py``, migration 274). This
module holds the vocabulary of its keys and every read of it. It registers no
route. ``fields.py``, ``modules.py``, ``export.py`` and ``settings.py`` call it.

**The keys.** One row for each key of an entity:

* a core entity (``lead``, ``contact``, ``company``, ``deal``) has its
  STANDARD keys (:func:`standard_keys`): the real column behind each list
  column of ``export.COLUMNS``, then each key of ``core.SYSTEM_FIELDS``. A
  list column that is a join names its real column through
  :data:`LIST_ALIASES` (``status_name`` is ``status_id``, a deal's
  ``organization_name`` is ``company_id``). Each custom field is
  ``custom.<key>``, the prefix of the custom list filters;
* a generic module (``module:<ApiName>``) has the API name of each field.

**Filled.** A value is filled unless it is NULL, JSON ``null``, an empty or
blank string, ``[]``, ``{}`` or ``false``. ``false`` counts as empty: Zoho
sends ``false`` for an unticked checkbox, and an unticked box is the default,
not an answer. ``0`` is filled. ``distinct`` counts the different filled
values.

**The wire.** ``fill`` on a field definition is the share ``filled / total``,
from 0 to 1, with four decimals. It is ``None`` when the entity holds no
record, or when the key has no row yet. ``distinct`` is the count of
different filled values.

**Ship dark.** Each read first asks ``flags.fill_rank_allowed``. An org
outside ``CRM_FILL_RANK_ORGS`` gets ``None`` from each function here, and the
routes then serve ``fill``, ``distinct``, ``empty_columns`` and ``labels`` as
``null``. That is the shape of the CRM before this slice.

**Tenancy (R5).** The organization comes from the server: the caller passes
``UserContext.organization_id`` or the bound tenant, never request input.
Each read names the organization and runs in the caller's
``_tenant_session()``, so FORCE row level security binds it too.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from gateway.routes.crm.core import (
    CUSTOM_PREFIX,
    ENTITIES,
    FIELD_ENTITY_BY_SLUG,
    SYSTEM_FIELDS,
    from_jsonb,
    is_mirror_mode,
)
from gateway.routes.crm.flags import fill_rank_allowed
from pydantic import BaseModel
from sqlalchemy import text

#: The provider whose profile holds the labels. One provider today.
PROVIDER = "zoho"

#: A list column that is a join → the real column it shows, per entity slug.
#: Every other list column is a real column of its own table.
LIST_ALIASES: dict[str, dict[str, str]] = {
    "leads": {"status_name": "status_id"},
    "deals": {"status_name": "status_id", "organization_name": "company_id"},
}

#: The name column of each entity. A list always shows it, so it is never in
#: ``empty_columns``. Contacts draw ``first_name last_name`` under one header.
NAME_KEYS: frozenset[str] = frozenset({"name", "lead_name", "first_name"})

#: The keys that never show in ``empty_columns``: the name column and the
#: time of the last activity, which the list shows in each case.
NEVER_EMPTY: frozenset[str] = NAME_KEYS | {"last_activity_at"}

_FILL_SQL = (
    "SELECT key, filled, total, distinct_count FROM crm_field_fill "
    "WHERE organization_id = CAST(:org AS uuid) AND entity = :entity"
)


@dataclass(frozen=True)
class Fill:
    """The fill numbers of one key."""

    filled: int
    total: int
    distinct: int

    @property
    def share(self) -> float | None:
        """``filled / total``, from 0 to 1. ``None`` with no record."""
        if self.total <= 0:
            return None
        return round(self.filled / self.total, 4)


def list_columns(slug: str) -> tuple[str, ...]:
    """The keys of the list columns of *slug*, as ``export.COLUMNS`` names
    them. One vocabulary with the list and the export.

    A late import: ``export`` imports this module.
    """
    from gateway.routes.crm.export import COLUMNS

    return tuple(key for key, _header in COLUMNS[slug])


@functools.cache
def standard_keys(slug: str) -> tuple[str, ...]:
    """The standard keys of a core entity, in order, with no repeat: the real
    column of each list column, then each system field."""
    aliases = LIST_ALIASES.get(slug, {})
    keys: list[str] = []
    for key in (
        *(aliases.get(k, k) for k in list_columns(slug)),
        *(f.key for f in SYSTEM_FIELDS[slug]),
    ):
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def fill_key(key: str, *, is_system: bool, module: bool = False) -> str:
    """The ``crm_field_fill.key`` of a field definition."""
    if module or is_system:
        return key
    return f"{CUSTOM_PREFIX}{key}"


async def read_fill(db: Any, org: str, entity: str) -> dict[str, Fill]:
    """Every fill row of one entity (``crm_field_defs.entity`` word)."""
    rows = (await db.execute(
        text(_FILL_SQL), {"org": str(org), "entity": entity},
    )).fetchall()
    return {
        str(r.key): Fill(int(r.filled or 0), int(r.total or 0), int(r.distinct_count or 0))
        for r in rows
    }


async def fills_for(db: Any, org: str | None, entity: str) -> dict[str, Fill] | None:
    """The fill rows of *entity*, or ``None`` when the flag shuts *org* out.

    No read happens for an org outside the list.
    """
    if not org or not fill_rank_allowed(org):
        return None
    return await read_fill(db, str(org), entity)


def annotate[M: BaseModel](
    defs: Sequence[M], fills: dict[str, Fill] | None, *, module: bool = False,
) -> list[M]:
    """Each definition with ``fill`` and ``distinct`` from *fills*.

    ``fills`` of ``None`` (the flag is off) leaves each definition as it was.
    A key with no row gets ``None`` in both.
    """
    if fills is None:
        return list(defs)
    out: list[M] = []
    for d in defs:
        key = str(getattr(d, "key", ""))
        system = bool(getattr(d, "is_system", False))
        found = fills.get(fill_key(key, is_system=system, module=module))
        out.append(d.model_copy(update={
            "fill": found.share if found is not None else None,
            "distinct": found.distinct if found is not None else None,
        }))
    return out


def empty_set(slug: str, fills: dict[str, Fill]) -> list[str]:
    """The ONE rule of ``empty_columns`` (§18.9.6 CRM-Q17a-1 item 4).

    A standard key of *slug* with ``total > 0`` and ``filled = 0``. The name
    column and ``last_activity_at`` never show. Each key shows as its real
    column AND, for a joined list column, as the list key too, so the list,
    the layout and the export each find it in their own words.
    """
    empty = {
        key for key in standard_keys(slug)
        if key not in NEVER_EMPTY
        and (f := fills.get(key)) is not None and f.total > 0 and f.filled == 0
    }
    for alias, real in LIST_ALIASES.get(slug, {}).items():
        if real in empty:
            empty.add(alias)
    return sorted(empty)


async def empty_columns(db: Any, org: str | None, slug: str) -> list[str] | None:
    """``empty_columns`` of *slug* for *org*, or ``None`` when the flag shuts
    *org* out. ``GET /crm/fields/{entity}?layout=1`` serves it, and the CSV
    export leaves each key out. Both call this function."""
    fills = await fills_for(db, org, FIELD_ENTITY_BY_SLUG[slug])
    if fills is None:
        return None
    return empty_set(slug, fills)


class EntityLabel(BaseModel):
    """The source's own names of one core entity (§18.9.1)."""

    label: str
    singular_label: str | None = None


#: The Zoho module of each core entity → its API slug. ``Entity.zoho_module``
#: is the one answer to "which Zoho module is a deal".
_SLUG_BY_MODULE: dict[str, str] = {
    e.zoho_module: e.slug for e in ENTITIES.values() if e.zoho_module
}


def labels_of(profile: dict[str, Any] | None) -> dict[str, EntityLabel]:
    """The plural and singular label of each core entity that *profile* names.

    ``_module_rows`` in the Zoho mapping stores ``label`` (the plural) and
    ``singular_label`` for each module.
    """
    out: dict[str, EntityLabel] = {}
    for m in (profile or {}).get("modules") or []:
        if not isinstance(m, dict):
            continue
        slug = _SLUG_BY_MODULE.get(str(m.get("api_name") or ""))
        label = m.get("label")
        if slug is None or not isinstance(label, str) or not label.strip():
            continue
        singular = m.get("singular_label")
        if not isinstance(singular, str) or not singular.strip():
            singular = None
        out[slug] = EntityLabel(
            label=label.strip(), singular_label=singular.strip() if singular else None,
        )
    return out


async def source_labels(db: Any, org: str | None) -> dict[str, EntityLabel] | None:
    """The source's labels of the four core entities (CRM-Q17a-1 item 6).

    ``None`` for an org outside ``CRM_FILL_RANK_ORGS``, in Native mode, or
    with no source profile yet.
    """
    from gateway.crm_sync import profiles

    if not org or not fill_rank_allowed(org):
        return None
    if not await is_mirror_mode(db, str(org)):
        return None
    latest = await profiles.latest(db, str(org))
    if latest is None:
        return None
    labels = labels_of(from_jsonb(latest.profile))
    return labels or None


__all__ = [
    "LIST_ALIASES",
    "NAME_KEYS",
    "NEVER_EMPTY",
    "EntityLabel",
    "Fill",
    "annotate",
    "empty_columns",
    "empty_set",
    "fill_key",
    "fills_for",
    "labels_of",
    "list_columns",
    "read_fill",
    "source_labels",
    "standard_keys",
]
