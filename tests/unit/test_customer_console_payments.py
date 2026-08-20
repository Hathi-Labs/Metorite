"""CP-9 + SC-4g — the money -> entitlement path, against a REAL Postgres.

Spec: ``project-docs/specs/customer_console.md`` §6 **CP-9** (done-when 1-17) ·
``project-docs/specs/subscription_console.md`` **SC-4g** (done-when 1-8; clause
9 is the owner-gated rehearsal and is deliberately NOT claimed here) ·
``work_plan.md`` §1 R7/R8.

⚠️ **R8 binds this file, and it is not decoration.** Its subject is a
CHECK-constrained state machine, a UNIQUE idempotency key, a partial-index
race and a fulfilment **transaction** — the exact class of thing a hermetic
fake agrees with and a real server rejects. It skips loudly without a database
and ``pr-check.yml``'s hand-maintained skip-guard names it, so CI cannot go
back to skipping it while reporting green (CP-3's finding, and
``test_this_suite_is_named_in_the_ci_skip_guard`` below is what keeps that
true from inside the suite).

⚠️ **No Razorpay account is involved, and none may be** — creating one is
OWNER-GATE even in test mode (customer_console.md §8 gate 3). Every capture
here runs through ``payments.FakeProvider``, whose signature is the **real**
HMAC-SHA256 over the raw body: only the network is fake. **SC-4g clause 2's
test-mode capture rehearsal is therefore NOT met by this suite**, is not
claimed to be, and is handed to the owner scripted.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_customer_console_payments.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from customer_console import auth, credits, lifecycle, payments, store
from customer_console.keys import ENV_DISCOUNT, mint_key, split_key
from customer_console.main import app
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit._customer_console_ladder import (
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
    ladder,
    mint_deployment_key,
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
INTERNAL = "test-internal-token"
OP = {"Authorization": f"Bearer {TOKEN}"}

#: The four tables no organization-key route may write (§9.3(4)). Named once:
#: the transitive fence, the create-order snapshot and the equivalence fence
#: all read this, so widening the class is one edit and not three.
ENTITLEMENT_TABLES = ("org_subscription", "seat_grant", "seat_assignment",
                      "credit_ledger")

#: THE carve-out, written as a `(route function, callee)` PAIR — never a
#: sentence like "redeem may write seats", which is a general licence. Every
#: other callee on that route, and every other org-key route, stays red.
#:
#: ⚠️ It lives HERE, in the test module, and never in application code: a
#: production allow-list is a switch somebody flips under deadline.
FULFIL_ALLOW_LIST: frozenset[tuple[str, str]] = frozenset({
    ("redeem_discount_code", "payments.fulfil"),
})

#: ⚠️ **A SECOND exemption, and it is a DEVIATION from CP-9 §9.3(4) as written
#: — declared here rather than resolved by narrowing the fence.**
#:
#: §9.3(4) says *"exactly ONE edge is permitted"*. Built against the tree, the
#: fence is red on a **second, pre-existing** edge the ticket did not know
#: about: ``POST /v1/chat/completions`` is organization-key authenticated
#: (CP-3) and CP-6 made it write the metering **draw** —
#: ``chat_completions -> store.record_usage -> store.add_credit`` -> INSERT
#: INTO ``credit_ledger``. That shipped on 2026-08-12, long before CP-9.
#:
#: Why it is exempted rather than fixed, and why it is not the thing CP-3's
#: lesson forbids: CP-3's defect was the metered party **reporting its own
#: usage** — ``/usage/record`` under the customer's key, where a negative
#: ``billed_credits`` minted 100,000 credits. Here the customer's key opens the
#: route but **our infrastructure decides the amount**, from tokens the Router
#: itself counted and a rate card the customer cannot reach; the draw is
#: negative-only and is written in the same transaction as the ``usage_event``
#: whose ``(organization_id, request_id)`` key makes it idempotent.
#:
#: Why it is a SEPARATE constant rather than a second member of the list above:
#: ``test_the_fulfil_allow_list_has_exactly_one_entry`` is contents-pinned by
#: the ticket, and the one thing worse than an exemption is an exemption
#: smuggled into a list the ticket counts. This one carries its own
#: count-and-contents fence, so it cannot grow either.
#:
#: **Recorded as a finding for the board** (CLAUDE.md §5: existing violations
#: are findings, not refactors) — and stated in the PR rather than left for a
#: reviewer to discover.
METERING_EXEMPTION: frozenset[tuple[str, str]] = frozenset({
    ("chat_completions", "store.add_credit"),
})

#: What the walk may cross. Two entries, two arguments, two fences.
PERMITTED_EDGES = FULFIL_ALLOW_LIST | METERING_EXEMPTION

_PACKAGE = Path(__file__).resolve().parents[2] / (
    "apps/services/customer_console/customer_console"
)


def _payments_migration() -> Path:
    """Find this slice's migration by CONTENT, never by number or by name.

    Two reasons, and the second is the load-bearing one:

    * ``test_migration_prefixes.py`` forbids a Console suite from naming a
      migration path at all — the hand-transcribed ladder is how five suites
      went stale and one of them failed four assertions away from the cause;
    * **R1**: the number is taken at build time and *re-checked at merge*. A
      suite that hard-coded ``007`` would have to be edited by whoever
      renumbers on a collision — i.e. the fence would break in the exact
      situation it exists to survive. Content is stable across a renumber.

    The same idiom ``test_crm_migration.py`` uses to find BOTH CRM migrations.
    """
    for path in ladder():
        text_ = Path(path).read_text(encoding="utf-8")
        if "CREATE TABLE IF NOT EXISTS payment_order" in text_:
            return Path(path)
    raise AssertionError(
        "no migration on the Customer Console ladder creates payment_order"
    )


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema():
    """Build the schema — and replay the ladder, proving 007 is idempotent.

    Applied TWICE on purpose (done-when 1). Every migration is written
    ``IF NOT EXISTS``; a claim of replay-safety that holds only by reading the
    DDL is unenforceable, and the deploy replays the whole ladder every time.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def fake():
    """The fake provider, installed for the duration of one test."""
    provider = payments.FakeProvider()
    payments.set_provider(provider)
    yield provider
    payments.set_provider(None)


