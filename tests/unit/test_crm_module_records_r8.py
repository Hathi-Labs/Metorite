"""R8 — the generic record store of the mirror, against a real Postgres
(WS-53 CRM-Q6a, ``crm_platform.md`` §18.7).

Migration 251 adds ``crm_module_records``. The sync reads each generic module
of the source profile (Products, Quotes and the other standard modules, and
each custom module) after the known entities and the CRM-Q5 steps. It writes
through the module door in ``routes/crm/core.py`` and nothing else.

What this proves, with two organizations and the non-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), one test for each "Done when"
item of §18.7:

1. 2 Products and 1 record of a custom module ``Install_Base`` land as 3 rows,
   keyed by ``(org, 'zoho', module, ext_id)``;
2. ``crm_field_defs`` holds the fields under ``module:Products`` and
   ``module:Install_Base``;
3. a second sync with no change adds no row;
4. a changed record updates ``fields`` and ``ext_modified_at``, and a deleted
   record goes;
5. org B reads none of org A's rows through the app role;
6. a scope error on one module marks only that module unavailable, the other
   modules sync, and the run is ``ok``;
7. a linking module and a subform never get a row;
8. a run that stops at the reserve continues in the next run, with no
   duplicate row;
9. ``GET /crm/sync/health`` lists each module with ``zoho_count`` and ``ours``.

It also shows the owner gate: a connection made before this slice lacks the
new scopes, so each module is unavailable, with no call, and the run is
``ok``.

``tests/unit/test_crm_module_records.py`` holds the rules that need no
database. The fake Zoho is the one of the CRM-Q1 and CRM-Q5 suites.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_crm_pipelines_r8 import Q1Zoho, _req
from tests.unit.test_crm_pipelines_r8 import _admin as _admin_of
from tests.unit.test_crm_q5_lost_data_r8 import Q5Zoho
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    MEMBER,
    OUTSIDER,
    SENTINEL,
    _assert_non_priv,
    _connect,
    _count,
    _q,
    _the_loop_is_not_stopping,
    ns,
    stamp,
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

#: The modules of the fake account: two known, two generic, and four that the
#: sync must never read (a linking module, a subform, a module the API does
#: not support, and a standard module that is not on the list).
MODULES: list[dict[str, Any]] = [
    {"api_name": "Deals", "module_name": "Potentials", "plural_label": "Deals",
     "generated_type": "default", "api_supported": True},
    {"api_name": "Leads", "module_name": "Leads", "plural_label": "Leads",
     "generated_type": "default", "api_supported": True},
    {"api_name": "Products", "module_name": "Products", "plural_label": "Products",
     "generated_type": "default", "api_supported": True},
    {"api_name": "Install_Base", "module_name": "CustomModule3",
     "plural_label": "Install Base", "generated_type": "custom", "api_supported": True},
    {"api_name": "Deals_X_Products", "module_name": "Deals_X_Products",
     "plural_label": "Deal Products", "generated_type": "linking", "api_supported": True},
    {"api_name": "Line_Items", "module_name": "Line_Items",
     "plural_label": "Line Items", "generated_type": "subform", "api_supported": True},
    {"api_name": "Hidden_Things", "module_name": "Hidden_Things",
     "plural_label": "Hidden", "generated_type": "custom", "api_supported": False},
    {"api_name": "Forecasts", "module_name": "Forecasts", "plural_label": "Forecasts",
     "generated_type": "default", "api_supported": True},
]

#: The ``settings/fields`` of the two generic modules.
MODULE_FIELDS: dict[str, list[dict[str, Any]]] = {
    "Products": [
        {"api_name": "Product_Name", "field_label": "Product Name",
         "data_type": "text", "display_field": True, "sequence_number": 1},
        {"api_name": "Unit_Price", "field_label": "Unit Price",
         "data_type": "currency", "sequence_number": 2},
        {"api_name": "Vendor_Name", "field_label": "Vendor", "data_type": "lookup",
         "lookup": {"module": {"api_name": "Vendors", "id": "9"}},
         "sequence_number": 3},
    ],
    "Install_Base": [
        {"api_name": "Name", "field_label": "Install", "data_type": "text",
         "display_field": True, "sequence_number": 1},
        {"api_name": "Account", "field_label": "Account", "data_type": "lookup",
         "lookup": {"module": "Accounts"}, "sequence_number": 2},
    ],
}

#: The modules that must never be read.
NEVER_READ = ("Deals_X_Products", "Line_Items", "Hidden_Things", "Forecasts")


class ModZoho(Q5Zoho, Q1Zoho):
    """The fake Zoho of CRM-Q1 (discovery) and CRM-Q5 (faults), plus a module
    list and the field definitions of the generic modules."""

    def __init__(self, tag: str, api_host: str, accounts_host: str) -> None:
        super().__init__(tag, api_host, accounts_host)
        self.modules = [dict(m) for m in MODULES]
        #: Discovery cannot read the module list (a section that fails).
        self.modules_error = False
        #: Modules whose count endpoint answers 500.
        self.count_faults: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v8/settings/modules":
            self.requests.append(request)
            if self.modules_error:
                return httpx.Response(500, json={"code": "INTERNAL_ERROR"})
            return httpx.Response(200, json={"modules": self.modules})
        count = re.fullmatch(r"/crm/v8/(\w+)/actions/count", path)
        if count and count.group(1) in self.count_faults:
            self.requests.append(request)
            return httpx.Response(500, json={"code": "INTERNAL_ERROR"})
        return super().handle(request)

    def changed_reads(self, module: str) -> int:
        """Reads of the changed list only (no deleted list, no count)."""
        return sum(1 for r in self.requests if r.url.path == f"/crm/v2/{module}")

    def count_reads(self, module: str) -> int:
        return sum(1 for r in self.requests
                   if r.url.path == f"/crm/v8/{module}/actions/count")

    def _fields(self, module: str) -> list[dict[str, Any]]:
        if module in MODULE_FIELDS:
            return [dict(f) for f in MODULE_FIELDS[module]]
        return super()._fields(module)

    def reads_of(self, module: str) -> list[httpx.Request]:
        return [r for r in self.requests
                if re.fullmatch(rf"/crm/v2/{module}(/deleted)?", r.url.path)]


@pytest.fixture
def mz(ns, monkeypatch):
    """Two fake Zoho tenants under every provider call, and the CRM-Q1 and
    CRM-Q6a tables cleaned after the test."""
    from gateway.crm_sync import engine

    a = ModZoho("A", "www.zohoapis.in", "accounts.zoho.in")
    b = ModZoho("B", "www.zohoapis.eu", "accounts.zoho.eu")

    def transport(request: httpx.Request) -> httpx.Response:
        for z in (a, b):
            if request.url.host in (z.api_host, z.accounts_host):
                return z.handle(request)
        raise AssertionError(f"a call to an unknown host {request.url.host}")

    monkeypatch.setattr(engine, "TRANSPORT", httpx.MockTransport(transport))
    yield ns, a, b
    with ns.admin_engine.begin() as c:
        for org in (ns.org_a, ns.org_b):
            for table in (
                "crm_module_records", "crm_pipeline_stages", "crm_pipelines",
                "crm_source_profiles",
            ):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


async def _connect_all(p: Any, org: str, z: ModZoho) -> str:
    """A connection made after this slice: it holds every scope of the consent."""
    from gateway.routes.crm.connection import ZOHO_READ_SCOPES

    row_id = await _connect(p, org, z)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET scopes = :s "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {"s": list(ZOHO_READ_SCOPES), "o": org})
    return row_id


def _seed(z: ModZoho) -> None:
    """The known entities the engine needs, then 2 Products and 1 Install_Base."""
    z.add("Accounts", "a1", 1, Account_Name=f"{z.tag}-Company")
    z.add("Products", "p1", 10, Product_Name=f"{z.tag}-Widget", Unit_Price=12.5,
          Vendor_Name={"id": "v1", "name": "Vendor One"},
          Owner={"id": "u1", "name": "Member", "email": MEMBER}, **{"$approved": True})
    z.add("Products", "p2", 11, Product_Name=f"{z.tag}-Gadget", Unit_Price=99,
          Owner={"id": "u2", "name": "Outside", "email": OUTSIDER})
    z.add("Install_Base", "i1", 12, Name=f"{z.tag}-Install",
          Account={"id": "a1", "name": "Acme"})
    for module in NEVER_READ:
        z.add(module, f"{module}-1", 13, Name="never")


def _records(p: Any, org: str) -> dict[tuple[str, str], Any]:
    rows = _q(p, "SELECT module, ext_source, ext_id, name, owner_email, fields, "
                 "ext_url, ext_created_at, ext_modified_at, updated_at, id "
                 "FROM crm_module_records WHERE organization_id = CAST(:o AS uuid)",
              o=org)
    return {(r[0], r[2]): r for r in rows}


async def _run(org: str, **kw: Any) -> Any:
    from gateway.crm_sync import engine

    return await engine.run_org(org, **kw)


def _grant_all_scopes(p: Any, org: str) -> None:
    """The connection row gains every scope of the consent, and keeps its id."""
    from gateway.routes.crm.connection import ZOHO_READ_SCOPES

    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET scopes = :s "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {"s": list(ZOHO_READ_SCOPES), "o": org})


def _age_module_reads(p: Any, org: str, hours: int = 2) -> None:
    """Move the last read of each generic module's changed list back."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = jsonb_set(cursor_state, "
            "'{checked_at}', to_jsonb((now() - make_interval(hours => :h))::text)) "
            "WHERE organization_id = CAST(:o AS uuid) AND module LIKE 'zoho:module:%' "
            "AND module NOT LIKE '%deleted' AND cursor_state ? 'checked_at'"),
            {"o": org, "h": hours})


