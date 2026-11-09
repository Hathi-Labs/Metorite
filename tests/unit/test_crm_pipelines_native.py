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
- CRM-M3b (D166, migration 398, found by content): the win chance and the
  warning days on the membership. One pipeline's edit leaves the other, the
  clamp, Mirror, a new, moved and converted deal, the overdue reads, the
  take-out and its Undo, the old route for every pipeline, the sync, the
  fold, a Zoho-shaped org before and after the migration, and a second org.

⚠️ Every test here runs in the R8 lane (``pytestmark``). Without a ladder
database the whole file skips, and ``ci_unit_guard.py --must-run`` fails CI
on that skip.
"""

from __future__ import annotations

import ast
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
        # CRM-M4a: `lead` is a kind now. Another word is not.
        ({"name": "X", "kind": "contact", "stages": [{"name": "A"}]}, 422, None),
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
    # Repair round 1 (P1): the stage that the create made goes too. The
    # stages that the create reused stay.
    assert _count(pns, "crm_deal_statuses", pns.org_a) == 4
    assert _one(pns, "SELECT count(*) FROM crm_deal_statuses WHERE organization_id = "
                     "CAST(:o AS uuid) AND name = 'Site visit'", o=pns.org_a) == 0
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


# ── Repair round 1 ──────────────────────────────────────────────────────────


def _row_of(p: Any, org: str, area: str, action: str, entity: str) -> Any:
    with p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT * FROM crm_change_log WHERE organization_id = CAST(:o AS uuid) "
            "AND area = :a AND action = :act AND entity_id = :e ORDER BY at DESC LIMIT 1"),
            {"o": org, "a": area, "act": action, "e": entity}).first()


async def test_undo_of_a_create_removes_the_stages_it_made_and_logs_each(pns) -> None:
    """P1. The Undo removes "Site visit", which the create made, with one row
    that undoes the create row of the stage. "Proposal", which the create
    reused, stays. The single-pipeline board then shows no empty lane.
    Mutation: drop the stage loop of ``_undo_pipeline`` -> "Site visit" stays."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    site = made["stages"][0]["id"]
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 200, undo.text
    assert _one(pns, "SELECT count(*) FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=site) == 0
    assert _one(pns, "SELECT count(*) FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=ids["proposal"]) == 1
    gone = _row_of(pns, pns.org_a, "deal_stage", "delete", site)
    created = _row_of(pns, pns.org_a, "deal_stage", "create", site)
    assert gone is not None and gone.reversible is False
    assert str(gone.undoes_id) == str(created.id) and str(created.undone_by) == str(gone.id)
    assert 'Removed the deal stage "Site visit" with the Undo of the pipeline' in gone.summary
    board = await _call(pns, admin, "GET", "/crm/statuses/deal")
    assert [s["name"] for s in board.json()] == ["Qualify", "Proposal", "Won", "Lost"]


async def test_undo_of_a_create_refuses_while_a_new_stage_holds_a_record(pns) -> None:
    """P1. A deal with no pipeline sits in "Site visit", which the create
    made. The Undo refuses, names the stage and the count, and changes
    nothing."""
    _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    site = made["stages"][0]["id"]
    _one(pns, "INSERT INTO crm_deals (organization_id, name, status_id) "
              "VALUES (CAST(:o AS uuid), 'Loose deal', CAST(:s AS uuid)) RETURNING id",
         o=pns.org_a, s=site)
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 409 and _code(undo) == "in_use", undo.text
    assert '"Site visit"' in _words(undo) and "holds 1 deal now" in _words(undo)
    assert _count(pns, "crm_pipelines", pns.org_a) == 2
    assert _one(pns, "SELECT count(*) FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=site) == 1


async def test_undo_of_a_create_keeps_a_new_stage_that_another_pipeline_holds(pns) -> None:
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    made = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    site = made["stages"][0]["id"]
    added = await _call(pns, admin, "POST", f"/crm/pipelines/{ids['pipeline']}/stages",
                        {"status_id": site})
    assert added.status_code == 201, added.text
    (row,) = [x for x in _rows(pns, pns.org_a)
              if x.area == "pipeline" and x.action == "create"]
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 200, undo.text
    assert _one(pns, "SELECT count(*) FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=site) == 1, "the default pipeline holds it now"


@pytest.mark.parametrize(("column", "code", "words"), [
    ("hidden", "stage_hidden", "is hidden. Show it in CRM settings first, or use another name."),
    ("source_gone_at", "stage_gone", "Zoho no longer lists the stage"),
])
async def test_a_hidden_or_gone_stage_is_never_reused_in_silence(
    pns, column: str, code: str, words: str,
) -> None:
    """P2. The preview and the create refuse it by name, and nothing is made."""
    ids = _seed(pns, pns.org_a)
    value = "true" if column == "hidden" else "now()"
    _one(pns, f"UPDATE crm_deal_statuses SET {column} = {value} "
              "WHERE id = CAST(:s AS uuid) RETURNING id", s=ids["proposal"])
    admin = _admin(pns.org_a)
    plan = await _call(pns, admin, "GET", "/crm/pipelines/preview", params={
        "name": "X", "stages": json.dumps([{"name": "Site visit"}, {"name": "Proposal"}])})
    made = await _call(pns, admin, "POST", "/crm/pipelines",
                       {"name": "X", "stages": [{"name": "Site visit"}, {"name": "Proposal"}]})
    for r in (plan, made):
        assert r.status_code == 409 and _code(r) == code, r.text
        assert words in _words(r)
    assert _count(pns, "crm_pipelines", pns.org_a) == 1


async def test_only_a_visible_stage_counts_as_won_or_open(pns) -> None:
    """P2. The source pipeline's only won stage is hidden, and the org has no
    other. A copy refuses: a hidden stage cannot close a deal."""
    ids = _seed(pns, pns.org_a)
    _one(pns, "UPDATE crm_deal_statuses SET hidden = true WHERE id = CAST(:s AS uuid) "
              "RETURNING id", s=ids["won"])
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines",
                    {"name": "Renewals", "copy_from": ids["pipeline"]})
    assert r.status_code == 422, r.text
    assert "no won stage" in _words(r)


@pytest.mark.parametrize(("name", "kind", "chance"), [
    ("Closed Won", "won", 100), ("closed  LOST", "lost", 0),
])
async def test_a_new_closing_word_with_no_type_closes(pns, name: str, kind: str, chance: int) -> None:
    """P2 (verifier). "Closed Won" with no type is a won stage, as it reads,
    and no org stage of that type is added beside it."""
    _seed(pns, pns.org_a)
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines",
                    {"name": "Service", "stages": [{"name": "Booked"}, {"name": name}]})
    assert r.status_code == 201, r.text
    stages = r.json()["stages"]
    assert stages[1]["type"] == kind and stages[1]["probability"] == chance
    assert [s["type"] for s in stages].count(kind) == 1


async def test_a_closing_word_that_the_member_set_to_open_stays_open(pns) -> None:
    """The dialog sends "open" for a closing word that the member set to Open,
    and the route keeps that choice."""
    _seed(pns, pns.org_a)
    r = await _call(pns, _admin(pns.org_a), "POST", "/crm/pipelines",
                    {"name": "Service", "stages": [{"name": "Closed Won", "type": "open"}]})
    assert r.status_code == 201, r.text
    first = r.json()["stages"][0]
    assert (first["name"], first["type"]) == ("Closed Won", "open")


async def test_the_create_refuses_a_plan_that_changed_after_the_card(pns) -> None:
    """P3. The card sent its planned ids. A stage that a person made between
    the card and Apply changes the plan, and the create refuses."""
    _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    plan = (await _call(pns, admin, "GET", "/crm/pipelines/preview", params={
        "name": "AMC contracts", "stages": json.dumps(AMC["stages"])})).json()
    planned = [s["id"] for s in plan["stages"]]
    # Someone makes "Site visit" after the card.
    _one(pns, "INSERT INTO crm_deal_statuses (organization_id, name, type, position) "
              "VALUES (CAST(:o AS uuid), 'Site visit', 'open', 99) RETURNING id", o=pns.org_a)
    stale = await _call(pns, admin, "POST", "/crm/pipelines", {**AMC, "planned": planned})
    assert stale.status_code == 409 and _code(stale) == "plan_changed", stale.text
    assert "Ask for the card again" in _words(stale)
    assert _count(pns, "crm_pipelines", pns.org_a) == 1
    fresh = (await _call(pns, admin, "GET", "/crm/pipelines/preview", params={
        "name": "AMC contracts", "stages": json.dumps(AMC["stages"])})).json()
    ok = await _call(pns, admin, "POST", "/crm/pipelines",
                     {**AMC, "planned": [s["id"] for s in fresh["stages"]]})
    assert ok.status_code == 201, ok.text


# ── Repair round 2 ──────────────────────────────────────────────────────────


async def test_the_undo_of_a_create_locks_the_new_stages_before_the_pipeline(
    pns, monkeypatch,
) -> None:
    """P3 (round 2). A deal write takes the key lock of its stage before the
    key lock of its pipeline. The Undo takes them in the same order, so the
    two never wait on each other in a cycle. Mutation: lock the pipeline
    first in ``_undo_pipeline`` -> red."""
    from gateway.routes.crm import pipelines

    _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    assert (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).status_code == 201
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]
    real = pipelines.load_row
    order: list[str] = []

    async def spy(db: Any, table: str, row_id: str, **kw: Any) -> Any:
        if kw.get("lock"):
            order.append(table)
        return await real(db, table, row_id, **kw)

    monkeypatch.setattr(pipelines, "load_row", spy)
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 200, undo.text
    assert order[:2] == ["crm_deal_statuses", "crm_pipelines"], order


class _LockError(Exception):
    """A driver error, as asyncpg raises it, with its SQLSTATE."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"lock failure {sqlstate}")
        self.sqlstate = sqlstate


@pytest.mark.parametrize("sqlstate", ["40P01", "55P03"])
async def test_an_undo_that_meets_another_write_reads_plain_words(
    pns, monkeypatch, sqlstate: str,
) -> None:
    """P3 (round 2). A deadlock (40P01) or a lock that is not free (55P03)
    in an Undo is a 409 with plain words and a next step, never a 500. The
    transaction rolls back, so nothing is undone."""
    from gateway.routes.crm import change_log, pipelines
    from sqlalchemy.exc import DBAPIError

    _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    assert (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).status_code == 201
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]

    async def deadlock(*_a: Any, **_kw: Any) -> Any:
        raise DBAPIError("SELECT ... FOR UPDATE", None, _LockError(sqlstate))

    monkeypatch.setattr(pipelines, "load_row", deadlock)
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 409 and _code(undo) == "undo_conflict", undo.text
    assert _words(undo) == change_log.UNDO_CONFLICT
    assert "Try Undo again" in _words(undo)
    assert _count(pns, "crm_pipelines", pns.org_a) == 2, "nothing was undone"


async def test_another_database_error_of_an_undo_is_not_called_a_conflict(
    pns, monkeypatch,
) -> None:
    """Only the two lock states map to the 409. Any other error stays an
    error, so the mapping hides no defect."""
    from gateway.routes.crm import pipelines
    from sqlalchemy.exc import DBAPIError

    _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    assert (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).status_code == 201
    (row,) = [x for x in _rows(pns, pns.org_a) if x.area == "pipeline"]

    async def broken(*_a: Any, **_kw: Any) -> Any:
        raise DBAPIError("SELECT 1", None, _LockError("42P01"))

    monkeypatch.setattr(pipelines, "load_row", broken)
    with pytest.raises(DBAPIError):
        await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")


# ── CRM-M3b: the win chance and the warning days on the membership (D166) ───


def _m3b_migration() -> Path:
    """Migration 398, found by CONTENT, never by number (R1)."""
    found = [
        p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
        if re.search(r"ALTER TABLE crm_pipeline_stages\s+ADD COLUMN IF NOT EXISTS "
                     r"max_dwell_days SMALLINT NULL", p.read_text(encoding="utf-8"))
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def test_the_m3b_migration_is_expand_only_and_says_why() -> None:
    """R1, R6. A free number, a header that names the parts, two nullable
    columns, each CHECK NOT VALID and then validated, and the lock timeout.
    No drop, no rename, no default, and no backfill (repair 2): old code runs
    between the migration and the restart, and a copied value would hide its
    edit of the stage row."""
    path = _m3b_migration()
    numbers = [int(p.name.split("_", 1)[0]) for p in path.parent.glob("[0-9]*.sql")]
    assert numbers.count(int(path.name.split("_", 1)[0])) == 1
    sql = path.read_text(encoding="utf-8")
    header = sql.split("\nBEGIN;", 1)[0]
    for required in ("What:", "Why:", "Depends on:", "Expand only (R6)", "R5", "R1"):
        assert required in header, required
    bare = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    for forbidden in ("DROP TABLE", "DROP COLUMN", "RENAME", "TRUNCATE", "DELETE FROM",
                      "INSERT INTO", "DEFAULT", "UPDATE "):
        assert forbidden not in bare, forbidden
    assert "SET LOCAL lock_timeout" in bare
    assert re.search(r"ADD COLUMN IF NOT EXISTS probability SMALLINT NULL;", bare)
    for check in ("crm_pipeline_stages_probability_pct", "crm_pipeline_stages_max_dwell_days_min"):
        assert re.search(rf"ADD CONSTRAINT {check}\s+CHECK \([^;]*\) NOT VALID", bare), check
        assert f"VALIDATE CONSTRAINT {check}" in bare, check
    # Repair 2: no backfill, and the header says why.
    assert "Why no backfill" in header and "restarts the services" in header


def test_the_seed_copies_the_values_as_core_does() -> None:
    """``seed.py`` is a leaf, so it keeps its own copy of the copy rule."""
    from gateway.routes.crm import core, seed

    assert core.copied_values_sql() == seed.COPIED_VALUES
    assert "probability, max_dwell_days" in seed._PIPELINE_STAGES_SQL


def test_the_value_route_takes_no_via_no_tenant_and_whole_numbers_only() -> None:
    from gateway.routes.crm.pipelines import StageValuesIn
    from pydantic import ValidationError

    for body in ({"probability": 40, "via": "chat"}, {"organization_id": ORG},
                 {"probability": "40"}, {"probability": 40.5}, {"probability": True}):
        with pytest.raises(ValidationError):
            StageValuesIn.model_validate(body)


def test_the_value_route_takes_the_flag_before_the_admin_floor() -> None:
    """Mutation: put the admin floor first -> a member of a dark org gets 403."""
    from gateway.routes.crm import core
    from gateway.routes.crm.flags import require_crm_pipelines

    path = "/pipelines/{pipeline_id}/stages/{status_id}"
    route = next(r for r in core.router.routes
                 if getattr(r, "path", "").endswith(path) and "PATCH" in (r.methods or set()))
    deps = [d.dependency for d in route.dependencies]
    floor = next(i for i, d in enumerate(deps)
                 if "require_permission" in getattr(d, "__qualname__", ""))
    assert deps.index(require_crm_pipelines) < floor, deps


def _membership(p: Any, pipeline: str, status: str) -> tuple[Any, Any] | None:
    """The stored (probability, max_dwell_days) of one membership, or None."""
    with p.admin_engine.begin() as c:
        row = c.execute(text(
            "SELECT probability, max_dwell_days FROM crm_pipeline_stages "
            "WHERE pipeline_id = CAST(:p AS uuid) AND status_id = CAST(:s AS uuid)"),
            {"p": pipeline, "s": status}).first()
    return (row.probability, row.max_dwell_days) if row is not None else None


async def _lanes(p: Any, user: Any, pipeline: str | None = None) -> dict[str, dict]:
    """The board, as {stage name: the status of its lane}."""
    params = {"pipeline_id": pipeline} if pipeline else None
    r = await _call(p, user, "GET", "/crm/pipeline", params=params)
    assert r.status_code == 200, r.text
    return {lane["status"]["name"]: lane["status"] for lane in r.json()["lanes"]}


async def _stages_of(p: Any, user: Any) -> dict[str, dict[str, tuple[Any, Any]]]:
    """``GET /crm/pipelines``, as {pipeline: {stage: (probability, max_dwell_days)}}."""
    r = await _call(p, user, "GET", "/crm/pipelines")
    assert r.status_code == 200, r.text
    return {pl["name"]: {s["name"]: (s["probability"], s["max_dwell_days"])
                         for s in pl["stages"]} for pl in r.json()}


def _values_path(pipeline: str, status: str) -> str:
    return f"/crm/pipelines/{pipeline}/stages/{status}"


def _changes(p: Any, area: str, action: str) -> list[Any]:
    return [x for x in _rows(p, p.org_a) if x.area == area and x.action == action]


async def test_a_win_chance_edited_in_one_pipeline_leaves_the_other(pns) -> None:
    """Done when 3 (D166). "Proposal" sits in two pipelines. A win chance and
    warning days set in "AMC contracts" change that pipeline only: the
    pipeline list, its board, and the default board keep the old value. The
    stage row keeps it too. One Change history row, and its Undo puts the old
    values back."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    # The create copies the values of the stage row into each membership.
    assert _membership(pns, amc["id"], ids["proposal"]) == (50, None)
    r = await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                    {"probability": 35, "max_dwell_days": 9})
    assert r.status_code == 200, r.text
    shown = {s["name"]: s for s in r.json()["stages"]}
    assert (shown["Proposal"]["probability"], shown["Proposal"]["max_dwell_days"]) == (35, 9)
    assert _membership(pns, amc["id"], ids["proposal"]) == (35, 9)
    # The default pipeline holds the row that old code wrote: NULL reads the stage row.
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    listed = await _stages_of(pns, admin)
    assert listed["AMC contracts"]["Proposal"] == (35, 9)
    assert listed["Sales pipeline"]["Proposal"] == (50, None)
    amc_board = await _lanes(pns, admin, amc["id"])
    assert (amc_board["Proposal"]["probability"], amc_board["Proposal"]["max_dwell_days"]) == (35, 9)
    assert (await _lanes(pns, admin, ids["pipeline"]))["Proposal"]["probability"] == 50
    assert (await _lanes(pns, admin))["Proposal"]["probability"] == 50, "the default pipeline"
    statuses = {s["name"]: s for s in
                (await _call(pns, admin, "GET", "/crm/statuses/deal")).json()}
    assert statuses["Proposal"]["probability"] == 50, "the stage row stays"
    (row,) = _changes(pns, "pipeline_stage", "change")
    # "before" holds the stored values. "after" names no stage (repair 8), so
    # Change history shows no "Stage: Proposal -> Proposal" line.
    assert row.before == {"status_id": ids["proposal"], "stage": "Proposal",
                          "probability": 50, "max_dwell_days": None}
    assert row.after == {"status_id": ids["proposal"], "probability": 35, "max_dwell_days": 9}
    assert row.ladder_level == "team" and str(row.entity_id) == amc["id"]
    assert "win chance" in row.summary and "warning days" in row.summary
    # The same value again changes nothing and logs nothing.
    again = await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                        {"probability": 35})
    assert again.status_code == 200 and len(_changes(pns, "pipeline_stage", "change")) == 1
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 200, undo.text
    assert _membership(pns, amc["id"], ids["proposal"]) == (50, None)
    assert (await _lanes(pns, admin, amc["id"]))["Proposal"]["probability"] == 50
    twice = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert twice.status_code == 409 and _code(twice) == "already_undone", twice.text


