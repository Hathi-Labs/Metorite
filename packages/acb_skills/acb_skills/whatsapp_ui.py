"""The WhatsApp run profile and its native UI tool (WS-47 WAC-10a).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §12.

**The profile.** ``bot_run`` opens :func:`whatsapp_run` around the run. While
it is open, the tool injection seam (``orchestrator._tool_injection``) does
three things for every agent of the run, nested runs too:

* It takes :data:`WITHHELD_TOOLS` away: the tools that DELIVER an answer to
  the web chat. A WhatsApp member can see none of it, so each one only cost
  context. Every tool that does something stays.
* It leaves out the web prompt blocks (the generative UI directive and the
  output discipline block).
* It gives :func:`whatsapp_ui` to the run's OWN agent, and to no agent that
  agent calls. A specialist answers in text, and the main agent decides what
  the member sees.

**The tool queues, and ``bot_run`` sends.** :func:`whatsapp_ui` checks the
element, renders an image if it needs one, and adds it to the run's outbox.
After the run, ``bot_run`` sends the text reply first, then each queued
element, under the same send mark. The tool never reaches Meta itself.

**A tap is the member's next message.** A button or a list row comes back as
an ``interactive`` message, and ``inbound`` turns its title into the member's
text turn. So a tap is untrusted input exactly like typed text, and no id from
the phone ever selects an org or a record (§5.11 "The tap is untrusted input").

**A reaction is not an element (WAC-10e).** Kind "react" queues one emoji
for the member's own message. ``bot_run`` sends it first and best effort, so
a refused reaction never costs the member the answer.

**Reads only, as in WAC-3.** Buttons and rows ask or narrow a question. A
write still goes to the web app until WAC-4.

Fence: ``tests/unit/test_wac_native_ui.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from acb_skills import whatsapp_cards as cards
from acb_skills import whatsapp_engine as engine
from acb_skills import whatsapp_render as render

#: The tools a WhatsApp run does not get: the WEB DELIVERY tools only.
#:
#: ⚠️ The rule (owner, 2026-10-10): a tool leaves a WhatsApp run only when its
#: job is HOW an answer reaches the web chat (a card, the side panel, a file
#: card, an upload, the web design kits). A tool that DOES something (search,
#: diagnose, build, configure, delegate) stays, whatever its use on a phone,
#: because a WhatsApp member may need it. The channel changes delivery, never
#: what the assistant can do. ``whatsapp_ui`` is the WhatsApp delivery tool.
#: The D85 shell block and the other gates of the seam still apply on top.
WITHHELD_TOOLS: frozenset[str] = frozenset({
    # web cards, and the web's own card for a question
    "emit_generative_ui", "ask_questions",
    # the "Todos (n/m)" panel above the web chat input
    "manage_todo_list",
    # the web design language for cards and reports
    "load_design_system", "load_artifact_kit",
    # a file shows as a web download card, and an upload is a web upload
    "write_artifact", "share_artifact", "read_attachment",
    # a background run posts its answer into the web chat thread
    "call_agent_background",
})

#: The most elements one reply sends. More reads as spam on a phone.
MAX_MESSAGES = 3
#: WhatsApp's own limits (Cloud API "interactive" object).
BODY_MAX = 1024
BUTTON_TITLE_MAX = 20
MAX_BUTTONS = 3
LIST_BUTTON_MAX = 20
ROW_TITLE_MAX = 24
ROW_DESCRIPTION_MAX = 72
SECTION_TITLE_MAX = 24
MAX_ROWS = 10
FOOTER_MAX = 60
CAPTION_MAX = 1024
#: A link button opens the Metorite app and nothing else, so a model that was
#: talked into it cannot send the member to a phishing page. The host must be
#: exactly this, with no port and no user part.
LINK_HOST = "app.metorite.com"
#: A browser (WHATWG) and Python's ``urlsplit`` read a backslash or an ``@``
#: differently: ``https://evil.com\@app.metorite.com`` is evil.com to a phone.
#: So a URL that holds one, a space or a control character is refused.
_URL_REFUSED = re.compile(r"[\\@\s\x00-\x1f\x7f]")
URL_MAX = 2000
#: The most characters of one element's rendition that the thread keeps.
RENDITION_MAX = 2000
#: Between a reply's text and the renditions of its elements in the thread
#: (U+2063, an invisible separator). A resend cuts the record here, so the
#: member never gets the bracketed renditions as text (:func:`resend_text`).
RENDITION_MARK = "⁣"
#: At the start of the thread record of a reply that code built (a view, no
#: AI call): U+2064, invisible. ``bot_run`` reads it (WAC-10c).
VIEW_MARK = "⁤"


@dataclass(frozen=True)
class OutMessage:
    """One element that waits for ``bot_run`` to send it.

    ``rendition`` is plain text of what the member sees. The thread keeps it,
    so the next turn knows which buttons it offered.
    """

    kind: str  # "interactive", "image", "text" (a view's own text) or "reaction"
    rendition: str
    interactive: dict[str, Any] | None = None
    png: bytes | None = None
    caption: str | None = None
    #: The emoji of a "reaction". It goes ON the member's message (WAC-10e).
    emoji: str | None = None

    @property
    def counts(self) -> bool:
        """True for an element that counts toward :data:`MAX_MESSAGES`. A
        view's text and a reaction are not elements."""
        return self.kind not in ("text", "reaction")


