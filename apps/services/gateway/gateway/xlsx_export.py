"""The gateway's ONE XLSX writer — shape rows, guard cells, write a workbook.

Spec: ``project-docs/specs/chat_parity.md`` §DOC row DOC-6. Used by the chat
tool ``make_spreadsheet`` (through the two ``export/workbook.xlsx`` routes of
the CRM and of Projects), and by the Export menus of task 4139. Like
``csv_export.py`` beside it, the cell rule is a **security control**, so a
second writer would be a second copy that misses the next fix.

Two cell rules, both applied to every cell:

* :func:`cell_value` is the rule of the sandbox data engine
  (``orchestrator/sandbox/data_engine.py::_xlsx_value``). The engine is one
  script of the sandbox image and is not a module that the gateway imports,
  so the rule is copied here. ``tests/unit/test_xlsx_export.py`` feeds the
  same values to both and fails when the two answers differ (R7).
* A string is a string and never a formula, and a string that
  ``csv_export.csv_cell`` would guard also gets Excel's own quote prefix, so
  an edit of the cell cannot turn it into a formula.

A sheet is shaped here (columns, order, sort, filter, groups with subtotals,
a totals row), from rows that the SERVER read as the member. The caller never
sends cells for a record: it sends a query and this shape.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import Response
from gateway.csv_export import csv_cell

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#: The longest ``spec`` query parameter of an export route.
MAX_SPEC_CHARS = 8000
#: The most data rows of one file, over all its sheets. Above it the file is
#: refused whole: this writer never makes a partial file.
MAX_EXPORT_ROWS = 10_000
MAX_SHEETS = 10
MAX_COLUMNS = 100
MAX_SORTS = 5
MAX_WHERE = 10
#: The cell limit of Excel.
MAX_CELL_CHARS = 32_767
MAX_SHEET_NAME = 31
MAX_HEADER = 100
MAX_WIDTH = 60
#: The names of a column type.
TYPES = ("text", "number", "integer", "currency", "date", "datetime")
_NUMERIC = ("number", "integer", "currency")
#: Number fields that name or rank a record. ``totals: true`` never adds them.
_NEVER_SUMMED = frozenset({"number", "probability"})
#: A longer number text is never a number: ``1e999999999`` would fill memory.
_MAX_DIGITS = 30
#: The first year that Excel holds as a date.
_FIRST_YEAR = 1900
#: The tests of a ``where`` entry.
WHERE_OPS = ("equals", "not_equals", "contains", "in", "empty", "not_empty")
_SHEET_KEYS = frozenset({"name", "source", "columns", "where", "sort", "group_by", "totals"})
_COLUMN_KEYS = frozenset({"field", "header", "type", "currency"})
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\\x00-\x1f]")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class SpreadsheetError(ValueError):
    """The spec cannot make a spreadsheet. The message says what to fix."""


@dataclass(frozen=True)
class Field:
    """One field that a source offers: the key and the label of the app."""

    key: str
    label: str


@dataclass(frozen=True)
class Column:
    field: str
    header: str = ""
    type: str = ""
    currency: str = ""


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class SheetPlan:
    """The shape of one sheet. ``source`` is the caller's query, read by the
    source loader of the route and not by this module."""

    name: str
    source: dict[str, Any]
    columns: tuple[Column, ...] = ()
    where: tuple[Condition, ...] = ()
    sort: tuple[tuple[str, bool], ...] = ()
    group_by: str = ""
    totals: bool | tuple[str, ...] = False


@dataclass
class Table:
    """The rows of one source, with the fields they hold."""

    fields: list[Field]
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Built:
    data: bytes
    #: One entry for each sheet: its name, row count, headers and group count.
    sheets: list[dict[str, Any]]
    rows: int


def too_many(rows: int, *, at_least: bool = False) -> SpreadsheetError:
    counted = f"at least {rows}" if at_least else str(rows)
    return SpreadsheetError(
        f"{counted} rows match, and the export cap is {MAX_EXPORT_ROWS} rows in one "
        "file. Nothing was exported. Add a filter and export again."
    )


# ── The spec ──────────────────────────────────────────────────────────────


def _text(value: Any, where: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpreadsheetError(f"{where} must be text")
    if len(value.strip()) > limit:
        raise SpreadsheetError(f"{where} is longer than {limit} characters")
    return value.strip()


def _column(raw: Any, where: str) -> Column:
    if isinstance(raw, str):
        return Column(field=_text(raw, where, MAX_HEADER))
    if not isinstance(raw, dict) or set(raw) - _COLUMN_KEYS:
        raise SpreadsheetError(
            f'{where} is a field name, or {{"field", "header", "type", "currency"}}')
    kind = str(raw.get("type") or "").strip().lower()
    if kind and kind not in TYPES:
        raise SpreadsheetError(f"{where}.type must be one of: {', '.join(TYPES)}")
    code = str(raw.get("currency") or "").strip().upper()
    if code and not _CURRENCY.match(code):
        raise SpreadsheetError(f"{where}.currency is a three letter code such as INR")
    header = raw.get("header")
    return Column(
        field=_text(raw.get("field"), f"{where}.field", MAX_HEADER),
        header=_text(header, f"{where}.header", MAX_HEADER) if header else "",
        type=kind or ("currency" if code else ""),
        currency=code,
    )


def _condition(raw: Any, where: str) -> Condition:
    if not isinstance(raw, dict) or "field" not in raw:
        raise SpreadsheetError(f'{where} is {{"field", one of {", ".join(WHERE_OPS)}}}')
    ops = [k for k in raw if k != "field"]
    if len(ops) != 1 or ops[0] not in WHERE_OPS:
        raise SpreadsheetError(f"{where} takes exactly one of: {', '.join(WHERE_OPS)}")
    op = ops[0]
    if op == "in" and not isinstance(raw[op], list):
        raise SpreadsheetError(f'{where}."in" is a list')
    return Condition(field=_text(raw["field"], f"{where}.field", MAX_HEADER), op=op, value=raw[op])


def _list(raw: Any, where: str, most: int) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpreadsheetError(f"{where} must be a list")
    if len(raw) > most:
        raise SpreadsheetError(f"{where} holds at most {most} entries")
    return raw


def _sort(raw: Any, where: str) -> tuple[str, bool]:
    if isinstance(raw, str):
        name = raw.strip()
        return (name[1:], True) if name.startswith("-") else (name, False)
    if isinstance(raw, dict) and "field" in raw and not set(raw) - {"field", "desc"}:
        return (_text(raw["field"], f"{where}.field", MAX_HEADER), bool(raw.get("desc")))
    raise SpreadsheetError(f'{where} is a field name ("-name" for descending) or {{"field","desc"}}')


def sheet_names(names: Sequence[str]) -> list[str]:
    """Legal and distinct Excel sheet names, in order."""
    used: set[str] = set()
    out: list[str] = []
    for raw in names:
        stem = " ".join(_BAD_SHEET_CHARS.sub(" ", raw).split()).strip("' ") or "Sheet"
        name, n = stem[:MAX_SHEET_NAME], 1
        while name.lower() in used or name.lower() == "history":
            n += 1
            suffix = f" {n}"
            name = stem[: MAX_SHEET_NAME - len(suffix)] + suffix
        used.add(name.lower())
        out.append(name)
    return out


def parse_spec(raw: Any) -> list[SheetPlan]:
    """The checked ``sheets`` of a spec, or :class:`SpreadsheetError`.

    An unknown key is refused by name. A silently dropped key would write a
    file that is not the one the caller asked for.
    """
    if not isinstance(raw, dict):
        raise SpreadsheetError('the spec must be an object with "sheets"')
    sheets = _list(raw.get("sheets"), '"sheets"', MAX_SHEETS)
    if not sheets:
        raise SpreadsheetError('the spec needs "sheets": a list of at least one')
    plans: list[SheetPlan] = []
    for i, item in enumerate(sheets):
        where = f"sheets[{i}]"
        if not isinstance(item, dict):
            raise SpreadsheetError(f"{where} must be an object")
        unknown = sorted(set(item) - _SHEET_KEYS)
        if unknown:
            raise SpreadsheetError(
                f"{where} has unknown keys: {', '.join(unknown)}. "
                f"A sheet takes: {', '.join(sorted(_SHEET_KEYS))}")
        source = item.get("source")
        if not isinstance(source, dict):
            raise SpreadsheetError(f'{where} needs "source": an object with "type"')
        totals = item.get("totals", False)
        if isinstance(totals, list):
            totals = tuple(_text(t, f"{where}.totals", MAX_HEADER)
                           for t in _list(totals, f"{where}.totals", MAX_COLUMNS))
        elif not isinstance(totals, bool):
            raise SpreadsheetError(f"{where}.totals is true or a list of field names")
        group = item.get("group_by")
        plans.append(SheetPlan(
            name=str(item.get("name") or f"Sheet{i + 1}"),
            source=source,
            columns=tuple(_column(c, f"{where}.columns[{j}]") for j, c in enumerate(
                _list(item.get("columns"), f"{where}.columns", MAX_COLUMNS))),
            where=tuple(_condition(c, f"{where}.where[{j}]") for j, c in enumerate(
                _list(item.get("where"), f"{where}.where", MAX_WHERE))),
            sort=tuple(_sort(s, f"{where}.sort[{j}]") for j, s in enumerate(
                _list(item.get("sort"), f"{where}.sort", MAX_SORTS))),
            group_by=_text(group, f"{where}.group_by", MAX_HEADER) if group else "",
            totals=totals,
        ))
    names = sheet_names([p.name for p in plans])
    return [SheetPlan(**{**p.__dict__, "name": n}) for p, n in zip(plans, names, strict=True)]


def parse_query_spec(raw: str, source_type: str) -> list[SheetPlan]:
    """The sheets of the ``spec`` query parameter of an export route. Each
    source must be of the type that the route reads."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SpreadsheetError('"spec" is not JSON') from exc
    plans = parse_spec(data)
    for plan in plans:
        if plan.source.get("type") != source_type:
            raise SpreadsheetError(
                f'sheet "{plan.name}": this route reads sources of type "{source_type}" only')
    return plans


