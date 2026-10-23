"""R8 — the expand migration of the shared mailbox on a real Postgres (WS-65 SM-1).

Spec: ``project-docs/specs/shared_mailbox.md`` §4.4, §5.2, §7, §8.2 step 1
and §10 row SM-1. The migration is found by CONTENT, never by number (R1).
The table and trigger checks run on the promoted phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS role.

* the two new columns exist and are nullable, with no default;
* the four new tables carry ENABLE and FORCE row level security, and a policy;
* a grant or an assignment on a PRIVATE mailbox fails, and on a shared one
  of the bound organization it passes;
* a grant on a shared mailbox of ANOTHER organization fails;
* the send log takes ``provider_message_id`` once, and refuses each other
  change;
* ``admin`` holds ``email:shared:manage`` in each organization, on the
  promoted catalog and on the replayed ladder, and a new organization gets it
  from ``provision_org_roles``. No other role holds it;
* the migration runs twice with no error.

R7, the mutations. Each one turns a test here red (run 2026-10-23):

* S1 drop ``AND a.shared_since IS NOT NULL`` from the guard ->
  ``test_a_grant_on_a_private_mailbox_fails``.
* S2 drop ``FORCE  ROW LEVEL SECURITY`` from the RLS block and the phase ->
  ``test_the_four_tables_force_row_level_security``.
* S3 drop the ``OLD.provider_message_id IS NOT NULL`` check ->
  ``test_the_send_log_sets_the_provider_id_once``.
* S4 drop part 5 (the rewrite of ``provision_org_roles``) ->
  ``test_the_seed_on_the_promoted_catalog``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_shared_mailbox_schema_r8.py -v -rs
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import apply_ladder
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

TABLES = ("email_mailbox_grants", "email_thread_assignments", "email_send_log",
          "email_mailbox_member_prefs")
CAPABILITY = "email:shared:manage"


def _migration() -> Path:
    """The SM-1 migration, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS email_mailbox_grants" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _assert_non_priv(engine: Any) -> None:
    with engine.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed")


@pytest.fixture()
def boxes(promoted):  # noqa: F811 — a fixture
    """One private and one shared mailbox in org A, one shared in org B."""
    p = promoted
    tag = uuid.uuid4().hex[:8]

    def _box(org: str, shared: bool) -> str:
        with p.admin_engine.begin() as c:
            return str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, organization_id, shared_since) VALUES "
                "(:u, 'microsoft', :m, 'x', CAST(:o AS uuid), "
                "CASE WHEN :s THEN now() END) RETURNING id"),
                {"u": f"conn-{tag}@sm1.test", "m": f"box-{uuid.uuid4().hex[:6]}@sm1.test",
                 "o": org, "s": shared}).scalar_one())

    made = {"private_a": _box(p.org_a, False), "shared_a": _box(p.org_a, True),
            "shared_b": _box(p.org_b, True)}
    try:
        yield made
    finally:
        with p.admin_engine.begin() as c:
            ids = list(made.values())
            for table in TABLES:
                c.execute(text(f"DELETE FROM {table} WHERE account_id = ANY(CAST(:i AS uuid[]))"),
                          {"i": ids})
            c.execute(text("DELETE FROM email_accounts WHERE id = ANY(CAST(:i AS uuid[]))"),
                      {"i": ids})


def _as_app(app_eng: Any, org: str, sql: str, params: dict[str, Any]) -> Any:
    """One statement as the app role, with *org* bound as the tenant."""
    with app_eng.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        return c.execute(text(sql), params)


_GRANT = ("INSERT INTO email_mailbox_grants (account_id, subject, level, created_by) "
          "VALUES (CAST(:a AS uuid), :s, :l, 'admin@sm1.test')")
_ASSIGN = ("INSERT INTO email_thread_assignments (account_id, thread_id, "
           "assignee_email, assigned_by) VALUES (CAST(:a AS uuid), 't-1', "
           "'priya@sm1.test', 'priya@sm1.test')")


# ── The columns and the tables ──────────────────────────────────────────────

