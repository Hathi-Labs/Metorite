"""DB-backed resolution of a member's effective access.

Reads the org/role/override tables from ``infra/postgres/130_org_access_control.sql``
and turns an email into an :class:`~acb_auth.permissions.EffectiveAccess`.
Pure matching logic lives in :mod:`acb_auth.permissions`; this module is the
I/O half.

Spec: ``project-docs/specs/org_access_control.md`` §5.

Why resolve per request instead of stuffing permissions in the session JWT: a
JWT outlives an access change. "I revoked WhatsApp an hour ago and they still
have it" is the failure that makes people stop trusting the whole model, so
the session carries identity only and access is resolved server-side behind a
short TTL cache.
"""
from __future__ import annotations

import os
import time

from acb_common import get_logger

# The one shared pool (BO-10) — see the Engine section below. `get_session_factory`
# is re-exported under the private name this module has always used
# (`tests/unit/test_signin_requests.py` monkeypatches it onto a fixture engine).
# `current_tenant` + `tenant_session` are the ONE tenant-GUC seam (db.py), reused
# (never re-implemented) by BOTH the bound role leg in `resolve_access` (under
# IDENTITY_CUTOVER) and the RBAC bridge (which writes RLS-FORCED tables and so
# must SET LOCAL `app.tenant_id` before its UPDATEs) — R5.
from acb_common.db import current_tenant, tenant_session
from acb_common.db import get_session_factory as _get_session_factory

from acb_auth.permissions import (
    LEGACY_ROLE_MAP,
    EffectiveAccess,
    build_access,
)

_log = get_logger("acb_auth.access")

#: Short enough that a revocation lands within a minute, long enough that a
#: chatty page does not issue one query per API call.
CACHE_TTL_SECONDS = 60.0

#: Possession of the internal bearer token is already total authority (it can
#: assert any X-User-Email), so the service principal is granted everything
#: rather than pretending to a narrower set it could trivially escape.
SERVICE_ACCESS = EffectiveAccess(
    roles=frozenset({"agent_service"}),
    role_granted=frozenset({"*"}),
)

_cache: dict[str, tuple[float, EffectiveAccess]] = {}
#: Set once the access tables are confirmed missing, so we degrade to the
#: legacy mapping without re-querying a failing table on every request.
_tables_missing = False


# ── H6 slice 3b — the read cutover flag (DARK, default OFF) ──────────────────

