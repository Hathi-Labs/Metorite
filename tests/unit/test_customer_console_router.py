"""CP-4 — the Router forwards, resolves tiers, and counts. It does not price.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-4 · D32.1 / D32.7.

Acceptance: *"Forwards to the same providers using today's machinery, writes
``usage_event``, no pricing and no gate. Done when: a completion through the
Router is byte-identical for the client; one ``usage_event`` row exists per
completion; a retried ``request_id`` writes one row, not two."*

The provider call is stubbed through :func:`customer_console.router.set_provider_call`.
That seam exists because the alternative is a test that spends money at DeepSeek
on every run, which means in practice nobody runs it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from customer_console import router as router_mod
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (  # noqa: E402
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

#: 📌 IMPORTED, never re-declared. `Wrapper` is the litellm-shaped source the
#: close fences need at both ends of the stream path — the walk's loser and the
#: route's winner — and two copies of it would drift.
from tests.unit.test_router_failover import Wrapper

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
#: The Router's own token — the only credential that may write the meter.
INT = {"Authorization": "Bearer internal"}
ENC_KEY = "test-encryption-key-not-a-real-one"


#: What the stub provider returns. Deliberately carries fields the Router has no
#: opinion about, so "byte-identical" is a real assertion rather than a
#: comparison of two things the Router built.
PROVIDER_RESPONSE = {
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "created": 1_755_000_000,
    "model": "deepseek/deepseek-v4-pro",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "Sixteen pumps are overdue."},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 40,
              "prompt_tokens_details": {"cached_tokens": 900}},
    "system_fingerprint": "fp_deadbeef",
}


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def calls():
    """Records what the Router asked the provider for."""
    seen: list[dict] = []

    async def _stub(**kwargs):
        seen.append(kwargs)
        return dict(PROVIDER_RESPONSE)

    router_mod.set_provider_call(_stub)
    yield seen


@pytest.fixture
def client(monkeypatch, calls):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture(autouse=True)
def _box():
    """The deployment every org here is provisioned onto (MT-1j slice 4).

    Autouse and per-test: this suite's subject is rating and metering, not
    placement, and a sibling fence deliberately empties the table.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        ensure_deployment(conn)
    eng.dispose()


@pytest.fixture
def org_key(client, db, monkeypatch):
    """A provisioned org, a live key, and a platform DeepSeek credential."""
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    slug = f"router-{uuid.uuid4().hex[:8]}"
    client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    token = client.post("/keys", headers=OP, json={"org_slug": slug}).json()["token"]

    with db.begin() as c:
        c.execute(
            text("INSERT INTO provider_credential (provider, secret_enc, label) "
                 "VALUES ('deepseek', :s, 'platform') "
                 "ON CONFLICT DO NOTHING"),
            {"s": router_mod.encrypt_secret("sk-provider-secret")},
        )
    return slug, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org_id(db, org_key):
    slug, _ = org_key
    with db.begin() as c:
        return str(c.execute(
            text("SELECT id FROM organization WHERE slug = :s"), {"s": slug}
        ).scalar_one())


@pytest.fixture
def priced_card(db):
    """A rate card with real numbers, for the life of one test.

    **The seeded card stays at zero.** Setting a real price for a real model is
    the owner's commercial act (§8, D19.2) — a rate card set by an agent on
    estimates is a rate card you change on customers who have already seen it.
    So rating is proven against a fixture price and removed afterwards, and the
    ``002_seed_catalog.sql`` rows are never touched.

    2 / 6 / 0.5 credits per 1k (input / output / cached input) — the same shape
    ``test_customer_console_credits.py`` rates against, so the arithmetic in one
    place can be checked against the other.
    """
    # D67: the price is keyed on the TIER the customer picked. The rating
    # tests below bill through `tier-balanced`, so that is what gets a card.
    tier = "tier-balanced"
    with db.begin() as c:
        c.execute(text(
            "INSERT INTO tier_rate_card (tier, task, input_credits_per_1k, "
            " output_credits_per_1k, cached_input_credits_per_1k, "
            " pricing_mode, effective_from) "
            "VALUES (:t, 'chat', 2, 6, 0.5, 'priced', now()) "
            "ON CONFLICT DO NOTHING"), {"t": tier})
    yield tier
    with db.begin() as c:
        # Only the fixture's row: migration 015 seeds NO tier rates, and
        # the ships-unpriced idea holds for the slate.
        c.execute(text(
            "DELETE FROM tier_rate_card WHERE tier = :t "
            "  AND (input_credits_per_1k <> 0 OR output_credits_per_1k <> 0)"),
            {"t": tier})


@pytest.fixture
def gate_on(monkeypatch):
    """Turn the CP-6 refusals on. They ship OFF (CLAUDE.md §4, ship dark)."""
    monkeypatch.setenv("CUSTOMER_CONSOLE_SPEND_GATE", "1")


#: What one stubbed completion costs against `priced_card`:
#: 1200 prompt of which 900 cached -> 300 fresh @2/1k = 0.60
#:                                  + 900 cached @0.5/1k = 0.45
#:                                  +  40 output  @6/1k = 0.24
CALL_COST = Decimal("1.29")


def _grant(client, slug: str, credits: str):
    return client.post("/credits/grant", headers=OP,
                       json={"org_slug": slug, "credits": credits})


def _charge(client, organization_id: str, credits: str, run_id: str | None = None):
    """Draw the balance down through the REAL metering path, not by editing it."""
    return client.post("/usage/record", headers=INT, json={
        "organization_id": organization_id,
        "request_id": f"seed-{uuid.uuid4().hex}",
        "billed_credits": credits, "run_id": run_id, "model": "m"})


def _complete(client, key, **extra):
    return client.post("/v1/chat/completions", headers={**key, **extra.pop(
        "headers", {})}, json={
            "model": "tier-balanced",
            "messages": [{"role": "user", "content": "hi"}], **extra})


# ── Tier resolution ─────────────────────────────────────────────────────────

class TestTierResolution:
    def test_a_tier_resolves_to_the_bound_model(self, db):
        with db.begin() as c:
            assert router_mod.resolve_tier(c, "tier-balanced").model \
                == "deepseek/deepseek-v4-pro"

    def test_the_newest_binding_in_effect_wins(self, db):
        with db.connect() as c:
            tx = c.begin()
            c.execute(text(
                "INSERT INTO tier_binding (tier, model, effective_from) "
                "VALUES ('tier-balanced', 'newprovider/next', now())"))
            assert router_mod.resolve_tier(c, "tier-balanced").model \
                == "newprovider/next"
            tx.rollback()

    def test_a_future_binding_is_staged_not_live(self, db):
        # Re-pointing a tier is "insert a row with a later date", never "edit
        # the live one", so a swap can be staged and history stays readable.
        with db.connect() as c:
            tx = c.begin()
            c.execute(text(
                "INSERT INTO tier_binding (tier, model, effective_from) "
                "VALUES ('tier-balanced', 'future/model', now() + interval '7 days')"))
            assert router_mod.resolve_tier(c, "tier-balanced").model \
                == "deepseek/deepseek-v4-pro"
            tx.rollback()

    def test_an_unknown_tier_raises_rather_than_defaulting(self, db):
        with db.begin() as c:
            with pytest.raises(router_mod.TierUnknown):
                router_mod.resolve_tier(c, "tier-imaginary")


# ── Pass-through ────────────────────────────────────────────────────────────

class TestPassThrough:
    def test_the_response_reaches_the_client_unchanged(self, client, org_key):
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced",
            "messages": [{"role": "user", "content": "which pumps are overdue?"}]})

        assert r.status_code == 200, r.text
        # Byte-identical: every field the provider sent, including ones the
        # Router has no opinion about.
        assert r.json() == PROVIDER_RESPONSE

    def test_the_tier_is_translated_to_a_real_model_for_the_provider(
            self, client, org_key, calls):
        _, key = org_key
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-fast", "messages": [{"role": "user", "content": "hi"}]})

        assert calls[-1]["model"] == "deepseek/deepseek-chat"

    def test_the_customer_never_sees_the_provider_secret(self, client, org_key, calls):
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}]})

        # It reached the provider...
        assert calls[-1]["api_key"] == "sk-provider-secret"
        # ...and not the customer.
        assert "sk-provider-secret" not in r.text

    def test_known_parameters_are_forwarded(self, client, org_key, calls):
        _, key = org_key
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2, "top_p": 0.9})

        assert calls[-1]["temperature"] == 0.2
        assert calls[-1]["top_p"] == 0.9

    def test_an_UNKNOWN_parameter_is_rejected_not_forwarded(self, client, org_key):
        """The reversal, and the reason for it.

        This test previously asserted the opposite — "a provider parameter the
        Router does not know about is not its business to reject" — which sounds
        like reasonable pass-through humility and was in fact a live
        credential-exfiltration hole: `api_base` is such a parameter, and
        forwarding it sent our platform provider key to a host the caller chose.

        A pass-through can be permissive about *content* and must not be
        permissive about *routing and cost*. Since the two arrive in the same
        JSON object, the only safe posture is an allowlist.
        """
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}],
            "some_future_param": True})

        assert r.status_code == 422

    def test_naming_a_raw_model_is_a_400_not_a_silent_coercion(self, client, org_key):
        # D32.7: silent coercion hides a misconfigured agent behind a bill.
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "deepseek/deepseek-chat",
            "messages": [{"role": "user", "content": "hi"}]})

        assert r.status_code == 400
        assert "tier" in r.json()["detail"]

    def test_an_anonymous_caller_cannot_spend_our_provider_account(self, client):
        r = client.post("/v1/chat/completions", json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 401


# ── Metering ────────────────────────────────────────────────────────────────

class TestMetering:
    def test_one_completion_writes_one_usage_row_attributed_to_the_key(
            self, client, org_key, db):
        slug, key = org_key
        rid = f"r-{uuid.uuid4().hex}"
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "client_ref": rid,
            "messages": [{"role": "user", "content": "hi"}]})

        with db.begin() as c:
            row = c.execute(text(
                "SELECT o.slug, u.tier, u.model, u.prompt_tokens, "
                "       u.completion_tokens, u.cached_tokens, u.billed_credits "
                "FROM usage_event u JOIN organization o "
                "ON o.id = u.organization_id WHERE u.client_ref = :r"
            ), {"r": rid}).first()

        assert row is not None
        assert row[0] == slug
        assert row[1] == "tier-balanced"
        assert row[2] == "deepseek/deepseek-v4-pro"
        assert (row[3], row[4]) == (1200, 40)
        # OpenAI-style nested cached_tokens, normalised.
        assert row[5] == 900
        # CP-4 is UNPRICED on purpose — CP-6 sets the card against this data.
        assert row[6] == Decimal("0.0000")

    def test_a_customer_CANNOT_suppress_their_own_meter(self, client, org_key, db):
        """The reversal. CP-4 keyed the meter on a caller-supplied id, so five
        completions sent with one reused id produced ONE usage row while the
        provider was called five times — the customer decided whether they were
        billed. request_id is now server-generated; the caller's value is
        `client_ref`, stored and trusted for nothing (migration 005)."""
        _, key = org_key
        ref = f"r-{uuid.uuid4().hex}"
        body = {"model": "tier-balanced", "client_ref": ref,
                "messages": [{"role": "user", "content": "hi"}]}

        for _ in range(5):
            assert client.post("/v1/chat/completions",
                               headers=key, json=body).status_code == 200

        with db.begin() as c:
            n = c.execute(text(
                "SELECT count(*) FROM usage_event WHERE client_ref = :r"),
                {"r": ref}).scalar_one()
            distinct = c.execute(text(
                "SELECT count(DISTINCT request_id) FROM usage_event "
                "WHERE client_ref = :r"), {"r": ref}).scalar_one()

        # Five provider calls, five metered rows, five distinct server ids.
        assert n == 5
        assert distinct == 5

    def test_attribution_headers_land_on_the_usage_row(self, client, org_key, db):
        _, key = org_key
        rid = f"r-{uuid.uuid4().hex}"
        client.post("/v1/chat/completions",
                    headers={**key, "X-CC-Member": "alice@corp.com",
                             "X-CC-Agent": "email-assistant",
                             "X-CC-Module": "email", "X-CC-Run": "run-42"},
                    json={"model": "tier-balanced", "client_ref": rid,
                          "messages": [{"role": "user", "content": "hi"}]})

        with db.begin() as c:
            row = c.execute(text(
                "SELECT user_email, agent, module_slug, run_id FROM usage_event "
                "WHERE client_ref = :r"), {"r": rid}).first()

        assert row == ("alice@corp.com", "email-assistant", "email", "run-42")

    def test_a_call_without_a_request_id_is_still_metered(self, client, org_key, db):
        slug, key = org_key
        before = self._count(db, slug)
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}]})
        assert self._count(db, slug) == before + 1

    def test_a_metering_failure_does_not_fail_the_completion(
            self, client, org_key, monkeypatch):
        # An unmetered call is a revenue problem; a failed call is a product
        # problem, and the product problem is worse.
        _, key = org_key
        from customer_console import main as main_mod

        def _boom(*a, **k):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(main_mod.store, "record_usage", _boom)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}]})

        assert r.status_code == 200
        assert r.json() == PROVIDER_RESPONSE

    @staticmethod
    def _count(db, slug: str) -> int:
        with db.begin() as c:
            return c.execute(text(
                "SELECT count(*) FROM usage_event u JOIN organization o "
                "ON o.id = u.organization_id WHERE o.slug = :s"), {"s": slug}
            ).scalar_one()

    @staticmethod
    def _count_served(db, slug: str) -> int:
        """Rows recording a call that SERVED — the ones :meth:`_count` used to
        be the whole of.

        Migration 020 (§8.1) put refusals in this same table, so a bare
        ``count(*)`` stopped answering *"was this call metered"* and started
        answering *"did anything happen"*. Two shipped tests asserted the
        first sentence through the second one, and they now say what they
        always meant.
        """
        with db.begin() as c:
            return c.execute(text(
                "SELECT count(*) FROM usage_event u JOIN organization o "
                "ON o.id = u.organization_id "
                "WHERE o.slug = :s AND u.refusal_reason IS NULL"), {"s": slug}
            ).scalar_one()

    @staticmethod
    def _refusals(db, slug: str) -> list[str]:
        """Every refusal slug this organization has collected, oldest first."""
        with db.begin() as c:
            return [r[0] for r in c.execute(text(
                "SELECT u.refusal_reason FROM usage_event u JOIN organization o "
                "ON o.id = u.organization_id "
                "WHERE o.slug = :s AND u.refusal_reason IS NOT NULL "
                "ORDER BY u.created_at, u.id"), {"s": slug}).all()]