def _add_member(p: Any, org: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'executive', 'active', CAST(:o AS uuid))"),
            {"e": MEMBER, "o": org})


# ── 1. The records land ──────────────────────────────────────────────────────


async def test_two_products_and_one_custom_record_land_as_three_rows(mz) -> None:
    p, a, _b = mz
    _seed(a)
    _add_member(p, p.org_a)
    await _connect_all(p, p.org_a, a)

    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    rows = _records(p, p.org_a)
    assert set(rows) == {("Products", "p1"), ("Products", "p2"), ("Install_Base", "i1")}
    assert {r[1] for r in rows.values()} == {"zoho"}
    p1, p2, i1 = rows["Products", "p1"], rows["Products", "p2"], rows["Install_Base", "i1"]
    # The name is the display field. The owner is kept only for a member.
    assert (p1[3], p1[4]) == ("A-Widget", MEMBER)
    assert (p2[3], p2[4]) == ("A-Gadget", None)
    assert i1[3] == "A-Install"
    # Each value keyed as Zoho names it, a lookup as Zoho sends it, no $ key.
    assert p1[5]["Unit_Price"] == 12.5
    assert p1[5]["Vendor_Name"] == {"id": "v1", "name": "Vendor One"}
    assert "id" not in p1[5] and "$approved" not in p1[5]
    assert i1[5]["Account"] == {"id": "a1", "name": "Acme"}
    # The deep link uses the tab of the module, and the provider's times land.
    assert p1[6] == "https://crm.zoho.in/crm/orgA/tab/Products/p1"
    assert i1[6] == "https://crm.zoho.in/crm/orgA/tab/CustomModule3/i1"
    assert p1[8] == datetime.fromisoformat(stamp(10)).astimezone(UTC)
    assert p1[7] == datetime.fromisoformat(stamp(0)).astimezone(UTC)
    # Each module reports under its own key, and the known entities still sync.
    assert result.counts["module:Products"]["upserted"] == 2
    assert result.counts["module:Install_Base"]["upserted"] == 1
    assert _count(p, "crm_companies", p.org_a) == 1
    # The profile marks the two generic modules mirrored.
    profile = _q(p, "SELECT profile FROM crm_source_profiles WHERE organization_id = "
                    "CAST(:o AS uuid)", o=p.org_a)[0][0]
    by_name = {m["api_name"]: m for m in profile["modules"]}
    assert by_name["Products"]["mirrored"] is True
    assert by_name["Install_Base"]["mirrored"] is True
    assert by_name["Products"]["display_field"] == "Product_Name"


