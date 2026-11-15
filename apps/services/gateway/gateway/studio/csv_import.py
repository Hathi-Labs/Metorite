"""The CSV planner of an object import (WS-STU C11, task 1419).

Spec: ``project-docs/specs/ai_studio.md`` §14 C11. Tier HIGH. This is the
PURE half of the import: it takes the text of a CSV, a map of columns to
fields and the fields of one object, and it answers a plan. It opens no
connection, writes nothing and has no route. The second slice puts a door
on it, and the write goes through :mod:`gateway.studio.batch_store`, so the
row rules (C8a) bind each row as they bind a single write.

**What a plan holds.** The preview rows (the first :data:`PREVIEW_ROWS` valid
records, as the store would keep them), one :class:`~gateway.studio.batch_store.BatchItem`
for each valid row, and an error list. An error names the data row, the field
key and one sentence. It NEVER echoes a cell, because a CSV holds member data.

**The caps.** At most :data:`MAX_ROWS` data rows (the cap of one batch), at
most :data:`MAX_BYTES` bytes of text and :data:`MAX_COLUMNS` columns. A plan
with more than :data:`APPROVAL_ROWS` valid rows has ``needs_approval``: the
door asks for ONE approval of the whole list, bound to its digest.

**Rows.** A row number counts the rows with a cell, from 1, after the header. A blank line has no number.

**Cells.** An empty cell is no value. A date is ``YYYY-MM-DD`` and a date and
time is RFC 3339 with an offset. A checkbox takes true, false, yes, no, y, n, 1 or
0. A money amount is decimal text in the currency that the caller names. A
percent is ``12.5`` or ``12.5%``, stored in basis points. A many field and a
multi-select take values split on ``;``. A person, a relation, a file, a geo
point, a quantity and a lines field take no CSV cell: the plan refuses a map
to them, and says so once.

**One mapping engine (D205).** This module is the Studio side of the one
import planner. The CRM planner (CRX-2, task 2735) has not landed. When it
does, the shared parts (the header match, the error list) move to it. The
fence ``tests/unit/test_studio_csv_import.py`` pins the contract here.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from acb_common import studio_fields
from acb_common.studio_fields import NAME_PATTERN, FieldError, FieldSpec, RecordError

from gateway.studio.batch_store import MAX_BATCH_ITEMS, BatchItem, list_digest
from gateway.studio.objects_store import Write

#: One import is one batch, so the row cap is the cap of a batch.
MAX_ROWS = MAX_BATCH_ITEMS
MAX_BYTES = 2 * 1024 * 1024
MAX_COLUMNS = 80
#: A plan with more valid rows than this needs one approval of its digest.
APPROVAL_ROWS = 100
PREVIEW_ROWS = 5
MAX_ERRORS = 100
#: The separator of a many field and a multi-select cell.
LIST_SEPARATOR = ";"
ACTION = "csv_import"

#: The types that no CSV cell can fill.
UNMAPPABLE: frozenset[str] = frozenset({"person", "relation", "file", "geo", "quantity"})

_TRUE = frozenset({"true", "yes", "y", "1"})
_FALSE = frozenset({"false", "no", "n", "0"})
_KEY_RE = re.compile(NAME_PATTERN, re.ASCII)
_NUMBER_RE = re.compile(r"-?[0-9]{1,15}(?:\.[0-9]+)?", re.ASCII)


class CsvImportError(ValueError):
    """A CSV or a map that the planner refuses as a whole. The text never
    holds a cell."""


@dataclass(frozen=True)
class RowError:
    """One refused cell or row. ``row`` counts data rows from 1 (the header is
    not a row). ``field`` is a field key or None for a whole row."""

    row: int
    field: str | None
    message: str


@dataclass(frozen=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Plan:
    """The answer of :func:`plan_import`."""

    total_rows: int
    items: tuple[BatchItem, ...]
    preview: tuple[dict[str, Any], ...]
    errors: tuple[RowError, ...]
    errors_truncated: bool
    needs_approval: bool
    skipped_columns: tuple[str, ...] = field(default=())

    @property
    def valid_rows(self) -> int:
        return len(self.items)

    def digest(self, package_id: str) -> str:
        """The digest that an approval binds (:func:`batch_store.list_digest`)."""
        return list_digest(package_id, ACTION, self.items)

    def writes_by_key(self) -> dict[str, tuple[Write, ...]]:
        """What the batch store takes again on each chunk."""
        return {item.key: item.writes for item in self.items}


# --- parse --------------------------------------------------------------------


def parse_csv(text: str) -> Table:
    """The headers and rows of *text*. It strips a BOM, takes a comma, a
    semicolon or a tab as the separator, and skips a row with no cell. It
    refuses a CSV over the caps, with no header, or with a repeated header."""
    if not isinstance(text, str):
        raise CsvImportError("The CSV must be text.")
    if len(text.encode("utf-8", "surrogatepass")) > MAX_BYTES:
        raise CsvImportError(f"The CSV is over {MAX_BYTES // 1024} KB. Split it.")
    text = text.removeprefix("﻿")
    if "\x00" in text:
        raise CsvImportError("The CSV holds a NUL character.")
    if not text.strip():
        raise CsvImportError("The CSV is empty.")
    delimiter = _delimiter(text)
    grid: list[list[str]] = []
    try:
        for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True):
            if any(c.strip() for c in r):
                grid.append(r)
                if len(grid) > MAX_ROWS + 1:  # stop at the cap: no 1M-row grid
                    raise CsvImportError(f"The CSV has more than {MAX_ROWS} rows. Split it.")
    except csv.Error:
        raise CsvImportError("The CSV is not well formed. Check the quotes.") from None
    if not grid:
        raise CsvImportError("The CSV has no header row.")
    headers = tuple(h.strip() for h in grid[0])
    if len(headers) > MAX_COLUMNS:
        raise CsvImportError(f"The CSV has more than {MAX_COLUMNS} columns.")
    if any(not h for h in headers):
        raise CsvImportError("Each column needs a header.")
    if len({h.casefold() for h in headers}) != len(headers):
        raise CsvImportError("Two columns have the same header.")
    body = grid[1:]
    if len(body) > MAX_ROWS:
        raise CsvImportError(f"The CSV has more than {MAX_ROWS} rows. Split it.")
    return Table(headers=headers, rows=tuple(tuple(c.strip() for c in r) for r in body))


def _delimiter(text: str) -> str:
    """The separator that gives the first record the most columns, read with
    the quotes. A quoted header that holds a ``;`` does not count."""
    best, width = ",", 0
    for d in (",", ";", "\t"):
        try:
            first = next(
                csv.reader(io.StringIO(text.lstrip("\r\n"), newline=""), delimiter=d, strict=True),
                [],
            )
        except csv.Error:
            continue
        if len(first) > width:
            best, width = d, len(first)
    return best


# --- the map ------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def suggest_mapping(headers: Sequence[str], fields: Sequence[FieldSpec]) -> dict[str, str]:
    """A first map: a header that equals a field key or label (case and
    punctuation ignored) maps to it. A field takes one header at most."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for h in headers:
        n = _norm(h)
        for f in fields:
            if f.key in taken or f.lines is not None or f.type in UNMAPPABLE:
                continue
            if n and n in (_norm(f.key), _norm(f.label or "")):
                out[h] = f.key
                taken.add(f.key)
                break
    return out


