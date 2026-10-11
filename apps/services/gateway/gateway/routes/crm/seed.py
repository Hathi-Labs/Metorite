"""The starting pipeline of an organization that picks Native mode (WS-53 CRM-T5).

Spec: ``project-docs/specs/crm_platform.md`` §4.2 step 2 and the CRM-T5 row
of §13.3.

An organization gets its own 6 deal stages, 5 lead stages and 6 lost reasons
in ONE write: the ``PATCH /crm/settings`` that sets ``mode`` from NULL to
``native`` (``routes/crm/settings.py``). It calls :func:`seed_native_pipeline`
in the same transaction as the mode change. Nothing seeds on a read, and
``provision_organization`` seeds nothing. A Mirror organization gets its stages
from the source system (CRM-Z4).

The three lists are the ``VALUES`` of ``infra/postgres/144_crm.sql``.
``tests/unit/test_crm_migration.py`` reads that file and fails if the two
disagree, so a stage renamed in one place is renamed in both or the build is
red.

Each INSERT carries ``ON CONFLICT (organization_id, name|label) DO NOTHING``,
the per-tenant keys of migration 241. A second call adds no row. The route
calls it only on the change from NULL to ``native``, which happens once, so a
stage that an admin deletes later does not come back.

The tenant comes from the caller's ``UserContext.organization_id``, which
``acb_auth`` resolves on the server. FORCE row level security then refuses a
row for any other tenant through ``WITH CHECK``.

This module is a leaf. It imports nothing from its siblings.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

#: ``crm_lead_statuses``: (name, color, position, type, is_default).
#: ``won`` for a lead means qualified (crm_app.md §3.7 step 4).
LEAD_STATUSES: tuple[tuple[str, str, int, str, bool], ...] = (
    ("New", "gray", 10, "open", True),
    ("Contacted", "blue", 20, "ongoing", False),
    ("Nurture", "amber", 30, "ongoing", False),
    ("Qualified", "green", 40, "won", False),
    ("Lost", "red", 50, "lost", False),
)

#: ``crm_deal_statuses``: (name, color, position, type, is_default, probability).
DEAL_STATUSES: tuple[tuple[str, str, int, str, bool, int], ...] = (
    ("Qualification", "gray", 10, "open", True, 10),
    ("Needs Analysis", "blue", 20, "ongoing", False, 25),
    ("Proposal", "violet", 30, "ongoing", False, 50),
    ("Negotiation", "amber", 40, "ongoing", False, 75),
    ("Closed Won", "green", 50, "won", False, 100),
    ("Closed Lost", "red", 60, "lost", False, 0),
)

#: ``crm_lost_reasons``: (label, position).
LOST_REASONS: tuple[tuple[str, int], ...] = (
    ("Price", 10),
    ("Competitor", 20),
    ("No budget", 30),
    ("No response", 40),
    ("Requirement dropped", 50),
    ("Other", 60),
)

_LEAD_SQL = (
    "INSERT INTO crm_lead_statuses "
    "(organization_id, name, color, position, type, is_default) "
    "VALUES (CAST(:organization_id AS uuid), :name, :color, :position, :type, "
    ":is_default) "
    "ON CONFLICT (organization_id, name) DO NOTHING RETURNING id"
)

_DEAL_SQL = (
    "INSERT INTO crm_deal_statuses "
    "(organization_id, name, color, position, type, is_default, probability) "
    "VALUES (CAST(:organization_id AS uuid), :name, :color, :position, :type, "
    ":is_default, :probability) "
    "ON CONFLICT (organization_id, name) DO NOTHING RETURNING id"
)

_REASON_SQL = (
    "INSERT INTO crm_lost_reasons (organization_id, label, position) "
    "VALUES (CAST(:organization_id AS uuid), :label, :position) "
    "ON CONFLICT (organization_id, label) DO NOTHING RETURNING id"
)


async def _insert(db: Any, sql: str, params: dict[str, Any]) -> int:
    """1 when the row went in, 0 when the per-tenant key already held it."""
    return len((await db.execute(text(sql), params)).fetchall())


async def seed_native_pipeline(db: Any, organization_id: str) -> dict[str, int]:
    """Insert the starting pipeline of *organization_id*. Return the new counts.

    The caller owns the transaction. One row per statement, so each row meets
    its own ``ON CONFLICT`` and the count of new rows is exact.
    """
    org = str(organization_id)
    added = {"lead_statuses": 0, "deal_statuses": 0, "lost_reasons": 0}
    for name, color, position, kind, is_default in LEAD_STATUSES:
        added["lead_statuses"] += await _insert(db, _LEAD_SQL, {
            "organization_id": org, "name": name, "color": color,
            "position": position, "type": kind, "is_default": is_default,
        })
    for name, color, position, kind, is_default, probability in DEAL_STATUSES:
        added["deal_statuses"] += await _insert(db, _DEAL_SQL, {
            "organization_id": org, "name": name, "color": color,
            "position": position, "type": kind, "is_default": is_default,
            "probability": probability,
        })
    for label, position in LOST_REASONS:
        added["lost_reasons"] += await _insert(db, _REASON_SQL, {
            "organization_id": org, "label": label, "position": position,
        })
    return added
