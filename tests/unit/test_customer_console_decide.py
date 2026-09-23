"""WS-31 CP-13a — the `decide` task, the native handler and the door.

Spec: ``project-docs/specs/customer_console.md`` §6A.14 (D75) — clauses 1 to
14, the wire contract, and the "Done when" table rows 1-10, 14 and 15. The
handler seam is §6A.10b.

🔴 **What this suite proves, in one line each.**

* `033` seeds the task in tokens and `tier-decide` HIDDEN, and nothing else.
* ``handlers.py`` imports no tenant package.
* ``native_typesafe`` is in both invocation sets, and a typo is refused.
* Our ``boolean`` is the vendor's ``noul``, and neither ``noul`` nor
  ``legend`` reaches the caller. A RECORDED vendor body goes in and our shape
  comes out.
* Input tokens land in ``prompt_tokens``, and a ZERO output price costs zero.
  The ``usage_event`` row a real call wrote says so (R8).
* Clause 13 refuses BEFORE the vendor call. The fake records no call.
* A vendor 429 stays 429, and a 529 becomes 502.
* The response names the tier and never the model.
* A ``cc_depl_`` key without ``X-CC-Member`` is refused.
* The litellm family still goes to litellm.

⚠️ **The vendor body is RECORDED FROM THE API REFERENCE, not from a live
call.** No key exists yet (§6A.14 owner gates). The live check in §6A.14
proves the shape against the vendor itself.

**R8.** Every database clause runs against a real Postgres 16 through
``tests/unit/_customer_console_ladder.py``. A skipped R8 test proves nothing.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from customer_console import catalog, handlers  # noqa: E402
from customer_console import router as router_mod  # noqa: E402
from customer_console.decide import (  # noqa: E402
    MAX_CHOICE_OPTIONS,
    MAX_CRITERION_CHARS,
    MAX_CRITERION_KEY_CHARS,
    MAX_INSTRUCTIONS_CHARS,
    MAX_QUESTIONS,
    MAX_STATE_TOKENS,
)
from customer_console.handlers import (  # noqa: E402
    DecidePayload,
    NativeProviderError,
    ProviderResult,
    Question,
    TypeSafeHandler,
)
from sqlalchemy import create_engine, text  # noqa: E402

from tests.unit._customer_console_ladder import (  # noqa: E402
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
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

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "apps/services/customer_console/customer_console"

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}

#: The same constant every Console suite uses. `provider_credential` decrypts
#: with the env key at READ time.
ENC_KEY = "test-encryption-key-not-a-real-one"

#: The pinned id (clause 6). The prefix names the credential.
JEV = "typesafe/jev-1.13.0"

#: The label every row this suite writes carries, so a teardown removes ITS
#: OWN row and never a row a sibling suite owns.
_FENCE_LABEL = "decide-fence"

#: The vendor's published prices (§6A.14 facts table). Output is FREE, and
#: clause 9 says the profile holds ZERO for it, never NULL.
INPUT_PER_1M = Decimal("0.042")
OUTPUT_PER_1M = Decimal("0")

INPUT_TOKENS = 312
OUTPUT_TOKENS = 4

#: What the recorded call costs us: input x 0.042 / 1e6, at 8 places.
CALL_COST = (Decimal(INPUT_TOKENS) * INPUT_PER_1M / Decimal(1_000_000)).quantize(
    Decimal("0.00000001"))

#: A TypeSafe System One response, built from the API reference (read
#: 2026-09-23). ⚠️ It carries the three things that must NOT leak: the word
#: `noul`, a score's `legend`, and the model id the vendor echoes.
RECORDED = {
    "model": "jev-1.13.0",
    "answers": {
        "cold": {"type": "noul", "probability": 0.93},
        "rule": {
            "type": "choice",
            "choice": "fyi",
            "probabilities": {"needs_reply": 0.08, "fyi": 0.87, "none": 0.05},
            "confidence": 0.81,
        },
        "urgency": {
            "type": "score",
            "score": "high",
            "legend": {"low": "can wait", "mid": "this week", "high": "today"},
            "probabilities": {"low": 0.1, "mid": 0.2, "high": 0.7},
            "confidence": 0.66,
        },
    },
    "usage": {"input_tokens": INPUT_TOKENS, "output_tokens": OUTPUT_TOKENS},
}

#: The request that matches :data:`RECORDED`, in OUR words.
QUESTIONS = {
    "cold": {
        "type": "boolean",
        "instructions": "Is this an unsolicited sales email from a stranger?",
        "criteria": {"true": "a cold pitch", "false": "anything else"},
    },
    "rule": {
        "type": "choice",
        "instructions": "Which rule fits this email best?",
        "criteria": {"needs_reply": "a reply", "fyi": "read only", "none": "no rule"},
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is it?",
        "criteria": {"low": "can wait", "mid": "this week", "high": "today"},
    },
}

STATE = "From: a@b.example\nSubject: pumps\n\nSixteen pumps are overdue."


def _questions() -> dict[str, Question]:
    return {qid: Question(type=q["type"], instructions=q["instructions"],
                          criteria=q["criteria"]) for qid, q in QUESTIONS.items()}


class _Vendor:
    """A recorded vendor behind ``httpx.MockTransport``. No network."""

    def __init__(self, status: int = 200, body: object = RECORDED) -> None:
        self.status = status
        self.body = body
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def _answer(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json=self.body)
        return httpx.MockTransport(_answer)

    def sent(self) -> dict:
        return json.loads(self.requests[-1].content)


def _run_handler(vendor: _Vendor, *, api_base: str | None = None) -> ProviderResult:
    handler = TypeSafeHandler(transport=vendor.transport())
    payload = DecidePayload(model=JEV, state=STATE, questions=_questions(),
                            api_key="sk-typesafe-test", api_base=api_base)
    return asyncio.run(handler.call("decide", payload))


# ── Row 3: the plane boundary ────────────────────────────────────────────────

class TestTheHandlerImportsNoTenantPackage:
    @pytest.mark.parametrize("module", ["handlers.py", "decide.py"])
    def test_no_import_under_packages_acb(self, module):
        """§6A.10b clause 1. The Console is cross-tenant, and `acb_*` is the
        tenant plane. A module that imported one would read tenant config."""
        tree = ast.parse((CONSOLE / module).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not [m for m in imported if m.split(".")[0].startswith("acb_")]

    def test_no_vendor_sdk_is_imported(self):
        """Clause 5: httpx directly, never the vendor's package."""
        source = (CONSOLE / "handlers.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert not [r for r in roots if "typesafe" in r.lower()]


# ── Row 4: the two invocation sets ───────────────────────────────────────────

class TestNativeTypesafeIsInBothSets:
    def test_the_operator_may_write_it(self):
        assert "native_typesafe" in catalog.KNOWN_INVOCATIONS
        assert catalog.check_invocation("native_typesafe") == "native_typesafe"

    def test_the_router_may_call_it(self):
        assert "native_typesafe" in router_mod.SERVING_INVOCATIONS
        # The strict subset stands: `aembedding` still has no door.
        assert router_mod.SERVING_INVOCATIONS < catalog.KNOWN_INVOCATIONS

    def test_every_native_serving_verb_has_a_handler(self):
        """A serving name with no handler would 502 on its first call."""
        native = {v for v in router_mod.SERVING_INVOCATIONS
                  if v.startswith(router_mod.NATIVE_PREFIX)}
        assert native == set(handlers.NATIVE_HANDLERS)

    def test_a_typo_is_refused(self):
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_invocation("native_typesafo")

    def test_every_registered_handler_answers_the_same_call(self):
        """§6A.10b clause 2: one `call(task, payload)` per handler."""
        import inspect

        for name, handler in handlers.NATIVE_HANDLERS.items():
            params = list(inspect.signature(handler.call).parameters)
            assert params == ["task", "payload"], name
            assert inspect.iscoroutinefunction(handler.call), name


# ── Row 5: the vocabulary stays inside the handler ───────────────────────────

class TestTheHandlerMapsTheVendorShape:
    def test_boolean_goes_out_as_noul(self):
        vendor = _Vendor()
        _run_handler(vendor)
        sent = vendor.sent()
        assert sent["questions"]["cold"]["type"] == "noul"
        assert sent["questions"]["rule"]["type"] == "choice"
        assert sent["questions"]["urgency"]["type"] == "score"
        assert sent["questions"]["cold"]["criteria"] == QUESTIONS["cold"]["criteria"]

    def test_the_vendor_sees_the_bare_model_id_and_our_key(self):
        """Clause 6: the prefix names OUR credential, and the vendor must not
        see it."""
        vendor = _Vendor()
        _run_handler(vendor)
        request = vendor.requests[-1]
        assert vendor.sent()["model"] == "jev-1.13.0"
        assert vendor.sent()["state"] == STATE
        assert request.method == "POST"
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer sk-typesafe-test"

    def test_the_credential_api_base_wins(self):
        vendor = _Vendor()
        _run_handler(vendor, api_base="https://proxy.example/")
        assert str(vendor.requests[-1].url) == "https://proxy.example/v1/systemone"

    def test_a_recorded_response_comes_back_in_OUR_shape(self):
        result = _run_handler(_Vendor())
        assert result.body["answers"] == {
            "cold": {"type": "boolean", "probability": 0.93},
            "rule": {
                "type": "choice",
                "choice": "fyi",
                "probabilities": {"needs_reply": 0.08, "fyi": 0.87, "none": 0.05},
                "confidence": 0.81,
            },
            "urgency": {
                "type": "score",
                "score": "high",
                "probabilities": {"low": 0.1, "mid": 0.2, "high": 0.7},
                "confidence": 0.66,
            },
        }

    def test_neither_noul_nor_legend_nor_the_model_leaks(self):
        body = json.dumps(_run_handler(_Vendor()).body).lower()
        assert "noul" not in body
        assert "legend" not in body
        assert "jev" not in body

    def test_a_bare_probability_is_a_boolean_answer_too(self):
        recorded = json.loads(json.dumps(RECORDED))
        recorded["answers"]["cold"] = 0.2
        result = _run_handler(_Vendor(body=recorded))
        assert result.body["answers"]["cold"] == {"type": "boolean", "probability": 0.2}

    def test_the_usage_is_TOKENS_and_carries_no_quantity(self):
        """Clause 8. Input is the prompt, and a quantity would send the meter
        down the per-unit branch, where `tokens` has no column."""
        result = _run_handler(_Vendor())
        assert result.usage.prompt_tokens == INPUT_TOKENS
        assert result.usage.completion_tokens == OUTPUT_TOKENS
        assert result.quantity is None
        assert result.body["usage"] == {"input_tokens": INPUT_TOKENS,
                                        "output_tokens": OUTPUT_TOKENS}

    def test_a_missing_answer_is_an_error_and_never_a_guess(self):
        recorded = json.loads(json.dumps(RECORDED))
        del recorded["answers"]["rule"]
        with pytest.raises(NativeProviderError) as exc:
            _run_handler(_Vendor(body=recorded))
        assert exc.value.status_code is None


# ── Row 6, the database-free half: a zero output price costs zero ────────────

class TestAZeroOutputPriceCostsZero:
    def test_zero_is_a_price_and_NULL_is_not(self):
        """Clause 9. `vendor_cost_usd` answers None when completion tokens are
        above zero and the output price is NULL. Output is free, so the
        profile carries 0, and the call costs its input alone."""
        usage = router_mod.ExtractedUsage(prompt_tokens=INPUT_TOKENS,
                                          completion_tokens=OUTPUT_TOKENS)
        assert router_mod.vendor_cost_usd(
            usage, input_per_1m=INPUT_PER_1M, output_per_1m=Decimal(0),
            cached_per_1m=None) == CALL_COST
        assert router_mod.vendor_cost_usd(
            usage, input_per_1m=INPUT_PER_1M, output_per_1m=None,
            cached_per_1m=None) is None


# ── Row 8, the database-free half: the error carries its status ──────────────

class TestTheHandlerErrorCarriesTheStatus:
    @pytest.mark.parametrize("status", [401, 422, 429, 529])
    def test_status_code_is_on_the_error(self, status):
        """Clause 12. `walk_chain` reads `exc.status_code`. Without it a 429
        reads as None and reaches the caller as a 502."""
        with pytest.raises(NativeProviderError) as exc:
            _run_handler(_Vendor(status=status, body={"error": "quoted request"}))
        assert exc.value.status_code == status
        assert "quoted request" not in str(exc.value)

    def test_a_dropped_connection_carries_no_status(self):
        def _drop(request):
            raise httpx.ConnectError("refused", request=request)

        handler = TypeSafeHandler(transport=httpx.MockTransport(_drop))
        payload = DecidePayload(model=JEV, state=STATE, questions=_questions(),
                                api_key="k")
        with pytest.raises(NativeProviderError) as exc:
            asyncio.run(handler.call("decide", payload))
        assert exc.value.status_code is None


# ── Row 10, the database-free half: the default call picks the family ───────

class TestTheDefaultCallPicksTheFamily:
    def test_the_default_provider_call_is_the_production_one(self):
        """The dispatch lives INSIDE `_PROVIDER_CALL[0]` (artefact 5). If the
        default were `_litellm_call` again, a native verb would reach litellm.
        """
        assert router_mod._default_provider_call is not router_mod._litellm_call

    def test_a_litellm_verb_still_goes_to_litellm(self, monkeypatch):
        import litellm
        seen: list = []

        async def _fake(**kwargs):
            seen.append(kwargs)
            return "ok"

        monkeypatch.setattr(litellm, "atranscription", _fake, raising=False)
        answer = asyncio.run(router_mod._default_provider_call(
            invocation="atranscription", model="m"))
        assert answer == "ok"
        assert seen == [{"model": "m"}]

    def test_a_native_verb_goes_to_its_handler_and_never_to_litellm(self, monkeypatch):
        import litellm
        called: list = []

        async def _never(**kwargs):
            raise AssertionError("a native verb reached litellm")

        class _Handler:
            async def call(self, task, payload):
                called.append((task, payload.model))
                return ProviderResult(body={}, usage=router_mod.ExtractedUsage())

        monkeypatch.setattr(litellm, "acompletion", _never, raising=False)
        monkeypatch.setitem(handlers.NATIVE_HANDLERS, "native_typesafe", _Handler())
        payload = DecidePayload(model=JEV, state=STATE, questions=_questions(),
                                api_key="k")
        asyncio.run(router_mod._default_provider_call(
            invocation="native_typesafe", task="decide", model=JEV, payload=payload))
        assert called == [("decide", JEV)]

    def test_litellm_refuses_a_native_verb_by_name(self):
        """`native_typesafe` is in the serving set, and litellm holds no such
        attribute. Reaching `_litellm_call` with it is a wiring bug."""
        with pytest.raises(router_mod.UnservableInvocation):
            asyncio.run(router_mod._litellm_call(invocation="native_typesafe"))


# ── The database: rows 1, 6, 7, 8, 9 and 14 ──────────────────────────────────

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
def default_call():
    """The PRODUCTION provider call, installed for one test.

    ⚠️ Restored afterwards, because `_PROVIDER_CALL` is process-global and a
    sibling suite may have left a stub in it.
    """
    original = router_mod._PROVIDER_CALL[0]
    router_mod.set_provider_call(router_mod._default_provider_call)
    yield
    router_mod.set_provider_call(original)


@pytest.fixture
def client(monkeypatch, db, default_call):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    from customer_console.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def org(client, db):
    """A provisioned org on the shared box, its owner, and a live org key."""
    with db.begin() as c:
        ensure_deployment(c)
    slug = f"dcd-{uuid.uuid4().hex[:8]}"
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
def vendor(monkeypatch):
    """The real `TypeSafeHandler`, answering from a recorded body."""
    recorded = _Vendor()
    monkeypatch.setitem(handlers.NATIVE_HANDLERS, "native_typesafe",
                        TypeSafeHandler(transport=recorded.transport()))
    return recorded


def _bind_decide(db, models: list[str]):
    """Declare, profile and bind ``models`` as ranks 1..n of `tier-decide`.

    ⚠️ **The binding carries its OWN ``effective_from``**, a random instant on
    2026-01-01, and the teardown deletes exactly those rows. The scratch
    database is shared across sessions, so a teardown by tier alone could
    delete another session's binding.
    """
    from datetime import UTC, datetime, timedelta

    since = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
        microseconds=uuid.uuid4().int % 86_400_000_000)
    with db.begin() as c:
        for rank, model in enumerate(models, start=1):
            c.execute(text(
                "INSERT INTO model_capability (model, task, invocation, streams) "
                "VALUES (:m, 'decide', 'native_typesafe', FALSE)"), {"m": model})
            c.execute(text(
                "INSERT INTO model_profile (model, vendor_input_per_1m_usd, "
                " vendor_output_per_1m_usd, context_window) "
                "VALUES (:m, :i, :o, 64000)"),
                {"m": model, "i": INPUT_PER_1M, "o": OUTPUT_PER_1M})
            c.execute(text(
                "INSERT INTO tier_binding (tier, model, task, rank, effective_from) "
                "VALUES ('tier-decide', :m, 'decide', :r, :f)"),
                {"m": model, "r": rank, "f": since})
        c.execute(text(
            "INSERT INTO provider_credential (provider, secret_enc, label) "
            "VALUES ('typesafe', :s, :l) ON CONFLICT DO NOTHING"),
            {"s": router_mod.encrypt_secret("sk-typesafe-fence"), "l": _FENCE_LABEL})
    return since