async def test_the_undo_of_an_edit_puts_null_back_where_the_pipeline_followed_the_stage(
    pns,
) -> None:
    """Repair 3. A membership that old code wrote holds NULL and follows the
    stage row. The edit row keeps the STORED value, so its Undo puts NULL
    back, and the pipeline follows a later edit of the stage row again. A
    value that the pipeline shows already logs nothing."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    path = _values_path(ids["pipeline"], ids["proposal"])
    same = await _call(pns, admin, "PATCH", path, {"probability": 50})
    assert same.status_code == 200 and _changes(pns, "pipeline_stage", "change") == []
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    r = await _call(pns, admin, "PATCH", path, {"probability": 40, "max_dwell_days": 5})
    assert r.status_code == 200, r.text
    (row,) = _changes(pns, "pipeline_stage", "change")
    assert (row.before["probability"], row.before["max_dwell_days"]) == (None, None)
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 200, undo.text
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    # The pipeline follows the stage row again: a stage-row edit shows on the board.
    _one(pns, "UPDATE crm_deal_statuses SET probability = 55, max_dwell_days = 8 "
              "WHERE id = CAST(:s AS uuid) RETURNING id", s=ids["proposal"])
    lane = (await _lanes(pns, admin, ids["pipeline"]))["Proposal"]
    assert (lane["probability"], lane["max_dwell_days"]) == (55, 8)


async def test_the_clamp_and_each_bad_value_is_a_4xx_with_words(pns) -> None:
    """D-CRM-10 on the new route, on the state the write leaves, and each bad
    input is a 4xx that says what to do. Nothing is written."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    for key, chance, pinned in (("won", 80, 100), ("lost", 5, 0)):
        r = await _call(pns, admin, "PATCH", _values_path(amc["id"], ids[key]),
                        {"probability": chance})
        assert r.status_code == 422, r.text
        assert f"always forecasts {pinned}%" in r.text, r.text
        assert f"Set the win chance to {pinned}" in r.text, r.text
    proposal = _values_path(amc["id"], ids["proposal"])
    for body in ({"probability": 101}, {"probability": -1}, {"probability": None},
                 {"probability": "40"}, {"probability": 40.5}, {"probability": True},
                 {"max_dwell_days": 0}, {"max_dwell_days": 40000}, {},
                 {"probability": 40, "via": "chat"}, {"organization_id": OTHER}):
        r = await _call(pns, admin, "PATCH", proposal, body)
        assert r.status_code == 422, (body, r.text)
    # A stage that the pipeline does not list, an unknown stage, a bad id.
    missing = await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["qualify"]),
                          {"probability": 20})
    assert missing.status_code == 404 and _code(missing) == "stage_not_in_pipeline", missing.text
    assert "Add the stage to the pipeline first" in _words(missing)
    unknown = await _call(pns, admin, "PATCH", _values_path(amc["id"], str(uuid4())),
                          {"probability": 20})
    assert unknown.status_code == 404 and _code(unknown) == "no_stage", unknown.text
    bad = await _call(pns, admin, "PATCH", _values_path("not-a-uuid", ids["proposal"]),
                      {"probability": 20})
    assert bad.status_code == 422, bad.text
    # A lead pipeline gives 404 with words.
    lead = str(_one(pns, "INSERT INTO crm_pipelines (organization_id, name, position, "
                         "is_default, kind) VALUES (CAST(:o AS uuid), 'Web', 9, false, "
                         "'lead') RETURNING id", o=pns.org_a))
    as_lead = await _call(pns, admin, "PATCH", _values_path(lead, ids["proposal"]),
                          {"probability": 20})
    assert as_lead.status_code == 404 and _code(as_lead) == "no_pipeline", as_lead.text
    assert "Pick a deal pipeline in CRM settings" in _words(as_lead)
    # NULL warning days cannot turn the warning off while the stage row has a limit.
    _one(pns, "UPDATE crm_deal_statuses SET max_dwell_days = 14 WHERE id = CAST(:s AS uuid) "
              "RETURNING id", s=ids["proposal"])
    off = await _call(pns, admin, "PATCH", proposal, {"max_dwell_days": None})
    assert off.status_code == 422 and "cannot turn it off yet" in off.text, off.text
    assert _changes(pns, "pipeline_stage", "change") == []
    assert _membership(pns, amc["id"], ids["proposal"]) == (50, None)
    # A member who passes the CRM access rule meets the admin floor.
    _settings(pns, pns.org_a, "native", access="everyone")
    member = await _call(pns, _member(pns.org_a), "PATCH", proposal, {"probability": 30})
    assert member.status_code == 403 and "admin:access:manage" in member.text, member.text


