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

#: A TypeSafe System One response in the DOCUMENTED shape (docs.typesafe.ai
#: /api.md, re-read 2026-09-24 for CP-13h). A `noul` answer is the `noul`
#: field. A score is a level POSITION with index-keyed `legend` and
#: `probabilities`. ⚠️ It carries the three things that must NOT leak: the
#: word `noul`, a score's `legend`, and the model id the vendor echoes.
RECORDED = {
    "model": "jev-1.13.0",
    "answers": {
        "cold": {"type": "noul", "noul": 0.93},
        "rule": {
            "type": "choice",
            "choice": "fyi",
            "probabilities": {"needs_reply": 0.08, "fyi": 0.87, "none": 0.05},
            "confidence": 0.81,
        },
        "urgency": {
            "type": "score",
            "score": 1.8,
            "legend": {"0": "can wait", "1": "this week", "2": "today"},
            "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
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
        # CP-13h: TypeSafe documents score levels as an ORDERED ARRAY, so a
        # map would be a 422. The descriptions go out, lowest first.
        assert sent["questions"]["urgency"]["criteria"] == ["can wait", "this week", "today"]

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
                # The position, the nearest level, and level-keyed
                # probabilities: the ONE meaning (CP-13h).
                "score": 1.8,
                "level": "high",
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


def _bind_decide(db, models: list[str], *, invocation: str = "native_typesafe",
                 provider: str = "typesafe"):
    """Declare, profile and bind ``models`` as ranks 1..n of `tier-decide`.

    ``invocation`` and ``provider`` default to TypeSafe direct. CP-13h binds
    the reseller with ``native_aimlapi`` and ``aimlapi``.

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
                "VALUES (:m, 'decide', :v, FALSE)"), {"m": model, "v": invocation})
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
            "VALUES (:p, :s, :l) ON CONFLICT DO NOTHING"),
            {"p": provider, "s": router_mod.encrypt_secret(f"sk-{provider}-fence"),
             "l": _FENCE_LABEL})
    return since


def _unbind_decide(db, models: list[str], since, *, provider: str = "typesafe") -> None:
    with db.begin() as c:
        for model in models:
            c.execute(text(
                "DELETE FROM tier_binding WHERE tier = 'tier-decide' "
                "AND task = 'decide' AND model = :m AND effective_from = :f"),
                {"m": model, "f": since})
            c.execute(text("DELETE FROM model_capability WHERE model = :m"), {"m": model})
            c.execute(text("DELETE FROM model_profile WHERE model = :m"), {"m": model})
        c.execute(text(
            "DELETE FROM provider_credential WHERE provider = :p AND label = :l"),
            {"p": provider, "l": _FENCE_LABEL})


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
        # CP-13c: the reason is a CODE, so the tenant facade can fall back
        # on it without reading the sentence.
        detail = r.json()["detail"]
        assert detail["reason"] == "tier_unknown"
        assert "tier-decide" in detail["error"]
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
        # CP-13c: a structured reason, and the sentence beside it.
        assert r.json()["detail"]["reason"] == "invalid_request"
        assert words in r.json()["detail"]["error"]
        assert recording_fake["calls"] == []
        assert _rows(db, org["id"]) == []

    def test_an_oversize_state_is_refused(self, client, db, org, recording_fake):
        state = "x" * (MAX_STATE_TOKENS * 4 + 4)
        r = _decide(client, org["key"], state=state)
        assert r.status_code == 400, r.text
        assert f"the limit is {MAX_STATE_TOKENS}" in r.json()["detail"]["error"]
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
        assert "state plus the longest question" in r.json()["detail"]["error"]
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
        assert r.json()["detail"]["reason"] == "invalid_request"
        assert words in r.json()["detail"]["error"]
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


# ── CP-13b: "Try a decision" on /tiers (§6A.14, row 16) ───────────────────

def _operator(db, role: str) -> tuple[dict[str, str], str]:
    """An operator holding *role*, with a live session. Returns the header
    and the email, so a test can find the audit row that names them."""
    from datetime import UTC, datetime, timedelta

    from customer_console import operator_sessions, store

    email = f"try-{role}-{uuid.uuid4().hex[:8]}@fracktal.in"
    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with db.begin() as c:
        row = c.execute(text(
            "INSERT INTO operator (email, role) VALUES (:e, :r) RETURNING id"),
            {"e": email, "r": role}).first()
        store.operator_session_insert(
            c, operator_id=str(row[0]), prefix=issued.prefix,
            key_hash=issued.key_hash, expires_at=now + timedelta(hours=12))
    return {"Authorization": f"Bearer {issued.token}"}, email


def _try(client, headers, *, questions=None, state=STATE):
    return client.post("/catalog/decide/try", headers=headers, json={
        "state": state, "questions": QUESTIONS if questions is None else questions})


def _usage_count(db) -> int:
    with db.begin() as c:
        return c.execute(text("SELECT count(*) FROM usage_event")).scalar_one()


def _try_audits(db, actor: str) -> list:
    with db.begin() as c:
        return c.execute(text(
            "SELECT organization_id, detail FROM control_audit "
            "WHERE action = 'catalog.decide_try' AND actor = :a"), {"a": actor}).all()


@_DB
class TestTryADecision:
    """Row 16. The operator route answers, meters nothing, and audits once."""

    def test_an_admin_sees_the_answer_tokens_latency_and_cost(
        self, client, db, bound
    ):
        headers, email = _operator(db, "admin")
        before = _usage_count(db)
        r = _try(client, headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tier"] == "tier-decide"
        assert body["model"] == JEV
        assert body["answers"]["cold"] == {"type": "boolean", "probability": 0.93}
        assert body["usage"] == {"input_tokens": INPUT_TOKENS,
                                 "output_tokens": OUTPUT_TOKENS}
        assert isinstance(body["latency_ms"], int) and body["latency_ms"] >= 0
        # Clause 9: output is free, so the input alone, at 8 places.
        assert Decimal(body["vendor_cost_usd"]) == CALL_COST
        # 🔴 The key never comes back, and the vendor's words stay home.
        assert "sk-typesafe" not in r.text
        for word in ("noul", "legend"):
            assert word not in r.text.lower(), word
        # 🔴 Row 16: no usage_event row.
        assert _usage_count(db) == before
        audits = _try_audits(db, email)
        assert len(audits) == 1
        org_id, detail = audits[0]
        assert org_id is None
        assert detail["input_tokens"] == INPUT_TOKENS
        assert detail["output_tokens"] == OUTPUT_TOKENS
        assert Decimal(detail["vendor_cost_usd"]) == CALL_COST
        assert "sk-typesafe" not in json.dumps(detail)

    def test_the_call_goes_through_the_provider_seam_with_the_platform_key(
        self, client, db, bound, recording_fake
    ):
        """A fake set by `set_provider_call` sees the call, so no real vendor
        is reached from a test. The payload names no organization."""
        headers, _ = _operator(db, "admin")
        before = _usage_count(db)
        r = _try(client, headers)
        assert r.status_code == 200, r.text
        assert len(recording_fake["calls"]) == 1
        call = recording_fake["calls"][0]
        assert call["invocation"] == "native_typesafe"
        assert call["task"] == "decide"
        payload = call["payload"]
        assert isinstance(payload, DecidePayload)
        assert payload.organization_id is None
        assert payload.api_key == "sk-typesafe-fence"
        assert bound.requests == []
        assert _usage_count(db) == before

    @pytest.mark.parametrize("role", ["viewer", "editor"])
    def test_a_rank_below_admin_is_403(self, client, db, bound, recording_fake, role):
        headers, _ = _operator(db, role)
        r = _try(client, headers)
        assert r.status_code == 403, r.text
        assert recording_fake["calls"] == []

    def test_a_clause_13_breach_is_a_400_with_no_vendor_call(
        self, client, db, bound, recording_fake
    ):
        headers, email = _operator(db, "admin")
        before = _usage_count(db)
        r = _try(client, headers, questions={f"q{i}": {"type": "boolean", "instructions": "x"}
                                             for i in range(MAX_QUESTIONS + 1)})
        assert r.status_code == 400, r.text
        assert f"at most {MAX_QUESTIONS} questions" in r.json()["detail"]
        assert recording_fake["calls"] == []
        assert _usage_count(db) == before
        assert _try_audits(db, email) == []

    def test_an_unbound_tier_is_a_400_naming_tier_unknown(
        self, client, db, recording_fake
    ):
        headers, email = _operator(db, "admin")
        r = _try(client, headers)
        assert r.status_code == 400, r.text
        assert "tier_unknown" in r.json()["detail"]
        assert recording_fake["calls"] == []
        assert _try_audits(db, email) == []

    def test_a_vendor_failure_goes_through_the_one_mapping_and_is_audited(
        self, client, db, bound
    ):
        """Review fix. A failed call may still be paid for, so it leaves ONE
        audit row with its outcome, and still no usage row."""
        headers, email = _operator(db, "admin")
        before = _usage_count(db)
        bound.status = 429
        bound.body = {"error": "the vendor quotes the request here"}
        r = _try(client, headers)
        assert r.status_code == 429, r.text
        assert r.json()["detail"] == "upstream provider error"
        assert "quotes the request" not in r.text
        assert _usage_count(db) == before
        audits = _try_audits(db, email)
        assert len(audits) == 1
        org_id, detail = audits[0]
        assert org_id is None
        assert detail["outcome"] == "upstream_failed"
        assert detail["upstream_status"] == 429
        assert "quotes the request" not in json.dumps(detail)

    def test_an_unreadable_200_is_audited_as_unreadable(self, client, db, bound):
        headers, email = _operator(db, "admin")
        bound.body = {"answers": "not a map", "usage": {"input_tokens": 5}}
        r = _try(client, headers)
        assert r.status_code == 502, r.text
        [(_, detail)] = _try_audits(db, email)
        assert detail["outcome"] == "unreadable"

    def test_a_served_call_is_audited_as_served(self, client, db, bound):
        headers, email = _operator(db, "admin")
        assert _try(client, headers).status_code == 200
        [(_, detail)] = _try_audits(db, email)
        assert (detail["outcome"], detail["upstream_status"]) == ("served", 200)

    def test_a_byok_key_alone_is_never_spent(
        self, client, db, org, bound, recording_fake
    ):
        """Platform credential ONLY. The one `typesafe` row belongs to a
        customer, so the route answers 503 and no vendor sees the call."""
        with db.begin() as c:
            c.execute(text(
                "UPDATE provider_credential SET organization_id = CAST(:o AS uuid) "
                "WHERE provider = 'typesafe' AND label = :l"),
                {"o": org["id"], "l": _FENCE_LABEL})
            platform = c.execute(text(
                "SELECT count(*) FROM provider_credential WHERE provider = 'typesafe' "
                "AND organization_id IS NULL AND revoked_at IS NULL")).scalar_one()
        assert platform == 0
        headers, email = _operator(db, "admin")
        r = _try(client, headers)
        assert r.status_code == 503, r.text
        assert "typesafe" in r.json()["detail"]
        assert recording_fake["calls"] == []
        assert bound.requests == []
        assert _try_audits(db, email) == []

    def test_no_key_at_all_is_a_503_that_names_the_vendor(
        self, client, db, bound, recording_fake
    ):
        with db.begin() as c:
            c.execute(text(
                "DELETE FROM provider_credential WHERE provider = 'typesafe' AND label = :l"),
                {"l": _FENCE_LABEL})
        headers, _ = _operator(db, "admin")
        r = _try(client, headers)
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == "no provider credential configured for 'typesafe'"
        assert recording_fake["calls"] == []


# ── CP-13h: Jev through the AI/ML API reseller (§6A.14 CP-13h) ─────────────

#: What the operator binds. `aimlapi` names the credential, and the reseller
#: sees `typesafe/jev`.
AIML_JEV = "aimlapi/typesafe/jev"

#: The reseller's documented response, VERBATIM from
#: https://docs.aimlapi.com/api-references/decision-models/typesafe/jev (read
#: 2026-09-24). It carries every word that must not leak: `noul`, `legend`,
#: the dated model id, and the `meta` block with `credits_used`.
AIML_RECORDED = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {
        "is_urgent": {"type": "noul", "noul": 0.96},
        "department": {
            "type": "choice",
            "choice": "billing",
            "confidence": 0.97,
            "probabilities": {"billing": 0.98, "technical": 0.02, "sales": 0},
        },
        "frustration": {
            "type": "score",
            "score": 1.3,
            "confidence": 0.55,
            "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
            "probabilities": {"0": 0, "1": 0.7, "2": 0.3},
        },
    },
    "usage": {"input_tokens": 403, "output_tokens": 73},
    "meta": {"usage": {"credits_used": 47, "usd_spent": 0.0000235}},
}

AIML_STATE = "Help! My payments have been failing for 3 days and nobody answers support."

#: The documented request, in OUR words. The score levels are a map here,
#: and the handler sends the descriptions as the reseller's ordered array.
AIML_QUESTIONS = {
    "is_urgent": {"type": "boolean", "instructions": "Does this convey urgency?",
                  "criteria": {}},
    "department": {
        "type": "choice",
        "instructions": "Which team should handle this?",
        "criteria": {
            "billing": "Payments, invoicing, refunds",
            "technical": "Bugs, outages, integrations",
            "sales": "Pricing, upgrades, new accounts",
        },
    },
    "frustration": {
        "type": "score",
        "instructions": "How frustrated is the customer?",
        "criteria": {"calm": "Calm", "frustrated": "Frustrated",
                     "very_angry": "Very angry"},
    },
}

AIML_COST = Decimal("0.0000235")


def _aiml_questions() -> dict[str, Question]:
    return {qid: Question(type=q["type"], instructions=q["instructions"],
                          criteria=q["criteria"]) for qid, q in AIML_QUESTIONS.items()}


def _run_aiml(vendor: _Vendor, *, api_base: str | None = None) -> ProviderResult:
    handler = handlers.AimlApiHandler(transport=vendor.transport())
    payload = DecidePayload(model=AIML_JEV, state=AIML_STATE, questions=_aiml_questions(),
                            api_key="sk-aimlapi-test", api_base=api_base)
    return asyncio.run(handler.call("decide", payload))


class TestAimlApiIsRegistered:
    def test_native_aimlapi_is_in_both_invocation_sets(self):
        assert "native_aimlapi" in catalog.KNOWN_INVOCATIONS
        assert "native_aimlapi" in router_mod.SERVING_INVOCATIONS
        assert isinstance(handlers.NATIVE_HANDLERS["native_aimlapi"],
                          handlers.AimlApiHandler)

    def test_one_wire_class_serves_both_vendors(self):
        """No parallel abstraction: both are instances of ONE class."""
        for name in ("native_typesafe", "native_aimlapi"):
            assert isinstance(handlers.NATIVE_HANDLERS[name], handlers.SystemOneHandler)
        assert handlers.native_vendors() == frozenset({"typesafe", "aimlapi"})

    def test_the_pairing_refuses_it_for_chat(self):
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_invocation_for_task("native_aimlapi", "chat")
        assert catalog.check_invocation_for_task("native_aimlapi", "decide") == "native_aimlapi"


class TestAimlApiRequest:
    def test_the_request_goes_to_the_reseller_with_the_prefix_stripped(self):
        vendor = _Vendor(body=AIML_RECORDED)
        _run_aiml(vendor)
        request = vendor.requests[-1]
        assert request.method == "POST"
        assert str(request.url) == "https://api.aimlapi.com/v1/decisions"
        assert request.headers["Authorization"] == "Bearer sk-aimlapi-test"
        sent = vendor.sent()
        assert sent["model"] == "typesafe/jev"
        assert sent["state"] == AIML_STATE

    def test_the_questions_go_out_in_the_resellers_words(self):
        vendor = _Vendor(body=AIML_RECORDED)
        _run_aiml(vendor)
        questions = vendor.sent()["questions"]
        assert questions["is_urgent"]["type"] == "noul"
        assert questions["department"] == {
            "type": "choice",
            "instructions": "Which team should handle this?",
            "criteria": AIML_QUESTIONS["department"]["criteria"],
        }
        # The reseller documents score levels as an ORDERED ARRAY.
        assert questions["frustration"] == {
            "type": "score",
            "instructions": "How frustrated is the customer?",
            "criteria": ["Calm", "Frustrated", "Very angry"],
        }

    def test_a_blank_level_description_sends_its_key(self):
        vendor = _Vendor(body=AIML_RECORDED)
        handler = handlers.AimlApiHandler(transport=vendor.transport())
        questions = _aiml_questions()
        questions["frustration"] = Question(
            type="score", instructions="x", criteria={"low": "", "high": "very"})
        asyncio.run(handler.call("decide", DecidePayload(
            model=AIML_JEV, state="s", questions=questions, api_key="k")))
        assert vendor.sent()["questions"]["frustration"]["criteria"] == ["low", "very"]

    def test_the_credential_api_base_wins(self):
        vendor = _Vendor(body=AIML_RECORDED)
        _run_aiml(vendor, api_base="https://proxy.example/")
        assert str(vendor.requests[-1].url) == "https://proxy.example/v1/decisions"


class TestAimlApiResponse:
    def test_the_documented_body_comes_back_in_OUR_shape(self):
        result = _run_aiml(_Vendor(body=AIML_RECORDED))
        assert result.body["answers"] == {
            "is_urgent": {"type": "boolean", "probability": 0.96},
            "department": {
                "type": "choice",
                "choice": "billing",
                "probabilities": {"billing": 0.98, "technical": 0.02, "sales": 0.0},
                "confidence": 0.97,
            },
            "frustration": {
                "type": "score",
                # The 0-based POSITION, as the vendor sent it.
                "score": 1.3,
                # The caller's key of the nearest position.
                "level": "frustrated",
                # The reseller keys levels by index. The caller reads its own keys.
                "probabilities": {"calm": 0.0, "frustrated": 0.7, "very_angry": 0.3},
                "confidence": 0.55,
            },
        }

    def test_no_vendor_word_leaks(self):
        body = json.dumps(_run_aiml(_Vendor(body=AIML_RECORDED)).body).lower()
        for word in ("noul", "legend", "typesafe", "jev", "meta", "credits_used"):
            assert word not in body, word

    def test_usage_and_the_vendor_reported_cost(self):
        result = _run_aiml(_Vendor(body=AIML_RECORDED))
        assert result.usage.prompt_tokens == 403
        assert result.usage.completion_tokens == 73
        assert result.usage.vendor_reported_cost_usd == AIML_COST
        assert result.quantity is None

    @pytest.mark.parametrize("usd, expected", [
        ("0.0000235", AIML_COST),
        (0, Decimal(0)),
        (-0.01, None),
        (True, None),
        ("not a number", None),
        ("NaN", None),
        ("Infinity", None),
        (None, None),
        ([1], None),
    ])
    def test_the_cost_parse_is_defensive(self, usd, expected):
        recorded = json.loads(json.dumps(AIML_RECORDED))
        recorded["meta"]["usage"]["usd_spent"] = usd
        result = _run_aiml(_Vendor(body=recorded))
        assert result.usage.vendor_reported_cost_usd == expected
        # A missing or odd cost never fails a served call.
        assert result.body["answers"]["is_urgent"]["probability"] == 0.96

    @pytest.mark.parametrize("meta", [None, "x", {}, {"usage": "x"}])
    def test_a_missing_meta_block_is_no_cost_and_still_served(self, meta):
        recorded = json.loads(json.dumps(AIML_RECORDED))
        if meta is None:
            del recorded["meta"]
        else:
            recorded["meta"] = meta
        result = _run_aiml(_Vendor(body=recorded))
        assert result.usage.vendor_reported_cost_usd is None

    def test_typesafe_direct_never_reads_a_meta_cost(self):
        """`native_typesafe` is unchanged: it reads no vendor cost."""
        recorded = json.loads(json.dumps(RECORDED))
        recorded["meta"] = {"usage": {"usd_spent": 1}}
        assert _run_handler(_Vendor(body=recorded)).usage.vendor_reported_cost_usd is None


class TestAFractionalScoreReads:
    """CP-13a's latent bug. Both vendors document a score that can land
    between levels. A float was refused, and that was a TERMINAL 502 after
    the vendor had charged us."""

    def test_typesafe_direct_reads_a_fractional_score(self):
        recorded = json.loads(json.dumps(RECORDED))
        recorded["answers"]["urgency"]["score"] = 1.05
        result = _run_handler(_Vendor(body=recorded))
        assert result.body["answers"]["urgency"]["score"] == 1.05

    def test_the_reseller_reads_a_fractional_score(self):
        assert _run_aiml(_Vendor(body=AIML_RECORDED)).body[
            "answers"]["frustration"]["score"] == 1.3

    @pytest.mark.parametrize("bad", [True, None, [1], {"x": 1}])
    def test_a_bool_or_a_non_number_is_still_refused(self, bad):
        recorded = json.loads(json.dumps(AIML_RECORDED))
        recorded["answers"]["frustration"]["score"] = bad
        with pytest.raises(NativeProviderError) as exc:
            _run_aiml(_Vendor(body=recorded))
        assert exc.value.terminal is True

    def test_typesafe_direct_reads_the_documented_noul_field(self):
        recorded = json.loads(json.dumps(RECORDED))
        recorded["answers"]["cold"] = {"type": "noul", "noul": 0.4}
        result = _run_handler(_Vendor(body=recorded))
        assert result.body["answers"]["cold"] == {"type": "boolean", "probability": 0.4}


class TestAimlApiErrorMapping:
    """Identical to TypeSafe: the status travels, the body does not."""

    @pytest.mark.parametrize("status", [401, 422, 429, 500, 529])
    def test_status_code_is_on_the_error(self, status):
        with pytest.raises(NativeProviderError) as exc:
            _run_aiml(_Vendor(status=status, body={"error": "quoted request"}))
        assert exc.value.status_code == status
        assert exc.value.terminal is False
        assert "quoted request" not in str(exc.value)

    def test_an_unreadable_200_is_terminal(self):
        with pytest.raises(NativeProviderError) as exc:
            _run_aiml(_Vendor(body={"answers": "not a map"}))
        assert exc.value.terminal is True
        assert exc.value.status_code is None

    def test_a_dropped_connection_carries_no_status(self):
        def _drop(request):
            raise httpx.ConnectError("refused", request=request)

        handler = handlers.AimlApiHandler(transport=httpx.MockTransport(_drop))
        payload = DecidePayload(model=AIML_JEV, state="s", questions=_aiml_questions(),
                                api_key="k")
        with pytest.raises(NativeProviderError) as exc:
            asyncio.run(handler.call("decide", payload))
        assert exc.value.status_code is None


def _score(handler_cls, score, *, criteria=None):
    """One score answer through a real handler, from a recorded body."""
    criteria = criteria or {"calm": "Calm", "frustrated": "Frustrated", "very_angry": "Very angry"}
    body = {"answers": {"s": {"type": "score", "score": score, "confidence": 0.5,
                              "legend": {}, "probabilities": {"0": 0.2, "1": 0.8}}},
            "usage": {"input_tokens": 5, "output_tokens": 1}}
    vendor = _Vendor(body=body)
    model = AIML_JEV if handler_cls is handlers.AimlApiHandler else JEV
    payload = DecidePayload(model=model, state="s", api_key="k", questions={
        "s": Question(type="score", instructions="x", criteria=criteria)})
    return asyncio.run(handler_cls(transport=vendor.transport()).call(
        "decide", payload)).body["answers"]["s"]


class TestOneScoreMeaning:
    """Review P1-1. ONE meaning for both vendors, so a failover never changes
    it: `score` is the 0-based position, `level` the nearest caller key, and
    `probabilities` are keyed by caller level keys."""

    @pytest.mark.parametrize("handler_cls", [handlers.TypeSafeHandler,
                                             handlers.AimlApiHandler])
    @pytest.mark.parametrize("position, level", [
        (0, "calm"), (0.49, "calm"), (0.5, "frustrated"), (1.3, "frustrated"),
        (1.5, "very_angry"), (2, "very_angry"),
    ])
    def test_level_is_the_nearest_position_rounded_half_up(self, handler_cls, position, level):
        answer = _score(handler_cls, position)
        assert answer["score"] == position
        assert answer["level"] == level
        assert answer["probabilities"] == {"calm": 0.2, "frustrated": 0.8}

    @pytest.mark.parametrize("position, level", [(3.4, "very_angry"), (-0.7, "calm")])
    def test_an_out_of_range_position_clamps_its_level_and_logs_once(
        self, caplog, position, level
    ):
        with caplog.at_level("WARNING", logger="customer_console.handlers"):
            answer = _score(handlers.AimlApiHandler, position)
        assert answer["score"] == position
        assert answer["level"] == level
        alarms = [r for r in caplog.records
                  if r.getMessage() == "handlers.score_out_of_range"]
        assert len(alarms) == 1

    def test_an_in_range_position_logs_nothing(self, caplog):
        with caplog.at_level("WARNING", logger="customer_console.handlers"):
            _score(handlers.AimlApiHandler, 1.3)
        assert not [r for r in caplog.records
                    if r.getMessage() == "handlers.score_out_of_range"]

    def test_a_level_key_as_the_score_reads_as_its_position(self):
        answer = _score(handlers.TypeSafeHandler, "very_angry")
        assert (answer["score"], answer["level"]) == (2, "very_angry")

    def test_an_unknown_string_is_unreadable(self):
        with pytest.raises(NativeProviderError) as exc:
            _score(handlers.AimlApiHandler, "loud")
        assert exc.value.terminal is True

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
    def test_a_non_finite_position_is_unreadable(self, bad):
        """httpx will not encode these, so the parse is called directly."""
        question = Question(type="score", instructions="x",
                            criteria={"a": "A", "b": "B"})
        with pytest.raises(NativeProviderError):
            handlers._answer(question, {"type": "score", "score": bad})


class TestAMismatchedPrefixNeverLeavesTheBox:
    """Review P1-2, layer (a). The model prefix picks the key, the handler
    picks the host. A mismatch would post one vendor's key to the other."""

    @pytest.mark.parametrize("handler_cls, model", [
        (handlers.TypeSafeHandler, AIML_JEV),
        (handlers.AimlApiHandler, JEV),
        (handlers.AimlApiHandler, "typesafe/jev"),
        (handlers.TypeSafeHandler, "jev-1.13.0"),
    ])
    def test_a_mismatch_makes_ZERO_network_calls(self, caplog, handler_cls, model):
        vendor = _Vendor(body=AIML_RECORDED)
        payload = DecidePayload(model=model, state="s", questions=_aiml_questions(),
                                api_key="sk-must-not-travel")
        with (
            caplog.at_level("ERROR", logger="customer_console.handlers"),
            pytest.raises(NativeProviderError) as exc,
        ):
            asyncio.run(handler_cls(transport=vendor.transport()).call("decide", payload))
        assert vendor.requests == []
        assert exc.value.status_code is None
        assert exc.value.terminal is False
        assert "model prefix does not match handler" in str(exc.value)
        [alarm] = [r for r in caplog.records
                   if r.getMessage() == "handlers.model_prefix_mismatch"]
        assert "sk-must-not-travel" not in str(alarm.__dict__)


class TestTheDeclareRefusesAMismatchedPair:
    """Review P1-2, layer (b). The verb-to-vendor map comes from the handler
    table, never from a typed list."""

    def test_the_map_is_read_from_the_handler_table(self):
        for verb, handler in handlers.NATIVE_HANDLERS.items():
            assert handlers.native_provider_of(verb) == handler.provider
        assert handlers.native_provider_of("acompletion") is None

    @pytest.mark.parametrize("model, verb", [
        (AIML_JEV, "native_typesafe"),
        (JEV, "native_aimlapi"),
        ("openai/gpt-4o", "native_aimlapi"),
    ])
    def test_a_mismatch_is_refused_naming_both(self, model, verb):
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_model_for_invocation(model, verb)
        vendor = handlers.native_provider_of(verb)
        assert repr(model.partition("/")[0]) in str(exc.value)
        assert repr(vendor) in str(exc.value)

    @pytest.mark.parametrize("model, verb", [
        (AIML_JEV, "native_aimlapi"), (JEV, "native_typesafe"),
        ("openai/gpt-4o", "acompletion"),
    ])
    def test_a_matching_pair_passes(self, model, verb):
        assert catalog.check_model_for_invocation(model, verb) is None


class TestTheReportedCostCeiling:
    """Review P2. `provider_cost_usd` is NUMERIC(14, 8). A huge figure would
    fail the INSERT and lose the usage row, so it reads as None."""

    @pytest.mark.parametrize("raw, expected", [
        ("1000", Decimal("1000.00000000")),
        ("1000.00000001", None),
        (1e9, None),
        ("99999999999999", None),
    ])
    def test_the_ceiling(self, raw, expected):
        assert router_mod.reported_cost_usd(raw) == expected

    def test_above_the_ceiling_logs(self, caplog):
        with caplog.at_level("WARNING", logger="customer_console.router"):
            assert router_mod.reported_cost_usd(5000) is None
        assert [r for r in caplog.records
                if r.getMessage() == "router.reported_cost_above_ceiling"]


@pytest.fixture
def aiml_bound(db, monkeypatch):
    """The operator's CP-13h path: declare `aimlapi/typesafe/jev` with
    `native_aimlapi`, bind `tier-decide`, install an `aimlapi` key. The real
    handler answers from the documented body."""
    recorded = _Vendor(body=json.loads(json.dumps(AIML_RECORDED)))
    monkeypatch.setitem(handlers.NATIVE_HANDLERS, "native_aimlapi",
                        handlers.AimlApiHandler(transport=recorded.transport()))
    since = _bind_decide(db, [AIML_JEV], invocation="native_aimlapi", provider="aimlapi")
    yield recorded
    _unbind_decide(db, [AIML_JEV], since, provider="aimlapi")


def _aiml_decide(client, key):
    return client.post("/v1/decide", headers=key, json={
        "tier": "tier-decide", "state": AIML_STATE, "questions": AIML_QUESTIONS})


@_DB
class TestAimlApiEndToEnd:
    """R8: the row a real call through `POST /v1/decide` wrote."""

    def test_the_door_meters_the_vendor_reported_cost(self, client, db, org, aiml_bound):
        r = _aiml_decide(client, org["key"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tier"] == "tier-decide"
        assert body["answers"]["frustration"]["score"] == 1.3
        lowered = r.text.lower()
        for word in ("jev", "typesafe", "noul", "legend", "meta", "credits_used", "aimlapi"):
            assert word not in lowered, word
        with db.begin() as c:
            rows = c.execute(text(
                "SELECT model, prompt_tokens, completion_tokens, provider_cost_usd, "
                "       cost_source, metering_fault "
                "FROM usage_event WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org["id"]}).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.model == AIML_JEV
        assert (row.prompt_tokens, row.completion_tokens) == (403, 73)
        assert Decimal(row.provider_cost_usd) == AIML_COST
        assert row.cost_source == "vendor"
        assert row.metering_fault is None
        # The reseller saw its own key and the bare model.
        assert aiml_bound.requests[-1].headers["Authorization"] == "Bearer sk-aimlapi-fence"
        assert aiml_bound.sent()["model"] == "typesafe/jev"

    @pytest.mark.parametrize("vendor_status, ours", [(429, 429), (500, 502), (401, 502)])
    def test_a_vendor_failure_maps_as_typesafe_does(
        self, client, db, org, aiml_bound, vendor_status, ours
    ):
        aiml_bound.status = vendor_status
        aiml_bound.body = {"error": "the vendor quotes the request here"}
        r = _aiml_decide(client, org["key"])
        assert r.status_code == ours, r.text
        assert r.json()["detail"] == "upstream provider error"
        assert _rows(db, org["id"]) == []

    def test_an_unreadable_200_is_a_502(self, client, db, org, aiml_bound):
        aiml_bound.body = {"answers": "not a map", "usage": {"input_tokens": 5}}
        r = _aiml_decide(client, org["key"])
        assert r.status_code == 502, r.text
        assert _rows(db, org["id"]) == []

    def test_the_capability_write_refuses_it_for_chat(self, client):
        model = f"aimlapi/pair-{uuid.uuid4().hex[:6]}"
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "chat", "invocation": "native_aimlapi"})
        assert r.status_code == 400, r.text
        assert "serves only decide" in r.json()["detail"]

    @pytest.mark.parametrize("model, verb", [
        ("aimlapi/typesafe/jev", "native_typesafe"),
        ("typesafe/jev-1.13.0", "native_aimlapi"),
    ])
    def test_the_capability_write_refuses_a_mismatched_pair(self, client, db, model, verb):
        """Review P1-2, layer (b): a 400 at declare time that names both the
        credential the prefix picks and the vendor the verb calls."""
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "decide", "invocation": verb})
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert repr(model.partition("/")[0]) in detail
        assert repr(handlers.native_provider_of(verb)) in detail
        with db.begin() as c:
            assert c.execute(text(
                "SELECT count(*) FROM model_capability WHERE model = :m AND task = 'decide' "
                "AND invocation = :v"), {"m": model, "v": verb}).scalar_one() == 0

    def test_a_mismatched_row_written_before_the_rule_never_reaches_a_vendor(
        self, client, db, org, monkeypatch
    ):
        """Review P1-2, layer (a), end to end. A row that pairs the reseller's
        model with `native_typesafe` finds the `aimlapi` key. The handler
        refuses it with ZERO network calls, and the caller reads a 502."""
        typesafe = _Vendor()
        monkeypatch.setitem(handlers.NATIVE_HANDLERS, "native_typesafe",
                            handlers.TypeSafeHandler(transport=typesafe.transport()))
        since = _bind_decide(db, [AIML_JEV], invocation="native_typesafe",
                             provider="aimlapi")
        try:
            r = _aiml_decide(client, org["key"])
        finally:
            _unbind_decide(db, [AIML_JEV], since, provider="aimlapi")
        assert r.status_code == 502, r.text
        assert typesafe.requests == []
        assert _rows(db, org["id"]) == []

    def test_a_huge_reported_cost_falls_back_to_the_computed_cost(
        self, client, db, org, aiml_bound
    ):
        """Review P2. Above the ceiling the figure reads as None, so the row
        is still written, with the profile arithmetic."""
        aiml_bound.body["meta"]["usage"]["usd_spent"] = 1e12
        r = _aiml_decide(client, org["key"])
        assert r.status_code == 200, r.text
        with db.begin() as c:
            row = c.execute(text(
                "SELECT provider_cost_usd, cost_source FROM usage_event "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org["id"]}).one()
        expected = (Decimal(403) * INPUT_PER_1M / Decimal(1_000_000)).quantize(
            Decimal("0.00000001"))
        assert Decimal(row.provider_cost_usd) == expected
        assert row.cost_source == "computed"

    def test_the_documented_score_reads_the_same_at_the_door_the_facade_and_the_tool(
        self, client, db, org, aiml_bound, monkeypatch
    ):
        """Review P1-1. The documented reseller answer goes through the
        Console door, then through `acb_llm.decide`, then through the tool's
        formatter. Each layer reads ONE meaning, and none refuses a float."""
        import acb_llm
        from acb_auth import console_resolve
        from acb_common.settings import get_settings
        from acb_skills import decide_tools

        levels = ["Calm", "Frustrated", "Very angry"]
        aiml_bound.body["answers"] = {"q": AIML_RECORDED["answers"]["frustration"]}
        # The question the tool builds: each level is its own key.
        question = {"type": "score", "instructions": "How frustrated is the customer?",
                    "criteria": {lvl: lvl for lvl in levels}}
        r = client.post("/v1/decide", headers=org["key"], json={
            "tier": "tier-decide", "state": AIML_STATE, "questions": {"q": question}})
        assert r.status_code == 200, r.text
        door = r.json()
        assert door["answers"]["q"] == {
            "type": "score", "score": 1.3, "level": "Frustrated",
            "probabilities": {"Calm": 0.0, "Frustrated": 0.7, "Very angry": 0.3},
            "confidence": 0.55,
        }

        # The tenant side reads the door's own JSON.
        def _client(timeout=None):
            return httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=door)))

        monkeypatch.setenv("DECIDE_ENABLED", "true")
        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.metorite.test")
        monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_abcd_secretsecretsecret")
        monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "false")
        get_settings.cache_clear()
        monkeypatch.setattr(console_resolve, "_new_http_client", _client)
        try:
            decision = asyncio.run(acb_llm.decide(
                AIML_STATE, {"q": acb_llm.ScoreQuestion(
                    question["instructions"], question["criteria"])}))
            answer = decision["q"]
            assert isinstance(answer, acb_llm.ScoreAnswer)
            assert (answer.score, answer.level, answer.confidence) == (1.3, "Frustrated", 0.55)

            line = asyncio.run(decide_tools.decide(
                question["instructions"], AIML_STATE, kind="score",
                options="\n".join(levels)))
            assert line == "Frustrated (position 1.3, confidence 0.55)"
        finally:
            get_settings.cache_clear()


