"""D67 wired: the customer pays for the TIER they picked (migration 015).

Owner decision 2026-08-30. Spec: `customer_console.md` §6A.12.

🔴 **What this suite exists to prove.** Until D67 the customer's price was
whatever the SERVING model cost: a failover changed what they paid mid-day,
and two tiers sharing one model could not differ in price — no premium tier
without a premium model. Each claim below is asserted against the REAL rows
the route wrote, in a real Postgres (R8):

* two tiers on ONE model bill differently (the impossible-before case);
* a failover keeps the tier's price while the vendor cost follows the model;
* an unpriced tier bills zero loudly and keeps the usage row;
* a future-dated re-price does not bill until its date arrives;
* the slate ships whole — every capability tier registered, video and music
  tasks present — and ships UNPRICED.

⚠️ Fixture idiom copied from ``test_customer_console_pricing_truth.py``:
stubbed provider, per-test org/tier/vendor names, seed rows never touched.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from customer_console import router as router_mod  # noqa: E402
from tests.unit._customer_console_ladder import (  # noqa: E402
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

#: The stub's counts: 1200 prompt of which 900 cached, 40 completion.
RESPONSE = {
    "id": "chatcmpl-tp",
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

#: What those counts bill at in=2 / out=6 / cached=0.5 credits per 1k:
#: 300×2/1k + 900×0.5/1k + 40×6/1k = 1.29 credits.
RATE_A = {"i": 2, "o": 6, "c": Decimal("0.5")}
BILL_A = Decimal("1.2900")
#: And at exactly double: the premium tier on the SAME model.
RATE_B = {"i": 4, "o": 12, "c": Decimal("1.0")}
BILL_B = Decimal("2.5800")

#: The slate migration 015 registers (beside anything tests add).
SLATE = {
    "tier-fast", "tier-balanced", "tier-powerful", "tier-code",
    "tier-vision", "tier-image", "tier-stt", "tier-tts", "tier-embed",
    "tier-video", "tier-music",
}


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
def serve():
    async def _ok(**kwargs):
        return dict(RESPONSE)

    router_mod.set_provider_call(_ok)
    yield


@pytest.fixture
def vendor():
    """A vendor slug OWNED by this test — never the shared seed vendors."""
    return f"tp{uuid.uuid4().hex[:8]}"


@pytest.fixture
def org(client, db, vendor):
    slug = f"tp-{uuid.uuid4().hex[:8]}"
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


def _stage(db, *, tier: str, models: list[str]):
    """Register the tier, declare the models, bind one chain."""
    with db.begin() as c:
        eff = c.execute(text("SELECT now()")).scalar_one()
        c.execute(
            text("INSERT INTO tier_catalog (slug, label) VALUES (:t, :t) "
                 "ON CONFLICT DO NOTHING"), {"t": tier})
        for rank, m in enumerate(models, start=1):
            c.execute(
                text("INSERT INTO model_capability (model, task, invocation) "
                     "VALUES (:m, 'chat', 'acompletion') "
                     "ON CONFLICT DO NOTHING"),
                {"m": m})
            c.execute(
                text("INSERT INTO tier_binding (tier, task, model, rank, "
                     "effective_from) VALUES (:t, 'chat', :m, :r, :eff)"),
                {"t": tier, "m": m, "r": rank, "eff": eff})


def _price(db, tier: str, rate: dict, *, when: str = "now()") -> None:
    with db.begin() as c:
        c.execute(
            text("INSERT INTO tier_rate_card (tier, task, "
                 "input_credits_per_1k, output_credits_per_1k, "
                 "cached_input_credits_per_1k, pricing_mode, effective_from) "
                 f"VALUES (:t, 'chat', :i, :o, :c, 'priced', {when})"),
            {"t": tier, **rate})


def _row(db, org_id: str):
    with db.begin() as c:
        return c.execute(
            text("SELECT model, tier, billed_credits, served_rank "
                 "FROM usage_event "
                 "WHERE organization_id = CAST(:o AS uuid) "
                 "ORDER BY created_at DESC LIMIT 1"),
            {"o": org_id}).first()


def _ask(client, headers, tier):
    return client.post("/v1/chat/completions", headers=headers, json={
        "model": tier, "messages": [{"role": "user", "content": "hi"}]})


# ── The slate (the owner's 2026-08-30 directive) ────────────────────────────

def test_the_whole_slate_is_registered(db):
    with db.begin() as c:
        slugs = {r[0] for r in c.execute(
            text("SELECT slug FROM tier_catalog"))}
    assert SLATE <= slugs


def test_video_and_music_are_tasks_priced_in_seconds(db):
    with db.begin() as c:
        units = {r[0]: r[1] for r in c.execute(text(
            "SELECT slug, natural_unit FROM task_catalog "
            "WHERE slug IN ('video', 'music')"))}
    assert units == {"video": "seconds", "music": "seconds"}


def test_the_slate_ships_unpriced(db):
    """Migration 015 seeds NO rates — pricing is the owner's act (H-42).

    Scoped to the slate's own tiers so another suite's test rates cannot
    fail it: the claim is 'the MIGRATION prices nothing', not 'the table
    is empty'."""
    with db.begin() as c:
        n = c.execute(
            text("SELECT COUNT(*) FROM tier_rate_card "
                 "WHERE tier = ANY(:slate)"),
            {"slate": list(SLATE)}).scalar_one()
    assert n == 0


