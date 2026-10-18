"""R8 — ``GET /crm/sync/health``, against a real Postgres (WS-53 CRM-Q9).

The route is read only. These tests prove, with two organizations and the
non-privileged role:

- the shape of the answer after a real run (entities, modules, history, credits);
- the route is for an admin: a member gets 403;
- one organization never sees the counts, the runs or the modules of the other;
- a key in ``counts`` that the route does not know passes through;
- no token and no record content reach the answer (sentinel).

It reuses the fake Zoho and the fixtures of ``test_crm_pipelines_r8``.
"""

# ruff: noqa: F811  (q and ns are imported fixtures)

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_module_records_r8 import (  # noqa: F401  (mz is a fixture)
    _age_module_reads,
    _connect_all,
    _run,
    _seed,
    mz,
)
from tests.unit.test_crm_pipelines_r8 import (  # noqa: F401  (q and ns are fixtures)
    SENTINEL,
    _admin,
    _member,
    _req,
    _sync,
    ns,
    q,
)
from tests.unit.test_crm_sync_engine_r8 import _q, seed_basic
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database (see the sibling suites).
pytestmark = _DB_GATE

ENTITY_KEYS = ["organizations", "contacts", "leads", "deals", "activities"]


async def test_the_health_route_has_the_contract_shape(q) -> None:
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    got = await _req(_admin(q.org_a), "GET", "/crm/sync/health")
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == {
        "last_run", "last_success_at", "next_run_at", "entities", "modules",
        "history", "credits", "running", "holds",  # holds: CRM-Q16
        "activities",  # activities: CRM-Q24
        "open_questions"}  # open_questions: CRM-Q32-4, None outside the list
    assert set(body["activities"]) == {
        "parentless", "dropped", "waiting", "recovered_7d", "unkept"}
    assert set(body["last_run"]) == {
        "kind", "status", "started_at", "finished_at", "error", "credits_used"}
    assert body["last_run"]["status"] == "ok" and body["last_run"]["error"] is None
    assert body["last_success_at"] and body["next_run_at"]
    assert [e["key"] for e in body["entities"]][:5] == ENTITY_KEYS
    for entity in body["entities"]:
        assert set(entity) == {"key", "zoho_count", "ours", "last_run", "short_by"}
        assert "seen" in entity["last_run"]
        # The CRM-Q5 steps (tasks, converted_leads, deal_contacts) pass through
        # with their own keys, and the card reads a missing count as 0.
        if entity["key"] in ENTITY_KEYS:
            assert "failed" in entity["last_run"]
    # Zoho's count is the estimate, ours is the local rows with ext_source = zoho.
    by_key = {e["key"]: e for e in body["entities"]}
    assert by_key["organizations"]["zoho_count"] == 5 and by_key["organizations"]["ours"] == 5
    assert by_key["contacts"]["zoho_count"] == 2 and by_key["contacts"]["ours"] == 2
    assert by_key["organizations"]["last_run"]["seen"] == 5
    assert {m["api_name"] for m in body["modules"]} >= {"Deals", "Installs"}
    assert len(body["history"]) == 1 and body["history"][0]["kind"] == "import"
    assert body["credits"]["budget_24h"] > 0
    assert body["credits"]["used_24h"] >= 0


async def test_the_health_route_is_for_an_admin(q) -> None:
    await _sync(q, q.org_a, q.a)
    assert (await _req(_member(q.org_a), "GET", "/crm/sync/health")).status_code == 403


async def test_the_health_route_before_any_run(q) -> None:
    got = await _req(_admin(q.org_a), "GET", "/crm/sync/health")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["last_run"] is None and body["history"] == [] and body["modules"] == []
    assert [e["key"] for e in body["entities"]] == ENTITY_KEYS
    assert all(e["ours"] == 0 and e["zoho_count"] is None for e in body["entities"])
    # CRM-Q24 item 12: no cursor row and no run, and the block still serves.
    assert body["activities"] == BLANK_ACTIVITIES
    assert all(e["short_by"] is None for e in body["entities"])


