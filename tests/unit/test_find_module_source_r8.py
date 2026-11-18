"""R8 — the mirrored CRM modules are a source of the find engine (FIND-11,
task 3710, ``find_engine.md`` §9.2).

Against a REAL Postgres, as the non-privileged role of the phase-4 rehearsal.
The seed goes in as the superuser. Each read below goes through the CRM
source of the engine (``sources.REGISTRY["crm"]``), the company route and
``GET /search/names``, as a member of one org.

1. A record name finds the record, and a near spelling ("Operater Associates")
   finds it by the near rung, with ``matched_by``.
2. Org B never shows: no record, no name and no near name.
3. A module that the profile does not mirror never shows, though its rows
   exist. A member with no ``feature:crm`` gets nothing.
4. Exact comes before near in ``resolve_name``.
5. With either flag empty, no module record shows.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_aggregate_fields_r8 import (
    _TABLES,
    _admin,
    _defs,
    _mirror_org,
    _module,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_DEFS = [("Region", "text", None, False, False)]

#: Org A, module Operator_Associates: (name, region).
_ROWS_A = [
    ("Operator Associates Pvt Ltd", "Pune"),
    ("Operator Lee", "Delhi"),
    ("Operater Kim", "Mumbai"),
    ("Zenith Traders", "Goa"),
]
#: Org A, a module that the profile does not mirror.
_ROWS_HIDDEN = [("Operator Hidden Corp", "Nowhere")]
#: Org B, the same module name.
_ROWS_B = [("Operator Secret Rival", "Rivalcity")]


def _seed(p: Any, org: str, module: str, rows: list[tuple[str, str]]) -> None:
    with p.admin_engine.begin() as c:
        for i, (name, region) in enumerate(rows):
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :n, "
                "CAST(:f AS jsonb))"),
                {"o": org, "m": module, "e": f"{module}{i}", "n": name,
                 "f": json.dumps({"Region": region})})


def _flags(monkeypatch: Any, find: str, module: str) -> None:
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "find_orgs", find, raising=False)
    monkeypatch.setattr(settings, "crm_module_find_orgs", module, raising=False)
    monkeypatch.setattr(settings, "context_recency", True, raising=False)


@pytest.fixture
def world(promoted, monkeypatch):
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    _flags(monkeypatch, "*", "*")
    a, b = promoted.org_a, promoted.org_b
    _mirror_org(promoted, a, [_module("Operator_Associates"),
                              _module("Hidden_Module", mirrored=False)])
    _mirror_org(promoted, b, [_module("Operator_Associates")])
    for org in (a, b):
        _defs(promoted, org, "module:Operator_Associates", _DEFS)
    _seed(promoted, a, "Operator_Associates", _ROWS_A)
    _seed(promoted, a, "Hidden_Module", _ROWS_HIDDEN)
    _seed(promoted, b, "Operator_Associates", _ROWS_B)
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (a, b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


async def _as(p: Any, user: Any, make: Any) -> Any:
    """Run ``make()`` as *user*, in the tenant of the user's org."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    bind_tenant(user.organization_id)
    scope = clear_tenant()
    try:
        bind_tenant(user.organization_id)
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            return await make()
    finally:
        release_tenant(scope)


async def _find(p: Any, org: str, q: str, user: Any = None) -> list[Any]:
    from gateway.retrieval import sources

    who = user or _admin(org)
    return await _as(p, who, lambda: sources.REGISTRY["crm"].find(who, q, 10))


def _module_hits(hits: list[Any]) -> list[Any]:
    return [h for h in hits if h.kind == "module_record"]


async def test_a_record_name_finds_the_record(world, app_engine) -> None:
    hits = _module_hits(await _find(world, world.org_a, "operator associates"))
    assert [h.title for h in hits] == ["Operator Associates Pvt Ltd"]
    assert hits[0].hint == "Operator_Associate"
    assert hits[0].href.startswith("/crm?tab=module&module=Operator_Associates&mrec=")