@dataclass
class WhatsAppRun:
    """The open WhatsApp run: its own agent and its outbox."""

    agent: str
    outbox: list[OutMessage] = field(default_factory=list)
    #: The view runner of ``whatsapp_channel.views`` for this member, or None.
    #: It takes a view name and returns an object with ``text`` and ``ui``.
    views: Callable[[str], Awaitable[Any]] | None = None
    #: The org of the phone's link row. Each link button carries it.
    org: str | None = None
    #: Sends one text to the member NOW, before the answer (WAC-10e, kind
    #: "working"). ``bot_run`` gives it. It returns False when nothing went.
    notify: Callable[[str], Awaitable[bool]] | None = None
    #: True once the member heard that the job takes a while.
    notified: bool = False


_RUN: ContextVar[WhatsAppRun | None] = ContextVar("whatsapp_run", default=None)


@contextlib.contextmanager
def whatsapp_run(agent: str, *,
                 views: Callable[[str], Awaitable[Any]] | None = None,
                 org: str | None = None,
                 notify: Callable[[str], Awaitable[bool]] | None = None,
                 ) -> Iterator[WhatsAppRun]:
    """Open the WhatsApp profile for the run of *agent* in this context.

    Like ``refuse_cards``: a nested ``run_agent`` and each task the run starts
    copy the context, so they see the profile too. Closing it restores the
    value it found.
    """
    run = WhatsAppRun(agent=agent, views=views, org=org, notify=notify)
    token = _RUN.set(run)
    try:
        yield run
    finally:
        _RUN.reset(token)


def current_run() -> WhatsAppRun | None:
    """The open WhatsApp run, or None outside one."""
    return _RUN.get()


def gets_ui_tool(agent_name: str | None) -> bool:
    """True for the run's own agent only (see the module docstring)."""
    run = _RUN.get()
    return run is not None and bool(agent_name) and agent_name == run.agent


# ── Checks ──────────────────────────────────────────────────────────────────


class _Refused(ValueError):
    """The element breaks a rule. The message tells the model what to fix."""


def _text(data: dict[str, Any], key: str, *, limit: int, required: bool = True) -> str:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise _Refused(f'"{key}" is required')
        return ""
    if not isinstance(value, str):
        raise _Refused(f'"{key}" must be text')
    value = value.strip()
    if len(value) > limit:
        raise _Refused(f'"{key}" is {len(value)} characters. The limit is {limit}')
    return value


def _short(value: Any, limit: int, what: str) -> str:
    """A title cut to *limit* with an ellipsis. Titles get cut, not refused:
    a model that writes 21 characters should not spend a turn on it."""
    if not isinstance(value, str) or not value.strip():
        raise _Refused(f"each {what} needs a text title")
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _footer(data: dict[str, Any], interactive: dict[str, Any]) -> None:
    footer = _text(data, "footer", limit=FOOTER_MAX, required=False)
    if footer:
        interactive["footer"] = {"text": footer}


def _buttons(data: dict[str, Any]) -> OutMessage:
    body = _text(data, "body", limit=BODY_MAX)
    raw = data.get("buttons")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_BUTTONS:
        raise _Refused(f'"buttons" must be a list of 1 to {MAX_BUTTONS} titles')
    titles = [_short(t, BUTTON_TITLE_MAX, "button") for t in raw]
    if len({t.casefold() for t in titles}) != len(titles):
        raise _Refused("two buttons have the same title")
    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": body},
        "action": {"buttons": [
            {"type": "reply", "reply": {"id": f"b{i + 1}", "title": t}}
            for i, t in enumerate(titles)
        ]},
    }
    _footer(data, interactive)
    return OutMessage("interactive", f"{body}\n[Buttons: {' | '.join(titles)}]",
                      interactive=interactive)


def _unique(title: str, seen: set[str]) -> str:
    """*title*, or *title* with " (2)", " (3)" … cut to fit, when a row of
    the list has it already. Two tasks of one name, or two notifications
    that cut to the same 24 characters, are common in real data (WAC-10c
    review), and a refused list would lose the whole view."""
    if title.casefold() not in seen:
        return title
    n = 2
    while True:
        tag = f" ({n})"
        cand = title[: ROW_TITLE_MAX - len(tag)].rstrip() + tag
        if cand.casefold() not in seen:
            return cand
        n += 1