async def test_one_organization_never_sees_the_other(q) -> None:
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    b = await _req(_admin(q.org_b), "GET", "/crm/sync/health")
    assert b.status_code == 200
    body = b.json()
    assert body["last_run"] is None and body["history"] == []
    assert body["modules"] == [] and body["last_success_at"] is None
    assert all(e["ours"] == 0 for e in body["entities"])
    assert body["credits"]["used_24h"] == 0
    a = (await _req(_admin(q.org_a), "GET", "/crm/sync/health")).json()
    assert any(e["ours"] for e in a["entities"])


async def test_an_unknown_count_key_passes_through(q) -> None:
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    with q.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_runs SET "
            "counts = counts || CAST(:c AS jsonb), "
            "estimate = COALESCE(estimate, '{}'::jsonb) || CAST(:e AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {"c": json.dumps({"tasks": {"seen": 0, "unavailable": 1, "skipped": 2}}),
             "e": json.dumps({"tasks": 7}), "o": q.org_a})
    body = (await _req(_admin(q.org_a), "GET", "/crm/sync/health")).json()
    tasks = next(e for e in body["entities"] if e["key"] == "tasks")
    assert tasks["last_run"] == {"seen": 0, "unavailable": 1, "skipped": 2}
    assert tasks["zoho_count"] == 7 and tasks["ours"] is None


async def test_no_secret_and_no_record_reach_the_answer(q) -> None:
    seed_basic(q.a, "A")
    q.a.add("Leads", "Al2", 20, Last_Name=SENTINEL, Company=SENTINEL)
    await _sync(q, q.org_a, q.a)
    got = await _req(_admin(q.org_a), "GET", "/crm/sync/health")
    assert SENTINEL not in got.text
    assert "ACCESS" not in got.text and "REFRESH" not in got.text


async def test_the_health_route_is_404_with_crm_mirror_off(q, monkeypatch) -> None:
    from acb_common import get_settings

    await _sync(q, q.org_a, q.a)
    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    assert (await _req(_admin(q.org_a), "GET", "/crm/sync/health")).status_code == 404


async def test_a_stale_running_row_is_not_live(q) -> None:
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    admin = _admin(q.org_a)
    assert (await _req(admin, "GET", "/crm/sync/health")).json()["running"] is False
    # Fresh heartbeat: live.
    with q.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_runs SET status = 'running', finished_at = NULL, "
            "heartbeat_at = now() WHERE organization_id = CAST(:o AS uuid)"),
            {"o": q.org_a})
    live = (await _req(admin, "GET", "/crm/sync/health")).json()
    assert live["running"] is True and live["next_run_at"] is None
    # A crashed run: the row says running, the heartbeat is old.
    with q.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_runs SET heartbeat_at = now() - interval '1 hour' "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {"o": q.org_a})
    stale = (await _req(admin, "GET", "/crm/sync/health")).json()
    assert stale["last_run"]["status"] == "running"
    assert stale["running"] is False


# ── CRM-Q24: a short read, and the activities the mirror does not hold ───────
#
# The fake Zoho of the CRM-Q6a suite (``mz``): two orgs, two generic modules
# (Products and Install_Base), and pages of 2 records.

BLANK_ACTIVITIES = {
    "parentless": {}, "dropped": {}, "waiting": {}, "recovered_7d": {}, "unkept": {},
}


def _count_says(z: Any, module: str, count: int | None) -> None:
    """Make Zoho's count of ``module`` say ``count``, while the list keeps
    what it holds. ``None`` gives the real count back. A Zoho user who cannot
    see every record gets this shape (the probe of 2026-10-16). A second call
    for another module wraps the first, so both hold."""
    if count is None:
        z.__dict__.pop("handle", None)
        return
    real = z.handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/crm/v8/{module}/actions/count":
            z.requests.append(request)
            return httpx.Response(200, json={"count": count})
        return real(request)

    z.handle = handle