# ── Usage extraction, across provider shapes ────────────────────────────────

class TestUsageExtraction:
    def test_anthropic_style_top_level_cache_counter(self):
        u = router_mod.usage_from_response({"usage": {
            "prompt_tokens": 100, "completion_tokens": 10,
            "cache_read_input_tokens": 60}})
        assert (u.prompt_tokens, u.completion_tokens, u.cached_tokens) == (100, 10, 60)

    def test_openai_style_nested_cache_counter(self):
        u = router_mod.usage_from_response({"usage": {
            "prompt_tokens": 100, "completion_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 60}}})
        assert u.cached_tokens == 60

    def test_a_response_with_no_usage_block_yields_zeros_not_an_error(self):
        u = router_mod.usage_from_response({"choices": []})
        assert (u.prompt_tokens, u.completion_tokens, u.cached_tokens) == (0, 0, 0)

    def test_garbage_never_raises(self):
        for junk in (None, "nonsense", 42, {"usage": "not-a-dict"},
                     {"usage": {"prompt_tokens": "many"}}):
            assert router_mod.usage_from_response(junk).prompt_tokens == 0


# ── Provider credentials ────────────────────────────────────────────────────

class TestProviderCredentials:
    def test_secrets_round_trip_and_are_not_stored_in_the_clear(
            self, db, monkeypatch):
        monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
        enc = router_mod.encrypt_secret("sk-very-secret")

        assert "sk-very-secret" not in enc
        assert router_mod.decrypt_secret(enc) == "sk-very-secret"

    def test_a_byok_organizations_own_key_wins_over_the_platforms(
            self, client, db, monkeypatch):
        # §3.4: BYOK is a tier, not an exception — such an org is metered but
        # not charged for tokens, which also caps our exposure on big accounts.
        monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
        slug = f"byok-{uuid.uuid4().hex[:8]}"
        org_id = client.post("/orgs/provision", headers=OP, json={
            "slug": slug, "name": "N",
            "owner_email": f"o@{slug}.com",
            "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
        }).json()["organization_id"]

        with db.connect() as c:
            tx = c.begin()
            c.execute(text(
                "INSERT INTO provider_credential (provider, secret_enc) "
                "VALUES ('anthropic', :s)"),
                {"s": router_mod.encrypt_secret("platform-key")})
            c.execute(text(
                "INSERT INTO provider_credential "
                "(provider, organization_id, secret_enc) "
                "VALUES ('anthropic', CAST(:o AS uuid), :s)"),
                {"o": org_id, "s": router_mod.encrypt_secret("customer-own-key")})

            own = router_mod.provider_credential(c, provider="anthropic",
                                                 org_id=org_id)
            platform = router_mod.provider_credential(c, provider="anthropic")
            tx.rollback()

        assert own[0] == "customer-own-key"
        assert platform[0] == "platform-key"

    def test_a_missing_credential_is_a_503_not_a_crash(self, client, org_key, db):
        _, key = org_key
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_binding (tier, model, effective_from) "
                "VALUES ('tier-orphan', 'nosuchprovider/model', "
                "        now() - interval '1 day') ON CONFLICT DO NOTHING"))

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-orphan", "messages": [{"role": "user", "content": "hi"}]})

        assert r.status_code == 503
        assert "nosuchprovider" in r.json()["detail"]


# ── The holes independent verification found in CP-4 ────────────────────────

class TestTheCustomerCannotRedirectOurCredential:
    """F1, HIGH. CP-4 used `extra="allow"` and excluded two fields by name, so
    `api_base` from the request body reached the provider call — and was only
    overridden when the credential row carried one, which the platform's own
    row does not. Verification measured a 200 with OUR key sent to
    `https://attacker.example/v1`. One field, total compromise of the credential
    `004_provider_keys.sql` exists to protect."""

    def test_api_base_in_the_body_is_rejected_not_forwarded(self, client, org_key, calls):
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "x"}],
            "api_base": "https://attacker.example/v1"})

        assert r.status_code == 422, r.text
        assert calls == [] or calls[-1].get("api_base") != "https://attacker.example/v1"

    @pytest.mark.parametrize("field", [
        "api_base", "base_url", "api_key", "custom_llm_provider",
        "extra_headers", "num_retries", "timeout", "mock_response",
    ])
    def test_no_routing_or_cost_parameter_can_be_set_by_a_caller(
            self, client, org_key, field):
        # An allowlist, so this is closed by construction rather than by having
        # thought of each name — but the names are pinned because they are the
        # ones that were measured to matter.
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "x"}],
            field: "anything"})
        assert r.status_code == 422, f"{field} was accepted"

    def test_the_router_pins_its_own_retry_and_timeout_ceilings(
            self, client, org_key, calls):
        _, key = org_key
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "x"}]})

        assert calls[-1]["num_retries"] == 1
        assert calls[-1]["timeout"] == 120

    def test_max_tokens_is_clamped_not_trusted(self, client, org_key, calls):
        _, key = org_key
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 10_000_000})

        assert calls[-1]["max_tokens"] == 32_000


#: What a streaming provider emits, as the frames it emits them in. Byte
#: strings on purpose: done-when 1 is about bytes, and a fixture built from
#: dicts would let the Router re-serialise and still pass.
#:
#: The last frame carries the usage, which is what `stream_options.
#: include_usage` asks an OpenAI-compatible provider for. The frames before it
#: carry none — that IS the streaming shape, and metering a guess instead is
#: the defect done-when 2 exists to stop.
PROVIDER_FRAMES = [
    b'data: {"id":"chatcmpl-s1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{"role":"assistant","content":"Sixteen"}}]}\n\n',
    b'data: {"id":"chatcmpl-s1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{"content":" pumps"}}]}\n\n',
    b'data: {"id":"chatcmpl-s1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":1200,"completion_tokens":40,'
    b'"prompt_tokens_details":{"cached_tokens":900}}}\n\n',
    b"data: [DONE]\n\n",
]


def _streaming_provider(frames=None, *, fail_before_first=False):
    """A provider whose call returns an async iterator of SSE frames."""
    emitted = list(PROVIDER_FRAMES if frames is None else frames)

    async def _stub(**kwargs):
        if fail_before_first:
            raise RuntimeError("provider refused the stream")

        async def _gen():
            for f in emitted:
                yield f

        return _gen()

    return _stub


class TestStreaming:
    """CP-4b. The half that matters most: every agent runtime streams.

    Replaces CP-4's explicit 501. That refusal was honest — CP-4 handed
    litellm's ``CustomStreamWrapper`` to FastAPI, which could not serialise it,
    so the client got a 500 **and** a phantom zero-token usage row was committed
    for a completion nobody received. Both defects are fenced below as
    permanent, not merely fixed.
    """

    # ── done-when 1 ──
    def test_the_relayed_frames_are_byte_identical_to_the_providers(
            self, client, org_key):
        _, key = org_key
        router_mod.set_provider_call(_streaming_provider())

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            assert r.status_code == 200
            body = b"".join(r.iter_bytes())

        # Frame boundaries, ordering and the sentinel, with nothing
        # re-serialised in between.
        assert body == b"".join(PROVIDER_FRAMES)

    def test_the_response_is_an_event_stream(self, client, org_key):
        _, key = org_key
        router_mod.set_provider_call(_streaming_provider())
        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            # A buffering proxy turns a stream into one late blob.
            assert r.headers.get("x-accel-buffering") == "no"
            r.read()

    def test_the_sentinel_is_added_when_the_provider_omits_it(
            self, client, org_key):
        # A client that never sees `[DONE]` waits for its own timeout.
        _, key = org_key
        router_mod.set_provider_call(
            _streaming_provider(PROVIDER_FRAMES[:-1]))

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            body = b"".join(r.iter_bytes())

        assert body.endswith(b"data: [DONE]\n\n")
        assert body.count(b"data: [DONE]") == 1

    def test_the_router_asks_for_the_usage_frame(self, client, org_key):
        # Without `include_usage` an OpenAI-compatible provider reports no
        # counts on a stream at all, and we would be metering a guess.
        seen: list[dict] = []

        async def _stub(**kwargs):
            seen.append(kwargs)

            async def _gen():
                for f in PROVIDER_FRAMES:
                    yield f

            return _gen()

        _, key = org_key
        router_mod.set_provider_call(_stub)
        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            r.read()

        assert seen[-1]["stream"] is True
        assert seen[-1]["stream_options"] == {"include_usage": True}

    # ── done-when 2 ──
    def test_one_stream_writes_one_usage_row(self, client, org_key, db):
        slug, key = org_key
        before = TestMetering._count(db, slug)
        router_mod.set_provider_call(_streaming_provider())

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            r.read()

        assert TestMetering._count(db, slug) == before + 1

    def test_the_row_carries_the_counts_the_STREAM_reported(
            self, client, org_key, db):
        # From the final frame, not from the request and not from a guess.
        slug, key = org_key
        router_mod.set_provider_call(_streaming_provider())

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            r.read()

        with db.begin() as c:
            row = c.execute(text(
                "SELECT prompt_tokens, completion_tokens, cached_tokens "
                "FROM usage_event u JOIN organization o "
                "ON o.id = u.organization_id WHERE o.slug = :s "
                "ORDER BY u.created_at DESC LIMIT 1"), {"s": slug}).first()

        assert tuple(row) == (1200, 40, 900)

    # ── done-when 4 ──
    def test_a_stream_that_never_starts_writes_no_usage_row(
            self, client, org_key, db):
        """The phantom-row defect that produced the 501, as a permanent fence."""
        slug, key = org_key
        before = TestMetering._count(db, slug)
        router_mod.set_provider_call(_streaming_provider(fail_before_first=True))

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            body = b"".join(r.iter_bytes())

        assert TestMetering._count(db, slug) == before
        # The stream still closes cleanly, or the client waits for a timeout.
        assert body == b"data: [DONE]\n\n"

    def test_an_empty_stream_writes_no_usage_row(self, client, org_key, db):
        # A provider that opens the stream and sends nothing has delivered
        # nothing. `started` is False, so there is nothing to meter.
        slug, key = org_key
        before = TestMetering._count(db, slug)
        router_mod.set_provider_call(_streaming_provider([]))

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            r.read()

        assert TestMetering._count(db, slug) == before

    # ── done-when 5 ──
    def test_a_refused_stream_never_opens_the_stream(
            self, client, org_key, db, gate_on):
        """A refusal inside an SSE frame is one every client renders as content.

        So the balance gate has to answer with its own status code, before a
        single frame exists.
        """
        slug, key = org_key
        # ⚠️ SERVED rows. The gate refuses before a frame exists, and since
        # migration 020 that refusal writes its own row (§8.1) — which is not
        # what this test is about.
        before = TestMetering._count_served(db, slug)
        opened: list[bool] = []

        async def _stub(**kwargs):
            opened.append(True)
            raise AssertionError("the gate let a refused stream reach the provider")

        router_mod.set_provider_call(_stub)
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "stream": True,
            "messages": [{"role": "user", "content": "x"}]})

        assert r.status_code == 402, r.text
        assert not r.headers["content-type"].startswith("text/event-stream")
        assert r.json()["detail"]["reason"] == "insufficient_credits"
        assert opened == []
        assert TestMetering._count_served(db, slug) == before
        # 📌 The POSITIVE half, so this stream-named test carries its own pin
        # instead of borrowing `TestARefusalReachesTheMeter`'s. The gate
        # refused before a frame existed, and the meter still recorded the
        # wall (§8.1). A negative-only assertion stays green on the day the
        # stream path silently stops metering its refusals.
        assert TestMetering._refusals(db, slug) == ["insufficient_credits"]

    def test_an_unknown_tier_refuses_a_stream_with_400(self, client, org_key):
        # The same refusal the buffered path gives. `stream: true` is not a way
        # around D32.7's "name a tier, not a model".
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "deepseek/deepseek-v4-pro", "stream": True,
            "messages": [{"role": "user", "content": "x"}]})

        assert r.status_code == 400
        assert "name a tier" in r.json()["detail"]


