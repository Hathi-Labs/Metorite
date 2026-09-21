"""Migration 206 — a member of the organization IS in the directory.

Spec: ``people_center_app.md`` §2 · owner directive 2026-09-20.

**What this replaces.** PR #326 shipped `POST /people/sync-members` and a
"Sync members" button. The owner's objection was the right one: an invariant a
human maintains by hand is an invariant that is false most of the time, and
silently. Migration 206 puts the rule in the database, where none of the five
``app_user`` writers can skip it and where SQL run on the box during an
incident still obeys it.

**R8 throughout, and there is no hermetic half.** Every claim here is about a
TRIGGER: whether it fires, on which statements, what it writes, and what it
declines to write. A fake agrees with whatever SQL it is handed and has no
triggers at all, so a hermetic version of this file would assert only that the
test's own dictionary works. That is the failure mode R8 exists for.

⚠️ **Writes here COMMIT.** Every test seeds a unique address and deletes it in
``finally``.

⚠️ **The ladder does NOT build the tenancy layer** — ``_tenant_ladder`` skips
``infra/postgres/generated/``, so ``people`` here has no
``organization_id`` and no row-level security. That exercises migration 206's
*no-column* arm. The *with-column* arm is exercised by
:func:`test_the_trigger_fills_the_tenant_when_the_column_exists`, which adds
the column the generated layer would and rebuilds the function, because that
is the shape production may be in and nobody knows which (H-104).
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
MIGRATION = REPO / "infra" / "postgres" / "206_people_from_membership.sql"


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _org(conn) -> uuid.UUID:
    oid = uuid.uuid4()
    conn.execute(text(
        "INSERT INTO organization (id, slug, display_name) "
        "VALUES (:id, :slug, 'Trigger Test') ON CONFLICT DO NOTHING"),
        {"id": oid, "slug": f"trg-{oid.hex[:8]}"})
    return oid


def _address(tag: str = "m") -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@membership.example"


def _directory(conn, email: str) -> list[dict]:
    return [dict(r) for r in conn.execute(text(
        "SELECT name, email, status, source, source_key FROM people "
        " WHERE lower(email) = lower(:e)"), {"e": email}).mappings()]


def _cleanup(engine, *emails: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM people WHERE email = ANY(:e)"),
                     {"e": list(emails)})
        conn.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"),
                     {"e": list(emails)})


# ══════════════════════════════════════════════════════════════════════════
# The trigger
# ══════════════════════════════════════════════════════════════════════════

def test_inviting_a_member_creates_their_directory_row(eng) -> None:
    """The whole point. No endpoint is called and no button is pressed."""
    email = _address()
    try:
        with eng.begin() as conn:
            org = _org(conn)
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Ada Lovelace', :o, 'active')"),
                {"e": email, "o": org})
            rows = _directory(conn, email)
        assert len(rows) == 1
        assert rows[0]["name"] == "Ada Lovelace"
        assert rows[0]["status"] == "active"
        assert rows[0]["source"] == "member"
    finally:
        _cleanup(eng, email)


def test_the_source_key_is_the_ADDRESS_not_the_name(eng) -> None:
    """Migration 148's second unique index, and the trap PR #306 hit.

    148 backfills `source_key` as `<source>:<lower(name)>`. Two members who
    share a name collide on that index and abort the ladder replay. Keyed on
    the address — which is what is actually unique for this source — the row
    is also skipped by that backfill's `WHERE source_key IS NULL`.
    """
    one, two = _address("dup1"), _address("dup2")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            for addr in (one, two):
                conn.execute(text(
                    "INSERT INTO app_user (email, display_name, "
                    "organization_id, status) "
                    "VALUES (:e, 'Same Name', :o, 'active')"),
                    {"e": addr, "o": org})
            assert _directory(conn, one)[0]["source_key"] == f"member:{one}"
            assert _directory(conn, two)[0]["source_key"] == f"member:{two}"
    finally:
        _cleanup(eng, one, two)


def test_an_invited_member_arrives_as_invited(eng) -> None:
    email = _address("inv")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Not Yet', :o, 'invited')"),
                {"e": email, "o": org})
            assert _directory(conn, email)[0]["status"] == "invited"
    finally:
        _cleanup(eng, email)


def test_a_suspended_member_gets_NO_row(eng) -> None:
    """The two status vocabularies differ, and the gap is not guessed at.

    `app_user.status` has four values and migration 148's CHECK has a
    different four. Only `active` and `invited` map. Inventing `alumni` here
    would put an off-boarded colleague back into the assignee picker, which is
    D63's seal-don't-inherit question (H-49) and is not settled.
    """
    email = _address("susp")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Gone', :o, 'suspended')"),
                {"e": email, "o": org})
            assert _directory(conn, email) == []
    finally:
        _cleanup(eng, email)


def test_activating_an_invited_member_later_still_creates_the_row(eng) -> None:
    """ON UPDATE, not only ON INSERT.

    A member can be created `suspended` or `removed` and reinstated later, and
    the access-request path activates a row that was already there. Without
    the UPDATE arm those people would never reach the directory.
    """
    email = _address("react")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Back Again', :o, 'removed')"),
                {"e": email, "o": org})
            assert _directory(conn, email) == []
            conn.execute(text(
                "UPDATE app_user SET status = 'active' WHERE email = :e"),
                {"e": email})
            assert len(_directory(conn, email)) == 1
    finally:
        _cleanup(eng, email)


def test_a_second_update_does_not_duplicate_or_overwrite(eng) -> None:
    """Idempotent, and it never rewrites what the person has since edited.

    The row is theirs once it exists. A trigger that re-asserted the name on
    every `app_user` touch would silently undo somebody's own profile edit.
    """
    email = _address("once")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Original', :o, 'active')"),
                {"e": email, "o": org})
            conn.execute(text(
                "UPDATE people SET name = 'What They Chose', "
                "title = 'Staff Engineer' WHERE lower(email) = :e"),
                {"e": email})
            conn.execute(text(
                "UPDATE app_user SET display_name = 'Renamed Upstream' "
                " WHERE email = :e"), {"e": email})
            rows = _directory(conn, email)
        assert len(rows) == 1
        assert rows[0]["name"] == "What They Chose"
    finally:
        _cleanup(eng, email)


def test_a_member_with_no_address_is_skipped_rather_than_failing(eng) -> None:
    """A trigger that raised would make the member write fail outright.

    An address is the join key, so a row without one cannot be a directory
    entry. Declining is right; taking the invite down with it is not.
    """
    with eng.begin() as conn:
        org = _org(conn)
        # `app_user.email` is NOT NULL in practice, so the reachable case is
        # whitespace. The member write must still succeed.
        email = "   "
        try:
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Blank', :o, 'active')"),
                {"e": email, "o": org})
            assert conn.execute(text(
                "SELECT count(*) FROM people WHERE name = 'Blank'"),
            ).scalar() == 0
        finally:
            conn.execute(text("DELETE FROM app_user WHERE email = :e"),
                         {"e": email})


def test_a_contractor_keeps_their_row_and_needs_no_login(eng) -> None:
    """§2's other half survives. This change is one-directional.

    A directory row with no `app_user` is the contractor case (D-PC-12), and
    nothing here removes or requires a login for one.
    """
    email = _address("contractor")
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO people (id, name, email, status, skills, "
                "source, source_key, updated_by, updated_at) "
                "VALUES (gen_random_uuid(), 'Freelance Fay', :e, 'contractor', "
                "ARRAY[]::text[], 'manual', :k, 'test', now())"),
                {"e": email, "k": f"manual:{email}"})
            rows = _directory(conn, email)
            has_login = conn.execute(text(
                "SELECT count(*) FROM app_user WHERE lower(email) = :e"),
                {"e": email}).scalar()
        assert rows[0]["status"] == "contractor"
        assert has_login == 0
    finally:
        _cleanup(eng, email)


# ══════════════════════════════════════════════════════════════════════════
# The migration itself
# ══════════════════════════════════════════════════════════════════════════

def test_the_trigger_is_attached_to_app_user(eng) -> None:
    """Structural. The application no longer writes this row at all.

    `provision_member` and the signup path both used to call
    `ensure_directory_row`; that function is gone and the trigger is the only
    writer. If somebody drops it, every invite silently stops producing a
    profile — so its existence is asserted rather than assumed.
    """
    with eng.begin() as conn:
        found = conn.execute(text(
            "SELECT tgname FROM pg_trigger "
            " WHERE tgrelid = 'app_user'::regclass AND NOT tgisinternal"),
        ).scalars().all()
    assert "trg_app_user_directory_row" in found


def test_the_migration_is_idempotent_and_backfills(eng) -> None:
    """Re-running 206 writes nothing new, and picks up a member it missed.

    Both halves in one test because they share a setup: a member row inserted
    with the trigger disabled is exactly the pre-206 state the backfill is
    for, and re-running the whole file must then create that row and no other.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    email = _address("backfill")
    try:
        with eng.begin() as conn:
            org = _org(conn)
            # The pre-206 world: a member with no directory row.
            conn.execute(text(
                "ALTER TABLE app_user DISABLE TRIGGER "
                "trg_app_user_directory_row"))
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Predates It', :o, 'active')"),
                {"e": email, "o": org})
            conn.execute(text(
                "ALTER TABLE app_user ENABLE TRIGGER "
                "trg_app_user_directory_row"))
            assert _directory(conn, email) == []
            before = conn.execute(
                text("SELECT count(*) FROM people")).scalar()

        # The file carries its own BEGIN/COMMIT, so it needs a connection
        # that is not already inside a transaction block.
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(sql))

        with eng.begin() as conn:
            assert len(_directory(conn, email)) == 1, "backfill missed a member"
            after = conn.execute(
                text("SELECT count(*) FROM people")).scalar()
        assert after == before + 1, "the backfill wrote more than it should"

        # Second run: nothing.
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(sql))
        with eng.begin() as conn:
            again = conn.execute(
                text("SELECT count(*) FROM people")).scalar()
        assert again == after, "re-running the migration was not idempotent"
    finally:
        _cleanup(eng, email)


