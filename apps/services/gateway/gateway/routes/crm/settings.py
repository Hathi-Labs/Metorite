"""CRM · settings — the mode of the organization and who may open the CRM.

Spec: ``project-docs/specs/crm_platform.md`` §3.1, §3.4, §10 items 5 and 6,
§16 Q3. WS-53 CRM-T5.

    GET    /crm/settings
    PATCH  /crm/settings

**Admin only.** Both routes need ``admin:access:manage`` (§10 item 5: only an
org admin changes the mode). The router's access rule passes an admin too, so
an admin who sets ``access = 'groups'`` with no group of their own still
reaches this page.

**The mode.** Two changes of mode are open. From NULL (not chosen) to
``native`` (CRM-T5): the same transaction seeds the organization's stages and
lost reasons (``seed.seed_native_pipeline``). From ``native`` or ``mirror``
back to NULL (CRM-U5b, "Change CRM setup"): allowed only with 0 rows in the
five record tables and no Zoho connection that is ``connected``, else a 409
(``crm_has_records``, ``crm_connected``). The reset clears ``mode`` and
``connection_id`` and deletes the stages and lost reasons, in the same
transaction, and keeps ``access`` and ``access_groups``. Each other change of
mode is a 422. Only the Zoho callback of ``connection.py`` (CRM-Z2) sets
``mirror``. A
``mode`` equal to the stored one is not a change, so it is a 200 that writes no
seed. The upsert writes only if the mode is still the one this request read,
else it answers 409.

**The access rule.** ``access`` is ``admins``, ``everyone`` or ``groups``.
Each entry of ``access_groups`` goes through the Projects grant vocabulary
(``routes/projects/core.validate_grant_subject``, D12), and only a
``group:<slug>`` subject is accepted. An email or ``org`` is a 422: the rule
names groups, and ``everyone`` already says "the whole organization".

**No tenant in the body.** The organization is ``UserContext.organization_id``
from the server (R5, R11). The response does not name it either.

**The start over (WS-53 CRM-X0, §19.11 item 6).** ``POST /crm/reset`` with
``{"confirm": "DELETE", "then": "none" | "native" | "mirror"}``. Admin only,
and any other ``confirm`` is a 422. It is the one route that removes CRM
records, and it does four things in this order:

1. ``then = "mirror"`` makes the Zoho consent URL first, so a missing Zoho
   client is a 503 before anything goes.
2. It disconnects Zoho through the ONE disconnect logic
   (``connection.disconnect_zoho``): a revoke at Zoho, and no token left for a
   run to read.
3. In ONE transaction, it takes the ``crm_settings`` row ``FOR UPDATE``. A
   sync page reads the mode ``FOR SHARE`` (``engine.MODE_SHARE_SQL``), so the
   lock waits for a page in flight, and the next page reads the new mode and
   stops. Then ``core.reset_crm_from_admin`` deletes the records, and the row
   gets the new mode with no connection. ``then = "native"`` seeds the Native
   stages in the same transaction.
4. One audit line through ``acb_audit``: the admin, the time and the counts.

``GET /crm/settings/counts`` reads the same counts for the hub, admin only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, require_permission
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.connections import store
from gateway.routes.crm.access import (
    ACCESS_MODES,
    ADMIN_PERMISSION,
    DEFAULT_ACCESS,
    read_settings,
)
from gateway.routes.crm.connection import (
    OWNING_APP,
    PROVIDER,
    consent_url_for,
    disconnect_zoho,
)
from gateway.routes.crm.core import (
    _tenant_session,
    count_records,
    reset_crm_from_admin,
    router,
)
from gateway.routes.crm.flags import crm_mirror_allowed
from gateway.routes.crm.seed import seed_native_pipeline
from gateway.routes.projects.core import validate_grant_subject
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_log = get_logger("crm.settings")

#: The ``crm_settings.mode`` vocabulary, mirrored from the CHECK in
#: migration 243.
MODES: tuple[str, ...] = ("native", "mirror")

#: The one change of mode that CRM-T5 allows: (stored, requested).
ALLOWED_MODE_CHANGES: frozenset[tuple[str | None, str | None]] = frozenset({
    (None, "native"),
    # CRM-U5b: Change CRM setup. Each needs 0 records and no live connection.
    ("native", None),
    ("mirror", None),
})

#: The record tables. Every row counts, native or mirrored. The generic record
#: store of the mirror (CRM-Q6a) counts too.
_RECORD_TABLES: tuple[str, ...] = (
    "crm_leads", "crm_deals", "crm_contacts", "crm_companies", "crm_activities",
    "crm_module_records",
)

#: Child first. Nothing references them with 0 records, but a status that a
#: record names is ``ON DELETE RESTRICT``, so the order is the safe one.
_RESET_DELETES: tuple[str, ...] = (
    # CRM-Q6a: the reset deletes the module cursors below, so a generic record
    # that lands after the count must not stay without its cursor.
    "crm_module_records",
    "crm_status_changes", "crm_pipeline_stages", "crm_pipelines",
    "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
    # The shape of the old Zoho account (CRM-Q1). A new setup reads its own.
    "crm_source_profiles",
    # Zoho sync state. A cursor with ``initial_done_at`` set would make a later
    # reconnect skip the first import (engine.py reads it), so it goes too.
    "crm_sync_cursors", "crm_sync_runs",
)

_HAS_RECORDS = {
    "code": "crm_has_records",
    "message": (
        "This CRM has records, so its setup cannot change. "
        "Remove the records first."
    ),
}

_UPSERT_SQL = (
    "INSERT INTO crm_settings "
    "(organization_id, mode, access, access_groups, updated_by, updated_at) "
    "VALUES (CAST(:organization_id AS uuid), :mode, :access, :access_groups, "
    ":updated_by, now()) "
    "ON CONFLICT (organization_id) DO UPDATE SET "
    "mode = EXCLUDED.mode, access = EXCLUDED.access, "
    "access_groups = EXCLUDED.access_groups, "
    "updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at "
    # The mode this request read. The Zoho callback can set 'mirror' between
    # the read and this write. Without the guard, this write puts the stale
    # mode back over it (CRM-Z2 review P2). No row back means the guard refused.
    "WHERE crm_settings.mode IS NOT DISTINCT FROM :expected_mode "
    "RETURNING organization_id, mode, access, access_groups, updated_by, "
    "updated_at"
)


class CrmSettingsModel(BaseModel):
    mode: str | None = None
    access: str = DEFAULT_ACCESS
    access_groups: list[str] = []
    updated_by: str | None = None
    updated_at: datetime | None = None


class CrmSettingsIn(BaseModel):
    mode: str | None = None
    access: str | None = None
    access_groups: list[str] | None = None


def _wire(row: Any) -> CrmSettingsModel:
    if row is None:
        return CrmSettingsModel()
    return CrmSettingsModel(
        mode=getattr(row, "mode", None),
        access=getattr(row, "access", None) or DEFAULT_ACCESS,
        access_groups=list(getattr(row, "access_groups", None) or []),
        updated_by=getattr(row, "updated_by", None),
        updated_at=getattr(row, "updated_at", None),
    )


def _organization(user: UserContext) -> str:
    """The caller's organization, from the server. None is a 403."""
    if not user.organization_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: you are in no organization.",
        )
    return str(user.organization_id)


