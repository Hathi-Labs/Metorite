"""Tenant-plane organization purge — the destructive half of offboarding (CP-2g).

Spec: ``project-docs/specs/customer_console.md`` §CP-2g. The Console owns the
lifecycle (``customer_console.lifecycle``: ``deleted`` is reachable only from
``cancelled``, i.e. only after the export window) and the Console's
``POST /orgs/purge`` strips its own plane; THIS module destroys the tenant
plane — every row the organization owns in the tenant database.

## Why this is safe to be a single DELETE (measured 2026-08-24, scratch ladder)

Every tenant-scoped table carries ``organization_id REFERENCES organization
ON DELETE CASCADE`` (33 tables at build time — ``app_user``,
``org_membership``, ``org_role``/``org_group``, the ``pm_*`` family, the
``gtd_person_*`` family, ``tenant_placement``, ``provider_keys``,
``model_config``, ``mcp_servers``, ``plugins``), so deleting the
``organization`` row deletes the tenant plane.

⚠️ **The three ``crm_*`` tables that LOOK org-scoped are not** — measured the
hard way: ``crm_activities``/``crm_deals``/``crm_contacts`` carry an
``organization_id`` that references ``crm_organizations`` (the CRM's *company
records*), not the tenant ``organization``. The CRM family has **no tenancy
column at all yet** (the un-threaded MT-1j remainder), so there is nothing
per-tenant this purge could address there; ``_NOT_TENANT_SCOPED`` names the
exclusion and the R8 fence re-derives it from ``information_schema`` — the
day CRM threading lands, that fence goes red and this purge must grow the
CRM deletes.

What is deliberately KEPT:

- ``user_identity`` — global, email-keyed, shared across organizations. An
  identity whose last membership dies becomes the org-less sign-in (D51's
  join-vs-create chooser), which is exactly the state a fresh start needs.
- ``auth_email_otp_token`` — email-keyed and short-lived; it expires on its
  own schedule.

Unlike this module's neighbour ``promote_invited_member`` (best-effort, never
raises), **this function RAISES on failure** — a purge that half-happened must
be reported verbatim to the operator, never swallowed into a green tick.

R11: the org is looked up by slug server-side; nothing about tenancy is taken
from request headers. The caller contract (the operator BFF) is: call only for
an organization the Console already holds at ``status = 'deleted'``.
"""
from __future__ import annotations

from typing import Any

from acb_common import get_logger
from acb_common.db import get_session_factory as _get_session_factory
from acb_common.db import tenant_session

_log = get_logger("acb_auth.offboard")

_ORG_BY_SLUG_SQL = "SELECT id FROM organization WHERE slug = :slug"

#: Tables whose ``organization_id`` is NOT the tenant (it references
#: ``crm_organizations`` — a CRM company record). Named so the R8 fence can
#: assert the exclusion is exactly this set and nothing more: a NEW table
#: with a non-cascading tenant ``organization_id`` must fail the suite, not
#: slip into an exclusion written for a different fact.
_NOT_TENANT_SCOPED: frozenset[str] = frozenset(
    {"crm_activities", "crm_deals", "crm_contacts"}
)

_DELETE_ORG_SQL = "DELETE FROM organization WHERE id = CAST(:org_id AS uuid)"


async def purge_tenant_organization(*, slug: str) -> dict[str, Any]:
    """Destroy every tenant-plane row the organization owns. Idempotent.

    Returns a receipt: per-table counts for the explicit deletes, ``1`` for the
    organization row (whose FK cascade removes the other 33 tables' rows), or
    ``{"already_absent": True}`` when no such org exists — the retry arm, so a
    purge whose Console half failed can simply be run again.
    """
    slug = (slug or "").strip().lower()
    if not slug:
        raise ValueError("slug is required")

    from sqlalchemy import text

    factory = _get_session_factory()
    async with factory() as session:
        org_id = (
            await session.execute(text(_ORG_BY_SLUG_SQL), {"slug": slug})
        ).scalar_one_or_none()
    if org_id is None:
        return {"slug": slug, "already_absent": True, "deleted": {}}
    org_id = str(org_id)

    deleted: dict[str, int] = {}
    # One DELETE whose FK cascade is the purge. Bound through tenant_session
    # (the ONE GUC seam, R5) for uniformity — ``organization`` itself is
    # RLS-exempt, and referential cascades run below RLS either way.
    async with tenant_session(org_id) as session:
        result = await session.execute(text(_DELETE_ORG_SQL), {"org_id": org_id})
        deleted["organization"] = result.rowcount or 0

    _log.warning(
        "tenant_org_purged", slug=slug, organization_id=org_id, deleted=deleted
    )
    return {
        "slug": slug,
        "organization_id": org_id,
        "already_absent": False,
        "deleted": deleted,
        "cascaded": "every table with ON DELETE CASCADE to organization",
        "kept": [
            "user_identity",
            "auth_email_otp_token",
            "crm_* (no tenancy column yet — the MT-1j remainder)",
        ],
    }
