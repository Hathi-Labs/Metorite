"""Migration 013 wired: cost recorded, BYOK zero-rated, served step written.

Spec: `ai_metering_and_analytics.md` §6 A1 · §3.4 · §3.6 (slice 12).

🔴 **What this suite exists to prove.** `provider_cost_usd` sat unwritten for
twelve migrations while the margin queries COALESCE'd it; `_rate_completion`'s
own docstring admitted BYOK would be mischarged the day a card was priced; and
a failover's only record was a log line. Each claim below is asserted against
the REAL row the route wrote, in a real Postgres (R8).

⚠️ Fixture idiom follows `test_customer_console_router.py`: stubbed provider
call, per-test org and tier names, seed rows never touched.
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
    "id": "chatcmpl-pt",
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

#: What those counts cost at in=3 / out=15 / cached=0.3 USD per 1M:
#: 300×3 + 900×0.3 + 40×15 = 1770 micro-dollars.
EXPECTED_COST = Decimal("0.00177000")


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db():
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _box(db):
    with db.begin() as conn:
        ensure_deployment(conn)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def serve():
    """Stub the provider. Returns the recorder; tests may swap the behaviour."""
    seen: list[dict] = []

    async def _ok(**kwargs):
        seen.append(kwargs)
        return dict(RESPONSE)

    router_mod.set_provider_call(_ok)
    yield seen


@pytest.fixture
def vendor():
    """A vendor slug OWNED by this test, so no fixture here ever fights the
    router suite over the shared 'deepseek' platform slot — the one-live-key
    index means whoever inserts first wins, which made this suite's ordering
    visible in another file's assertion."""
    return f"ptv{uuid.uuid4().hex[:8]}"


@pytest.fixture
def org(client, db, vendor):
    """A provisioned org with a live cc_live_ key and a platform credential."""
    slug = f"pt-{uuid.uuid4().hex[:8]}"
    client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    token = client.post(
        "/keys", headers=OP, json={"org_slug": slug}).json()["token"]
    with db.begin() as c:
        c.execute(
            text("INSERT INTO provider_credential (provider, secret_enc, "
                 "label) VALUES (:v, :s, 'platform')"),
            {"v": vendor, "s": router_mod.encrypt_secret("sk-platform-secret")},
        )
        org_id = str(c.execute(
            text("SELECT id FROM organization WHERE slug = :s"),
            {"s": slug}).scalar_one())
    return slug, org_id, {"Authorization": f"Bearer {token}"}


def _stage(db, *, tier: str, models: list[str], profile: dict | None):
    """Declare, bind one chain at one timestamp, and optionally profile."""
    with db.begin() as c:
        eff = c.execute(text("SELECT now()")).scalar_one()
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
        if profile is not None:
            for m in models:
                c.execute(
                    text("INSERT INTO model_profile (model, "
                         "vendor_input_per_1m_usd, vendor_output_per_1m_usd, "
                         "vendor_cached_input_per_1m_usd) "
                         "VALUES (:m, :vin, :vout, :vc) "
                         "ON CONFLICT (model) DO UPDATE SET "
                         "vendor_input_per_1m_usd = EXCLUDED.vendor_input_per_1m_usd, "
                         "vendor_output_per_1m_usd = EXCLUDED.vendor_output_per_1m_usd, "
                         "vendor_cached_input_per_1m_usd = "
                         "EXCLUDED.vendor_cached_input_per_1m_usd"),
                    {"m": m, "vin": profile.get("vin"),
                     "vout": profile.get("vout"), "vc": profile.get("vc")})


def _row(db, org_id: str):
    with db.begin() as c:
        return c.execute(
            text("SELECT model, provider_cost_usd, served_rank, byok_served, "
                 "billed_credits FROM usage_event "
                 "WHERE organization_id = CAST(:o AS uuid) "
                 "ORDER BY created_at DESC LIMIT 1"),
            {"o": org_id}).first()


def _ask(client, headers, tier):
    return client.post("/v1/chat/completions", headers=headers, json={
        "model": tier, "messages": [{"role": "user", "content": "hi"}]})


# ── A1: the cost is finally recorded ────────────────────────────────────────

def test_the_cost_of_a_call_is_recorded_from_the_profile(
        client, db, org, vendor, serve):
    """🔴 The headline. Priced profile in, real dollars on the row out."""
    slug, org_id, key = org
    tier = f"tier-pt-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[f"{vendor}/pt-{uuid.uuid4().hex[:6]}"],
           profile={"vin": 3, "vout": 15, "vc": Decimal("0.3")})

    assert _ask(client, key, tier).status_code == 200
    row = _row(db, org_id)
    assert row is not None
    assert row.provider_cost_usd == EXPECTED_COST
    assert row.served_rank == 1
    assert row.byok_served is False


