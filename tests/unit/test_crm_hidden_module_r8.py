"""R8 — a core module that the Zoho account turned off (WS-53 CRM-Q15).

Found on a live tenant on 2026-10-15. The customer hid Accounts and Deals in
Zoho and keeps the business in custom modules. Zoho then answers each record
read of those two modules with HTTP 400 ``INVALID_MODULE``, and the v8 reads
with ``NO_PERMISSION``. The sync read Accounts first, failed, and so mirrored
nothing at all, not even the Contacts and Leads it could read.

The rule this file pins:

- A module the account refuses is skipped, core or optional. The run is
  ``ok``, the other modules land, and the connection stays connected.
- It reports ``unavailable`` AND ``hidden`` under its slug, so the health card
  does not tell the admin to connect again.
- It deletes nothing. The rows a module mirrored before it was turned off stay.
- Any other failure of a core module still fails the run.

``tests/unit/test_crm_q5_lost_data_r8.py`` owns the fake Zoho with faults.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_q5_lost_data_r8 import _seed, zoho  # noqa: F401
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
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

HIDDEN = (400, {"code": "INVALID_MODULE", "message": "the module name given seems to be invalid",
                "status": "error"})
NO_PERMISSION = (400, {"code": "NO_PERMISSION", "message": "permission denied to access the module",
                       "status": "error"})


def _hide(z, module: str, fault=HIDDEN) -> None:
    z.faults[module] = fault


def _calls(z, module: str) -> int:
    return sum(1 for r in z.requests if r.url.path.rstrip("/").split("/")[3:4] == [module])


def _age_checked_at(ns, org: str, module: str) -> None:
    """Move the last ask of an unavailable module two hours back."""
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = jsonb_set(cursor_state, "
            "'{checked_at}', to_jsonb((now() - interval '2 hours')::text)) "
            "WHERE organization_id = CAST(:o AS uuid) AND module = :m"),
            {"o": org, "m": module})


async def test_hidden_accounts_and_deals_no_longer_stop_the_other_modules(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, b = zoho
    _seed(a)
    _seed(b)
    a.add("Leads", "l1", 4, Last_Name="Lead", Company="Acme", Lead_Status="Fresh")
    a.add("Deals", "d1", 5, Deal_Name="Deal", Stage="Qualify")
    _hide(a, "Accounts")
    _hide(a, "Deals", NO_PERMISSION)
    await _connect(ns, ns.org_a, a)
    await _connect(ns, ns.org_b, b)

    first = await engine.run_org(ns.org_a)
    assert (first.status, first.error) == ("ok", None)
    assert _count(ns, "crm_companies", ns.org_a) == 0
    assert _count(ns, "crm_deals", ns.org_a) == 0
    assert _count(ns, "crm_contacts", ns.org_a) == 2, "Contacts land although Accounts is off"
    assert _count(ns, "crm_leads", ns.org_a) == 1
    for slug in ("organizations", "deals"):
        assert first.counts[slug]["unavailable"] == 1
        assert first.counts[slug]["hidden"] == 1
    assert first.counts["contacts"].get("unavailable", 0) == 0
    status = _q(ns, "SELECT status FROM integration_connections WHERE "
                    "organization_id = CAST(:o AS uuid)", o=ns.org_a)
    assert [r[0] for r in status] == ["connected"], "a hidden module is not a dead token"

    # Org B uses the same Zoho ids, and its Accounts is on. It is untouched.
    other = await engine.run_org(ns.org_b)
    assert other.status == "ok"
    assert _count(ns, "crm_companies", ns.org_b) == 1
    assert other.counts["organizations"].get("hidden", 0) == 0

    # The plan leaves import, and the hidden modules are not asked again at once.
    before = (_calls(a, "Accounts"), _calls(a, "Deals"))
    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "incremental")
    assert second.counts["organizations"]["hidden"] == 1
    assert (_calls(a, "Accounts"), _calls(a, "Deals")) == before

    # The customer turns Accounts on again. The next ask, after the hour, reads it.
    a.faults.pop("Accounts")
    _age_checked_at(ns, ns.org_a, "zoho:company")
    third = await engine.run_org(ns.org_a)
    assert third.status == "ok"
    assert third.counts["organizations"].get("unavailable", 0) == 0
    assert _count(ns, "crm_companies", ns.org_a) == 1
    # The contacts landed with no company while Accounts was off, and they did
    # not change in Zoho. The return of Accounts reads them in full once, so
    # the link fills in (review P1).
    assert _count(ns, "crm_contacts", ns.org_a, "company_id IS NULL") == 0
    queued = _q(ns, "SELECT cursor_state FROM crm_sync_cursors WHERE "
                    "organization_id = CAST(:o AS uuid) AND module = 'zoho:field_rules'",
                o=ns.org_a)
    assert "contact" not in (queued[0][0] or {}).get("reread", []), "the re-read is done"


async def test_a_module_turned_off_later_keeps_its_rows(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _count(ns, "crm_companies", ns.org_a) == 1

    _hide(a, "Accounts")
    # Force a full compare of every entity: a long gap since the last success.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_runs SET finished_at = finished_at - interval '60 days', "
            "started_at = started_at - interval '60 days' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a})
    run = await engine.run_org(ns.org_a)
    assert run.status == "ok"
    assert run.counts["organizations"]["hidden"] == 1
    assert run.counts["organizations"]["deleted"] == 0
    assert _count(ns, "crm_companies", ns.org_a) == 1, "turning a module off deletes nothing"


async def test_any_other_failure_of_a_core_module_still_fails_the_run(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    _hide(a, "Accounts", (400, {"code": "INVALID_DATA", "message": "x", "status": "error"}))
    await _connect(ns, ns.org_a, a)

    run = await engine.run_org(ns.org_a)
    assert run.status == "failed"
    assert run.counts["organizations"].get("unavailable", 0) == 0


async def test_a_refused_deleted_list_alone_is_reported_not_swallowed(ns, zoho) -> None:
    """Review P2. The changed list reads and only the deleted list is refused."""
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"

    real = a.handle

    def handle(request):
        if request.url.path == "/crm/v2/Accounts/deleted":
            a.requests.append(request)
            return httpx.Response(NO_PERMISSION[0], json=NO_PERMISSION[1])
        return real(request)

    a.handle = handle
    # Force a full compare, so the deleted list is read in this run.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_runs SET finished_at = finished_at - interval '60 days', "
            "started_at = started_at - interval '60 days' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a})
    run = await engine.run_org(ns.org_a)
    assert run.status == "ok"
    assert run.counts["organizations"]["hidden"] == 1
    assert _count(ns, "crm_companies", ns.org_a) == 1


async def test_deal_roles_wait_while_deals_is_off(ns, zoho) -> None:
    """Review P2. No role call while Deals is off, and the queue is kept."""
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
    _hide(a, "Deals")
    hidden = await engine.run_org(ns.org_a)
    assert hidden.status == "ok"
    assert hidden.counts["deals"]["hidden"] == 1
    again = await engine.run_org(ns.org_a)
    assert again.status == "ok"
    assert a.role_calls == ["d1"], "no role read while Deals is off"

    a.faults.pop("Deals")
    _age_checked_at(ns, ns.org_a, "zoho:deal")
    back = await engine.run_org(ns.org_a)
    assert back.status == "ok"
    assert a.role_calls[-1] == "d1", "the kept queue is read once Deals is back"