def _list(data: dict[str, Any]) -> OutMessage:
    body = _text(data, "body", limit=BODY_MAX)
    button = _short(data.get("button") or "View options", LIST_BUTTON_MAX, "list button")
    if "sections" in data:
        raw_sections = data["sections"]
        if not isinstance(raw_sections, list) or not raw_sections:
            raise _Refused('"sections" must be a list of {"title", "rows"}')
    else:
        raw_sections = [{"title": "", "rows": data.get("rows")}]
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines: list[str] = []
    count = 0
    for sec in raw_sections:
        if not isinstance(sec, dict):
            raise _Refused('each section must be {"title", "rows"}')
        rows = sec.get("rows")
        if not isinstance(rows, list) or not rows:
            raise _Refused('each section needs a list of "rows"')
        out_rows = []
        for row in rows:
            if isinstance(row, str):
                row = {"title": row}
            if not isinstance(row, dict):
                raise _Refused('each row must be {"title", "description"}')
            count += 1
            title = _unique(_short(row.get("title"), ROW_TITLE_MAX, "row"), seen)
            seen.add(title.casefold())
            item: dict[str, Any] = {"id": f"r{count}", "title": title}
            desc = row.get("description")
            if isinstance(desc, str) and desc.strip():
                item["description"] = _short(desc, ROW_DESCRIPTION_MAX, "row description")
            out_rows.append(item)
            lines.append(f"- {title}" + (f" ({item['description']})"
                                         if "description" in item else ""))
        section: dict[str, Any] = {"rows": out_rows}
        title = sec.get("title")
        if isinstance(title, str) and title.strip():
            section["title"] = _short(title, SECTION_TITLE_MAX, "section")
        sections.append(section)
    if count > MAX_ROWS:
        raise _Refused(f"a list holds at most {MAX_ROWS} rows. Send the first "
                       f"{MAX_ROWS} and say how many more there are")
    if len(sections) > 1 and any("title" not in s for s in sections):
        raise _Refused("with two or more sections, each section needs a title")
    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": body},
        "action": {"button": button, "sections": sections},
    }
    _footer(data, interactive)
    return OutMessage("interactive", f"{body}\n[List \"{button}\":\n" + "\n".join(lines) + "]",
                      interactive=interactive)


def _link(data: dict[str, Any]) -> OutMessage:
    body = _text(data, "body", limit=BODY_MAX)
    label = _short(data.get("label") or "Open in Metorite", BUTTON_TITLE_MAX, "link")
    raw = data.get("url")
    if not isinstance(raw, str) or not raw.strip():
        raise _Refused('"url" is required')
    run = _RUN.get()
    url = safe_link(raw, org=run.org if run is not None else None)
    if url is None:
        raise _Refused(f"a link must open a page of https://{LINK_HOST}. "
                       "Other sites are refused")
    interactive = {
        "type": "cta_url",
        "body": {"text": body},
        "action": {"name": "cta_url",
                   "parameters": {"display_text": label, "url": url}},
    }
    _footer(data, interactive)
    return OutMessage("interactive", f"{body}\n[Link: {label} -> {url}]",
                      interactive=interactive)


def safe_link(raw: str, *, org: str | None = None) -> str | None:
    """*raw* rebuilt from its checked parts, or None when it is not a page of
    ``https://app.metorite.com``. The URL that goes out is the rebuilt one,
    so a part that Python and a browser read differently cannot pass.

    *org* is the run's org, from the phone's link row. It goes on the link as
    ``org=<id>``, and replaces any ``org`` the model wrote, so the web app can
    open the link in the member's account for that org (WAC-10d, owner
    2026-10-10). The model never chooses it.
    """
    url = raw.strip()
    if len(url) > URL_MAX or _URL_REFUSED.search(url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme != "https" or parts.netloc.lower() != LINK_HOST
            or port is not None or parts.username or parts.password):
        return None
    query = parts.query
    if org:
        pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "org"]
        query = urlencode([*pairs, ("org", org)])
    return urlunsplit(("https", LINK_HOST, parts.path or "/", query, parts.fragment))


def _list_of(data: dict[str, Any], key: str, *, required: bool = True) -> list[Any] | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, list):
        raise _Refused(f'"{key}" must be a list')
    return value


#: The chart kinds the old SVG renderer draws: the fallback when the engine is
#: off or cannot draw (WAC-10f).
_OLD_KINDS = ("bar", "line", "progress", "donut")


def _join(values: Any) -> str:
    return ", ".join(map(str, values or []))


def _chart_rendition(kind: str, title: str, data: dict[str, Any]) -> str:
    """The thread's text of a chart: its kind, its title and its numbers.

    It never raises: an odd shape gives the head alone. The thread keeps it,
    so the next turn knows what the image showed (``thread_record``).
    """
    head = f"[Chart, {kind}: {title}]"
    try:
        series = data.get("series")
        if isinstance(series, list) and kind in ("bar", "line", "area", "radar"):
            axis = data.get("axes") if kind == "radar" else data.get("labels")
            return f"{head} {_join(axis)}\n" + "\n".join(
                f"{s.get('name')}: {_join(s.get('values'))}" for s in series if isinstance(s, dict))
        if kind in ("bar", "line", "funnel", "donut", "progress"):
            return head + " " + ", ".join(
                f"{lab}: {val}" for lab, val in zip(data.get("labels") or [], data.get("values") or [],
                                                    strict=False))
        if kind == "waterfall":
            return head + " " + ", ".join(
                f"{s.get('label')}: {'total' if s.get('total') else s.get('value')}"
                for s in data.get("steps") or [] if isinstance(s, dict))
        if kind == "heatmap":
            return f"{head} {_join(data.get('x'))}\n" + "\n".join(
                f"{y}: {_join(r)}" for y, r in zip(data.get("y") or [], data.get("values") or [],
                                                   strict=False))
        if kind in ("scatter", "box"):
            key = "points" if kind == "scatter" else "values"
            return head + " " + "; ".join(
                f"{g.get('name')}: {len(g.get(key) or [])} {key}"
                for g in data.get("groups") or [] if isinstance(g, dict))
        if kind == "calendar":
            days = data.get("days") or []
            return f"{head} {len(days)} days, total {sum(float(d[1]) for d in days):g}"
    except (TypeError, ValueError, AttributeError, IndexError):
        pass
    return head


