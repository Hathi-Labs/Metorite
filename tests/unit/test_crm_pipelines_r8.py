"""R8 — discovery, the source profile and the deal pipelines, against a real
Postgres (WS-53 CRM-Q1 and CRM-Q2, ``crm_platform.md`` §18).

Migration 247 adds ``crm_source_profiles``, ``crm_pipelines``,
``crm_pipeline_stages``, ``crm_deals.pipeline_id`` and
``crm_deal_statuses.forecast_category``. The engine reads a fake Zoho
(``httpx.MockTransport``, no network) and writes through the mirror bypass.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

- discovery stores a profile, versions it only on a change, counts its
  calls, reads no record, and puts the org domain and edition in
  ``provider_meta`` (which a token refresh and a reconnect keep);
- a fake Zoho with two pipelines gives two pipelines with their stage
  membership, one global stage list, and a forecast category on the stages;
- a deal finds its pipeline from a name string, from an object, and by the
  default fallback, and a pipeline that Zoho drops is deleted here;
- org B never sees the pipelines, the profile or the deals of org A;
- ``GET /crm/pipelines``, ``?pipeline_id=`` on the deal list and the board, and
  ``GET /crm/source-profile`` answer the contract shape;
- the Native seed and the migration backfill each give ONE default pipeline;
- the U5b reset deletes the pipelines;
- the budget follows the edition;
- a write to a pipeline in Mirror mode is 409 ``mirror_mode_settings``, and in
  Native mode an admin can rename it and add or remove its stages;
- the deep link is built for each entity, and is null without a domain.

``test_crm_discovery.py`` holds the rules that need no database.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401  (ns is a fixture)
    SENTINEL,
    ZohoTenant,
    _assert_non_priv,
    _connect,
    _count,
    _q,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

PIPELINES = {"pipeline": [
    {"id": "p1", "display_value": "Standard", "default": True, "maps": [
        {"actual_value": "Qualify", "sequence_number": 1,
         "forecast_category": {"name": "Pipeline"}, "forecast_type": "Open"},
        {"actual_value": "Won It", "sequence_number": 2,
         "forecast_category": {"name": "Closed Won"}, "forecast_type": "Closed Won"},
    ]},
    {"id": "p2", "display_value": "Renewals", "default": False, "maps": [
        {"actual_value": "Qualify", "sequence_number": 1},
        {"actual_value": "Renewal Review", "sequence_number": 2},
        {"actual_value": "Gone", "sequence_number": 3, "forecast_type": "Closed Lost"},
    ]},
]}


class Q1Zoho(ZohoTenant):
    """The fake Zoho of ``test_crm_sync_engine_r8`` plus the discovery reads."""

    def __init__(self, tag: str, api_host: str, accounts_host: str) -> None:
        super().__init__(tag, api_host, accounts_host)
        self.org: dict[str, Any] | None = {
            "id": "9", "domain_name": f"org{tag}", "iso_currency": "INR",
            "time_zone": "Asia/Kolkata", "mc_status": False,
            "company_name": SENTINEL,
            "license_details": {
                "paid": True, "paid_type": "professional", "users_license_purchased": 12,
            },
        }
        self.org_error = False
        self.pipelines: dict[str, Any] = json.loads(json.dumps(PIPELINES))
        #: One org read is one discovery.
        self.org_reads = 0
        self.settings_paths: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/crm/v8/settings/"):
            self.settings_paths.append(path)
        if path == "/crm/v8/org":
            self.org_reads += 1
            if self.org_error:
                return httpx.Response(401, json={"code": "OAUTH_SCOPE_MISMATCH"})
            return httpx.Response(200, json={"org": [self.org] if self.org else []})
        if path == "/crm/v8/settings/modules":
            return httpx.Response(200, json={"modules": [
                {"api_name": "Deals", "module_name": "Potentials",
                 "plural_label": "Deals", "generated_type": "default",
                 "api_supported": True, "visible": True},
                {"api_name": "Installs", "module_name": "Installs",
                 "plural_label": "Installs", "generated_type": "custom",
                 "api_supported": True, "visible": True},
            ]})
        if path == "/crm/v8/settings/layouts":
            module = request.url.params.get("module", "")
            return httpx.Response(200, json={"layouts": [
                {"id": f"LD-{module}", "name": "Standard", "sections": []}]})
        if path == "/crm/v8/settings/pipeline":
            return httpx.Response(200, json=self.pipelines)
        return super().handle(request)


@pytest.fixture
async def q(ns, monkeypatch):  # noqa: F811 — a fixture
    """Two fake Zoho tenants wired under the engine, and the new tables cleaned."""
    from gateway.crm_sync import engine

    a = Q1Zoho("A", "www.zohoapis.in", "accounts.zoho.in")
    b = Q1Zoho("B", "www.zohoapis.eu", "accounts.zoho.eu")

    def transport(request: httpx.Request) -> httpx.Response:
        for z in (a, b):
            if request.url.host in (z.api_host, z.accounts_host):
                return z.handle(request)
        raise AssertionError(f"a call to an unknown host {request.url.host}")

    monkeypatch.setattr(engine, "TRANSPORT", httpx.MockTransport(transport))
    #: For each discovery: the credits it spent, and the budget right after it.
    found: list[SimpleNamespace] = []
    original = engine._discover

    async def spy(ctx: Any) -> None:
        before = ctx.credits
        await original(ctx)
        found.append(SimpleNamespace(
            cost=ctx.credits - before, budget=ctx.budget_total, org=ctx.org,
            read=ctx.credits != before,
        ))

    monkeypatch.setattr(engine, "_discover", spy)
    yield SimpleNamespace(p=ns, a=a, b=b, org_a=ns.org_a, org_b=ns.org_b, found=found)
    with ns.admin_engine.begin() as c:
        for org in (ns.org_a, ns.org_b):
            for table in ("crm_pipeline_stages", "crm_pipelines", "crm_source_profiles"):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str) -> Any:
    return _principal("admin@crm-sync.test", org, "feature:crm", "admin:access:manage")


def _member(org: str) -> Any:
    return _principal("member@crm-sync.test", org, "feature:crm")


async def _req(user: Any, method: str, path: str, body: dict | None = None) -> Any:
    """One request through the real ``/crm`` router, as *user*."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
        ) as client:
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


