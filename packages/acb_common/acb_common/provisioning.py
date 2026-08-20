"""Tenant-plane organization provisioning — the caller for migration 179.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1j slice 4b · D43-A
(one SQL function, not a role-template table) · D43-C (building and R8-testing
this is agent-safe; EXECUTING it against a real second organization is the
``work_plan.md`` §6 owner gate, because H3's RLS promotion has not happened).

**One function, one statement, no policy.** ``provision_organization`` in
``infra/postgres/179_org_provisioning.sql`` is the whole act — the
``organization`` row, its ``tenant_placement``, the six system roles and
(optionally) a named owner, idempotent on the slug and atomic within the
calling statement. This module is the seam that lets Python invoke it. It adds
no rules of its own on purpose:

* **No client-side validation.** A blank slug is refused *by 179*, and the
  exception reaches the caller unchanged. A guard here would be a second
  refusal that can drift from the one the database actually enforces — and a
  test asserting the guard would be asserting this file rather than the
  contract (the vacuity the 2026-08-19 audit killed).
* **No second engine.** The session comes from
  ``acb_common.db.get_session_factory()`` — the ONE async engine and pool for a
  process (BO-10, R5(b)). This module is placed beside it, next to
  :mod:`acb_common.placement`, which READS the ``tenant_placement`` row this act
  writes and refuses rather than guessing when it is missing.

  ⚠️ **Deliberately NOT ``tenant_session()`` and deliberately NOT ``get_db()``,
  and both halves are decisions.** ``tenant_session()`` binds
  ``app.tenant_id`` and raises ``TenantUnbound`` with nothing bound — but this
  function *creates* the tenant, so there is no organization to bind to until
  after the statement it would have wrapped. That is the same argument
  ``acb_auth.console_resolve`` makes for sign-in ("the tenant is the ANSWER"),
  and this is the same acquisition idiom it and ``acb_auth.access`` use, so
  H2's conversion stays mechanical. ``get_db()`` is the *other* wrong answer:
  its own docstring says it "stays for the ~200 existing call sites, which
  MT-1c converts; it is not a second sanctioned way in", and
  ``tests/unit/test_db_engine_seam.py::
  test_get_db_sites_elsewhere_only_ratchet_down`` freezes their count — a new
  one there is new debt against a ratchet that may only go down.
* **No tenant from request input.** ``slug`` and ``owner_email`` are what an
  operator (or, later, CP-2c's route behind its own credential) names. This
  function creates a tenant; it never *resolves* the acting one, and nothing
  here reads ``current_tenant()``. ``user_management_contract.md`` R11 is about
  the acting identity, and provisioning is not an act performed *as* the
  organization being created.

⚠️ **Its first caller is CP-2c's self-serve signup route
(`gateway/routes/signup.py`), built 2026-08-20 — step 1 of the two-plane
orchestration.** It is not yet reachable *in production*: signup is behind
`SELF_SERVE_SIGNUP_ENABLED` (default OFF) and the Console it mirrors to is
deployed nowhere (D47). So the "callable nothing calls" note that used to live
on 179 is retired — it has a caller now; it is simply dark (spec §11 MT-1j
slice 4b; CP-2c slice 2).
"""
from __future__ import annotations

from acb_common import get_logger

_log = get_logger("provisioning")


# ── The two typed, catchable refusals (slice 7) ─────────────────────────────
#
# Migration 180 tags provision_org_owner's TWO caller-recoverable refusals with
# DEDICATED, DISTINCT SQLSTATEs so this seam can classify on the code ALONE —
# never on Postgres prose (the gap the "deliberately not translated" block below
# used to leave, sanctioned for repair by spec §11 slice 7). Custom class P1:
# Postgres defines only class P0 (PL/pgSQL), so P1001/P1002 collide with nothing
# built in — and deliberately NOT 23505 (unique_violation), which the genuine
# app_user / user_role unique constraints in the SAME function also raise.

