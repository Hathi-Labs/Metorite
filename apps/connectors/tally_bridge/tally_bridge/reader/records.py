"""Read the records out of a parsed Tally answer.

Each reader here matches one request of ``reader.recording``. The shapes come
from SYNTHETIC answers until the README's recordings land. A recorded answer
that differs wins, and this module changes to match it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from tally_bridge.reader.parse import TallyAnswerError
from tally_bridge.reader.values import Amount, from_tally_date, parse_amount


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


# --- Reports (R04 to R11) ---------------------------------------------------------
#
# A report export is a flat run of sibling elements, not one element per row.
# For example the trial balance repeats DSPACCNAME, DSPACCINFO, DSPACCNAME,
# DSPACCINFO. The readers below walk the children in order and start a new row
# at each row's first tag. These shapes are the common Tally export shapes.
# They are SYNTHETIC until the README's recordings prove them.


def _amount(el: ET.Element | None, path: str) -> Amount:
    text = ""
    if el is not None:
        found = el.find(path)
        text = (found.text or "").strip() if found is not None else ""
    try:
        return parse_amount(text)
    except ValueError as exc:
        raise TallyAnswerError(f"{path} is not an amount.") from exc


def _date(text: str) -> date | None:
    if not text.strip():
        return None
    try:
        return from_tally_date(text)
    except ValueError as exc:
        raise TallyAnswerError(f"{text!r} is not a Tally date.") from exc


def _deep_text(el: ET.Element, path: str) -> str:
    found = el.find(path)
    return (found.text or "").strip() if found is not None else ""


@dataclass(frozen=True)
class GroupTotal:
    name: str
    debit: Amount
    credit: Amount


def trial_balance(root: ET.Element) -> list[GroupTotal]:
    """Pairs of DSPACCNAME and DSPACCINFO (R04)."""
    out: list[GroupTotal] = []
    name: str | None = None
    for el in root:
        if el.tag == "DSPACCNAME":
            name = _deep_text(el, "DSPDISPNAME")
        elif el.tag == "DSPACCINFO" and name:
            out.append(
                GroupTotal(
                    name,
                    _amount(el, "DSPCLDRAMT/DSPCLDRAMTA"),
                    _amount(el, "DSPCLCRAMT/DSPCLCRAMTA"),
                )
            )
            name = None
    return out


@dataclass(frozen=True)
class Line:
    name: str
    amount: Amount


def profit_and_loss(root: ET.Element) -> list[Line]:
    """Pairs of BSNAME and BSAMT (R05). The main amount wins over the sub-amount."""
    out: list[Line] = []
    name: str | None = None
    for el in root:
        if el.tag == "BSNAME":
            name = _deep_text(el, "DSPACCNAME/DSPDISPNAME")
        elif el.tag == "BSAMT" and name:
            main = _amount(el, "BSMAINAMT")
            out.append(Line(name, main if main.side else _amount(el, "BSSUBAMT")))
            name = None
    return out


@dataclass(frozen=True)
class StatementRow:
    on: date | None
    voucher_type: str
    number: str
    ledger: str
    narration: str
    debit: Amount
    credit: Amount


_ROW_FIELDS = {
    "DSPVCHTYPE": "voucher_type",
    "DSPVCHNUMBER": "number",
    "DSPEXPLVCHNUMBER": "number",
    "DSPVCHLEDACCOUNT": "ledger",
    "DSPVCHNARR": "narration",
}


def ledger_statement(root: ET.Element) -> list[StatementRow]:
    """A run of DSPVCHDATE, DSPVCHLEDACCOUNT, DSPVCHTYPE, ... per row (R06)."""
    rows: list[dict] = []
    for el in root:
        text = (el.text or "").strip()
        if el.tag == "DSPVCHDATE":
            rows.append({"on": _date(text)})
        elif not rows:
            continue
        elif el.tag in _ROW_FIELDS:
            rows[-1].setdefault(_ROW_FIELDS[el.tag], text)
        elif el.tag in ("DSPVCHDRAMT", "DSPVCHCRAMT"):
            rows[-1][el.tag] = _parse(text, el.tag)
    return [
        StatementRow(
            r["on"],
            r.get("voucher_type", ""),
            r.get("number", ""),
            r.get("ledger", ""),
            r.get("narration", ""),
            r.get("DSPVCHDRAMT", parse_amount("")),
            r.get("DSPVCHCRAMT", parse_amount("")),
        )
        for r in rows
    ]


def _parse(text: str, what: str) -> Amount:
    try:
        return parse_amount(text)
    except ValueError as exc:
        raise TallyAnswerError(f"{what} is not an amount.") from exc


@dataclass(frozen=True)
class Bill:
    on: date | None
    ref: str
    party: str
    amount: Amount
    due: date | None


def bills(root: ET.Element) -> list[Bill]:
    """A run of BILLFIXED, BILLCL, BILLDUE per bill (R07 and R08)."""
    out: list[dict] = []
    for el in root:
        if el.tag == "BILLFIXED":
            out.append(
                {
                    "on": _date(_deep_text(el, "BILLDATE")),
                    "ref": _deep_text(el, "BILLREF"),
                    "party": _deep_text(el, "BILLPARTY"),
                }
            )
        elif out and el.tag == "BILLCL":
            out[-1]["amount"] = _parse((el.text or "").strip(), "BILLCL")
        elif out and el.tag == "BILLDUE":
            out[-1]["due"] = _date(el.text or "")
    return [
        Bill(b["on"], b["ref"], b["party"], b.get("amount", parse_amount("")), b.get("due"))
        for b in out
    ]


@dataclass(frozen=True)
class Entry:
    ledger: str
    amount: Amount


@dataclass(frozen=True)
class StockEntry:
    item: str
    quantity: str
    rate: str
    amount: Amount


@dataclass(frozen=True)
class Voucher:
    guid: str
    on: date | None
    voucher_type: str
    number: str
    party: str
    narration: str
    entries: list[Entry]
    stock: list[StockEntry]

    @property
    def amount(self) -> Amount:
        """The party's entry, or the largest entry when no party is named."""
        for e in self.entries:
            if self.party and e.ledger == self.party:
                return e.amount
        return max(
            (e.amount for e in self.entries), key=lambda a: a.value, default=parse_amount("")
        )


