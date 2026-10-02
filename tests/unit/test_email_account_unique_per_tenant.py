"""EM-T2a — a mailbox row is unique per organization (migration 223).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.5, EM-T2a.

Migration 17 made ``(user_id, provider, email_address)`` unique with no tenant,
and migration 47 made ``(user_id) WHERE is_default`` unique with no tenant. A
member who moves to another organization and connects the same mailbox again
then hits a unique violation that row level security hides from the read
first. Migration 223 replaces both rules with per-tenant ones.

R7 fences named here:

* ``email-account-unique-per-tenant``: the same member, provider and address
  connect in org A and in org B, through the REAL handlers, as the
  non-privileged role ``acb_app_h3rls`` on the phase-4-promoted two-org catalog
  of ``test_h3_rls_promotion_rehearsal``. Both rows exist. A second row in ONE
  organization is still a unique violation.
* ``email-default-per-tenant``: the first mailbox of a member in org B is the
  default while org A holds a default.
* ``email-unique-index-has-tenant``: no unique index on ``email_accounts``,
  other than the primary key, lacks ``organization_id``.
* ``email-223-both-orders``: 223 applies to a database that carries the
  generated tenancy phases and the old constraint (production), and to a fresh
  ladder database with no tenancy phase. A second run changes nothing.
* ``email-no-conflict-target``: no code and no migration names a conflict
  target on ``email_accounts``. A named target pins a statement to one index,
  and replacing that index then fails at plan time (42P10, migration 162).
* ``email-create-account-names-its-tenant``: ``create_account`` takes the
  organization from the session, names it in the INSERT, and answers 403
  without one.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_account_unique_per_tenant.py -v -rs
"""
from __future__ import annotations

import inspect
import os
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.email.transport import accounts, oauth
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.unit._tenant_ladder import INIT_SCHEMA, _exec_file, ladder, tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "infra" / "postgres"

OLD_CONSTRAINT = "email_accounts_user_id_provider_email_address_key"
OLD_DEFAULT = "idx_email_accounts_one_default"
NEW_MAILBOX = "uq_email_accounts_org_owner_mailbox"
NEW_DEFAULT = "uq_email_accounts_org_one_default"


def _migration_223() -> Path:
    """Found by CONTENT, never by number: R1 can renumber it at merge."""
    hits = [
        p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
        if NEW_MAILBOX in p.read_text(encoding="utf-8")
        and "CREATE UNIQUE INDEX" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one migration to create {NEW_MAILBOX}, got {hits}"
    return hits[0]


def _number(path: str | Path) -> int:
    return int(os.path.basename(str(path)).split("_", 1)[0])


def _run_223(admin_url) -> list[tuple[str, str]]:
    """Apply the migration file on its own AUTOCOMMIT connection.

    Returns the ``(severity, message)`` of every notice, so a test can read
    the RAISE WARNING of the backfill.
    """
    notices: list[tuple[str, str]] = []
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as conn:
            raw = conn.connection.dbapi_connection
            raw.add_notice_handler(
                lambda d: notices.append((d.severity or "", d.message_primary or "")))
            with raw.cursor() as cur:
                cur.execute(_migration_223().read_text(encoding="utf-8"))
    finally:
        eng.dispose()
    return notices


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _unique_indexes(conn) -> dict[str, list[str]]:
    """Every unique index on ``email_accounts`` but the primary key, with its
    key columns."""
    rows = conn.execute(text(
        "SELECT ic.relname AS name, "
        "       array_agg(a.attname ORDER BY k.ord) AS cols "
        "  FROM pg_index i "
        "  JOIN pg_class ic ON ic.oid = i.indexrelid "
        "  CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) "
        "  LEFT JOIN pg_attribute a "
        "         ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
        " WHERE i.indrelid = 'email_accounts'::regclass "
        "   AND i.indisunique AND NOT i.indisprimary "
        " GROUP BY ic.relname")).all()
    return {r.name: [c for c in r.cols if c] for r in rows}


def _shape(conn) -> dict:
    """What 223 decides: indexes, constraints, the column and the rows."""
    return {
        "indexes": sorted(conn.execute(text(
            "SELECT indexname || ' ' || indexdef FROM pg_indexes "
            "WHERE tablename = 'email_accounts'")).scalars().all()),
        "constraints": sorted(conn.execute(text(
            "SELECT conname || ' ' || pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE conrelid = 'email_accounts'::regclass"),
        ).scalars().all()),
        "rows": sorted(
            tuple(map(str, r)) for r in conn.execute(text(
                "SELECT id, user_id, organization_id, is_default "
                "FROM email_accounts")).all()),
    }


def _org_fks(conn) -> list[tuple[str, str]]:
    """``(name, confdeltype)`` of each foreign key on ``organization_id``."""
    return [tuple(r) for r in conn.execute(text(
        "SELECT c.conname, c.confdeltype::text "
        "  FROM pg_constraint c "
        "  JOIN pg_attribute a "
        "    ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
        " WHERE c.conrelid = 'email_accounts'::regclass AND c.contype = 'f' "
        "   AND a.attname = 'organization_id' ORDER BY c.conname")).all()]


class _Store:
    def encrypt(self, raw: str) -> str:
        return f"enc:{raw}"


@pytest.fixture()
def fakes(monkeypatch):
    """The key store and the sync loop, faked. The database stays real."""
    import email_ingestion.scheduler as sched
    from acb_llm import key_store

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "refresh_account_sync", _noop)