def _check_mapping(
    headers: Sequence[str], fields: Sequence[FieldSpec], mapping: Mapping[str, str]
) -> dict[str, FieldSpec]:
    by_key = {f.key: f for f in fields}
    used: dict[str, FieldSpec] = {}
    seen: set[str] = set()
    for header, key in mapping.items():
        if not isinstance(header, str) or not isinstance(key, str):
            raise CsvImportError("The map is column header to field key, both text.")
        if header not in headers:
            raise CsvImportError("The map names a column that the CSV does not have.")
        spec = by_key.get(key)
        if spec is None:
            shown = key if _KEY_RE.fullmatch(key) else "that key"
            raise CsvImportError(f"The map names `{shown}`, which is not a field of this object.")
        if spec.lines is not None or spec.type in UNMAPPABLE:
            raise CsvImportError(f"Field `{key}` takes no value from a CSV cell.")
        if key in seen:
            raise CsvImportError(f"Two columns map to field `{key}`.")
        seen.add(key)
        used[header] = spec
    if not used:
        raise CsvImportError("Map at least one column to a field.")
    return used


# --- one cell -----------------------------------------------------------------


#: The minor digits of the currencies that do not have 2. It is the table of
#: ``minorDigits()`` in ``fieldCheck.ts`` (Intl, CLDR): the grid and the form read
#: that one, so an import must store the same minor units. ISO 4217 differs for
#: HUF, IDR, COP, IQD and a few more, so do NOT use ISO here. Fence:
#: ``test_the_minor_digits_table_equals_intl`` (runs where node is installed).
MINOR_DIGITS: dict[str, int] = {
    **dict.fromkeys(
        (
            "AFN",
            "ALL",
            "BIF",
            "CLP",
            "COP",
            "DJF",
            "GNF",
            "HUF",
            "IDR",
            "IQD",
            "IRR",
            "ISK",
            "JPY",
            "KMF",
            "KPW",
            "KRW",
            "LAK",
            "LBP",
            "MGA",
            "MMK",
            "PKR",
            "PYG",
            "RWF",
            "SLL",
            "SOS",
            "SYP",
            "UGX",
            "VND",
            "VUV",
            "XAF",
            "XOF",
            "XPF",
            "YER",
        ),
        0,
    ),
    **dict.fromkeys(("BHD", "JOD", "KWD", "LYD", "OMR", "TND"), 3),
}
_THOUSANDS_RE = re.compile(r"-?[1-9][0-9]{0,2}(?:,[0-9]{3})+(?:\.[0-9]+)?", re.ASCII)
#: A float keeps 15 significant digits exactly. A number field refuses more.
_MAX_DIGITS = 15


