"""The XML envelopes that the bridge sends to TallyPrime (brief section 5.4).

Two rules shape this module.

**Rule 3 of section 6.5: no raw XML, ever.** A caller never passes XML or
TDL. It names a report or a collection from the fixed tables below, and it
gives typed values: a company name, dates and a ledger name. Those values go
only into ``STATICVARIABLES``, as element text. ElementTree escapes ``&``,
``<`` and ``>`` in element text, so a value cannot close its element and
open another. A value with a character that XML 1.0 cannot hold is refused.

**Rule 7: the read path never sends an import.** ``TALLYREQUEST`` is always
the constant of ``_REQUEST_KIND``, and no input reaches it. The fence is
``tests/unit/test_tally_bridge_reader.py``.

Each report name and collection is a guess until it is proven on a real
TallyPrime. The README of the bridge lists the requests to record.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from tally_bridge.reader.values import to_tally_date

# The one kind of request the reader sends. Never an input.
_REQUEST_KIND = "Export"

# The reports that the read tools use. A report name is never an input.
REPORTS = frozenset(
    {
        "Trial Balance",
        "Profit and Loss",
        "Ledger Vouchers",
        "Bills Receivable",
        "Bills Payable",
        "Day Book",
        "Stock Summary",
    }
)


@dataclass(frozen=True)
class Collection:
    tally_type: str
    fetch: tuple[str, ...]


# The inline TDL collections. The TDL text comes from this table only.
COLLECTIONS: dict[str, Collection] = {
    "BridgeCompanies": Collection("Company", ("Name", "StartingFrom", "BooksFrom")),
    "BridgeLedgers": Collection("Ledger", ("Name", "Parent", "OpeningBalance", "ClosingBalance")),
}

# XML 1.0 allows tab, newline and carriage return, and no other control character.
_NOT_XML = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\ud800-\\udfff\\ufffe\\uffff]")
_MAX_INPUT = 200


def _value(text: str) -> str:
    """A typed input, checked before it goes into the envelope."""
    if not isinstance(text, str):
        raise TypeError("An envelope input must be text.")
    if _NOT_XML.search(text):
        raise ValueError("The input holds a character that XML cannot carry.")
    if len(text) > _MAX_INPUT:
        raise ValueError(f"The input is longer than {_MAX_INPUT} characters.")
    return text


def _envelope(kind: str, request_id: str) -> tuple[ET.Element, ET.Element]:
    env = ET.Element("ENVELOPE")
    header = ET.SubElement(env, "HEADER")
    ET.SubElement(header, "VERSION").text = "1"
    ET.SubElement(header, "TALLYREQUEST").text = _REQUEST_KIND
    ET.SubElement(header, "TYPE").text = kind
    ET.SubElement(header, "ID").text = request_id
    desc = ET.SubElement(ET.SubElement(env, "BODY"), "DESC")
    return env, desc


def _statics(
    desc: ET.Element,
    company: str | None,
    from_date: date | None = None,
    to_date: date | None = None,
    ledger: str | None = None,
) -> None:
    sv = ET.SubElement(desc, "STATICVARIABLES")
    if company is not None:
        ET.SubElement(sv, "SVCURRENTCOMPANY").text = _value(company)
    if from_date is not None:
        ET.SubElement(sv, "SVFROMDATE").text = to_tally_date(from_date)
    if to_date is not None:
        ET.SubElement(sv, "SVTODATE").text = to_tally_date(to_date)
    if ledger is not None:
        ET.SubElement(sv, "LEDGERNAME").text = _value(ledger)
    ET.SubElement(sv, "SVEXPORTFORMAT").text = "$$SysName:XML"


def _bytes(env: ET.Element) -> bytes:
    # Rule 7, a second lock: every builder ends here, and the request it built
    # must hold exactly one TALLYREQUEST, and that one must be the export kind.
    kinds = [el.text for el in env.iter("TALLYREQUEST")]
    if kinds != [_REQUEST_KIND]:
        raise RuntimeError("The reader builds export requests only.")
    return ET.tostring(env, encoding="utf-8", xml_declaration=False)


def report(
    name: str,
    *,
    company: str | None,
    from_date: date | None = None,
    to_date: date | None = None,
    ledger: str | None = None,
) -> bytes:
    """A report export, such as the trial balance on one date."""
    if name not in REPORTS:
        raise ValueError(f"Unknown report {name!r}.")
    env, desc = _envelope("Data", name)
    _statics(desc, company, from_date, to_date, ledger)
    return _bytes(env)


# A Tally GUID: hex groups joined by dashes, such as
# "a1b2c3d4-0000-1111-2222-333344445555-0000001a". Only this shape may go into
# the header, so no input text ever reaches the request kind or the TDL.
GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4,12}){3,6}")


def is_guid(text: str) -> bool:
    """The whole text is a GUID. fullmatch, so a trailing newline fails too."""
    return GUID.fullmatch(text) is not None


def voucher(guid: str, *, company: str) -> bytes:
    """One voucher by its GUID, as an object export."""
    if not is_guid(guid):
        raise ValueError("Not a Tally voucher GUID.")
    env, desc = _envelope("Object", "Voucher")
    env.find("HEADER/ID").set("TYPE", "GUID")
    env.find("HEADER/ID").text = guid
    ET.SubElement(env.find("HEADER"), "SUBTYPE").text = "Voucher"
    _statics(desc, company)
    fetch = ET.SubElement(desc, "FETCHLIST")
    for field in VOUCHER_FETCH:
        ET.SubElement(fetch, "FETCH").text = field
    return _bytes(env)


# The TDL of one voucher by its type and number. The formula is fixed text.
# The member's type and number reach Tally only as the values of the two
# variables, in STATICVARIABLES, as escaped element text. So no input text can
# become part of a formula (brief section 6.5 rule 3).
_BY_NUMBER = "BridgeVoucherByNumber"
_VAR_TYPE = "BridgeVchType"
_VAR_NUMBER = "BridgeVchNumber"
_BY_NUMBER_FILTER = f"$VoucherTypeName = ##{_VAR_TYPE} AND $VoucherNumber = ##{_VAR_NUMBER}"


def voucher_by_number(voucher_type: str, number: str, *, company: str) -> bytes:
    """The vouchers of one type with one number, as a collection export."""
    env, desc = _envelope("Collection", _BY_NUMBER)
    _statics(desc, company)
    sv = desc.find("STATICVARIABLES")
    ET.SubElement(sv, _VAR_TYPE).text = _value(voucher_type)
    ET.SubElement(sv, _VAR_NUMBER).text = _value(number)
    message = ET.SubElement(ET.SubElement(desc, "TDL"), "TDLMESSAGE")
    coll = ET.SubElement(message, "COLLECTION", {"NAME": _BY_NUMBER, "ISMODIFY": "No"})
    ET.SubElement(coll, "TYPE").text = "Voucher"
    ET.SubElement(coll, "FETCH").text = ", ".join((*VOUCHER_FETCH, "Guid"))
    ET.SubElement(coll, "FILTER").text = "BridgeVchMatch"
    ET.SubElement(
        message, "SYSTEM", {"TYPE": "Formulae", "NAME": "BridgeVchMatch"}
    ).text = _BY_NUMBER_FILTER
    for var in (_VAR_TYPE, _VAR_NUMBER):
        ET.SubElement(ET.SubElement(message, "VARIABLE", {"NAME": var}), "TYPE").text = "String"
    return _bytes(env)


VOUCHER_FETCH = (
    "Date",
    "VoucherTypeName",
    "VoucherNumber",
    "PartyLedgerName",
    "Narration",
    "AllLedgerEntries",
    "AllInventoryEntries",
)


def collection(name: str, *, company: str | None) -> bytes:
    """A collection export with its inline TDL definition."""
    spec = COLLECTIONS.get(name)
    if spec is None:
        raise ValueError(f"Unknown collection {name!r}.")
    env, desc = _envelope("Collection", name)
    _statics(desc, company)
    message = ET.SubElement(ET.SubElement(desc, "TDL"), "TDLMESSAGE")
    coll = ET.SubElement(message, "COLLECTION", {"NAME": name, "ISMODIFY": "No"})
    ET.SubElement(coll, "TYPE").text = spec.tally_type
    ET.SubElement(coll, "FETCH").text = ", ".join(spec.fetch)
    return _bytes(env)