# ── 2. The field definitions ─────────────────────────────────────────────────


async def test_the_fields_of_each_module_land_in_crm_field_defs(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    rows = _q(p, "SELECT entity, key, type, options, source FROM crm_field_defs "
                 "WHERE organization_id = CAST(:o AS uuid) AND entity LIKE 'module:%' "
                 "ORDER BY entity, position", o=p.org_a)
    got = {(r[0], r[1]): r for r in rows}
    assert set(got) == {
        ("module:Products", "Product_Name"), ("module:Products", "Unit_Price"),
        ("module:Products", "Vendor_Name"), ("module:Install_Base", "Name"),
        ("module:Install_Base", "Account"),
    }
    assert got["module:Products", "Unit_Price"][2] == "currency"
    # The field definition names the target module of a lookup, in both forms.
    assert got["module:Products", "Vendor_Name"][3] == {"lookup_module": "Vendors"}
    assert got["module:Install_Base", "Account"][3] == {"lookup_module": "Accounts"}
    assert {r[4] for r in rows} == {"zoho"}
    # The four known entities keep their own names.
    known = {r[0] for r in _q(p, "SELECT DISTINCT entity FROM crm_field_defs WHERE "
                                 "organization_id = CAST(:o AS uuid) AND entity NOT "
                                 "LIKE 'module:%'", o=p.org_a)}
    assert known <= {"lead", "contact", "company", "deal"}


# ── 3. A replay adds nothing ─────────────────────────────────────────────────


async def test_a_second_sync_with_no_change_adds_no_row(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    before = {k: (r[10], r[9]) for k, r in _records(p, p.org_a).items()}

    again = await _run(p.org_a)
    assert (again.status, again.kind) == ("ok", "incremental")
    after = {k: (r[10], r[9]) for k, r in _records(p, p.org_a).items()}
    assert after == before, "no new row, and no row rewritten"
    assert _count(p, "crm_module_records", p.org_a) == 3
    assert again.counts["module:Products"]["upserted"] == 0

    # A full re-read of the same records (the module cursors are gone) still
    # adds no row and rewrites none: the door keys on the record and skips a
    # write that changes nothing.
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_sync_cursors WHERE organization_id = "
                       "CAST(:o AS uuid) AND module LIKE 'zoho:module:%'"), {"o": p.org_a})
    full = await _run(p.org_a)
    assert full.status == "ok"
    assert full.counts["module:Products"]["seen"] == 2
    assert full.counts["module:Products"]["upserted"] == 0
    assert full.counts["module:Products"]["failed"] == 0
    assert {k: (r[10], r[9]) for k, r in _records(p, p.org_a).items()} == before


