"""Task 3864: the CRM chat forgives a near name.

1. ``crm_list_fields`` with a query and no entity scans every module, and with
   neither argument refuses in words that name the two ways.
2. ``crm_list_records`` and ``crm_get_record`` serve a word that names ONE
   mirrored module ("invoices") as that module, and say so.
3. ``FieldScope.ref`` resolves ONE field label to its key, and the tool says so.
4. ``_mark_unavailable`` logs the change of state, not each pass.

Database-free, so it never skips.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import acb_skills.crm_tools as T
import pytest
from fastapi import HTTPException
from gateway.crm_sync import engine
from gateway.crm_sync.runs import CursorRow
from gateway.routes.crm import field_sql, fields

from tests.unit._crm_agent_fakes import FakeClient, FakeResponse, fake_gateway

_MODULES = [
    {"api_name": "Invoices", "label": "Invoices", "singular_label": "Invoice", "count": 9},
    {"api_name": "Payment_Made", "label": "Payments Made", "singular_label": "Payment Made",
     "count": 4},
    {"api_name": "Payment_Received", "label": "Payments Received",
     "singular_label": "Payment Received", "count": 3},
]
_CAPEX = {"key": "Capex_Amount", "label": "Capex", "type": "currency"}


class _Routed(FakeClient):
    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        path = "/" + url.split("/", 3)[-1]
        self._calls.append({"method": method, "path": path,
                            "params": dict(kwargs.get("params") or {})})
        answer = self._responder({"path": path, "params": kwargs.get("params") or {}})
        if isinstance(answer, tuple):
            return FakeResponse(answer[1], answer[0])
        return FakeResponse(answer)


def _gateway(monkeypatch, search_off: bool = True) -> list[dict]:
    def answer(call: dict) -> Any:
        path = call["path"]
        if path == T.FIELDS_SEARCH_PATH:
            return (404, {"detail": fields._find_off().detail})
        if path == "/crm/modules":
            return _MODULES
        if path == "/crm/fields/module:Invoices":
            return [_CAPEX, {"key": "Total", "label": "Total", "type": "currency"}]
        if path == "/crm/fields/deals":
            return [{"key": "cf_capex_plan", "label": "Plan", "type": "text"}]
        if path.startswith("/crm/fields/"):
            return []
        word = path.split("/")[3] if path.startswith("/crm/modules/") else ""
        if word.lower() in ("invoices", "invoice") and path.endswith("/records"):
            return {"module": "Invoices", "total": 1,
                    "field_defs": [{"key": "Total", "label": "Total", "type": "currency"}],
                    "rows": [{"id": "r1", "name": "INV-1", "owner_email": None}]}
        if word == "payment":
            return (404, {"detail": {"code": "unknown_module", "message":
                    "'payment' fits more than one module: Payment_Made, Payment_Received."}})
        return (404, {"detail": {"code": "unknown_module",
                                 "message": "This module is not mirrored."}})

    return fake_gateway(T, monkeypatch, answer, client_class=_Routed)


# ── 1. crm_list_fields with no entity ───────────────────────────────────────


def test_a_query_with_no_entity_scans_the_modules_and_the_core_types(monkeypatch) -> None:
    _gateway(monkeypatch)
    out = asyncio.run(T.crm_list_fields(query="CAPEX"))
    lines = out.splitlines()
    assert "- Invoices · Capex_Amount (Capex, currency)" in lines
    assert "- deals · cf_capex_plan (Plan, text)" in lines
    assert not any("Total" in ln for ln in lines[1:-1])
    assert lines[-1] == T.SCAN_HINT and ".has" not in out


def test_the_scan_is_capped_with_a_more_line(monkeypatch) -> None:
    many = [{"key": f"Capex_{i}", "label": f"Capex {i}", "type": "text"} for i in range(55)]

    def answer(call: dict) -> Any:
        if call["path"] == T.FIELDS_SEARCH_PATH:
            return (404, {"detail": fields._find_off().detail})
        return _MODULES if call["path"] == "/crm/modules" else (
            many if call["path"] == "/crm/fields/module:Invoices" else [])

    fake_gateway(T, monkeypatch, answer, client_class=_Routed)
    out = asyncio.run(T.crm_list_fields(query="capex"))
    assert len([ln for ln in out.splitlines() if ln.startswith("- ")]) == T.SCAN_LINES
    assert "15 more." in out


def test_a_scan_with_no_hit_says_so(monkeypatch) -> None:
    _gateway(monkeypatch)
    assert "No field of any module matches 'zzz'" in asyncio.run(T.crm_list_fields(query="zzz"))


def test_no_entity_and_no_query_names_the_two_ways(monkeypatch) -> None:
    _gateway(monkeypatch)
    out = asyncio.run(T.crm_list_fields())
    assert out.startswith("Refused:")
    assert "Give entity" in out and "give query" in out


# ── 2. an entity word that names a module ───────────────────────────────────


@pytest.mark.parametrize("word", ["invoices", "invoice", "Invoices", "INVOICE"])
def test_a_module_word_serves_the_module_and_says_which(monkeypatch, word) -> None:
    calls = _gateway(monkeypatch)
    out = asyncio.run(T.crm_list_records(word, custom={"Total": "5"}, page_size=5))
    assert "this is the module Invoices" in out and "INV-1" in out
    last = next(c for c in calls if c["path"].startswith("/crm/modules/"))
    assert last["params"]["custom.Total"] == "5" and last["params"]["page_size"] == 5


def test_a_core_type_is_not_a_module(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asyncio.run(T.crm_list_records("deals"))
    assert "/crm/modules" not in [c["path"] for c in calls]


def test_a_module_word_with_a_core_only_filter_is_refused(monkeypatch) -> None:
    _gateway(monkeypatch)
    out = asyncio.run(T.crm_list_records("invoices", status="Won"))
    assert out.startswith("Refused:") and "Unknown CRM record type" in out and "status" in out


def test_a_custom_key_that_the_module_lacks_is_named_not_dropped(monkeypatch) -> None:
    _gateway(monkeypatch)
    out = asyncio.run(T.crm_list_records("invoices", custom={"Total": "5", "Nope": "x"}))
    assert "Not applied: Nope is no field of Invoices" in out and "Total is no" not in out
    assert "Not applied" not in asyncio.run(T.crm_list_records("invoices", custom={"Total": "5"}))


def test_a_word_that_cannot_be_a_module_name_is_refused_with_no_read(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    out = asyncio.run(T.crm_list_records("../x/y"))
    assert out.startswith("Refused: Unknown CRM record type") and calls == []


def test_two_modules_or_none_keep_the_refusal_with_close_names(monkeypatch) -> None:
    _gateway(monkeypatch)
    out = asyncio.run(T.crm_list_records("payment"))
    assert out.startswith("Refused: Unknown CRM record type 'payment'")
    assert "Payment_Made" in out and "Payment_Received" in out
    none = asyncio.run(T.crm_list_records("zzzz"))
    assert none.startswith("Refused: Unknown CRM record type 'zzzz'")
    assert "This module is not mirrored." in none


def test_get_record_serves_a_module_word(monkeypatch) -> None:
    served: list[str] = []

    async def fake(mod: str, record: str, page: int) -> str:
        served.append(mod)
        return "RECORD"

    _gateway(monkeypatch)
    monkeypatch.setattr(T, "_module_record_answer", fake)
    out = asyncio.run(T.crm_get_record("invoice", "0f8fad5b-d9cb-469f-a165-70867728950e"))
    assert served == ["invoice"] and out.endswith("RECORD") and "the module invoice" in out


def test_the_one_resolver_takes_a_final_s_more_or_less() -> None:
    from gateway.routes.crm.modules import resolve_module

    mirrored = {"Invoices": {"api_name": "Invoices", "label": "Invoices"},
                "Quote": {"api_name": "Quote", "label": "Quote"}}
    assert resolve_module(mirrored, "invoice") == "Invoices"
    assert resolve_module(mirrored, "Quotes") == "Quote"


# ── 3. a field label as the key ─────────────────────────────────────────────


def _scope() -> field_sql.FieldScope:
    ref = field_sql.FieldRef
    return field_sql.FieldScope(
        column="fields", owner="module:Payment_Made", fields={
            "Party_Amount": ref(key="Party_Amount", type="currency", label="Rent Actual Amount"),
            "Rent": ref(key="Rent", type="currency", label="Rent Basic Amount"),
            "Dup_A": ref(key="Dup_A", type="text", label="Same Name"),
            "Dup_B": ref(key="Dup_B", type="text", label="same_name"),
        })


@pytest.mark.parametrize("asked", ["Rent_Actual_Amount", "rent actual-amount", "RENT ACTUAL AMOUNT"])
def test_one_label_resolves_to_its_key(asked) -> None:
    got = _scope().ref(asked, by_label=True)
    assert got.key == "Party_Amount" and got.via == asked
    assert field_sql.label_resolved(got) == {asked: "Party_Amount"}


def test_a_real_key_wins_and_has_no_via() -> None:
    assert _scope().ref("Rent").via is None


def test_two_labels_keep_the_422_with_its_list() -> None:
    with pytest.raises(HTTPException) as err:
        _scope().ref("same name", by_label=True)
    assert err.value.status_code == 422
    assert "is not a field of module:Payment_Made" in err.value.detail["message"]


def test_a_filter_name_is_not_label_resolved() -> None:
    """Only a measure and a group take a label: the filter readers use the asked name."""
    with pytest.raises(HTTPException):
        _scope().ref("Rent Actual Amount")


def test_a_core_scope_takes_no_label() -> None:
    ref = field_sql.FieldRef
    core = field_sql.FieldScope(column="custom", owner="deals", fields={
        "cf_a": ref(key="cf_a", type="number", label="Region Size")})
    with pytest.raises(HTTPException):
        field_sql.check_measure(core, "Region Size")


def test_check_measure_and_check_group_resolve_a_label() -> None:
    assert field_sql.check_measure(_scope(), "Rent Actual Amount").key == "Party_Amount"
    scope = field_sql.FieldScope(column="fields", owner="module:M", fields={
        "Kind": field_sql.FieldRef(key="Kind", type="select", label="Kind of Rent")})
    assert field_sql.check_group(scope, "kind of rent", None).key == "Kind"


def test_the_answer_names_the_key_it_used() -> None:
    note = T._resolved_note(
        {}, {"resolved": {"Rent_Actual_Amount": "Party_Amount"},
             "field_label": "Rent Actual Amount"})
    assert note == "\nUsed 'Party_Amount' (label 'Rent Actual Amount') for 'Rent_Actual_Amount'."
    assert T._resolved_note({}, {}) == ""


# ── 4. the hidden-module log fires on a change ──────────────────────────────


def _ctx() -> Any:
    @asynccontextmanager
    async def page():
        yield None

    return SimpleNamespace(
        recover=set(), page=page, cursors={}, counts={"deal": {}, "deals": {}, "lead": {}},
        module_step=lambda *a, **k: None)


def _mark(monkeypatch, state: dict, reason: str) -> CursorRow:
    async def save(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(engine, "_save_cursor", save)
    monkeypatch.setattr(engine, "_count_unavailable", lambda *a, **k: None)
    row = CursorRow("changed:deal", None, state, None, None)
    ctx = _ctx()
    asyncio.run(engine._mark_unavailable(ctx, "deal", row, reason))
    return next(iter(ctx.cursors.values()))


class _Log:
    """Stands in for the structlog logger of the engine."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def warning(self, message: str, *args: Any) -> None:
        self.lines.append(message % args)


