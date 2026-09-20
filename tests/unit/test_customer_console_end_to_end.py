"""The WHOLE product, in one journey: a vendor account to a billed call.

🔴 **Why this suite exists.** Every step of this chain already had a test, and
no test had the chain. A break at any seam -- a tier bound to a model nobody
declared, a price written at the wrong scale, a grant that never reaches the
balance, a key that resolves the wrong organization -- would pass every
existing suite and fail the product. This walks the operator's real path
through the real routes, in order, and then has a customer use it.

The journey, and it is the console's own route for every step:

    1  install a vendor account        POST /providers/credentials
    2  declare two models              POST /catalog/capabilities
    3  record what they cost US        POST /catalog/profiles
    4  bind three tiers to them        POST /catalog/bindings
    5  price the credit itself         POST /catalog/credit-price
    6  set what we aim to keep         POST /catalog/tier-margins
    7  price each tier                 POST /catalog/tier-rates
    8  create the customer             POST /orgs/provision
    9  grant them credits              POST /credits/grant
   10  issue them a key                POST /keys
   11  they call all three tiers       POST /v1/chat/completions
   12  each bills its OWN price, and the balance falls by the sum
   13  drained, the next call is REFUSED and the refusal is recorded

⚠️ **Three tiers, TWO models, one key.** The point of the slate is that a
customer reaches several models through one credential and pays a different
price for each. One tier and one model would prove none of that.

⚠️ **R8.** Every assertion is against rows a real Postgres actually holds.

⚠️ The provider is stubbed. This proves OUR chain end to end -- the metering,
the rating, the ledger and the walls. It does not call a vendor, and it is not
a claim that any vendor's account works.
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

#: 1200 prompt of which 900 cached, 40 completion. So 300 tokens bill at the
#: input rate, 900 at the cached rate, and 40 at the output rate.
RESPONSE = {
    "id": "chatcmpl-e2e",
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

#: Three tiers at three prices, per MILLION credits. The bills below are
#: arithmetic a reader can check:
#:     300 x in/1e6  +  900 x cached/1e6  +  40 x out/1e6
PLAN = {
    "fast":     {"in": 1000, "out": 3000,  "cached": 250,  "bill": Decimal("0.6450")},
    "balanced": {"in": 2000, "out": 6000,  "cached": 500,  "bill": Decimal("1.2900")},
    "powerful": {"in": 4000, "out": 12000, "cached": 1000, "bill": Decimal("2.5800")},
}
TOTAL_BILL = Decimal("4.5150")
GRANT = Decimal("100")


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
def calls():
    """Every provider call the router made, so the test can ask WHICH model
    answered. A bill is only right if the model that earned it is right."""
    seen: list[dict] = []

    async def _ok(**kwargs):
        seen.append(kwargs)
        return dict(RESPONSE)

    router_mod.set_provider_call(_ok)
    yield seen
    router_mod.set_provider_call(None)


def _balance(db, org_id: str) -> Decimal:
    with db.begin() as c:
        return c.execute(
            text("SELECT COALESCE(SUM(delta), 0) FROM credit_ledger "
                 "WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org_id},
        ).scalar_one()


def _ask(client, key, tier):
    return client.post("/v1/chat/completions", headers=key, json={
        "model": tier, "messages": [{"role": "user", "content": "hi"}]})


# ── The journey ────────────────────────────────────────────────────────────

def test_a_vendor_account_becomes_a_billed_call_for_three_tiers(
        client, db, calls):
    """🔴 The whole product, in order, through the console's own routes."""
    run = uuid.uuid4().hex[:8]
    vendor = f"e2e{run}"
    cheap = f"{vendor}/cheap-{run}"
    strong = f"{vendor}/strong-{run}"
    tiers = {name: f"tier-e2e-{name}-{run}" for name in PLAN}

    # ── 1. The vendor account the Router will call on ──────────────────────
    r = client.post("/providers/credentials", headers=OP, json={
        "provider": vendor, "secret": "sk-not-a-real-key", "label": "e2e"})
    assert r.status_code in (200, 201), r.text

    # ── 2. Two models, so the customer reaches more than one ───────────────
    for m in (cheap, strong):
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": m, "task": "chat", "invocation": "acompletion"})
        assert r.status_code in (200, 201), r.text

    # ── 3. What the VENDOR charges US. Not what a customer pays. ───────────
    for m, vin, vout in ((cheap, "0.50", "1.50"), (strong, "3.00", "9.00")):
        r = client.post("/catalog/profiles", headers=OP, json={
            "model": m,
            "vendor_input_per_1m_usd": vin,
            "vendor_output_per_1m_usd": vout,
            "vendor_cached_input_per_1m_usd": "0.10"})
        assert r.status_code in (200, 201), r.text

    # ── 4. Three tiers. Two share the cheap model, one takes the strong. ───
    # 🔴 `fast` and `balanced` run on the SAME model at DIFFERENT prices,
    # which is the thing D67 exists to make possible.
    for name, model in (("fast", cheap), ("balanced", cheap), ("powerful", strong)):
        with db.begin() as c:
            c.execute(
                text("INSERT INTO tier_catalog (slug, label, task) "
                     "VALUES (:t, :t, 'chat') ON CONFLICT DO NOTHING"),
                {"t": tiers[name]})
        r = client.post("/catalog/bindings", headers=OP, json={
            "tier": tiers[name], "task": "chat", "model": model})
        assert r.status_code in (200, 201), r.text

    # ── 5. What one credit is worth. Everything downstream needs it. ───────
    r = client.post("/catalog/credit-price", headers=OP, json={
        "inr_per_credit": "1", "usd_to_inr": "88"})
    assert r.status_code in (200, 201), r.text

    # ── 6. What we aim to keep, and when to complain ───────────────────────
    for name in PLAN:
        r = client.post("/catalog/tier-margins", headers=OP, json={
            "tier": tiers[name], "margin_multiplier": "2.5", "margin_floor": "0.4"})
        assert r.status_code == 200, r.text

    # ── 7. What the CUSTOMER pays, per tier ────────────────────────────────
    for name, p in PLAN.items():
        r = client.post("/catalog/tier-rates", headers=OP, json={
            "tier": tiers[name], "task": "chat", "unit": "tokens",
            "pricing_mode": "priced",
            "input_per_1m": str(p["in"]),
            "output_per_1m": str(p["out"]),
            "cached_input_per_1m": str(p["cached"])})
        assert r.status_code in (200, 201), r.text

    # ── 8. The customer ────────────────────────────────────────────────────
    slug = f"e2e-{run}"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "End To End Ltd",
        "owner_email": f"owner@{slug}.example",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    assert r.status_code in (200, 201), r.text
    with db.begin() as c:
        org_id = str(c.execute(
            text("SELECT id FROM organization WHERE slug = :s"),
            {"s": slug}).scalar_one())

    # A fresh customer owes nothing and holds nothing.
    assert _balance(db, org_id) == 0

    # ── 9. The credits ─────────────────────────────────────────────────────
    r = client.post("/credits/grant", headers=OP, json={
        "org_slug": slug, "credits": str(GRANT),
        "reason": "purchase", "ref": f"bank-{run}"})
    assert r.status_code in (200, 201), r.text
    assert _balance(db, org_id) == GRANT

    # ── 10. The key they call with ─────────────────────────────────────────
    r = client.post("/keys", headers=OP, json={"org_slug": slug})
    assert r.status_code in (200, 201), r.text
    key = {"Authorization": f"Bearer {r.json()['token']}"}

    # ── 11. They use all three tiers, on ONE key ───────────────────────────
    for name in ("fast", "balanced", "powerful"):
        answer = _ask(client, key, tiers[name])
        assert answer.status_code == 200, f"{name}: {answer.text}"

    # ⚠️ The customer reached BOTH models through one credential. One model
    # would prove nothing about a slate.
    served = [c["model"] for c in calls]
    assert served == [cheap, cheap, strong], served
    assert len(set(served)) == 2, "the customer never reached a second model"

    # ── 12. Each tier billed its OWN price ─────────────────────────────────
    with db.begin() as c:
        rows = c.execute(
            text("SELECT tier, model, billed_credits FROM usage_event "
                 "WHERE organization_id = CAST(:o AS uuid) "
                 "ORDER BY created_at"),
            {"o": org_id}).all()
    assert len(rows) == 3, f"expected three usage rows, got {len(rows)}"
    for row, name in zip(rows, ("fast", "balanced", "powerful"), strict=True):
        assert row.tier == tiers[name]
        assert row.billed_credits == PLAN[name]["bill"], (
            f"{name} billed {row.billed_credits}, expected {PLAN[name]['bill']}")

    # 🔴 Two tiers on ONE model billed DIFFERENTLY. That is the slate working.
    assert rows[0].model == rows[1].model
    assert rows[0].billed_credits != rows[1].billed_credits

    # ── and the balance fell by exactly the sum ────────────────────────────
    assert _balance(db, org_id) == GRANT - TOTAL_BILL

    # The customer's own view agrees with the ledger. A billing page that
    # disagreed with the ledger is the defect this line exists to catch.
    # ── and the customer can SEE what they may pick ───────────────────────
    # 🔴 "Access to multiple models" is only real if the customer's own picker
    # offers them. `tier_catalog.customer_visible` decides this, and the three
    # tiers staged above are visible by default, so all three must appear.
    picker = client.get("/my/tiers", headers=key)
    assert picker.status_code == 200, picker.text
    offered = {t["slug"] for t in picker.json()["rows"]}
    for name in PLAN:
        assert tiers[name] in offered, (
            f"{name} bills the customer but its own picker never offers it")

    # ⚠️ And a HIDDEN tier stays hidden. `tier-stt` ships customer_visible
    # FALSE because the app selects it, never a person -- a picker entry for
    # one of those offers a choice nobody can act on.
    assert "tier-stt" not in offered

    mine = client.get("/me/billing", headers=key)
    assert mine.status_code == 200, mine.text
    # ⚠️ Nested under `credits`, and a FLOAT -- the one outlier from this
    # codebase's strings-for-money rule (H-79 tracks the flip).
    assert Decimal(str(mine.json()["credits"]["balanceCredits"])) == GRANT - TOTAL_BILL