def test_the_two_columns_are_nullable_with_no_default(promoted) -> None:  # noqa: F811
    with promoted.admin_engine.connect() as c:
        rows = {
            (r[0], r[1]): (r[2], r[3], r[4])
            for r in c.execute(text(
                "SELECT table_name, column_name, is_nullable, column_default, data_type "
                "FROM information_schema.columns WHERE (table_name, column_name) IN "
                "(('email_accounts', 'shared_since'), ('email_messages', 'drafted_by'))"))
        }
    assert rows[("email_accounts", "shared_since")] == (
        "YES", None, "timestamp with time zone")
    assert rows[("email_messages", "drafted_by")] == ("YES", None, "text")


def test_the_four_tables_force_row_level_security(promoted) -> None:  # noqa: F811
    with promoted.admin_engine.connect() as c:
        for table in TABLES:
            row = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:t)"), {"t": table}).first()
            assert row == (True, True), f"{table} does not FORCE row level security"
            policies = c.execute(text(
                "SELECT count(*) FROM pg_policies WHERE tablename = :t"),
                {"t": table}).scalar()
            assert policies >= 1, f"{table} has no policy"


# ── The guard: a grant opens only a shared row ──────────────────────────────

def test_a_grant_on_a_private_mailbox_fails(promoted, app_engine, boxes) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with pytest.raises(DBAPIError, match="not a shared mailbox"):
        _as_app(app_engine, promoted.org_a, _GRANT,
                {"a": boxes["private_a"], "s": "org", "l": "read"})


def test_a_grant_on_a_shared_mailbox_passes(promoted, app_engine, boxes) -> None:  # noqa: F811
    _as_app(app_engine, promoted.org_a, _GRANT,
            {"a": boxes["shared_a"], "s": "group:sales", "l": "send"})
    with promoted.admin_engine.connect() as c:
        level = c.execute(text(
            "SELECT level FROM email_mailbox_grants WHERE account_id = CAST(:a AS uuid)"),
            {"a": boxes["shared_a"]}).scalar()
    assert level == "send"


def test_a_grant_on_another_orgs_mailbox_fails(promoted, app_engine, boxes) -> None:  # noqa: F811
    with pytest.raises(DBAPIError):
        _as_app(app_engine, promoted.org_a, _GRANT,
                {"a": boxes["shared_b"], "s": "org", "l": "read"})


def test_a_grant_cannot_move_onto_a_private_mailbox(promoted, app_engine, boxes) -> None:  # noqa: F811
    _as_app(app_engine, promoted.org_a, _GRANT,
            {"a": boxes["shared_a"], "s": "org", "l": "read"})
    with pytest.raises(DBAPIError, match="not a shared mailbox"):
        _as_app(app_engine, promoted.org_a,
                "UPDATE email_mailbox_grants SET account_id = CAST(:p AS uuid) "
                "WHERE account_id = CAST(:a AS uuid)",
                {"a": boxes["shared_a"], "p": boxes["private_a"]})


def test_a_bad_subject_or_level_fails(promoted, app_engine, boxes) -> None:  # noqa: F811
    for subject, level in (("Org", "read"), ("sales", "read"), ("org", "write")):
        with pytest.raises(DBAPIError):
            _as_app(app_engine, promoted.org_a, _GRANT,
                    {"a": boxes["shared_a"], "s": subject, "l": level})


def test_an_assignment_on_a_private_mailbox_fails(promoted, app_engine, boxes) -> None:  # noqa: F811
    with pytest.raises(DBAPIError, match="not a shared mailbox"):
        _as_app(app_engine, promoted.org_a, _ASSIGN, {"a": boxes["private_a"]})
    _as_app(app_engine, promoted.org_a, _ASSIGN, {"a": boxes["shared_a"]})


# ── The send log is append-only ─────────────────────────────────────────────

def test_the_send_log_sets_the_provider_id_once(promoted, app_engine, boxes) -> None:  # noqa: F811
    org = promoted.org_a
    row = _as_app(app_engine, org,
                  "INSERT INTO email_send_log (account_id, sent_by, channel, "
                  "recipients_count) VALUES (CAST(:a AS uuid), 'priya@sm1.test', "
                  "'web', 2) RETURNING id", {"a": boxes["shared_a"]}).scalar_one()
    with pytest.raises(DBAPIError, match="only provider_message_id"):
        _as_app(app_engine, org,
                "UPDATE email_send_log SET sent_by = 'rahul@sm1.test' WHERE id = :i",
                {"i": row})
    _as_app(app_engine, org,
            "UPDATE email_send_log SET provider_message_id = 'pm-1' WHERE id = :i",
            {"i": row})
    with pytest.raises(DBAPIError, match="already set"):
        _as_app(app_engine, org,
                "UPDATE email_send_log SET provider_message_id = 'pm-2' WHERE id = :i",
                {"i": row})
    with promoted.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT provider_message_id FROM email_send_log WHERE id = :i"),
            {"i": row}).scalar() == "pm-1"