async def test_the_value_route_is_404_for_an_org_off_the_flag(pns, monkeypatch) -> None:
    """Ship dark: an org off ``CRM_PIPELINES_ORGS`` gets 404, never 403."""
    from acb_common import get_settings

    ids = _seed(pns, pns.org_a)
    # Each feature holder passes the CRM access rule, so the flag answers.
    _settings(pns, pns.org_a, "native", access="everyone")
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", "", raising=False)
    for user in (_admin(pns.org_a), _member(pns.org_a)):
        r = await _call(pns, user, "PATCH", _values_path(ids["pipeline"], ids["proposal"]),
                        {"probability": 30})
        assert r.status_code == 404, r.text
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)


async def test_a_type_change_meets_the_clamp_in_each_pipeline(pns) -> None:
    """D-CRM-10 moves with the win chance (D166). A ``type`` change of the old
    route is refused while a pipeline gives the stage another win chance, and
    the words name that pipeline. With ``probability`` in the same request it
    sets the value in every pipeline."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    path = f"/crm/statuses/deal/{ids['proposal']}"
    assert (await _call(pns, admin, "PATCH", path, {"probability": 100})).status_code == 200
    assert (await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                        {"probability": 35})).status_code == 200
    refused = await _call(pns, admin, "PATCH", path, {"type": "won"})
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert '"AMC contracts"' in detail and "always forecasts 100%" in detail, detail
    assert _one(pns, "SELECT type FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=ids["proposal"]) == "open", "nothing changed"
    both = await _call(pns, admin, "PATCH", path, {"type": "won", "probability": 100})
    assert both.status_code == 200, both.text
    assert _membership(pns, amc["id"], ids["proposal"]) == (100, None)


async def test_the_old_route_sets_every_pipeline_and_its_undo_puts_each_back(pns) -> None:
    """Item 6. ``PATCH /crm/statuses/deal/{id}`` writes the stage row and each
    membership. Its row keeps the old value of each membership, so its Undo
    puts back the win chance of each pipeline, and the older edit of one
    pipeline can then go back too. No Undo stays stuck."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    edit = await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                       {"probability": 35})
    assert edit.status_code == 200, edit.text
    every = await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids['proposal']}",
                        {"probability": 60})
    assert every.status_code == 200, every.text
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (60, None)
    assert _membership(pns, amc["id"], ids["proposal"]) == (60, None)
    (stage_row,) = _changes(pns, "deal_stage", "change")
    assert stage_row.before["pipelines"] == {ids["pipeline"]: {"probability": None},
                                             amc["id"]: {"probability": 35}}
    assert stage_row.after["probability"] == 60
    (one,) = _changes(pns, "pipeline_stage", "change")
    stale = await _call(pns, admin, "POST", f"/crm/change-history/{one.id}/undo")
    assert stale.status_code == 409 and _code(stale) == "changed_since", stale.text
    back = await _call(pns, admin, "POST", f"/crm/change-history/{stage_row.id}/undo")
    assert back.status_code == 200, back.text
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    assert _membership(pns, amc["id"], ids["proposal"]) == (35, None)
    assert _one(pns, "SELECT probability FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=ids["proposal"]) == 50
    first = await _call(pns, admin, "POST", f"/crm/change-history/{one.id}/undo")
    assert first.status_code == 200, first.text
    assert _membership(pns, amc["id"], ids["proposal"]) == (50, None)


async def test_mirror_gives_409_and_its_warning_days_reach_every_pipeline(pns) -> None:
    """Done when 6 for the new route, and Mirror stays one value for each
    stage: its ``max_dwell_days`` edit (UX-Q5) reaches every membership, and
    the ``ui_probability`` overlay wins over each pipeline's value."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    assert (await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                        {"probability": 35})).status_code == 200
    _settings(pns, pns.org_a, "mirror")
    for body in ({"probability": 40}, {"max_dwell_days": 5}):
        r = await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]), body)
        assert r.status_code == 409 and _code(r) == "mirror_mode_settings", r.text
    days = await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids['proposal']}",
                       {"max_dwell_days": 12})
    assert days.status_code == 200, days.text
    assert _membership(pns, amc["id"], ids["proposal"]) == (35, 12)
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, 12)
    overlay = await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids['proposal']}",
                          {"ui_probability": 70})
    assert overlay.status_code == 200, overlay.text
    for pipeline in (amc["id"], ids["pipeline"], None):
        lane = (await _lanes(pns, admin, pipeline))["Proposal"]
        assert (lane["probability"], lane["max_dwell_days"]) == (70, 12), pipeline


async def test_a_new_a_moved_and_a_converted_deal_read_the_membership(pns) -> None:
    """A deal with no win chance takes the one of its stage in ITS pipeline:
    a new deal, a moved deal and the deal of a convert."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    site = next(s["id"] for s in amc["stages"] if s["name"] == "Site visit")
    for status, chance in ((ids["proposal"], 35), (site, 20)):
        assert (await _call(pns, admin, "PATCH", _values_path(amc["id"], status),
                            {"probability": chance})).status_code == 200
    made = await _call(pns, admin, "POST", "/crm/deals",
                       {"name": "In AMC", "pipeline_id": amc["id"], "status_id": ids["proposal"]})
    assert made.status_code == 201 and made.json()["probability"] == 35, made.text
    sales = await _call(pns, admin, "POST", "/crm/deals",
                        {"name": "In sales", "pipeline_id": ids["pipeline"],
                         "status_id": ids["proposal"]})
    assert sales.status_code == 201 and sales.json()["probability"] == 50, sales.text
    # A moved deal with no win chance of its own.
    moved = str(_one(pns, "INSERT INTO crm_deals (organization_id, name, status_id, "
                          "pipeline_id) VALUES (CAST(:o AS uuid), 'Moves', CAST(:s AS uuid), "
                          "CAST(:pl AS uuid)) RETURNING id",
                     o=pns.org_a, s=site, pl=amc["id"]))
    r = await _call(pns, admin, "PATCH", f"/crm/deals/{moved}", {"status_id": ids["proposal"]})
    assert r.status_code == 200 and r.json()["probability"] == 35, r.text
    # The deal of a convert starts in the first open stage of its pipeline.
    for name, kind, pos in (("New", "open", 10), ("Qualified", "won", 40)):
        _one(pns, "INSERT INTO crm_lead_statuses (organization_id, name, type, position) "
                  "VALUES (CAST(:o AS uuid), :n, :t, :pos) RETURNING id",
             o=pns.org_a, n=name, t=kind, pos=pos)
    lead = str(_one(pns, "INSERT INTO crm_leads (organization_id, lead_name, status_id) "
                         "SELECT CAST(:o AS uuid), 'Ravi Menon', id FROM crm_lead_statuses "
                         "WHERE organization_id = CAST(:o AS uuid) AND name = 'New' RETURNING id",
                    o=pns.org_a))
    converted = await _call(pns, admin, "POST", f"/crm/leads/{lead}/convert",
                            {"pipeline_id": amc["id"], "deal": {"name": "Acme AMC"}})
    assert converted.status_code == 200, converted.text
    deal = converted.json()["deal"]
    assert (deal["pipeline_id"], deal["status_id"], deal["probability"]) == (amc["id"], site, 20)


async def test_the_overdue_reads_use_the_warning_days_of_the_deal_pipeline(pns) -> None:
    """The stuck-deal read and the My Day read take the warning days of the
    deal's stage in its pipeline, and a deal with no pipeline reads the
    default deal pipeline."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant, tenant_session
    from gateway.routes.crm import aggregate

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    assert (await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                        {"max_dwell_days": 3})).status_code == 200
    for name, pipeline in (("AMC deal", amc["id"]), ("Sales deal", ids["pipeline"]),
                           ("No pipeline", None)):
        _one(pns, "INSERT INTO crm_deals (organization_id, name, status_id, pipeline_id, "
                  "owner_email, status_changed_at) VALUES (CAST(:o AS uuid), :n, "
                  "CAST(:s AS uuid), CAST(:pl AS uuid), :e, now() - interval '5 days') "
                  "RETURNING id",
             o=pns.org_a, n=name, s=ids["proposal"], pl=pipeline, e=admin.email)
    r = await _call(pns, admin, "GET", "/crm/aggregate/stage-dwell")
    assert r.status_code == 200, r.text
    assert {(d["name"], d["limit_days"]) for d in r.json()["overdue"]} == {("AMC deal", 3)}
    # The default deal pipeline gets a limit: the deal with no pipeline joins it.
    assert (await _call(pns, admin, "PATCH", _values_path(ids["pipeline"], ids["proposal"]),
                        {"max_dwell_days": 2})).status_code == 200
    r = await _call(pns, admin, "GET", "/crm/aggregate/stage-dwell")
    overdue = {(d["name"], d["limit_days"]) for d in r.json()["overdue"]}
    assert overdue == {("AMC deal", 3), ("Sales deal", 2), ("No pipeline", 2)}
    lanes = {s["name"]: s["max_dwell_days"] for s in r.json()["stages"]}
    assert lanes["Proposal"] == 2, "the stage list reads the default pipeline"
    scope = clear_tenant()
    try:
        bind_tenant(pns.org_a)
        async with (tenant_engine_scope(pns.app_url.render_as_string(hide_password=False)),
                    tenant_session(pns.org_a) as db):
            mine = await aggregate.my_overdue_deals(db, admin, limit=10)
    finally:
        release_tenant(scope)
    assert {(m["name"], m["limit_days"]) for m in mine} == overdue


async def test_a_take_out_and_its_undo_keep_the_values(pns) -> None:
    """The take-out row keeps both values in "before", so its Undo puts them
    back. An add copies the stage row, and an edit after the add must be
    undone first."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    assert (await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                        {"probability": 35, "max_dwell_days": 9})).status_code == 200
    out = await _call(pns, admin, "DELETE", _values_path(amc["id"], ids["proposal"]))
    assert out.status_code == 200, out.text
    (taken,) = _changes(pns, "pipeline_stage", "delete")
    assert (taken.before["probability"], taken.before["max_dwell_days"]) == (35, 9)
    (edit,) = _changes(pns, "pipeline_stage", "change")
    gone = await _call(pns, admin, "POST", f"/crm/change-history/{edit.id}/undo")
    assert gone.status_code == 409 and _code(gone) == "gone", gone.text
    assert "Undo the change that took it out first" in _words(gone)
    back = await _call(pns, admin, "POST", f"/crm/change-history/{taken.id}/undo")
    assert back.status_code == 200, back.text
    assert _membership(pns, amc["id"], ids["proposal"]) == (35, 9)
    # The edit can go back now that the stage is back.
    edit_back = await _call(pns, admin, "POST", f"/crm/change-history/{edit.id}/undo")
    assert edit_back.status_code == 200, edit_back.text
    # An add copies the stage row. An edit after it is undone first.
    put = await _call(pns, admin, "POST", f"/crm/pipelines/{amc['id']}/stages",
                      {"status_id": ids["qualify"]})
    assert put.status_code == 201, put.text
    assert _membership(pns, amc["id"], ids["qualify"]) == (10, None)
    (add,) = [x for x in _changes(pns, "pipeline_stage", "create") if x.undoes_id is None]
    assert (add.after["probability"], add.after["max_dwell_days"]) == (10, None)
    assert (await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["qualify"]),
                        {"probability": 15})).status_code == 200
    stale = await _call(pns, admin, "POST", f"/crm/change-history/{add.id}/undo")
    assert stale.status_code == 409 and _code(stale) == "changed_since", stale.text
    newest = [x for x in _changes(pns, "pipeline_stage", "change")
              if x.undone_by is None and x.undoes_id is None][-1]
    newest_back = await _call(pns, admin, "POST", f"/crm/change-history/{newest.id}/undo")
    assert newest_back.status_code == 200, newest_back.text
    add_back = await _call(pns, admin, "POST", f"/crm/change-history/{add.id}/undo")
    assert add_back.status_code == 200, add_back.text
    assert _membership(pns, amc["id"], ids["qualify"]) is None