@pytest.fixture
def client(monkeypatch, fake):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    return TestClient(app)


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture(autouse=True)
def _box():
    """The deployment every org here is provisioned onto (MT-1j slice 4).

    Autouse and per-test: ``_new_org`` is a plain function taking a ``client``,
    and this suite's subject is money, not placement.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        ensure_deployment(conn)
    eng.dispose()


def _new_org(client, prefix: str = "pay", *, billing_state: str = "KA",
             owner_email: str | None = None) -> str:
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "Acme Pumps",
        "owner_email": owner_email or f"owner@{slug}.test",
        "gstin": "29ABCDE1234F1Z5", "billing_state": billing_state,
        "core_seats": 3, "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
    })
    assert r.status_code == 200, r.text
    return slug


def _org_id(client, slug: str) -> str:
    r = client.get(f"/billing/summary?org_slug={slug}", headers=OP)
    assert r.status_code == 200, r.text
    return r.json()["organization_id"]


def _org_key(client, slug: str) -> str:
    r = client.post("/keys", headers=OP, json={"org_slug": slug,
                                               "label": "checkout"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _lifecycle(client, slug: str, target: str) -> None:
    r = client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": slug, "target": target})
    assert r.status_code == 200, r.text


@pytest.fixture
def org(client):
    """A provisioned organization and its own read-mostly key."""
    slug = _new_org(client)
    return {"slug": slug, "id": _org_id(client, slug),
            "key": _org_key(client, slug)}


def _create_order(client, key: str, *, plan: str = "sales", quantity: int = 2):
    return client.post(
        "/billing/orders", headers=_headers(key),
        json={"lines": [{"plan_slug": plan, "quantity": quantity}]},
    )


def _order(client, key: str, **kwargs) -> dict:
    r = _create_order(client, key, **kwargs)
    assert r.status_code == 200, r.text
    return r.json()


def _issue_code(client, **body) -> dict:
    payload = {"label": "customer zero", "kind": "percent",
               "percent_bp": 10000, **body}
    r = client.post("/discounts", headers=OP, json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _provider_order_id(db, order_id: str) -> str:
    with db.begin() as c:
        return c.execute(
            text("SELECT provider_order_id FROM payment_order WHERE id = :i"),
            {"i": order_id},
        ).scalar_one()


def _capture(client, fake, db, order: dict, *, event_id: str | None = None,
             kind: str = "payment.captured", amount: int | None = None,
             customer_id: str | None = "cust_FAKE0001"):
    """Deliver a signed capture for an order, exactly as Razorpay would."""
    raw, headers = fake.capture_event(
        provider_order_id=_provider_order_id(db, order["id"]),
        amount_paise=order["total_paise"] if amount is None else amount,
        event_id=event_id or f"evt_{uuid.uuid4().hex[:12]}",
        kind=kind, customer_id=customer_id,
    )
    return client.post("/billing/webhooks/razorpay", content=raw,
                       headers=headers)


def _uncommented(path: Path) -> str:
    """Source with comment lines dropped.

    A structural scan that reads comments finds the WORD it is looking for in
    the sentence explaining why the word is absent — which passes a fence for
    the opposite of the reason it exists.
    """
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )


def _rows(db, table: str, org_id: str) -> list[dict]:
    with db.begin() as c:
        return [
            dict(r._mapping) for r in c.execute(
                text(f"SELECT * FROM {table} WHERE organization_id = :o"),
                {"o": org_id},
            )
        ]


def _snapshot(db, org_id: str) -> dict[str, int]:
    """Row counts for the four tables a checkout must not touch."""
    with db.begin() as c:
        return {
            table: int(c.execute(
                text(f"SELECT count(*) FROM {table} "
                     "WHERE organization_id = :o"),
                {"o": org_id},
            ).scalar_one())
            for table in ENTITLEMENT_TABLES
        }


# ── Clause 1 — the tables, their constraints, and integer paise ─────────────

class TestTheSchema:
    def test_the_ladder_replays_cleanly_with_007(self, db):
        """Idempotence proven against a real server, not against our DDL."""
        with db.begin() as c:
            apply_ladder(c)

    def test_every_money_column_is_an_integer_of_paise(self, db):
        """Structural, over the LIVE catalog — the fence §9.2 names.

        A ``NUMERIC``/``REAL``/``DOUBLE`` money column in these tables is how
        Rs 1,800.00 becomes Rs 1,799.99 somewhere nobody looks. Read from
        ``information_schema`` rather than from the migration text, so a column
        added by a later migration is covered without anyone remembering.
        """
        with db.begin() as c:
            offenders = c.execute(
                text(
                    """
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN ('payment_order', 'payment_order_line',
                                         'payment_event', 'discount_code',
                                         'discount_redemption')
                      AND (column_name LIKE '%_paise'
                           OR column_name LIKE '%amount%')
                      AND data_type NOT IN ('bigint', 'integer', 'smallint')
                    """
                )
            ).all()
        assert offenders == [], f"non-integer money columns: {offenders}"

    def test_no_route_in_the_checkout_path_builds_an_amount_from_a_float(self):
        """The other half of §9.2's fence: no float arithmetic in the path.

        ``payments.paise`` refuses a float outright, and this scan is what
        stops a caller computing one before it gets there — a ``float(`` or a
        ``0.18`` in the checkout path is a rounding bug with a customer's
        invoice on the other end.
        """
        body = _uncommented(_PACKAGE / "payments.py")
        assert "float(" not in body
        # A percentage as a decimal literal is the shape that rounds wrong.
        assert not re.search(r"\b0\.\d+\b", body), "no float rates in the path"

    def test_paise_refuses_a_float(self):
        with pytest.raises(TypeError):
            payments.paise(1800.00)
        assert payments.paise(Decimal("1800.00")) == 180000
        assert payments.paise(Decimal("0.00")) == 0

    @pytest.mark.parametrize("bad", ["pending", "refunded", "", "CAPTURED"])
    def test_the_status_check_refuses_a_state_off_the_machine(self, db, org,
                                                              bad):
        with pytest.raises((IntegrityError, DBAPIError)), db.begin() as c:
            c.execute(
                text(
                    "INSERT INTO payment_order (organization_id, status, "
                    "provider, gross_paise, taxable_paise, gst_paise, "
                    "total_paise, expires_at) VALUES (:o, :s, 'none', 0, "
                    "0, 0, 0, now())"
                ),
                {"o": org["id"], "s": bad},
            )

    def test_the_arithmetic_check_refuses_a_total_that_does_not_add_up(
        self, db, org
    ):
        """gross - discount = taxable, taxable + gst = total. In the DATABASE.

        The columns are redundant by design — they are the invoice's own lines
        — and redundancy without a constraint is just an opportunity to
        disagree.
        """
        with pytest.raises((IntegrityError, DBAPIError)), db.begin() as c:
            c.execute(
                text(
                    "INSERT INTO payment_order (organization_id, provider,"
                    " gross_paise, discount_paise, taxable_paise, "
                    "gst_paise, total_paise, expires_at) "
                    "VALUES (:o, 'none', 1000, 0, 1000, 180, 9999, now())"
                ),
                {"o": org["id"]},
            )

    def test_the_event_id_is_the_primary_key(self, db):
        """Transport dedup is an INDEX, not an ``if`` somebody can skip."""
        with db.begin() as c:
            key = c.execute(
                text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                    " AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = 'payment_event'::regclass "
                    "  AND i.indisprimary"
                )
            ).scalar_one()
        assert key == "provider_event_id"


# ── Clause 2 — the state machine, parametrised over the whole state set ─────

#: §9.2's graph, transcribed HERE rather than read from
#: ``payments._ORDER_TRANSITIONS``. Reading the module's own dict would make the
#: fence below say *"the graph equals itself"* — the vacuous shape — so this is
#: deliberately a **second copy**, and the only other copy is the ``CHECK``
#: constraint in the migration (compared separately, against a real server).
#:
#: Two edges leave ``created`` that the spec's one-line chain
#: (``created → attempted → captured|failed|abandoned``) does not draw, and both
#: are load-bearing rather than slack: ``created → captured`` is SC-4g's ₹0 path,
#: which never reaches a provider and therefore never *attempts* anything, and
#: ``created → failed`` is a provider reporting a failure against an order we
#: never marked attempted. ``created → abandoned`` is expiry, which §9.2 states
#: outright. Widening this dict is how the fence stops meaning anything, so a
#: fifth edge belongs in the ticket first.
SPEC_ORDER_GRAPH: dict[str, frozenset[str]] = {
    "created": frozenset({"attempted", "captured", "failed", "abandoned"}),
    "attempted": frozenset({"captured", "failed", "abandoned"}),
    "captured": frozenset(),
    "failed": frozenset(),
    "abandoned": frozenset(),
}


class TestTheStateMachine:
    def test_the_database_vocabulary_is_exactly_the_graphs(self, db):
        """The CHECK constraint and the graph are the only two copies, compared.

        A state added to one and not the other is either a state the machine
        can reach and the database rejects (a 500 on a real payment) or one the
        database accepts and the machine has no edges for (an order nothing can
        move). Both are silent until they are expensive.
        """
        with db.begin() as c:
            definition = c.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'payment_order'::regclass "
                    "  AND contype = 'c' "
                    "  AND pg_get_constraintdef(oid) LIKE '%status%'"
                )
            ).scalar_one()
        assert set(re.findall(r"'(\w+)'::text", definition)) == (
            payments.ORDER_STATES
        )

    @pytest.mark.parametrize("state", sorted(payments.ORDER_STATES))
    @pytest.mark.parametrize("target", sorted(payments.ORDER_STATES))
    def test_every_state_pair_agrees_with_the_specs_graph(self, state, target):
        """Clause 2, and **all 25 pairs assert** — repaired 2026-08-18 (F1).

        As first written this test asserted only inside
        ``if state in ORDER_TERMINAL_STATES``, so the ten pairs whose SOURCE is
        non-terminal asserted **nothing at all**: ``created → created``,
        ``attempted → created`` and ``attempted → attempted`` — the three
        off-graph edges a non-terminal state could grow — were fenced nowhere in
        the tree. A parametrisation with no assertion on 40 percent of its cases
        is the shape that reports 25 green tests while testing 15.

        Both halves of clause 2 now ride here, over the FULL cross product:

        * the predicate equals :data:`SPEC_ORDER_GRAPH` — an independent
          transcription of §9.2, so widening *or* narrowing the shipped graph
          goes red;
        * every pair off that graph is **refused by name** through
          ``assert_order_transition``, which is the half a boolean check cannot
          see, and a terminal source says so in the message.

        Red-first evidence (run and reverted during the repair): admitting
        ``created → created`` in ``payments._ORDER_TRANSITIONS`` fails exactly
        one case — ``[created-created]`` — and nothing else.
        """
        expected = target in SPEC_ORDER_GRAPH[state]
        assert payments.can_order_transition(state, target) is expected, (
            f"{state!r} -> {target!r} disagrees with §9.2's graph"
        )
        if expected:
            payments.assert_order_transition(state, target)
            return
        with pytest.raises(lifecycle.TransitionRefused) as exc:
            payments.assert_order_transition(state, target)
        if state in payments.ORDER_TERMINAL_STATES:
            assert "terminal" in str(exc.value)

    def test_the_graph_the_fence_reads_is_the_graph_the_module_ships(self):
        """The transcription above is compared to the module's dict ONCE.

        Kept separate from the parametrisation on purpose: this is the place a
        renamed state or a fifth key is caught, and keeping it here means the
        25 cases above never degrade into ``graph == graph``.
        """
        assert set(SPEC_ORDER_GRAPH) == payments.ORDER_STATES
        assert {
            state for state, targets in SPEC_ORDER_GRAPH.items() if not targets
        } == payments.ORDER_TERMINAL_STATES

    def test_the_terminal_states_are_the_three_named(self):
        assert {
            "captured", "failed", "abandoned",
        } == payments.ORDER_TERMINAL_STATES

    def test_a_move_off_the_graph_is_refused_by_name(self):
        with pytest.raises(lifecycle.TransitionRefused) as exc:
            payments.assert_order_transition("captured", "created")
        assert "terminal" in str(exc.value)

    def test_no_code_path_drives_an_order_to_failed(self):
        """**Nothing writes `failed`** — the P0 repair, pinned structurally.

        Added 2026-08-19. The state stays on §9.2's graph, because an explicit
        customer cancel is a real order-level failure the surface half may add;
        what the repair removed is the *webhook* driving it, which turned a
        retryable attempt failure into a dead order. A behavioural fence
        (``test_a_failed_attempt_then_a_successful_capture_fulfils``) proves the
        webhook path; this one is what stops a second, quieter writer appearing
        somewhere else in the package.

        Read from the AST rather than by grep, and a computed ``target=`` is a
        failure rather than an invisible pass — a fence that cannot see its
        subject is the failure mode F5 named one round ago.
        """
        assert "failed" in payments.ORDER_STATES, (
            "the STATE survives; only its writer was removed"
        )
        targets: set[str] = set()
        for path in sorted(_PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", ""))
                if name != "transition_order":
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "target":
                        continue
                    assert isinstance(keyword.value, ast.Constant), (
                        f"{path.name}: a COMPUTED transition target is "
                        "exactly what this fence cannot see"
                    )
                    targets.add(keyword.value.value)
        assert targets == {"captured", "abandoned"}, (
            f"the only two states anything drives an order to; got {targets}"
        )


# ── Clause 3 — creating an order moves nothing ─────────────────────────────

class TestCreatingAnOrder:
    def test_an_order_changes_no_balance_no_seat_no_subscription(
        self, client, db, org
    ):
        """Snapshot and diff all four tables around the write (clause 3)."""
        before = _snapshot(db, org["id"])
        order = _order(client, org["key"])
        after = _snapshot(db, org["id"])

        assert before == after, (
            "creating an order wrote an entitlement row: "
            f"{before} -> {after}"
        )
        assert order["status"] == "created"
        assert order["provider"] == "razorpay"
        # 2 x Rs 600 = Rs 1,200 -> 120000 paise, + 18 percent GST.
        assert order["gross_paise"] == 120000
        assert order["gst_paise"] == 21600
        assert order["total_paise"] == 141600

    @pytest.mark.parametrize("plan", ["no_such_plan", "rnd", "support"])
    def test_an_unknown_or_inactive_plan_is_400(self, client, org, plan):
        """`rnd`/`support` are seeded INACTIVE — the checkout cannot sell them."""
        r = _create_order(client, org["key"], plan=plan)
        assert r.status_code == 400, r.text

    def test_an_order_with_nothing_to_pay_is_refused(self, client, org):
        """`company` is Rs 0 and is not sold; an order collecting nothing is not
        a purchase. The Rs 0 path is reached by REDEEMING, never by ordering
        only free rows."""
        r = _create_order(client, org["key"], plan="company")
        assert r.status_code == 400, r.text

    def test_the_gst_split_follows_the_customers_state(self, client, db):
        """Snapshotted onto the order, never joined at render time (SC-5e)."""
        home = _new_org(client, "home", billing_state=payments.home_state())
        away = _new_org(client, "away", billing_state="MH")
        assert _order(client, _org_key(client, home))["gst_split"] == (
            "cgst_sgst"
        )
        assert _order(client, _org_key(client, away))["gst_split"] == "igst"


# ── Clause 4 — the transitive fence and its ONE named carve-out ────────────
#
# Structural, not an example test: it walks `app.routes` x their dependency
# trees x the CALL GRAPH of the store/payments functions those routes reach.
# A route-body scan would pass while `redeem -> fulfil -> store.grant_seats`
# wrote seats two hops down.

def _package_modules(package: Path = _PACKAGE) -> dict[str, ast.Module]:
    """Parse a package's modules. ``package`` is a parameter for ONE reason.

    The fence machinery below has to be testable against a graph whose shape we
    chose, or its own depth is unfenced — which is exactly what finding F5
    caught: narrowing the walk to depth 1 left all 105 tests green. The default
    is the real package and every production caller uses it.
    """
    return {
        path.stem: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(package.glob("*.py"))
        if path.stem != "__init__"
    }


def _module_aliases(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """``(module alias -> module, imported name -> qualified name)``."""
    modules: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "customer_console":
                for alias in node.names:
                    modules[alias.asname or alias.name] = alias.name
            elif node.module.startswith("customer_console."):
                owner = node.module.split(".")[-1]
                for alias in node.names:
                    names[alias.asname or alias.name] = f"{owner}.{alias.name}"
    return modules, names


_WRITE = re.compile(
    r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(" + "|".join(ENTITLEMENT_TABLES)
    + r")\b",
    re.IGNORECASE,
)


def _call_graph(
    package: Path = _PACKAGE,
) -> tuple[dict[str, set[str]], set[str]]:
    """``(edges, writers)`` over the whole package, by qualified name."""
    edges: dict[str, set[str]] = {}
    writers: set[str] = set()
    trees = _package_modules(package)
    local: dict[str, set[str]] = {
        module: {
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for module, tree in trees.items()
    }

    for module, tree in trees.items():
        module_aliases, name_aliases = _module_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            qualified = f"{module}.{node.name}"
            source = ast.unparse(node)
            if _WRITE.search(source):
                writers.add(qualified)
            callees: set[str] = set()
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                callee = _resolve_callee(
                    call.func, module, module_aliases, name_aliases, local,
                )
                if callee:
                    callees.add(callee)
            edges[qualified] = callees
    return edges, writers


def _resolve_callee(func, module, module_aliases, name_aliases, local):
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        owner = module_aliases.get(func.value.id)
        return f"{owner}.{func.attr}" if owner else None
    if isinstance(func, ast.Name):
        if func.id in local.get(module, set()):
            return f"{module}.{func.id}"
        return name_aliases.get(func.id)
    return None


def _org_key_routes() -> list[str]:
    """Every route function a CUSTOMER's own key opens, derived from the app.

    Derived from ``auth.ORGANIZATION_KEY_DEPENDENCIES`` and the live dependency
    tree — never a hand-list, because the failure mode of forgetting is a new
    customer-reachable surface nobody fenced.
    """
    def opens(dependant) -> bool:
        if dependant.call in auth.ORGANIZATION_KEY_DEPENDENCIES:
            return True
        return any(opens(sub) for sub in dependant.dependencies)

    return [
        route.endpoint.__name__
        for route in app.routes
        if isinstance(route, APIRoute) and opens(route.dependant)
    ]


def _walk(
    edges: dict[str, set[str]],
    writers: set[str],
    routes: list[str],
    permitted: frozenset[tuple[str, str]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Every ``(route, path-to-a-writer)`` reachable from ``routes``.

    ⚠️ **The depth of this loop is the whole fence**, which is why it is a
    named function taking its graph rather than a closure over the real one
    (finding F5, 2026-08-18): narrowing it to direct callees left every one of
    the 105 tests green, because
    ``test_no_org_key_route_writes_an_entitlement_or_ledger_row`` asserts an
    EMPTY list — and an empty list is what a walk that goes nowhere returns.
    Two fences below feed it graphs whose answer is known and non-empty:
    a synthetic 3-hop chain, and the real tree with nothing allow-listed.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    for route in routes:
        start = f"main.{route}"
        seen = {start}
        stack = [(start, (start,))]
        while stack:
            node, path = stack.pop()
            for callee in sorted(edges.get(node, ())):
                if (route, callee) in permitted:
                    continue
                if callee in writers:
                    found.append((route, (*path, callee)))
                if callee not in seen and callee in edges:
                    seen.add(callee)
                    stack.append((callee, (*path, callee)))
    return found


def _reachable(start: str, edges: dict[str, set[str]]) -> set[str]:
    """Everything ``start`` can reach, transitively. Excludes ``start``."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        for callee in edges.get(stack.pop(), ()):
            if callee not in seen:
                seen.add(callee)
                stack.append(callee)
    return seen


def _violations() -> list[tuple[str, tuple[str, ...]]]:
    edges, writers = _call_graph()
    return _walk(edges, writers, _org_key_routes(), PERMITTED_EDGES)


#: Three modules whose only interesting property is a writer **three hops**
#: from a route. Written as real source and parsed by the real
#: :func:`_call_graph`, so the self-test below covers the graph builder's
#: cross-module resolution as well as the walk — a hand-built ``edges`` dict
#: would have tested the walk against our own idea of what the parser produces.
#:
#: The imports say ``customer_console`` because that is the package prefix
#: :func:`_module_aliases` resolves; nothing here is ever imported, only parsed.
_SYNTHETIC_PACKAGE: dict[str, str] = {
    "main.py": (
        "from customer_console import helpers\n"
        "\n"
        "\n"
        "def synthetic_route():\n"
        "    return helpers.helper()\n"
    ),
    "helpers.py": (
        "from customer_console import store\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return helper2()\n"
        "\n"
        "\n"
        "def helper2():\n"
        "    return store.grant_seats()\n"
    ),
    "store.py": (
        "from sqlalchemy import text\n"
        "\n"
        "\n"
        "def grant_seats():\n"
        '    return text("INSERT INTO seat_grant (organization_id) '
        'VALUES (:org)")\n'
    ),
}


class TestNoOrgKeyRouteWritesAnEntitlement:
    def test_no_org_key_route_writes_an_entitlement_or_ledger_row(self):
        """CP-3's lesson, widened and made TRANSITIVE (§9.3(4), done-when 4).

        Mutation evidence (run and reverted during the build): pointing
        ``POST /billing/seats`` at the organization-key dependency turns this
        red with the path ``main.assign_seat -> store.try_assign_seat``, which
        is TWO hops — a route-body scan would have stayed green.
        """
        assert _violations() == [], (
            "an organization-key route reaches a writer of "
            f"{ENTITLEMENT_TABLES}: {_violations()}"
        )

    def test_the_fulfil_allow_list_has_exactly_one_entry(self):
        """Count AND contents (hardened at re-audit).

        A count-only assertion passes on a SWAPPED pair such as
        ``("create_order", "payments.fulfil")`` — which is the mutation that
        would matter, because it licenses the one route that must never grant.

        There are exactly two authorised ways to move value in this service:
        **issue a discount code** (``Operator``), or **capture a payment**
        (a signature-verified webhook). If you are here to add a third, that is
        a decision for the ticket, not for this list.
        """
        assert frozenset({
            ("redeem_discount_code", "payments.fulfil"),
        }) == FULFIL_ALLOW_LIST

    def test_the_metering_exemption_has_exactly_one_entry(self):
        """The DECLARED deviation, pinned so it cannot grow either.

        ``POST /v1/chat/completions`` is organization-key authenticated (CP-3)
        and CP-6 made it write the metering draw. That predates CP-9 by six
        days and is not what CP-3's lesson forbids — the customer's key opens
        the route, but OUR infrastructure decides the amount, from tokens the
        Router counted. It is exempted by NAME, with its own count-and-contents
        fence, because an exemption smuggled into the list the ticket counts is
        worse than an exemption argued in the open.

        If you are here to add a second entry: the answer is almost certainly
        that the write belongs behind the internal token, which is what
        ``/usage/record`` had to become after verification minted 100,000
        credits through it.
        """
        assert frozenset({
            ("chat_completions", "store.add_credit"),
        }) == METERING_EXEMPTION

    def test_the_metering_exemption_is_still_needed_and_still_that_shape(self):
        """A dead exemption is one nobody notices has stopped being true.

        Asserted from the graph: the exempted edge really is reachable, really
        does end at a ``credit_ledger`` writer, and the route it hangs off is
        really the Router's — so if CP-4b or a later ticket moves the draw
        behind the internal token, this goes red and the exemption is deleted
        rather than inherited.
        """
        edges, writers = _call_graph()
        assert "store.record_usage" in edges["main.chat_completions"]
        assert "store.add_credit" in edges["store.record_usage"]
        assert "store.add_credit" in writers
        assert "chat_completions" in _org_key_routes()

    def test_the_fence_actually_walks_into_the_store(self):
        """Guards the fence itself: a walk that stops at depth 1 proves nothing.

        ``payments.fulfil -> store.grant_seats`` must be an EDGE this graph
        knows about, or the transitive claim above is vacuous — the failure
        mode where a green fence and no fence are the same thing.
        """
        edges, writers = _call_graph()
        assert "store.grant_seats" in edges["payments.fulfil"]
        assert "store.grant_seats" in writers
        assert "store.activate_subscription" in writers
        assert "store.add_credit" in writers

    def test_the_walk_reports_a_three_hop_chain_on_a_graph_we_built(
        self, tmp_path
    ):
        """A self-test of the fence MACHINERY (finding F5, 2026-08-18).

        ``test_the_fence_actually_walks_into_the_store`` guards the *graph* —
        that ``payments.fulfil -> store.grant_seats`` is an edge at all. It does
        not guard the **walk**, and the two are different claims: narrowing
        :func:`_walk` to direct callees left every one of the 105 tests green,
        because the fence that rides it asserts an EMPTY list and a walk that
        goes nowhere returns one.

        So the walk is run against a graph whose answer is known and NOT empty:
        three synthetic modules parsed by the real :func:`_call_graph`, with a
        writer three hops from the route. A depth-1 walk finds nothing here and
        this fence says so; the real tree can never make that argument, because
        on the real tree the correct answer is ``[]``.
        """
        package = tmp_path / "synthetic_console"
        package.mkdir()
        for name, source in _SYNTHETIC_PACKAGE.items():
            (package / name).write_text(source, encoding="utf-8")

        edges, writers = _call_graph(package)
        assert "store.grant_seats" in writers, (
            "the synthetic writer must be detected, or the fence is vacuous"
        )
        assert _walk(edges, writers, ["synthetic_route"], frozenset()) == [(
            "synthetic_route",
            ("main.synthetic_route", "helpers.helper", "helpers.helper2",
             "store.grant_seats"),
        )]

    def test_the_real_walk_crosses_intermediates_when_nothing_is_permitted(
        self
    ):
        """The same claim, on the REAL tree (finding F5, 2026-08-18).

        Emptying the permitted set turns the two licensed edges back into
        findings — and both of them are reached through an **intermediate**, so
        this is the real graph demonstrating that the walk descends:
        ``redeem_discount_code`` reaches ``store.grant_seats`` only via
        ``main._apply_redemption -> payments.fulfil`` (three hops), and
        ``chat_completions`` reaches ``store.add_credit`` only via
        ``store.record_usage`` (two). A walk narrowed to direct callees reports
        **nothing at all** here, which is the mutation this fence exists to
        fail on.

        Asserted as containment plus a floor on path length rather than as an
        exact list: what is being fenced is the walk's DEPTH, and pinning the
        tree's exact shape here would duplicate
        ``test_no_org_key_route_writes_an_entitlement_or_ledger_row`` while
        going red for reasons that have nothing to do with depth.
        """
        edges, writers = _call_graph()
        found = _walk(edges, writers, _org_key_routes(), frozenset())

        assert ("redeem_discount_code",
                ("main.redeem_discount_code", "main._apply_redemption",
                 "payments.fulfil", "store.grant_seats")) in found, found
        assert ("chat_completions",
                ("main.chat_completions", "store.record_usage",
                 "store.add_credit")) in found, found
        assert found, "the licensed edges must reappear once nothing is allowed"
        assert min(len(path) for _route, path in found) >= 3, (
            "every finding here is reached through an intermediate; a "
            f"depth-1 walk could not have produced {found}"
        )

    def test_fulfil_reaches_exactly_the_three_named_writers(self):
        """The allow-list licenses a SUBTREE — so its contents are pinned too.

        *(Finding F7, 2026-08-18. Observation closed rather than argued away.)*
        The carve-out is a ``(route, callee)`` pair, and :func:`_walk` skips the
        callee it names: on reaching ``payments.fulfil`` from
        ``redeem_discount_code`` the walk stops descending. That is correct —
        the pair is what licenses the grant — but it means **everything
        reachable inside ``fulfil`` is unfenced from that route**, so a fourth
        writer added inside it would land under the customer's own key with no
        fence going red anywhere.

        This pins ``fulfil``'s own reachable writer set by CONTENTS. The three
        are the ones §9.6 names: the subscription, the seat grant, and the
        credit-pack branch that has zero rows to write until packs are priced.
        A fourth goes red here. Red-first evidence: a transient
        ``store.release_seat`` call inside ``fulfil`` fails with
        ``store.release_seat`` in the difference.

        ⚠️ If you are here because you added one: the question is not whether
        the fence should be widened. It is whether a customer-presented
        discount code should be able to cause that write.
        """
        edges, writers = _call_graph()
        assert _reachable("payments.fulfil", edges) & writers == {
            "store.activate_subscription",
            "store.add_credit",
            "store.grant_seats",
        }

    def test_the_allow_listed_route_really_does_reach_fulfil(self):
        """The carve-out is load-bearing, not decorative.

        If ``redeem_discount_code`` stopped reaching ``payments.fulfil``, the
        allow-list would be a dead entry that nobody notices — and B1's whole
        point is that a fence red by construction gets NARROWED by whoever
        meets it. So the pair is asserted to be needed, from both ends.
        """
        edges, _ = _call_graph()
        reachable, stack = set(), ["main.redeem_discount_code"]
        while stack:
            node = stack.pop()
            for callee in edges.get(node, ()):
                if callee not in reachable:
                    reachable.add(callee)
                    stack.append(callee)
        assert "payments.fulfil" in reachable

    def test_fulfil_has_exactly_two_call_sites(self):
        """Done-when 9 — structural, so a third path cannot quietly appear.

        The two are the webhook capture and SC-4g's redemption. Two
        implementations of "grant what was bought" is how a customer pays for
        something the free path gave away (D42); two CALL SITES of one
        implementation is the shape that keeps them identical.
        """
        edges, _ = _call_graph()
        callers = sorted(
            caller for caller, callees in edges.items()
            if "payments.fulfil" in callees or (
                caller.startswith("payments.") and "fulfil" in callees
            )
        )
        assert callers == ["main._apply_redemption",
                           "main._handle_webhook_event"], callers


# ── Clause 5 — can_pay, and who may reach the checkout ────────────────────

class TestTheLifecycleGate:
    def test_can_pay_is_true_everywhere_except_deleted(self):
        for state, caps in lifecycle.STATES.items():
            assert caps.can_pay == (state != "deleted"), state

    def test_can_pay_is_the_last_field(self):
        """Appended LAST, and the next one must be too (§9.3(5), nit 3).

        A field inserted anywhere but last silently re-maps every positional
        row's booleans — ``suspended`` would quietly become AI-enabled while
        every existing test kept passing. Which is why the rows below are
        keyword-constructed.
        """
        fields = list(lifecycle.OrgCapabilities.__dataclass_fields__)
        assert fields[-1] == "can_pay"

    def test_the_states_table_is_keyword_constructed(self):
        """Source-level, because nothing else can see a POSITIONAL argument.

        The dataclass having five fields does not tell you whether the six rows
        pass them in the right order — the bug this pins is invisible to every
        behavioural test until a state's booleans are silently rotated.
        """
        source = (_PACKAGE / "lifecycle.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        constructions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "OrgCapabilities"
        ]
        assert len(constructions) == len(lifecycle.STATES) == 6
        for call in constructions:
            assert call.args == [], "positional OrgCapabilities construction"
            assert {kw.arg for kw in call.keywords} == set(
                lifecycle.OrgCapabilities.__dataclass_fields__
            )

    def test_a_suspended_org_can_create_an_order_and_a_deleted_one_cannot(
        self, client
    ):
        """§9.3(5)'s fence. ``can_use_ai`` is the WRONG gate for paying.

        Measured 2026-08-18: ``auth.organization_from_key`` 403s a suspended
        organization, so the customer who most needs to pay was the one the
        door was shut on — while ``lifecycle.py``'s own doctrine says *"a
        suspended customer who cannot log in cannot pay you"*.
        """
        slug = _new_org(client, "susp")
        key = _org_key(client, slug)
        _lifecycle(client, slug, "suspended")
        assert _create_order(client, key).status_code == 200

        # And the AI door is still shut for the same key — one machine, two
        # questions, and they must not have converged.
        assert client.get("/me", headers=_headers(key)).status_code == 403

        _lifecycle(client, slug, "cancelled")
        _lifecycle(client, slug, "deleted")
        r = _create_order(client, key)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "organization is deleted"

    def test_the_checkout_takes_no_organization_from_request_input(
        self, client, org
    ):
        """R11: the org is a property of the credential, full stop."""
        r = client.post(
            "/billing/orders", headers={**_headers(org["key"]),
                                        "X-CC-Org": "somebody-else"},
            json={"lines": [{"plan_slug": "sales", "quantity": 1}],
                  "org_slug": "somebody-else"},
        )
        # `extra="forbid"` refuses the body field outright; the header is not
        # bound anywhere, which is the desired outcome and is why it is pinned.
        assert r.status_code == 422, r.text


# ── Clauses 6, 7, 8 — the webhook: signature, dedup, amount ────────────────

class TestTheWebhook:
    def test_a_mis_signed_webhook_is_refused_before_its_body_is_parsed(
        self, client, db
    ):
        """Done-when 6, and the body is chosen so parsing would be OBSERVABLE.

        A malformed JSON payload would be a 422-for-schema if anything read it;
        it is a 400-for-signature, and no ``payment_event`` row exists.
        """
        raw = b"{this is not json at all"
        before = _event_count(db)
        r = client.post(
            "/billing/webhooks/razorpay", content=raw,
            headers={"x-razorpay-signature": "deadbeef",
                     "x-razorpay-event-id": "evt_never"},
        )
        assert r.status_code == 400, r.text
        assert _event_count(db) == before

    def test_an_unsigned_webhook_is_refused(self, client, db):
        before = _event_count(db)
        r = client.post("/billing/webhooks/razorpay", content=b"{}",
                        headers={"x-razorpay-event-id": "evt_unsigned"})
        assert r.status_code == 400
        assert _event_count(db) == before

    def test_a_correct_signature_over_a_different_body_is_refused(
        self, client, fake, db
    ):
        """The signature is over the RAW BYTES, not over a shape.

        Signing one body and delivering another is the substitution attack the
        HMAC exists to stop, and it is the case a fake signer that returned a
        constant would pass.
        """
        raw, headers = fake.capture_event(
            provider_order_id="order_X", amount_paise=100, event_id="evt_sub")
        tampered = raw.replace(b'"amount": 100', b'"amount": 1')
        before = _event_count(db)
        r = client.post("/billing/webhooks/razorpay", content=tampered,
                        headers=headers)
        assert r.status_code == 400
        assert _event_count(db) == before

    def test_a_non_ascii_signature_header_is_refused_not_a_crash(
        self, client, fake, db
    ):
        """P2, 2026-08-19. ``hmac.compare_digest`` on **str** is ASCII-only.

        Starlette decodes header bytes as latin-1, so a byte above 0x7F in
        ``x-razorpay-signature`` reaches the comparison as a non-ASCII ``str``
        and ``compare_digest`` raises ``TypeError`` — an unhandled 500 on a
        route that is, by design, reachable without a bearer token. It is
        unreachable only while no credentials are configured.

        A refusal is the only correct answer: the presented value cannot equal
        a hex digest. Red-first evidence: against the str comparison this test
        does not fail with 500, it **errors** with the raw ``TypeError``,
        because ``TestClient`` re-raises server exceptions.
        """
        raw, headers = fake.capture_event(
            provider_order_id="order_X", amount_paise=100,
            event_id="evt_nonascii")
        # Passed as BYTES: httpx encodes str header values as ASCII and would
        # refuse to send this at the client, testing nothing.
        hostile = dict(headers)
        hostile["x-razorpay-signature"] = b"\xc3\xa9" + b"0" * 62
        before = _event_count(db)
        r = client.post("/billing/webhooks/razorpay", content=raw,
                        headers=hostile)
        assert r.status_code == 400, r.text
        assert _event_count(db) == before, "nothing is recorded"

    def test_a_duplicate_delivery_of_one_event_id_is_a_no_op(
        self, client, fake, db, org
    ):
        """Done-when 7, first half: TRANSPORT dedup, and that is all it is."""
        order = _order(client, org["key"])
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        first = _capture(client, fake, db, order, event_id=event_id)
        second = _capture(client, fake, db, order, event_id=event_id)

        assert first.status_code == 200 and first.json()["fulfilled"] is True
        assert second.status_code == 200
        assert second.json() == {"recorded": False, "fulfilled": False}
        assert _events_for(db, order["id"]) == 1
        assert _grants_for(db, org["id"], "sales") == 1

    def test_two_different_event_ids_for_one_order_fulfil_exactly_once(
        self, client, fake, db, org
    ):
        """Done-when 7's second half — the case the PRIMARY KEY cannot cover.

        Razorpay sends ``payment.captured`` AND ``order.paid`` for one payment:
        two events, two ids, one payment. The key never sees them as
        duplicates and never will. What makes the second harmless is that
        ``captured`` is terminal.

        ⚠️ A fence that delivers the SAME event id twice does not test this at
        all — that is the test above, and both are kept because they answer
        different questions.
        """
        order = _order(client, org["key"])
        first = _capture(client, fake, db, order, kind="payment.captured")
        second = _capture(client, fake, db, order, kind="order.paid")

        assert first.json() == {"recorded": True, "fulfilled": True}
        assert second.json() == {"recorded": True, "fulfilled": False}
        assert _events_for(db, order["id"]) == 2, "both events are RECORDED"
        assert _grants_for(db, org["id"], "sales") == 1, "exactly one fulfils"

    def test_a_capture_whose_amount_disagrees_is_refused_and_alerted(
        self, client, fake, db, org, caplog
    ):
        """Done-when 8. Never resolved in the customer's favour silently."""
        order = _order(client, org["key"])
        with caplog.at_level("ERROR"):
            r = _capture(client, fake, db, order,
                         amount=order["total_paise"] - 1)
        assert r.status_code == 409, r.text
        assert any(rec.message == "payments.amount_mismatch"
                   for rec in caplog.records), caplog.text
        assert _grants_for(db, org["id"], "sales") == 0
        # The receipt rolls back with the refusal, so a corrected re-delivery
        # is evaluated afresh rather than deduped into silence.
        assert _events_for(db, order["id"]) == 0

    def test_an_event_for_an_unknown_order_is_recorded_and_not_acted_on(
        self, client, fake
    ):
        raw, headers = fake.capture_event(
            provider_order_id="order_NOBODY", amount_paise=100,
            event_id=f"evt_{uuid.uuid4().hex[:12]}")
        r = client.post("/billing/webhooks/razorpay", content=raw,
                        headers=headers)
        assert r.json() == {"recorded": True, "fulfilled": False}

    def test_a_capture_with_no_matching_order_is_kept_and_alerted_at_error(
        self, client, fake, db, caplog
    ):
        """**Money received with nothing granted.** Added 2026-08-18 (F2).

        The test above pins the RESPONSE. This one pins the two things that
        make the response survivable, because the arm it covers is not the
        benign case the corpus called it: a signature-verified ``captured``
        payment whose ``order_id`` matches no row here is a customer charged
        with no entitlement written, and our 200 is what stops Razorpay
        retrying. The only two things standing between that and silence are:

        1. the ``payment_event`` receipt is **kept**, with ``order_id`` NULL —
           it is the sole record that the money arrived, and CP-8's
           reconciliation is specified to start from exactly these rows;
        2. the line is at **ERROR** and carries the **amount and both provider
           identifiers** in its structured fields, so the operator can find the
           payment at the provider from the log alone.

        A WARNING here is the wrong severity for "we hold a customer's money
        and granted nothing", and an alert without the amount is one that
        cannot be triaged. Red-first evidence: the arm as first shipped logged
        at ``warning`` with only ``event``, and this fence fails on both counts.
        """
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        raw, headers = fake.capture_event(
            provider_order_id="order_ORPHAN", amount_paise=141600,
            event_id=event_id, payment_id="pay_ORPHAN01")
        with caplog.at_level("ERROR"):
            r = client.post("/billing/webhooks/razorpay", content=raw,
                            headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"recorded": True, "fulfilled": False}

        with db.begin() as c:
            row = c.execute(
                text("SELECT order_id, kind FROM payment_event "
                     "WHERE provider_event_id = :e"), {"e": event_id}).first()
        assert row is not None, "the receipt is the only trace of the money"
        assert row.order_id is None, "an orphan receipt names no order"
        assert row.kind == "payment.captured"

        alerts = [rec for rec in caplog.records
                  if rec.message == "payments.webhook_unknown_order"]
        assert len(alerts) == 1, caplog.text
        alert = alerts[0]
        assert alert.levelname == "ERROR", "a paid orphan is not a warning"
        assert alert.amount_paise == 141600, "triage needs the AMOUNT"
        assert alert.provider_order_id == "order_ORPHAN"
        assert alert.provider_payment_id == "pay_ORPHAN01"

    def test_a_failed_attempt_does_not_close_the_order(
        self, client, fake, db, org, caplog
    ):
        """**A failed ATTEMPT is not a failed ORDER** (P0 repair, 2026-08-19).

        Replaces ``test_a_failed_payment_marks_the_order_failed``, which pinned
        the defect rather than the design: one Razorpay order accepts MANY
        payment attempts until one captures, so a UPI timeout, a declined card
        and an abandoned 3DS step are all **attempt**-level events. Driving the
        ORDER to ``failed`` — a terminal state — made the retry that succeeded
        thirty seconds later unfulfillable.

        The receipt is still written, which is what makes SC-4a's *"a failed
        payment says so"* buildable: it reads ``payment_event``, not a closed
        order.
        """
        order = _order(client, org["key"])
        with caplog.at_level("INFO"):
            r = _capture(client, fake, db, order, kind="payment.failed")
        assert r.json() == {"recorded": True, "fulfilled": False}
        assert _status(db, order["id"]) == "created", (
            "a failed attempt leaves the order OPEN for the next one"
        )
        assert _events_for(db, order["id"]) == 1, "the attempt IS recorded"
        assert _grants_for(db, org["id"], "sales") == 0
        assert any(rec.message == "payments.attempt_failed"
                   for rec in caplog.records), caplog.text

    def test_a_failed_attempt_then_a_successful_capture_fulfils(
        self, client, fake, db, org
    ):
        """**THE P0.** Red against 5acad0c1; the whole reason for the fix above.

        The sequence is the ordinary one, not an exotic race: the customer's
        UPI collect times out, Razorpay sends ``payment.failed``, the customer
        taps *retry* inside the **same** Checkout, the card captures and
        ``payment.captured`` arrives for the **same provider order** with the
        **correct amount**.

        Against the shipped code that second event found an order in ``failed``
        — terminal, ``_ORDER_TRANSITIONS["failed"] = frozenset()`` — so
        ``fulfil`` raised ``TransitionRefused``, the ``except`` arm logged INFO
        ``payments.already_fulfilled`` (a false line: nothing had been
        fulfilled), and the route answered **200**, which is exactly what stops
        Razorpay retrying. ₹1,416 taken, nothing granted, no alert.

        Nothing in the 105-test suite delivered failed-then-captured for one
        order, which is how it shipped green.
        """
        order = _order(client, org["key"])
        first = _capture(client, fake, db, order, kind="payment.failed")
        second = _capture(client, fake, db, order, kind="payment.captured")

        assert first.json() == {"recorded": True, "fulfilled": False}
        assert second.json() == {"recorded": True, "fulfilled": True}, (
            "the retry that actually captured must fulfil"
        )
        assert _status(db, order["id"]) == "captured"
        assert _grants_for(db, org["id"], "sales") == 1, "exactly one fulfil"
        assert _events_for(db, order["id"]) == 2, "both receipts are kept"

    def test_a_capture_after_abandonment_alerts_at_error(
        self, client, fake, db, org, caplog
    ):
        """The OTHER half of the P0: not every ``TransitionRefused`` is benign.

        One ``except TransitionRefused`` arm covered two situations that could
        not be further apart:

        * the order is ``captured`` — the SECOND event of one capture, the money
          guard doing its job, correctly INFO;
        * the order is terminal for any **other** reason — here ``abandoned``,
          written by the TTL sweep that ``redeem`` runs — in which case a
          signature-verified capture with the right amount has arrived and we
          have granted **nothing**. That is the same class as
          ``payments.webhook_unknown_order`` and it takes the same severity and
          the same three structured fields, so the payment is findable at the
          provider from the log line alone.

        Red-first evidence: against the single INFO arm this fence fails on
        ``payments.capture_after_terminal`` being absent *and* on the benign
        line being present.
        """
        order = _order(client, org["key"])
        with db.begin() as c:
            c.execute(
                text("UPDATE payment_order SET expires_at = now() - "
                     "interval '1 minute' WHERE id = :i"), {"i": order["id"]})
        # `abandoned` is written by the clock, observed at the next write that
        # touches the order — here the redeem route's own expiry transaction.
        code = _issue_code(client, org_slug=org["slug"], percent_bp=10000)
        refused = client.post(f"/billing/orders/{order['id']}/redeem",
                              headers=_headers(org["key"]),
                              json={"code": code["code"]})
        assert refused.status_code == 409, refused.text
        assert _status(db, order["id"]) == "abandoned"

        provider_order_id = _provider_order_id(db, order["id"])
        with caplog.at_level("INFO"):
            r = _capture(client, fake, db, order)
        assert r.status_code == 200, r.text
        assert r.json() == {"recorded": True, "fulfilled": False}
        assert _events_for(db, order["id"]) == 1, "the receipt is KEPT"
        assert _grants_for(db, org["id"], "sales") == 0

        assert [rec for rec in caplog.records
                if rec.message == "payments.already_fulfilled"] == [], (
            "an abandoned order is not the benign duplicate"
        )
        alerts = [rec for rec in caplog.records
                  if rec.message == "payments.capture_after_terminal"]
        assert len(alerts) == 1, caplog.text
        alert = alerts[0]
        assert alert.levelname == "ERROR", (
            "money received with nothing granted is not an INFO line"
        )
        assert alert.amount_paise == order["total_paise"], "triage needs it"
        assert alert.provider_order_id == provider_order_id
        assert alert.provider_payment_id == "pay_FAKE0001"
        assert alert.status == "abandoned"

    def test_a_capture_does_not_transition_the_organization(
        self, client, db, fake
    ):
        """Done-when 16 — asserted directly on ``organization.status``.

        A suspended organization that pays is still suspended the microsecond
        after the webhook returns 200. Letting an unattended callback drive the
        lifecycle would make an outside system the writer of our account state:
        a replayed capture or a captured-then-refunded payment would reinstate
        an account nobody decided to reinstate.
        """
        slug = _new_org(client, "still-susp")
        org_id = _org_id(client, slug)
        key = _org_key(client, slug)
        _lifecycle(client, slug, "suspended")

        order = _order(client, key)
        assert _capture(client, fake, db, order).json()["fulfilled"] is True

        with db.begin() as c:
            assert c.execute(
                text("SELECT status FROM organization WHERE id = :i"),
                {"i": org_id},
            ).scalar_one() == "suspended"
        # The purchased term IS recorded — the two rows disagree on purpose,
        # and reinstatement stays the operator's act through /orgs/lifecycle.
        assert _rows(db, "org_subscription", org_id)[0]["status"] == "active"


def _event_count(db) -> int:
    with db.begin() as c:
        return int(c.execute(
            text("SELECT count(*) FROM payment_event")).scalar_one())


def _events_for(db, order_id: str) -> int:
    with db.begin() as c:
        return int(c.execute(
            text("SELECT count(*) FROM payment_event WHERE order_id = :o"),
            {"o": order_id}).scalar_one())


def _grants_for(db, org_id: str, plan: str) -> int:
    with db.begin() as c:
        return int(c.execute(
            text("SELECT count(*) FROM seat_grant WHERE organization_id = :o "
                 "AND plan_slug = :p"),
            {"o": org_id, "p": plan}).scalar_one())


def _status(db, order_id: str) -> str:
    with db.begin() as c:
        return c.execute(
            text("SELECT status FROM payment_order WHERE id = :i"),
            {"i": order_id}).scalar_one()


# ── Clause 10 — no credentials, no checkout, no webhook ────────────────────

class TestTheSeamRefusesWithoutCredentials:
    @pytest.fixture
    def unconfigured(self, monkeypatch):
        """No installed provider and no env — the shipped state everywhere."""
        payments.set_provider(None)
        for name in payments.PROVIDER_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
        return TestClient(app)

    def test_creating_an_order_is_503_naming_the_missing_variables(
        self, client, org, unconfigured
    ):
        r = _create_order(unconfigured, org["key"])
        assert r.status_code == 503, r.text
        for name in payments.PROVIDER_ENV_VARS:
            assert name in r.json()["detail"]

    def test_the_webhook_is_503_rather_than_accepting_what_it_cannot_verify(
        self, unconfigured
    ):
        r = unconfigured.post("/billing/webhooks/razorpay", content=b"{}",
                              headers={"x-razorpay-signature": "x"})
        assert r.status_code == 503, r.text

    def test_no_code_path_invents_a_default_endpoint_or_key(self):
        """Structural: the seam has no localhost default to fall back to."""
        source = _uncommented(_PACKAGE / "payments.py")
        assert "localhost" not in source
        assert 'os.environ.get(name, "")' in source, (
            "credentials are read with an EMPTY fallback, never a value"
        )
        assert payments.RazorpayProvider.API_BASE == (
            "https://api.razorpay.com/v1"
        )


# ── Clauses 13, 14 — ownership, and reading an order back ─────────────────

class TestReadingAnOrderBack:
    def test_a_foreign_order_and_an_unknown_order_refuse_identically(
        self, client, db, org
    ):
        """Done-when 13 — compared on STATUS **and BODY BYTES** (§9.3(7)).

        A 403-for-foreign / 404-for-unknown split is a membership oracle over
        other tenants' order ids, run from a customer's own key. Four
        responses, one shape.
        """
        other_slug = _new_org(client, "other")
        other_key = _org_key(client, other_slug)
        theirs = _order(client, other_key)
        unknown = str(uuid.uuid4())
        mine = _headers(org["key"])

        responses = [
            client.get(f"/billing/orders/{theirs['id']}", headers=mine),
            client.get(f"/billing/orders/{unknown}", headers=mine),
            client.post(f"/billing/orders/{theirs['id']}/redeem",
                        headers=mine, json={"code": "cc_disc_a_b"}),
            client.post(f"/billing/orders/{unknown}/redeem",
                        headers=mine, json={"code": "cc_disc_a_b"}),
        ]
        statuses = {r.status_code for r in responses}
        bodies = {r.content for r in responses}
        assert statuses == {404}, [r.status_code for r in responses]
        assert len(bodies) == 1, bodies
        assert responses[0].json() == {"detail": "no such order"}

    def test_a_malformed_order_id_refuses_identically_too(self, client, org):
        """Not a 500 from the driver, and not a third distinguishable shape."""
        r = client.get("/billing/orders/not-a-uuid",
                       headers=_headers(org["key"]))
        assert r.status_code == 404
        assert r.json() == {"detail": "no such order"}

    def test_an_order_read_is_scoped_to_the_key(self, client, org):
        """The list of org A NEVER contains an order of org B — by IDS."""
        other = _org_key(client, _new_org(client, "list-other"))
        theirs = _order(client, other)
        mine = _order(client, org["key"])

        page = client.get("/billing/orders",
                          headers=_headers(org["key"])).json()
        ids = {o["id"] for o in page["orders"]}
        assert mine["id"] in ids
        assert theirs["id"] not in ids

    def test_the_order_read_carries_no_provider_identifiers(self):
        """Structural over the RESPONSE MODEL (§9.3a).

        A later field addition has to argue with a red test rather than with a
        reviewer's attention — ``_capability_block``'s argument, one endpoint
        along: a field nothing reads is a field somebody eventually reads.
        """
        from customer_console.main import OrderView

        fields = set(OrderView.model_fields)
        assert not any("provider_" in name for name in fields), fields
        assert "provider" in fields, "the PATH is public; the ids are not"
        assert fields == {
            "id", "status", "provider", "gross_paise", "discount_paise",
            "taxable_paise", "gst_paise", "total_paise", "gst_split",
            "expires_at", "created_at", "terminal_at", "lines", "discount",
        }

    def test_a_terminal_order_is_visible_and_filterable(
        self, client, db, org
    ):
        """Done-when 14 — which is what makes SC-4a done-when 5 buildable.

        ⚠️ **Rewritten 2026-08-19 with the P0 repair**, and the rewrite is the
        point rather than a fixture detail. It used to reach a terminal state by
        delivering ``payment.failed``, which is exactly the transition the
        repair removed: a failed **attempt** no longer closes the **order**.
        Order-level failure is ``abandoned``, written by the clock, and that is
        what a customer's orders page actually has to render. The acceptance
        this fence carries — a non-open order is readable and filterable — is
        unchanged.
        """
        order = _order(client, org["key"])
        with db.begin() as c:
            c.execute(
                text("UPDATE payment_order SET expires_at = now() - "
                     "interval '1 minute' WHERE id = :i"), {"i": order["id"]})
        code = _issue_code(client, org_slug=org["slug"], percent_bp=10000)
        client.post(f"/billing/orders/{order['id']}/redeem",
                    headers=_headers(org["key"]),
                    json={"code": code["code"]})

        read = client.get(f"/billing/orders/{order['id']}",
                          headers=_headers(org["key"])).json()
        assert read["status"] == "abandoned"
        assert read["terminal_at"] is not None

        page = client.get("/billing/orders?status=abandoned",
                          headers=_headers(org["key"])).json()
        assert order["id"] in {o["id"] for o in page["orders"]}

    def test_an_unknown_status_filter_is_400_not_silently_ignored(
        self, client, org
    ):
        r = client.get("/billing/orders?status=refunded",
                       headers=_headers(org["key"]))
        assert r.status_code == 400, r.text

    def test_the_list_carries_no_lines_and_a_named_page_size(self, client, org):
        _order(client, org["key"])
        page = client.get("/billing/orders",
                          headers=_headers(org["key"])).json()
        assert page["orders"][0]["lines"] is None
        assert page["next"] is None  # fewer than a full page


# ── §6 item (f) — the customer-key catalog read ────────────────────────────
#
# The one thing WS-30 SC-4a's launch slice could not get honestly: its
# done-when 1 forbids a hard-coded price ladder in TypeScript, and until this
# route existed no credential a customer holds could read the priced catalog.
# `GET /billing/summary` is Operator and cross-org; `/me/billing` and `/me`
# carry no catalog at all.

class TestTheCatalogRead:
    def test_the_catalog_read_never_boards_an_inactive_row(
        self, client, db, org
    ):
        """§6 item (f) — ``active`` is in the WHERE clause, not in the caller.

        `rnd` and `support` are seeded INACTIVE because their Centers are not
        registered yet (`002_seed_catalog.sql`), so a catalog read that boards
        them offers a customer a package the product cannot deliver. A row
        seeded HERE covers the case where somebody later flips the seed: the
        fence must not depend on a particular pair of slugs still being off.

        Mutation evidence (run and reverted during the build): dropping
        ``WHERE active`` from ``store.active_plans`` fails this test on the
        seeded slug and on both seeded ones, and nothing else in the suite
        notices.
        """
        hidden = f"hidden-{uuid.uuid4().hex[:8]}"
        with db.begin() as c:
            c.execute(
                text("INSERT INTO plan_catalog (slug, name, kind, price_inr, "
                     "active, sort_order) VALUES (:s, 'Not For Sale', "
                     "'center', 999.00, FALSE, 99)"),
                {"s": hidden},
            )
            # The seeded pair, asserted to BE inactive rather than assumed:
            # a fence whose subject silently became active would otherwise
            # pass by testing nothing.
            off = {
                r[0] for r in c.execute(
                    text("SELECT slug FROM plan_catalog WHERE NOT active")
                )
            }
        assert {hidden, "rnd", "support"} <= off, off

        r = client.get("/billing/catalog", headers=_headers(org["key"]))
        assert r.status_code == 200, r.text
        slugs = [p["slug"] for p in r.json()["plans"]]

        assert hidden not in slugs
        assert "rnd" not in slugs and "support" not in slugs
        # Non-vacuous: an empty catalog would satisfy every assertion above.
        assert {"core", "sales", "complete"} <= set(slugs), slugs

    def test_the_catalog_read_carries_no_per_org_state_and_paise_only(
        self, client, db
    ):
        """§6 item (f) — the field set, the denomination, and both doors.

        Four properties in one fence because they are one decision — *what a
        customer credential may learn from the catalog*:

        1. the field set is **exactly** five names, structurally over the
           response model AND over the wire, so a per-org field (seats held,
           entitlement state, an org-specific price) argues with a red test;
        2. money is **paise**, asserted against the NUMERIC rupees in the
           database rather than against a constant, so emitting ``price_inr``
           or dropping the conversion is red;
        3. two different organizations get a **byte-identical** answer — the
           real statement of "no per-org pricing";
        4. the door is ``can_pay``: a **suspended** org reads it (that is the
           whole reason it is not ``KeyCaller``), a **deleted** one is 403.

        Mutation evidence (run and reverted): emitting ``int(price_inr)``
        instead of ``payments.paise(...)`` fails on 2; adding an
        ``organization_id`` field to ``CatalogPlanView`` fails on 1.
        """
        from customer_console.main import CatalogPlanView

        expected = {"slug", "name", "kind", "price_paise", "sort_order"}
        assert set(CatalogPlanView.model_fields) == expected
        assert not any(
            "org" in name or "seat" in name or "entitle" in name
            for name in CatalogPlanView.model_fields
        )

        slug = _new_org(client, "cat")
        key = _org_key(client, slug)
        _lifecycle(client, slug, "suspended")

        r = client.get("/billing/catalog", headers=_headers(key))
        assert r.status_code == 200, r.text
        plans = r.json()["plans"]
        assert plans, "the seeded catalog is never empty"
        assert all(set(p) == expected for p in plans), plans

        # ...and the AI door is still shut for the same key, so the two
        # questions have not converged into one.
        assert client.get("/me", headers=_headers(key)).status_code == 403

        with db.begin() as c:
            rupees = {
                r_[0]: r_[1] for r_ in c.execute(
                    text("SELECT slug, price_inr FROM plan_catalog "
                         "WHERE active")
                )
            }
        for plan in plans:
            assert plan["price_paise"] == int(rupees[plan["slug"]] * 100)
        # An anchor a reader can check by eye: Core is Rs 600.00.
        core = next(p for p in plans if p["slug"] == "core")
        assert core["price_paise"] == 60000

        # Deterministic, and it is the catalog's own order.
        assert [p["sort_order"] for p in plans] == sorted(
            p["sort_order"] for p in plans
        )

        other = _org_key(client, _new_org(client, "cat-other"))
        assert client.get(
            "/billing/catalog", headers=_headers(other)
        ).json() == r.json()

        _lifecycle(client, slug, "cancelled")
        _lifecycle(client, slug, "deleted")
        dead = client.get("/billing/catalog", headers=_headers(key))
        assert dead.status_code == 403, dead.text
        assert dead.json()["detail"] == "organization is deleted"


# ── §6 item (g) — the customer-key seats read ──────────────────────────────
#
# The seat grid the post-signup MVP (`subscription_console.md` SC-1a) needs.
# `GET /billing/summary` computes the same numbers but is Operator/cross-org and
# takes an `org_slug` a customer must never name; `/me/billing` carries no seats.
# `GET /me/seats` is the customer-key read on the SAME `can_pay` door item (f)
# used, and it shares `billing_summary`'s plan loop — the one `_seat_grid` helper
# — with the org id from the credential: no second SQL, no recompute (done-when 19).

class TestTheSeatsRead:
    def test_the_seats_read_is_scoped_to_the_key(self, client, db, org):
        """Org A's read never carries org B's rows (§6 item (g), done-when 19).

        The two-org isolation precedent is
        ``test_an_order_read_is_scoped_to_the_key``. The organization is the
        CREDENTIAL's (``caller.organization_id``), so nothing on the wire can
        redirect the read: A presents its own key AND — deliberately — B's slug
        on the query string, and the slug is inert.

        Mutation evidence (run and reverted during the build): giving ``my_seats``
        an ``org_slug`` query param and reading ``_org_id(conn, org_slug)`` from it
        makes A's request return B's ``sales`` row — this fence reddens on
        ``"sales" not in slugs``.
        """
        other_slug = _new_org(client, "seats-other")
        other_id = _org_id(client, other_slug)
        # Give B a plan A does NOT hold, so "read B by slug" is observable on the
        # wire rather than hidden behind two identical Core rows.
        with db.begin() as c:
            c.execute(
                text("INSERT INTO seat_grant (organization_id, plan_slug, "
                     "quantity_purchased, reason) "
                     "VALUES (:o, 'sales', 5, 'seats-read-test')"),
                {"o": other_id},
            )

        r = client.get(f"/me/seats?org_slug={other_slug}",
                       headers=_headers(org["key"]))
        assert r.status_code == 200, r.text
        slugs = {p["plan_slug"] for p in r.json()["plans"]}
        # A's own provisioned Core seats are present (non-vacuous: the read
        # returned rows), and B's plan never boards A's answer.
        assert "core" in slugs, slugs
        assert "sales" not in slugs, slugs

    def test_the_seats_read_carries_the_seat_vocabulary_and_nothing_else(
        self, client, db, org
    ):
        """The field set, the counts, the zero-clamp, and both doors (item (g)).

        Four properties, one decision — *what a customer credential learns from
        its seats*:

        1. the field set is **exactly** the five seat names, structurally over the
           ``SeatPlanView`` model AND over the wire, so an ``org`` or price field
           argues with a red test;
        2. the four counts equal ``seats.seat_counts`` fed the SAME
           ``store.seat_rows`` — asserted by re-deriving them, so a second SQL or a
           recompute is red;
        3. an **oversubscribed** plan (``assigned > purchased``) reports
           ``available == 0`` — the zero-clamp — not a negative, and surfaces
           ``oversubscribed`` rather than hiding behind the clamp;
        4. the door is ``can_pay``: a **suspended** org reads it, a **deleted** one
           is 403.

        Mutation evidence (run and reverted): recomputing ``available`` inline as
        ``purchased - assigned`` without the zero-clamp fails property 3 on the
        oversubscribed plan (``-1 != 0``); adding an ``organization_id`` or a
        price field to ``SeatPlanView`` fails property 1's field-set equality.
        """
        from customer_console import store
        from customer_console.main import SeatPlanView
        from customer_console.seats import seat_counts

        org_id = org["id"]

        # The known fixture the spec names: provisioning grants Core 3 and
        # assigns the owner one → purchased 3 / assigned 1 / available 2. Then a
        # second, OVERSUBSCRIBED plan: one seat bought, two people assigned.
        with db.begin() as c:
            c.execute(
                text("INSERT INTO seat_grant (organization_id, plan_slug, "
                     "quantity_purchased, reason) "
                     "VALUES (:o, 'sales', 1, 'seats-read-test')"),
                {"o": org_id},
            )
            for _ in range(2):
                ident = c.execute(
                    text("INSERT INTO user_identity (email) VALUES (:e) "
                         "RETURNING id"),
                    {"e": f"seat-{uuid.uuid4().hex[:12]}@t.test"},
                ).scalar_one()
                c.execute(
                    text("INSERT INTO seat_assignment (organization_id, "
                         "plan_slug, user_identity_id, source) "
                         "VALUES (:o, 'sales', :i, 'alacarte')"),
                    {"o": org_id, "i": ident},
                )

        # 1 — the field set, on the MODEL.
        expected = {"plan_slug", "purchased", "assigned", "available",
                    "oversubscribed"}
        assert set(SeatPlanView.model_fields) == expected
        assert not any(
            "org" in name or "price" in name or "entitle" in name
            for name in SeatPlanView.model_fields
        )

        r = client.get("/me/seats", headers=_headers(org["key"]))
        assert r.status_code == 200, r.text
        plans = r.json()["plans"]
        by_slug = {p["plan_slug"]: p for p in plans}
        assert set(by_slug) == {"core", "sales"}, by_slug

        # 1 — the field set, on the WIRE.
        assert all(set(p) == expected for p in plans), plans

        # 2 — every count equals seat_counts fed the SAME seat_rows. This is the
        # statement of "one vocabulary, never a second SQL": the wire is the fold.
        with db.begin() as c:
            for slug, row in by_slug.items():
                grants, assigned = store.seat_rows(
                    c, org_id=org_id, plan_slug=slug)
                want = seat_counts(slug, grants, assigned)
                assert row == {
                    "plan_slug": slug,
                    "purchased": want.purchased,
                    "assigned": want.assigned,
                    "available": want.available,
                    "oversubscribed": want.oversubscribed,
                }, row

        # The Core anchor a reader can check by eye.
        assert by_slug["core"] == {
            "plan_slug": "core", "purchased": 3, "assigned": 1,
            "available": 2, "oversubscribed": False,
        }

        # 3 — the oversubscribed plan: clamped to 0, never negative, and flagged.
        sales = by_slug["sales"]
        assert sales["purchased"] == 1 and sales["assigned"] == 2
        assert sales["available"] == 0, "the zero-clamp, not purchased - assigned"
        assert sales["oversubscribed"] is True

        # 4 — the can_pay door. A suspended org still reads its seats...
        _lifecycle(client, org["slug"], "suspended")
        suspended = client.get("/me/seats", headers=_headers(org["key"]))
        assert suspended.status_code == 200, suspended.text
        # ...and the AI door is shut for the same key, so the two questions have
        # not converged into one.
        assert client.get("/me", headers=_headers(org["key"])).status_code == 403

        # ...a deleted org is refused (cancelled is the only path to deleted).
        _lifecycle(client, org["slug"], "cancelled")
        _lifecycle(client, org["slug"], "deleted")
        dead = client.get("/me/seats", headers=_headers(org["key"]))
        assert dead.status_code == 403, dead.text
        assert dead.json()["detail"] == "organization is deleted"

    def test_the_seats_read_is_empty_for_an_org_that_holds_nothing(
        self, client, db
    ):
        """A never-provisioned org reads ``200 {plans: []}`` (§6 item (g), R8).

        The empty-holdings edge the shared ``_seat_grid`` loop must not crash on:
        an org that holds a grant on no plan and an assignment on no plan. Every
        active plan is skipped (``not grants and not assigned``), so the grid
        folds to the empty list — not ``None``, not a 500, not a zero row for a
        plan the org never touched.

        Provisioning always seeds Core (``core_seats >= 1``, and the owner takes a
        seat), so the zero-holdings org is made by stripping those seeds from a
        freshly provisioned, ``active`` (``can_pay``) org — the state a real org
        reaches the moment it releases its last seat and lets its last grant lapse.
        """
        slug = _new_org(client, "seats-empty")
        org_id = _org_id(client, slug)
        key = _org_key(client, slug)
        with db.begin() as c:
            c.execute(
                text("DELETE FROM seat_assignment WHERE organization_id = :o"),
                {"o": org_id},
            )
            c.execute(
                text("DELETE FROM seat_grant WHERE organization_id = :o"),
                {"o": org_id},
            )

        r = client.get("/me/seats", headers=_headers(key))
        assert r.status_code == 200, r.text
        assert r.json() == {"plans": []}


# ── §6 item (h) — the customer-authenticated seat WRITE ────────────────────
#
# The write-side twin of item (g). A THIRD deployment-key capability
# (`seat_admin`) opens `POST /registry/seats` + `/registry/seats/release`, on
# which the CONSOLE — not only the browser tier — authorises "admin, not any
# member": the org and the acting admin are derived from
# `store.deployment_visible_orgs(deployment_id, actor_email)` (placement∩
# membership, never a body field — R11), the actor's `org_membership.role`/
# `status` is read on the resolved pair, and an unknown target is refused
# rather than `ensure_identity`-minted. The read after a write reconciles with
# `GET /me/seats` — the same `_seat_grid` → `seat_counts`, no second SQL.

def _default_box_id(db) -> str:
    """The id of the deployment every payments-suite org is provisioned onto."""
    with db.begin() as c:
        return ensure_deployment(c)


def _seat_admin_key(db, deployment_id: str) -> str:
    """A TEST `cc_depl_` key carrying `seat_admin` — ship-dark: no live key does.

    The capability NAME comes from `customer_console.auth`, defined once there,
    so the mint site, the door and this fixture cannot drift on the string.
    """
    with db.begin() as c:
        return mint_deployment_key(
            c, deployment_id=deployment_id,
            capabilities=[auth.SEAT_ADMIN_CAPABILITY],
        )


def _resolve_only_key(db, deployment_id: str) -> str:
    """A key carrying only the column default `{resolve}` — no write capability."""
    with db.begin() as c:
        return mint_deployment_key(c, deployment_id=deployment_id)


def _add_member(db, *, org_id: str, email: str, role: str = "member") -> str:
    """Add an ACTIVE membership; return the identity id. The identity exists
    because the target must be a member (clause 4) — never minted by the door."""
    with db.begin() as c:
        identity = store.ensure_identity(c, email=email)
        c.execute(
            text(
                """
                INSERT INTO org_membership
                    (organization_id, user_identity_id, role, status, joined_at)
                VALUES (:o, :i, :r, 'active', now())
                ON CONFLICT (organization_id, user_identity_id) DO NOTHING
                """
            ),
            {"o": org_id, "i": identity, "r": role},
        )
    return identity


def _grant(db, *, org_id: str, plan_slug: str, quantity: int) -> None:
    with db.begin() as c:
        c.execute(
            text("INSERT INTO seat_grant (organization_id, plan_slug, "
                 "quantity_purchased, reason) VALUES (:o, :p, :q, 'sa-test')"),
            {"o": org_id, "p": plan_slug, "q": quantity},
        )


def _seat_directly(db, *, org_id: str, plan_slug: str, identity_id: str) -> None:
    """Consume a seat WITHOUT going through the door — used to fill capacity."""
    with db.begin() as c:
        c.execute(
            text("INSERT INTO seat_assignment (organization_id, plan_slug, "
                 "user_identity_id, source) VALUES (:o, :p, :i, 'alacarte')"),
            {"o": org_id, "p": plan_slug, "i": identity_id},
        )


def _live_seat_count(db, *, org_id: str, plan_slug: str, email: str) -> int:
    with db.begin() as c:
        return int(c.execute(
            text(
                """
                SELECT count(*) FROM seat_assignment sa
                JOIN user_identity ui ON ui.id = sa.user_identity_id
                WHERE sa.organization_id = :o AND sa.plan_slug = :p
                  AND ui.email = :e AND sa.released_at IS NULL
                """
            ),
            {"o": org_id, "p": plan_slug, "e": email},
        ).scalar_one())


def _provision_on(client, prefix: str, label: str) -> str:
    """Provision an org onto a NAMED deployment (not the suite default box)."""
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "Other Co",
        "owner_email": f"owner@{slug}.test",
        "gstin": "29ABCDE1234F1Z5", "billing_state": "KA",
        "core_seats": 3, "deployment_label": label,
    })
    assert r.status_code == 200, r.text
    return slug


