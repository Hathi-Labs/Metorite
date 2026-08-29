"""WS-31 — what a model IS, as opposed to what it is used for.

Spec: ``project-docs/specs/ai_metering_and_analytics.md`` §5.
Migration: 012, ``model_profile``. ⚠️ Named without its directory — a fence
scans this whole file, docstrings included, for a ladder path.

⚠️ **The subject is a number that is WRONG rather than absent.** The operator
console draws these on the card somebody picks a model from, so:

  1. A blank box must arrive as NULL and render as an em dash. Arriving as 0
     puts "0 tokens" on the card, which reads as a broken model rather than a
     missing row — and "free" in the price column, which reads as a bargain.
  2. A second save must UPDATE. A table that accumulated rows would leave the
     console reading whichever one the query happened to sort first.

R8: every test below runs against a real Postgres.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    return eng


@pytest.fixture
def conn(engine):
    """Rolled back, so no test reads a row another one wrote."""
    with engine.connect() as c:
        tx = c.begin()
        try:
            yield c
        finally:
            tx.rollback()


@pytest.fixture
def model() -> str:
    return f"acme/m-{uuid.uuid4().hex[:8]}"


UPSERT = text(
    """
    INSERT INTO model_profile (
        model, label, context_window, max_output,
        vendor_input_per_1m_usd, vendor_output_per_1m_usd,
        description, reads_images, thinks_first, updated_at
    ) VALUES (
        :model, :label, :ctx, :out, :vin, :vout, :descr, :imgs, :think, now()
    )
    ON CONFLICT (model) DO UPDATE SET
        label = EXCLUDED.label,
        context_window = EXCLUDED.context_window,
        max_output = EXCLUDED.max_output,
        vendor_input_per_1m_usd = EXCLUDED.vendor_input_per_1m_usd,
        vendor_output_per_1m_usd = EXCLUDED.vendor_output_per_1m_usd,
        description = EXCLUDED.description,
        reads_images = EXCLUDED.reads_images,
        thinks_first = EXCLUDED.thinks_first,
        updated_at = now()
    """
)


def save(conn, model: str, **over):
    row = {
        "model": model, "label": None, "ctx": None, "out": None,
        "vin": None, "vout": None, "descr": "", "imgs": False, "think": False,
    }
    row.update(over)
    conn.execute(UPSERT, row)


def read(conn, model: str):
    return conn.execute(
        text("SELECT * FROM model_profile WHERE model = :m"), {"m": model}
    ).mappings().first()


class TestUnknownIsNotZero:
    def test_a_blank_measurement_stays_NULL(self, conn, model):
        # 🔴 The failure this table's CHECK constraint exists for. A window of
        # zero reads as a broken model on the card; a NULL renders as a dash.
        save(conn, model)
        row = read(conn, model)
        assert row["context_window"] is None
        assert row["max_output"] is None
        assert row["vendor_input_per_1m_usd"] is None

    def test_a_zero_window_is_REFUSED_by_the_database(self, conn, model):
        # In the schema rather than in whichever caller happens to be careful:
        # the console, a script and a hand-run SQL statement all write here.
        with pytest.raises(Exception) as exc:
            save(conn, model, ctx=0)
        assert "model_profile_positive" in str(exc.value)

    def test_a_negative_price_is_refused(self, conn, model):
        with pytest.raises(Exception) as exc:
            save(conn, model, vin=-1)
        assert "model_profile_positive" in str(exc.value)

    def test_a_price_of_zero_is_ALLOWED(self, conn, model):
        # ⚠️ Unlike a window. A free model is a real thing — a local one, or a
        # vendor's free tier — and refusing zero here would make it unrecordable.
        save(conn, model, vin=0, vout=0)
        assert read(conn, model)["vendor_input_per_1m_usd"] == 0


class TestSavingTwice:
    def test_the_second_save_UPDATES_rather_than_adding(self, conn, model):
        save(conn, model, ctx=100_000)
        save(conn, model, ctx=200_000)
        rows = conn.execute(
            text("SELECT count(*) FROM model_profile WHERE model = :m"),
            {"m": model},
        ).scalar_one()
        assert rows == 1
        assert read(conn, model)["context_window"] == 200_000

    def test_clearing_a_box_clears_the_COLUMN(self, conn, model):
        # ⚠️ The upsert writes every column, so blanking a field must blank the
        # row. A COALESCE-style merge would make a wrong number impossible to
        # remove — an operator would clear the box, save, and see it come back.
        save(conn, model, ctx=200_000)
        save(conn, model, ctx=None)
        assert read(conn, model)["context_window"] is None

    def test_the_two_capability_flags_survive_a_round_trip(self, conn, model):
        save(conn, model, imgs=True, think=True)
        row = read(conn, model)
        assert row["reads_images"] is True
        assert row["thinks_first"] is True


class TestWhatItIsNotTiedTo:
    def test_a_profile_may_exist_for_an_UNDECLARED_model(self, conn, model):
        # ⚠️ Researching models before connecting them is the normal order. A
        # foreign key to `model_capability` would forbid it for no benefit.
        save(conn, model, descr="not connected yet")
        assert read(conn, model)["description"] == "not connected yet"

    def test_a_declared_model_with_NO_profile_is_normal(self, conn):
        # Nothing is seeded, on purpose — a table of hardcoded windows is a
        # mirror of eleven vendors' documentation. The console draws dashes.
        name = f"acme/bare-{uuid.uuid4().hex[:8]}"
        conn.execute(
            text("INSERT INTO model_capability (model, task, invocation) "
                 "VALUES (:m, 'chat', 'acompletion')"),
            {"m": name},
        )
        assert read(conn, name) is None

    def test_it_is_keyed_on_the_MODEL_not_the_model_and_task(self, conn, model):
        # 🔴 A context window is a property of the model. Keying on (model,
        # task) would give a model declared for both chat and image two copies,
        # free to disagree — and then the card has to pick one.
        save(conn, model, ctx=128_000)
        columns = conn.execute(
            text(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                " AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'model_profile'::regclass AND i.indisprimary"
            )
        ).scalars().all()
        assert columns == ["model"]
