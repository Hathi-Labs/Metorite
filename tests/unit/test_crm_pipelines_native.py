"""R7 + R8 fence — an admin creates a deal pipeline (WS-CRM CRM-M3a).

Spec: ``project-docs/specs/crm_conversational.md`` §8 CRM-M3, §4 (D164) and
D166 item 2. Route: ``POST /crm/pipelines`` and ``GET /crm/pipelines/preview``
in ``apps/services/gateway/gateway/routes/crm/pipelines.py``.

**What this file holds.**

- ``CRM_PIPELINES_ORGS`` ships empty, and its check runs before the admin
  floor, so an org off the list gets 404 and never 403.
- ``change_log.via_of``: ``chat`` only for a ``chat:`` stamp beside the
  internal bearer. A browser-shaped request that forges the stamp logs ``ui``.
- The run of an org off the list holds no ``crm_create_pipeline``, in the own
  tools of the CRM assistant and in a pack build.
- Through the real ``/crm`` router, against a real Postgres, as the
  NON-privileged app role (R8): a create from a list and from a copy, two
  pipelines that share one "Proposal" row, the org's own won and lost stages,
  each refusal, Mirror mode, the Change history row and its Undo, the preview,
  ``GET /crm/mode``, a write that rolls back, and a second org that reads zero
  rows.

⚠️ Every test here runs in the R8 lane (``pytestmark``). Without a ladder
database the whole file skips, and ``ci_unit_guard.py --must-run`` fails CI
on that skip.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._crm_fakes import crm_switch_on
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_change_history import (  # noqa: F401 — ns is a fixture
    _admin,
    _member,
    _one,
    _rows,
    _settings,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

#: The internal bearer of these tests. The agent runtime and the BFF hold it.
TOKEN = "m3a-test-internal-token-0123456789abcdef"
STAMP = "chat:crm-assistant"
ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


# ── The flag ────────────────────────────────────────────────────────────────


def test_the_flag_ships_empty_and_reads_the_org(monkeypatch) -> None:
    from acb_common import get_settings
    from acb_common.settings import Settings
    from gateway.routes.crm import flags

    assert Settings.model_fields["crm_pipelines_orgs"].default == ""
    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    monkeypatch.setattr(settings, "crm_pipelines_orgs", "", raising=False)
    assert flags.pipelines_allowed(ORG) is False
    monkeypatch.setattr(settings, "crm_pipelines_orgs", f" {ORG.upper()} ", raising=False)
    assert flags.pipelines_allowed(ORG) is True
    assert flags.pipelines_allowed(OTHER) is False
    monkeypatch.setattr(settings, "crm_pipelines_orgs", "*", raising=False)
    assert flags.pipelines_allowed(OTHER) is True
    assert flags.pipelines_allowed(None) is False
    monkeypatch.setattr(settings, "crm_enabled", False, raising=False)
    assert flags.pipelines_allowed(ORG) is False, "the CRM switch comes first"


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/crm/pipelines"), ("GET", "/crm/pipelines/preview"),
])
def test_the_flag_runs_before_the_admin_floor(method: str, path: str) -> None:
    """Mutation: put the admin floor first -> a member of a dark org gets 403."""
    from gateway.routes.crm import core
    from gateway.routes.crm.flags import require_crm_pipelines

    route = next(r for r in core.router.routes
                 if getattr(r, "path", "") == path and method in (r.methods or set()))
    deps = [d.dependency for d in route.dependencies]
    flag = deps.index(require_crm_pipelines)
    floor = next(i for i, d in enumerate(deps)
                 if "require_permission" in getattr(d, "__qualname__", ""))
    assert flag < floor, deps


def test_the_body_takes_no_via_and_no_tenant() -> None:
    from gateway.routes.crm.pipelines import PipelineCreateIn
    from pydantic import ValidationError

    for extra in ("via", "organization_id", "actor_email"):
        with pytest.raises(ValidationError):
            PipelineCreateIn.model_validate({"name": "AMC", "stages": [], extra: "x"})


# ── via: the server decides it (acceptance d) ───────────────────────────────


def _request(headers: list[tuple[str, str]]) -> Any:
    from starlette.requests import Request

    return Request({
        "type": "http", "method": "POST", "path": "/crm/pipelines", "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    })


@pytest.mark.parametrize(("headers", "via"), [
    ([("Authorization", f"Bearer {TOKEN}"), ("X-Actor-Via", STAMP)], "chat"),
    # The browser shape: the stamp, with no internal bearer.
    ([("X-Actor-Via", STAMP)], "ui"),
    ([("Authorization", "Bearer not-the-token"), ("X-Actor-Via", STAMP)], "ui"),
    ([("Authorization", f"Bearer {TOKEN}")], "ui"),
    ([("Authorization", f"Bearer {TOKEN}"), ("X-Actor-Via", STAMP),
      ("X-Actor-Via", STAMP)], "ui"),
    ([("Authorization", f"Bearer {TOKEN}"), ("X-Actor-Via", "mcp:claude")], "ui"),
    ([("Authorization", f"Bearer {TOKEN}"), ("X-Actor-Via", "chat")], "ui"),
    ([("Authorization", f"Bearer {TOKEN}"), ("X-Actor-Via", "chat: a b")], "ui"),
])
async def test_via_is_chat_only_for_the_stamp_beside_the_internal_bearer(
    monkeypatch, headers: list[tuple[str, str]], via: str,
) -> None:
    """Mutation: drop the bearer check of ``via_of`` -> the browser row says chat -> red."""
    from gateway.routes.crm import change_log

    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", TOKEN)
    assert await change_log.via_of(_request(headers)) == via


def test_the_crm_proxy_builds_its_headers_and_forwards_no_browser_header() -> None:
    """The half of ``via_of`` that lives in the BFF. The proxy builds the
    upstream headers from ``gatewayHeaders()`` alone, so a browser's
    ``X-Actor-Via`` never reaches the gateway. ``actorVia.test.ts`` runs the
    handler with a forged header. This reads the source too."""
    source = (_ROOT / "workbench/control_plane/src/app/api/crm/[...path]/route.ts").read_text(
        encoding="utf-8")
    assert "...(await gatewayHeaders())" in source
    assert "req.headers" not in source


# ── The run drops the tool for an org off the flag (acceptance e) ───────────


def _agent_module() -> Any:
    from tests.unit._crm_agent_fakes import load_agent_module

    return load_agent_module("crm_m3a_native")


def _own_names(ti: Any, agents: list[Any]) -> set[str]:
    return {ti._tool_name(t) for a in agents for _attr, pool in ti._own_tool_pools(a)
            for t in pool}


@pytest.mark.parametrize(("listed", "kept"), [("", False), ("*", True)])
async def test_the_run_holds_the_tool_only_for_an_org_on_the_flag(
    monkeypatch, listed: str, kept: bool,
) -> None:
    """Mutation: drop the flag drop -> the tool stays for a dark org -> red."""
    pytest.importorskip("agent_framework")
    import orchestrator._tool_injection as ti
    from acb_common import get_settings

    async def native() -> str:
        return "native"

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", listed, raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: ORG)
    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", native)
    agents = _agent_module().build_agents()
    assert "crm_create_pipeline" in _own_names(ti, agents), "non-vacuity"
    await ti._drop_mirror_record_writes(agents)
    names = _own_names(ti, agents)
    assert ("crm_create_pipeline" in names) is kept
    assert "create_lead" in names, "only the pipeline tool leaves"


@pytest.mark.parametrize(("listed", "kept"), [("", False), ("*", True)])
async def test_a_pack_build_holds_the_tool_only_for_an_org_on_the_flag(
    monkeypatch, listed: str, kept: bool,
) -> None:
    import orchestrator._tool_injection as ti
    from acb_common import get_settings

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", listed, raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: ORG)
    module = _agent_module()
    tools, dropped = await ti._drop_pack_mirror_writes(
        [module.crm_create_pipeline, module.search_crm], [])
    assert ("crm_create_pipeline" in [ti._tool_name(t) for t in tools]) is kept
    assert dropped == ([] if kept else ["crm_create_pipeline"])


def test_a_run_with_no_org_holds_no_tool(monkeypatch) -> None:
    """The check fails closed: no org of the run reads as closed."""
    import orchestrator._tool_injection as ti
    from acb_common import get_settings

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", "*", raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: None)
    assert ti._crm_pipelines_open() is False


def test_this_suite_is_named_in_both_must_run_lists() -> None:
    """An R8 suite that CI does not list skips in silence when the database
    gate is disarmed. ``deploy.yml`` copies the list of ``pr-check.yml``."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+tests/unit/test_crm_pipelines_native\.py \\?$", workflow, re.M), name


