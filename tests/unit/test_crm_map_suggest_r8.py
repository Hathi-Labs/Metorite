"""R8 — the suggestion, the overlay and Undo, on a real Postgres
(WS-53 CRM-Q20-2, ``crm_platform.md`` §18.9.6, option A).

Each request runs as the NON-privileged role of the phase-4 rehearsal, so
RLS binds each read and each write. The ``decide`` facade is the scripted
fake of ``test_crm_map_pipeline_r8.py``: no real model call.

What this proves:

* item 5: the open suggestion of an empty slot lists its module, its
  fields and its stage, for an admin only;
* item 6: the preview shows real records of the module, through the
  executor, at most ``PREVIEW_ROWS``, and writes nothing;
* item 6 and option A: one approve makes the module play the slot, and
  ``GET /crm/mode`` names it in ``overlays`` for each member. No live rule
  changes, and the picks by hand of the open draft stay;
* item 7: Undo puts the slot back, exactly, and records a dismissal;
* the dismissal: a dismissed module is not offered again until the schema
  changes;
* tenancy (the narrow reviewer): ``overlays`` names only a module that the
  bound org mirrors, and never a module of another org;
* option A item 5: each core reader gives the same answer with and without
  an approved role.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _admin_of,
    _imported,
    _member_of,
    _req,
    fr,
)
from tests.unit.test_crm_map_pipeline_r8 import (  # noqa: F401
    PLANS,
    ROLE,
    STAGE,
    TARGET,
    _insert_version,
    _no_deals,
    _plans,
    _rule_rows,
    _run,
    _seed_module,
    _versions,
    mp_env,
)
from tests.unit.test_crm_map_preview_r8 import _fingerprint
from tests.unit.test_crm_sync_engine_r8 import _q, ns  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

SUGGEST = "/crm/mapping/suggestions"
PREVIEW = "/crm/mapping/preview"
REPO = Path(__file__).resolve().parents[2]


async def _suggested(p: Any, a: Any) -> dict[str, Any]:
    """Org A with an empty Deals slot and a module Commercials, after one run
    of the pipeline. Returns the one suggestion."""
    await _imported(p, p.org_a, a)
    _no_deals(p, p.org_a)
    _seed_module(p, p.org_a, "Commercials", "Commercials")
    _everyone(p, p.org_a)
    got = await _run(p.org_a)
    assert got.reason == "done" and got.draft is not None, got
    listed = await _req(_admin_of(p.org_a), "GET", SUGGEST)
    assert listed.status_code == 200, listed.text
    (one,) = listed.json()["suggestions"]
    return one


def _everyone(p: Any, *orgs: str) -> None:
    """Each member of *orgs* may open the CRM, so a member reads the mode."""
    with p.admin_engine.begin() as c:
        for org in orgs:
            c.execute(text("UPDATE crm_settings SET access = 'everyone' "
                           "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})


async def _mode(user: Any) -> dict[str, Any]:
    got = await _req(user, "GET", "/crm/mode")
    assert got.status_code == 200, got.text
    return got.json()


async def _approve(org: str, slot: str, version: int) -> Any:
    return await _req(_admin_of(org), "POST", f"{SUGGEST}/{slot}/approve", {"version": version})


def _approved_items(p: Any, org: str) -> list[dict[str, Any]]:
    (row,) = _q(p, "SELECT rules FROM crm_mapping_plans WHERE organization_id = "
                   "CAST(:o AS uuid) AND state = 'approved'", o=org)
    return [i for i in row[0] if i.get("kind", "field") != "field"]


def _dismissed(p: Any, org: str) -> Any:
    rows = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                 "CAST(:o AS uuid) AND module = 'zoho:map_dismissed'", o=org)
    return rows[0][0] if rows else None


# ── Item 5: the suggestion of an empty slot ──────────────────────────────────


async def test_5_the_suggestion_names_the_module_its_fields_and_its_stage(mp_env) -> None:
    p, a, _b = mp_env
    one = await _suggested(p, a)
    assert (one["slot"], one["module"], one["label"], one["records"]) == (
        "deal", "Commercials", "Commercials", 24)
    assert {t["target"]: t["labels"] for t in one["targets"]} == {
        "name": ["Commercial Name"], "expected_close_date": ["Closing Date"],
        "probability": ["Probability"]}
    assert one["stage"]["label"] == "Status"
    assert one["stage"]["values"]["Won"] == "won"
    # A member gets no suggestion.
    member = await _req(_member_of(p.org_a), "GET", SUGGEST)
    assert member.status_code == 403, member.text


async def test_5_a_slot_that_holds_records_gets_no_suggestion(mp_env) -> None:
    p, a, _b = mp_env
    await _suggested(p, a)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, ext_source, ext_id, status_id) "
            "SELECT CAST(:o AS uuid), 'Now in Zoho', 'zoho', 'd-new', id FROM crm_deal_statuses "
            "WHERE organization_id = CAST(:o AS uuid) LIMIT 1"), {"o": p.org_a})
    listed = (await _req(_admin_of(p.org_a), "GET", SUGGEST)).json()
    assert listed["suggestions"] == []


# ── Item 6: the preview shows real records and writes nothing ────────────────


async def test_6_the_preview_shows_the_records_of_the_module_and_writes_nothing(
    mp_env, monkeypatch,
) -> None:
    from gateway.routes.crm import mapping_plans

    p, a, _b = mp_env
    one = await _suggested(p, a)
    before = _fingerprint(p)
    got = await _req(_admin_of(p.org_a), "POST", PREVIEW,
                     {"version": one["version"], "slot": "deal"})
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["entities"] == []
    overlay = body["overlay"]
    assert (overlay["slot"], overlay["module"], overlay["total"]) == ("deal", "Commercials", 24)
    assert overlay["columns"] == ["name", "expected_close_date", "probability", "stage"]
    assert len(overlay["rows"]) == 24
    names = {r["values"]["name"] for r in overlay["rows"]}
    assert names == {f"Commercials {i}" for i in range(24)}
    stages = [r["values"]["stage"] for r in overlay["rows"]]
    assert {"won", "lost", "open"} <= set(stages) <= {"won", "lost", "open", None}
    # A stage value with no entry shows with no stage, and the count says so.
    assert overlay["unsorted"] == stages.count(None)
    assert _fingerprint(p) == before, "the preview wrote nothing"

    assert mapping_plans.PREVIEW_ROWS == 200
    monkeypatch.setattr(mapping_plans, "PREVIEW_ROWS", 5)
    small = (await _req(_admin_of(p.org_a), "POST", PREVIEW,
                        {"version": one["version"], "slot": "deal"})).json()["overlay"]
    assert len(small["rows"]) == 5 and small["total"] == 24

    # With no slot, the body of CRM-Q19 stays as it was.
    plain = (await _req(_admin_of(p.org_a), "POST", PREVIEW,
                        {"version": one["version"]})).json()
    assert "overlay" not in plain
    none = await _req(_admin_of(p.org_a), "POST", PREVIEW,
                      {"version": one["version"], "slot": "company"})
    assert none.status_code == 404 and none.json()["detail"]["code"] == "no_role"


# ── Item 6 and option A: one approve, and the overlay ────────────────────────


async def test_6_one_approve_plays_the_slot_and_each_member_reads_the_overlay(mp_env) -> None:
    p, a, _b = mp_env
    one = await _suggested(p, a)
    rules = _rule_rows(p, p.org_a)
    assert "overlays" not in await _mode(_admin_of(p.org_a))
    got = await _approve(p.org_a, "deal", one["version"])
    assert got.status_code == 200, got.text
    assert got.json()["module"] == "Commercials"
    kinds = sorted(i["kind"] for i in _approved_items(p, p.org_a))
    assert kinds == ["role", "stage", "target", "target", "target"]
    assert _rule_rows(p, p.org_a) == rules, "no live rule changed"
    assert (await _mode(_admin_of(p.org_a)))["overlays"] == {"deal": "Commercials"}
    assert (await _mode(_member_of(p.org_a)))["overlays"] == {"deal": "Commercials"}
    assert "overlays" not in await _mode(_admin_of(p.org_b)), "org B has no overlay"
    listed = (await _req(_admin_of(p.org_a), "GET", SUGGEST)).json()
    assert listed["suggestions"] == []
    assert listed["overlays"] == [{"slot": "deal", "module": "Commercials",
                                   "label": "Commercials"}]
    # The module list that the rail row opens answers.
    rows = await _req(_member_of(p.org_a), "GET", "/crm/modules/Commercials/records")
    assert rows.status_code == 200 and rows.json()["total"] == 24


async def test_6_an_approve_keeps_the_picks_by_hand_of_the_open_draft(mp_env) -> None:
    p, a, _b = mp_env
    await _suggested(p, a)
    hand = await _req(_admin_of(p.org_a), "POST", PLANS, {"changes": [
        {"op": "put", "entity": "leads", "field_api_name": "Why_Lost",
         "target_column": "lost_note"}]})
    assert hand.status_code == 201, hand.text
    (one,) = (await _req(_admin_of(p.org_a), "GET", SUGGEST)).json()["suggestions"]
    assert one["version"] == hand.json()["version"], "the hand draft holds the suggestion"
    got = await _approve(p.org_a, "deal", one["version"])
    assert got.status_code == 200, got.text
    approved = (await _plans(p.org_a))["approved"]
    _version, state, _by, based_on, items = _versions(p, p.org_a)[-1]
    assert (state, based_on) == ("draft", approved), "the open draft moved onto the new map"
    assert any(i.get("field_api_name") == "Why_Lost" for i in items), "the pick stays"


async def test_6_an_approve_of_a_draft_that_moved_is_409(mp_env) -> None:
    p, a, _b = mp_env
    one = await _suggested(p, a)
    old = _insert_version(p, p.org_a, "draft", [ROLE, TARGET], one["version"])
    got = await _approve(p.org_a, "deal", old)
    assert got.status_code == 409 and got.json()["detail"]["code"] == "plan_moved"


# ── Item 7: Undo, and the dismissal ──────────────────────────────────────────


async def test_7_undo_puts_the_slot_back_and_the_run_does_not_offer_it_again(mp_env) -> None:
    p, a, _b = mp_env
    one = await _suggested(p, a)
    rules = _rule_rows(p, p.org_a)
    assert (await _approve(p.org_a, "deal", one["version"])).status_code == 200
    undo = await _req(_admin_of(p.org_a), "POST", f"{SUGGEST}/deal/undo")
    assert undo.status_code == 200, undo.text
    assert _approved_items(p, p.org_a) == []
    assert _rule_rows(p, p.org_a) == rules, "Undo is exact: no record or rule moved"
    assert "overlays" not in await _mode(_member_of(p.org_a))
    assert (await _req(_admin_of(p.org_a), "GET", SUGGEST)).json() == {
        "suggestions": [], "overlays": []}
    state = _dismissed(p, p.org_a)
    assert state["dismissed"]["deal"]["module"] == "Commercials"
    assert state["dismissed"]["deal"]["schema"]

    calls, count = len(p.fake.calls), len(_versions(p, p.org_a))
    again = await _run(p.org_a)
    assert again.requests == 0 and len(p.fake.calls) == calls
    assert len(_versions(p, p.org_a)) == count, "the run wrote no draft again"
    second = await _req(_admin_of(p.org_a), "POST", f"{SUGGEST}/deal/undo")
    assert second.status_code == 404 and second.json()["detail"]["code"] == "no_overlay"


async def test_a_dismissed_module_comes_back_only_after_the_schema_changes(mp_env) -> None:
    p, a, _b = mp_env
    one = await _suggested(p, a)
    got = await _req(_admin_of(p.org_a), "POST", f"{SUGGEST}/deal/dismiss",
                     {"version": one["version"]})
    assert got.status_code == 200, got.text
    assert (await _req(_admin_of(p.org_a), "GET", SUGGEST)).json()["suggestions"] == []
    # The draft held only the suggestion, so no draft stays for the card.
    approved = (await _plans(p.org_a))["approved"]
    assert [v for v in _versions(p, p.org_a) if v[1] == "draft" and v[3] == approved] == []

    count = len(_versions(p, p.org_a))
    assert (await _run(p.org_a)).draft is None
    assert len(_versions(p, p.org_a)) == count, "the same schema: nothing is offered"

    # A new module changes the schema of the roles, so the module comes back.
    _seed_module(p, p.org_a, "Visits", "Visits", records=3)
    assert (await _run(p.org_a)).draft is not None
    (back,) = (await _req(_admin_of(p.org_a), "GET", SUGGEST)).json()["suggestions"]
    assert (back["slot"], back["module"]) == ("deal", "Commercials")


async def test_a_dismiss_keeps_the_picks_by_hand_of_the_draft(mp_env) -> None:
    p, a, _b = mp_env
    await _suggested(p, a)
    hand = await _req(_admin_of(p.org_a), "POST", PLANS, {"changes": [
        {"op": "put", "entity": "leads", "field_api_name": "Why_Lost",
         "target_column": "lost_note"}]})
    assert hand.status_code == 201, hand.text
    got = await _req(_admin_of(p.org_a), "POST", f"{SUGGEST}/deal/dismiss",
                     {"version": hand.json()["version"]})
    assert got.status_code == 200, got.text
    _version, state, _by, _based, items = _versions(p, p.org_a)[-1]
    assert state == "draft"
    assert any(i.get("field_api_name") == "Why_Lost" for i in items), "the pick stays"
    assert not [i for i in items if i.get("kind") in ("role", "target", "stage")]


# ── Tenancy: the overlays of the bound org only ──────────────────────────────


async def test_overlays_name_only_a_module_of_the_bound_org(mp_env) -> None:
    p, a, b = mp_env
    await _imported(p, p.org_a, a)
    await _imported(p, p.org_b, b)
    _seed_module(p, p.org_b, "Tender_Register", "Tender Register")
    _everyone(p, p.org_a, p.org_b)
    await _plans(p.org_a)
    await _plans(p.org_b)
    role = {"kind": "role", "slot": "deal", "module": "Tender_Register", "profile_version": 1}
    with p.admin_engine.begin() as c:
        for org in (p.org_a, p.org_b):
            c.execute(text(
                "UPDATE crm_mapping_plans SET rules = rules || CAST(:r AS jsonb) WHERE "
                "organization_id = CAST(:o AS uuid) AND state = 'approved'"),
                {"o": org, "r": json.dumps([role])})
    # Org B mirrors the module, so its rail points there.
    assert (await _mode(_member_of(p.org_b)))["overlays"] == {"deal": "Tender_Register"}
    # Org A names the same module in its plan, but does not mirror it: no overlay.
    assert "overlays" not in await _mode(_member_of(p.org_a))


# ── Option A item 5: each core reader stays as it is ─────────────────────────

#: The modules of the core readers. None of them reads the plan. CRM-Q20-3a
#: (§18.13.5) takes out aggregate.py and crm_tools.py on purpose: they read
#: the module of an approved overlay (``routes/crm/overlay.py``).
READERS = (
    "apps/services/gateway/gateway/routes/crm/records.py",
    "apps/services/gateway/gateway/routes/crm/export.py",
    "apps/services/gateway/gateway/routes/crm/activities.py",
    "apps/services/gateway/gateway/routes/crm/deal_contacts.py",
    "apps/services/gateway/gateway/routes/shell/search.py",
    "apps/services/gateway/gateway/routes/shell/feed.py",
)


def test_no_core_reader_names_the_plan_or_the_overlay() -> None:
    for rel in READERS:
        source = (REPO / rel).read_text(encoding="utf-8")
        for word in ("crm_mapping_plans", "mapping_plans", "approved_overlays",
                     "mapping/suggestions", "map_pipeline"):
            assert word not in source, f"{rel} names {word}"


async def _readers(p: Any, org: str) -> dict[str, Any]:
    """The answer of each core reader for an admin of *org*."""
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.crm.aggregate import my_day_rows
    from gateway.routes.shell.search import PROVIDERS

    admin = _admin_of(org)
    out: dict[str, Any] = {}
    for slug in ("leads", "contacts", "organizations", "deals"):
        got = await _req(admin, "GET", f"/crm/{slug}")
        assert got.status_code == 200, got.text
        out[slug] = got.json()
        csv = await _req(admin, "GET", f"/crm/export/{slug}.csv")
        assert csv.status_code == 200, csv.text
        out[f"export:{slug}"] = csv.text
        agg = await _req(admin, "GET", f"/crm/aggregate?entity={slug}&measure=count&group_by=owner")
        assert agg.status_code == 200, agg.text
        out[f"aggregate:{slug}"] = agg.json()
    company = out["organizations"]["rows"][0]["id"]
    related = await _req(admin, "GET", f"/crm/contacts?company_id={company}")
    assert related.status_code == 200, related.text
    out["related"] = related.json()
    token = bind_tenant(org)
    try:
        out["search"] = await PROVIDERS["crm"][2](admin, "Company")
        feed = await my_day_rows(admin, now=datetime(2026, 10, 17, 9, tzinfo=UTC), limit=20)
        out["feed"] = json.loads(json.dumps(feed, default=str))
    finally:
        release_tenant(token)
    return out


async def test_each_core_reader_gives_the_same_answer_with_and_without_a_role(mp_env) -> None:
    p, a, _b = mp_env
    await _imported(p, p.org_a, a)
    _seed_module(p, p.org_a, "Commercials", "Commercials")
    before = await _readers(p, p.org_a)
    assert before["deals"]["rows"] and before["search"], "the readers see records"

    approved = (await _plans(p.org_a))["approved"]
    draft = _insert_version(p, p.org_a, "draft", [ROLE, TARGET, STAGE], approved)
    got = await _req(_admin_of(p.org_a), "POST", f"{PLANS}/{draft}/approve")
    assert got.status_code == 200, got.text
    assert (await _mode(_admin_of(p.org_a)))["overlays"] == {"deal": "Commercials"}, (
        "the role is in use, so the comparison is not blind")

    after = await _readers(p, p.org_a)
    # CRM-Q20-3a item 7: the aggregate of deals now counts the records of the
    # module that plays Deals. Each other reader gives the answer of before.
    module = _q(p, "SELECT count(*) FROM crm_module_records WHERE organization_id = "
                   "CAST(:o AS uuid) AND module = 'Commercials'", o=p.org_a)[0][0]
    assert after.pop("aggregate:deals")["total"] == module > 0
    before.pop("aggregate:deals")
    assert after == before


# ── Round 2 review: a write on an older draft of the same base ───────────────


async def _older_sibling(p: Any, a: Any) -> tuple[dict[str, Any], int]:
    """The pipeline draft S, then a pick by hand H built on S. Both sit on
    the version in use. Returns the suggestion as the panel read it (on S)
    and the version of H."""
    one = await _suggested(p, a)
    hand = await _req(_admin_of(p.org_a), "POST", PLANS, {"changes": [
        {"op": "put", "entity": "leads", "field_api_name": "Why_Lost",
         "target_column": "lost_note"}]})
    assert hand.status_code == 201, hand.text
    newer = int(hand.json()["version"])
    assert newer > one["version"] and hand.json()["based_on"] == (await _plans(p.org_a))["approved"]
    return one, newer


def _open_items(p: Any, org: str) -> list[dict[str, Any]]:
    approved = _q(p, "SELECT version FROM crm_mapping_plans WHERE organization_id = "
                     "CAST(:o AS uuid) AND state = 'approved'", o=org)[0][0]
    rows = _q(p, "SELECT rules FROM crm_mapping_plans WHERE organization_id = "
                 "CAST(:o AS uuid) AND state = 'draft' AND based_on = :b "
                 "ORDER BY version DESC LIMIT 1", o=org, b=approved)
    return rows[0][0] if rows else []


async def test_r2_an_approve_of_an_older_draft_is_409_and_the_pick_survives(mp_env) -> None:
    """Round 2 review P1. The panel holds S, and a pick by hand wrote H on
    top of S. An approve of S used to pass, and ``carry_draft`` of S then
    dropped the pick of H with no word."""
    p, a, _b = mp_env
    stale, newer = await _older_sibling(p, a)
    count = len(_versions(p, p.org_a))
    got = await _approve(p.org_a, "deal", stale["version"])
    assert got.status_code == 409, got.text
    assert got.json()["detail"]["code"] == "plan_moved"
    assert len(_versions(p, p.org_a)) == count, "the refused approve wrote nothing"
    assert _approved_items(p, p.org_a) == []
    assert any(i.get("field_api_name") == "Why_Lost" for i in _open_items(p, p.org_a))
    # The suggestion read again names the open draft, and its approve keeps the pick.
    (fresh,) = (await _req(_admin_of(p.org_a), "GET", SUGGEST)).json()["suggestions"]
    assert fresh["version"] == newer
    assert (await _approve(p.org_a, "deal", newer)).status_code == 200
    assert any(i.get("field_api_name") == "Why_Lost" for i in _open_items(p, p.org_a))


async def test_r2_a_dismiss_of_an_older_draft_is_409_and_records_nothing(mp_env) -> None:
    """Round 2 review P2. A dismiss of S used to record the dismissal and
    skip ``carry_draft``, so the open draft H kept the items of the slot and
    the Field map card could approve them."""
    p, a, _b = mp_env
    stale, newer = await _older_sibling(p, a)
    got = await _req(_admin_of(p.org_a), "POST", f"{SUGGEST}/deal/dismiss",
                     {"version": stale["version"]})
    assert got.status_code == 409 and got.json()["detail"]["code"] == "plan_moved"
    assert _dismissed(p, p.org_a) is None, "nothing is recorded"
    # The open draft is dismissed through its own version, and it then loses
    # the items of the slot and keeps the pick.
    ok = await _req(_admin_of(p.org_a), "POST", f"{SUGGEST}/deal/dismiss", {"version": newer})
    assert ok.status_code == 200, ok.text
    items = _open_items(p, p.org_a)
    assert any(i.get("field_api_name") == "Why_Lost" for i in items)
    assert not [i for i in items if i.get("kind") in ("role", "target", "stage")]


async def test_r2_a_dismiss_during_a_run_does_not_come_back(mp_env, monkeypatch) -> None:
    """Round 2 review P2, made on purpose. A run reads the dismissals when it
    starts. The admin dismisses the suggestion while the run asks. The run
    then writes its cached items: the role must not come back."""
    from gateway.routes.crm import mapping_plans

    p, a, _b = mp_env
    one = await _suggested(p, a)
    real = mapping_plans.write_draft
    order: list[str] = []

    async def dismiss_first(db: Any, **kw: Any) -> Any:
        # The run has loaded its inputs and asked. The admin's dismiss
        # commits in its own session now, before the run writes.
        got = await _req(_admin_of(p.org_a), "POST", f"{SUGGEST}/deal/dismiss",
                         {"version": one["version"]})
        assert got.status_code == 200, got.text
        order.append("dismissed")
        row = await real(db, **kw)
        order.append("written" if row is not None else "nothing")
        return row

    monkeypatch.setattr(mapping_plans, "write_draft", dismiss_first)
    got = await _run(p.org_a)
    assert order == ["dismissed", "nothing"], order
    assert got.draft is None
    drafts = [v for v in _versions(p, p.org_a) if v[1] == "draft"
              and any(i.get("kind") == "role" for i in v[4])]
    # The dismiss deleted the draft, which held only the suggestion, and no
    # new draft brings the role back.
    assert drafts == []
    assert (await _req(_admin_of(p.org_a), "GET", SUGGEST)).json()["suggestions"] == []