def test_the_trigger_adapts_when_the_TENANCY_LAYER_IS_PROMOTED_LATER(eng) -> None:
    """The regression that 36 errors in `test_h3_rls_promotion_rehearsal` found.

    The first version of this migration chose the INSERT's column list ONCE,
    at migration time, around whichever schema was present that day. Promote
    the generated tenancy layer afterwards — which adds `organization_id`,
    NOT NULL — and the stale function names no such column, so EVERY member
    write fails. Org provisioning is a member write. That is a failed signup,
    arriving from a table nobody would think to look at.

    ⚠️ **This test deliberately does NOT re-run the migration.** Re-running
    would repair the old design too and prove nothing. The column is added
    the way the generated layer adds it, and the trigger is expected to cope
    on its next fire, because it reads the schema at run time.

    It also proves the value comes from `NEW.organization_id` and not from
    `current_setting('app.tenant_id')`: no tenant is bound here, which is
    exactly the state the org-provisioning functions insert an owner in.
    """
    email = _address("promoted")
    try:
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(
                "ALTER TABLE people ADD COLUMN IF NOT EXISTS "
                "organization_id UUID DEFAULT "
                "current_setting('app.tenant_id', true)::uuid"))

        with eng.begin() as conn:
            org = _org(conn)
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Promoted', :o, 'active')"),
                {"e": email, "o": org})
            got = conn.execute(text(
                "SELECT organization_id FROM people "
                " WHERE lower(email) = :e"), {"e": email}).scalar()
        assert got == org, "the trigger did not carry the member's tenant"
    finally:
        _cleanup(eng, email)
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(
                "ALTER TABLE people DROP COLUMN IF EXISTS organization_id"))


