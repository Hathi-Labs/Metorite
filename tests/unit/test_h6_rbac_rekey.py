"""WS-29 H6 slice 4 (D48) — the RBAC re-key EXPAND: migration 184 + dual-write.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H6 (Slice 4 — RBAC
re-key, EXPAND) · ``saas_multitenancy.md`` §11 · D48.

**What this slice is.** D48 re-keys the three RBAC tables — ``user_role``,
``user_permission_override``, ``org_group_member`` — from ``app_user.id`` →
``user_identity.id``, in an expand/contract shipped dark behind
``IDENTITY_CUTOVER``. This is the EXPAND, and it moves NO read:

* **Migration 184** adds a nullable ``user_identity_id`` column to each table,
  BACKFILLS it via the ``lower(email)`` bridge
  (RBAC.user_id → app_user.id → lower(app_user.email) → user_identity.id) and
  INDEXES it; and
* every RBAC **INSERT** dual-writes ``user_identity_id`` through that same bridge,
  so the column stays current going forward.

``app_user.id`` stays the AUTHORITATIVE key; nothing reads ``user_identity_id``
yet (the per-module READ cutovers are separate, later, flag-gated PRs).

⚠️ **The two fences this suite names (R7).**

* **backfill-correctness / dual-write-correctness** — every RBAC row's
  ``user_identity_id`` equals the ``user_identity.id`` whose ``lower(email)``
  matches its ``user_id``'s ``app_user.email``; NULL only where no identity
  exists; and a fresh INSERT populates both columns to the same identity.
* **idempotent-expand** — re-running the migration is a zero-net-change no-op
  (``IS DISTINCT FROM`` guard), and it moves no grant (sets ONLY
  ``user_identity_id``).

Two halves, the ``test_h6_identity_shadow.py`` model:

* the **structural** half takes no database and must never skip — it pins the
  five dual-write sites, the migration's idempotent/additive shape and its
  no-move guarantee; and
* the **R8** half answers to ``TENANT_LADDER_DATABASE_URL`` — never to
  ``DATABASE_URL`` — and proves against a REAL Postgres that the backfill bridges
  correctly, is idempotent, and that ``set_roles`` / the owner bootstrap
  dual-write both columns to the same identity. A fake agrees with any SQL it is
  handed (R8).

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    uv run pytest tests/unit/test_h6_rbac_rekey.py -v -rs
"""
from __future__ import annotations

import inspect
import os
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

#: Subjects are IMPORTED, never transcribed — a copy here would keep passing
#: after somebody edited the real statement.
from acb_auth.access import _BOOTSTRAP_OWNER_SQL
from gateway.routes.admin import groups as groups_mod
from gateway.routes.admin import members as members_mod
from gateway.routes.admin._common import set_roles
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

#: The three RBAC tables D48 re-keys.
_RBAC_TABLES = ("user_role", "user_permission_override", "org_group_member")


# ── The structural half: no database, and it must never skip ────────────────

def _rekey_migration() -> Path:
    """The RBAC re-key EXPAND migration, found BY CONTENT never by number (R1).

    It is the only ``.sql`` file that adds ``user_identity_id`` to all THREE RBAC
    tables and backfills them through the ``lower(email)`` bridge under an
    ``IS DISTINCT FROM`` guard — that trio is the content discriminator, so a
    merge renumber cannot make this fence point at the wrong file or go vacuous.
    """
    matches = []
    for path in sorted(_LADDER_DIR.glob("*.sql")):
        if not re.match(r"^\d+_", path.name):
            continue
        body = path.read_text(encoding="utf-8")
        if (
            body.count("ADD COLUMN IF NOT EXISTS user_identity_id") >= 3
            and "IS DISTINCT FROM ui.id" in body
            and "lower(ui.email) = lower(au.email)" in body
        ):
            matches.append(path)
    assert len(matches) == 1, (
        "expected exactly one RBAC re-key EXPAND migration (adds user_identity_id "
        "to all three RBAC tables and backfills via the lower(email) bridge, "
        f"IS DISTINCT FROM-guarded); found {[p.name for p in matches]}"
    )
    return matches[0]