#: SQLSTATE migration 180 raises when the owner_email's ``app_user`` row already
#: belongs to a DIFFERENT organization (the cross-tenant guard, re-tagged from
#: the generic P0001 it shared in 179).
SQLSTATE_OWNER_BELONGS_ELSEWHERE = "P1001"

#: SQLSTATE migration 180's create-only guard raises when the target org already
#: holds an ``owner`` role for an address other than the one provisioning it.
SQLSTATE_SLUG_OWNED_BY_ANOTHER = "P1002"


class ProvisioningRefused(Exception):
    """Base for a tenant-plane provisioning refusal a CALLER can recover from.

    The two subclasses below are what a self-serve signup route (CP-2c slice 2)
    catches to answer "slug taken" / "email already elsewhere" distinctly. The
    two NON-recoverable raises (a blank slug, an org with no owner role) stay a
    plain :class:`sqlalchemy.exc.DBAPIError` — they are programming errors, not
    signup outcomes, and are never translated to this hierarchy.
    """


class OwnerBelongsElsewhere(ProvisioningRefused):
    """The ``owner_email`` already belongs to a DIFFERENT organization.

    Migration 180 raises SQLSTATE ``P1001``. Adopting the address would MOVE a
    member between tenants (S1-1's cross-tenant write) and attach this org's
    ``owner`` role to a row that stays in the other tenant. Distinct from
    :class:`SlugOwnedByAnother`: this is about the EMAIL, not the slug.
    """


class SlugOwnedByAnother(ProvisioningRefused):
    """The target slug's organization is already OWNED by another address.

    Migration 180's create-only guard raises SQLSTATE ``P1002``. Completing it
    would make ``owner_email`` a co-owner of a stranger's organization — the
    tenant-plane twin of the slice-1 Console P0. Distinct from
    :class:`OwnerBelongsElsewhere`: this is about the SLUG, not the email.
    """


#: SQLSTATE → typed refusal. The ONLY dispatch table; classification reads this
#: keyed on the driver's SQLSTATE and nothing else (constraint C).
_REFUSAL_BY_SQLSTATE: dict[str, type[ProvisioningRefused]] = {
    SQLSTATE_OWNER_BELONGS_ELSEWHERE: OwnerBelongsElsewhere,
    SQLSTATE_SLUG_OWNED_BY_ANOTHER: SlugOwnedByAnother,
}


def _sqlstate(exc: Exception) -> str | None:
    """The SQLSTATE of a DBAPI error, from the DRIVER's own attribute.

    Never parsed from the message. SQLAlchemy wraps the driver error in
    ``.orig``; asyncpg (the async seam) and psycopg3 (the sync R8 shell) both
    expose ``.sqlstate``, psycopg2 exposes ``.pgcode``. Returns ``None`` when
    the exception carries no SQLSTATE at all.
    """
    orig = getattr(exc, "orig", exc)
    code = getattr(orig, "sqlstate", None)
    if code is None:
        code = getattr(orig, "pgcode", None)
    return code


def _translate_refusal(exc: Exception) -> ProvisioningRefused | None:
    """Map a dedicated-SQLSTATE refusal to its typed exception, else ``None``.

    Dispatches on :func:`_sqlstate` ALONE — the message is used only to carry
    the human-readable detail onto the typed exception, never to CLASSIFY.
    ``None`` means "not one of the two caller-recoverable refusals", so the seam
    re-raises the original error unchanged (constraints B + C).
    """
    cls = _REFUSAL_BY_SQLSTATE.get(_sqlstate(exc) or "")
    if cls is None:
        return None
    return cls(str(getattr(exc, "orig", exc)))

#: Every argument is CAST explicitly. 179's parameters are all TEXT, and an
#: untyped NULL bound through asyncpg's prepare step leaves the server to infer
#: a type for a parameter that has no value — which is how a call resolves to
#: "function … does not exist" against a function that plainly does. The casts
#: also document, at the one call site, that this is a 7-argument function and
#: not a shorter overload.
_PROVISION_SQL = """
SELECT provision_organization(
    CAST(:slug         AS TEXT),
    CAST(:display_name AS TEXT),
    CAST(:owner_email  AS TEXT),
    CAST(:domain       AS TEXT),
    CAST(:tier         AS TEXT),
    CAST(:target       AS TEXT),
    CAST(:region       AS TEXT)
)::text AS organization_id
"""