def _engine_png(spec: dict[str, Any]) -> bytes | None:
    """The chart from the chart engine (WAC-10f), or None to fall back.

    A bad spec raises ``RenderError`` with the engine's reason, so the model
    can fix the data. An engine that cannot draw (off, missing, broken) falls
    back to the old renderer, and never costs the member the chart.
    """
    if not engine.enabled():
        return None
    try:
        return engine.render_png(_with_hues(spec))
    except engine.EngineUnavailable:
        return None


def _with_hues(spec: dict[str, Any]) -> dict[str, Any]:
    """The spec with each status word as one of the six hue names.

    The engine knows the hue NAMES only. The rule from a word ("On hold",
    "Shipped") to a hue is ``statusAccent.ts``'s, which ``cards.hue``
    mirrors under its own fence, so a chart and a card never disagree
    (review, 2026-10-11).
    """
    out = dict(spec)
    if isinstance(spec.get("tones"), list):
        out["tones"] = [cards.hue(t) for t in spec["tones"]]
    if isinstance(spec.get("rows"), list):
        out["rows"] = [{**r, "status": cards.hue(r["status"])}
                       if isinstance(r, dict) and r.get("status") is not None else r
                       for r in spec["rows"]]
    return out


def _chart(data: dict[str, Any]) -> OutMessage:
    kind = str(data.get("type") or "bar").strip().lower()
    title = _text(data, "title", limit=80)
    caption = _text(data, "caption", limit=CAPTION_MAX, required=False)
    png = _engine_png({**data, "type": kind})
    if png is None and kind not in _OLD_KINDS:
        raise _Refused(f'chart "type" must be one of: {", ".join(_OLD_KINDS)}')
    if png is None:
        png = render.to_png(_old_chart_svg(kind, title, data))
    rendition = _chart_rendition(kind, title, data) + (f"\n{caption}" if caption else "")
    return OutMessage("image", rendition, png=png, caption=caption or None)


def _old_chart_svg(kind: str, title: str, data: dict[str, Any]) -> str:
    """The old SVG renderer's chart: bar, line, progress or donut."""
    subtitle = _text(data, "subtitle", limit=120, required=False) or None
    labels = [str(x) for x in (_list_of(data, "labels") or [])]
    values = _list_of(data, "values") or []
    unit = data.get("unit") if isinstance(data.get("unit"), str) else ""
    if kind == "bar":
        svg = render.bar_svg(title, labels, values, subtitle=subtitle, unit=unit[:8])
    elif kind == "line":
        svg = render.line_svg(title, labels, values, subtitle=subtitle, unit=unit[:8])
    elif kind == "progress":
        svg = render.progress_svg(title, labels, values, subtitle=subtitle,
                                  totals=_list_of(data, "totals", required=False))
    else:
        svg = cards.donut_svg(title, labels, values, subtitle=subtitle,
                              tones=_list_of(data, "tones", required=False))
    return svg


def _table(data: dict[str, Any]) -> OutMessage:
    title = _text(data, "title", limit=80)
    subtitle = _text(data, "subtitle", limit=120, required=False) or None
    caption = _text(data, "caption", limit=CAPTION_MAX, required=False)
    columns = [str(c) for c in (_list_of(data, "columns") or [])]
    rows = _list_of(data, "rows") or []
    svg = render.table_svg(title, columns, rows, subtitle=subtitle)
    lines = [" | ".join(columns)] + [
        " | ".join("" if c is None else str(c) for c in r) for r in rows
    ]
    rendition = f"[Table: {title}]\n" + "\n".join(lines) + (f"\n{caption}" if caption else "")
    return OutMessage("image", rendition, png=render.to_png(svg), caption=caption or None)


def _image(data: dict[str, Any], svg: str, rendition: str) -> OutMessage:
    caption = _text(data, "caption", limit=CAPTION_MAX, required=False)
    return OutMessage("image", rendition + (f"\n{caption}" if caption else ""),
                      png=render.to_png(svg), caption=caption or None)


def _head(data: dict[str, Any]) -> tuple[str, str | None]:
    return (_text(data, "title", limit=80),
            _text(data, "subtitle", limit=120, required=False) or None)


def _stats(data: dict[str, Any]) -> OutMessage:
    title, subtitle = _head(data)
    tiles = _list_of(data, "tiles") or []
    svg = cards.stats_svg(title, tiles, subtitle=subtitle)
    pairs = "; ".join(f"{t.get('label')}: {t.get('value')}"
                      + (f" ({t.get('delta')})" if t.get("delta") else "") for t in tiles)
    return _image(data, svg, f"[Stats: {title}] {pairs}")


