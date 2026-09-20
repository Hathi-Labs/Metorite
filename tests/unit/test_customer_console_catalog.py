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

    #: The catalog paths §6A.5 actually binds — the COMMERCIAL TERMS. A past
    #: invoice was computed against a binding and a rate, so a mutable one
    #: destroys the audit trail at exactly the moment a customer disputes a
    #: charge, which is the only moment it matters.
    #:
    #: ⚠️ Adding a path here is an edit somebody has to justify in review.
    INSERT_ONLY = (
        "/catalog/bindings",
        "/catalog/rates",
        "/catalog/tier-rates",
        "/catalog/tier-margins",
        "/catalog/credit-price",
    )

    #: ⚠️ **NOT insert-only, and never was.** These write FACTS about a model
    #: rather than commercial terms, and both were mutable before this list
    #: existed: `POST /catalog/profiles` is documented as "the only catalog
    #: write that is not insert-only", and `POST /catalog/capabilities` is an
    #: UPSERT, because correcting what a model can do destroys no audit trail.
    #:
    #: 🔴 `DELETE /catalog/capabilities` joined them on 2026-09-21. Until it
    #: existed a model could enter the catalog with one click and never leave,
    #: so every mis-click was permanent and the tier pickers filled with models
    #: nobody meant to sell. Nobody is billed against a capability, and the
    #: route refuses while a tier still serves from the model.
    #: `/catalog/feed/sync` is reference data from litellm. It writes no
    #: term, nothing billing reads, and a bad sync is one more sync away from
    #: fixed.
    NOT_COMMERCIAL = (
        "/catalog/capabilities",
        "/catalog/profiles",
        "/catalog/feed/sync",
    )

    def _catalog_routes(self) -> list[tuple[str, str]]:
        """Every `(verb, path)` the Console declares under `/catalog/`.

        Read from the AST, not the text — the docstrings SAY 'never UPDATE'
        while explaining the rule, so a grep would match its own prose.
        """
        tree = ast.parse(MAIN_SRC.read_text(encoding="utf-8"))
        out = []
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
                if isinstance(path, str) and "/catalog/" in path:
                    out.append((verb, path))
        return out

    def test_no_route_updates_or_deletes_a_binding_or_a_rate(self):
        bad = [f"{v.upper()} {p}" for v, p in self._catalog_routes()
               if p in self.INSERT_ONLY and v in ("patch", "put", "delete")]
        assert not bad, f"catalog COMMERCIAL writes must be INSERT-only: {bad}"

    def test_every_catalog_path_is_classified_one_way_or_the_other(self):
        """🔴 The fence that keeps the exemption from becoming a loophole.

        This test used to scan every `/catalog/` path and refuse PATCH, PUT and
        DELETE on all of them, which was broader than the rule its own name
        states. Narrowing it to the commercial paths is right — and it would be
        the wrong kind of right if a NEW path could then land unclassified and
        mutable by default. So: every catalog path is on one of the two lists,
        and a new one fails here until somebody decides which.
        """
        # ⚠️ WRITES only. A read decides nothing about mutability, and
        # `GET /catalog/models` would otherwise have to be filed under a
        # contract about writing.
        unclassified = sorted({
            p for v, p in self._catalog_routes()
            if v != "get"
            and p not in self.INSERT_ONLY and p not in self.NOT_COMMERCIAL
        })
        assert not unclassified, (
            "a new /catalog/ path must be declared INSERT_ONLY (a commercial "
            f"term) or NOT_COMMERCIAL (a fact about a model): {unclassified}")

    def test_removing_a_capability_is_gated_no_harder_than_declaring_one(self):
        """The same act, undone. A remove behind `admin` plus an elevation
        window while the declare is plain `editor` would teach an operator to
        reach for the break-glass token to undo their own mis-click."""
        from customer_console.operator_roles import MATRIX

        declare = MATRIX[("POST", "/catalog/capabilities")]
        remove = MATRIX[("DELETE", "/catalog/capabilities")]
        assert remove.min_role == declare.min_role
        assert remove.elevated is declare.elevated is False

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
_MINE = ("test/%", "a/%", "strandco/%")


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
                      "model_capability", "model_profile",
                      # ⚠️ The strand test PLANTS a feed row and a vendor key,
                      # because the offer list is computed from both. Left
                      # behind they would make a later suite see a vendor
                      # nobody installed.
                      "vendor_price_feed"):
            for like in _MINE:
                c.execute(text(
                    f"DELETE FROM {table} WHERE model LIKE :p"),
                    {"p": like})
        c.execute(text(
            "DELETE FROM provider_credential WHERE provider = 'strandco'"))
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


