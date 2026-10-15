"""A report from the chat: one file the web draws, and one PDF (B1).

Spec: ``project-docs/specs/projects_ai_chat.md`` §14.9.

Owner directive, 2026-10-16: a report that a member asks for must be "a proper
report with proper imagery and graphs", drawn as a card in the chat, and it
must use the systems we have instead of text that the model makes up.

:func:`make_report` takes ONE spec and makes three things from it:

* ``outputs/<slug>.report.json`` -- the report file of record
  (:data:`REPORT_KIND`, version :data:`REPORT_VERSION`). Its visuals stay as
  DATA, so the web draws live charts from them.
* ``outputs/<slug>.pdf`` -- the same report as A4 pages. ``documents.prepare``
  draws every visual by code, and the ONE PDF seam
  (``gateway.pdf_render.render_pdf``, kind ``document``) lays it out.
* ONE ``generative_ui`` event with the template ``report``, through
  ``emit_generative_ui``. Its data is the file plus its ``path``.

On WhatsApp it writes no file and draws no card: the PDF goes to the member
through the run's own document delivery (``whatsapp_ui.queue_document``). A
channel changes the delivery, never the function (owner rule, 2026-10-10).

The checks are the checks of ``documents.py``, not a copy: the same refused
keys, the same text limits, the same section rule and the same visual
builders. This module adds only what a report has and a document does not:
the summary, the KPI tiles, the period and the sources.

A mapper (slice B3) that builds a spec by code calls :func:`make_report`
with it, and the numbers then come from code, never from the model.

Fence: ``tests/unit/test_chat_reports.py``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import re
from datetime import date
from typing import Any

from acb_skills import documents as docs
from acb_skills.documents import DocumentError

#: The ``kind`` of a report file. The web viewer reads it.
REPORT_KIND = "metorite.report"
REPORT_VERSION = 1
#: The genUI template that draws a report card in the chat.
REPORT_TEMPLATE = "report"
#: The suffix of a report file. The web opens a file with it as a report.
REPORT_SUFFIX = ".report.json"

SUMMARY_MAX = 2000
MAX_KPIS = 8
KPI_LABEL_MAX = 60
KPI_VALUE_MAX = 40
KPI_UNIT_MAX = 16
KPI_DELTA_MAX = 24
PERIOD_MAX = 80
DATE_MAX = 40
MAX_SOURCES = 12
SLUG_MAX = 60
GOOD = ("up", "down")

#: Markup that a markdown-lite field never holds: an HTML tag, an image, a
#: link, or a URL. The web draws the text as text, and the PDF escapes it.
_MARKUP = re.compile(
    r"<\s*/?\s*[a-zA-Z][^>]*>|!\[|\]\(|https?://|\bwww\.|\b(?:javascript|data|file):\S",
    re.IGNORECASE)
_SLUG_UNSAFE = re.compile(r"[^\w]+", re.UNICODE)
#: A list item of markdown-lite at the start of a line.
_LIST_ITEM = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
#: ``*italic*``, and not a part of ``**bold**``. The PDF prints the words.
_ITALIC = re.compile(r"(?<![*\w])\*(?!\*)([^*\n]+?)\*(?![*\w])")

#: The field guide. ``make_report({})`` and ``load_design_system("report")``
#: both return it, so it costs no schema tokens.
REPORT_GUIDE = """\
make_report(spec) -- one report: a card in the chat, a full view, and a PDF.

spec fields (only "title" and "sections" are required):
- "title" (120 characters), "subtitle" (200), "for" (who it is for, 200),
  "period" (for example "Q3 2026", 80), "date" (40, default today).
- "summary": the answer in a few lines, 2000 characters. Markdown-lite:
  **bold**, *italic*, "- " lists and line breaks. No HTML, image or link.
- "kpis": 1 to 8 headline numbers, each {"label", "value", "unit",
  "delta" (for example "+12%"), "good": "up"|"down"}. They show as tiles
  at the top.
- "sections": 1 to 30, each {"heading", "text" (markdown-lite, 2400),
  "bullets" (16 lines of 240), "visual"}. Each section needs one of them.
- "sources": up to 12 lines that say where the numbers came from, for
  example "CRM pipeline, 16 Oct 2026".
- "filename": the file name, else the title.

A "visual" is {"kind", "title" (required), ...its data}. A report holds at
most 12 visuals, and the kpis count as one.
- chart: {"type": bar|line|donut|progress (and more when the chart engine is
  on), "labels", "values" (or "series": [{"name", "values"}]), "unit"}
