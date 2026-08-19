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

⚠️ **Nothing calls this in production yet, and that is a stated state rather
than an oversight.** Its first caller is CP-2c's self-serve signup route; until
then the "callable nothing calls" note that used to live on 179 lives here,
honestly (spec §11 MT-1j slice 4b).
"""
from __future__ import annotations

from acb_common import get_logger

_log = get_logger("provisioning")

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
        Exception: whatever the database raised — 179's refusals reach the
            caller unchanged (a blank slug, an owner who already belongs to
            another organization, an organization with no owner role). They are
            deliberately not translated into a local exception type: the
            message names the function and the condition, and a wrapper here
            would be a second vocabulary for the same refusal.
    """
    from sqlalchemy import text

    # Imported inside the function, like :mod:`acb_common.placement` does:
    # importing this module must not drag SQLAlchemy's async stack into a
    # process that never opens a connection (``acb_common.db``'s own rule).
    from acb_common.db import get_session_factory

    factory = get_session_factory()
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
        # A function that RETURNS UUID and did not raise returned a row; if it
        # somehow did not, committing would bank an unknown state.
        assert row is not None, "provision_organization returned no row"
        await session.commit()

    organization_id = str(row["organization_id"])
    _log.info(
        "provisioning.local_organization",
        slug=slug,
        organization_id=organization_id,
        owner=bool(owner_email),
    )
    return organization_id
