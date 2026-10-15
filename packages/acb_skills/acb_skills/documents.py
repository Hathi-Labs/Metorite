"""A deck or a report from a short spec: check it, and draw its visuals (WS-47 WAC-12).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §15.

The model sends ONE spec: the format (``deck`` or ``report``), a title, and
the slides or the sections. :func:`prepare` checks it and returns the LAYOUT
that ``gateway.pdf_render`` lays out in its child process (source kind
``document``). Two rules make that safe:

* **The model gives text and numbers, never markup or bytes.** A spec key
  that would carry an image, an SVG, HTML or a URL is refused by name
  (:data:`_REFUSED_KEYS`). Text stays text: ``pdf_render`` escapes each value
  before it builds HTML.
* **Only code draws a visual.** Each visual goes through the SAME builder as
  the chat image of its kind (``whatsapp_ui.build``), inside
  ``whatsapp_ui.printing``. So a card keeps its SVG (vector in the PDF) and a
  chart comes from the chart engine in its light mode. A deck has the look of
  a chart on WhatsApp.

Fence: ``tests/unit/test_wac_documents.py``.
"""

from __future__ import annotations

import base64
import re
from datetime import date
from typing import Any

#: The formats. A deck is 16:9 slides, a report is A4 pages.
FORMATS = ("deck", "report")
#: The slide types of a deck.
SLIDE_TYPES = ("cover", "section", "content", "quote", "closing")
#: The visuals a slide or a section may hold: the image kinds of ``whatsapp_ui``.
VISUAL_KINDS = ("chart", "stats", "table", "board", "timeline", "gantt", "agenda")

MAX_SLIDES = 20
MAX_SECTIONS = 30
MAX_VISUALS = 12
MAX_BULLETS = 8
TITLE_MAX = 120
SUBTITLE_MAX = 200
TEXT_MAX = 1200
BULLET_MAX = 240
QUOTE_MAX = 400
CAPTION_MAX = 1024
FILENAME_MAX = 80

#: Keys that would carry markup, bytes or a resource. The model never gives
#: one: only code draws a visual, and a PDF loads nothing.
_REFUSED_KEYS = frozenset({
    "svg", "png", "jpg", "jpeg", "image", "images", "img", "html", "css",
    "style", "url", "src", "href", "link", "data_url", "base64", "b64", "pdf",
})
#: The deepest nesting a spec may have. A visual's data is three levels deep.
_MAX_DEPTH = 8
_FILENAME_UNSAFE = re.compile(r"[^\w .,()&+-]+", re.UNICODE)


class DocumentError(ValueError):
    """The spec cannot make a document. The message says what to fix."""


