"""WS-29 H6 slice 1 (MT-1a-2) — the identity-shadow dual-write + catch-up backfill.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H6 ·
``saas_multitenancy.md`` §11 / §1.5 · D15.

**What this slice is.** After H3's phase-4 RLS, identity resolution reads
``app_user`` on an UNBOUND session and bricks; H6 moves it onto the RLS-EXEMPT
tables ``user_identity`` + ``org_membership``. Before ANY read can move, those
tables must be COMPLETE and CURRENT. Migration 159 seeded them ONCE; nothing kept
them current since. Slice 1 closes that gap WITHOUT moving a read — it is DARK:

* a **dual-write** on the two ``app_user`` write paths
  (``acb_auth.access.mirror_identity_membership``, invoked from
  ``_BOOTSTRAP_OWNER_SQL``'s caller and ``_PROVISION_MEMBER_SQL``'s), and
* a **catch-up backfill** migration that re-runs 159's idempotent seed for every
  member added since.

``app_user`` stays authoritative and its writes are byte-identical; no read moves.

⚠️ **The invariant this suite fences (§H6:672-674).** The invite path is "an
account-takeover primitive under two orgs": one email may hold membership in TWO
organizations, so ``org_membership`` is a CREATE-ONLY insert keyed on
``(organization_id, user_id)`` and an identity is NEVER moved between orgs. The
two-org membership test IS that fence (R7).

Two halves, and the split is deliberate (the ``test_app_user_upserts.py`` model):

* the **structural** half takes no database and must never skip — it pins the
  create-only guard, the functional-index target, the app_user statements staying
  un-polluted, and the backfill's idempotent shape; and
* the **R8** half answers to ``TENANT_LADDER_DATABASE_URL`` — never to
  ``DATABASE_URL`` (``test_app_user_upserts.py`` records the reason at length) —
  and proves against a REAL Postgres that one email holds membership in two orgs,
  that re-inviting never rewrites the first, and that the backfill reconciles and
  is idempotent. A fake agrees with any SQL it is handed, which is precisely how
  slice 6's ``ON CONFLICT (email)`` shipped green (R8).

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    uv run pytest tests/unit/test_h6_identity_shadow.py -v -rs
"""
from __future__ import annotations

import inspect
import os
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

#: The subjects are IMPORTED, never transcribed — a copy here would keep passing
#: after somebody edited the real statement.
from acb_auth.access import (
    _BOOTSTRAP_OWNER_SQL,
    _MIRROR_IDENTITY_SQL,
    _MIRROR_MEMBERSHIP_SQL,
    mirror_identity_membership,
)
from gateway.routes.admin._common import _PROVISION_MEMBER_SQL
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector (infra/postgres/01_schema.sql needs uuid-ossp AND vector). "
        "A skip here is not a pass; CI must set it. ⚠️ Do NOT export it as "
        "DATABASE_URL — see this module's docstring."
    ),
)

_ROOT = Path(__file__).resolve().parents[2]
_LADDER_DIR = _ROOT / "infra" / "postgres"


# ── The structural half: no database, and it must never skip ────────────────

def _backfill_migration() -> Path:
    """The catch-up migration, found BY CONTENT never by number (R1).

    159 and the catch-up are the only two ``.sql`` files that seed BOTH shadow
    tables from ``app_user``. 159 also ``CREATE TABLE``s them; the catch-up only
    seeds — that is the content discriminator, so a merge renumber cannot make
    this fence point at the wrong file or go vacuous.
    """
    seeds = []
    for path in sorted(_LADDER_DIR.glob("*.sql")):
        if not re.match(r"^\d+_", path.name):
            continue
        body = path.read_text(encoding="utf-8")
        if "INSERT INTO user_identity" in body and "INSERT INTO org_membership" in body:
            seeds.append((path, body))
    catchup = [p for p, body in seeds if "CREATE TABLE" not in body]
    assert len(catchup) == 1, (
        "expected exactly one catch-up backfill migration (seeds both shadow "
        f"tables, creates neither); found {[p.name for p in catchup]}"
    )
    return catchup[0]