def _sql_only(body: str) -> str:
    """The executable SQL of a migration, with ``--`` comment content stripped.

    A header comment legitimately NAMES columns (``user_id``, ``SET NULL``) in
    prose; a fence must read the STATEMENTS, not the commentary."""
    lines = []
    for line in body.splitlines():
        code = re.split(r"--", line, maxsplit=1)[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


def _update_set_clauses(sql: str) -> list[str]:
    """Every UPDATE's assignment list — the text between ``SET`` and the first
    ``FROM``/``WHERE``, for UPDATE statements ONLY. Lets a fence assert the grant
    key columns are never WRITTEN even though they legitimately appear in the
    predicate (and in the ALTER's ``ON DELETE SET NULL``)."""
    return re.findall(
        r"UPDATE\s+\w+\s+\w+\s+SET\s+(.*?)\s*\bFROM\b",
        sql, flags=re.IGNORECASE | re.DOTALL,
    )


class TestTheMigrationShape:
    """The ratchet. Breaking the EXPAND's invariants must fail here, no database."""

    def test_the_migration_is_additive_and_idempotent(self):
        """R6 EXPAND: ADD COLUMN / CREATE INDEX are IF NOT EXISTS, and every
        backfill UPDATE is IS DISTINCT FROM-guarded (re-run = 0 rows)."""
        sql = _sql_only(_rekey_migration().read_text(encoding="utf-8"))
        assert sql.count("ADD COLUMN IF NOT EXISTS user_identity_id") == 3, (
            "the migration must add user_identity_id to all three RBAC tables"
        )
        assert sql.count("CREATE INDEX IF NOT EXISTS") == 3, (
            "each new column must be indexed (org_id-free — it is a join key)"
        )
        assert sql.count("IS DISTINCT FROM ui.id") == 3, (
            "each backfill must be IS DISTINCT FROM-guarded — else a re-run "
            "rewrites rows and it is not idempotent (R6)"
        )
        assert "CREATE TABLE" not in sql.upper(), (
            "the EXPAND creates a table — it must only ALTER (R5: no new table)"
        )

    def test_the_backfill_moves_no_grant(self):
        """Each UPDATE sets ONLY user_identity_id — never the grant key columns —
        so it can never move a role/permission/group membership."""
        sql = _sql_only(_rekey_migration().read_text(encoding="utf-8"))
        clauses = _update_set_clauses(sql)
        assert len(clauses) == 3, (
            f"expected three backfill UPDATEs; found {len(clauses)}"
        )
        for clause in clauses:
            assert "user_identity_id" in clause, (
                "an UPDATE sets something other than user_identity_id"
            )
            for forbidden in ("user_id", "role_id", "permission", "group_id"):
                assert not re.search(rf"\b{forbidden}\b", clause), (
                    f"the backfill writes {forbidden} — it must set only "
                    "user_identity_id, never a grant key"
                )

    def test_the_fk_nulls_rather_than_cascades(self):
        """The shadow FK is ON DELETE SET NULL, not CASCADE: app_user.id is still
        the authoritative key, so a deleted user_identity must NULL the shadow,
        never cascade-delete an RBAC grant app_user.id still backs. The CONTRACT
        slice (OWNER-GATE) re-keys to CASCADE when user_identity_id takes over."""
        body = _rekey_migration().read_text(encoding="utf-8")
        assert body.count("REFERENCES user_identity(id) ON DELETE SET NULL") == 3, (
            "each user_identity_id FK must be ON DELETE SET NULL"
        )
        # A shadow-FK cascade would delete authoritative grants when an identity
        # is removed — never in the EXPAND.
        assert "REFERENCES user_identity(id) ON DELETE CASCADE" not in body, (
            "a user_identity_id FK cascades — that would delete an authoritative "
            "app_user-keyed grant on an identity delete"
        )

    def test_the_migration_does_not_touch_rls(self):
        """R6 expand re-keys no RLS. ENABLE/FORCE/POLICY live in
        generated/04_policies.sql and stay unchanged."""
        body = _rekey_migration().read_text(encoding="utf-8").upper()
        for token in ("ENABLE ROW LEVEL SECURITY", "FORCE  ROW LEVEL SECURITY",
                      "FORCE ROW LEVEL SECURITY", "CREATE POLICY", "DROP POLICY"):
            assert token not in body, (
                f"the EXPAND changes RLS ({token}) — RLS is generated/'s"
            )


# ── The five dual-write sites — each carries the same lower(email) bridge ────
#
# The bridge is IDENTICAL across the four `:uid`-keyed INSERTs; the bootstrap
# INSERT resolves the identity by `:email` (its app_user is inside the same WITH
# and unreadable, and at bootstrap the identity is created AFTER — NULL on a fresh
# box). These fragments each fit on ONE source line, so they survive
# `inspect.getsource`; the R8 half proves the bridge SQL actually resolves.

#: The bridge tokens every `:uid`-keyed dual-write must carry.
_UID_BRIDGE_TOKENS = (
    "user_identity_id",
    "SELECT ui.id FROM user_identity ui",
    "lower(au.email) = lower(ui.email)",
    "WHERE au.id = CAST(:uid AS uuid)",
)


class TestTheDualWriteSites:
    """All five RBAC INSERTs dual-write user_identity_id (no database)."""

    def _uid_site_carries_the_bridge(self, label: str, src: str) -> None:
        for token in _UID_BRIDGE_TOKENS:
            assert token in src, f"{label} is missing dual-write token: {token!r}"

    def test_set_roles_dual_writes(self):
        """user_role INSERT — admin/_common.set_roles."""
        self._uid_site_carries_the_bridge(
            "set_roles", inspect.getsource(set_roles)
        )

    def test_add_group_member_dual_writes_both_inserts(self):
        """org_group_member INSERT + the center-access user_permission_override
        INSERT — admin/groups.add_group_member. Both are `:uid`-keyed."""
        src = inspect.getsource(groups_mod.add_group_member)
        self._uid_site_carries_the_bridge("add_group_member", src)
        assert "INSERT INTO org_group_member" in src
        assert "INSERT INTO user_permission_override" in src
        # BOTH inserts name the new column (two occurrences of the FK column).
        assert src.count("user_identity_id") >= 2, (
            "add_group_member has an INSERT that does not dual-write "
            "user_identity_id"
        )

    def test_set_member_overrides_dual_writes(self):
        """user_permission_override INSERT — admin/members.set_member_overrides."""
        self._uid_site_carries_the_bridge(
            "set_member_overrides",
            inspect.getsource(members_mod.set_member_overrides),
        )

    def test_the_bootstrap_owner_insert_dual_writes_by_email(self):
        """user_role INSERT in _BOOTSTRAP_OWNER_SQL — resolves by lower(:email)
        because its app_user is written in the same WITH (unreadable) and the
        identity is minted AFTER the bootstrap commit (NULL on a fresh box)."""
        sql = _BOOTSTRAP_OWNER_SQL
        assert "user_identity_id" in sql, "the bootstrap INSERT does not dual-write"
        assert "LEFT JOIN user_identity ui" in sql, (
            "the bootstrap INSERT does not LEFT JOIN user_identity (a fresh box "
            "has no identity yet — an inner join would drop the owner's role)"
        )
        assert "lower(ui.email) = lower(:email)" in sql, (
            "the bootstrap INSERT does not resolve the identity by lower(:email)"
        )

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """An R8 suite absent from pr-check.yml's hand-list skips silently and
        leaves the job green (that file's own warning)."""
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_h6_rbac_rekey.py" in workflow

    def test_the_owning_spec_names_this_suite(self):
        spec = (_ROOT / "project-docs/specs/saas_multitenancy_handover.md").read_text(
            encoding="utf-8")
        assert "test_h6_rbac_rekey.py" in spec


# ── The R8 half: the backfill + dual-write against the replayed ladder ───────

@pytest.fixture(scope="module")
def replayed():
    """Build the tenant schema from ``infra/postgres/`` (incl. migration 184),
    then replay it — twice, to prove replay-safety like the deploy."""
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
            text("INSERT INTO organization (slug, display_name) "
                 "VALUES (:s, :s) RETURNING id"),
            {"s": slug},
        ).scalar_one()
    )


