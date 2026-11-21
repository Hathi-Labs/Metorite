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
_NOT_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
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