#: 📌 **`_stream_chain` is gone, and `bound_tier` took its work.** It bound a
#: `tier-sf-…` chain and removed nothing, so 368 rows accumulated in one
#: worktree's scratch database. They passed the catalog fence only because
#: somebody had hand-written a `model_capability` row for each model. On a
#: fresh database they fail it. One fixture now binds every chain in this
#: file, and it takes its rows away with it.


def _served(db, slug: str):
    """The newest usage row for this org, as (model, served_rank)."""
    with db.begin() as c:
        return c.execute(text(
            "SELECT model, served_rank FROM usage_event u JOIN organization o "
            "ON o.id = u.organization_id WHERE o.slug = :s "
            "ORDER BY u.created_at DESC LIMIT 1"), {"s": slug}).first()


class TestAStreamFailsOverBeforeItsFirstFrame:
    """§8.6 — the boundary, and the four clauses that fix where it sits.

    🔴 **What this suite exists to prove.** A streamed request used to take
    step 1 of the chain and stop there, so a provider being down cost the
    customer their request while the chain the operator configured did
    nothing. The line is now drawn at the FIRST FRAME: the route pulls it
    while walking, and only then does the 200 status line go out.

    ⚠️ **Both models are the SAME vendor on purpose.** A 529 does not strike
    a vendor off, and the shared `deepseek` platform credential is the one
    `org_key` provisions — so these fences test the WALK, not credential
    plumbing.
    """

    PRIMARY = "deepseek/sf-primary"
    BACKUP = "deepseek/sf-backup"

    @staticmethod
    def _provider(seen: list[str], *, primary_fails, backup_frames=None):
        """Step 1 raises what the test asks for. Step 2 streams normally."""
        frames = PROVIDER_FRAMES if backup_frames is None else backup_frames

        async def _stub(**kwargs):
            model = kwargs["model"]
            seen.append(model)
            if model == TestAStreamFailsOverBeforeItsFirstFrame.PRIMARY:
                raise primary_fails

            async def _gen():
                for f in frames:
                    yield f

            return _gen()

        return _stub

    # ── done-when 1 ──
    def test_a_529_before_any_frame_serves_the_backup_as_ONE_clean_stream(
            self, client, org_key, db, bound_tier):
        """🔴 The headline. The client must not be able to tell it happened."""
        _, key = org_key
        tier = bound_tier([self.PRIMARY, self.BACKUP], reads_images=None)
        seen: list[str] = []

        class _Overloaded(Exception):
            status_code = 529

        router_mod.set_provider_call(
            self._provider(seen, primary_fails=_Overloaded("busy")))

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": tier, "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            assert r.status_code == 200
            body = b"".join(r.iter_bytes())

        assert seen == [self.PRIMARY, self.BACKUP]
        # ⚠️ EXACT BYTES, not a substring. The first frame is pulled by the
        # route and replayed by the generator, so the two failures this
        # mechanism can have are a DUPLICATED first frame and a MISSING one.
        # Only equality catches both.
        assert body == b"".join(PROVIDER_FRAMES)

    def test_the_row_records_the_step_that_ANSWERED(
            self, client, org_key, db, bound_tier):
        # done-when 3. The customer pays for the model they got.
        slug, key = org_key
        tier = bound_tier([self.PRIMARY, self.BACKUP], reads_images=None)

        class _Overloaded(Exception):
            status_code = 529

        router_mod.set_provider_call(
            self._provider([], primary_fails=_Overloaded("busy")))

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": tier, "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            r.read()

        row = _served(db, slug)
        assert row is not None
        assert row.model == self.BACKUP
        assert row.served_rank == 2

    # ── done-when 2 ──
    def test_a_400_before_any_frame_calls_NO_second_step(
            self, client, org_key, db, bound_tier):
        # Every step fails a malformed request identically, so walking the
        # chain spends money to learn nothing.
        slug, key = org_key
        before = TestMetering._count(db, slug)
        tier = bound_tier([self.PRIMARY, self.BACKUP], reads_images=None)
        seen: list[str] = []

        class _BadRequest(Exception):
            status_code = 400

        router_mod.set_provider_call(
            self._provider(seen, primary_fails=_BadRequest("malformed")))

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": tier, "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            body = b"".join(r.iter_bytes())

        assert seen == [self.PRIMARY]
        # Clause 5: a walk that ends with nothing still closes cleanly, and
        # writes no row. `_REFUSAL_REASONS` stays closed — this wall was the
        # vendor's, not the customer's.
        assert body == b"data: [DONE]\n\n"
        assert TestMetering._count(db, slug) == before

    # ── the other side of the boundary ──
    def test_a_failure_AFTER_the_first_frame_does_NOT_fail_over(
            self, client, org_key, db, bound_tier):
        """The half of §3.6 that did not move, pinned at the ROUTE.

        Once a frame has reached the client the request is half answered.
        Serving the rest from a second model would splice two different
        completions into one response, which is worse than the error.
        """
        _, key = org_key
        tier = bound_tier([self.PRIMARY, self.BACKUP], reads_images=None)
        seen: list[str] = []

        async def _stub(**kwargs):
            seen.append(kwargs["model"])

            async def _gen():
                yield PROVIDER_FRAMES[0]
                raise RuntimeError("provider dropped the connection")

            return _gen()

        router_mod.set_provider_call(_stub)

        # The error reaches the client, and that is the CORRECT outcome. A
        # response already committed to one model cannot honestly continue on
        # another.
        with pytest.raises(RuntimeError), client.stream(
                "POST", "/v1/chat/completions", headers=key, json={
                    "model": tier, "stream": True,
                    "messages": [{"role": "user", "content": "x"}]}) as r:
            b"".join(r.iter_bytes())

        assert seen == [self.PRIMARY], "a started stream must not fail over"
        assert self.BACKUP not in seen


class TestTheWinningStreamIsCLOSED:
    """§8.6 — the socket the walk WON is released, whatever the client does.

    🔴 **This window is new, and the walk moving earlier is what opened it.**
    Starlette 1.1.0 never calls `aclose` on a body iterator, and the provider
    stream is now open before the response exists. So a client that goes away
    used to cost nothing and would now cost one held connection for the life
    of the process.

    ⚠️ **The source here is WRAPPER-shaped, not a bare async generator.** The
    loop's own finaliser closes an abandoned generator whatever the Router
    does, so a leak test written against one stays green after somebody
    deletes the close. `router_failover.Wrapper` is the shape litellm returns.
    """

    @staticmethod
    def _caller(org_id: str):
        from customer_console.auth import Caller
        return Caller(organization_id=org_id, key_prefix="cc_live_fence")

    @staticmethod
    def _drive(head, source, org_id, *, read):
        """Run `_streamed_completion` the way Starlette would, then stop.

        `read` decides how the client behaves: ``"one"`` takes a frame and
        walks away, ``"all"`` reads to the end or to the provider's error.
        """
        from customer_console.main import _streamed_completion

        async def _run():
            gen = _streamed_completion(
                head, source,
                org_id=org_id,
                caller=TestTheWinningStreamIsCLOSED._caller(org_id),
                resolved=router_mod.ResolvedTier(
                    tier="tier-balanced", model="deepseek/x", task="chat"),
                client_ref=None,
            )
            got = []
            if read == "one":
                async for frame in gen:
                    got.append(frame)
                    break          # the client stops reading here
                await gen.aclose()  # and Starlette drops the iterator
            else:
                with pytest.raises(RuntimeError):
                    async for frame in gen:
                        got.append(frame)
            return got

        return asyncio.run(_run())

    def test_a_stream_read_to_the_END_is_closed(self, client, org_key, db):
        # The ordinary exit. `aclose` on a finished stream is a no-op, so the
        # route closes on every path rather than reasoning about which it took.
        _, key = org_key
        made: list[Wrapper] = []

        async def _stub(**kwargs):
            made.append(Wrapper(frames=list(PROVIDER_FRAMES)))
            return made[-1]

        router_mod.set_provider_call(_stub)

        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": "tier-balanced", "stream": True,
                "messages": [{"role": "user", "content": "x"}]}) as r:
            body = b"".join(r.iter_bytes())

        # Byte-identity survives the close, and the close happened once.
        assert body == b"".join(PROVIDER_FRAMES)
        assert made[0].closed == 1

    def test_an_ABANDONED_stream_is_closed(self, org_id):
        """🔴 The headline. The client left, and the socket still goes back.

        Driven directly, because the HTTP layer cannot be made to abandon a
        response deterministically — the same reason `TestTheRelayItself`
        gives for its own direct drive.
        """
        source = Wrapper(frames=list(PROVIDER_FRAMES[1:]))
        got = self._drive([PROVIDER_FRAMES[0]], source, org_id, read="one")

        assert got == [PROVIDER_FRAMES[0]], "the head still reached the client"
        assert source.closed == 1, "an abandoned stream held its connection"

    def test_a_stream_that_DIES_mid_relay_is_closed(self, org_id):
        # The provider, not the client, ends it. The socket is still ours.
        source = Wrapper(raise_first=RuntimeError("provider dropped it"))
        got = self._drive([PROVIDER_FRAMES[0]], source, org_id, read="all")

        assert got == [PROVIDER_FRAMES[0]]
        assert source.closed == 1