def _assign(client, key: str, *, actor: str, member: str, plan: str = "sales"):
    return client.post(
        "/registry/seats", headers=_headers(key),
        json={"actor_email": actor, "member_email": member, "plan_slug": plan},
    )


class TestTheSeatAdminWrite:
    def test_a_seat_admin_key_assigns_a_member_and_the_read_reflects_it(
        self, client, db
    ):
        """The happy path, end to end (§6 item (h), done-when).

        A `seat_admin` key placed for org A assigns an org-A member to an
        available `sales` seat; the row exists and `GET /me/seats` for A shows
        `assigned+1 / available-1` - the same `_seat_grid` the read renders.

        Mutation evidence (run and reverted during the build): commenting the
        `store.try_assign_seat` call in `assign_seat_admin` leaves the read
        unchanged — `sales.assigned` stays 0 — so this fence reddens on
        `assigned == 1`.
        """
        slug = _new_org(client, "sa-assign")
        org_id = _org_id(client, slug)
        key = _org_key(client, slug)
        owner = f"owner@{slug}.test"          # provisioned owner: admin, active
        _grant(db, org_id=org_id, plan_slug="sales", quantity=2)
        target = f"member-{uuid.uuid4().hex[:8]}@{slug}.test"
        _add_member(db, org_id=org_id, email=target)

        sa_key = _seat_admin_key(db, _default_box_id(db))
        r = _assign(client, sa_key, actor=owner, member=target)
        assert r.status_code == 200, r.text
        assert r.json() == {"assigned": True, "plan_slug": "sales"}

        assert _live_seat_count(
            db, org_id=org_id, plan_slug="sales", email=target) == 1

        seats = client.get("/me/seats", headers=_headers(key)).json()["plans"]
        sales = next(p for p in seats if p["plan_slug"] == "sales")
        assert sales["assigned"] == 1, sales
        assert sales["available"] == 1, sales  # purchased 2 - assigned 1

    def test_a_seat_admin_assign_refuses_at_the_cap(self, client, db):
        """No self-serve oversubscription (done-when).

        `sales` bought 1, already filled → `available == 0`; a fresh member is
        refused **409 `buy_more`** and no row is written. Oversubscription stays
        the Operator-only escape hatch.

        Mutation evidence (run and reverted): removing the `if not
        decision.allowed` guard writes an over-cap row and answers 200 — this
        fence reddens on the 409 assertion and on the row count.
        """
        slug = _new_org(client, "sa-cap")
        org_id = _org_id(client, slug)
        owner = f"owner@{slug}.test"
        _grant(db, org_id=org_id, plan_slug="sales", quantity=1)
        # Fill the one seat directly so available == 0.
        filler = _add_member(
            db, org_id=org_id, email=f"filler-{uuid.uuid4().hex[:8]}@{slug}.test")
        _seat_directly(db, org_id=org_id, plan_slug="sales", identity_id=filler)

        target = f"member-{uuid.uuid4().hex[:8]}@{slug}.test"
        _add_member(db, org_id=org_id, email=target)
        sa_key = _seat_admin_key(db, _default_box_id(db))

        r = _assign(client, sa_key, actor=owner, member=target)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["reason"] == "seat_cap_exceeded"
        assert detail["buy_more"]["plan_slug"] == "sales"
        assert _live_seat_count(
            db, org_id=org_id, plan_slug="sales", email=target) == 0

    def test_a_seat_admin_key_cannot_write_another_deployments_org(
        self, client, db
    ):
        """The placement bound (done-when).

        A key placed for A, naming an admin+member of B (a DIFFERENT box) →
        refused, and B's seats never move. The org+actor is the placement∩
        membership join, so B is invisible to A's key.

        Mutation evidence (run and reverted): removing the `JOIN org_placement`
        / `p.deployment_id = :dep` predicate from `store.deployment_visible_orgs`
        makes B resolve on A's key and the write lands on B — this fence reddens
        on the 403 and on B's unchanged seat count.
        """
        # A on the suite's default box; B on a distinct box.
        _new_org(client, "sa-a")  # ensures the default box has a placed org
        other_label = f"other-box-{uuid.uuid4().hex[:8]}"
        with db.begin() as c:
            ensure_deployment(c, label=other_label)
        slug_b = _provision_on(client, "sa-b", other_label)
        org_b = _org_id(client, slug_b)
        owner_b = f"owner@{slug_b}.test"       # admin of B, placed on other box
        _grant(db, org_id=org_b, plan_slug="sales", quantity=3)
        target_b = f"member-{uuid.uuid4().hex[:8]}@{slug_b}.test"
        _add_member(db, org_id=org_b, email=target_b)

        # Key placed for A's box, actor+target both from B.
        sa_key = _seat_admin_key(db, _default_box_id(db))
        r = _assign(client, sa_key, actor=owner_b, member=target_b)
        assert r.status_code == 403, r.text
        assert _live_seat_count(
            db, org_id=org_b, plan_slug="sales", email=target_b) == 0

    def test_a_non_admin_actor_is_refused(self, client, db):
        """The CONSOLE, not only Next, enforces admin-not-member (done-when).

        The resolved actor's `org_membership.role='member'` → **403**, nothing
        written — refused by the console's own read of its registry vocabulary.

        Mutation evidence (run and reverted): dropping the role/status check in
        `_seat_admin_for_deployment` lets the plain member assign into the
        available seat and answer 200 — this fence reddens on the 403.
        """
        slug = _new_org(client, "sa-member")
        org_id = _org_id(client, slug)
        _grant(db, org_id=org_id, plan_slug="sales", quantity=2)
        actor = f"plain-{uuid.uuid4().hex[:8]}@{slug}.test"
        _add_member(db, org_id=org_id, email=actor, role="member")
        target = f"member-{uuid.uuid4().hex[:8]}@{slug}.test"
        _add_member(db, org_id=org_id, email=target)

        sa_key = _seat_admin_key(db, _default_box_id(db))
        r = _assign(client, sa_key, actor=actor, member=target)
        assert r.status_code == 403, r.text
        assert _live_seat_count(
            db, org_id=org_id, plan_slug="sales", email=target) == 0

    def test_an_unknown_or_cross_org_target_member_is_refused(self, client, db):
        """No `ensure_identity`-minting of an arbitrary email (done-when).

        A `member_email` with no membership in the resolved org → refused, and
        **no `user_identity` is created** for it. The self-serve door validates
        the target against membership; it does not mint like the operator path.
        """
        slug = _new_org(client, "sa-target")
        org_id = _org_id(client, slug)
        owner = f"owner@{slug}.test"
        _grant(db, org_id=org_id, plan_slug="sales", quantity=2)
        unknown = f"nobody-{uuid.uuid4().hex[:8]}@ghost.test"

        sa_key = _seat_admin_key(db, _default_box_id(db))
        r = _assign(client, sa_key, actor=owner, member=unknown)
        assert r.status_code == 404, r.text
        with db.begin() as c:
            minted = int(c.execute(
                text("SELECT count(*) FROM user_identity WHERE email = :e"),
                {"e": unknown},
            ).scalar_one())
        assert minted == 0, "the door minted an identity for an unknown email"

    def test_a_seat_admin_release_frees_the_seat(self, client, db):
        """Release drops assigned by one; an unassigned release is a no-op."""
        slug = _new_org(client, "sa-rel")
        org_id = _org_id(client, slug)
        key = _org_key(client, slug)
        owner = f"owner@{slug}.test"
        _grant(db, org_id=org_id, plan_slug="sales", quantity=2)
        target = f"member-{uuid.uuid4().hex[:8]}@{slug}.test"
        _add_member(db, org_id=org_id, email=target)
        sa_key = _seat_admin_key(db, _default_box_id(db))

        assert _assign(client, sa_key, actor=owner, member=target).status_code == 200

        rel = client.post(
            "/registry/seats/release", headers=_headers(sa_key),
            json={"actor_email": owner, "member_email": target,
                  "plan_slug": "sales"},
        )
        assert rel.status_code == 200, rel.text
        assert rel.json() == {"released": True}

        seats = client.get("/me/seats", headers=_headers(key)).json()["plans"]
        sales = next(p for p in seats if p["plan_slug"] == "sales")
        assert sales["assigned"] == 0 and sales["available"] == 2, sales

        # A second release of the now-unassigned member is a 200 no-op.
        again = client.post(
            "/registry/seats/release", headers=_headers(sa_key),
            json={"actor_email": owner, "member_email": target,
                  "plan_slug": "sales"},
        )
        assert again.status_code == 200, again.text
        assert again.json() == {"released": False}

    def test_the_seat_admin_door_needs_the_capability(self, client, db, caplog):
        """The door: `{resolve}`-only → 403 logged; a `cc_live_` key → 401.

        Proves the org key gained no write (401 at the door, before any body),
        and that a valid deployment key without the capability is refused with
        the capability it lacks named and a `capability_refused` log line.
        """
        slug = _new_org(client, "sa-door")
        owner = f"owner@{slug}.test"  # already an active owner from provisioning
        box = _default_box_id(db)

        # A {resolve}-only deployment key → 403 at the write, capability named.
        resolve_only = _resolve_only_key(db, box)
        with caplog.at_level("WARNING", logger="platform.auth"):
            r = _assign(client, resolve_only, actor=owner, member=owner)
        assert r.status_code == 403, r.text
        assert "seat_admin" in r.json()["detail"]
        assert any(
            rec.message == "deployment_key.capability_refused"
            for rec in caplog.records
        ), "the capability refusal was not logged"

        # A cc_live_ ORG key → 401 at the door: it is not a deployment key and it
        # is not the operator token, so it reaches no write.
        org_key = _org_key(client, slug)
        r2 = _assign(client, org_key, actor=owner, member=owner)
        assert r2.status_code == 401, r2.text

    def test_a_deployment_seat_admin_may_not_name_an_org(self, client, db):
        """R11 shape-guard: a deployment key may not NAME an org (item (h)).

        The deployment arm DERIVES the org from `deployment_visible_orgs`
        (placement∩membership); a body that also sets `org_slug` is a caller who
        believes it named its tenant, and that is **400, never ignored**.

        Red-first (run and reverted in a scratch copy of `main.py`): dropping the
        `if req.org_slug is not None: raise 400` guard in
        `_seat_admin_for_deployment` makes this request **200** — `org_slug` is
        ignored, not honoured, so the org still resolves from the credential and
        the seat is written. This fence therefore pins the CONTRACT that naming
        an org is REFUSED: it reddens on the 400 assertion (turns 200) and on the
        seat-row count (turns 1).
        """
        slug = _new_org(client, "sa-name-org")
        org_id = _org_id(client, slug)
        owner = f"owner@{slug}.test"          # provisioned owner: admin, active
        _grant(db, org_id=org_id, plan_slug="sales", quantity=2)
        target = f"member-{uuid.uuid4().hex[:8]}@{slug}.test"
        _add_member(db, org_id=org_id, email=target)

        sa_key = _seat_admin_key(db, _default_box_id(db))
        r = client.post(
            "/registry/seats", headers=_headers(sa_key),
            json={"actor_email": owner, "member_email": target,
                  "plan_slug": "sales", "org_slug": slug},
        )
        assert r.status_code == 400, r.text
        assert _live_seat_count(
            db, org_id=org_id, plan_slug="sales", email=target) == 0

    def test_an_operator_seat_admin_may_not_name_an_actor(self, client, db):
        """R11 shape-guard: the operator arm has no actor (item (h)).

        The operator NAMES the org and acts as staff, never as a member; an
        `actor_email` under this scheme is a caller who believes the write is
        acting-as someone, which is not a thing here — **400, never ignored**,
        the mirror of the deployment arm's `org_slug` refusal.

        Red-first (run and reverted in a scratch copy of `main.py`): dropping the
        `if req.actor_email is not None: raise 400` guard in
        `_seat_admin_for_operator` lets the request through — the org resolves
        from `org_slug`, the target is a member, and the seat is written (**200**).
        This fence reddens on the 400 assertion and on the seat-row count.
        """
        slug = _new_org(client, "sa-op-actor")
        org_id = _org_id(client, slug)
        owner = f"owner@{slug}.test"
        _grant(db, org_id=org_id, plan_slug="sales", quantity=2)
        target = f"member-{uuid.uuid4().hex[:8]}@{slug}.test"
        _add_member(db, org_id=org_id, email=target)

        # The OPERATOR token (not a deployment key) → `caller is None` → the
        # operator arm, which names the org and takes no actor.
        r = client.post(
            "/registry/seats", headers=OP,
            json={"actor_email": owner, "member_email": target,
                  "plan_slug": "sales", "org_slug": slug},
        )
        assert r.status_code == 400, r.text
        assert _live_seat_count(
            db, org_id=org_id, plan_slug="sales", email=target) == 0

    def test_a_deployment_seat_admin_requires_an_actor_email(self, client, db):
        """R11 shape-guard: the deployment arm REQUIRES `actor_email` (item (h)).

        Org and acting admin are derived TOGETHER from
        `deployment_visible_orgs(deployment_id, actor_email)`; with no
        `actor_email` there is no member to resolve, so the write is refused
        **400** up front rather than resolving on a `None` identity.

        Red-first (run and reverted in a scratch copy of `main.py`): dropping the
        `if not req.actor_email: raise 400` guard in `_seat_admin_for_deployment`
        sends `email=None` into `deployment_visible_orgs`, which matches no
        membership, so the arm falls through to a DIFFERENT refusal (**403**) —
        never 200, never a row. This fence pins the 400 CONTRACT: it reddens the
        moment the guard is gone (the status is no longer 400).
        """
        slug = _new_org(client, "sa-no-actor")
        org_id = _org_id(client, slug)
        _grant(db, org_id=org_id, plan_slug="sales", quantity=2)
        target = f"member-{uuid.uuid4().hex[:8]}@{slug}.test"
        _add_member(db, org_id=org_id, email=target)

        sa_key = _seat_admin_key(db, _default_box_id(db))
        r = client.post(
            "/registry/seats", headers=_headers(sa_key),
            json={"member_email": target, "plan_slug": "sales"},
        )
        assert r.status_code == 400, r.text
        assert _live_seat_count(
            db, org_id=org_id, plan_slug="sales", email=target) == 0


