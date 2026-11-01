"""R8 — custom modules stay and become editable in Native mode, against a real
Postgres (WS-CRM CRM-Z10d, ``crm_platform.md`` §3.4).

``PATCH /crm/modules/{module}/records/{id}`` runs through the real ``/crm``
router as the non-privileged role of the phase-4 rehearsal. What this proves:

1. a Mirror org gets 409 ``mirrored_record``, and the record stays as it was;
2. after the mode flips to Native the same PATCH gives 200, and the module
   list count is the count of before (a cutover keeps every record, D133.2);
3. an unknown key, a wrong type and a non-member owner give 422, and a record
   of another org gives 404;
4. ``fields`` merges per key, a ``null`` removes a key, and a lookup keeps
   ``{id, name}``;
5. ``GET /crm/modules`` and the AI catalogue list the modules in Native mode
   while records exist, and list none when no record exists;
6. a Native write sets no ``zoho_dirty`` (the table has no such column), and
   the sync door still overwrites a record in Mirror mode.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_modules_routes_r8 import (  # noqa: F401
    PROFILE_A,
    _field_def,
    _get,
    _module,
    _record,
    _seed_org,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

REP = "rep@crm-q6b-r8.test"
MEMBER = "new.owner@crm-z10d-r8.test"


async def _patch(
    p: Any, org: str, path: str, body: dict[str, Any], *, engine_scope: bool = True,
) -> Any:
    """One PATCH through the real router. The tenant comes from the user.

    ``engine_scope=False`` is for a caller that already holds ONE
    ``tenant_engine_scope`` around several concurrent PATCHes. See
    ``test_two_concurrent_patches_of_different_keys_both_land``.
    """
    from contextlib import nullcontext

    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    from tests.unit.test_crm_modules_routes_r8 import _member

    user = _member(org)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    engine = (
        tenant_engine_scope(p.app_url.render_as_string(hide_password=False))
        if engine_scope else nullcontext()
    )
    try:
        async with (
            engine,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            return await client.patch(path, json=body)
    finally:
        release_tenant(scope)


def _set_mode(p: Any, org: str, mode: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_settings SET mode = :m WHERE organization_id = CAST(:o AS uuid)"),
            {"m": mode, "o": org})


def _add_member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org})


def _row(p: Any, record: str) -> Any:
    with p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT name, owner_email, fields, ext_source, ext_id FROM crm_module_records "
            "WHERE id = CAST(:i AS uuid)"), {"i": record}).first()


def _count(p: Any, org: str, module: str) -> int:
    with p.admin_engine.begin() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM crm_module_records WHERE module = :m "
            "AND organization_id = CAST(:o AS uuid)"), {"m": module, "o": org}).scalar())


def _seed_install_base(p: Any, org: str, n: int = 3) -> list[str]:
    """A custom module with a text, a number, a lookup and a select field."""
    for key, ftype, target in (
        ("Name", "text", None), ("Site_Count", "number", None),
        ("Vendor", "lookup", "Vendors"), ("Notes", "text", None),
    ):
        _field_def(p, org, "Install_Base", key, ftype, target=target)
    return [
        _record(p, org, "Install_Base", f"ib{i}", f"Site {i}",
                {"Name": f"Site {i}", "Site_Count": i, "Notes": "keep me"})
        for i in range(n)
    ]


# ── 1 and 2. Mirror gives 409, Native gives 200, the count stays ─────────────


async def test_mirror_patch_is_409_and_native_patch_is_200_with_the_same_count(ns) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    ids = _seed_install_base(ns, a, 3)
    _add_member(ns, a, MEMBER)
    path = f"/crm/modules/Install_Base/records/{ids[0]}"
    body = {"name": "Renamed", "owner_email": MEMBER, "fields": {"Site_Count": 42}}

    res = await _patch(ns, a, path, body)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "mirrored_record"
    before = _row(ns, ids[0])
    assert before.name == "Site 0" and before.fields["Site_Count"] == 0

    listed = await _get(ns, a, "/crm/modules")
    assert {m["api_name"]: m["count"] for m in listed.json()}["Install_Base"] == 3

    _set_mode(ns, a, "native")
    res = await _patch(ns, a, path, body)
    assert res.status_code == 200, res.text
    out = res.json()["record"]
    assert out["name"] == "Renamed" and out["owner_email"] == MEMBER
    assert out["fields"]["Site_Count"] == 42
    after = _row(ns, ids[0])
    assert after.name == "Renamed" and after.owner_email == MEMBER
    # The key the body did not name keeps its value.
    assert after.fields["Notes"] == "keep me"
    assert (after.ext_source, after.ext_id) == ("zoho", "ib0")

    listed = await _get(ns, a, "/crm/modules")
    assert {m["api_name"]: m["count"] for m in listed.json()}["Install_Base"] == 3
    assert _count(ns, a, "Install_Base") == 3


# ── 3. The refusals ──────────────────────────────────────────────────────────


async def test_unknown_key_wrong_type_and_non_member_owner_are_422(ns) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="native", modules=PROFILE_A)
    ids = _seed_install_base(ns, a, 1)
    path = f"/crm/modules/Install_Base/records/{ids[0]}"

    res = await _patch(ns, a, path, {"fields": {"Not_A_Field": "x"}})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "field_unknown"

    res = await _patch(ns, a, path, {"fields": {"Site_Count": "many"}})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "field_value"

    res = await _patch(ns, a, path, {"owner_email": "stranger@elsewhere.test"})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "owner_not_member"

    res = await _patch(ns, a, path, {"tenant": "x"})  # a key the body does not take
    assert res.status_code == 422, res.text
    # Nothing was written.
    row = _row(ns, ids[0])
    assert row.name == "Site 0" and row.fields["Site_Count"] == 0


async def test_a_foreign_org_record_and_an_unknown_module_are_404(ns) -> None:
    a, b = ns.org_a, ns.org_b
    _seed_org(ns, a, mode="native", modules=PROFILE_A)
    _seed_org(ns, b, mode="native", modules=PROFILE_A)
    a_ids = _seed_install_base(ns, a, 1)
    b_ids = _seed_install_base(ns, b, 1)

    res = await _patch(ns, b, f"/crm/modules/Install_Base/records/{a_ids[0]}", {"name": "X"})
    assert res.status_code == 404, res.text
    assert _row(ns, a_ids[0]).name == "Site 0"

    res = await _patch(ns, b, f"/crm/modules/Nope/records/{b_ids[0]}", {"name": "X"})
    assert res.status_code == 404, res.text
    assert res.json()["detail"]["code"] == "unknown_module"


# ── 4. The merge ─────────────────────────────────────────────────────────────


async def test_fields_merge_per_key_a_null_removes_and_a_lookup_keeps_id_and_name(ns) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="native", modules=PROFILE_A)
    ids = _seed_install_base(ns, a, 1)
    path = f"/crm/modules/Install_Base/records/{ids[0]}"

    res = await _patch(ns, a, path, {"fields": {
        "Vendor": {"id": "v-77", "name": "Acme"}, "Notes": None,
    }})
    assert res.status_code == 200, res.text
    fields = _row(ns, ids[0]).fields
    assert fields["Vendor"] == {"id": "v-77", "name": "Acme"}
    assert "Notes" not in fields
    assert fields["Site_Count"] == 0 and fields["Name"] == "Site 0"


# ── 4b. NUL and size refusals, the owner rule, concurrent keys ───────────────


@pytest.mark.parametrize("case", [
    "lookup_name_nul", "lookup_id_nul", "text_value_nul", "key_nul",
    "lookup_name_long", "lookup_id_long", "name_nul",
])
async def test_nul_and_over_long_values_are_422_and_write_nothing(ns, case) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="native", modules=PROFILE_A)
    ids = _seed_install_base(ns, a, 1)
    path = f"/crm/modules/Install_Base/records/{ids[0]}"
    before = _row(ns, ids[0])
    bodies = {
        "lookup_name_nul": {"fields": {"Vendor": {"id": "x", "name": "\x00"}}},
        "lookup_id_nul": {"fields": {"Vendor": {"id": "x\x00", "name": "ok"}}},
        "text_value_nul": {"fields": {"Notes": "a\x00b"}},
        "key_nul": {"fields": {"Notes\x00": "v"}},
        "lookup_name_long": {"fields": {"Vendor": {"id": "x", "name": "n" * 501}}},
        "lookup_id_long": {"fields": {"Vendor": {"id": "i" * 65, "name": "ok"}}},
        "name_nul": {"name": "a\x00b"},
    }
    res = await _patch(ns, a, path, bodies[case])
    assert res.status_code == 422, res.text
    after = _row(ns, ids[0])
    assert (after.name, after.owner_email, after.fields) == (
        before.name, before.owner_email, before.fields)


async def test_an_unchanged_non_member_owner_stays_and_a_change_to_one_is_422(ns) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="native", modules=PROFILE_A)
    ids = _seed_install_base(ns, a, 1)  # the seed owner is REP, who is no member
    path = f"/crm/modules/Install_Base/records/{ids[0]}"

    res = await _patch(ns, a, path, {"owner_email": REP, "name": "Kept owner"})
    assert res.status_code == 200, res.text
    assert _row(ns, ids[0]).owner_email == REP

    res = await _patch(ns, a, path, {"owner_email": "stranger@elsewhere.test"})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "owner_not_member"
    assert _row(ns, ids[0]).owner_email == REP


async def test_two_concurrent_patches_of_different_keys_both_land(ns) -> None:
    import asyncio

    a = ns.org_a
    _seed_org(ns, a, mode="native", modules=PROFILE_A)
    ids = _seed_install_base(ns, a, 1)
    path = f"/crm/modules/Install_Base/records/{ids[0]}"

    # 🔴 ONE engine scope around both PATCHes, never one each (task 1332).
    # ``tenant_engine_scope`` saves DATABASE_URL on entry and writes it back on
    # exit. Two scopes in two concurrent tasks exit in the order the PATCHes
    # finish. When the second one in finished last, it wrote back the DSN of
    # this module's dedicated database, and the module then dropped that
    # database. Every later engine in the process pointed at a database that
    # did not exist. The first access read there set acb_auth's
    # ``_tables_missing`` latch, and four tests of test_h3 and test_h6 failed
    # in pr-check run 41305223510.
    async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
        first, second = await asyncio.gather(
            _patch(ns, a, path, {"fields": {"Site_Count": 7}}, engine_scope=False),
            _patch(ns, a, path, {"fields": {"Notes": "changed"}}, engine_scope=False),
        )
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    fields = _row(ns, ids[0]).fields
    assert fields["Site_Count"] == 7 and fields["Notes"] == "changed"
    assert fields["Name"] == "Site 0"


# ── 5. The module list and the AI catalogue in Native mode ───────────────────


async def test_native_lists_modules_with_records_and_none_without(ns, monkeypatch) -> None:
    from acb_common import get_settings

    a, b = ns.org_a, ns.org_b
    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", "*", raising=False)
    _seed_org(ns, a, mode="native", modules=PROFILE_A)
    _seed_org(ns, b, mode="native", modules=PROFILE_A)
    _record(ns, a, "Products", "p1", "Widget")
    _record(ns, a, "Install_Base", "i1", "Site")

    listed = await _get(ns, a, "/crm/modules")
    assert listed.status_code == 200, listed.text
    assert {m["api_name"]: m["count"] for m in listed.json()} == {
        "Products": 1, "Install_Base": 1,
    }
    assert (await _get(ns, a, "/crm/modules/Products/records")).status_code == 200
    # Quotes holds no record here: it is not listed, and its path is 404.
    assert (await _get(ns, a, "/crm/modules/Quotes/records")).status_code == 404

    # The AI catalogue keeps the modules that hold records.
    cat = await _get(ns, a, "/crm/catalogue")
    assert cat.status_code == 200, cat.text
    body = cat.json()
    assert body["mode"] == "native"
    modules = {e["module"] for e in body["entries"] if e["kind"] == "module"}
    assert modules == {"Products", "Install_Base"}

    # An org with a profile and no record lists none.
    assert (await _get(ns, b, "/crm/modules")).json() == []
    cat_b = await _get(ns, b, "/crm/catalogue")
    assert not [e for e in cat_b.json()["entries"] if e["kind"] == "module"]


# ── 6. No zoho_dirty, and the sync door still overwrites in Mirror mode ──────


async def test_a_native_write_sets_no_zoho_dirty_and_the_sync_door_overwrites_in_mirror(
    ns,
) -> None:
    a = ns.org_a
    _seed_org(ns, a, mode="mirror", modules=PROFILE_A)
    ids = _seed_install_base(ns, a, 1)
    with ns.admin_engine.begin() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'crm_module_records'")).fetchall()}
    assert "zoho_dirty" not in cols

    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.routes.crm.core import (
        _tenant_session,
        upsert_module_records_from_source,
    )

    scope = clear_tenant()
    try:
        bind_tenant(a)
        async with (
            tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)),
            _tenant_session() as db,
        ):
            landed = await upsert_module_records_from_source(
                db, "zoho", "Install_Base", "ib0",
                {"name": "From Zoho", "owner_email": None,
                 "fields": {"Name": "From Zoho"}, "ext_url": None,
                 "ext_created_at": None, "ext_modified_at": None},
            )
    finally:
        release_tenant(scope)
    assert landed is True
    assert _row(ns, ids[0]).name == "From Zoho"
