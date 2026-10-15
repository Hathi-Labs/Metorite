"""R8 — hold a module, and sync one module on its own (WS-53 CRM-Q16).

Spec: ``project-docs/specs/crm_platform.md`` §18.8. On 2026-10-15 the first
import of one org read 18,674 activities with everything else and strained the
production database. The owner asked for three things, and this file pins them:

- The first import holds a LARGE module before its first record read, and
  syncs the rest. A held module does not keep the plan at ``import``.
- An admin can sync a held module on its own (``only``), and turn it on or off
  (``PUT /crm/sync/modules/{key}``). An admin release is remembered, so the
  next import does not hold the module again.
- A hold belongs to one organization. Org B with the same Zoho ids is not held
  by org A's choice.

The limit is 5,000 records in production. The tests set it to 2, so a fake
tenant of three notes is large.

``tests/unit/test_crm_q5_lost_data_r8.py`` owns the fake Zoho fixture.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_module_records_r8 import (  # noqa: F401
    _add_member,
    _connect_all,
    _records,
    _run,
    mz,
)
from tests.unit.test_crm_module_records_r8 import _seed as _seed_modules
from tests.unit.test_crm_q5_lost_data_r8 import _seed, zoho  # noqa: F401
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _admin,
    _connect,
    _count,
    _q,
    _the_loop_is_not_stopping,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database (see the engine suite).
pytestmark = _DB_GATE


@pytest.fixture(autouse=True)
def _small_limit(monkeypatch):
    from gateway.crm_sync import holds

    monkeypatch.setattr(holds, "LARGE_MODULE_RECORDS", 2)


def _notes(z, n: int = 3) -> None:
    for i in range(n):
        z.add("Notes", f"n{i}", 10 + i, Note_Title=f"Note {i}", Note_Content="Body",
              Parent_Id={"id": "c1"}, **{"$se_module": "Contacts"})


def _reads(z, module: str) -> int:
    return sum(1 for r in z.requests if r.url.path == f"/crm/v2/{module}")


def _holds(ns, org: str) -> dict[str, Any]:
    rows = _q(ns, "SELECT sync_holds FROM crm_settings WHERE "
                  "organization_id = CAST(:o AS uuid)", o=org)
    return rows[0][0] if rows else {}


async def _call_json(user: Any, method: str, path: str, body: Any = None) -> Any:
    """``_call`` of the engine suite, with a JSON body."""
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


async def test_the_first_import_holds_a_large_module_and_syncs_the_rest(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _notes(a)
    await _connect(ns, ns.org_a, a)

    first = await engine.run_org(ns.org_a)
    assert (first.status, first.error) == ("ok", None)
    assert _count(ns, "crm_contacts", ns.org_a) == 2, "the rest syncs"
    assert _count(ns, "crm_companies", ns.org_a) == 1
    assert _reads(a, "Notes") == 0, "a large module is held BEFORE its first read"
    assert _count(ns, "crm_activities", ns.org_a) == 0
    assert first.counts["activities"]["held"] == 1
    entry = _holds(ns, ns.org_a)["activities"]
    assert (entry["held"], entry["by"], entry["count"]) == (True, "auto", 3)
    assert "contacts" not in _holds(ns, ns.org_a), "a small module is not held"

    # A held module does not keep the plan at import, and it stays unread.
    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "incremental")
    assert _reads(a, "Notes") == 0
    assert second.counts["activities"]["held"] == 1


async def test_a_held_module_syncs_on_its_own_and_only_it(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _notes(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"

    contacts_before = _reads(a, "Contacts")
    alone = await engine.run_org(ns.org_a, only=frozenset({"activities"}))
    assert alone.status == "ok"
    assert _count(ns, "crm_activities", ns.org_a, "type = 'note'") == 3
    assert _reads(a, "Contacts") == contacts_before, "the other modules are not read"
    assert alone.counts["contacts"].get("held", 0) == 0, "an on-its-own run marks no other key"
    assert _holds(ns, ns.org_a)["activities"]["held"] is True, "the hold stays"


async def test_an_admin_release_is_remembered_and_a_hold_deletes_nothing(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _notes(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    admin = _admin("admin@crm-sync.test", ns.org_a)

    released = await _call_json(admin, "PUT", "/crm/sync/modules/activities", {"sync": True})
    assert released.status_code == 200, released.text
    assert released.json()["holds"]["activities"]["held"] is False
    assert released.json()["holds"]["activities"]["by"] == "admin"
    run = await engine.run_org(ns.org_a)
    assert run.status == "ok"
    assert _count(ns, "crm_activities", ns.org_a, "type = 'note'") == 3
    assert _holds(ns, ns.org_a)["activities"]["held"] is False, "not held again"

    # Hold Contacts by hand. Its rows stay, and it is not read.
    held = await _call_json(admin, "PUT", "/crm/sync/modules/contacts", {"sync": False})
    assert held.status_code == 200
    before = _reads(a, "Contacts")
    again = await engine.run_org(ns.org_a)
    assert again.status == "ok"
    assert again.counts["contacts"]["held"] == 1
    assert _reads(a, "Contacts") == before
    assert _count(ns, "crm_contacts", ns.org_a) == 2, "a hold deletes nothing"


async def test_an_unknown_key_is_refused_and_starts_nothing(ns, zoho) -> None:
    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    admin = _admin("admin@crm-sync.test", ns.org_a)

    for key in ("bogus", "module:Nope"):
        put = await _call_json(admin, "PUT", f"/crm/sync/modules/{key}", {"sync": False})
        assert put.status_code == 422, (key, put.text)
    post = await _call_json(admin, "POST", "/crm/sync/zoho", {"only": "bogus"})
    assert post.status_code == 422
    assert _holds(ns, ns.org_a) == {}
    assert _count(ns, "crm_sync_runs", ns.org_a) == 0


async def test_a_hold_belongs_to_one_organization(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, b = zoho
    for z in (a, b):
        _seed(z)
        _notes(z)
    await _connect(ns, ns.org_a, a)
    await _connect(ns, ns.org_b, b)
    # Org B turns Activities on before its first import. Org A does not.
    released = await _call_json(
        _admin("admin@crm-sync.test", ns.org_b), "PUT",
        "/crm/sync/modules/activities", {"sync": True},
    )
    assert released.status_code == 200

    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert (await engine.run_org(ns.org_b)).status == "ok"
    assert _holds(ns, ns.org_a)["activities"]["held"] is True
    assert _holds(ns, ns.org_b)["activities"]["held"] is False
    assert _count(ns, "crm_activities", ns.org_a) == 0
    assert _count(ns, "crm_activities", ns.org_b, "type = 'note'") == 3


async def test_the_health_card_reads_the_holds(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _notes(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"

    health = await _call_json(_admin("admin@crm-sync.test", ns.org_a), "GET", "/crm/sync/health")
    assert health.status_code == 200, health.text
    hold = health.json()["holds"]["activities"]
    assert (hold["held"], hold["by"], hold["count"]) == (True, "auto", 3)
    row = next(e for e in health.json()["entities"] if e["key"] == "activities")
    assert row["last_run"]["held"] == 1


async def test_a_large_custom_module_is_held_before_its_first_read(mz) -> None:
    """A generic module is counted before its first import, and held when large."""
    p, a, _b = mz
    _seed_modules(a)
    for i in (2, 3):
        a.add("Install_Base", f"i{i}", 12 + i, Name=f"Install {i}")
    _add_member(p, p.org_a)
    await _connect_all(p, p.org_a, a)

    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    rows = _records(p, p.org_a)
    assert {m for m, _ in rows} == {"Products"}, "Products land, Install_Base waits"
    assert _reads(a, "Install_Base") == 0
    assert result.counts["module:Install_Base"]["held"] == 1
    entry = _holds(p, p.org_a)["module:Install_Base"]
    assert (entry["held"], entry["by"], entry["count"]) == (True, "auto", 3)

    from gateway.crm_sync import engine

    alone = await engine.run_org(p.org_a, only=frozenset({"module:Install_Base"}))
    assert alone.status == "ok"
    assert {k for m, k in _records(p, p.org_a) if m == "Install_Base"} == {"i1", "i2", "i3"}



# ── The review round (2026-10-15) ────────────────────────────────────────────


async def test_a_held_parent_relinks_its_children_when_it_syncs(ns, zoho) -> None:
    """Review P1. Notes landed with no contact while Contacts was held. A sync
    of Contacts on its own re-reads the notes once, so the link fills in."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Contacts", "c3", 4, Last_Name="Three", Account_Name={"id": "a1"})
    _notes(a, 1)  # one note on c1: small, so Activities is not held
    await _connect(ns, ns.org_a, a)

    first = await engine.run_org(ns.org_a)
    assert first.status == "ok"
    assert first.counts["contacts"]["held"] == 1, "3 contacts is large at the test limit"
    assert _count(ns, "crm_contacts", ns.org_a) == 0
    assert _count(ns, "crm_activities", ns.org_a) == 0, "a note with no parent waits"

    alone = await engine.run_org(ns.org_a, only=frozenset({"contacts"}))
    assert alone.status == "ok"
    assert _count(ns, "crm_contacts", ns.org_a) == 3

    after = await engine.run_org(ns.org_a)
    assert after.status == "ok"
    assert _count(ns, "crm_activities", ns.org_a, "contact_id IS NOT NULL") == 1