def source_args(plan: SheetPlan, allowed: frozenset[str]) -> dict[str, Any]:
    """The filters of one source. An unknown one is refused by name, because a
    filter that is dropped would export more than the caller asked for."""
    args = {k: v for k, v in plan.source.items() if k != "type" and v not in (None, "")}
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise SpreadsheetError(
            f'sheet "{plan.name}": the source has no filter {", ".join(unknown)}. '
            f"It takes: {', '.join(sorted(allowed))}")
    return args


# ── Values ────────────────────────────────────────────────────────────────


def cell_value(value: Any) -> Any:
    """A value that openpyxl writes as itself. Any other type becomes its text.

    The copy of the sandbox data engine's rule (see the module docstring). A
    string keeps at most :data:`MAX_CELL_CHARS`, the cell limit of Excel.
    """
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)[:MAX_CELL_CHARS]
    if isinstance(value, float) and (value != value or abs(value) == float("inf")):
        return str(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, Decimal, date)):
        return value
    text = value.hex() if isinstance(value, bytes) else str(value)
    return text[:MAX_CELL_CHARS]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value: Any) -> Any:
    """*value* as a number, or *value* itself when it is not one."""
    if isinstance(value, bool) or not isinstance(value, str):
        return value
    cleaned = value.strip().replace(",", "")
    try:
        parsed = Decimal(cleaned)
    except InvalidOperation:
        return value
    if not parsed.is_finite() or abs(parsed.adjusted()) > _MAX_DIGITS:
        return value
    return int(parsed) if parsed == parsed.to_integral_value() and "." not in cleaned \
        else float(parsed)