class TestTheRelayItself:
    """done-when 3, and the generator mechanics the HTTP tests cannot reach.

    A client that disconnects mid-stream has still cost us the provider call.
    Dropping that row is a revenue hole that scales with flaky networks, and
    writing it twice is the credibility event `request_id UNIQUE` exists to
    prevent. So the relay is exercised directly: the HTTP layer cannot be made
    to abandon a response deterministically.
    """

    @staticmethod
    async def _drive(frames, *, stop_after=None):
        finished: list[tuple] = []

        async def _src():
            for f in frames:
                yield f

        gen = router_mod.relay_stream(
            _src(), on_finish=lambda u, s: finished.append((u, s)))
        got = []
        try:
            async for frame in gen:
                got.append(frame)
                if stop_after is not None and len(got) >= stop_after:
                    break
        finally:
            await gen.aclose()
        return got, finished

    def test_an_abandoned_stream_is_metered_once(self):
        got, finished = asyncio.run(
            self._drive(PROVIDER_FRAMES, stop_after=1))

        assert got == PROVIDER_FRAMES[:1]
        # EXACTLY once, and it did start, so the provider call is charged.
        assert len(finished) == 1
        assert finished[0][1] is True

    def test_a_completed_stream_reports_finish_exactly_once(self):
        got, finished = asyncio.run(self._drive(PROVIDER_FRAMES))

        assert got == PROVIDER_FRAMES
        assert len(finished) == 1
        usage, started = finished[0]
        assert started is True
        assert (usage.prompt_tokens, usage.completion_tokens) == (1200, 40)

    def test_the_LAST_usage_report_wins_not_the_first(self):
        """Some providers report usage cumulatively, on more than one frame.

        The final frame is the only one that carries the whole call. Keeping
        the first report would under-bill every such provider, silently and
        forever. No fixture with usage on ONE frame can tell the two rules
        apart, which is why this one carries it on two.
        """
        partial = (
            b'data: {"id":"c","choices":[{"index":0,"delta":{"content":"a"}}],'
            b'"usage":{"prompt_tokens":1200,"completion_tokens":5}}\n\n'
        )
        final = (
            b'data: {"id":"c","choices":[{"index":0,"delta":{},'
            b'"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":1200,"completion_tokens":40}}\n\n'
        )
        _, finished = asyncio.run(self._drive([partial, final]))

        usage, started = finished[0]
        assert started is True
        assert usage.completion_tokens == 40, (
            "the LAST report is the whole call; the first is only a prefix"
        )

    def test_a_source_that_yields_nothing_reports_started_false(self):
        _, finished = asyncio.run(self._drive([]))

        assert len(finished) == 1
        assert finished[0][1] is False

    def test_a_provider_error_mid_stream_still_meters_what_arrived(self):
        """We paid for the frames that did arrive, so they are still metered."""
        async def _src():
            yield PROVIDER_FRAMES[0]
            raise RuntimeError("provider dropped the connection")

        finished: list[tuple] = []

        async def _run():
            gen = router_mod.relay_stream(
                _src(), on_finish=lambda u, s: finished.append((u, s)))
            got = []
            with pytest.raises(RuntimeError):
                async for frame in gen:
                    got.append(frame)
            return got

        got = asyncio.run(_run())

        assert got == PROVIDER_FRAMES[:1]
        assert len(finished) == 1
        assert finished[0][1] is True

    def test_an_object_source_is_serialised_once_and_only_once(self):
        # The production litellm path yields OBJECTS, not frames. `frame_of` is
        # the one serialisation point, and it must produce one SSE frame.
        got, _ = asyncio.run(self._drive([
            {"id": "x", "choices": [{"delta": {"content": "hi"}}]}]))

        assert got[0] == (
            b'data: {"id": "x", "choices": [{"delta": {"content": "hi"}}]}\n\n')
        assert got[-1] == b"data: [DONE]\n\n"


class TestFailureShapes:
    def test_a_provider_error_becomes_502_not_500(self, client, org_key):
        # F12: a provider 429 was indistinguishable from a Router bug, so no
        # caller could tell retryable from fatal.
        _, key = org_key

        async def _boom(**kwargs):
            raise RuntimeError("provider exploded")

        router_mod.set_provider_call(_boom)
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "x"}]})

        assert r.status_code == 502
        assert "provider exploded" not in r.text

    def test_an_upstream_4xx_is_relayed_as_itself(self, client, org_key):
        _, key = org_key

        class Upstream(Exception):
            status_code = 429

        async def _boom(**kwargs):
            raise Upstream("rate limited")

        router_mod.set_provider_call(_boom)
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "x"}]})

        assert r.status_code == 429

    def test_a_failed_provider_call_writes_no_usage_row(self, client, org_key, db):
        slug, key = org_key

        async def _boom(**kwargs):
            raise RuntimeError("nope")

        router_mod.set_provider_call(_boom)
        before = TestMetering._count(db, slug)
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "x"}]})

        assert TestMetering._count(db, slug) == before

    def test_a_broken_encryption_key_fails_CLOSED_with_503(
            self, client, org_key, monkeypatch):
        # F11: this raised from inside the request and surfaced as a 500, which
        # reads as a bug rather than as "this deployment is misconfigured".
        _, key = org_key
        monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", "a-different-key")

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "messages": [{"role": "user", "content": "x"}]})

        assert r.status_code == 503


class TestTheProviderSecretNeverReachesALog:
    """F8, an R7 violation: `004_provider_keys.sql` says "never logged" and
    nothing tested it. CP-3's verification had already found a log fence that
    passed while leaking through `extra={}` and `exc_info`, so this reads the
    whole record."""

    def test_no_log_record_carries_the_provider_secret(self, client, org_key, caplog):
        import logging as _logging
        _, key = org_key

        async def _boom(**kwargs):
            # The dangerous frame: `secret` and `call_kwargs` are live locals
            # where the traceback is produced.
            raise RuntimeError("upstream died")

        router_mod.set_provider_call(_boom)
        with caplog.at_level(_logging.DEBUG):
            client.post("/v1/chat/completions", headers=key, json={
                "model": "tier-balanced",
                "messages": [{"role": "user", "content": "x"}]})

        haystack = []
        for rec in caplog.records:
            haystack.append(rec.getMessage())
            haystack.extend(str(v) for v in rec.__dict__.values())
            if rec.exc_info:
                haystack.append(str(rec.exc_info))
        assert "sk-provider-secret" not in "\n".join(haystack)


# ── CP-6: rating and the ledger draw ────────────────────────────────────────

class TestRatingOnTheRouterPath:
    """*"CP-6 sets the rate card against the burn this slice measures."*

    CP-4 wrote ``billed_credits = 0`` unconditionally. It now rates the
    completion against the card in force and `record_usage` negates that into
    `credit_ledger` in the same transaction as the usage row.
    """

    def test_a_priced_completion_draws_exactly_its_rated_cost(
            self, client, org_key, org_id, db, priced_card):
        slug, key = org_key
        _grant(client, slug, "100")
        ref = f"r-{uuid.uuid4().hex}"

        assert _complete(client, key, client_ref=ref).status_code == 200

        with db.begin() as c:
            billed = c.execute(text(
                "SELECT billed_credits FROM usage_event WHERE client_ref = :r"),
                {"r": ref}).scalar_one()
            drawn = c.execute(text(
                "SELECT delta FROM credit_ledger WHERE organization_id = :o "
                "  AND reason = 'usage'"), {"o": org_id}).scalar_one()
            ledger_sum = c.execute(text(
                "SELECT SUM(delta) FROM credit_ledger "
                "WHERE organization_id = :o"), {"o": org_id}).scalar_one()

        assert billed == CALL_COST
        # The draw is the negation of the charge — one row, not a second
        # opinion about what was spent.
        assert drawn == -CALL_COST
        # Acceptance clause 1: the balance the customer is shown IS the sum.
        assert Decimal(client.get("/me", headers=key).json()["credit_balance"]) \
            == ledger_sum == Decimal("100") - CALL_COST

    def test_cached_tokens_are_billed_at_the_cache_rate_end_to_end(
            self, client, org_key, db, priced_card):
        # 900 of the 1200 prompt tokens were cache reads. At the full input
        # rate the same call would cost 2.64; the discount we advertise has to
        # be real all the way to the ledger row, not just in the pure function.
        slug, key = org_key
        _grant(client, slug, "100")
        ref = f"r-{uuid.uuid4().hex}"
        _complete(client, key, client_ref=ref)

        with db.begin() as c:
            billed = c.execute(text(
                "SELECT billed_credits FROM usage_event WHERE client_ref = :r"),
                {"r": ref}).scalar_one()

        assert billed == CALL_COST < Decimal("2.64")

    def test_an_unpriced_model_is_still_metered_at_zero_not_dropped(
            self, client, org_key, db):
        # No `priced_card` fixture: the seeded card is all zeros, so rating
        # raises UnpricedModel. The completion must still be counted — the row
        # is the evidence CP-6 prices against, and losing it to a pricing gap
        # is how a month of burn disappears.
        _, key = org_key
        ref = f"r-{uuid.uuid4().hex}"

        assert _complete(client, key, client_ref=ref).status_code == 200

        with db.begin() as c:
            row = c.execute(text(
                "SELECT billed_credits, prompt_tokens FROM usage_event "
                "WHERE client_ref = :r"), {"r": ref}).first()

        assert row == (Decimal("0.0000"), 1200)

    def test_an_unpriced_model_writes_no_ledger_row_at_all(
            self, client, org_key, org_id, db):
        _, key = org_key
        _complete(client, key)

        with db.begin() as c:
            rows = c.execute(text(
                "SELECT count(*) FROM credit_ledger WHERE organization_id = :o"),
                {"o": org_id}).scalar_one()

        # A zero-delta row is noise in the one table a customer reads during a
        # dispute.
        assert rows == 0


# ── CP-6: the balance gate ──────────────────────────────────────────────────

class TestTheBalanceGate:
    """Acceptance: *"a zero-balance org gets 402 with the top-up payload while
    a non-AI endpoint on the same org still returns 200"*.

    Under organization-key auth the non-AI endpoints are ``GET /me`` and
    ``GET /me/billing`` — the surfaces a customer needs precisely when they are
    out of credits, since a customer who cannot see their balance cannot top it
    up.
    """

    def test_the_gate_ships_OFF_so_CP_4_behaviour_is_unchanged(
            self, client, org_key):
        # A newly provisioned org is `trial` with a zero balance, and how many
        # credits a trial starts with is an OPEN OWNER INPUT (§9.2). Enforcing
        # that today would refuse the first AI call of every new customer.
        _, key = org_key
        assert _complete(client, key).status_code == 200

    def test_a_zero_balance_trial_org_gets_402_with_a_top_up_payload(
            self, client, org_key, gate_on):
        _, key = org_key

        r = _complete(client, key)

        assert r.status_code == 402, r.text
        detail = r.json()["detail"]
        assert detail["reason"] == "insufficient_credits"
        # The payload the UI renders as "out of credits — top up".
        assert detail["top_up"]["balance_credits"] == "0"
        assert detail["top_up"]["is_trial"] is True
        assert Decimal(detail["top_up"]["credits_required"]) > 0

    def test_a_non_AI_endpoint_on_the_same_org_still_returns_200(
            self, client, org_key, gate_on):
        # The same key, the same organization, the same moment.
        _, key = org_key
        assert _complete(client, key).status_code == 402

        assert client.get("/me", headers=key).status_code == 200
        assert client.get("/me/billing", headers=key).status_code == 200

    def test_the_provider_is_never_reached_when_the_gate_refuses(
            self, client, org_key, calls, gate_on):
        # The whole point of a PRE-flight: a gate after the provider call
        # refuses a request we have already paid for.
        _, key = org_key
        before = len(calls)

        _complete(client, key)

        assert len(calls) == before

    def test_a_refused_call_writes_no_SERVED_usage_row(
            self, client, org_key, db, gate_on):
        """CP-6's clause, kept, plus slice 5's (§8.1).

        The refusal is not metered as a call — nothing served, so nothing is
        counted and nothing is charged. Since migration 020 it does leave ONE
        row saying which wall the customer hit, and that row is what A5 reads.
        """
        slug, key = org_key
        before = TestMetering._count_served(db, slug)

        assert _complete(client, key).status_code == 402

        assert TestMetering._count_served(db, slug) == before
        assert TestMetering._refusals(db, slug) == ["insufficient_credits"]

    def test_an_org_with_credits_passes_the_gate(
            self, client, org_key, gate_on):
        slug, key = org_key
        _grant(client, slug, "100")

        assert _complete(client, key).status_code == 200

    def test_a_paid_org_at_zero_keeps_working_into_the_grace_overdraft(
            self, client, org_key, gate_on):
        # Edge one of the shipped OverdraftPolicy, over HTTP. A hard cut-off at
        # exactly zero lands mid-workflow and costs more in support than the
        # overdraft ever will.
        slug, key = org_key
        client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": slug, "target": "active"})

        assert _complete(client, key).status_code == 200

    def test_a_paid_org_is_refused_only_PAST_the_grace_floor(
            self, client, org_key, org_id, gate_on):
        # Edge two, over HTTP, against the shipped value of 100 credits.
        slug, key = org_key
        client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": slug, "target": "active"})

        # Balance -99.9999: one quantum of grace left.
        _charge(client, org_id, "99.9999")
        assert _complete(client, key).status_code == 200

        # Balance -100.0000 exactly: the floor is reached, the next call stops.
        _charge(client, org_id, "0.0001")
        assert _complete(client, key).status_code == 402

    def test_a_trial_org_gets_no_grace_at_all(self, client, org_key, gate_on):
        # An unpaid account is where overdraft turns into unrecoverable cost.
        # The org fixture is `trial`, and one credit is enough to buy one call.
        slug, key = org_key
        _grant(client, slug, "1")

        assert _complete(client, key).status_code == 200