# ── The database tests ──────────────────────────────────────────────────────


async def _call(
    p: Any, user: Any, method: str, path: str, body: dict | None = None, *,
    headers: dict[str, str] | None = None, params: dict[str, str] | None = None,
) -> Any:
    """One request through the real ``/crm`` router. The tenant comes from the user."""
    import httpx
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
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            return await client.request(
                method, path, json=body, headers=headers or {}, params=params)
    finally:
        release_tenant(scope)


@pytest.fixture
def pns(ns, monkeypatch):  # noqa: F811 — a fixture
    """The ladder with the CRM on, the pipelines flag on and the internal token set."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", "*", raising=False)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", TOKEN)
    _settings(ns, ns.org_a, "native")
    _settings(ns, ns.org_b, "native")
    return ns


def _seed(p: Any, org: str, *, closing: bool = True) -> dict[str, str]:
    """The default pipeline "Sales pipeline": Qualify, Proposal, Won, Lost."""
    stages = [("qualify", "Qualify", "open", 10, 10), ("proposal", "Proposal", "open", 20, 50)]
    if closing:
        stages += [("won", "Won", "won", 30, 100), ("lost", "Lost", "lost", 40, 0)]
    ids: dict[str, str] = {}
    for key, name, kind, pos, chance in stages:
        ids[key] = str(_one(
            p, "INSERT INTO crm_deal_statuses (organization_id, name, type, position, probability) "
               "VALUES (CAST(:o AS uuid), :n, :t, :pos, :pr) RETURNING id",
            o=org, n=name, t=kind, pos=pos, pr=chance))
    ids["pipeline"] = str(_one(
        p, "INSERT INTO crm_pipelines (organization_id, name, position, is_default) "
           "VALUES (CAST(:o AS uuid), 'Sales pipeline', 0, true) RETURNING id", o=org))
    with p.admin_engine.begin() as c:
        for place, (key, *_rest) in enumerate(stages):
            c.execute(text(
                "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id, position) "
                "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :pos)"),
                {"o": org, "p": ids["pipeline"], "s": ids[key], "pos": place})
    return ids


def _count(p: Any, table: str, org: str) -> int:
    return int(_one(p, f"SELECT count(*) FROM {table} WHERE organization_id = CAST(:o AS uuid)",
                    o=org) or 0)


def _code(r: Any) -> str | None:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _words(r: Any) -> str:
    detail = r.json().get("detail")
    if isinstance(detail, dict):
        return str(detail.get("message") or "")
    return json.dumps(detail)


AMC = {"name": "AMC contracts", "stages": [{"name": "Site visit"}, {"name": " proposal "}]}


async def test_an_admin_creates_a_pipeline_from_a_list_and_two_pipelines_share_proposal(
    pns, app_engine,  # noqa: F811
) -> None:
    """Done when 2 (D166 item 2) and item i: a stage name that the CRM holds
    reuses that row, a new name makes a stage in this pipeline only, and the
    org's won and lost stages join so that a deal can close."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    r = await _call(pns, admin, "POST", "/crm/pipelines", AMC)
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["name"] == "AMC contracts" and made["is_default"] is False
    assert [s["name"] for s in made["stages"]] == ["Site visit", "Proposal", "Won", "Lost"]
    assert [s["id"] for s in made["stages"][1:]] == [ids["proposal"], ids["won"], ids["lost"]]
    site = made["stages"][0]["id"]
    # One "Proposal" row, in both pipelines.
    assert _one(pns, "SELECT count(*) FROM crm_deal_statuses WHERE organization_id = "
                     "CAST(:o AS uuid) AND lower(name) = 'proposal'", o=pns.org_a) == 1
    assert _one(pns, "SELECT count(*) FROM crm_pipeline_stages WHERE status_id = "
                     "CAST(:s AS uuid)", s=ids["proposal"]) == 2
    # The new stage sits in the new pipeline only, not in the default one.
    assert _one(pns, "SELECT count(*) FROM crm_pipeline_stages WHERE status_id = "
                     "CAST(:s AS uuid)", s=site) == 1
    assert _one(pns, "SELECT type FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=site) == "open"
    # Two pipelines: the Deals board shows the switcher (`showPicker`).
    listed = await _call(pns, admin, "GET", "/crm/pipelines")
    assert [p["name"] for p in listed.json()] == ["Sales pipeline", "AMC contracts"]
    # Change history: one pipeline row, and the new stage's own row.
    rows = _rows(pns, pns.org_a)
    pipeline_rows = [x for x in rows if x.area == "pipeline"]
    assert len(pipeline_rows) == 1
    row = pipeline_rows[0]
    assert (row.action, row.ladder_level, row.via) == ("create", "company", "ui")
    assert row.entity_id == made["id"] and row.reversible is True
    assert row.after["name"] == "AMC contracts"
    assert [s["name"] for s in row.after["stages"]] == ["Site visit", "Proposal", "Won", "Lost"]
    assert row.summary == 'Created the pipeline "AMC contracts" with 4 stages'
    stage_rows = [x for x in rows if x.area == "deal_stage"]
    assert len(stage_rows) == 1 and stage_rows[0].entity_id == site
    assert len(rows) == 2


async def test_a_copy_takes_the_stages_in_order(pns) -> None:
    ids = _seed(pns, pns.org_a)
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines",
                    {"name": "Renewals", "copy_from": ids["pipeline"]})
    assert r.status_code == 201, r.text
    assert [s["id"] for s in r.json()["stages"]] == [
        ids["qualify"], ids["proposal"], ids["won"], ids["lost"]]
    assert _count(pns, "crm_deal_statuses", pns.org_a) == 4, "a copy makes no stage"
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    assert row.after["copy_of"] == "Sales pipeline"
    assert row.summary == 'Created the pipeline "Renewals", a copy of "Sales pipeline", with 4 stages'


