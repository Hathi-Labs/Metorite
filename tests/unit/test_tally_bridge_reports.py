"""TLY-1 PR 3: the report tools of brief section 6.4 (tools 3 to 9).

Each tool runs against a fake reader that serves SYNTHETIC answers. A pass
here is never a pass of brief tests 6, 7 or 8: those need the real Tally
screen and the recorded answers of the README.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from defusedxml import ElementTree as SafeET
from mcp import types

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from tally_bridge.reader.parse import parse_answer  # noqa: E402
from tally_bridge.settings import Settings  # noqa: E402
from tally_bridge.tools import build_read_server  # noqa: E402

SYNTHETIC = ROOT / "fixtures" / "synthetic"
ANSWERS = {
    "Trial Balance": "SYNTHETIC_trial_balance.xml",
    "Profit and Loss": "SYNTHETIC_profit_and_loss.xml",
    "Ledger Vouchers": "SYNTHETIC_ledger_vouchers.xml",
    "Bills Receivable": "SYNTHETIC_bills_receivable.xml",
    "Bills Payable": "SYNTHETIC_bills_receivable.xml",
    "Day Book": "SYNTHETIC_day_book.xml",
    "Stock Summary": "SYNTHETIC_stock_summary.xml",
}
GUID = "a1b2c3d4-0000-1111-2222-333344445555-00000065"
ONE = Settings(allowed_companies=("Alpha Co",))


class FakeReader:
    def __init__(self) -> None:
        self.sent: list = []

    async def is_up(self) -> bool:
        return True

    async def post(self, envelope: bytes):
        req = SafeET.fromstring(envelope)
        self.sent.append(req)
        kind = req.find("HEADER/TYPE").text
        if kind == "Object":
            return _one_voucher(req.find("HEADER/ID").text)
        return parse_answer((SYNTHETIC / ANSWERS[req.find("HEADER/ID").text]).read_bytes())


def _one_voucher(guid: str):
    root = parse_answer((SYNTHETIC / "SYNTHETIC_day_book.xml").read_bytes())
    msg = root.find("BODY/DATA/TALLYMESSAGE")
    for v in list(msg):
        if v.findtext("GUID") != guid:
            msg.remove(v)
    return root


def call(tool: str, args: dict, settings: Settings = ONE, reader: FakeReader | None = None):
    server = build_read_server(reader or FakeReader(), settings)
    req = types.CallToolRequest(
        method="tools/call", params=types.CallToolRequestParams(name=tool, arguments=args)
    )
    return asyncio.run(server._mcp_server.request_handlers[types.CallToolRequest](req)).root


def ok(result) -> dict:
    assert result.isError is not True, result.content[0].text
    return json.loads(result.content[0].text)


def err(result) -> str:
    assert result.isError is True
    return result.content[0].text


def _statics(req) -> dict[str, str]:
    return {el.tag: el.text for el in req.find("BODY/DESC/STATICVARIABLES")}


# --- trial_balance -------------------------------------------------------------


def test_trial_balance_from_the_start_of_the_financial_year():
    reader = FakeReader()
    body = ok(call("trial_balance", {"as_of": "2026-03-31"}, reader=reader))
    sv = _statics(reader.sent[0])
    assert (sv["SVFROMDATE"], sv["SVTODATE"]) == ("20250401", "20260331")
    assert body["groups"] == [
        {"name": "Capital Account", "debit": "0.00", "credit": "500000.00"},
        {"name": "Sundry Debtors", "debit": "12500.50", "credit": "0.00"},
        {"name": "Cash-in-Hand", "debit": "1250.75", "credit": "0.00"},
    ]
    assert body["as_of"] == "2026-03-31" and body["company"] == "Alpha Co"


def test_trial_balance_in_april_starts_the_same_april():
    reader = FakeReader()
    ok(call("trial_balance", {"as_of": "2026-04-10"}, reader=reader))
    assert _statics(reader.sent[0])["SVFROMDATE"] == "20260401"


# --- profit_and_loss -------------------------------------------------------------


def test_profit_and_loss_groups_and_net():
    body = ok(call("profit_and_loss", {"from_date": "2025-04-01", "to_date": "2026-03-31"}))
    assert body["groups"] == [
        {"name": "Sales Accounts", "amount": "90000.00", "side": "Cr"},
        {"name": "Purchase Accounts", "amount": "60000.00", "side": "Dr"},
        {"name": "Indirect Expenses", "amount": "5000.25", "side": "Dr"},
    ]
    assert body["net"] == {"amount": "24999.75", "side": "Cr", "result": "profit"}


def test_profit_and_loss_refuses_more_than_a_year():
    text = err(call("profit_and_loss", {"from_date": "2024-04-01", "to_date": "2026-03-31"}))
    assert text == "The range is 730 days. Ask for 366 days or less."


# --- ledger_statement (brief test 9) -------------------------------------------------


def test_ledger_statement_rows():
    reader = FakeReader()
    body = ok(
        call(
            "ledger_statement",
            {"ledger": "Alpha Traders", "from_date": "2026-01-01", "to_date": "2026-01-31"},
            reader=reader,
        )
    )
    assert _statics(reader.sent[0])["LEDGERNAME"] == "Alpha Traders"
    assert body["rows"] == [
        {
            "date": "2026-01-03",
            "voucher_type": "Sales",
            "number": "101",
            "ledger": "Sales",
            "narration": "Invoice 101",
            "amount": "11800.00",
            "side": "Dr",
        },
        {
            "date": "2026-01-15",
            "voucher_type": "Receipt",
            "number": "7",
            "ledger": "Bank",
            "narration": "",
            "amount": "10000.00",
            "side": "Cr",
        },
    ]
    # Not proven yet: Tally's opening and closing wait for recording R06.
    assert body["opening"] is None and body["closing"] is None


def test_brief_test_9_a_range_of_120_days_is_refused_by_name():
    reader = FakeReader()
    text = err(
        call(
            "ledger_statement",
            {"ledger": "Cash", "from_date": "2026-01-01", "to_date": "2026-04-30"},
            reader=reader,
        )
    )
    assert text == "The range is 120 days. Ask for 92 days or less."
    assert reader.sent == []


@pytest.mark.parametrize(
    "args,sentence",
    [
        ({"from_date": "01-01-2026", "to_date": "2026-01-31"}, "Write from_date as YYYY-MM-DD."),
        (
            {"from_date": "2026-02-01", "to_date": "2026-01-31"},
            "The end date is before the start date.",
        ),
    ],
)
def test_bad_dates_are_one_plain_sentence(args, sentence):
    assert err(call("ledger_statement", {"ledger": "Cash", **args})) == sentence


# --- outstanding ------------------------------------------------------------------------


def test_outstanding_receivable_with_age_and_total():
    body = ok(call("outstanding", {"side": "receivable", "as_of": "2026-03-31"}))
    assert body["total"] == {"amount": "12500.00", "side": "Dr"}
    assert body["bills"][0] == {
        "date": "2026-01-01",
        "ref": "INV-101",
        "party": "Alpha Traders",
        "due": "2026-01-31",
        "age_days": 59,
        "amount": "11800.00",
        "side": "Dr",
    }
    # No due date: the age counts from the bill date.
    assert body["bills"][1]["due"] is None and body["bills"][1]["age_days"] == 21


def test_outstanding_filters_one_party_in_the_bridge():
    reader = FakeReader()
    body = ok(
        call(
            "outstanding",
            {"side": "payable", "as_of": "2026-03-31", "party": "beta & sons"},
            reader=reader,
        )
    )
    assert reader.sent[0].find("HEADER/ID").text == "Bills Payable"
    assert [b["ref"] for b in body["bills"]] == ["INV-140"]
    assert b"beta" not in SafeET.tostring(reader.sent[0])


def test_outstanding_refuses_another_side():
    assert err(call("outstanding", {"side": "both", "as_of": "2026-03-31"})) == (
        "Set side to receivable or payable."
    )


# --- list_vouchers and get_voucher --------------------------------------------------------


def test_list_vouchers_filters_and_pages():
    body = ok(
        call(
            "list_vouchers",
            {"from_date": "2026-01-01", "to_date": "2026-01-31", "party": "Alpha Traders"},
        )
    )
    assert [v["number"] for v in body["vouchers"]] == ["101", "7"]
    assert body["vouchers"][0] == {
        "number": "101",
        "date": "2026-01-03",
        "type": "Sales",
        "party": "Alpha Traders",
        "amount": "11800.00",
        "side": "Dr",
        "guid": GUID,
    }
    assert "next_cursor" not in body
    body = ok(
        call(
            "list_vouchers",
            {"from_date": "2026-01-01", "to_date": "2026-01-31", "voucher_type": "payment"},
        )
    )
    assert [v["number"] for v in body["vouchers"]] == ["3"]
    # A voucher with no party answers its largest entry.
    assert body["vouchers"][0]["amount"] == "2500.00"


def test_list_vouchers_refuses_more_than_31_days_and_a_bad_cursor():
    assert "Ask for 31 days or less" in err(
        call("list_vouchers", {"from_date": "2026-01-01", "to_date": "2026-02-28"})
    )
    assert "cursor is not valid" in err(
        call("list_vouchers", {"from_date": "2026-01-01", "to_date": "2026-01-31", "cursor": "-1"})
    )


def test_list_vouchers_cursor_moves_the_page(monkeypatch):
    from tally_bridge.tools import reports

    monkeypatch.setattr(reports, "PAGE", 2)
    args = {"from_date": "2026-01-01", "to_date": "2026-01-31"}
    first = ok(call("list_vouchers", args))
    assert first["row_count"] == 2 and first["truncated"] is True and first["next_cursor"] == "2"
    second = ok(call("list_vouchers", {**args, "cursor": first["next_cursor"]}))
    assert [v["number"] for v in second["vouchers"]] == ["3"] and "next_cursor" not in second


def test_get_voucher_by_guid():
    reader = FakeReader()
    body = ok(call("get_voucher", {"guid": GUID}, reader=reader))
    header = reader.sent[0].find("HEADER")
    assert (header.find("TYPE").text, header.find("ID").get("TYPE")) == ("Object", "GUID")
    assert body["number"] == "101" and body["party"] == "Alpha Traders"
    assert body["ledger_entries"][1] == {"ledger": "Sales", "amount": "10000.00", "side": "Cr"}
    assert body["stock_entries"] == [
        {
            "item": "Widget",
            "quantity": "10 Nos",
            "rate": "1000.00/Nos",
            "amount": "10000.00",
            "side": "Cr",
        }
    ]


@pytest.mark.parametrize(
    "guid",
    ["", "abc", "a1b2c3d4</ID><TALLYREQUEST>", "x" * 60, GUID + "\n", " " + GUID, GUID + " "],
)
def test_get_voucher_refuses_text_that_is_not_a_guid(guid):
    reader = FakeReader()
    assert "not a Tally voucher GUID" in err(call("get_voucher", {"guid": guid}, reader=reader))
    assert reader.sent == []


def test_get_voucher_with_no_match():
    text = err(call("get_voucher", {"guid": "ffffffff-0000-1111-2222-333344445555-00000001"}))
    assert text == "No voucher matches in this company."


# --- stock_summary ---------------------------------------------------------------------------


def test_stock_summary_splits_quantity_unit_and_rate():
    body = ok(call("stock_summary", {"as_of": "2026-03-31", "item": "gadget"}))
    assert body["items"] == [
        {
            "item": "Gadget Box",
            "quantity": "5",
            "unit": "Box",
            "rate": "150.50",
            "value": {"amount": "752.50", "side": "Dr"},
        }
    ]


# --- Every tool ------------------------------------------------------------------------------


CALLS = [
    ("trial_balance", {"as_of": "2026-03-31"}),
    ("profit_and_loss", {"from_date": "2025-04-01", "to_date": "2026-03-31"}),
    ("ledger_statement", {"ledger": "Cash", "from_date": "2026-01-01", "to_date": "2026-01-31"}),
    ("outstanding", {"side": "receivable", "as_of": "2026-03-31"}),
    ("list_vouchers", {"from_date": "2026-01-01", "to_date": "2026-01-31"}),
    ("get_voucher", {"guid": GUID}),
    ("stock_summary", {"as_of": "2026-03-31"}),
]


@pytest.mark.parametrize("tool,args", CALLS)
def test_each_tool_answers_company_row_count_and_truncated(tool, args):
    body = ok(call(tool, args))
    assert body["company"] == "Alpha Co"
    assert isinstance(body["row_count"], int) and isinstance(body["truncated"], bool)


@pytest.mark.parametrize("tool,args", CALLS)
def test_each_tool_refuses_a_company_off_the_allow_list(tool, args):
    reader = FakeReader()
    assert "is not allowed" in err(call(tool, {**args, "company": "Other Co"}, reader=reader))
    assert reader.sent == []


def test_an_answer_over_20_kb_is_refused(monkeypatch):
    from tally_bridge.tools import common

    monkeypatch.setattr(common, "MAX_ANSWER_BYTES", 100)
    assert err(call("trial_balance", {"as_of": "2026-03-31"})) == common.TOO_LARGE


def test_all_nine_tools_fit_the_size_budget():
    server = build_read_server(FakeReader(), ONE)
    tools = asyncio.run(server.list_tools())
    assert len(tools) == 9
    body = json.dumps(
        {"tools": [t.model_dump(exclude_none=True, by_alias=True) for t in tools]},
        separators=(",", ":"),
    )
    # Brief section 6.4 rule 8: about 1,500 tokens, so about 6 KB for all tools.
    assert len(body) < 5000, len(body)
    for t in tools:
        assert "title" not in t.inputSchema
        for prop in t.inputSchema.get("properties", {}).values():
            assert "anyOf" not in prop and "title" not in prop


@pytest.mark.parametrize(
    "as_of", ["20260331", " 2026-03-31", "2026-03-31 ", "2026-3-31", "2026-03-31T00:00"]
)
def test_a_date_must_be_exactly_yyyy_mm_dd(as_of):
    reader = FakeReader()
    assert (
        err(call("trial_balance", {"as_of": as_of}, reader=reader)) == "Write as_of as YYYY-MM-DD."
    )
    assert reader.sent == []


@pytest.mark.parametrize(
    "tool,args,sentence",
    [
        ("trial_balance", {}, "Check these inputs: as_of."),
        ("ledger_statement", {"ledger": "Cash"}, "Check these inputs: from_date, to_date."),
        ("find_ledgers", {"query": "a", "limit": "many"}, "Check these inputs: limit."),
    ],
)
def test_a_missing_or_wrong_input_is_one_plain_sentence(tool, args, sentence):
    reader = FakeReader()
    assert err(call(tool, args, reader=reader)) == sentence
    assert reader.sent == []


# --- get_voucher by voucher type and number ------------------------------------------


class ByNumberReader(FakeReader):
    """Answers the by-number collection from the SYNTHETIC day book, filtered
    by the two static variables, as Tally's fixed formula would."""

    async def post(self, envelope: bytes):
        req = SafeET.fromstring(envelope)
        self.sent.append(req)
        if req.find("HEADER/ID").text != "BridgeVoucherByNumber":
            return await super().post(envelope)
        sv = req.find("BODY/DESC/STATICVARIABLES")
        want = (sv.findtext("BridgeVchType"), sv.findtext("BridgeVchNumber"))
        root = parse_answer((SYNTHETIC / "SYNTHETIC_day_book.xml").read_bytes())
        msg = root.find("BODY/DATA/TALLYMESSAGE")
        for v in list(msg):
            if (v.findtext("VOUCHERTYPENAME"), v.findtext("VOUCHERNUMBER")) != want:
                msg.remove(v)
        return root