def _new_role(conn, org: str, slug: str = "member") -> str:
    return str(
        conn.execute(
            text("INSERT INTO org_role (organization_id, slug, display_name) "
                 "VALUES (CAST(:o AS uuid), :s, :s) RETURNING id"),
            {"o": org, "s": slug},
        ).scalar_one()
    )


def _new_group(conn, org: str, slug: str = "grp") -> str:
    return str(
        conn.execute(
            text("INSERT INTO org_group (organization_id, slug, display_name) "
                 "VALUES (CAST(:o AS uuid), :s, :s) RETURNING id"),
            {"o": org, "s": slug},
        ).scalar_one()
    )


def _seed_app_user(conn, org: str, email: str, status: str = "active") -> str:
    return str(
        conn.execute(
            text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "                      organization_id, joined_at) "
                "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid), now()) "
                "RETURNING id"
            ),
            {"e": email, "s": status, "o": org},
        ).scalar_one()
    )


def _seed_identity(conn, email: str) -> str:
    return str(
        conn.execute(
            text("INSERT INTO user_identity (email, display_name) "
                 "VALUES (:e, :e) RETURNING id"),
            {"e": email},
        ).scalar_one()
    )


def _run_rekey(conn) -> int:
    """Execute migration 184 verbatim inside ``conn``'s txn; return the rowcount
    of its LAST statement (the org_group_member backfill UPDATE). ADD COLUMN /
    CREATE INDEX are IF NOT EXISTS so re-applying the already-built schema is
    inert; only the guarded UPDATEs do work."""
    sql = _rekey_migration().read_text(encoding="utf-8")
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
        return cur.rowcount