@pytest.fixture
def caplog(monkeypatch):
    log = _Log()
    monkeypatch.setattr(engine, "_log", log)
    return log


def _logged(log: _Log) -> list[str]:
    return [m for m in log.lines if "crm.sync.module_unavailable" in m]


def test_the_first_hidden_pass_logs_once(monkeypatch, caplog) -> None:
    _mark(monkeypatch, {}, "hidden")
    assert len(_logged(caplog)) == 1


def test_the_same_reason_again_logs_nothing_and_keeps_the_state(monkeypatch, caplog) -> None:
    marked = _mark(monkeypatch, {"unavailable": True, "reason": "hidden"}, "hidden")
    assert _logged(caplog) == []
    assert marked.state["unavailable"] is True and marked.state["reason"] == "hidden"
    assert marked.state["in_progress"] is False and "checked_at" in marked.state


def test_a_new_reason_logs_again(monkeypatch, caplog) -> None:
    _mark(monkeypatch, {"unavailable": True, "reason": "scope"}, "hidden")
    assert len(_logged(caplog)) == 1


# ── 5. a module named by its label ("Collection" is Payment_Received) ───────

_COLLECTION = {
    "Payment_Received": {"api_name": "Payment_Received", "label": "Collection",
                         "singular_label": "Collections"},
    "Payment_Made": {"api_name": "Payment_Made", "label": "Payment Made",
                     "singular_label": "Payment Made"},
}