def identity_cutover_enabled() -> bool:
    """Whether sign-in resolves identity through the RLS-EXEMPT shadow tables.

    WS-29 H6 slice 3b (D48). After H3's phase-4 RLS the tenant-DISCOVERY read on
    the sign-in path runs on an UNBOUND session — the tenant is what it is trying
    to resolve — so a read of the FORCE-RLS'd ``app_user`` returns ZERO rows and
    every sign-in bricks (`saas_multitenancy_handover.md` §H6, characterized by
    ``test_h3_rls_promotion_rehearsal.py``). With this flag ON,
    :func:`resolve_identity` reads ``user_identity ⋈ org_membership ⋈
    organization`` instead — the same RLS-EXEMPT tables ``console_resolve``
    already reads unbound — filtered to an ACTIVE membership, and
    ``deps._with_resolved_access`` binds the resolved tenant BEFORE the bound
    role leg (``resolve_access``) reads ``app_user``.

    **Default is UNSET = OFF, fail-closed — byte-identical to today.** Building
    the branch DARK is AGENT-SAFE (D48); FLIPPING it on a live box is OWNER-GATE,
    as are H3 phase-4 promotion and the prod backfill. Read here (not the
    Next-side ``CUSTOMER_CONSOLE_RESOLVE_ENABLED``): the two-phase resolve is
    entirely server-side in ``acb_auth``. The env idiom matches
    ``deps._refuse_llm_key_identity`` / ``_trust_unverified_sso_headers``.
    """
    return os.getenv("IDENTITY_CUTOVER", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# ── Engine (the one shared pool — acb_common.db, BO-10) ─────────────────────
#
# This module used to build its own 5+10 engine. It ran *inside* the gateway
# process alongside the route packages' pools, so it was the reason the gateway
# could never get to one pool by converting routes alone — hence the seam living
# in `acb_common`, the only package both sides already depend on.
#
# Two things came free with the move. This engine never carried the connect-phase
# and `idle in transaction` bounds that came out of the 2026-08-06 incident
# (`acb_common.db.engine_connect_args`) — a permission resolution that hung used
# to hold its lock indefinitely. And it resolved `DATABASE_URL` with its own copy
# of the coercion, which is now one function.
#
# `_get_session_factory` is imported at the top of this module. It keeps the
# private name so `tests/unit/test_signin_requests.py`, which monkeypatches it
# onto a fixture engine, keeps working.


# ── Cache ───────────────────────────────────────────────────────────────────

def invalidate(email: str | None = None) -> None:
    """Drop cached access for one member, or everyone when email is None.

    Every admin write path calls this. Without it the 60s TTL becomes the
    latency of a permission change, which is fine for revocation-by-timeout
    but infuriating for an admin watching a toggle appear to do nothing.
    """
    if email:
        _cache.pop(email.lower().strip(), None)
    else:
        _cache.clear()


def _cache_get(email: str) -> EffectiveAccess | None:
    hit = _cache.get(email)
    if hit is None:
        return None
    expires_at, access = hit
    if expires_at < time.monotonic():
        _cache.pop(email, None)
        return None
    return access


def _cache_put(email: str, access: EffectiveAccess) -> None:
    _cache[email] = (time.monotonic() + CACHE_TTL_SECONDS, access)


# ── Legacy fallback ─────────────────────────────────────────────────────────

def legacy_fallback_enabled() -> bool:
    """Whether a missing access table degrades to the legacy role mapping.

    **Off by default** since BO-2 residual #1 landed. Before authentication was
    enforced app-wide, refusing everyone when the tables were absent would have
    been an outage with no way back in, so the fallback was automatic. Now that
    an unauthenticated caller is rejected outright, the remaining case is an
    authenticated member on a deployment whose migration has not run — and
    quietly granting them an *approximation* of access is worse than refusing:
    it is the access model silently not being the access model.

    Deploy order makes this safe: ``.github/workflows/deploy.yml`` runs
    ``apply_migrations.sh`` before restarting the gateway, so by the time this
    code serves traffic the tables exist.

    ``ACCESS_LEGACY_FALLBACK=1`` re-enables it. That is the recovery hatch for
    a failed migration — turn it on, fix the migration, turn it off.
    """
    return os.getenv("ACCESS_LEGACY_FALLBACK", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def legacy_access(role: str | None) -> EffectiveAccess:
    """Approximate the pre-128 world from the legacy ``executive``/``employee``.

    Reached only when the access tables are absent AND
    :func:`legacy_fallback_enabled` is on — see spec §7. A member whose row
    *does* exist never lands here; an unknown user resolves to nothing.
    """
    slug = LEGACY_ROLE_MAP.get((role or "employee").lower(), "member")
    if slug in ("admin", "agent_service"):
        return build_access(
            ["feature:*", "agents:run:*", "agents:manage", "apps:use:*",
             "apps:create", "apps:publish", "workflows:publish",
             "admin:members:read",
             "admin:members:invite", "admin:members:manage",
             "admin:roles:manage", "admin:access:manage",
             "admin:settings:manage", "admin:audit:read",
             "integrations:manage", "data:org:read"],
            roles=[slug],
        )
    return build_access(
        ["feature:chat", "feature:email", "feature:tasks", "feature:notes",
         "feature:memory", "feature:dashboard", "feature:artifacts",
         "agents:run:*", "apps:use:*"],
        roles=[slug],
    )


def _degraded(legacy_role: str | None) -> EffectiveAccess:
    """What a failed/absent access lookup resolves to.

    Fail CLOSED unless the operator opted into the legacy mapping. See
    :func:`legacy_fallback_enabled` for why the default flipped.
    """
    if legacy_fallback_enabled():
        return legacy_access(legacy_role)
    return EffectiveAccess(is_active=False)


# ── Resolution ──────────────────────────────────────────────────────────────

_ACCESS_SQL = """
    SELECT u.id::text                       AS user_id,
           u.organization_id::text          AS organization_id,
           u.status                         AS status,
           u.role                           AS legacy_role,
           COALESCE(
               (SELECT array_agg(DISTINCT r.slug)
                  FROM user_role ur
                  JOIN org_role r ON r.id = ur.role_id
                 WHERE ur.user_id = u.id),
               ARRAY[]::text[]
           )                                AS roles,
           COALESCE(
               (SELECT array_agg(DISTINCT rp.permission)
                  FROM user_role ur
                  JOIN org_role_permission rp ON rp.role_id = ur.role_id
                 WHERE ur.user_id = u.id),
               ARRAY[]::text[]
           )                                AS role_permissions,
           COALESCE(
               (SELECT array_agg(o.permission || '=' || o.effect)
                  FROM user_permission_override o
                 WHERE o.user_id = u.id),
               ARRAY[]::text[]
           )                                AS overrides
      FROM app_user u
     WHERE lower(u.email) = :email
     LIMIT 1
"""


#: One row per address, bumped on every repeat knock. ``status`` is deliberately
#: NOT in the DO UPDATE arm: a denied address that keeps signing in must move
#: ``last_seen_at``/``attempt_count`` without returning to the owner's queue
#: (spec §6 done-when 9). ``first_seen_at`` is likewise never rewritten — "first
#: locked out at 16:21 yesterday" is the fact that makes the row legible.
_ACCESS_REQUEST_UPSERT_SQL = """
    INSERT INTO access_request (email, display_name)
    VALUES (:email, :name)
    ON CONFLICT (lower(email)) DO UPDATE
       SET last_seen_at  = now(),
           attempt_count = access_request.attempt_count + 1,
           display_name  = COALESCE(NULLIF(EXCLUDED.display_name, ''),
                                    access_request.display_name)
"""


async def _record_signin_request(email: str, display_name: str = "") -> None:
    """File an unprovisioned sign-in in ``access_request``. Best-effort.

    Spec: ``project-docs/specs/colleague_onboarding.md`` §6 (N6a).

    **Never raises, never changes the caller's answer.** The queue is a
    convenience for the owner; the refusal above it is the security answer, and
    a missing table, a full disk or a lock timeout must not turn "you have no
    access" into a 500. It opens its own short session rather than reusing the
    resolution one so a failed write cannot poison the read that produced it.

    Deliberately does **not** touch ``_tables_missing``: that flag means "the
    access model is not deployed" and permanently degrades every resolution.
    A deployment on which migration 143 has not run yet has a perfectly working
    access model and merely no queue, so it logs once per TTL and carries on.

    ``display_name`` is accepted and stored but the resolver has none to give —
    ``UserContext`` carries an email only. The column exists so an approval
    path (or a future IdP claim) can fill it; the UI falls back to the address.
    """
    try:
        from sqlalchemy import text  # noqa: PLC0415

        factory = _get_session_factory()
        async with factory() as session:
            await session.execute(
                text(_ACCESS_REQUEST_UPSERT_SQL),
                {"email": email, "name": display_name},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "access_request_record_failed", email=email, error=str(exc)[:200],
        )


# ── Identity shadow mirror (WS-29 H6 slice 1, MT-1a-2) — DARK ────────────────
#
# After H3's phase-4 RLS, identity resolution (which reads `app_user` on an
# UNBOUND session) bricks; H6 moves it onto the RLS-EXEMPT tables `user_identity`
# + `org_membership`. Before ANY read can move, those tables must be COMPLETE and
# kept CURRENT. Migration 159 seeded them once; migration 182 catches up the gap
# since; this keeps them current going forward by DUAL-WRITING on the two
# `app_user` write paths (`_BOOTSTRAP_OWNER_SQL` here, `_PROVISION_MEMBER_SQL` in
# the gateway admin package). It moves NO read — `app_user` stays authoritative —
# so this is dark by construction (`saas_multitenancy_handover.md` §H6).
#
# ⚠️ **Same SHAPE as `console_resolve._UPSERT_IDENTITY_SQL` / `_ORG_BY_SLUG_SQL`
# — the tenant-plane identity-write idiom — reused, not forked.** The constants
# are DEFINED here rather than imported from `console_resolve` on purpose:
# `test_console_dependency_boundary.py` caps that module's importers at the three
# sign-in-path callers because it allocates a SEAT (`resolve_for_signin`), and
# this dual-write allocates none. Two structurally-identical statements on the
# `(lower(email))` functional index cannot drift into two behaviours; a fourth
# `console_resolve` importer would break a farmable-cap fence.

#: One row per human, keyed on `lower(email)` (162's functional index; R10). The
#: `display_name` arm never blanks a stored name — same COALESCE as the Console
#: projection's upsert.
_MIRROR_IDENTITY_SQL = """
    INSERT INTO user_identity (email, display_name)
    VALUES (:email, :name)
    ON CONFLICT (lower(email)) DO UPDATE
       SET display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''),
                                   user_identity.display_name),
           updated_at = now()
    RETURNING id::text AS id
"""

#: ⚠️ **CREATE-ONLY, and that is the load-bearing invariant (§H6:672-674).**
#: `ON CONFLICT (organization_id, user_id) DO NOTHING` — a membership already
#: mirrored is left EXACTLY as it was, and a human who belongs to a second org
#: gets a SECOND row rather than having the first rewritten. There is deliberately
#: NO `SET organization_id`: the invite path is "an account-takeover primitive
#: under two orgs", and an UPDATE of the identity's org between orgs is exactly
#: what this refuses. `resolved_at` is left NULL — a bootstrap/invite is NOT a
#: registry resolution (that column is `console_resolve`'s), and NULL is what 159
#: seeded and 182 backfills, so a dual-written row and a backfilled row match. No
#: `role` column exists on this table.
_MIRROR_MEMBERSHIP_SQL = """
    INSERT INTO org_membership (organization_id, user_id, status, joined_at)
    VALUES (CAST(:org AS UUID), CAST(:uid AS UUID), :status,
            CASE WHEN :status = 'active' THEN now() END)
    ON CONFLICT (organization_id, user_id) DO NOTHING
"""

#: §6(k)'s join key is the SLUG, never the Console's UUID. Same shape as
#: `console_resolve._ORG_BY_SLUG_SQL`; used only by the bootstrap caller, which
#: knows its org by slug rather than by id.
_MIRROR_ORG_BY_SLUG_SQL = "SELECT id::text AS id FROM organization WHERE slug = :slug"


async def mirror_identity_membership(
    *,
    email: str,
    display_name: str = "",
    org_id: str | None = None,
    org_slug: str | None = None,
    status: str = "active",
) -> None:
    """Mirror an ``app_user`` write into ``user_identity`` + ``org_membership``.

    Best-effort, and **never raises, never changes the caller's answer** — the
    exact posture of :func:`_record_signin_request` above, and for the same
    reason: ``app_user`` is authoritative and its write must be BYTE-IDENTICAL to
    today (`saas_multitenancy_handover.md` §H6). It opens its OWN short session
    rather than riding the caller's transaction so a failed shadow write cannot
    poison the authoritative one; **this function's own writes touch
    ``user_identity`` + ``org_membership``, which are RLS-EXEMPT, so THOSE need no
    tenant bind.** (The RBAC bridge it calls at the end writes RLS-FORCED tables and
    so DOES run GUC-bound — see :func:`_bridge_rbac_to_identity`; do not read this
    exemption as covering it.) A failure leaves the row for migration 182's
    idempotent catch-up to reconcile.

    Pass ``org_id`` when the caller already holds it (the invite path) or
    ``org_slug`` when it knows the org by name (the bootstrap path). One row per
    ``lower(email)`` in ``user_identity``; a create-only INSERT into
    ``org_membership`` that never moves an identity between orgs.

    ⚠️ **Create-only: this mirrors membership EXISTENCE, not later STATUS.** The
    ``ON CONFLICT DO NOTHING`` means a subsequent suspend / remove / reactivate
    (``members.py``, which mutates ``app_user.status`` and does NOT call this
    helper) is NOT propagated — ``org_membership.status`` drifts stale. Harmless
    while dark (no tenant-plane reader consumes it), but the H6 read-cutover MUST
    add status mirroring to those paths, or reconcile status against ``app_user``,
    before it reads ``org_membership.status`` — else a suspended member is
    admitted from a stale row. See migration 182's header and §H6.
    """
    email = (email or "").strip()
    if not email or "@" not in email:
        return
    identity_id: str | None = None
    bridge_org: str | None = None
    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            resolved_org = org_id
            if resolved_org is None:
                if not org_slug:
                    return
                row = (
                    await session.execute(
                        text(_MIRROR_ORG_BY_SLUG_SQL), {"slug": org_slug}
                    )
                ).mappings().first()
                if row is None:
                    return
                resolved_org = row["id"]

            identity = (
                await session.execute(
                    text(_MIRROR_IDENTITY_SQL),
                    {"email": email, "name": display_name or ""},
                )
            ).mappings().first()
            if identity is None:
                return
            await session.execute(
                text(_MIRROR_MEMBERSHIP_SQL),
                {"org": str(resolved_org), "uid": identity["id"], "status": status},
            )
            await session.commit()
            identity_id = identity["id"]
            # The human's org — carried out of the (RLS-exempt) mirror session so
            # the RBAC bridge below can GUC-bind it. It equals the human's
            # `app_user.organization_id` (a human is in ONE org, migration 162), and
            # binding it is the ONLY way the bridge's UPDATEs can see the RLS-FORCED
            # RBAC rows; `app_user` cannot be re-read here to derive it (unbound → 0
            # rows). See `_bridge_rbac_to_identity`.
            bridge_org = str(resolved_org)
    except Exception as exc:
        _log.warning(
            "identity_mirror_failed", email=email, error=str(exc)[:200],
        )
        return

    # H6 slice 4 (D48) — the identity is now DURABLE, so bridge this human's RBAC
    # rows that were written BEFORE it existed. The canonical case is a
    # PROVISIONED owner: `provision_org_owner` (the tenant-plane SQL, migration
    # 180) INSERTs the owner's `user_role` with no identity yet to resolve, and
    # this mirror mints that identity only AFTER provisioning commits — so the
    # dual-write's identity-FIRST bridge (migration 184) never fired for it. A
    # bootstrap owner and any future RBAC-first order are the same shape. Running
    # the backfill HERE — inside the ONE identity-creation seam, called by
    # invite / approve / bootstrap AND now provision — closes every such case in
    # one place, robust to creation order. Separate best-effort step on its OWN
    # session, so a failure never touches the identity/membership write just
    # committed (see `_bridge_rbac_to_identity`).
    await _bridge_rbac_to_identity(
        email=email, identity_id=identity_id, org_id=bridge_org,
    )


# ── Identity shadow: RBAC re-key bridge (WS-29 H6 slice 4, D48) — DARK ──
#
# Migration 184 added a nullable `user_identity_id` to the three RBAC tables and
# DUAL-WRITES it on the five Python RBAC INSERTs (`set_roles`, `add_group_member`
# x2, `set_member_overrides`, `_BOOTSTRAP_OWNER_SQL`), each resolving the identity
# through the `lower(email)` bridge at INSERT time — the identity-FIRST order.
#
# There is a SIXTH RBAC INSERT the dual-write cannot reach: `provision_org_owner`
# (the tenant-plane SQL function, `infra/postgres/180_...:153-155`), which INSERTs
# the owner's `user_role` DURING provisioning — before any `user_identity` exists,
# because provisioning mints the owner's identity only AFTER it commits (via this
# module's mirror, called from the provision caller). That is the RBAC-FIRST order,
# and migration 184's one-shot backfill only catches rows that predate IT. So
# whenever the mirror ensures a human's identity, it ALSO backfills that human's
# existing RBAC rows here — closing the provisioned owner (who would otherwise get
# a NULL `user_role.user_identity_id` and, once IDENTITY_CUTOVER flips the role leg
# onto it, NO roles → locked out of their own org) and every future RBAC-first
# case in ONE place. `app_user.id` stays authoritative; this writes only the shadow
# column, which nothing reads yet (dark).
#
# ⚠️ **The bridge writes the three RLS-FORCED RBAC tables, so it runs GUC-BOUND —
# unlike the mirror's OWN writes above, which touch the RLS-EXEMPT `user_identity`
# / `org_membership` and need no bind.** `user_role` / `user_permission_override` /
# `org_group_member` all carry `FORCE ROW LEVEL SECURITY` keyed on
# `organization_id = current_setting('app.tenant_id', true)::uuid`
# (`generated/04_policies.sql`), so an UPDATE on an UNBOUND session sees the GUC as
# NULL → 0 visible rows → it silently no-ops (best-effort swallows it), leaving the
# provisioned owner's `user_identity_id` NULL — the same class of bug as slice 3b's
# unbound resolve. The fix binds `app.tenant_id` to the human's org (a human is in
# ONE org — `app_user` is globally unique on `lower(email)`, migration 162) via the
# ONE GUC seam `tenant_session()` (`SET LOCAL`, so it never leaks back to the pool),
# so the UPDATE sees + writes the rows. The org is the mirror's already-resolved
# org (== the human's `app_user.organization_id`); it CANNOT be re-derived by
# reading `app_user` here, because that read is itself unbound → 0 rows (the H3
# rehearsal's chicken-and-egg, `test_the_unbound_tenant_discovery_read_returns_
# nothing`). This same GUC-bind requirement binds ANY H6 write path that touches an
# RLS-FORCED table — see migration 179's "REVISIT AT H3" note and 184's header.

#: The three RBAC tables D48 re-keys.
_BRIDGE_RBAC_TABLES = ("user_role", "user_permission_override", "org_group_member")

#: One scoped UPDATE per RBAC table, via the SAME `lower(email)` bridge migration
#: 184 uses (RBAC.user_id → app_user.id → lower(email) → identity), narrowed to
#: ONE human and setting ONLY the still-NULL shadow column — never a grant key, so
#: it can never move a role/permission/group membership, and `IS NULL` makes a
#: re-run touch zero rows (idempotent). `table` is a fixed literal from the tuple
#: above, never request input.
#:
#: ⚠️ **These UPDATEs target RLS-FORCED tables, so they run inside
#: `tenant_session()` bound to the human's org** (see `_bridge_rbac_to_identity`):
#: unbound, FORCE RLS makes each match 0 rows. Neither statement names
#: `organization_id` — the bound tenant is what makes the row visible (USING) and
#: the unchanged `organization_id` is what passes WITH CHECK.
_BRIDGE_RBAC_SQL: tuple[str, ...] = tuple(
    f"""
    UPDATE {table} rbac
       SET user_identity_id = CAST(:identity AS uuid)
      FROM app_user au
     WHERE rbac.user_id = au.id
       AND lower(au.email) = lower(:email)
       AND rbac.user_identity_id IS NULL
    """
    for table in _BRIDGE_RBAC_TABLES
)


async def _bridge_rbac_to_identity(
    *, email: str, identity_id: str, org_id: str | None,
) -> None:
    """Backfill a human's existing RBAC rows' ``user_identity_id`` (H6 slice 4).

    Called by :func:`mirror_identity_membership` AFTER the identity is durable, so
    the "RBAC row created before the identity existed" order — a PROVISIONED owner
    (`provision_org_owner`'s ``user_role`` INSERT), a bootstrap owner, any future
    one — ends with the shadow column populated, matching migration 184's
    dual-write of the identity-FIRST order. One scoped UPDATE per RBAC table via
    the same ``lower(email)`` bridge, setting ONLY ``user_identity_id`` and only
    where it is still NULL: idempotent, moves no grant, and never overwrites a
    value 184 or a dual-write already set correctly.

    ⚠️ **GUC-BOUND, because the three RBAC tables are RLS-FORCED.** ``user_role`` /
    ``user_permission_override`` / ``org_group_member`` all carry ``FORCE ROW LEVEL
    SECURITY`` keyed on ``organization_id = current_setting('app.tenant_id',
    true)::uuid`` (``generated/04_policies.sql``). Run UNBOUND, each UPDATE sees the
    GUC as NULL → 0 visible rows → it silently no-ops and the provisioned owner's
    ``user_identity_id`` stays NULL — the SAME class as slice 3b's unbound resolve,
    NOT the exempt-table case the mirror's own ``user_identity`` / ``org_membership``
    writes are (those legitimately need no bind). So this runs inside the ONE GUC
    seam :func:`acb_common.db.tenant_session` (``SET LOCAL app.tenant_id`` — never a
    session ``SET``, so it cannot leak to the next pooled borrower), bound to
    ``org_id`` = the human's org. That org is threaded in from the mirror's
    already-resolved org (== the human's ``app_user.organization_id``; a human is in
    ONE org — ``app_user`` is globally unique on ``lower(email)``, migration 162).
    It is NOT re-derived by reading ``app_user`` here: that read is itself unbound →
    0 rows, the chicken-and-egg the H3 rehearsal pins
    (``test_the_unbound_tenant_discovery_read_returns_nothing``). The UPDATE writes
    ONLY ``user_identity_id`` (never ``organization_id``), so the bound tenant makes
    the rows visible (USING) and the unchanged ``organization_id`` passes WITH CHECK.

    Best-effort, and **never raises, never changes the caller's answer** — the
    exact posture of :func:`mirror_identity_membership`. It opens its OWN short
    session via the shared engine seam (``tenant_session`` → ``get_session_factory``
    — no new connection site and no second GUC-application path, R5) AFTER the
    identity commit, so a failure here (e.g. a deployment where migration 184 has
    not yet added the column, or no org to bind) is logged and swallowed and can
    never roll back the identity/membership write. DARK: it writes only the shadow
    column, which nothing reads yet.
    """
    email = (email or "").strip()
    if not email or "@" not in email or not identity_id or not org_id:
        return
    try:
        from sqlalchemy import text

        # tenant_session applies SET LOCAL app.tenant_id for org_id and commits on
        # exit; the UPDATEs run inside that one bound transaction. Reusing it keeps
        # this to the ONE GUC seam (R5) — no set_config re-implementation here.
        async with tenant_session(str(org_id)) as session:
            for sql in _BRIDGE_RBAC_SQL:
                await session.execute(
                    text(sql), {"identity": str(identity_id), "email": email},
                )
    except Exception as exc:
        _log.warning(
            "identity_rbac_bridge_failed", email=email, error=str(exc)[:200],
        )


# ── Identity shadow: forward STATUS mirror (WS-29 H6 slice 3a, D48) — DARK ────
#
# Slice 1's `mirror_identity_membership` above is CREATE-ONLY: `ON CONFLICT DO
# NOTHING` mirrors membership EXISTENCE and never touches an existing row. So the
# lifecycle paths that mutate `app_user.status` — `members.py`'s suspend / remove
# / reactivate, and approve's invited→active transition — did NOT propagate, and
# `org_membership.status` drifted STALE (migration 182's header + §H6:671-681
# document this as a latent P0 for the read cutover). D48 (2026-08-22, owner-
# ratified) mirrors status FORWARD into the RLS-EXEMPT `org_membership.status`,
# because reconciling from `app_user` post-RLS is impossible — the identity leg
# reads UNBOUND while `app_user` is RLS-forced. STILL DARK: no reader consumes
# `org_membership.status` yet (that is slice 3b).

#: ⚠️ **A SCOPED UPDATE of an EXISTING (org, identity) row — it NEVER creates a
#: row and NEVER moves an identity between orgs (§H6:672-674; R7).** It sets ONLY
#: `status` (and `joined_at` on activation, exactly as the authoritative
#: `app_user` write does — see `members.update_member` / `_PROVISION_MEMBER_SQL`).
#: `organization_id` and `user_id` appear ONLY in the WHERE and are NEVER in a
#: SET clause; the identity is resolved by `lower(email)` (162's functional
#: index; R10) inside the predicate. If no membership row exists (a member who
#: predates the shadow, or whose slice-1 existence mirror failed) the UPDATE
#: no-ops (0 rows) — existence is the reconcile migration's + slice-1's job, and
#: the status path must never mint a row. `last_active_at` is deliberately NOT
#: written: the `app_user` status write does not touch it either, so mirroring it
#: would make the shadow DIVERGE from what it mirrors rather than converge.
_MIRROR_STATUS_SQL = """
    UPDATE org_membership
       SET status = :status,
           joined_at = CASE WHEN :status = 'active'
                            THEN COALESCE(joined_at, now())
                            ELSE joined_at END
     WHERE organization_id = CAST(:org AS UUID)
       AND user_id = (
           SELECT id FROM user_identity WHERE lower(email) = lower(:email)
       )
"""


async def mirror_membership_status(
    *,
    email: str,
    org_id: str,
    status: str,
) -> None:
    """Propagate an ``app_user`` status change into ``org_membership.status``.

    WS-29 H6 slice 3a (D48), DARK. The forward half of the status-drift the
    slice-1 create-only mirror left open: ``members.py``'s suspend / remove /
    reactivate and approve's invited→active transition mutate ``app_user.status``
    and the create-only :func:`mirror_identity_membership`
    (``ON CONFLICT DO NOTHING``) never touches the existing row, so
    ``org_membership.status`` drifted stale (§H6:671-681, migration 182's header).

    Best-effort, and **never raises, never changes the caller's answer** — the
    exact posture of :func:`mirror_identity_membership` and
    :func:`_record_signin_request`, and for the same reason: ``app_user`` is
    authoritative and its write must be BYTE-IDENTICAL to today
    (`saas_multitenancy_handover.md` §H6). It opens its OWN short session rather
    than riding the caller's transaction, so a failed shadow write cannot poison
    the authoritative one; the shadow tables are RLS-EXEMPT, so no tenant bind is
    needed.

    ⚠️ **Preserves slice 1's invariant: this is a SCOPED UPDATE of an EXISTING
    (org, identity) row.** It updates ONLY ``status`` (and ``joined_at`` on
    activation); it never writes ``organization_id`` or ``user_id`` and so can
    never move an identity between orgs (R7 — fenced by
    ``test_h6_identity_shadow.py``). A missing membership row is a 0-row no-op:
    the status path never creates a row.
    """
    email = (email or "").strip()
    if not email or "@" not in email or not org_id or not status:
        return
    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            await session.execute(
                text(_MIRROR_STATUS_SQL),
                {"status": status, "org": str(org_id), "email": email},
            )
            await session.commit()
    except Exception as exc:
        _log.warning(
            "identity_status_mirror_failed", email=email, error=str(exc)[:200],
        )


# ── Identity shadow: PURGE closure (WS-29 H6 orphan-closure, D48) — DARK ──────
#
# The mirror image of `mirror_identity_membership`. A member PURGE
# (`gateway/routes/admin/members.purge_member`) deletes the authoritative
# `app_user` row and every credential/grant, but the create-only slice-1 mirror
# never removed the RLS-EXEMPT shadow — so a purged ACTIVE member left an active
# `org_membership` ORPHAN (identity present, no `app_user`). While dark that is
# harmless (nothing reads `org_membership.status`), but the moment the H6 read
# cutover stops gating on `app_user` that orphan is a wrong-ADMIT, and reconcile
# 183 CANNOT fix it (it joins `app_user`, which is gone). This closes it at the
# SOURCE by deleting ONLY THIS org's `org_membership` row.
#
# ⚠️ **The purge NEVER deletes `user_identity` — that is load-bearing, not an
# omission (R7 fence: `no-user_identity-delete-on-purge`,
# `test_h6_identity_shadow.py`).** Deleting the GLOBAL identity on the human's
# LAST membership was a check-then-cascade RACE: the `NOT EXISTS` re-eval runs on
# the purge's ORIGINAL snapshot and cannot see a concurrent (other-org,
# same-human) membership committed in between, so `org_membership.user_id ...
# ON DELETE CASCADE` then erased that OTHER org's row — org A's purge wiping org
# B's member, the exact cross-tenant erasure this closure was meant to make
# impossible. A membership-less `user_identity` row is instead HARMLESS: the read
# cutover's identity leg reads `user_identity ⋈ org_membership`, so an identity
# with zero memberships resolves to no org → no access (fail-closed), and a later
# re-join reuses it via `ON CONFLICT (lower(email))`. Dropping the identity
# delete removes the race BY CONSTRUCTION; global `user_identity`
# lifecycle/pruning of the membership-less leftover is deferred to slice 5's
# atomic mirror+prune (§H6).

#: Delete THIS org's membership for the identity resolved by `lower(email)`
#: (162's functional index; R10). Scoped to `(org, identity)` — it removes at
#: most the one row and never another org's membership for the same human.
_PURGE_MEMBERSHIP_SQL = """
    DELETE FROM org_membership m
     USING user_identity ui
     WHERE m.user_id = ui.id
       AND lower(ui.email) = lower(:email)
       AND m.organization_id = CAST(:org AS UUID)
"""


async def purge_identity_shadow(*, email: str, org_id: str) -> None:
    """Remove THIS org's identity-shadow membership for a member being PURGED.

    WS-29 H6 orphan-closure (D48), DARK. The mirror image of
    :func:`mirror_identity_membership`: where provisioning create-only-INSERTs
    the shadow, a purge deletes ONLY the org's ``org_membership`` row — the
    active shadow the read cutover would otherwise wrong-ADMIT (a member of
    another org keeps every one of their memberships untouched, because this is
    scoped to ``(org, identity)``).

    ⚠️ **It NEVER deletes ``user_identity``, and that is deliberate (R7 fence:
    ``no-user_identity-delete-on-purge``).** Deleting the GLOBAL identity on the
    human's LAST membership was a check-then-cascade RACE: the ``NOT EXISTS``
    re-eval ran on the purge's ORIGINAL snapshot and could not see a concurrent
    (other-org, same-human) membership committed in between, so
    ``org_membership.user_id ... ON DELETE CASCADE`` then erased that OTHER org's
    row — org A's purge wiping org B's member. A membership-less ``user_identity``
    is HARMLESS instead: the read cutover's identity leg reads
    ``user_identity ⋈ org_membership``, so zero memberships resolve to no org →
    no access (fail-closed), and a later re-join reuses the row via
    ``ON CONFLICT (lower(email))``. Global ``user_identity`` lifecycle/pruning of
    the membership-less leftover is deferred to slice 5's atomic mirror+prune
    (§H6).

    Best-effort, and **never raises, never changes the caller's answer** — the
    exact posture of :func:`mirror_identity_membership` /
    :func:`mirror_membership_status`, and for the same reason: ``app_user`` is
    authoritative and the purge transaction must be untouched by a shadow write.
    It opens its OWN short session AFTER the authoritative purge has committed,
    so a failed shadow delete cannot poison (or roll back) the irreversible
    purge; the shadow tables are RLS-EXEMPT, so no tenant bind is needed.

    ⚠️ **A failed best-effort delete is NOT self-healed by migration 182/183.**
    182 only ``INSERT ... DO NOTHING`` (it never deletes) and 183
    ``UPDATE ... FROM app_user`` joins the now-purged ``app_user`` row, so
    neither can reach a purge orphan (stated in 183's header, §H6 and
    ``members.py``). If this throws after the authoritative purge commits, the
    active ``org_membership`` shadow orphan REMAINS — closing that
    swallowed-failure window is slice 5's atomic prune (or a dedicated
    purge-reconcile), so the failure is logged rather than silently trusted to
    reconcile.
    """
    email = (email or "").strip()
    if not email or "@" not in email or not org_id:
        return
    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            await session.execute(
                text(_PURGE_MEMBERSHIP_SQL),
                {"email": email, "org": str(org_id)},
            )
            await session.commit()
    except Exception as exc:
        _log.warning(
            "identity_shadow_purge_failed", email=email, error=str(exc)[:200],
        )


async def resolve_access(
    email: str | None,
    *,
    legacy_role: str | None = None,
    use_cache: bool = True,
    record_request: bool = False,
) -> EffectiveAccess:
    """Resolve a member's effective access by email.

    An unknown email resolves to no access. A suspended or removed member
    resolves to no access regardless of the roles still on their row — the
    status check is not a filter on the query but a property of the result, so
    a stale cache entry can never outlive a suspension by more than the TTL.

    ``record_request`` opts the caller into filing an unprovisioned email as a
    sign-in request. It defaults to **False** and must stay that way: this
    function is not sign-in-only. ``routes/rooms.py`` fans it out over room
    participants' emails and :func:`resolve_session_access` folds it over
    session subjects — neither is somebody knocking at the front door, and
    filing them would fill the owner's queue with people who never tried to
    sign in. Exactly one caller passes it: ``acb_auth.deps._with_resolved_access``,
    which runs per authenticated request. See spec §6 done-when 3.
    """
    global _tables_missing

    if not email:
        return EffectiveAccess(is_active=False)
    key = email.lower().strip()

    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    if _tables_missing:
        return _degraded(legacy_role)

    try:
        from sqlalchemy import text  # noqa: PLC0415

        # H6 slice 3b — the ROLE LEG session. Under H3 phase-4 RLS ``app_user``
        # is FORCE-RLS'd, so when the cutover is ON and ``deps._with_resolved_access``
        # has already bound the tenant (the identity leg resolved the org), this
        # read MUST run on a session whose ``app.tenant_id`` GUC is set — else RLS
        # returns zero rows and a live member resolves ``is_active=False``, the
        # total-lockout brick the cutover exists to fix. Reuse the ONE GUC seam
        # (``tenant_session`` → ``set_config``, db.py); do NOT apply the GUC a
        # second way (R5). Flag OFF — or ON with nothing bound (a background
        # fan-out over someone else's email) — is byte-identical to today: a raw
        # unbound session, no GUC, ``app_user`` read by email as it always was.
        if identity_cutover_enabled() and current_tenant() is not None:
            session_cm = tenant_session()
        else:
            session_cm = _get_session_factory()()
        async with session_cm as session:
            row = (
                await session.execute(text(_ACCESS_SQL), {"email": key})
            ).mappings().first()
    except Exception as exc:  # noqa: BLE001
        # Distinguish "migration hasn't run" (degrade to legacy, permanently)
        # from a transient DB blip (degrade for this request only).
        message = str(exc).lower()
        if "does not exist" in message or "undefinedtable" in message:
            _tables_missing = True
            _log.error(
                "access_tables_missing",
                detail=(
                    "apply infra/postgres/130_org_access_control.sql. Members "
                    "resolve to NO ACCESS until it runs; set "
                    "ACCESS_LEGACY_FALLBACK=1 to degrade to the legacy "
                    "executive/employee mapping instead."
                ),
            )
        else:
            _log.warning("access_resolve_failed", error=str(exc))
        return _degraded(legacy_role)

    if row is None:
        # Authenticated by the IdP but not provisioned here. No access, and
        # deliberately not auto-provisioned: an admin invites people. Logged
        # loudly because the 2026-07-30 lockout was exactly this branch firing
        # silently — the operator saw a dead-end screen with no server-side
        # trace of WHO was being refused or WHY. Cached like every other
        # resolution, so the warning fires once per TTL, not per request.
        _log.warning(
            "access_unprovisioned_signin",
            email=key,
            detail=(
                "authenticated by the IdP but has no app_user row — an owner "
                "must invite them via /settings/members (or see "
                "ensure_owner_bootstrap if NOBODY holds owner)"
            ),
        )
        # The log line above is what the 2026-07-30 lockout needed and what
        # 2026-08-04 proved insufficient: 53 of them for one address over 18
        # hours, and nothing read them back. AFTER the log, never instead of
        # it, and only for a real sign-in (see the docstring).
        if record_request:
            await _record_signin_request(key)
        refused = EffectiveAccess(is_active=False)
        if use_cache:
            _cache_put(key, refused)
        return refused

    overrides: list[tuple[str, str]] = []
    for entry in row["overrides"] or []:
        perm, _, effect = str(entry).rpartition("=")
        if perm and effect in ("allow", "deny"):
            overrides.append((perm, effect))

    access = build_access(
        row["role_permissions"] or [],
        overrides,
        roles=row["roles"] or [],
        is_active=row["status"] == "active",
    )

    if use_cache:
        _cache_put(key, access)
    return access


#: ⚠️ **H6 slice 3b — the IDENTITY LEG (DARK behind ``IDENTITY_CUTOVER``).**
#: The tenant-discovery read that resolves *which tenant* and *is this member
#: live*, from the RLS-EXEMPT ``user_identity ⋈ org_membership ⋈ organization``
#: — the SAME tables (and unbound posture) ``console_resolve._READ_SQL`` already
#: reads at sign-in, reused not forked. Filtered to an ACTIVE membership: a
#: suspended / removed / invited member (or one with no shadow row) resolves to
#: no org, so nothing binds and the bound role leg then refuses — fail-closed.
#: Returns ``id``/``org`` under the SAME column aliases as the ``app_user`` read
#: below, so :func:`resolve_identity` is source-agnostic. NEVER names
#: ``app_user`` (FORCE-RLS'd → 0 rows unbound, the brick this replaces). The
#: status filter is only SAFE because the orphan-closure slice keeps
#: ``org_membership`` free of purged/rolled-back ghosts (§H6).
#:
#: ⚠️ **``LIMIT 2``, not ``LIMIT 1`` — the P2a multi-org-safe fetch.** A human
#: may hold an ACTIVE membership in more than one org, and console-created
#: memberships leave ``joined_at`` NULL, so the old ``ORDER BY joined_at DESC …
#: LIMIT 1`` would tiebreak on ``slug`` and silently bind the WRONG org.
#: Fetching TWO lets :func:`resolve_identity` decide by COUNT: exactly one
#: active membership binds; MORE THAN ONE with no disambiguating host is the
#: WorkspaceChooserRequired / deny case (host-based selection is MT-1f, not this
#: slice). The ``ORDER BY o.slug`` only stabilises the two-row window; it never
#: DECIDES which org wins, because two rows always deny.
_IDENTITY_LEG_SQL = """
    SELECT ui.id::text AS id,
           o.id::text  AS org
      FROM user_identity ui
      JOIN org_membership m ON m.user_id = ui.id
      JOIN organization o   ON o.id = m.organization_id
     WHERE lower(ui.email) = :email
       AND m.status = 'active'
     ORDER BY o.slug
     LIMIT 2
"""


async def resolve_identity(email: str | None) -> tuple[str | None, str | None]:
    """Return ``(user_id, organization_id)`` for an email, or ``(None, None)``.

    ⚠️ **H6 slice 3b — the read cutover, DARK behind ``IDENTITY_CUTOVER``.** This
    is the tenant-DISCOVERY read on the sign-in path
    (``deps._with_resolved_access``): it runs UNBOUND, because the tenant is what
    it resolves, so after H3's phase-4 RLS the default ``app_user`` read returns
    ZERO rows (``app_user`` is FORCE-RLS'd) and sign-in bricks. With
    :func:`identity_cutover_enabled` ON it reads :data:`_IDENTITY_LEG_SQL` — the
    RLS-EXEMPT ``user_identity ⋈ org_membership ⋈ organization``, filtered to an
    ACTIVE membership — and ``deps`` binds the resolved tenant BEFORE the bound
    role leg (:func:`resolve_access`) reads ``app_user``. Flag OFF is
    byte-identical to today: the same ``app_user`` statement, so the shadow is
    never consulted while the flag is unset.

    ⚠️ **The returned ``user_id`` is id-space-dependent (P2b).** OFF it is
    ``app_user.id``; ON it is the RLS-EXEMPT ``user_identity.id`` — a stable
    per-human identity id, a DIFFERENT UUID space. It is an opaque identity token
    for display/correlation only (``/auth/me`` surfaces it), never an
    ``app_user`` foreign key: the backend keys on email, and the org is the
    second element, bound before :func:`resolve_access`'s ``app_user`` read.

    ⚠️ **Multi-org SAFE, never silently-wrong (P2a).** A human with exactly ONE
    active membership binds it. A human with >1 active membership and no
    disambiguating host resolves to ``(None, None)`` — the deny / chooser case
    (host-based selection is MT-1f); this NEVER arbitrarily picks one.
    """
    if not email:
        return None, None
    key = email.lower().strip()
    try:
        from sqlalchemy import text  # noqa: PLC0415

        factory = _get_session_factory()
        async with factory() as session:
            if identity_cutover_enabled():
                # P2a: fetch up to TWO active memberships and decide by COUNT —
                # exactly one binds; zero is "not a member"; more than one is the
                # deny / WorkspaceChooserRequired case, never an arbitrary bind.
                rows = (
                    await session.execute(
                        text(_IDENTITY_LEG_SQL), {"email": key},
                    )
                ).mappings().all()
                if len(rows) != 1:
                    return None, None
                return rows[0]["id"], rows[0]["org"]
            # Flag OFF — byte-identical to today: the single ``app_user`` read.
            row = (
                await session.execute(
                    text(
                        "SELECT id::text AS id, organization_id::text AS org "
                        "FROM app_user WHERE lower(email) = :email LIMIT 1"
                    ),
                    {"email": key},
                )
            ).mappings().first()
    except Exception:  # noqa: BLE001
        return None, None
    if row is None:
        return None, None
    return row["id"], row["org"]


#: The address that holds ``owner`` in the organization with a given slug. Joins
#: user_role → org_role('owner') → app_user, scoped to the org by slug. One row
#: is returned because an org has one owner in practice; ``LIMIT 1`` makes the
#: read total even if a historical co-owner exists.
_ORG_OWNER_SQL = """
    SELECT au.email AS email
      FROM organization o
      JOIN org_role r ON r.organization_id = o.id AND r.slug = 'owner'
      JOIN user_role ur ON ur.role_id = r.id
      JOIN app_user au ON au.id = ur.user_id
     WHERE o.slug = :slug
     LIMIT 1
"""


async def org_owner_of(slug: str | None) -> str | None:
    """Return the email that OWNS the organization with this ``slug``, or ``None``.

    A plain identity READ, the sibling of :func:`resolve_identity`, placed here
    for CP-2c step 0's pre-flight ``SlugTaken`` classification — the read analogue
    of the Console's ``membership_of``, so a self-serve signup can classify a
    taken slug BEFORE any write, exactly as migration 180's create-only guard
    refuses it at write time (``saas_multitenancy.md`` §11 slice 7). ``None`` for
    an unknown slug, an unplaced slug, or a slug whose org has no owner yet (the
    crash-resume shape — not a conflict). **This read is UX-only, not the
    boundary:** it never raises (a lookup error resolves to ``None``, matching
    :func:`resolve_identity`'s posture), so on a transient failure it returns
    ``None`` = "not taken" and the signup proceeds to the write — where
    migration 180's guard is the authoritative refusal (``SlugOwnedByAnother``).
    Degrading open here trades a friendly pre-flight "that name is taken" for a
    hard write-time error on the rare read failure; it can never admit a hijack,
    because the DB-level guard, not this read, is what enforces create-only.
    """
    if not slug:
        return None
    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(text(_ORG_OWNER_SQL), {"slug": slug})
            ).mappings().first()
    except Exception:
        return None
    if row is None:
        return None
    return row["email"]


#: The organization a person already belongs to, by their globally-unique
#: ``app_user`` row. ``app_user`` is unique on ``lower(email)`` since migration
#: 162, so one address is a member of at most one organization; the join carries
#: the org's ``slug`` and ``display_name`` back so a caller can NAME the caller's
#: OWN org without a second read.
_MEMBERSHIP_SQL = """
    SELECT o.slug         AS slug,
           o.display_name AS display_name
      FROM app_user au
      JOIN organization o ON o.id = au.organization_id
     WHERE lower(au.email) = :email
     LIMIT 1
"""


async def membership_of(email: str | None) -> tuple[str, str] | None:
    """Return ``(org_slug, org_display_name)`` for the org this email belongs to.

    A plain identity READ, the sibling of :func:`org_owner_of` and
    :func:`resolve_identity`, placed here for CP-2c step 0a's ``AlreadyMember``
    pre-flight — the tenant-plane analogue of the Console's ``membership_of``.
    It reads ``app_user ⋈ organization``; ``app_user`` is globally unique on
    ``lower(email)`` (migration 162), so a hit is the caller's ONE own
    organization. Returns the caller's OWN org (slug + display_name), or
    ``None``.

    ``None`` means **"not a member"** — no ``app_user`` row, or a row with a
    NULL ``organization_id``. It is the answer a self-serve signup pre-flight
    wants: *nobody by this address yet, proceed*.

    **This read is UX-only, not the boundary, and it fails OPEN.** A transient
    lookup error resolves to ``None`` (same posture as :func:`org_owner_of` and
    :func:`resolve_identity`), because the DB-level guards are authoritative: a
    ``None`` from a read failure just lets the signup proceed to the WRITE,
    where migration 180's create-only guard / the cross-tenant guard
    (``SlugOwnedByAnother`` / ``OwnerBelongsElsewhere``) is the real refusal.
    Degrading open here can never admit a hijack — it only trades a friendly
    pre-flight ``AlreadyMember`` for the write-time typed raise on the rare read
    failure, which CP-2c's route maps to the SAME code.
    """
    if not email:
        return None
    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(_MEMBERSHIP_SQL), {"email": email.lower().strip()}
                )
            ).mappings().first()
    except Exception:
        return None
    if row is None:
        return None
    return row["slug"], row["display_name"]