def _board(data: dict[str, Any]) -> OutMessage:
    title, subtitle = _head(data)
    columns = _list_of(data, "columns") or []
    svg = cards.board_svg(title, columns, subtitle=subtitle)
    lines = [f"{c.get('name')} ({c.get('total', len(c.get('cards') or []))}): "
             + ", ".join(str(k.get("title")) for k in (c.get("cards") or [])[:cards.MAX_CARDS])
             for c in columns]
    return _image(data, svg, f"[Board: {title}]\n" + "\n".join(lines))


def _timeline(data: dict[str, Any]) -> OutMessage:
    title, subtitle = _head(data)
    events = _list_of(data, "events") or []
    svg = cards.timeline_svg(title, events, subtitle=subtitle)
    lines = [f"{e.get('date', '')} {e.get('title')} ({e.get('state', 'upcoming')})"
             for e in events]
    return _image(data, svg, f"[Timeline: {title}]\n" + "\n".join(lines))


def _agenda(data: dict[str, Any]) -> OutMessage:
    title, subtitle = _head(data)
    items = _list_of(data, "items") or []
    all_day = _list_of(data, "all_day", required=False)
    svg = cards.agenda_svg(title, items, subtitle=subtitle, all_day=all_day)
    lines = [f"{i.get('start')}-{i.get('end', '')} {i.get('title')}" for i in items]
    head = f"[Agenda: {title}]" + (f" All day: {', '.join(map(str, all_day))}" if all_day else "")
    return _image(data, svg, head + "\n" + "\n".join(lines))


def _gantt(data: dict[str, Any]) -> OutMessage:
    title, subtitle = _head(data)
    rows = _list_of(data, "rows") or []
    today = data.get("today") if isinstance(data.get("today"), str) else None
    lines = [f"{r.get('label')}: {r.get('start')} to {r.get('end', r.get('start'))}"
             + (f", {r.get('progress')}%" if r.get("progress") is not None else "")
             for r in rows]
    rendition = f"[Schedule: {title}]\n" + "\n".join(lines)
    png = _engine_png({**data, "type": "gantt"})  # the chart language (WAC-10f)
    if png is not None:
        caption = _text(data, "caption", limit=CAPTION_MAX, required=False)
        return OutMessage("image", rendition + (f"\n{caption}" if caption else ""),
                          png=png, caption=caption or None)
    svg = cards.gantt_svg(title, rows, subtitle=subtitle, today=today)
    return _image(data, svg, rendition)


#: A family or a flag sequence is longer than one code point, and no emoji
#: is longer than this.
EMOJI_MAX = 10
_ZWJ = "‍"
_KEYCAP = "⃣"
_SELECTORS = frozenset({0xFE0E, 0xFE0F})
_SKIN = range(0x1F3FB, 0x1F400)
_REGIONAL = range(0x1F1E6, 0x1F200)
#: The tag letters and the cancel tag of a subdivision flag (England).
_TAGS = range(0xE0020, 0xE0080)
#: The emoji outside the main emoji blocks (U+1F000 to U+1FAFF). A curated
#: list, so a degree sign, a Braille blank or a box line is never an emoji.
_BMP_EMOJI = frozenset(
    "©®‼⁉™ℹ↔↕↖↗↘↙↩↪⌚⌛⌨⏏⏩⏪⏫⏬⏭⏮⏯⏰⏱⏲⏳⏸⏹⏺Ⓜ▪▫▶◀◻◼◽◾☀☁☂☃☄☎☑☔☕☘☝☠☢☣☦☪☮"
    "☯☸☹☺♀♂♟♠♣♥♦♨♻♾♿⚒⚓⚔⚕⚖⚗⚙⚛⚜⚠⚡⚧⚪⚫⚰⚱⚽⚾⛄⛅⛈⛎⛏⛑⛓⛔⛩⛪⛰⛱⛲⛳⛴⛵⛷⛸⛹⛺⛽✂"
    "✅✈✉✊✋✌✍✏✒✔✖✝✡✨✳✴❄❇❌❎❓❔❕❗❣❤➕➖➗➡➰➿⤴⤵⬅⬆⬇⬛⬜⭐⭕〰〽㊗㊙")


def _emoji_part(part: str) -> bool:
    """One emoji between two zero width joiners: a flag, a keycap, or a base
    with its selectors, a skin tone and tag letters."""
    first = ord(part[0])
    if first in _REGIONAL:
        return len(part) == 2 and ord(part[1]) in _REGIONAL
    if part[0] in "0123456789#*":
        return part[1:] in (_KEYCAP, "️" + _KEYCAP)
    if not (0x1F000 <= first <= 0x1FAFF and first not in _SKIN) \
            and part[0] not in _BMP_EMOJI:
        return False
    return all(ord(c) in _SELECTORS or ord(c) in _SKIN or ord(c) in _TAGS
               for c in part[1:])


def _emoji(raw: Any) -> str:
    """One emoji, or :class:`ValueError`. Meta takes any emoji, and refuses
    text, so text, a run of emoji or a hidden mark never reaches it."""
    value = str(raw or "").strip()
    parts = value.split(_ZWJ)
    if not value or len(value) > EMOJI_MAX or not all(p and _emoji_part(p) for p in parts):
        raise _Refused('"emoji" must be one emoji, such as 👍')
    return value