def _moment(value: Any, kind: str) -> Any:
    """*value* as a date or a naive UTC datetime, or *value* itself."""
    if isinstance(value, str):
        text = value.strip()
        try:
            if _ISO_DATE.match(text):
                value = date.fromisoformat(text)
            elif _ISO_DATETIME.match(text):
                value = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:
                return value
        except ValueError:
            return value
    if isinstance(value, date) and value.year < _FIRST_YEAR:
        return value.isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.date() if kind == "date" else value
    if isinstance(value, date) and kind == "datetime":
        return datetime(value.year, value.month, value.day)
    return value


def infer_type(values: Sequence[Any]) -> str:
    """The type of a column from its stored values. Text is the default.

    A number stored as text stays text: a phone number or a PIN code that
    turned into a number would lose its leading zero.
    """
    found = [v for v in values if not _blank(v)]
    if not found:
        return "text"
    if all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in found):
        whole = all(float(v) == int(float(v)) for v in found)
        return "integer" if whole else "number"
    if all(isinstance(v, str) and _ISO_DATE.match(v.strip()) for v in found) or all(
            type(v) is date for v in found):
        return "date"
    if all((isinstance(v, str) and _ISO_DATETIME.match(v.strip())) or isinstance(v, datetime)
           for v in found):
        return "datetime"
    return "text"


