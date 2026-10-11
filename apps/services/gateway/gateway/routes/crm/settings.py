"""CRM · settings — the mode of the organization and who may open the CRM.

Spec: ``project-docs/specs/crm_platform.md`` §3.1, §3.4, §10 items 5 and 6,
§16 Q3. WS-53 CRM-T5.

    GET    /crm/settings
    PATCH  /crm/settings

**Admin only.** Both routes need ``admin:access:manage`` (§10 item 5: only an
org admin changes the mode). The router's access rule passes an admin too, so
an admin who sets ``access = 'groups'`` with no group of their own still
reaches this page.

**The mode.** CRM-T5 allows ONE change of mode: from NULL (not chosen) to
``native``. The same transaction seeds the organization's stages and lost
reasons (``seed.seed_native_pipeline``). Each other change of mode is a 422
until CRM-Z2 builds the connection that Mirror mode needs. A ``mode`` equal to
the stored one is not a change, so it is a 200 that writes no seed.

**The access rule.** ``access`` is ``admins``, ``everyone`` or ``groups``.
Each entry of ``access_groups`` goes through the Projects grant vocabulary
(``routes/projects/core.validate_grant_subject``, D12), and only a
``group:<slug>`` subject is accepted. An email or ``org`` is a 422: the rule
names groups, and ``everyone`` already says "the whole organization".

**No tenant in the body.** The organization is ``UserContext.organization_id``
from the server (R5, R11). The response does not name it either.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.crm.access import (
    ACCESS_MODES,
    ADMIN_PERMISSION,
    DEFAULT_ACCESS,
    read_settings,
)
from gateway.routes.crm.core import _tenant_session, router
from gateway.routes.crm.seed import seed_native_pipeline
from gateway.routes.projects.core import validate_grant_subject
from pydantic import BaseModel
from sqlalchemy import text

#: The ``crm_settings.mode`` vocabulary, mirrored from the CHECK in
#: migration 242.
MODES: tuple[str, ...] = ("native", "mirror")

#: The one change of mode that CRM-T5 allows: (stored, requested).
ALLOWED_MODE_CHANGES: frozenset[tuple[str | None, str | None]] = frozenset({
    (None, "native"),
})

_UPSERT_SQL = (
    "INSERT INTO crm_settings "
    "(organization_id, mode, access, access_groups, updated_by, updated_at) "
    "VALUES (CAST(:organization_id AS uuid), :mode, :access, :access_groups, "
    ":updated_by, now()) "
    "ON CONFLICT (organization_id) DO UPDATE SET "
    "mode = EXCLUDED.mode, access = EXCLUDED.access, "
    "access_groups = EXCLUDED.access_groups, "
    "updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at "
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
                f"{requested or 'not chosen'} yet. Only the first choice of "
                "'native' is open. Mirror mode comes with CRM-Z2."
            ),
        )
    return True


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
        merged = {
            "organization_id": org,
            "mode": values.get("mode", current.mode),
            "access": values.get("access", current.access),
            "access_groups": values.get("access_groups", current.access_groups),
            "updated_by": (user.email or "").strip().lower() or None,
        }
        row = (await db.execute(text(_UPSERT_SQL), merged)).first()
        if mode_changes and merged["mode"] == "native":
            await seed_native_pipeline(db, org)
        return _wire(row)