# ── CP-6: the per-run circuit breaker ───────────────────────────────────────

class TestThePerRunCircuitBreaker:
    """§4.4: *"An agent in a tool loop can burn a large amount in minutes… A
    per-run spend ceiling is not optional."*

    The run is `X-CC-Run`, D1's attribution unit — the same one an operator
    debugs. Charges are seeded through `/usage/record` (the Router's own
    internal token) rather than by running 388 completions, so the assertion is
    about the ceiling rather than about the test's patience.
    """

    def test_a_run_at_the_ceiling_is_refused_403(
            self, client, org_key, org_id, gate_on):
        slug, key = org_key
        _grant(client, slug, "10000")          # the BALANCE is not the issue
        _charge(client, org_id, "500", run_id="run-hot")

        r = _complete(client, key, headers={"X-CC-Run": "run-hot"})

        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert detail["reason"] == "run_ceiling_exceeded"
        assert detail["run_id"] == "run-hot"
        assert Decimal(detail["ceiling_credits"]) == Decimal("500")

    def test_the_refusal_is_403_not_402_because_topping_up_would_not_help(
            self, client, org_key, org_id, gate_on):
        # The organization has 10,000 credits. Telling it "out of credits —
        # top up" would be a lie the UI renders as a payment problem.
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, "500", run_id="run-hot")

        assert _complete(
            client, key, headers={"X-CC-Run": "run-hot"}).status_code != 402

    def test_a_runaway_loop_terminates(
            self, client, org_key, org_id, db, gate_on, priced_card):
        """The shape it exists to stop, through the real path.

        The run is seeded just under the ceiling; the loop then keeps calling,
        each completion adding its rated cost, until the Router stops it. What
        is asserted is that the loop ENDS — and that it ends because the run
        tripped, not because the test ran out of iterations.
        """
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, str(Decimal("500") - CALL_COST), run_id="loop")

        statuses = []
        for _ in range(10):
            r = _complete(client, key, headers={"X-CC-Run": "loop"})
            statuses.append(r.status_code)
            if r.status_code != 200:
                break

        assert statuses == [200, 403]
        with db.begin() as c:
            spent = c.execute(text(
                "SELECT SUM(billed_credits) FROM usage_event "
                "WHERE organization_id = :o AND run_id = 'loop'"),
                {"o": org_id}).scalar_one()
        assert spent == Decimal("500.0000")

    def test_a_different_run_for_the_same_org_is_unaffected(
            self, client, org_key, org_id, gate_on):
        # A tripwire on one loop, not a budget on the customer.
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, "500", run_id="run-hot")

        assert _complete(
            client, key, headers={"X-CC-Run": "run-cold"}).status_code == 200

    def test_a_call_with_no_run_id_is_not_subject_to_the_breaker(
            self, client, org_key, org_id, gate_on):
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, "500", run_id="run-hot")

        assert _complete(client, key).status_code == 200

    def test_the_breaker_is_off_with_the_gate_off(
            self, client, org_key, org_id):
        # One flag governs both refusals, so an owner enables spend enforcement
        # once rather than discovering the second half later.
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, "500", run_id="run-hot")

        assert _complete(
            client, key, headers={"X-CC-Run": "run-hot"}).status_code == 200


# ── A5 slice 5: the meter records the WALL (§8.1, migration 020) ────────────


def _refusal_row(db, org_id: str) -> dict | None:
    """The single refusal row for this organization, as a dict, or ``None``."""
    with db.begin() as c:
        row = c.execute(text(
            "SELECT request_id, refusal_reason, billed_credits, quantity, "
            "       unit, tier, task, model, provider_cost_usd, run_id, "
            "       client_ref "
            "FROM usage_event "
            "WHERE organization_id = :o AND refusal_reason IS NOT NULL"
        ), {"o": org_id}).all()
    assert len(row) <= 1, f"expected at most one refusal row, found {len(row)}"
    if not row:
        return None
    keys = ("request_id", "refusal_reason", "billed_credits", "quantity",
            "unit", "tier", "task", "model", "provider_cost_usd", "run_id",
            "client_ref")
    return dict(zip(keys, row[0], strict=True))


class TestARefusalReachesTheMeter:
    """§8.1, A5: *"is a customer hitting a wall"* needs the wall in the meter.

    🔴 **Every test here DRIVES the HTTP route.** A hand-inserted row proves
    the column exists and proves nothing about the hazard this slice is really
    about — the 400 raises from inside the serving transaction, so a refusal
    row written on that connection rolls back with the raise and the meter
    records nothing at all.
    """

    def test_an_unknown_tier_writes_one_row_that_SURVIVES_the_raise(
            self, client, org_key, org_id, db):
        """The fence for §8.1 clause 3, and the reason the slice is not a
        one-line insert."""
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "deepseek/deepseek-v4-pro",
            "messages": [{"role": "user", "content": "hi"}]})

        assert r.status_code == 400, r.text
        row = _refusal_row(db, org_id)
        assert row is not None, (
            "the 400 raised from inside the serving transaction and the "
            "refusal row rolled back with it — §8.1 clause 3"
        )
        assert row["refusal_reason"] == "tier_unknown"

    def test_the_row_holds_the_shape_A5_reads(self, client, org_key, org_id, db):
        """§8.1 clause 4, field by field."""
        _, key = org_key
        ref = f"cust-{uuid.uuid4().hex[:8]}"
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-imaginary", "client_ref": ref,
            "messages": [{"role": "user", "content": "hi"}]})

        row = _refusal_row(db, org_id)
        assert row is not None
        # 📌 The customer's OWN correlation id, exactly as a served row carries
        # it. Without it, "my request failed" has nothing support can match.
        assert row["client_ref"] == ref
        # We served nothing, so we charge nothing and consume nothing.
        assert row["billed_credits"] == Decimal("0.0000")
        assert row["quantity"] == Decimal(0)
        # The task's own unit, so the row reads beside a served one.
        assert row["unit"] == "tokens"
        assert row["task"] == "chat"
        # ⚠️ The REQUESTED tier, never a resolved one. At `tier_unknown` there
        # is nothing to resolve, and this is the fact A5 reports.
        assert row["tier"] == "tier-imaginary"
        # No model answered and no vendor billed us.
        assert row["model"] is None
        assert row["provider_cost_usd"] is None
        # Minted exactly as the served path mints it (001:271 NOT NULL UNIQUE).
        assert row["request_id"].startswith("rtr-")

    def test_a_HUGE_caller_label_is_clipped_before_it_persists(
            self, client, org_key, org_id, db):
        """🔴 A refused request is FREE, and its `model` is whatever they typed.

        Nothing upstream bounds the field, so without a clip a five-megabyte
        "model" string would persist once per 400 at no cost to the sender —
        unbounded growth an attacker drives for the price of a rejected
        request. The cell is OBSERVABILITY ("which tier did they ask for"), so
        a clipped value answers the question just as well.
        """
        from customer_console.main import _REFUSAL_LABEL_MAX

        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "x" * 100_000,
            "messages": [{"role": "user", "content": "hi"}]})

        # The customer still gets their refusal, and it is still a 400.
        assert r.status_code == 400, r.text
        row = _refusal_row(db, org_id)
        assert row is not None
        assert len(row["tier"]) <= _REFUSAL_LABEL_MAX
        assert _REFUSAL_LABEL_MAX == 200

    def test_a_refusal_draws_no_credit(self, client, org_key, org_id, db):
        _, key = org_key
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-imaginary",
            "messages": [{"role": "user", "content": "hi"}]})

        with db.begin() as c:
            drawn = c.execute(text(
                "SELECT count(*) FROM credit_ledger WHERE organization_id = :o"
            ), {"o": org_id}).scalar_one()
        assert drawn == 0, "a refusal must not move the ledger"

    def test_a_refused_STREAM_is_metered_the_same_way(
            self, client, org_key, org_id, db):
        # `stream: true` is not a way around the meter any more than it is a
        # way around D32.7's "name a tier, not a model".
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "deepseek/deepseek-v4-pro", "stream": True,
            "messages": [{"role": "user", "content": "x"}]})

        assert r.status_code == 400
        assert _refusal_row(db, org_id)["refusal_reason"] == "tier_unknown"

    def test_the_402_writes_the_word_the_customer_reads(
            self, client, org_key, org_id, db, gate_on):
        # W3: the slug is COPIED from `decide_spend`, never a second spelling.
        _, key = org_key
        r = _complete(client, key)

        assert r.status_code == 402
        assert r.json()["detail"]["reason"] == "insufficient_credits"
        row = _refusal_row(db, org_id)
        assert row["refusal_reason"] == "insufficient_credits"
        assert row["tier"] == "tier-balanced"
        assert row["billed_credits"] == Decimal("0.0000")

    def test_the_403_row_carries_its_RUN(
            self, client, org_key, org_id, db, gate_on):
        # A ceiling refusal without its run is not actionable — the breaker
        # reads the same field to decide the refusal.
        slug, key = org_key
        _grant(client, slug, "10000")
        _charge(client, org_id, "500", run_id="run-hot")

        r = _complete(client, key, headers={"X-CC-Run": "run-hot"})

        assert r.status_code == 403
        row = _refusal_row(db, org_id)
        assert row["refusal_reason"] == "run_ceiling_exceeded"
        assert row["run_id"] == "run-hot"

    def test_a_503_credential_failure_writes_NOTHING(
            self, client, org_key, org_id, db, monkeypatch):
        """Named non-goal: OUR failure is not a customer wall.

        One table that mixes a broken vendor with a customer at a wall answers
        neither question. The 502 half is
        ``TestFailureShapes::test_a_failed_provider_call_writes_no_usage_row``.
        """
        _, key = org_key
        monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", "a-different-key")
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced",
            "messages": [{"role": "user", "content": "hi"}]})

        assert r.status_code == 503, r.text
        with db.begin() as c:
            rows = c.execute(text(
                "SELECT count(*) FROM usage_event WHERE organization_id = :o"
            ), {"o": org_id}).scalar_one()
        assert rows == 0

    def test_an_anonymous_401_writes_nothing_because_it_CANNOT(self, client, db):
        """🔴 Structural, not a policy choice.

        Authentication refuses before the code knows the organization, and
        ``usage_event.organization_id`` is NOT NULL (001:256). A row needs a
        tenant and at 401 there is none. Do not invent a system organization
        to make it fit.
        """
        with db.begin() as c:
            before = c.execute(
                text("SELECT count(*) FROM usage_event")).scalar_one()

        r = client.post("/v1/chat/completions", json={
            "model": "tier-balanced",
            "messages": [{"role": "user", "content": "hi"}]})

        assert r.status_code == 401
        with db.begin() as c:
            after = c.execute(
                text("SELECT count(*) FROM usage_event")).scalar_one()
        assert after == before