async def test_a_new_won_stage_takes_its_type_and_its_fixed_win_chance(pns) -> None:
    _seed(pns, pns.org_a)
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", {
        "name": "Service", "stages": [{"name": "Booked"}, {"name": "Done", "type": "won"}]})
    assert r.status_code == 201, r.text
    assert [s["name"] for s in r.json()["stages"]] == ["Booked", "Done", "Lost"]
    assert _one(pns, "SELECT probability FROM crm_deal_statuses WHERE organization_id = "
                     "CAST(:o AS uuid) AND name = 'Done'", o=pns.org_a) == 100


async def test_each_refusal_writes_nothing_and_says_what_to_do(pns) -> None:
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    cases: list[tuple[dict[str, Any], int, str | None]] = [
        ({"name": "sales PIPELINE ", "copy_from": ids["pipeline"]}, 409, "name_taken"),
        ({"name": "X"}, 422, None),
        ({"name": "X", "copy_from": ids["pipeline"], "stages": [{"name": "A"}]}, 422, None),
        ({"name": "  ", "stages": [{"name": "A"}]}, 422, None),
        ({"name": "x" * 121, "stages": [{"name": "A"}]}, 422, None),
        ({"name": "X", "stages": [{"name": "Won"}, {"name": "Lost"}]}, 422, None),
        ({"name": "X", "stages": []}, 422, None),
        ({"name": "X", "stages": [{"name": "Proposal", "type": "won"}]}, 422, None),
        ({"name": "X", "stages": [{"name": "A"}, {"name": " a "}]}, 422, None),
        ({"name": "X", "stages": [{"name": "A", "type": "maybe"}]}, 422, None),
        ({"name": "X", "stages": [{"name": f"S{i}"} for i in range(51)]}, 422, None),
        ({"name": "X", "copy_from": str(uuid4())}, 404, "no_pipeline"),
        ({"name": "X", "copy_from": "not-an-id"}, 422, None),
        ({"name": "X", "stages": [{"name": "A"}], "via": "chat"}, 422, None),
        ({"name": "X", "kind": "lead", "stages": [{"name": "A"}]}, 422, None),
    ]
    for body, status, code in cases:
        r = await _call(pns, admin, "POST", "/crm/pipelines", body)
        assert r.status_code == status, (body, r.text)
        assert _code(r) == code, (body, r.text)
        assert _words(r).strip(), (body, "a refusal with no words")
    assert "Give the new pipeline another name" in _words(
        await _call(pns, admin, "POST", "/crm/pipelines", cases[0][0]))
    assert _count(pns, "crm_pipelines", pns.org_a) == 1
    assert _count(pns, "crm_deal_statuses", pns.org_a) == 4
    assert _count(pns, "crm_change_log", pns.org_a) == 0