async def test_the_sync_and_the_fold_write_the_memberships(pns) -> None:
    """Mirror mode keeps one value for each stage: a Zoho win chance reaches
    each membership, and the switch to Native folds the overlay into each
    membership, so the board reads the same before and after the fold."""
    from acb_common.db import tenant_session
    from gateway.crm_sync import engine
    from gateway.routes.crm.cutover import fold_cutover_overlay

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    assert (await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                        {"probability": 35})).status_code == 200
    _settings(pns, pns.org_a, "mirror")
    url = pns.app_url.render_as_string(hide_password=False)
    value = engine._PicklistValue(name="Proposal", ext_id="zp1", position=1, type="open",
                                  probability=65, forecast_category=None)
    async with tenant_engine_scope(url), tenant_session(pns.org_a) as db:
        await engine._source_status(db, "crm_deal_statuses", value, frozenset({"zp1"}))
    assert _membership(pns, amc["id"], ids["proposal"]) == (65, None)
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (65, None)
    _one(pns, "UPDATE crm_deal_statuses SET ui_probability = 70 WHERE id = CAST(:s AS uuid) "
              "RETURNING id", s=ids["proposal"])
    before = {pl: (await _lanes(pns, admin, pl))["Proposal"]["probability"]
              for pl in (amc["id"], ids["pipeline"])}
    assert before == {amc["id"]: 70, ids["pipeline"]: 70}
    async with tenant_engine_scope(url), tenant_session(pns.org_a) as db:
        await fold_cutover_overlay(db, pns.org_a)
    assert _membership(pns, amc["id"], ids["proposal"]) == (70, None)
    after = {pl: (await _lanes(pns, admin, pl))["Proposal"]["probability"]
             for pl in (amc["id"], ids["pipeline"])}
    assert after == before


async def test_a_zoho_shaped_org_reads_the_same_board_after_the_migration(pns) -> None:
    """Done when 7 (R8). A Mirror org with Zoho stages, win chances, an
    overlay and warning days, two pipelines, and memberships as old code
    wrote them (no values). The board and the pipeline list read the same
    before and after migration 398 runs, and a second run changes nothing.
    The file writes no membership (repair 2), so each one reads its stage
    row, even a stage row outside a CHECK."""
    from tests.unit._tenant_ladder import _exec_file

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    _settings(pns, pns.org_a, "mirror")
    odd = str(_one(pns, "INSERT INTO crm_deal_statuses (organization_id, name, type, position, "
                        "probability, max_dwell_days) VALUES (CAST(:o AS uuid), 'Odd', 'open', "
                        "25, 150, 0) RETURNING id", o=pns.org_a))
    _one(pns, "UPDATE crm_deal_statuses SET ui_probability = 70, ext_id = 'z-prop' "
              "WHERE id = CAST(:s AS uuid) RETURNING id", s=ids["proposal"])
    _one(pns, "UPDATE crm_deal_statuses SET max_dwell_days = 7 WHERE id = CAST(:s AS uuid) "
              "RETURNING id", s=ids["qualify"])
    partners = str(_one(pns, "INSERT INTO crm_pipelines (organization_id, name, position, "
                             "is_default, ext_source, ext_id) VALUES (CAST(:o AS uuid), "
                             "'Partners', 1, false, 'zoho', 'z-2') RETURNING id", o=pns.org_a))
    with pns.admin_engine.begin() as c:
        for place, sid in enumerate((ids["qualify"], ids["proposal"], odd, ids["won"])):
            c.execute(text(
                "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id, "
                "position) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :pos)"),
                {"o": pns.org_a, "p": partners, "s": sid, "pos": place})
        # Old code wrote no value: each membership holds NULL, as before the migration.
        c.execute(text(
            "UPDATE crm_pipeline_stages SET probability = NULL, max_dwell_days = NULL "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": pns.org_a})

    async def board() -> list[Any]:
        shape: list[Any] = []
        for pipeline in (None, ids["pipeline"], partners):
            lanes = await _lanes(pns, admin, pipeline)
            shape.append({n: (s["probability"], s["source_probability"], s["max_dwell_days"])
                          for n, s in lanes.items()})
        shape.append(await _stages_of(pns, admin))
        return shape

    before = await board()
    assert before[2]["Proposal"][0] == 70 and before[2]["Qualify"][2] == 7
    for _ in range(2):
        with pns.admin_engine.connect() as c:
            _exec_file(c, str(_m3b_migration()))
            c.commit()
        assert await board() == before
    for pipeline, status in ((partners, ids["proposal"]), (partners, ids["qualify"]),
                             (partners, odd), (ids["pipeline"], ids["won"])):
        assert _membership(pns, pipeline, status) == (None, None), "no backfill"


async def test_a_second_org_reads_and_writes_nothing_of_the_first(
    pns, app_engine,  # noqa: F811
) -> None:
    """R5. Org B cannot set a value in a pipeline of org A (404), and reads no
    membership of org A as the app role."""
    ids = _seed(pns, pns.org_a)
    _seed(pns, pns.org_b)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    other = await _call(pns, _admin(pns.org_b), "PATCH",
                        _values_path(amc["id"], ids["proposal"]), {"probability": 5})
    assert other.status_code == 404 and _code(other) == "no_pipeline", other.text
    assert _membership(pns, amc["id"], ids["proposal"]) == (50, None)
    assert "AMC contracts" not in await _stages_of(pns, _admin(pns.org_b))
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": pns.org_b})
        assert c.execute(text(
            "SELECT count(*) FROM crm_pipeline_stages "
            "WHERE organization_id = CAST(:a AS uuid)"), {"a": pns.org_a}).scalar() == 0


# ── CRM-M3b repair round 1 ──────────────────────────────────────────────────


@pytest.mark.parametrize("flag", ["", "*"])
async def test_the_undo_of_a_take_out_meets_the_clamp_of_the_stage_now(
    pns, monkeypatch, flag: str,
) -> None:
    """Repair 1, in the order of the review (P1). Take "Proposal" (open, 50)
    out of a pipeline, make it ``won`` at 100 through the old route, and undo
    the take-out. The pipeline shows ``won`` at 100, never the stored value.
    The take-out, the old route and the Undo are not behind
    ``CRM_PIPELINES_ORGS``, so the case runs with the flag off too. With the
    flag on, the stage first gets 35 in a second pipeline through the new
    route."""
    from acb_common import get_settings

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    if flag:
        pid = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()["id"]
        edit = await _call(pns, admin, "PATCH", _values_path(pid, ids["proposal"]),
                           {"probability": 35})
        assert edit.status_code == 200, edit.text
        stored = (35, None)
    else:
        # The membership holds a copy of the stage row, as the seed and a
        # stage add write it.
        pid = ids["pipeline"]
        with pns.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE crm_pipeline_stages SET probability = 50 "
                "WHERE pipeline_id = CAST(:p AS uuid) AND status_id = CAST(:s AS uuid)"),
                {"p": pid, "s": ids["proposal"]})
        stored = (50, None)
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", flag, raising=False)
    out = await _call(pns, admin, "DELETE", _values_path(pid, ids["proposal"]))
    assert out.status_code == 200, out.text
    (taken,) = _changes(pns, "pipeline_stage", "delete")
    assert (taken.before["probability"], taken.before["max_dwell_days"]) == stored
    won = await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids['proposal']}",
                      {"type": "won", "probability": 100})
    assert won.status_code == 200, won.text
    back = await _call(pns, admin, "POST", f"/crm/change-history/{taken.id}/undo")
    assert back.status_code == 200, back.text
    assert _membership(pns, pid, ids["proposal"]) == (None, None), "the stored value broke the clamp"
    lane = (await _lanes(pns, admin, pid))["Proposal"]
    assert (lane["type"], lane["probability"]) == ("won", 100)
    listed = {(pl["id"], s["name"]): (s["type"], s["probability"])
              for pl in (await _call(pns, admin, "GET", "/crm/pipelines")).json()
              for s in pl["stages"]}
    assert listed[(pid, "Proposal")] == ("won", 100)
    (put_back,) = [x for x in _changes(pns, "pipeline_stage", "create") if x.undoes_id]
    assert put_back.after["probability"] is None, "the row logs what the Undo wrote"


