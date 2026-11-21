"""Read the records out of a parsed Tally answer.

Each reader here matches one request of ``reader.recording``. The shapes come
from SYNTHETIC answers until the README's recordings land. A recorded answer
that differs wins, and this module changes to match it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from tally_bridge.reader.parse import TallyAnswerError
from tally_bridge.reader.values import Amount, parse_amount


@dataclass(frozen=True)
class Ledger:
    name: str
    group: str
    opening: Amount
    closing: Amount


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _name(el: ET.Element) -> str:
    return _text(el, "NAME") or (el.get("NAME") or "").strip()


def company_names(root: ET.Element) -> list[str]:
    """The open companies, from the answer to ``BridgeCompanies`` (R02)."""
    names = [_name(el) for el in root.iter("COMPANY")]
    return [n for n in names if n]


def ledgers(root: ET.Element) -> list[Ledger]:
    """The ledgers, from the answer to ``BridgeLedgers`` (R03)."""
    out = []
    for el in root.iter("LEDGER"):
        name = _name(el)
        if not name:
            continue
        try:
            opening = parse_amount(_text(el, "OPENINGBALANCE"))
            closing = parse_amount(_text(el, "CLOSINGBALANCE"))
        except ValueError as exc:
            raise TallyAnswerError(f"A balance of ledger {name!r} is not an amount.") from exc
        out.append(Ledger(name, _text(el, "PARENT"), opening, closing))
    return out
