"""Migration 233 — an activated member is ACTIVE in the directory.

Owner report, 2026-10-08: the ClickUp import matched 1 of 51 people. The
import reads ``people`` rows with ``status = 'active'``, and every colleague
but the founder was still ``invited`` there, though Organisation showed them
active. Migration 206's trigger created the row at invite time and then met
it with ``ON CONFLICT DO NOTHING`` at activation, so the row never moved.

**R8 throughout.** Every claim is about a trigger and a backfill on a real
Postgres. A fake has no triggers.

⚠️ **Writes here COMMIT.** Every test seeds a unique address and deletes it in
``finally``.

⚠️ **The module fixture re-applies 233.** ``test_people_from_membership``
re-runs 206 in one test, which puts 206's function back. Without the re-apply,
the order the files run in would decide what this file tests.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "infra" / "postgres" / "233_people_status_follows_member.sql"


def _apply(engine) -> None:
    # The file carries its own BEGIN/COMMIT.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(MIGRATION.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    _apply(engine)
    yield engine
    engine.dispose()


def _org(conn) -> uuid.UUID:
    oid = uuid.uuid4()
    conn.execute(text(
        "INSERT INTO organization (id, slug, display_name) "
        "VALUES (:id, :slug, 'Status Test') ON CONFLICT DO NOTHING"),
        {"id": oid, "slug": f"sts-{oid.hex[:8]}"})
    return oid


def _address(tag: str = "m") -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@status.example"


def _row(conn, email: str) -> dict:
    rows = [dict(r) for r in conn.execute(text(
        "SELECT name, status FROM people WHERE lower(email) = lower(:e)"),
        {"e": email}).mappings()]
    assert len(rows) == 1, rows
    return rows[0]


def _member(conn, email: str, name: str, org, status: str) -> None:
    conn.execute(text(
        "INSERT INTO app_user (email, display_name, organization_id, status) "
        "VALUES (:e, :n, :o, :s)"), {"e": email, "n": name, "o": org, "s": status})


def _cleanup(engine, *emails: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM people WHERE email = ANY(:e)"),
                     {"e": list(emails)})
        conn.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"),
                     {"e": list(emails)})


# ══════════════════════════════════════════════════════════════════════════
# The trigger
# ══════════════════════════════════════════════════════════════════════════

def test_activating_an_invited_member_makes_their_row_active(eng) -> None:
    """The defect itself. This is the assertion that failed on production."""
    email = _address("act")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            _member(conn, email, "Divya JM", org, "invited")
            assert _row(conn, email)["status"] == "invited"
            conn.execute(text(
                "UPDATE app_user SET status = 'active' WHERE email = :e"),
                {"e": email})
            assert _row(conn, email)["status"] == "active"
    finally:
        _cleanup(eng, email)


@pytest.mark.parametrize("chosen", ["contractor", "alumni"])
def test_a_status_chosen_in_People_is_never_overridden(eng, chosen: str) -> None:
    """Only ``invited -> active``. Anything else is a decision in People."""
    email = _address("keep")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            _member(conn, email, "Kept", org, "invited")
            conn.execute(text(
                "UPDATE people SET status = :s WHERE lower(email) = :e"),
                {"s": chosen, "e": email})
            conn.execute(text(
                "UPDATE app_user SET status = 'active' WHERE email = :e"),
                {"e": email})
            assert _row(conn, email)["status"] == chosen
    finally:
        _cleanup(eng, email)


def test_suspending_a_member_does_not_touch_the_row(eng) -> None:
    """D63's question (H-49) stays open. Nothing here guesses at it."""
    email = _address("susp")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            _member(conn, email, "Paused", org, "active")
            conn.execute(text(
                "UPDATE app_user SET status = 'suspended' WHERE email = :e"),
                {"e": email})
            assert _row(conn, email)["status"] == "active"
    finally:
        _cleanup(eng, email)


def test_a_placeholder_name_takes_the_members_name(eng) -> None:
    """An invite with no name leaves the address prefix as the name.

    Production held ``ddheeraj832`` for a member whose name is ``Dheeraj``,
    so the import's name match could not find him.
    """
    email = _address("anon")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            _member(conn, email, "", org, "invited")
            assert _row(conn, email)["name"] == email.split("@")[0]
            conn.execute(text(
                "UPDATE app_user SET display_name = 'Dheeraj', "
                "status = 'active' WHERE email = :e"), {"e": email})
            assert _row(conn, email) == {"name": "Dheeraj", "status": "active"}
    finally:
        _cleanup(eng, email)


def test_a_name_somebody_chose_is_never_rewritten(eng) -> None:
    email = _address("own")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            _member(conn, email, "", org, "invited")
            conn.execute(text(
                "UPDATE people SET name = 'What They Chose' "
                " WHERE lower(email) = :e"), {"e": email})
            conn.execute(text(
                "UPDATE app_user SET display_name = 'Upstream', "
                "status = 'active' WHERE email = :e"), {"e": email})
            assert _row(conn, email) == {"name": "What They Chose", "status": "active"}
    finally:
        _cleanup(eng, email)