# ── SC-4g — discount codes, the refusal partition, and the Rs 0 path ───────

class TestDiscountCodes:
    def test_a_code_is_minted_through_keys_py_and_never_stored_in_the_clear(
        self, client, db
    ):
        """Done-when 17 / SC-4g (i). The issue response is the ONLY place it
        exists — asserted over the stored row and the audit trail, never by
        reading the code."""
        issued = _issue_code(client, label="never in the clear")
        token = issued["code"]
        parsed = split_key(token)
        assert parsed is not None, "the shared seam must PARSE our format"
        prefix, secret = parsed
        assert prefix.startswith(f"cc_{ENV_DISCOUNT}_")
        assert prefix == issued["prefix"]

        with db.begin() as c:
            stored = c.execute(
                text("SELECT prefix, code_hash FROM discount_code "
                     "WHERE prefix = :p"), {"p": prefix}).first()
            audit = [
                r[0] for r in c.execute(
                    text("SELECT detail::text FROM control_audit "
                         "WHERE action = 'discount.issue'"))
            ]
        assert secret not in stored.code_hash
        assert stored.code_hash == mint_key.__globals__["hash_secret"](secret)
        assert not any(secret in row for row in audit), "secret in an audit row"
        assert any(prefix in row for row in audit), "prefix should be there"

    def test_the_five_refusals_partition_three_and_two(self, client, db, org):
        """SC-4g done-when 4 — BOTH halves in one test, deliberately.

        As first written the clause demanded five distinct reasons *and*
        unknown-equals-wrong-org in the same sentence; an implementer satisfies
        one half and quietly drops the other. Keeping both halves here is what
        stops the contradiction reappearing.
        """
        order = _order(client, org["key"])

        def _redeem(code: str):
            return client.post(
                f"/billing/orders/{order['id']}/redeem",
                headers=_headers(org["key"]), json={"code": code})

        past = "2020-01-01T00:00:00Z"
        expired = _issue_code(client, org_slug=org["slug"], expires_at=past)
        revoked = _issue_code(client, org_slug=org["slug"])
        with db.begin() as c:
            c.execute(text("UPDATE discount_code SET revoked_at = now() "
                           "WHERE prefix = :p"), {"p": revoked["prefix"]})
        exhausted = _issue_code(client, org_slug=org["slug"], percent_bp=5000)
        # Spend its single redemption on ANOTHER order of the same org.
        spent_on = _order(client, org["key"])
        assert client.post(
            f"/billing/orders/{spent_on['id']}/redeem",
            headers=_headers(org["key"]),
            json={"code": exhausted["code"]}).status_code == 200

        reasons = {
            name: _redeem(code["code"]).json()["detail"]["reason"]
            for name, code in (("expired", expired), ("revoked", revoked),
                               ("exhausted", exhausted))
        }
        assert reasons == {"expired": "expired", "revoked": "revoked",
                           "exhausted": "exhausted"}
        assert len(set(reasons.values())) == 3, "three DISTINCT reasons"

        # …and the collapsed half: unknown ≡ wrong-org, byte for byte.
        stranger = _new_org(client, "stranger")
        theirs = _issue_code(client, org_slug=stranger, label="not yours")
        unknown = mint_key(env=ENV_DISCOUNT).token
        wrong_org = _redeem(theirs["code"])
        no_such = _redeem(unknown)
        assert wrong_org.status_code == no_such.status_code == 404
        assert wrong_org.content == no_such.content
        assert wrong_org.json() == {"detail": "no such discount code"}

    def test_a_wrong_secret_on_a_real_prefix_is_the_collapsed_shape_too(
        self, client, org
    ):
        order = _order(client, org["key"])
        real = _issue_code(client, org_slug=org["slug"])
        forged = f"{real['prefix']}_notthesecret"
        r = client.post(f"/billing/orders/{order['id']}/redeem",
                        headers=_headers(org["key"]), json={"code": forged})
        assert r.status_code == 404
        assert r.json() == {"detail": "no such discount code"}

    def test_an_open_code_is_redeemable_by_any_org(self, client, org):
        """``organization_id IS NULL`` = open. The other half of the partition's
        visibility rule, which the refusal tests can only show negatively."""
        code = _issue_code(client, percent_bp=5000, label="launch promo")
        order = _order(client, org["key"])
        r = client.post(f"/billing/orders/{order['id']}/redeem",
                        headers=_headers(org["key"]),
                        json={"code": code["code"]})
        assert r.status_code == 200, r.text
        assert r.json()["discount_paise"] == 60000

    def test_a_hundred_percent_code_completes_with_zero_provider_calls(
        self, client, db, org, fake
    ):
        """SC-4g done-when 1 — the whole D42 flow, paying nothing."""
        order = _order(client, org["key"])
        calls_before = len(fake.created)
        code = _issue_code(client, org_slug=org["slug"], percent_bp=10000)

        r = client.post(f"/billing/orders/{order['id']}/redeem",
                        headers=_headers(org["key"]),
                        json={"code": code["code"]})
        assert r.status_code == 200, r.text
        body = r.json()

        assert len(fake.created) == calls_before, (
            "the coupon path deliberately never reaches the provider"
        )
        assert body["status"] == "captured"
        assert body["provider"] == "none"
        assert body["discount_paise"] == 120000
        assert body["taxable_paise"] == 0
        assert body["gst_paise"] == 0, "100 percent off -> taxable 0 -> GST 0"
        assert body["total_paise"] == 0
        assert body["discount"]["code_prefix"] == code["prefix"]

        with db.begin() as c:
            row = c.execute(
                text("SELECT provider_order_id FROM payment_order "
                     "WHERE id = :i"), {"i": order["id"]}).scalar_one()
        assert row is None, "no provider identifier survives the Rs 0 path"

        redemption = _rows(db, "discount_redemption", org["id"])
        assert len(redemption) == 1
        assert (redemption[0]["gross_paise"], redemption[0]["discount_paise"],
                redemption[0]["net_paise"]) == (120000, 120000, 0)
        # …and the ENTITLEMENT actually landed.
        assert _grants_for(db, org["id"], "sales") == 1
        assert _rows(db, "org_subscription", org["id"])[0]["status"] == (
            "active"
        )

    def test_a_partial_code_routes_the_remainder_through_the_provider(
        self, client, db, org, fake
    ):
        """SC-4g done-when 3 — ONE order, not a second flow."""
        order = _order(client, org["key"])
        code = _issue_code(client, org_slug=org["slug"], percent_bp=5000)
        calls_before = len(fake.created)

        body = client.post(f"/billing/orders/{order['id']}/redeem",
                           headers=_headers(org["key"]),
                           json={"code": code["code"]}).json()
        assert body["status"] == "created", "still open — money is still owed"
        assert body["provider"] == "razorpay"
        assert body["discount_paise"] == 60000
        assert body["taxable_paise"] == 60000
        assert body["gst_paise"] == 10800
        assert body["total_paise"] == 70800
        assert len(fake.created) == calls_before + 1, (
            "the provider order is REPLACED for the discounted amount; one "
            "created for the pre-discount total would overcharge the customer"
        )
        assert fake.created[-1]["amount"] == 70800

        # Nothing granted yet: fulfilment is on CAPTURE.
        assert _grants_for(db, org["id"], "sales") == 0
        fresh = {**order, "total_paise": 70800}
        assert _capture(client, fake, db, fresh).json()["fulfilled"] is True
        assert _grants_for(db, org["id"], "sales") == 1

    def test_a_fixed_code_larger_than_the_gross_is_clamped(self, client, org):
        """Never negative: a negative order total is a refund path (SC-4g (iii))."""
        order = _order(client, org["key"])
        code = _issue_code(client, org_slug=org["slug"], kind="fixed",
                           percent_bp=None, amount_paise=999_999_999)
        body = client.post(f"/billing/orders/{order['id']}/redeem",
                           headers=_headers(org["key"]),
                           json={"code": code["code"]}).json()
        assert body["discount_paise"] == order["gross_paise"]
        assert body["total_paise"] == 0

    def test_redemption_is_idempotent(self, client, db, org):
        """SC-4g done-when 5, first half — ``UNIQUE (code, order)``."""
        order = _order(client, org["key"])
        code = _issue_code(client, org_slug=org["slug"], percent_bp=5000,
                           max_redemptions=5)
        head = {"code": code["code"]}
        first = client.post(f"/billing/orders/{order['id']}/redeem",
                            headers=_headers(org["key"]), json=head)
        second = client.post(f"/billing/orders/{order['id']}/redeem",
                             headers=_headers(org["key"]), json=head)
        assert first.status_code == second.status_code == 200
        assert first.json()["total_paise"] == second.json()["total_paise"]
        assert len(_rows(db, "discount_redemption", org["id"])) == 1

    def test_a_second_different_code_on_one_order_is_refused(
        self, client, org
    ):
        """Stacking is a commercial decision nobody has taken, so it is refused
        rather than invented here. Named separately from the five: it is a
        statement about the ORDER, not about the code."""
        order = _order(client, org["key"])
        one = _issue_code(client, org_slug=org["slug"], percent_bp=1000)
        two = _issue_code(client, org_slug=org["slug"], percent_bp=1000)
        assert client.post(f"/billing/orders/{order['id']}/redeem",
                           headers=_headers(org["key"]),
                           json={"code": one["code"]}).status_code == 200
        r = client.post(f"/billing/orders/{order['id']}/redeem",
                        headers=_headers(org["key"]),
                        json={"code": two["code"]})
        assert r.status_code == 409
        assert r.json()["detail"]["reason"] == "already_discounted"

    def test_a_concurrent_double_redeem_yields_one_success_and_one_refusal(
        self, client, db, org
    ):
        """SC-4g done-when 5, second half — RACED, not reasoned about.

        Two DIFFERENT orders, one ``max_redemptions = 1`` code, two threads
        released together. ``UNIQUE (discount_code_id, order_id)`` does not
        cover this — it stops one order redeeming one code twice, which is a
        different claim from N redemptions in total — so what holds is
        ``store.lock_discount_capacity``. Ten iterations, because a race that
        reproduces once in five is still a race.
        """
        for _ in range(10):
            code = _issue_code(client, org_slug=org["slug"], percent_bp=1000,
                               max_redemptions=1)
            orders = [_order(client, org["key"]) for _ in range(2)]
            gate = threading.Barrier(2, timeout=30)

            def _race(order_id: str, _code=code["code"], _gate=gate):
                c = TestClient(app)
                _gate.wait()
                return c.post(f"/billing/orders/{order_id}/redeem",
                              headers=_headers(org["key"]),
                              json={"code": _code})

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [f.result() for f in
                           [pool.submit(_race, o["id"]) for o in orders]]

            assert sorted(r.status_code for r in results) == [200, 409], (
                [r.status_code for r in results]
            )
            with db.begin() as c:
                used = int(c.execute(
                    text("SELECT count(*) FROM discount_redemption r "
                         "JOIN discount_code d ON d.id = r.discount_code_id "
                         "WHERE d.prefix = :p"),
                    {"p": code["prefix"]}).scalar_one())
            assert used == 1, f"max_redemptions=1 but {used} landed"

    def test_issue_and_redemption_each_land_an_audit_row(
        self, client, db, org
    ):
        """SC-4g done-when 7 — asserted over the audit RECORD, not the code."""
        order = _order(client, org["key"])
        code = _issue_code(client, org_slug=org["slug"], percent_bp=5000)
        client.post(f"/billing/orders/{order['id']}/redeem",
                    headers=_headers(org["key"]),
                    json={"code": code["code"]})
        with db.begin() as c:
            rows = {
                r[0]: (r[1], r[2]) for r in c.execute(
                    text("SELECT action, actor, detail::text FROM "
                         "control_audit WHERE organization_id = :o "
                         "AND action LIKE 'discount%'"),
                    {"o": org["id"]})
            }
        assert set(rows) == {"discount.issue", "discount.redeem"}
        for _actor, detail in rows.values():
            assert code["prefix"] in detail
            assert code["code"] not in detail, "the SECRET must never land"
        assert rows["discount.issue"][0] == "operator"
        assert rows["discount.redeem"][0] == "organization"

    def test_a_redeem_attempt_logs_the_prefix_and_only_the_prefix(
        self, client, org, caplog
    ):
        """The signal a rate limiter would later be sized from (9.3(6))."""
        order = _order(client, org["key"])
        code = _issue_code(client, org_slug=org["slug"], percent_bp=5000)
        with caplog.at_level("INFO"):
            r = client.post(f"/billing/orders/{order['id']}/redeem",
                            headers=_headers(org["key"]),
                            json={"code": code["code"]})
        assert r.status_code == 200, r.text
        attempts = [rec for rec in caplog.records
                    if rec.message == "payments.redeem_attempt"]
        assert len(attempts) == 1, caplog.text
        assert attempts[0].code_prefix == code["prefix"]
        assert code["code"] not in json.dumps(
            attempts[0].__dict__, default=str)

    def test_a_failing_redeem_attempt_is_logged_and_carries_no_secret(
        self, client, org, caplog
    ):
        """P2, 2026-08-19. **The measured attempt rate was zero by construction.**

        ``_log_redeem_attempt``'s own docstring says the line exists to supply
        the MEASURED attempt rate that 9.3(6) defers the rate-limit decision
        to. It sat *below* ``_verified_code``, which raises on all four refusal
        shapes — malformed, unknown prefix, wrong secret, wrong org — so under
        the only traffic anyone would size a limiter against (probing), the
        counter never incremented at all. The one attempt it did record was the
        successful redemption, i.e. the case a limiter is not for.

        The fence above keeps the *success* case and the prefix-only rule; this
        one covers the failing attempt. Red-first: the log list is empty.
        """
        order = _order(client, org["key"])
        minted = mint_key(env=ENV_DISCOUNT)
        with caplog.at_level("INFO"):
            r = client.post(f"/billing/orders/{order['id']}/redeem",
                            headers=_headers(org["key"]),
                            json={"code": minted.token})
        assert r.status_code == 404, r.text
        attempts = [rec for rec in caplog.records
                    if rec.message == "payments.redeem_attempt"]
        assert len(attempts) == 1, (
            "a REFUSED attempt is the one a limiter would be sized on"
        )
        assert attempts[0].code_prefix == minted.prefix
        assert minted.token not in json.dumps(
            attempts[0].__dict__, default=str), "the SECRET must never land"


