"""CRM · the cutover preview (WS-53 CRM-Z10a, ``crm_platform.md`` §3.4, D133).

    GET /crm/cutover/preview     what moves to Metorite, and what does not

The preview comes before the switch from Zoho to Metorite (CRM-Z10b). An admin
reads it, and the owner reads it with a customer.

⚠️ **It writes nothing, and it calls no source.** The R8 test hashes every
``crm_*`` table and ``integration_connections`` before and after the call, and
a fake source records no call. The counts in Zoho come from the last good sync
run (``crm_sync_runs.counts`` and ``estimate``), shown "as of <time>". A live
Zoho count would read a credential, and a token refresh is a write.

⚠️ **Dark, admin only, Mirror mode only.** The route needs the CRM kill switch
(at the router), ``CRM_MIRROR`` (404 otherwise) and ``admin:access:manage``
(403 otherwise). An organization in another mode gets 409 ``not_mirror``.

A module is **not complete** when it is held (CRM-Q16), unavailable (CRM-Q15),
or the run that counted it in Zoho wrote records as ``failed`` or ``refused``.
The switch waits for such a module unless the admin leaves it out (D133).

⚠️ **The Zoho count of a key is its own.** An incremental run carries no core
estimate, and its ``seen`` is the number of changed records. So the count of a
key comes from the newest good run that HAS an estimate for it, with that run's
time. It never falls back to ``seen``. When no good run has an estimate for the
key, the count is ``None`` and the card shows a dash. ``failed`` and ``refused``
come from that same run. When no run counted the key, they come from the newest
good run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.crm.access import ADMIN_PERMISSION, read_settings
from gateway.routes.crm.core import _tenant_session, from_jsonb, router
from gateway.routes.crm.fill_rank import labels_of
from gateway.routes.crm.flags import require_crm_mirror
from pydantic import BaseModel
from sqlalchemy import text

_GUARDS = [Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)]

MODULE_KEY_PREFIX = "module:"

#: The five core keys of a sync run, with the table of each and the word that a
#: member reads when the source gave no label.
_CORE: tuple[tuple[str, str, str, str], ...] = (
    ("organizations", "crm_companies", "Companies", "organizations"),
    ("contacts", "crm_contacts", "Contacts", "contacts"),
    ("leads", "crm_leads", "Leads", "leads"),
    ("deals", "crm_deals", "Deals", "deals"),
    ("activities", "crm_activities", "Activities", ""),
)

#: The tables that carry an owner and a ``custom`` column.
_OWNED_TABLES: tuple[str, ...] = ("crm_companies", "crm_contacts", "crm_leads", "crm_deals")

_ACTIVITY_LABELS = {"note": "Notes", "call": "Calls", "meeting": "Meetings", "task": "Tasks"}

#: The most owner names that the preview lists.
_OWNER_LIMIT = 100

# Every statement below reads. A name that reaches an f-string comes from the
# constants above, never from a request (R5).
_SPLIT_SQL = (
    "SELECT count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM {table} WHERE organization_id = CAST(:org AS uuid)"
)
_ACTIVITY_SQL = (
    "SELECT type, count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_activities WHERE organization_id = CAST(:org AS uuid) GROUP BY type"
)
_MODULE_SQL = (
    "SELECT module, count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_module_records WHERE organization_id = CAST(:org AS uuid) "
    "GROUP BY module"
)
_OWNER_SQL = (
    "SELECT name, sum(n)::bigint AS records FROM ("
    + " UNION ALL ".join(
        f"SELECT custom ->> '_owner_name' AS name, count(*) AS n FROM {t} "
        "WHERE organization_id = CAST(:org AS uuid) AND owner_email IS NULL "
        "AND custom ->> '_owner_name' IS NOT NULL GROUP BY 1"
        for t in _OWNED_TABLES
    )
    + ") u GROUP BY name ORDER BY records DESC, name LIMIT :lim"
)
_OWNER_TOTAL_SQL = (
    "SELECT count(DISTINCT name) AS names FROM ("
    + " UNION ALL ".join(
        f"SELECT custom ->> '_owner_name' AS name FROM {t} "
        "WHERE organization_id = CAST(:org AS uuid) AND owner_email IS NULL "
        "AND custom ->> '_owner_name' IS NOT NULL"
        for t in _OWNED_TABLES
    )
    + ") u"
)
_PIPELINES_SQL = (
    "SELECT count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_pipelines WHERE organization_id = CAST(:org AS uuid)"
)
_STAGES_SQL = (
    "SELECT count(*) AS n FROM crm_pipeline_stages "
    "WHERE organization_id = CAST(:org AS uuid)"
)
_LINKS_SQL = (
    "SELECT count(*) FILTER (WHERE ext_source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE ext_source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_deal_contacts WHERE organization_id = CAST(:org AS uuid)"
)
_FIELDS_SQL = (
    "SELECT count(*) FILTER (WHERE source = 'zoho') AS from_zoho, "
    "count(*) FILTER (WHERE source IS DISTINCT FROM 'zoho') AS native "
    "FROM crm_field_defs WHERE organization_id = CAST(:org AS uuid)"
)
_VIEWS_SQL = (
    "SELECT count(*) AS n FROM crm_views WHERE organization_id = CAST(:org AS uuid)"
)
_COLUMNS = "id, kind, status, started_at, finished_at, heartbeat_at, counts, estimate, credits_used, error"
#: The newest good run, however old.
_NEWEST_OK_SQL = (
    f"SELECT {_COLUMNS} FROM crm_sync_runs "
    "WHERE organization_id = CAST(:org AS uuid) AND status = 'ok' "
    "ORDER BY started_at DESC LIMIT 1"
)
#: The newest good run that holds an estimate for one key.
_NEWEST_ESTIMATE_SQL = (
    f"SELECT {_COLUMNS} FROM crm_sync_runs "
    "WHERE organization_id = CAST(:org AS uuid) AND status = 'ok' "
    "AND estimate ->> CAST(:key AS text) IS NOT NULL "
    "ORDER BY started_at DESC LIMIT 1"
)
_PROFILE_SQL = (
    "SELECT profile -> 'modules' AS modules FROM crm_source_profiles "
    "WHERE organization_id = CAST(:org AS uuid) "
    "ORDER BY fetched_at DESC, version DESC LIMIT 1"
)


class Split(BaseModel):
    """Rows that came from Zoho, and rows that members made in Metorite."""

    from_zoho: int = 0
    native: int = 0


class PartRow(BaseModel):
    label: str
    metorite: Split


class RecordRow(BaseModel):
    key: str
    label: str
    #: Zoho's count in the newest good run that counted this key. ``None`` when
    #: no good run did.
    zoho: int | None = None
    #: The age of that run, in seconds. ``None`` with ``zoho``.
    zoho_age_seconds: int | None = None
    metorite: Split
    #: The parts of the row, for the activities (notes, calls …).
    parts: list[PartRow] = []
    #: ``complete`` or the reason the module is not: ``held``, ``unavailable``,
    #: ``failed`` or ``refused``.
    state: Literal["complete", "held", "unavailable", "failed", "refused"] = "complete"
    #: The records that the run behind ``zoho`` did not write (``failed``).
    failed: int = 0
    refused: int = 0


class OtherRow(BaseModel):
    key: str
    label: str
    metorite: Split


class OwnerRow(BaseModel):
    name: str
    records: int


class LabelRow(BaseModel):
    key: str
    label: str
    singular: str | None = None


class SyncFacts(BaseModel):
    #: The end of the last good run. ``None`` when no run ended well.
    last_good_at: datetime | None = None
    age_seconds: int | None = None
    #: A run is open now.
    running: bool = False


class CutoverPreview(BaseModel):
    """``GET /crm/cutover/preview``. Counts, names and times. No token."""

    mode: Literal["mirror"] = "mirror"
    sync: SyncFacts
    records: list[RecordRow]
    other: list[OtherRow]
    unmatched_owners: list[OwnerRow]
    unmatched_owner_names: int = 0
    labels_kept: list[LabelRow]
    #: The label of each module that is not complete. The switch waits for them.
    not_complete: list[str]
    #: What stays in Zoho. Attachments first.
    stays_in_zoho: list[str]


def _int(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


def _step(entry: Any) -> dict[str, int]:
    """The counts of one key. A run may store a plain number or an object."""
    if isinstance(entry, dict):
        return {k: v for k, v in ((k, _int(v)) for k, v in entry.items()) if v is not None}
    value = _int(entry)
    return {"seen": value} if value is not None else {}


async def _counting_run(db: Any, org: str, key: str) -> Any:
    """The newest good run with an estimate for *key*, or ``None``."""
    from gateway.crm_sync import runs

    row = (await db.execute(text(_NEWEST_ESTIMATE_SQL), {"org": org, "key": key})).first()
    return runs._row(row) if row is not None else None


def _zoho_count(key: str, run: Any) -> int | None:
    """Zoho's count from a run that holds an estimate. Never ``seen``."""
    if run is None:
        return None
    return _int((run.estimate or {}).get(key))