async def test_a_copy_and_a_status_undo_never_put_back_a_value_that_breaks_the_clamp(
    pns,
) -> None:
    """Repair 1 for each other write that copies a value into a membership:
    a copy of a pipeline, a stage add, and the Undo of an old-route edit after
    the stage became ``won``. Each writes NULL in place of a value that breaks
    the clamp of the stage now, and NULL reads the stage row."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    # States that no route makes: "Won" at 40 in the default pipeline, and a
    # "won" stage row at 60 that no pipeline lists.
    with pns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_pipeline_stages SET probability = 40 "
            "WHERE pipeline_id = CAST(:p AS uuid) AND status_id = CAST(:s AS uuid)"),
            {"p": ids["pipeline"], "s": ids["won"]})
    signed = str(_one(pns, "INSERT INTO crm_deal_statuses (organization_id, name, type, "
                           "position, probability) VALUES (CAST(:o AS uuid), 'Signed', 'won', "
                           "35, 60) RETURNING id", o=pns.org_a))
    copy = await _call(pns, admin, "POST", "/crm/pipelines",
                       {"name": "Renewals", "copy_from": ids["pipeline"]})
    assert copy.status_code == 201, copy.text
    renewals = copy.json()["id"]
    assert _membership(pns, renewals, ids["won"]) == (None, None)
    assert _membership(pns, renewals, ids["proposal"]) == (50, None), "a value that fits stays"
    put = await _call(pns, admin, "POST", f"/crm/pipelines/{renewals}/stages",
                      {"status_id": signed})
    assert put.status_code == 201, put.text
    assert _membership(pns, renewals, signed) == (None, None)
    # The old route: the stage row is 100 already, so its second edit writes
    # only the membership, and its Undo puts back 35 after the type change.
    path = f"/crm/statuses/deal/{ids['proposal']}"
    assert (await _call(pns, admin, "PATCH", path, {"probability": 100})).status_code == 200
    assert (await _call(pns, admin, "PATCH", _values_path(ids["pipeline"], ids["proposal"]),
                        {"probability": 35})).status_code == 200
    assert (await _call(pns, admin, "PATCH", path, {"probability": 100})).status_code == 200
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (100, None)
    assert (await _call(pns, admin, "PATCH", path, {"type": "won"})).status_code == 200
    second = _changes(pns, "deal_stage", "change")[-2]
    assert second.before == {"pipelines": {ids["pipeline"]: {"probability": 35}}}, second.before
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{second.id}/undo")
    assert undo.status_code == 200, undo.text
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    lane = (await _lanes(pns, admin, ids["pipeline"]))["Proposal"]
    assert (lane["type"], lane["probability"]) == ("won", 100)


async def test_the_weighted_aggregate_reads_the_lanes_of_the_board(pns) -> None:
    """Repair 4. A deal with no win chance of its own. The weighted aggregate
    of one pipeline reads the win chance of the stage in that pipeline, as the
    board of that pipeline does. With no pipeline, both read the default deal
    pipeline. One deal never gives two numbers."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    amc = (await _call(pns, admin, "POST", "/crm/pipelines", AMC)).json()
    assert (await _call(pns, admin, "PATCH", _values_path(amc["id"], ids["proposal"]),
                        {"probability": 20})).status_code == 200
    _one(pns, "INSERT INTO crm_deals (organization_id, name, status_id, pipeline_id, amount, "
              "currency) VALUES (CAST(:o AS uuid), 'Weighted', CAST(:s AS uuid), "
              "CAST(:pl AS uuid), 1000, 'INR') RETURNING id",
         o=pns.org_a, s=ids["proposal"], pl=amc["id"])
    for pipeline, expected in ((amc["id"], 200.0), (None, 500.0)):
        params = {"pipeline_id": pipeline} if pipeline else None
        board = await _call(pns, admin, "GET", "/crm/pipeline", params=params)
        assert board.status_code == 200, board.text
        (lane,) = [x for x in board.json()["lanes"] if x["status"]["name"] == "Proposal"]
        query = {"entity": "deals", "measure": "weighted", **(params or {})}
        agg = await _call(pns, admin, "GET", "/crm/aggregate", params=query)
        assert agg.status_code == 200, agg.text
        assert (lane["weighted"], agg.json()["total"]) == (expected, expected), pipeline


#: CRM-M3b round 2 (P3): the reader fence reads each module of the CRM
#: routes, not five of them.
_CRM_ROUTES = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"

#: The two values of a deal stage that sit on its membership (migration 398).
_VALUE_COLUMNS = frozenset({"probability", "max_dwell_days"})

#: The form that reads the membership first. The fence removes it before it looks.
_MEMBERSHIP_FIRST = re.compile(r"COALESCE\(ps\.(probability|max_dwell_days), s\.\1\)")

#: Each SQL form that reads a stage-row value alone, over the text of one
#: string. The fence joins adjacent literals, the parts of an f-string and a
#: ``+`` of literals first, so a SELECT split across two literals is one text.
_SQL_FORMS: dict[str, re.Pattern[str]] = {
    # A bare column, not ``x.column`` and not an ``AS column`` name.
    "select": re.compile(
        r"\bSELECT\b(?:(?!\bFROM\b).)*(?<![\w.])(?<!AS )(?:probability|max_dwell_days)\b"
        r"(?:(?!\bSELECT\b).)*\bFROM crm_deal_statuses\b", re.S),
    "star": re.compile(r"\bSELECT \* FROM crm_deal_statuses\b"),
    "alias": re.compile(r"\bs\.(?:probability|max_dwell_days)\b"),
}

#: Repair 3 (P3): each name that one statement gives to ``crm_deal_statuses``,
#: with or without ``AS``. A word of SQL after the table is no name.
_STAGE_ALIAS = re.compile(
    r"\bcrm_deal_statuses(?:\s+AS)?\s+(?!(?:WHERE|ON|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS"
    r"|NATURAL|ORDER|GROUP|LIMIT|OFFSET|FOR|USING|SET|UNION|EXCEPT|INTERSECT|HAVING"
    r"|WINDOW|RETURNING|AND|OR|AS|VALUES|SELECT|FROM|TABLESAMPLE)\b)([A-Za-z_]\w*)", re.I)


def _named_stage_value(sql: str) -> bool:
    """True when *sql* reads a value through a name of ``crm_deal_statuses``:
    each alias that the statement gives it (:data:`_STAGE_ALIAS`), or the
    table name itself (repair 3). ``s.`` stays the ``alias`` form, because a
    fragment can name it with no table."""
    names = {"crm_deal_statuses", *_STAGE_ALIAS.findall(sql)}
    pattern = "|".join(re.escape(n) for n in sorted(names))
    return re.search(
        rf"(?<![\w.])(?:{pattern})\.(?:probability|max_dwell_days)\b", sql, re.I,
    ) is not None


def _sql_forms(sql: str) -> list[str]:
    """Each form of :data:`_SQL_FORMS` that *sql* matches, and ``named``."""
    forms = [form for form, rule in _SQL_FORMS.items() if rule.search(sql)]
    return [*forms, "named"] if _named_stage_value(sql) else forms


#: The helpers that load one stage row or the stage-row list.
_LOADERS = frozenset({"load_row", "require_row", "read_status", "read_statuses"})

#: The functions that may read the stage-row value alone, each with its
#: reason. Each one must still read it, so the list cannot rot.
_STAGE_ROW_OWNERS: dict[tuple[str, str], str] = {
    ("core.py", "_effective_columns"): (
        "it builds the stage-row list of GET /crm/statuses, and the list that reads "
        "the membership first"),
    ("core.py", "read_deal_stages"): "an org with no deal pipeline has no membership",
    ("cutover.py", "fold_cutover_overlay"): "a write: the switch folds the overlay",
    ("seed.py", "<module>"): "a write: a new membership copies the stage row",
    ("stage_metadata.py", "import_zoho_stages"): "a write: the repair plans the stage row",
}