def test_no_profile_means_cost_NULL_never_zero(client, db, org, vendor, serve):
    # A zero would read as "this vendor is free" in every margin sum.
    slug, org_id, key = org
    tier = f"tier-pt-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[f"{vendor}/pt-{uuid.uuid4().hex[:6]}"],
           profile=None)

    assert _ask(client, key, tier).status_code == 200
    assert _row(db, org_id).provider_cost_usd is None


def test_cached_tokens_without_a_cached_price_leave_cost_NULL(
        client, db, org, vendor, serve):
    # The stub reports 900 cached tokens. Costing them at the input rate
    # would overstate the cost — the computation refuses instead.
    slug, org_id, key = org
    tier = f"tier-pt-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[f"{vendor}/pt-{uuid.uuid4().hex[:6]}"],
           profile={"vin": 3, "vout": 15, "vc": None})

    assert _ask(client, key, tier).status_code == 200
    assert _row(db, org_id).provider_cost_usd is None


# ── §3.6 slice 12: the served step is durable evidence ──────────────────────

def test_a_failover_records_the_rank_that_ANSWERED(
        client, db, org, vendor, serve):
    """🔴 Rank 2 on the row is the evidence a chain earned its keep."""
    slug, org_id, key = org
    tier = f"tier-pt-{uuid.uuid4().hex[:6]}"
    primary = f"{vendor}/pt-a-{uuid.uuid4().hex[:6]}"
    backup = f"{vendor}/pt-b-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[primary, backup],
           profile={"vin": 3, "vout": 15, "vc": Decimal("0.3")})

    calls = {"n": 0}

    class _Overloaded(Exception):
        status_code = 503

    async def _flaky(**kwargs):
        calls["n"] += 1
        if kwargs["model"] == primary:
            raise _Overloaded("provider down")
        return dict(RESPONSE)

    router_mod.set_provider_call(_flaky)

    assert _ask(client, key, tier).status_code == 200
    row = _row(db, org_id)
    assert row.model == backup
    assert row.served_rank == 2
    # And the cost is priced at the model that ANSWERED, not the primary.
    assert row.provider_cost_usd == EXPECTED_COST


# ── §3.4: BYOK is metered and NOT charged ───────────────────────────────────