async def test_two_stages_that_match_one_name_give_409(pns) -> None:
    _seed(pns, pns.org_a)
    _one(pns, "INSERT INTO crm_deal_statuses (organization_id, name, type, position) "
              "VALUES (CAST(:o AS uuid), 'PROPOSAL', 'open', 50) RETURNING id", o=pns.org_a)
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines",
                    {"name": "X", "stages": [{"name": "proposal"}]})
    assert r.status_code == 409 and _code(r) == "stage_ambiguous", r.text
    assert '"Proposal"' in _words(r) and '"PROPOSAL"' in _words(r)


async def test_an_org_with_no_lost_stage_is_told_to_add_one(pns) -> None:
    _seed(pns, pns.org_a, closing=False)
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines",
                    {"name": "X", "stages": [{"name": "Qualify"}, {"name": "Signed", "type": "won"}]})
    assert r.status_code == 422, r.text
    assert "no lost stage" in _words(r)
    assert _count(pns, "crm_deal_statuses", pns.org_a) == 2


async def test_a_member_gets_403_and_an_org_off_the_flag_gets_404(pns, monkeypatch) -> None:
    from acb_common import get_settings

    _seed(pns, pns.org_a)
    # Each feature holder passes the CRM access rule, so the floor and the
    # flag of the route answer.
    _settings(pns, pns.org_a, "native", access="everyone")
    member = await _call(pns, _member(pns.org_a), "POST", "/crm/pipelines", AMC)
    assert member.status_code == 403, member.text
    assert "admin:access:manage" in member.text
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", pns.org_b, raising=False)
    for user in (_admin(pns.org_a), _member(pns.org_a)):
        for method, path in (("POST", "/crm/pipelines"), ("GET", "/crm/pipelines/preview")):
            r = await _call(pns, user, method, path, AMC if method == "POST" else None,
                            params={"name": "X", "stages": "[]"} if method == "GET" else None)
            assert r.status_code == 404, (method, r.text)
    assert _count(pns, "crm_pipelines", pns.org_a) == 1