# ── Shape ─────────────────────────────────────────────────────────────────


def _resolve(name: str, fields: Sequence[Field], where: str) -> str:
    """The key of the field that *name* names, by key and then by label."""
    wanted = name.strip().casefold()
    for f in fields:
        if f.key.casefold() == wanted:
            return f.key
    for f in fields:
        if f.label.casefold() == wanted:
            return f.key
    offered = ", ".join(
        f.key if f.key.casefold() == f.label.casefold() else f"{f.key} ({f.label})"
        for f in fields
    )
    raise SpreadsheetError(
        f'{where}: there is no field "{name}". The fields of this source are: {offered}')


def _passes(row: dict[str, Any], key: str, cond: Condition) -> bool:
    value = row.get(key)
    if cond.op == "empty":
        return _blank(value) == bool(cond.value)
    if cond.op == "not_empty":
        return (not _blank(value)) == bool(cond.value)
    have = "" if value is None else str(value).strip().casefold()
    if cond.op == "contains":
        return str(cond.value).strip().casefold() in have
    if cond.op == "in":
        return have in {str(v).strip().casefold() for v in cond.value}
    same = have == ("" if cond.value is None else str(cond.value).strip().casefold())
    return same if cond.op == "equals" else not same


def _order_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return (0, float(value))
    if isinstance(value, datetime):
        return (1, value.isoformat())
    if isinstance(value, date):
        return (1, value.isoformat())
    return (2, str(value).casefold())


def _sorted(rows: list[dict[str, Any]], sorts: Sequence[tuple[str, bool]]) -> list[dict[str, Any]]:
    """The rows in the order of *sorts*. An empty value is always last."""
    out = list(rows)
    for key, desc in reversed(sorts):
        filled = [r for r in out if not _blank(r.get(key))]
        empty = [r for r in out if _blank(r.get(key))]
        filled.sort(key=lambda r, k=key: _order_key(r[k]), reverse=desc)
        out = filled + empty
    return out


def _format(kind: str, currency: str) -> str:
    if kind == "integer":
        return "#,##0"
    if kind == "number":
        return "#,##0.00"
    if kind == "currency":
        return f'"{currency}" #,##0.00' if currency else "#,##0.00"
    if kind == "date":
        return "yyyy-mm-dd"
    if kind == "datetime":
        return "yyyy-mm-dd hh:mm"
    return "General"


@dataclass
class _Shaped:
    headers: list[str]
    types: list[str]
    currencies: list[str]
    #: ``(values, bold)`` in the order of the sheet.
    lines: list[tuple[list[Any], bool]]
    count: int
    groups: int
    filtered: bool