def _decimal(f: FieldSpec, cell: str) -> Decimal:
    """The decimal of *cell*. A comma is a thousands group only: ``1,234.5``.
    ``1,5`` is refused, because a European file means 1.5 and a read as 15
    would be a silent ten-fold error. A space inside is refused too."""
    text = cell
    if "," in text:
        if not _THOUSANDS_RE.fullmatch(text):
            raise FieldError(f.key, f"Field `{f.key}` takes a number as 1234.5 or 1,234.5.")
        text = text.replace(",", "")
    if not _NUMBER_RE.fullmatch(text):
        if re.fullmatch(r"-?[0-9]{16,}(?:\.[0-9]+)?", text, re.ASCII):
            raise FieldError(f.key, f"Field `{f.key}` takes at most 15 digits before the point.")
        raise FieldError(f.key, f"Field `{f.key}` takes a number as 1234.5, with no exponent.")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise FieldError(f.key, f"Field `{f.key}` takes a number as 1234.5.") from None


def _money(f: FieldSpec, cell: str, currency: str | None) -> dict[str, Any]:
    """Whole minor units of *currency*, as the grid reads them (:data:`MINOR_DIGITS`)."""
    if currency is None:
        raise FieldError(f.key, f"Field `{f.key}` needs a currency for the import.")
    digits = MINOR_DIGITS.get(currency, 2)
    minor = _decimal(f, cell) * 10**digits
    if abs(minor) >= studio_fields.MAX_ABS_NUMBER:
        raise FieldError(f.key, f"Field `{f.key}` takes an amount below 10^15 minor units.")
    if minor != minor.to_integral_value():
        raise FieldError(f.key, f"Field `{f.key}` takes at most {digits} decimals in {currency}.")
    return {"minor": int(minor), "currency": currency}


def _scalar(f: FieldSpec, cell: str, currency: str | None) -> Any:
    t = f.type
    if t == "number":
        d = _decimal(f, cell)
        if len(d.normalize().as_tuple().digits) > _MAX_DIGITS:
            raise FieldError(f.key, f"Field `{f.key}` takes at most {_MAX_DIGITS} digits.")
        if d == d.to_integral_value():
            return int(d)
        if "e" in repr(float(d)):
            raise FieldError(f.key, f"Field `{f.key}` takes a number that is not this small.")
        return float(d)
    if t == "money":
        return _money(f, cell, currency)
    if t == "percent":
        d = _decimal(f, cell.removesuffix("%").strip()) * 100
        if d != d.to_integral_value():
            raise FieldError(f.key, f"Field `{f.key}` takes at most two decimals of a percent.")
        return int(d)
    if t in ("rating", "duration"):
        d = _decimal(f, cell)
        if d != d.to_integral_value():
            raise FieldError(f.key, f"Field `{f.key}` takes a whole number.")
        return int(d)
    if t == "checkbox":
        low = cell.casefold()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise FieldError(f.key, f"Field `{f.key}` takes true, false, yes, no, 1 or 0.")
    return cell