@pytest.mark.parametrize("word", ["Collection", "collections", "COLLECTION", "collection"])
def test_the_one_resolver_finds_a_module_by_its_label(word) -> None:
    from gateway.routes.crm.modules import resolve_module

    assert resolve_module(_COLLECTION, word) == "Payment_Received"


def test_a_truly_absent_module_keeps_the_404() -> None:
    from gateway.routes.crm.modules import resolve_module

    with pytest.raises(HTTPException) as err:
        resolve_module(_COLLECTION, "Zebra")
    assert "This module is not mirrored." in err.value.detail["message"]


def test_the_module_used_line() -> None:
    assert T._module_used("module:Collection", "Payment_Received") == (
        "Used module Payment_Received for 'Collection'.\n")
    assert T._module_used("module:Payment_Received", "Payment_Received") == ""
    assert T._module_used("Products", None) == ""


def test_an_aggregate_on_a_label_says_which_module_served(monkeypatch) -> None:
    def answer(call: dict) -> Any:
        return {"entity": "module:Payment_Received", "measure": "count", "groups": [],
                "total": 3.0, "records": 3}

    fake_gateway(T, monkeypatch, answer, client_class=_Routed)
    out = asyncio.run(T.crm_aggregate("module:Collection"))
    assert "Used module Payment_Received for 'Collection'." in out