_IMAP = {
    "imap_host": "imap.contoso.test", "imap_port": 993,
    "imap_username": "u", "imap_password": "p",
    "smtp_host": "smtp.contoso.test", "smtp_port": 587,
}


def _admin_rows(admin_engine, mailbox: str) -> list:
    with admin_engine.connect() as c:
        return c.execute(text(
            "SELECT user_id, organization_id::text AS org, is_default "
            "FROM email_accounts WHERE email_address = :m ORDER BY created_at"),
            {"m": mailbox}).mappings().all()


def _purge(admin_engine, *, mailbox: str | None = None, owner: str | None = None) -> None:
    with admin_engine.begin() as c:
        if mailbox:
            c.execute(text("DELETE FROM email_accounts WHERE email_address = :m"),
                      {"m": mailbox})
        if owner:
            c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                      {"u": owner})


# ══════════════════════════════════════════════════════════════════════════
# R8 — two organizations, the real handlers, a non-privileged role
# ══════════════════════════════════════════════════════════════════════════

@_DB_GATE
class TestTwoOrganizations:

    async def test_one_mailbox_connects_in_org_a_and_in_org_b_through_oauth(
        self, promoted, app_engine,  # noqa: F811
    ):
        """The defect, stated as the behaviour it broke. Against migration
        17's rule the INSERT for org B raises a unique violation."""
        _assert_non_priv(app_engine)
        p = promoted
        member = f"member-{uuid.uuid4().hex[:8]}@em-t2a.test"
        mailbox = f"box-{uuid.uuid4().hex[:8]}@contoso.test"
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                for org in (p.org_a, p.org_b):
                    await oauth._save_account(
                        org=org, member=member, owner=member,
                        provider="microsoft", mailbox=mailbox,
                        encrypted_creds="x",
                    )
            rows = _admin_rows(p.admin_engine, mailbox)
            assert sorted(r["org"] for r in rows) == sorted([p.org_a, p.org_b]), (
                "the same mailbox did not connect in both organizations"
            )
            assert all(r["is_default"] for r in rows), (
                "the first mailbox of the member in org B is not the default "
                "while org A holds one"
            )
        finally:
            _purge(p.admin_engine, mailbox=mailbox)

    async def test_create_account_writes_one_row_per_org_and_a_default_in_each(
        self, promoted, app_engine, fakes,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        member = f"member-{uuid.uuid4().hex[:8]}@em-t2a.test"
        mailbox = f"box-{uuid.uuid4().hex[:8]}@contoso.test"
        app_dsn = p.app_url.render_as_string(hide_password=False)
        req = accounts.CreateAccountRequest(
            provider="imap", email_address=mailbox, credentials=_IMAP)
        try:
            async with tenant_engine_scope(app_dsn):
                made = {}
                for org in (p.org_a, p.org_b):
                    user = UserContext(
                        email=member, role=UserRole.EMPLOYEE, organization_id=org)
                    made[org] = await accounts.create_account(req, user=user)
                # The same row again in org A is a duplicate, not a new row.
                with pytest.raises(HTTPException) as dup:
                    await accounts.create_account(req, user=UserContext(
                        email=member, role=UserRole.EMPLOYEE,
                        organization_id=p.org_a))
                assert dup.value.status_code == 409
            assert made[p.org_a].is_default and made[p.org_b].is_default
            rows = _admin_rows(p.admin_engine, mailbox)
            assert sorted(r["org"] for r in rows) == sorted([p.org_a, p.org_b])
            assert all(r["user_id"] == member for r in rows)
        finally:
            _purge(p.admin_engine, mailbox=mailbox)

    def test_a_second_row_in_one_org_is_a_unique_violation(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Per tenant, not abandoned. The bound role writes the same row
        twice into org A."""
        _assert_non_priv(app_engine)
        p = promoted
        member = f"member-{uuid.uuid4().hex[:8]}@em-t2a.test"
        mailbox = f"box-{uuid.uuid4().hex[:8]}@contoso.test"
        insert = text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid))")
        args = {"u": member, "m": mailbox, "o": p.org_a}
        try:
            with app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": p.org_a})
                c.execute(insert, args)
            with pytest.raises(IntegrityError) as err, app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": p.org_a})
                c.execute(insert, args)
            assert err.value.orig.diag.constraint_name == NEW_MAILBOX
        finally:
            _purge(p.admin_engine, mailbox=mailbox)

    def test_a_second_default_in_one_org_is_a_unique_violation(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        member = f"member-{uuid.uuid4().hex[:8]}@em-t2a.test"
        insert = text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, is_default, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', true, CAST(:o AS uuid))")
        try:
            with app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": p.org_a})
                c.execute(insert, {"u": member, "m": "one@contoso.test", "o": p.org_a})
            with pytest.raises(IntegrityError) as err, app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": p.org_a})
                c.execute(insert, {"u": member, "m": "two@contoso.test", "o": p.org_a})
            assert err.value.orig.diag.constraint_name == NEW_DEFAULT
        finally:
            _purge(p.admin_engine, owner=member)

    def test_no_unique_index_but_the_primary_key_lacks_the_tenant(
        self, promoted,  # noqa: F811
    ):
        with promoted.admin_engine.connect() as c:
            found = _unique_indexes(c)
            invalid = c.execute(text(
                "SELECT ic.relname FROM pg_index i "
                "JOIN pg_class ic ON ic.oid = i.indexrelid "
                "WHERE i.indrelid = 'email_accounts'::regclass "
                "AND NOT i.indisvalid")).scalars().all()
        assert set(found) >= {NEW_MAILBOX, NEW_DEFAULT}, found
        assert OLD_CONSTRAINT not in found and OLD_DEFAULT not in found, found
        lacking = sorted(n for n, cols in found.items() if "organization_id" not in cols)
        assert not lacking, f"unique indexes with no tenant: {lacking}"
        assert not invalid, f"INVALID indexes on email_accounts: {invalid}"


# ══════════════════════════════════════════════════════════════════════════
# The migration, in both orders
# ══════════════════════════════════════════════════════════════════════════

@_DB_GATE
class TestTheProductionOrder:

    def test_223_applies_over_the_tenancy_phases_and_the_old_constraint(
        self, promoted,  # noqa: F811
    ):
        """Production: generated/01..04 are applied, the column is NOT NULL,
        ``email_accounts_org_fk`` cascades, and the old rules still exist.

        The promoted catalog ran 223 BEFORE the generated phases, so this
        first puts the production shape back: the old constraint, the old
        index, no new index and no foreign key from 223's column clause.
        """
        p = promoted
        admin = p.admin_engine
        member = f"member-{uuid.uuid4().hex[:8]}@em-t2a.test"
        with admin.begin() as c:
            for name, _kind in _org_fks(c):
                if name != "email_accounts_org_fk":
                    c.execute(text(f'ALTER TABLE email_accounts DROP CONSTRAINT "{name}"'))
            c.execute(text(f"DROP INDEX IF EXISTS {NEW_MAILBOX}"))
            c.execute(text(f"DROP INDEX IF EXISTS {NEW_DEFAULT}"))
            c.execute(text(
                f"ALTER TABLE email_accounts ADD CONSTRAINT {OLD_CONSTRAINT} "
                "UNIQUE (user_id, provider, email_address)"))
            c.execute(text(
                f"CREATE UNIQUE INDEX {OLD_DEFAULT} ON email_accounts (user_id) "
                "WHERE is_default"))
            c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, is_default, organization_id) "
                "VALUES (:u, 'microsoft', 'prod@contoso.test', 'x', true, "
                "CAST(:o AS uuid))"), {"u": member, "o": p.org_a})
            assert _org_fks(c) == [("email_accounts_org_fk", "c")]
        try:
            admin_url = admin.url
            notices = _run_223(admin_url)
            assert not [n for n in notices if n[0] == "WARNING"], notices
            with admin.connect() as c:
                found = _unique_indexes(c)
                assert set(found) == {NEW_MAILBOX, NEW_DEFAULT}, found
                assert _org_fks(c) == [("email_accounts_org_fk", "c")], (
                    "223 added a second foreign key over an existing column"
                )
                nullable = c.execute(text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'email_accounts' "
                    "AND column_name = 'organization_id'")).scalar_one()
                assert nullable == "NO", "223 loosened phase 3's NOT NULL"
                before = _shape(c)

            _run_223(admin_url)
            with admin.connect() as c:
                assert _shape(c) == before, "a second run of 223 changed something"
        finally:
            _purge(admin, owner=member)


@pytest.fixture(scope="module")
def fresh_db():
    """A dedicated database built from the ladder UP TO 223, with no tenancy
    phase. Never the shared ladder database, which other suites replay."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_emt2a"
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": dedicated})
        c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    maint.dispose()

    url = admin_url.set(database=dedicated)
    eng = create_engine(url, future=True)
    n223 = _number(_migration_223())
    with eng.begin() as conn:
        _exec_file(conn, INIT_SCHEMA)
        for path in ladder():
            if _number(path) < n223:
                _exec_file(conn, path)
    try:
        yield url, eng, n223
    finally:
        eng.dispose()
        maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with maint.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": dedicated})
            c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
        maint.dispose()


@_DB_GATE
class TestTheFreshLadderOrder:

    def test_223_declares_backfills_and_replaces_then_a_rerun_is_a_no_op(
        self, fresh_db,
    ):
        url, eng, n223 = fresh_db
        org_x, org_y = str(uuid.uuid4()), str(uuid.uuid4())
        with eng.begin() as c:
            has_col = c.execute(text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'email_accounts' "
                "AND column_name = 'organization_id'")).scalar_one()
            assert has_col == 0, "the fresh ladder already has the column"
            for oid, slug in ((org_x, "emt2a-x"), (org_y, "emt2a-y")):
                c.execute(text(
                    "INSERT INTO organization (id, slug, display_name) "
                    "VALUES (CAST(:i AS uuid), :s, :s)"), {"i": oid, "s": slug})
            c.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, status) "
                "VALUES ('known@emt2a.test', 'K', CAST(:o AS uuid), 'active')"),
                {"o": org_x})
            # The member's address in another case: the backfill lowers both.
            c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, is_default) VALUES "
                "('Known@EMT2A.test', 'gmail', 'k@contoso.test', 'x', true), "
                "('ghost@emt2a.test', 'imap', 'g@contoso.test', 'x', true)"))

        notices = _run_223(url)
        warnings = [m for s, m in notices if s == "WARNING"]
        assert len(warnings) == 1 and warnings[0].startswith("223: 1 "), notices

        with eng.connect() as c:
            orgs = dict(c.execute(text(
                "SELECT user_id, organization_id::text FROM email_accounts")).all())
            assert orgs == {"Known@EMT2A.test": org_x, "ghost@emt2a.test": None}, (
                "with two organizations an unmatched row must stay NULL, "
                "never be guessed"
            )
            assert _org_fks(c) == [("email_accounts_organization_id_fkey", "c")], (
                "the column 223 declares must cascade on an organization purge"
            )
            default = c.execute(text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'email_accounts' "
                "AND column_name = 'organization_id'")).scalar_one()
            assert "current_setting('app.tenant_id'" in default
            found = _unique_indexes(c)
            assert found == {
                NEW_MAILBOX: ["organization_id", "user_id", "provider", "email_address"],
                NEW_DEFAULT: ["organization_id", "user_id"],
            }, found
            before = _shape(c)

        _run_223(url)
        with eng.connect() as c:
            assert _shape(c) == before, "a second run of 223 changed something"

        # One organization left: the unmatched row has one plain owner. The
        # ladder seeds a `default` organization too, so every other one goes.
        with eng.begin() as c:
            c.execute(text("DELETE FROM organization WHERE id <> CAST(:o AS uuid)"),
                      {"o": org_x})
        notices = _run_223(url)
        assert not [n for n in notices if n[0] == "WARNING"], notices
        with eng.connect() as c:
            left = c.execute(text(
                "SELECT count(*) FROM email_accounts WHERE organization_id IS NULL"),
            ).scalar_one()
        assert left == 0, "the only organization did not adopt the unmatched row"

        # The rest of the ladder still applies over 223.
        with eng.begin() as c:
            for path in ladder():
                if _number(path) > n223:
                    _exec_file(c, path)