# ── D67's headline: the price follows the TIER ──────────────────────────────

def test_two_tiers_on_ONE_model_bill_differently(client, db, org, vendor,
                                                 serve):
    """🔴 The impossible-before case, and the reason the key moved."""
    slug, org_id, key = org
    model = f"{vendor}/tp-{uuid.uuid4().hex[:6]}"
    lo = f"tier-tp-{uuid.uuid4().hex[:6]}"
    hi = f"tier-tp-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=lo, models=[model])
    _stage(db, tier=hi, models=[model])
    _price(db, lo, RATE_A)
    _price(db, hi, RATE_B)

    assert _ask(client, key, lo).status_code == 200
    assert Decimal(_row(db, org_id).billed_credits) == BILL_A

    assert _ask(client, key, hi).status_code == 200
    row = _row(db, org_id)
    assert row.model == model  # SAME model...
    assert Decimal(row.billed_credits) == BILL_B  # ...premium price


def test_a_failover_keeps_the_tier_price(client, db, org, vendor):
    """The customer picked a tier. The backup answering is OUR problem —
    their price does not move."""
    slug, org_id, key = org
    tier = f"tier-tp-{uuid.uuid4().hex[:6]}"
    primary = f"{vendor}/tp-a-{uuid.uuid4().hex[:6]}"
    backup = f"{vendor}/tp-b-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[primary, backup])
    _price(db, tier, RATE_A)

    class _Overloaded(Exception):
        status_code = 503

    async def _flaky(**kwargs):
        if kwargs["model"] == primary:
            raise _Overloaded("provider down")
        return dict(RESPONSE)

    router_mod.set_provider_call(_flaky)

    assert _ask(client, key, tier).status_code == 200
    row = _row(db, org_id)
    assert row.model == backup
    assert row.served_rank == 2
    # The price the customer pays is the TIER's, unmoved by the failover.
    assert Decimal(row.billed_credits) == BILL_A


def test_an_unpriced_tier_bills_zero_and_keeps_the_row(client, db, org,
                                                       vendor, serve):
    slug, org_id, key = org
    tier = f"tier-tp-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[f"{vendor}/tp-{uuid.uuid4().hex[:6]}"])
    # No _price() on purpose.

    assert _ask(client, key, tier).status_code == 200
    row = _row(db, org_id)
    assert row is not None  # the evidence survives
    assert Decimal(row.billed_credits) == 0


