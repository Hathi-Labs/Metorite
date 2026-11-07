"""The org default of the shell layout (WS-PKG PKG-17a).

Spec: ``project-docs/specs/package_host.md`` §9.1. An admin sets, for each
role, the default star set (``pins``) and the Home ``preset``. A member with
no answer of their own gets the default of their role.

**What "sidebar order" means here.** The star model of
``navigation_shell.md`` §3.2b keeps the order of ``nav.ts``. So the default
is a set of stars and a preset, and never an order (open question PKG-Q13).

**The store.** The document lives in the generic ``org_settings`` table under
the key ``shell_layout``. It needs no migration. ``load_org_setting`` and
``save_org_setting`` bind the tenant from the session (R5). The request names
no org. The shape is ``{"roles": {"<role>": {"preset": ..., "pins": [...]}}}``.

**Dark.** Both routes answer 404 while ``PACKAGE_HOST_ORGS`` leaves the org
out. The flag check runs before the permission check.

**The vocabulary stays single.** The panes and the presets come from
``routes/admin/me.py`` (read only), and the roles from ``acb_auth``.

**The resolver is pure.** :func:`resolve_layout` reads the member's stored
prefs and the org default. It does not know which panes the member holds.
The client's ``visibleSections`` subset rule hides a pane the member lacks.

Fences: ``tests/unit/test_shell_layout.py`` and
``tests/unit/test_shell_layout_r8.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from acb_auth import (
    ASSIGNABLE_SYSTEM_ROLES,
    UserContext,
    get_current_user,
    require_permission,
)
from acb_common import get_logger
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gateway.package_host.flags import package_host_allowed
from gateway.routes.admin.me import MAX_PINS, SHELL_PANES, SHELL_PRESETS
from gateway.routes.shell.search import router

_log = get_logger(__name__)

#: The key in ``org_settings``.
LAYOUT_KEY = "shell_layout"

#: The roles a person holds, the highest first. A custom role reads as
#: ``member`` on the client (``roleDefault``), so it has no entry here.
ROLE_ORDER: tuple[str, ...] = tuple(
    r for r in ("owner", "admin", "manager", "member", "guest")
    if r in ASSIGNABLE_SYSTEM_ROLES
)


class RoleLayout(BaseModel):
    """The default of one role. ``None`` leaves that part to the system."""

    model_config = ConfigDict(extra="forbid")

    preset: str | None = None
    pins: list[str] | None = Field(default=None, max_length=MAX_PINS)

    @field_validator("preset")
    @classmethod
    def _preset(cls, v: str | None) -> str | None:
        if v is not None and v not in SHELL_PRESETS:
            raise ValueError(f"unknown preset: {v[:40]}")
        return v

    @field_validator("pins")
    @classmethod
    def _pins(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        for pane in v:
            if pane not in SHELL_PANES:
                raise ValueError(f"unknown pane: {str(pane)[:40]}")
        if len(set(v)) != len(v):
            raise ValueError("a pane appears twice")
        return v


class ShellLayout(BaseModel):
    """The document: one :class:`RoleLayout` for each role that has a default."""

    model_config = ConfigDict(extra="forbid")

    roles: dict[str, RoleLayout] = Field(default_factory=dict, max_length=len(ROLE_ORDER))

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: dict[str, RoleLayout]) -> dict[str, RoleLayout]:
        for role in v:
            if role not in ROLE_ORDER:
                raise ValueError(f"unknown role: {str(role)[:40]}")
        return v


class LayoutResponse(ShellLayout):
    updatedBy: str = ""
    updatedAt: str = ""


def coerce_stored(raw: Any) -> ShellLayout:
    """A usable document from a stored blob. A bad role entry drops alone, so
    one bad entry never resets the other roles."""
    roles_raw = raw.get("roles") if isinstance(raw, dict) else None
    if not isinstance(roles_raw, dict):
        return ShellLayout()
    roles: dict[str, RoleLayout] = {}
    for role, entry in roles_raw.items():
        if role not in ROLE_ORDER:
            continue
        try:
            roles[role] = RoleLayout.model_validate(entry)
        except ValueError:
            continue
    return ShellLayout(roles=roles)


def resolve_layout(
    member_prefs: dict[str, Any] | None,
    org_default: ShellLayout | dict[str, Any] | None,
    role: str | Sequence[str] | None,
) -> dict[str, Any]:
    """The pins and the preset that a member starts from.

    * ``pins``: the member's own list when it is a list (an empty list
      counts). Else the pins of the default of the member's role. Else None.
    * ``preset``: the member's own preset when it is set. Else the preset of
      the default of the role. Else None, and the client uses ``roleDefault``.

    A member with many roles gets the highest role that has an entry. The
    function does not check that the member holds each pane.
    """
    prefs = member_prefs if isinstance(member_prefs, dict) else {}
    doc = org_default if isinstance(org_default, ShellLayout) else coerce_stored(org_default)
    held = {role.lower()} if isinstance(role, str) else {str(r).lower() for r in role or ()}
    entry = next((doc.roles[r] for r in ROLE_ORDER if r in held and r in doc.roles), None)

    own_pins = prefs.get("pins")
    pins = list(own_pins) if isinstance(own_pins, list) else (
        list(entry.pins) if entry and entry.pins is not None else None)
    own_preset = prefs.get("preset")
    preset = own_preset if isinstance(own_preset, str) and own_preset else (
        entry.preset if entry else None)
    return {"preset": preset, "pins": pins}


# ── The routes ─────────────────────────────────────────────────────────────


async def _flag_gate(user: Annotated[UserContext, Depends(get_current_user)]) -> None:
    """404 while ``PACKAGE_HOST_ORGS`` leaves the org out. It runs before the
    permission check, so an org off the list learns nothing from a 403."""
    if not package_host_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not found")


@router.get(
    "/layout",
    response_model=LayoutResponse,
    dependencies=[Depends(_flag_gate)],
    summary="The org default of the shell layout (PKG-17a)",
)
async def get_layout(
    _user: Annotated[UserContext, Depends(get_current_user)],
) -> LayoutResponse:
    """The stored default for each role, or the empty default. Any member."""
    from acb_common import load_org_setting

    try:
        raw = load_org_setting(LAYOUT_KEY, default={})
    except Exception:
        _log.warning("shell_layout.read_failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Layout unavailable") from None
    doc = coerce_stored(raw)
    blob = raw if isinstance(raw, dict) else {}
    return LayoutResponse(
        roles=doc.roles,
        updatedBy=str(blob.get("updatedBy") or ""),
        updatedAt=str(blob.get("updatedAt") or ""),
    )


@router.put(
    "/layout",
    response_model=LayoutResponse,
    dependencies=[Depends(_flag_gate), require_permission("admin:settings:manage")],
    summary="Set the org default of the shell layout (PKG-17a)",
)
async def put_layout(
    body: ShellLayout,
    user: Annotated[UserContext, Depends(get_current_user)],
) -> LayoutResponse:
    """Replace the whole document. The last write wins."""
    from acb_common import save_org_setting

    updated_at = datetime.now(UTC).isoformat()
    try:
        save_org_setting(
            LAYOUT_KEY,
            {
                "roles": body.model_dump()["roles"],
                "updatedBy": user.email or "",
                "updatedAt": updated_at,
            },
            updated_by=user.email or "",
        )
    except Exception:
        _log.warning("shell_layout.write_failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Layout not saved") from None
    _log.info("shell_layout.updated", roles=sorted(body.roles), by=user.email)
    return LayoutResponse(roles=body.roles, updatedBy=user.email or "", updatedAt=updated_at)