def _identity_of(conn, table: str, user_id: str) -> str | None:
    # `table` is always one of _RBAC_TABLES (fixed literals), never user input.
    assert table in _RBAC_TABLES
    row = conn.execute(
        text(f"SELECT user_identity_id::text FROM {table} "
             " WHERE user_id = CAST(:u AS uuid) LIMIT 1"),
        {"u": user_id},
    ).first()
    return None if row is None or row[0] is None else row[0]


@_DB_GATE
class TestTheBackfill:
    """§H6 done-when (EXPAND): the migration bridges every RBAC row to the
    lower(email) identity, NULL only where none, and is idempotent (R6)."""

    def _seed_rbac_rows(self, conn, org, uid) -> None:
        """One row in each RBAC table, keyed on app_user.id, user_identity_id
        left NULL (the pre-EXPAND state a backfill must reconcile)."""
        role = _new_role(conn, org, f"role-{uuid.uuid4().hex[:6]}")
        grp = _new_group(conn, org, f"grp-{uuid.uuid4().hex[:6]}")
        conn.execute(
            text("INSERT INTO user_role (user_id, role_id) "
                 "VALUES (CAST(:u AS uuid), CAST(:r AS uuid))"),
            {"u": uid, "r": role},
        )
        conn.execute(
            text("INSERT INTO user_permission_override "
                 "  (user_id, permission, effect) "
                 "VALUES (CAST(:u AS uuid), 'feature:x', 'allow')"),
            {"u": uid},
        )
        conn.execute(
            text("INSERT INTO org_group_member (group_id, user_id) "
                 "VALUES (CAST(:g AS uuid), CAST(:u AS uuid))"),
            {"g": grp, "u": uid},
        )

    def test_the_backfill_bridges_every_rbac_row_to_its_identity(self, conn):
        org = _new_org(conn, "h6rk-bf")
        email = "member@h6rk.example"
        uid = _seed_app_user(conn, org, email)
        wanted = _seed_identity(conn, email)
        self._seed_rbac_rows(conn, org, uid)

        # Pre-backfill: the shadow column is NULL on every RBAC row.
        for tbl in _RBAC_TABLES:
            assert _identity_of(conn, tbl, uid) is None

        self._run_and_assert_bridged(conn, uid, wanted)

    def _run_and_assert_bridged(self, conn, uid, wanted) -> None:
        _run_rekey(conn)
        for tbl in _RBAC_TABLES:
            got = _identity_of(conn, tbl, uid)
            assert got == wanted, (
                f"{tbl}.user_identity_id backfilled to {got}, not the "
                f"lower(email)-bridged identity {wanted}"
            )

    def test_the_bridge_is_case_insensitive(self, conn):
        """R10: a case-variant between app_user.email and user_identity.email
        still bridges (lower() on both sides)."""
        org = _new_org(conn, "h6rk-case")
        uid = _seed_app_user(conn, org, "Casey@H6rk.Example")
        wanted = _seed_identity(conn, "casey@h6rk.example")
        self._seed_rbac_rows(conn, org, uid)
        self._run_and_assert_bridged(conn, uid, wanted)

    def test_a_row_whose_app_user_has_no_identity_stays_null(self, conn):
        """"NULL only where no identity exists": a member who predates the shadow
        and never signed in has no user_identity, so the bridge finds nothing."""
        org = _new_org(conn, "h6rk-noident")
        uid = _seed_app_user(conn, org, "orphan@h6rk.example")  # no identity
        self._seed_rbac_rows(conn, org, uid)

        _run_rekey(conn)
        for tbl in _RBAC_TABLES:
            assert _identity_of(conn, tbl, uid) is None, (
                f"{tbl}.user_identity_id was set for a member with no identity"
            )

    def test_the_backfill_is_idempotent(self, conn):
        """R6: a re-run is a zero-net-change no-op — values unchanged and the
        last guarded UPDATE touches 0 rows."""
        org = _new_org(conn, "h6rk-idem")
        email = "idem@h6rk.example"
        uid = _seed_app_user(conn, org, email)
        wanted = _seed_identity(conn, email)
        self._seed_rbac_rows(conn, org, uid)

        _run_rekey(conn)
        snapshot = {t: _identity_of(conn, t, uid) for t in _RBAC_TABLES}
        assert all(v == wanted for v in snapshot.values())

        # Re-run = zero NET change: the `IS DISTINCT FROM ui.id` guard means no
        # value moves. (A multi-statement DDL+DML script's `cur.rowcount` is not a
        # reliable per-statement count under the simple query protocol, so
        # idempotence is proven by the snapshot NOT changing — the true invariant.)
        _run_rekey(conn)
        assert {t: _identity_of(conn, t, uid) for t in _RBAC_TABLES} == snapshot, (
            "a re-run changed a user_identity_id value — not idempotent"
        )


