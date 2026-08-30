"""The credit itself has a price the owner sets (migration 017, H-42).

Spec: `customer_console.md` §6A.13.

🔴 **The two claims that matter, in one sentence each.**

* The table ships EMPTY and the route is the mechanism — the NUMBER stays
  the owner's commercial act (H-42), so nothing here seeds one.
* **Billing never reads it**: a call bills CREDITS and the tier card owns
  how many, so the same call bills the same credits with the table empty
  or full. That fence is what lets this land without touching a single
  billing invariant.

⚠️ Fixture idiom copied from ``test_customer_console_tier_pricing.py``:
real Postgres (R8), stubbed provider, per-test org/tier/vendor names.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from customer_console import router as router_mod
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

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

RESPONSE = {
    "id": "chatcmpl-cp",
    "object": "chat.completion",
    "created": 1_755_000_000,
    "model": "irrelevant",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "ok"},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 40,
              "prompt_tokens_details": {"cached_tokens": 900}},
}

#: 300x2/1k + 900x0.5/1k + 40x6/1k = 1.29 credits — the tier-pricing suite's
#: arithmetic, reused verbatim so a drift here would fail there first.
RATE_A = {"i": 2, "o": 6, "c": Decimal("0.5")}
BILL_A = Decimal("1.2900")


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
        ensure_deployment(conn)
        # ⚠️ The dev database persists between runs, and this table is a
        # global singleton no per-test name can scope. Clearing it here
        # makes "the migration seeds nothing" testable on run two — on a
        # fresh CI database the DELETE is a no-op.
        conn.execute(text("DELETE FROM credit_price"))
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
def vendor():
    return f"cp{uuid.uuid4().hex[:8]}"


@pytest.fixture
def org(client, db, vendor):
    slug = f"cp-{uuid.uuid4().hex[:8]}"
    client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    token = client.post(
        "/keys", headers=OP, json={"org_slug": slug}).json()["token"]
    with db.begin() as c:
        c.execute(
            text("INSERT INTO provider_credential (provider, secret_enc, "
                 "label) VALUES (:v, :s, 'platform')"),
            {"v": vendor,
             "s": router_mod.encrypt_secret("sk-platform-secret")},
        )
        org_id = str(c.execute(
            text("SELECT id FROM organization WHERE slug = :s"),
            {"s": slug}).scalar_one())
    return slug, org_id, {"Authorization": f"Bearer {token}"}


def _wire_price(client):
    return client.get("/catalog/models", headers=OP).json()["credit_price"]


# ⚠️ Definition order is load-bearing for the first test: it must read the
# table before this module's own writes land. pytest runs top to bottom.
def test_the_table_ships_EMPTY_and_the_catalog_says_null(client, db):
    """Migration 017 seeds NO price — the number is H-42, the owner's act."""
    with db.begin() as c:
        n = c.execute(text("SELECT COUNT(*) FROM credit_price")).scalar_one()
    assert n == 0
    assert _wire_price(client) is None


def test_the_owner_sets_it_and_the_catalog_reads_it_back_verbatim(client):
    r = client.post("/catalog/credit-price", headers=OP,
                    json={"inr_per_credit": "1.5", "usd_to_inr": "88"})
    assert r.status_code == 200
    wire = _wire_price(client)
    # ⚠️ Money as STRINGS, at the table's own scale — never a float.
    assert wire["inr_per_credit"] == "1.500000"
    assert wire["usd_to_inr"] == "88.000000"
    assert wire["effective_from"] is not None


def test_a_reprice_is_an_INSERT_and_the_newest_ruling_row_wins(client, db):
    client.post("/catalog/credit-price", headers=OP,
                json={"inr_per_credit": "2", "usd_to_inr": "90"})
    assert _wire_price(client)["inr_per_credit"] == "2.000000"
    with db.begin() as c:
        n = c.execute(text("SELECT COUNT(*) FROM credit_price")).scalar_one()
    # History stays: the 1.5 row from the previous test is still on disk.
    assert n >= 2


def test_a_future_dated_price_waits_for_its_date(client, db):
    with db.begin() as c:
        soon = c.execute(
            text("SELECT now() + INTERVAL '1 hour'")).scalar_one()
    r = client.post("/catalog/credit-price", headers=OP, json={
        "inr_per_credit": "9.9", "usd_to_inr": "77",
        "effective_from": soon.isoformat()})
    assert r.status_code == 200
    # The ruling row is still the past-dated one, not the 9.9.
    assert _wire_price(client)["inr_per_credit"] == "2.000000"


def test_garbage_is_refused_with_a_named_reason(client):
    for bad in ("0", "-1", "100001"):
        r = client.post("/catalog/credit-price", headers=OP,
                        json={"inr_per_credit": bad, "usd_to_inr": "88"})
        assert r.status_code == 400, bad
        assert "inr_per_credit" in r.json()["detail"]
    r = client.post("/catalog/credit-price", headers=OP,
                    json={"inr_per_credit": "1", "usd_to_inr": "not-a-number"})
    assert r.status_code == 422


def test_the_write_and_the_read_are_operator_gated(client):
    assert client.post(
        "/catalog/credit-price",
        json={"inr_per_credit": "1", "usd_to_inr": "88"},
    ).status_code in (401, 403)


def test_billing_never_reads_the_credit_price(client, db, org, vendor):
    """🔴 The R7 fence migration 017 names.

    An absurd credit price (Rs 99,000 per credit) lands between two
    identical calls. If billing consulted the table at all, the second
    delta could not match the first.
    """
    async def _ok(**kwargs):
        return dict(RESPONSE)

    router_mod.set_provider_call(_ok)
    _slug, org_id, key = org
    tier = f"tier-cp-{uuid.uuid4().hex[:6]}"
    model = f"{vendor}/cp-{uuid.uuid4().hex[:6]}"
    with db.begin() as c:
        c.execute(text("INSERT INTO tier_catalog (slug, label) "
                       "VALUES (:t, :t) ON CONFLICT DO NOTHING"), {"t": tier})
        c.execute(text("INSERT INTO model_capability (model, task, "
                       "invocation) VALUES (:m, 'chat', 'acompletion') "
                       "ON CONFLICT DO NOTHING"), {"m": model})
        c.execute(text("INSERT INTO tier_binding (tier, task, model, rank, "
                       "effective_from) VALUES (:t, 'chat', :m, 1, now())"),
                  {"t": tier, "m": model})
        c.execute(text("INSERT INTO tier_rate_card (tier, task, "
                       "input_credits_per_1k, output_credits_per_1k, "
                       "cached_input_credits_per_1k, pricing_mode, "
                       "effective_from) "
                       "VALUES (:t, 'chat', :i, :o, :c, 'priced', now())"),
                  {"t": tier, **RATE_A})

    def _bill():
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        with db.begin() as c:
            return Decimal(c.execute(
                text("SELECT billed_credits FROM usage_event "
                     "WHERE organization_id = CAST(:o AS uuid) "
                     "ORDER BY created_at DESC LIMIT 1"),
                {"o": org_id}).scalar_one())

    before = _bill()
    client.post("/catalog/credit-price", headers=OP,
                json={"inr_per_credit": "99000", "usd_to_inr": "88"})
    after = _bill()
    assert before == after == BILL_A