#: One mutation of each form that the fence catches, as (form, source). Each
#: must turn it red. The three ``named`` ones are repair 3: a JOIN alias, an
#: ``AS`` alias and the table name.
_MUTATIONS: tuple[tuple[str, str], ...] = (
    ("select", (
        "async def lanes(db):\n"
        "    return await db.execute(text('SELECT id, probability '\n"
        "                                 'FROM crm_deal_statuses'))\n")),
    ("star", "SQL = 'SELECT * FROM crm_deal_statuses ORDER BY position, name'\n"),
    ("alias", "LIMIT = 'CAST(s.max_dwell_days AS int) AS limit_days'\n"),
    ("named", (
        "SQL = ('SELECT ps.status_id, ds.probability FROM crm_pipeline_stages ps '\n"
        "       'JOIN crm_deal_statuses ds ON ds.id = ps.status_id')\n")),
    ("named", "SQL = 'SELECT st.max_dwell_days FROM crm_deal_statuses AS st'\n"),
    ("named", "SQL = 'SELECT crm_deal_statuses.probability FROM crm_deal_statuses'\n"),
    ("attr", (
        "async def chance(db, sid):\n"
        "    stage = await load_row(db, 'crm_deal_statuses', sid)\n"
        "    return stage.probability\n")),
    ("getattr", (
        "TABLE = 'crm_deal_statuses'\n"
        "async def limit(db, sid):\n"
        "    stage = await require_row(db, TABLE, sid, 'Status')\n"
        "    return getattr(stage, 'max_dwell_days', None)\n")),
    ("read_status", (
        "async def one(db, sid):\n"
        "    return status_wire(await read_status(db, 'crm_deal_statuses', sid))\n")),
    ("read_statuses",
     "async def all_(db):\n    return await read_statuses(db, 'crm_deal_statuses')\n"),
)


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """The string constants of the module level, by name."""
    found: dict[str, str] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        value = getattr(node, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.update({t.id: value.value for t in targets if isinstance(t, ast.Name)})
    return found


def _text_of(node: ast.AST, names: dict[str, str]) -> str | None:
    """The text of a string node: a literal, adjacent literals (the AST joins
    them), an f-string with each module constant in place, or a ``+`` of
    those. None when a part is no string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name):
                parts.append(names.get(part.value.id, "{}"))
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text_of(node.left, names), _text_of(node.right, names)
        return left + right if left is not None and right is not None else None
    return None


def _stage_row_reads(source: str) -> list[tuple[str, str]]:
    """Each read of a stage-row value alone in *source*, as (function, form)."""
    tree = ast.parse(source)
    names = _module_strings(tree)
    parent: dict[ast.AST, ast.AST] = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def owner(node: ast.AST) -> str:
        chain = []
        while node in parent:
            node = parent[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                chain.append(node.name)
        return ".".join(reversed(chain)) or "<module>"

    def loads_a_stage(node: ast.AST) -> str | None:
        """The name of the loader when *node* loads a deal stage row."""
        call = node.value if isinstance(node, ast.Await) else node
        if not isinstance(call, ast.Call):
            return None
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        tables = [a.value if isinstance(a, ast.Constant) else names.get(getattr(a, "id", ""))
                  for a in call.args]
        return name if name in _LOADERS and "crm_deal_statuses" in tables else None

    found: list[tuple[str, str]] = []
    done: set[int] = set()
    for node in ast.walk(tree):
        if id(node) in done or not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        sql = _text_of(node, names)
        if sql is None:
            continue
        done.update(id(sub) for sub in ast.walk(node))
        found += [(owner(node), form) for form in _sql_forms(_MEMBERSHIP_FIRST.sub("", sql))]
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        rows = {t.id for node in ast.walk(fn) if isinstance(node, ast.Assign)
                and loads_a_stage(node.value) for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(fn):
            loader = loads_a_stage(node) if isinstance(node, ast.Call) else None
            if loader in ("read_status", "read_statuses"):
                found.append((owner(node), loader))
            if isinstance(node, ast.Attribute) and node.attr in _VALUE_COLUMNS and (
                    getattr(node.value, "id", None) in rows or loads_a_stage(node.value)):
                found.append((owner(node), "attr"))
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr"
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in _VALUE_COLUMNS
                    and (getattr(node.args[0], "id", None) in rows
                         or loads_a_stage(node.args[0]))):
                found.append((owner(node), "getattr"))
    return found


def test_no_deal_reader_reads_the_stage_row_value_alone() -> None:
    """R7 for the rule of ``apps/services/gateway/AGENTS.md`` (CRM-M3b repair
    5, widened in round 2). A deal reader never reads ``probability`` or
    ``max_dwell_days`` of the stage row alone, so one deal never shows two
    win chances. The fence parses each module of ``routes/crm/`` and finds:

    - SQL that names a bare column, ``s.<column>`` or ``SELECT *`` with
      ``FROM crm_deal_statuses``. Adjacent literals, an f-string with a
      module constant, and a ``+`` of literals join first.
    - SQL that reads a column through each alias that the statement gives
      to ``crm_deal_statuses``, with or without ``AS``, or through the table
      name itself (repair 3).
    - ``read_status`` or ``read_statuses`` of ``crm_deal_statuses``.
    - ``load_row``, ``require_row``, ``read_status`` or ``read_statuses`` of
      ``crm_deal_statuses``, and then ``.probability``, ``.max_dwell_days``
      or ``getattr`` of either, in the same function.

    Only the functions of ``_STAGE_ROW_OWNERS`` may read the stage row, each
    for its reason. The held-stage read of the board is the one use of the
    stage-row list: a stage that the pipeline does not list has no
    membership.

    Out of reach, so a reviewer checks them: a table name in a local name or
    a parameter (``load_row(db, table, ...)``), a row passed to another
    function and read there, ``getattr`` with a column in a name, SQL that
    joins a runtime value between SELECT and FROM, and each reader outside
    ``routes/crm/`` (``crm_sync``). Mutation: each entry of ``_MUTATIONS``
    turns the scan red."""
    for form, mutation in _MUTATIONS:
        assert form in {f for _owner, f in _stage_row_reads(mutation)}, (
            "non-vacuity", form, mutation)
    found: dict[tuple[str, str], set[str]] = {}
    held: dict[str, int] = {}
    for path in sorted(_CRM_ROUTES.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for where, form in _stage_row_reads(source):
            found.setdefault((path.name, where), set()).add(form)
        held[path.name] = source.count('EFFECTIVE_STATUS_COLUMNS["crm_deal_statuses"]')
    assert len(held) > 40, "the fence reads each module"
    unexpected = {k: sorted(v) for k, v in found.items() if k not in _STAGE_ROW_OWNERS}
    assert unexpected == {}, unexpected
    assert set(_STAGE_ROW_OWNERS) <= set(found), set(_STAGE_ROW_OWNERS) - set(found)
    assert {name: n for name, n in held.items() if n} == {"pipeline.py": 1}, held


@pytest.mark.parametrize("sqlstate", ["40P01", "40001", "55P03"])
async def test_the_value_route_answers_try_again_on_a_lock_failure(
    pns, monkeypatch, sqlstate: str,
) -> None:
    """R7 for the 409 ``try_again`` of the value route (repair 6). A deadlock
    (40P01), a serialization failure (40001) or a lock that is not free
    (55P03) gives 409 with words that say to try again, never a 500. The
    transaction rolls back, so nothing changes."""
    from gateway.routes.crm import pipelines
    from sqlalchemy.exc import DBAPIError

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)

    async def locked(*_a: Any, **_kw: Any) -> Any:
        raise DBAPIError("UPDATE crm_pipeline_stages", None, _LockError(sqlstate))

    monkeypatch.setattr(pipelines, "set_stage_values_in", locked)
    r = await _call(pns, admin, "PATCH", _values_path(ids["pipeline"], ids["proposal"]),
                    {"probability": 30})
    assert r.status_code == 409 and _code(r) == "try_again", r.text
    assert "Try again" in _words(r)
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)


async def test_another_database_error_of_the_value_route_stays_an_error(
    pns, monkeypatch,
) -> None:
    """Only the three transient states map to the 409. Any other error stays
    an error, so the mapping hides no defect."""
    from gateway.routes.crm import pipelines
    from sqlalchemy.exc import DBAPIError

    ids = _seed(pns, pns.org_a)

    async def broken(*_a: Any, **_kw: Any) -> Any:
        raise DBAPIError("UPDATE crm_pipeline_stages", None, _LockError("42P01"))

    monkeypatch.setattr(pipelines, "set_stage_values_in", broken)
    with pytest.raises(DBAPIError):
        await _call(pns, _admin(pns.org_a), "PATCH",
                    _values_path(ids["pipeline"], ids["proposal"]), {"probability": 30})


async def test_the_undo_of_an_edit_of_a_deleted_stage_says_it_cannot_come_back(pns) -> None:
    """Repair 7. A status delete removes each membership of the stage too, so
    no take-out is left to undo. The words say that the stage was deleted,
    and never ask for the Undo of a take-out."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    edit = await _call(pns, admin, "PATCH", _values_path(ids["pipeline"], ids["qualify"]),
                       {"probability": 15})
    assert edit.status_code == 200, edit.text
    deleted = await _call(pns, admin, "DELETE", f"/crm/statuses/deal/{ids['qualify']}")
    assert deleted.status_code == 200, deleted.text
    (row,) = _changes(pns, "pipeline_stage", "change")
    undo = await _call(pns, admin, "POST", f"/crm/change-history/{row.id}/undo")
    assert undo.status_code == 409 and _code(undo) == "gone", undo.text
    words = _words(undo)
    assert '"Qualify" was deleted, so this change cannot come back' in words, words
    assert "took it out" not in words, words


# ── CRM-M3b repair round 2 ──────────────────────────────────────────────────


async def _undo_of(p: Any, user: Any, row: Any) -> Any:
    return await _call(p, user, "POST", f"/crm/change-history/{row.id}/undo")


def _open_change(p: Any, area: str, action: str) -> Any:
    """The newest row of *area* and *action* that is no Undo."""
    return [x for x in _changes(p, area, action) if x.undoes_id is None][-1]


def _renewals(p: Any) -> str:
    """A second deal pipeline, "Renewals", with no stage."""
    return str(_one(p, "INSERT INTO crm_pipelines (organization_id, name, position, is_default) "
                       "VALUES (CAST(:o AS uuid), 'Renewals', 1, false) RETURNING id",
                    o=p.org_a))


def _signed(p: Any) -> str:
    """A second visible ``won`` stage, so "Won" may change its type."""
    return str(_one(p, "INSERT INTO crm_deal_statuses (organization_id, name, type, position, "
                       "probability) VALUES (CAST(:o AS uuid), 'Signed', 'won', 35, 100) "
                       "RETURNING id", o=p.org_a))


def _strip(p: Any, row: Any, *keys: str) -> None:
    """Take *keys* out of "before" and "after" of one change row, as main's
    code wrote it before CRM-M3b. The guard trigger of migration 341 keeps a
    row fixed, so the test turns the triggers off for its own write."""
    with p.admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        for key in keys:
            c.execute(text(
                "UPDATE crm_change_log SET before = before - CAST(:k AS text), "
                "after = after - CAST(:k AS text) WHERE id = CAST(:id AS uuid)"),
                {"k": key, "id": str(row.id)})


@pytest.mark.parametrize("flag", ["*", ""])
async def test_a_status_undo_clamps_a_membership_that_came_after_the_change(
    pns, monkeypatch, flag: str,
) -> None:
    """Round 2 item 1 (P1), the order of the re-review. "Won" becomes
    ``open`` at 40 through the old route, a stage add copies 40 into
    "Renewals", and the Undo of the old route puts the type back. The Undo
    gives NULL to the membership of Renewals, so Renewals shows ``won`` at
    100, and a new deal there takes 100. The old route, the stage add and the
    Undo are not behind ``CRM_PIPELINES_ORGS``, so the case runs with the
    flag off too. The Undo row logs the NULL, and the Undo of the add works
    out of order, because the clamp took its 40."""
    from acb_common import get_settings

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    _signed(pns)
    renewals = _renewals(pns)
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", flag, raising=False)
    retype = await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids['won']}",
                         {"type": "open", "probability": 40})
    assert retype.status_code == 200, retype.text
    add = await _call(pns, admin, "POST", f"/crm/pipelines/{renewals}/stages",
                      {"status_id": ids["won"]})
    assert add.status_code == 201, add.text
    assert _membership(pns, renewals, ids["won"]) == (40, None)
    undo = await _undo_of(pns, admin, _open_change(pns, "deal_stage", "change"))
    assert undo.status_code == 200, undo.text
    assert _membership(pns, renewals, ids["won"]) == (None, None), "the clamp of the type now"
    assert _membership(pns, ids["pipeline"], ids["won"]) == (None, None)
    lane = (await _lanes(pns, admin, renewals))["Won"]
    assert (lane["type"], lane["probability"]) == ("won", 100)
    listed = {(pl["id"], s["name"]): (s["type"], s["probability"])
              for pl in (await _call(pns, admin, "GET", "/crm/pipelines")).json()
              for s in pl["stages"]}
    assert listed[(renewals, "Won")] == listed[(ids["pipeline"], "Won")] == ("won", 100)
    (back,) = [x for x in _changes(pns, "deal_stage", "change") if x.undoes_id]
    assert back.before["pipelines"][renewals] == {"probability": 40}, back.before
    assert back.after["pipelines"][renewals] == {"probability": None}, back.after
    deal = await _call(pns, admin, "POST", "/crm/deals",
                       {"name": "Renewed", "pipeline_id": renewals, "status_id": ids["won"]})
    assert deal.status_code == 201 and deal.json()["probability"] == 100, deal.text
    _one(pns, "DELETE FROM crm_deals WHERE pipeline_id = CAST(:pl AS uuid) RETURNING id",
         pl=renewals)
    # The Undo of the add, out of order: the clamp took its 40, so it works.
    out = await _undo_of(pns, admin, _open_change(pns, "pipeline_stage", "create"))
    assert out.status_code == 200, out.text
    assert _membership(pns, renewals, ids["won"]) is None
    (taken,) = [x for x in _changes(pns, "pipeline_stage", "delete") if x.undoes_id]
    assert taken.before["probability"] is None, "the take-out logs what it took"