- stats: {"tiles": [{"label", "value", "delta", "good": "up"|"down"}]}
- table: {"columns" (1-6), "rows": [[cell, ...]] (1-20, one cell a column)}
- board: {"columns": [{"name", "cards": [{"title", "meta"}]}] (1-4)}
- timeline: {"events": [{"date", "title", "state"}]}
- gantt: {"rows": [{"label", "start", "end"}]} (ISO dates)
- agenda: {"items": [{"start": "09:30", "end", "title", "detail"}]}

Rules: collect the facts first, from the tools that compute them. Copy each
number as the tool gave it, and never invent one. Put each number in a kpi or
a visual, not only in the text. Write one point a section: a heading that
says the point, a short text, and one visual.
"""


def _markdown_lite(value: str, where: str, key: str) -> str:
    if value and _MARKUP.search(value):
        raise DocumentError(
            f'{where}: "{key}" takes markdown-lite text only: **bold**, *italic*, '
            "lists and line breaks. Remove the HTML, the image or the link")
    return value


def _scalar(data: dict[str, Any], key: str, limit: int, where: str, *,
            required: bool = False) -> str | int | float | None:
    """A number stays a number. Text stays text, in its limit."""
    raw = data.get(key)
    if isinstance(raw, bool):
        raise DocumentError(f'{where}: "{key}" must be a number or text')
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not math.isfinite(raw):
            raise DocumentError(f'{where}: "{key}" must be a finite number')
        return raw
    text = docs._text(data, key, limit, where, required=required)
    return text or None


def _kpis(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise DocumentError(f'"kpis" must be a list of 1 to {MAX_KPIS} tiles')
    if len(raw) > MAX_KPIS:
        raise DocumentError(f'"kpis" holds at most {MAX_KPIS} tiles. Keep the headline numbers')
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        where = f"kpis[{i}]"
        if not isinstance(item, dict):
            raise DocumentError(f'{where} must be an object with "label" and "value"')
        tile: dict[str, Any] = {
            "label": docs._text(item, "label", KPI_LABEL_MAX, where, required=True),
            "value": _scalar(item, "value", KPI_VALUE_MAX, where, required=True),
        }
        unit = docs._text(item, "unit", KPI_UNIT_MAX, where)
        if unit:
            tile["unit"] = unit
        delta = _scalar(item, "delta", KPI_DELTA_MAX, where)
        if delta is not None:
            tile["delta"] = delta
        if item.get("good") is not None:
            good = str(item["good"]).strip().lower()
            if good not in GOOD:
                raise DocumentError(f'{where}: "good" must be "up" or "down"')
            tile["good"] = good
        out.append(tile)
    return out


def _sections(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise DocumentError('a report needs "sections": a list of at least one')
    if len(raw) > docs.MAX_SECTIONS:
        raise DocumentError(
            f"a report holds at most {docs.MAX_SECTIONS} sections. Merge or cut some")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        checked = docs._section(item, i)  # the limits of a document section
        where = f"sections[{i}]"
        section: dict[str, Any] = {}
        if checked["heading"]:
            section["heading"] = checked["heading"]
        if checked["text"]:
            section["text"] = _markdown_lite(checked["text"], where, "text")
        if checked["bullets"]:
            section["bullets"] = [_markdown_lite(b, where, "bullets") for b in checked["bullets"]]
        if "visual" in checked:
            visual = checked["visual"]
            if not isinstance(visual, dict):
                raise DocumentError(
                    f'{where}: "visual" must be an object with "kind" and its data')
            kind = str(visual.get("kind") or "").strip().lower()
            if kind not in docs.VISUAL_KINDS:
                raise DocumentError(
                    f'{where}: "visual.kind" must be one of: {", ".join(docs.VISUAL_KINDS)}')
            section["visual"] = {"kind": kind, **{k: v for k, v in visual.items() if k != "kind"}}
        out.append(section)
    return out


def _sources(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DocumentError('"sources" must be a list of text')
    if len(raw) > MAX_SOURCES:
        raise DocumentError(f'"sources" holds at most {MAX_SOURCES} lines')
    return [docs._text({"s": s}, "s", docs.BULLET_MAX, f"sources[{i}]", required=True)
            for i, s in enumerate(raw)]


def slug_of(title: str, given: str = "") -> str:
    """A safe file stem from *given*, else from *title*: ``q3-pipeline-review``."""
    stem = (given or title or "report").strip()
    for suffix in (REPORT_SUFFIX, ".json", ".pdf", ".md"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    slug = _SLUG_UNSAFE.sub("-", stem.lower()).strip("-_")[:SLUG_MAX].strip("-_")
    return slug or "report"


def check_report(spec: Any, *, today: date | None = None) -> dict[str, Any]:
    """The checked report, as the file holds it with no ``pdf``, or
    :class:`DocumentError` that says what to fix.

    It draws nothing. :func:`layout_of` draws the visuals, and a visual that
    cannot be drawn is refused there.
    """
    if not isinstance(spec, dict):
        raise DocumentError('"spec" must be an object')
    docs._scan(spec, "spec")
    report: dict[str, Any] = {
        "kind": REPORT_KIND,
        "version": REPORT_VERSION,
        "title": docs._text(spec, "title", docs.TITLE_MAX, "spec", required=True),
    }
    for key, limit in (("subtitle", docs.SUBTITLE_MAX), ("for", docs.SUBTITLE_MAX)):
        value = docs._text(spec, key, limit, "spec")
        if value:
            report[key] = value
    report["date"] = (docs._text(spec, "date", DATE_MAX, "spec")
                      or (today or date.today()).strftime("%d %b %Y"))
    period = docs._text(spec, "period", PERIOD_MAX, "spec")
    if period:
        report["period"] = period
    summary = _markdown_lite(docs._text(spec, "summary", SUMMARY_MAX, "spec"), "spec", "summary")
    if summary:
        report["summary"] = summary
    kpis = _kpis(spec.get("kpis"))
    if kpis:
        report["kpis"] = kpis
    report["sections"] = _sections(spec.get("sections"))
    visuals = sum(1 for s in report["sections"] if "visual" in s) + (1 if kpis else 0)
    if visuals > docs.MAX_VISUALS:
        raise DocumentError(
            f"a report holds at most {docs.MAX_VISUALS} visuals, and the kpis count "
            "as one. Merge or cut some")
    sources = _sources(spec.get("sources"))
    if sources:
        report["sources"] = sources
    return report


# ── The PDF: the same report, as the layout of ``documents.prepare`` ────────


def _pdf_text(text: str) -> str:
    """Markdown-lite as the text of a PDF: a list item gets a bullet, and
    italic prints as plain words. ``**bold**`` and line breaks stay, because
    ``pdf_render`` draws both."""
    return _ITALIC.sub(r"\1", _LIST_ITEM.sub(lambda m: m.group(1) + "• ", text))


def _shown(value: Any) -> str:
    """A number as the member wrote it: 128, 12.5, 1234567.5. Never "1.2e+06"."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _tile(kpi: dict[str, Any]) -> dict[str, Any]:
    """One KPI as a tile of the ``stats`` visual."""
    unit = kpi.get("unit", "")
    value = _shown(kpi["value"])
    tile: dict[str, Any] = {"label": kpi["label"],
                            "value": f"{value}{unit}" if unit in ("%", "") else f"{value} {unit}"}
    delta = kpi.get("delta")
    if delta is not None:
        tile["delta"] = (f"+{_shown(delta)}" if isinstance(delta, (int, float)) and delta > 0
                         else _shown(delta))
    if kpi.get("good"):
        tile["good"] = kpi["good"]
    return tile