async def provision_local_organization(
    slug: str,
    display_name: str | None = None,
    owner_email: str | None = None,
    *,
    domain: str | None = None,
    tier: str = "pool",
    target: str = "primary",
    region: str = "ap-south-1",
) -> str:
    """Provision *slug* on THIS deployment's tenant database. Idempotent.

    Returns the ``organization.id`` — the local one, always; the Customer
    Console's UUID for the same customer is a different identifier in a
    different database and is deliberately never written here (D32.4, and
    ``test_deployment_resolve_cache.py::
    test_the_console_uuid_is_never_written_to_the_projection``). **The slug is
    the join key between the two planes.**

    Called twice with the same slug it converges: one organization, one
    placement, six roles, one owner. That is 179's property, not this
    function's — everything below is one statement.

    Args:
        slug: The organization's slug, and the cross-plane join key. Blank is
            refused by 179, not here.
        display_name: Defaults (in SQL) to the slug.
        owner_email: Optional. Absent, the organization lands with roles and a
            placement but no owner — recoverable, and the shape 179 argues for
            when the Console creates the customer before it knows who owns it.
        domain: Optional email domain for the organization row.
        tier: Placement tier — ``pool`` | ``bridge`` | ``silo`` (§1.5). The
            CHECK constraint lives on ``tenant_placement`` (migration 159) and
            is not restated here; a second copy of a constraint is a second
            thing to drift.
        target: Which database within the placement. ``primary`` on day one for
            everyone — the INDIRECTION is the point, not the value.
        region: Placement region.

    Raises:
        OwnerBelongsElsewhere: the ``owner_email`` already belongs to a
            DIFFERENT organization (migration 180's SQLSTATE ``P1001``).
        SlugOwnedByAnother: the target slug's organization is already owned by
            another address (migration 180's create-only guard, SQLSTATE
            ``P1002``) — the tenant-plane twin of the slice-1 Console P0.
        sqlalchemy.exc.DBAPIError: any OTHER database refusal, unchanged — a
            blank slug and an org with no owner role stay raw. They are the
            two NON-recoverable raises (generic P0001): a wrapper for them would
            be a second vocabulary for a programming error, not a signup outcome.

    ⚠️ **The translation slice 4 deferred, now supplied for slice 7's first
    caller (CP-2c slice 2).** Classification is on the driver's SQLSTATE ALONE
    (:func:`_translate_refusal`), never on Postgres prose — the two dedicated
    codes are the whole grammar.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    # Imported inside the function, like :mod:`acb_common.placement` does:
    # importing this module must not drag SQLAlchemy's async stack into a
    # process that never opens a connection (``acb_common.db``'s own rule).
    from acb_common.db import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            row = (
                await session.execute(
                    text(_PROVISION_SQL),
                    {
                        "slug": slug,
                        "display_name": display_name,
                        "owner_email": owner_email,
                        "domain": domain,
                        "tier": tier,
                        "target": target,
                        "region": region,
                    },
                )
            ).mappings().first()
            # A function that RETURNS UUID and did not raise returned a row; if
            # it somehow did not, committing would bank an unknown state.
            assert row is not None, "provision_organization returned no row"
            await session.commit()
    except DBAPIError as exc:
        # Map the two caller-recoverable refusals (180's dedicated SQLSTATEs) to
        # their typed exceptions; re-raise every other DBAPIError unchanged. The
        # session context manager above has already rolled back, so a refused
        # act has written nothing (the "refused, writes nothing" contract).
        typed = _translate_refusal(exc)
        if typed is not None:
            raise typed from exc
        raise

    organization_id = str(row["organization_id"])
    _log.info(
        "provisioning.local_organization",
        slug=slug,
        organization_id=organization_id,
        owner=bool(owner_email),
    )
    return organization_id


# ── CP-2e · the signup Console-mirror marker + GST profile (WS-31) ───────────
#
# Two additive writes onto the tenant `organization` row, both keyed on the slug
# and both through the ONE shared engine (`get_session_factory`, the same idiom
# `provision_local_organization` uses — no new engine site, R5(b); no new table;
# COLUMNS on the already-exempt `organization`, migration 181).
#
# ⚠️ **Both are best-effort by design — they never raise.** They are MIRROR
# writes onto a projection the Customer Console is the authority for, exactly the
# posture `acb_auth.console_resolve`'s projection writes take ("a failed cache
# write is a missing fallback, not a refusal"). A failed marker write only leaves
# the org for the next reconciler pass (idempotent-on-slug), and a failed profile
# write only degrades `gstin`/`billing_state` to NULL, which the sweep still
# re-drives faithfully on (customer_console.md CP-2e's stated residual). Raising
# would turn a SUCCESSFUL Console mirror into a 500 for the caller, which is the
# one thing neither the signup route nor the sweep may allow.
#
# ⚠️ **`mark_console_mirrored` is the SAME writer the signup route (on step-2
# success) and the reconciler (on a mirror success) both call — one function, one
# idiom, no second copy.** `console_mirrored_at IS NULL` in the WHERE keeps it
# monotonic: it stamps the marker exactly once and a re-drive of an already-marked
# org touches nothing.

_PERSIST_BILLING_PROFILE_SQL = """
    UPDATE organization
       SET gstin = :gstin,
           billing_state = :billing_state,
           updated_at = now()
     WHERE slug = :slug