# ══════════════════════════════════════════════════════════════════════════
# Source fences — no database
# ══════════════════════════════════════════════════════════════════════════

#: ``ON CONFLICT (`` or ``ON CONFLICT ON CONSTRAINT``: both pin a statement to
#: one index. A bare ``ON CONFLICT DO ...`` survives the index changing.
_TARGET = re.compile(r"ON\s+CONFLICT\s*(\(|ON\s+CONSTRAINT)", re.I)
_INSERT_EA = re.compile(r"INSERT\s+INTO\s+(?:public\.)?email_accounts\b", re.I)
#: Where one statement ends: a semicolon, the close of a Python string, or the
#: next INSERT.
_END = re.compile(r";|\"\"\"|'''|INSERT\s+INTO", re.I)


def _conflict_targets(source: str) -> list[str]:
    found = []
    for m in _INSERT_EA.finditer(source):
        rest = source[m.end():m.end() + 4000]
        end = _END.search(rest)
        stmt = rest[: end.start()] if end else rest
        if _TARGET.search(stmt):
            found.append(stmt[:120])
    return found


def _sources() -> list[Path]:
    files = sorted(_MIGRATIONS.glob("[0-9]*_*.sql"))
    for top in ("apps", "packages"):
        files += sorted(p for p in (_ROOT / top).rglob("*.py")
                        if "node_modules" not in p.parts and ".venv" not in p.parts)
    return files


