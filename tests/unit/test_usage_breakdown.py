"""Spend by APP for a customer, and one customer's breakdown for us. Usage slice 2.

Spec: ``ai_metering_and_analytics.md`` §5 · D66 · H-133 · H-134.

🔴 **The customer page could never show an app.** ``usage_by_activity``
groups by ``COALESCE(agent, module_slug)``. Every agent call names its agent,
so the app was unreachable. That was harmless while ``module_slug`` was always
empty, and usage slice 1 fills it in.

🔴 **The operator could see a customer's total and never what it went on.**
H-133 put the daily series on the customer page. When the customer asks where
the credits went, we need the split their admin sees, plus our cost.

⚠️ **R8.** Grouping, CITEXT folding, the NULL-safe member filter and the join
keys are all properties of what Postgres does. A fake agrees with whatever it
is handed.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_usage_breakdown.py -v -rs
"""
from __future__ import annotations

import ast
import os
import pathlib
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("sqlalchemy")
from customer_console import store
from sqlalchemy import create_engine

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()
_NEEDS_DB = pytest.mark.skipif(
    not _URL,
    reason="CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
    "A skip here is not a pass; CI must set it.",
)
UN = store.UNATTRIBUTED_ACTIVITY
MAIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "apps/services/customer_console/customer_console/main.py"
)


@pytest.fixture(scope="module")
def engine():
    if not _URL:
        pytest.skip("no database")
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    return eng


@pytest.fixture
def conn(engine):
    """Rolled back after every test, so tests never see each other."""
    with engine.connect() as connection:
        tx = connection.begin()
        try:
            yield connection
        finally:
            tx.rollback()


@pytest.fixture
def org(conn) -> str:
    return store.ensure_organization(
        conn, slug=f"brk-{uuid.uuid4().hex[:8]}", name="Breakdown Pvt Ltd",
        gstin="29ABCDE1234F1Z5", billing_state="KA",
    )


def _use(conn, org, *, credits="1", cost="0.001", **fields):
    assert store.record_usage(
        conn, org_id=org, request_id=f"req-{uuid.uuid4().hex}",
        billed_credits=Decimal(credits), provider_cost_usd=Decimal(cost), **fields,
    ) is True


@_NEEDS_DB
class TestSpendByApp:
    def test_an_app_holds_its_agents_and_they_ADD_UP(self, conn, org):
        """🔴 The whole read. Two agents in one app, one in another."""
        _use(conn, org, module_slug="projects", agent="projects-assistant", credits="10")
        _use(conn, org, module_slug="projects", agent="projects-assistant", credits="5")
        _use(conn, org, module_slug="projects", agent="task-manager", credits="2")
        _use(conn, org, module_slug="crm", agent="crm-assistant", credits="4")

        rows = store.usage_by_app(conn, org_id=org)

        assert [r["app"] for r in rows] == ["projects", "crm"]
        projects = rows[0]
        assert projects["credits"] == Decimal("17")
        assert projects["calls"] == 3
        assert [(g["agent"], g["credits"]) for g in projects["agents"]] == [
            ("projects-assistant", Decimal("15")), ("task-manager", Decimal("2"))]
        assert sum(g["credits"] for g in projects["agents"]) == projects["credits"]

    def test_a_call_with_NO_APP_is_a_row_not_a_gap(self, conn, org):
        """Dropping it would make the apps silently fail to add up to the
        organization's total. A named gap is also how the operator SEES that
        some caller is still not attributing."""
        _use(conn, org, module_slug=None, agent=None, credits="7")

        rows = store.usage_by_app(conn, org_id=org)

        assert rows[0]["app"] == UN
        assert rows[0]["agents"][0]["agent"] == UN

    def test_ONE_PERSON_sees_only_their_own_apps(self, conn, org):
        """The member view. CITEXT, so the case a browser sends does not
        matter — only a real server knows that."""
        _use(conn, org, module_slug="projects", agent="a", user_email="Dana@Acme.com")
        _use(conn, org, module_slug="crm", agent="b", user_email="ravi@acme.com")

        mine = store.usage_by_app(conn, org_id=org, member="dana@acme.com")

        assert [r["app"] for r in mine] == ["projects"]

    def test_a_REFUSAL_is_not_a_call(self, conn, org):
        _use(conn, org, module_slug="projects", agent="a")
        _use(conn, org, module_slug="projects", agent="a", credits="0",
             refusal_reason="insufficient_credits")

        assert store.usage_by_app(conn, org_id=org)[0]["calls"] == 1


