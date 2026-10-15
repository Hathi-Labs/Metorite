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

from tests.unit.test_crm_hidden_module_r8 import NO_PERMISSION, _hide
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
    # Round 2: an unheld parent of the key gets ONE incremental page, so a
    # child of a parent made since the last normal run keeps its link.
    assert _reads(a, "Contacts") == contacts_before + 1, "the parent read is incremental"
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
        # As after a real hold: the last compare of Contacts is as old.
        c.execute(text(
            "UPDATE crm_sync_cursors SET last_reconciled_at = now() - interval '61 days' "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:contact'"),
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



# ── The second review round (2026-10-15) ─────────────────────────────────────


async def test_the_gap_compare_runs_once_and_never_for_a_hidden_module(ns, zoho) -> None:
    """Round 2, P1. One completed compare answers the gap for 50 days, and a
    module the account refuses is never due, because it cannot be walked."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _hide(a, "Accounts")
    await _connect(ns, ns.org_a, a)
    first = await engine.run_org(ns.org_a)
    assert first.status == "ok" and first.counts["organizations"]["hidden"] == 1
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET last_pulled_at = now() - interval '61 days' "
            "WHERE organization_id = CAST(:o AS uuid) "
            "AND module IN ('zoho:contact:deleted', 'zoho:company:deleted')"),
            {"o": ns.org_a})
        c.execute(text(
            "UPDATE crm_sync_cursors SET last_reconciled_at = now() - interval '61 days' "
            "WHERE organization_id = CAST(:o AS uuid) "
            "AND module IN ('zoho:contact', 'zoho:company')"),
            {"o": ns.org_a})

    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "reconcile"), "contacts get one compare"
    third = await engine.run_org(ns.org_a)
    assert (third.status, third.kind) == ("ok", "incremental"), "and only one"
    # Accounts is hidden, so its deleted list never moves. Its 61-day gap did
    # not make the second run a compare of Accounts, nor the third.
    assert third.counts["organizations"]["hidden"] == 1


async def test_a_resumed_import_from_before_this_slice_still_holds(ns, zoho) -> None:
    """Round 2, P1. The import that the incident interrupted carries an
    estimate with no ``tasks`` key and no hold entry. On resume it counts once
    more and holds, so the big module is not read with the rest."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _notes(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _holds(ns, ns.org_a)["activities"]["held"] is True
    # Make it look like an import from before CRM-Q16 that stopped mid-way.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_settings SET sync_holds = '{}'::jsonb "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a})
        c.execute(text(
            "UPDATE crm_sync_runs SET status = 'stopped', error = 'shutdown', "
            "estimate = estimate - 'tasks' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a})

    run = await engine.run_org(ns.org_a)
    assert (run.status, run.kind) == ("ok", "import")
    assert _holds(ns, ns.org_a)["activities"]["held"] is True
    assert _reads(a, "Notes") == 0, "held again before its first read"


async def test_an_only_run_of_a_child_reads_its_unheld_parents(ns, zoho) -> None:
    """Round 2, P2. A note on a contact made since the last normal run lands
    with its link, because the "on its own" run reads the parent too."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    a.add("Contacts", "c9", 30, Last_Name="New", Account_Name={"id": "a1"})
    a.add("Notes", "n9", 31, Note_Title="On the new one", Note_Content="Body",
          Parent_Id={"id": "c9"}, **{"$se_module": "Contacts"})

    def deleted_reads() -> int:
        return sum(1 for r in a.requests if r.url.path == "/crm/v2/Contacts/deleted")

    gone_before = deleted_reads()
    alone = await engine.run_org(ns.org_a, only=frozenset({"activities"}))
    assert alone.status == "ok"
    assert _count(ns, "crm_contacts", ns.org_a, "ext_id = 'c9'") == 1
    assert _count(ns, "crm_activities", ns.org_a,
                  "ext_id = 'n9' AND contact_id IS NOT NULL") == 1
    # Round 3: the parent's changes only. Its deleted list is not read.
    assert deleted_reads() == gone_before


async def test_the_children_of_a_parent_that_was_in_are_re_read_in_full(
    ns, zoho,
) -> None:
    """Round 3. Contacts were in, then held, then synced on its own. The notes
    are then read in FULL once: a bound to the hold lost records (round 3)."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    for i in range(4):  # four old notes, two pages at the suite's page size
        a.add("Notes", f"o{i}", 10 + i, Note_Title=f"Old {i}", Note_Content="Body",
              Parent_Id={"id": "c1"}, **{"$se_module": "Contacts"})
    await _connect(ns, ns.org_a, a)
    admin = _admin("admin@crm-sync.test", ns.org_a)
    # Four notes are large at the test limit. The admin wants them all in.
    on = await _call_json(admin, "PUT", "/crm/sync/modules/activities", {"sync": True})
    assert on.status_code == 200
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _count(ns, "crm_activities", ns.org_a) == 4
    held = await _call_json(admin, "PUT", "/crm/sync/modules/contacts", {"sync": False})
    assert held.status_code == 200
    a.add("Notes", "n5", 40, Note_Title="New", Note_Content="Body",
          Parent_Id={"id": "c1"}, **{"$se_module": "Contacts"})
    assert (await engine.run_org(ns.org_a)).status == "ok"  # marks contacts held_skipped
    assert (await engine.run_org(ns.org_a, only=frozenset({"contacts"}))).status == "ok"

    before = _reads(a, "Notes")
    after = await engine.run_org(ns.org_a)
    assert after.status == "ok"
    assert _reads(a, "Notes") - before >= 3, "five notes, two a page: a full read"
    assert _count(ns, "crm_activities", ns.org_a) == 5


async def test_deal_roles_wait_while_deals_is_held(ns, zoho) -> None:
    """Verifier F4. No role call while Deals is held, and the queue is kept."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Deals", "d1", 4, Deal_Name="Deal", Stage="Qualify")
    a.roles_fault = (400, {})  # the first role read fails, so d1 stays queued
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert a.role_calls == ["d1"]
    a.roles_fault = None
    a.roles["d1"] = [{"id": "c1", "Contact_Role": {"name": "Buyer"}}]
    admin = _admin("admin@crm-sync.test", ns.org_a)

    held = await _call_json(admin, "PUT", "/crm/sync/modules/deals", {"sync": False})
    assert held.status_code == 200
    run = await engine.run_org(ns.org_a)
    assert run.status == "ok" and run.counts["deals"]["held"] == 1
    assert a.role_calls == ["d1"], "no role read while Deals is held"

    released = await _call_json(admin, "PUT", "/crm/sync/modules/deals", {"sync": True})
    assert released.status_code == 200
    back = await engine.run_org(ns.org_a)
    assert back.status == "ok"
    assert a.role_calls == ["d1", "d1"], "the kept queue is read once Deals is back"


async def test_a_hold_with_no_settings_row_is_a_409(ns, zoho) -> None:
    """Verifier F4. An org with no settings row has nothing to hold against."""
    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_settings WHERE organization_id = CAST(:o AS uuid)"),
                  {"o": ns.org_a})

    put = await _call_json(
        _admin("admin@crm-sync.test", ns.org_a), "PUT",
        "/crm/sync/modules/contacts", {"sync": False},
    )
    assert put.status_code == 409, put.text
    assert put.json()["detail"] == "no_settings"


async def test_a_refused_deleted_list_gets_one_compare_not_one_every_run(ns, zoho) -> None:
    """Round 2, P1. The changed list reads and only the deleted list is
    refused, so that cursor never moves. The gap earns one compare. The
    completed compare then answers for 50 days, not every run."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    real = a.handle

    def handle(request):
        if request.url.path == "/crm/v2/Contacts/deleted":
            a.requests.append(request)
            return httpx.Response(NO_PERMISSION[0], json=NO_PERMISSION[1])
        return real(request)

    a.handle = handle
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET last_pulled_at = now() - interval '61 days' "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:contact:deleted'"),
            {"o": ns.org_a})
        c.execute(text(
            "UPDATE crm_sync_cursors SET last_reconciled_at = now() - interval '61 days' "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:contact'"),
            {"o": ns.org_a})

    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "reconcile")
    third = await engine.run_org(ns.org_a)
    assert (third.status, third.kind) == ("ok", "incremental"), "one compare, then quiet"
    assert _count(ns, "crm_contacts", ns.org_a) == 2


# ── The third review round (2026-10-15) ──────────────────────────────────────


async def test_a_child_never_imported_gets_a_full_first_import_after_a_parent_hold(
    ns, zoho,
) -> None:
    """Round 3, P1. Notes older than the contacts watermark. Activities was
    held from the start, Contacts was held and released. The first import of
    the notes must read all of them, not only the ones after the hold."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)  # c1 and c2 at minutes 2 and 3
    for i in range(3):  # three notes at minutes 0 to 2, older than the contacts
        a.add("Notes", f"n{i}", i, Note_Title=f"Note {i}", Note_Content="Body",
              Parent_Id={"id": "c1"}, **{"$se_module": "Contacts"})
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"  # activities auto-held
    assert _count(ns, "crm_activities", ns.org_a) == 0
    admin = _admin("admin@crm-sync.test", ns.org_a)
    assert (await _call_json(admin, "PUT", "/crm/sync/modules/contacts", {"sync": False})).status_code == 200
    assert (await engine.run_org(ns.org_a)).status == "ok"  # contacts skipped, marked
    assert (await _call_json(admin, "PUT", "/crm/sync/modules/contacts", {"sync": True})).status_code == 200
    assert (await engine.run_org(ns.org_a)).status == "ok"  # contacts back, notes queued
    assert (await _call_json(admin, "PUT", "/crm/sync/modules/activities", {"sync": True})).status_code == 200

    run = await engine.run_org(ns.org_a)
    assert run.status == "ok"
    assert _count(ns, "crm_activities", ns.org_a, "contact_id IS NOT NULL") == 3


async def test_a_child_made_during_a_hold_lands_when_the_parent_comes_back(
    ns, zoho,
) -> None:
    """Round 3, P1. A contact and its note made after the last read of
    Contacts and before the hold. The note landed with no parent during the
    hold. The release re-reads the notes in full, so it lands with its link."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    a.add("Contacts", "c9", 5, Last_Name="New", Account_Name={"id": "a1"})
    a.add("Notes", "n9", 6, Note_Title="On the new one", Note_Content="Body",
          Parent_Id={"id": "c9"}, **{"$se_module": "Contacts"})
    admin = _admin("admin@crm-sync.test", ns.org_a)
    assert (await _call_json(admin, "PUT", "/crm/sync/modules/contacts", {"sync": False})).status_code == 200
    held = await engine.run_org(ns.org_a)
    assert held.status == "ok"
    assert _count(ns, "crm_activities", ns.org_a, "ext_id = 'n9'") == 0, "no parent yet"

    assert (await _call_json(admin, "PUT", "/crm/sync/modules/contacts", {"sync": True})).status_code == 200
    back = await engine.run_org(ns.org_a)
    assert back.status == "ok"
    assert _count(ns, "crm_contacts", ns.org_a, "ext_id = 'c9'") == 1
    assert _count(ns, "crm_activities", ns.org_a,
                  "ext_id = 'n9' AND contact_id IS NOT NULL") == 1


async def test_an_only_run_right_after_a_release_keeps_the_relink(ns, zoho) -> None:
    """Round 4. Contacts held, then released, then Activities synced on its
    own before any normal run. The parent-only read must not clear the mark,
    or the note that landed with no parent during the hold never relinks."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    admin = _admin("admin@crm-sync.test", ns.org_a)
    assert (await _call_json(admin, "PUT", "/crm/sync/modules/contacts", {"sync": False})).status_code == 200
    a.add("Contacts", "c9", 5, Last_Name="New", Account_Name={"id": "a1"})
    a.add("Notes", "n9", 6, Note_Title="On the new one", Note_Content="Body",
          Parent_Id={"id": "c9"}, **{"$se_module": "Contacts"})
    assert (await engine.run_org(ns.org_a)).status == "ok"  # marks contacts, n9 has no parent
    assert _count(ns, "crm_activities", ns.org_a, "ext_id = 'n9'") == 0
    assert (await _call_json(admin, "PUT", "/crm/sync/modules/contacts", {"sync": True})).status_code == 200

    alone = await engine.run_org(ns.org_a, only=frozenset({"activities"}))
    assert alone.status == "ok"
    normal = await engine.run_org(ns.org_a)
    assert normal.status == "ok"
    assert _count(ns, "crm_contacts", ns.org_a, "ext_id = 'c9'") == 1
    assert _count(ns, "crm_activities", ns.org_a,
                  "ext_id = 'n9' AND contact_id IS NOT NULL") == 1