def reaction(emoji: str) -> OutMessage:
    """A reaction on the member's message (WAC-10e). Not an element: it never
    counts toward :data:`MAX_MESSAGES`, and a later one replaces it."""
    value = _emoji(emoji)
    return OutMessage("reaction", f"[Reaction: {value}]", emoji=value)


def _react(data: dict[str, Any]) -> OutMessage:
    return reaction(data.get("emoji"))


_BUILDERS = {
    "react": _react,
    "buttons": _buttons,
    "list": _list,
    "link": _link,
    "chart": _chart,
    "table": _table,
    "stats": _stats,
    "board": _board,
    "timeline": _timeline,
    "agenda": _agenda,
    "gantt": _gantt,
}


def build(kind: str, data: dict[str, Any]) -> OutMessage:
    """The checked element, or :class:`ValueError` with what to fix."""
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise _Refused(f'"kind" must be one of: {", ".join(_BUILDERS)}')
    if not isinstance(data, dict):
        raise _Refused('"data" must be an object')
    msg = builder(data)
    if len(msg.rendition) > RENDITION_MAX:
        msg = OutMessage(msg.kind, msg.rendition[: RENDITION_MAX - 1] + "…",
                         interactive=msg.interactive, png=msg.png, caption=msg.caption)
    return msg


# ── The tool ────────────────────────────────────────────────────────────────