def _check_mode(stored: str | None, requested: str | None) -> bool:
    """True when the write changes the mode. A refused change is a 422."""
    if requested == stored:
        return False
    if requested is not None and requested not in MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown CRM mode '{requested}'. One of: {list(MODES)}.",
        )
    if (stored, requested) not in ALLOWED_MODE_CHANGES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"The CRM mode cannot change from {stored or 'not chosen'} to "
                f"{requested or 'not chosen'} yet. Choose 'native' first, or "
                "change the setup back to not chosen. Mirror mode starts "
                "from Connect Zoho."
            ),
        )
    return True


async def _check_reset_allowed(db: Any, org: str) -> None:
    """409 unless the org has 0 records and no connected Zoho (CRM-U5b)."""
    for table in _RECORD_TABLES:
        found = (await db.execute(
            text(f"SELECT 1 FROM {table} LIMIT 1"),
        )).first()
        if found is not None:
            raise HTTPException(status_code=409, detail=dict(_HAS_RECORDS))
    connection = await store.get(db, org, PROVIDER, OWNING_APP)
    if connection is not None and connection.status == store.STATUS_CONNECTED:
        raise HTTPException(status_code=409, detail={
            "code": "crm_connected",
            "message": "Disconnect Zoho first, then change the setup.",
        })


