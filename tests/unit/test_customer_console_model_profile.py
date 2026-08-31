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
import typing
import uuid
from decimal import Decimal

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
        vendor_per_minute_usd, vendor_per_character_usd, vendor_per_image_usd,
        description, reads_images, thinks_first, updated_at
    ) VALUES (
        :model, :label, :ctx, :out, :vin, :vout,
        :vmin, :vchar, :vimg, :descr, :imgs, :think, now()
    )
    ON CONFLICT (model) DO UPDATE SET
        label = EXCLUDED.label,
        context_window = EXCLUDED.context_window,
        max_output = EXCLUDED.max_output,
        vendor_input_per_1m_usd = EXCLUDED.vendor_input_per_1m_usd,
        vendor_output_per_1m_usd = EXCLUDED.vendor_output_per_1m_usd,
        vendor_per_minute_usd = EXCLUDED.vendor_per_minute_usd,
        vendor_per_character_usd = EXCLUDED.vendor_per_character_usd,
        vendor_per_image_usd = EXCLUDED.vendor_per_image_usd,
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
        "vmin": None, "vchar": None, "vimg": None,
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


class TestThePerUnitCosts:
    """H-78 (§6A.11a): the three per-unit columns migration 019 adds.

    ⚠️ The profile holds the TASK's natural unit, and the feed holds the
    VENDOR's. litellm prices transcription per second, `task_catalog` prices
    `transcribe` per minute, and the x60 conversion happens once inside the
    FEED-READ projection (`_FEED_COLS`, which §6A.11a clauses 5 to 7 build).
    Nothing here converts anything.

    (This named "the declare-and-prefill seam" until 2026-08-31. §6A.11a
    retracted that seam — it does not exist, and the copy is client-side.)
    """

    def test_all_three_are_nullable_and_default_to_unknown(self, conn, model):
        save(conn, model)
        row = read(conn, model)
        assert row["vendor_per_minute_usd"] is None
        assert row["vendor_per_character_usd"] is None
        assert row["vendor_per_image_usd"] is None

    def test_a_negative_per_minute_price_is_refused(self, conn, model):
        with pytest.raises(Exception) as exc:
            save(conn, model, vmin=-1)
        assert "model_profile_positive" in str(exc.value)

    def test_a_negative_per_character_price_is_refused(self, conn, model):
        with pytest.raises(Exception) as exc:
            save(conn, model, vchar="-0.000015")
        assert "model_profile_positive" in str(exc.value)

    def test_a_negative_per_image_price_is_refused(self, conn, model):
        with pytest.raises(Exception) as exc:
            save(conn, model, vimg=-1)
        assert "model_profile_positive" in str(exc.value)

    def test_a_small_price_survives_the_round_trip(self, conn, model):
        # 🔴 NUMERIC(18, 10), wider than the token columns on purpose. OpenAI
        # text-to-speech charges 0.000015 per character today, and four
        # decimals would store that as zero — a free model on the card.
        save(conn, model, vchar="0.000015", vimg="0.04", vmin="0.006")
        row = read(conn, model)
        assert row["vendor_per_character_usd"] == Decimal("0.000015")
        assert row["vendor_per_image_usd"] == Decimal("0.04")
        assert row["vendor_per_minute_usd"] == Decimal("0.006")

    def test_a_price_of_zero_is_ALLOWED(self, conn, model):
        # The CHECK reads `>= 0`, like the token columns. A free model is a
        # real thing, and only NULL says "nobody has told us".
        save(conn, model, vmin=0, vchar=0, vimg=0)
        assert read(conn, model)["vendor_per_image_usd"] == 0


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


# ---- The ROUTE, not the table (2026-08-30) --------------------------------
#
# Nothing anywhere POSTed /catalog/profiles until the owner did, live, and
# hit a 500 on every save: the route bound `CatalogCaller`, whose OPERATOR
# arm returns None by design, and then read `staff.actor`. So the store
# above was proven and the door in front of it never was. These tests are
# the door's.