def _age_profile(p: Any, org: str, hours: int = 48) -> None:
    """Make the latest profile old, so the next run is due for a discovery."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_source_profiles SET fetched_at = :t "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org, "t": datetime.now(UTC) - timedelta(hours=hours)})


def _deal(z: Q1Zoho, ident: str, minute: int, **fields: Any) -> None:
    z.add("Deals", ident, minute, **{"Deal_Name": f"Deal {ident}", "Stage": "Qualify", **fields})


async def _sync(q: Any, org: str, z: Q1Zoho) -> Any:
    from gateway.crm_sync import engine

    await _connect(q.p, org, z)
    result = await engine.run_org(org)
    assert result.status == "ok", result
    return result


def _names(rows: list[Any]) -> list[Any]:
    return [r[0] for r in rows]


# ── 1. Discovery: the profile, its versions, its credits ─────────────────────


async def test_discovery_stores_a_profile_and_versions_it_only_on_change(
    q, app_engine,  # noqa: F811
) -> None:
    from gateway.crm_sync import engine

    _assert_non_priv(app_engine)
    _deal(q.a, "d1", 1, Pipeline="Standard", Description=SENTINEL)
    first = await _sync(q, q.org_a, q.a)
    rows = _q(q.p, "SELECT version, profile, profile_hash FROM crm_source_profiles "
                   "WHERE organization_id = CAST(:o AS uuid)", o=q.org_a)
    assert [r[0] for r in rows] == [1]
    profile = rows[0][1]
    assert profile["org"]["edition"] == "professional"
    assert profile["org"]["licences"] == 12
    assert profile["org"]["domain_name"] == "orgA"
    assert {m["api_name"] for m in profile["modules"]} == {"Deals", "Installs"}
    assert [p["name"] for p in profile["pipelines"]] == ["Standard", "Renewals"]
    # Metadata only: no record content and no company name.
    assert SENTINEL not in json.dumps(profile)
    # The discovery calls cost credits: org, modules, 4 layouts, 4 fields and
    # 1 pipeline read. The run counts them.
    assert q.a.org_reads == 1
    assert q.found[0].cost == 11
    assert first.credits_used >= 11

    # A run inside 24 hours reads nothing again.
    assert (await engine.run_org(q.org_a)).status == "ok"
    assert q.a.org_reads == 1 and q.found[1].cost == 0

    # 24 hours later, an unchanged account keeps version 1 and moves fetched_at.
    _age_profile(q.p, q.org_a)
    assert (await engine.run_org(q.org_a)).status == "ok"
    assert q.a.org_reads == 2 and q.found[2].cost == 11
    again = _q(q.p, "SELECT version, fetched_at FROM crm_source_profiles WHERE "
                    "organization_id = CAST(:o AS uuid)", o=q.org_a)
    assert [r[0] for r in again] == [1]
    assert datetime.now(UTC) - again[0][1] < timedelta(minutes=5)

    # A change in the account is a new version.
    q.a.org["license_details"]["paid_type"] = "enterprise"
    _age_profile(q.p, q.org_a)
    assert (await engine.run_org(q.org_a)).status == "ok"
    versions = _q(q.p, "SELECT version, profile->'org'->>'edition' FROM "
                       "crm_source_profiles WHERE organization_id = CAST(:o AS uuid) "
                       "ORDER BY version", o=q.org_a)
    assert [tuple(r) for r in versions] == [(1, "professional"), (2, "enterprise")]


async def test_domain_and_edition_reach_provider_meta_and_survive_a_refresh(q) -> None:
    from acb_common.db import tenant_session
    from gateway.connections import store
    from gateway.crm_sources.base import SourceCredential

    await _sync(q, q.org_a, q.a)
    meta = _q(q.p, "SELECT provider_meta FROM integration_connections WHERE "
                   "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    assert meta["domain_name"] == "orgA" and meta["edition"] == "professional"
    assert meta["tabs"]["Deals"] == "Potentials"
    assert meta["accounts_server"] == "https://accounts.zoho.in"

    async with tenant_session(q.org_a) as db:
        row = await store.get(db, q.org_a, "zoho", "crm")
        fresh = SourceCredential(
            access_token="NEW", refresh_token="R", scopes=(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            provider_meta={"accounts_server": "https://accounts.zoho.in",
                           "api_domain": "https://www.zohoapis.in", "location": "in"},
        )
        await store.update_tokens(db, q.org_a, row.id, fresh)
    after = _q(q.p, "SELECT provider_meta FROM integration_connections WHERE "
                    "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    assert after["domain_name"] == "orgA" and after["tabs"]["Deals"] == "Potentials"

    async with tenant_session(q.org_a) as db:
        await store.save_credential(
            db, q.org_a, "zoho", "crm", fresh, connected_by="admin@crm-sync.test")
    # A new consent may be another org: it drops what the sync learned.
    reconnect = _q(q.p, "SELECT provider_meta FROM integration_connections WHERE "
                        "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    assert "domain_name" not in reconnect and "tabs" not in reconnect


async def test_an_unreadable_org_keeps_the_floor_budget_and_still_syncs(q) -> None:
    from acb_common.db import tenant_session
    from gateway.crm_sync import engine, profiles

    q.a.org_error = True
    _deal(q.a, "d1", 1, Pipeline="Standard")
    await _sync(q, q.org_a, q.a)
    profile = _q(q.p, "SELECT profile FROM crm_source_profiles WHERE "
                      "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    assert profile["org"]["edition"] is None
    assert profile["unreadable"] == ["org"]
    async with tenant_session(q.org_a) as db:
        assert await profiles.budget_for(db, q.org_a) is None
    assert engine.daily_budget(profile) == engine.DAILY_CREDIT_BUDGET == 500
    assert _count(q.p, "crm_deals", q.org_a) == 1
    meta = _q(q.p, "SELECT provider_meta FROM integration_connections WHERE "
                   "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    assert "domain_name" not in meta


# ── 2. The edition budget ────────────────────────────────────────────────────


async def test_the_run_budget_follows_the_edition_from_the_profile(q) -> None:
    from acb_common.db import tenant_session
    from gateway.crm_sync import engine, profiles

    q.b.org["license_details"] = {"paid": False}  # Free
    await _sync(q, q.org_a, q.a)  # Professional with 12 licences: 50,000 + 6,000
    await _sync(q, q.org_b, q.b)
    seen = [f.budget for f in q.found]
    assert seen[0] == 5_600  # 10 % of 56,000, set right after the first discovery
    assert seen[1] == 500  # Free: 10 % of 5,000
    async with tenant_session(q.org_a) as db:
        assert await profiles.budget_for(db, q.org_a) == 5_600
    async with tenant_session(q.org_b) as db:
        assert await profiles.budget_for(db, q.org_b) == 500
    # The next run starts from the profile: the share minus the credits of the last 24 hours.
    assert (await engine.run_org(q.org_a)).status == "ok"
    seen = [f.budget for f in q.found]
    assert seen[2] is not None and 5_600 - 300 <= seen[2] < 5_600


# ── 3. Pipelines from Zoho ───────────────────────────────────────────────────


async def test_two_zoho_pipelines_land_with_their_stage_membership(q) -> None:
    await _sync(q, q.org_a, q.a)
    pipes = _q(q.p, "SELECT name, is_default, position, ext_source, ext_id, "
                    "layout_ext_id FROM crm_pipelines WHERE organization_id = "
                    "CAST(:o AS uuid) ORDER BY position", o=q.org_a)
    assert [tuple(r) for r in pipes] == [
        ("Standard", True, 0, "zoho", "p1", "LD-Deals"),
        ("Renewals", False, 1, "zoho", "p2", "LD-Deals"),
    ]
    members = _q(q.p, "SELECT p.name, s.name, ps.position FROM crm_pipeline_stages ps "
                      "JOIN crm_pipelines p ON p.id = ps.pipeline_id "
                      "JOIN crm_deal_statuses s ON s.id = ps.status_id "
                      "WHERE ps.organization_id = CAST(:o AS uuid) "
                      "ORDER BY p.position, ps.position", o=q.org_a)
    assert [tuple(r) for r in members] == [
        ("Standard", "Qualify", 0), ("Standard", "Won It", 1),
        ("Renewals", "Qualify", 0), ("Renewals", "Renewal Review", 1),
        ("Renewals", "Gone", 2),
    ]
    # One stage is one row: "Qualify" sits in both pipelines.
    assert _count(q.p, "crm_deal_statuses", q.org_a, "name = 'Qualify'") == 1
    # A stage that only the pipeline lists is appended, with its guessed type.
    kinds = {r[0]: (r[1], r[2]) for r in _q(
        q.p, "SELECT name, type, forecast_category FROM crm_deal_statuses WHERE "
             "organization_id = CAST(:o AS uuid)", o=q.org_a)}
    assert kinds["Qualify"] == ("open", "Pipeline")
    assert kinds["Won It"] == ("won", "Closed Won")
    assert kinds["Gone"] == ("lost", "Closed Lost")
    assert kinds["Renewal Review"][0] == "open"


async def test_each_deal_finds_its_pipeline_by_name_by_object_and_by_default(q) -> None:
    _deal(q.a, "by-name", 1, Pipeline="Renewals")
    _deal(q.a, "by-object", 2, Pipeline={"id": "p1", "name": "Standard"})
    _deal(q.a, "by-object-name-only", 3, Pipeline={"name": "Renewals"})
    _deal(q.a, "unknown-id", 4, Pipeline={"id": "zzz", "name": "Renewals"})
    _deal(q.a, "none", 5)
    _deal(q.a, "unknown-name", 6, Pipeline="Does not exist")
    await _sync(q, q.org_a, q.a)
    got = {r[0]: r[1] for r in _q(
        q.p, "SELECT d.ext_id, p.name FROM crm_deals d LEFT JOIN crm_pipelines p "
             "ON p.id = d.pipeline_id WHERE d.organization_id = CAST(:o AS uuid)",
        o=q.org_a)}
    assert got == {
        "by-name": "Renewals", "by-object": "Standard",
        "by-object-name-only": "Renewals", "unknown-id": "Renewals",
        "none": "Standard",  # a deal that names none joins the default
        "unknown-name": None,  # a deal that names an unknown pipeline stays unlinked
    }
    custom = _q(q.p, "SELECT custom FROM crm_deals WHERE ext_id = 'by-name' AND "
                     "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    assert custom["Pipeline"] == "Renewals"


async def test_a_pipeline_that_zoho_drops_is_deleted_and_its_deals_keep_a_null(q) -> None:
    from gateway.crm_sync import engine

    _deal(q.a, "r1", 1, Pipeline="Renewals")
    _deal(q.a, "s1", 2, Pipeline="Standard")
    await _sync(q, q.org_a, q.a)
    q.a.pipelines["pipeline"] = q.a.pipelines["pipeline"][:1]
    _age_profile(q.p, q.org_a)
    assert (await engine.run_org(q.org_a)).status == "ok"
    assert _names(_q(q.p, "SELECT name FROM crm_pipelines WHERE organization_id = "
                          "CAST(:o AS uuid)", o=q.org_a)) == ["Standard"]
    assert _count(q.p, "crm_pipeline_stages", q.org_a) == 2
    nulls = _q(q.p, "SELECT ext_id FROM crm_deals WHERE pipeline_id IS NULL AND "
                    "organization_id = CAST(:o AS uuid)", o=q.org_a)
    assert _names(nulls) == ["r1"]
    assert _count(q.p, "crm_deals", q.org_a) == 2


async def test_a_stage_removed_from_a_pipeline_in_zoho_leaves_the_membership(q) -> None:
    from gateway.crm_sync import engine

    await _sync(q, q.org_a, q.a)
    q.a.pipelines["pipeline"][1]["maps"].pop(1)  # drop "Renewal Review"
    _age_profile(q.p, q.org_a)
    assert (await engine.run_org(q.org_a)).status == "ok"
    names = _names(_q(q.p, "SELECT s.name FROM crm_pipeline_stages ps JOIN "
                           "crm_pipelines p ON p.id = ps.pipeline_id JOIN "
                           "crm_deal_statuses s ON s.id = ps.status_id WHERE "
                           "p.ext_id = 'p2' AND ps.organization_id = CAST(:o AS uuid) "
                           "ORDER BY ps.position", o=q.org_a))
    assert names == ["Qualify", "Gone"]
    # The stage itself stays: it is one global list.
    assert _count(q.p, "crm_deal_statuses", q.org_a, "name = 'Renewal Review'") == 1


# ── 4. Org isolation ─────────────────────────────────────────────────────────


async def test_org_b_never_sees_the_pipelines_profile_or_deals_of_org_a(q) -> None:
    q.b.pipelines["pipeline"] = [
        {"id": "b1", "display_value": "B-only", "default": True,
         "maps": [{"actual_value": "Qualify", "sequence_number": 1}]},
    ]
    q.b.org["license_details"]["paid_type"] = "standard"
    _deal(q.a, "a-deal", 1, Pipeline="Renewals")
    _deal(q.b, "b-deal", 1, Pipeline="B-only")
    await _sync(q, q.org_a, q.a)
    await _sync(q, q.org_b, q.b)

    assert _count(q.p, "crm_pipelines", q.org_a) == 2
    assert _count(q.p, "crm_pipelines", q.org_b) == 1
    for org, other in ((q.org_a, "B-only"), (q.org_b, "Renewals")):
        got = await _req(_admin(org), "GET", "/crm/pipelines")
        assert got.status_code == 200, got.text
        assert other not in [p["name"] for p in got.json()]
    a_pipe = {p["name"]: p["id"] for p in (await _req(_admin(q.org_a), "GET",
                                                      "/crm/pipelines")).json()}
    b_pipe = {p["name"]: p["id"] for p in (await _req(_admin(q.org_b), "GET",
                                                      "/crm/pipelines")).json()}
    assert set(a_pipe) == {"Standard", "Renewals"} and set(b_pipe) == {"B-only"}
    # B asks for A's pipeline: the list is empty and the board is a 404.
    spy = await _req(_admin(q.org_b), "GET", f"/crm/deals?pipeline_id={a_pipe['Renewals']}")
    assert spy.status_code == 200 and spy.json() == {"rows": [], "total": 0}
    board = await _req(_admin(q.org_b), "GET", f"/crm/pipeline?pipeline_id={a_pipe['Renewals']}")
    assert board.status_code == 404
    # B cannot rename or change A's pipeline, and A's profile is not B's.
    steal = await _req(_admin(q.org_b), "PATCH", f"/crm/pipelines/{a_pipe['Renewals']}",
                       {"name": "Mine"})
    assert steal.status_code in (404, 409)
    editions = {r[0]: r[1] for r in _q(
        q.p, "SELECT organization_id::text, profile->'org'->>'edition' FROM "
             "crm_source_profiles")}
    assert editions == {q.org_a: "professional", q.org_b: "standard"}
    a_profile = await _req(_admin(q.org_a), "GET", "/crm/source-profile")
    b_profile = await _req(_admin(q.org_b), "GET", "/crm/source-profile")
    assert a_profile.json()["edition"] == "professional"
    assert b_profile.json()["edition"] == "standard"


# ── 5. The routes ────────────────────────────────────────────────────────────


async def test_get_pipelines_has_the_contract_shape(q) -> None:
    await _sync(q, q.org_a, q.a)
    got = await _req(_admin(q.org_a), "GET", "/crm/pipelines")
    assert got.status_code == 200, got.text
    body = got.json()
    assert [p["name"] for p in body] == ["Standard", "Renewals"]
    first = body[0]
    assert set(first) == {"id", "name", "is_default", "position", "ext_url", "stages"}
    assert first["is_default"] is True and body[1]["is_default"] is False
    assert first["ext_url"] == "https://crm.zoho.in/crm/orgA/tab/Potentials"
    assert [s["name"] for s in first["stages"]] == ["Qualify", "Won It"]
    assert set(first["stages"][0]) == {
        "id", "name", "position", "type", "probability", "forecast_category"}
    assert first["stages"][1]["type"] == "won"
    assert first["stages"][1]["forecast_category"] == "Closed Won"
    assert first["stages"][0]["probability"] == 10


async def test_a_member_the_access_rule_admits_reads_pipelines_but_not_the_profile(q) -> None:
    await _sync(q, q.org_a, q.a)
    with q.p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'everyone' WHERE "
                       "organization_id = CAST(:o AS uuid)"), {"o": q.org_a})
    member = _member(q.org_a)
    assert (await _req(member, "GET", "/crm/pipelines")).status_code == 200
    assert (await _req(member, "GET", "/crm/source-profile")).status_code == 403
    assert (await _req(member, "PATCH", f"/crm/pipelines/{_pid(q)}",
                       {"name": "x"})).status_code == 403


def _pid(q: Any, name: str = "Standard") -> str:
    return str(_q(q.p, "SELECT id FROM crm_pipelines WHERE name = :n AND "
                       "organization_id = CAST(:o AS uuid)", n=name, o=q.org_a)[0][0])


async def test_the_pipeline_filter_on_the_list_and_the_board(q) -> None:
    for i in range(3):
        _deal(q.a, f"s{i}", i + 1, Pipeline="Standard")
    _deal(q.a, "r1", 10, Pipeline="Renewals")
    await _sync(q, q.org_a, q.a)
    admin = _admin(q.org_a)
    std, ren = _pid(q), _pid(q, "Renewals")

    everything = (await _req(admin, "GET", "/crm/deals")).json()
    assert everything["total"] == 4
    assert {r["pipeline_id"] for r in everything["rows"]} == {std, ren}
    only_std = (await _req(admin, "GET", f"/crm/deals?pipeline_id={std}")).json()
    assert only_std["total"] == 3
    assert {r["pipeline_id"] for r in only_std["rows"]} == {std}
    only_ren = (await _req(admin, "GET", f"/crm/deals?pipeline_id={ren}")).json()
    assert [r["ext_id"] for r in only_ren["rows"]] == ["r1"]
    # An id that is not a UUID is a 422, and a contact list has no pipeline.
    assert (await _req(admin, "GET", "/crm/deals?pipeline_id=nope")).status_code == 422
    assert (await _req(admin, "GET", f"/crm/contacts?pipeline_id={std}")).status_code == 422

    board = (await _req(admin, "GET", "/crm/pipeline")).json()
    assert sum(lane["count"] for lane in board["lanes"]) == 4
    std_board = (await _req(admin, "GET", f"/crm/pipeline?pipeline_id={std}")).json()
    assert [lane["status"]["name"] for lane in std_board["lanes"]] == ["Qualify", "Won It"]
    assert sum(lane["count"] for lane in std_board["lanes"]) == 3
    ren_board = (await _req(admin, "GET", f"/crm/pipeline?pipeline_id={ren}")).json()
    assert [lane["status"]["name"] for lane in ren_board["lanes"]] == [
        "Qualify", "Renewal Review", "Gone"]
    assert sum(lane["count"] for lane in ren_board["lanes"]) == 1
    card = ren_board["lanes"][0]["rows"][0]
    assert card["pipeline_id"] == ren


async def test_a_mirrored_deal_carries_ext_url_and_null_without_a_domain(q) -> None:
    _deal(q.a, "555", 1, Pipeline="Standard")
    z = q.a
    z.add("Accounts", "77", 2, Account_Name="Acme")
    z.add("Contacts", "88", 3, Last_Name="Rao")
    z.add("Leads", "99", 4, Last_Name="Lee", Company="Acme")
    await _sync(q, q.org_a, q.a)
    admin = _admin(q.org_a)
    expected = {
        "deals": "https://crm.zoho.in/crm/orgA/tab/Potentials/555",
        "organizations": "https://crm.zoho.in/crm/orgA/tab/Accounts/77",
        "contacts": "https://crm.zoho.in/crm/orgA/tab/Contacts/88",
        "leads": "https://crm.zoho.in/crm/orgA/tab/Leads/99",
    }
    for slug, url in expected.items():
        rows = (await _req(admin, "GET", f"/crm/{slug}")).json()["rows"]
        assert [r["ext_url"] for r in rows] == [url], slug
        one = (await _req(admin, "GET", f"/crm/{slug}/{rows[0]['id']}")).json()
        assert one["ext_url"] == url, slug
    board = (await _req(admin, "GET", "/crm/pipeline")).json()
    assert board["lanes"][0]["rows"][0]["ext_url"] == expected["deals"]
    # No domain, no link.
    with q.p.admin_engine.begin() as c:
        c.execute(text("UPDATE integration_connections SET provider_meta = "
                       "provider_meta - 'domain_name' WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": q.org_a})
    rows = (await _req(admin, "GET", "/crm/deals")).json()["rows"]
    assert [r["ext_url"] for r in rows] == [None]


async def test_a_native_record_has_no_ext_url(q) -> None:
    admin = _admin(q.org_a)
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    made = await _req(admin, "POST", "/crm/deals", {"name": "Mine"})
    assert made.status_code == 201, made.text
    assert made.json()["ext_url"] is None
    rows = (await _req(admin, "GET", "/crm/deals")).json()["rows"]
    assert [r["ext_url"] for r in rows] == [None]


async def test_the_source_profile_route_has_the_contract_shape(q) -> None:
    admin = _admin(q.org_a)
    await _connect(q.p, q.org_a, q.a)
    none_yet = await _req(admin, "GET", "/crm/source-profile")
    assert none_yet.status_code == 404
    assert none_yet.json()["detail"]["code"] == "no_source_profile"
    from gateway.crm_sync import engine

    assert (await engine.run_org(q.org_a)).status == "ok"
    got = await _req(admin, "GET", "/crm/source-profile")
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == {
        "version", "fetched_at", "edition", "currency", "modules", "pipelines"}
    assert body["version"] == 1 and body["edition"] == "professional"
    assert body["currency"] == "INR" and body["pipelines"] == 2
    assert body["fetched_at"]
    by_name = {m["api_name"]: m for m in body["modules"]}
    assert set(by_name["Installs"]) == {
        "api_name", "label", "custom", "api_supported", "mirrored"}
    assert by_name["Installs"]["custom"] is True and by_name["Installs"]["mirrored"] is False
    assert by_name["Deals"]["mirrored"] is True
    # No record, no token and no company name.
    assert SENTINEL not in got.text and "ACCESS" not in got.text


async def test_the_source_profile_route_is_404_with_crm_mirror_off(q, monkeypatch) -> None:
    from acb_common import get_settings

    await _sync(q, q.org_a, q.a)
    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    off = await _req(_admin(q.org_a), "GET", "/crm/source-profile")
    assert off.status_code == 404


# ── 6. The Native seed, the backfill and the reset ───────────────────────────


async def test_native_seed_gives_one_default_pipeline_with_all_stages(q) -> None:
    admin = _admin(q.org_a)
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    body = (await _req(admin, "GET", "/crm/pipelines")).json()
    assert [(p["name"], p["is_default"]) for p in body] == [("Sales pipeline", True)]
    assert [s["name"] for s in body[0]["stages"]] == [
        "Qualification", "Needs Analysis", "Proposal", "Negotiation",
        "Closed Won", "Closed Lost"]
    assert body[0]["ext_url"] is None
    # A new deal joins the default pipeline, and so does a converted lead.
    made = await _req(admin, "POST", "/crm/deals", {"name": "D"})
    assert made.status_code == 201, made.text
    assert made.json()["pipeline_id"] == body[0]["id"]
    lead = await _req(admin, "POST", "/crm/leads", {"first_name": "Ann", "last_name": "Lee"})
    conv = await _req(admin, "POST", f"/crm/leads/{lead.json()['id']}/convert", {})
    assert conv.status_code == 200, conv.text
    assert conv.json()["deal"]["pipeline_id"] == body[0]["id"]
    # A second PATCH to native is no change, so no second pipeline.
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    assert _count(q.p, "crm_pipelines", q.org_a) == 1


def _backfill_block() -> str:
    """The backfill DO block of migration 247, found by CONTENT (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS crm_pipelines" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    sql = found[0].read_text(encoding="utf-8")
    start = sql.index("DO $backfill$")
    return sql[start:sql.index("$backfill$;", start + 14) + len("$backfill$;")]


def _run_sql(engine: Any, sql: str) -> None:
    with engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


async def test_the_backfill_gives_each_native_org_one_default_pipeline(q) -> None:
    # Two native-style orgs with stages and deals but no pipeline (old data).
    for org in (q.org_a, q.org_b):
        with q.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO crm_settings (organization_id, mode) "
                "VALUES (CAST(:o AS uuid), :m) ON CONFLICT (organization_id) "
                "DO UPDATE SET mode = EXCLUDED.mode"),
                {"o": org, "m": "native" if org == q.org_a else "mirror"})
            for position, name in enumerate(("New", "Won")):
                c.execute(text(
                    "INSERT INTO crm_deal_statuses (organization_id, name, position, "
                    "type) VALUES (CAST(:o AS uuid), :n, :p, 'open')"),
                    {"o": org, "n": name, "p": position})
            c.execute(text(
                "INSERT INTO crm_deals (organization_id, name, status_id) "
                "SELECT CAST(:o AS uuid), 'old deal', id FROM crm_deal_statuses "
                "WHERE organization_id = CAST(:o AS uuid) AND name = 'New'"),
                {"o": org})
    block = _backfill_block()
    _run_sql(q.p.admin_engine, block)
    rows = _q(q.p, "SELECT is_default, name FROM crm_pipelines WHERE organization_id = "
                   "CAST(:o AS uuid)", o=q.org_a)
    assert [tuple(r) for r in rows] == [(True, "Sales pipeline")]
    assert _count(q.p, "crm_pipeline_stages", q.org_a) == 2
    assert _count(q.p, "crm_deals", q.org_a, "pipeline_id IS NOT NULL") == 1
    # A Mirror org gets none: its pipelines come from discovery.
    assert _count(q.p, "crm_pipelines", q.org_b) == 0
    assert _count(q.p, "crm_deals", q.org_b, "pipeline_id IS NULL") == 1
    # A replay changes nothing, and a stage that an admin removed stays removed.
    with q.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_pipeline_stages WHERE organization_id = "
                       "CAST(:o AS uuid) AND position = 1"), {"o": q.org_a})
    _run_sql(q.p.admin_engine, block)
    assert _count(q.p, "crm_pipelines", q.org_a) == 1
    assert _count(q.p, "crm_pipeline_stages", q.org_a) == 1
    with q.p.admin_engine.begin() as c:  # clean the rows this test made by hand
        for org in (q.org_a, q.org_b):
            c.execute(text("DELETE FROM crm_deals WHERE organization_id = "
                           "CAST(:o AS uuid)"), {"o": org})


