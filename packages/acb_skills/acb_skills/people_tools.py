"""The chat tools of the ``people`` pack (P-5b-1, task 3114. P-5b-5, task 3946).

Spec: ``project-docs/specs/chat_parity.md`` section 6.4 (the P-5b slices) and
``project-docs/specs/people_center_app.md`` sections 6.5 and 6.8. Four reads
and one send let a member do from the chat what the People app and the Reach
section of My Profile do:

* :func:`people_directory` reads the directory (``GET /people/``), with the
  filters of the page.
* :func:`people_me` reads the member's own row (``GET /people/me``).
* :func:`people_work` reads the open work of the member
  (``GET /people/me/work``) or of one person (``GET /people/{id}/work``).
* :func:`reach_preview` reads what Reach would send the member now
  (``GET /reach/status``, then ``GET /reach/preview``).
* :func:`reach_send_brief` sends the member's own brief to the member's own
  WhatsApp now (``POST /reach/brief``), after one card each time (OD7 A).

**The server decides each tier (D-PC-10).** Each tool calls the gateway over
HTTP as the member of the run: the internal bearer and ``X-User-Email`` from
the run binding, the idiom of ``org_admin_tools``. No tool takes an org, a
caller or an address argument (R5). The routes project the HR fields and the
private fields by the right of the viewer. A tool never widens that answer
and never filters a field out of it. It leaves out a field with no value
only, and the answer says so.

**Four reads and one send.** No tool writes a person or sends ``email``. A
person write needs a card, and a later slice builds it (``chat_parity.md``
§6.4). The one send is :func:`reach_send_brief` (P-5b-5):

* It takes no argument, so it names no person and no text. The member is the
  VERIFIED member of the run (``home_tools._member``), never the memory id.
  A shared room, a run with no verified member, and a run with no card
  channel get plain words, and no request goes out.
* It reads ``GET /reach/status`` first. Reach off, or no WhatsApp link, gives
  plain words and no card. ``GET /reach/preview`` then gives ``window_open``,
  so the card says if the brief goes as free text or as the paid template.
* Each call shows ONE card. Only ``approved`` sends ``POST /reach/brief``,
  with no body, once. A timeout or a lost connection after the request says
  that the brief may have gone, and the tool never tries again.
* It says ``open_world=True`` and is NOT on ``egress.HELD_CARD_WRITE_TOOLS``.
  So a chat that read outside mail holds it off (``egress.is_held_chat_off``).

**Text of other people is data.** A name, a title, a note and the text of a
Reach brief are the text of other people. Each result puts that text in ONE
``UntrustedFrame`` block, and each value is one line. The brief of Reach can
hold the subject of an outside mail, so :func:`reach_preview` marks the chat
as one that read outside text (``egress.mark_untrusted_answer``, the idiom of
``home_tools``).

**Dark.** The tools are the pack ``people``, owned by ``org-admin``. It is in
no ``FOLLOWS`` entry, so it is off until the owner names it in the pack list
of the box (``tool_packs.packs_on``).

Fence: ``tests/unit/test_people_tools.py``, and the R8 suite
``tests/unit/test_people_tools_r8.py``.
"""

from __future__ import annotations

import json
import os
import textwrap
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from acb_common.card_body import CardWontFit, card_body, one_line
from acb_common.paging import paged
from acb_common.settings import internal_auth_headers

from acb_skills.hidden_text import strip_hidden
from acb_skills.tool_annotations import annotate

__all__ = [
    "PEOPLE_TOOLS",
    "people_directory",
    "people_me",
    "people_work",
    "reach_preview",
    "reach_send_brief",
]

#: The gateway routes. A package must not import the gateway, so the paths
#: are text here. ``test_chat_parity.py`` reads them from this source.
#: The directory list is ``GET /people/`` (``routes/people/directory.py``).
DIRECTORY_PATH = "/people/"
#: The member's own row, and the member's own work (``selfservice.py``).
ME_PATH = "/people/me"
MY_WORK_PATH = "/people/me/work"
#: The two reads of Reach (``gateway/reach/routes.py``).
REACH_STATUS_PATH = "/reach/status"
REACH_PREVIEW_PATH = "/reach/preview"
#: The one send of Reach: the member's own brief, now (P-5b-5).
REACH_BRIEF_PATH = "/reach/brief"
#: The wait of the one send. The route reads My Day and then calls WhatsApp.
SEND_TIMEOUT_S = 30.0