async def test_a_mirror_org_gets_409_and_nothing_is_written(pns) -> None:
    """Done when 6 (the create half). A Mirror CRM takes its pipelines from Zoho."""
    ids = _seed(pns, pns.org_a)
    _settings(pns, pns.org_a, "mirror")
    admin = _admin(pns.org_a)
    for body in (AMC, {"name": "Renewals", "copy_from": ids["pipeline"]}):
        r = await _call(pns, admin, "POST", "/crm/pipelines", body)
        assert r.status_code == 409 and _code(r) == "mirror_mode_settings", r.text
    plan = await _call(pns, admin, "GET", "/crm/pipelines/preview",
                       params={"name": "Renewals", "copy_from": ids["pipeline"]})
    assert plan.status_code == 409 and _code(plan) == "mirror_mode_settings", plan.text
    assert _count(pns, "crm_pipelines", pns.org_a) == 1
    assert _count(pns, "crm_change_log", pns.org_a) == 0


async def test_a_second_org_reads_zero_rows(pns, app_engine) -> None:  # noqa: F811
    """R5, R8: org B reads none of org A's pipelines, stages or rows."""
    _seed(pns, pns.org_a)
    assert (await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", AMC)).status_code == 201
    other = _admin(pns.org_b)
    assert (await _call(pns, other, "GET", "/crm/pipelines")).json() == []
    history = await _call(pns, other, "GET", "/crm/change-history")
    assert history.json() == {"rows": [], "total": 0}
    # Org B may use the same name: the check of a name is inside the org.
    _seed(pns, pns.org_b)
    mine = await _call(pns, other, "POST", "/crm/pipelines", AMC)
    assert mine.status_code == 201, mine.text
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_b})
        names = [r.name for r in c.execute(text("SELECT name FROM crm_pipelines ORDER BY position"))]
        assert names == ["Sales pipeline", "AMC contracts"]
        assert c.execute(text(
            "SELECT count(*) FROM crm_pipelines WHERE organization_id = CAST(:a AS uuid)"),
            {"a": pns.org_a}).scalar() == 0