class TestTheThreeBranchesTheRefusalWriterCanTake:
    """R7 for the three ways `_record_refusal` writes nothing.

    Each branch is a place where a refusal loses its meter row. All three are
    deliberate, and none of them was pinned until 2026-08-31 — so each one
    could have been deleted, or could have started firing, with every suite
    still green.
    """

    @staticmethod
    def _refuse_with(monkeypatch, exc):
        """Make the spend gate return *exc* from `_spend_refusal`.

        Patched at the MODULE attribute, because the route looks the name up
        at call time. Nothing else about the gate changes, so the refusal
        travels the real path the 402 and the 403 travel.
        """
        from customer_console import main as main_mod

        monkeypatch.setattr(main_mod, "_spend_gate_enabled", lambda: True)
        monkeypatch.setattr(main_mod, "_spend_refusal",
                            lambda conn, caller: exc)

    def test_a_FOURTH_slug_is_dropped_and_LOGGED(
            self, client, org_key, org_id, db, monkeypatch, caplog):
        """🔴 The guard on the vocabulary, and why the assertion is a LOG line.

        A slug nobody declared would fail migration 020's CHECK. The writer
        drops it first, so a typo never becomes an IntegrityError on the
        hottest path in the system.

        ⚠️ **"No row" alone does not fence this.** Delete the guard and the
        insert fails the CHECK, `_record_refusal` swallows it, and there is
        still no row — a test asserting only absence stays green. The two log
        lines are what tell the branches apart, so both are asserted.
        """
        from fastapi import HTTPException

        _, key = org_key
        self._refuse_with(monkeypatch, HTTPException(
            status_code=402, detail={"reason": "out_of_cheese"}))

        with caplog.at_level(logging.WARNING):
            r = _complete(client, key)

        # The customer still gets their refusal, unchanged.
        assert r.status_code == 402, r.text
        assert r.json()["detail"]["reason"] == "out_of_cheese"
        assert _refusal_row(db, org_id) is None
        assert "router.refusal_slug_unknown" in caplog.text
        assert "router.refusal_metering_failed" not in caplog.text, (
            "the writer reached the database with an undeclared slug — the "
            "vocabulary guard is gone"
        )

    def test_a_METER_failure_never_turns_a_REFUSAL_into_a_500(
            self, client, org_key, org_id, db, monkeypatch, caplog):
        """🔴 Best effort, the same rule `_record_completion` follows.

        An unmetered refusal is a reporting gap. A refusal the customer never
        receives, because the meter fell over, is an outage — and the outage
        is worse. So the writer swallows its own failure.
        """
        from customer_console import store as store_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("the meter is down")

        monkeypatch.setattr(store_mod, "record_usage", _boom)
        _, key = org_key

        with caplog.at_level(logging.ERROR):
            r = client.post("/v1/chat/completions", headers=key, json={
                "model": "tier-imaginary",
                "messages": [{"role": "user", "content": "hi"}]})

        assert r.status_code == 400, r.text
        assert "name a tier" in r.json()["detail"]
        assert _refusal_row(db, org_id) is None
        assert "router.refusal_metering_failed" in caplog.text

    def test_a_refusal_whose_detail_is_a_STRING_writes_no_row(
            self, client, org_key, org_id, db, monkeypatch):
        """Today's behaviour, pinned. It is deliberate and it is narrow.

        The slug must be the word already inside the body the customer reads,
        and a plain-string detail carries no such word. Minting one from the
        status code would be the second spelling W3 forbids.

        ⚠️ **Both shipped gate refusals carry a dict** — `decide_spend` and
        `_spend_refusal` build one each — so nothing in the tree reaches this
        branch. It is pinned because a future refusal that answers a bare
        sentence would lose its row SILENTLY, and this test is what turns that
        into a visible decision.
        """
        from fastapi import HTTPException

        _, key = org_key
        self._refuse_with(monkeypatch, HTTPException(
            status_code=403, detail="run stopped"))

        r = _complete(client, key)

        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "run stopped"
        assert _refusal_row(db, org_id) is None


# ── G-3: the CALLER declares the task ───────────────────────────────────────

class TestTheCallerDeclaresTheTask:
    """D61 G-3. **The Router never sniffs the payload.**

    `vision` uses the same provider verb as `chat` and differs only in which
    model is bound, so somebody has to say which it is. The alternative —
    inspecting the messages for image parts — is INFERENCE, and D32.7 is
    hostile to inference in this exact area: *"a bare model id is rejected 400,
    not coerced, because silent coercion hides a misconfigured agent behind a
    bill."*
    """

    def test_the_task_defaults_to_chat_so_no_existing_caller_changes(self):
        from customer_console.main import CompletionRequest

        assert CompletionRequest(
            model="tier-fast", messages=[{"role": "user", "content": "x"}]
        ).task == "chat"

    def test_a_caller_may_declare_one(self):
        from customer_console.main import CompletionRequest

        req = CompletionRequest(
            model="tier-stt", task="transcribe",
            messages=[{"role": "user", "content": "x"}],
        )
        assert req.task == "transcribe"

    def test_the_router_NEVER_inspects_the_payload_to_guess(self):
        """The fence for G-3's whole decision.

        ⚠️ Read from the AST, not the text — this module's docstrings discuss
        `image_url` and sniffing precisely while explaining why it is refused,
        so a grep would match the prose that forbids it.

        The names below are how payload inspection would actually be written.
        `_sanitize_messages_for_provider` is a REPAIR, not a decision, and it
        lives on the tenant side.
        """
        import ast
        import pathlib

        import customer_console.main as main_mod

        src = pathlib.Path(main_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        handler = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            and n.name == "chat_completions"
        )
        names = {
            s.attr for s in ast.walk(handler) if isinstance(s, ast.Attribute)
        } | {
            s.id for s in ast.walk(handler) if isinstance(s, ast.Name)
        }
        literals = {
            s.value for s in ast.walk(handler)
            if isinstance(s, ast.Constant) and isinstance(s.value, str)
        }
        for sniffed in ("image_url", "input_audio", "content_type"):
            assert sniffed not in literals, (
                f"the Router reads {sniffed!r} from the payload — that is "
                "inference, and G-3 says the CALLER declares the task"
            )
        assert "task" in names or "task" in literals


class TestTaskRoutingEndToEnd:
    def test_an_unbound_task_is_a_400_and_NOT_the_chat_binding(
            self, client, org_key):
        """§6A.9 rule 2. Serving an image request from a text model is D32.7's
        coercion defect in different clothes — it answers, plausibly, and
        bills."""
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-fast", "task": "image",
            "messages": [{"role": "user", "content": "a cat"}]})

        assert r.status_code == 400, r.text
        assert "image" in r.json()["detail"]

    def test_the_default_chat_path_is_unchanged(self, client, org_key):
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced",
            "messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 200, r.text

    def test_rating_picks_the_card_for_THIS_task_not_the_newest_one(self, db):
        """⚠️ One model can serve several tasks, at different prices.

        A rating path that looked up by model alone would take the NEWEST card
        for that model whatever task it priced — so an `image` card added after
        a `chat` card would silently re-price every chat completion. No fixture
        with one card per model can tell the two lookups apart, which is why
        this one writes two.
        """
        from customer_console.main import _rate_completion
        from customer_console.router import ExtractedUsage

        tier = f"tier-test-{uuid.uuid4().hex[:8]}"
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_catalog (slug, label) VALUES (:t, :t)"),
                {"t": tier})
            # chat FIRST, image SECOND. The wrong lookup takes the newest.
            c.execute(text(
                "INSERT INTO tier_rate_card (tier, task, unit, "
                " input_credits_per_1k, output_credits_per_1k, pricing_mode, "
                " effective_from) VALUES "
                "(:t, 'chat', 'tokens', 2, 6, 'priced', now() - interval '1 h')"),
                {"t": tier})
            c.execute(text(
                "INSERT INTO tier_rate_card (tier, task, unit, "
                " input_credits_per_1k, output_credits_per_1k, "
                " credits_per_unit, pricing_mode, effective_from) VALUES "
                "(:t, 'image', 'images', 0, 0, 999, 'priced', now())"),
                {"t": tier})

        with db.begin() as c:
            chat_billed, chat_unit = _rate_completion(
                c, tier=tier, model="x/irrelevant",
                usage=ExtractedUsage(prompt_tokens=1000, completion_tokens=500),
                task="chat",
            )
            # ⚠️ The IMAGE call is what distinguishes the two lookups.
            # `resolve_tier_rate` has no task default, but the CALLER does —
            # a chat-only fixture proves nothing. Measured: the mutation
            # survived twice before this line existed.
            img_billed, img_unit = _rate_completion(
                c, tier=tier, model="x/irrelevant",
                usage=ExtractedUsage(), task="image",
            )

        # 1000 fresh input @2 + 500 output @6 = 2 + 3 = 5 credits.
        assert chat_unit == "tokens"
        assert chat_billed == Decimal("5.0")
        # The image card is per IMAGE, and rating it as tokens would
        # report "tokens" here and bill from the wrong column.
        assert img_unit == "images", (
            "the chat card was used to price an image call"
        )

        with db.begin() as c:
            c.execute(text("DELETE FROM tier_rate_card WHERE tier = :t"),
                      {"t": tier})
            c.execute(text("DELETE FROM tier_catalog WHERE slug = :t"),
                      {"t": tier})

    def test_the_usage_row_records_the_task_and_the_unit(
            self, client, org_key, db, priced_card):
        """A row that says `0.4` without saying `minutes` cannot be checked."""
        slug, key = org_key
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced",
            "messages": [{"role": "user", "content": "x"}]})

        with db.begin() as c:
            row = c.execute(text(
                "SELECT task, unit, quantity FROM usage_event u "
                "JOIN organization o ON o.id = u.organization_id "
                "WHERE o.slug = :s ORDER BY u.created_at DESC LIMIT 1"),
                {"s": slug}).first()

        assert row[0] == "chat"
        assert row[1] == "tokens"
        # ⚠️ NULL on purpose for a token-priced call: the three token columns
        # already carry the quantity, and a second copy is a second thing to
        # disagree with. `quantity` is for the units that have no column.
        assert row[2] is None


# ── D-AI-2: the Router image rule (§3.2, §8.5 slice 4) ──────────────────────

#: A payload that CARRIES an image. Every test that sends it declares its own
#: task, because the Router must reach the same answer from the declaration
#: alone — the parts below are decoration the resolution never opens.
IMAGE_MESSAGES = [{
    "role": "user",
    "content": [
        {"type": "text", "text": "what is in this picture"},
        {"type": "image_url",
         "image_url": {"url": "https://example.invalid/pump.png"}},
    ],
}]