# ── fix round: scan words, notes, module: prefix, label of a text number ────


def _scan_gateway(monkeypatch, mods, defs) -> None:
    def answer(call: dict) -> Any:
        path = call["path"]
        if path == T.FIELDS_SEARCH_PATH:
            return (404, {"detail": fields._find_off().detail})
        if path == "/crm/modules":
            return mods
        return defs.get(path, [])

    fake_gateway(T, monkeypatch, answer, client_class=_Routed)


_RENT = [{"key": "Rent_Basic_Amount", "label": "Rent Basic", "type": "currency"},
         {"key": "CapexAmount", "label": "Capex Total", "type": "currency"}]


@pytest.mark.parametrize("query", ["Rent_Basic_Amount", "rent basic", "capex amount", "CAPEX"])
def test_the_scan_matches_words_not_one_raw_substring(monkeypatch, query) -> None:
    _scan_gateway(monkeypatch, _MODULES[:1], {"/crm/fields/module:Invoices": _RENT})
    assert "- Invoices · " in asyncio.run(T.crm_list_fields(query=query))


def test_the_scan_needs_every_word(monkeypatch) -> None:
    _scan_gateway(monkeypatch, _MODULES[:1], {"/crm/fields/module:Invoices": _RENT})
    assert "No field of any module matches" in asyncio.run(
        T.crm_list_fields(query="rent zebra"))