async def test_undo_deletes_the_pipeline_and_refuses_while_a_deal_names_it(pns) -> None:
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    deal = str(_one(pns, "INSERT INTO crm_deals (organization_id, name, status_id, pipeline_id) "
                         "VALUES (CAST(:o AS uuid), 'Acme AMC', CAST(:s AS uuid), "
                         "CAST(:pl AS uuid)) RETURNING id",
                    o=pns.org_a, s=ids["proposal"], pl=made["id"]))
    held = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert held.status_code == 409 and _code(held) == "in_use", held.text
    assert "holds 1 deal now" in _words(held)
    _one(pns, "DELETE FROM crm_deals WHERE id = CAST(:d AS uuid) RETURNING id", d=deal)
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 200, undo.text
    out = undo.json()["change"]
    assert out["action"] == "delete" and out["undoes_id"] == str(row.id)
    assert [s["name"] for s in out["before"]["stages"]] == ["Site visit", "Proposal", "Won", "Lost"]
    assert _count(pns, "crm_pipelines", pns.org_a) == 1
    assert _count(pns, "crm_deal_statuses", pns.org_a) == 5, "the stages stay"
    again = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert again.status_code == 409 and _code(again) == "already_undone", again.text


async def test_undo_refuses_when_the_pipeline_changed_since(pns) -> None:
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = (await _call(pns, admin, "POST", "/crm/pipelines",
                        {"name": "Renewals", "copy_from": ids["pipeline"]})).json()
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    out = await _call(pns, admin, "DELETE", f"/crm/pipelines/{made['id']}/stages/{ids['qualify']}")
    assert out.status_code == 200, out.text
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 409 and _code(undo) == "changed_since", undo.text
    assert _count(pns, "crm_pipelines", pns.org_a) == 2