@pytest.fixture
def bound_tier(db):
    """Bind a fresh tier, and REMOVE what it wrote afterwards.

    **THE ONE tier-binding fixture in this file**, and every fence that needs a
    binding of its own goes through it. It takes one model or a whole CHAIN,
    which is what lets the image rule and the stream walk share it. A second
    binding helper beside this one is a second place to forget the teardown,
    which is exactly the defect below.

    🔴 **The teardown is not tidiness, and this fixture exists for it.**
    `GET /catalog/models` reports every binding whose ``(model, task)`` pair
    declares no `model_capability` row as **unserved**, and
    `test_customer_console_catalog.py::test_the_seeded_world_has_NO_unserved_binding`
    fails while one exists. These bindings name models that nothing declares.
    The scratch database is REUSED between runs, so a row left behind fails a
    SIBLING suite on the next sweep. Measured on one worktree: 419 rows from
    the image fences and 368 from the stream fences. `vision_bound` and
    `priced` clean up for the same reason.

    ⚠️ **A chain shares ONE ``effective_from``** (migration 011). Reading the
    newest row per rank instead would splice two chains together, so a fixture
    that dated each step separately would build a shape the Router never sees.

    ⚠️ **The test writes the `model_profile` row ITSELF.** Nothing seeds that
    table (§3.7 rule 4) and nothing populates ``reads_images``, so a fixture
    that leaned on the ladder would prove D-AI-2 against an empty table and
    pass for the wrong reason. ``reads_images=None`` leaves the model with NO
    profile row at all, which is the launch state and must read the same as
    FALSE.

    🔴 **``reads_images`` takes a LIST for a PER-STEP flag**, one entry per
    rank, and the entries may disagree. `resolve_vision_chain` reads the flag
    on EVERY step (§3.2 step 3b), so a fence for a mixed chain has to be able
    to build one. A scalar keeps the older shape: the flag lands on the RANK-1
    model, and the steps behind it get no profile row. Passing a list is what
    lets `TestABlindStepNeverEntersALiftChain` share this fixture instead of
    minting a second one.
    """
    made: list[tuple[str, list[str]]] = []

    def _bind(model: str | list[str], *, reads_images,
              task: str = "chat") -> str:
        models = [model] if isinstance(model, str) else list(model)
        if isinstance(reads_images, list):
            flags = list(reads_images)
        else:
            flags = [reads_images] + [None] * (len(models) - 1)
        tier = f"tier-fx-{uuid.uuid4().hex[:8]}"
        made.append((tier, models))
        with db.begin() as c:
            eff = c.execute(text("SELECT now()")).scalar_one()
            for rank, (step, flag) in enumerate(
                    zip(models, flags, strict=True), start=1):
                c.execute(
                    text("INSERT INTO tier_binding (tier, task, model, rank, "
                         "effective_from) VALUES (:t, :k, :m, :r, :eff)"),
                    {"t": tier, "k": task, "m": step, "r": rank, "eff": eff})
                if flag is not None:
                    c.execute(
                        text("INSERT INTO model_profile (model, reads_images) "
                             "VALUES (:m, :r) ON CONFLICT (model) DO UPDATE "
                             "SET reads_images = EXCLUDED.reads_images"),
                        {"m": step, "r": flag})
        return tier

    yield _bind
    with db.begin() as c:
        for tier, models in made:
            c.execute(text("DELETE FROM tier_binding WHERE tier = :t"),
                      {"t": tier})
            for step in models:
                c.execute(text("DELETE FROM model_profile WHERE model = :m"),
                          {"m": step})


@pytest.fixture
def vision_bound(db):
    """`tier-vision` bound to one model, for the life of one test.

    F3 measured `tier-vision` as UNBOUND, and migration 010 seeds no binding
    for it. So the fall path has nowhere to land until a test builds it, and
    the row is removed afterwards — the unbound fence next door depends on the
    launch state still being the launch state.
    """
    model = "deepseek/vision-eyes"
    with db.begin() as c:
        c.execute(
            text("INSERT INTO tier_binding (tier, task, model, rank, "
                 "effective_from) VALUES ('tier-vision', 'vision', :m, 1, "
                 "now())"),
            {"m": model})
    yield model
    with db.begin() as c:
        c.execute(text("DELETE FROM tier_binding WHERE tier = 'tier-vision'"))


@pytest.fixture
def vision_unbound(db):
    """Assert the launch state, and hand it to the test that needs it."""
    with db.begin() as c:
        bound = c.execute(text(
            "SELECT count(*) FROM tier_binding WHERE tier = 'tier-vision'"
        )).scalar_one()
    assert bound == 0, (
        "`tier-vision` is bound, so this fence cannot measure the unbound "
        "wall. A sibling test left its binding behind."
    )


@pytest.fixture
def priced(db):
    """Price one (tier, task) pair, so a bill proves WHICH pair was read.

    The card and its catalog row are removed afterwards. Migration 015 seeds
    NO tier rates, and pricing the slate is the owner's commercial act (H-42)
    — a fixture that left a price behind would make the next suite read a
    number nobody chose.
    """
    made: list[str] = []

    def _price(tier: str, task: str) -> None:
        made.append(tier)
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_catalog (slug, label) VALUES (:t, :t) "
                "ON CONFLICT (slug) DO NOTHING"), {"t": tier})
            c.execute(text(
                "INSERT INTO tier_rate_card (tier, task, "
                " input_credits_per_1k, output_credits_per_1k, "
                " cached_input_credits_per_1k, unit, pricing_mode, "
                " effective_from) "
                "VALUES (:t, :k, 2, 6, 0.5, 'tokens', 'priced', now())"),
                {"t": tier, "k": task})

    yield _price
    with db.begin() as c:
        for tier in made:
            c.execute(text("DELETE FROM tier_rate_card WHERE tier = :t"),
                      {"t": tier})
            c.execute(text("DELETE FROM tier_catalog WHERE slug = :t"),
                      {"t": tier})


def _last_row(db, slug: str):
    """The newest usage row for this org — tier, task, model, rank, credits."""
    with db.begin() as c:
        return c.execute(text(
            "SELECT tier, task, model, served_rank, billed_credits, unit "
            "FROM usage_event u JOIN organization o "
            "ON o.id = u.organization_id WHERE o.slug = :s "
            "AND u.refusal_reason IS NULL "
            "ORDER BY u.created_at DESC LIMIT 1"), {"s": slug}).first()


