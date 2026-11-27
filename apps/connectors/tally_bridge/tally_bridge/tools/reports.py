"""The report tools of brief section 6.4: tools 3 to 9.

Each tool follows the rules in ``tools/server.py``. In short: it takes the
optional ``company``, the dates are ``YYYY-MM-DD``, an amount is a string with
two decimals and a side, and every failure is one plain sentence.

The date inputs are ``from_date`` and ``to_date``, because ``from`` is a
reserved word in Python. A filter (party, item, voucher type) runs inside the
bridge, so the text of the member never reaches Tally.

**Not proven yet.** The shapes of the answers are SYNTHETIC until the README's
recordings land. ``ledger_statement`` answers ``opening`` and ``closing`` as
null until recording R06 shows where Tally puts them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from tally_bridge.localcopy.store import CopyStore
from tally_bridge.reader import TallyReader, envelope, records
from tally_bridge.reader.values import Amount
from tally_bridge.settings import Settings
from tally_bridge.tools.common import (
    READ_ONLY,
    Refusal,
    amount,
    day,
    day_range,
    fy_start,
    guarded,
    iso,
    pick_company,
    short,
)
from tally_bridge.tools.fromcopy import source_of, tally_or_copy

VOUCHER_DAYS = 31
PL_DAYS = 366
PAGE = 50

Company = Annotated[str | None, Field(description="Company name. Optional with one company.")]
Day = Annotated[str, Field(description="YYYY-MM-DD.")]


def _signed(a: Amount) -> Decimal:
    """Credit is plus and debit is minus, for a sum only. Never answered bare."""
    if a.side is None:
        return Decimal(0)
    return a.value if a.side == "Cr" else -a.value


def _from_signed(total: Decimal) -> Amount:
    if total == 0:
        return Amount(Decimal(0), None)
    return Amount(abs(total), "Cr" if total > 0 else "Dr")


def _rows(items: list[Any], cap: int) -> tuple[list[Any], bool]:
    return items[:cap], len(items) > cap


def register_report_tools(
    mcp: FastMCP, reader: TallyReader, settings: Settings, copy: CopyStore | None = None
) -> None:
    _balances(mcp, reader, settings)
    _ledgers(mcp, reader, settings)
    _vouchers(mcp, reader, settings, copy)
    _stock(mcp, reader, settings)


def _balances(mcp: FastMCP, reader: TallyReader, settings: Settings) -> None:
    cap = settings.max_rows

    @mcp.tool(
        name="trial_balance",
        description="Each group's debit and credit totals on one date.",
        annotations=READ_ONLY,
    )
    async def trial_balance(as_of: Day, company: Company = None) -> CallToolResult:
        async def work() -> dict[str, Any]:
            on = day(as_of, "as_of")
            name = pick_company(settings, company)
            root = await reader.post(
                envelope.report("Trial Balance", company=name, from_date=fy_start(on), to_date=on)
            )
            groups, cut = _rows(records.trial_balance(root), cap)
            return {
                "company": name,
                "as_of": on.isoformat(),
                "row_count": len(groups),
                "truncated": cut,
                "groups": [
                    {"name": g.name, "debit": g.debit.text, "credit": g.credit.text} for g in groups
                ],
            }

        return await guarded(settings, "trial_balance", work, company=company)

    @mcp.tool(
        name="profit_and_loss",
        description="Income and expense groups over a range, and the net result.",
        annotations=READ_ONLY,
    )
    async def profit_and_loss(
        from_date: Day, to_date: Day, company: Company = None
    ) -> CallToolResult:
        async def work() -> dict[str, Any]:
            first, last = day_range(from_date, to_date, PL_DAYS)
            name = pick_company(settings, company)
            root = await reader.post(
                envelope.report("Profit and Loss", company=name, from_date=first, to_date=last)
            )
            lines = records.profit_and_loss(root)
            net = _from_signed(sum((_signed(x.amount) for x in lines), Decimal(0)))
            shown, cut = _rows(lines, cap)
            return {
                "company": name,
                "from": first.isoformat(),
                "to": last.isoformat(),
                "row_count": len(shown),
                "truncated": cut,
                "groups": [{"name": x.name, **amount(x.amount)} for x in shown],
                # Credit over debit is a profit. The sign rule is not proven yet.
                "net": {
                    **amount(net),
                    "result": {"Cr": "profit", "Dr": "loss"}.get(net.side or ""),
                },
            }

        return await guarded(settings, "profit_and_loss", work, company=company)


def _ledgers(mcp: FastMCP, reader: TallyReader, settings: Settings) -> None:
    cap = settings.max_rows

    @mcp.tool(
        name="ledger_statement",
        description="One ledger's entries over a range of 92 days or less.",
        annotations=READ_ONLY,
    )
    async def ledger_statement(
        ledger: Annotated[str, Field(description="Exact ledger name.")],
        from_date: Day,
        to_date: Day,
        company: Company = None,
    ) -> CallToolResult:
        async def work() -> dict[str, Any]:
            short(ledger)
            first, last = day_range(from_date, to_date, settings.max_range_days)
            name = pick_company(settings, company)
            root = await reader.post(
                envelope.report(
                    "Ledger Vouchers", company=name, from_date=first, to_date=last, ledger=ledger
                )
            )
            rows, cut = _rows(records.ledger_statement(root), cap)
            return {
                "company": name,
                "ledger": ledger,
                "from": first.isoformat(),
                "to": last.isoformat(),
                "opening": None,
                "closing": None,
                "row_count": len(rows),
                "truncated": cut,
                "rows": [
                    {
                        "date": iso(r.on),
                        "voucher_type": r.voucher_type,
                        "number": r.number,
                        "ledger": r.ledger,
                        "narration": r.narration,
                        **amount(r.debit if r.debit.side else r.credit),
                    }
                    for r in rows
                ],
            }

        return await guarded(settings, "ledger_statement", work, company=company)

    @mcp.tool(
        name="outstanding",
        description="Open bills to receive or pay on one date, with age in days.",
        annotations=READ_ONLY,
    )
    async def outstanding(
        side: Annotated[str, Field(description="receivable or payable.")],
        as_of: Day,
        party: Annotated[str | None, Field(description="Exact party name.")] = None,
        company: Company = None,
    ) -> CallToolResult:
        async def work() -> dict[str, Any]:
            if side not in ("receivable", "payable"):
                raise Refusal("Set side to receivable or payable.")
            short(party)
            on = day(as_of, "as_of")
            name = pick_company(settings, company)
            report = "Bills Receivable" if side == "receivable" else "Bills Payable"
            root = await reader.post(envelope.report(report, company=name, to_date=on))
            found = [
                b
                for b in records.bills(root)
                if party is None or b.party.casefold() == party.casefold()
            ]
            total = _from_signed(sum((_signed(b.amount) for b in found), Decimal(0)))
            bills, cut = _rows(found, cap)
            return {
                "company": name,
                "side": side,
                "as_of": on.isoformat(),
                "row_count": len(bills),
                "truncated": cut,
                "total": amount(total),
                "bills": [
                    {
                        "date": iso(b.on),
                        "ref": b.ref,
                        "party": b.party,
                        "due": iso(b.due),
                        "age_days": _age(b.due or b.on, on),
                        **amount(b.amount),
                    }
                    for b in bills
                ],
            }

        return await guarded(settings, "outstanding", work, company=company)


def _vouchers(
    mcp: FastMCP, reader: TallyReader, settings: Settings, copy: CopyStore | None
) -> None:
    @mcp.tool(
        name="list_vouchers",
        description="Vouchers over 31 days or less, one page at a time.",
        annotations=READ_ONLY,
    )
    async def list_vouchers(
        from_date: Day,
        to_date: Day,
        voucher_type: Annotated[str | None, Field(description="Such as Sales.")] = None,
        party: Annotated[str | None, Field(description="Exact party name.")] = None,
        cursor: Annotated[str | None, Field(description="next_cursor of the last page.")] = None,
        company: Company = None,
    ) -> CallToolResult:
        async def work() -> dict[str, Any]:
            short(voucher_type, party)
            start = _cursor(cursor)
            first, last = day_range(from_date, to_date, VOUCHER_DAYS)
            name = pick_company(settings, company)
            root, snap = await tally_or_copy(
                lambda: reader.post(
                    envelope.report("Day Book", company=name, from_date=first, to_date=last)
                ),
                copy,
                name,
                settings.copy_max_age_days,
            )
            listed = _listed(root, snap, first, last)
            found = [
                v
                for v in listed
                if (voucher_type is None or v.voucher_type.casefold() == voucher_type.casefold())
                and (party is None or v.party.casefold() == party.casefold())
            ]
            page = found[start : start + PAGE]
            more = start + PAGE < len(found)
            body: dict[str, Any] = {
                "company": name,
                "from": first.isoformat(),
                "to": last.isoformat(),
                "row_count": len(page),
                "truncated": more,
                "vouchers": [
                    {
                        "number": v.number,
                        "date": iso(v.on),
                        "type": v.voucher_type,
                        "party": v.party,
                        **amount(v.amount),
                        "guid": v.guid,
                    }
                    for v in page
                ],
            }
            if more:
                body["next_cursor"] = str(start + PAGE)
            if snap is not None:
                body["source"] = source_of(snap)
            return body

        return await guarded(settings, "list_vouchers", work, company=company)

    @mcp.tool(
        name="get_voucher",
        description="One voucher by guid, or by voucher_type and number.",
        annotations=READ_ONLY,
    )
    async def get_voucher(
        guid: Annotated[str | None, Field(description="GUID from list_vouchers.")] = None,
        voucher_type: Annotated[
            str | None, Field(description="With number, such as Sales.")
        ] = None,
        number: Annotated[str | None, Field(description="With voucher_type.")] = None,
        company: Company = None,
    ) -> CallToolResult:
        async def work() -> dict[str, Any]:
            name, request = _voucher_request(guid, voucher_type, number, company)
            root, snap = await tally_or_copy(
                lambda: reader.post(request), copy, name, settings.copy_max_age_days
            )
            found = _found(root, snap, guid, voucher_type, number)
            if not found:
                raise Refusal("No voucher matches in this company.")
            if len(found) > 1:
                raise Refusal(
                    f"{len(found)} vouchers have that type and number. "
                    "Ask with the GUID from list_vouchers."
                )
            v = found[0]
            return {
                "company": name,
                "guid": v.guid or guid,
                "number": v.number,
                "date": iso(v.on),
                "type": v.voucher_type,
                "party": v.party,
                "narration": v.narration,
                "row_count": len(v.entries) + len(v.stock),
                "truncated": False,
                "ledger_entries": [{"ledger": e.ledger, **amount(e.amount)} for e in v.entries],
                "stock_entries": [
                    {"item": s.item, "quantity": s.quantity, "rate": s.rate, **amount(s.amount)}
                    for s in v.stock
                ],
                **({"source": source_of(snap)} if snap is not None else {}),
            }

        def _voucher_request(guid, voucher_type, number, company) -> tuple[str, bytes]:
            by_number = voucher_type is not None or number is not None
            if (guid is None) == (not by_number):
                raise Refusal("Give the guid, or the voucher_type and the number.")
            if guid is not None:
                if not envelope.is_guid(guid):
                    raise Refusal("That is not a Tally voucher GUID. Find it with list_vouchers.")
                name = pick_company(settings, company)
                return name, envelope.voucher(guid, company=name)
            if not voucher_type or not number:
                raise Refusal("Give both the voucher_type and the number.")
            short(voucher_type, number)
            name = pick_company(settings, company)
            return name, envelope.voucher_by_number(voucher_type, number, company=name)

        return await guarded(settings, "get_voucher", work, company=company)


def _stock(mcp: FastMCP, reader: TallyReader, settings: Settings) -> None:
    cap = settings.max_rows

    @mcp.tool(
        name="stock_summary",
        description="Stock items on one date: quantity, unit, rate and value.",
        annotations=READ_ONLY,
    )
    async def stock_summary(
        as_of: Day,
        item: Annotated[str | None, Field(description="Part of the item name.")] = None,
        company: Company = None,
    ) -> CallToolResult:
        async def work() -> dict[str, Any]:
            short(item)
            on = day(as_of, "as_of")
            name = pick_company(settings, company)
            root = await reader.post(envelope.report("Stock Summary", company=name, to_date=on))
            found = [
                s
                for s in records.stock_summary(root)
                if item is None or item.casefold() in s.item.casefold()
            ]
            lines, cut = _rows(found, cap)
            return {
                "company": name,
                "as_of": on.isoformat(),
                "row_count": len(lines),
                "truncated": cut,
                "items": [
                    {
                        "item": s.item,
                        "quantity": s.quantity,
                        "unit": s.unit,
                        "rate": s.rate,
                        "value": amount(s.value),
                    }
                    for s in lines
                ],
            }

        return await guarded(settings, "stock_summary", work, company=company)


def _listed(root: Any, snap: Any, first: date, last: date) -> list[Any]:
    """The vouchers of [first, last], from TallyPrime or from the local copy."""
    if root is not None:
        return records.vouchers(root)
    return sorted(
        (v for v in snap.vouchers.values() if v.on and first <= v.on <= last),
        key=lambda v: (v.on, v.number),
    )


def _found(
    root: Any, snap: Any, guid: str | None, voucher_type: str | None, number: str | None
) -> list[Any]:
    """The vouchers that match, from TallyPrime or from the local copy."""
    if root is not None:
        return records.vouchers(root)
    if guid is not None:
        return [snap.vouchers[guid]] if guid in snap.vouchers else []
    return [
        v for v in snap.vouchers.values() if v.voucher_type == voucher_type and v.number == number
    ]


def _age(since: date | None, on: date) -> int | None:
    return (on - since).days if since else None


def _cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    if not cursor.isdigit() or len(cursor) > 6:
        raise Refusal("That cursor is not valid. Ask for the first page again.")
    return int(cursor)
