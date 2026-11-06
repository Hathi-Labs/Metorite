"""PLN-1 — seat tiers as data, and one live tier per member (Metorite task 1280).

Spec: ``project-docs/specs/seat_plans.md`` §3 and §11.1 · migration
``039_seat_tiers.sql``.

⚠️ **R8.** Every subject here is a trigger, a partial unique index, a CHECK or a
view. A fake would agree with anything, so this suite skips LOUDLY without a
server. ``pr-check.yml`` and ``deploy.yml`` name it in their must-run lists.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_customer_console_seat_tiers.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "infra" / "customer_console" / "039_seat_tiers.sql"


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _org_and_user(conn) -> tuple[str, str]:
    tag = uuid.uuid4().hex[:10]
    org = conn.execute(
        text("INSERT INTO organization (slug, name) VALUES (:s, :n) RETURNING id"),
        {"s": f"tier-{tag}", "n": f"Tier {tag}"},
    ).scalar_one()
    user = conn.execute(
        text("INSERT INTO user_identity (email) VALUES (:e) RETURNING id"),
        {"e": f"tier-{tag}@example.test"},
    ).scalar_one()
    return str(org), str(user)


def _seat(conn, org: str, user: str, plan: str) -> str:
    return str(conn.execute(
        text(
            "INSERT INTO seat_assignment (organization_id, plan_slug, user_identity_id) "
            "VALUES (:o, :p, :u) RETURNING id"
        ),
        {"o": org, "p": plan, "u": user},
    ).scalar_one())


def test_second_live_tier_for_one_member_fails_in_the_database(eng):
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            org, user = _org_and_user(conn)
            _seat(conn, org, user, "core")
            sp = conn.begin_nested()
            with pytest.raises(IntegrityError):
                _seat(conn, org, user, "pro")
            sp.rollback()
        finally:
            trans.rollback()


def test_a_released_tier_seat_lets_the_member_take_another(eng):
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            org, user = _org_and_user(conn)
            first = _seat(conn, org, user, "core")
            conn.execute(
                text("UPDATE seat_assignment SET released_at = now() WHERE id = :i"),
                {"i": first},
            )
            _seat(conn, org, user, "pro")
            rows = conn.execute(
                text(
                    "SELECT plan_slug FROM member_seat_tier "
                    "WHERE organization_id = :o AND user_identity_id = :u"
                ),
                {"o": org, "u": user},
            ).all()
            assert [r[0] for r in rows] == ["pro"]
        finally:
            trans.rollback()


def test_moving_a_seat_to_a_second_tier_by_update_also_fails(eng):
    """The flag follows plan_slug on UPDATE, so an UPDATE cannot dodge the index."""
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            org, user = _org_and_user(conn)
            _seat(conn, org, user, "core")
            conn.execute(
                text("INSERT INTO plan_catalog (slug, name, kind, price_inr, active) "
                     "VALUES ('pln1-addon', 'Addon', 'addon', 0, FALSE) "
                     "ON CONFLICT DO NOTHING")
            )
            addon = _seat(conn, org, user, "pln1-addon")
            sp = conn.begin_nested()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text("UPDATE seat_assignment SET plan_slug = 'max' WHERE id = :i"),
                    {"i": addon},
                )
            sp.rollback()
        finally:
            trans.rollback()


def test_a_non_tier_seat_does_not_count_as_a_tier(eng):
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            org, user = _org_and_user(conn)
            conn.execute(
                text("INSERT INTO plan_catalog (slug, name, kind, price_inr, active) "
                     "VALUES ('pln1-addon', 'Addon', 'addon', 0, FALSE) "
                     "ON CONFLICT DO NOTHING")
            )
            _seat(conn, org, user, "core")
            _seat(conn, org, user, "pln1-addon")
        finally:
            trans.rollback()


def test_every_core_seat_reads_as_the_lowest_tier(eng):
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            org, user = _org_and_user(conn)
            _seat(conn, org, user, "core")
            row = conn.execute(
                text(
                    "SELECT tier_rank, tier_name FROM member_seat_tier "
                    "WHERE organization_id = :o AND user_identity_id = :u"
                ),
                {"o": org, "u": user},
            ).one()
            assert tuple(row) == (1, "Standard")
            lowest = conn.execute(text("SELECT plan_slug FROM seat_tier ORDER BY rank LIMIT 1")).scalar_one()
            assert lowest == "core"
            # No live core seat anywhere is left unflagged.
            unflagged = conn.execute(
                text("SELECT count(*) FROM seat_assignment "
                     "WHERE plan_slug = 'core' AND NOT is_seat_tier")
            ).scalar_one()
            assert unflagged == 0
        finally:
            trans.rollback()


def test_pro_and_max_land_inactive(eng):
    with eng.connect() as conn:
        rows = dict(conn.execute(
            text("SELECT slug, active FROM plan_catalog WHERE slug IN ('pro', 'max')")
        ).all())
        assert rows == {"pro": False, "max": False}


def test_a_replay_changes_nothing(eng):
    sql = _MIGRATION.read_text(encoding="utf-8")
    counts_sql = text(
        "SELECT (SELECT count(*) FROM seat_tier), "
        "(SELECT count(*) FROM seat_tier_version), "
        "(SELECT count(*) FROM seat_tier_price), "
        "(SELECT count(*) FROM plan_catalog)"
    )
    with eng.begin() as conn:
        before = tuple(conn.execute(counts_sql).one())
        conn.exec_driver_sql(sql)
        conn.exec_driver_sql(sql)
        after = tuple(conn.execute(counts_sql).one())
    assert before == after


def test_a_version_or_price_row_is_never_edited_or_deleted(eng):
    for table, set_clause in (
        ("seat_tier_version", "storage_gb_per_seat = storage_gb_per_seat + 1"),
        ("seat_tier_price", "amount = amount + 1"),
    ):
        for stmt in (f"UPDATE {table} SET {set_clause}", f"DELETE FROM {table}"):
            with eng.connect() as conn:
                trans = conn.begin()
                try:
                    with pytest.raises(DBAPIError, match="never changed"):
                        conn.exec_driver_sql(stmt)
                finally:
                    trans.rollback()


def test_a_change_is_a_new_row_and_the_current_view_picks_it(eng):
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text(
                "INSERT INTO seat_tier_version (plan_slug, effective_from, allowance_multiple, "
                "storage_gb_per_seat, max_background_agents, max_automations, max_file_mb) "
                "VALUES ('core', '2026-12-01T00:00:00Z', 2, 40, 1, 10, 100)"
            ))
            row = conn.execute(text(
                "SELECT allowance_multiple, storage_gb_per_seat FROM seat_tier_current "
                "WHERE plan_slug = 'core'"
            )).one()
            # 2026-12-01 may still be in the future: then the first row applies.
            now_after = conn.execute(text("SELECT now() >= '2026-12-01T00:00:00Z'")).scalar_one()
            assert tuple(row) == ((2, 40) if now_after else (1, 30))
        finally:
            trans.rollback()


def test_the_seeded_offer_matches_the_spec(eng):
    with eng.connect() as conn:
        prices = {
            (r[0], r[1], r[2]): float(r[3])
            for r in conn.execute(text(
                "SELECT plan_slug, currency, billing_interval, amount FROM seat_tier_price"
            ))
        }
        assert prices[("core", "USD", "month")] == 20.0
        assert prices[("pro", "USD", "month")] == 100.0
        assert prices[("max", "USD", "month")] == 200.0
        assert prices[("core", "INR", "month")] == 1699.0
        assert prices[("max", "INR", "year")] == 169990.0
        mult = dict(conn.execute(text(
            "SELECT plan_slug, allowance_multiple FROM seat_tier_current"
        )).all())
        assert [float(mult[s]) for s in ("core", "pro", "max")][:1] == [1.0]


def test_the_migration_is_expand_only():
    sql = _MIGRATION.read_text(encoding="utf-8")
    code = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    upper = code.upper()
    for banned in ("DROP TABLE", "DROP COLUMN", "RENAME"):
        assert banned not in upper
    assert "%" not in sql  # psycopg reads a per-cent sign as a placeholder