# ── 4. A change and a delete ─────────────────────────────────────────────────


async def test_a_changed_record_updates_and_a_deleted_record_goes(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"

    product = next(r for r in a.data["Products"] if r["id"] == "p1")
    product.update(Unit_Price=15, Modified_Time=stamp(40), Product_Name="A-Widget 2")
    a.drop("Products", "p2", tombstone=True)
    _age_module_reads(p, p.org_a)  # an idle module reads once an hour
    second = await _run(p.org_a)
    assert (second.status, second.kind) == ("ok", "incremental")
    rows = _records(p, p.org_a)
    assert ("Products", "p2") not in rows
    p1 = rows["Products", "p1"]
    assert p1[5]["Unit_Price"] == 15 and p1[3] == "A-Widget 2"
    assert p1[8] == datetime.fromisoformat(stamp(40)).astimezone(UTC)
    assert second.counts["module:Products"]["deleted"] == 1
    assert ("Install_Base", "i1") in rows


# ── 5. Org isolation through the app role ────────────────────────────────────


async def test_org_b_reads_none_of_org_a_rows_through_the_app_role(mz, app_engine) -> None:
    p, a, b = mz
    _assert_non_priv(app_engine)
    for z in (a, b):
        _seed(z)  # the SAME Zoho ids in both tenants, with different names
    await _connect_all(p, p.org_a, a)
    await _connect_all(p, p.org_b, b)
    for org in (p.org_a, p.org_b):
        assert (await _run(org)).status == "ok"

    def names_as(org: str | None) -> list[str]:
        # A fresh session each time, so no tenant setting carries over.
        fresh = create_engine(p.app_url, poolclass=NullPool)
        try:
            with fresh.begin() as c:
                if org is not None:
                    c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                              {"o": org})
                return sorted(r[0] for r in c.execute(text(
                    "SELECT name FROM crm_module_records")).all())
        finally:
            fresh.dispose()

    assert names_as(p.org_a) == ["A-Gadget", "A-Install", "A-Widget"]
    assert names_as(p.org_b) == ["B-Gadget", "B-Install", "B-Widget"]
    assert names_as(None) == [], "no tenant bound reads nothing"
    # WITH CHECK: a session bound to B cannot write a row stamped for A.
    with app_engine.connect() as c, pytest.raises(DBAPIError):
        c.execute(text("SELECT set_config('app.tenant_id', :o, false)"), {"o": p.org_b})
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id) VALUES (CAST(:o AS uuid), 'Products', 'zoho', 'x')"), {"o": p.org_a})
    # A delete upstream in A leaves B alone.
    a.drop("Install_Base", "i1", tombstone=True)
    assert (await _run(p.org_a)).status == "ok"
    assert ("Install_Base", "i1") not in _records(p, p.org_a)
    assert ("Install_Base", "i1") in _records(p, p.org_b)


