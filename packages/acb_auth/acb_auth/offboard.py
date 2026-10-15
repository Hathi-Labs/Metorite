"""Tenant-plane organization purge — the destructive half of offboarding (CP-2g).

Spec: ``project-docs/specs/customer_console.md`` §CP-2g. The Console owns the
lifecycle (``customer_console.lifecycle``: ``deleted`` is reachable only from
``cancelled``, i.e. only after the export window) and the Console's
``POST /orgs/purge`` strips its own plane; THIS module destroys the tenant
plane — every row the organization owns **in the tenant-scoped schema**.

## Why this is safe to be a single DELETE (measured 2026-08-24, scratch ladder)

Every tenant-scoped table carries ``organization_id REFERENCES organization
ON DELETE CASCADE`` (33 tables at build time — ``app_user``,
``org_membership``, ``org_role``/``org_group``, the ``pm_*`` family, the
the ``people*`` family, ``tenant_placement``, ``provider_keys``,
``model_config``, ``mcp_servers``, ``plugins``), so deleting the
``organization`` row deletes the tenant plane.

**The ``crm_*`` family cascades too, since WS-53 CRM-T1 (2026-10-11).** Three
of its tables used to carry an ``organization_id`` that referenced the CRM's
*company records*, not the tenant. 144_crm.sql renamed that column to
``company_id``, and migration 241 gave all 13 ``crm_*`` tables the tenant
``organization_id`` with ``ON DELETE CASCADE``. So ``_NOT_TENANT_SCOPED`` is
empty, and the R8 fence re-derives that from ``information_schema``.

⚠️ **The purge deletes CRM activities, deals and leads first (WS-53 CRM-T4).**
``crm_leads.status_id`` and ``crm_deals.status_id`` are ``ON DELETE RESTRICT``,
and the one cascading DELETE reaches both the stage and the record that sits in
it. Measured 2026-10-11 on Postgres 16: the bare cascade did NOT fail, because
the RESTRICT check fires after the statement, when the cascade has already
removed the leads and deals. The purge still deletes ``crm_activities``,
``crm_deals`` and ``crm_leads`` for the org, in that order and before the
``organization`` delete, so it does not rest on that trigger order. No
migration is needed. ``test_org_purge_tenant.py`` seeds two orgs with CRM rows
in stages and fences the result.

What is deliberately KEPT:

- ``user_identity`` — global, email-keyed, shared across organizations. An
  identity whose last membership dies becomes the org-less sign-in (D51's
  join-vs-create chooser), which is exactly the state a fresh start needs.
- ``auth_email_otp_token`` — email-keyed and short-lived; it expires on its
  own schedule.

⚠️ **What this purge CANNOT reach — the honest boundary** (review round 1,
measured): ~119 of the tenant database's ~155 tables carry NO
``organization_id`` and no FK path to ``organization`` at all — the
un-threaded MT-1j remainder (chat, email, WhatsApp, meetings, GTD, apps,
workflows), where rows are keyed by email or by nothing. Their data is
not attributable to a tenant, so no per-tenant delete can exist for it until
threading lands; deleting by member email would be WRONG (an address can
belong to a future org). The operator-facing copy states this instead of
promising "everything", and WS-29's board row carries it as the standing
finding it already is.

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

#: Tables whose ``organization_id`` is NOT the tenant. EMPTY since WS-53
#: CRM-T1: the three CRM tables that were here now call the company
#: ``company_id``. Kept, so the R8 fence can still assert the exclusion is
#: exactly this set and nothing more: a NEW table with a non-cascading
#: ``organization_id`` must fail the suite, not slip into an exclusion written
#: for a different fact.
_NOT_TENANT_SCOPED: frozenset[str] = frozenset()

_DELETE_ORG_SQL = "DELETE FROM organization WHERE id = CAST(:org_id AS uuid)"

#: Deleted first, in this order, inside the tenant session. Children before the
#: records they point at, and records before the stages their ``status_id``
#: RESTRICTs. Every name is a literal of ours, never caller text.
_CRM_DELETES: tuple[tuple[str, str], ...] = (
    ("crm_activities", "DELETE FROM crm_activities "
     "WHERE organization_id = CAST(:org_id AS uuid)"),
    ("crm_deals", "DELETE FROM crm_deals "
     "WHERE organization_id = CAST(:org_id AS uuid)"),
    ("crm_leads", "DELETE FROM crm_leads "
     "WHERE organization_id = CAST(:org_id AS uuid)"),
)


async def organization_id_for_slug(slug: str) -> str | None:
    """The tenant org id for a slug, or None. The ONE unbound read here.

    ``organization`` is RLS-EXEMPT, and this read finds WHICH tenant to bind,
    so it cannot bind one first. It is the read that
    ``test_db_engine_seam.py::_FACTORY_OPEN_ALLOW`` declares for this file.
    Two operator acts use it: the org purge below and the member door in
    ``gateway/routes/operator.py``. One read for both keeps the count at one.

    The slug is compared byte for byte. The caller trims whitespace and never
    case-folds, for the reason :func:`purge_tenant_organization` records.
    """
    from sqlalchemy import text

    factory = _get_session_factory()
    async with factory() as session:
        org_id = (
            await session.execute(text(_ORG_BY_SLUG_SQL), {"slug": slug})
        ).scalar_one_or_none()
    return None if org_id is None else str(org_id)


async def purge_tenant_organization(*, slug: str) -> dict[str, Any]:
    """Destroy every tenant-plane row the organization owns. Idempotent.

    Returns a receipt: per-table counts for the explicit deletes, ``1`` for the
    organization row (whose FK cascade removes the other 33 tables' rows), or
    ``{"already_absent": True}`` when no such org exists — the retry arm, so a
    purge whose Console half failed can simply be run again.
    """
    # Whitespace-trim ONLY — never case-fold. The tenant lookup must be
    # byte-symmetric with what the Console's org list reported, or a
    # mixed-case slug turns the purge into a silent no-op that answers 200
    # (review round 1, P1: `.lower()` here + a mixed-case org meant "already
    # absent" — and the registry half then destroyed the only record of it).
    slug = (slug or "").strip()
    if not slug:
        raise ValueError("slug is required")

    from sqlalchemy import text

    org_id = await organization_id_for_slug(slug)
    if org_id is None:
        return {"slug": slug, "already_absent": True, "deleted": {}}

    deleted: dict[str, int] = {}
    # One DELETE whose FK cascade is the purge. Bound through tenant_session
    # (the ONE GUC seam, R5) for uniformity — ``organization`` itself is
    # RLS-exempt, and referential cascades run below RLS either way.
    async with tenant_session(org_id) as session:
        # Each DELETE names the org itself: a role that bypasses RLS (a superuser,
        # or any pre-promotion database) would otherwise delete every org's rows.
        # Only non-zero counts reach the
        # receipt, so an org with no CRM rows keeps the one-key receipt.
        for table, sql in _CRM_DELETES:
            removed = (
                await session.execute(text(sql), {"org_id": org_id})
            ).rowcount or 0
            if removed:
                deleted[table] = removed
        result = await session.execute(text(_DELETE_ORG_SQL), {"org_id": org_id})
        deleted["organization"] = result.rowcount or 0

    if deleted["organization"] == 0:
        # The row vanished between lookup and delete (a concurrent purge won).
        # Answering "deleted: 0, already_absent: false" would read as a purge
        # that destroyed nothing while claiming it ran — say which it was.
        return {"slug": slug, "already_absent": True, "deleted": {}}

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
            "un-threaded tables (chat/email/wa/meetings/gtd/apps/workflows "
            "— no tenancy column yet; the MT-1j remainder)",
        ],
    }
