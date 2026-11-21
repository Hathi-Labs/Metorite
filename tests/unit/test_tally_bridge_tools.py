"""TLY-1 PR 3: the read tools ``status`` and ``find_ledgers``, and the
company allow-list (brief sections 6.4 and 6.5 rule 4).

The tools run against a fake reader that serves SYNTHETIC answers. A pass
here is never a pass of brief tests 6, 7 or 8.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from defusedxml import ElementTree as SafeET

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from tally_bridge.reader import TallyDown, TallyRefused, TallyTimeout  # noqa: E402
from tally_bridge.reader.parse import parse_answer  # noqa: E402
from tally_bridge.settings import Settings  # noqa: E402
from tally_bridge.tools import build_read_server  # noqa: E402

SYNTHETIC = ROOT / "fixtures" / "synthetic"
COMPANIES = b"<ENVELOPE><COLLECTION><COMPANY NAME='Alpha Co'><NAME>Alpha Co</NAME></COMPANY><COMPANY NAME='Hidden Co'><NAME>Hidden Co</NAME></COMPANY></COLLECTION></ENVELOPE>"


class FakeReader:
    """Serves SYNTHETIC answers, and records each request."""

    def __init__(self, up: bool = True, fail: Exception | None = None) -> None:
        self.up = up
        self.fail = fail
        self.sent: list[bytes] = []

    async def is_up(self) -> bool:
        return self.up

    async def post(self, envelope: bytes):
        self.sent.append(envelope)
        if self.fail is not None:
            raise self.fail
        req = SafeET.fromstring(envelope)
        if req.find("HEADER/ID").text == "BridgeCompanies":
            return parse_answer(COMPANIES)
        return parse_answer((SYNTHETIC / "SYNTHETIC_ledgers.xml").read_bytes())


def _call(reader, settings: Settings, tool: str, args: dict | None = None):
    server = build_read_server(reader, settings)
    result = asyncio.run(
        server._mcp_server.request_handlers[_CallToolRequest](_req(tool, args or {}))
    )
    return result.root


from mcp import types as _t  # noqa: E402

_CallToolRequest = _t.CallToolRequest


def _req(tool: str, args: dict):
    return _t.CallToolRequest(
        method="tools/call", params=_t.CallToolRequestParams(name=tool, arguments=args)
    )


def _ok(result) -> dict:
    assert result.isError is not True, result.content[0].text
    return json.loads(result.content[0].text)


def _err(result) -> str:
    assert result.isError is True
    return result.content[0].text


ONE = Settings(allowed_companies=("Alpha Co",))
TWO = Settings(allowed_companies=("Alpha Co", "Beta Co"))


# --- The allow-list (rule 4) -------------------------------------------------------


def test_no_allowed_company_refuses_every_book_read():
    reader = FakeReader()
    text = _err(_call(reader, Settings(), "find_ledgers", {"query": "a"}))
    assert "No company is allowed yet" in text
    assert reader.sent == []


def test_one_allowed_company_needs_no_company_input():
    reader = FakeReader()
    body = _ok(_call(reader, ONE, "find_ledgers", {"query": "a"}))
    assert body["company"] == "Alpha Co"
    assert b"<SVCURRENTCOMPANY>Alpha Co</SVCURRENTCOMPANY>" in reader.sent[0]


def test_two_allowed_companies_need_a_name():
    reader = FakeReader()
    assert "Name the company" in _err(_call(reader, TWO, "find_ledgers", {"query": "a"}))
    body = _ok(_call(reader, TWO, "find_ledgers", {"query": "a", "company": "Beta Co"}))
    assert body["company"] == "Beta Co"


@pytest.mark.parametrize("name", ["Hidden Co", "alpha co", "Alpha Co ", "Alpha"])
def test_a_company_off_the_allow_list_is_refused_and_never_sent(name):
    reader = FakeReader()
    text = _err(_call(reader, ONE, "find_ledgers", {"query": "a", "company": name}))
    assert "is not allowed" in text
    assert reader.sent == []


# --- status ------------------------------------------------------------------------


def test_status_names_only_allowed_companies():
    body = _ok(_call(FakeReader(), TWO, "status"))
    assert body["tally"] == "up"
    assert body["companies"] == [
        {"name": "Alpha Co", "open": True},
        {"name": "Beta Co", "open": False},
    ]
    assert "Hidden Co" not in json.dumps(body)


def test_status_with_tally_down():
    assert _err(_call(FakeReader(up=False), ONE, "status")).startswith("TallyPrime is not running")


def test_status_when_the_company_list_is_refused_says_unknown():
    body = _ok(_call(FakeReader(fail=TallyRefused("x")), ONE, "status"))
    assert body["companies"] == [{"name": "Alpha Co", "open": None}]


# --- find_ledgers ---------------------------------------------------------------------


def test_find_ledgers_answers_amounts_with_a_side():
    body = _ok(_call(FakeReader(), ONE, "find_ledgers", {"query": "alpha"}))
    assert body == {
        "company": "Alpha Co",
        "row_count": 1,
        "truncated": False,
        "ledgers": [
            {
                "name": "Alpha Traders",
                "group": "Sundry Debtors",
                "closing": {"amount": "12500.50", "side": "Dr"},
            }
        ],
    }


def test_find_ledgers_by_group_and_limit():
    body = _ok(_call(FakeReader(), ONE, "find_ledgers", {"query": "", "limit": 1}))
    assert body["row_count"] == 1 and body["truncated"] is True
    body = _ok(_call(FakeReader(), ONE, "find_ledgers", {"query": "", "group": "cash-in-hand"}))
    assert [r["name"] for r in body["ledgers"]] == ["Cash"]
    assert body["ledgers"][0]["closing"] == {"amount": "1250.75", "side": "Dr"}


@pytest.mark.parametrize("query", ["<", '"', "'", "</NAME><x>", "]]>", "a<b&c\"d'e"])
def test_brief_test_10_special_characters_find_nothing_and_never_reach_tally(query):
    plain, reader = FakeReader(), FakeReader()
    _ok(_call(plain, ONE, "find_ledgers", {"query": "x"}))
    body = _ok(_call(reader, ONE, "find_ledgers", {"query": query}))
    assert body["row_count"] == 0
    # The request does not depend on the query at all.
    assert reader.sent == plain.sent


def test_an_ampersand_matches_a_real_ampersand_in_a_name():
    body = _ok(_call(FakeReader(), ONE, "find_ledgers", {"query": "&"}))
    assert [r["name"] for r in body["ledgers"]] == ["Beta & Sons"]


def test_find_ledgers_refuses_a_limit_over_fifty():
    result = _call(FakeReader(), ONE, "find_ledgers", {"query": "a", "limit": 51})
    assert result.isError is True


@pytest.mark.parametrize(
    "fail,sentence",
    [
        (TallyDown(), "TallyPrime is not running on the office machine."),
        (TallyTimeout(), "TallyPrime did not answer in 25 seconds. Try again in a minute."),
        (
            TallyRefused("Could not find Company"),
            "TallyPrime refused the request. Check that the company 'Alpha Co' is open in TallyPrime.",
        ),
    ],
)
def test_each_reader_error_is_one_plain_sentence(fail, sentence):
    result = _call(
        FakeReader(fail=fail), ONE, "find_ledgers", {"query": "a", "company": "Alpha Co"}
    )
    assert _err(result) == sentence


def test_a_hung_reader_stops_at_the_deadline():
    class Hung(FakeReader):
        async def post(self, envelope):
            await asyncio.sleep(3600)

    fast = Settings(allowed_companies=("Alpha Co",), tally_timeout_s=0.2, call_deadline_s=0.2)
    assert "did not answer in 0.2 seconds" in _err(
        _call(Hung(), fast, "find_ledgers", {"query": "a"})
    )


def test_the_tools_list_stays_small():
    server = build_read_server(FakeReader(), ONE)
    tools = asyncio.run(server.list_tools())
    size = len(json.dumps([t.model_dump(exclude_none=True) for t in tools]))
    assert size < 2500, size  # brief rule 8: about 6 KB for all 9 tools