def test_byok_is_metered_and_billed_zero(client, db, org, vendor, serve):
    """🔴 The gap `_rate_completion` documented, closed before any real price.

    The org brings its own DeepSeek key AND the card carries a real price.
    The call must: run on their credential, write the usage row, bill zero,
    move NOTHING in the ledger, and record our cost as zero — we paid the
    vendor nothing.
    """
    slug, org_id, key = org
    tier = f"tier-pt-{uuid.uuid4().hex[:6]}"
    model = f"{vendor}/pt-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[model],
           profile={"vin": 3, "vout": 15, "vc": Decimal("0.3")})
    with db.begin() as c:
        c.execute(
            text("INSERT INTO provider_credential (provider, "
                 "organization_id, secret_enc, label) VALUES (:v, "
                 "CAST(:o AS uuid), :s, 'their own')"),
            {"v": vendor, "o": org_id,
             "s": router_mod.encrypt_secret("sk-their-own-key")})
        c.execute(
            text("INSERT INTO model_rate_card (model, task, "
                 "input_credits_per_1k, output_credits_per_1k, "
                 "cached_input_credits_per_1k, pricing_mode, effective_from) "
                 "VALUES (:m, 'chat', 2, 6, 0.5, 'priced', now())"),
            {"m": model})
        before = c.execute(
            text("SELECT COALESCE(SUM(delta), 0) FROM credit_ledger "
                 "WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org_id}).scalar_one()

    assert _ask(client, key, tier).status_code == 200

    row = _row(db, org_id)
    assert row.byok_served is True
    assert Decimal(row.billed_credits) == 0
    assert row.provider_cost_usd == Decimal("0.00000000")
    with db.begin() as c:
        after = c.execute(
            text("SELECT COALESCE(SUM(delta), 0) FROM credit_ledger "
                 "WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org_id}).scalar_one()
        # Fixture hygiene: this suite's card row, not the seed's.
        c.execute(text("DELETE FROM model_rate_card WHERE model = :m"),
                  {"m": model})
    assert after == before, "a BYOK call moved the ledger"


def test_a_platform_call_on_a_priced_card_still_bills(
        client, db, org, vendor, serve):
    # The control: same card shape, platform credential → the draw happens.
    slug, org_id, key = org
    tier = f"tier-pt-{uuid.uuid4().hex[:6]}"
    model = f"{vendor}/pt-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[model], profile=None)
    with db.begin() as c:
        c.execute(
            text("INSERT INTO model_rate_card (model, task, "
                 "input_credits_per_1k, output_credits_per_1k, "
                 "cached_input_credits_per_1k, pricing_mode, effective_from) "
                 "VALUES (:m, 'chat', 2, 6, 0.5, 'priced', now())"),
            {"m": model})

    assert _ask(client, key, tier).status_code == 200
    row = _row(db, org_id)
    with db.begin() as c:
        c.execute(text("DELETE FROM model_rate_card WHERE model = :m"),
                  {"m": model})
    assert row.byok_served is False
    # 300 uncached × 2/1k + 900 cached × 0.5/1k + 40 out × 6/1k
    assert Decimal(row.billed_credits) == Decimal("1.29")


# ── The writer's contract for callers that do not know the chain ────────────

def test_a_caller_that_names_no_rank_leaves_it_NULL(db, org):
    # The internal /usage/record path reports no chain; its rows must not
    # claim a rank-1 service nobody observed.
    from customer_console import store
    slug, org_id, _ = org
    with db.begin() as c:
        store.record_usage(
            conn=c, org_id=org_id, request_id=f"pt-{uuid.uuid4().hex}",
            billed_credits=Decimal(0), model="m", tier="t")
        row = c.execute(
            text("SELECT served_rank, byok_served FROM usage_event "
                 "WHERE organization_id = CAST(:o AS uuid) "
                 "ORDER BY created_at DESC LIMIT 1"),
            {"o": org_id}).first()
    assert row.served_rank is None
    assert row.byok_served is False


# ── Slice 12's read half: the catalog carries the failovers ─────────────────

def test_the_catalog_reports_the_failover_that_happened(
        client, db, org, vendor, serve):
    """🔴 The one durable proof a chain earns its keep, on the tiers page."""
    slug, org_id, key = org
    tier = f"tier-pt-{uuid.uuid4().hex[:6]}"
    primary = f"{vendor}/pt-a-{uuid.uuid4().hex[:6]}"
    backup = f"{vendor}/pt-b-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[primary, backup], profile=None)

    class _Down(Exception):
        status_code = 503

    async def _flaky(**kwargs):
        if kwargs["model"] == primary:
            raise _Down("down")
        return dict(RESPONSE)

    router_mod.set_provider_call(_flaky)
    assert _ask(client, key, tier).status_code == 200

    catalog = client.get("/catalog/models", headers=OP).json()
    mine = [f for f in catalog["failovers"] if f["tier"] == tier]
    assert mine, "the failover that just happened is not in the catalog"
    assert mine[0]["model"] == backup
    assert mine[0]["rank"] == 2
    assert mine[0]["requests"] >= 1


def test_a_primary_answer_is_NOT_reported_as_a_failover(
        client, db, org, vendor, serve):
    # Rank 1 is the system working. Reporting it would bury the real rows.
    slug, org_id, key = org
    tier = f"tier-pt-{uuid.uuid4().hex[:6]}"
    _stage(db, tier=tier, models=[f"{vendor}/pt-{uuid.uuid4().hex[:6]}"],
           profile=None)
    assert _ask(client, key, tier).status_code == 200
    catalog = client.get("/catalog/models", headers=OP).json()
    assert [f for f in catalog["failovers"] if f["tier"] == tier] == []


# ── H-76's second half: silence judged over EVERY organization ──────────────

def test_a_quiet_funded_customer_below_the_cap_is_still_reported(
        client, db, org, monkeypatch):
    """🔴 The cap hid the exact row A3 exists to find.

    The page sorts by spend and keeps `limit` rows, so a funded organization
    with NO usage — "somebody paid and never arrived" — fell off first. The
    silent list is now judged over every organization, uncapped. Proved the
    honest way: more organizations than the page holds, and the quiet one
    must appear in `silentSlugs` while absent from `rows`.

    📌 **First run of this test found a SECOND truncation**: 
    `credit_balance_by_org` carried `LIMIT 100` with no ORDER BY, so above a
    hundred organizations an ARBITRARY hundred had balances and everyone else
    read 0 — nondeterministically. The silent judgement below exercises that
    read for an org the cap would have dropped, so this test now fences both.
    """
    slug, org_id, _ = org
    # Fund the quiet org so A3's both-halves rule applies.
    r = client.post("/credits/grant", headers=OP,
                    json={"org_slug": slug, "credits": "500",
                          "reason": "grant"})
    assert r.status_code == 200, r.text

    # Crowd it off the page: the endpoint's cap is SPEND_PAGE_SIZE (100).
    # 105 organizations, each with one billed call TODAY, all louder than
    # the quiet org's zero.
    from customer_console import store as store_mod
    with db.begin() as c:
        for i in range(105):
            other = str(c.execute(
                text("INSERT INTO organization (slug, name) "
                     "VALUES (:s, 'N') RETURNING id"),
                {"s": f"loud-{uuid.uuid4().hex[:10]}"},
            ).scalar_one())
            store_mod.record_usage(
                conn=c, org_id=other,
                request_id=f"pt-loud-{uuid.uuid4().hex}",
                billed_credits=Decimal("5"), model="m", tier="t")

    view = client.get("/admin/usage/orgs", headers=OP).json()
    shown = {row["slug"] for row in view["rows"]}
    assert slug not in shown, (
        "the fixture failed to push the quiet org off the page — the test "
        "would pass without proving anything"
    )
    assert slug in view["silentSlugs"]