def _scan(value: Any, path: str, depth: int = 0) -> None:
    """Refuse a key of :data:`_REFUSED_KEYS` anywhere, and a deep nest."""
    if depth > _MAX_DEPTH:
        raise DocumentError(f"{path} nests too deep")
    if isinstance(value, dict):
        for key, inner in value.items():
            if str(key).strip().lower() in _REFUSED_KEYS:
                raise DocumentError(
                    f'{path}.{key}: a document takes text and numbers only. '
                    "Metorite draws every visual itself, from a \"visual\" spec")
            _scan(inner, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            _scan(inner, f"{path}[{i}]", depth + 1)


def _text(data: dict[str, Any], key: str, limit: int, where: str, *,
          required: bool = False) -> str:
    raw = data.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise DocumentError(f'{where}: "{key}" is required')
        return ""
    if not isinstance(raw, (str, int, float)):
        raise DocumentError(f'{where}: "{key}" must be text')
    value = str(raw).replace("\r", "").strip()
    if len(value) > limit:
        raise DocumentError(f'{where}: "{key}" is longer than {limit} characters. Shorten it')
    return value


def _bullets(data: dict[str, Any], where: str, most: int) -> list[str]:
    raw = data.get("bullets")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DocumentError(f'{where}: "bullets" must be a list of text')
    if len(raw) > most:
        raise DocumentError(f'{where}: at most {most} bullets. Split it across slides or sections')
    out = []
    for i, item in enumerate(raw):
        out.append(_text({"b": item}, "b", BULLET_MAX, f"{where}.bullets[{i}]", required=True))
    return out


def filename_of(title: str, given: str = "") -> str:
    """A safe ``.pdf`` file name from *given*, else from *title*."""
    stem = (given or title or "Document").strip()
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    stem = _FILENAME_UNSAFE.sub(" ", stem)
    stem = " ".join(stem.split())[:FILENAME_MAX].strip(" .") or "Document"
    return f"{stem}.pdf"


def _visual(raw: Any, where: str) -> dict[str, Any]:
    """One visual, drawn by the chat image builder in print mode."""
    from acb_skills import whatsapp_ui as wui

    if not isinstance(raw, dict):
        raise DocumentError(f'{where}: "visual" must be an object with "kind" and its data')
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in VISUAL_KINDS:
        raise DocumentError(f'{where}: "visual.kind" must be one of: {", ".join(VISUAL_KINDS)}')
    data = {k: v for k, v in raw.items() if k != "kind"}
    try:
        with wui.printing():
            msg = wui.build(kind, data)
    except (ValueError, TypeError, OverflowError) as exc:
        raise DocumentError(f"{where}.visual: {exc}") from exc
    if msg.svg:
        return {"svg": msg.svg, "rendition": msg.rendition}
    if msg.png:
        return {"png": base64.b64encode(msg.png).decode("ascii"), "rendition": msg.rendition}
    raise DocumentError(f"{where}.visual: it could not be drawn")


def _slide(raw: Any, i: int) -> dict[str, Any]:
    where = f"slides[{i}]"
    if not isinstance(raw, dict):
        raise DocumentError(f"{where} must be an object")
    kind = str(raw.get("type") or "content").strip().lower()
    if kind not in SLIDE_TYPES:
        raise DocumentError(f'{where}: "type" must be one of: {", ".join(SLIDE_TYPES)}')
    slide: dict[str, Any] = {
        "type": kind,
        "title": _text(raw, "title", TITLE_MAX, where, required=kind != "quote"),
        "subtitle": _text(raw, "subtitle", SUBTITLE_MAX, where),
        "text": _text(raw, "text", TEXT_MAX, where),
        "bullets": _bullets(raw, where, MAX_BULLETS),
    }
    if kind == "quote":
        slide["quote"] = _text(raw, "quote", QUOTE_MAX, where, required=True)
        slide["by"] = _text(raw, "by", SUBTITLE_MAX, where)
    if raw.get("visual") is not None:
        if kind != "content":
            raise DocumentError(f'{where}: only a "content" slide holds a visual')
        slide["visual"] = raw["visual"]
    return slide


def _section(raw: Any, i: int) -> dict[str, Any]:
    where = f"sections[{i}]"
    if not isinstance(raw, dict):
        raise DocumentError(f"{where} must be an object")
    section: dict[str, Any] = {
        "heading": _text(raw, "heading", TITLE_MAX, where),
        "text": _text(raw, "text", TEXT_MAX * 2, where),
        "bullets": _bullets(raw, where, MAX_BULLETS * 2),
    }
    if raw.get("visual") is not None:
        section["visual"] = raw["visual"]
    if not (section["heading"] or section["text"] or section["bullets"] or "visual" in section):
        raise DocumentError(f"{where} is empty")
    return section


def prepare(data: Any, *, today: date | None = None) -> dict[str, Any]:
    """The checked spec as a layout for ``pdf_render``, or :class:`DocumentError`.

    Every visual is drawn here, once, by code. The layout holds text, the
    SVG of our own renderers and the PNG of our own chart engine.
    """
    if not isinstance(data, dict):
        raise DocumentError('"data" must be an object')
    _scan(data, "data")
    fmt = str(data.get("format") or "").strip().lower()
    if fmt not in FORMATS:
        raise DocumentError(f'"format" must be one of: {", ".join(FORMATS)}')
    title = _text(data, "title", TITLE_MAX, "data", required=True)
    layout: dict[str, Any] = {
        "format": fmt,
        "title": title,
        "subtitle": _text(data, "subtitle", SUBTITLE_MAX, "data"),
        "for": _text(data, "for", SUBTITLE_MAX, "data"),
        "date": _text(data, "date", 40, "data") or (today or date.today()).strftime("%d %b %Y"),
    }
    key, most, one = (("slides", MAX_SLIDES, _slide) if fmt == "deck"
                      else ("sections", MAX_SECTIONS, _section))
    raw_items = data.get(key)
    if not isinstance(raw_items, list) or not raw_items:
        raise DocumentError(f'a {fmt} needs "{key}": a list of at least one')
    if len(raw_items) > most:
        raise DocumentError(f"a {fmt} holds at most {most} {key}. Merge or cut some")
    items = [one(raw, i) for i, raw in enumerate(raw_items)]
    if sum(1 for it in items if "visual" in it) > MAX_VISUALS:
        raise DocumentError(f"a document holds at most {MAX_VISUALS} visuals")
    for i, item in enumerate(items):
        if "visual" in item:
            item["visual"] = _visual(item["visual"], f"{key}[{i}]")
    layout[key] = items
    return layout


def rendition(layout: dict[str, Any], filename: str) -> str:
    """What the thread keeps of a sent document: its name and its outline."""
    if layout["format"] == "deck":
        heads = [s.get("title") or s.get("quote", "")[:40] for s in layout["slides"]]
        what = f"deck, {len(heads)} slides"
    else:
        heads = [s.get("heading") for s in layout["sections"] if s.get("heading")]
        what = f"report, {len(layout['sections'])} sections"
    return f"[Document: {filename} · {what}] " + " · ".join(h for h in heads if h)


__all__ = [
    "FORMATS",
    "MAX_SECTIONS",
    "MAX_SLIDES",
    "MAX_VISUALS",
    "SLIDE_TYPES",
    "VISUAL_KINDS",
    "DocumentError",
    "filename_of",
    "make_document",
    "prepare",
    "rendition",
]


# ── The web chat: the same document, as a file in the chat (H-315) ─────────


async def make_document(spec: dict[str, Any]) -> dict[str, Any]:
    """Make a PDF FILE from Metorite's data: a sales or pitch deck, a report or
    an analysis, laid out as slides or pages with charts, tiles and tables.
    The member gets a card with a preview and a download. Collect the facts
    first (CRM, email, projects). Never invent a number.

    spec: {"format": "deck"|"report", "title", "subtitle", "for" (who it is
    for), "filename",
    deck: "slides": [{"type": "cover"|"section"|"content"|"quote"|"closing",
    "title", "subtitle", "text", "bullets" (8), "quote", "by", "visual"}]
    x 1-20;
    report: "sections": [{"heading", "text", "bullets", "visual"}] x 1-30}.
    A "visual" is {"kind": chart|stats|table|board|timeline|gantt|agenda,
    "title", ...}, at most 12, and only on a "content" slide:
    - chart: {"type": bar|line|area|donut|funnel|progress|waterfall|...,
      "labels", "values" (or "series": [{"name", "values"}]), "unit"}
    - stats: {"tiles": [{"label", "value", "delta", "good": "up"|"down"}]}
    - table: {"columns" (6), "rows": [[cell]] (20)}
    - board: {"columns": [{"name", "cards": [{"title", "meta"}]}]}
    - timeline: {"events": [{"date", "title", "state"}]}
    - gantt: {"rows": [{"label", "start", "end"}]}
    - agenda: {"items": [{"start": "09:30", "end", "title", "detail"}]}
    Text and numbers only: **bold** works, and no HTML, image or URL. One
    idea a slide: a title that says the point, 3 to 5 short bullets or one
    visual with a lead line.

    Returns {"ok": true, "path", "download_url"}: put the download_url in
    your answer as a link. Or {"ok": false, "error"}: fix the spec and call
    again.
    """
    import asyncio
    import json

    from acb_skills.whatsapp_ui import current_run

    if current_run() is not None:
        return {"ok": False, "error": 'on WhatsApp, call whatsapp_ui kind "document"'}
    try:
        layout = await asyncio.to_thread(prepare, spec)
    except (ValueError, TypeError, OverflowError) as exc:
        return {"ok": False, "error": str(exc)}
    given = spec.get("filename") if isinstance(spec, dict) else None
    name = filename_of(layout["title"], given if isinstance(given, str) else "")
    try:
        from gateway.pdf_render import render_pdf  # the ONE PDF seam
    except ImportError:  # pragma: no cover - the skill runs in the gateway
        return {"ok": False, "error": "this server cannot make a PDF"}
    try:
        pdf = await render_pdf("document", json.dumps(layout, ensure_ascii=False),
                               member=_member(), org=_org())
    except ValueError as exc:  # pdf_render.PdfRenderError says what to fix
        return {"ok": False, "error": f"the PDF could not be made: {exc}"}
    from acb_skills.write_artifact import write_artifact

    written = await write_artifact(f"outputs/{name}", pdf, encoding=None)
    if "error" in written or not written.get("download_url"):
        # A quota, no thread folder, no workspace: write_artifact refuses
        # with a dict, and never raises. No file, so no "ok" (review).
        return {"ok": False, "error": f"the PDF was made but not saved: "
                                      f"{written.get('error') or 'no file'}"}
    return {"ok": True, "path": written.get("path"),
            "download_url": written.get("download_url"),
            "note": "Link the download_url in your answer, and say in one line "
                    "what the file holds."}


def _member() -> str:
    """The run's member: the run's own ContextVar, never the spec."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id

        return _get_memory_user_id() or "unknown"
    except Exception:
        return "unknown"


def _org() -> str | None:
    """The tenant the run bound, never the spec."""
    try:
        from acb_common.db import current_tenant

        return current_tenant()
    except Exception:
        return None