async def _reset_setup(db: Any, org: str) -> None:
    """Clear the mode, the connection pointer and the seeded pipeline."""
    await db.execute(text(
        "UPDATE crm_settings SET connection_id = NULL "
        "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
    try:
        for table in _RESET_DELETES:
            await db.execute(text(
                f"DELETE FROM {table} "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
        await db.execute(text(
            "DELETE FROM crm_field_defs WHERE organization_id = "
            "CAST(:o AS uuid) AND source = 'zoho'"), {"o": org})
    except DBAPIError as exc:
        # A record written after the count names a stage: RESTRICT refuses the
        # delete. That is "has records", not a 500.
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code == "23503":
            raise HTTPException(status_code=409, detail=dict(_HAS_RECORDS)) from exc
        raise


def _check_access(access: str | None) -> None:
    if access is not None and access not in ACCESS_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown CRM access '{access}'. One of: {list(ACCESS_MODES)}.",
        )


def _clean_groups(groups: list[str]) -> list[str]:
    """Each subject through the D12 validator, ``group:<slug>`` only, no twins."""
    cleaned: list[str] = []
    for subject in groups:
        value = validate_grant_subject(subject)
        if not value.startswith("group:"):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{subject}' is not a group. CRM access names groups as "
                    "'group:<slug>'. Use access 'everyone' for the whole "
                    "organization."
                ),
            )
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


@router.get(
    "/settings", response_model=CrmSettingsModel,
    dependencies=[require_permission(ADMIN_PERMISSION)],
)
async def get_crm_settings(
    user: UserContext = Depends(get_current_user),
) -> CrmSettingsModel:
    """The organization's settings. No row reads as mode NULL, access admins."""
    org = _organization(user)
    async with _tenant_session() as db:
        return _wire(await read_settings(db, org))


@router.patch(
    "/settings", response_model=CrmSettingsModel,
    dependencies=[require_permission(ADMIN_PERMISSION)],
)
async def patch_crm_settings(
    payload: CrmSettingsIn, user: UserContext = Depends(get_current_user),
) -> CrmSettingsModel:
    """Change the mode, the access rule or its groups. See the module doc."""
    org = _organization(user)
    values = payload.model_dump(exclude_unset=True)
    if "access" in values:
        if values["access"] is None:
            raise HTTPException(
                status_code=422, detail="'access' cannot be null.",
            )
        _check_access(values["access"])
    if "access_groups" in values:
        values["access_groups"] = _clean_groups(values["access_groups"] or [])
    async with _tenant_session() as db:
        current = _wire(await read_settings(db, org))
        mode_changes = (
            "mode" in values and _check_mode(current.mode, values["mode"])
        )
        resetting = mode_changes and values["mode"] is None
        if resetting:
            # Serialise with any other write of this settings row, then count.
            await db.execute(text(
                "SELECT 1 FROM crm_settings "
                "WHERE organization_id = CAST(:o AS uuid) FOR UPDATE"), {"o": org})
            await _check_reset_allowed(db, org)
        merged = {
            "organization_id": org,
            "mode": values.get("mode", current.mode),
            "access": values.get("access", current.access),
            "access_groups": values.get("access_groups", current.access_groups),
            "updated_by": (user.email or "").strip().lower() or None,
            "expected_mode": current.mode,
        }
        row = (await db.execute(text(_UPSERT_SQL), merged)).first()
        if row is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The CRM mode changed while this request ran. Load the "
                    "settings again, then send the change again."
                ),
            )
        if mode_changes and merged["mode"] == "native":
            await seed_native_pipeline(db, org)
        if resetting:
            await _reset_setup(db, org)
        result = _wire(row)
    if resetting:
        # Drop the credential too: revoke best-effort, delete the blob. A
        # `connected` row never gets here (409 above).
        await disconnect_zoho(org)
    return result


# ── The start over (WS-53 CRM-X0) ───────────────────────────────────────────

#: The one word the admin types. The dialog shows it, and the route checks it.
RESET_CONFIRM = "DELETE"