async def test_the_backfill_runs_under_a_role_that_row_level_security_binds(
    q, app_engine,  # noqa: F811
) -> None:
    """The branch for a role without BYPASSRLS binds each org in turn."""
    _assert_non_priv(app_engine)
    with q.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deal_statuses (organization_id, name, position, type) "
            "VALUES (CAST(:o AS uuid), 'Only', 0, 'open')"), {"o": q.org_a})
    block = _backfill_block()
    with app_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(block)
    assert _count(q.p, "crm_pipelines", q.org_a) == 1
    assert _count(q.p, "crm_pipeline_stages", q.org_a) == 1


async def test_the_u5b_reset_deletes_the_pipelines_and_the_profile(q) -> None:
    admin = _admin(q.org_a)
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    assert _count(q.p, "crm_pipelines", q.org_a) == 1
    assert _count(q.p, "crm_pipeline_stages", q.org_a) == 6
    with q.p.admin_engine.begin() as c:  # an old profile of a Zoho that was connected
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app, status) VALUES (CAST(:o AS uuid), 'zoho', 'crm', "
            "'disconnected')"), {"o": q.org_a})
        c.execute(text(
            "INSERT INTO crm_source_profiles (organization_id, connection_id, "
            "version, profile, profile_hash) SELECT CAST(:o AS uuid), id, 1, "
            "'{}', 'h' FROM integration_connections WHERE organization_id = "
            "CAST(:o AS uuid)"), {"o": q.org_a})
    reset = await _req(admin, "PATCH", "/crm/settings", {"mode": None})
    assert reset.status_code == 200, reset.text
    for table in ("crm_pipelines", "crm_pipeline_stages", "crm_source_profiles",
                  "crm_deal_statuses"):
        assert _count(q.p, table, q.org_a) == 0, table
    # Native again: one pipeline, six stages.
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    assert _count(q.p, "crm_pipelines", q.org_a) == 1
    assert _count(q.p, "crm_pipeline_stages", q.org_a) == 6


