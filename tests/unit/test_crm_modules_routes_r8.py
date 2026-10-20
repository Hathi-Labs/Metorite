"""R8 — the views of the generic Zoho modules, against a real Postgres
(WS-53 CRM-Q6b, ``crm_platform.md`` §18.7 "CRM-Q6b: the views").

``GET /crm/modules``, ``GET /crm/modules/{module}/records`` and
``GET /crm/modules/{module}/records/{id}`` run through the real ``/crm`` router
as the non-privileged role of the phase-4 rehearsal (no superuser, no
BYPASSRLS). The seed goes in as the superuser, so each read below is the first
thing the app role does.

What this proves, one test for each "Done when" item of §18.7 Q6b:

1. org isolation: org B reads none of org A's modules or records, also with
   the same Zoho ids, and a lookup never resolves into the other org;
2. the 404 for a module that the profile does not mirror, before any read;
3. the empty list for a Native org;
4. the lookup links: a known record, a mirrored generic record, and plain text
   for any other target.

It also proves the list contract of the module records: ``q`` on ``name``,
``sort`` from the allowlist, and the page parameters.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_TABLES = (
    "crm_module_records", "crm_field_defs", "crm_source_profiles",
    "integration_connections", "crm_deals", "crm_deal_statuses", "crm_settings",
)


def _member(org: str) -> Any:
    """A member with ``feature:crm``. The org's access rule is ``everyone``."""
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email="rep@crm-q6b-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm"]), organization_id=org,
    )