@_DB_GATE
class TestTheDualWriteAtRuntime:
    """§H6 done-when (EXPAND): a fresh RBAC INSERT populates both columns to the
    same identity — proven through the REAL write paths, not a transcription."""

    def test_the_bootstrap_owner_role_dual_writes_when_the_identity_exists(
        self, conn,
    ):
        """Drive the imported ``_BOOTSTRAP_OWNER_SQL`` against real PG: with the
        identity already present, the bootstrapped owner's user_role carries
        user_identity_id == that identity (both columns agree)."""
        org = _new_org(conn, "h6rk-boot")
        _new_role(conn, org, "owner")  # _BOOTSTRAP_OWNER_SQL needs the owner role
        email = "founder@h6rk.example"
        wanted = _seed_identity(conn, email)  # identity exists BEFORE bootstrap

        conn.execute(
            text(_BOOTSTRAP_OWNER_SQL), {"email": email, "org_slug": "h6rk-boot"}
        )

        row = conn.execute(
            text(
                "SELECT ur.user_identity_id::text "
                "  FROM user_role ur "
                "  JOIN app_user au ON au.id = ur.user_id "
                " WHERE lower(au.email) = lower(:e)"
            ),
            {"e": email},
        ).one()
        assert row[0] == wanted, (
            "the bootstrap owner user_role did not dual-write user_identity_id "
            "equal to the pre-existing identity"
        )

    async def test_set_roles_dual_writes_through_its_own_call_path(self):
        """Drive the REAL async ``set_roles`` against real PG (committing +
        cleaning up, the ``test_h6_identity_shadow`` call-path model): the
        assigned user_role carries user_identity_id == the member's identity."""
        from acb_common.db import get_session_factory

        slug = f"h6rk-sr-{uuid.uuid4().hex[:8]}"
        email = f"setroles-{uuid.uuid4().hex[:8]}@h6rk.example"

        async with tenant_engine_scope(_URL):
            factory = get_session_factory()
            async with factory() as session:
                org_id = str((await session.execute(
                    text("INSERT INTO organization (slug, display_name) "
                         "VALUES (:s, :s) RETURNING id"),
                    {"s": slug},
                )).scalar_one())
                role_id = str((await session.execute(
                    text("INSERT INTO org_role (organization_id, slug, "
                         "display_name) VALUES (CAST(:o AS uuid), 'member', "
                         "'Member') RETURNING id"),
                    {"o": org_id},
                )).scalar_one())
                uid = str((await session.execute(
                    text("INSERT INTO app_user (email, display_name, role, "
                         "status, organization_id, joined_at) VALUES "
                         "(:e, :e, 'employee', 'active', CAST(:o AS uuid), now()) "
                         "RETURNING id"),
                    {"e": email, "o": org_id},
                )).scalar_one())
                wanted = str((await session.execute(
                    text("INSERT INTO user_identity (email, display_name) "
                         "VALUES (:e, :e) RETURNING id"),
                    {"e": email},
                )).scalar_one())
                await session.commit()
            try:
                async with factory() as session:
                    await set_roles(session, uid, [(role_id, "member")], "pytest")
                    await session.commit()
                async with factory() as session:
                    got = (await session.execute(
                        text("SELECT user_identity_id::text FROM user_role "
                             " WHERE user_id = CAST(:u AS uuid)"),
                        {"u": uid},
                    )).scalar_one()
                assert got == wanted, (
                    "set_roles did not dual-write user_identity_id equal to the "
                    "member's lower(email)-derived identity"
                )
            finally:
                async with factory() as session:
                    await session.execute(
                        text("DELETE FROM organization WHERE slug = :s"),
                        {"s": slug},
                    )
                    await session.execute(
                        text("DELETE FROM user_identity WHERE lower(email) = "
                             "lower(:e)"),
                        {"e": email},
                    )
                    await session.commit()

    def test_the_ladder_dsn_does_not_leak_out_of_this_suite(self):
        """``DATABASE_URL`` must be untouched — setting it re-arms
        ``test_tenant_coverage.py``'s two DB-gated tests."""
        assert os.environ.get("DATABASE_URL", "") != _URL