# ── 6. A scope error on one module ───────────────────────────────────────────


async def test_a_scope_error_on_one_module_marks_only_that_module(mz) -> None:
    p, a, _b = mz
    _seed(a)
    a.scope_denied.add("Products")
    await _connect_all(p, p.org_a, a)

    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    assert result.counts["module:Products"]["unavailable"] == 1
    assert result.counts["module:Install_Base"]["unavailable"] == 0
    assert set(_records(p, p.org_a)) == {("Install_Base", "i1")}
    status = _q(p, "SELECT status FROM integration_connections WHERE "
                   "organization_id = CAST(:o AS uuid)", o=p.org_a)
    assert [r[0] for r in status] == ["connected"], "a missing scope is not a dead token"
    # The module is not asked again inside the hour.
    asked = len(a.reads_of("Products"))
    second = await _run(p.org_a)
    assert second.status == "ok" and second.counts["module:Products"]["unavailable"] == 1
    assert len(a.reads_of("Products")) == asked


@pytest.mark.parametrize("status,body", [
    (400, {"code": "INVALID_MODULE", "message": "the module name given seems to be invalid"}),
    (500, {"code": "INTERNAL_ERROR", "message": "boom"}),
])
async def test_a_400_or_500_on_one_module_marks_it_unavailable(mz, status, body) -> None:
    p, a, _b = mz
    _seed(a)
    a.faults["Install_Base"] = (status, body)
    await _connect_all(p, p.org_a, a)
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    assert result.counts["module:Install_Base"]["unavailable"] == 1
    assert {k[0] for k in _records(p, p.org_a)} == {"Products"}


async def test_a_plain_401_on_a_module_still_fails_the_run(mz) -> None:
    p, a, _b = mz
    _seed(a)
    a.faults["Products"] = (401, {"code": "INVALID_TOKEN", "message": "invalid oauth token"})
    await _connect_all(p, p.org_a, a)
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("failed", "source_error")