async def _get(p: Any, org: str, path: str) -> Any:
    """One GET through the real router. The tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    user = _member(org)

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
            return await client.get(path)
    finally:
        release_tenant(scope)


@pytest.fixture
def ns(promoted, monkeypatch):
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


# ── The seed, as the superuser ────────────────────────────────────────────────


def _module(api_name: str, *, mirrored: bool = True, custom: bool = False,
            label: str | None = None) -> dict[str, Any]:
    """One generic module row of a profile, in the shape ``_module_rows`` writes."""
    return {
        "api_name": api_name, "tab": api_name, "label": label or api_name,
        "singular_label": (label or api_name).rstrip("s"), "custom": custom,
        "generated_type": "custom" if custom else "default", "api_supported": True,
        "visible": True, "mirrored": mirrored, "generic": True,
        "scope": "ZohoCRM.modules.custom.READ" if custom
        else f"ZohoCRM.modules.{api_name.lower().replace('_', '')}.READ",
        "display_field": "Name",
    }


#: A profile in the order `_module_rows` keeps (by API name). The route must
#: give the profile order of `generic_modules`: standard modules first, then
#: each custom module.
PROFILE_A = [
    _module("Install_Base", custom=True, label="Install Base"),
    {"api_name": "Leads", "label": "Leads", "mirrored": True, "api_supported": True},
    _module("Products"),
    _module("Quotes"),
    _module("Vendors", mirrored=False),  # waits for its scope: not mirrored
]


def _seed_org(p: Any, org: str, *, mode: str, modules: list[dict[str, Any]]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access) "
            "VALUES (CAST(:o AS uuid), :m, 'everyone') "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = EXCLUDED.mode, "
            "access = EXCLUDED.access"), {"o": org, "m": mode})
        conn = c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app) VALUES (CAST(:o AS uuid), 'zoho', 'crm') RETURNING id"),
            {"o": org}).scalar()
        c.execute(text(
            "INSERT INTO crm_source_profiles (organization_id, connection_id, "
            "version, profile, profile_hash) VALUES (CAST(:o AS uuid), :c, 1, "
            "CAST(:p AS jsonb), :h)"),
            {"o": org, "c": conn, "p": json.dumps({"modules": modules}),
             "h": uuid.uuid4().hex})


def _record(p: Any, org: str, module: str, ext_id: str, name: str,
            fields: dict[str, Any] | None = None, *, modified: str = "2026-10-01") -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id, name, owner_email, fields, ext_url, ext_modified_at) VALUES "
            "(CAST(:o AS uuid), :m, 'zoho', :e, :n, 'rep@crm-q6b-r8.test', "
            "CAST(:f AS jsonb), :u, CAST(:t AS timestamptz)) RETURNING id"),
            {"o": org, "m": module, "e": ext_id, "n": name,
             "f": json.dumps(fields or {"Name": name}),
             "u": f"https://crm.zoho.in/crm/org1/tab/{module}/{ext_id}",
             "t": modified}).scalar())


def _field_def(p: Any, org: str, module: str, key: str, ftype: str = "text",
               *, target: str | None = None, position: int = 0) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
            "options, source, provider_api_name, position) VALUES "
            "(CAST(:o AS uuid), :e, :k, :l, :t, CAST(:opt AS jsonb), 'zoho', :k, :pos)"),
            {"o": org, "e": f"module:{module}", "k": key, "l": key.replace("_", " "),
             "t": ftype, "opt": json.dumps({"lookup_module": target}) if target else None,
             "pos": position})


def _deal(p: Any, org: str, ext_id: str, name: str) -> str:
    with p.admin_engine.begin() as c:
        status = c.execute(text(
            "INSERT INTO crm_deal_statuses (organization_id, name, position, type) "
            "VALUES (CAST(:o AS uuid), :n, 0, 'open') RETURNING id"),
            {"o": org, "n": f"Open {uuid.uuid4().hex[:6]}"}).scalar()
        return str(c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, ext_source, "
            "ext_id) VALUES (CAST(:o AS uuid), :n, :s, 'zoho', :e) RETURNING id"),
            {"o": org, "n": name, "s": status, "e": ext_id}).scalar())


# ── 1. The module list, and org isolation ────────────────────────────────────


async def test_the_module_list_follows_the_profile_order_with_counts(ns) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    _record(ns, a, "Products", "p1", "Widget")
    _record(ns, a, "Products", "p2", "Gadget")
    _record(ns, a, "Quotes", "q1", "Quote 1")

    res = await _get(ns, a, "/crm/modules")
    assert res.status_code == 200, res.text
    body = res.json()
    # Standard modules in the §18.7 order, then each custom module. A known
    # module and a module that waits for its scope are not listed.
    assert [m["api_name"] for m in body] == ["Products", "Quotes", "Install_Base"]
    assert {m["api_name"]: m["count"] for m in body} == {
        "Products": 2, "Quotes": 1, "Install_Base": 0,
    }
    assert body[2]["label"] == "Install Base"


async def test_org_b_reads_none_of_org_a(ns) -> None:
    a, b = ns.org_a, ns.org_b
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    _seed_org(ns, b, mode="mirror", modules=[_module("Products")])
    a_widget = _record(ns, a, "Products", "p1", "A-Widget")
    _record(ns, a, "Quotes", "q1", "A-Quote")
    b_widget = _record(ns, b, "Products", "p1", "B-Widget")  # the SAME Zoho id

    res = await _get(ns, b, "/crm/modules")
    assert res.status_code == 200, res.text
    assert [(m["api_name"], m["count"]) for m in res.json()] == [("Products", 1)]

    listed = await _get(ns, b, "/crm/modules/Products/records")
    assert listed.status_code == 200, listed.text
    assert [r["name"] for r in listed.json()["rows"]] == ["B-Widget"]
    assert listed.json()["total"] == 1

    # A's record id is not a record of B.
    assert (await _get(ns, b, f"/crm/modules/Products/records/{a_widget}")).status_code == 404
    # A module that only A's profile mirrors is unknown to B.
    assert (await _get(ns, b, "/crm/modules/Quotes/records")).status_code == 404

    own = await _get(ns, b, f"/crm/modules/Products/records/{b_widget}")
    assert own.status_code == 200, own.text
    assert own.json()["record"]["name"] == "B-Widget"


# ── 2. The 404 for a module that the profile does not mirror ─────────────────


@pytest.mark.parametrize("module", [
    "Nope",                    # not in the profile
    "Vendors",                 # in the profile, waits for its scope
    "Leads",                   # a known module: it has a table of its own
    "Products' OR '1'='1",     # never reaches SQL text
])
async def test_a_module_the_profile_does_not_mirror_is_404(ns, module: str) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    record = _record(ns, a, "Products", "p1", "Widget")
    for path in (
        f"/crm/modules/{module}/records",
        f"/crm/modules/{module}/records/{record}",
        f"/crm/modules/{module}/records/not-a-uuid",  # the module check comes first
    ):
        res = await _get(ns, a, path)
        assert res.status_code == 404, (path, res.text)
        assert res.json()["detail"]["code"] == "unknown_module"


async def test_a_record_of_another_module_is_404(ns) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    product = _record(ns, a, "Products", "p1", "Widget")
    assert (await _get(ns, a, f"/crm/modules/Quotes/records/{product}")).status_code == 404
    assert (await _get(ns, a, "/crm/modules/Products/records/not-a-uuid")).status_code == 422


# ── 3. The empty list for a Native org ───────────────────────────────────────


async def test_a_native_org_gets_an_empty_list(ns) -> None:
    a = ns.org_a
    # A profile and records left from an earlier mirror do not show.
    _seed_org(ns, a, mode="native", modules=PROFILE_A)
    record = _record(ns, a, "Products", "p1", "Widget")
    res = await _get(ns, a, "/crm/modules")
    assert res.status_code == 200, res.text
    assert res.json() == []
    assert (await _get(ns, a, "/crm/modules/Products/records")).status_code == 404
    assert (await _get(ns, a, f"/crm/modules/Products/records/{record}")).status_code == 404


async def test_a_mirror_org_with_no_profile_gets_an_empty_list(ns) -> None:
    a = ns.org_a
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access) "
            "VALUES (CAST(:o AS uuid), 'mirror', 'everyone')"), {"o": a})
    res = await _get(ns, a, "/crm/modules")
    assert res.status_code == 200, res.text
    assert res.json() == []


# ── 4. The lookup links ──────────────────────────────────────────────────────


async def test_lookups_link_a_known_record_a_generic_record_or_stay_text(ns) -> None:
    a, b = ns.org_a, ns.org_b
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    _seed_org(ns, b, mode="mirror", modules=PROFILE_A)
    deal_a = _deal(ns, a, "d1", "A-Deal")
    _deal(ns, b, "d1", "B-Deal")  # the same Zoho id in the other org
    product_a = _record(ns, a, "Products", "p1", "A-Widget")
    _record(ns, b, "Products", "p1", "B-Widget")
    for key, ftype, target in (
        ("Name", "text", None),
        ("Deal_Name", "lookup", "Deals"),
        ("Product_Name", "lookup", "Products"),
        ("Vendor_Name", "lookup", "Vendors"),      # not mirrored
        ("Contact_Name", "lookup", "Contacts"),    # known, but no local record
        ("Owner", "lookup", None),                 # names no module
    ):
        _field_def(ns, a, "Quotes", key, ftype, target=target)
    quote = _record(ns, a, "Quotes", "q1", "Quote 1", {
        "Name": "Quote 1",
        "Deal_Name": {"id": "d1", "name": "A-Deal"},
        "Product_Name": {"id": "p1", "name": "A-Widget"},
        "Vendor_Name": {"id": "v1", "name": "Vendor"},
        "Contact_Name": {"id": "c-missing", "name": "Nobody"},
        "Owner": {"id": "u1", "name": "Rep", "email": "rep@crm-q6b-r8.test"},
    })

    res = await _get(ns, a, f"/crm/modules/Quotes/records/{quote}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["module"]["api_name"] == "Quotes"
    assert body["record"]["ext_url"].endswith("/Quotes/q1")
    assert body["record"]["fields"]["Deal_Name"] == {"id": "d1", "name": "A-Deal"}
    defs = {d["key"]: d for d in body["field_defs"]}
    assert defs["Deal_Name"]["lookup_module"] == "Deals"
    assert defs["Owner"]["lookup_module"] is None
    assert body["links"] == {
        "Deal_Name": {"kind": "record", "entity": "deals", "module": None,
                      "id": deal_a, "name": "A-Deal"},
        "Product_Name": {"kind": "module", "entity": None, "module": "Products",
                         "id": product_a, "name": "A-Widget"},
    }


# ── The list contract ────────────────────────────────────────────────────────


async def test_the_record_list_searches_sorts_and_pages(ns) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    _record(ns, a, "Products", "p1", "Alpha widget", modified="2026-10-01")
    _record(ns, a, "Products", "p2", "Beta gadget", modified="2026-10-03")
    _record(ns, a, "Products", "p3", "Gamma widget", modified="2026-10-02")
    _field_def(ns, a, "Products", "Unit_Price", "currency", position=2)
    _field_def(ns, a, "Products", "Name", "text", position=1)

    newest = await _get(ns, a, "/crm/modules/Products/records")
    assert [r["name"] for r in newest.json()["rows"]] == [
        "Beta gadget", "Gamma widget", "Alpha widget",
    ]
    # The field definitions travel with the list, for its Columns control.
    assert [d["key"] for d in newest.json()["field_defs"]] == ["Name", "Unit_Price"]
    found = await _get(ns, a, "/crm/modules/Products/records?q=widget&sort=name&dir=asc")
    assert found.status_code == 200, found.text
    assert [r["name"] for r in found.json()["rows"]] == ["Alpha widget", "Gamma widget"]
    assert found.json()["total"] == 2
    second = await _get(
        ns, a, "/crm/modules/Products/records?sort=name&dir=asc&page=2&page_size=1",
    )
    assert [r["name"] for r in second.json()["rows"]] == ["Beta gadget"]
    assert second.json()["total"] == 3
    refused = await _get(ns, a, "/crm/modules/Products/records?sort=fields")
    assert refused.status_code == 422


# ── The field filters (CRM-U9) ───────────────────────────────────────────────


async def test_the_record_list_filters_by_field_owner_and_updated_day(ns) -> None:
    """``custom.<key>``, ``owner`` and ``updated_*`` narrow a module list.

    Fence for CRM-U9. A key with no definition is ignored. A bad day is 422.
    """
    a = ns.org_a
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    _field_def(ns, a, "Products", "Colour", "select", position=1)
    _field_def(ns, a, "Products", "Tags", "multiselect", position=2)
    _field_def(ns, a, "Products", "Active", "boolean", position=3)
    _record(ns, a, "Products", "p1", "Red one",
            {"Colour": "Red", "Tags": ["x", "y"], "Active": True},
            modified="2026-10-01")
    _record(ns, a, "Products", "p2", "Blue one",
            {"Colour": "Blue", "Tags": ["y"], "Active": False},
            modified="2026-10-05")
    _record(ns, a, "Products", "p3", "Green one",
            {"Colour": "Green", "Tags": [], "Active": True},
            modified="2026-10-09")
    base = "/crm/modules/Products/records?sort=name&dir=asc"

    async def names(query: str) -> list[str]:
        res = await _get(ns, a, f"{base}&{query}")
        assert res.status_code == 200, res.text
        return [r["name"] for r in res.json()["rows"]]

    assert await names("custom.Colour=Red") == ["Red one"]
    assert await names("custom.Colour.in=Red,Blue") == ["Blue one", "Red one"]
    assert await names("custom.Tags.in=x") == ["Red one"]
    assert await names("custom.Active=true") == ["Green one", "Red one"]
    assert await names("updated_from=2026-10-04&updated_to=2026-10-06") == ["Blue one"]
    assert await names("owner=me") == ["Blue one", "Green one", "Red one"]
    assert await names("owner=nobody@crm-q6b-r8.test") == []
    # An unknown key is ignored. A SQL fragment is only a name that is not known.
    assert len(await names("custom.Nope=1")) == 3
    assert len(await names("custom.Colour'%3B--=1")) == 3
    bad = await _get(ns, a, f"{base}&updated_from=yesterday")
    assert bad.status_code == 422
    # Org B reads none of it, with the same filter.
    _seed_org(ns, ns.org_b, mode="mirror", modules=PROFILE_A)
    _field_def(ns, ns.org_b, "Products", "Colour", "select")
    _record(ns, ns.org_b, "Products", "p1", "B red", {"Colour": "Red"})
    other = await _get(ns, ns.org_b, f"{base}&custom.Colour=Red")
    assert [r["name"] for r in other.json()["rows"]] == ["B red"]
    assert "Red one" not in other.text