# ── 7. Writes: Native yes, Mirror 409 ────────────────────────────────────────


async def test_mirror_mode_pipeline_writes_answer_409(q) -> None:
    await _sync(q, q.org_a, q.a)
    admin = _admin(q.org_a)
    std = _pid(q)
    stage = _q(q.p, "SELECT id FROM crm_deal_statuses WHERE name = 'Gone' AND "
                    "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    calls = [
        ("PATCH", f"/crm/pipelines/{std}", {"name": "Renamed"}),
        ("POST", f"/crm/pipelines/{std}/stages", {"status_id": str(stage)}),
        ("DELETE", f"/crm/pipelines/{std}/stages/{stage}", None),
    ]
    for method, path, body in calls:
        r = await _req(admin, method, path, body)
        assert r.status_code == 409, (method, r.text)
        assert r.json()["detail"]["code"] == "mirror_mode_settings"
    assert _names(_q(q.p, "SELECT name FROM crm_pipelines WHERE id = CAST(:i AS uuid)",
                     i=std)) == ["Standard"]
    assert _count(q.p, "crm_pipeline_stages", q.org_a) == 5


async def test_native_admin_renames_adds_and_removes_stages(q) -> None:
    admin = _admin(q.org_a)
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    only = (await _req(admin, "GET", "/crm/pipelines")).json()[0]
    pid = only["id"]

    renamed = await _req(admin, "PATCH", f"/crm/pipelines/{pid}", {"name": "  Big deals "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Big deals"
    blank = await _req(admin, "PATCH", f"/crm/pipelines/{pid}", {"name": "  "})
    assert blank.status_code == 422

    lost = next(s for s in only["stages"] if s["name"] == "Closed Lost")
    removed = await _req(admin, "DELETE", f"/crm/pipelines/{pid}/stages/{lost['id']}")
    assert removed.status_code == 200, removed.text
    assert "Closed Lost" not in [s["name"] for s in removed.json()["stages"]]
    # The stage row stays: it is one global list.
    assert _count(q.p, "crm_deal_statuses", q.org_a, "name = 'Closed Lost'") == 1

    added = await _req(admin, "POST", f"/crm/pipelines/{pid}/stages",
                       {"status_id": lost["id"]})
    assert added.status_code == 201, added.text
    assert [s["name"] for s in added.json()["stages"]][-1] == "Closed Lost"
    again = await _req(admin, "POST", f"/crm/pipelines/{pid}/stages",
                       {"status_id": lost["id"]})
    assert again.status_code == 201
    assert len(again.json()["stages"]) == 6

    # A stage that still holds a deal of the pipeline cannot leave it.
    deal = await _req(admin, "POST", "/crm/deals", {"name": "D"})
    assert deal.status_code == 201, deal.text
    stage_id = deal.json()["status_id"]
    stuck = await _req(admin, "DELETE", f"/crm/pipelines/{pid}/stages/{stage_id}")
    assert stuck.status_code == 409
    assert stuck.json()["detail"]["code"] == "stage_in_use"
    # A bad id is a 422, and an unknown pipeline is a 404.
    assert (await _req(admin, "PATCH", "/crm/pipelines/nope", {"name": "x"})).status_code == 422
    unknown = await _req(admin, "PATCH", "/crm/pipelines/" + "0" * 8 + "-0000-0000-0000-"
                         + "0" * 12, {"name": "x"})
    assert unknown.status_code == 404


async def test_a_new_native_stage_joins_the_default_pipeline_last(q) -> None:
    admin = _admin(q.org_a)
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    made = await _req(admin, "POST", "/crm/statuses/deal", {"name": "Extra", "type": "open"})
    assert made.status_code == 201, made.text
    pipeline = (await _req(admin, "GET", "/crm/pipelines")).json()[0]
    assert [s["name"] for s in pipeline["stages"]][-1] == "Extra"
    assert len(pipeline["stages"]) == 7
    # The stage is usable at once: a deal moves to it, and one is created in it.
    deal = await _req(admin, "POST", "/crm/deals", {"name": "D"})
    assert deal.status_code == 201, deal.text
    moved = await _req(admin, "PATCH", f"/crm/deals/{deal.json()['id']}",
                       {"status_id": made.json()["id"]})
    assert moved.status_code == 200, moved.text
    created = await _req(admin, "POST", "/crm/deals",
                         {"name": "E", "status_id": made.json()["id"]})
    assert created.status_code == 201, created.text
    # A lead status joins no pipeline.
    lead_status = await _req(admin, "POST", "/crm/statuses/lead", {"name": "L2", "type": "open"})
    assert lead_status.status_code == 201
    assert len((await _req(admin, "GET", "/crm/pipelines")).json()[0]["stages"]) == 7


async def test_a_no_op_patch_of_a_stage_the_pipeline_lacks_is_not_refused(q) -> None:
    admin = _admin(q.org_a)
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    deal = (await _req(admin, "POST", "/crm/deals", {"name": "D"})).json()
    pipe = (await _req(admin, "GET", "/crm/pipelines")).json()[0]
    with q.p.admin_engine.begin() as c:  # the stage leaves the pipeline behind the deal
        c.execute(text("DELETE FROM crm_pipeline_stages WHERE status_id = "
                       "CAST(:s AS uuid)"), {"s": deal["status_id"]})
    same = await _req(admin, "PATCH", f"/crm/deals/{deal['id']}",
                      {"status_id": deal["status_id"], "name": "D2"})
    assert same.status_code == 200, same.text
    assert pipe["stages"]


# ── 8. The raw API names ─────────────────────────────────────────────────────


def test_the_source_profile_migration_is_found_by_content() -> None:
    sql = re.compile(r"CREATE TABLE IF NOT EXISTS crm_source_profiles")
    found = [p for p in _MIGRATIONS.glob("*.sql")
             if p.name != "schema.generated.sql"
             and sql.search(p.read_text(encoding="utf-8"))]
    assert len(found) == 1


# ── Review fixes ─────────────────────────────────────────────────────────────


async def test_a_reconnect_to_another_zoho_org_drops_the_old_profile(q) -> None:
    from acb_common.db import tenant_session
    from gateway.connections import store
    from gateway.crm_sources.base import SourceCredential
    from gateway.crm_sync import engine

    await _sync(q, q.org_a, q.a)
    assert _count(q.p, "crm_source_profiles", q.org_a) == 1
    async with tenant_session(q.org_a) as db:
        row = await store.get(db, q.org_a, "zoho", "crm")
        await store.disconnect(db, q.org_a, row.id)
    meta = _q(q.p, "SELECT provider_meta FROM integration_connections WHERE "
                   "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    assert not {"domain_name", "edition", "tabs"} & set(meta)
    assert _count(q.p, "crm_source_profiles", q.org_a) == 0

    # Connect again, to the other Zoho org on another data centre.
    other = SourceCredential(
        access_token="ACCESS-B", refresh_token="REFRESH-B", scopes=(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        provider_meta={"api_domain": "https://www.zohoapis.eu",
                       "accounts_server": "https://accounts.zoho.eu", "location": "eu"},
    )
    async with tenant_session(q.org_a) as db:
        await store.save_credential(
            db, q.org_a, "zoho", "crm", other, connected_by="admin@crm-sync.test")
    before = len(q.found)
    assert (await engine.run_org(q.org_a)).status == "ok"
    assert q.found[before].cost == 11  # discovery was due at once
    meta = _q(q.p, "SELECT provider_meta FROM integration_connections WHERE "
                   "organization_id = CAST(:o AS uuid)", o=q.org_a)[0][0]
    assert meta["domain_name"] == "orgB"


async def test_deals_imported_before_discovery_are_linked_afterwards(q) -> None:
    from gateway.crm_sync import engine

    _deal(q.a, "r1", 1, Pipeline="Renewals")
    _deal(q.a, "o1", 2, Pipeline={"id": "p1", "name": "Standard"})
    _deal(q.a, "none", 3)
    _deal(q.a, "ghost", 4, Pipeline="Does not exist")
    await _sync(q, q.org_a, q.a)
    with q.p.admin_engine.begin() as c:  # the state of a deal read before 247
        c.execute(text("UPDATE crm_deals SET pipeline_id = NULL WHERE "
                       "organization_id = CAST(:o AS uuid)"), {"o": q.org_a})
    _age_profile(q.p, q.org_a)
    assert (await engine.run_org(q.org_a)).status == "ok"
    got = {r[0]: r[1] for r in _q(
        q.p, "SELECT d.ext_id, p.name FROM crm_deals d LEFT JOIN crm_pipelines p "
             "ON p.id = d.pipeline_id WHERE d.organization_id = CAST(:o AS uuid)",
        o=q.org_a)}
    assert got == {"r1": "Renewals", "o1": "Standard", "none": "Standard", "ghost": None}


async def test_a_deal_cannot_sit_in_a_stage_its_pipeline_lacks(q) -> None:
    admin = _admin(q.org_a)
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    pipe = (await _req(admin, "GET", "/crm/pipelines")).json()[0]
    lost = next(s for s in pipe["stages"] if s["name"] == "Closed Lost")
    gone = await _req(admin, "DELETE", f"/crm/pipelines/{pipe['id']}/stages/{lost['id']}")
    assert gone.status_code == 200, gone.text
    deal = await _req(admin, "POST", "/crm/deals", {"name": "D"})
    assert deal.status_code == 201, deal.text
    bad = await _req(admin, "PATCH", f"/crm/deals/{deal.json()['id']}",
                     {"status_id": lost["id"], "lost_reason_id": None})
    assert bad.status_code == 422, bad.text
    assert bad.json()["detail"]["code"] == "stage_not_in_pipeline"
    created = await _req(admin, "POST", "/crm/deals",
                         {"name": "E", "status_id": lost["id"]})
    assert created.status_code == 422
    assert created.json()["detail"]["code"] == "stage_not_in_pipeline"
    ok = await _req(admin, "PATCH", f"/crm/deals/{deal.json()['id']}",
                    {"status_id": pipe["stages"][1]["id"]})
    assert ok.status_code == 200, ok.text


async def test_native_writes_refuse_a_pipeline_that_a_provider_owns(q) -> None:
    admin = _admin(q.org_a)
    assert (await _req(admin, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    with q.p.admin_engine.begin() as c:  # left over from an org that was in Mirror mode
        pid = str(c.execute(text(
            "INSERT INTO crm_pipelines (organization_id, name, ext_source, ext_id) "
            "VALUES (CAST(:o AS uuid), 'Zoho one', 'zoho', 'zz') RETURNING id"),
            {"o": q.org_a}).scalar())
    status = (await _req(admin, "GET", "/crm/pipelines")).json()[0]["stages"][0]["id"]
    for method, path, body in (
        ("PATCH", f"/crm/pipelines/{pid}", {"name": "Mine"}),
        ("POST", f"/crm/pipelines/{pid}/stages", {"status_id": status}),
        ("DELETE", f"/crm/pipelines/{pid}/stages/{status}", None),
    ):
        r = await _req(admin, method, path, body)
        assert r.status_code == 409, (method, r.text)
        assert r.json()["detail"]["code"] == "mirrored_pipeline"


# ── 7. Deals board stages (owner report, 2026-10-12) ─────────────────────────


def _status(p: Any, org: str, name: str, kind: str, position: int) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deal_statuses (organization_id, name, position, type) "
            "VALUES (CAST(:o AS uuid), :n, :p, :t)"),
            {"o": org, "n": name, "p": position, "t": kind})


async def _store_profile(org: str, connection_id: str, stages: list[Any]) -> None:
    from acb_common.db import tenant_session
    from gateway.crm_sync import profiles

    profile = {"pipelines": [{
        "ext_id": "p1", "name": "Standard", "default": True, "layout_ext_id": None,
        "stages": [
            {"name": name, "sequence": i + 1, "forecast_category": None, "type": kind}
            for i, (name, kind) in enumerate(stages)
        ],
    }]}
    async with tenant_session(org) as db:
        await profiles.store(db, org, connection_id, profile)


def _types(p: Any, org: str) -> dict[str, str]:
    return {r[0]: r[1] for r in _q(
        p, "SELECT name, type FROM crm_deal_statuses WHERE "
           "organization_id = CAST(:o AS uuid)", o=org)}


async def test_a_stored_profile_retypes_an_existing_stage_with_no_zoho_call(q) -> None:
    """Payment Complete was mirrored as ``open`` before the pipeline said ``won``.

    The insert keeps an existing row, so only the type pass can fix it. It runs
    from the stored profile at the start of the run: this run stops before its
    first provider call, and the type is still fixed. A Native org with the
    same rows and the same profile is never touched.
    """
    from gateway.crm_sync import engine

    stages = [("Qualify", None), ("Payment Complete", "won"),
              ("Re-Evaluate", "lost"), ("Held By Hand", None)]
    conn_a = await _connect(q.p, q.org_a, q.a)
    conn_b = await _connect(q.p, q.org_b, q.b, mode="native")
    for org, conn in ((q.org_a, conn_a), (q.org_b, conn_b)):
        _status(q.p, org, "Qualify", "open", 0)
        _status(q.p, org, "Payment Complete", "open", 1)
        _status(q.p, org, "Re-Evaluate", "open", 2)
        # A type that the pipeline does not state stays as it is.
        _status(q.p, org, "Held By Hand", "won", 3)
        await _store_profile(org, conn, stages)
    q.a.requests.clear()

    result = await engine.run_org(q.org_a, should_stop=lambda: True)
    assert result.status == "stopped", result
    assert q.a.requests == []  # no Zoho call at all
    assert _types(q.p, q.org_a) == {
        "Qualify": "open", "Payment Complete": "won",
        "Re-Evaluate": "lost", "Held By Hand": "won",
    }

    with pytest.raises(engine.NotConnected):
        await engine.run_org(q.org_b)
    assert _types(q.p, q.org_b) == {
        "Qualify": "open", "Payment Complete": "open",
        "Re-Evaluate": "open", "Held By Hand": "won",
    }


async def test_discovery_retypes_an_existing_stage_too(q) -> None:
    """The same rule on the discovery path: ``Won It`` already exists as open."""
    _status(q.p, q.org_a, "Won It", "open", 0)
    await _sync(q, q.org_a, q.a)
    assert _types(q.p, q.org_a)["Won It"] == "won"


async def test_no_deal_disappears_from_a_pipeline_board(q) -> None:
    """A deal in a stage its pipeline does not list still has a column.

    The lane counts add up to the deals of the pipeline, with and without an
    owner filter. A stage that holds no deal of the pipeline is not added.
    """
    _deal(q.a, "s1", 1, Pipeline="Standard")
    _deal(q.a, "s2", 2, Pipeline="Standard")
    _deal(q.a, "d1", 3, Pipeline="Standard", Stage="Delivered & Installed")
    _deal(q.a, "d2", 4, Pipeline="Standard", Stage="Delivered & Installed")
    _deal(q.a, "n1", 5, Pipeline="Standard", Stage="New Lead")
    _deal(q.a, "r1", 6, Pipeline="Renewals", Stage="Meeting Scheduled")
    await _sync(q, q.org_a, q.a)
    with q.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_deals SET owner_email = 'x@crm-sync.test' WHERE "
            "organization_id = CAST(:o AS uuid) AND ext_id IN ('s1', 'd1')"),
            {"o": q.org_a})
    admin = _admin(q.org_a)
    std = _pid(q)

    board = (await _req(admin, "GET", f"/crm/pipeline?pipeline_id={std}")).json()
    lanes = [(lane["status"]["name"], lane["count"], lane["pipeline_position"])
             for lane in board["lanes"]]
    assert lanes == [
        ("Qualify", 2, 0), ("Won It", 0, 1),
        ("Delivered & Installed", 2, None), ("New Lead", 1, None),
    ]
    held = _count(q.p, "crm_deals", q.org_a,
                  f"pipeline_id = CAST('{std}' AS uuid)")
    assert held == 5
    assert sum(lane["count"] for lane in board["lanes"]) == held

    mine = (await _req(
        admin, "GET", f"/crm/pipeline?pipeline_id={std}&owner=x@crm-sync.test",
    )).json()
    assert [(lane["status"]["name"], lane["count"]) for lane in mine["lanes"]] == [
        ("Qualify", 1), ("Won It", 0), ("Delivered & Installed", 1),
    ]
    assert sum(lane["count"] for lane in mine["lanes"]) == 2

    # The whole-org board has no pipeline position and every stage.
    whole = (await _req(admin, "GET", "/crm/pipeline")).json()
    assert {lane["pipeline_position"] for lane in whole["lanes"]} == {None}
    assert sum(lane["count"] for lane in whole["lanes"]) == 6
