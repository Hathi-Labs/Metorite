"""The signup pre-flight must SEE a member of another organization.

🔴 **Measured on production, 2026-09-26.** `vjvarada@hathilabs.com` owns Hathi
Labs LLP and tried to create a second organization. `membership_of` read
`app_user`, which is FORCE-RLS'd, UNBOUND, so it saw zero rows and answered
"not a member". The write then failed on the RLS policy, and the route told the
person "sign-in is temporarily unavailable" nine times in a row, instead of
"you already belong to Hathi Labs LLP".

⚠️ **The bypass role cannot catch this.** The ladder role bypasses RLS, so a
test run as it passes with or without the fix. These run the REAL function as
the NOSUPERUSER NOBYPASSRLS `acb_app` role, on the phase-4-promoted catalog the
H3 rehearsal builds (R8).
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _seed_member(promo, org: str, email: str) -> None:
    """As the admin: an `app_user` row AND its identity-leg mirror."""
    with promo.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "organization_id) VALUES (:e, :e, 'employee', 'active', :o)"),
            {"e": email, "o": org})
        ident = c.execute(text(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
            "RETURNING id"), {"e": email}).scalar_one()
        c.execute(text(
            "INSERT INTO org_membership (organization_id, user_id, status) "
            "VALUES (:o, :u, 'active')"), {"o": org, "u": ident})


def _purge(promo, email: str) -> None:
    with promo.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"), {"e": email})
        c.execute(text("DELETE FROM user_identity WHERE lower(email) = :e"), {"e": email})


class TestMembershipOfUnderForceRls:
    async def test_a_member_of_another_org_is_seen_by_the_unbound_preflight(
        self, promoted, app_engine,  # noqa: F811
    ):
        from acb_auth.access import membership_of

        email = f"member-{uuid.uuid4().hex[:8]}@rls.example"
        _seed_member(promoted, promoted.org_a, email)
        app_dsn = promoted.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                got = await membership_of(email)
            assert got is not None, (
                "the pre-flight answered 'not a member' for a member of another "
                "organization, so signup reached the RLS-refused write"
            )
            assert got[0] == "h3rls-a"
        finally:
            _purge(promoted, email)

    async def test_a_stranger_is_still_not_a_member(
        self, promoted, app_engine,  # noqa: F811
    ):
        from acb_auth.access import membership_of

        app_dsn = promoted.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            assert await membership_of(f"nobody-{uuid.uuid4().hex[:8]}@rls.example") is None