def test_the_migration_revokes_delete_from_the_app_role() -> None:
    """The suite's app role has a private name and its own grants, so it cannot
    show the REVOKE of ``acb_app``. The text must hold it (§7 item 2)."""
    sql = _migration().read_text(encoding="utf-8")
    assert "REVOKE ALL ON TABLE email_send_log FROM acb_app;" in sql
    assert "GRANT SELECT, INSERT ON TABLE email_send_log TO acb_app;" in sql
    assert "GRANT UPDATE (provider_message_id) ON TABLE email_send_log TO acb_app;" in sql
    assert "DELETE" not in sql.split("REVOKE ALL ON TABLE email_send_log FROM acb_app;")[1] \
        .split("END IF;")[0]


# ── The seed of `email:shared:manage` ───────────────────────────────────────

def _held(conn: Any, org: str) -> dict[str, set[str]]:
    return {
        row[0]: set(row[1] or [])
        for row in conn.execute(
            text("SELECT r.slug, array_agg(p.permission) FROM org_role r "
                 "LEFT JOIN org_role_permission p ON p.role_id = r.id "
                 "WHERE r.organization_id = CAST(:o AS uuid) GROUP BY r.slug"),
            {"o": org},
        )
    }


def _assert_admin_only(held: dict[str, set[str]]) -> None:
    assert CAPABILITY in held["admin"], f"admin does not hold {CAPABILITY}"
    for role in ("manager", "member", "guest", "agent_service"):
        assert CAPABILITY not in held.get(role, set()), f"{role} holds {CAPABILITY}"
    assert "*" in held["owner"]


def test_the_seed_on_the_promoted_catalog(promoted) -> None:  # noqa: F811
    with promoted.admin_engine.connect() as c:
        trans = c.begin()
        try:
            default = c.execute(text(
                "SELECT id::text FROM organization WHERE slug = 'default'")).scalar()
            _assert_admin_only(_held(c, default))
            org = c.execute(text(
                "SELECT provision_organization('sm1-second', 'Second Org', "
                "'chief@sm1-second.test')")).scalar_one()
            _assert_admin_only(_held(c, str(org)))
        finally:
            trans.rollback()


@pytest.fixture(scope="module")
def replayed():
    """The plain ladder, applied twice: no tenancy column, the other arm."""
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def test_the_seed_on_the_replayed_ladder(replayed) -> None:
    with replayed.connect() as c:
        trans = c.begin()
        try:
            default = c.execute(text(
                "SELECT id::text FROM organization WHERE slug = 'default'")).scalar()
            _assert_admin_only(_held(c, default))
            rows = c.execute(text(
                "SELECT count(*) FROM org_role_permission p "
                "JOIN org_role r ON r.id = p.role_id WHERE r.slug = 'admin' "
                "AND p.permission = :c AND r.organization_id = CAST(:o AS uuid)"),
                {"c": CAPABILITY, "o": default}).scalar()
            assert rows == 1, "a replay granted the capability twice"
            org = c.execute(text(
                "SELECT provision_organization('sm1-ladder', 'Ladder Org', "
                "'chief@sm1-ladder.test')")).scalar_one()
            _assert_admin_only(_held(c, str(org)))
        finally:
            trans.rollback()


def test_the_migration_runs_twice(promoted) -> None:  # noqa: F811
    sql = _migration().read_text(encoding="utf-8")
    for _ in range(2):
        with promoted.admin_engine.connect() as c:
            with c.connection.dbapi_connection.cursor() as cur:
                cur.execute(sql)
            c.connection.dbapi_connection.commit()
    with promoted.admin_engine.connect() as c:
        src = c.execute(text(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "WHERE p.proname = 'provision_org_roles' LIMIT 1")).scalar()
        assert src.count(f"'{CAPABILITY}'") == 1
        for table in TABLES:
            assert c.execute(text(
                "SELECT count(*) FROM pg_policies WHERE tablename = :t"),
                {"t": table}).scalar() == 1
