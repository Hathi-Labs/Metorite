"""Org administration routes — shared kernel.

DB access, the org lookup, and the invariants every write path in this package
has to respect. Spec: ``project-docs/specs/org_access_control.md``.

The four invariants, stated once here because they are the difference between
an access model and an outage:

1. **The org always has an owner.** Every path that could remove the last
   `owner` assignment refuses. A deployment with no owner is one where nobody
   can grant access back, and the only recovery is SQL on the production box.
2. **Nobody grants above themselves.** Role assignment is checked against the
   caller's own lowest rank, so an `admin` cannot mint an `owner`.
3. **System roles are immutable.** The five seeded roles are the floor the
   bootstrap path depends on; custom roles are where admins express local
   policy.
4. **Nobody locks themselves out**, by any of the **four** doors that reach it.
   A caller may not put their own row into any status other than `active`
   (:func:`assert_not_self_lockout`, called by ``PATCH`` and ``DELETE`` on
   ``/admin/members/{email}`` **and by ``DELETE /members/{email}/purge``**),
   and may not strip their own row of ``admin:members:manage``
   (:func:`assert_not_self_demotion`, called by ``PUT /members/{email}/roles``)
   — that door leaves `status` alone and takes the *permission* instead,
   reaching the same lockout without touching the column the others guard.
   Invariant 1 is not a substitute for any of them: it refuses only while the
   caller is the *last* owner, which is a coincidence of org size, not a rule.

   ⚠️ A **fifth** route reaches the same outcome and guards it with its own
   inline comparison: ``PUT /members/{email}/overrides`` refuses a caller who
   denies themselves ``admin:access:manage``. It is not routed through the
   helper below and is therefore invisible to the door-enumeration test.
   Recorded, not fixed here — see ``colleague_onboarding.md`` §2 Step 5.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from acb_auth import (
    UserContext,
    get_current_user,
    invalidate_access,
    permission_matches,
)

# WS-29 H6 (DARK): the identity-shadow mirror lives in `acb_auth.access` (NOT
# `console_resolve`, whose importer set is capped by a farmable-seat fence). It is
# imported and called by `provision_member`'s CALLERS post-commit, never here — see
# the note in `provision_member` for why the mirror moved out of this transaction.
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException

# The shared gateway engine (BO-10) — see the DB section below for why the
# names are re-exported rather than imported at each call site.
#
# H2 (MT-1c): `_tenant_session` IS `acb_common.db.tenant_session` — the
# tenant-bound context manager every request handler in this package now
# acquires its session through. The tenant is bound centrally from the
# caller's `app_user` row (`_with_resolved_access` → `bind_tenant`), which is
# the SAME source `get_org_id` below resolves — S1-1's explicit
# `organization_id` predicates stay as defense in depth on top of it.
# `get_db` (unbound) remains re-exported ONLY for `routes/agent_skills.py`,
# which reaches the seam through this module and is not this package's to
# convert.
from gateway.db import get_db  # noqa: F401
from gateway.db import get_session_factory as _get_session_factory  # noqa: F401
from gateway.db import tenant_session as _tenant_session  # noqa: F401

# The ONE answer to "which tenant is this caller" (WS-29b, D-MT-1 (a)). Imported
# rather than re-derived: two implementations of that question is exactly how
# they drift, and the one that drifts is the one nobody re-reads.
# `routes/projects/core.py` owns it because Projects needed it first; the
# question it answers belongs to no package.
from gateway.routes.projects.core import NO_ORGANIZATION, resolve_organization_id
from sqlalchemy import text

_log = get_logger("gateway.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

#: Never assignable to a person — it is the internal service principal.
NON_ASSIGNABLE_ROLES = frozenset({"agent_service"})

#: ⚠️ **There is deliberately no ``DEFAULT_ORG_SLUG`` here any more.**
#:
#: It used to be the whole of this package's tenant model: ``get_org_id`` read
#: ``WHERE slug = 'default'`` and never consulted the caller, so every admin
#: read and every admin WRITE — invite, role grant, group membership, permission
#: override — landed in the `default` organization no matter who asked
#: (``multi_tenancy_leak_audit.md`` S1-1). A caller the permission system had
#: correctly authorised for *their own* org was silently redirected into
#: somebody else's access control.
#:
#: It is not kept as a fallback, because a fallback IS the bug: the day the
#: caller lookup returns nothing is exactly the day the slug would hand them
#: `default` again. Absence fails closed here (403), which is the whole point.
#:
#: The slug survives in precisely two places, both **provisioning, never
#: resolution**, and both outside the request path:
#:
#: * ``infra/postgres/130_org_access_control.sql`` seeds the single row.
#: * ``acb_auth.access._BOOTSTRAP_OWNER_SQL`` — first-run ownership recovery at
#:   gateway startup, which has no caller to derive a tenant from because its
#:   entire reason for existing is that there are no members yet.
#:
#: If a second organization is ever provisioned, neither of those is on a path a
#: request can reach, so neither can answer "which tenant is this request".


# ── DB (the one shared gateway engine — gateway/db.py, BO-10) ────────────────
#
# This package used to build its own engine here, with its own pool of 5+10.
# It now has none: `_tenant_session` and `_get_session_factory` at the top of
# this module are re-exports of the shared seam, so every
# `from ._common import _tenant_session` in this package keeps working with a
# single pool behind it.
#
# The re-export is deliberate rather than pointing each caller at `gateway.db`.
# Sibling modules import the name from here and the tests monkeypatch it *on the
# sibling* (`monkeypatch.setattr(groups, "_tenant_session", ...)`), so the name
# has to stay resolvable through this module for both to keep working.


# ── Auth gate ───────────────────────────────────────────────────────────────

async def require_admin_user(
    user: UserContext = Depends(get_current_user),
) -> UserContext:
    """Any admin surface: 401 anonymous, 403 without ``admin:members:read``.

    Read access is the floor for the whole package; individual write routes
    add their own narrower `require_permission`.
    """
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not user.has_permission("admin:members:read"):
        raise HTTPException(
            status_code=403, detail="Forbidden: missing permission 'admin:members:read'."
        )
    return user


# ── Org + role lookups ──────────────────────────────────────────────────────

#: Asked ONLY on the failure path of :func:`get_org_id`, to tell an operator
#: apart from a stranger. A deployment where migration 130 never ran and a
#: caller who simply has no ``app_user`` row are the same absence to the lookup
#: above, and they need opposite answers: one is "apply the migration", the
#: other is "your account is not set up". Both refuse.
_ANY_ORGANIZATION_SQL = "SELECT 1 FROM organization LIMIT 1"


async def get_org_id(db: Any, user: UserContext) -> str:
    """The **caller's** organization id, or a refusal. Never a slug.

    ⚠️ **This function used to ignore its caller entirely.** It read
    ``WHERE slug = 'default'``, so every route in this package — the roster,
    invites, role grants, group membership, permission overrides, the access
    queue and ``/auth/me`` — operated on one hard-coded organization regardless
    of who asked. With a second tenant that is not a read leak, it is an
    unbounded **write into another tenant's access control** by a caller the
    permission system correctly authorised for their own
    (``multi_tenancy_leak_audit.md`` S1-1).

    **R3: the tenant comes from the authenticated context, never from a request
    parameter.** ``user.email`` is asserted by the identity seam
    (``acb_auth.deps``, which refuses a bare ``X-User-Email`` when an internal
    token is configured), and D-MT-1 (a) makes that email a single-row answer:
    ``app_user.email`` is globally UNIQUE, so one person is in exactly one
    organization and nothing the caller sends can widen it. There is no
    ``org`` argument on any route in this package, and there must not be.

    **Fail closed, and say which failure it is.** A caller with no
    ``app_user`` row — an unprovisioned address, or the ``system:internal``
    service principal, which holds ``*`` and belongs to no organization
    (``deps.py`` branch 1b) — gets **403** carrying the same
    :data:`~gateway.routes.projects.core.NO_ORGANIZATION` message the Projects
    write path already uses for exactly this caller. 403 and not 404, for the
    reason ``projects.core.require_organization`` states: this says nothing
    about what exists, it says the caller's own account is not attached, and a
    404 would send somebody hunting for a record that was never created. R5's
    "404, never 403" governs *records* — a member, a role, a group, all of
    which now answer 404 across a tenant boundary.

    The **503** is kept for the one case it was actually written for: a
    deployment with no ``organization`` row at all. That is an operator fault
    with an actionable fix, and collapsing it into the 403 would send an
    operator looking at the wrong account. It costs one extra query on a path
    that already refuses.
    """
    org_id = await resolve_organization_id(db, user.email or "")
    if org_id:
        return org_id
    provisioned = (await db.execute(text(_ANY_ORGANIZATION_SQL))).first()
    if provisioned is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Organization not provisioned. Apply "
                "infra/postgres/130_org_access_control.sql."
            ),
        )
    raise HTTPException(status_code=403, detail=NO_ORGANIZATION)


async def find_member(db: Any, org_id: str, email: str) -> dict[str, Any] | None:
    """Fetch one member row by email **within one organization**, or ``None``.

    The non-raising half of :func:`get_member`. Provisioning needs it: it has
    to know whether the address already has a row *before* it writes one, and
    a 404 is the wrong answer there — an absent row is the normal case.

    ⚠️ **The ``organization_id`` predicate is half of S1-1's fix, not a
    tidy-up.** Making :func:`get_org_id` caller-derived scopes the queries that
    take an org id; it does nothing for the ones that reach a person by
    address, and every member-targeted route in this package goes through here
    — ``PATCH``/``DELETE``/``purge``/``roles``/``overrides`` on
    ``/admin/members/{email}``, both group-membership writes, and approve. A
    caller-derived org with an unscoped member lookup is the same cross-tenant
    write with an extra query in front of it.

    Case-insensitive on the address (R10) and exact on the tenant.
    """
    row = (
        await db.execute(
            text(
                "SELECT id::text AS id, email, display_name, avatar_url, status, "
                "       role AS legacy_role, invited_by, invited_at, joined_at, "
                "       last_login_at, last_active_at, created_at "
                "  FROM app_user WHERE lower(email) = :email "
                "   AND organization_id = CAST(:org AS uuid)"
            ),
            {"email": email.lower().strip(), "org": org_id},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def get_member(db: Any, org_id: str, email: str) -> dict[str, Any]:
    """Fetch one member row by email within one organization, or 404.

    **404, never 403** (R5): a member of another tenant and an address nobody
    has ever heard of must be the same answer, or the status code becomes an
    oracle for who exists in the deployment.
    """
    row = await find_member(db, org_id, email)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No member '{email}'.")
    return row


async def get_role(db: Any, org_id: str, slug: str) -> dict[str, Any]:
    """Fetch one role row by slug, or 404."""
    row = (
        await db.execute(
            text(
                "SELECT id::text AS id, slug, display_name, description, "
                "       is_system, rank "
                "  FROM org_role WHERE organization_id = CAST(:org AS uuid) AND slug = :slug"
            ),
            {"org": org_id, "slug": slug},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No role '{slug}'.")
    return dict(row)


async def roles_for_user(db: Any, user_id: str) -> list[str]:
    rows = (
        await db.execute(
            text(
                "SELECT r.slug FROM user_role ur "
                "  JOIN org_role r ON r.id = ur.role_id "
                " WHERE ur.user_id = CAST(:uid AS uuid) ORDER BY r.rank"
            ),
            {"uid": user_id},
        )
    ).scalars().all()
    return list(rows)


async def caller_rank(db: Any, org_id: str, user: UserContext) -> int:
    """The caller's most-privileged rank (lower = more privileged).

    The internal service principal and anyone holding ``*`` rank as owner;
    everyone else is bounded by the roles actually on their row.
    """
    if user.has_permission("*"):
        return 0
    rows = (
        await db.execute(
            text(
                "SELECT MIN(r.rank) AS rank "
                "  FROM app_user u "
                "  JOIN user_role ur ON ur.user_id = u.id "
                "  JOIN org_role r   ON r.id = ur.role_id "
                " WHERE lower(u.email) = :email AND r.organization_id = CAST(:org AS uuid)"
            ),
            {"email": (user.email or "").lower(), "org": org_id},
        )
    ).mappings().first()
    rank = rows["rank"] if rows else None
    return int(rank) if rank is not None else 1000


async def owner_count(db: Any, org_id: str, *, excluding_user_id: str | None = None) -> int:
    """How many active members would still hold `owner`."""
    sql = (
        "SELECT count(*) FROM user_role ur "
        "  JOIN org_role r ON r.id = ur.role_id "
        "  JOIN app_user u ON u.id = ur.user_id "
        " WHERE r.organization_id = CAST(:org AS uuid) AND r.slug = 'owner' "
        "   AND u.status = 'active'"
    )
    params: dict[str, Any] = {"org": org_id}
    if excluding_user_id:
        sql += " AND u.id <> CAST(:uid AS uuid)"
        params["uid"] = excluding_user_id
    return int((await db.execute(text(sql), params)).scalar() or 0)


async def assert_owner_survives(
    db: Any, org_id: str, *, excluding_user_id: str
) -> None:
    """Refuse a change that would leave the organization ownerless."""
    if await owner_count(db, org_id, excluding_user_id=excluding_user_id) == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "This would leave the organization with no owner. "
                "Assign another owner first."
            ),
        )


#: The outcome name a purge passes to :func:`assert_not_self_lockout`. It is
#: deliberately **not** a member of ``members.VALID_STATUSES``: a purge does not
#: set ``app_user.status``, it deletes the row. The helper's rule is "anything
#: that is not `active`", so a door that reaches ``is_active = False`` by
#: destroying the row is covered by the same sentence as one that writes a
#: column — which is the entire reason the rule was written that way and not as
#: a list of destructive statuses. Held as a constant so the route and the
#: wording table below cannot drift to two different spellings.
PURGE_OUTCOME = "purged"

#: How the refusal reads back to the caller who asked for it. Only the outcomes
#: a human actually chooses are worded; anything else falls through to the
#: generic message, because the RULE below is ``status != "active"`` and not
#: this table — a fifth `app_user.status` is guarded the day it is added rather
#: than the day somebody remembers to list it here.
_SELF_LOCKOUT_WORDING = {
    "removed": "You cannot remove yourself.",
    "suspended": "You cannot suspend yourself.",
    PURGE_OUTCOME: "You cannot permanently delete yourself.",
}

_SELF_LOCKOUT_ADVICE = "Ask another owner or admin to do it."


def assert_not_self_lockout(
    admin: UserContext, member: dict[str, Any], *, status: str | None
) -> None:
    """Refuse a caller who is taking their own access away — invariant 4.

    **One rule, two call sites.** ``PATCH /admin/members/{email}`` with a
    status and ``DELETE /admin/members/{email}`` reach the same
    ``is_active = False`` by the same mechanism, and the version of this check
    that lived *inside* the DELETE handler alone is exactly how the two doors
    came to disagree: the PATCH had no self-check at all, and its only
    invariant — :func:`assert_owner_survives` — refuses self-suspension solely
    by coincidence in an org that has one owner. Add a second owner (which §2
    Step 2 of ``colleague_onboarding.md`` exists to do) and either owner could
    suspend themselves out of the admin surface in one click, with
    ``admin:members:manage`` the floor for getting back in.

    The rule is stated as **"any status that is not `active`"**, never as a list
    of destructive ones: ``EffectiveAccess.is_active`` is ``status ==
    "active"`` exactly, so `invited` locks the caller out just as `suspended`
    does. ``status=None`` — a display-name-only patch — changes no access and
    passes.

    Case-insensitive on both sides. ``app_user.email`` and ``X-User-Email``
    come from the same directory but not necessarily with the same casing, and
    an IdP that returns a UPN cased differently between sessions must not
    silently switch the guard off (the property ``core.load_owned_meeting``
    preserves for Notes). An empty address on either side matches nothing — a
    caller with no identity of their own is not everybody.
    """
    if status is None or status == "active":
        return
    me = (admin.email or "").strip().lower()
    them = (member.get("email") or "").strip().lower()
    if not me or not them or me != them:
        return
    wording = _SELF_LOCKOUT_WORDING.get(
        status,
        f"You cannot set your own membership to '{status}' — "
        "it would take away your own access.",
    )
    raise HTTPException(status_code=409, detail=f"{wording} {_SELF_LOCKOUT_ADVICE}")


#: The capability a caller needs to undo whatever they just did to themselves.
#: It is the gate on all three doors — ``PATCH``, ``DELETE`` and ``PUT
#: …/roles`` — so a caller who no longer holds it cannot restore their own row
#: by any route.
SELF_RECOVERY_PERMISSION = "admin:members:manage"

#: Read as the third door's fence. Held as a constant for the same reason
#: ``_PROVISION_MEMBER_SQL`` is: the test fake answers it from a Python mapping,
#: and a mirror can only ever agree with itself.
_ROLE_PERMISSIONS_SQL = (
    "SELECT permission FROM org_role_permission "
    " WHERE role_id = ANY(CAST(:ids AS uuid[]))"
)


async def assert_not_self_demotion(
    db: Any,
    admin: UserContext,
    member: dict[str, Any],
    *,
    role_ids: list[tuple[str, str]],
) -> None:
    """Invariant 4's **third door**: ``PUT /admin/members/{email}/roles``.

    :func:`assert_not_self_lockout` closes the two doors that set
    ``status``. This one closes the door that leaves the status alone and
    takes the *permission* away instead — the outcome is the same lockout,
    reached by a route that never touches ``app_user.status``.

    Measured before this existed, in the two-owner world that
    ``colleague_onboarding.md`` §2 Step 2 exists to create: an owner calling
    ``PUT /admin/members/<their own address>/roles {"roles": ["member"]}`` was
    **accepted**. They kept `status = 'active'` and lost
    ``admin:members:manage``, which is the floor for undoing it — so recovery
    was the other owner, or hand-run SQL. With a single owner the call
    happened to be refused, but by :func:`assert_owner_survives` and its
    wording, i.e. by the same coincidence invariant 4 was written to stop
    relying on.

    ``{"roles": []}`` is **not** the sequence — a separate check rejects an
    empty set. The reachable one is a self-demotion to any role that does not
    carry the recovery permission.

    Checked against the permissions the *new* roles actually grant rather than
    against a list of role slugs, because a custom role may carry
    ``admin:members:manage`` and a seeded-slug allowlist would refuse a
    legitimate self-edit. Wildcards resolve through
    :func:`acb_auth.permission_matches`, so an owner holding ``*`` passes.
    """
    me = (admin.email or "").strip().lower()
    them = (member.get("email") or "").strip().lower()
    if not me or not them or me != them:
        return

    ids = [rid for rid, _slug in role_ids]
    if ids:
        granted = (
            await db.execute(text(_ROLE_PERMISSIONS_SQL), {"ids": ids})
        ).scalars().all()
        if any(
            permission_matches(str(p), SELF_RECOVERY_PERMISSION)
            for p in granted
        ):
            return

    raise HTTPException(
        status_code=409,
        detail=(
            "You cannot take your own member-management rights away — you "
            "would not be able to give them back. " + _SELF_LOCKOUT_ADVICE
        ),
    )


# ── Provisioning: the one path from an address to a member ──────────────────

def _iso(value: Any) -> str:
    """Timestamps on the wire are UTC ISO-8601, and absence is ``""``."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return "" if value is None else str(value)