# ── Shared-session authority (groups_sessions_authority.md §3) ──────────────

#: More per-session subjects than this is a data error, not a room.
_MAX_PARTICIPANT_EXPANSION = 200

_PARTICIPANT_SQL = """
SELECT subject FROM chat_session_participant WHERE session_id = :sid
"""

#: Both expansions are scoped to the actor's organization, joined in as `actor`.
#:
#: `org_group.slug` is unique only *within* an organization (`UNIQUE
#: (organization_id, slug)`, `138_groups_and_session_participants.sql:49`), so
#: the slug-only join matched every tenant's group of that name at once; and the
#: `org` subject, with no filter at all, expanded to every active user on the
#: box (`saas_multitenancy.md` §6.4, §6.5 — under D15 the tenant boundary is a
#: row, so both leak for real). This is the most consequential place to get it
#: wrong: `resolve_session_access` folds an *intersection*, and admitting a
#: member who was never in the room widens the fold rather than narrowing it.
#:
#: The organization is derived from the acting user's own `app_user` row, not
#: from a literal org slug, so it cannot go stale (`tenancy_and_visibility.md`
#: §2 done-when 1). An actor with no `app_user` row — or a member row with a
#: NULL `organization_id`, which migration 130 backfilled but nothing enforces
#: — matches nothing and drops out of the expansion. That fails *closed*: the
#: run keeps the actor's own access instead of borrowing someone else's.
_GROUP_MEMBER_SQL = """
SELECT au.email
FROM org_group g
JOIN org_group_member m ON m.group_id = g.id
JOIN app_user au ON au.id = m.user_id
JOIN app_user actor ON lower(actor.email) = :actor_email
WHERE g.slug = :slug
  AND au.status = 'active'
  AND g.organization_id = actor.organization_id
  AND au.organization_id = actor.organization_id
"""

