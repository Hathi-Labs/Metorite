"""TLY-4: the local copy of the bridge (``integrations_console.md`` §3.8.13).

Every Tally answer here is SYNTHETIC. A pass here proves the logic on Linux.
It is never a pass on TallyPrime: recording R13 and the office PC test that.
The owner's answers on task 3769: no copy inside Metorite (LNK-Q8), a copy
every 30 minutes and a maximum age of 7 days (LNK-Q9).
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from defusedxml import ElementTree as SafeET

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from mcp import types as _t  # noqa: E402
from tally_bridge.localcopy import CopyStore, DevFileKeyProvider, MemoryKeyProvider  # noqa: E402
from tally_bridge.localcopy.schedule import one_tick  # noqa: E402
from tally_bridge.localcopy.store import Snapshot  # noqa: E402
from tally_bridge.localcopy.sync import copy_window, months, sync_company  # noqa: E402
from tally_bridge.reader import TallyDown, envelope, records  # noqa: E402
from tally_bridge.reader.parse import parse_answer  # noqa: E402
from tally_bridge.reader.values import to_tally_date  # noqa: E402
from tally_bridge.settings import Settings, SettingsError  # noqa: E402
from tally_bridge.tools import build_read_server  # noqa: E402

SYNTHETIC = ROOT / "fixtures" / "synthetic"
CO = "Alpha Co"
ONE = Settings(allowed_companies=(CO,))
TODAY = date(2026, 11, 22)
G1 = "a1b2c3d4-0000-1111-2222-333344445555-00000065"
G2 = "a1b2c3d4-0000-1111-2222-333344445555-00000066"


def _vch(guid: str, on: date, vtype: str, number: str, alter: int, amount: str = "100.00") -> str:
    return (
        f'<VOUCHER VCHTYPE="{vtype}"><GUID>{guid}</GUID><ALTERID>{alter}</ALTERID>'
        f"<DATE>{to_tally_date(on)}</DATE><VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME>"
        f"<VOUCHERNUMBER>{number}</VOUCHERNUMBER><PARTYLEDGERNAME>Alpha Traders</PARTYLEDGERNAME>"
        f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>Alpha Traders</LEDGERNAME><AMOUNT>-{amount}"
        f"</AMOUNT></ALLLEDGERENTRIES.LIST><ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME>"
        f"<AMOUNT>{amount}</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER>"
    )


class Books:
    """A SYNTHETIC TallyPrime: ledgers from the fixture, and vouchers that the
    test adds. It answers BridgeVouchersSince by its three variables, as the
    TDL filter does."""

    def __init__(self) -> None:
        self.up = True
        self.vouchers: dict[str, tuple[date, int, str]] = {}
        self.sent: list = []

    def put(self, guid: str, on: date, vtype: str, number: str, alter: int, amount="100.00"):
        self.vouchers[guid] = (on, alter, _vch(guid, on, vtype, number, alter, amount))

    async def is_up(self) -> bool:
        return self.up

    async def post(self, env: bytes):
        req = SafeET.fromstring(env)
        self.sent.append(req)
        if not self.up:
            raise TallyDown
        name = req.findtext("HEADER/ID")
        if name == "BridgeLedgers":
            return parse_answer((SYNTHETIC / "SYNTHETIC_ledgers.xml").read_bytes())
        assert name == "BridgeVouchersSince", name
        sv = req.find("BODY/DESC/STATICVARIABLES")
        after = int(sv.findtext("BridgeAfterAlter"))
        lo = datetime.strptime(sv.findtext("BridgeFromDate"), "%Y%m%d").date()
        hi = datetime.strptime(sv.findtext("BridgeToDate"), "%Y%m%d").date()
        body = "".join(x for on, alt, x in self.vouchers.values() if alt > after and lo <= on <= hi)
        return parse_answer(
            f"<ENVELOPE><BODY><DATA><COLLECTION>{body}</COLLECTION></DATA></BODY></ENVELOPE>".encode()
        )


def _since_requests(books: Books) -> list:
    return [r for r in books.sent if r.findtext("HEADER/ID") == "BridgeVouchersSince"]


@pytest.fixture
def store(tmp_path):
    return CopyStore(tmp_path / "copy", MemoryKeyProvider())


def _sync(books, store, **kw):
    return asyncio.run(sync_company(books, store, CO, today=TODAY, **kw))


# --- The request -------------------------------------------------------------------


def test_the_since_request_carries_only_typed_variables():
    raw = envelope.vouchers_since(41, date(2026, 1, 1), date(2026, 1, 31), company=CO)
    req = SafeET.fromstring(raw)
    sv = req.find("BODY/DESC/STATICVARIABLES")
    assert sv.findtext("BridgeAfterAlter") == "41"
    assert sv.findtext("BridgeFromDate") == "20260101"
    assert sv.findtext("BridgeToDate") == "20260131"
    # The formula is fixed text. It names the variables and no value.
    formula = req.find(".//SYSTEM[@NAME='BridgeVchSince']").text
    assert "41" not in formula and "2026" not in formula


@pytest.mark.parametrize("bad", [-1, "5", 1.5, None])
def test_the_since_request_refuses_a_bad_alter_id(bad):
    with pytest.raises(ValueError):
        envelope.vouchers_since(bad, date(2026, 1, 1), date(2026, 1, 31), company=CO)


def test_a_voucher_reads_its_alter_id():
    root = parse_answer(
        f"<ENVELOPE><BODY><DATA><COLLECTION>{_vch(G1, TODAY, 'Sales', '1', 77)}"
        f"</COLLECTION></DATA></BODY></ENVELOPE>".encode()
    )
    assert records.vouchers(root)[0].alter_id == 77


# --- The window --------------------------------------------------------------------


def test_the_window_is_this_financial_year_and_the_last():
    assert copy_window(date(2026, 11, 22)) == (date(2025, 4, 1), date(2026, 11, 22))
    assert copy_window(date(2026, 3, 31)) == (date(2024, 4, 1), date(2026, 3, 31))
    assert copy_window(date(2026, 4, 1)) == (date(2025, 4, 1), date(2026, 4, 1))


def test_the_first_copy_asks_one_month_at_a_time():
    cut = months(date(2025, 4, 1), date(2026, 11, 22))
    assert len(cut) == 20
    assert cut[0] == (date(2025, 4, 1), date(2025, 4, 30))
    assert cut[9] == (date(2026, 1, 1), date(2026, 1, 31))
    assert cut[-1] == (date(2026, 11, 1), date(2026, 11, 22))
    assert all((hi - lo).days <= 30 for lo, hi in cut)


# --- The sync ----------------------------------------------------------------------


def test_the_first_copy_then_only_the_changes(store):
    books = Books()
    books.put(G1, date(2026, 1, 3), "Sales", "101", 10)
    books.put(G2, date(2025, 6, 9), "Receipt", "7", 12)
    snap = _sync(books, store)
    assert set(snap.vouchers) == {G1, G2} and snap.max_alter_id == 12
    assert len(_since_requests(books)) == 20
    assert {
        r.find("BODY/DESC/STATICVARIABLES").findtext("BridgeAfterAlter")
        for r in _since_requests(books)
    } == {"0"}
    assert len(snap.ledgers) > 0

    # An edit in TallyPrime raises the AlterID. The next copy asks once, for
    # the changes after 12 only, and replaces the voucher.
    books.sent.clear()
    books.put(G1, date(2026, 1, 3), "Sales", "101", 13, amount="250.00")
    again = _sync(books, store)
    asked = _since_requests(books)
    assert len(asked) == 1
    assert asked[0].find("BODY/DESC/STATICVARIABLES").findtext("BridgeAfterAlter") == "12"
    assert again.max_alter_id == 13
    assert str(again.vouchers[G1].amount.value) == "250.00"
    assert store.load(CO).max_alter_id == 13


def test_a_full_copy_drops_a_voucher_deleted_in_tally(store):
    books = Books()
    books.put(G1, date(2026, 1, 3), "Sales", "101", 10)
    books.put(G2, date(2026, 1, 9), "Receipt", "7", 11)
    _sync(books, store)
    del books.vouchers[G2]
    assert G2 in _sync(books, store).vouchers  # the known gap: no AlterID moves
    assert G2 not in _sync(books, store, full=True).vouchers


def test_a_failed_sync_keeps_the_old_copy(store):
    books = Books()
    books.put(G1, date(2026, 1, 3), "Sales", "101", 10)
    first = _sync(books, store)
    books.up = False
    with pytest.raises(TallyDown):
        _sync(books, store)
    assert store.load(CO).copied_at == first.copied_at


def test_a_new_financial_year_drops_what_left_the_window(store):
    books = Books()
    books.put(G1, date(2025, 3, 31), "Sales", "1", 5)  # outside the window
    books.put(G2, date(2025, 4, 1), "Sales", "2", 6)
    assert set(_sync(books, store).vouchers) == {G2}


# --- The store ---------------------------------------------------------------------


def test_the_file_is_encrypted_and_names_no_company(tmp_path):
    keyfile = tmp_path / "dev.key"
    store = CopyStore(tmp_path / "copy", DevFileKeyProvider(keyfile))
    books = Books()
    books.put(G1, date(2026, 1, 3), "Sales", "101", 10)
    _sync(books, store)
    files = list((tmp_path / "copy").iterdir())
    assert len(files) == 1
    raw = files[0].read_bytes()
    for text in (b"Alpha", b"Sales", b"Traders", G1.encode()):
        assert text not in raw and text not in files[0].name.encode()
    if os.name == "posix":
        assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
        assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600


def test_another_key_reads_no_copy(tmp_path):
    books = Books()
    books.put(G1, date(2026, 1, 3), "Sales", "101", 10)
    _sync(books, CopyStore(tmp_path / "copy", MemoryKeyProvider()))
    assert CopyStore(tmp_path / "copy", MemoryKeyProvider()).load(CO) is None


def test_keep_only_and_delete_all(store):
    for name in ("A", "B", "C"):
        store.save(Snapshot(company=name, copied_at=datetime.now(UTC)))
    assert store.keep_only(("B",)) == 2
    assert store.load("A") is None and store.load("B") is not None
    assert store.delete_all() == 1
    assert store.load("B") is None


# --- The schedule ------------------------------------------------------------------


def test_a_tick_deletes_a_company_that_left_and_copies_the_rest(store):
    store.save(Snapshot(company="Gone Co", copied_at=datetime.now(UTC)))
    books = Books()
    books.put(G1, date(2026, 1, 3), "Sales", "101", 10)
    days: dict = {}
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    assert asyncio.run(one_tick(books, store, ONE, full_day=days, now=now)) == {CO: "copied"}
    assert store.load("Gone Co") is None and days == {CO: TODAY}
    # The second tick of the day copies only the changes.
    books.sent.clear()
    asyncio.run(one_tick(books, store, ONE, full_day=days, now=now + timedelta(minutes=30)))
    assert len(_since_requests(books)) == 1


def test_a_tick_with_tally_closed_copies_nothing(store):
    books = Books()
    books.up = False
    assert asyncio.run(one_tick(books, store, ONE, full_day={})) == {CO: "tally_down"}
    assert books.sent == [] and store.load(CO) is None


def test_the_copy_settings_have_limits():
    assert Settings().copy_interval_min == 30 and Settings().copy_max_age_days == 7
    for bad in ({"copy_interval_min": 1}, {"copy_max_age_days": 0}, {"copy_max_age_days": 91}):
        with pytest.raises(SettingsError):
            Settings(**bad).validate()


# --- The reads with TallyPrime closed ----------------------------------------------


def _call(reader, store, tool: str, args: dict | None = None):
    server = build_read_server(reader, ONE, store)
    req = _t.CallToolRequest(
        method="tools/call", params=_t.CallToolRequestParams(name=tool, arguments=args or {})
    )
    return asyncio.run(server._mcp_server.request_handlers[_t.CallToolRequest](req)).root


def _ok(result) -> dict:
    assert result.isError is not True, result.content[0].text
    return json.loads(result.content[0].text)


def _err(result) -> str:
    assert result.isError is True
    return result.content[0].text


@pytest.fixture
def copied(store):
    books = Books()
    books.put(G1, date(2026, 1, 3), "Sales", "101", 10)
    books.put(G2, date(2026, 1, 15), "Receipt", "7", 11)
    asyncio.run(sync_company(books, store, CO, today=TODAY))
    books.up = False
    return books, store


def test_find_ledgers_answers_from_the_copy_and_names_its_age(copied):
    books, store = copied
    body = _ok(_call(books, store, "find_ledgers", {"query": "a"}))
    assert body["row_count"] > 0
    assert body["source"]["from"] == "local_copy"
    assert "TallyPrime is closed" in body["source"]["note"]
    assert isinstance(body["source"]["age_minutes"], int)


def test_list_vouchers_and_get_voucher_answer_from_the_copy(copied):
    books, store = copied
    body = _ok(
        _call(
            books,
            store,
            "list_vouchers",
            {"from_date": "2026-01-01", "to_date": "2026-01-31", "voucher_type": "Sales"},
        )
    )
    assert [v["guid"] for v in body["vouchers"]] == [G1]
    assert body["source"]["from"] == "local_copy"
    one = _ok(_call(books, store, "get_voucher", {"guid": G2}))
    assert one["number"] == "7" and one["source"]["from"] == "local_copy"
    by_number = _ok(_call(books, store, "get_voucher", {"voucher_type": "Sales", "number": "101"}))
    assert by_number["guid"] == G1
    assert "No voucher matches" in _err(
        _call(books, store, "get_voucher", {"voucher_type": "Sales", "number": "999"})
    )


def test_with_tally_up_the_reads_never_use_the_copy(copied):
    books, store = copied
    books.up = True
    assert "source" not in _ok(_call(books, store, "find_ledgers", {"query": "a"}))


def test_a_copy_past_the_maximum_age_answers_nothing(copied):
    books, store = copied
    old = store.load(CO)
    old.copied_at = datetime.now(UTC) - timedelta(days=8)
    store.save(old)
    text = _err(_call(books, store, "find_ledgers", {"query": "a"}))
    assert "8 days old" in text and "limit of 7 days" in text
    assert "Alpha Traders" not in text


def test_no_copy_keeps_the_plain_tally_down_sentence(store):
    books = Books()
    books.up = False
    with_copy_off = _err(_call(books, None, "find_ledgers", {"query": "a"}))
    no_file = _err(_call(books, store, "find_ledgers", {"query": "a"}))
    assert with_copy_off == no_file


def test_the_reports_that_need_tally_never_read_the_copy(copied):
    books, store = copied
    text = _err(_call(books, store, "trial_balance", {}))
    assert "source" not in text and "local copy" not in text


def test_status_with_tally_closed_names_the_copy_age(copied):
    books, store = copied
    body = _ok(_call(books, store, "status"))
    assert body["tally"] == "down"
    assert body["companies"][0]["name"] == CO
    assert isinstance(body["companies"][0]["copy_age_minutes"], int)