# ── Clause 15 / SC-4g 2 — the two paths write identical records ────────────

#: Excluded from the comparison **by CLASS, not by field list** (§9.6(3)), so a
#: column added later is covered by the rule instead of quietly escaping a
#: hand-list. Both classes are expressed as PREDICATES for exactly that reason:
#:
#: **(a) surrogate ids** — ``gen_random_uuid()`` primary keys. The database
#: mints them, not the code under test, so two runs of the SAME path already
#: differ on them. This is why the fence was unsatisfiable as first written.
#:
#: **(b) clock columns** — every timestamp the database wrote from ``now()``:
#: ``created_at``, ``updated_at``, ``effective_from``, ``assigned_at``, and —
#: found by building this — ``trial_ends_at``, which provisioning derives from
#: ``now()`` and which therefore differs by the milliseconds between two
#: fixtures. Named as a class and detected by TYPE, so the next such column is
#: covered without anyone remembering it exists.
#:
#: ⚠️ Excluding a class is not the same as ignoring it: the period columns that
#: fall inside class (b) are asserted EQUAL separately below, so the exclusion
#: cannot hide a difference in the term the two paths sold.
#:
#: ⚠️ Detected by the VALUE's type, never by the column's name. A name rule
#: would have swallowed ``provider_customer_id`` and
#: ``provider_subscription_id`` — the two columns whose difference the fence
#: exists to ASSERT — because they end in ``_id`` while holding provider TEXT.
def _excluded(free_value, paid_value) -> bool:
    import datetime as _dt

    return any(
        isinstance(value, uuid.UUID | _dt.datetime | _dt.date)
        for value in (free_value, paid_value)
    )