@_DB
class TestTryADecisionOnTheReseller:
    """Try a decision works for the new handler unchanged, and shows the
    cost the reseller reported."""

    def test_the_try_shows_the_vendor_reported_cost(self, client, db, aiml_bound):
        headers, email = _operator(db, "admin")
        before = _usage_count(db)
        r = client.post("/catalog/decide/try", headers=headers, json={
            "state": AIML_STATE, "questions": AIML_QUESTIONS})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["model"] == AIML_JEV
        assert body["answers"]["frustration"]["score"] == 1.3
        assert body["usage"] == {"input_tokens": 403, "output_tokens": 73}
        # The profile says 0.042 per million input. The reseller's own figure wins.
        assert Decimal(body["vendor_cost_usd"]) == AIML_COST
        assert "sk-aimlapi" not in r.text
        assert _usage_count(db) == before
        [(_, detail)] = _try_audits(db, email)
        assert Decimal(detail["vendor_cost_usd"]) == AIML_COST
        assert detail["cost_source"] == "vendor"

    def test_the_try_goes_through_the_provider_seam(
        self, client, db, aiml_bound, recording_fake
    ):
        headers, _ = _operator(db, "admin")
        r = client.post("/catalog/decide/try", headers=headers, json={
            "state": AIML_STATE, "questions": AIML_QUESTIONS})
        assert r.status_code == 200, r.text
        [call] = recording_fake["calls"]
        assert call["invocation"] == "native_aimlapi"
        assert call["payload"].api_key == "sk-aimlapi-fence"
        assert call["payload"].model == AIML_JEV
        assert aiml_bound.requests == []

    def test_the_typesafe_try_still_computes_its_cost(self, client, db, bound):
        """No vendor figure, so the profile arithmetic answers, as before."""
        headers, email = _operator(db, "admin")
        r = _try(client, headers)
        assert r.status_code == 200, r.text
        assert Decimal(r.json()["vendor_cost_usd"]) == CALL_COST
        [(_, detail)] = _try_audits(db, email)
        assert detail["cost_source"] == "computed"


def test_this_suite_is_named_in_the_ci_skip_guard():
    """A new R8 suite that CI does not name still SKIPS silently and reports
    green. This reads the hand-list and fails if the entry is gone."""
    ci = (ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_customer_console_decide.py" in ci