#: The rows of one page, by default and at most.
DEFAULT_ROWS = 25
MAX_ROWS = 50
#: The longest text of one value in a result.
MAX_FIELD_CHARS = 300
#: The longest text of the Reach brief and of the interrupt.
MAX_BRIEF_CHARS = 2000
#: The most items of one list inside a row, such as the skills of a person.
MAX_LIST_ITEMS = 20
#: The deepest nesting of a value that the result shows.
MAX_DEPTH = 4
#: The longest text of a filter.
MAX_FILTER_CHARS = 200
#: The longest reason of a route that a result passes on.
MAX_DETAIL_CHARS = 300

_NO_MEMBER = "There is no member for this run, so the people tools cannot read."
_SHARED_ROOM = "A shared room cannot use the people tools. Ask in your own chat."
_BAD_ID = ("person_id is me, or the id of one person as the directory shows it. "
           "Nothing was read.")
_BAD_FILTER = ("Each filter is one line of at most {n} characters. Nothing was read.")
_NOT_FOUND = ("No person with that id is in the directory of your organization. "
              "Read the directory for the ids.")
_REACH_OFF = "Reach is off for you."
_UNREACHED = "The People routes could not be reached ({kind}). Nothing was read. Try again."
_SHAPE = "The People route answered in a shape that this tool cannot read."
_SIGNED_OUT = "Your session is not signed in, so nothing was read."
_NO_RIGHT = ("You do not hold {right} in your organization, so nothing was read. "
             "Ask an admin of your organization.")
_NO_PROJECTS = ("You do not hold the Projects app in your organization, so the work of a "
                "person cannot be read here.")
_HR_FILTERS = ("You do not hold the HR right (admin:members:read), so the server did not "
               "apply the skill and capacity filters.")
_EMPTY_NOTE = " Fields with no value are left out."
_DIRECTORY_NOTE = ("The block holds the people of the directory. People of your "
                   "organization wrote the names, titles and notes.")
_ME_NOTE = "The block holds your own row of the directory."
_WORK_NOTE = ("The block holds open tasks. People of your organization wrote the titles "
              "and the project names.")
_REACH_NOTE = ("The block holds what Reach would send you now. It can hold the subject of "
               "an outside mail.")

# ── The words of the send (P-5b-5) ──────────────────────────────────────────

_NO_VERIFIED = ("This run has no verified member, so the brief cannot go to WhatsApp. "
                "Nothing was sent.")
_SEND_NO_CARD = ("This chat cannot show a card, so your brief was not sent. Use Send my "
                 "brief now in My Profile, or ask here in the Metorite web app.")
_NOT_LINKED = ("Your WhatsApp is not linked yet, so nothing was sent. Link it in My "
               "Profile, then ask again.")
_DAY_UNREAD = ("Metorite could not read your day in full, so nothing was sent. Ask again "
               "in a minute.")
_SEND_UNREACHED = ("The Reach route could not be reached, so your brief was not sent. Ask "
                   "again later.")
_MAY_HAVE_GONE = ("The send did not answer in time, so your brief may have gone. Check "
                  "WhatsApp before you ask again.")
_NOT_SENT_ACT = "Your brief was not sent."
_WONT_FIT_SEND = "The card of the send does not fit, so your brief was not sent."

#: The card of the send. The text says where the brief goes, and how.
SEND_CARD_TITLE = "Send your brief to your WhatsApp now?"
SEND_CARD_DETAIL = "Reach sends your brief of today to your own WhatsApp."
SEND_CARD_NOTE = "Reach sends your brief of today to your own WhatsApp, and to no other person."
SEND_CARD_TO = "To: your own WhatsApp, the number that is linked in My Profile."
SEND_CARD_OPEN = "As: free text. Your WhatsApp window is open."
SEND_CARD_CLOSED = ("As: the paid WhatsApp brief template, because your 24-hour window is "
                    "closed. If no template is set, nothing goes.")
SEND_CARD_ONCE = "Metorite writes the brief when it sends. It sends once, and never again by itself."