def test_a_future_dated_reprice_does_not_bill_early(client, db, org, vendor,
                                                    serve):
    """INSERT-only re-pricing: 'new rates from the 1st' is a future row,
    and until its date arrives the OLD price bills."""
    slug, org_id, key = org
    tier = f"tier-tp-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[f"{vendor}/tp-{uuid.uuid4().hex[:6]}"])
    _price(db, tier, RATE_A)
    _price(db, tier, RATE_B, when="now() + INTERVAL '7 days'")

    assert _ask(client, key, tier).status_code == 200
    assert Decimal(_row(db, org_id).billed_credits) == BILL_A


def test_a_model_card_row_no_longer_bills_anybody(client, db, org, vendor,
                                                  serve):
    """The old key is dead as a billing input: a priced MODEL card under an
    unpriced TIER bills zero. Guards against a resurrection of the old
    join."""
    slug, org_id, key = org
    tier = f"tier-tp-{uuid.uuid4().hex[:6]}"
    model = f"{vendor}/tp-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[model])
    with db.begin() as c:
        c.execute(
            text("INSERT INTO model_rate_card (model, task, "
                 "input_credits_per_1k, output_credits_per_1k, "
                 "pricing_mode, effective_from) "
                 "VALUES (:m, 'chat', 99, 99, 'priced', now())"),
            {"m": model})

    assert _ask(client, key, tier).status_code == 200
    row = _row(db, org_id)
    with db.begin() as c:
        c.execute(text("DELETE FROM model_rate_card WHERE model = :m"),
                  {"m": model})
    assert Decimal(row.billed_credits) == 0


# ---- D68: a tier serves ONE kind of job -----------------------------------

def test_the_slate_is_categorised(db):
    with db.begin() as c:
        tasks = {r[0]: r[1] for r in c.execute(
            text("SELECT slug, task FROM tier_catalog "
                 "WHERE slug = ANY(:slate)"), {"slate": list(SLATE)})}
    assert tasks["tier-fast"] == "chat"
    assert tasks["tier-code"] == "chat"
    assert tasks["tier-stt"] == "transcribe"
    assert tasks["tier-video"] == "video"
    assert all(v is not None for v in tasks.values())


def test_a_rate_for_the_wrong_kind_of_job_is_refused(client):
    r = client.post("/catalog/tier-rates", headers=OP, json={
        "tier": "tier-stt", "task": "chat", "unit": "tokens",
        "pricing_mode": "priced", "input_per_1k": "2"})
    assert r.status_code == 400
    assert "serves 'transcribe'" in r.json()["detail"]


def test_a_rate_for_the_tier_own_kind_still_lands(client, db):
    r = client.post("/catalog/tier-rates", headers=OP, json={
        "tier": "tier-music", "task": "music", "unit": "seconds",
        "pricing_mode": "priced", "credits_per_unit": "0.9"})
    assert r.status_code == 200, r.text
    with db.begin() as c:
        c.execute(text(
            "DELETE FROM tier_rate_card WHERE tier = 'tier-music'"))


def test_an_uncategorised_tier_keeps_the_old_freedom(client, db, vendor):
    """A test-registered tier has task NULL - the mismatch check must not
    fire, or every existing suite's staged tier breaks."""
    tier = f"tier-tp-{uuid.uuid4().hex[:6]}"
    model = f"{vendor}/tp-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[model])  # registers with task NULL
    r = client.post("/catalog/tier-rates", headers=OP, json={
        "tier": tier, "task": "chat", "unit": "tokens",
        "pricing_mode": "priced", "input_per_1k": "1"})
    assert r.status_code == 200, r.text
    with db.begin() as c:
        c.execute(text("DELETE FROM tier_rate_card WHERE tier = :t"),
                  {"t": tier})


