"""The requests to record on a real TallyPrime, before a tool builds on them.

Brief section 5.4: prove each envelope on the office Tally first. This module
writes each request to a file. A person sends each file to TallyPrime on the
office PC and saves the answer next to it:

    python -m tally_bridge record-requests <folder> --company "<exact company name>"
    curl -s -X POST http://127.0.0.1:9000 --data-binary @R03_ledgers.xml -o R03_ledgers.answer.xml

The company name comes from the person at run time. The program holds no
company name of ours (brief test 17).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tally_bridge.reader import envelope


@dataclass(frozen=True)
class Recording:
    file: str
    why: str
    build: Callable[[str, str], bytes]  # (company, ledger) -> request


_FY_FROM, _FY_TO = date(2025, 4, 1), date(2026, 3, 31)
_MONTH_FROM, _MONTH_TO = date(2026, 1, 1), date(2026, 1, 31)
_SAMPLE_GUID = "00000000-0000-0000-0000-000000000000-00000000"

RECORDINGS: tuple[Recording, ...] = (
    Recording(
        "R02_companies.xml",
        "The open companies, for status and the allow-list",
        lambda c, led: envelope.collection("BridgeCompanies", company=None),
    ),
    Recording(
        "R03_ledgers.xml",
        "All ledgers with group and balances, for find_ledgers",
        lambda c, led: envelope.collection("BridgeLedgers", company=c),
    ),
    Recording(
        "R04_trial_balance.xml",
        "Brief test 6: compare with the Tally screen, group by group",
        lambda c, led: envelope.report(
            "Trial Balance", company=c, from_date=_FY_FROM, to_date=_FY_TO
        ),
    ),
    Recording(
        "R05_profit_and_loss.xml",
        "profit_and_loss over one financial year",
        lambda c, led: envelope.report(
            "Profit and Loss", company=c, from_date=_FY_FROM, to_date=_FY_TO
        ),
    ),
    Recording(
        "R06_ledger_vouchers.xml",
        "Brief test 7, and the sign rule: use a ledger with a known Dr balance",
        lambda c, led: envelope.report(
            "Ledger Vouchers", company=c, from_date=_MONTH_FROM, to_date=_MONTH_TO, ledger=led
        ),
    ),
    Recording(
        "R07_bills_receivable.xml",
        "Brief test 8: the total against the Bills Receivable screen",
        lambda c, led: envelope.report("Bills Receivable", company=c, to_date=_FY_TO),
    ),
    Recording(
        "R08_bills_payable.xml",
        "outstanding with side payable",
        lambda c, led: envelope.report("Bills Payable", company=c, to_date=_FY_TO),
    ),
    Recording(
        "R09_day_book.xml",
        "list_vouchers over one month",
        lambda c, led: envelope.report(
            "Day Book", company=c, from_date=_MONTH_FROM, to_date=_MONTH_TO
        ),
    ),
    Recording(
        "R10_stock_summary.xml",
        "stock_summary on one date",
        lambda c, led: envelope.report("Stock Summary", company=c, to_date=_FY_TO),
    ),
    Recording(
        "R11_one_voucher.xml",
        "get_voucher: put the GUID of one voucher from R09 in place of the zeros",
        lambda c, led: envelope.voucher(_SAMPLE_GUID, company=c),
    ),
    Recording(
        "R12_voucher_by_number.xml",
        "get_voucher by type and number: put a voucher type and number from R09 in place of the placeholders",
        lambda c, led: envelope.voucher_by_number("VOUCHER TYPE", "NUMBER", company=c),
    ),
)


def write_requests(folder: Path, company: str, ledger: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for rec in RECORDINGS:
        path = folder / rec.file
        path.write_bytes(rec.build(company, ledger))
        written.append(path)
    return written