async def test_a_connection_without_the_new_scopes_reads_no_module(mz) -> None:
    """The owner gate: a connection made before CRM-Q6a must reconnect. Until
    then each module is unavailable, no call is made, and the run is ok."""
    p, a, _b = mz
    _seed(a)
    await _connect(p, p.org_a, a)  # scopes from before this slice
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    assert result.counts["module:Products"]["unavailable"] == 1
    assert result.counts["module:Install_Base"]["unavailable"] == 1
    assert a.reads_of("Products") == [] and a.reads_of("Install_Base") == []
    assert _count(p, "crm_module_records", p.org_a) == 0
    profile = _q(p, "SELECT profile FROM crm_source_profiles WHERE organization_id = "
                    "CAST(:o AS uuid)", o=p.org_a)[0][0]
    products = next(m for m in profile["modules"] if m["api_name"] == "Products")
    assert products["mirrored"] is False
    assert products["scope"] == "ZohoCRM.modules.products.READ"
    # The admin connects again and grants the scopes. The connection row and
    # the stored profile stay, and no clock moves: the NEXT run must see that
    # the scope set changed, discover again, and mirror the modules.
    _grant_all_scopes(p, p.org_a)
    healed = await _run(p.org_a)
    assert healed.status == "ok"
    assert _count(p, "crm_module_records", p.org_a) == 3
    names = {r[3] for r in _records(p, p.org_a).values()}
    assert names == {"A-Widget", "A-Gadget", "A-Install"}, "the display field was read"
    assert _count(p, "crm_field_defs", p.org_a, "entity = 'module:Products'") == 3


# ── 7. A linking module and a subform ────────────────────────────────────────


async def test_a_linking_module_and_a_subform_never_get_a_row(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    first = await _run(p.org_a)
    second = await _run(p.org_a)
    assert first.status == second.status == "ok"
    for module in NEVER_READ:
        assert a.reads_of(module) == [], module
        assert f"module:{module}" not in second.counts
    assert {k[0] for k in _records(p, p.org_a)} == {"Products", "Install_Base"}
    profile = _q(p, "SELECT profile FROM crm_source_profiles WHERE organization_id = "
                    "CAST(:o AS uuid)", o=p.org_a)[0][0]
    by_name = {m["api_name"]: m for m in profile["modules"]}
    for module in NEVER_READ:
        assert by_name[module]["mirrored"] is False and "generic" not in by_name[module]


# ── 8. The reserve ───────────────────────────────────────────────────────────


async def test_a_run_that_stops_at_the_reserve_continues_with_no_duplicate(
    mz, monkeypatch,
) -> None:
    from gateway.crm_sync import engine

    p, a, _b = mz
    _seed(a)
    for i in range(3, 6):  # 5 Products: three pages of 2
        a.add("Products", f"p{i}", 10 + i, Product_Name=f"Item {i}")
    await _connect_all(p, p.org_a, a)

    def low_after_first_page(_reads: int) -> None:
        # The first Products page is read and paid for. From now on the run
        # has only the reserve left, so the step must stop before the next page.
        monkeypatch.setattr(engine, "MODULES_RESERVE", 10**9)
        a.on_read.pop("Products", None)

    a.on_read["Products"] = low_after_first_page
    first = await _run(p.org_a)
    assert (first.status, first.error) == ("ok", None)
    assert _count(p, "crm_module_records", p.org_a, "module = 'Products'") == 2
    assert _count(p, "crm_module_records", p.org_a, "module = 'Install_Base'") == 0
    cursor = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                   "CAST(:o AS uuid) AND module = 'zoho:module:Products'", o=p.org_a)
    assert cursor and cursor[0][0]["in_progress"] is True

    monkeypatch.setattr(engine, "MODULES_RESERVE", 20)
    reads_before = len(a.reads_of("Products"))
    second = await _run(p.org_a)
    assert second.status == "ok"
    ids = [r[0] for r in _q(p, "SELECT ext_id FROM crm_module_records WHERE "
                               "organization_id = CAST(:o AS uuid) AND module = "
                               "'Products'", o=p.org_a)]
    assert sorted(ids) == ["p1", "p2", "p3", "p4", "p5"] and len(ids) == len(set(ids))
    assert _count(p, "crm_module_records", p.org_a, "module = 'Install_Base'") == 1
    # The second run went on from the saved cursor, not from the start.
    resumed = a.reads_of("Products")[reads_before]
    assert resumed.headers.get("If-Modified-Since") is not None


# ── 9. Sync health ───────────────────────────────────────────────────────────