@pytest.mark.parametrize("flag", ["*", ""])
async def test_a_status_undo_clamps_a_membership_that_a_take_out_undo_put_back(
    pns, monkeypatch, flag: str,
) -> None:
    """Round 2 item 1, the take-out form of P1. A take-out row that main
    wrote holds no values, so its Undo copies the stage row: 40 while "Won"
    is ``open``. The Undo of the old route then puts the type back, and the
    membership of Renewals takes NULL. Renewals shows ``won`` at 100."""
    from acb_common import get_settings

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    _signed(pns)
    renewals = _renewals(pns)
    with pns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id, position) "
            "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), 0)"),
            {"o": pns.org_a, "p": renewals, "s": ids["won"]})
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", flag, raising=False)
    out = await _call(pns, admin, "DELETE", _values_path(renewals, ids["won"]))
    assert out.status_code == 200, out.text
    taken = _open_change(pns, "pipeline_stage", "delete")
    _strip(pns, taken, "probability", "max_dwell_days")
    retype = await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids['won']}",
                         {"type": "open", "probability": 40})
    assert retype.status_code == 200, retype.text
    change = _open_change(pns, "deal_stage", "change")
    put = await _undo_of(pns, admin, taken)
    assert put.status_code == 200, put.text
    assert _membership(pns, renewals, ids["won"]) == (40, None), "a copy of the stage row"
    undo = await _undo_of(pns, admin, change)
    assert undo.status_code == 200, undo.text
    assert _membership(pns, renewals, ids["won"]) == (None, None)
    lane = (await _lanes(pns, admin, renewals))["Won"]
    assert (lane["type"], lane["probability"]) == ("won", 100)


@pytest.mark.parametrize("flag", ["*", ""])
async def test_a_status_undo_writes_only_the_memberships_its_row_names(
    pns, monkeypatch, flag: str,
) -> None:
    """Round 2 item 2 (P2), the order of the re-review. Every membership of
    "Proposal" holds 70 and the stage row 50. The old route sets 70, so it
    writes no membership, and its row logs ``pipelines: {}``. Its Undo puts
    the stage row back to 50 and leaves the membership at 70, because an
    Undo never fans out. The older change then still undoes (200). With the
    flag on, the 70 comes from the route of one pipeline. With the flag off,
    a stage add copies it, and the other membership goes out."""
    from acb_common import get_settings

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    path = f"/crm/statuses/deal/{ids['proposal']}"
    if flag:
        pid = ids["pipeline"]
        edit = await _call(pns, admin, "PATCH", _values_path(pid, ids["proposal"]),
                           {"probability": 70})
        assert edit.status_code == 200, edit.text
        older = _open_change(pns, "pipeline_stage", "change")
    else:
        monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", "", raising=False)
        pid = _renewals(pns)
        assert (await _call(pns, admin, "PATCH", path, {"probability": 70})).status_code == 200
        add = await _call(pns, admin, "POST", f"/crm/pipelines/{pid}/stages",
                          {"status_id": ids["proposal"]})
        assert add.status_code == 201, add.text
        older = _open_change(pns, "pipeline_stage", "create")
        undone = await _undo_of(pns, admin, _open_change(pns, "deal_stage", "change"))
        assert undone.status_code == 200, undone.text
        out = await _call(pns, admin, "DELETE", _values_path(ids["pipeline"], ids["proposal"]))
        assert out.status_code == 200, out.text
    assert _membership(pns, pid, ids["proposal"]) == (70, None)
    same = await _call(pns, admin, "PATCH", path, {"probability": 70})
    assert same.status_code == 200, same.text
    change = _open_change(pns, "deal_stage", "change")
    assert change.before == {"probability": 50, "pipelines": {}}, change.before
    assert change.after == {"probability": 70, "pipelines": {}}, change.after
    undo = await _undo_of(pns, admin, change)
    assert undo.status_code == 200, undo.text
    assert _membership(pns, pid, ids["proposal"]) == (70, None), "an Undo never fans out"
    assert (await _lanes(pns, admin, pid))["Proposal"]["probability"] == 70
    assert _one(pns, "SELECT probability FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=ids["proposal"]) == 50
    (back,) = [x for x in _changes(pns, "deal_stage", "change")
               if x.undoes_id == change.id]
    assert back.after == {"probability": 50, "pipelines": {}}, back.after
    shown = {r["id"]: r for r in (await _call(pns, admin, "GET", "/crm/change-history")
                                 ).json()["rows"]}
    assert shown[str(older.id)]["can_undo"] is True
    first = await _undo_of(pns, admin, older)
    assert first.status_code == 200, first.text
    if flag:
        assert _membership(pns, pid, ids["proposal"]) == (None, None)
        assert (await _lanes(pns, admin, pid))["Proposal"]["probability"] == 50
    else:
        assert _membership(pns, pid, ids["proposal"]) is None


@pytest.mark.parametrize("flag", ["*", ""])
async def test_the_undo_of_a_row_from_before_m3b_writes_the_stage_row_only(
    pns, monkeypatch, flag: str,
) -> None:
    """Round 2 item 2. A change row that main wrote has no ``pipelines``
    key. Its Undo puts the stage row back and writes no membership: a value
    that a pipeline took since stays. With the flag on, the edit of one
    pipeline sets it. With the flag off, a stage add copies it."""
    from acb_common import get_settings

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    old = await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids['proposal']}",
                      {"probability": 70})
    assert old.status_code == 200, old.text
    change = _open_change(pns, "deal_stage", "change")
    _strip(pns, change, "pipelines")
    with pns.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_pipeline_stages SET probability = NULL "
                       "WHERE status_id = CAST(:s AS uuid)"), {"s": ids["proposal"]})
    if flag:
        pid = ids["pipeline"]
        assert (await _call(pns, admin, "PATCH", _values_path(pid, ids["proposal"]),
                            {"probability": 30})).status_code == 200
        kept = (30, None)
    else:
        monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", "", raising=False)
        pid = _renewals(pns)
        assert (await _call(pns, admin, "POST", f"/crm/pipelines/{pid}/stages",
                            {"status_id": ids["proposal"]})).status_code == 201
        kept = (70, None)
    undo = await _undo_of(pns, admin, change)
    assert undo.status_code == 200, undo.text
    assert _one(pns, "SELECT probability FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=ids["proposal"]) == 50
    assert _membership(pns, pid, ids["proposal"]) == kept
    if not flag:
        assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)


@pytest.mark.parametrize("flag", ["*", ""])
async def test_the_undo_of_an_edit_never_refuses_for_the_clamp(
    pns, monkeypatch, flag: str,
) -> None:
    """Round 2 item 3, the order of the re-review. "Proposal" gets 60 and
    then 100 in the default pipeline, and the old route makes it ``won`` at
    100. The Undo of the second edit puts back 60, which a ``won`` stage
    does not take, so it writes NULL and answers 200, never 409. "Qualify"
    takes the same steps, undone newest first: each Undo answers 200, and
    the pipeline follows the stage row again. The edits need the flag. The
    type change and the Undo do not, so they run with the flag off too."""
    from acb_common import get_settings

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    edits: dict[str, list[Any]] = {}
    for key in ("proposal", "qualify"):
        path = _values_path(ids["pipeline"], ids[key])
        for chance in (60, 100):
            assert (await _call(pns, admin, "PATCH", path, {"probability": chance})
                    ).status_code == 200
        edits[key] = [x for x in _changes(pns, "pipeline_stage", "change")
                      if x.after["status_id"] == ids[key]]
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", flag, raising=False)
    retyped = {}
    for key in ("proposal", "qualify"):
        r = await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids[key]}",
                        {"type": "won", "probability": 100})
        assert r.status_code == 200, r.text
        retyped[key] = _open_change(pns, "deal_stage", "change")
    # The order of the review: the edit first.
    second = await _undo_of(pns, admin, edits["proposal"][1])
    assert second.status_code == 200, second.text
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    lane = (await _lanes(pns, admin, ids["pipeline"]))["Proposal"]
    assert (lane["type"], lane["probability"]) == ("won", 100)
    (put,) = [x for x in _changes(pns, "pipeline_stage", "change")
              if x.undoes_id == edits["proposal"][1].id]
    assert put.after == {"status_id": ids["proposal"], "probability": None}, put.after
    # Newest first: the type change, then each edit. Each answers 200.
    for row in (retyped["qualify"], *reversed(edits["qualify"])):
        r = await _undo_of(pns, admin, row)
        assert r.status_code == 200, (row.summary, r.text)
    assert _membership(pns, ids["pipeline"], ids["qualify"]) == (None, None)
    lane = (await _lanes(pns, admin, ids["pipeline"]))["Qualify"]
    assert (lane["type"], lane["probability"]) == ("open", 10)


@pytest.mark.parametrize("flag", ["*", ""])
async def test_a_stage_repair_clamps_each_membership_and_no_edit_undo_sticks(
    pns, monkeypatch, flag: str,
) -> None:
    """Round 2 items 1 and 3. The stage repair of the Zoho import changes a
    type and logs no change row (``stage_metadata.apply_repair``). A stage
    row at 100 makes the repair write the type alone. Each membership value
    that ``won`` does not take goes to NULL, so the pipeline shows 100. The
    Undo of each edit then answers 200, never a 409 that no Undo can clear:
    the clamp took the value of the edit."""
    from acb_common import get_settings
    from acb_common.db import tenant_session
    from gateway.routes.crm.stage_metadata import RepairPlan, apply_repair

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    # "Proposal": the old route sets 100 in each pipeline, then an edit sets 60.
    assert (await _call(pns, admin, "PATCH", f"/crm/statuses/deal/{ids['proposal']}",
                        {"probability": 100})).status_code == 200
    # "Qualify": the stage row is 100 and the membership follows it.
    _one(pns, "UPDATE crm_deal_statuses SET probability = 100 WHERE id = CAST(:s AS uuid) "
              "RETURNING id", s=ids["qualify"])
    for key in ("proposal", "qualify"):
        assert (await _call(pns, admin, "PATCH", _values_path(ids["pipeline"], ids[key]),
                            {"probability": 60})).status_code == 200
    edits = {x.after["status_id"]: x for x in _changes(pns, "pipeline_stage", "change")}
    monkeypatch.setattr(get_settings(), "crm_pipelines_orgs", flag, raising=False)
    url = pns.app_url.render_as_string(hide_password=False)
    plan = RepairPlan(updates={ids["proposal"]: {"type": "won"}, ids["qualify"]: {"type": "won"}})
    async with tenant_engine_scope(url), tenant_session(pns.org_a) as db:
        await apply_repair(db, plan)
    lanes = await _lanes(pns, admin, ids["pipeline"])
    for key, name in (("proposal", "Proposal"), ("qualify", "Qualify")):
        assert _membership(pns, ids["pipeline"], ids[key]) == (None, None), key
        assert (lanes[name]["type"], lanes[name]["probability"]) == ("won", 100), key
        r = await _undo_of(pns, admin, edits[ids[key]])
        assert r.status_code == 200, (key, r.text)
        assert (await _lanes(pns, admin, ids["pipeline"]))[name]["probability"] == 100
    rows = {r["id"]: r for r in (await _call(pns, admin, "GET", "/crm/change-history")
                                ).json()["rows"]}
    assert not any(rows[str(x.id)]["can_undo"] for x in edits.values())