class TestNoConflictTarget:

    def test_the_scan_sees_a_named_target(self) -> None:
        """The fence is not vacuous."""
        assert _conflict_targets(
            "INSERT INTO email_accounts (id) VALUES (1) "
            "ON CONFLICT (user_id, provider, email_address) DO NOTHING")
        assert _conflict_targets(
            f'"""INSERT INTO email_accounts (id) VALUES (1)\n'
            f'ON CONFLICT ON CONSTRAINT {OLD_CONSTRAINT} DO NOTHING"""')
        assert not _conflict_targets(
            "INSERT INTO email_accounts (id) VALUES (1) ON CONFLICT DO NOTHING")
        assert not _conflict_targets(
            "INSERT INTO email_accounts (id) VALUES (1); "
            "INSERT INTO other (id) VALUES (1) ON CONFLICT (id) DO NOTHING")

    def test_no_code_and_no_migration_names_a_conflict_target(self) -> None:
        offenders = {}
        for path in _sources():
            hits = _conflict_targets(path.read_text(encoding="utf-8", errors="replace"))
            if hits:
                offenders[str(path.relative_to(_ROOT))] = hits
        assert not offenders, (
            "these name a conflict target on email_accounts, so replacing its "
            f"unique index breaks them at plan time: {offenders}"
        )

    def test_no_code_names_a_unique_rule_of_email_accounts(self) -> None:
        names = (OLD_CONSTRAINT, OLD_DEFAULT, NEW_MAILBOX, NEW_DEFAULT)
        offenders = sorted(
            str(p.relative_to(_ROOT)) for p in _sources()
            if p.suffix == ".py"
            and any(n in p.read_text(encoding="utf-8", errors="replace") for n in names)
        )
        assert not offenders, offenders


class TestCreateAccountNamesItsTenant:

    def test_the_insert_names_organization_id(self) -> None:
        src = inspect.getsource(accounts.create_account)
        m = re.search(r"INSERT\s+INTO\s+email_accounts\s*\(([^)]*)\)", src, re.S)
        assert m and "organization_id" in m.group(1), (
            "create_account must name organization_id in its INSERT (R5)"
        )
        assert '"anonymous"' not in src, "the anonymous fallback is back"

    @pytest.mark.parametrize(
        "email,org",
        [("m@em-t2a.test", None),
         (None, "00000000-0000-0000-0000-0000000000b2"),
         (None, None)],
    )
    async def test_no_organization_or_no_member_is_403(
        self, email, org, monkeypatch,
    ) -> None:
        def _no_session(*_a, **_kw):
            raise AssertionError("a refused request opened a session")

        monkeypatch.setattr(accounts, "_tenant_session", _no_session)
        user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)
        req = accounts.CreateAccountRequest(
            provider="imap", email_address="m@contoso.test", credentials=_IMAP)
        with pytest.raises(HTTPException) as err:
            await accounts.create_account(req, user=user)
        assert err.value.status_code == 403
