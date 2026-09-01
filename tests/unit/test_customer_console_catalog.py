"""WS-31 CP-10 slice 3 — the operator's model catalog.

Spec: ``project-docs/specs/customer_console.md`` §6A CP-10 · §6A.5 (the
INSERT-only write contract) · §6A.9 · D60 · D61.

**What this closes.** `tier_binding` and `model_rate_card` were written exactly
once, by `002_seed_catalog.sql`. So adding a model, re-pointing a tier or
re-pricing one was a hand-run SQL statement against the live Console database —
an owner-gated production one-off, per change, for ever.

⚠️ **This builds the mechanism to price and prices NOTHING.** The ladder still
ships every card `unpriced`, and `test_the_rate_card_ships_unpriced` fails if
that stops being true. Setting a number is the owner's commercial act (H-42).
"""
from __future__ import annotations

import ast
import os
import pathlib
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from customer_console import catalog
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

_DB = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
CATALOG_SRC = ROOT / "apps/services/customer_console/customer_console/catalog.py"
MAIN_SRC = ROOT / "apps/services/customer_console/customer_console/main.py"

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}


# ── The rules, without a database ───────────────────────────────────────────

class TestTheRules:
    def test_an_unknown_provider_verb_is_refused(self):
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_invocation("aTeleport")
        assert catalog.check_invocation("atranscription") == "atranscription"

    def test_only_chat_and_speak_may_stream(self):
        """§6A.9 rule 4.

        A `transcribe` capability claiming to stream would have the Router hold
        a connection open for frames the provider never sends.
        """
        assert catalog.check_streams("chat", True) is True
        assert catalog.check_streams("transcribe", False) is False
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_streams("transcribe", True)

    def test_a_task_must_be_priced_in_its_OWN_unit(self):
        """`transcribe` is sold per minute (D19.2 says so in terms).

        Priced per 1k tokens it produces a number, and a plausible one, and a
        wrong one. That is why `task_catalog` carries `natural_unit` at all.
        """
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_rate(
                catalog.RateProposal(
                    model="m", task="transcribe", unit="tokens",
                    pricing_mode="priced", input_per_1k=Decimal(2)),
                natural_unit="minutes")
        assert "minutes" in str(exc.value)

    def test_priced_with_every_rate_at_zero_is_refused(self):
        """That is what `absorbed` is for — the whole point of G-4."""
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_rate(
                catalog.RateProposal(model="m", task="chat", unit="tokens",
                                     pricing_mode="priced"),
                natural_unit="tokens")
        assert "absorbed" in str(exc.value)

    def test_a_non_priced_card_carrying_a_RATE_is_refused(self):
        """⚠️ The shape that would ship a price nobody meant to switch on.

        A card with real numbers under `unpriced` reads as a draft, and the
        ladder fence counts exactly this shape.
        """
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_rate(
                catalog.RateProposal(
                    model="m", task="chat", unit="tokens",
                    pricing_mode="unpriced", input_per_1k=Decimal(2)),
                natural_unit="tokens")

    def test_a_negative_rate_is_refused(self):
        # It would CREDIT a customer for using the product.
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_rate(
                catalog.RateProposal(
                    model="m", task="chat", unit="tokens",
                    pricing_mode="priced", input_per_1k=Decimal(-1)),
                natural_unit="tokens")

    def test_absorbed_at_zero_is_accepted(self):
        # D19.2's embeddings: deliberately free, and not a mistake.
        catalog.check_rate(
            catalog.RateProposal(model="m", task="embed", unit="tokens",
                                 pricing_mode="absorbed"),
            natural_unit="tokens")