async def whatsapp_ui(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Send a native WhatsApp element after your text reply.

    Pick by the SHAPE of the answer:
    - items the member may open (tasks, emails, people, approvals) -> "list"
    - a short choice or the next question -> "buttons"
    - 2 to 8 headline numbers -> "stats"; a breakdown -> "chart" donut
    - numbers per item -> "chart" bar; a trend -> line; done/total -> progress
    - a mix over time -> area; stages -> funnel; a running total -> waterfall;
      spread -> box; x against y -> scatter; when -> heatmap or calendar;
      many axes -> radar
    - work by status -> "board"; dates and milestones -> "timeline"
    - one day of the calendar -> "agenda"; a project schedule -> "gantt"
    - rows with 3+ columns -> "table"; anything else rich -> "link"

    data per kind (every image takes "title", "subtitle", "caption"):
    - buttons: {"body", "buttons": [str x 1-3, 20 chars]}
    - list: {"body", "button": "View tasks", "rows": [{"title" (24),
      "description" (72)}] x 1-10} or "sections": [{"title", "rows"}]
    - link: {"body", "label", "url": "https://app.metorite.com/<page>"}
    - chart: {"type", ...}. bar, line, funnel, donut: "labels", "values"
      (bar and line: or "series": [{"name", "values"}]), "unit", "tones".
      area: "labels", "series". progress: "labels", "values" (done),
      "totals". scatter: "groups": [{"name", "points": [[x, y]]}].
      heatmap: "x", "y", "values" (a row of numbers per y). radar: "axes",
      "series", "max". box: "groups": [{"name", "values"}]. waterfall:
      "steps": [{"label", "value"} or {"label", "total": true}]. calendar:
      "days": [["2026-10-01", 3]]
    - table: {"columns" (6), "rows": [[cell]] (20)}
    - stats: {"tiles": [{"label", "value", "delta", "good": "up"|"down",
      "hint", "tone"}] x 1-8}
    - board: {"columns": [{"name", "total", "cards": [{"title", "meta",
      "tone"}]}] x 1-4}
    - timeline: {"events": [{"date", "title", "detail",
      "state": "done"|"current"|"upcoming"|"late"|"blocked"}] x 1-12}
    - agenda: {"items": [{"start": "09:30", "end", "title", "detail",
      "kind": "event"|"focus"|"task"|"free"}], "all_day": [str]}
    - gantt: {"rows": [{"label", "start", "end" (ISO dates), "status",
      "progress"}] x 1-15, "today"}
    - view: {"name"}: a ready view that the server reads and builds, with no
      rows from you. Names: my_day, due_today, overdue, calendar, approvals,
      menu. Prefer it whenever the question is one of these.
    - react: {"emoji": "👍"}: one emoji ON the member's message, as a person
      reacts. Not an element, and a later one replaces it. Call it in the
      same step as your first other tool call.
    - working: {"text"}: sent NOW, before your answer, once. Use it FIRST
      when the job takes more than about 15 seconds (an image, a
      calculation, code, a page, a long search): one line of what you are
      doing and about how long it takes, e.g. "Building the chart, about 30
      seconds." Not an element.
    A "tone" or "status" is a status word or a colour (green, amber, red,
    blue, violet, gray).

    A tap on a button or a row comes back as the member's next message. They
    change no data. Do not repeat in your text what the element shows. At
    most 3 elements in one reply. Returns {"ok": true}, or {"ok": false,
    "error"}: fix it and call again.
    """
    run = _RUN.get()
    if run is None:
        return {"ok": False, "error": "whatsapp_ui works only in a WhatsApp chat"}
    if str(kind or "").strip().lower() == "working":
        return await _working(run, data)
    if str(kind or "").strip().lower() == "react":
        try:
            message = reaction(data.get("emoji") if isinstance(data, dict) else None)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        run.outbox[:] = [m for m in run.outbox if m.kind != "reaction"]
        run.outbox.append(message)
        return {"ok": True, "note": "The reaction goes on the member's message. "
                                    "A message that needs no answer needs no text."}
    if _elements(run) >= MAX_MESSAGES:
        return {"ok": False,
                "error": f"this reply already has {MAX_MESSAGES} elements. Put the rest in text"}
    if str(kind or "").strip().lower() == "view":
        return await _send_view(run, data)
    try:
        # In a worker thread: drawing an image must never hold the gateway's
        # event loop, which serves every tenant (`whatsapp_render` locks).
        message = await asyncio.to_thread(build, str(kind or "").strip().lower(), data)
    except (ValueError, TypeError, OverflowError) as exc:  # RenderError is a ValueError
        return {"ok": False, "error": str(exc)}
    run.outbox.append(message)
    return {"ok": True, "queued": len(run.outbox),
            "note": "It goes after your text. Refer to it in one line."}


#: The longest "working" line. One line, read at a glance.
WORKING_MAX = 300


async def _working(run: WhatsAppRun, data: Any) -> dict[str, Any]:
    """Tell the member NOW that the job takes a while (WAC-10e).

    Sent at once through ``run.notify``, not queued, and only once a reply.
    It is not part of the answer, so the answer still goes in full after it.
    """
    text = " ".join(str((data or {}).get("text") or "").split()) \
        if isinstance(data, dict) else ""
    if not text:
        return {"ok": False, "error": '"text" is required: what you are doing, and how long'}
    if len(text) > WORKING_MAX:
        return {"ok": False, "error": f'"text" must be at most {WORKING_MAX} characters'}
    if run.notified:
        return {"ok": False, "error": "the member already knows. Carry on with the job"}
    if run.notify is None:
        return {"ok": False, "error": "no early message in this chat. Carry on"}
    run.notified = True  # before the await, so two calls in one step send once
    try:
        sent = await run.notify(text)
    except Exception:  # a nicety: never cost the member the answer
        sent = False
    run.notified = sent  # a failed one leaves the timer's own line free to go
    return ({"ok": True, "note": "Sent. Now do the job, then answer in full."} if sent
            else {"ok": False, "error": "the early message did not go. Carry on"})


def _elements(run: WhatsAppRun) -> int:
    """The elements queued so far. A view's text and a reaction are not."""
    return sum(1 for m in run.outbox if m.counts)


async def _send_view(run: WhatsAppRun, data: Any) -> dict[str, Any]:
    """Queue a prebuilt view: its text, then its elements (WAC-10c).

    The server reads the data and builds the elements, so the model writes
    no rows. It counts as the reply's elements, up to :data:`MAX_MESSAGES`.
    """
    name = data.get("name") if isinstance(data, dict) else None
    if run.views is None:
        return {"ok": False, "error": "no views in this chat"}
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "error": '"name" is required'}
    name = name.strip().lower()
    known = getattr(run.views, "names", None)
    if known is not None and name not in known:
        return {"ok": False, "error": f'no view named "{name}". Views: {", ".join(known)}'}
    try:
        view = await run.views(name)
    except PermissionError:
        return {"ok": False, "error": "this member cannot open that app"}
    except Exception as exc:  # a read or a render failed: say so, never raise
        return {"ok": False, "error": f"the view could not be built ({type(exc).__name__}). "
                                      "Answer from your own tools"}
    room = MAX_MESSAGES - _elements(run)
    if view.text:
        run.outbox.append(OutMessage("text", view.text))
    run.outbox.extend(list(view.ui)[:max(room, 0)])
    return {"ok": True, "sent": view.text,
            "note": "The view is sent. Add no text, or one short line."}


def rendition_of(messages: list[OutMessage]) -> str:
    """The thread text of the elements, in send order."""
    return "\n\n".join(m.rendition for m in messages)


def thread_record(reply: str, messages: list[OutMessage]) -> str:
    """What the thread keeps for one answer: the text, then the mark, then
    each element's rendition. With no element it is the text alone."""
    if not messages:
        return reply
    return (f"{reply}\n\n" if reply else "") + RENDITION_MARK + rendition_of(messages)


def resend_text(stored: str | None) -> str | None:
    """The text to send again from a stored record (:func:`thread_record`).

    The text part, so the member never gets the renditions as text. An answer
    of elements only has no text part, and then the renditions go, because
    some answer is better than none. A reaction is never sent as text: an
    answer that was a reaction only resends as "" (nothing goes out).
    """
    if stored is None:
        return None
    stored = stored.replace(VIEW_MARK, "")
    if RENDITION_MARK not in stored:
        return stored
    text, _mark, rest = stored.partition(RENDITION_MARK)
    return text.strip() or without_reactions(rest)


def without_reactions(records: str) -> str:
    """The element records with each reaction record cut out."""
    kept = [r for r in records.split("\n\n") if not r.startswith("[Reaction:")]
    return "\n\n".join(kept).strip()


