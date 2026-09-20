"""People Center · seed the directory from the org roster (H-124).

Spec: ``project-docs/specs/people_center_app.md`` §2 · PR #306 · H-124.

    POST /people/sync-members   → give every member a directory row

**What this repairs.** PR #306 made member provisioning write the
``gtd_people`` row that ``/people/me``, the org chart and the Projects
assignee picker all read. It fixed the cause and deliberately left the data:
every member created before 2026-09-20 still has no row, so they open *My
Profile* and read "An administrator can add you" — in an organization where
the person reading it is often the only administrator there is.

**Why this is a route and not a migration.** H-124 ordered the backfill after
H-104, and the blocker is real: ``gtd_people.organization_id`` is added by
``infra/postgres/generated/``, which ``apply_migrations.sh`` does not replay,
so nobody knows whether the column has reached production. A numbered
migration would have to write rows across every tenant, decide their
``organization_id`` without knowing the column exists, and satisfy a FORCE ROW
LEVEL SECURITY policy from a connection that binds no tenant. It is also
one-way (R6).

Running it here removes all four problems at once rather than solving them.
This executes inside ``_tenant_session``, so:

* the tenant is BOUND, which is what the generated policy's ``WITH CHECK``
  wants and what the column's ``DEFAULT current_setting('app.tenant_id')``
  reads — the row lands in the right organization by construction;
* it works identically whether or not the generated tenancy layer has been
  applied, so H-104 does not have to be settled first;
* it is repeatable and additive rather than one-way.

**It writes through ``ensure_directory_row`` and not its own INSERT.** That
function is the ONE place that knows the shape of a member's directory row —
including ``source_key``, whose absence aborted migration 148's replay on a
real database (PR #306). A second INSERT here would be a second answer to the
same question, and the two would drift on the next column. One round trip per
member is the right price for that: this is an administrator's one-off repair,
not a hot path.

⚠️ **Idempotent, and it reports what it did.** ``ensure_directory_row`` is
``ON CONFLICT DO NOTHING``, so pressing the button twice creates nothing the
second time. The response separates ``created`` from ``existing`` because "it
worked and there was nothing to do" and "it silently wrote nothing" are the
two outcomes a repair button must never render identically — the failure
CLAUDE.md §3 rule 8 is about.

⚠️ **Suspended and removed members get no row.** ``app_user.status`` has four
values and the directory's CHECK has a different four. Only ``active`` and
``invited`` map cleanly, and they are the members a directory is *for*. An
off-boarded person belongs in the directory as ``alumni``, which is D63's
seal-don't-inherit question (H-49) and is not settled — writing a guess here
would put a former colleague back in the assignee picker.
"""

from __future__ import annotations

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.routes.people.core import _tenant_session, can_manage_people, router
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.people.sync")

#: ``app_user.status`` → ``gtd_people.status``. The two vocabularies are
#: different tuples (``VALID_STATUSES`` in ``admin/members.py`` against
#: migration 148's CHECK), and only these two members belong in a directory.
#: Anything absent from this map is skipped, not guessed.
_STATUS_MAP = {"active": "active", "invited": "invited"}


class SyncResult(BaseModel):
    #: Members considered — the org's `active` and `invited` roster.
    members: int
    #: Directory rows this call inserted.
    created: int
    #: Members that already had one. `created + existing <= members`.
    existing: int
    #: Members skipped because the address is unusable, or because another
    #: tenant already holds it on migration 148's global index (H-125).
    skipped: int


@router.post("/sync-members", response_model=SyncResult)
async def sync_members(
    user: UserContext = Depends(get_current_user),
) -> SyncResult:
    """Give every member of the caller's organization a directory row.

    ``admin:members:manage`` — the same permission that adds a person by hand,
    because this is that act performed for the whole roster at once. Asked as
    a question rather than applied as a route dependency so the refusal reads
    the way the rest of this package's refusals do.
    """
    if not can_manage_people(user):
        raise HTTPException(
            status_code=403,
            detail="Adding people to the directory needs admin:members:manage.",
        )

    from gateway.routes.tasks.people import ensure_directory_row

    created = existing = skipped = 0
    async with _tenant_session() as db:
        # ⚠️ The tenant predicate is EXPLICIT, not left to RLS. `app_user`
        # carries a generated policy, but the generated layer is not on the
        # numbered ladder (H-104) and the scratch cluster has no FORCE RLS —
        # so relying on it would make this correct by accident on the only
        # database anybody tests against. Same idiom, and the same reason, as
        # `chart.py`'s `org_group` read. `NULLIF(…, '')` fails CLOSED: an
        # unbound GUC matches no row rather than every row.
        rows = (await db.execute(text(
            "SELECT email, display_name, status "
            "  FROM app_user "
            " WHERE organization_id = "
            "       CAST(NULLIF(current_setting('app.tenant_id', true), '') "
            "            AS uuid) "
            "   AND status = ANY(:statuses) "
            " ORDER BY lower(email)"),
            {"statuses": list(_STATUS_MAP)})).fetchall()

        for row in rows:
            wrote = await ensure_directory_row(
                db,
                email=row.email or "",
                display_name=row.display_name or "",
                status=_STATUS_MAP.get(row.status, "active"),
            )
            if wrote:
                created += 1
            elif (row.email or "").strip():
                # A row already existed, OR migration 148's GLOBAL
                # `lower(email)` index refused it to another tenant (H-125).
                # `ensure_directory_row` returns False for both and cannot
                # tell them apart from its rowcount, so ask.
                held = (await db.execute(text(
                    "SELECT 1 FROM gtd_people "
                    " WHERE lower(email) = lower(:email) LIMIT 1"),
                    {"email": row.email})).fetchone()
                if held:
                    existing += 1
                else:
                    skipped += 1
            else:
                skipped += 1

    _log.info(
        "people.sync_members",
        members=len(rows), created=created, existing=existing, skipped=skipped,
    )
    return SyncResult(
        members=len(rows), created=created, existing=existing, skipped=skipped,
    )
