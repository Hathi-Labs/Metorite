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

import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from customer_console import router as router_mod
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder  # noqa: E402

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


@pytest.fixture
def org_key(client, db, monkeypatch):
    """A provisioned org, a live key, and a platform DeepSeek credential."""
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    slug = f"router-{uuid.uuid4().hex[:8]}"
    client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com"})
    token = client.post("/keys", headers=OP, json={"org_slug": slug}).json()["token"]

    with db.begin() as c:
        c.execute(
            text("INSERT INTO provider_credential (provider, secret_enc, label) "
                 "VALUES ('deepseek', :s, 'platform') "
                 "ON CONFLICT DO NOTHING"),
            {"s": router_mod.encrypt_secret("sk-provider-secret")},
        )
    return slug, {"Authorization": f"Bearer {token}"}


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
            "owner_email": f"o@{slug}.com"}).json()["organization_id"]

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


class TestStreaming:
    """F3. CP-4 forwarded `stream` and handed litellm's CustomStreamWrapper to
    FastAPI, which failed to serialise it: the client got a 500 AND a phantom
    zero-token usage row was committed for a completion nobody received."""

    def test_streaming_is_refused_explicitly_not_with_a_500(self, client, org_key):
        _, key = org_key
        r = client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "stream": True,
            "messages": [{"role": "user", "content": "x"}]})

        assert r.status_code == 501
        assert "CP-4b" in r.json()["detail"]

    def test_a_refused_stream_writes_no_usage_row(self, client, org_key, db):
        slug, key = org_key
        before = TestMetering._count(db, slug)
        client.post("/v1/chat/completions", headers=key, json={
            "model": "tier-balanced", "stream": True,
            "messages": [{"role": "user", "content": "x"}]})
        assert TestMetering._count(db, slug) == before


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