def _unbind_decide(db, models: list[str], since) -> None:
    with db.begin() as c:
        for model in models:
            c.execute(text(
                "DELETE FROM tier_binding WHERE tier = 'tier-decide' "
                "AND task = 'decide' AND model = :m AND effective_from = :f"),
                {"m": model, "f": since})
            c.execute(text("DELETE FROM model_capability WHERE model = :m"), {"m": model})
            c.execute(text("DELETE FROM model_profile WHERE model = :m"), {"m": model})
        c.execute(text(
            "DELETE FROM provider_credential WHERE provider = 'typesafe' AND label = :l"),
            {"l": _FENCE_LABEL})


@pytest.fixture
def bound(db, vendor):
    """What CP-13b's operator does by hand, for ONE test: declare Jev, fill
    the profile (output ZERO, clause 9), bind `tier-decide`, install a key.

    🔴 **The migration seeds none of this, on purpose.** So every row here is
    removed again, and the "ships dark" fence below stays true.
    """
    since = _bind_decide(db, [JEV])
    yield vendor
    _unbind_decide(db, [JEV], since)


#: A second step on the same vendor, for the failover fence.
JEV_2 = "typesafe/jev-preview"


@pytest.fixture
def bound_two(db, vendor):
    """`tier-decide` bound to a TWO-step chain, both steps on one vendor."""
    since = _bind_decide(db, [JEV, JEV_2])
    yield vendor
    _unbind_decide(db, [JEV, JEV_2], since)


