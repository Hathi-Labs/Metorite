"""The margin we intend per tier, and the floor that alarms (migration 029).

Spec: `project-docs/specs/credit_pricing.md` §4.3.

🔴 **This suite exists because the WRITE half never shipped.** 029 said it
plainly — "an agent builds the mechanism, the owner sets the figures". The
table landed, and so did three reads: the pricing board's alarm, its floor
line, and the margin box that seeds every suggestion. No route did. So the
alarm could not fire and the box could not fill, because the only way into the
table was hand-written SQL.

🔴 **The table still ships EMPTY, and this suite proves it.** Every number in
it is a commercial decision H-42 owns. Building the mechanism sets nothing.

⚠️ Fixture idiom copied from `test_customer_console_credit_price.py`: real
Postgres (R8), operator token, per-test tier names.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder, ensure_deployment

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}
ENC_KEY = "test-encryption-key-not-a-real-one"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
        ensure_deployment(conn)
    eng.dispose()


@pytest.fixture
def db():
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def tier(db):
    """A registered tier of this test's own, so nothing collides."""
    slug = f"tier-tm-{uuid.uuid4().hex[:8]}"
    with db.begin() as c:
        c.execute(
            text("INSERT INTO tier_catalog (slug, label) VALUES (:t, :t)"),
            {"t": slug},
        )
    return slug


def _rows(db, tier: str) -> list:
    with db.begin() as c:
        return c.execute(
            text("SELECT margin_multiplier, margin_floor, effective_from "
                 "FROM tier_margin WHERE tier = :t ORDER BY effective_from"),
            {"t": tier},
        ).all()


# ── The claim H-42 rests on ────────────────────────────────────────────────

def test_the_LADDER_seeds_no_margin():
    """🔴 Migration 029 seeds no margin, and no later migration adds one.

    The same claim as `test_the_rate_card_ships_unpriced`. An agent builds the
    mechanism; the owner sets every figure. A seeded multiplier would be a
    commercial number nobody chose, presented to an operator as an answer.

    ⚠️ **Read from the LADDER FILES, never from the live table.** The obvious
    version counts rows, and it is wrong twice over. A scratch database keeps
    rows between runs, so anything an operator staged fails it — this suite met
    that on its first run. And on a FRESH database the usual repair, a DELETE
    in the module fixture, would remove the very rows it claims to look for,
    so the test would pass while a seeded ladder shipped. The files cannot go
    stale and cannot be polluted.
    """
    from pathlib import Path

    ladder = Path(__file__).resolve().parents[2] / "infra" / "customer_console"
    files = sorted(ladder.glob("*.sql"))
    assert files, "no ladder files found; the path is wrong"

    offenders = []
    for f in files:
        body = f.read_text(encoding="utf-8")
        # Strip SQL line comments first. 029 DESCRIBES the table at length,
        # and a file that explains why it seeds nothing must not fail the
        # fence that checks it seeds nothing.
        code = [
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        ]
        if any("INSERT INTO TIER_MARGIN" in line.upper() for line in code):
            offenders.append(f.name)
    assert offenders == [], f"the ladder seeds a margin in {offenders}; H-42 owns those numbers"


# ── The mechanism ──────────────────────────────────────────────────────────

def test_it_records_a_multiplier_and_a_floor(client, db, tier):
    r = client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "2.5", "margin_floor": "0.45"})
    assert r.status_code == 200, r.text

    rows = _rows(db, tier)
    assert len(rows) == 1
    assert rows[0].margin_multiplier == Decimal("2.500")
    assert rows[0].margin_floor == Decimal("0.450")


def test_either_number_may_be_OMITTED(client, db, tier):
    """⚠️ NULL is a real answer, not a missing one.

    A tier with no multiplier offers no suggestion. A tier with no floor never
    alarms. Both are states an operator chooses, and the board draws each
    differently from a zero.
    """
    assert client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_floor": "0.3"}).status_code == 200
    rows = _rows(db, tier)
    assert rows[0].margin_multiplier is None
    assert rows[0].margin_floor == Decimal("0.300")


def test_it_INSERTS_and_never_updates(client, db, tier):
    """A past suggestion stays readable against the numbers that produced it."""
    assert client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "2.0"}).status_code == 200
    assert client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "1.4"}).status_code == 200

    rows = _rows(db, tier)
    assert len(rows) == 2, "the second save overwrote the first"
    assert [r.margin_multiplier for r in rows] == [Decimal("2.000"), Decimal("1.400")]


def test_the_READ_takes_the_LATEST_row(client, db, tier):
    """The board must show what is in force, not the first thing ever set."""
    from customer_console import store

    client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "2.0", "margin_floor": "0.6"})
    client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "1.4", "margin_floor": "0.25"})

    with db.begin() as c:
        mine = [m for m in store.margin_by_tier(c, days=7) if m["tier"] == tier]
    assert mine, "the tier vanished from the margin read"
    assert Decimal(mine[0]["margin_multiplier"]) == Decimal("1.400")
    assert Decimal(mine[0]["margin_floor"]) == Decimal("0.250")


# ── The refusals ───────────────────────────────────────────────────────────

def test_an_unknown_tier_is_REFUSED(client):
    r = client.post("/catalog/tier-margins", headers=OP, json={
        "tier": "tier-does-not-exist", "margin_multiplier": "2"})
    assert r.status_code == 400
    assert "tier_catalog" in r.text


def test_a_multiplier_BELOW_ONE_is_refused_in_words(client, tier):
    """🔴 A multiplier under 1 sells below cost.

    The column's CHECK refuses it too, and that stays the fence — this route is
    not the only thing that can reach the table. What the route adds is a
    sentence naming the mode that DOES record deliberate free: absorbed.
    """
    r = client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "0.8"})
    assert r.status_code == 400
    assert "below cost" in r.text
    assert "absorbed" in r.text


@pytest.mark.parametrize("floor", ["1", "1.5", "-0.1"])
def test_a_floor_outside_zero_to_one_is_refused(client, tier, floor):
    """A floor is a FRACTION. A floor of 1 demands an infinite price."""
    r = client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_floor": floor})
    assert r.status_code == 400
    assert "FRACTION" in r.text or "fraction" in r.text


def test_a_RETRIED_post_at_one_timestamp_answers_409_not_500(client, tier):
    """The trap `/catalog/tier-rates` already answers for.

    An explicit `effective_from` duplicating the primary key used to reach the
    database and 500. A retried POST is an ordinary thing a client does.
    """
    when = "2026-10-01T00:00:00+00:00"
    first = client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "2", "effective_from": when})
    assert first.status_code == 200, first.text

    again = client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "2", "effective_from": when})
    assert again.status_code == 409
    assert "already exists" in again.text


def test_it_is_written_to_the_AUDIT_trail(client, db, tier):
    """A commercial decision leaves a record of who made it."""
    client.post("/catalog/tier-margins", headers=OP, json={
        "tier": tier, "margin_multiplier": "2.5", "margin_floor": "0.5"})
    with db.begin() as c:
        row = c.execute(
            text("SELECT detail FROM control_audit "
                 "WHERE action = 'catalog.tier_margin' "
                 "ORDER BY created_at DESC LIMIT 1")
        ).fetchone()
    assert row is not None, "setting a margin left no audit row"
    assert row.detail["tier"] == tier