async def test_tasks_are_held_under_their_own_key(ns, zoho) -> None:
    """Review P1. The estimate sums tasks into activities. The hold must not."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    for i in range(3):
        a.add("Tasks", f"t{i}", 20 + i, Subject=f"Task {i}", What_Id={"id": "a1"},
              **{"$se_module": "Accounts"})
    _notes(a, 1)
    await _connect(ns, ns.org_a, a)

    run = await engine.run_org(ns.org_a)
    assert run.status == "ok"
    entries = _holds(ns, ns.org_a)
    assert entries["tasks"]["held"] is True and entries["tasks"]["count"] == 3
    assert "activities" not in entries, "one note is not large"
    assert _reads(a, "Tasks") == 0
    assert _count(ns, "crm_activities", ns.org_a, "type = 'note'") == 1


async def test_an_import_run_reads_the_deleted_lists_of_the_modules_already_in(
    ns, zoho,
) -> None:
    """Review P2. A release puts the org back at ``import``. The modules that
    are in still read their deleted lists."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _notes(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _count(ns, "crm_contacts", ns.org_a) == 2

    a.drop("Contacts", "c2", tombstone=True)
    released = await _call_json(
        _admin("admin@crm-sync.test", ns.org_a), "PUT",
        "/crm/sync/modules/activities", {"sync": True},
    )
    assert released.status_code == 200
    run = await engine.run_org(ns.org_a)
    assert (run.status, run.kind) == ("ok", "import"), "the released module imports"
    assert _count(ns, "crm_contacts", ns.org_a, "ext_id = 'c2'") == 0, "the delete still lands"