@_DB
class TestRemovingAModelFromTheCatalog:
    """``DELETE /catalog/capabilities`` — the act the page never had.

    🔴 **Why it exists.** A model reaches the operator's Models page, and every
    backup-chain picker on Tiers, because it has a ``model_capability`` row.
    One click on the vendor feed writes one, and the live feed holds about
    4300 models. So the catalog could only ever grow, and a mis-click was
    permanent. Owner report, 2026-09-21.

    ⚠️ **This does NOT widen §6A.5.** Insert-only binds a ``tier_binding`` and
    a rate, because a past invoice was computed against them. A capability is
    a FACT about a model — which is why the declare beside it is already an
    UPSERT rather than an append — and nobody is billed against one.
    """

    def test_a_declared_model_can_be_removed_and_leaves_the_catalog(self, client):
        model = f"test/{uuid.uuid4().hex[:8]}"
        client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "chat", "invocation": "acompletion"})
        assert any(c["model"] == model for c in
                   client.get("/catalog/models", headers=OP).json()["capabilities"])

        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": model})
        assert r.status_code == 200, r.text
        assert r.json()["removed"] == 1

        body = client.get("/catalog/models", headers=OP).json()
        assert not any(c["model"] == model for c in body["capabilities"])

    def test_it_removes_EVERY_task_when_none_is_named(self, client):
        """An operator removing a model means the model. A half-removed model
        is a state nothing on the page can draw."""
        model = f"test/{uuid.uuid4().hex[:8]}"
        for task, verb in (("chat", "acompletion"), ("embed", "aembedding")):
            client.post("/catalog/capabilities", headers=OP, json={
                "model": model, "task": task, "invocation": verb})

        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": model})
        assert r.json()["removed"] == 2
        body = client.get("/catalog/models", headers=OP).json()
        assert not any(c["model"] == model for c in body["capabilities"])

    def test_one_task_can_be_removed_on_its_own(self, client):
        model = f"test/{uuid.uuid4().hex[:8]}"
        for task, verb in (("chat", "acompletion"), ("embed", "aembedding")):
            client.post("/catalog/capabilities", headers=OP, json={
                "model": model, "task": task, "invocation": verb})

        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": model, "task": "embed"})
        assert r.json()["removed"] == 1
        left = [c for c in client.get("/catalog/models", headers=OP).json()
                ["capabilities"] if c["model"] == model]
        assert [c["task"] for c in left] == ["chat"]

    def test_a_model_a_TIER_SERVES_FROM_is_refused_and_the_tiers_are_named(
        self, client, db,
    ):
        """🔴 The fence this endpoint exists behind.

        ``tier_binding`` carries NO foreign key to ``model_capability``, so a
        silent delete leaves ``resolve`` unable to find the verb for a model
        it still resolves to — a 500 on the first customer call, and invisible
        until then. The refusal names the tiers, because the operator's next
        act is on the Tiers page.
        """
        model = f"test/{uuid.uuid4().hex[:8]}"
        tier = f"tier-{uuid.uuid4().hex[:6]}"
        client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "chat", "invocation": "acompletion"})
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_binding (tier, task, model, rank, "
                "effective_from) VALUES (:t, 'chat', :m, 1, "
                "now() - interval '1 day')"), {"t": tier, "m": model})

        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": model})
        assert r.status_code == 400
        assert tier in r.json()["detail"]

        # ⚠️ And it really did not delete. A refusal that still wrote would be
        # the worst of both outcomes.
        assert any(c["model"] == model for c in
                   client.get("/catalog/models", headers=OP).json()["capabilities"])

    def test_a_SUPERSEDED_binding_does_not_block_the_removal(self, client, db):
        """⚠️ Otherwise a catalog could never shrink once anything had ever
        pointed at a model — every historical row would veto for ever.

        The endpoint reads the chain IN FORCE, the same rule
        ``GET /catalog/models`` applies.
        """
        old = f"test/{uuid.uuid4().hex[:8]}"
        new = f"test/{uuid.uuid4().hex[:8]}"
        tier = f"tier-{uuid.uuid4().hex[:6]}"
        for m in (old, new):
            client.post("/catalog/capabilities", headers=OP, json={
                "model": m, "task": "chat", "invocation": "acompletion"})
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_binding (tier, task, model, rank, "
                "effective_from) VALUES "
                "(:t, 'chat', :old, 1, now() - interval '2 days'), "
                "(:t, 'chat', :new, 1, now() - interval '1 day')"),
                {"t": tier, "old": old, "new": new})

        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": old})
        assert r.status_code == 200, r.text
        assert r.json()["removed"] == 1

    def test_removing_one_task_is_not_blocked_by_a_binding_on_ANOTHER(
        self, client, db,
    ):
        model = f"test/{uuid.uuid4().hex[:8]}"
        tier = f"tier-{uuid.uuid4().hex[:6]}"
        for task, verb in (("chat", "acompletion"), ("embed", "aembedding")):
            client.post("/catalog/capabilities", headers=OP, json={
                "model": model, "task": task, "invocation": verb})
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_binding (tier, task, model, rank, "
                "effective_from) VALUES (:t, 'chat', :m, 1, "
                "now() - interval '1 day')"), {"t": tier, "m": model})

        # The embed capability nothing serves from goes.
        assert client.request("DELETE", "/catalog/capabilities", headers=OP,
                              json={"model": model, "task": "embed"}
                              ).status_code == 200
        # The chat one, which a tier serves from, does not.
        assert client.request("DELETE", "/catalog/capabilities", headers=OP,
                              json={"model": model, "task": "chat"}
                              ).status_code == 400

    def test_the_saved_PRICES_survive_the_removal(self, client, db):
        """🔴 Owner decision, 2026-09-21. ``model_profile`` is KEPT.

        The prices are what a past cost figure was computed from, so deleting
        them would make a recorded cost unreconcilable. Re-adding the model
        restores the numbers instead of starting it costs-blind.
        """
        model = f"test/{uuid.uuid4().hex[:8]}"
        client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "chat", "invocation": "acompletion"})
        client.post("/catalog/profiles", headers=OP, json={
            "model": model, "vendor_input_per_1m_usd": "3.500000"})

        client.request("DELETE", "/catalog/capabilities", headers=OP,
                       json={"model": model})

        with db.begin() as c:
            kept = c.execute(text(
                "SELECT vendor_input_per_1m_usd FROM model_profile "
                "WHERE model = :m"), {"m": model}).first()
        assert kept is not None
        assert Decimal(kept[0]) == Decimal("3.5")

        # And re-adding it brings the number back, rather than landing blind.
        client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "chat", "invocation": "acompletion"})
        prof = [p for p in client.get("/catalog/models", headers=OP).json()
                ["profiles"] if p["model"] == model]
        assert Decimal(prof[0]["vendor_input_per_1m_usd"]) == Decimal("3.5")

    def test_removing_a_model_that_was_never_there_is_not_an_error(self, client):
        """The operator asked for it to be gone and it is gone. A 404 would be
        an error for the state they already wanted."""
        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": f"test/{uuid.uuid4().hex[:8]}"})
        assert r.status_code == 200
        assert r.json()["removed"] == 0

    def test_it_writes_an_audit_row_naming_the_model(self, client, db):
        model = f"test/{uuid.uuid4().hex[:8]}"
        client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "chat", "invocation": "acompletion"})
        client.request("DELETE", "/catalog/capabilities", headers=OP,
                       json={"model": model})

        with db.begin() as c:
            row = c.execute(text(
                "SELECT detail FROM control_audit "
                "WHERE action = 'catalog.capability_removed' "
                "ORDER BY created_at DESC LIMIT 1")).first()
        assert row is not None
        assert row[0]["model"] == model

    def test_a_removed_model_is_OFFERED_AGAIN_by_its_vendor(self, client, db):
        """🔴 The strand. Found by driving the real UI, 2026-09-21.

        `feed.available` excluded any model with a `model_capability` row OR a
        `model_profile` row. That was harmless while a profile could only
        belong to a declared model. The moment removal existed and KEPT the
        prices, a removed model was in neither list: gone from the catalog,
        and hidden from the offer list by its own surviving profile. There was
        no way to add it back.
        """
        # A model the feed carries, under a vendor we hold a platform key for.
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO provider_credential (provider, secret_enc) "
                "VALUES ('strandco', 'x') ON CONFLICT DO NOTHING"))
            c.execute(text(
                "INSERT INTO vendor_price_feed "
                "(model, provider, mode, task, invocation, context_window, "
                " vendor_input_per_1m_usd, vendor_output_per_1m_usd) "
                "VALUES ('strandco/one', 'strandco', 'chat', 'chat', "
                "        'acompletion', 8192, 1, 2) "
                "ON CONFLICT (model) DO NOTHING"))

        def offered() -> bool:
            body = client.get("/catalog/models", headers=OP).json()
            return any(f["model"] == "strandco/one"
                       for f in body["feed"]["available"])

        assert offered(), "the fixture is wrong: it must start on offer"

        # Add it the way the page does — a capability AND a profile.
        client.post("/catalog/capabilities", headers=OP, json={
            "model": "strandco/one", "task": "chat",
            "invocation": "acompletion"})
        client.post("/catalog/profiles", headers=OP, json={
            "model": "strandco/one", "vendor_input_per_1m_usd": "1"})
        assert not offered(), "a declared model must leave the offer list"

        # Remove it. The profile survives by design — and the model must come
        # back onto the shelf it came from.
        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": "strandco/one"})
        assert r.status_code == 200, r.text
        assert offered(), (
            "a removed model is in NEITHER list — its kept profile hid it "
            "from its own vendor's shelf")

    def test_a_STAGED_binding_BLOCKS_the_removal(self, client, db):
        """🔴 Found in adversarial review, 2026-09-21. A staged chain is
        ARMED, not superseded.

        ``POST /catalog/bindings`` takes a future ``effective_from`` on purpose
        — it stages a change without taking effect — and ``bind_tier`` checked
        the capability on the day it was staged. Remove the model in between
        and nothing says a word: ``resolve_chain`` returns it on the date,
        ``resolve_invocation`` finds no verb, and the 500 lands on a customer's
        first call. The ``unserved`` gap cannot show it either, because that
        reads the in-force chain alone.
        """
        model = f"test/{uuid.uuid4().hex[:8]}"
        tier = f"tier-{uuid.uuid4().hex[:6]}"
        client.post("/catalog/capabilities", headers=OP, json={
            "model": model, "task": "chat", "invocation": "acompletion"})
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_binding (tier, task, model, rank, "
                "effective_from) VALUES (:t, 'chat', :m, 1, "
                "now() + interval '10 days')"), {"t": tier, "m": model})

        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": model})
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert tier in detail
        # ⚠️ And it SAYS which kind, because "re-point it" means a different
        # act for a chain that has not started yet.
        assert "staged" in detail

    def test_a_typed_task_is_refused_rather_than_answering_removed_zero(
        self, client,
    ):
        """A 200 with ``removed: 0`` reads as success, so an operator who
        mistypes believes the model is gone. The declare refuses an unknown
        task; so does this."""
        r = client.request("DELETE", "/catalog/capabilities", headers=OP,
                           json={"model": "test/x", "task": "chatt"})
        assert r.status_code == 400
        assert "chatt" in r.json()["detail"]

    def test_the_offer_list_SAYS_we_already_hold_prices_for_a_model(
        self, client, db,
    ):
        """🔴 Found in adversarial review, 2026-09-21. The other half of
        "the prices are kept".

        A removed model returns to its vendor's shelf with ``model_profile``
        still in the database. The page's Add sends a capability POST **and** a
        profile POST built from litellm alone, and ``POST /catalog/profiles``
        replaces the WHOLE row — so re-adding would write NULL over the
        off-peak rates, the long-context tier, the label and the description,
        and every later call would cost at the peak rate with nothing said.

        The browser cannot know which shelf rows we have priced. This flag is
        how it finds out, and `FeedAvailable` skips the profile write on it.
        """
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO provider_credential (provider, secret_enc) "
                "VALUES ('strandco', 'x') ON CONFLICT DO NOTHING"))
            c.execute(text(
                "INSERT INTO vendor_price_feed "
                "(model, provider, mode, task, invocation, context_window, "
                " vendor_input_per_1m_usd, vendor_output_per_1m_usd) "
                "VALUES ('strandco/two', 'strandco', 'chat', 'chat', "
                "        'acompletion', 8192, 1, 2) "
                "ON CONFLICT (model) DO NOTHING"))

        def row():
            body = client.get("/catalog/models", headers=OP).json()
            return next((f for f in body["feed"]["available"]
                         if f["model"] == "strandco/two"), None)

        # Never seen before: nothing of ours to protect.
        assert row() is not None
        assert row()["profiled"] is False

        # Declare it, price it — including an OFF-PEAK rate, which is exactly
        # the field the feed does not carry and a re-add would erase.
        client.post("/catalog/capabilities", headers=OP, json={
            "model": "strandco/two", "task": "chat",
            "invocation": "acompletion"})
        client.post("/catalog/profiles", headers=OP, json={
            "model": "strandco/two",
            "vendor_input_per_1m_usd": "1",
            "vendor_input_offpeak_per_1m_usd": "0.22",
            "offpeak_start_utc": "16:30", "offpeak_end_utc": "00:30"})

        client.request("DELETE", "/catalog/capabilities", headers=OP,
                       json={"model": "strandco/two"})

        back = row()
        assert back is not None, "the removed model must return to the shelf"
        assert back["profiled"] is True, (
            "the shelf must say we already hold prices, or the browser "
            "re-saves a litellm-only profile over them")

        # And the off-peak rate really is still there to protect.
        with db.begin() as c:
            kept = c.execute(text(
                "SELECT vendor_input_offpeak_per_1m_usd FROM model_profile "
                "WHERE model = 'strandco/two'")).scalar()
        assert Decimal(kept) == Decimal("0.22")

    def test_it_is_operator_gated(self, client):
        assert client.request(
            "DELETE", "/catalog/capabilities", json={"model": "x/y"},
        ).status_code in (401, 403)
