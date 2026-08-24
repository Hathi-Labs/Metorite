"""D50.3's TENANT half — ``promote_invited_member`` at sign-in completion.

The Console promotes its registry membership at first resolve
(``store.activate_invited_member`` — fenced in
``test_customer_console_member_write.py``). WITHOUT the tenant twin the invited
colleague then dead-ends at the AccessGate: flag-OFF access reads
``app_user.status == "active"`` and flag-ON's identity leg filters
``m.status = 'active'``, so an ``invited`` row fails closed on both paths —
measured in review round 1, which is why this function exists. This suite is
its fence.

**R8** — every test here runs against a REAL Postgres built by the tenant
ladder (``TENANT_LADDER_DATABASE_URL``), because the promotion is two UPDATEs
whose guards live in SQL ``WHERE`` clauses, and a hermetic fake would agree
with whatever SQL it was handed (the five-live-bugs lesson).

⚠️ **Writes here are COMMITTED, not rolled back** — ``promote_invited_member``
opens its own sessions (that is its contract: best-effort, never rides the
caller's transaction), so a rollback fixture cannot contain it. Every test
seeds UNIQUE rows and deletes them in ``finally``.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _seed(conn, *, slug: str, email: str, om_status: str, au_status: str):
    """One org + one identity + one shadow membership + one app_user row."""
    org = str(
        conn.execute(
            text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"
            ),
            {"s": slug},
        ).scalar_one()
    )
    uid = str(
        conn.execute(
            text(
                "INSERT INTO user_identity (email, display_name) "
                "VALUES (:e, '') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()
    )
    conn.execute(
        text(
            "INSERT INTO org_membership (organization_id, user_id, status) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), :st)"
        ),
        {"o": org, "u": uid, "st": om_status},
    )
    conn.execute(
        text("INSERT INTO app_user (email, status) VALUES (:e, :st)"),
        {"e": email, "st": au_status},
    )
    return org, uid


def _cleanup(conn, *, email: str, org: str):
    conn.execute(
        text("DELETE FROM org_membership WHERE organization_id = CAST(:o AS uuid)"),
        {"o": org},
    )
    conn.execute(
        text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
        {"e": email},
    )
    conn.execute(text("DELETE FROM app_user WHERE lower(email) = lower(:e)"),
                 {"e": email})
    conn.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
                 {"o": org})


def _statuses(conn, *, email: str, org: str):
    au = conn.execute(
        text("SELECT status FROM app_user WHERE lower(email) = lower(:e)"),
        {"e": email},
    ).scalar_one_or_none()
    om = conn.execute(
        text(
            "SELECT status, joined_at FROM org_membership "
            " WHERE organization_id = CAST(:o AS uuid)"
        ),
        {"o": org},
    ).one_or_none()
    return au, (om[0] if om else None), (om[1] if om else None)


class TestThePromotion:
    async def test_an_invited_member_is_activated_on_both_tenant_tables(self, eng):
        """The headline: invited → active on ``app_user`` AND the shadow, with
        ``joined_at`` stamped — the AccessGate dead end is gone."""
        from acb_auth.access import promote_invited_member

        email = f"prom-{uuid.uuid4().hex[:8]}@x.example"
        slug = f"prom-{uuid.uuid4().hex[:8]}"
        with eng.begin() as conn:
            org, _ = _seed(conn, slug=slug, email=email,
                           om_status="invited", au_status="invited")
        try:
            async with tenant_engine_scope(_URL):
                await promote_invited_member(email=email)
            with eng.connect() as conn:
                au, om, joined = _statuses(conn, email=email, org=org)
            assert au == "active", "app_user must be promoted"
            assert om == "active", "the identity shadow must be promoted"
            assert joined is not None, "activation stamps joined_at"
        finally:
            with eng.begin() as conn:
                _cleanup(conn, email=email, org=org)

    @pytest.mark.parametrize("status", ["suspended", "removed", "active"])
    async def test_nothing_but_invited_is_ever_touched(self, eng, status):
        """The guard lives in the SQL ``WHERE`` on BOTH statements — the natural
        "anything not active → activate" silently un-suspends people, the exact
        failure ``colleague_onboarding.md`` §6 predicted. Shown red by removing
        ``AND m.status = 'invited'`` from ``_INVITED_MEMBERSHIPS_SQL``."""
        from acb_auth.access import promote_invited_member

        email = f"guard-{uuid.uuid4().hex[:8]}@x.example"
        slug = f"guard-{uuid.uuid4().hex[:8]}"
        with eng.begin() as conn:
            org, _ = _seed(conn, slug=slug, email=email,
                           om_status=status, au_status=status)
        try:
            async with tenant_engine_scope(_URL):
                await promote_invited_member(email=email)
            with eng.connect() as conn:
                au, om, _ = _statuses(conn, email=email, org=org)
            assert au == status and om == status
        finally:
            with eng.begin() as conn:
                _cleanup(conn, email=email, org=org)

    async def test_a_second_sign_in_is_a_no_op(self, eng):
        """Idempotent by construction: the shadow read finds no ``invited`` row
        the second time, so nothing runs — and ``joined_at`` keeps its first
        value (``COALESCE`` in the status mirror)."""
        from acb_auth.access import promote_invited_member

        email = f"twice-{uuid.uuid4().hex[:8]}@x.example"
        slug = f"twice-{uuid.uuid4().hex[:8]}"
        with eng.begin() as conn:
            org, _ = _seed(conn, slug=slug, email=email,
                           om_status="invited", au_status="invited")
        try:
            async with tenant_engine_scope(_URL):
                await promote_invited_member(email=email)
                with eng.connect() as conn:
                    _, _, first = _statuses(conn, email=email, org=org)
                await promote_invited_member(email=email)
            with eng.connect() as conn:
                au, om, again = _statuses(conn, email=email, org=org)
            assert (au, om) == ("active", "active")
            assert again == first, "joined_at is stamped once"
        finally:
            with eng.begin() as conn:
                _cleanup(conn, email=email, org=org)

    async def test_it_never_raises_on_garbage_or_a_missing_row(self, eng):
        """Best-effort contract: an address with no shadow row, an empty one and
        a non-address all return quietly — a failed promotion must never change
        the resolve answer (the caller admits either way; failing closed means
        the person just stays invited)."""
        from acb_auth.access import promote_invited_member

        async with tenant_engine_scope(_URL):
            await promote_invited_member(email=f"nobody-{uuid.uuid4().hex}@x.example")
            await promote_invited_member(email="")
            await promote_invited_member(email="not-an-address")


class TestTheSeamIsStructural:
    def test_the_app_user_write_runs_only_inside_tenant_session(self):
        """``app_user`` is RLS-FORCED in production, so the authoritative UPDATE
        must run GUC-bound. A promoted-catalog replay is a heavier fixture than
        this repo carries per-suite, so the bind is fenced STRUCTURALLY: inside
        ``promote_invited_member``'s source, the ``tenant_session(`` context
        opens before ``_PROMOTE_APP_USER_SQL`` executes — and the ladder-run
        tests above prove the statements themselves. (``tenant_session``'s own
        behaviour under FORCE RLS is proven by the H3 rehearsal suite.)"""
        import inspect

        from acb_auth import access

        src = inspect.getsource(access.promote_invited_member)
        opens = src.index("tenant_session(")
        writes = src.index("_PROMOTE_APP_USER_SQL")
        assert opens < writes, (
            "the app_user promotion must execute inside the ONE GUC seam "
            "(tenant_session) — it is RLS-forced in production"
        )
        # The guard is in the SQL itself, not an `if` around it.
        assert "AND status = 'invited'" in access._PROMOTE_APP_USER_SQL
        assert "AND m.status = 'invited'" in access._INVITED_MEMBERSHIPS_SQL

    def test_the_resolve_route_calls_it_only_on_admit(self):
        """One call site, sign-in completion, admitted decisions only — the
        farmable-surface rule the route's own docstring carries."""
        import inspect

        from gateway.routes import signin

        src = inspect.getsource(signin.resolve_sign_in)
        assert "promote_invited_member" in src
        admit_branch = src.index("if decision.admit:")
        call = src.index("promote_invited_member(")
        assert admit_branch < call, "the promotion must sit under decision.admit"