def test_the_same_effective_from_twice_answers_409_not_500(client, db):
    """A retried "price from the 1st" POST violated the PK and 500ed."""
    eff = "2030-01-02T00:00:00Z"
    body = {"tier": "tier-music", "task": "music", "unit": "seconds",
            "pricing_mode": "priced", "credits_per_unit": "0.9",
            "effective_from": eff}
    assert client.post("/catalog/tier-rates", headers=OP,
                       json=body).status_code == 200
    r2 = client.post("/catalog/tier-rates", headers=OP, json=body)
    assert r2.status_code == 409
    assert "effective_from" in r2.json()["detail"]
    # Clean the probe out of the REUSED dev database: the slate test reads
    # whole-table state, and a leftover priced row from a past run breaks it.
    with db.begin() as c:
        c.execute(text("DELETE FROM tier_rate_card WHERE effective_from = :e"),
                  {"e": eff})


# ---- D-AI-3: one column decides which tiers a customer may see ------------
#
# Migration 021 and `GET /my/tiers`. Spec:
# `ai_metering_and_analytics.md` §3.3 and §8.4 clauses 1, 3, 4, 5.
#
# 🔴 **The rule these tests hold.** TRUE means a person picks this tier on
# purpose. FALSE means the Router or the app picks it, so a picker entry
# would offer a choice nobody can act on.

#: The six §3.3 names. The migration turns each one FALSE.
HIDDEN = {
    "tier-vision", "tier-stt", "tier-tts", "tier-embed",
    "tier-video", "tier-music",
}

#: The five a customer picks. They keep the column's TRUE default.
PICKABLE = {
    "tier-fast", "tier-balanced", "tier-powerful", "tier-code", "tier-image",
}


def _visible(db) -> dict[str, bool]:
    """`customer_visible` for every slate row, read from the database."""
    with db.begin() as c:
        return {r[0]: r[1] for r in c.execute(
            text("SELECT slug, customer_visible FROM tier_catalog "
                 "WHERE slug = ANY(:slate)"), {"slate": list(SLATE)})}


def test_the_six_specialised_tiers_ship_hidden(db):
    """021's seed, read back from the ladder that applied it."""
    seen = _visible(db)
    assert {s: seen[s] for s in HIDDEN} == dict.fromkeys(HIDDEN, False)


def test_the_five_pickable_tiers_ship_visible(db):
    """The other half of the same claim. A seed that hid everything would
    pass the test above on its own."""
    seen = _visible(db)
    assert {s: seen[s] for s in PICKABLE} == dict.fromkeys(PICKABLE, True)


def test_a_tier_added_later_shows_up(db):
    """The default is TRUE (§3.1). A new tier is visible until an operator
    hides it, and a NOT NULL column with no default would refuse this
    INSERT instead."""
    slug = f"tier-tp-{uuid.uuid4().hex[:6]}"
    with db.begin() as c:
        c.execute(text("INSERT INTO tier_catalog (slug, label) "
                       "VALUES (:t, 'probe')"), {"t": slug})
        got = c.execute(
            text("SELECT customer_visible FROM tier_catalog WHERE slug = :t"),
            {"t": slug}).scalar_one()
        c.execute(text("DELETE FROM tier_catalog WHERE slug = :t"),
                  {"t": slug})
    assert got is True


def test_my_tiers_serves_the_words_and_never_the_slug_alone(client, org):
    """§8.4 clause 3. Every row carries `label` and `blurb`."""
    _slug, _org_id, key = org
    r = client.get("/my/tiers", headers=key)
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows, "the slate is not empty, so the read is not either"
    for row in rows:
        assert set(row) == {"slug", "label", "blurb"}
        assert row["label"].strip(), f"{row['slug']} carries no label"
    labels = {row["slug"]: row["label"] for row in rows}
    assert labels["tier-balanced"] == "Balanced"