def _cursor(p: Any, org: str, module: str) -> tuple[dict[str, Any] | None, Any]:
    rows = _q(p, "SELECT cursor_state, initial_done_at FROM crm_sync_cursors "
                 "WHERE organization_id = CAST(:o AS uuid) AND module = :m", o=org, m=module)
    return (rows[0][0], rows[0][1]) if rows else (None, None)


def _task(z: Any, ident: str, minute: int) -> None:
    """A Zoho task with no parent record and no person (DEWiN's 1,145)."""
    z.add("Tasks", ident, minute, Subject=f"{z.tag}-{ident}", Status="Not Started")


def _note_on(z: Any, ident: str, minute: int, module: str, parent: str) -> None:
    z.add("Notes", ident, minute, Note_Title=f"{z.tag}-{ident}", Note_Content="Body",
          Parent_Id={"id": parent, "name": "x"}, **{"$se_module": module})


async def _health(org: str) -> dict[str, Any]:
    got = await _req(_admin(org), "GET", "/crm/sync/health")
    assert got.status_code == 200, got.text
    return got.json()


def _row_of(body: dict[str, Any], key: str) -> dict[str, Any]:
    return next(e for e in body["entities"] if e["key"] == key)


async def test_item_5_a_module_and_a_core_entity_record_their_short_read(mz) -> None:
    p, a, _b = mz
    _seed(a)  # an Account a1, 2 Products, 1 Install_Base
    _count_says(a, "Products", 5)
    _count_says(a, "Accounts", 4)
    await _connect_all(p, p.org_a, a)
    first = await _run(p.org_a)
    assert (first.status, first.error) == ("ok", None)

    products, _ = _cursor(p, p.org_a, "zoho:module:Products")
    company, _ = _cursor(p, p.org_a, "zoho:company")
    install, _ = _cursor(p, p.org_a, "zoho:module:Install_Base")
    note, _ = _cursor(p, p.org_a, "zoho:note")
    assert products is not None and products["short_by"] == 3, "Zoho counts 5, we hold 2"
    assert company is not None and company["short_by"] == 3, "Zoho counts 4, we hold 1"
    assert install is not None and "short_by" not in install, "a whole read has none"
    assert note is not None and "short_by" not in note, "an activity entity gets none"
    assert "full_read" not in products and "read_drops" not in note

    body = await _health(p.org_a)
    assert _row_of(body, "module:Products")["short_by"] == 3
    assert _row_of(body, "organizations")["short_by"] == 3
    assert _row_of(body, "module:Install_Base")["short_by"] is None
    assert _row_of(body, "activities")["short_by"] is None

    # An incremental run reads no full list, so it keeps the number.
    again = await _run(p.org_a)
    assert again.kind == "incremental" and again.status == "ok"
    assert _cursor(p, p.org_a, "zoho:company")[0]["short_by"] == 3
    assert _row_of(await _health(p.org_a), "organizations")["short_by"] == 3


async def test_item_5_the_next_whole_full_read_clears_the_short_read(mz) -> None:
    """A walk (a reconcile) reads Zoho's count again. When the list now holds
    every record, ``short_by`` leaves the cursor."""
    p, a, _b = mz
    _seed(a)
    a.hide.add("Accounts")  # offset paged: a reconcile walks it each day
    _count_says(a, "Accounts", 4)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert _cursor(p, p.org_a, "zoho:company")[0]["short_by"] == 3

    _count_says(a, "Accounts", None)  # a Zoho admin connected: the count is 1
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_cursors SET last_reconciled_at = now() - "
                       "interval '2 days' WHERE organization_id = CAST(:o AS uuid) "
                       "AND module = 'zoho:company'"), {"o": p.org_a})
    walk = await _run(p.org_a)
    assert (walk.kind, walk.status) == ("reconcile", "ok")
    assert "short_by" not in _cursor(p, p.org_a, "zoho:company")[0]
    assert _row_of(await _health(p.org_a), "organizations")["short_by"] is None