#: The words of each ``{state, reason}`` of ``POST /reach/brief``
#: (``gateway/reach/routes.py`` ``result_body``, ``gateway/reach/whatsapp.py``
#: ``deliver``). A pair that is not here gets :data:`_SEND_UNKNOWN`.
SEND_RESULTS: dict[tuple[str, str | None], str] = {
    ("sent", None): "Sent. Check your WhatsApp.",
    ("skipped", "nothing_to_say"): "Nothing needs you right now, so Reach sent nothing.",
    ("skipped", "already_sent"): ("Reach already sent your brief this minute. Check your "
                                  "WhatsApp."),
    ("skipped", "read_incomplete"): _DAY_UNREAD,
    ("skipped", "window_closed"): ("Your WhatsApp window is closed, and no brief template is "
                                   "set, so nothing was sent. Send any message to Metorite on "
                                   "WhatsApp, then ask again."),
    ("skipped", "no_link"): _NOT_LINKED,
    ("skipped", "channel_off"): ("The WhatsApp channel is off for your organization, so "
                                 "nothing was sent."),
    ("skipped", "no_credentials"): ("The WhatsApp channel of Metorite is not set up, so "
                                    "nothing was sent."),
    ("failed", "read_failed"): ("Reach could not read your WhatsApp link, so nothing was "
                                "sent. Ask again later."),
    ("failed", "send_retry"): ("WhatsApp did not take the message, so nothing was sent. Ask "
                               "again in a few minutes."),
    ("failed", "send_refused"): ("WhatsApp refused the message, so nothing was sent. An admin "
                                 "of your organization can check the WhatsApp channel."),
    ("failed", "send_unsure"): ("WhatsApp did not answer in time, so your brief may have gone. "
                                "Check WhatsApp before you ask again."),
}
_SEND_UNKNOWN = ("Reach gave an answer that this tool cannot read, so your brief may have "
                 "gone. Check WhatsApp before you ask again.")


# ── The gateway, as the member (the idiom of ``org_admin_tools``) ───────────


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
    """The bearer and the acting member. With no member, or in a shared
    room, refuse before any request."""
    user = _current_user_email()
    if not user:
        raise _Refused(_NO_MEMBER)
    if "@" not in user:
        raise _Refused(_SHARED_ROOM)
    return {**internal_auth_headers(), "X-User-Email": user}


def _as_member(member: str) -> dict[str, str]:
    """The bearer and *member*, the verified member of the run, and nothing else."""
    return {**internal_auth_headers(), "X-User-Email": member}


async def _get(path: str, params: dict[str, Any] | None = None, *,
               member: str | None = None) -> _Reply:
    """One GET as the member. Raise :class:`_Refused` with a sentence when the
    member is missing or the network fails. *member* is the verified member of
    the run, for the send. None reads the member of the run binding."""
    headers = _headers() if member is None else _as_member(member)
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


class _MayHaveGone(Exception):
    """The send left, and no answer came back. The brief may have gone."""