class TestTheFreePathAndThePaidPathAreOneProduct:
    def test_the_free_path_and_the_paid_path_write_identical_records(
        self, client, db, fake
    ):
        """SC-4g's central fence (done-when 2, CP-9 done-when 15).

        Both paths run over an IDENTICAL basket and the written rows are
        compared field by field, minus the excluded classes. **Exactly two
        differences are allowed**: the ``seat_grant.reason`` prefix and
        ``org_subscription``'s three provider columns. A third difference means
        one of the two paths is not the product, which is precisely the failure
        D42 exists to prevent.

        ⚠️ As first written this fence was UNSATISFIABLE — "only the reference
        differs" is false against ``gen_random_uuid()`` ids and ``now()``
        defaults, so two runs of the SAME path already differed. It is
        satisfiable now because the excluded classes are named, which is what
        makes it a fence rather than a thing the next agent narrows.
        """
        # ONE owner for both organizations, deliberately: `user_identity` is
        # global, so a shared owner makes `seat_assignment.user_identity_id`
        # equal BY CONSTRUCTION rather than by exclusion — a difference removed
        # is worth more than a difference excused.
        owner = f"zero-{uuid.uuid4().hex[:8]}@fracktal.test"
        free_slug = _new_org(client, "free", owner_email=owner)
        paid_slug = _new_org(client, "paid", owner_email=owner)
        free_id, paid_id = (_org_id(client, free_slug),
                            _org_id(client, paid_slug))
        free_key, paid_key = (_org_key(client, free_slug),
                              _org_key(client, paid_slug))

        # The Rs 0 path.
        free_order = _order(client, free_key)
        code = _issue_code(client, org_slug=free_slug, percent_bp=10000)
        assert client.post(
            f"/billing/orders/{free_order['id']}/redeem",
            headers=_headers(free_key),
            json={"code": code["code"]}).status_code == 200

        # The paid path, over an identical basket.
        paid_order = _order(client, paid_key)
        assert _capture(client, fake, db, paid_order).json()["fulfilled"]

        differences: set[tuple[str, str]] = set()
        for table in ENTITLEMENT_TABLES:
            def _sorted(org_id: str, _table=table) -> list[dict]:
                # Ordered by plan then quantity so the two paths' rows pair up
                # deterministically — never by a clock, and never by the
                # planner's idea of order.
                return sorted(
                    _rows(db, _table, org_id),
                    key=lambda r: (str(r.get("plan_slug")),
                                   str(r.get("quantity_purchased")),
                                   str(r.get("source"))),
                )

            free_rows, paid_rows = _sorted(free_id), _sorted(paid_id)
            assert len(free_rows) == len(paid_rows), (
                f"{table}: {len(free_rows)} rows on the free path vs "
                f"{len(paid_rows)} on the paid one — a difference in COUNT is "
                "a difference in product"
            )
            for free_row, paid_row in zip(free_rows, paid_rows, strict=True):
                for column, value in free_row.items():
                    if _excluded(value, paid_row[column]):
                        continue
                    if value != paid_row[column]:
                        differences.add((table, column))

        assert differences == {
            ("seat_grant", "reason"),
            ("org_subscription", "provider"),
            ("org_subscription", "provider_customer_id"),
            ("org_subscription", "provider_subscription_id"),
        }, differences

        # And the ONE asserted difference is asserted, not merely allowed.
        free_sub = _rows(db, "org_subscription", free_id)[0]
        paid_sub = _rows(db, "org_subscription", paid_id)[0]
        assert (free_sub["provider"], free_sub["provider_customer_id"],
                free_sub["provider_subscription_id"]) == (None, None, None)
        assert paid_sub["provider"] == "razorpay"
        assert paid_sub["provider_customer_id"] == "cust_FAKE0001"
        assert paid_sub["provider_subscription_id"] is not None

        # The excluded CLASS cannot hide a difference in what was sold: the
        # term itself is asserted equal, explicitly.
        assert (free_sub["current_period_start"],
                free_sub["current_period_end"]) == (
            paid_sub["current_period_start"], paid_sub["current_period_end"])

    def test_a_discounted_grant_is_tellable_from_a_paid_one_a_year_later(
        self, client, db, fake
    ):
        """SC-4g done-when 6 — classified from the STORED ROWS ALONE.

        Three cases seeded, then told apart with no access to how they were
        created: a discounted purchase has a ``discount_redemption`` row
        referencing its order, a paid purchase has none, and an SC-4e-style
        adjustment has no order at all. ``seat_grant.reason`` carries
        ``<reason>:<ref>`` from the ONE vocabulary.

        ⚠️ This deliberately does NOT ride ``credit_ledger``: CP-9 §9.6 writes
        ZERO ledger rows on the subscription path at launch, so that version of
        the test would pass over an empty table — the disarmed-gate shape CP-3
        already cost us once.
        """
        free_slug = _new_org(client, "tell-free")
        paid_slug = _new_org(client, "tell-paid")
        adj_slug = _new_org(client, "tell-adj")
        free_id, paid_id = (_org_id(client, free_slug),
                            _org_id(client, paid_slug))
        adj_id = _org_id(client, adj_slug)

        free_order = _order(client, _org_key(client, free_slug))
        code = _issue_code(client, org_slug=free_slug, percent_bp=10000)
        client.post(f"/billing/orders/{free_order['id']}/redeem",
                    headers=_headers(_org_key(client, free_slug)),
                    json={"code": code["code"]})
        paid_order = _order(client, _org_key(client, paid_slug))
        _capture(client, fake, db, paid_order)
        # The adjustment: a goodwill grant with no order behind it.
        client.post("/credits/grant", headers=OP, json={
            "org_slug": adj_slug, "credits": "100", "reason": "adjustment",
            "ref": "note-4471"})

        def _classify(org_id: str) -> str:
            grants = [
                g for g in _rows(db, "seat_grant", org_id)
                if (g["reason"] or "").split(":", 1)[0] in credits.LEDGER_REASONS
            ]
            if not grants:
                return "adjustment"
            reason = grants[0]["reason"].split(":", 1)[0]
            assert reason in credits.LEDGER_REASONS, reason
            has_redemption = bool(_rows(db, "discount_redemption", org_id))
            assert (reason == "discount_redemption") == has_redemption
            return reason

        assert _classify(free_id) == "discount_redemption"
        assert _classify(paid_id) == "purchase"
        assert _classify(adj_id) == "adjustment"

    def test_the_reference_and_its_carrier_round_trip(self, client, db, fake):
        """``<reason>:<ref>``, recovered by ``split(":", 1)`` (§9.6(2))."""
        slug = _new_org(client, "carrier")
        org_id = _org_id(client, slug)
        order = _order(client, _org_key(client, slug))
        _capture(client, fake, db, order)
        grant = next(g for g in _rows(db, "seat_grant", org_id)
                     if g["plan_slug"] == "sales")
        reason, ref = grant["reason"].split(":", 1)
        assert reason == credits.LEDGER_REASON_PURCHASE
        assert ref == f"order:{order['id']}"