async def test_a_text_value_finds_the_record_as_the_module_list_does(world, app_engine) -> None:
    hits = _module_hits(await _find(world, world.org_a, "pune"))
    assert [h.title for h in hits] == ["Operator Associates Pvt Ltd"]


async def test_org_b_never_shows(world, app_engine) -> None:
    from gateway.retrieval import sources

    for q in ("operator", "secret rival", "rivalcity"):
        titles = [h.title for h in _module_hits(await _find(world, world.org_a, q))]
        assert not any("Secret" in t for t in titles), (q, titles)
    user = _admin(world.org_a)
    near = await _as(world, user, lambda: sources.REGISTRY["crm"].near_names(
        user, ["secrit", "rivel"], 5))
    assert "Secret" not in json.dumps({k: [n.name for n in v] for k, v in near.items()})
    # Org B sees its own record, and nothing of org A.
    own = [h.title for h in _module_hits(await _find(world, world.org_b, "operator"))]
    assert own == ["Operator Secret Rival"]


async def test_a_module_that_the_profile_does_not_mirror_never_shows(world, app_engine) -> None:
    from gateway.retrieval import sources

    assert _module_hits(await _find(world, world.org_a, "hidden corp")) == []
    user = _admin(world.org_a)
    near = await _as(world, user, lambda: sources.REGISTRY["crm"].near_names(
        user, ["hiden"], 5))
    assert "Hidden" not in json.dumps({k: [n.name for n in v] for k, v in near.items()})


async def test_a_member_with_no_crm_feature_gets_nothing(world, app_engine) -> None:
    from acb_auth import UserContext, UserRole, build_access

    nobody = UserContext(email="n@r8.test", role=UserRole.EMPLOYEE,
                         access=build_access(["feature:people"]),
                         organization_id=world.org_a)
    assert await _find(world, world.org_a, "operator", user=nobody) == []


@pytest.mark.parametrize(("find", "module"), [("", "*"), ("*", ""), ("", "")])
async def test_with_either_flag_empty_no_module_record_shows(
        world, app_engine, monkeypatch, find, module) -> None:
    _flags(monkeypatch, find, module)
    assert _module_hits(await _find(world, world.org_a, "operator associates")) == []


async def test_a_typo_finds_the_record_by_the_near_rung(world, app_engine) -> None:
    from gateway.retrieval import company

    user = _admin(world.org_a)
    answer = await _as(world, user, lambda: company.company_search(
        q="Operater Associates", apps="crm", k=5, user=user))
    hits = [h for h in answer["hits"] if h["kind"] == "module_record"]
    # "Operater Kim" holds one word of two, by the word rung. The near hit holds both.
    near = [h for h in hits if h["title"] == "Operator Associates Pvt Ltd"]
    assert len(near) == 1
    assert near[0]["matched_by"] == "near"
    assert near[0]["as"] == "Operator"
    assert hits.index(near[0]) == 0
    assert "Secret" not in json.dumps(answer)


async def test_resolve_name_gives_exact_before_near(world, app_engine) -> None:
    from gateway.retrieval import names

    user = _admin(world.org_a)
    body = await _as(world, user, lambda: names.resolve_names(
        name="Operator Lee", kinds=None, user=user))
    rows = [r for r in body["rows"] if r["kind"] == "module_record"]
    assert [(r["name"], r["match"]) for r in rows] == [("Operator Lee", "exact")]
    assert rows[0]["module"] == "Operator_Associate"
    body = await _as(world, user, lambda: names.resolve_names(
        name="Operater", kinds=None, user=user))
    matches = [(r["name"], r["match"]) for r in body["rows"] if r["kind"] == "module_record"]
    assert ("Operater Kim", "exact") in matches
    assert ("Operator Lee", "near") in matches
    assert matches.index(("Operater Kim", "exact")) < matches.index(("Operator Lee", "near"))
    assert "Secret" not in json.dumps(body)
    assert "Hidden" not in json.dumps(body)