def coerce_cell(f: FieldSpec, cell: str, *, currency: str | None = None) -> Any:
    """The checked value of *cell* for *f*, or None for an empty cell. Raises
    :class:`FieldError`. It never echoes the cell."""
    if not cell.strip():
        return None
    if f.many or f.type == "multiSelect":
        parts = [p.strip() for p in cell.split(LIST_SEPARATOR) if p.strip()]
        if not parts:
            return None
        if f.type == "multiSelect":
            return studio_fields.check_value(f, parts)
        return studio_fields.check_value(f, [_scalar(f, p, currency) for p in parts])
    return studio_fields.check_value(f, _scalar(f, cell.strip(), currency))


# --- the plan -----------------------------------------------------------------


def plan_import(
    table: Table,
    fields: Sequence[FieldSpec],
    mapping: Mapping[str, str],
    *,
    object_id: str,
    currency: str | None = None,
) -> Plan:
    """Plan the import of *table* into the object *object_id*.

    *mapping* maps a header to a field key. A valid row becomes one create
    write, with the key ``row-<n>``. An invalid row becomes errors and no
    item. The plan lists at most :data:`MAX_ERRORS` errors.
    """
    if currency is not None and not re.fullmatch(r"[A-Z]{3}", currency):
        raise CsvImportError("The currency is three capital letters, as INR.")
    cols = _check_mapping(table.headers, fields, mapping)
    index = {h: i for i, h in enumerate(table.headers)}
    skipped = tuple(h for h in table.headers if h not in cols)
    mapped_keys = {f.key for f in cols.values()}
    stuck = [f.key for f in fields if f.required and f.lines is None and f.type in UNMAPPABLE]
    if stuck:
        raise CsvImportError(
            "This object cannot take a CSV import: "
            + ", ".join(f"`{k}`" for k in stuck)
            + " is required and takes no CSV cell. Add the records in the app, or make the field optional."
        )
    missing = [f.key for f in fields if f.required and f.lines is None and f.key not in mapped_keys]
    if missing:
        raise CsvImportError(
            "Map a column to each required field. Missing: "
            + ", ".join(f"`{k}`" for k in missing)
            + "."
        )
    items: list[BatchItem] = []
    preview: list[dict[str, Any]] = []
    errors: list[RowError] = []
    n_errors = 0
    for n, row in enumerate(table.rows, start=1):
        data: dict[str, Any] = {}
        bad: list[RowError] = []
        if len(row) > len(table.headers):
            bad.append(RowError(n, None, "The row has more cells than the header has columns."))
        for header, spec in cols.items():
            i = index[header]
            cell = row[i] if i < len(row) else ""
            try:
                value = coerce_cell(spec, cell, currency=currency)
            except FieldError as exc:
                bad.append(RowError(n, spec.key, exc.message))
                continue
            if value is not None:
                data[spec.key] = value
        if not bad:
            try:
                data = studio_fields.validate_record(fields, data)
                studio_fields.record_json(data)
            except FieldError as exc:
                bad.append(RowError(n, exc.field, exc.message))
            except RecordError as exc:
                bad.append(RowError(n, None, str(exc)))
        if bad:
            n_errors += len(bad)
            errors.extend(bad[: max(0, MAX_ERRORS - len(errors))])
            continue
        items.append(
            BatchItem(key=f"row-{n}", writes=(Write(op="create", object_id=object_id, data=data),))
        )
        if len(preview) < PREVIEW_ROWS:
            preview.append(data)
    return Plan(
        total_rows=len(table.rows),
        items=tuple(items),
        preview=tuple(preview),
        errors=tuple(errors),
        errors_truncated=n_errors > len(errors),
        needs_approval=len(items) > APPROVAL_ROWS,
        skipped_columns=skipped,
    )