def _state(key: str, run: Any, held: frozenset[str]) -> tuple[str, int, int]:
    """``(state, failed, refused)`` of one count key."""
    step = _step(run.counts.get(key)) if run is not None else {}
    failed, refused = step.get("failed", 0), step.get("refused", 0)
    if key in held:
        return "held", failed, refused
    if step.get("unavailable"):
        return "unavailable", failed, refused
    if failed > 0:
        return "failed", failed, refused
    if refused > 0:
        return "refused", failed, refused
    return "complete", failed, refused


def _split(row: Any) -> Split:
    return Split(from_zoho=int(row.from_zoho or 0), native=int(row.native or 0))


async def _record(
    db: Any, org: str, key: str, good: Any, held: frozenset[str], now: datetime,
) -> dict[str, Any]:
    """The Zoho count, its age and the state of one key."""
    counting = await _counting_run(db, org, key)
    state, failed, refused = _state(key, counting or good, held)
    age = None
    if counting is not None and counting.finished_at is not None:
        age = max(int((now - counting.finished_at).total_seconds()), 0)
    zoho = _zoho_count(key, counting)
    return {
        "zoho": zoho, "zoho_age_seconds": age if zoho is not None else None,
        "state": state, "failed": failed, "refused": refused,
    }


async def _one(db: Any, sql: str, org: str) -> Any:
    return (await db.execute(text(sql), {"org": org})).first()