def _voucher(el: ET.Element) -> Voucher:
    entries = [
        Entry(_deep_text(e, "LEDGERNAME"), _parse(_deep_text(e, "AMOUNT"), "AMOUNT"))
        for tag in ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST")
        for e in el.findall(tag)
    ]
    stock = [
        StockEntry(
            _deep_text(e, "STOCKITEMNAME"),
            _deep_text(e, "BILLEDQTY") or _deep_text(e, "ACTUALQTY"),
            _deep_text(e, "RATE"),
            _parse(_deep_text(e, "AMOUNT"), "AMOUNT"),
        )
        for tag in ("ALLINVENTORYENTRIES.LIST", "INVENTORYENTRIES.LIST")
        for e in el.findall(tag)
    ]
    return Voucher(
        _deep_text(el, "GUID"),
        _date(_deep_text(el, "DATE")),
        _deep_text(el, "VOUCHERTYPENAME") or (el.get("VCHTYPE") or ""),
        _deep_text(el, "VOUCHERNUMBER"),
        _deep_text(el, "PARTYLEDGERNAME"),
        _deep_text(el, "NARRATION"),
        entries,
        stock,
    )


def vouchers(root: ET.Element) -> list[Voucher]:
    """Every VOUCHER element (R09 and R11)."""
    return [_voucher(el) for el in root.iter("VOUCHER")]


@dataclass(frozen=True)
class StockLine:
    item: str
    quantity: str
    unit: str
    rate: str
    value: Amount


def _qty(text: str) -> tuple[str, str]:
    parts = text.strip().split(None, 1)
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else ""


def stock_summary(root: ET.Element) -> list[StockLine]:
    """Pairs of DSPACCNAME and DSPSTKINFO (R10)."""
    out: list[StockLine] = []
    name: str | None = None
    for el in root:
        if el.tag == "DSPACCNAME":
            name = _deep_text(el, "DSPDISPNAME")
        elif el.tag == "DSPSTKINFO" and name:
            qty, unit = _qty(_deep_text(el, "DSPSTKCL/DSPCLQTY"))
            rate = _deep_text(el, "DSPSTKCL/DSPCLRATE").split("/", 1)[0].strip()
            out.append(StockLine(name, qty, unit, rate, _amount(el, "DSPSTKCL/DSPCLAMTA")))
            name = None
    return out