def test_my_tiers_hides_every_specialised_tier(client, org):
    """🔴 The headline. A `customer_visible` FALSE row never reaches a
    customer, and `tier-stt` is the named case."""
    _slug, _org_id, key = org
    served = {row["slug"] for row in
              client.get("/my/tiers", headers=key).json()["rows"]}
    assert "tier-stt" not in served
    assert not (served & HIDDEN)
    assert served >= PICKABLE


def test_my_tiers_agrees_with_the_column(client, db, org):
    """The route filters in SQL, so the answer is the column. A test tier
    that another suite registered is included on purpose: the claim is
    'every visible row and nothing else', not 'the slate'."""
    _slug, _org_id, key = org
    served = {row["slug"] for row in
              client.get("/my/tiers", headers=key).json()["rows"]}
    with db.begin() as c:
        expected = {r[0] for r in c.execute(text(
            "SELECT slug FROM tier_catalog WHERE customer_visible"))}
    assert served == expected


def test_my_tiers_is_ordered_by_sort_order(client, db, org):
    """The operator sets the order, and the route keeps it."""
    _slug, _org_id, key = org
    served = [row["slug"] for row in
              client.get("/my/tiers", headers=key).json()["rows"]]
    with db.begin() as c:
        order = {r[0]: r[1] for r in c.execute(text(
            "SELECT slug, sort_order FROM tier_catalog"))}
    ranks = [order[s] for s in served]
    assert ranks == sorted(ranks)


def test_my_tiers_names_no_model_and_no_price(client, db, org, vendor):
    """§8.4 clause 5, and D66. A bound model must not leak through the
    tier that serves it."""
    _slug, _org_id, key = org
    model = f"{vendor}/tp-{uuid.uuid4().hex[:6]}"
    with db.begin() as c:
        c.execute(
            text("INSERT INTO model_capability (model, task, invocation) "
                 "VALUES (:m, 'chat', 'acompletion') ON CONFLICT DO NOTHING"),
            {"m": model})
        c.execute(
            text("INSERT INTO tier_binding (tier, task, model, rank, "
                 "effective_from) VALUES ('tier-fast', 'chat', :m, 9, now())"),
            {"m": model})
    try:
        body = client.get("/my/tiers", headers=key).text
    finally:
        with db.begin() as c:
            c.execute(text("DELETE FROM tier_binding WHERE model = :m"),
                      {"m": model})
    assert model not in body
    assert vendor not in body
    # ⚠️ The blurb is customer copy, and it may say the word "price". The
    # claim is about FACTS, so this reads the field names the shape carries.
    for field in ("model", "provider", "rate", "credits", "task"):
        assert f'"{field}"' not in body, f"{field!r} left the tier read"


def test_my_tiers_refuses_a_caller_with_no_key(client):
    """The read sits behind the customer key, like every `/my/*` route."""
    assert client.get("/my/tiers").status_code == 401


def test_my_tiers_refuses_the_operator_token(client):
    """🔴 The operator token is not a customer key. A route that took both
    would be a second scheme on a `/my/*` door."""
    assert client.get("/my/tiers", headers=OP).status_code == 401


def test_my_tiers_reads_the_same_slate_for_two_organizations(client, db, org):
    """The slate is the product, so it is not per-tenant. This asserts the
    consequence a reviewer must be able to see: the answer holds no fact
    about the caller, so one organization reads nothing of another's."""
    _slug_a, org_id_a, key_a = org
    slug_b = f"tp-{uuid.uuid4().hex[:8]}"
    client.post("/orgs/provision", headers=OP, json={
        "slug": slug_b, "name": "B", "owner_email": f"o@{slug_b}.com",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    token_b = client.post(
        "/keys", headers=OP, json={"org_slug": slug_b}).json()["token"]
    key_b = {"Authorization": f"Bearer {token_b}"}

    body_a = client.get("/my/tiers", headers=key_a).json()
    body_b = client.get("/my/tiers", headers=key_b).json()
    assert body_a == body_b
    text_a = client.get("/my/tiers", headers=key_a).text
    assert org_id_a not in text_a
    assert slug_b not in text_a