_ORG_MEMBER_SQL = """
SELECT au.email
FROM app_user au
JOIN app_user actor ON lower(actor.email) = :actor_email
WHERE au.status = 'active'
  AND au.organization_id = actor.organization_id
"""


async def resolve_session_access(
    session_id: str | None,
    actor_email: str | None,
) -> tuple[EffectiveAccess, list[str]]:
    """The authority a run in *session_id* acts with, and whose it is.

    The rule (``groups_sessions_authority.md`` §3): **a shared run acts with
    the intersection of every participant's resolved access — viewers
    included — attributed to the typer.** Actor-authority ("whoever typed")
    leaks by construction: the permitted member's tool output lands in a
    transcript the denied member reads.

    Mechanics:

    * Participant subjects are expanded at read time — an email is itself,
      ``group:<slug>`` becomes the group's active members, ``org`` becomes
      every active member (an org-visible room is readable by all of them,
      so all of them cap it). Both expansions stay inside the *actor's*
      organization; see ``_GROUP_MEMBER_SQL``.
    * The actor is always included, so a solo session — or any session
      recorded before migration 138 — resolves to exactly the actor's own
      access, byte-identically to today. Everything here activates only when
      a second distinct member exists.
    * Each member resolves through :func:`resolve_access` (shared 60s cache);
      a suspended member resolves inactive, which zeroes the intersection —
      the room is capped until they are removed, which is the visible act.
    * Fail-open to actor-only on any lookup error, matching
      :func:`resolve_access`'s posture: the sharing feature must not become
      a new way for a solo run to lose its authority. (Pre-133 databases land
      here via the missing-table branch.)

    Returns ``(access, members)`` where *members* is the sorted list of
    emails the intersection covered — the provenance the room UI shows, so a
    cap is never silent.
    """
    actor = (actor_email or "").lower().strip()
    actor_access = await resolve_access(actor)
    if not session_id:
        return actor_access, [actor] if actor else []

    emails: set[str] = {actor} if actor else set()
    try:
        from sqlalchemy import text

        # H6 — the participant/member expansion under phase-4 RLS. This block
        # reads `chat_session_participant`, `org_group_member` and `app_user`
        # (via `_PARTICIPANT_SQL` / `_ORG_MEMBER_SQL` / `_GROUP_MEMBER_SQL`), all
        # FORCE-RLS'd (`generated/04_policies.sql`), so an UNBOUND read returns
        # ZERO rows once the cutover is live and the shared-run authority would
        # silently collapse to actor-only. When the cutover is ON and a tenant is
        # bound — the request path, where `deps._with_resolved_access` binds the
        # resolved org before `assert_can_run_agent_in_session` reaches here —
        # read through the ONE GUC seam `tenant_session()` (`SET LOCAL
        # app.tenant_id`, reused not forked — R5), exactly as `resolve_access`'s
        # role leg does. Flag OFF, or ON with nothing bound, stays the raw
        # unbound session: byte-identical to today. The two BACKGROUND callers —
        # `executor._integration_authorizer` and `chat_fold._run_authority` —
        # run with no request context, so `current_tenant()` is None and they
        # take that same unbound branch: never `TenantUnbound` (the bind branch
        # is entered only when a tenant is actually bound), fail-closed under
        # phase-4 RLS (0 rows → the fold degrades to actor-only) until H4 threads
        # an explicit tenant through those service paths (§H6 pre-flip checklist).
        if identity_cutover_enabled() and current_tenant() is not None:
            session_cm = tenant_session()
        else:
            session_cm = _get_session_factory()()
        async with session_cm as session:
            subjects = [
                r[0]
                for r in (
                    await session.execute(
                        text(_PARTICIPANT_SQL), {"sid": session_id},
                    )
                ).fetchall()
            ]
            for subject in subjects:
                s = (subject or "").strip()
                if not s:
                    continue
                if s == "org":
                    rows = (
                        await session.execute(
                            text(_ORG_MEMBER_SQL), {"actor_email": actor},
                        )
                    ).fetchall()
                    emails.update(r[0].lower() for r in rows if r[0])
                elif s.startswith("group:"):
                    rows = (
                        await session.execute(
                            text(_GROUP_MEMBER_SQL),
                            {"slug": s[len("group:"):], "actor_email": actor},
                        )
                    ).fetchall()
                    emails.update(r[0].lower() for r in rows if r[0])
                elif "@" in s:
                    emails.add(s.lower())
                if len(emails) > _MAX_PARTICIPANT_EXPANSION:
                    raise ValueError(
                        f"session {session_id!r} expands past "
                        f"{_MAX_PARTICIPANT_EXPANSION} members"
                    )
    except Exception as exc:
        message = str(exc).lower()
        if "does not exist" in message or "undefinedtable" in message:
            _log.debug("session_access_tables_missing", session=session_id)
        else:
            _log.warning(
                "session_access_resolve_failed",
                session=session_id, error=str(exc)[:200],
            )
        return actor_access, sorted(emails)

    if len(emails) <= 1:
        return actor_access, sorted(emails)

    folded = actor_access
    for email in sorted(emails - {actor}):
        folded = folded.intersect(await resolve_access(email))
    return folded, sorted(emails)