async def test_item_8_the_drops_of_the_last_full_read_stay_after_an_incremental_run(
    mz,
) -> None:
    p, a, _b = mz
    _seed(a)
    _task(a, "t1", 30)
    _task(a, "t2", 31)
    _note_on(a, "n1", 32, "Payments", "x1")  # a module the profile does not hold
    _note_on(a, "n2", 33, "Install_Base", "i9")  # a mirrored module, record not in
    await _connect_all(p, p.org_a, a)
    first = await _run(p.org_a)
    assert (first.status, first.error) == ("ok", None)
    assert first.counts["activity_drops"] == {"(none)": 2, "Payments": 1, "Install_Base": 1}

    state, _ = _cursor(p, p.org_a, "zoho:activity_drops")
    assert state is not None
    assert state["drops"]["task"] == {"(none)": 2}
    assert state["drops"]["note"] == {"Payments": 1, "Install_Base": 1}
    assert set(state["at"]) >= {"task", "note"}
    # Zoho counts 2 notes and we hold none, and still no short read: the
    # drops explain the gap of an activity entity (item 5).
    for module in ("zoho:note", "zoho:task"):
        assert "short_by" not in (_cursor(p, p.org_a, module)[0] or {}), module

    second = await _run(p.org_a)
    assert second.kind == "incremental" and second.status == "ok"
    assert second.counts.get("activity_drops", {}) == {}, "this run dropped nothing"
    body = await _health(p.org_a)
    assert body["activities"] == {
        "parentless": {"task": 2},
        "dropped": {"Payments": 1, "Install_Base": 1},
        "waiting": {"note": 1},
        "recovered_7d": {},
        # Payments is not synced, and the one Install_Base drop waits.
        "unkept": {},
    }


async def test_item_8_a_full_read_over_two_runs_keeps_the_drops_of_both(mz) -> None:
    """The engine pages 2 records at a time. Page 2 of Notes fails, so the
    first run stops mid-read. The second run ends the read, and the row holds
    the drops of the whole read."""
    p, a, _b = mz
    _seed(a)
    for i in range(3):
        _note_on(a, f"n{i}", 30 + i, "Payments", f"x{i}")
    a.fail_after["Notes"] = 1
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "failed"
    mid, _ = _cursor(p, p.org_a, "zoho:note")
    assert mid is not None and mid["in_progress"] is True
    assert mid["full_read"] is True and mid["read_drops"] == {"Payments": 2}
    before = _cursor(p, p.org_a, "zoho:activity_drops")[0] or {}
    assert "note" not in (before.get("drops") or {}), "the read has not ended"

    del a.fail_after["Notes"]
    assert (await _run(p.org_a)).status == "ok"
    state, _ = _cursor(p, p.org_a, "zoho:activity_drops")
    assert state is not None and state["drops"]["note"] == {"Payments": 3}
    done, _ = _cursor(p, p.org_a, "zoho:note")
    assert done is not None and "read_drops" not in done and "full_read" not in done


async def test_item_9_and_10_a_recovered_activity_counts_for_7_days(mz) -> None:
    p, a, _b = mz
    _seed(a)
    _note_on(a, "n1", 30, "Install_Base", "i2")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert (await _health(p.org_a))["activities"]["waiting"] == {"note": 1}

    a.add("Install_Base", "i2", 70, Name="A-Late", Account={"id": "a1", "name": "x"})
    _age_module_reads(p, p.org_a)
    linked = await _run(p.org_a)
    assert linked.status == "ok"
    assert linked.counts["activity_recovered"] == {"Install_Base": 1}
    stored = _q(p, "SELECT counts -> 'activity_recovered' FROM crm_sync_runs WHERE "
                   "organization_id = CAST(:o AS uuid) AND id = CAST(:i AS uuid)",
                o=p.org_a, i=linked.run_id)
    assert stored[0][0] == {"Install_Base": 1}
    body = await _health(p.org_a)
    assert body["activities"]["recovered_7d"] == {"Install_Base": 1}
    assert body["activities"]["waiting"] == {}, "a linked id leaves the list"
    assert all(e["key"] != "activity_recovered" for e in body["entities"])

    # A run that started more than 7 days ago no longer counts.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_runs SET started_at = now() - interval '8 days' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": linked.run_id})
    assert (await _health(p.org_a))["activities"]["recovered_7d"] == {}