# ── SC-4g (v) — the ledger vocabulary, fenced where it is REAL today ───────

def _validates_the_vocabulary(tree: ast.Module) -> bool:
    """Does this module validate a ``reason`` field against LEDGER_REASONS?

    What licenses shape (4): a request model's ``.reason`` is only as good as
    the model, so the fence goes and looks at the model instead of trusting the
    call site's spelling.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        decorators = ast.unparse(ast.Module(body=list(node.decorator_list),
                                            type_ignores=[]))
        # `ast.unparse` normalises string quoting, so match on the shape rather
        # than on the quote character the source happens to use.
        if re.search(r"field_validator\(['\"]reason['\"]\)", decorators):
            return "LEDGER_REASONS" in ast.unparse(node)
    return False


def _bound_from_reason_for(tree: ast.Module, name: str) -> bool:
    """Is ``name`` assigned from ``reason_for(...)`` anywhere in this module?"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(
                node.value, ast.Call):
            continue
        callee = getattr(node.value.func, "attr",
                         getattr(node.value.func, "id", ""))
        if callee != "reason_for":
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return True
    return False


class TestTheLedgerVocabulary:
    def test_every_add_credit_call_site_passes_a_named_reason(self):
        """Structural over the CALL SITES — real in this slice, unlike a data
        test over a table the subscription path leaves empty.

        Four shapes are accepted and nothing else:

        1. a literal that IS a member;
        2. a ``LEDGER_REASON_*`` constant;
        3. a local bound from ``payments.reason_for(...)`` — the ONE
           sanctioned derivation, itself total over the vocabulary and raising
           on anything else;
        4. ``<request model>.reason``, but **only** in a module that validates
           that field against ``LEDGER_REASONS`` — which is why
           ``CreditGrantRequest`` grew a validator in this slice rather than
           being exempted here.

        A bare variable from anywhere else is an offender, because "it probably
        holds a member" is the assumption this fence exists to remove.
        """
        offenders: list[str] = []
        for module, tree in _package_modules().items():
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "attr", getattr(call.func, "id", ""))
                if name != "add_credit":
                    continue
                reason = next(
                    (kw.value for kw in call.keywords if kw.arg == "reason"),
                    None,
                )
                if reason is None:
                    offenders.append(f"{module}: add_credit with no reason=")
                elif isinstance(reason, ast.Constant):
                    if reason.value not in credits.LEDGER_REASONS:
                        offenders.append(f"{module}: {reason.value!r}")
                elif isinstance(reason, ast.Name):
                    if not (reason.id.startswith("LEDGER_REASON")
                            or _bound_from_reason_for(tree, reason.id)):
                        offenders.append(f"{module}: {reason.id}")
                elif isinstance(reason, ast.Attribute):
                    if not (reason.attr == "reason"
                            and _validates_the_vocabulary(tree)):
                        offenders.append(f"{module}: .{reason.attr}")
                else:
                    offenders.append(f"{module}: {ast.dump(reason)[:40]}")
        assert offenders == [], offenders

    def test_reason_for_is_total_over_the_vocabulary_and_refuses_the_rest(self):
        """The sanctioned derivation, pinned — or the rule above has a hole."""
        assert payments.reason_for("order:x") == "purchase"
        assert payments.reason_for("redemption:x") == "discount_redemption"
        for value in payments._REASON_FOR_REFERENCE_KIND.values():
            assert value in credits.LEDGER_REASONS
        with pytest.raises(ValueError):
            payments.reason_for("refund:x")

    def test_an_operator_grant_with_an_unknown_reason_is_refused(
        self, client, org
    ):
        """The EXPAND-phase half of SC-4g (v), and the only half in this slice.

        ``/credits/grant`` accepted any string until now, which is what made
        *"distinguishable a year later"* unenforceable. Narrowing the request
        model comes first; a ``CHECK`` on the column would reject rows the
        running code can still write, which is R6's subject exactly.
        """
        r = client.post("/credits/grant", headers=OP, json={
            "org_slug": org["slug"], "credits": "10", "reason": "goodwill"})
        assert r.status_code == 422, r.text
        ok = client.post("/credits/grant", headers=OP, json={
            "org_slug": org["slug"], "credits": "10",
            "reason": "adjustment", "ref": "note-1"})
        assert ok.status_code == 200, ok.text

    def test_the_vocabulary_is_the_five_named_reasons(self):
        assert {
            "usage", "purchase", "discount_redemption", "adjustment", "grant",
        } == credits.LEDGER_REASONS

    def test_the_launch_subscription_path_writes_no_ledger_rows(
        self, client, db, org, fake
    ):
        """Stated as a fact rather than left implicit: §9.1 sells no credit
        pack, so the ledger branch in ``fulfil`` has zero rows to write — which
        is exactly why done-when 6 rides ``discount_redemption`` instead."""
        order = _order(client, org["key"])
        _capture(client, fake, db, order)
        assert _rows(db, "credit_ledger", org["id"]) == []
        assert payments.PACK_KINDS.isdisjoint({"core", "center", "addon",
                                               "bundle"})