async def test_via_is_chat_for_the_agent_and_ui_for_a_forged_stamp(pns) -> None:
    """Acceptance d, through the real router. The browser-shaped request names
    the session's member and forges the stamp. It has no internal bearer,
    because the BFF builds its own headers. It logs ``ui``."""
    _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    forged = await _call(pns, admin, "POST", "/crm/pipelines",
                         {"name": "Forged", "stages": [{"name": "Forged stage"}]},
                         headers={"X-Actor-Via": STAMP})
    wrong = await _call(pns, admin, "POST", "/crm/pipelines",
                        {"name": "Wrong", "stages": [{"name": "Wrong stage"}]},
                        headers={"X-Actor-Via": STAMP, "Authorization": "Bearer guess"})
    agent = await _call(pns, admin, "POST", "/crm/pipelines",
                        {"name": "Agent", "stages": [{"name": "Agent stage"}]},
                        headers={"X-Actor-Via": STAMP, "Authorization": f"Bearer {TOKEN}"})
    assert {forged.status_code, wrong.status_code, agent.status_code} == {201}
    by = {(x.area, x.entity_id): x.via for x in _rows(pns, pns.org_a)}
    for made, via in ((forged.json(), "ui"), (wrong.json(), "ui"), (agent.json(), "chat")):
        assert by[("pipeline", made["id"])] == via, made["name"]
        assert by[("deal_stage", made["stages"][0]["id"])] == via, made["name"]


async def test_the_preview_writes_nothing_and_the_create_makes_what_it_showed(pns) -> None:
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    plan = await _call(pns, admin, "GET", "/crm/pipelines/preview", params={
        "name": "AMC contracts", "stages": json.dumps(AMC["stages"])})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["copy_of"] is None and body["kind"] == "deal"
    assert [(s["name"], s["source"]) for s in body["stages"]] == [
        ("Site visit", "new"), ("Proposal", "existing"), ("Won", "added"), ("Lost", "added")]
    assert body["stages"][0]["id"] is None and body["stages"][1]["id"] == ids["proposal"]
    assert _count(pns, "crm_pipelines", pns.org_a) == 1
    assert _count(pns, "crm_deal_statuses", pns.org_a) == 4
    assert _count(pns, "crm_change_log", pns.org_a) == 0
    bad = await _call(pns, admin, "GET", "/crm/pipelines/preview",
                      params={"name": "X", "stages": "not json"})
    assert bad.status_code == 422, bad.text
    made = await _call(pns, admin, "POST", "/crm/pipelines", AMC)
    assert [s["name"] for s in made.json()["stages"]] == [s["name"] for s in body["stages"]]
    member = await _call(pns, _member(pns.org_a), "GET", "/crm/pipelines/preview",
                         params={"name": "Y", "copy_from": ids["pipeline"]})
    assert member.status_code == 403, member.text


async def test_the_mode_read_names_the_create_only_on_the_flag(pns, monkeypatch) -> None:
    from acb_common import get_settings

    admin = _admin(pns.org_a)
    assert (await _call(pns, admin, "GET", "/crm/mode")).json()["pipeline_create"] is True
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", "", raising=False)
    assert "pipeline_create" not in (await _call(pns, admin, "GET", "/crm/mode")).json()
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", "*", raising=False)
    _settings(pns, pns.org_a, "mirror")
    assert "pipeline_create" not in (await _call(pns, admin, "GET", "/crm/mode")).json()


async def test_a_create_that_fails_late_leaves_no_row(pns, monkeypatch) -> None:
    """The pipeline, its new stage and their rows commit together, or none of
    them does. Mutation: commit before the last log row -> a pipeline stays."""
    from gateway.routes.crm import pipelines

    _seed(pns, pns.org_a)

    async def broken(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("the log write failed")

    monkeypatch.setattr(pipelines, "log_change", broken)
    with pytest.raises(RuntimeError, match="the log write failed"):
        await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines", AMC)
    assert _count(pns, "crm_pipelines", pns.org_a) == 1
    assert _count(pns, "crm_deal_statuses", pns.org_a) == 4
    assert _count(pns, "crm_change_log", pns.org_a) == 0