def _stage_one_priced_tier(client, db, run):
    """A vendor, a model, a bound tier and a price. The shortest complete
    chain, for the two wall tests below."""
    vendor = f"e2ez{run}"
    model = f"{vendor}/m-{run}"
    tier = f"tier-e2ez-{run}"

    client.post("/providers/credentials", headers=OP, json={
        "provider": vendor, "secret": "sk-not-a-real-key"})
    client.post("/catalog/capabilities", headers=OP, json={
        "model": model, "task": "chat", "invocation": "acompletion"})
    client.post("/catalog/profiles", headers=OP, json={
        "model": model, "vendor_input_per_1m_usd": "1",
        "vendor_output_per_1m_usd": "2",
        "vendor_cached_input_per_1m_usd": "0.1"})
    with db.begin() as c:
        c.execute(text("INSERT INTO tier_catalog (slug, label, task) "
                       "VALUES (:t, :t, 'chat') ON CONFLICT DO NOTHING"),
                  {"t": tier})
    client.post("/catalog/bindings", headers=OP, json={
        "tier": tier, "task": "chat", "model": model})
    client.post("/catalog/credit-price", headers=OP, json={
        "inr_per_credit": "1", "usd_to_inr": "88"})
    client.post("/catalog/tier-rates", headers=OP, json={
        "tier": tier, "task": "chat", "unit": "tokens", "pricing_mode": "priced",
        "input_per_1m": "2000", "output_per_1m": "6000",
        "cached_input_per_1m": "500"})

    slug = f"e2ez-{run}"
    client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "Broke Ltd", "owner_email": f"o@{slug}.example",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    with db.begin() as c:
        org_id = str(c.execute(
            text("SELECT id FROM organization WHERE slug = :s"),
            {"s": slug}).scalar_one())
    token = client.post("/keys", headers=OP, json={"org_slug": slug}).json()["token"]
    return tier, slug, org_id, {"Authorization": f"Bearer {token}"}