async def test_a_deleted_list_older_than_the_provider_keeps_plans_a_full_compare(
    ns, zoho,
) -> None:
    """Review P2. After a long hold the deleted list has a gap that the
    provider no longer fills. The next run compares every id."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET last_pulled_at = now() - interval '61 days' "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:contact:deleted'"),
            {"o": ns.org_a})

    run = await engine.run_org(ns.org_a)
    assert (run.status, run.kind) == ("ok", "reconcile")
    assert _count(ns, "crm_contacts", ns.org_a) == 2, "a compare of live rows deletes nothing"


async def test_a_start_over_forgets_the_holds(ns, zoho) -> None:
    """Review P2. The next account's first import decides again."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _notes(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _holds(ns, ns.org_a)["activities"]["held"] is True

    reset = await _call_json(
        _admin("admin@crm-sync.test", ns.org_a), "POST", "/crm/reset",
        {"confirm": "DELETE", "then": "none"},
    )
    assert reset.status_code == 200, reset.text
    assert _holds(ns, ns.org_a) == {}


async def test_a_core_module_is_not_a_hold_key(mz) -> None:
    """Review note. ``module:Deals`` would hold nothing, so it is refused."""
    p, a, _b = mz
    _seed_modules(a)
    _add_member(p, p.org_a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    admin = _admin("admin@crm-sync.test", p.org_a)

    ok = await _call_json(admin, "PUT", "/crm/sync/modules/module:Products", {"sync": False})
    assert ok.status_code == 200, ok.text
    refused = await _call_json(admin, "PUT", "/crm/sync/modules/module:Deals", {"sync": False})
    assert refused.status_code == 422
