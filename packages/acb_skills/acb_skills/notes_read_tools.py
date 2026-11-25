"""The chat tools of the ``notes`` pack (task 3963, the notes read tools).

Spec: ``project-docs/specs/chat_parity.md`` section 6.4 (P-4d). Six reads let
a member do from the chat what the Notes page shows. Each tool sends GET
routes of ``gateway/routes/notes/``. Each route checks the owner of a meeting,
or reads the rows of the member only:

* :func:`notes_meetings` lists the meetings of the member (``GET /notes/meetings``).
* :func:`notes_meeting` reads one meeting of the member, with one page of the
  transcript (``GET /notes/meetings/{id}``).
* :func:`notes_settings`, :func:`notes_templates`, :func:`notes_glossary` and
  :func:`notes_copilot_instructions` read the settings of the Note Taker.

**Thirteen reads are NOT here.** The routes for the note, the actions, the
agenda, the context, the bot, the live session and the bot list read a meeting
by its id with no owner check, or they refresh a bot and write rows. The route
of the bot identity starts a browser on the bot profile. They stay in ``DEBT``
and wait for task 3966.

**Reads only.** No tool writes, joins a meeting, starts a bot or reaches a
person. The module has no ``POST``, ``PUT``, ``PATCH`` or ``DELETE``. Each of
the six routes is a plain read with no side effect.

**The server decides each right.** Each tool calls the gateway over HTTP as the
member of the run: the internal bearer and ``X-User-Email`` from the run
binding, the idiom of ``people_tools``. No tool takes an org, a caller or an
address argument (R5). A meeting id must be one UUID, and a bad id sends no
request. Each path parameter goes through ``urllib.parse.quote``.

**Text of other people is data.** A transcript, a note, a glossary term and a
title come from people. Each result puts that text in ONE ``UntrustedFrame``
block, and a tool that read meeting text marks the chat as one that read
outside text (``egress.mark_untrusted_answer``).

**No secret.** Each tool drops a key that names a token, a cookie, a password
or a screenshot. A URL in a text loses its user name, password, query string
and fragment, also when it is scheme-relative or sits in quotes or in angle
brackets. A failed run of a summary shows a fixed text, never the error of a
provider.

**The pack.** The tools are the pack ``notes``, owned by ``org-admin`` (the
owner of the dark packs that no agent runs). It is in no ``FOLLOWS`` entry. A
run loads it where the owner lists ``notes`` in the pack list of the box, or
where that list is ``*``.

Fence: ``tests/unit/test_notes_read_tools.py``.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from acb_common.card_body import one_line
from acb_common.paging import paged
from acb_common.settings import internal_auth_headers

from acb_skills.hidden_text import strip_hidden
from acb_skills.tool_annotations import annotate

__all__ = [
    "NOTES_READ_TOOLS",
    "notes_copilot_instructions",
    "notes_glossary",
    "notes_meeting",
    "notes_meetings",
    "notes_settings",
    "notes_templates",
]

#: The rows of one page, by default and at most.
DEFAULT_ROWS = 25
MAX_ROWS = 50
#: The longest text of one value in a result, and of a long text.
MAX_FIELD_CHARS = 300
MAX_LONG_CHARS = 4000
#: The most items of one list inside a value, and the deepest nesting.
MAX_LIST_ITEMS = 20
MAX_DEPTH = 4
#: The longest filter, and the most meetings that one request asks for.
MAX_FILTER_CHARS = 200
FETCH_MEETINGS = 500
#: The longest reason of a route that a result passes on.
MAX_DETAIL_CHARS = 300
#: The runs of a meeting that the detail shows.
MAX_RUNS = 3

#: A key that names a secret never reaches the model.
_SECRET_WORDS = ("token", "cookie", "password", "passwd", "secret", "authorization",
                 "credential", "screenshot", "api_key", "apikey", "session_id")

_NO_MEMBER = "There is no member for this run, so the notes tools cannot read."
_SHARED_ROOM = "A shared room cannot use the notes tools. Ask in your own chat."
_BAD_ID = ("meeting_id is the id of one meeting, as notes_meetings shows it. "
           "Nothing was read.")
_BAD_FILTER = "The query is one line of at most {n} characters. Nothing was read."
_NOT_FOUND = ("No meeting with that id is in your library. Read notes_meetings "
              "for the ids.")
_UNREACHED = "The Notes routes could not be reached ({kind}). Nothing was read. Try again."
_SHAPE = "The Notes route answered in a shape that this tool cannot read."
_SIGNED_OUT = "Your session is not signed in, so nothing was read."
_NO_RIGHT = ("You do not hold {right} in your organization, so nothing was read. "
             "Ask an admin of your organization.")
_RUN_FAILED = "The run failed. See the meeting page."
_TEXT_NOTE = "People of your organization, or the people in a call, wrote this text."


# ── The gateway, as the member (the idiom of ``people_tools``) ──────────────


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _current_user_email() -> str:
    """The member of this run, from the run's ContextVar, and nothing else."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id
        return _get_memory_user_id() or ""
    except Exception:
        return ""