def test_the_spend_gate_ships_OFF_and_a_thin_balance_still_serves(
        client, db, calls):
    """🔴 The SHIPPED state, said out loud.

    `CUSTOMER_CONSOLE_SPEND_GATE` is unset in production, so neither the spend
    refusal nor the reserve runs, and a customer with less than one call's
    worth of credit is still served -- the balance simply goes negative.

    ⚠️ **This is the documented ordering, not an oversight.** H-42 is "price
    the rate card, THEN flip the spend gate", because arming the wall before
    the prices exist refuses customers for a price nobody set. This test
    exists so that the day somebody flips it, the change in behaviour is
    already written down here rather than discovered in support.
    """
    run = uuid.uuid4().hex[:8]
    tier, slug, org_id, key = _stage_one_priced_tier(client, db, run)
    client.post("/credits/grant", headers=OP, json={
        "org_slug": slug, "credits": "2", "reason": "purchase", "ref": f"b-{run}"})

    assert _ask(client, key, tier).status_code == 200
    assert _balance(db, org_id) == Decimal("2") - Decimal("1.2900")

    # A second call costs more than the 0.71 left, and is served anyway.
    assert _ask(client, key, tier).status_code == 200
    assert _balance(db, org_id) < 0, (
        "the balance did not go negative; the gate may now be ON by default, "
        "and if so this test and H-42 both need rereading")
    assert len(calls) == 2