class TestTheTwoGaps:
    """⚠️ Neither table shows these alone, and that is where mistakes live."""

    def test_capable_but_unbound_is_money_left_on_the_table(self):
        gap = catalog.unbound_capabilities(
            capabilities=[("gpt-4o", "image"), ("gpt-4o", "chat")],
            bindings=[("gpt-4o", "chat")])
        assert gap == [{"model": "gpt-4o", "task": "image"}]

    def test_bound_but_NOT_capable_is_a_500_waiting_to_happen(self):
        """The dangerous one. The Router resolves a model, then cannot pick a
        verb — on the first request, not here."""
        gap = catalog.unserved_bindings(
            capabilities=[("gpt-4o", "chat")],
            bindings=[("gpt-4o", "chat"), ("whisper", "transcribe")])
        assert gap == [{"model": "whisper", "task": "transcribe"}]

    def test_a_fully_wired_model_shows_in_neither_gap(self):
        pairs = [("m", "chat")]
        assert catalog.unbound_capabilities(pairs, pairs) == []
        assert catalog.unserved_bindings(pairs, pairs) == []


class TestTheWriteContractIsInsertOnly:
    """§6A.5. A mutable rate card destroys the audit trail at exactly the
    moment a customer disputes a charge, which is the only moment it matters.
    """

    def test_the_catalog_module_offers_no_update(self):
        tree = ast.parse(CATALOG_SRC.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        offenders = [n for n in names if "update" in n or "edit" in n]
        assert not offenders, f"INSERT-only: {offenders}"

    def test_no_route_updates_or_deletes_a_binding_or_a_rate(self):
        """Read from the AST, not the text — the docstrings SAY 'never UPDATE'
        while explaining the rule, so a grep would match its own prose."""
        tree = ast.parse(MAIN_SRC.read_text(encoding="utf-8"))
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                verb = getattr(dec.func, "attr", "")
                path = (dec.args[0].value
                        if dec.args and isinstance(dec.args[0], ast.Constant)
                        else "")
                if not isinstance(path, str) or "/catalog/" not in path:
                    continue
                if verb in ("patch", "put", "delete"):
                    bad.append(f"{verb.upper()} {path}")
        assert not bad, f"catalog writes must be INSERT-only, found: {bad}"

    def test_the_sharp_writes_need_admin_AND_a_window(self):
        """Re-pointing a tier decides what every customer call runs on. Pricing
        decides what they are billed. Both are as sharp as a provider key."""
        from customer_console.operator_roles import MATRIX

        for route in ("/catalog/bindings", "/catalog/rates",
                      "/catalog/tier-rates", "/catalog/credit-price"):
            rule = MATRIX[("POST", route)]
            assert rule.elevated is True, route
        # And reading is not a privilege.
        assert MATRIX[("GET", "/catalog/models")].elevated is False


# ── The routes, against a real database ─────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema():
    if not _URL:
        return
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", "test-key-not-real")
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


#: Prefixes this suite invents. Everything it writes is namespaced so
#: teardown can find it.
_MINE = ("test/%", "a/%")


@pytest.fixture(autouse=True)
def _clean_up_what_this_suite_writes():
    """Delete every row this suite creates, after each test.

    ⚠️ **Not optional.** These tests write through the ROUTES, which
    commit — so a rolled-back fixture connection cannot help. And they
    create PRICED rate cards on purpose, which trips a sibling fence:
    `test_customer_console_sql.py::test_the_rate_card_ships_unpriced`
    counts the WHOLE table deliberately, so that a later migration
    cannot ship a price under cover of a narrowed query. Measured: it
    went red the first time this suite ran ahead of it.

    That fence is right and this cleanup is the cost of it. The Router
    suite's `priced_card` pays the same cost the same way.
    """
    yield
    if not _URL:
        return
    eng = create_engine(_URL, future=True)
    with eng.begin() as c:
        for table in ("model_rate_card", "tier_binding",
                      "model_capability"):
            for like in _MINE:
                c.execute(text(
                    f"DELETE FROM {table} WHERE model LIKE :p"),
                    {"p": like})
    eng.dispose()


@_DB
class TestTheCatalogRead:
    def test_it_reports_the_seeded_world(self, client):
        body = client.get("/catalog/models", headers=OP).json()

        assert {t["slug"] for t in body["tasks"]} == {
            "chat", "embed", "vision", "transcribe", "speak", "image",
            "video", "music"}  # video and music joined in 015 (D67's slate)
        assert any(c["task"] == "transcribe" for c in body["capabilities"])
        assert any(b["tier"] == "tier-stt" for b in body["bindings"])
        # The registry (015): the whole slate reaches the console, so an
        # EMPTY tier can render instead of not existing.
        assert {t["slug"] for t in body["tier_registry"]} >= {
            "tier-fast", "tier-video", "tier-music"}

    def test_the_seeded_world_has_NO_unserved_binding(self, client):
        """Every seeded binding must name a model that declares the capability.

        An unserved binding is a 500 on the first request, so the ladder must
        not ship one — this is the fence for that.
        """
        body = client.get("/catalog/models", headers=OP).json()
        assert body["unserved"] == []

    def test_only_the_binding_IN_FORCE_is_listed(self, client, db):
        """Superseded rows stay for the audit trail. Showing them would read
        as 'these are all live'."""
        tier = f"tier-{uuid.uuid4().hex[:6]}"
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO model_capability (model, task, invocation) "
                "VALUES ('a/one', 'chat', 'acompletion'), "
                "       ('a/two', 'chat', 'acompletion') "
                "ON CONFLICT DO NOTHING"))
            c.execute(text(
                "INSERT INTO tier_binding (tier, task, model, effective_from) "
                "VALUES (:t, 'chat', 'a/one', now() - interval '2 days'), "
                "       (:t, 'chat', 'a/two', now() - interval '1 day')"),
                {"t": tier})

        rows = [b for b in client.get("/catalog/models", headers=OP).json()
                ["bindings"] if b["tier"] == tier]

        assert len(rows) == 1
        assert rows[0]["model"] == "a/two"

    def test_a_future_dated_binding_is_staged_not_live(self, client, db):
        tier = f"tier-{uuid.uuid4().hex[:6]}"
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO model_capability (model, task, invocation) "
                "VALUES ('a/soon', 'chat', 'acompletion') "
                "ON CONFLICT DO NOTHING"))
            c.execute(text(
                "INSERT INTO tier_binding (tier, task, model, effective_from) "
                "VALUES (:t, 'chat', 'a/soon', now() + interval '10 days')"),
                {"t": tier})

        rows = [b for b in client.get("/catalog/models", headers=OP).json()
                ["bindings"] if b["tier"] == tier]
        assert rows == []

    def test_it_is_operator_gated(self, client):
        assert client.get("/catalog/models").status_code in (401, 403)