class TestTheDualWriteShape:
    """The ratchet. Breaking the invariant must fail here even without a database."""

    def test_the_membership_mirror_is_create_only(self):
        """§H6:672-674: INSERT, never an UPDATE of an identity's org.

        ``ON CONFLICT (organization_id, user_id) DO NOTHING`` and NO
        ``organization_id`` in any SET clause — the account-takeover primitive is
        exactly ``DO UPDATE SET organization_id = …``.
        """
        assert re.search(
            r"ON\s+CONFLICT\s*\(\s*organization_id\s*,\s*user_id\s*\)\s*DO\s+NOTHING",
            _MIRROR_MEMBERSHIP_SQL,
            re.IGNORECASE,
        ), "the membership mirror is not create-only on (organization_id, user_id)"
        assert "DO UPDATE" not in _MIRROR_MEMBERSHIP_SQL.upper(), (
            "the membership mirror rewrites on conflict — it must DO NOTHING"
        )
        assert not re.search(
            r"SET[\s\S]*organization_id", _MIRROR_MEMBERSHIP_SQL, re.IGNORECASE
        ), "the membership mirror sets organization_id — it must never move an org"

    def test_the_identity_mirror_targets_the_functional_index(self):
        """One row per ``lower(email)`` (162's index; R10)."""
        assert re.search(
            r"ON\s+CONFLICT\s*\(\s*lower\(email\)\s*\)", _MIRROR_IDENTITY_SQL,
            re.IGNORECASE,
        ), "the identity mirror does not target (lower(email))"

    def test_the_app_user_statements_are_not_polluted_by_the_dual_write(self):
        """The regression fence: ``app_user`` writes stay byte-identical.

        The dual-write is purely additive and lives in a SEPARATE statement on a
        SEPARATE session, so neither authoritative upsert may name a shadow
        table. If one does, the ``app_user`` write is no longer what it was.
        """
        for label, sql in (
            ("_BOOTSTRAP_OWNER_SQL", _BOOTSTRAP_OWNER_SQL),
            ("_PROVISION_MEMBER_SQL", _PROVISION_MEMBER_SQL),
        ):
            assert "user_identity" not in sql, f"{label} now names user_identity"
            assert "org_membership" not in sql, f"{label} now names org_membership"

    def test_the_mirror_is_best_effort(self):
        """Mirrors ``_record_signin_request``: it catches and never re-raises, so
        a failed shadow write can never break the authoritative ``app_user`` one."""
        src = inspect.getsource(mirror_identity_membership)
        assert "except Exception" in src, "the mirror does not swallow its errors"
        # No bare `raise` anywhere in the body — a re-raise would defeat the
        # best-effort posture and could surface on the app_user write path.
        assert not re.search(r"\braise\b", src), (
            "the mirror re-raises — it must be best-effort/log-and-continue"
        )

    def test_the_backfill_reruns_159s_seed_idempotently(self):
        """The catch-up is additive and never rewrites (R6)."""
        body = _backfill_migration().read_text(encoding="utf-8")
        assert body.count("ON CONFLICT") >= 2, (
            "the backfill's two inserts are not both conflict-guarded"
        )
        assert "DO NOTHING" in body, "the backfill is not idempotent (no DO NOTHING)"
        assert "CREATE TABLE" not in body, (
            "the backfill creates a table — it must only seed (R5: no new table)"
        )
        assert not re.search(r"SET[\s\S]*organization_id", body, re.IGNORECASE), (
            "the backfill rewrites organization_id — it must never move an org"
        )
        # No `role` column on the tenant-plane org_membership — do not invent one.
        assert "role" not in body.lower().split("org_membership", 1)[-1][:400], (
            "the backfill references a role column org_membership does not have"
        )

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """The hand-list discovers NOTHING (pr-check.yml's own warning): an R8
        suite absent from it skips silently and leaves the job green."""
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_h6_identity_shadow.py" in workflow

    def test_the_owning_spec_names_this_suite(self):
        spec = (_ROOT / "project-docs/specs/saas_multitenancy_handover.md").read_text(
            encoding="utf-8")
        assert "test_h6_identity_shadow.py" in spec


# ── The R8 half: the dual-write + backfill against the replayed ladder ───────

@pytest.fixture(scope="module")
def replayed():
    """Build the tenant schema from ``infra/postgres/`` (incl. the catch-up
    migration), then replay it — twice, to prove replay-safety like the deploy."""
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(replayed):
    """One rolled-back transaction per test — writes never outlive a test."""
    with replayed.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def _new_org(conn, slug: str) -> str:
    return str(
        conn.execute(
            text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"
            ),
            {"s": slug},
        ).scalar_one()
    )


def _mirror_identity(conn, email: str, name: str = "") -> str:
    return str(
        conn.execute(
            text(_MIRROR_IDENTITY_SQL), {"email": email, "name": name}
        ).mappings().one()["id"]
    )


def _mirror_membership(conn, org: str, uid: str, status: str = "active") -> int:
    return conn.execute(
        text(_MIRROR_MEMBERSHIP_SQL), {"org": org, "uid": uid, "status": status}
    ).rowcount