def shape(plan: SheetPlan, table: Table) -> _Shaped:
    """The lines of one sheet, from the rows that the server read."""
    where = f'sheet "{plan.name}"'
    fields = table.fields
    rows = table.rows
    for cond in plan.where:
        key = _resolve(cond.field, fields, f"{where} where")
        rows = [r for r in rows if _passes(r, key, cond)]

    if plan.columns:
        picked = [(c, _resolve(c.field, fields, f"{where} columns")) for c in plan.columns]
    else:
        used = [f for f in fields if any(not _blank(r.get(f.key)) for r in rows)] or fields
        picked = [(Column(field=f.key), f.key) for f in used[:MAX_COLUMNS]]
    labels = {f.key: f.label for f in fields}

    group = _resolve(plan.group_by, fields, f"{where} group_by") if plan.group_by else ""
    if group and group not in [k for _, k in picked]:
        picked.insert(0, (Column(field=group), group))

    types: list[str] = []
    currencies: list[str] = []
    for column, key in picked:
        kind = column.type or infer_type([r.get(key) for r in rows])
        code = column.currency
        if kind == "currency" and not code:
            codes = {str(r.get("currency") or "").upper() for r in rows
                     if not _blank(r.get("currency"))}
            code = next(iter(codes)) if len(codes) == 1 else ""
        types.append(kind)
        currencies.append(code if _CURRENCY.match(code or "") else "")
    headers = [column.header or labels[key] for column, key in picked]
    headers = [f"{h} (UTC)" if t == "datetime" and "(UTC)" not in h else h
               for h, t in zip(headers, types, strict=True)]

    def convert(value: Any, kind: str) -> Any:
        if kind in _NUMERIC:
            return _number(value)
        if kind in ("date", "datetime"):
            return _moment(value, kind)
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return "" if value is None else value

    typed = [
        {**r, **{key: convert(r.get(key), kind)
                 for (_, key), kind in zip(picked, types, strict=True)}}
        for r in rows
    ]

    summed: list[int] = []
    if plan.totals is True or (group and plan.totals is False):
        summed = [i for i, t in enumerate(types)
                  if t in _NUMERIC and picked[i][1] not in _NEVER_SUMMED]
    elif plan.totals:
        keys = [k for _, k in picked]
        for name in plan.totals:
            key = _resolve(name, fields, f"{where} totals")
            if key not in keys:
                raise SpreadsheetError(f'{where} totals: "{name}" is not one of the columns')
            i = keys.index(key)
            if types[i] not in _NUMERIC:
                raise SpreadsheetError(
                    f'{where} totals: "{name}" is not a number. Set its column "type"')
            summed.append(i)

    def total_line(part: list[dict[str, Any]], label: str) -> list[Any]:
        line: list[Any] = [None] * len(picked)
        for i in summed:
            key = picked[i][1]
            if types[i] == "currency" and not picked[i][0].currency:
                codes = {str(r.get("currency") or "").upper() for r in part
                         if not _blank(r.get("currency"))}
                if len(codes) > 1:
                    line[i] = "mixed currencies"
                    continue
            values = [r[key] for r in part
                      if isinstance(r[key], (int, float, Decimal))
                      and not isinstance(r[key], bool)]
            line[i] = sum(values) if values else 0
        spot = next((i for i in range(len(picked)) if i not in summed), None)
        if spot is not None:
            line[spot] = label
        return line

    def plain(part: list[dict[str, Any]]) -> list[tuple[list[Any], bool]]:
        return [([r[key] for _, key in picked], False) for r in part]

    sorts = [(_resolve(n, fields, f"{where} sort"), d) for n, d in plan.sort]
    ordered = _sorted(typed, sorts)
    lines: list[tuple[list[Any], bool]] = []
    groups = 0
    if group:
        desc = next((d for k, d in sorts if k == group), False)
        ordered = _sorted(ordered, [(group, desc)])
        buckets: dict[str, list[dict[str, Any]]] = {}
        for r in ordered:
            buckets.setdefault(str(r.get(group) if not _blank(r.get(group)) else "").casefold(),
                               []).append(r)
        for part in buckets.values():
            name = part[0].get(group)
            shown = "(blank)" if _blank(name) else str(name)
            lines.extend(plain(part))
            lines.append((total_line(part, f"{shown}: {len(part)} records"), True))
        groups = len(buckets)
    else:
        lines.extend(plain(ordered))
    if plan.totals:
        lines.append((total_line(ordered, f"Total: {len(ordered)} records"), True))
    return _Shaped(headers, types, currencies, lines, len(ordered), groups, bool(plan.where))


# ── The workbook ──────────────────────────────────────────────────────────


