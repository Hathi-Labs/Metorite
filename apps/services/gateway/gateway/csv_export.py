"""The gateway's ONE CSV writer — RFC 4180, BOM, and formula neutralisation.

Every filtered-list export in this service renders through here: Projects'
``GET /projects/export/tasks.csv`` (WS-27ae) and the CRM's
``GET /crm/export/{entity}.csv`` (WS-26i-export).

It lives beside ``gateway/db.py`` rather than inside either route package
because it belongs to neither: the second app to want a CSV is the moment a
copy would appear, and the thing that would be copied is a **security
control**. :func:`csv_cell` is the difference between a spreadsheet and remote
code execution on the machine of whoever double-clicks the file; a second copy
is one that does not get the next fix (CLAUDE.md §4, "a second implementation
of an existing seam is a defect, not a feature").

Fences: ``tests/unit/test_projects_export.py`` and
``tests/unit/test_crm_export.py`` — the first pins the bytes of the guard, the
second pins that the CRM route actually applies it.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

#: The first characters a spreadsheet treats as the start of a FORMULA.
#:
#: ``=``/``+``/``-``/``@`` are Excel's and Google Sheets' formula leaders; TAB
#: and CR are in the list because both strip to nothing before the leader is
#: read, so ``\t=cmd|'/c calc'!A0`` is the same attack with a byte in front.
FORMULA_LEADERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

#: A cell that is exactly a number — the one thing that may keep a leading ``-``.
#:
#: Without this exemption an estimate of ``-30`` would export as ``'-30`` and
#: every numeric column would arrive in the spreadsheet as text, which breaks
#: the sums people export a CSV to compute. A bare number is not a formula:
#: Excel reads ``-5`` as the number minus five, while ``-5+cmd`` is not a number
#: and is therefore still guarded.
_BARE_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?$")

#: The prefix a guarded cell gets.
INJECTION_GUARD = "'"

#: Excel reads a UTF-8 CSV as the system code page unless it sees a BOM, so a
#: record named "Café" arrives as "CafÃ©" without one. The byte order mark is
#: the only thing that makes a non-ASCII export readable to the audience that
#: opens it in Excel, which is this feature's whole audience.
BOM = "﻿"


def csv_cell(value: Any) -> str:
    """One value → the text that goes in the cell, formula-neutralised.

    **The decision, stated so it can be argued with rather than discovered:** a
    cell whose first character is one of :data:`FORMULA_LEADERS` is prefixed
    with a single apostrophe. A record named ``=SUM(A1:A9)`` exports as
    ``'=SUM(A1:A9)``.

    Why a prefix and not "quote it": quoting does **not** help — Excel evaluates
    the cell after the CSV quoting is stripped, which is why `csv.QUOTE_ALL` is
    not a mitigation for this at all. Why guard rather than document: the values
    here are counterparty-authored (task titles, tags, custom-field text, and on
    the CRM side 3,993 rows imported out of Zoho — thousands of strings nobody
    in this company typed), and the payoff is `=cmd|'/c calc'!A0`, which
    executes with the credentials of whoever double-clicks the file. That is
    arbitrary code execution on the accountant's laptop in exchange for a
    formatting nicety.

    Why the apostrophe is acceptable: it is **visible, reversible and one
    character**, so a person who genuinely wanted a formula can see what
    happened and delete it. A silently executing formula has no such tell.

    A bare number (:data:`_BARE_NUMBER`) is exempt — see its note.

    ``None`` is the empty cell, never the string ``"None"``.
    """
    if value is None:
        return ""
    text_value = value if isinstance(value, str) else str(value)
    if not text_value:
        return ""
    if _BARE_NUMBER.match(text_value):
        return text_value
    if text_value[0] in FORMULA_LEADERS:
        return INJECTION_GUARD + text_value
    return text_value


def csv_document(
    header: Sequence[str], rows: Iterable[Sequence[Any]],
) -> str:
    """A header row and its body → the whole file, ready to be a response body.

    ``\\r\\n`` and ``QUOTE_MINIMAL`` are RFC 4180: the module quotes any cell
    holding a comma, a quote or a newline and doubles embedded quotes. That half
    is deliberately NOT hand-rolled — every hand-written CSV writer in the world
    gets the embedded-newline case wrong.

    Body cells go through :func:`csv_cell`; the header does not, because those
    strings are ours.
    """
    return "".join(csv_lines(header, rows))


def csv_lines(
    header: Sequence[str] | None, rows: Iterable[Sequence[Any]],
) -> Iterator[str]:
    """The same file as :func:`csv_document`, one line at a time.

    The first line carries the :data:`BOM` and the header. With ``header``
    ``None``, it gives the body lines only, for the next part of a file that is
    written in parts. A stream (the CRM's ``GET /crm/export/all.zip``,
    WS-53 CRM-X0) uses it, so a large file never sits whole in memory. It is
    the same writer, with the same guard, and never a second one.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

    def take() -> str:
        line = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return line

    if header is not None:
        writer.writerow(list(header))
        yield BOM + take()
    for row in rows:
        writer.writerow([csv_cell(cell) for cell in row])
        yield take()


__all__ = [
    "BOM",
    "FORMULA_LEADERS",
    "INJECTION_GUARD",
    "csv_cell",
    "csv_document",
    "csv_lines",
]
