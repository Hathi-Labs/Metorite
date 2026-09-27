"""Decode an uploaded export and read it as CSV.

Spec: ``project_import.md`` §4.3 items 6 and 11.

Two rules every adapter shares:

* **Encoding.** Excel re-saves a CSV as cp1252. Try a BOM, then UTF-8, then
  cp1252, and report which one won, so the dry run can say so.
* **Duplicate headers survive.** Jira repeats a column name once per value.
  ``csv.DictReader`` keeps only the last one, silently. :func:`read_csv`
  returns the header list and plain rows instead.
"""

from __future__ import annotations

import codecs
import csv
import io
import sys
from typing import NamedTuple

# One ClickUp cell (a long description or a comment list) can pass the
# default 128 KiB field limit.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def decode(raw: bytes) -> tuple[str, str]:
    """Return ``(text, encoding)``. Never raises on bad bytes."""
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


class Table(NamedTuple):
    header: list[str]
    rows: list[list[str]]
    #: Rows that held more cells than the header. The extra cells are cut,
    #: and an adapter must report the count: an unquoted comma shifts every
    #: later column of that row.
    wide_rows: int = 0


def read_csv(text: str) -> Table:
    """Each row is padded or cut to the header width."""
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return Table([], [])
    width = len(header)
    rows: list[list[str]] = []
    wide = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) > width and any(cell.strip() for cell in row[width:]):
            wide += 1
        rows.append((row + [""] * width)[:width])
    return Table(header, rows, wide)