def _cell(sheet: Any, value: Any, number_format: str = "General") -> Any:
    """One guarded cell of a sheet."""
    from openpyxl.cell import WriteOnlyCell

    cell = WriteOnlyCell(sheet, cell_value(value))
    if isinstance(cell.value, str):
        # A string is a string, never a formula or an Excel error code.
        cell.data_type = "s"
        if csv_cell(cell.value) != cell.value:
            cell.quotePrefix = True
    elif cell.value is not None and number_format != "General":
        cell.number_format = number_format
    return cell


def _width(value: Any) -> int:
    if isinstance(value, datetime):
        return 16
    if isinstance(value, date):
        return 10
    if isinstance(value, (int, float, Decimal)):
        return len(f"{value:,}") + 4
    return max((len(part) for part in str(value or "").split("\n")), default=0)


def build(plans: Sequence[SheetPlan], tables: Sequence[Table]) -> Built:
    """One ``.xlsx`` from the shape and the rows of each sheet.

    The row cap holds for the whole file, over the rows that the sources read.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    read = sum(len(t.rows) for t in tables)
    if read > MAX_EXPORT_ROWS:
        raise too_many(read)
    shaped = [shape(p, t) for p, t in zip(plans, tables, strict=True)]
    # Write-only: a large file never holds one object for each cell.
    book = Workbook(write_only=True)
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="D9E1F2")
    summary: list[dict[str, Any]] = []
    for plan, part in zip(plans, shaped, strict=True):
        sheet = book.create_sheet(plan.name)
        # A write-only sheet takes its widths and its frozen row before any row.
        widths = [len(h) for h in part.headers]
        for values, _ in part.lines:
            for c, value in enumerate(values):
                widths[c] = max(widths[c], _width(value))
        for c, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(c)].width = min(width + 2, MAX_WIDTH)
        sheet.freeze_panes = "A2"
        if not any(strong for _, strong in part.lines) and part.headers:
            sheet.auto_filter.ref = (
                f"A1:{get_column_letter(len(part.headers))}{len(part.lines) + 1}")
        head = []
        for header in part.headers:
            cell = _cell(sheet, header)
            cell.font = bold
            cell.fill = fill
            head.append(cell)
        sheet.append(head)
        for values, strong in part.lines:
            line = []
            for c, value in enumerate(values):
                cell = _cell(sheet, value, _format(part.types[c], part.currencies[c]))
                if strong:
                    cell.font = bold
                line.append(cell)
            sheet.append(line)
        summary.append({"sheet": plan.name, "rows": part.count,
                        "columns": part.headers, "groups": part.groups})
    out = io.BytesIO()
    book.save(out)
    return Built(out.getvalue(), summary, sum(s["rows"] for s in summary))


#: The bytes of the summary header. An HTTP client refuses a very long header.
_SUMMARY_BYTES = 8000


def summary_header(built: Built) -> str:
    """The sheets of the file as ASCII JSON, short enough for one header."""
    sheets = [{**s, "columns": [str(c)[:60] for c in s["columns"][:40]]}
              for s in built.sheets]
    text = json.dumps(sheets, ensure_ascii=True)
    if len(text) > _SUMMARY_BYTES:
        text = json.dumps([{**s, "columns": []} for s in sheets], ensure_ascii=True)
    return text


def workbook_response(built: Built) -> Response:
    """The file as the answer of an export route."""
    return Response(
        content=built.data,
        media_type=MEDIA_TYPE,
        headers={
            # A fixed name: the caller names its own file, and a name built
            # from record text would be a header-injection surface.
            "Content-Disposition": 'attachment; filename="export.xlsx"',
            "X-Export-Rows": str(built.rows),
            "X-Export-Summary": summary_header(built),
        },
    )


__all__ = [
    "MAX_EXPORT_ROWS",
    "MAX_SPEC_CHARS",
    "MEDIA_TYPE",
    "parse_query_spec",
    "source_args",
    "summary_header",
    "workbook_response",
    "Built",
    "Column",
    "Condition",
    "Field",
    "SheetPlan",
    "SpreadsheetError",
    "Table",
    "build",
    "cell_value",
    "infer_type",
    "parse_spec",
    "shape",
    "sheet_names",
    "too_many",
]