async def resolve_assignable_roles(
    db: Any, org_id: str, slugs: list[str], admin: UserContext
) -> list[tuple[str, str]]:
    """Validate role slugs and return ``[(role_id, slug)]``.

    Enforces invariant 2: an admin cannot assign a role more privileged than
    their own, so `admin` cannot mint an `owner` and escalate laterally. Lives
    in the leaf because it IS one of this package's invariants — every path
    that hands somebody a role goes through it.
    """
    if not slugs:
        raise HTTPException(status_code=400, detail="At least one role is required.")

    rows = (
        await db.execute(
            text(
                "SELECT id::text AS id, slug, rank FROM org_role "
                " WHERE organization_id = CAST(:org AS uuid) AND slug = ANY(:slugs)"
            ),
            {"org": org_id, "slugs": list(slugs)},
        )
    ).mappings().all()

    found = {r["slug"]: r for r in rows}
    missing = [s for s in slugs if s not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown role(s): {missing}.")

    blocked = [s for s in slugs if s in NON_ASSIGNABLE_ROLES]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=f"Role(s) {blocked} are internal and cannot be assigned to people.",
        )

    my_rank = await caller_rank(db, org_id, admin)
    too_high = [s for s in slugs if int(found[s]["rank"]) < my_rank]
    if too_high:
        raise HTTPException(
            status_code=403,
            detail=f"You cannot assign role(s) {too_high} — they outrank you.",
        )
    return [(found[s]["id"], s) for s in slugs]