"""

_MARK_CONSOLE_MIRRORED_SQL = """
    UPDATE organization
       SET console_mirrored_at = now(),
           updated_at = now()
     WHERE slug = :slug
       AND console_mirrored_at IS NULL
"""


async def persist_org_billing_profile(
    slug: str,
    *,
    gstin: str | None,
    billing_state: str | None,
) -> None:
    """Persist a signup's GST profile onto the tenant ``organization`` row.

    Called by the signup route AFTER step 1 (the tenant org now exists) and
    BEFORE step 2 (the Console mirror), so that a transient step-2 failure still
    leaves ``gstin``/``billing_state`` recorded for the reconciler to re-drive on
    — they are threaded to the Console in step 2 and persisted NOWHERE else on the
    tenant plane (migration 179's INSERT writes only slug/display_name/domain).

    Best-effort: a write failure is logged and swallowed. The org still exists and
    still works dark; the profile simply degrades to NULL, which the sweep
    tolerates (customer_console.md CP-2e).
    """
    try:
        from sqlalchemy import text

        from acb_common.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(_PERSIST_BILLING_PROFILE_SQL),
                {"slug": slug, "gstin": gstin, "billing_state": billing_state},
            )
            await session.commit()
    except Exception as exc:
        _log.warning(
            "provisioning.billing_profile_write_failed",
            slug=slug,
            error=str(exc)[:200],
        )


async def mark_console_mirrored(slug: str) -> bool:
    """Stamp ``organization.console_mirrored_at = now()`` for *slug*, once.

    The marker the reconciler's sweep selects on being NULL. Called on a
    Console-mirror SUCCESS by both the signup route (step-2 success) and the
    reconciler. ``console_mirrored_at IS NULL`` in the WHERE makes it idempotent
    and monotonic — a re-drive of an already-marked org marks nothing.

    Returns True iff this call stamped a row (a previously-unmirrored org).
    Best-effort: a write failure is logged, swallowed, and returns False, so the
    org is simply left for the next sweep (the mirror is idempotent-on-slug).
    """
    try:
        from sqlalchemy import text

        from acb_common.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(_MARK_CONSOLE_MIRRORED_SQL), {"slug": slug}
            )
            await session.commit()
            return (result.rowcount or 0) > 0
    except Exception as exc:
        _log.warning(
            "provisioning.console_marker_write_failed",
            slug=slug,
            error=str(exc)[:200],
        )
        return False
