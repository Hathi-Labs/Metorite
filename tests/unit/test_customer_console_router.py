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
    model = "deepseek/deepseek-v4-pro"
    with db.begin() as c:
        c.execute(text(
            # CP-10 slice 2: `pricing_mode` is what makes a card billable,
            # not the numbers — a zero cannot carry three meanings (G-4).
            # The conflict target is TARGETLESS because the primary key is
            # now (model, task, effective_from).
            "INSERT INTO model_rate_card (model, input_credits_per_1k, "
            " output_credits_per_1k, cached_input_credits_per_1k, "
            " pricing_mode, effective_from) "
            "VALUES (:m, 2, 6, 0.5, 'priced', now()) "
            "ON CONFLICT DO NOTHING"), {"m": model})
    yield model
    with db.begin() as c:
        # Only the fixture's row: the seed is priced at zero and stays.
        c.execute(text(
            "DELETE FROM model_rate_card WHERE model = :m "
            "  AND (input_credits_per_1k <> 0 OR output_credits_per_1k <> 0)"),
            {"m": model})


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
        before = TestMetering._count(db, slug)
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
        assert TestMetering._count(db, slug) == before

    def test_an_unknown_tier_refuses_a_stream_with_400(self, client, org_key):
        # The same refusal the buffered path gives. `stream: true` is not a way
        # around D32.7's "name a tier, not a model".
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "deepseek/deepseek-v4-pro", "stream": True,
            "messages": [{"role": "user", "content": "x"}]})

        assert r.status_code == 400
        assert "name a tier" in r.json()["detail"]


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

    def test_a_refused_call_writes_no_usage_row(
            self, client, org_key, db, gate_on):
        slug, key = org_key
        before = TestMetering._count(db, slug)

        _complete(client, key)

        assert TestMetering._count(db, slug) == before

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
