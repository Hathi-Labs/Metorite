"""The CRM mirror sync, rules that need no database (WS-53 CRM-Z4 and CRM-Z5).

``tests/unit/test_crm_sync_engine_r8.py`` runs the engine on a real Postgres.
This file holds the pure rules: the Zoho mapping, the run plan, the error
codes, and the fences on the package.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from gateway.crm_sources.base import (
    BudgetExhausted,
    NeedsReconnect,
    RateLimited,
    SourceError,
    UntrustedHost,
)
from gateway.crm_sources.zoho import mapping
from gateway.crm_sync import engine
from gateway.crm_sync.runs import CursorRow

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE = _ROOT / "apps" / "services" / "gateway" / "gateway" / "crm_sync"
NOW = datetime(2026, 10, 11, tzinfo=UTC)


# ── The mapping ──────────────────────────────────────────────────────────────

def test_a_company_maps_to_columns_and_the_rest_goes_to_custom() -> None:
    mapped = mapping.map_record(
        "company",
        {"id": "1", "Account_Name": " Acme ", "Employees": "12", "Annual_Revenue": 5,
         "Billing_City": "Pune", "Region": "south", "$state": "save",
         "Modified_Time": "2026-01-01T10:00:00+05:30", "Created_Time": "x",
         "Owner": {"id": "u1", "name": "Ann", "email": "Ann@X.com"}},
        modified_at=None, owners={},
    )
    v = mapped.values
    assert (v["name"], v["no_of_employees"], v["address"]) == ("Acme", 12, {"city": "Pune"})
    assert v["custom"] == {"Created_Time": "x", "Region": "south"}
    assert v["source"] == "import"
    assert v["ext_modified_at"] == datetime.fromisoformat("2026-01-01T10:00:00+05:30")
    assert (mapped.owner_email, mapped.owner_name) == ("ann@x.com", "Ann")
    assert mapped.table == "crm_companies"


def test_the_owner_email_comes_from_the_user_list_when_the_record_has_none() -> None:
    mapped = mapping.map_record(
        "deal", {"id": "1", "Owner": {"id": "u9", "name": "Bo"}},
        modified_at=None, owners={"u9": "bo@x.com"})
    assert mapped.owner_email == "bo@x.com"


def test_a_number_that_does_not_fit_becomes_null_and_the_record_still_maps() -> None:
    mapped = mapping.map_record(
        "deal", {"id": "7", "Amount": 10**15, "Probability": "x", "Closing_Date": "2026-02-03"},
        modified_at=None, owners={})
    assert mapped.values["amount"] is None and mapped.values["probability"] is None
    assert mapped.values["expected_close_date"].isoformat() == "2026-02-03"
    assert mapped.values["name"] == "Deal 7"
    assert "currency" not in mapped.values  # NOT NULL with a default: keep INR


def test_a_contact_with_only_a_surname_keeps_it() -> None:
    mapped = mapping.map_record(
        "contact", {"id": "1", "Last_Name": "Solo"}, modified_at=None, owners={})
    assert (mapped.values["first_name"], mapped.values["last_name"]) == ("Solo", None)


def test_the_links_of_each_entity() -> None:
    contact = mapping.map_record(
        "contact", {"id": "1", "Account_Name": {"id": "a9"}}, modified_at=None, owners={})
    assert [(k.ext_id, k.candidates) for k in contact.links] == [
        ("a9", (("company_id", "company"),))]
    deal = mapping.map_record(
        "deal", {"id": "1", "Contact_Name": {"id": "c1"}, "Stage": "Won"},
        modified_at=None, owners={})
    assert deal.deal_contact is not None and deal.deal_contact.ext_id == "c1"
    assert deal.status_name == "Won"
    note = mapping.map_record(
        "note", {"id": "1", "Parent_Id": {"id": "p1"}, "$se_module": "Leads"},
        modified_at=None, owners={})
    assert note.links[0].candidates == (("lead_id", "lead"),)
    assert "source" not in note.values  # crm_activities has no such column
    call = mapping.map_record(
        "call", {"id": "1", "Who_Id": {"id": "w"}, "What_Id": {"id": "x"},
                 "$se_module": "Deals"}, modified_at=None, owners={})
    assert [k.candidates[0][0] for k in call.links] == ["contact_id", "deal_id"]


def test_a_field_def_is_made_only_for_a_custom_field() -> None:
    custom = mapping.map_field_def("deal", {
        "api_name": "Region", "field_label": "Region", "data_type": "picklist",
        "pick_list_values": [{"display_value": "South", "actual_value": "s"}],
        "sequence_number": 4}, 0)
    assert custom is not None
    assert (custom["key"], custom["type"], custom["position"]) == ("Region", "select", 4)
    assert custom["options"] == [{"label": "South", "value": "s"}]
    assert mapping.map_field_def("deal", {"api_name": "Deal_Name"}, 0) is None
    assert mapping.map_field_def("deal", {"api_name": "$state"}, 0) is None


# ── The plan ─────────────────────────────────────────────────────────────────

def _done(entity: str, **kw: object) -> tuple[str, CursorRow]:
    module = engine.changed_module(entity)
    return module, CursorRow(module, None, kw.get("state"), NOW - timedelta(days=1),  # type: ignore[arg-type]
                             kw.get("reconciled"))  # type: ignore[arg-type]


def test_the_plan_is_an_import_until_each_entity_has_had_a_full_read() -> None:
    assert engine.plan({}, NOW, None)[0] == "import"
    cursors = dict(_done(e) for e in engine.ENTITY_ORDER if e != "meeting")
    assert engine.plan(cursors, NOW, None)[0] == "import"
    # An optional module that never read does not hold the plan at import (CRM-Q5).
    cursors = dict(_done(e) for e in engine.ENTITY_ORDER if e != "task")
    assert engine.plan(cursors, NOW, NOW)[0] == "incremental"
    cursors = dict(_done(e) for e in engine.ENTITY_ORDER)
    assert engine.plan(cursors, NOW, NOW) == ("incremental", frozenset())


def test_the_plan_reconciles_an_offset_paged_entity_at_most_once_in_24_hours() -> None:
    state = {"offset_paged": True}
    cursors = dict(_done(e) for e in engine.ENTITY_ORDER)
    cursors.update([_done("company", state=state, reconciled=None)])
    assert engine.plan(cursors, NOW, NOW) == ("reconcile", frozenset({"company"}))
    cursors.update([_done("company", state=state, reconciled=NOW - timedelta(hours=3))])
    assert engine.plan(cursors, NOW, NOW)[0] == "incremental"
    cursors.update([_done("company", state=state, reconciled=NOW - timedelta(hours=25))])
    assert engine.plan(cursors, NOW, NOW)[0] == "reconcile"


def test_the_plan_reconciles_everything_after_a_50_day_gap() -> None:
    cursors = dict(_done(e) for e in engine.ENTITY_ORDER)
    kind, entities = engine.plan(cursors, NOW, NOW - timedelta(days=51))
    assert kind == "reconcile" and entities == frozenset(engine.ENTITY_ORDER)


def test_the_cursor_round_trips_through_its_stored_state() -> None:
    from gateway.crm_sources.base import SourceCursor

    cursor = SourceCursor(since=NOW, page=3, page_token="tok", seen_ids=("a", "b"))
    assert engine.cursor_from(engine.cursor_state(cursor)) == cursor


# ── The error codes never carry free text ────────────────────────────────────

@pytest.mark.parametrize(("exc", "code"), [
    (NeedsReconnect("secret ACCESS-123 text"), "needs_reconnect"),
    (BudgetExhausted("x", used=1, budget=1), "budget"),
    (RateLimited("x", tries=5), "rate_limited"),
    (UntrustedHost("https://evil.example"), "untrusted_host"),
    (SourceError("Zoho said: customer Acme owes money"), "source_error"),
    (RuntimeError("customer Acme"), "internal"),
])
def test_the_error_code_is_a_short_safe_word(exc: Exception, code: str) -> None:
    assert engine.error_code(exc) == code


def test_a_provider_code_is_kept_only_when_it_looks_like_a_code() -> None:
    bad = SourceError("x")
    bad.code = "has spaces and Acme data"
    assert engine.error_code(bad) == "refresh_failed"
    good = SourceError("x")
    good.code = "INVALID_TOKEN"
    assert engine.error_code(good) == "INVALID_TOKEN"


# ── The fences on the package ────────────────────────────────────────────────

def _names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


def test_the_sync_uses_no_native_write_path_and_no_global_zoho_client() -> None:
    """The mirror writes through the two bypass functions and raw SQL. It never
    calls ``insert_row`` or ``update_row`` (CRM-Z6 refuses them in Mirror mode),
    never the legacy sync, and never an ``ingestion`` client."""
    files = sorted(_PACKAGE.glob("*.py"))
    assert {f.name for f in files} >= {"engine.py", "runs.py", "scheduler.py"}
    banned = {"insert_row", "update_row", "upsert_by_zoho_id", "sync_zoho",
              "execute_push", "run_cycle", "ingestion", "ingestion.sources.zoho"}
    for path in files:
        hit = _names(path) & banned
        assert not hit, f"{path.name} names {sorted(hit)}"


def test_the_engine_calls_no_zoho_client_directly() -> None:
    """It builds the adapter through the registry and names the provider only
    in constants."""
    names = _names(_PACKAGE / "engine.py")
    assert "ZohoSource" not in names
    assert "source_class" in names and "mapping_for" in names


def test_the_engine_is_the_caller_of_both_mirror_writes() -> None:
    names = _names(_PACKAGE / "engine.py")
    assert {"upsert_from_source", "delete_from_source"} <= names