def test_a_failed_module_list_is_named_in_both_answers(monkeypatch) -> None:
    def answer(call: dict) -> Any:
        if call["path"] == T.FIELDS_SEARCH_PATH:
            return (404, {"detail": fields._find_off().detail})
        if call["path"] == "/crm/modules":
            return (500, {"detail": "boom"})
        return [{"key": "cf_capex", "label": "Capex", "type": "text"}] if (
            call["path"] == "/crm/fields/deals") else []

    fake_gateway(T, monkeypatch, answer, client_class=_Routed)
    hit = asyncio.run(T.crm_list_fields(query="capex"))
    miss = asyncio.run(T.crm_list_fields(query="zebra"))
    assert "Not read: the module list." in hit and "Not read: the module list." in miss


def test_a_http_error_drops_one_part_and_names_it(monkeypatch) -> None:
    import httpx

    class Boom(_Routed):
        async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            if url.endswith("/crm/fields/leads"):
                raise httpx.ConnectError("down")
            return await super().request(method, url, **kwargs)

    def answer(call: dict) -> Any:
        if call["path"] == T.FIELDS_SEARCH_PATH:
            return (404, {"detail": fields._find_off().detail})
        return [] if call["path"] == "/crm/modules" else [
            {"key": "cf_capex", "label": "Capex", "type": "text"}]

    fake_gateway(T, monkeypatch, answer, client_class=Boom)
    out = asyncio.run(T.crm_list_fields(query="capex"))
    assert "Not read: leads." in out and "- deals · cf_capex" in out


def test_a_cut_module_list_says_how_many_were_read(monkeypatch) -> None:
    many = [{"api_name": f"M{i}", "label": f"M{i}"} for i in range(T.SCAN_MODULES + 5)]
    _scan_gateway(monkeypatch, many, {})
    miss = asyncio.run(T.crm_list_fields(query="zebra"))
    assert f"Read {T.SCAN_MODULES} of {T.SCAN_MODULES + 5} modules" in miss
    _scan_gateway(monkeypatch, many, {"/crm/fields/module:M0": _RENT})
    assert f"Read {T.SCAN_MODULES} of {T.SCAN_MODULES + 5} modules" in asyncio.run(
        T.crm_list_fields(query="capex"))


def test_the_module_prefix_is_a_module_word_for_a_record_tool(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    out = asyncio.run(T.crm_list_records("module:Invoices"))
    assert "INV-1" in out and any(c["path"].startswith("/crm/modules/Invoices") for c in calls)
    served: list[str] = []

    async def fake(mod: str, record: str, page: int) -> str:
        served.append(mod)
        return "RECORD"

    monkeypatch.setattr(T, "_module_record_answer", fake)
    asyncio.run(T.crm_get_record("module:Invoices", "0f8fad5b-d9cb-469f-a165-70867728950e"))
    assert served == ["Invoices"]
    assert asyncio.run(T.crm_list_records("module:../x")).startswith(
        "Refused: Unknown CRM record type")


def test_a_label_of_a_text_number_field_is_admitted_and_summed() -> None:
    ref = field_sql.FieldRef
    scope = field_sql.FieldScope(column="fields", owner="module:Payment_Made", fields={
        "Rent": ref(key="Rent", type="text", label="Rent Basic Amount"),
        "Total": ref(key="Total", type="currency", label="Total")})

    class DB:
        async def execute(self, *a: Any, **k: Any) -> Any:
            class R:
                def fetchall(self) -> list:
                    return [(0, 10, 0, 0, None, None)]
            return R()

    import gateway.routes.crm.core as core

    monkey = pytest.MonkeyPatch()
    monkey.setattr(core, "current_tenant", lambda: "00000000-0000-0000-0000-000000000001")
    try:
        keys = field_sql.text_number_keys("sum", "Rent Basic Amount", None, [])
        s2, counts = asyncio.run(field_sql.admit_text_numbers(DB(), scope, keys, measured=keys))
    finally:
        monkey.undo()
    got = field_sql.check_measure(s2, "Rent Basic Amount")
    assert got.key == "Rent" and got.text_number and "Rent" in counts


def test_a_split_of_one_field_by_key_and_label_is_not_distinct() -> None:
    with pytest.raises(HTTPException):
        field_sql.check_split(_scope(), "Rent,Rent Basic Amount")