# ── Ownership bootstrap (the way back in) ───────────────────────────────────

#: The organization this deployment bootstraps an owner into.
#:
#: Still a literal — provisioning a *customer* organization's first owner is
#: onboarding's job, not an env var's, and EXECUTIVE_EMAILS names the operator
#: of this box. Named once and bound into both queries below so the guard and
#: the insert can never disagree about which organization they mean; the guard
#: used to answer a different question from the insert, which is the whole bug
#: (`tenancy_and_visibility.md` §1.1 site 9).
_BOOTSTRAP_ORG_SLUG = "default"

_BOOTSTRAP_OWNER_SQL = """
WITH org AS (
    SELECT id FROM organization WHERE slug = :org_slug
),
member AS (
    INSERT INTO app_user (email, display_name, role, status,
                          organization_id, joined_at)
    SELECT :email, :email, 'executive', 'active', org.id, now() FROM org
    -- `(lower(email))`, not `(email)`: migration 162 replaced `app_user`'s
    -- byte-exact `app_user_email_key` with `app_user_email_lower_key ON
    -- app_user (lower(email))`, and an `ON CONFLICT` target must name an index
    -- that EXISTS. Against the post-162 ladder `(email)` raises 42P10 —
    -- `there is no unique or exclusion constraint matching the ON CONFLICT
    -- specification` — at PLAN time, so it took out the fresh-insert path too,
    -- not just the conflict one, and `ensure_owner_bootstrap()`'s catch-all
    -- turned that into a silent `ownership_bootstrap_failed`: an ownerless box
    -- that never bootstraps and never says so. Reproduced red against a
    -- ladder-replayed Postgres before this line changed (WS-29 MT-1j slice 6,
    -- `saas_multitenancy.md` §11). Fence: `tests/unit/test_app_user_upserts.py`.
    ON CONFLICT (lower(email)) DO UPDATE
        SET status = 'active',
            organization_id = COALESCE(app_user.organization_id,
                                       EXCLUDED.organization_id)
    RETURNING id
)
-- H6 slice 4 (D48) DUAL-WRITE: the RBAC re-key EXPAND shadows `user_identity_id`
-- on every RBAC INSERT so migration 184's column stays current. At bootstrap the
-- identity is created by `mirror_identity_membership` AFTER this commit, so the
-- LEFT JOIN resolves NULL on a fresh box (correct — "NULL only where no identity
-- exists"; the mirror + a later backfill re-run reconcile it). `app_user.id`
-- stays the AUTHORITATIVE key; this only READS `user_identity`, never writes it.
INSERT INTO user_role (user_id, role_id, assigned_by, user_identity_id)
SELECT member.id, r.id, 'bootstrap:executive_emails', ui.id
FROM member, org_role r, org
LEFT JOIN user_identity ui ON lower(ui.email) = lower(:email)
WHERE r.organization_id = org.id AND r.slug = 'owner'
ON CONFLICT DO NOTHING
"""

