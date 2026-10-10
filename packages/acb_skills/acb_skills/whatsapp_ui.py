"""The WhatsApp run profile and its native UI tool (WS-47 WAC-10a).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §12.

**The profile.** ``bot_run`` opens :func:`whatsapp_run` around the run. While
it is open, the tool injection seam (``orchestrator._tool_injection``) does
three things for every agent of the run, nested runs too:

* It takes :data:`WITHHELD_TOOLS` away. They draw web cards, write files or
  fix code, and a WhatsApp member can see none of that. Each one only cost
  context.
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

**Reads only, as in WAC-3.** Buttons and rows ask or narrow a question. A
write still goes to the web app until WAC-4.

Fence: ``tests/unit/test_wac_native_ui.py``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from acb_skills import whatsapp_render as render

#: The tools a WhatsApp run does not get. Each one draws a web card, writes a
#: file, fixes code or reaches the open web, and the member's scope is their
#: own Metorite work (§5.5 "The scope rule"). ``ask_questions`` and
#: ``request_confirmation`` stay refused by ``refuse_cards`` as well.
WITHHELD_TOOLS: frozenset[str] = frozenset({
    # web cards and the web canvas
    "emit_generative_ui", "ask_questions", "manage_todo_list",
    "load_design_system", "load_artifact_kit",
    # files and shares in the web workspace
    "write_artifact", "share_artifact", "read_attachment",
    # code, diagnostics and set-up
    "get_errors", "run_diagnostics", "list_integrations", "run_script",
    "code_task", "run_command", "install_dependency", "spawn_copilot_agent",
    # the open web, outside the scope rule
    "web_search", "fetch_page",
    # a background run answers in the web chat, which this member is not in
    "call_agent_background",
    # specialists that build or configure, which a chat on a phone cannot do
    "app_builder", "apis_config",
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
#: A link button opens Metorite and nothing else, so a model that was talked
#: into it cannot send the member to a phishing page.
LINK_HOST = "metorite.com"


@dataclass(frozen=True)
class OutMessage:
    """One element that waits for ``bot_run`` to send it.

    ``rendition`` is plain text of what the member sees. The thread keeps it,
    so the next turn knows which buttons it offered.
    """

    kind: str  # "interactive" or "image"
    rendition: str
    interactive: dict[str, Any] | None = None
    png: bytes | None = None
    caption: str | None = None


@dataclass
class WhatsAppRun:
    """The open WhatsApp run: its own agent and its outbox."""

    agent: str
    outbox: list[OutMessage] = field(default_factory=list)


_RUN: ContextVar[WhatsAppRun | None] = ContextVar("whatsapp_run", default=None)


@contextlib.contextmanager
def whatsapp_run(agent: str) -> Iterator[WhatsAppRun]:
    """Open the WhatsApp profile for the run of *agent* in this context.

    Like ``refuse_cards``: a nested ``run_agent`` and each task the run starts
    copy the context, so they see the profile too. Closing it restores the
    value it found.
    """
    run = WhatsAppRun(agent=agent)
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
            title = _short(row.get("title"), ROW_TITLE_MAX, "row")
            if title.casefold() in seen:
                raise _Refused(f'two rows have the title "{title}"')
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
    url = data.get("url")
    if not isinstance(url, str):
        raise _Refused('"url" is required')
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (host == LINK_HOST or host.endswith("." + LINK_HOST)):
        raise _Refused(f"a link must open https://app.{LINK_HOST}. Other sites are refused")
    interactive = {
        "type": "cta_url",
        "body": {"text": body},
        "action": {"name": "cta_url",
                   "parameters": {"display_text": label, "url": url.strip()}},
    }
    _footer(data, interactive)
    return OutMessage("interactive", f"{body}\n[Link: {label} -> {url.strip()}]",
                      interactive=interactive)


def _list_of(data: dict[str, Any], key: str, *, required: bool = True) -> list[Any] | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, list):
        raise _Refused(f'"{key}" must be a list')
    return value


def _chart(data: dict[str, Any]) -> OutMessage:
    kind = data.get("type") or "bar"
    title = _text(data, "title", limit=80)
    subtitle = _text(data, "subtitle", limit=120, required=False) or None
    caption = _text(data, "caption", limit=CAPTION_MAX, required=False)
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
        raise _Refused('chart "type" must be "bar", "line" or "progress"')
    pairs = ", ".join(f"{lab}: {val}" for lab, val in zip(labels, values))
    rendition = f"[Chart, {kind}: {title}] {pairs}" + (f"\n{caption}" if caption else "")
    return OutMessage("image", rendition, png=render.to_png(svg), caption=caption or None)


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


_BUILDERS = {
    "buttons": _buttons,
    "list": _list,
    "link": _link,
    "chart": _chart,
    "table": _table,
}


def build(kind: str, data: dict[str, Any]) -> OutMessage:
    """The checked element, or :class:`ValueError` with what to fix."""
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise _Refused(f'"kind" must be one of: {", ".join(_BUILDERS)}')
    if not isinstance(data, dict):
        raise _Refused('"data" must be an object')
    return builder(data)


# ── The tool ────────────────────────────────────────────────────────────────


async def whatsapp_ui(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Send a native WhatsApp element after your text reply.

    kind and data:
    - "buttons": up to 3 quick replies.
      data={"body": str, "buttons": [str, ...]}. A title has at most 20 characters.
    - "list": a menu of up to 10 rows. data={"body": str, "button": "View tasks",
      "rows": [{"title": str (24), "description": str (72)}]}, or
      "sections": [{"title": str, "rows": [...]}] in place of "rows".
    - "link": one button that opens Metorite. data={"body": str,
      "label": str (20), "url": "https://app.metorite.com/<page>"}.
    - "chart": an image. data={"type": "bar" | "line" | "progress",
      "title": str, "labels": [str], "values": [number], "unit": str,
      "totals": [number] (progress), "subtitle": str, "caption": str}.
    - "table": an image. data={"title": str, "columns": [str] (6 at most),
      "rows": [[cell, ...]] (20 at most), "subtitle": str, "caption": str}.

    A tap on a button or a row comes back as the member's next message, with
    the title as its text. Buttons and rows ask or narrow a question. They
    change no data. Do not repeat in your text what the element shows.
    At most 3 elements in one reply.

    Returns {"ok": true} when the element is queued, or {"ok": false,
    "error": ...}. Fix the error and call again.
    """
    run = _RUN.get()
    if run is None:
        return {"ok": False, "error": "whatsapp_ui works only in a WhatsApp chat"}
    if len(run.outbox) >= MAX_MESSAGES:
        return {"ok": False,
                "error": f"this reply already has {MAX_MESSAGES} elements. Put the rest in text"}
    try:
        message = build(str(kind or "").strip().lower(), data)
    except (ValueError, TypeError) as exc:  # RenderError is a ValueError
        return {"ok": False, "error": str(exc)}
    run.outbox.append(message)
    return {"ok": True, "queued": len(run.outbox),
            "note": "It goes after your text. Refer to it in one line."}


def rendition_of(messages: list[OutMessage]) -> str:
    """The thread text of the elements, in send order."""
    return "\n\n".join(m.rendition for m in messages)