def test_the_trigger_can_never_take_down_a_member_write(eng) -> None:
    """Fail-open, and this is the property that matters most.

    A directory row is a convenience. Membership is the product. A trigger
    able to refuse `INSERT INTO app_user` can refuse a SIGNUP — the worst
    outage this system has. So an unexpected failure becomes a WARNING and
    the member is still created.

    The failure is induced the way the real one arrived: a NOT NULL column on
    `people` that the trigger does not fill. The member write must still
    succeed, and the directory row must simply be absent — repairable by
    re-running the migration's backfill.
    """
    email = _address("failopen")
    try:
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            # Added WITH a default so the existing rows satisfy NOT NULL,
            # then the default is dropped — so an INSERT that does not name
            # the column fails, which is the shape of the real breakage.
            conn.execute(text(
                "ALTER TABLE people ADD COLUMN IF NOT EXISTS "
                "trigger_cannot_know text NOT NULL DEFAULT 'seeded'"))
            conn.execute(text(
                "ALTER TABLE people ALTER COLUMN trigger_cannot_know "
                "DROP DEFAULT"))

        with eng.begin() as conn:
            org = _org(conn)
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Survivor', :o, 'active')"),
                {"e": email, "o": org})
            member = conn.execute(text(
                "SELECT count(*) FROM app_user WHERE email = :e"),
                {"e": email}).scalar()
            assert member == 1, "the trigger took the member write down with it"
            assert _directory(conn, email) == []
    finally:
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(
                "ALTER TABLE people DROP COLUMN IF EXISTS "
                "trigger_cannot_know"))
        _cleanup(eng, email)


def test_the_trigger_fills_the_tenant_when_the_column_exists(eng) -> None:
    """The arm the test ladder cannot otherwise reach (H-104).

    The generated tenancy layer is not on the numbered ladder, so nobody knows
    whether production's `people` carries `organization_id`. This adds the
    column the generated file would, re-runs 206 so the function is rebuilt
    around it, and proves the trigger writes the MEMBER's organization rather
    than leaning on `current_setting('app.tenant_id')` — which is unbound
    during org provisioning and would land NULL.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    email = _address("tenanted")
    try:
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(
                "ALTER TABLE people ADD COLUMN IF NOT EXISTS "
                "organization_id UUID DEFAULT "
                "current_setting('app.tenant_id', true)::uuid"))
            conn.execute(text(sql))

        with eng.begin() as conn:
            org = _org(conn)
            # Deliberately NO `app.tenant_id` bound, which is the provisioning
            # case the default cannot serve.
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'Tenanted', :o, 'active')"),
                {"e": email, "o": org})
            got = conn.execute(text(
                "SELECT organization_id FROM people "
                " WHERE lower(email) = :e"), {"e": email}).scalar()
        assert got == org, "the trigger did not carry the member's tenant"
    finally:
        _cleanup(eng, email)
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(
                "ALTER TABLE people DROP COLUMN IF EXISTS organization_id"))
            conn.execute(text(sql))