@_NEEDS_DB
class TestOurCostJoinsOnTheSameKeys:
    """🔴 The operator route joins cost onto the customer's own rows BY KEY.
    A key the two reads spell differently joins to nothing and shows a free
    call — the confident-wrong-number `analytics.margin_ratio` exists to
    avoid."""

    def test_by_APP_and_by_APP_AGENT(self, conn, org):
        _use(conn, org, module_slug="projects", agent="p", cost="0.25")
        _use(conn, org, module_slug="projects", agent="q", cost="0.50")
        _use(conn, org, module_slug=None, agent=None, cost="0.10")

        by_app = store.usage_cost_by(conn, org_id=org, by="app")
        by_pair = store.usage_cost_by(conn, org_id=org, by="app_agent")
        rows = store.usage_by_app(conn, org_id=org)

        for r in rows:
            assert r["app"] in by_app
            for g in r["agents"]:
                assert (r["app"], g["agent"]) in by_pair
        assert by_app["projects"] == Decimal("0.75")
        assert by_pair[("projects", "q")] == Decimal("0.50")
        assert by_app[UN] == Decimal("0.10")

    def test_by_MEMBER_folds_case_exactly_as_the_member_read_does(self, conn, org):
        """Two spellings of one address are one person, in both reads."""
        _use(conn, org, user_email="Dana@Acme.com", cost="0.30")
        _use(conn, org, user_email="dana@acme.com", cost="0.20")
        _use(conn, org, user_email=None, cost="0.05")

        cost = store.usage_cost_by(conn, org_id=org, by="member")
        people = store.usage_by_member(conn, org_id=org)

        assert len(people) == 2
        for p in people:
            assert p["member"] in cost, f"{p['member']} joins to no cost"
        dana = next(p for p in people if p["member"] != UN)
        assert cost[dana["member"]] == Decimal("0.50")

    def test_an_unknown_dimension_is_a_KeyError_never_SQL(self, conn, org):
        """⚠️ The grouping comes from a fixed map. An arbitrary string must
        never reach the statement."""
        with pytest.raises(KeyError):
            store.usage_cost_by(conn, org_id=org, by="user_email; DROP TABLE x")


# ── The routes ──────────────────────────────────────────────────────────────