@_DB
class TestTheCatalogWrites:
    def test_a_capability_can_be_declared_and_read_back(self, client):
        model = f"test/{uuid.uuid4().hex[:8]}"
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "image",
            "invocation": "aimage_generation"})
        assert r.status_code == 200, r.text

        body = client.get("/catalog/models", headers=OP).json()
        assert {"model": model, "task": "image"} in body["unbound"]

    def test_an_unknown_verb_is_refused_with_400(self, client):
        r = client.post("/catalog/capabilities", headers=OP, json={
            "model": "m", "task": "chat", "invocation": "aTeleport"})
        assert r.status_code == 400

    def test_a_binding_to_an_INCAPABLE_model_is_refused(self, client):
        """Without this the Router resolves a model and cannot pick a verb.

        An UNREGISTERED tier on purpose: a slate tier would be refused one
        check earlier (D68, wrong kind of job) and never reach this one."""
        r = client.post("/catalog/bindings", headers=OP, json={
            "tier": f"tier-{uuid.uuid4().hex[:6]}", "task": "image",
            "model": f"test/{uuid.uuid4().hex[:8]}"})
        assert r.status_code == 400
        assert "capability" in r.json()["detail"]

    def test_the_wrong_KIND_of_job_on_a_slate_tier_is_refused(self, client):
        """D68: tier-fast serves chat. Image on it is a mis-click, stopped
        here rather than on a customer's first call."""
        r = client.post("/catalog/bindings", headers=OP, json={
            "tier": "tier-fast", "task": "image",
            "model": f"test/{uuid.uuid4().hex[:8]}"})
        assert r.status_code == 400
        assert "serves 'chat'" in r.json()["detail"]

    def test_binding_APPENDS_and_the_newest_wins(self, client, db):
        model = f"test/{uuid.uuid4().hex[:8]}"
        tier = f"tier-{uuid.uuid4().hex[:6]}"
        client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "chat", "invocation": "acompletion"})

        for _ in range(2):
            assert client.post("/catalog/bindings", headers=OP, json={
                "tier": tier, "task": "chat", "model": model,
            }).status_code == 200

        with db.begin() as c:
            rows = c.execute(text(
                "SELECT count(*) FROM tier_binding WHERE tier = :t"),
                {"t": tier}).scalar_one()
        # TWO rows, not one overwritten: history stays reconstructable.
        assert rows == 2

    def test_a_rate_in_the_WRONG_unit_is_refused(self, client):
        # D67: prices are keyed on the tier. tier-stt ships on the slate.
        r = client.post("/catalog/tier-rates", headers=OP, json={
            "tier": "tier-stt", "task": "transcribe", "unit": "tokens",
            "pricing_mode": "priced", "input_per_1k": "2"})
        assert r.status_code == 400
        assert "minutes" in r.json()["detail"]

    def test_the_model_keyed_price_write_is_GONE(self, client):
        """D67: the route answers 410 and names its successor — a working
        write here would store a number nothing bills against."""
        r = client.post("/catalog/rates", headers=OP, json={
            "model": "m", "task": "chat", "unit": "tokens",
            "pricing_mode": "priced", "input_per_1k": "2"})
        assert r.status_code == 410
        assert "tier-rates" in r.json()["detail"]

    def test_a_per_minute_TIER_rate_is_accepted_and_read_back(
            self, client, db):
        tier = f"tr-{uuid.uuid4().hex[:8]}"
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_catalog (slug, label) VALUES (:t, :t)"),
                {"t": tier})
        r = client.post("/catalog/tier-rates", headers=OP, json={
            "tier": tier, "task": "transcribe", "unit": "minutes",
            "pricing_mode": "priced", "credits_per_unit": "0.4"})
        assert r.status_code == 200, r.text

        got = client.get("/catalog/models", headers=OP).json()["tier_rates"]
        mine = [x for x in got if x["tier"] == tier]
        assert mine and mine[0]["unit"] == "minutes"
        assert mine[0]["pricing_mode"] == "priced"
        with db.begin() as c:
            c.execute(text("DELETE FROM tier_rate_card WHERE tier = :t"),
                      {"t": tier})
            c.execute(text("DELETE FROM tier_catalog WHERE slug = :t"),
                      {"t": tier})

    def test_a_rate_for_an_unregistered_tier_is_refused(self, client):
        r = client.post("/catalog/tier-rates", headers=OP, json={
            "tier": f"ghost-{uuid.uuid4().hex[:8]}", "task": "chat",
            "unit": "tokens", "pricing_mode": "priced", "input_per_1k": "2"})
        assert r.status_code == 400
        assert "tier_catalog" in r.json()["detail"]

    def test_priced_at_zero_is_refused_with_a_usable_reason(self, client):
        r = client.post("/catalog/tier-rates", headers=OP, json={
            "tier": "tier-fast", "task": "chat", "unit": "tokens",
            "pricing_mode": "priced"})
        assert r.status_code == 400
        assert "absorbed" in r.json()["detail"]

    def test_an_unknown_task_is_refused_everywhere(self, client):
        for path, body in (
            ("/catalog/capabilities",
             {"model": "m", "task": "nope", "invocation": "acompletion"}),
            ("/catalog/bindings",
             {"tier": "t", "task": "nope", "model": "m"}),
            ("/catalog/tier-rates",
             {"tier": "tier-fast", "task": "nope", "unit": "tokens",
              "pricing_mode": "unpriced"}),
        ):
            r = client.post(path, headers=OP, json=body)
            assert r.status_code == 400, path

    def test_the_writes_are_operator_gated(self, client):
        for path in ("/catalog/capabilities", "/catalog/bindings",
                     "/catalog/rates", "/catalog/tier-rates"):
            assert client.post(path, json={}).status_code in (401, 403), path


def test_this_suite_is_named_in_the_ci_skip_guard():
    """The hand-maintained R8 list defends itself, the way the others do."""
    ci = (ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_customer_console_catalog.py" in ci