#: ``SELECT ... FOR UPDATE`` on the org's settings row. A sync page holds the
#: same row ``FOR SHARE`` (``engine.MODE_SHARE_SQL``), so this waits for it.
_LOCK_SETTINGS_SQL = (
    "SELECT mode FROM crm_settings "
    "WHERE organization_id = CAST(:o AS uuid) FOR UPDATE"
)

_CLEAR_CONNECTION_SQL = (
    "UPDATE crm_settings SET connection_id = NULL "
    "WHERE organization_id = CAST(:o AS uuid)"
)


class ResetIn(BaseModel):
    """The body of ``POST /crm/reset``. No other field is allowed (R11)."""

    model_config = ConfigDict(extra="forbid")

    confirm: str
    then: Literal["none", "native", "mirror"] = "none"


class ResetOut(BaseModel):
    #: The records that went, by entity.
    removed: dict[str, int]
    #: The mode after the start over.
    mode: str | None = None
    #: ``then = "mirror"``: the Zoho consent to follow.
    consent_url: str | None = None


class CountsOut(BaseModel):
    counts: dict[str, int]
    total: int


def _audit_reset(user: UserContext, org: str, then: str, removed: dict[str, int]) -> None:
    """One audit line: the admin, the time (on the event) and the counts."""
    try:
        from acb_audit import AuditEvent, record

        record(AuditEvent(
            actor=f"user:{(user.email or '').strip().lower()}",
            action="crm.reset",
            target=f"crm:{org}",
            payload={"organization_id": org, "then": then, "removed": dict(removed)},
            organization_id=org,
        ))
    except Exception as exc:  # an audit backend that is down must not undo a reset
        _log.warning("crm.reset.audit_failed error=%s", type(exc).__name__)


@router.get(
    "/settings/counts", response_model=CountsOut,
    dependencies=[require_permission(ADMIN_PERMISSION)],
)
async def get_crm_counts(user: UserContext = Depends(get_current_user)) -> CountsOut:
    """The records of the org, by entity, for the setup hub and the dialog."""
    org = _organization(user)
    async with _tenant_session() as db:
        counts = await count_records(db, org)
    return CountsOut(counts=counts, total=sum(counts.values()))


@router.post(
    "/reset", response_model=ResetOut,
    dependencies=[require_permission(ADMIN_PERMISSION)],
)
async def reset_crm(
    payload: ResetIn, user: UserContext = Depends(get_current_user),
) -> ResetOut:
    """Start the CRM over. See the module doc for the order of the steps."""
    org = _organization(user)
    if payload.confirm != RESET_CONFIRM:
        raise HTTPException(
            status_code=422,
            detail=f"Type {RESET_CONFIRM} to confirm. Nothing was removed.",
        )
    consent_url: str | None = None
    if payload.then == "mirror":
        # Before anything goes: a Zoho that cannot start must not cost the records.
        if not crm_mirror_allowed(org):
            raise HTTPException(status_code=409, detail={
                "code": "mirror_unavailable",
                "message": "Zoho sync is not on for this organization. Nothing was removed.",
            })
        consent_url = consent_url_for(org, (user.email or "").strip().lower())
    # The sync stops: no token for a run, and a revoke at Zoho.
    await disconnect_zoho(org)
    mode = "native" if payload.then == "native" else None
    async with _tenant_session() as db:
        # Waits for a sync page in flight. A later page reads the new mode.
        await db.execute(text(_LOCK_SETTINGS_SQL), {"o": org})
        current = _wire(await read_settings(db, org))
        removed = await reset_crm_from_admin(db, org)
        row = (await db.execute(text(_UPSERT_SQL), {
            "organization_id": org,
            "mode": mode,
            "access": current.access,
            "access_groups": current.access_groups,
            "updated_by": (user.email or "").strip().lower() or None,
            "expected_mode": current.mode,
        })).first()
        if row is None:  # the lock makes this unreachable, and it stays a 409
            raise HTTPException(status_code=409, detail=(
                "The CRM mode changed while this request ran. Nothing was removed."
            ))
        await db.execute(text(_CLEAR_CONNECTION_SQL), {"o": org})
        if mode == "native":
            await seed_native_pipeline(db, org)
    _audit_reset(user, org, payload.then, removed)
    return ResetOut(removed=removed, mode=mode, consent_url=consent_url)