def test_with_the_spend_gate_ARMED_a_drained_customer_is_REFUSED(
        client, db, calls, monkeypatch):
    """🔴 The wall the owner will arm, proven before they arm it.

    Credits are the product's only meter. With the gate on, a customer who
    cannot pay for a call is refused BEFORE the vendor is called -- otherwise
    we buy work we cannot bill, and the loss is silent.
    """
    monkeypatch.setenv("CUSTOMER_CONSOLE_SPEND_GATE", "1")
    run = uuid.uuid4().hex[:8]
    tier, slug, org_id, key = _stage_one_priced_tier(client, db, run)

    # ⚠️ Enough for the RESERVE, not merely for the bill. The hold sizes
    # itself on `MAX_OUTPUT_FOR_HOLD` because the real ceiling is the
    # provider's and we cannot see it from here, so it reserves generously
    # and releases the difference. 40 credits covers one call's reserve.
    client.post("/credits/grant", headers=OP, json={
        "org_slug": slug, "credits": "40", "reason": "purchase", "ref": f"b-{run}"})

    first = _ask(client, key, tier)
    assert first.status_code == 200, first.text
    assert len(calls) == 1

    # Now take the rest away, and the next call cannot reserve.
    client.post("/credits/grant", headers=OP, json={
        "org_slug": slug, "credits": "-38.71", "reason": "adjustment",
        "ref": f"drain-{run}"})

    refused = _ask(client, key, tier)
    assert refused.status_code == 402, refused.text
    # The refusal is MACHINE-READABLE, and that matters more than its prose:
    # the customer workbench renders a top-up prompt from this, so the shape
    # is a contract. It carries the balance, what the call needed, and whether
    # the organization is still on trial.
    body = refused.json()["detail"]
    assert body["reason"] == "insufficient_credits", body
    assert "balance_credits" in body["top_up"], body
    assert "credits_required" in body["top_up"], body

    # ⚠️ The vendor was NOT called again. A refusal that still calls the
    # vendor costs us money for work we then refuse to bill.
    assert len(calls) == 1, f"the vendor was called {len(calls)} times"

    # And the refusal is on the record, billing nothing.
    with db.begin() as c:
        walls = c.execute(
            text("SELECT refusal_reason, billed_credits FROM usage_event "
                 "WHERE organization_id = CAST(:o AS uuid) "
                 "  AND refusal_reason IS NOT NULL"),
            {"o": org_id}).all()
    assert len(walls) == 1, "the refusal left no row"
    assert walls[0].billed_credits == 0, "a refused call billed the customer"