async def preview(db: Any, org: str) -> CutoverPreview:
    """Build the preview from reads only. ``org`` is the bound tenant."""
    from gateway.crm_sync import holds, runs

    due = await runs.due_info(db, org)
    # The newest run that ended well, however far back it is.
    good_row = (await db.execute(text(_NEWEST_OK_SQL), {"org": org})).first()
    good = runs._row(good_row) if good_row is not None else None
    held = holds.held_keys(await holds.read(db, org))
    profile = await _one(db, _PROFILE_SQL, org)
    modules_body = from_jsonb(profile.modules) if profile is not None else None
    labels = labels_of(modules_body)
    module_labels = {
        f"{MODULE_KEY_PREFIX}{m['api_name']}": str(m.get("label") or m["api_name"])
        for m in (modules_body if isinstance(modules_body, list) else [])
        if isinstance(m, dict) and m.get("api_name")
    }

    activity_rows = (await db.execute(text(_ACTIVITY_SQL), {"org": org})).fetchall()
    records: list[RecordRow] = []
    for key, table, fallback, slug in _CORE:
        facts = await _record(db, org, key, good, held, due.db_now)
        if key == "activities":
            parts = [
                PartRow(label=_ACTIVITY_LABELS.get(str(r.type), str(r.type).title()),
                        metorite=_split(r))
                for r in sorted(activity_rows, key=lambda r: str(r.type))
            ]
            split = Split(
                from_zoho=sum(p.metorite.from_zoho for p in parts),
                native=sum(p.metorite.native for p in parts),
            )
        else:
            parts = []
            split = _split(await _one(db, _SPLIT_SQL.format(table=table), org))
        label = labels[slug].label if slug in labels else fallback
        records.append(RecordRow(
            key=key, label=label, metorite=split, parts=parts, **facts,
        ))

    module_rows = {
        f"{MODULE_KEY_PREFIX}{r.module}": _split(r)
        for r in (await db.execute(text(_MODULE_SQL), {"org": org})).fetchall()
    }
    module_keys = set(module_rows) | {
        k for k in held if k.startswith(MODULE_KEY_PREFIX)
    } | {
        k for k in (good.counts if good is not None else {})
        if k.startswith(MODULE_KEY_PREFIX)
    }
    for key in sorted(module_keys):
        facts = await _record(db, org, key, good, held, due.db_now)
        records.append(RecordRow(
            key=key, label=module_labels.get(key, key[len(MODULE_KEY_PREFIX):]),
            metorite=module_rows.get(key, Split()), **facts,
        ))

    stages = await _one(db, _STAGES_SQL, org)
    views = await _one(db, _VIEWS_SQL, org)
    other = [
        OtherRow(key="pipelines", label="Pipelines",
                 metorite=_split(await _one(db, _PIPELINES_SQL, org))),
        OtherRow(key="stages", label="Pipeline stages",
                 metorite=Split(native=int(stages.n or 0))),
        OtherRow(key="deal_contacts", label="Contacts linked to deals",
                 metorite=_split(await _one(db, _LINKS_SQL, org))),
        OtherRow(key="fields", label="Fields",
                 metorite=_split(await _one(db, _FIELDS_SQL, org))),
        OtherRow(key="views", label="Saved views",
                 metorite=Split(native=int(views.n or 0))),
    ]

    owners = (await db.execute(
        text(_OWNER_SQL), {"org": org, "lim": _OWNER_LIMIT})).fetchall()
    names = await _one(db, _OWNER_TOTAL_SQL, org)

    mirrored = {
        f"{MODULE_KEY_PREFIX}{m['api_name']}"
        for m in (modules_body if isinstance(modules_body, list) else [])
        if isinstance(m, dict) and m.get("mirrored") and m.get("api_name")
    }
    core_modules = {e.zoho_module for e in _core_entities()}
    unread = sorted(
        str(m.get("label") or m["api_name"])
        for m in (modules_body if isinstance(modules_body, list) else [])
        if isinstance(m, dict) and m.get("api_name") and m.get("api_supported")
        and m.get("visible") is not False
        and m["api_name"] not in core_modules
        and f"{MODULE_KEY_PREFIX}{m['api_name']}" not in mirrored
    )

    last_good = good.finished_at if good is not None else None
    age = None
    if last_good is not None:
        age = max(int((due.db_now - last_good).total_seconds()), 0)
    return CutoverPreview(
        sync=SyncFacts(last_good_at=last_good, age_seconds=age, running=due.running),
        records=records,
        other=other,
        unmatched_owners=[OwnerRow(name=str(r.name), records=int(r.records)) for r in owners],
        unmatched_owner_names=int(names.names or 0) if names is not None else 0,
        labels_kept=[
            LabelRow(key=k, label=v.label, singular=v.singular_label)
            for k, v in sorted(labels.items())
        ],
        not_complete=[r.label for r in records if r.state != "complete"],
        stays_in_zoho=["Attachments", *unread],
    )


def _core_entities() -> list[Any]:
    from gateway.routes.crm.core import ENTITIES

    return [e for e in ENTITIES.values() if e.zoho_module]


@router.get(
    "/cutover/preview", response_model=CutoverPreview, dependencies=_GUARDS,
)
async def get_cutover_preview(
    user: UserContext = Depends(get_current_user),
) -> CutoverPreview:
    """What moves to Metorite when the org leaves Zoho, and what does not."""
    if not user.organization_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: you are in no organization.",
        )
    org = str(user.organization_id)
    async with _tenant_session() as db:
        settings = await read_settings(db, org)
        if getattr(settings, "mode", None) != "mirror":
            raise HTTPException(status_code=409, detail={
                "code": "not_mirror",
                "message": "The cutover is for a CRM that mirrors Zoho.",
            })
        return await preview(db, org)