def test_get_voucher_by_type_and_number():
    reader = ByNumberReader()
    body = ok(call("get_voucher", {"voucher_type": "Sales", "number": "101"}, reader=reader))
    assert body["guid"] == GUID and body["party"] == "Alpha Traders"
    req = reader.sent[0]
    assert req.find("HEADER/ID").text == "BridgeVoucherByNumber"
    assert [el.text for el in req.iter("TALLYREQUEST")] == ["Export"]


@pytest.mark.parametrize(
    "number", ['101" OR 1=1', "##BridgeVchType", "$$SysName:XML", "</BridgeVchNumber><x>", "a & b"]
)
def test_the_type_and_number_stay_values_and_never_enter_the_formula(number):
    reader = ByNumberReader()
    call("get_voucher", {"voucher_type": "Sales", "number": number}, reader=reader)
    req = reader.sent[0]
    assert req.find("BODY/DESC/STATICVARIABLES/BridgeVchNumber").text == number
    formula = req.find("BODY/DESC/TDL/TDLMESSAGE/SYSTEM").text
    assert formula == "$VoucherTypeName = ##BridgeVchType AND $VoucherNumber = ##BridgeVchNumber"


@pytest.mark.parametrize(
    "args,sentence",
    [
        ({}, "Give the guid, or the voucher_type and the number."),
        ({"guid": GUID, "number": "101"}, "Give the guid, or the voucher_type and the number."),
        ({"number": "101"}, "Give both the voucher_type and the number."),
        ({"voucher_type": "Sales", "number": ""}, "Give both the voucher_type and the number."),
        ({"voucher_type": "Sales", "number": "9"}, "No voucher matches in this company."),
    ],
)
def test_get_voucher_refuses_an_unclear_ask(args, sentence):
    assert err(call("get_voucher", args, reader=ByNumberReader())) == sentence


def test_two_vouchers_with_one_number_ask_for_the_guid():
    class Twice(ByNumberReader):
        async def post(self, envelope: bytes):
            root = await super().post(envelope)
            msg = root.find("BODY/DATA/TALLYMESSAGE")
            msg.append(next(iter(msg)))
            return root

    text = err(call("get_voucher", {"voucher_type": "Sales", "number": "101"}, reader=Twice()))
    assert text == "2 vouchers have that type and number. Ask with the GUID from list_vouchers."