# ══════════════════════════════════════════════════════════════════════════
# The backfill
# ══════════════════════════════════════════════════════════════════════════

def test_the_backfill_repairs_a_drifted_row_and_is_idempotent(eng) -> None:
    """The 2026-10-08 production shape: a member active, their row invited.

    The drift is built with the trigger disabled, which is the state 206's
    function left behind. The backfill must repair it, touch nothing else,
    and write nothing on a second run.
    """
    drifted, chosen = _address("drift"), _address("chosen")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            _member(conn, drifted, "", org, "invited")
            _member(conn, chosen, "Freelance", org, "invited")
            conn.execute(text(
                "UPDATE people SET status = 'contractor' WHERE lower(email) = :e"),
                {"e": chosen})
            conn.execute(text(
                "ALTER TABLE app_user DISABLE TRIGGER trg_app_user_directory_row"))
            conn.execute(text(
                "UPDATE app_user SET status = 'active', display_name = 'Kiran Kumar' "
                " WHERE email = ANY(:e)"), {"e": [drifted, chosen]})
            conn.execute(text(
                "ALTER TABLE app_user ENABLE TRIGGER trg_app_user_directory_row"))
            assert _row(conn, drifted)["status"] == "invited"

        _apply(eng)
        with eng.begin() as conn:
            assert _row(conn, drifted) == {"name": "Kiran Kumar", "status": "active"}
            assert _row(conn, chosen) == {"name": "Freelance", "status": "contractor"}
            stamp = conn.execute(text(
                "SELECT updated_at FROM people WHERE lower(email) = :e"),
                {"e": drifted}).scalar()

        _apply(eng)
        with eng.begin() as conn:
            again = conn.execute(text(
                "SELECT updated_at FROM people WHERE lower(email) = :e"),
                {"e": drifted}).scalar()
        assert again == stamp, "the second run rewrote a row it had repaired"
    finally:
        _cleanup(eng, drifted, chosen)


def _body() -> str:
    """233 without its own BEGIN/COMMIT, so a test can roll it back."""
    sql = MIGRATION.read_text(encoding="utf-8")
    return sql.replace("\nBEGIN;\n", "\n", 1).replace("\nCOMMIT;\n", "\n", 1)


def test_the_trigger_and_backfill_match_ONLY_the_members_tenant(eng) -> None:
    """Migration 209 lets two customers each hold a row for one address.

    Activation at customer A must not touch customer B's row, in the trigger
    or in the backfill. This adds the column the generated tenancy layer adds
    (H-104) and rebuilds the function around it.

    ⚠️ **All of it is ONE transaction, rolled back.** An ADD COLUMN that
    commits and a DROP COLUMN after it each spend one of the table's 1600
    column slots for good. That is how the shared scratch database wore out.
    """
    email, drifted = _address("twotenants"), _address("twodrift")
    conn = eng.connect()
    tx = conn.begin()
    try:
        conn.execute(text(
            "ALTER TABLE people ADD COLUMN IF NOT EXISTS "
            "organization_id UUID DEFAULT "
            "current_setting('app.tenant_id', true)::uuid"))
        conn.execute(text(_body()))
        a, b = _org(conn), _org(conn)

        def b_row(addr: str) -> None:
            # B holds a directory entry with no login there, still `invited`.
            conn.execute(text(
                "INSERT INTO people (id, name, email, status, skills, source, "
                "source_key, updated_by, updated_at, organization_id) "
                "VALUES (gen_random_uuid(), 'At B', :e, 'invited', "
                "ARRAY[]::text[], 'manual', :k, 'test', now(), :o)"),
                {"e": addr, "k": f"manual-b:{addr}", "o": b})

        def by_org(addr: str) -> dict:
            return {
                r.organization_id: r.status
                for r in conn.execute(text(
                    "SELECT organization_id, status FROM people "
                    " WHERE lower(email) = :e"), {"e": addr})
            }

        # The trigger.
        b_row(email)
        _member(conn, email, "At A", a, "invited")
        conn.execute(text(
            "UPDATE app_user SET status = 'active' WHERE email = :e"), {"e": email})
        assert by_org(email) == {a: "active", b: "invited"}

        # The backfill.
        b_row(drifted)
        _member(conn, drifted, "At A", a, "invited")
        conn.execute(text(
            "ALTER TABLE app_user DISABLE TRIGGER trg_app_user_directory_row"))
        conn.execute(text(
            "UPDATE app_user SET status = 'active' WHERE email = :e"), {"e": drifted})
        conn.execute(text(
            "ALTER TABLE app_user ENABLE TRIGGER trg_app_user_directory_row"))
        assert by_org(drifted) == {a: "invited", b: "invited"}
        conn.execute(text(_body()))
        assert by_org(drifted) == {a: "active", b: "invited"}
    finally:
        tx.rollback()
        conn.close()