# ── The seam itself, and the CI gate that keeps this file honest ──────────

class TestTheProviderSeam:
    def test_the_fake_signs_with_the_real_algorithm(self, fake):
        """Only the network is fake (§9.4).

        Computed here from ``hmac``/``hashlib`` directly rather than from the
        module under test — a fake and a verifier that share a wrong
        implementation agree perfectly.
        """
        import hashlib
        import hmac as hmac_

        raw = b'{"event":"payment.captured"}'
        expected = hmac_.new(fake.webhook_secret.encode(), raw,
                             hashlib.sha256).hexdigest()
        assert fake.sign(raw) == expected
        assert len(expected) == 64

    def test_the_razorpay_provider_calls_the_documented_endpoint(self):
        """The HTTP shape, fenced without an account (which is OWNER-GATE).

        ⚠️ **Nothing on a request path calls ``create_order`` against the REAL
        provider in this slice, and no agent may make it do so** — creating the
        account is owner-side even in test mode. What is fenced here is the
        request we WOULD send: the endpoint, the basic auth, and that the
        amount crosses as integer paise.
        """
        import httpx

        seen: dict[str, object] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"id": "order_REAL01", "amount": 141600})

        transport = httpx.MockTransport(_handler)
        provider = payments.RazorpayProvider(
            key_id="rzp_test_x", key_secret="s", webhook_secret="w")
        with httpx.Client(transport=transport) as http:
            original = httpx.post
            httpx.post = lambda url, **kw: http.post(
                url, **{k: v for k, v in kw.items() if k != "timeout"})
            try:
                result = provider.create_order(
                    amount_paise=141600, receipt="ord-1",
                    notes={"organization_id": "org-1"})
            finally:
                httpx.post = original

        assert seen["url"] == "https://api.razorpay.com/v1/orders"
        assert seen["auth"] is not None, "basic auth from the env credentials"
        assert seen["body"] == {
            "amount": 141600, "currency": "INR", "receipt": "ord-1",
            "notes": {"organization_id": "org-1"},
        }
        assert isinstance(seen["body"]["amount"], int)
        assert result.provider_order_id == "order_REAL01"

    def test_no_razorpay_sdk_dependency_was_added(self):
        """§9.4: two endpoints and one HMAC do not justify a supply-chain
        decision on a CROSS-TENANT service."""
        pyproject = _uncommented(
            Path(__file__).resolve().parents[2] /
            "apps/services/customer_console/pyproject.toml"
        )
        assert "razorpay" not in pyproject
        assert "httpx" in pyproject, "the HTTP client IS declared"

    def test_the_webhook_verifier_is_an_authenticating_dependency(self):
        """Or CP-2b clause 1's fence goes red — correctly (§9.5).

        The webhook is a door with no bearer token. Expressed as anything but
        an authenticating dependency it would make the route look
        unauthenticated, and a signature check that lives in a route body is
        one refactor away from not existing.
        """
        assert auth.razorpay_webhook_event in auth.AUTHENTICATING_DEPENDENCIES
        assert auth.organization_for_payment in (
            auth.AUTHENTICATING_DEPENDENCIES
        )
        assert auth.organization_for_payment in (
            auth.ORGANIZATION_KEY_DEPENDENCIES
        )

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """Done-when 11 / SC-4g 8 — the hand-list discovers NOTHING.

        An R8 suite that is not named there still runs, still skips silently
        without a database, and still leaves the job green: the exact failure
        this step exists to catch, which is why the suite asserts its own
        membership rather than trusting a reviewer to notice.
        """
        workflow = (Path(__file__).resolve().parents[2] /
                    ".github/workflows/pr-check.yml").read_text(
                        encoding="utf-8")
        assert "tests/unit/test_customer_console_payments.py" in workflow

    def test_the_owning_specs_name_this_suite(self):
        """The §7 command block, in the same PR that creates the file."""
        root = Path(__file__).resolve().parents[2] / "project-docs/specs"
        for spec in ("customer_console.md", "subscription_console.md"):
            body = (root / spec).read_text(encoding="utf-8")
            assert "test_customer_console_payments.py" in body, spec


# ── Expiry: `abandoned` is written by the CLOCK, never by the customer ─────

class TestExpiry:
    def test_an_expired_order_is_abandoned_and_refuses_redemption(
        self, client, db, org
    ):
        """§9.2. The customer walking away tells you nothing; the clock does."""
        order = _order(client, org["key"])
        code = _issue_code(client, org_slug=org["slug"], percent_bp=10000)
        with db.begin() as c:
            c.execute(
                text("UPDATE payment_order SET expires_at = now() - "
                     "interval '1 minute' WHERE id = :i"), {"i": order["id"]})

        r = client.post(f"/billing/orders/{order['id']}/redeem",
                        headers=_headers(org["key"]),
                        json={"code": code["code"]})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == {"reason": "order_not_open",
                                      "status": "abandoned"}
        # The abandonment SURVIVED the refusal — it is written in its own
        # transaction precisely so a rollback cannot take it with it.
        assert _status(db, order["id"]) == "abandoned"
        assert _rows(db, "discount_redemption", org["id"]) == []

    def test_a_terminal_order_cannot_be_redeemed(self, client, db, org, fake):
        order = _order(client, org["key"])
        _capture(client, fake, db, order)
        code = _issue_code(client, org_slug=org["slug"], percent_bp=10000)
        r = client.post(f"/billing/orders/{order['id']}/redeem",
                        headers=_headers(org["key"]),
                        json={"code": code["code"]})
        assert r.status_code == 409
        assert r.json()["detail"]["status"] == "captured"


# ── The migration's own text: replay safety and documentation ─────────────

class TestTheMigrationFile:
    def test_every_statement_is_replay_safe(self):
        source = _payments_migration().read_text(encoding="utf-8")
        creates = re.findall(r"CREATE\s+(TABLE|INDEX|UNIQUE INDEX)\s+(?!IF)",
                             source)
        assert creates == [], f"non-idempotent DDL: {creates}"

    def test_the_tables_carry_comments(self):
        """A table whose reason lives only in a spec is a table nobody reads
        the spec for."""
        source = _payments_migration().read_text(encoding="utf-8")
        for table in ("payment_order", "payment_event", "discount_code",
                      "discount_redemption"):
            assert f"COMMENT ON TABLE {table}" in source, table