def _identity_count(conn, email: str) -> int:
    return conn.execute(
        text("SELECT count(*) FROM user_identity WHERE lower(email) = lower(:e)"),
        {"e": email},
    ).scalar_one()


def _membership_orgs(conn, uid: str) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            text(
                "SELECT organization_id FROM org_membership "
                " WHERE user_id = CAST(:u AS uuid) ORDER BY organization_id"
            ),
            {"u": uid},
        )
    ]


def _membership_for_org(conn, org: str) -> int:
    return conn.execute(
        text(
            "SELECT count(*) FROM org_membership "
            " WHERE organization_id = CAST(:o AS uuid)"
        ),
        {"o": org},
    ).scalar_one()


def _run_backfill(conn) -> None:
    """Execute the catch-up migration file verbatim, inside ``conn``'s txn.

    Via the DBAPI cursor for the reason ``_tenant_ladder._exec_file`` documents:
    one file, many statements, and no client-side ``%`` placeholder pass.
    """
    sql = _backfill_migration().read_text(encoding="utf-8")
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


@_DB_GATE
class TestOneEmailInTwoOrgs:
    """§H6 done-when 2 + 3 — the load-bearing invariant, against real Postgres."""

    def test_one_email_holds_membership_in_two_orgs(self, conn):
        org_a = _new_org(conn, "h6-two-a")
        org_b = _new_org(conn, "h6-two-b")
        email = "shared.human@h6.example"

        uid = _mirror_identity(conn, email, "Shared Human")
        assert _mirror_membership(conn, org_a, uid) == 1
        assert _mirror_membership(conn, org_b, uid) == 1

        assert _identity_count(conn, email) == 1, "one email must be one identity"
        orgs = _membership_orgs(conn, uid)
        assert set(orgs) == {org_a, org_b}, (
            f"one identity must hold TWO memberships; got {orgs}"
        )
        assert len(orgs) == 2

    def test_re_inviting_into_a_different_org_never_rewrites_the_first(self, conn):
        """The create-only guard: a second org INSERTs a second row and the first
        is left EXACTLY as it was — the identity is never moved."""
        org_a = _new_org(conn, "h6-move-a")
        org_b = _new_org(conn, "h6-move-b")
        email = "poach.target@h6.example"
        uid = _mirror_identity(conn, email)

        assert _mirror_membership(conn, org_a, uid, status="active") == 1
        # Re-mirroring the SAME (org, uid) writes nothing — create-only.
        assert _mirror_membership(conn, org_a, uid, status="invited") == 0, (
            "the second mirror into the SAME org rewrote the row"
        )
        # A DIFFERENT org is a second membership, not a move.
        assert _mirror_membership(conn, org_b, uid) == 1
        assert _membership_orgs(conn, uid) == sorted([org_a, org_b])

        # org A's row is byte-unchanged — still 'active', still org A.
        row = conn.execute(
            text(
                "SELECT status, organization_id::text FROM org_membership "
                " WHERE user_id = CAST(:u AS uuid) "
                "   AND organization_id = CAST(:o AS uuid)"
            ),
            {"u": uid, "o": org_a},
        ).one()
        assert row[0] == "active", "org A's membership status was rewritten"
        assert row[1] == org_a, "the identity was moved between orgs"

    def test_the_identity_mirror_keeps_one_row_per_email(self, conn):
        """A UPN case change must not mint a second human (R10)."""
        uid1 = _mirror_identity(conn, "casey@alpha.example", "Casey")
        uid2 = _mirror_identity(conn, "Casey@Alpha.Example", "")
        assert uid1 == uid2, "a case-variant address minted a second identity"
        assert _identity_count(conn, "casey@alpha.example") == 1