#: Does *this* organization have an owner — not "does an owner exist anywhere".
#:
#: `org_role` is per-organization, so an unscoped `r.slug = 'owner'` answered a
#: question nobody asked: once any one tenant had an owner, the guard below went
#: permanently false and `ensure_owner_bootstrap()` became a no-op for an
#: organization that had none. That is a **lockout, not a leak** — no owner
#: means no inviter, and the only way in is hand-run SQL. RLS does not fix it,
#: because the defect is a missing WHERE on a startup path, not a visible row
#: (`saas_multitenancy.md` §6.4; `tenancy_and_visibility.md` §1.1 site 9).
_HAS_OWNER_SQL = """
SELECT 1 FROM user_role ur
JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner'
JOIN organization o ON o.id = r.organization_id AND o.slug = :org_slug
LIMIT 1
"""


async def ensure_owner_bootstrap() -> str | None:
    """If NOBODY holds ``owner``, provision the first ``EXECUTIVE_EMAILS``
    address as an active owner — creating the ``app_user`` row if needed.

    Exists because of the 2026-07-30 production lockout: the model is
    invite-only ("an admin invites people"), and migration 128's SQL
    bootstrap can only *promote an existing row* — SQL cannot read env vars,
    so the spec's promised EXECUTIVE_EMAILS fallback was never implementable
    there. On a deployment where ``app_user`` was empty, that left zero
    members, zero owners, and **no inviter**: everyone authenticated, nobody
    provisioned, and the only fix was hand-run SQL. This closes the loop at
    gateway startup, the first place both the database AND the environment
    are readable.

    Deliberately narrow: it runs only when nobody holds ``owner`` **in the
    organization it would provision into** (``_BOOTSTRAP_ORG_SLUG``) — one
    real owner there (however provisioned) makes this a no-op forever, so a
    stale or placeholder EXECUTIVE_EMAILS can never overwrite real
    membership. The scoping is the 2026-08-08 fix: unscoped, another tenant's
    owner satisfied the guard and re-locked this deployment out of its own
    bootstrap. Returns the provisioned email, or ``None`` when it did
    nothing. Never raises: an ownerless deployment with a broken bootstrap
    should still boot and serve /health, not crash-loop.
    """
    raw = os.environ.get("EXECUTIVE_EMAILS", "")
    candidate = next(
        (e.strip().lower() for e in raw.split(",") if "@" in e), None,
    )
    try:
        from sqlalchemy import text  # noqa: PLC0415

        # H6 — bind the has-owner read + the bootstrap INSERT under phase-4 RLS.
        # `_HAS_OWNER_SQL` (via `user_role`) and `_BOOTSTRAP_OWNER_SQL`'s INSERT
        # both touch the FORCE-RLS'd `app_user`/`user_role`, and this runs at
        # gateway STARTUP with no request — so nothing binds and, under phase-4,
        # the read says "ownerless" while the INSERT's WITH CHECK compares
        # `organization_id` against the unset GUC (NULL) and REFUSES it: an
        # ownerless box could never bootstrap its first owner (the still-open
        # write-brick §H6 flagged; characterized by
        # `test_the_unbound_owner_bootstrap_write_is_rejected`). Fix: resolve the
        # default org's id from the RLS-EXEMPT `organization` table (readable
        # unbound — `gen_tenant_migration.EXEMPT`), then run both statements
        # inside `tenant_session(default_org_id)` — the ONE GUC seam, reused not
        # forked (R5). The `default` org row is seeded (migration 178), so the
        # bind is possible; if it is somehow absent there is nothing to bootstrap
        # into, so fall back to the raw unbound session, byte-identical to the
        # pre-H6 path (and a no-op under phase-4 RLS — fail-closed).
        factory = _get_session_factory()
        async with factory() as probe:
            org_row = (
                await probe.execute(
                    text("SELECT id FROM organization WHERE slug = :org_slug"),
                    {"org_slug": _BOOTSTRAP_ORG_SLUG},
                )
            ).first()
        default_org_id = str(org_row[0]) if org_row else None

        session_cm = (
            tenant_session(default_org_id)
            if default_org_id is not None
            else factory()
        )
        async with session_cm as session:
            has_owner = await session.execute(
                text(_HAS_OWNER_SQL), {"org_slug": _BOOTSTRAP_ORG_SLUG},
            )
            if has_owner.first() is not None:
                return None
            if candidate is None:
                _log.warning(
                    "ownership_bootstrap_no_candidate",
                    detail=(
                        "no member holds 'owner' and EXECUTIVE_EMAILS names "
                        "no address — nobody can grant access; set "
                        "EXECUTIVE_EMAILS or provision an owner by SQL"
                    ),
                )
                return None
            await session.execute(
                text(_BOOTSTRAP_OWNER_SQL),
                {"email": candidate, "org_slug": _BOOTSTRAP_ORG_SLUG},
            )
            # `tenant_session()` commits on exit; the raw-session fallback does
            # not, so commit explicitly only in that branch.
            if default_org_id is None:
                await session.commit()
        invalidate(candidate)
        # H6 slice 1: keep the identity shadow current. Best-effort and after the
        # authoritative commit above — a failure here changes nothing about the
        # owner that was just provisioned.
        await mirror_identity_membership(
            email=candidate, display_name=candidate,
            org_slug=_BOOTSTRAP_ORG_SLUG, status="active",
        )
        _log.warning(
            "ownership_bootstrapped",
            email=candidate,
            detail="no member held 'owner'; provisioned from EXECUTIVE_EMAILS",
        )
        return candidate
    except Exception as exc:
        _log.warning("ownership_bootstrap_failed", error=str(exc)[:200])
        return None
