"""WS-31 CP-EMB — the embedding door, ``POST /v1/embeddings``.

Spec: ``project-docs/specs/customer_console.md`` §6A.16 · D19.2 · D61 G-4 ·
D32.7. Owner directive, 2026-10-16.

🔴 **These fences DRIVE the route** and read the rows it wrote. The provider
call is the one seam, ``router.set_provider_call``, so no test spends money
at AI/ML API.

🔴 **The binding under test is the SEEDED one.** The migration writes the
``tier-embed`` binding, its capability and the ``absorbed`` card. So a served
call here proves the seed and the door together. Only the vendor key is
minted by the suite, because a secret never lives in a migration.

**R8.** Every database clause runs against a real Postgres through
``tests/unit/_customer_console_ladder.py``. A skipped R8 test proves nothing.
"""
from __future__ import annotations

import os
import pathlib
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from customer_console import catalog
from customer_console import router as router_mod
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
    ladder,
    mint_deployment_key,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

_DB = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}
ENC_KEY = "test-encryption-key-not-a-real-one"

#: What the migration binds, and what litellm is handed for it.
SEEDED_MODEL = "aimlapi/text-embedding-3-small"
LITELLM_MODEL = "openai/text-embedding-3-small"
AIMLAPI_HOST = "https://api.aimlapi.com/v1"

#: The label of the vendor key this suite mints, so the teardown removes
#: its OWN row and never a row a sibling suite owns.
_FENCE_LABEL = "embed-fence"
_SECRET = "sk-aimlapi-embed-fence"

PROMPT_TOKENS = 17


# ── Pure fences: no database ────────────────────────────────────────────────

def test_aembedding_is_a_serving_verb():
    """Item 1. The door exists, so the Router may call the verb, and the
    serving set never holds a verb the operator cannot write."""
    assert "aembedding" in router_mod.SERVING_INVOCATIONS
    assert router_mod.SERVING_INVOCATIONS <= catalog.KNOWN_INVOCATIONS


class TestTheVendorRewrite:
    """Item 2. ONE rewrite, for ONE vendor."""

    def test_aimlapi_goes_to_litellm_as_openai_at_the_aimlapi_host(self):
        assert router_mod.litellm_target(SEEDED_MODEL, None) == (
            LITELLM_MODEL, AIMLAPI_HOST)

    def test_a_stored_host_wins_and_gains_v1_once(self):
        assert router_mod.litellm_target(SEEDED_MODEL, "https://h.example/") == (
            LITELLM_MODEL, "https://h.example/v1")
        assert router_mod.litellm_target(SEEDED_MODEL, "https://h.example/v1") == (
            LITELLM_MODEL, "https://h.example/v1")

    @pytest.mark.parametrize("model", [
        "openai/text-embedding-3-small", "deepseek/deepseek-chat", "aimlapi",
        "groq/whisper-large-v3-turbo",
    ])
    def test_every_other_model_passes_through(self, model):
        assert router_mod.litellm_target(model, "b") == (model, "b")


class TestTheVectorReader:
    def test_reads_the_vectors_in_index_order(self):
        body = {"data": [{"index": 1, "embedding": [2.0]},
                         {"index": 0, "embedding": [1.0]}]}
        assert router_mod.embedding_vectors(body, 2) == [[1.0], [2.0]]

    @pytest.mark.parametrize(("body", "count"), [
        ({}, 1),
        (None, 1),
        ({"data": "x"}, 1),
        ({"data": [{"index": 0}]}, 1),
        ({"data": [{"index": 0, "embedding": "abc"}]}, 1),
        ({"data": [{"index": 0, "embedding": [1.0]}]}, 2),  # a short count
        ({"data": [{"index": 0, "embedding": [1.0]},
                   {"index": 0, "embedding": [1.0]}]}, 2),  # one index twice
    ])
    def test_an_unreadable_answer_is_none(self, body, count):
        assert router_mod.embedding_vectors(body, count) is None