async def test_sync_health_lists_each_module_with_zoho_count_and_ours(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    got = await _req(_admin_of(p.org_a), "GET", "/crm/sync/health")
    assert got.status_code == 200, got.text
    by_key = {e["key"]: e for e in got.json()["entities"]}
    assert by_key["module:Products"]["zoho_count"] == 2
    assert by_key["module:Products"]["ours"] == 2
    assert by_key["module:Products"]["last_run"]["upserted"] == 2
    assert by_key["module:Install_Base"]["zoho_count"] == 1
    assert by_key["module:Install_Base"]["ours"] == 1
    mirrored = {m["api_name"] for m in got.json()["modules"] if m["mirrored"]}
    assert {"Products", "Install_Base"} <= mirrored
    assert not mirrored & set(NEVER_READ)
    # An incremental run keeps the module counts from the cursor row.
    assert (await _run(p.org_a)).status == "ok"
    later = {e["key"]: e for e in (await _req(
        _admin_of(p.org_a), "GET", "/crm/sync/health")).json()["entities"]}
    assert later["module:Products"]["zoho_count"] == 2
    assert later["module:Products"]["ours"] == 2
    assert SENTINEL not in got.text


async def test_sync_health_shows_a_module_that_waits_for_a_reconnect(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect(p, p.org_a, a)  # scopes from before this slice
    assert (await _run(p.org_a)).status == "ok"
    body = (await _req(_admin_of(p.org_a), "GET", "/crm/sync/health")).json()
    by_key = {e["key"]: e for e in body["entities"]}
    assert by_key["module:Products"]["last_run"]["unavailable"] == 1
    assert by_key["module:Products"]["ours"] == 0


# ── The migration ────────────────────────────────────────────────────────────


def test_the_module_records_migration_is_found_by_content_and_replays(mz) -> None:
    p, _a, _b = mz
    sql = re.compile(r"CREATE TABLE IF NOT EXISTS crm_module_records")
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if f.name != "schema.generated.sql"
             and sql.search(f.read_text(encoding="utf-8"))]
    assert len(found) == 1
    # The ladder applied it once already. A second run must change nothing.
    with p.admin_engine.begin() as c:
        _exec_file(c, str(found[0]))
    forced = _q(p, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                   "WHERE oid = to_regclass('crm_module_records')")
    assert forced == [(True, True)]
    policy = _q(p, "SELECT qual IS NOT NULL, with_check IS NOT NULL FROM pg_policies "
                   "WHERE tablename = 'crm_module_records'")
    assert policy and all(row == (True, True) for row in policy)


# ── Review fixes (2026-10-12) ────────────────────────────────────────────────


async def test_a_revoked_scope_stops_a_module_when_discovery_keeps_the_old_list(mz) -> None:
    """The run decides availability from the connection's scopes at run time.
    Here discovery cannot read the module list, so the profile keeps the old
    ``mirrored`` flags. The module must still count as unavailable."""
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET scopes = ARRAY['ZohoCRM.modules.leads.READ'] "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    a.modules_error = True
    _age_module_reads(p, p.org_a)
    before = a.changed_reads("Products")
    second = await _run(p.org_a)
    assert second.status == "ok"
    assert second.counts["module:Products"]["unavailable"] == 1
    assert second.counts["module:Install_Base"]["unavailable"] == 1
    assert a.changed_reads("Products") == before, "no call for a module without its scope"


async def test_a_module_that_hides_modified_time_is_read_again_after_the_interval(
    mz,
) -> None:
    """A module with no ``Modified_Time`` pages by offset and has no cursor to
    move. It is read again in full once each ``RECONCILE_EVERY``, and a full
    re-read that finds no change writes nothing."""
    p, a, _b = mz
    _seed(a)
    a.hide.add("Install_Base")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    first = _records(p, p.org_a)["Install_Base", "i1"]

    a.add("Install_Base", "i2", 30, Name="A-Install 2")
    _age_module_reads(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert ("Install_Base", "i2") not in _records(p, p.org_a), "not inside the interval"

    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET last_reconciled_at = now() - interval '25 hours' "
            "WHERE organization_id = CAST(:o AS uuid) "
            "AND module = 'zoho:module:Install_Base'"), {"o": p.org_a})
    again = await _run(p.org_a)
    assert again.status == "ok"
    rows = _records(p, p.org_a)
    assert rows["Install_Base", "i2"][3] == "A-Install 2"
    assert rows["Install_Base", "i1"][9] == first[9], "the unchanged record is not rewritten"
    assert again.counts["module:Install_Base"]["upserted"] == 1