def layout_of(report: dict[str, Any]) -> dict[str, Any]:
    """The PDF layout of a checked report. It draws every visual by code.

    The summary and the KPI tiles lead, as one section, and the sources end
    it. Those two are drawn apart from the member's sections and joined after,
    so a report of 30 sections still fits ``prepare``'s own limit.
    """
    head = {"format": "report", "title": report["title"],
            "subtitle": report.get("subtitle", ""), "for": report.get("for", ""),
            "date": report["date"]}
    body = []
    for section in report["sections"]:
        part = {k: v for k, v in section.items() if k in ("heading", "visual")}
        if section.get("text"):
            part["text"] = _pdf_text(section["text"])
        if section.get("bullets"):
            part["bullets"] = [_pdf_text(b) for b in section["bullets"]]
        body.append(part)
    layout = docs.prepare({**head, "sections": body})
    lead: dict[str, Any] | None = None
    if report.get("summary") or report.get("kpis"):
        lead = {"heading": "Summary" if report.get("summary") else "Key figures"}
        if report.get("summary"):
            lead["text"] = _pdf_text(report["summary"])
        if report.get("kpis"):
            lead["visual"] = {"kind": "stats", "title": "Key figures",
                              "tiles": [_tile(k) for k in report["kpis"]]}
    tail = {"heading": "Sources", "bullets": report["sources"]} if report.get("sources") else None
    extra = [s for s in (lead, tail) if s]
    if extra:
        drawn = docs.prepare({**head, "sections": extra})["sections"]
        layout["sections"] = ([drawn[0]] if lead else []) + layout["sections"] \
            + ([drawn[-1]] if tail else [])
    if report.get("period"):
        # The meta line of the title reads "Prepared for X · <period> · <date>".
        layout["date"] = f"{report['period']} · {report['date']}"
    return layout


# ── The tool ────────────────────────────────────────────────────────────────