# ── CRM-M3b repair round 3 ──────────────────────────────────────────────────

#: The lowest pipeline id, so its membership is the first that a writer of
#: one stage locks (``ORDER BY pipeline_id``).
_EARLY = "00000000-0000-4000-8000-000000000001"

#: The backends of this database that wait on a lock: a row, or the create lock.
_WAITING_SQL = (
    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
    "AND wait_event_type = 'Lock'"
)


async def _until_waiting(p: Any, count: int, what: str) -> None:
    """Poll until *count* backends wait on a lock, else fail in 30 s."""
    import asyncio

    for _ in range(300):
        if int(_one(p, _WAITING_SQL) or 0) >= count:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{what} never waited on a lock")


@pytest.mark.parametrize("body", [{"probability": 40}, {"type": "open"}])
async def test_a_status_patch_and_a_pipeline_copy_at_one_moment_never_deadlock(
    pns, body: dict[str, Any],
) -> None:
    """Repair 3 (P1), the probe of the second re-review. The status PATCH
    locks the stage row and then each membership: the fan-out of a win
    chance, or the sweep of a type change. A copy locks each membership of
    the source and then its stage row. A third transaction holds the first
    membership of the stage, so the PATCH waits there. Then the copy starts
    and waits, and then the hold ends. The PATCH takes the create lock first
    now, so the copy waits for the whole PATCH: no cycle, both answer 2xx,
    and the copy reads the value of the PATCH. Mutation: take the lock out of
    ``admin.patch_status_in`` -> 40P01 in the PATCH, and a 409 here."""
    import asyncio

    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    retype = "type" in body
    stage = ids["won"] if retype else ids["proposal"]
    if retype:
        _signed(pns)
    _one(pns, "INSERT INTO crm_pipelines (id, organization_id, name, position, is_default) "
              "VALUES (CAST(:i AS uuid), CAST(:o AS uuid), 'Early', 1, false) RETURNING id",
         i=_EARLY, o=pns.org_a)
    _one(pns, "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id, "
              "position) VALUES (CAST(:o AS uuid), CAST(:pl AS uuid), CAST(:s AS uuid), 0) "
              "RETURNING 1", o=pns.org_a, pl=_EARLY, s=stage)
    tasks: list[Any] = []
    with pns.admin_engine.connect() as c:
        hold = c.begin()
        try:
            c.execute(text(
                "SELECT 1 FROM crm_pipeline_stages WHERE pipeline_id = CAST(:pl AS uuid) "
                "AND status_id = CAST(:s AS uuid) FOR UPDATE"), {"pl": _EARLY, "s": stage})
            tasks.append(asyncio.ensure_future(_call(
                pns, admin, "PATCH", f"/crm/statuses/deal/{stage}", body)))
            await _until_waiting(pns, 1, "the PATCH")
            tasks.append(asyncio.ensure_future(_call(
                pns, admin, "POST", "/crm/pipelines",
                {"name": "Copy", "copy_from": ids["pipeline"]})))
            await _until_waiting(pns, 2, "the copy")
            hold.commit()
        finally:
            if hold.is_active:
                hold.rollback()
            done = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=90)
    patch, copy = done
    assert not isinstance(patch, BaseException), repr(patch)
    assert not isinstance(copy, BaseException), repr(copy)
    assert patch.status_code == 200, patch.text
    assert copy.status_code == 201, copy.text
    if retype:
        assert patch.json()["type"] == "open"
    else:
        assert _membership(pns, copy.json()["id"], stage) == (40, None), "the copy came second"


@pytest.mark.parametrize("sqlstate", ["40P01", "40001", "55P03"])
async def test_the_status_patch_answers_try_again_on_a_lock_failure(
    pns, monkeypatch, sqlstate: str,
) -> None:
    """Repair 3 (P1). A deadlock (40P01), a serialization failure (40001) or
    a lock that is not free (55P03) in ``PATCH /crm/statuses/{kind}/{id}``
    gives 409 ``try_again`` with words that say to try again, never a 500.
    The transaction rolls back, so nothing changes."""
    from gateway.routes.crm import admin as admin_routes
    from sqlalchemy.exc import DBAPIError

    ids = _seed(pns, pns.org_a)

    async def locked(*_a: Any, **_kw: Any) -> Any:
        raise DBAPIError("UPDATE crm_pipeline_stages", None, _LockError(sqlstate))

    monkeypatch.setattr(admin_routes, "patch_status_in", locked)
    r = await _call(pns, _admin(pns.org_a), "PATCH", f"/crm/statuses/deal/{ids['proposal']}",
                    {"probability": 30})
    assert r.status_code == 409 and _code(r) == "try_again", r.text
    assert _words(r) == (
        "Someone changed this deal stage at the same moment, so nothing changed. Try again.")
    assert _one(pns, "SELECT probability FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)",
                s=ids["proposal"]) == 50


async def test_another_database_error_of_the_status_patch_stays_an_error(
    pns, monkeypatch,
) -> None:
    """Only the three transient states map to the 409. Any other error stays
    an error, so the mapping hides no defect."""
    from gateway.routes.crm import admin as admin_routes
    from sqlalchemy.exc import DBAPIError

    ids = _seed(pns, pns.org_a)

    async def broken(*_a: Any, **_kw: Any) -> Any:
        raise DBAPIError("UPDATE crm_deal_statuses", None, _LockError("42P01"))

    monkeypatch.setattr(admin_routes, "patch_status_in", broken)
    with pytest.raises(DBAPIError):
        await _call(pns, _admin(pns.org_a), "PATCH",
                    f"/crm/statuses/deal/{ids['proposal']}", {"probability": 30})


async def _edit_twice_and_retype(
    p: Any, user: Any, ids: dict[str, str],
) -> tuple[Any, Any, Any]:
    """The stage "Proposal" gets 60 and then 100 in the default pipeline, and
    the old route makes it ``won`` at 100. Returns the two edit rows and the
    row of the type change."""
    path = _values_path(ids["pipeline"], ids["proposal"])
    for chance in (60, 100):
        assert (await _call(p, user, "PATCH", path, {"probability": chance})).status_code == 200
    first, second = [x for x in _changes(p, "pipeline_stage", "change") if x.undoes_id is None]
    r = await _call(p, user, "PATCH", f"/crm/statuses/deal/{ids['proposal']}",
                    {"type": "won", "probability": 100})
    assert r.status_code == 200, r.text
    return first, second, _open_change(p, "deal_stage", "change")


async def test_an_edit_undo_after_the_type_came_back_says_the_type_replaced_it(pns) -> None:
    """Repair 3 (P2). The order of the re-review: a copied value 30, an edit
    to 60, an edit to 100, the type to ``won``. Then the Undo of the second
    edit (out of order) writes NULL through the clamp, and the Undo of the
    type change makes "Proposal" ``open`` at 50 again. The Undo of the first
    edit still refuses, because no Undo puts 30 back. No newer change is
    open now, so the words say that the type change replaced the value, and
    the member sets it again. A value that a newer open change holds keeps
    the words "Undo the newer change first"."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    _one(pns, "UPDATE crm_pipeline_stages SET probability = 30 WHERE pipeline_id = "
              "CAST(:pl AS uuid) AND status_id = CAST(:s AS uuid) RETURNING 1",
         pl=ids["pipeline"], s=ids["proposal"])
    first, second, retyped = await _edit_twice_and_retype(pns, admin, ids)
    newer = await _undo_of(pns, admin, first)
    assert newer.status_code == 409 and _code(newer) == "changed_since", newer.text
    assert _words(newer) == "This changed again after this change. Undo the newer change first."
    for row in (second, retyped):
        r = await _undo_of(pns, admin, row)
        assert r.status_code == 200, (row.summary, r.text)
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    lane = (await _lanes(pns, admin, ids["pipeline"]))["Proposal"]
    assert (lane["type"], lane["probability"]) == ("open", 50)
    for _ in range(2):
        r = await _undo_of(pns, admin, first)
        assert r.status_code == 409 and _code(r) == "changed_since", r.text
        assert _words(r) == (
            'A change of the stage type replaced the win chance of "Proposal" in the '
            'pipeline "Sales pipeline", so this change cannot come back. Set the win '
            "chance again in that pipeline."), _words(r)
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    again = await _call(pns, admin, "PATCH", _values_path(ids["pipeline"], ids["proposal"]),
                        {"probability": 30})
    assert again.status_code == 200, again.text
    assert (await _lanes(pns, admin, ids["pipeline"]))["Proposal"]["probability"] == 30


async def test_an_edit_undo_that_writes_nothing_logs_no_value_line(pns) -> None:
    """Repair 3 (P3). The Undo of the second edit writes NULL through the
    clamp, while "Proposal" is ``won``. The Undo of the first edit then puts
    back NULL, which the membership holds already, so it writes nothing. Its
    row names no value: Change history shows no "Win chance: None → None"
    line, and the summary says that nothing changed. The lane shows 100."""
    ids = _seed(pns, pns.org_a)
    admin = _admin(pns.org_a)
    first, second, _retyped = await _edit_twice_and_retype(pns, admin, ids)
    for row in (second, first):
        r = await _undo_of(pns, admin, row)
        assert r.status_code == 200, (row.summary, r.text)
    assert _membership(pns, ids["pipeline"], ids["proposal"]) == (None, None)
    lane = (await _lanes(pns, admin, ids["pipeline"]))["Proposal"]
    assert (lane["type"], lane["probability"]) == ("won", 100)
    (undo,) = [x for x in _changes(pns, "pipeline_stage", "change") if x.undoes_id == first.id]
    assert undo.after == {"status_id": ids["proposal"]}, undo.after
    assert undo.before == {"status_id": ids["proposal"], "stage": "Proposal"}, undo.before
    assert undo.summary == (
        'Nothing changed: the stage "Proposal" in the pipeline "Sales pipeline" already '
        "has the win chance that this Undo puts back"), undo.summary
    rows = {r["id"]: r for r in (await _call(pns, admin, "GET", "/crm/change-history")
                                ).json()["rows"]}
    shown = rows[str(undo.id)]
    assert not ({"probability", "max_dwell_days"} & (set(shown["after"]) | set(shown["before"])))