@pytest.fixture
def recording_fake():
    """A fake set by `set_provider_call`. It records and answers our shape."""
    state: dict = {"calls": []}

    async def _fake(**kwargs):
        state["calls"].append(kwargs)
        return ProviderResult(
            body={"answers": {}, "usage": {"input_tokens": 1, "output_tokens": 0}},
            usage=router_mod.ExtractedUsage(prompt_tokens=1))

    original = router_mod._PROVIDER_CALL[0]
    router_mod.set_provider_call(_fake)
    yield state
    router_mod.set_provider_call(original)


def _decide(client, key, *, tier="tier-decide", questions=None, state=STATE,
            headers=None, **fields):
    body = {"tier": tier, "state": state,
            "questions": QUESTIONS if questions is None else questions, **fields}
    return client.post("/v1/decide", headers={**key, **(headers or {})}, json=body)


_ROW = (
    "SELECT task, tier, model, prompt_tokens, completion_tokens, quantity, "
    "       billed_credits, provider_cost_usd, metering_fault, refusal_reason "
    "FROM usage_event WHERE organization_id = CAST(:o AS uuid)"
)


def _rows(db, org_id: str) -> list:
    with db.begin() as c:
        return c.execute(text(_ROW), {"o": org_id}).all()


@_DB
class TestTheMigrationSeedsTheTaskAndAHiddenTier:
    """Row 1 and row 2."""

    def test_decide_is_a_task_priced_in_tokens(self, db):
        with db.begin() as c:
            unit = c.execute(text(
                "SELECT natural_unit FROM task_catalog WHERE slug = 'decide'")).scalar_one()
        assert unit == "tokens"

    def test_tier_decide_serves_decide_and_the_customer_does_not_see_it(self, db):
        with db.begin() as c:
            row = c.execute(text(
                "SELECT task, customer_visible FROM tier_catalog "
                "WHERE slug = 'tier-decide'")).one()
        assert row.task == "decide"
        assert row.customer_visible is False

    def test_the_ladder_seeds_no_binding_capability_profile_or_rate(self, db):
        """The operator makes those writes (CP-13b). A seeded binding would
        serve the day the key goes in."""
        with db.begin() as c:
            counts = {
                "binding": c.execute(text(
                    "SELECT count(*) FROM tier_binding WHERE tier = 'tier-decide' "
                    "OR task = 'decide'")).scalar_one(),
                "capability": c.execute(text(
                    "SELECT count(*) FROM model_capability WHERE task = 'decide'")
                ).scalar_one(),
                "profile": c.execute(text(
                    "SELECT count(*) FROM model_profile WHERE model LIKE 'typesafe/%'")
                ).scalar_one(),
                "rate": c.execute(text(
                    "SELECT count(*) FROM tier_rate_card WHERE tier = 'tier-decide'")
                ).scalar_one(),
            }
        assert counts == {"binding": 0, "capability": 0, "profile": 0, "rate": 0}

    def test_a_replay_changes_nothing(self, db):
        with db.begin() as c:
            apply_ladder(c)
            tasks = c.execute(text(
                "SELECT count(*) FROM task_catalog WHERE slug = 'decide'")).scalar_one()
            tiers = c.execute(text(
                "SELECT count(*) FROM tier_catalog WHERE slug = 'tier-decide'")).scalar_one()
        assert (tasks, tiers) == (1, 1)