# ── The reply guard (WAC-10c, owner screenshots of 2026-10-10) ───────────────
#
# A model that reads its own past replies can copy their element records
# ("[Table: …]") into its text, or promise "tap any of these" with nothing to
# tap. :func:`polish` makes the reply honest: a copied record becomes the real
# element when it can, and is cut when it cannot. A tap promise with nothing
# tappable becomes a list of the reply's own bullets, or loses the sentence.

_RECORD_HEAD = re.compile(
    r"^\s*\[(Table|Chart|Buttons|List|Link|Stats|Board|Timeline|Agenda|Schedule"
    r"|Reaction)\b",
    re.IGNORECASE)
_TAP_PROMISE = re.compile(r"\b(?:tap|tapping|select one|pick one)\b", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-•▪◦·]|\d+[.)])\s+(.+)$")
_SENTENCE = re.compile(r"[^.!?\n]*\b(?:tap|tapping|select one|pick one)\b[^.!?\n]*[.!?]?",
                       re.IGNORECASE)
_MARKS = re.compile(r"[*_~`]")
#: Where a bullet's head ends: " — ", " – ", " - " or ": ".
_HEAD_END = re.compile(r"\s[—–-]\s|:\s")


def _tappable(messages: list[OutMessage]) -> bool:
    return any(m.kind == "interactive" and (m.interactive or {}).get("type")
               in ("button", "list") for m in messages)


def _table_from_record(lines: list[str]) -> OutMessage | None:
    """A copied "[Table: t]" record with "a | b" lines, as a real table."""
    title = re.sub(r"^\s*\[Table:\s*|\]\s*$", "", lines[0]).strip() or "Table"
    rows = [[c.strip() for c in ln.split("|")] for ln in lines[1:] if "|" in ln]
    if len(rows) < 2:
        return None
    cols, body = rows[0], [r for r in rows[1:] if len(r) == len(rows[0])]
    if not body:
        return None
    try:
        return build("table", {"title": title[:80], "columns": cols[:6],
                               "rows": [r[:6] for r in body[:20]]})
    except (ValueError, TypeError, OverflowError):
        return None


def _chart_from_record(line: str) -> OutMessage | None:
    """A copied "[Chart, bar: t] a: 1, b: 2" record, as a real chart."""
    m = re.match(r"^\s*\[Chart,\s*(\w+):\s*([^\]]*)\]\s*(.*)$", line)
    if not m:
        return None
    labels: list[str] = []
    values: list[float] = []
    for pair in m.group(3).split(","):
        label, sep, raw = pair.rpartition(":")
        if not sep:
            continue
        try:
            values.append(float(raw.strip().split()[0]))
        except (ValueError, IndexError):
            return None
        labels.append(label.strip())
    try:
        return build("chart", {"type": m.group(1).lower(),
                               "title": m.group(2).strip()[:80] or "Chart",
                               "labels": labels, "values": values})
    except (ValueError, TypeError, OverflowError):
        return None


def _bullet_row(item: str) -> dict[str, str]:
    """A bullet "*FDM PRINTS* — 3 batches on hold" as a list row: the head is
    the title, the rest is the description."""
    item = _MARKS.sub("", item).strip()
    parts = _HEAD_END.split(item, maxsplit=1)
    title = parts[0].strip() or item
    rest = parts[1].strip() if len(parts) > 1 else ""
    return {"title": title, **({"description": rest} if rest else {})}


def polish(reply: str, messages: list[OutMessage]) -> tuple[str, list[OutMessage]]:
    """The reply with no copied element records and no empty tap promise.

    It may draw, so call it in a worker thread. It never raises: on any
    failure the reply and the elements stay as they were.
    """
    try:
        return _polish(reply, list(messages))
    except Exception:  # the guard must never cost the member the answer
        return reply, list(messages)


def _polish(reply: str, out: list[OutMessage]) -> tuple[str, list[OutMessage]]:
    def room() -> int:
        return MAX_MESSAGES - sum(1 for m in out if m.counts)

    kept: list[str] = []
    lines = reply.split("\n")
    i = 0
    while i < len(lines):
        head = _RECORD_HEAD.match(lines[i])
        if not head:
            kept.append(lines[i])
            i += 1
            continue
        block = [lines[i]]
        i += 1
        while i < len(lines) and lines[i].strip() and (
                "|" in lines[i] or lines[i].lstrip().startswith("- ")):
            block.append(lines[i])
            i += 1
        kind = head.group(1).lower()
        element = None
        if room() > 0 and kind == "table":
            element = _table_from_record(block)
        elif room() > 0 and kind == "chart":
            element = _chart_from_record(block[0])
        if element is not None:
            out.append(element)
    text = "\n".join(kept)

    if _TAP_PROMISE.search(text) and not _tappable(out):
        items = [m.group(1) for m in (_BULLET.match(ln) for ln in text.split("\n")) if m]
        element = None
        if len(items) >= 2 and room() > 0:
            try:
                element = build("list", {"body": "Tap one to dig in.", "button": "Dig in",
                                         "rows": [_bullet_row(it) for it in items[:MAX_ROWS]]})
            except (ValueError, TypeError, OverflowError):
                element = None
        if element is not None:
            out.append(element)
        else:
            text = _SENTENCE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, out