@_DB_GATE
class TestTheCatchUpBackfill:
    """§H6 done-when: the backfill reconciles and is idempotent (R6)."""

    def _seed_app_user(self, conn, org: str, email: str, status: str = "active"):
        conn.execute(
            text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "                      organization_id, joined_at) "
                "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid), now())"
            ),
            {"e": email, "s": status, "o": org},
        )

    def test_the_backfill_reconciles_members_added_since_159(self, conn):
        """Members added AFTER 159 (no dual-write) get shadow rows on backfill."""
        org = _new_org(conn, "h6-backfill")
        emails = [f"bf-{i}@h6.example" for i in range(4)]
        for e in emails:
            self._seed_app_user(conn, org, e)
        # Before the backfill: no shadow rows for these post-159 members.
        assert _membership_for_org(conn, org) == 0

        _run_backfill(conn)

        assert _membership_for_org(conn, org) == 4, (
            "count(org_membership) must reconcile to app_user rows with a tenant"
        )
        for e in emails:
            assert _identity_count(conn, e) == 1

    def test_the_backfill_is_idempotent(self, conn):
        """Re-run = zero net change, and no existing row is rewritten."""
        org = _new_org(conn, "h6-idem")
        self._seed_app_user(conn, org, "idem@h6.example")
        _run_backfill(conn)
        first = _membership_for_org(conn, org)
        created = conn.execute(
            text(
                "SELECT created_at FROM org_membership "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one()

        _run_backfill(conn)
        assert _membership_for_org(conn, org) == first == 1, (
            "the re-run changed the membership count — not idempotent"
        )
        created_again = conn.execute(
            text(
                "SELECT created_at FROM org_membership "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one()
        assert created_again == created, "the re-run rewrote an existing row"

    def test_the_dual_write_and_the_backfill_converge(self, conn):
        """A dual-written row is not double-counted by a later backfill."""
        org = _new_org(conn, "h6-converge")
        email = "converge@h6.example"
        self._seed_app_user(conn, org, email)
        # The dual-write already mirrored this member.
        uid = _mirror_identity(conn, email, "Converge")
        _mirror_membership(conn, org, uid)
        assert _membership_for_org(conn, org) == 1

        _run_backfill(conn)
        assert _membership_for_org(conn, org) == 1, (
            "the backfill duplicated a row the dual-write already wrote"
        )

    def test_the_ladder_dsn_does_not_leak_out_of_this_suite(self):
        """``DATABASE_URL`` must be untouched — setting it re-arms
        ``test_tenant_coverage.py``'s two DB-gated tests."""
        assert os.environ.get("DATABASE_URL", "") != _URL


# ── The R8 half, through the mirror's OWN call path (tenant_engine_scope) ─────
#
# The class above proves the SQL; this proves ``mirror_identity_membership``
# wires it correctly — resolves the org, upserts the identity, inserts the
# membership, commits — and that it is best-effort. Modelled on
# ``test_org_provisioning.TestSliceFourTheSeamCaller``: these COMMIT, so they use
# uuid'd identifiers and clean up after themselves.

@_DB_GATE
class TestTheMirrorThroughItsOwnCallPath:

    async def _read_back(self, factory, email):
        async with factory() as session:
            ident = (
                await session.execute(
                    text("SELECT id::text AS id FROM user_identity "
                         " WHERE lower(email) = lower(:e)"),
                    {"e": email},
                )
            ).mappings().first()
            memberships = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM org_membership m "
                        "  JOIN user_identity ui ON ui.id = m.user_id "
                        " WHERE lower(ui.email) = lower(:e)"
                    ),
                    {"e": email},
                )
            ).scalar_one()
        return ident, memberships

    async def test_the_mirror_writes_both_tables_through_its_own_call_path(self):
        from acb_common.db import get_session_factory

        slug = f"h6-callpath-{uuid.uuid4().hex[:8]}"
        email = f"callpath-{uuid.uuid4().hex[:8]}@h6.example"

        async with tenant_engine_scope(_URL):
            factory = get_session_factory()
            async with factory() as session:
                org_id = str(
                    (
                        await session.execute(
                            text(
                                "INSERT INTO organization (slug, display_name) "
                                "VALUES (:s, :s) RETURNING id"
                            ),
                            {"s": slug},
                        )
                    ).scalar_one()
                )
                await session.commit()
            try:
                await mirror_identity_membership(
                    email=email, display_name="Call Path",
                    org_id=org_id, status="active",
                )
                ident, memberships = await self._read_back(factory, email)
                assert ident is not None, "the mirror wrote no user_identity row"
                assert memberships == 1, "the mirror wrote no org_membership row"
            finally:
                async with factory() as session:
                    # CASCADE removes the membership with the org.
                    await session.execute(
                        text("DELETE FROM organization WHERE slug = :s"),
                        {"s": slug},
                    )
                    await session.execute(
                        text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                        {"e": email},
                    )
                    await session.commit()

    async def test_the_mirror_never_raises_on_a_bad_org(self):
        """Best-effort: a non-existent org (FK violation) is swallowed, and a
        call with neither org_id nor org_slug is a quiet no-op."""
        async with tenant_engine_scope(_URL):
            # A random UUID names no organization → the membership INSERT's FK
            # would fail; the mirror must catch it and return None.
            assert await mirror_identity_membership(
                email=f"ghost-{uuid.uuid4().hex[:8]}@h6.example",
                org_id=str(uuid.uuid4()), status="active",
            ) is None
            # No org named at all — nothing to write, no raise.
            assert await mirror_identity_membership(
                email=f"ghost2-{uuid.uuid4().hex[:8]}@h6.example",
            ) is None