# ── The database half ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _schema():
    if not _URL:
        pytest.skip("CUSTOMER_CONSOLE_DATABASE_URL unset")
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db(_schema):
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def provider():
    """The stubbed provider call. It records the kwargs and answers vectors.

    ⚠️ The original call is restored afterwards. A stub left installed
    reaches every suite that shares this process.
    """
    state: dict = {"calls": [], "reply": None}

    async def _stub(**kwargs):
        state["calls"].append(kwargs)
        if state["reply"] is not None:
            return state["reply"]
        texts = kwargs["input"]
        return {
            "object": "list",
            "data": [{"object": "embedding", "index": i, "embedding": [0.25] * 1536}
                     for i in range(len(texts))],
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": PROMPT_TOKENS, "total_tokens": PROMPT_TOKENS},
        }

    original = router_mod._PROVIDER_CALL[0]
    router_mod.set_provider_call(_stub)
    yield state
    router_mod.set_provider_call(original)


@pytest.fixture
def client(monkeypatch, db, provider):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AIMLAPI_API_KEY", raising=False)
    from customer_console.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def org(client, db):
    """A provisioned org on the shared box, its owner, and a live org key."""
    with db.begin() as c:
        ensure_deployment(c)
    slug = f"emb-{uuid.uuid4().hex[:8]}"
    owner = f"o@{slug}.example"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": owner,
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    assert r.status_code == 200, r.text
    token = client.post("/keys", headers=OP, json={"org_slug": slug}).json()["token"]
    with db.begin() as c:
        org_id = str(c.execute(text("SELECT id FROM organization WHERE slug = :s"),
                               {"s": slug}).scalar_one())
    return {"slug": slug, "owner": owner, "id": org_id,
            "key": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def aimlapi_key(db, monkeypatch):
    """The PLATFORM `aimlapi` key, for ONE test. Never an org row: an org
    row is BYOK, and BYOK bills zero for a different reason (§3.4)."""
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    with db.begin() as c:
        c.execute(text(
            "INSERT INTO provider_credential (provider, secret_enc, label) "
            "VALUES ('aimlapi', :s, :l) ON CONFLICT DO NOTHING"),
            {"s": router_mod.encrypt_secret(_SECRET), "l": _FENCE_LABEL})
    yield
    with db.begin() as c:
        c.execute(text(
            "DELETE FROM provider_credential "
            "WHERE provider = 'aimlapi' AND label = :l"), {"l": _FENCE_LABEL})


def _embed(client, key, *, model="tier-embed", input_=None, headers=None, **fields):
    body = {"model": model, "input": ["alpha", "beta"] if input_ is None else input_,
            **fields}
    return client.post("/v1/embeddings", headers={**key, **(headers or {})}, json=body)


_ROW = (
    "SELECT task, tier, model, prompt_tokens, unit, billed_credits, "
    "       refusal_reason, metering_fault, user_email "
    "FROM usage_event WHERE organization_id = CAST(:o AS uuid)"
)


def _rows(db, org_id: str) -> list:
    with db.begin() as c:
        return c.execute(text(_ROW), {"o": org_id}).all()


def _ledger(db, org_id: str) -> list:
    with db.begin() as c:
        return c.execute(text(
            "SELECT delta, reason FROM credit_ledger "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org_id}).all()


def _seed_sql() -> str:
    """This slice's migration, found by CONTENT and never by number (R1)."""
    for path in ladder():
        body = pathlib.Path(path).read_text(encoding="utf-8")
        if "aimlapi/text-embedding-3-small" in body:
            return body
    raise AssertionError("no Console migration seeds the embedding door")


@_DB
class TestTheSeed:
    """Item 6. Production gets the three rows on deploy, by the ladder."""

    def test_the_binding_the_capability_and_the_absorbed_card(self, db):
        with db.begin() as c:
            resolved = router_mod.resolve_tier(c, "tier-embed", "embed")
            assert resolved.model == SEEDED_MODEL
            assert router_mod.resolve_invocation(c, SEEDED_MODEL, "embed") == "aembedding"
            card = router_mod.resolve_tier_rate(c, "tier-embed", "embed")
        assert card.pricing_mode == "absorbed" and card.is_absorbed
        assert card.unit == "tokens"
        assert (card.input_per_1m, card.output_per_1m, card.cached_input_per_1m,
                card.credits_per_unit) == (0, 0, 0, 0)

    def test_a_replay_adds_nothing(self, db):
        def _counts(c):
            return tuple(c.execute(text(sql)).scalar_one() for sql in (
                "SELECT count(*) FROM tier_binding WHERE tier = 'tier-embed'",
                "SELECT count(*) FROM model_capability WHERE task = 'embed'",
                "SELECT count(*) FROM tier_rate_card WHERE tier = 'tier-embed'",
            ))

        seed = _seed_sql()
        with db.begin() as c:
            before = _counts(c)
            c.execute(text(seed))
            assert _counts(c) == before

    def test_a_rebind_is_never_undone_by_a_replay(self, db):
        """An operator re-binds the tier. The replay must not add our row back,
        and must not declare our model again once it is out of force."""
        seed = _seed_sql()
        with db.connect() as c:
            trans = c.begin()
            try:
                # `now()` is the transaction's start, so this row is newer
                # than the seed's and already in force.
                c.execute(text(
                    "INSERT INTO tier_binding (tier, model, task, effective_from) "
                    "VALUES ('tier-embed', 'other/embedder', 'embed', now())"))
                c.execute(text(
                    "DELETE FROM model_capability WHERE model = :m AND task = 'embed'"),
                    {"m": SEEDED_MODEL})
                c.execute(text(seed))
                declared = c.execute(text(
                    "SELECT count(*) FROM model_capability WHERE model = :m"),
                    {"m": SEEDED_MODEL}).scalar_one()
                in_force = router_mod.resolve_tier(c, "tier-embed", "embed").model
                assert declared == 0
                assert in_force == "other/embedder"
            finally:
                trans.rollback()


@_DB
class TestTheDoorServes:
    """Item 2. One vector per input, in order, and the answer names the TIER."""

    def test_a_served_call_answers_openai_shaped_vectors(
        self, client, org, aimlapi_key, provider
    ):
        r = _embed(client, org["key"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["object"] == "list"
        assert [d["index"] for d in body["data"]] == [0, 1]
        assert all(len(d["embedding"]) == 1536 for d in body["data"])
        assert body["model"] == "tier-embed"
        assert "aimlapi" not in r.text and "text-embedding" not in r.text
        assert body["usage"]["prompt_tokens"] == PROMPT_TOKENS

    def test_a_single_string_input_is_one_vector(self, client, org, aimlapi_key):
        r = _embed(client, org["key"], input_="one")
        assert r.status_code == 200, r.text
        assert len(r.json()["data"]) == 1

    def test_the_vendor_call_uses_the_stored_credential(
        self, client, db, org, aimlapi_key, provider, monkeypatch
    ):
        """Item 4. The secret is the stored row's, and no env key is read."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-must-not-be-used")
        r = _embed(client, org["key"])
        assert r.status_code == 200, r.text
        with db.begin() as c:
            stored = router_mod.provider_credential(c, provider="aimlapi")
        call = provider["calls"][0]
        assert call["api_key"] == stored.secret
        assert call["api_key"] != "sk-env-must-not-be-used"
        assert call["model"] == LITELLM_MODEL
        assert call["api_base"].endswith("/v1")
        assert call["invocation"] == "aembedding"
        assert call["input"] == ["alpha", "beta"]
        # An allowlist: nothing the caller sent reaches the vendor but texts.
        assert set(call) == {"model", "input", "api_key", "invocation",
                             "num_retries", "timeout", "api_base"}

    def test_the_deployment_key_serves_a_proven_member(
        self, client, db, org, aimlapi_key
    ):
        """Item 7. The same arm every door takes (H-152)."""
        with db.begin() as c:
            token = mint_deployment_key(c, deployment_id=ensure_deployment(c),
                                        capabilities=["serve"])
        key = {"Authorization": f"Bearer {token}"}
        refused = _embed(client, key)
        assert refused.status_code == 400, refused.text
        r = _embed(client, key, headers={"X-CC-Member": org["owner"],
                                         "X-CC-Member-Proven": "1"})
        assert r.status_code == 200, r.text
        rows = _rows(db, org["id"])
        assert len(rows) == 1 and rows[0].user_email == org["owner"]

    def test_an_unreadable_answer_is_a_502_after_the_row(
        self, client, db, org, aimlapi_key, provider
    ):
        provider["reply"] = {"data": [], "usage": {"prompt_tokens": 3}}
        r = _embed(client, org["key"])
        assert r.status_code == 502, r.text
        rows = _rows(db, org["id"])
        assert len(rows) == 1 and rows[0].prompt_tokens == 3


@_DB
class TestTheDoorRefuses:
    """Item 3. A bare model id, or an unbound tier, is a 400 and one row."""

    @pytest.mark.parametrize("model", [SEEDED_MODEL, "tier-embed-unbound"])
    def test_a_model_id_or_an_unbound_tier_is_400(
        self, client, db, org, aimlapi_key, provider, model
    ):
        r = _embed(client, org["key"], model=model)
        assert r.status_code == 400, r.text
        assert provider["calls"] == []
        rows = _rows(db, org["id"])
        assert len(rows) == 1 and rows[0].refusal_reason == "tier_unknown"
        assert rows[0].task == "embed"

    @pytest.mark.parametrize("body", [
        {"model": "tier-embed", "input": []},
        {"model": "tier-embed", "input": [""]},
        {"model": "tier-embed", "input": ["x"] * 129},
        {"model": "tier-embed", "input": ["x"], "dimensions": 256},
        {"model": "tier-embed", "input": ["x"], "encoding_format": "base64"},
    ])
    def test_a_bad_body_is_422_before_anything_resolves(
        self, client, db, org, provider, body
    ):
        r = client.post("/v1/embeddings", headers=org["key"], json=body)
        assert r.status_code == 422, r.text
        assert provider["calls"] == []
        assert _rows(db, org["id"]) == []

    def test_no_key_for_the_vendor_is_503_and_no_call(self, client, db, org, provider):
        with db.begin() as c:
            live = c.execute(text(
                "SELECT count(*) FROM provider_credential "
                "WHERE provider = 'aimlapi' AND revoked_at IS NULL")).scalar_one()
        if live:
            pytest.skip("a live aimlapi row from another suite is present")
        r = _embed(client, org["key"])
        assert r.status_code == 503, r.text
        assert provider["calls"] == []


@_DB
class TestTheMeterRecordsAndDrawsNothing:
    """Item 5. One row with the tokens, `absorbed`, and no ledger line."""

    def test_a_served_call_records_tokens_and_bills_zero(
        self, client, db, org, aimlapi_key
    ):
        r = _embed(client, org["key"])
        assert r.status_code == 200, r.text
        rows = _rows(db, org["id"])
        assert len(rows) == 1
        row = rows[0]
        assert (row.task, row.tier, row.model) == ("embed", "tier-embed", SEEDED_MODEL)
        assert row.prompt_tokens == PROMPT_TOKENS
        assert row.unit == "tokens"
        assert row.billed_credits == 0
        assert row.refusal_reason is None and row.metering_fault is None
        assert _ledger(db, org["id"]) == []

    def test_a_PRICED_card_would_draw_so_the_zero_is_the_cards(
        self, client, db, org, aimlapi_key
    ):
        """The mutation guard. A later priced card bills, so the zero above
        comes from `absorbed` and not from a door that never rates."""
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_rate_card (tier, task, unit, credits_per_unit, "
                " input_credits_per_1m, output_credits_per_1m, "
                " cached_input_credits_per_1m, pricing_mode, effective_from) "
                "VALUES ('tier-embed', 'embed', 'tokens', 0, 1000000, 0, 0, "
                " 'priced', now())"))
        try:
            r = _embed(client, org["key"])
            assert r.status_code == 200, r.text
            rows = _rows(db, org["id"])
            assert len(rows) == 1 and rows[0].billed_credits == Decimal(PROMPT_TOKENS)
        finally:
            with db.begin() as c:
                c.execute(text(
                    "DELETE FROM tier_rate_card WHERE tier = 'tier-embed' "
                    "AND task = 'embed' AND pricing_mode = 'priced'"))