async def make_report(spec: dict[str, Any]) -> dict[str, Any]:
    """Make a REPORT: a card in the chat, a full view, and a PDF, with a
    summary, KPI tiles, charts, tables and the sources. Use it for EVERY
    report, review or analysis that the member asks for. Never write a report
    as a .md file. Collect the facts first, from the tools that compute them,
    and never invent a number.

    spec: {"title", "subtitle", "for", "period", "summary", "kpis": [{"label",
    "value", "unit", "delta", "good"}], "sections": [{"heading", "text",
    "bullets", "visual"}] x 1-30, "sources": [str]}. Call make_report({})
    once first: it returns the field guide and the visual shapes.

    Returns {"ok": true, "path", "pdf_path"}, or {"ok": false, "error"}: fix
    the spec and call again.
    """
    if spec is None or spec == {}:
        return {"ok": False, "error": "Nothing was made. Here is the field guide. "
                                      "Call again with a spec.", "guide": REPORT_GUIDE}
    from acb_skills.whatsapp_ui import current_run

    run = current_run()
    if run is not None and run.document is None:
        return {"ok": False, "error": "documents are off in this chat. Answer in text"}
    try:
        report = check_report(spec)
        layout = await asyncio.to_thread(layout_of, report)
    except (ValueError, TypeError, OverflowError) as exc:
        return {"ok": False, "error": str(exc)}
    given = spec.get("filename") if isinstance(spec.get("filename"), str) else ""
    if run is not None:
        # WhatsApp: the same PDF, as a file the member can forward. No card.
        from acb_skills.whatsapp_ui import queue_document

        return await queue_document(layout, docs.filename_of(report["title"], given))
    return await _make_on_the_web(report, layout, slug_of(report["title"], given))


async def _make_on_the_web(report: dict[str, Any], layout: dict[str, Any],
                           slug: str) -> dict[str, Any]:
    try:
        from gateway.pdf_render import render_pdf  # the ONE PDF seam
    except ImportError:  # pragma: no cover - the skill runs in the gateway
        return {"ok": False, "error": "this server cannot make a PDF"}
    try:
        pdf = await render_pdf("document", json.dumps(layout, ensure_ascii=False),
                               member=docs._member(), org=docs._org())
    except ValueError as exc:  # pdf_render.PdfRenderError says what to fix
        return {"ok": False, "error": f"the PDF could not be made: {exc}"}
    # The package re-exports the FUNCTION under the module's name, so the
    # module is imported by name. A test records the writes through it.
    wa = importlib.import_module("acb_skills.write_artifact")
    saved = await wa.write_artifact(f"outputs/{slug}.pdf", pdf, encoding=None)
    if "error" in saved or not saved.get("path"):
        return {"ok": False, "error": f"the PDF was made but not saved: "
                                      f"{saved.get('error') or 'no file'}"}
    pdf_path = str(saved["path"])
    # The report file takes the stem of the PDF that was really written. A
    # free PDF name means that a file of that stem has no PDF, so it is
    # replaced: a new name would break the ".report.json" suffix.
    stem = pdf_path.rsplit("/", 1)[-1][: -len(".pdf")]
    body = {**report, "pdf": pdf_path}
    written = await wa.write_artifact(
        f"outputs/{stem}{REPORT_SUFFIX}",
        json.dumps(body, ensure_ascii=False, indent=2), overwrite=True)
    if "error" in written or not written.get("path"):
        return {"ok": False, "error": f"the report was made but not saved: "
                                      f"{written.get('error') or 'no file'}"}
    path = str(written["path"])
    card = {"type": "template",
            "props": {"name": REPORT_TEMPLATE, "data": {**body, "path": path}}}
    try:
        drawn = await wa.emit_generative_ui(json.dumps(card, ensure_ascii=False))
    except Exception as exc:  # the emit never raises by contract
        drawn = {"ok": False, "error": str(exc)}
    if drawn.get("ok") is True:
        return {"ok": True, "path": path, "pdf_path": pdf_path,
                "note": "The member sees the report card. Say in one or two lines "
                        "what it shows. Do not paste a link."}
    # No card (no live chat, or a chat that cannot draw it): the PDF is still
    # there, so the member gets a link to it.
    return {"ok": True, "path": path, "pdf_path": pdf_path,
            "download_url": saved.get("download_url"),
            "note": "The chat could not draw the report card. Link the "
                    "download_url of the PDF, and say in one or two lines what "
                    "it holds."}


__all__ = [
    "REPORT_GUIDE",
    "REPORT_KIND",
    "REPORT_SUFFIX",
    "REPORT_TEMPLATE",
    "REPORT_VERSION",
    "check_report",
    "layout_of",
    "make_report",
    "slug_of",
]