async def set_roles(
    db: Any, user_id: str, role_ids: list[tuple[str, str]], assigned_by: str | None
) -> None:
    """Replace a member's role assignments wholesale."""
    await db.execute(
        text("DELETE FROM user_role WHERE user_id = CAST(:uid AS uuid)"),
        {"uid": user_id},
    )
    for role_id, _slug in role_ids:
        await db.execute(
            text(
                # H6 slice 4 (D48) DUAL-WRITE: shadow `user_identity_id` via the
                # lower(email) bridge (RBAC.user_id → app_user → user_identity) so
                # migration 184's column stays current. app_user.id stays the
                # authoritative key; this only READS user_identity. NULL only where
                # no identity exists (backfill 184 / CONTRACT slice reconcile).
                "INSERT INTO user_role (user_id, role_id, assigned_by, user_identity_id) "
                "VALUES (CAST(:uid AS uuid), CAST(:rid AS uuid), :by, "
                "        (SELECT ui.id FROM user_identity ui "
                "           JOIN app_user au ON lower(au.email) = lower(ui.email) "
                "          WHERE au.id = CAST(:uid AS uuid))) "
                "ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id, "rid": role_id, "by": assigned_by},
        )


#: The one provisioning statement, held as a module constant so the fence can
#: read it. ``tests/unit/test_signin_requests.py`` asserts against THIS STRING —
#: the fake DB in that file re-implements the ``ON CONFLICT`` arms in Python and
#: a mirror can only ever agree with itself, so a behavioural test alone cannot
#: notice the guard being widened. The structural assertion is the fence.
#:
#: **The guard names the statuses it rewrites; it never names the ones it does
#: not.** A `<>`/`NOT IN` comparison against ``app_user.status`` would mean
#: "every status except…", which is how a row nobody meant to touch gets
#: rewritten — `app_user.status <> 'active'` is exactly the mutation that turns
#: approve into a way to reinstate a REMOVED member.
#:
#: The two arms, and why each is where it is:
#:
#: * ``invited`` → the caller's ``status``, unconditionally. This is the only
#:   route to `active`, and it is what makes approve one action rather than
#:   §2's two clicks (§6 done-when 7).
#: * ``removed`` → the caller's ``status``, but **never `active`**. Bringing an
#:   off-boarded person back is a `removed → active` transition, which on
#:   ``PATCH /admin/members/{email}`` requires ``admin:members:manage``; approve
#:   holds only the weaker ``admin:members:invite``. Invite (``'invited'``)
#:   passes the clause and keeps its pre-extraction behaviour byte-for-byte.
#:
#: `active` and `suspended` rows are never rewritten by either caller.
#:
#: ⚠️ **The trailing ``WHERE`` is the tenant fence, and it is on the DO UPDATE
#: arm rather than in Python because that is the only place a conflicting row
#: can be seen.** ``app_user.email`` is globally UNIQUE (D-MT-1 (a)), so
#: inviting an address that already belongs to ANOTHER organization conflicts
#: with a row the inviting admin may not touch. Before the fence, this
#: statement's ``SET organization_id = EXCLUDED.organization_id`` *moved that
#: person into the inviter's tenant* — the whole membership, roles about to be
#: replaced by ``set_roles``, in one unauthenticated-by-anything write. That is
#: S1-1's write leak surviving the caller-derived ``get_org_id``, because the
#: id being correct says nothing about the row being reachable.
#:
#: With the fence the arm is skipped, nothing is written, and ``get_member``
#: below answers 404 — the same answer as an address that does not exist (R5),
#: so the invite form is not an oracle for the deployment's directory. The
#: caller's transaction is abandoned before ``commit`` by that raise.
#:
#: ``organization_id`` is now ``COALESCE(app_user.organization_id, EXCLUDED…)``
#: rather than ``EXCLUDED`` outright — the same shape, for the same reason, as
#: ``acb_auth.access._BOOTSTRAP_OWNER_SQL``: a row that already has a tenant
#: keeps it, and a legacy row with a NULL one is adopted by the org that is
#: legitimately provisioning it. The ``IS NULL`` arm of the fence is what lets
#: that adoption happen at all.
_PROVISION_MEMBER_SQL = """
    INSERT INTO app_user (email, display_name, organization_id,
                          status, invited_by, invited_at, joined_at)
    VALUES (:email, :name, CAST(:org AS uuid), :status, :by, now(),
            CASE WHEN :status = 'active' THEN now() END)
    -- `(lower(email))`, not `(email)`: migration 162 replaced the byte-exact
    -- `app_user_email_key` with `app_user_email_lower_key ON app_user
    -- (lower(email))`, and an `ON CONFLICT` target must name an index that
    -- EXISTS. Against the post-162 ladder `(email)` raises 42P10 — `there is no
    -- unique or exclusion constraint matching the ON CONFLICT specification` —
    -- at PLAN time, so EVERY invite and every sign-in approval failed, the
    -- fresh-address ones included. Reproduced red against a ladder-replayed
    -- Postgres before this line changed (WS-29 MT-1j slice 6,
    -- `saas_multitenancy.md` §11). The tenant fence below is unchanged and is
    -- now reachable for a differently-cased address as well, which is the point
    -- of the functional index. Fence: `tests/unit/test_app_user_upserts.py`.
    ON CONFLICT (lower(email)) DO UPDATE
       SET organization_id = COALESCE(app_user.organization_id,
                                      EXCLUDED.organization_id),
           display_name    = COALESCE(NULLIF(EXCLUDED.display_name, ''),
                                      app_user.display_name),
           status          = CASE
                               WHEN app_user.status = 'invited'
                                    THEN :status
                               WHEN app_user.status = 'removed'
                                    AND :status <> 'active'
                                    THEN :status
                               ELSE app_user.status
                             END,
           joined_at       = CASE
                               WHEN app_user.status = 'invited'
                                    AND :status = 'active'
                                    THEN COALESCE(app_user.joined_at, now())
                               ELSE app_user.joined_at
                             END,
           updated_at      = now()
     WHERE app_user.organization_id IS NULL
        OR app_user.organization_id = EXCLUDED.organization_id
"""


#: ⚠️ **The ONE statement in this package that deliberately crosses the tenant
#: boundary**, and it exists because the database's uniqueness and this
#: package's matching disagree about what "the same address" means.
#:
#: ``app_user_email_key`` used to be ``UNIQUE (email)`` — **byte-exact** — while
#: every lookup here matches ``lower(email)`` (R10). A row stored as
#: ``Casey@Alpha.Example`` therefore did **not** conflict with the lower-cased
#: address :func:`provision_member` inserts, and Postgres cheerfully wrote a
#: SECOND ``app_user`` row. Found by driving the real routes against a real
#: Postgres: every hermetic test was green, because a fake dict keyed
#: case-insensitively cannot reproduce a byte-exact index.
#:
#: What that cost under D-MT-1 (a): the same human ended up with a row in two
#: organizations, ``resolve_organization_id`` returned whichever the planner
#: handed back first, and a person's tenant became non-deterministic. The
#: ``ON CONFLICT`` fence could not catch it — no conflict ever happened.
#:
#: **Migration 162 landed the proper fix** (``app_user_email_lower_key ON
#: app_user (lower(email))``, and it dropped the byte-exact constraint), so a
#: differently-cased address now conflicts. This check stays anyway, and not
#: as a leftover: the conflict it prevents is a CROSS-TENANT one, which the
#: upsert's trailing ``WHERE`` fence skips **silently** — zero rows written and
#: no error. Asking first is what turns that into an explicit 404, the same
#: answer as every other cross-tenant miss (R5, so this is not an oracle for
#: the deployment's directory). It still returns the address's STORED spelling,
#: which is now belt-and-braces rather than the whole mechanism.
_ADDRESS_TENANT_SQL = (
    "SELECT organization_id::text AS org, email FROM app_user "
    " WHERE lower(email) = :email"
)


async def provision_member(
    db: Any,
    org_id: str,
    *,
    email: str,
    display_name: str,
    roles: list[str],
    admin: UserContext,
    status: str,
) -> tuple[dict[str, Any], list[str]]:
    """Create-or-update the ``app_user`` row and set its roles. THE one path.

    Extracted from ``members.invite_member`` so that approving a sign-in
    request (``access_requests.approve_access_request``) provisions through
    exactly the same code — including invariants 1 and 2, which a second
    hand-rolled INSERT would quietly skip. Spec ``colleague_onboarding.md`` §6
    done-when 8.

    Deliberately left to the CALLER: the commit — the caller's
    ``_tenant_session`` block commits on clean exit (so an approval can mark
    its request decided in the same transaction), ``invalidate_for``,
    ``record_admin_change`` (the audit action differs — `org.member_invited`
    vs `org.access_request_approved`) and the response model.

    ``status`` is what a NEW row gets; on an existing row it is applied only by
    the arms `_PROVISION_MEMBER_SQL` names, which is where that rule is
    documented. The short version: **neither caller can activate a row that is
    not `invited`**, so approve — on the weaker ``admin:members:invite`` — can
    reverse neither a suspension nor an off-boarding, both of which are
    ``admin:members:manage`` decisions.

    Invariant 1 is enforced here rather than only in ``members.py`` because
    ``set_roles`` below **replaces** a member's assignments wholesale: inviting
    or approving the last `owner` with the default `member` role would delete
    the org's only owner grant, and the only way back is SQL on the box.

    ``org_id`` is the CALLER's organization — :func:`get_org_id` derives it from
    the authenticated address and every caller of this function passes what it
    returned. It bounds three things here and each is a separate door:
    :func:`resolve_assignable_roles` (which roles exist to grant),
    :func:`find_member` (whose row is being replaced), and the upsert's own
    ``WHERE`` fence (whose row may be written at all).
    """
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 254:
        raise HTTPException(status_code=400, detail="A valid email is required.")

    # Which tenant already holds this address, if any — asked case-insensitively
    # across the whole directory, because the unique index is not. See
    # `_ADDRESS_TENANT_SQL` for what this is defending against and why it is
    # the only cross-tenant read in the package.
    known = (
        await db.execute(text(_ADDRESS_TENANT_SQL), {"email": email})
    ).mappings().all()
    if any(r["org"] and r["org"] != org_id for r in known):
        raise HTTPException(status_code=404, detail=f"No member '{email}'.")
    # Bind the address as it is STORED, so the upsert lands on the existing row
    # instead of inserting a differently-cased twin beside it. A brand-new
    # address stays lower-cased, which is what makes every future match work.
    #
    # The row is chosen explicitly — mine, else the unattached one, else the
    # lower-cased new address — and never "whichever came back first". A
    # directory that already holds a cased pair (which is what this code could
    # produce before) must not have its outcome decided by the planner.
    stored_email = next(
        (r["email"] for r in known if r["org"] == org_id),
        next((r["email"] for r in known if not r["org"]), email),
    )

    role_ids = await resolve_assignable_roles(db, org_id, roles or ["member"], admin)

    # Refuse BEFORE the upsert, like every sibling write does (`members.py`
    # `update_member` / `remove_member` / `set_member_roles`): an existing row
    # whose roles are about to be replaced by a set without `owner` is the same
    # lockout as demoting them on the roles screen. Narrower than the roles
    # route on purpose — it only asks when the grant is actually about to be
    # taken away, so provisioning is not blocked in an org that has no owner
    # yet (the bootstrap state, where nothing is being lost).
    existing = await find_member(db, org_id, email)
    if (
        existing is not None
        and "owner" not in {slug for _rid, slug in role_ids}
        # Short-circuits, so provisioning a NEW address still costs no extra
        # query — the check only asks when there is a grant to lose.
        and "owner" in await roles_for_user(db, existing["id"])
    ):
        await assert_owner_survives(db, org_id, excluding_user_id=existing["id"])

    await db.execute(
        text(_PROVISION_MEMBER_SQL),
        {"email": stored_email, "name": display_name or "", "org": org_id,
         "by": admin.email, "status": status},
    )
    # Re-read through the SAME tenant predicate the fence uses. When the upsert
    # declined a foreign row this is the 404 the caller receives, and it is
    # raised before `set_roles` — so no role is granted to a person the caller
    # could not have written to in the first place.
    member = await get_member(db, org_id, email)
    await set_roles(db, member["id"], role_ids, admin.email)

    # ⚠️ The RLS-EXEMPT identity-shadow mirror is deliberately NOT called here.
    # It is the CALLER's responsibility, AFTER its `_tenant_session` has
    # committed — the same post-commit posture `members.update_member` /
    # `remove_member` already use, and the fix for the WS-29 H6 orphan-closure
    # slice: mirroring inside this still-open transaction committed the shadow on
    # its own session while the authoritative `app_user` write could still roll
    # back (a concurrent-approve 409 in `access_requests._decide`), leaving an
    # active shadow ORPHAN. Both callers (`members.invite_member`,
    # `access_requests.approve_access_request`) now call
    # `mirror_identity_membership` + `mirror_membership_status` once their commit
    # is durable — so the shadow only ever mirrors an `app_user` write that
    # actually landed. See `acb_auth.access` and §H6.
    return member, [slug for _rid, slug in role_ids]


def invalidate_for(*emails: str | None) -> None:
    """Drop cached access so an admin change lands immediately, not in 60s."""
    for email in emails:
        if email:
            invalidate_access(email)


def record_admin_change(
    actor: str | None, action: str, target: str, **payload: Any
) -> None:
    """Append an access change to the audit log.

    Every write in this package goes through here. "Who could see what, and
    who changed it" is the first question asked after an incident and the
    first thing an access review needs; structlog alone does not survive as a
    queryable record.

    Best-effort by contract (``acb_audit.record`` logs always, DB-writes if it
    can) — an audit backend that is down must not block an admin from
    revoking someone's access.
    """
    try:
        from acb_audit import AuditEvent, record  # noqa: PLC0415

        record(AuditEvent(
            actor=f"user:{actor}" if actor else "system:internal",
            action=action,
            target=target,
            payload=payload,
        ))
    except Exception as exc:  # noqa: BLE001
        _log.warning("admin_audit_failed", action=action, error=str(exc))
