"""The vendor price change log — migration 032's trigger, against a real database.

🔴 **R8, and here it is the whole point.** The rule under test is a Postgres
trigger, and a hermetic fake would agree with whatever SQL it was handed. The
only thing that can say whether `IS DISTINCT FROM` fires on a NULL-to-value
move is Postgres.

📌 **The property that matters is the one that writes NOTHING.** This is a
CHANGE log, not a snapshot log. The feed syncs roughly 2700 models and almost
none of them move, so a sync that changes no price must leave no row. Get that
wrong and the table grows by 2700 rows a day to record nothing.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _URL, reason="R8 requires a REAL Postgres; set CUSTOMER_CONSOLE_DATABASE_URL"
)


@pytest.fixture()
def db():
    from sqlalchemy import create_engine

    return create_engine(_URL, future=True)


@pytest.fixture()
def model(db):
    """A throwaway feed row, removed afterwards along with its history."""
    name = f"zz-test/{uuid.uuid4().hex[:12]}"
    yield name
    with db.begin() as conn:
        conn.execute(text("DELETE FROM vendor_price_history WHERE model = :m"), {"m": name})
        conn.execute(text("DELETE FROM vendor_price_feed WHERE model = :m"), {"m": name})


def _history(conn, model):
    return conn.execute(
        text(
            "SELECT reason, vendor_input_per_1m_usd, vendor_cached_input_per_1m_usd "
            "FROM vendor_price_history WHERE model = :m ORDER BY id"
        ),
        {"m": model},
    ).all()


def _seed(conn, model, *, inp="1.000000", out="3.000000"):
    conn.execute(
        text(
            "INSERT INTO vendor_price_feed "
            "(model, provider, mode, vendor_input_per_1m_usd, vendor_output_per_1m_usd) "
            "VALUES (:m, 'zz', 'chat', :i, :o)"
        ),
        {"m": model, "i": inp, "o": out},
    )


def test_the_first_sighting_is_recorded_as_first(db, model):
    with db.begin() as conn:
        _seed(conn, model)
        rows = _history(conn, model)
    assert [r[0] for r in rows] == ["first"]


def test_a_sync_that_changes_NOTHING_writes_nothing(db, model):
    """🔴 The property that keeps this table small.

    The feed upserts every model on every run and touches `synced_at` each
    time. If that alone wrote history, the log would record 2700 non-events a
    day and the real price moves would be unfindable inside them.
    """
    with db.begin() as conn:
        _seed(conn, model)
        conn.execute(
            text("UPDATE vendor_price_feed SET synced_at = now() WHERE model = :m"),
            {"m": model},
        )
        rows = _history(conn, model)
    assert [r[0] for r in rows] == ["first"], "a no-op sync must not write history"


def test_a_real_price_move_is_recorded_as_change(db, model):
    with db.begin() as conn:
        _seed(conn, model)
        conn.execute(
            text(
                "UPDATE vendor_price_feed SET vendor_input_per_1m_usd = 1.500000 WHERE model = :m"
            ),
            {"m": model},
        )
        rows = _history(conn, model)
    assert [r[0] for r in rows] == ["first", "change"]
    assert str(rows[1][1]) == "1.500000"


def test_a_price_appearing_out_of_NULL_is_a_change(db, model):
    """⚠️ The `IS DISTINCT FROM` case, and the reason `<>` would be wrong.

    `NULL <> 0.1` evaluates to NULL, which is not true, so a plain comparison
    records nothing here. A vendor introducing a cache price for the first time
    is exactly the move somebody wants to see.
    """
    with db.begin() as conn:
        _seed(conn, model)
        conn.execute(
            text(
                "UPDATE vendor_price_feed SET vendor_cached_input_per_1m_usd = 0.100000 "
                "WHERE model = :m"
            ),
            {"m": model},
        )
        rows = _history(conn, model)
    assert [r[0] for r in rows] == ["first", "change"]
    assert str(rows[1][2]) == "0.100000"


def test_a_price_going_BACK_to_null_is_also_a_change(db, model):
    with db.begin() as conn:
        _seed(conn, model)
        conn.execute(
            text("UPDATE vendor_price_feed SET vendor_output_per_1m_usd = NULL WHERE model = :m"),
            {"m": model},
        )
        rows = _history(conn, model)
    assert [r[0] for r in rows] == ["first", "change"]


def test_successive_moves_each_get_their_own_row(db, model):
    """The read this table exists for: a price over time, in order."""
    with db.begin() as conn:
        _seed(conn, model)
        for price in ("1.100000", "1.200000", "1.300000"):
            conn.execute(
                text("UPDATE vendor_price_feed SET vendor_input_per_1m_usd = :p WHERE model = :m"),
                {"m": model, "p": price},
            )
        rows = _history(conn, model)
    assert [r[0] for r in rows] == ["first", "change", "change", "change"]
    assert [str(r[1]) for r in rows] == [
        "1.000000",
        "1.100000",
        "1.200000",
        "1.300000",
    ]


def test_the_reason_vocabulary_is_closed(db, model):
    """A third word would split a bucket silently. The constraint refuses it."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO vendor_price_history (model, provider, reason) "
                "VALUES (:m, 'zz', 'adjusted')"
            ),
            {"m": model},
        )
