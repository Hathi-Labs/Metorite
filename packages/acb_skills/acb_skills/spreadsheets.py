"""``make_spreadsheet``: an Excel file in the chat, in the shape that was asked.

Spec: ``project-docs/specs/chat_parity.md`` §DOC row DOC-6.

The model sends ONE spec: a **query** and a **shape**. It never sends the rows
of a record. For a CRM or a Projects query, this tool asks the gateway to build
the file as the member, through the access rules of that app
(``GET /crm/export/workbook.xlsx`` and ``GET /projects/export/workbook.xlsx``),
and gets back the file. For ``rows`` the model gives a small table that it
already holds, and the same writer builds it here.

The file becomes a card in the chat through the safe writer of
``documents.save_outputs``. There is one XLSX writer, ``gateway.xlsx_export``.

Fence: ``tests/unit/test_make_spreadsheet.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

#: The most rows that a ``rows`` source holds. A larger set comes from a query.
MAX_INLINE_ROWS = 500
#: The most keys of one inline row.
MAX_INLINE_KEYS = 40
#: The longest wait for the gateway to read the rows and build the file.
TIMEOUT_SECONDS = 240.0
FILENAME_MAX = 80
#: The characters of a refusal of the gateway that the model reads.
_DETAIL_MAX = 2000

_ROUTES = {
    "crm": "/crm/export/workbook.xlsx",
    "tasks": "/projects/export/workbook.xlsx",
}
_APPS = {"crm": "CRM", "tasks": "Projects"}


def filename_of(title: str, given: str = "") -> str:
    """A safe ``.xlsx`` file name from *given*, else from *title*."""
    from acb_skills.documents import _FILENAME_UNSAFE

    stem = (given or title or "Export").strip()
    if stem.lower().endswith(".xlsx"):
        stem = stem[:-5]
    stem = _FILENAME_UNSAFE.sub(" ", stem)
    stem = " ".join(stem.split())[:FILENAME_MAX].strip(" .") or "Export"
    return f"{stem}.xlsx"


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _source_types(spec: Any) -> list[str]:
    sheets = spec.get("sheets") if isinstance(spec, dict) else None
    if not isinstance(sheets, list) or not sheets:
        raise ValueError('the spec needs "sheets": a list of at least one')
    kinds = []
    for i, sheet in enumerate(sheets):
        source = sheet.get("source") if isinstance(sheet, dict) else None
        kind = str((source or {}).get("type") or "").strip().lower() \
            if isinstance(source, dict) else ""
        if kind not in ("crm", "tasks", "rows"):
            raise ValueError(
                f'sheets[{i}].source needs "type": "crm", "tasks" or "rows"')
        kinds.append(kind)
    return kinds


def _normalised(spec: dict[str, Any]) -> dict[str, Any]:
    """The sheets of *spec*, with the common names of a CRM entity resolved."""
    from acb_skills.crm_tools import _ENTITY_ALIASES, ENTITY_LABELS

    sheets = []
    for sheet in spec["sheets"]:
        source = dict(sheet["source"])
        source["type"] = str(source["type"]).strip().lower()
        if source["type"] == "crm":
            name = str(source.get("module") or "").strip()
            low = name.lower()
            source["module"] = low if low in ENTITY_LABELS else _ENTITY_ALIASES.get(low, name)
        sheets.append({**sheet, "source": source})
    return {"sheets": sheets}


def _filters(spec: dict[str, Any]) -> list[str]:
    """One line for each sheet: what the file was filtered by."""
    out = []
    for sheet in spec["sheets"]:
        source = sheet["source"]
        parts = [f"{k}={v}" for k, v in source.items()
                 if k not in ("type", "rows", "module") and v not in (None, "")]
        parts += [" ".join(f"{k}={v}" for k, v in c.items())
                  for c in sheet.get("where") or [] if isinstance(c, dict)]
        what = source.get("module") or source["type"]
        name = sheet.get("name") or what
        out.append(f"{name}: {what}, {'; '.join(parts) if parts else 'no filter'}")
    return out


def _inline(plan: Any) -> Any:
    """The table of a ``rows`` source."""
    from gateway import xlsx_export as xe

    unknown = sorted(set(plan.source) - {"type", "rows"})
    if unknown:
        raise xe.SpreadsheetError(f'a "rows" source takes "rows" only, not {", ".join(unknown)}')
    rows = plan.source.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise xe.SpreadsheetError('"rows" is a list of objects, one for each row')
    if len(rows) > MAX_INLINE_ROWS:
        raise xe.SpreadsheetError(
            f'"rows" holds at most {MAX_INLINE_ROWS} rows. Use a "crm" or "tasks" source, '
            "so the server reads them")
    keys: dict[str, None] = {}
    for row in rows:
        keys.update(dict.fromkeys(map(str, row)))
    if len(keys) > MAX_INLINE_KEYS:
        raise xe.SpreadsheetError(f'"rows" holds at most {MAX_INLINE_KEYS} different keys')
    clean = [{str(k): v if v is None or isinstance(v, (str, int, float, bool)) else json.dumps(v)
              for k, v in row.items()} for row in rows]
    return xe.Table(fields=[xe.Field(k, k) for k in keys], rows=clean)


def _build_inline(spec: dict[str, Any]) -> tuple[bytes, int, list[dict[str, Any]]]:
    from gateway import xlsx_export as xe

    plans = xe.parse_spec(spec)
    built = xe.build(plans, [_inline(p) for p in plans])
    return built.data, built.rows, built.sheets


async def _fetch(kind: str, spec: dict[str, Any]) -> tuple[bytes, int, list[dict[str, Any]]]:
    """The file of a query, built by the gateway as the member."""
    from acb_common.tool_refusals import response_body, safe_detail

    from acb_skills.crm_tools import _current_user_email, _gateway_url, _headers

    query = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        resp = await client.request(
            "GET", f"{_gateway_url()}{_ROUTES[kind]}", headers=_headers(),
            params={"spec": query},
        )
    status = int(resp.status_code)
    if status >= 400:
        safe, _ = safe_detail(status, response_body(resp),
                              member=_current_user_email(), limit=_DETAIL_MAX)
        if status == 403:
            raise ValueError(f"{_APPS[kind]} is not open to you. {safe}".strip())
        if status == 404 and safe in ("", "Not Found"):
            raise ValueError(f"{_APPS[kind]} is off for this organization.")
        raise ValueError(safe or f"the export failed ({status})")
    rows = int(resp.headers.get("X-Export-Rows") or 0)
    sheets = json.loads(resp.headers.get("X-Export-Summary") or "[]")
    return bytes(resp.content), rows, sheets


async def make_spreadsheet(spec: dict[str, Any]) -> dict[str, Any]:
    """Make an Excel FILE (.xlsx) in the chat: export records, or a table, in
    the shape the member asks. Use it for any "export", "Excel" or
    "spreadsheet" request. The server reads the records as the member, so the
    file holds ALL matching rows (up to 10,000), not a page. Do not read the
    records first, and never type them into the spec.

    spec: {"title", "filename", "sheets": [1-10 of {"name", "source",
    "columns", "where", "sort", "group_by", "totals"}]}.
    source, one of:
    - {"type": "crm", "module": "leads"|"deals"|"contacts"|"organizations"|a
      module name (crm_list_modules), filters: "q", "owner" ("me"),
      "status_id", "created_from", "created_to", "updated_from", "updated_to",
      "close_from", "close_to", "custom": {"key": "value"}}
    - {"type": "tasks", filters: "project_id", "include_subtree",
      "status_category", "assignees", "unassigned", "overdue", "due_before",
      "tags", "q", "include_archived", "scope": "mine"}
    - {"type": "rows", "rows": [{"key": value}]} for a small table you
      already hold (500 rows).
    A file takes crm sources, or tasks sources, or rows. Not a mix.
    columns: field names or {"field", "header", "type": text|number|integer|
    currency|date|datetime, "currency": "INR"}, in order. With none, every
    field that holds a value. where: [{"field", "equals"|"not_equals"|
    "contains"|"in"|"empty"|"not_empty": value}]. sort: ["city", "-amount"].
    group_by: a field, and each group gets a subtotal row. totals: true, or the
    number fields to add up, for a total row with the record count.

    An unknown field answers with the fields that exist: fix and call again.
    Returns {"ok": true, "path", "rows", "sheets", "filters"}: a card shows the
    file, so name it, say the row count and the filters, and paste no link.
    Or {"ok": false, "error"}: nothing was exported.
    """
    from acb_skills.whatsapp_ui import current_run

    if current_run() is not None:
        return _error("WhatsApp cannot send a spreadsheet. Ask in the web chat.")
    try:
        kinds = _source_types(spec)
        if len(set(kinds)) > 1:
            return _error("one file takes crm sources, or tasks sources, or rows. "
                          "Make one file for each.")
        kind = kinds[0]
        checked = _normalised(spec)
        if kind == "rows":
            data, rows, sheets = await asyncio.to_thread(_build_inline, checked)
        else:
            from acb_skills.crm_tools import _current_user_email

            if "@" not in _current_user_email():
                # A shared room runs as the room, which is no member.
                return _error(f"This chat has no one member to act as, so it cannot read "
                              f"{_APPS[kind]}. Ask in your own chat.")
            data, rows, sheets = await _fetch(kind, checked)
    except ImportError:  # pragma: no cover - the skill runs in the gateway
        return _error("this server cannot make a spreadsheet")
    except httpx.HTTPError as exc:
        return _error(f"the export did not finish ({type(exc).__name__}). Nothing was exported.")
    except (ValueError, TypeError, OverflowError) as exc:
        return _error(f"{exc} Nothing was exported.")

    from acb_skills import documents as docs

    title = spec.get("title") if isinstance(spec.get("title"), str) else ""
    given = spec.get("filename") if isinstance(spec.get("filename"), str) else ""
    name = filename_of(title or "", given or "")
    saved = await docs.save_outputs(name[: -len(".xlsx")], (".xlsx",),
                                    lambda _rels: {".xlsx": data}, announce=True)
    if "error" in saved:
        return _error(f"the spreadsheet was made but not saved: {saved['error']}")
    written = saved["files"][".xlsx"]
    return {"ok": True, "path": written["path"],
            "download_url": written.get("download_url"),
            "rows": rows, "sheets": sheets, "filters": _filters(checked),
            "row_cap": 10_000,
            "note": "A card in the chat shows the file. Name it, say how many rows each "
                    "sheet holds and which filters picked them, and paste no link."}


__all__ = ["MAX_INLINE_ROWS", "filename_of", "make_spreadsheet"]