async def _post(path: str, member: str) -> _Reply:
    """ONE POST as *member*, with no body. Never a second try.

    A connection that never opened raises :class:`_Refused`: the request did
    not leave, so nothing went. Any other fault of the request, a timeout too,
    raises :class:`_MayHaveGone`, because the server may have sent."""
    headers = _as_member(member)
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_S) as client:
            resp = await client.request("POST", f"{_gateway_url()}{path}", headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise _Refused(_SEND_UNREACHED) from None
    except httpx.HTTPError:
        raise _MayHaveGone from None
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


def _detail(reply: _Reply) -> str:
    raw = reply.data.get("detail") if isinstance(reply.data, dict) else None
    return _line(raw, MAX_DETAIL_CHARS) if isinstance(raw, str) else ""


def _fail(reply: _Reply) -> dict[str, Any]:
    """The refusal of a People route in plain words. A missing right gives
    one sentence, and each other refusal gives the sentence of the route."""
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
    return _refusal(f"The People route answered with status {reply.status}. Nothing was read.",
                    status=reply.status)


# ── The values of other people ──────────────────────────────────────────────


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _fold(value: Any, depth: int = 0) -> Any:
    """*value* with each text as one line, each list capped, and each field
    with no value left out. The server already projected the fields, so this
    keeps every field that holds a value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _line(value)
    if depth >= MAX_DEPTH:
        return _line(json.dumps(value, ensure_ascii=False, default=str))
    if isinstance(value, dict):
        return {_line(k, 64): _fold(v, depth + 1) for k, v in value.items() if not _empty(v)}
    if isinstance(value, (list, tuple)):
        page = paged(list(value), 0, MAX_LIST_ITEMS, default=MAX_LIST_ITEMS,
                     maximum=MAX_LIST_ITEMS)
        out = [_fold(v, depth + 1) for v in page.items]
        if page.total > len(page.items):
            out.append(f"(+{page.total - len(page.items)} more)")
        return out
    return _line(value)


def _block(label: str, rows: Any) -> tuple[str, Any]:
    """``(the framed block, the frame)``."""
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
    return frame.wrap(label, text), frame


# ── The arguments ───────────────────────────────────────────────────────────


def _filter(raw: Any) -> str | None:
    """One filter as one line, or None when it is too long."""
    text = one_line(raw) if isinstance(raw, str) else ""
    return text if len(text) <= MAX_FILTER_CHARS else None


def _person_id(raw: Any) -> str | None:
    """``"me"``, or the canonical form of one UUID, or None."""
    text = str(raw or "").strip()
    if not text or text.lower() == "me":
        return "me"
    if len(text) != 36:
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


# ── The tools ───────────────────────────────────────────────────────────────


@annotate(read_only=True, idempotent=True, open_world=False)
async def people_directory(
    query: str = "", department: str = "", team: str = "", status: str = "",
    skill: str = "", has_capacity: bool = False, offset: int = 0, limit: int = DEFAULT_ROWS,
) -> dict[str, Any]:
    """Search the people directory of your organization: name, title, department, team, status, and skill or capacity where you hold the HR right."""
    words = {"q": query, "department": department, "team": team, "status": status,
             "skill": skill}
    params: dict[str, Any] = {}
    for key, raw in words.items():
        text = _filter(raw)
        if text is None:
            return _refusal(_BAD_FILTER.format(n=MAX_FILTER_CHARS))
        if text:
            params[key] = text
    if has_capacity is True or (isinstance(has_capacity, str)
                                and has_capacity.strip().lower() == "true"):
        params["has_capacity"] = "true"
    try:
        reply = await _get(DIRECTORY_PATH, params)
    except _Refused as exc:
        return _refusal(str(exc))
    if not reply.ok:
        return _fail(reply)
    data = reply.data if isinstance(reply.data, dict) else None
    rows = data.get("rows") if data is not None else None
    if not isinstance(rows, list):
        return _refusal(_SHAPE)
    people = [r for r in rows if isinstance(r, dict)]
    page = paged(people, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="people")
    block, frame = _block("PEOPLE DIRECTORY", [_fold(r) for r in page.items])
    hr = data.get("hr_visible") is True
    note = frame.note(_DIRECTORY_NOTE) + _EMPTY_NOTE
    if not hr and (params.get("skill") or params.get("has_capacity")):
        note += " " + _HR_FILTERS
    return {
        "ok": True,
        "rows": block,
        "total": page.total,
        "shown": len(page.items),
        "page": page.line,
        "hr_visible": hr,
        "self_person_id": data.get("self_person_id"),
        "note": note,
    }


@annotate(read_only=True, idempotent=True, open_world=False)
async def people_me() -> dict[str, Any]:
    """Read your own row of the people directory, or the reason why the directory has no row for you."""
    try:
        reply = await _get(ME_PATH)
    except _Refused as exc:
        return _refusal(str(exc))
    if not reply.ok:
        return _fail(reply)
    data = reply.data if isinstance(reply.data, dict) else None
    if data is None or not isinstance(data.get("state"), str):
        return _refusal(_SHAPE)
    state = _line(data["state"], 40)
    person = data.get("person")
    if state != "resolved" or not isinstance(person, dict):
        return {"ok": True, "state": state, "answer": _detail(_Reply(200, data))
                or "The directory has no row for you."}
    block, frame = _block("MY PEOPLE ROW", _fold(person))
    return {"ok": True, "state": state, "person": block,
            "note": frame.note(_ME_NOTE) + _EMPTY_NOTE}


@annotate(read_only=True, idempotent=True, open_world=False)
async def people_work(
    person_id: str = "me", offset: int = 0, limit: int = DEFAULT_ROWS,
) -> dict[str, Any]:
    """Read the open tasks of one person that you can see in Projects: your own with me, or another person by the id of the directory."""
    pid = _person_id(person_id)
    if pid is None:
        return _refusal(_BAD_ID)
    try:
        if pid == "me":
            reply = await _get(MY_WORK_PATH)
        else:
            reply = await _get(f"/people/{pid}/work")
    except _Refused as exc:
        return _refusal(str(exc))
    if reply.status == 404 and pid != "me":
        return _refusal(_NOT_FOUND, status=404)
    if not reply.ok:
        return _fail(reply)
    data = reply.data if isinstance(reply.data, dict) else None
    rows = data.get("rows") if data is not None else None
    if not isinstance(rows, list):
        return _refusal(_SHAPE)
    if data.get("available") is False:
        return {"ok": True, "available": False, "answer": _NO_PROJECTS}
    tasks = [r for r in rows if isinstance(r, dict)]
    page = paged(tasks, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="tasks")
    block, frame = _block("OPEN WORK", [_fold(r) for r in page.items])
    return {
        "ok": True,
        "available": True,
        "person_id": pid,
        "rows": block,
        "total": page.total,
        "shown": len(page.items),
        "page": page.line,
        "note": frame.note(_WORK_NOTE),
    }


async def _mark_read() -> None:
    """Mark the chat of the run: it read text that an outside sender can
    write. The idiom of ``home_tools._mark_read``. The tenant and the session
    come from the run binding only."""
    from acb_skills.egress import mark_untrusted_answer

    await mark_untrusted_answer()


@annotate(read_only=True, idempotent=True, open_world=False)
async def reach_preview() -> dict[str, Any]:
    """Read what Reach would send you on WhatsApp now: the brief and the interrupt. It sends nothing."""
    try:
        status = await _get(REACH_STATUS_PATH)
        if status.status == 404:
            return {"ok": True, "answer": _REACH_OFF}
        if not status.ok:
            return _fail(status)
        preview = await _get(REACH_PREVIEW_PATH)
    except _Refused as exc:
        return _refusal(str(exc))
    if preview.status == 404:
        return {"ok": True, "answer": _REACH_OFF}
    if not preview.ok:
        return _fail(preview)
    data = preview.data if isinstance(preview.data, dict) else None
    if data is None:
        return _refusal(_SHAPE)
    state = status.data if isinstance(status.data, dict) else {}
    texts = {key: _line(data[key], MAX_BRIEF_CHARS) for key in ("brief", "interrupts")
             if isinstance(data.get(key), str) and data[key].strip()}
    if texts:
        await _mark_read()
    block, frame = _block("REACH PREVIEW", texts)
    return {
        "ok": True,
        "preview": block,
        "has_brief": "brief" in texts,
        "has_interrupt": "interrupts" in texts,
        "available": state.get("available") is True,
        "linked": data.get("linked") is True,
        "window_open": data.get("window_open") is True,
        "held": data.get("held") is True,
        "note": frame.note(_REACH_NOTE) + " Reach sent nothing.",
    }


# ── The send of the brief (P-5b-5, task 3946, OD7 A) ────────────────────────


def _run_member() -> str:
    """The verified member of this run, or ``""``. The one rule is
    ``home_tools._member``: ``member_verified`` is ``"1"``, the member holds
    an address, and the member of the artifact context is the same. Never the
    memory id, which a shared room binds to ``room:<thread>``."""
    from acb_skills.home_tools import _member

    return _member()


def _sender() -> tuple[str, str | None]:
    """``(member, None)`` for the send, or ``("", the refusal)``. A shared
    room and a run with no verified member get their own sentence."""
    memory = _current_user_email()
    if memory and "@" not in memory:
        return "", _SHARED_ROOM
    member = _run_member()
    if not member:
        return "", _NO_VERIFIED
    return member, None


def _not_sent(message: str, **extra: Any) -> dict[str, Any]:
    return _refusal(message, sent=False, **extra)


async def _send_window(member: str) -> tuple[bool, dict[str, Any] | None]:
    """``(window_open, None)`` when the send may show its card, or
    ``(False, the answer)`` when it may not. It reads ``GET /reach/status``
    first: Reach off, or no link, stops here with no preview read. Then
    ``GET /reach/preview`` gives ``window_open``, which the status lacks.
    Raise :class:`_Refused` when the network fails."""
    status = await _get(REACH_STATUS_PATH, member=member)
    state = status.data if isinstance(status.data, dict) else None
    if status.status == 404 or (status.ok and state is not None
                                and state.get("available") is not True):
        return False, _not_sent(_REACH_OFF)
    if not status.ok:
        return False, {**_fail(status), "sent": False}
    if state is None:
        return False, _not_sent(_SHAPE)
    if state.get("linked") is not True:
        return False, _not_sent(_NOT_LINKED)
    preview = await _get(REACH_PREVIEW_PATH, member=member)
    if preview.status == 404:
        return False, _not_sent(_REACH_OFF)
    if preview.status == 503:
        return False, _not_sent(_DAY_UNREAD, status=503)
    if not preview.ok:
        return False, {**_fail(preview), "sent": False}
    data = preview.data if isinstance(preview.data, dict) else None
    if data is None or not isinstance(data.get("window_open"), bool):
        return False, _not_sent(_SHAPE)
    if data.get("linked") is not True:
        return False, _not_sent(_NOT_LINKED)
    return data["window_open"], None


def _send_card(window_open: bool) -> str:
    """The text of the card. Raise :class:`_Refused` when it does not fit."""
    rows = [("to", SEND_CARD_TO),
            ("as", SEND_CARD_OPEN if window_open else SEND_CARD_CLOSED),
            ("once", SEND_CARD_ONCE)]
    try:
        return str(card_body(SEND_CARD_NOTE, rows))
    except CardWontFit:
        raise _Refused(_WONT_FIT_SEND) from None


async def _decide_send(context: str, member: str) -> str:
    """Show ONE card, bound to *member*, and wait. The decision is
    ``approved``, ``revised`` (the member wrote in the chat), ``stopped`` or
    ``no_card``. The shape of ``org_admin_tools._decide``."""
    from acb_skills import ask_tools

    answer = await ask_tools.request_confirmation(
        SEND_CARD_TITLE, SEND_CARD_DETAIL, context, non_interactive_default="deny",
        close_on_steer=True, responder=member, fenced=True)
    if answer is True:
        return "approved"
    if answer is ask_tools.REVISED:
        return "revised"
    if not ask_tools.confirmation_channel_open():
        return "no_card"
    return "stopped"


def _send_result(reply: _Reply) -> dict[str, Any]:
    """The answer of ``POST /reach/brief`` in one plain sentence."""
    if reply.status == 404:
        return _not_sent(_REACH_OFF, status=404)
    if reply.status >= 500:
        return _refusal(_MAY_HAVE_GONE, sent=None, status=reply.status)
    if not reply.ok:
        return {**_fail(reply), "sent": False}
    data = reply.data if isinstance(reply.data, dict) else {}
    state, reason = data.get("state"), data.get("reason")
    if not isinstance(state, str) or not (reason is None or isinstance(reason, str)):
        return _refusal(_SEND_UNKNOWN, sent=None)
    said = SEND_RESULTS.get((state, reason))
    if said is None:
        return _refusal(_SEND_UNKNOWN, sent=None, state=_line(state, 40),
                        reason=_line(reason, 40) if reason else None)
    if state == "sent":
        return {"ok": True, "sent": True, "state": state, "reason": None, "answer": said}
    gone = None if (state, reason) == ("failed", "send_unsure") else False
    return {"ok": state == "skipped" and reason in ("nothing_to_say", "already_sent"),
            "sent": gone, "state": state, "reason": reason, "answer": said}


@annotate(read_only=False, destructive=False, idempotent=False, open_world=True)
async def reach_send_brief() -> dict[str, Any]:
    """Send your own brief of today to your own WhatsApp now. It shows one card first, each time, and names no person and no text."""
    from acb_skills import ask_tools

    member, refusal = _sender()
    if refusal is not None:
        return _not_sent(refusal)
    if not ask_tools.confirmation_channel_open():
        return _not_sent(_SEND_NO_CARD, decision="no_card")
    try:
        window, stop = await _send_window(member)
        if stop is not None:
            return stop
        context = _send_card(window)
    except _Refused as exc:
        return _not_sent(str(exc))
    decision = await _decide_send(context, member)
    if decision == "no_card":
        return _not_sent(_SEND_NO_CARD, decision=decision)
    if decision != "approved":
        return _not_sent(ask_tools.card_not_done_text(_NOT_SENT_ACT), decision=decision)
    try:
        reply = await _post(REACH_BRIEF_PATH, member)
    except _Refused as exc:
        return _not_sent(str(exc), decision=decision)
    except _MayHaveGone:
        return _refusal(_MAY_HAVE_GONE, sent=None, decision=decision)
    return {**_send_result(reply), "decision": decision}


#: Each tool of the ``people`` pack, in the order of its skill family.
PEOPLE_TOOLS = (people_directory, people_me, people_work, reach_preview, reach_send_brief)