async def test_a_first_import_over_two_runs_counts_as_the_full_read(
    mz, monkeypatch,
) -> None:
    """A hidden-``Modified_Time`` module whose first import stops at the
    reserve finishes on the next run. That finish is a full read, so the run
    after it must not read every page again at once."""
    from gateway.crm_sync import engine

    p, a, _b = mz
    _seed(a)
    a.hide.add("Install_Base")
    for i in range(2, 5):  # i1 from the seed plus three more: two pages of 2
        a.add("Install_Base", f"i{i}", 30 + i, Name=f"A-Install {i}")
    await _connect_all(p, p.org_a, a)

    def low_after_first_page(_reads: int) -> None:
        monkeypatch.setattr(engine, "MODULES_RESERVE", 10**9)
        a.on_read.pop("Install_Base", None)

    a.on_read["Install_Base"] = low_after_first_page
    assert (await _run(p.org_a)).status == "ok"
    monkeypatch.setattr(engine, "MODULES_RESERVE", 20)
    assert (await _run(p.org_a)).status == "ok"
    assert _count(p, "crm_module_records", p.org_a, "module = 'Install_Base'") == 4
    done = _q(p, "SELECT initial_done_at, last_reconciled_at FROM crm_sync_cursors "
                 "WHERE organization_id = CAST(:o AS uuid) "
                 "AND module = 'zoho:module:Install_Base'", o=p.org_a)
    assert done and done[0][0] is not None
    assert done[0][1] is not None, "the finished first import is the full read"


async def test_a_failed_module_count_is_not_asked_again_on_every_run(mz) -> None:
    p, a, _b = mz
    _seed(a)
    a.count_faults.add("Products")
    await _connect_all(p, p.org_a, a)
    for _ in range(3):
        assert (await _run(p.org_a)).status == "ok"
    assert a.count_reads("Products") == 1, "one credit a day, not one a run"
    assert a.count_reads("Install_Base") == 1


async def test_an_idle_module_reads_its_changed_list_at_most_once_an_hour(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    after_import = a.changed_reads("Products")
    for _ in range(2):
        assert (await _run(p.org_a)).status == "ok"
    assert a.changed_reads("Products") == after_import, "no read inside the hour"
    _age_module_reads(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    assert a.changed_reads("Products") == after_import + 1, "one read after the hour"


async def test_module_field_reads_stop_at_the_reserve_and_wait_for_the_next_discovery(
    mz, monkeypatch,
) -> None:
    from gateway.crm_sync import engine

    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    monkeypatch.setattr(engine, "MODULES_RESERVE", 10**9)  # only the reserve is left
    assert (await _run(p.org_a)).status == "ok"
    fields = [r.url.params.get("module") for r in a.requests
              if r.url.path == "/crm/v8/settings/fields"]
    assert "Products" not in fields and "Install_Base" not in fields
    assert _count(p, "crm_field_defs", p.org_a, "entity LIKE 'module:%'") == 0
    assert _count(p, "crm_module_records", p.org_a) == 0, "no record without its fields"

    # The next discovery reads them. A profile with fields still to read is due
    # again after an hour, not after a day.
    monkeypatch.setattr(engine, "MODULES_RESERVE", 20)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_source_profiles SET fetched_at = now() - "
                       "interval '2 hours' WHERE organization_id = CAST(:o AS uuid)"),
                  {"o": p.org_a})
    assert (await _run(p.org_a)).status == "ok"
    assert _count(p, "crm_field_defs", p.org_a, "entity LIKE 'module:%'") == 5
    names = {r[3] for r in _records(p, p.org_a).values()}
    assert names == {"A-Widget", "A-Gadget", "A-Install"}
