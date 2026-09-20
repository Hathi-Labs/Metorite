"""Migration 207 — every app is open to everybody; actions are what a role narrows.

Owner directive, 2026-09-21: *"all the apps should be available to all the
people. It is just that, depending on their permission levels, they might not
be allowed to do certain actions."*

**What this is really testing is the boundary, not the grant.** Widening
`feature:` is the easy half and one query proves it. The half worth a suite is
that nothing ELSE widened with it — that a member who can now open the People
app still cannot edit a colleague, still cannot read the HR fields, and still
cannot reach the console that decides who can do what.

R8 for the migration itself (catalog and seed state need a real database) and
hermetic for the permission algebra, which is pure and has no business being
asked of Postgres.
"""

from __future__ import annotations

import os
import uuid

import pytest
from acb_auth.permissions import permission_matches

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

live = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


# ══════════════════════════════════════════════════════════════════════════
# The algebra — pure, and the reason the migration is safe
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("capability", [
    "admin:members:read",
    "admin:members:manage",
    "admin:roles:manage",
    "admin:access:manage",
    "admin:settings:manage",
    "admin:audit:read",
    "billing:purchase",
    "integrations:manage",
    "projects:settings:write",
    "memory:write_org",
    "apps:publish",
    "agents:manage",
])
def test_the_feature_wildcard_grants_no_capability(capability: str) -> None:
    """`feature:*` must not reach a single verb. This is the whole safety case.

    If it did, migration 207 would have quietly made every colleague an
    administrator while claiming to have only unhidden some panes.
    """
    assert not permission_matches("feature:*", capability)


def test_the_feature_wildcard_does_grant_every_app() -> None:
    for slug in ("chat", "tasks", "projects", "people", "approvals", "crm",
                 "notes", "dashboard", "artifacts", "memory", "workflows"):
        assert permission_matches("feature:*", f"feature:{slug}")


def test_the_wildcard_does_not_match_the_bare_prefix() -> None:
    """`feature:` alone is not a feature, and `feature:*` must not admit it.

    A trailing-colon permission is what a bad string concatenation produces,
    and admitting it would turn a formatting bug into an access grant.
    """
    assert not permission_matches("feature:*", "feature:")


# ══════════════════════════════════════════════════════════════════════════
# The migration — against a real database
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def eng():
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _perms(conn, slug: str, org=None) -> set[str]:
    from sqlalchemy import text
    sql = ("SELECT p.permission FROM org_role r "
           "  JOIN org_role_permission p ON p.role_id = r.id "
           " WHERE r.slug = :s")
    params = {"s": slug}
    if org is not None:
        sql += " AND r.organization_id = :o"
        params["o"] = org
    return set(conn.execute(text(sql), params).scalars().all())


@live
@pytest.mark.parametrize("slug", ["member", "manager"])
def test_the_role_holds_the_wildcard_and_no_leftover_list(eng, slug: str) -> None:
    """The list is REPLACED, not appended to.

    Leaving the old explicit rows beside the wildcard would show the Roles
    screen the rule and its own history at once, which is the "why are both
    here?" the migration exists to avoid.
    """
    with eng.begin() as conn:
        perms = _perms(conn, slug)
    features = {p for p in perms if p.startswith("feature:")}
    assert features == {"feature:*"}, features


@live
def test_a_member_keeps_exactly_the_capabilities_it_had(eng) -> None:
    """The delete is scoped to `feature:`, and this is what says so.

    A `LIKE 'feature:%'` that had been written `LIKE '%'` would pass the test
    above — the role would hold `feature:*` and nothing else — while silently
    stripping every capability the role needs.
    """
    with eng.begin() as conn:
        perms = _perms(conn, "member")
    assert {p for p in perms if not p.startswith("feature:")} == {
        "agents:run:*", "apps:use:*", "integrations:use:*", "memory:read_org",
    }


@live
def test_a_manager_keeps_the_grant_that_makes_them_a_manager(eng) -> None:
    with eng.begin() as conn:
        perms = _perms(conn, "manager")
    assert "admin:members:read" in perms
    assert "data:org:read" in perms


@live
def test_guest_is_untouched(eng) -> None:
    """"All the people" is the organization's people.

    A guest is an external collaborator. Widening them is a different
    decision, and a wildcard that swept them in would have made it by
    accident.
    """
    with eng.begin() as conn:
        perms = _perms(conn, "guest")
    assert {p for p in perms if p.startswith("feature:")} == {"feature:chat"}


@live
def test_a_member_is_still_not_an_admin(eng) -> None:
    """The property the owner is relying on, resolved the way the app does.

    Organisation is gated on `is_admin`, which comes from
    `admin:members:read` and from no feature at all. A member holding every
    app must still not be able to open the console that decides who holds
    what.
    """
    from acb_auth import build_access

    with eng.begin() as conn:
        perms = _perms(conn, "member")
    access = build_access(sorted(perms))
    # `can_use_feature` is the app's own question, asked the app's own way.
    assert access.can_use_feature("people")
    assert access.can_use_feature("projects")
    # And the console that decides all of this stays shut.
    assert not access.has("admin:members:read")
    assert not access.has("admin:members:manage")
    assert not access.has("admin:roles:manage")
    # The decision carries its REASON, so a future widening cannot pass this
    # by accident — "default-deny" is the only acceptable answer here.
    assert access.decide("admin:members:manage").source == "default-deny"


@live
def test_a_NEW_organization_gets_the_same_defaults(eng) -> None:
    """The second seed, which is the one that would have been forgotten.

    `provision_org_roles` carries its own copy of the bundles. Fixing only
    the existing rows would repair today's customers and re-break every one
    signed up afterwards — and nobody would notice until a new customer asked
    why their team cannot open Projects.
    """
    from sqlalchemy import text

    org = uuid.uuid4()
    slug = f"newco-{org.hex[:8]}"
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :s, 'New Co')"), {"id": org, "s": slug})
            conn.execute(text("SELECT provision_org_roles(:o)"), {"o": org})
            member = _perms(conn, "member", org)
            manager = _perms(conn, "manager", org)
        assert "feature:*" in member, member
        assert "feature:*" in manager, manager
        # And the capabilities still differ, so the seed was not flattened.
        assert "admin:members:read" in manager
        assert "admin:members:read" not in member
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM organization WHERE id = :id"),
                         {"id": org})


@live
def test_replaying_the_migration_changes_nothing(eng) -> None:
    from pathlib import Path

    from sqlalchemy import text

    sql = (Path(__file__).resolve().parents[2] / "infra" / "postgres"
           / "207_every_app_by_default.sql").read_text(encoding="utf-8")
    with eng.begin() as conn:
        before = _perms(conn, "member")
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(sql))
        conn.execute(text(sql))
    with eng.begin() as conn:
        assert _perms(conn, "member") == before