class TestTheProfileRoute:
    TOKEN = "test-operator-token"
    OP: typing.ClassVar[dict[str, str]] = {
        "Authorization": f"Bearer {TOKEN}"}

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", self.TOKEN)
        pytest.importorskip("fastapi")
        from customer_console.main import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_a_negative_measurement_is_a_422_not_a_500(self, client):
        """The route mirrors `model_profile_positive` (012) the way
        `set_credit_price` mirrors its CHECK: refused at the door, never
        an IntegrityError stack for the operator to read."""
        r = client.post("/catalog/profiles", headers=self.OP, json={
            "model": f"test/{uuid.uuid4().hex[:8]}",
            "vendor_input_per_1m_usd": "-1"})
        assert r.status_code == 422
        r2 = client.post("/catalog/profiles", headers=self.OP, json={
            "model": f"test/{uuid.uuid4().hex[:8]}", "context_window": 0})
        assert r2.status_code == 422

    def test_an_operator_can_actually_save_a_profile(self, client, engine):
        """The owner's live repro, verbatim: assemblyai/best from the feed -
        every fact null, label null. It answered 500 for every operator."""
        name = f"test/{uuid.uuid4().hex[:8]}"
        r = client.post("/catalog/profiles", headers=self.OP, json={
            "model": name, "label": None,
            "context_window": None, "max_output": None,
            "vendor_input_per_1m_usd": None,
            "vendor_output_per_1m_usd": None,
            "vendor_cached_input_per_1m_usd": None,
            "description": "", "reads_images": False, "thinks_first": False})
        assert r.status_code == 200, r.text

        with engine.begin() as conn:
            row = read(conn, name)
            assert row is not None
            # And the audit row names a real actor - the crash site.
            actor = conn.execute(text(
                "SELECT actor FROM control_audit "
                "WHERE action = 'catalog.profile' "
                "ORDER BY created_at DESC LIMIT 1")).scalar()
            conn.execute(text(
                "DELETE FROM model_profile WHERE model = :m"), {"m": name})
        assert actor, "the profile audit row must name who saved it"

    def test_the_per_unit_prices_read_back_BYTE_IDENTICAL(
            self, client, engine):
        """🔴 §6A.11a clause 6: the profile write is a PASS-THROUGH.

        The one unit change in this feature — per second to per minute —
        happens in the FEED READ, once. If this route also converted, a
        transcription price would be multiplied by 3600 between the box the
        operator typed it into and the column billing reads, and nothing on
        any screen would say so.

        So: post three per-unit prices, read the three columns, and demand
        the same numbers. Add any arithmetic to `set_model_profile` and this
        test goes red.
        """
        name = f"test/{uuid.uuid4().hex[:8]}"
        r = client.post("/catalog/profiles", headers=self.OP, json={
            "model": name,
            "vendor_per_minute_usd": "0.006",
            "vendor_per_character_usd": "0.000015",
            "vendor_per_image_usd": "0.04",
        })
        assert r.status_code == 200, r.text

        with engine.begin() as conn:
            row = read(conn, name)
            assert row is not None
            got = (row["vendor_per_minute_usd"],
                   row["vendor_per_character_usd"],
                   row["vendor_per_image_usd"])
            conn.execute(text(
                "DELETE FROM model_profile WHERE model = :m"), {"m": name})
        assert got == (Decimal("0.006"), Decimal("0.000015"), Decimal("0.04"))

    def test_the_per_unit_prices_UPSERT_like_every_other_column(
            self, client, engine):
        """A second save must UPDATE. `set_model_profile` is the one catalog
        write that is not insert-only, and a per-unit price left behind by
        the ON CONFLICT list would freeze at whatever landed first."""
        name = f"test/{uuid.uuid4().hex[:8]}"
        for price in ("0.006", "0.012"):
            r = client.post("/catalog/profiles", headers=self.OP, json={
                "model": name, "vendor_per_minute_usd": price})
            assert r.status_code == 200, r.text

        with engine.begin() as conn:
            row = read(conn, name)
            assert row is not None
            got = row["vendor_per_minute_usd"]
            conn.execute(text(
                "DELETE FROM model_profile WHERE model = :m"), {"m": name})
        assert got == Decimal("0.012")

    def test_a_negative_per_unit_price_is_a_422_not_a_500(self, client):
        """The route mirrors `model_profile_positive`'s new clauses too."""
        for field in ("vendor_per_minute_usd", "vendor_per_character_usd",
                      "vendor_per_image_usd"):
            r = client.post("/catalog/profiles", headers=self.OP, json={
                "model": f"test/{uuid.uuid4().hex[:8]}", field: "-1"})
            assert r.status_code == 422, f"{field} answered {r.status_code}"

    def test_the_catalog_read_sends_the_three_as_STRINGS(
            self, client, engine):
        """Money as strings, the rule every price on this wire follows. A
        parsed float re-formatted is how 0.000015 stops matching itself."""
        name = f"test/{uuid.uuid4().hex[:8]}"
        r = client.post("/catalog/profiles", headers=self.OP, json={
            "model": name,
            "vendor_per_minute_usd": "0.006",
            "vendor_per_character_usd": "0.000015",
            "vendor_per_image_usd": "0.04",
        })
        assert r.status_code == 200, r.text
        try:
            body = client.get("/catalog/models", headers=self.OP)
            assert body.status_code == 200, body.text
            profile = next(
                p for p in body.json()["profiles"] if p["model"] == name)
        finally:
            with engine.begin() as conn:
                conn.execute(text(
                    "DELETE FROM model_profile WHERE model = :m"),
                    {"m": name})
        assert profile["vendor_per_minute_usd"] == "0.0060000000"
        assert profile["vendor_per_character_usd"] == "0.0000150000"
        assert profile["vendor_per_image_usd"] == "0.0400000000"
        # ⚠️ The per-SECOND name belongs to the FEED table alone. It must
        # never appear on a profile.
        assert "vendor_per_second_usd" not in profile

    def test_a_customer_key_may_NOT_write_our_reference_data(self, client):
        """The door also admitted `can_pay` customer keys - D66 in spirit:
        the customer never brings a model, so they never describe one
        either. A key-shaped token must be refused, not served."""
        r = client.post(
            "/catalog/profiles",
            headers={"Authorization": "Bearer cc_live_abcd_efgh12345678"},
            json={"model": "x/y"})
        assert r.status_code in (401, 403)

    def test_anonymous_is_refused(self, client):
        assert client.post(
            "/catalog/profiles", json={"model": "x/y"}
        ).status_code in (401, 403)