@_DB
class TestTheCapabilityWrite:
    """Row 4, through the operator route, and clause 14."""

    def test_a_typo_is_refused_by_the_capability_write(self, client):
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": JEV, "task": "decide", "invocation": "native_typesafo"})
        assert r.status_code == 400, r.text
        assert "native_typesafo" in r.json()["detail"]

    @pytest.mark.parametrize("task, verb, words", [
        # A decision handed to a chat verb would answer 502 on the first call.
        ("decide", "acompletion", "takes only a native invocation"),
        # A chat request sent to a decision vendor.
        ("chat", "native_typesafe", "serves only decide"),
        ("transcribe", "native_typesafe", "serves only decide"),
    ])
    def test_the_verb_must_fit_the_task(self, client, task, verb, words):
        """Fix after review: `check_invocation_for_task` refuses the pair at
        declare time, in both directions, and names the rule."""
        model = f"typesafe/pair-{uuid.uuid4().hex[:6]}"
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": task, "invocation": verb})
        assert r.status_code == 400, r.text
        assert words in r.json()["detail"]

    def test_the_legal_pair_is_accepted(self, client, db):
        model = f"typesafe/pair-{uuid.uuid4().hex[:6]}"
        try:
            r = client.post("/catalog/capabilities", headers=OP, json={
                "model": model, "task": "decide", "invocation": "native_typesafe"})
            assert r.status_code == 200, r.text
        finally:
            with db.begin() as c:
                c.execute(text("DELETE FROM model_capability WHERE model = :m"),
                          {"m": model})

    def test_a_decide_capability_may_not_stream(self, client):
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": JEV, "task": "decide", "invocation": "native_typesafe",
            "streams": True})
        assert r.status_code == 400, r.text


