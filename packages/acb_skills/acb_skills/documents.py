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
    "announce_saved",
    "filename_of",
    "make_document",
    "prepare",
    "rendition",
    "save_outputs",
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

    Returns {"ok": true, "path"}: a card shows the file, so name it in words
    and paste no link. Or {"ok": false, "error"}: fix the spec, call again.
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
    saved = await save_outputs(name[: -len(".pdf")], (".pdf",), lambda _rels: {".pdf": pdf},
                               announce=True)
    if "error" in saved:
        # A quota, no thread folder, no workspace, a link: nothing was
        # written, so no "ok" (review of H-315).
        return {"ok": False, "error": f"the PDF was made but not saved: {saved['error']}"}
    written = saved["files"][".pdf"]
    return {"ok": True, "path": written["path"],
            "download_url": written.get("download_url"),
            "note": "A card in the chat shows the file. Name it in words, say in "
                    "one line what it holds, and paste no link."}


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


# ── The safe write of a chat document (H-236) ──────────────────────────────
#
# A sandbox container mounts the chat's outputs folder read-write, and a
# command in it can plant a link there. ``write_artifact`` resolves its path
# with plain path calls, so a write through a planted link could change a
# host file outside the tenant dir. So a document goes through the safe
# opener, under the broker's dir lock, as ``package_artifacts`` does: each
# file is a NEW file (``safe_open.publish_bytes``), a link at any depth fails
# the write, and a name that is taken, also by a link, is never replaced.

#: The most stems tried before the write gives up.
_MAX_NAMES = 1000


def _publish_all(root: Any, rels: dict[str, str], data: dict[str, bytes]) -> bool:
    """Publish every file of one stem, or none. False when a name is taken."""
    from acb_skills import safe_open

    made: list[str] = []
    for suffix, rel in rels.items():
        try:
            safe_open.publish_bytes(root, rel, data[suffix])
        except FileExistsError:
            for done in made:
                safe_open.unlink(root, done)
            return False
        made.append(rel)
    return True


async def save_outputs(
    stem: str, suffixes: tuple[str, ...],
    contents: Any, *, announce: bool,
) -> dict[str, Any]:
    """Write the files ``<stem><suffix>`` of one document into this chat's
    outputs, with ONE free stem for all of them.

    *contents* takes ``{suffix: path}`` and returns ``{suffix: bytes}``, so a
    file can name the path of its sibling. A taken stem becomes
    ``<stem> (1)``, and every file of the set takes the same one. With
    *announce* off, each file registers the workspace and gets no card.

    Returns ``{"files": {suffix: {"path", "size", "sha256", "mime_type",
    "download_url"}}, "dest": ...}``, or ``{"error"}``. The working dir, the
    thread and the member come from the run's artifact context, never from
    the model (R5). Fence: ``tests/unit/test_chat_reports.py``.
    """
    import asyncio
    import hashlib
    import importlib
    import mimetypes
    from pathlib import Path

    from acb_skills import safe_open
    from acb_skills.agent_paths import refused_write

    wa = importlib.import_module("acb_skills.write_artifact")
    ctx = wa.artifact_context()
    if not ctx.get("workspace_root"):
        return {"error": "No workspace is configured for this run, so nothing was written."}
    # A delegated run delivers to the chat that called it (``_destination``).
    dest, delegated = wa._destination(ctx)
    batch = not delegated and wa._is_batch_run(ctx)
    root = Path(str(dest["workspace_root"])).resolve()
    folder = "outputs"
    if not batch:
        probe = f"outputs/{stem}{suffixes[0]}"
        scoped = wa._chat_document_path(probe, probe, dest)
        if isinstance(scoped, dict):
            return scoped
        folder = scoped.rsplit("/", 1)[0]
    session_id = str(dest.get("session_id") or "")
    try:
        async with wa._chat_dir_guard(root) as over_quota:
            if over_quota:
                return {"error": "The working dir of the chat is over its quota, "
                                 "so nothing was written."}
            for n in range(_MAX_NAMES):
                name = stem if n == 0 else f"{stem} ({n})"
                rels = {sfx: f"{folder}/{name}{sfx}" for sfx in suffixes}
                for rel in rels.values():
                    reason = refused_write(root, rel, member=ctx.get("member"),
                                           thread_id=dest.get("session_id"))
                    if reason:
                        return {"error": f"The file was refused: {reason}."}
                # A batch name is free only when the history holds no write
                # there either (H-227, ``write_artifact._take_batch_name``).
                if batch and any([await wa._path_has_history(r, ctx) for r in rels.values()]):
                    continue
                data = contents(rels)
                if await asyncio.to_thread(_publish_all, root, rels, data):
                    break
            else:
                return {"error": "No free name for the file, so nothing was written."}
    except safe_open.UnsafePath:
        return {"error": "The file was refused: its folder goes through a link."}
    except OSError as exc:
        return {"error": f"The file could not be written: {exc.strerror or exc}."}
    files: dict[str, dict[str, Any]] = {}
    for sfx, rel in rels.items():
        body = data[sfx]
        mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        files[sfx] = {
            "path": rel, "size": len(body), "sha256": hashlib.sha256(body).hexdigest(),
            "mime_type": mime,
            "download_url": f"/api/agent/workspace/{session_id}/file?path={rel}"
                            if session_id else None,
        }
        mirror = asyncio.ensure_future(wa.mirror_to_blob_store(
            rel, body, mime_type=mime, action="create",
            actor=wa.BATCH_ACTOR if batch else "agent",
            target=dest if delegated else None,
        ))
        _announce(ctx, dest, files[sfx], mirror_task=mirror, announce=announce)
    return {"files": files, "dest": dest}


def _announce(ctx: Any, dest: Any, file: dict[str, Any], *, mirror_task: Any = None,
              announce: bool = True) -> None:
    """``write_artifact._notify`` for one saved file, in the chat that the
    file went to. With *announce* off, it only registers the workspace."""
    import asyncio
    import importlib
    from datetime import UTC, datetime

    wa = importlib.import_module("acb_skills.write_artifact")
    if not dest.get("session_id"):
        return
    asyncio.ensure_future(wa._notify(
        session_id=str(dest["session_id"]),
        workspace_root=str(dest["workspace_root"]),
        artifact={"path": file["path"], "name": file["path"].rsplit("/", 1)[-1],
                  "size": file["size"], "sha256": file["sha256"],
                  "mime_type": file["mime_type"],
                  "modified_at": datetime.now(tz=UTC).isoformat(), "is_dir": False},
        gateway_url=ctx.get("gateway_url", "http://127.0.0.1:8000"),
        gateway_token=ctx.get("gateway_token", "sk-local-dev-change-me"),
        mirror_task=mirror_task,
        announce=announce,
    ))


def announce_saved(saved: dict[str, Any], suffix: str) -> None:
    """Show one file of :func:`save_outputs` as a file card, after the fact.

    ``make_report`` calls it for the PDF when the chat could not draw the
    report card, so the member still sees the file.
    """
    import importlib

    wa = importlib.import_module("acb_skills.write_artifact")
    _announce(wa.artifact_context(), saved["dest"], saved["files"][suffix])
