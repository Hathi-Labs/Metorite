"""The admin view of the packages of an org (STU-J4, ``ai_studio.md`` §14 J4).

One read for a holder of the admin right: every package of the bound org with
its owner, its state, its live version, the count of its action calls and its
last error. A member gets the list of their own packages from the owner list
(``packages_store.list_summaries``), and nothing else leaves this module.

**The gate.** :func:`holds_admin_right` reads ``admin:members:manage`` from the
resolved access, as ``routes/admin/members.py`` does. The handler calls it, and
a member who lacks it gets the member list, not a 403. This module creates no
permission and never reads a right from a request.

**The answer holds no secret.** No manifest, no reason text, no record value
and no email but the owner. The last error holds the action id, the outcome and
the time, and never the ``reason`` of the audit row.

**What a "run" is.** An action call through the door writes one ``app_audit``
row with ``kind = 'action'`` and ``detail.action``. It is not a workflow run.
The UI says "Action calls". A create, an object sync and an install write
``kind = 'action'`` rows too, but with ``detail.op`` and no ``detail.action``.
They are not calls, so the count and the last error read only rows with
``detail.action``.

**Use.** Nothing writes a token or a cost for a package action, so no source
exists for the use of a package. The answer holds ``use: null``, and the UI
says "Not tracked yet".

**One statement, bounded.** One set-based statement reads at most
:data:`MAX_PACKAGES` packages. For each package, the count reads at most
:data:`MAX_COUNT` of the newest action rows. The last error looks inside the
same window of the newest :data:`MAX_COUNT` rows. Both walk
``idx_app_audit_app_kind_at`` (``app_id, kind, at DESC``) and filter the rows
with ``detail.action``. So an old error past the window shows as "No errors".
No N+1.

Shared installs ("Shared with them") are not here. They come after
``package_host.resolve.visible_installs`` merges (J2a).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

__all__ = ["ADMIN_RIGHT", "MAX_COUNT", "MAX_PACKAGES", "AdminRow",
           "holds_admin_right", "list_org_packages", "row_out"]

#: The admin right of the member roster. The same string that gates
#: ``routes/admin/members.py``.
ADMIN_RIGHT = "admin:members:manage"

#: The most packages one answer holds. A larger org gets ``truncated``.
MAX_PACKAGES = 500

#: The most action calls that the count reads for one package.
MAX_COUNT = 10_000

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: ``:cap`` is :data:`MAX_PACKAGES` + 1, so one extra row says "truncated".
_SQL = (
    "SELECT a.id::text, a.slug, a.name, a.description, a.owner_email, a.status, "
    "a.live_version, a.created_at, a.updated_at, a.manifest->>'id', "
    "(SELECT count(*) FROM (SELECT 1 FROM app_audit c WHERE c.app_id = a.id "
    f"AND c.organization_id = {_BOUND_ORG} AND c.kind = 'action' "
    "AND c.detail->>'action' IS NOT NULL ORDER BY c.at DESC, c.id DESC "
    "LIMIT :count_cap) s) AS calls, "
    "e.detail->>'action', e.detail->>'outcome', e.at "
    "FROM apps a LEFT JOIN LATERAL ("
    "SELECT w.detail, w.at FROM (SELECT x.id, x.detail, x.at FROM app_audit x "
    f"WHERE x.app_id = a.id AND x.organization_id = {_BOUND_ORG} AND x.kind = 'action' "
    "AND x.detail->>'action' IS NOT NULL "
    "ORDER BY x.at DESC, x.id DESC LIMIT :count_cap) w "
    "WHERE w.detail->>'outcome' IN ('failed', 'refused') "
    "ORDER BY w.at DESC, w.id DESC LIMIT 1) e ON true "
    f"WHERE a.organization_id = {_BOUND_ORG} AND a.format = 'manifest' "
    "ORDER BY a.updated_at DESC, a.id LIMIT :cap"
)


@dataclass(frozen=True)
class AdminRow:
    """One package of the org, with no manifest."""

    id: str
    slug: str
    name: str
    description: str
    owner_email: str
    status: str
    live_version: int | None
    created_at: datetime
    updated_at: datetime
    manifest_id: str
    action_calls: int
    last_error_action: str | None
    last_error_outcome: str | None
    last_error_at: datetime | None


def holds_admin_right(user: Any) -> bool:
    """True when the resolved access of *user* holds the admin right."""
    return bool(user.has_permission(ADMIN_RIGHT))


async def list_org_packages(db: Any) -> tuple[list[AdminRow], bool]:
    """Every package of the bound org, newest change first, and whether more
    exist than the cap. One statement. The caller opens a bound session."""
    rows = (await db.execute(text(_SQL), {
        "cap": MAX_PACKAGES + 1, "count_cap": MAX_COUNT})).all()
    out = [AdminRow(
        id=str(r[0]), slug=str(r[1]), name=str(r[2]), description=str(r[3] or ""),
        owner_email=str(r[4]), status=str(r[5]),
        live_version=None if r[6] is None else int(r[6]),
        created_at=r[7], updated_at=r[8], manifest_id=str(r[9] or ""),
        action_calls=int(r[10] or 0),
        last_error_action=None if r[11] is None else str(r[11])[:128],
        last_error_outcome=None if r[12] is None else str(r[12]),
        last_error_at=r[13]) for r in rows[:MAX_PACKAGES]]
    return out, len(rows) > MAX_PACKAGES


def row_out(row: AdminRow, member: str) -> dict[str, Any]:
    """The answer for one row. ``mine`` says the caller owns it. ``use`` is
    always null, because no source of use exists yet."""
    error = None
    if row.last_error_outcome is not None:
        error = {"action": row.last_error_action, "outcome": row.last_error_outcome,
                 "at": row.last_error_at}
    return {
        "id": row.id, "slug": row.slug, "name": row.name,
        "description": row.description, "status": row.status,
        "live_version": row.live_version, "manifest_id": row.manifest_id,
        "created_at": row.created_at, "updated_at": row.updated_at,
        "owner_email": row.owner_email,
        "mine": row.owner_email.strip().lower() == member.strip().lower(),
        "state": "draft" if row.live_version is None else "live",
        "action_calls": row.action_calls,
        "action_calls_capped": row.action_calls >= MAX_COUNT,
        "last_error": error,
        "use": None,
    }