@_DB
class TestTheDoorShipsDark:
    def test_an_unbound_tier_is_a_400_and_writes_one_refusal_row(
        self, client, db, org, recording_fake
    ):
        """Nothing binds `tier-decide` until an operator does. The door says
        so, and the vendor sees nothing."""
        r = _decide(client, org["key"])
        assert r.status_code == 400, r.text
        assert recording_fake["calls"] == []
        rows = _rows(db, org["id"])
        assert [(row.task, row.refusal_reason) for row in rows] == [
            ("decide", "tier_unknown")]


@_DB
class TestAServedCallIsMeteredInTokens:
    """Row 6 and row 9. R8: the row a real call wrote."""

    def test_input_tokens_land_in_prompt_tokens_and_output_costs_zero(
        self, client, db, org, bound
    ):
        r = _decide(client, org["key"])
        assert r.status_code == 200, r.text
        rows = _rows(db, org["id"])
        assert len(rows) == 1
        row = rows[0]
        assert (row.task, row.tier, row.model) == ("decide", "tier-decide", JEV)
        assert row.prompt_tokens == INPUT_TOKENS
        assert row.completion_tokens == OUTPUT_TOKENS
        # A token call carries no quantity (clause 8).
        assert row.quantity is None
        # The meter READ the call. Zero prompt tokens would say otherwise.
        assert row.metering_fault is None
        assert row.refusal_reason is None
        # Clause 9: output is free and costs ZERO, so the input alone.
        assert Decimal(row.provider_cost_usd) == CALL_COST
        # Clause 10: the migration priced nothing, so the call bills zero.
        assert Decimal(row.billed_credits) == 0

    def test_the_response_names_the_tier_and_never_the_model(
        self, client, org, bound
    ):
        r = _decide(client, org["key"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tier"] == "tier-decide"
        assert body["answers"]["cold"] == {"type": "boolean", "probability": 0.93}
        assert body["usage"] == {"input_tokens": INPUT_TOKENS,
                                 "output_tokens": OUTPUT_TOKENS}
        assert body["request_id"].startswith("rtr-")
        lowered = r.text.lower()
        for word in ("jev", "typesafe", "noul", "legend"):
            assert word not in lowered, word

    def test_the_vendor_saw_the_credential_and_the_bare_model(
        self, client, org, bound
    ):
        assert _decide(client, org["key"]).status_code == 200
        request = bound.requests[-1]
        assert request.headers["Authorization"] == "Bearer sk-typesafe-fence"
        assert bound.sent()["model"] == "jev-1.13.0"
        assert bound.sent()["questions"]["cold"]["type"] == "noul"

    def test_a_fake_set_by_set_provider_call_still_sees_the_call(
        self, client, org, bound, recording_fake
    ):
        """Artefact 5. The dispatch is INSIDE the seam, so a fake replaces
        it whole and the real vendor is never reached."""
        r = _decide(client, org["key"])
        assert r.status_code == 200, r.text
        assert len(recording_fake["calls"]) == 1
        call = recording_fake["calls"][0]
        assert call["invocation"] == "native_typesafe"
        assert call["task"] == "decide"
        assert isinstance(call["payload"], DecidePayload)
        assert bound.requests == []


@_DB
class TestClause13RefusesBeforeTheVendor:
    """Row 7. Each breach is a 400 that names the rule, the fake records NO
    call, and no usage row is written."""

    @pytest.mark.parametrize("questions, words", [
        ({f"q{i}": {"type": "boolean", "instructions": "x"}
          for i in range(MAX_QUESTIONS + 1)}, f"at most {MAX_QUESTIONS} questions"),
        ({}, "one or more questions"),
        ({"q": {"type": "noul", "instructions": "x"}}, "type must be one of"),
        ({"q": {"type": "choice", "instructions": "x",
                "criteria": {f"o{i}": "o" for i in range(MAX_CHOICE_OPTIONS + 1)}}},
         f"at most {MAX_CHOICE_OPTIONS} options"),
        ({"q": {"type": "score", "instructions": "x", "criteria": {"one": "1"}}},
         "from 2 to 10 levels"),
        ({"q": {"type": "score", "instructions": "x",
                "criteria": {f"l{i}": "l" for i in range(11)}}},
         "from 2 to 10 levels"),
    ])
    def test_a_breach_is_a_400_naming_the_rule(
        self, client, db, org, recording_fake, questions, words
    ):
        r = _decide(client, org["key"], questions=questions)
        assert r.status_code == 400, r.text
        assert words in r.json()["detail"]
        assert recording_fake["calls"] == []
        assert _rows(db, org["id"]) == []

    def test_an_oversize_state_is_refused(self, client, db, org, recording_fake):
        state = "x" * (MAX_STATE_TOKENS * 4 + 4)
        r = _decide(client, org["key"], state=state)
        assert r.status_code == 400, r.text
        assert f"the limit is {MAX_STATE_TOKENS}" in r.json()["detail"]
        assert recording_fake["calls"] == []
        assert _rows(db, org["id"]) == []

    def test_a_state_under_the_limit_plus_a_long_question_is_refused(
        self, client, db, org, recording_fake
    ):
        """Fix after review. The vendor window is 32k for `state` PLUS the
        longest question. A state of about 31k tokens passes alone, and one
        long question takes the pair over."""
        state = "x" * (31_000 * 4)
        questions = {
            "q": {"type": "choice", "instructions": "i" * MAX_INSTRUCTIONS_CHARS,
                  "criteria": {f"o{i}": "d" * 100 for i in range(10)}},
        }
        r = _decide(client, org["key"], state=state, questions=questions)
        assert r.status_code == 400, r.text
        assert "state plus the longest question" in r.json()["detail"]
        assert recording_fake["calls"] == []
        assert _rows(db, org["id"]) == []

    @pytest.mark.parametrize("question, words", [
        ({"type": "boolean", "instructions": "i" * (MAX_INSTRUCTIONS_CHARS + 1)},
         f"instructions take at most {MAX_INSTRUCTIONS_CHARS} characters"),
        ({"type": "boolean", "instructions": "x",
          "criteria": {"true": "d" * (MAX_CRITERION_CHARS + 1), "false": "no"}},
         f"takes at most {MAX_CRITERION_CHARS} characters"),
        ({"type": "choice", "instructions": "x",
          "criteria": {"k" * (MAX_CRITERION_KEY_CHARS + 1): "d", "b": "d"}},
         f"a criterion key takes at most {MAX_CRITERION_KEY_CHARS} characters"),
    ])
    def test_a_long_field_is_a_400_and_not_a_422(
        self, client, db, org, recording_fake, question, words
    ):
        r = _decide(client, org["key"], questions={"q": question})
        assert r.status_code == 400, r.text
        assert words in r.json()["detail"]
        assert recording_fake["calls"] == []
        assert _rows(db, org["id"]) == []

    def test_the_limits_are_at_the_edge_and_not_inside_it(
        self, client, org, bound
    ):
        """The mutation guard: a limit off by one would refuse this. The
        state fills the window exactly, once the question is counted."""
        from customer_console.decide import DecideQuestion, question_chars

        questions = {
            "q": {"type": "choice", "instructions": "i" * MAX_INSTRUCTIONS_CHARS,
                  "criteria": {f"o{i}": "o" for i in range(MAX_CHOICE_OPTIONS)}},
        }
        used = question_chars(DecideQuestion(**questions["q"]))
        bound.body = {"answers": {"q": {"choice": "o1", "probabilities": {},
                                        "confidence": 0.5}},
                      "usage": {"input_tokens": 9, "output_tokens": 1}}
        r = _decide(client, org["key"], questions=questions,
                    state="x" * (MAX_STATE_TOKENS * 4 - used))
        assert r.status_code == 200, r.text


@_DB
class TestAVendorFailureGoesThroughTheOneMapping:
    """Row 8. A 429 stays 429 and a 529 becomes 502 (clause 12)."""

    @pytest.mark.parametrize("vendor_status, ours", [(429, 429), (529, 502), (401, 502)])
    def test_the_status_the_caller_reads(
        self, client, db, org, bound, vendor_status, ours
    ):
        bound.status = vendor_status
        bound.body = {"error": "the vendor quotes the request here"}
        r = _decide(client, org["key"])
        assert r.status_code == ours, r.text
        assert r.json()["detail"] == "upstream provider error"
        assert "quotes the request" not in r.text
        # A broken vendor is not a customer wall, so no row (§8.1).
        assert _rows(db, org["id"]) == []


@_DB
class TestAnUnreadable200IsTerminal:
    """Fix after review. A 200 we cannot read has been PAID for, so the
    chain stops, the caller reads 502, and one alarm names the call."""

    def test_the_second_step_is_never_called(
        self, client, db, org, bound_two, caplog
    ):
        bound_two.body = {"answers": "not a map", "usage": {"input_tokens": 5}}
        with caplog.at_level("ERROR", logger="customer_console.handlers"):
            r = _decide(client, org["key"])
        assert r.status_code == 502, r.text
        assert r.json()["detail"] == "upstream provider error"
        # Both steps share one recorded vendor, so the request count is the
        # count of steps the walk tried.
        assert len(bound_two.requests) == 1
        assert bound_two.sent()["model"] == "jev-1.13.0"
        alarms = [rec for rec in caplog.records
                  if rec.getMessage() == "handlers.vendor_unreadable"]
        assert len(alarms) == 1
        alarm = alarms[0]
        assert alarm.upstream_status == 200
        assert alarm.router_org == org["id"]
        assert alarm.router_request.startswith("rtr-")
        # The body and the key never reach the log.
        assert "not a map" not in str(alarm.__dict__)
        assert "sk-typesafe" not in str(alarm.__dict__)
        assert _rows(db, org["id"]) == []


class TestTheTerminalRule:
    """The database-free half of the unreadable-200 fix."""

    def test_a_body_that_is_not_json_is_terminal_too(self):
        def _html(request):
            return httpx.Response(200, text="<html>oops</html>")

        handler = TypeSafeHandler(transport=httpx.MockTransport(_html))
        payload = DecidePayload(model=JEV, state=STATE, questions=_questions(),
                                api_key="k")
        with pytest.raises(NativeProviderError) as exc:
            asyncio.run(handler.call("decide", payload))
        assert exc.value.terminal is True
        assert exc.value.status_code is None

    def test_a_refusal_is_NOT_terminal(self):
        """A 529 must still fail over. Only the unreadable 200 stops the walk."""
        with pytest.raises(NativeProviderError) as exc:
            _run_handler(_Vendor(status=529, body={}))
        assert exc.value.terminal is False

    def test_walk_chain_stops_on_a_terminal_error(self):
        steps = [router_mod.ResolvedTier(tier="tier-decide", model=m, task="decide")
                 for m in (JEV, JEV_2)]
        tried: list[str] = []

        async def _attempt(step):
            tried.append(step.model)
            raise NativeProviderError(None, "unreadable", terminal=True)

        with pytest.raises(router_mod.UpstreamFailed):
            asyncio.run(router_mod.walk_chain(steps, _attempt))
        assert tried == [JEV]

    def test_walk_chain_still_fails_over_on_a_retryable_error(self):
        steps = [router_mod.ResolvedTier(tier="tier-decide", model=m, task="decide")
                 for m in (JEV, JEV_2)]
        tried: list[str] = []

        async def _attempt(step):
            tried.append(step.model)
            if step.model == JEV:
                raise NativeProviderError(529, "overloaded")
            return "ok"

        answer, served = asyncio.run(router_mod.walk_chain(steps, _attempt))
        assert (answer, served.model, tried) == ("ok", JEV_2, [JEV, JEV_2])


def test_the_payload_repr_never_prints_the_key():
    payload = DecidePayload(model=JEV, state=STATE, questions=_questions(),
                            api_key="sk-secret-never-printed")
    assert "sk-secret-never-printed" not in repr(payload)


@_DB
class TestTheDeploymentArm:
    """Row 14. The door takes `ServingCaller`, never `KeyCaller`."""

    def _key(self, db, capabilities):
        with db.begin() as c:
            deployment_id = ensure_deployment(c)
            return mint_deployment_key(c, deployment_id=deployment_id,
                                       capabilities=capabilities)

    def test_a_deployment_key_without_x_cc_member_is_refused(
        self, client, db, org, recording_fake
    ):
        token = self._key(db, ["serve"])
        r = _decide(client, {"Authorization": f"Bearer {token}"})
        assert r.status_code == 400, r.text
        assert "X-CC-Member" in r.json()["detail"]
        assert recording_fake["calls"] == []

    def test_a_deployment_key_and_a_member_are_served(
        self, client, db, org, bound
    ):
        token = self._key(db, ["serve"])
        r = _decide(client, {"Authorization": f"Bearer {token}"},
                    headers={"X-CC-Member": org["owner"]})
        assert r.status_code == 200, r.text
        rows = _rows(db, org["id"])
        assert len(rows) == 1 and rows[0].task == "decide"

    def test_a_key_without_serve_is_403(self, client, db, org):
        token = self._key(db, ["resolve"])
        r = _decide(client, {"Authorization": f"Bearer {token}"},
                    headers={"X-CC-Member": org["owner"]})
        assert r.status_code == 403, r.text
        assert "serve" in r.json()["detail"]


def test_this_suite_is_named_in_the_ci_skip_guard():
    """A new R8 suite that CI does not name still SKIPS silently and reports
    green. This reads the hand-list and fails if the entry is gone."""
    ci = (ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_customer_console_decide.py" in ci