class TestTheRouterImageRule:
    """§3.2 / §8.5 — an image follows the chat model when it can (D-AI-2).

    🔴 **The money is the whole reason.** A chat model that already reads
    images answers with ONE call. Sending the image to a separate vision model
    costs a second call for the same turn, every turn.

    ⚠️ **Every test here drives the HTTP route**, because the rule is about
    which model the ROUTE calls and which pair the ROUTE bills. A direct call
    to the resolver proves the resolution and not the serving.
    """

    CHAT_MODEL = "deepseek/sees-images"
    BLIND_MODEL = "deepseek/reads-text-only"

    # ── §3.2 step 0.5: a tier that binds the declared task serves it ──
    def test_a_caller_that_NAMES_a_vision_tier_is_served_by_it(
            self, client, org_key, db, calls, vision_bound):
        """🔴 The regression this rule exists to stop.

        `tier-vision` binds `vision` and binds NO chat model. A resolution
        that reads the chat binding first answers *"no binding for tier
        'tier-vision' on task 'vision'"*, which is false — the binding the
        caller named is right there. That call was a 200 before D-AI-2, and it
        stays one.
        """
        slug, key = org_key

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-vision", "task": "vision",
            "messages": IMAGE_MESSAGES})

        assert r.status_code == 200, r.text
        assert [c["model"] for c in calls] == [vision_bound]
        row = _last_row(db, slug)
        # No lift and no fall. The tier the caller picked serves and bills.
        assert row.tier == "tier-vision"
        assert row.task == "vision"

    def test_a_SECOND_vision_tier_serves_ITSELF_too(
            self, client, org_key, db, calls, bound_tier):
        """The rule reads the declared TASK, never a list of slugs.

        An operator who adds a second vision tier tomorrow must not have to
        edit the Router. This is why step 0.5 is stated about the task.
        """
        slug, key = org_key
        model = "deepseek/second-eyes"
        # Through the fixture, so the binding LEAVES with the test. A `vision`
        # binding that outlives it is an `unserved` row for the catalog fence
        # next door, and the scratch database is reused.
        tier = bound_tier(model, task="vision", reads_images=None)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        assert r.status_code == 200, r.text
        assert [c["model"] for c in calls] == [model]
        assert _last_row(db, slug).tier == tier

    # ── clause 1: one model on a TRUE flag ──
    def test_a_chat_model_that_reads_images_serves_the_image_ITSELF(
            self, client, org_key, db, calls, bound_tier):
        _, key = org_key
        tier = bound_tier(self.CHAT_MODEL, reads_images=True)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        assert r.status_code == 200, r.text
        # 🔴 ONE model, one call. Two calls here is the cost defect D-AI-2
        # exists to close, and it would still answer the customer correctly.
        assert [c["model"] for c in calls] == [self.CHAT_MODEL]

    # ── clause 4 + §8.5 clause 4: the row says vision, the bill says chat ──
    def test_the_lift_records_VISION_and_bills_the_CHAT_pair(
            self, client, org_key, db, calls, priced, bound_tier):
        slug, key = org_key
        tier = bound_tier(self.CHAT_MODEL, reads_images=True)
        # Only the CHAT pair is priced. A bill computed from (tier, `vision`)
        # would find no card and report zero.
        priced(tier, "chat")

        client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        row = _last_row(db, slug)
        assert row is not None
        assert row.tier == tier
        assert row.model == self.CHAT_MODEL
        # 📌 The CUSTOMER asked for vision, so analytics must say vision.
        assert row.task == "vision"
        assert row.served_rank == 1
        # The (chosen tier, `chat`) card rated it — 1200 prompt of which 900
        # cached, 40 output, exactly `CALL_COST`.
        assert row.billed_credits == CALL_COST

    # ── clause 2: the fall ──
    def test_a_chat_model_that_reads_no_image_falls_to_the_vision_tier(
            self, client, org_key, db, calls, vision_bound, bound_tier):
        _, key = org_key
        tier = bound_tier(self.BLIND_MODEL, reads_images=False)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        assert r.status_code == 200, r.text
        # The image reaches the model that can see it, and the text-only chat
        # model is never asked about a picture it cannot read.
        assert [c["model"] for c in calls] == [vision_bound]

    def test_a_model_with_NO_profile_row_falls_the_same_way(
            self, client, org_key, db, calls, vision_bound, bound_tier):
        """The LAUNCH state. Nothing populates `reads_images` (§3.7 rule 4).

        An absent row means nobody told us, and D-AI-2 then sends the image to
        a model that certainly reads it rather than to one that may drop it in
        silence.
        """
        _, key = org_key
        tier = bound_tier("deepseek/unprofiled", reads_images=None)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        assert r.status_code == 200, r.text
        assert [c["model"] for c in calls] == [vision_bound]

    def test_the_fall_bills_the_VISION_pair(
            self, client, org_key, db, vision_bound, priced, bound_tier):
        slug, key = org_key
        tier = bound_tier(self.BLIND_MODEL, reads_images=False)
        # The chosen tier is priced and `tier-vision` is not. A bill that read
        # the chosen tier would report `CALL_COST` here.
        priced(tier, "chat")

        client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        row = _last_row(db, slug)
        assert row is not None
        assert row.tier == "tier-vision"
        assert row.task == "vision"
        assert row.model == vision_bound
        assert row.billed_credits == Decimal("0.0000"), (
            "the chosen tier's chat card priced a call that ran on "
            "`tier-vision` — the bill must follow the pair that SERVED"
        )

    # ── clause 3: the wall ──
    def test_an_unbound_vision_tier_answers_400_AND_CALLS_NOBODY(
            self, client, org_key, org_id, db, calls, vision_unbound, bound_tier):
        """§3.2 step 4. A silent drop makes the model answer about text it
        cannot see, and that answer looks correct."""
        slug, key = org_key
        tier = bound_tier(self.BLIND_MODEL, reads_images=False)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        assert r.status_code == 400, r.text
        # The sentence names BOTH halves, so an operator knows which to fix.
        assert r.json()["detail"] == (
            f"no vision model is bound; the chat model for tier {tier} "
            "does not read images"
        )
        assert calls == [], "a refused image call must reach no provider"
        assert _last_row(db, slug) is None

    def test_the_image_wall_writes_ONE_tier_unknown_row_naming_tier_vision(
            self, client, org_key, org_id, db, vision_unbound, bound_tier):
        """🔴 The slug and the sentence are two different things.

        `020_usage_refusal.sql`'s CHECK closes the vocabulary at three slugs,
        and `main._REFUSAL_REASONS` names the same three. A fourth spelling of
        one wall is what both exist to stop, so the row says `tier_unknown`
        truthfully — nothing binds `tier-vision` — while the HTTP detail says
        the vision sentence.
        """
        from customer_console.main import _REFUSAL_REASONS

        _, key = org_key
        tier = bound_tier(self.BLIND_MODEL, reads_images=False)

        client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        row = _refusal_row(db, org_id)
        assert row is not None
        assert row["refusal_reason"] == "tier_unknown"
        assert len(_REFUSAL_REASONS) == 3
        # ⚠️ The MISSING binding, not the tier the caller named. `tier-vision`
        # is the thing an operator has to go and bind.
        assert row["tier"] == "tier-vision"
        assert row["task"] == "vision"
        # `vision` prices in tokens (`010`:46), so the row does too. Minutes
        # here would mean the unit came from somewhere other than the catalog.
        assert row["unit"] == "tokens"
        assert row["billed_credits"] == Decimal("0.0000")

    # ── §3.2 step 0 ──
    def test_a_tier_with_no_chat_binding_hits_the_wall_it_ALREADY_had(
            self, client, org_key, org_id, db, calls):
        """Step 0. Nothing about D-AI-2 changes this refusal."""
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-imaginary", "task": "vision",
            "messages": IMAGE_MESSAGES})

        assert r.status_code == 400, r.text
        assert "name a tier, not a model" in r.json()["detail"]
        assert calls == []
        row = _refusal_row(db, org_id)
        # The tier the CALLER named, because that is the one that binds
        # nothing. `tier-vision` is not the repair here.
        assert row["tier"] == "tier-imaginary"
        assert row["task"] == "vision"

    # ── clause 5: nothing reads the payload ──
    def test_an_IMAGE_in_the_payload_with_task_chat_stays_on_the_chat_binding(
            self, client, org_key, db, calls, vision_bound, bound_tier):
        """🔴 The fence for G-3 on this slice.

        `tier-vision` IS bound here, so a Router that sniffed the payload
        would have somewhere to send this call — and it would send it. The
        caller declared nothing, so the task defaults to `chat` and the chat
        binding serves, image parts and all.
        """
        slug, key = org_key
        tier = bound_tier(self.BLIND_MODEL, reads_images=False)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "messages": IMAGE_MESSAGES})

        assert r.status_code == 200, r.text
        assert [c["model"] for c in calls] == [self.BLIND_MODEL], (
            "the Router inferred `vision` from the payload — G-3 says the "
            "CALLER declares the task"
        )
        assert _last_row(db, slug).task == "chat"

    def test_the_vision_resolution_reads_a_TIER_and_never_a_PAYLOAD(self):
        """The resolver's own half of clause 5, read from the AST.

        ⚠️ Read structurally rather than by grep: the docstrings around this
        rule discuss `image_url` and sniffing precisely while explaining why
        both are refused, so a text search matches the prose that forbids it.
        """
        import ast
        import inspect
        import pathlib

        import customer_console.router as router_file

        assert set(inspect.signature(
            router_mod.resolve_vision_chain).parameters) == {"conn", "tier"}

        src = pathlib.Path(router_file.__file__).read_text(encoding="utf-8")
        fn = next(
            n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            and n.name == "resolve_vision_chain"
        )
        literals = {
            s.value for s in ast.walk(fn)
            if isinstance(s, ast.Constant) and isinstance(s.value, str)
        }
        for sniffed in ("messages", "image_url", "content", "content_type"):
            assert sniffed not in literals, (
                f"the vision resolution reads {sniffed!r} — that is "
                "inference, and the CALLER declares the task"
            )

    # ── the stream takes the same two paths ──
    def test_a_STREAMED_vision_call_lifts_and_falls_the_same_way(
            self, client, org_key, db, vision_bound, bound_tier):
        """Slice 11 walks the chain AFTER this resolution, so both agree.

        A `stream: true` flag is not a second router any more than it is a way
        around the meter. One resolution serves both branches, and this test
        drives the lift and the fall through the streamed one.
        """
        slug, key = org_key
        seen: list[str] = []

        async def _stub(**kwargs):
            seen.append(kwargs["model"])

            async def _gen():
                for f in PROVIDER_FRAMES:
                    yield f

            return _gen()

        router_mod.set_provider_call(_stub)

        lifts = bound_tier(self.CHAT_MODEL, reads_images=True)
        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": lifts, "task": "vision", "stream": True,
                "messages": IMAGE_MESSAGES}) as r:
            assert r.status_code == 200
            r.read()

        assert seen == [self.CHAT_MODEL]
        lifted = _last_row(db, slug)
        assert lifted.model == self.CHAT_MODEL
        # 📌 The streamed row says `vision` too, or the two paths report one
        # customer's image work as two different things.
        assert lifted.task == "vision"

        falls = bound_tier(self.BLIND_MODEL, reads_images=False)
        with client.stream("POST", "/v1/chat/completions", headers=key, json={
                "model": falls, "task": "vision", "stream": True,
                "messages": IMAGE_MESSAGES}) as r:
            assert r.status_code == 200
            r.read()

        assert seen == [self.CHAT_MODEL, vision_bound]
        fell = _last_row(db, slug)
        assert fell.tier == "tier-vision"
        assert fell.task == "vision"

    def test_a_STREAMED_call_on_an_unbound_vision_tier_is_400(
            self, client, org_key, db, calls, vision_unbound, bound_tier):
        """`stream: true` reaches the same wall, and reaches no provider."""
        _, key = org_key
        tier = bound_tier(self.BLIND_MODEL, reads_images=False)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "stream": True,
            "messages": IMAGE_MESSAGES})

        assert r.status_code == 400, r.text
        assert "does not read images" in r.json()["detail"]
        assert calls == []

    # ── the chat path did not move ──
    def test_a_plain_CHAT_call_reads_no_profile_and_is_unchanged(
            self, client, org_key, db, calls, bound_tier):
        """D-AI-2 turns on `task`, and a chat call never asks about images."""
        slug, key = org_key
        tier = bound_tier(self.BLIND_MODEL, reads_images=False)

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "messages": [{"role": "user", "content": "hi"}]})

        assert r.status_code == 200, r.text
        assert [c["model"] for c in calls] == [self.BLIND_MODEL]
        assert _last_row(db, slug).task == "chat"


def _scratch_step(vendor: str, *, sees: bool) -> str:
    """Mint a model id nothing else in the run holds.

    A `model_profile` row is keyed on the model alone, so two fences that
    shared an id would share a flag and `bound_tier`'s teardown would remove
    a row the other one still needs. The name says what the step DOES, so a
    failure message reads without a lookup.
    """
    return f"{vendor}/{'sees' if sees else 'blind'}-{uuid.uuid4().hex[:8]}"


class TestABlindStepNeverEntersALiftChain:
    """§3.2 step 3b (D16) / §8.5 clauses 7 and 8.

    🔴 **The harm is a CONFIDENT WRONG ANSWER.** The flag used to be read on
    the rank-1 step alone. So a blind rank 2 could serve an image request and
    answer about a picture it never saw, with a 200 the customer reads as
    correct. §3.2 refuses at the image wall for exactly this reason.

    ⚠️ **Every test here drives the HTTP route.** The rule is about which
    model the ROUTE calls, and a direct call to the resolver proves the
    resolution and not the serving.
    """

    # ── clause 7 ──
    def test_a_blind_rank_2_never_enters_the_lift_chain(
            self, client, org_key, bound_tier, vision_bound):
        """Rank 1 sees, rank 1 fails, and no second model is asked.

        `vision_bound` binds `tier-vision` here, so a resolution that spliced
        the two chains together would show up as a third model in `seen`.
        """
        seen: list[str] = []

        async def _fail(**kwargs):
            seen.append(kwargs["model"])
            exc = Exception("overloaded")
            exc.status_code = 500
            raise exc

        router_mod.set_provider_call(_fail)
        _, key = org_key
        sees = _scratch_step("deepseek", sees=True)
        blind = _scratch_step("deepseek", sees=False)
        tier = bound_tier([sees, blind], reads_images=[True, False])

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        # An exhausted chain is the 502 it has always been. The chain is one
        # step long, so there is nothing left to try.
        assert r.status_code == 502, r.text
        assert seen == [sees], (
            "the blind rank-2 step answered about a picture it never saw"
        )

    def test_a_SEEING_rank_2_stays_in_the_lift_chain(
            self, client, org_key, calls, bound_tier, vision_bound):
        """The filter reads the whole chain, and not the head of it.

        Rank 1 is blind and rank 2 sees. One model in the chosen tier can
        still answer, so the lift holds and the second call to `tier-vision`
        is saved.
        """
        _, key = org_key
        blind = _scratch_step("deepseek", sees=False)
        sees = _scratch_step("deepseek", sees=True)
        tier = bound_tier([blind, sees], reads_images=[False, True])

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        assert r.status_code == 200, r.text
        assert [c["model"] for c in calls] == [sees]

    def test_a_chain_with_NO_seeing_step_falls_to_the_vision_tier(
            self, client, org_key, calls, bound_tier, vision_bound):
        """An empty filter result falls, exactly as one FALSE step does."""
        _, key = org_key
        tier = bound_tier(
            [_scratch_step("deepseek", sees=False),
             _scratch_step("deepseek", sees=False)],
            reads_images=[False, False])

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        assert r.status_code == 200, r.text
        assert [c["model"] for c in calls] == [vision_bound]

    # ── clause 8 ──
    def test_an_unkeyed_rank_1_never_promotes_a_blind_rank_2(
            self, client, org_key, calls, bound_tier, vision_bound):
        """🔴 This shape needs NO failover at all, which is the wider half.

        The route drops every step it holds no key for before it tries
        anything. So an unkeyed rank 1 used to make the blind rank 2 the FIRST
        step the Router called, with nothing having failed.

        ⚠️ **The answer is the 503 an unconfigured vendor already gets, and
        NOT a fall to `tier-vision`.** A credential-aware fall is a third
        resolution rule and §3.2 records no decision on it (§8.5 clause 8).
        `tier-vision` is bound here, and the route still does not reach it.
        """
        _, key = org_key
        vendor = f"nokey{uuid.uuid4().hex[:8]}"
        tier = bound_tier(
            [_scratch_step(vendor, sees=True),
             _scratch_step("deepseek", sees=False)],
            reads_images=[True, False])

        r = client.post("/v1/chat/completions", headers=key, json={
            "model": tier, "task": "vision", "messages": IMAGE_MESSAGES})

        assert r.status_code == 503, r.text
        assert vendor in r.json()["detail"]
        assert calls == [], (
            "a blind model answered because rank 1 held no credential"
        )


def test_n_is_capped_like_max_tokens(client, org_key):
    """`n` MULTIPLIES output cost; uncapped it defeated the 32k ceiling."""
    _, key = org_key
    r = client.post("/v1/chat/completions", headers=key, json={
        "model": "tier-balanced",
        "messages": [{"role": "user", "content": "hi"}],
        "n": 50})
    assert r.status_code == 422


def test_a_vendor_auth_failure_answers_502_never_401(client, org_key):
    """Relayed verbatim, a revoked PLATFORM key told the customer to rotate
    THEIR key — every OpenAI-compatible SDK reads 401 that way. Our
    upstream's auth is our outage: 502."""
    async def _reject(**kwargs):
        exc = Exception("Unauthorized")
        exc.status_code = 401
        raise exc

    router_mod.set_provider_call(_reject)
    _, key = org_key
    r = client.post("/v1/chat/completions", headers=key, json={
        "model": "tier-balanced",
        "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502
    assert r.json()["detail"] == "upstream provider error"
