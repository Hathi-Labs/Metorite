"""The chat tools of the ``people`` pack (P-5b-1, Metorite task 3114).

Spec: ``project-docs/specs/chat_parity.md`` section 6.4 (the P-5b slices) and
``project-docs/specs/people_center_app.md`` sections 6.5 and 6.8. Four reads
let a member do from the chat what the People app and the Reach preview show:

* :func:`people_directory` reads the directory (``GET /people/``), with the
  filters of the page.
* :func:`people_me` reads the member's own row (``GET /people/me``).
* :func:`people_work` reads the open work of the member
  (``GET /people/me/work``) or of one person (``GET /people/{id}/work``).
* :func:`reach_preview` reads what Reach would send the member now
  (``GET /reach/status``, then ``GET /reach/preview``).

**The server decides each tier (D-PC-10).** Each tool calls the gateway over
HTTP as the member of the run: the internal bearer and ``X-User-Email`` from
the run binding, the idiom of ``org_admin_tools``. No tool takes an org, a
caller or an address argument (R5). The routes project the HR fields and the
private fields by the right of the viewer. A tool never widens that answer
and never filters a field out of it. It leaves out a field with no value
only, and the answer says so.

**Reads only.** No tool writes, shows a card or sends ``email``. A person
write needs a card, and a later slice builds it (``chat_parity.md`` §6.4).

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
from acb_common.card_body import one_line
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


async def _get(path: str, params: dict[str, Any] | None = None) -> _Reply:
    """One GET as the member. Raise :class:`_Refused` with a sentence when the
    member is missing or the network fails."""
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


#: Each tool of the ``people`` pack, in the order of its skill family.
PEOPLE_TOOLS = (people_directory, people_me, people_work, reach_preview)