async def test_item_10_an_org_with_no_cursor_rows_serves_and_sees_no_other_org(mz) -> None:
    p, a, _b = mz
    _seed(a)
    _task(a, "t1", 30)
    _count_says(a, "Products", 5)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert (await _health(p.org_a))["activities"]["parentless"] == {"task": 1}

    assert not _q(p, "SELECT 1 FROM crm_sync_cursors WHERE organization_id = "
                     "CAST(:o AS uuid)", o=p.org_b)
    other = await _health(p.org_b)
    assert other["activities"] == BLANK_ACTIVITIES
    assert all(e["short_by"] is None for e in other["entities"])


async def test_a_drop_on_a_core_module_is_unkept(mz) -> None:
    """Fix round of 2026-10-17. The sync never keeps a drop on a core module
    (``engine._note_dropped``), so no ``waiting`` entry holds it. Before the
    fix the card hid it, because Deals is a module that the mirror reads."""
    p, a, _b = mz
    _seed(a)
    _note_on(a, "n1", 32, "Deals", "d-missing")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    block = (await _health(p.org_a))["activities"]
    assert block["dropped"] == {"Deals": 1}
    assert block["waiting"] == {}
    assert block["unkept"] == {"Deals": 1}


async def test_the_overflow_of_a_full_wait_list_is_unkept(mz, monkeypatch) -> None:
    """Fix round of 2026-10-17. Past ``recover.CAP`` a drop on a mirrored
    module is not kept. Those drops are unkept, and the kept ones wait."""
    from gateway.crm_sync import recover

    monkeypatch.setattr(recover, "CAP", 1)
    p, a, _b = mz
    _seed(a)
    for i in range(3):
        _note_on(a, f"n{i}", 32 + i, "Install_Base", f"i-missing-{i}")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    # The run that queues the overflow read clears the mark, and the read in
    # the next run sets it again while the list is still full.
    assert (await _run(p.org_a)).status == "ok"
    block = (await _health(p.org_a))["activities"]
    assert block["dropped"] == {"Install_Base": 3}
    assert block["waiting"] == {"note": 1}
    assert block["unkept"] == {"Install_Base": 2}


async def test_item_7_a_tie_too_wide_counts_failed_and_never_ends_the_import(
    mz, monkeypatch,
) -> None:
    """Seven Accounts share one second. Pages hold 2, and Zoho serves 4 by
    offset with no page token. The import of Accounts must not end as done."""
    from gateway.crm_sources.zoho import client

    monkeypatch.setattr(client, "OFFSET_LIMIT", 4)
    p, a, _b = mz
    a.add("Accounts", "a0", 1, Account_Name="A-First")
    for i in range(7):
        a.add("Accounts", f"t{i}", 2, Account_Name=f"A-Tie-{i}")
    await _connect_all(p, p.org_a, a)
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None), "the other modules read on"
    assert result.counts["organizations"]["failed"] == 1
    # Fix round: the run names the stall, and Sync health passes it through,
    # so the card can say that the sync of Accounts is paused.
    assert result.counts["organizations"]["tie_too_wide"] == 1
    row = _row_of(await _health(p.org_a), "organizations")
    assert row["last_run"]["tie_too_wide"] == 1
    assert "tie_too_wide" not in _row_of(await _health(p.org_a), "contacts")["last_run"]
    state, done_at = _cursor(p, p.org_a, "zoho:company")
    assert done_at is None, "initial_done_at must not move"
    assert state is not None and state["in_progress"] is True
    again = await _run(p.org_a)
    assert again.kind == "import", "the plan stays at import"