class _Refused(Exception):
    """A refusal that is an answer: its text goes to the model."""


@dataclass(frozen=True)
class _Reply:
    """One answer of the gateway: the status and the decoded JSON (or None)."""

    status: int
    data: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _headers() -> dict[str, str]:
    """The bearer and the acting member. With no member, or in a shared room,
    refuse before any request."""
    user = _current_user_email()
    if not user:
        raise _Refused(_NO_MEMBER)
    if "@" not in user:
        raise _Refused(_SHARED_ROOM)
    return {**internal_auth_headers(), "X-User-Email": user}


async def _get(path: str, params: dict[str, Any] | None = None) -> _Reply:
    """One GET as the member. Raise :class:`_Refused` with a sentence when the
    member is missing or the network fails. This module sends no other verb."""
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                "GET", f"{_gateway_url()}{path}", headers=headers, params=params or None)
    except httpx.HTTPError as exc:
        raise _Refused(_UNREACHED.format(kind=type(exc).__name__)) from None
    try:
        data = resp.json()
    except Exception:
        data = None
    return _Reply(resp.status_code, data)


# ── The plain words ─────────────────────────────────────────────────────────


def _refusal(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _line(value: Any, width: int = MAX_FIELD_CHARS) -> str:
    """*value* as one line of at most *width* characters, with no hidden
    character. A cut line ends with "..."."""
    text = one_line(strip_hidden(str(value)))
    if len(text) <= width:
        return text
    return textwrap.shorten(text, width=width, placeholder="...")


def _long(value: Any, width: int = MAX_LONG_CHARS) -> str:
    """A long text with its line breaks, with no hidden character, cut at
    *width* characters."""
    text = _scrub(strip_hidden(str(value)).strip())
    if len(text) <= width:
        return text
    cut = textwrap.wrap(text, width, max_lines=1, placeholder="...", replace_whitespace=False,
                        drop_whitespace=False)
    return cut[0] if cut else "..."


def _detail(reply: _Reply) -> str:
    raw = reply.data.get("detail") if isinstance(reply.data, dict) else None
    return _line(raw, MAX_DETAIL_CHARS) if isinstance(raw, str) else ""


def _fail(reply: _Reply) -> dict[str, Any]:
    """The refusal of a Notes route in plain words."""
    detail = _detail(reply)
    if reply.status == 401:
        return _refusal(_SIGNED_OUT, status=401)
    if reply.status == 403:
        right = "the right that this needs"
        marker = "missing permission '"
        if marker in detail:
            right = detail.split(marker, 1)[1].split("'", 1)[0] or right
        return _refusal(_NO_RIGHT.format(right=right), status=403)
    if detail:
        return _refusal(detail, status=reply.status)
    return _refusal(f"The Notes route answered with status {reply.status}. Nothing was read.",
                    status=reply.status)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


#: A URL in free text: with a scheme, or scheme-relative (``//host/x``). A
#: delimiter such as ``<``, a quote or a bracket ends it.
_URL_IN_TEXT = re.compile(r"""(?<![\w/:.-])(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?//[^\s<>"'`)\]]+""")


def _scrub(text: str) -> str:
    """*text* with each URL in it made safe: no user name, password, query
    string or fragment. A URL can hold a token in each of them."""
    return _URL_IN_TEXT.sub(lambda m: _safe_url(m.group(0)), text)


def _secret_key(key: Any) -> bool:
    low = str(key).lower()
    return any(word in low for word in _SECRET_WORDS)


def _fold(value: Any, depth: int = 0) -> Any:
    """*value* with each text as one line, each list capped, each secret key
    and each field with no value left out."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _line(_scrub(value))
    if depth >= MAX_DEPTH:
        return _line(json.dumps(value, ensure_ascii=False, default=str))
    if isinstance(value, dict):
        return {_line(k, 64): _fold(v, depth + 1) for k, v in value.items()
                if not _empty(v) and not _secret_key(k)}
    if isinstance(value, (list, tuple)):
        page = paged(list(value), 0, MAX_LIST_ITEMS, default=MAX_LIST_ITEMS,
                     maximum=MAX_LIST_ITEMS)
        out = [_fold(v, depth + 1) for v in page.items]
        if page.total > len(page.items):
            out.append(f"(+{page.total - len(page.items)} more)")
        return out
    return _line(value)


def _pick(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """The named fields of one row, folded, with no empty value. A field that
    the list does not name never passes."""
    if not isinstance(row, dict):
        return {}
    return {k: _fold(row[k]) for k in fields if not _empty(row.get(k))}


def _block(label: str, rows: Any) -> tuple[str, Any]:
    """``(the framed block, the frame)``."""
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
    return frame.wrap(label, text), frame


async def _mark_read() -> None:
    """Mark the chat of the run: it read text that other people wrote. The
    tenant and the session come from the run binding only."""
    from acb_skills.egress import mark_untrusted_answer

    await mark_untrusted_answer()


def _framed(label: str, rows: Any, key: str, note: str = _TEXT_NOTE,
            **extra: Any) -> dict[str, Any]:
    block, frame = _block(label, rows)
    return {"ok": True, key: block, **extra, "note": frame.note(note)}


def _meeting_id(raw: Any) -> str | None:
    """The canonical form of one UUID, or None."""
    text = str(raw or "").strip()
    if len(text) != 36:
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def _meeting_path(template: str, mid: str) -> str:
    """The route of a meeting: *template* holds one ``{}`` for the id, and the
    id goes through ``quote`` (the fence of ``chat_parity`` reads the whole
    template in each tool)."""
    return template.replace("{}", quote(mid, safe=""))


def _safe_url(raw: Any) -> str:
    """A URL with no user name, no password, no query string and no fragment: a
    secret can live in each of them."""
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return ""
    host = parts.netloc.rpartition("@")[2]
    return _line(urlunsplit((parts.scheme, host, parts.path, "", "")))


async def _read_meeting(mid_raw: Any, template: str,
                        ) -> tuple[str, _Reply] | dict[str, Any]:
    """The reply of a meeting route, or the refusal as a dict."""
    mid = _meeting_id(mid_raw)
    if mid is None:
        return _refusal(_BAD_ID)
    try:
        reply = await _get(_meeting_path(template, mid))
    except _Refused as exc:
        return _refusal(str(exc))
    if reply.status == 404:
        return _refusal(_NOT_FOUND, status=404)
    if not reply.ok:
        return _fail(reply)
    return mid, reply


async def _read(path: str, params: dict[str, Any] | None = None) -> _Reply | dict[str, Any]:
    try:
        reply = await _get(path, params)
    except _Refused as exc:
        return _refusal(str(exc))
    return reply if reply.ok else _fail(reply)


_MEETING_FIELDS = ("id", "title", "platform", "status", "language", "duration_s",
                   "segment_count", "has_notes", "template_key", "start_at", "created_at",
                   "scheduled_at", "copilot_enabled", "pending_actions", "action_count",
                   "agenda_count", "has_brief", "attendee_count", "is_live")
_SEGMENT_FIELDS = ("idx", "start_s", "end_s", "speaker_label", "text")


# ── The tools ───────────────────────────────────────────────────────────────


@annotate(read_only=True, idempotent=True, open_world=False)
async def notes_meetings(query: str = "", offset: int = 0,
                         limit: int = DEFAULT_ROWS) -> dict[str, Any]:
    """List your own meetings, newest first, with an optional search of titles and transcripts."""
    params: dict[str, Any] = {"limit": FETCH_MEETINGS}
    text = one_line(query) if isinstance(query, str) else ""
    if len(text) > MAX_FILTER_CHARS:
        return _refusal(_BAD_FILTER.format(n=MAX_FILTER_CHARS))
    if text:
        params["query"] = text
    got = await _read("/notes/meetings", params)
    if isinstance(got, dict):
        return got
    if not isinstance(got.data, list):
        return _refusal(_SHAPE)
    rows = [r for r in got.data if isinstance(r, dict)]
    page = paged(rows, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="meetings")
    capped = len(rows) >= FETCH_MEETINGS
    if page.items:
        await _mark_read()
    extra: dict[str, Any] = {}
    if capped:
        # The route answers with at most FETCH_MEETINGS rows and has no offset.
        extra = {"total_is_at_least": True, "capped_note": (
            f"The library holds at least {len(rows)} meetings, and this read shows "
            f"the newest {FETCH_MEETINGS} only. Use query to narrow it.")}
    line = page.line
    if capped:
        # Never say "last page": the route cuts the list at the cap.
        end = page.offset + len(page.items)
        line = (f"Showing {page.offset + 1}-{end} of at least {len(rows)} meetings. "
                f"This read holds the newest {FETCH_MEETINGS} only. Use query to narrow it.")
        if end < len(rows):
            line += f" More meetings are in this read: offset={end}."
    return _framed("MEETINGS", [_pick(r, _MEETING_FIELDS) for r in page.items], "rows",
                   total=page.total, shown=len(page.items), page=line, **extra)


@annotate(read_only=True, idempotent=True, open_world=False)
async def notes_meeting(meeting_id: str, segments_offset: int = 0,
                        segments_limit: int = DEFAULT_ROWS) -> dict[str, Any]:
    """Read one of your meetings: the details, the summary, the attendees, the last runs and one page of the transcript."""
    got = await _read_meeting(meeting_id, "/notes/meetings/{}")
    if isinstance(got, dict):
        return got
    mid, reply = got
    data = reply.data
    if not isinstance(data, dict):
        return _refusal(_SHAPE)
    segments = [s for s in (data.get("segments") or []) if isinstance(s, dict)]
    seg_page = paged(segments, segments_offset, segments_limit, default=DEFAULT_ROWS,
                     maximum=MAX_ROWS, noun="transcript segments", param="segments_offset")
    recs = [r for r in (data.get("recordings") or []) if isinstance(r, dict)]
    runs = [r for r in (data.get("runs") or []) if isinstance(r, dict)]
    run_page = paged(runs, 0, MAX_RUNS, default=MAX_RUNS, maximum=MAX_RUNS, noun="runs")
    body: dict[str, Any] = _pick(data, _MEETING_FIELDS)
    body["recordings"] = len(recs)
    for key in ("summary_md", "scratch_notes"):
        if not _empty(data.get(key)):
            body[key] = _long(data[key])
    if isinstance(data.get("speaker_names"), dict):
        body["speaker_names"] = _fold(data["speaker_names"])
    attendees = [a for a in (data.get("attendees") or []) if isinstance(a, dict)]
    body["attendees"] = [_pick(a, ("name", "email")) for a in
                         paged(attendees, 0, MAX_LIST_ITEMS, default=MAX_LIST_ITEMS,
                               maximum=MAX_LIST_ITEMS).items]
    # The error text of a provider can hold a URL with a token, so a failed run
    # carries a fixed text.
    body["last_runs"] = [
        {**_pick(r, ("kind", "status", "stage", "created_at", "finished_at")),
         **({"error": _RUN_FAILED} if not _empty(r.get("error")) else {})}
        for r in run_page.items]
    body["transcript"] = [_pick(s, _SEGMENT_FIELDS) for s in seg_page.items]
    await _mark_read()
    return _framed("MEETING", body, "meeting", meeting_id=mid,
                   transcript_total=seg_page.total, transcript_shown=len(seg_page.items),
                   transcript_page=seg_page.line)


@annotate(read_only=True, idempotent=True, open_world=False)
async def notes_settings() -> dict[str, Any]:
    """Read your Note Taker settings: copilot defaults, live transcription, the default template and auto-dispatch."""
    got = await _read("/notes/settings")
    if isinstance(got, dict):
        return got
    data = got.data if isinstance(got.data, dict) else None
    settings = data.get("settings") if data is not None else None
    if not isinstance(settings, dict):
        return _refusal(_SHAPE)
    body = _fold({k: v for k, v in settings.items() if k != "copilot_instructions"})
    if not _empty(settings.get("copilot_instructions")):
        body["copilot_instructions"] = _long(settings["copilot_instructions"], 1500)
    return _framed("NOTES SETTINGS", body, "settings",
                   sensitivities=_fold(data.get("sensitivities") or []))


@annotate(read_only=True, idempotent=True, open_world=False)
async def notes_templates() -> dict[str, Any]:
    """List the meeting templates that Notes ships, each with its key and label."""
    got = await _read("/notes/templates")
    if isinstance(got, dict):
        return got
    if not isinstance(got.data, list):
        return _refusal(_SHAPE)
    rows = [_pick(r, ("key", "label")) for r in got.data if isinstance(r, dict)]
    page = paged(rows, 0, MAX_ROWS, default=MAX_ROWS, maximum=MAX_ROWS, noun="templates")
    return {"ok": True, "templates": page.items, "total": page.total, "page": page.line}


@annotate(read_only=True, idempotent=True, open_world=False)
async def notes_glossary(offset: int = 0, limit: int = DEFAULT_ROWS) -> dict[str, Any]:
    """List your glossary terms that help the transcript spell names and jargon."""
    got = await _read("/notes/glossary")
    if isinstance(got, dict):
        return got
    if not isinstance(got.data, list):
        return _refusal(_SHAPE)
    rows = [_pick(r, ("id", "term")) for r in got.data if isinstance(r, dict)]
    page = paged(rows, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="terms")
    return _framed("GLOSSARY", page.items, "rows", total=page.total, shown=len(page.items),
                   page=page.line)


@annotate(read_only=True, idempotent=True, open_world=False)
async def notes_copilot_instructions() -> dict[str, Any]:
    """Read your standing instructions for the meeting copilot."""
    got = await _read("/notes/copilot/instructions")
    if isinstance(got, dict):
        return got
    if not isinstance(got.data, dict):
        return _refusal(_SHAPE)
    text = got.data.get("instructions")
    if _empty(text):
        return {"ok": True, "has_instructions": False,
                "answer": "You have set no standing instructions for the copilot."}
    return _framed("COPILOT INSTRUCTIONS", _long(text, 2000), "instructions",
                   has_instructions=True)


#: Each tool of the ``notes`` pack, in the order of its skill family.
NOTES_READ_TOOLS = (
    notes_meetings, notes_meeting, notes_settings, notes_templates, notes_glossary,
    notes_copilot_instructions,
)
