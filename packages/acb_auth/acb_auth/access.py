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

# The one shared pool (BO-10) — see the Engine section below. Re-exported under
# the private name this module has always used.
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

        factory = _get_session_factory()
        async with factory() as session:
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


async def resolve_identity(email: str | None) -> tuple[str | None, str | None]:
    """Return ``(user_id, organization_id)`` for an email, or ``(None, None)``."""
    if not email:
        return None, None
    try:
        from sqlalchemy import text  # noqa: PLC0415

        factory = _get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id::text AS id, organization_id::text AS org "
                        "FROM app_user WHERE lower(email) = :email LIMIT 1"
                    ),
                    {"email": email.lower().strip()},
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
    crash-resume shape — not a conflict). Never raises: a lookup error resolves
    to ``None``, matching :func:`resolve_identity`'s posture, so a caller cannot
    mistake a transient failure for "slug is free".
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

        factory = _get_session_factory()
        async with factory() as session:
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
INSERT INTO user_role (user_id, role_id, assigned_by)
SELECT member.id, r.id, 'bootstrap:executive_emails'
FROM member, org_role r, org
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

        factory = _get_session_factory()
        async with factory() as session:
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
            await session.commit()
        invalidate(candidate)
        _log.warning(
            "ownership_bootstrapped",
            email=candidate,
            detail="no member held 'owner'; provisioned from EXECUTIVE_EMAILS",
        )
        return candidate
    except Exception as exc:
        _log.warning("ownership_bootstrap_failed", error=str(exc)[:200])
        return None