@_NEEDS_DB
class TestTheRoutes:
    @pytest.fixture
    def served(self, monkeypatch):
        from fastapi.testclient import TestClient
        from sqlalchemy import text

        from tests.unit._customer_console_ladder import (
            DEFAULT_DEPLOYMENT_LABEL,
            ensure_deployment,
        )

        token = "test-operator-token"
        monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", token)
        monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", "test-enc-key-not-real")
        eng = create_engine(_URL, future=True)
        with eng.begin() as c:
            apply_ladder(c)
            ensure_deployment(c)

        from customer_console.main import app
        client = TestClient(app)
        op = {"Authorization": f"Bearer {token}"}
        slug = f"brk-{uuid.uuid4().hex[:8]}"
        client.post("/orgs/provision", headers=op, json={
            "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
            "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
        key = client.post("/keys", headers=op, json={"org_slug": slug}).json()["token"]
        with eng.begin() as c:
            org_id = str(c.execute(
                text("SELECT id FROM organization WHERE slug = :s"), {"s": slug}
            ).scalar_one())
            _use(c, org_id, module_slug="projects", agent="projects-assistant",
                 user_email="dana@acme.com", credits="12", cost="0.02")
            _use(c, org_id, module_slug="crm", agent="crm-assistant",
                 user_email="ravi@acme.com", credits="3", cost="0.01")
        eng.dispose()
        return client, op, {"Authorization": f"Bearer {key}"}, slug

    def test_the_CUSTOMER_read_carries_no_cost_no_model_no_tier(self, served):
        """🔴 D66, checked on the WIRE and not only in the SQL. A response
        field is how a cost reaches a screen even when the query is clean."""
        client, _, key, _ = served
        r = client.get("/my/usage/apps", headers=key)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [a["app"] for a in body["rows"]] == ["projects", "crm"]

        def keys(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    yield k
                    yield from keys(v)
            elif isinstance(x, list):
                for v in x:
                    yield from keys(v)

        leaked = [k for k in keys(body)
                  if any(w in k.lower() for w in ("cost", "model", "provider", "tier", "margin"))]
        assert not leaked, f"the customer read leaks {leaked}"

    def test_a_member_scope_narrows_the_customer_read(self, served):
        client, _, key, _ = served
        r = client.get("/my/usage/apps", headers=key, params={"member": "ravi@acme.com"})
        assert [a["app"] for a in r.json()["rows"]] == ["crm"]

    def test_the_OPERATOR_breakdown_has_apps_people_and_cost(self, served):
        client, op, _, slug = served
        r = client.get("/admin/usage/breakdown", headers=op, params={"org_slug": slug})
        assert r.status_code == 200, r.text
        body = r.json()
        apps = {a["app"]: a for a in body["apps"]}
        assert apps["projects"]["credits"] == "12.0000"
        assert Decimal(apps["projects"]["costUsd"]) == Decimal("0.02")
        assert Decimal(apps["projects"]["agents"][0]["costUsd"]) == Decimal("0.02")
        people = {m["member"]: m for m in body["members"]}
        assert Decimal(people["dana@acme.com"]["costUsd"]) == Decimal("0.02")
        assert "realisedMargin" in apps["projects"]

    def test_a_CUSTOMER_KEY_cannot_open_the_operator_breakdown(self, served):
        """Our cost, behind the operator door and nothing else."""
        client, _, key, slug = served
        r = client.get("/admin/usage/breakdown", headers=key, params={"org_slug": slug})
        assert r.status_code in (401, 403)

    def test_an_unknown_customer_is_a_404(self, served):
        client, op, _, _ = served
        r = client.get("/admin/usage/breakdown", headers=op, params={"org_slug": "no-such-org"})
        assert r.status_code == 404


# ── The fence (hermetic, never skips) ───────────────────────────────────────


class TestNoCustomerRouteReachesOurCost:
    def test_no_my_route_calls_the_cost_read_or_returns_a_cost_shape(self):
        """🔴 The rule a well-meaning future change breaks by adding one
        obviously useful column to the customer page.

        Reads the AST, not the text, so a comment naming the function does not
        trip it.
        """
        tree = ast.parse(MAIN.read_text(encoding="utf-8"))
        cost_names = {"usage_cost_by", "OpAppRow", "OpAgentRow", "OpMemberRow",
                      "OrgBreakdownView"}
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            paths = [
                d.args[0].value
                for d in node.decorator_list
                if isinstance(d, ast.Call) and d.args
                and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)
            ]
            if not any(p.startswith("/my/") for p in paths):
                continue
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
                n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            if node.returns is not None:
                used |= {n.id for n in ast.walk(node.returns) if isinstance(n, ast.Name)}
            hit = used & cost_names
            if hit:
                offenders.append(f"{node.name}: {sorted(hit)}")
        assert not offenders, f"customer routes that reach our cost: {offenders}"

    def test_the_fence_can_actually_SEE_the_new_routes(self):
        """A fence that finds no `/my/` route passes for the wrong reason."""
        src = MAIN.read_text(encoding="utf-8")
        assert '@app.get("/my/usage/apps")' in src
        assert '@app.get("/admin/usage/breakdown")' in src
