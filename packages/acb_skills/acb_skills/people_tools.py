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

**Self writes (P-5b-2, Metorite task 3943, owner answer OD1 B).** Five
tools change the member's OWN record, and no other:

* :func:`update_my_profile` sends ``PATCH /people/{id}`` (``profile.py``).
* :func:`set_my_skills` sends ``PUT /people/{id}/skills`` (``skills.py``).
* :func:`set_my_credentials` sends ``PUT /people/{id}/credentials``.
* :func:`add_my_absence` sends ``POST /people/{id}/absences`` (``absences.py``).
* :func:`remove_my_absence` sends ``DELETE /people/{id}/absences/{aid}``.

The id is the member's own. Each write reads it from ``GET /people/me`` as the
member, and no tool takes a person id, so no tool can address another person.
The route still checks the field class (``fields.authorize_write``), so the
server stays the judge. Each write shows ONE card with the old and the new
value, and it sends only on ``approved``. A decline, a timeout, a chat with no
card and a card that does not fit send nothing. No tool sends ``email``,
``status``, ``manager_id`` or ``employee_id`` (D-PC-2): the body holds only
the keys of :data:`SELF_PROFILE_FIELDS`.

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
import re
import textwrap
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import httpx
from acb_common.card_body import CardWontFit, card_body, one_line
from acb_common.paging import paged
from acb_common.settings import internal_auth_headers

from acb_skills.hidden_text import body_hidden_problem, hidden_problem, strip_hidden
from acb_skills.tool_annotations import annotate

__all__ = [
    "PEOPLE_TOOLS",
    "PEOPLE_WRITE_TOOLS",
    "SELF_PROFILE_FIELDS",
    "add_my_absence",
    "people_directory",
    "people_me",
    "people_work",
    "reach_preview",
    "remove_my_absence",
    "set_my_credentials",
    "set_my_skills",
    "update_my_profile",
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

# ── The self writes (P-5b-2, task 3943) ─────────────────────────────────────

#: The fields that :func:`update_my_profile` may send. They are the self class
#: of ``gateway/routes/people/fields.py`` (``SELF_FIELDS``), less ``avatar``
#: (``UPLOAD_ONLY_FIELDS``: a file, never a PATCH key) and less ``skills``
#: (:func:`set_my_skills` writes the skills through their own route, D-PC-6).
#: ``test_people_tools.py`` pins this set against ``SELF_FIELDS``.
SELF_PROFILE_FIELDS: tuple[str, ...] = (
    "preferred_name", "pronouns", "location", "timezone", "working_hours", "bio",
    "links", "languages", "interests", "domain", "resume_summary", "years_experience",
    "max_concurrent_tasks", "phone", "emergency_contact", "birthday",
)
#: The fields that no tool of this module ever sends (D-PC-2: ``email`` is the
#: self predicate. The others are claims of the organization).
NEVER_SENT: frozenset[str] = frozenset({"email", "status", "manager_id", "employee_id"})
#: The private tier (D-PC-3): only the member and an admin with
#: ``admin:members:manage`` read these. The card says so.
PRIVATE_PROFILE_FIELDS: frozenset[str] = frozenset({"phone", "emergency_contact", "birthday"})
#: The text fields of the profile, and the longest value of each kind.
_SHORT_TEXT = ("preferred_name", "pronouns", "location", "timezone", "domain", "phone")
_LONG_TEXT = ("bio", "resume_summary")
_TEXT_LISTS = ("languages", "interests")
_OBJECTS = ("working_hours", "links", "emergency_contact")
_NUMBERS = ("years_experience", "max_concurrent_tasks")
MAX_SHORT_CHARS = 200
MAX_LONG_CHARS = 2000
MAX_ITEM_CHARS = 100
MAX_ITEMS = 50
#: The longest JSON text of one object field.
MAX_OBJECT_CHARS = 2000
#: The highest number of a number field.
MAX_NUMBER = 1000
#: The most rows of one skills or credentials list (``person_skills.py``).
MAX_SKILL_ROWS = 200
MAX_CREDENTIAL_ROWS = 100
#: The kinds of an absence (``absences.py`` ``ABSENCE_KINDS``).
ABSENCE_KINDS: tuple[str, ...] = ("away", "holiday", "partial")
#: The longest note of an absence.
MAX_NOTE_CHARS = 300
#: The keys that a row of skills and of credentials may hold
#: (``skills.py`` ``SkillIn`` and ``CredentialIn``).
_SKILL_KEYS = ("skill", "level", "years", "last_used_year", "evidence")
_CREDENTIAL_KEYS = ("kind", "title", "issuer", "year_from", "year_to", "detail")
_MONTH_DAY = re.compile(r"\d\d-\d\d")

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
_W_NO_MEMBER = "There is no member for this run, so nothing was saved."
_W_SHARED = "A shared room cannot change a People record. Ask in your own chat."
_W_NONE = ("Name at least one field to change. Nothing was saved. The fields are: "
           + ", ".join(SELF_PROFILE_FIELDS) + ".")
_W_SAME = "Your record holds these values already. Nothing was saved."
_W_BAD = "{field}: {why} Nothing was saved."
_W_FENCE = "{field}: the text holds « or », and the card cannot show it as it is sent."
_W_NO_ROW = "Nothing was saved. {detail}"
_W_UNREACHED = ("The People routes could not be reached ({kind}). Nothing was saved. "
                "Try again.")
_W_MAYBE = ("The {act} may have been saved. Read your row again before you try again.")
_W_SHAPE = "The People route answered in a shape that this tool cannot read. Nothing was saved."
_W_NO_CARD = ("This chat shows no card, so nothing was saved. Make the change in the "
              "People app.")
_W_NOT_DONE = "Nothing was saved. The decision was {decision}."
_W_WONT_FIT = ("This change does not fit on one card, so nothing was saved. Make it in the "
               "People app, or change fewer values at a time.")
_W_BAD_ABSENCE_ID = "absence_id is the id of one of your absences. Nothing was removed."
_W_NOT_MINE = ("None of your absences has that id, so nothing was removed. Read your "
               "absences in the People app for the ids.")
_W_CARD_NOTE = "Your own People record. The email address never changes here."
_W_PRIVATE = ("Phone, emergency contact and birthday are private. Only you and an admin with "
              "the right admin:members:manage see them.")


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


# ── The self writes (P-5b-2, task 3943, owner answer OD1 B) ─────────────────


class _RouteRefused(_Refused):
    """A refusal of a People route before the card: it keeps the status."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(str(result.get("error") or ""))
        self.result = result


def _answer(exc: _Refused) -> dict[str, Any]:
    """The result of a refusal that a write raised."""
    if isinstance(exc, _RouteRefused):
        return exc.result
    return _refusal(str(exc))


def _member_headers() -> dict[str, str]:
    """The headers of a write. With no member, or in a shared room, refuse
    before any request, with the words of a write."""
    user = _current_user_email()
    if not user:
        raise _Refused(_W_NO_MEMBER)
    if "@" not in user:
        raise _Refused(_W_SHARED)
    return {**internal_auth_headers(), "X-User-Email": user}


async def _send(method: str, path: str, body: Any = None, act: str = "") -> _Reply:
    """One request as the member. *act* names a write, and a read gives none.
    Raise :class:`_Refused` with a sentence when the member is missing or the
    network fails."""
    headers = _member_headers()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                method, f"{_gateway_url()}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise _Refused(_W_MAYBE.format(act=act) if act
                       else _W_UNREACHED.format(kind=type(exc).__name__)) from None
    try:
        data = resp.json()
    except Exception:
        data = None
    return _Reply(resp.status_code, data)


def _route_words(reply: _Reply) -> str:
    """The sentence of the route, on one line. A 422 of FastAPI holds a list,
    so its first message gives the words."""
    raw = reply.data.get("detail") if isinstance(reply.data, dict) else None
    if isinstance(raw, str):
        return _line(raw, MAX_DETAIL_CHARS)
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        first = raw[0]
        where = ".".join(str(p) for p in first.get("loc") or () if p != "body")
        msg = str(first.get("msg") or "The value is not valid.")
        return _line(f"{where}: {msg}." if where else f"{msg}.", MAX_DETAIL_CHARS)
    return ""


def _write_fail(reply: _Reply, **extra: Any) -> dict[str, Any]:
    """The refusal of a People write: "Nothing was saved." and the sentence of
    the route, in plain words."""
    words = _route_words(reply)
    if reply.status == 401:
        return _refusal("Nothing was saved. Your session is not signed in.",
                        status=401, **extra)
    marker = "missing permission '"
    if reply.status == 403 and marker in words:
        right = words.split(marker, 1)[1].split("'", 1)[0] or "the right that this needs"
        return _refusal(f"Nothing was saved. You do not hold {right} in your organization. "
                        "Ask an admin of your organization.", status=403, **extra)
    if words:
        return _refusal(f"Nothing was saved. {words}", status=reply.status, **extra)
    return _refusal(f"Nothing was saved. The People route answered with status "
                    f"{reply.status}.", status=reply.status, **extra)


async def _own() -> tuple[str, dict[str, Any]]:
    """``(the member's own person id, the member's own row)``, read from
    ``GET /people/me`` as the member. Raise :class:`_Refused` with plain words
    when the directory has no row for the member."""
    reply = await _send("GET", ME_PATH)
    if not reply.ok:
        raise _RouteRefused(_write_fail(reply))
    data = reply.data if isinstance(reply.data, dict) else {}
    person = data.get("person")
    if data.get("state") != "resolved" or not isinstance(person, dict):
        words = _route_words(reply) or "The directory has no row for you."
        raise _Refused(_W_NO_ROW.format(detail=words))
    try:
        pid = str(uuid.UUID(str(person.get("id") or "")))
    except ValueError:
        raise _Refused(_W_SHAPE) from None
    return pid, person


# ── The values that a write sends ───────────────────────────────────────────


def _text_arg(field: str, raw: Any, limit: int) -> str | None:
    """*raw* as one line, ``""`` to clear the field. Raise :class:`_Refused`
    for a value that the card cannot show as it is sent."""
    if not isinstance(raw, str):
        raise _Refused(_W_BAD.format(field=field, why="Send text."))
    problem = hidden_problem(raw, field)
    if problem is not None:
        raise _Refused(f"{problem} Nothing was saved.")
    if "«" in raw or "»" in raw:
        raise _Refused(_W_FENCE.format(field=field) + " Nothing was saved.")
    text = one_line(raw)
    if len(text) > limit:
        raise _Refused(_W_BAD.format(field=field, why=f"Send at most {limit} characters."))
    return text


def _number_arg(field: str, raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= MAX_NUMBER:
        raise _Refused(_W_BAD.format(field=field,
                                     why=f"Send a whole number from 0 to {MAX_NUMBER}."))
    return raw


def _list_arg(field: str, raw: Any) -> list[str]:
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
        raise _Refused(_W_BAD.format(field=field,
                                     why=f"Send a list of at most {MAX_ITEMS} words."))
    out: list[str] = []
    for item in raw:
        text = _text_arg(field, item, MAX_ITEM_CHARS)
        if text and text not in out:
            out.append(text)
    return out


def _object_arg(field: str, raw: Any) -> dict[str, Any] | None:
    """*raw* as an object, or None for ``{}``, which clears the field."""
    if not isinstance(raw, dict):
        raise _Refused(_W_BAD.format(field=field, why="Send an object."))
    problem = body_hidden_problem(raw)
    if problem is not None:
        raise _Refused(f"{field}: {problem} Nothing was saved.")
    text = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), default=str)
    if "«" in text or "»" in text:
        raise _Refused(_W_FENCE.format(field=field) + " Nothing was saved.")
    if len(text) > MAX_OBJECT_CHARS:
        raise _Refused(_W_BAD.format(field=field,
                                     why=f"Send at most {MAX_OBJECT_CHARS} characters."))
    return json.loads(text) if raw else None


def _profile_value(field: str, raw: Any) -> Any:
    """The value of one profile field as the PATCH sends it."""
    if field in _SHORT_TEXT or field in _LONG_TEXT or field == "birthday":
        limit = MAX_LONG_CHARS if field in _LONG_TEXT else MAX_SHORT_CHARS
        text = _text_arg(field, raw, limit)
        if field == "birthday" and text and not _MONTH_DAY.fullmatch(text):
            raise _Refused(_W_BAD.format(field=field, why="Send MM-DD, with no year."))
        return text or None
    if field in _TEXT_LISTS:
        return _list_arg(field, raw)
    if field in _OBJECTS:
        return _object_arg(field, raw)
    return _number_arg(field, raw)


def _same(old: Any, new: Any) -> bool:
    """True when the row holds *new* already. No value equals an empty one."""
    if _empty(old) and _empty(new):
        return True
    return old == new


# ── The card ────────────────────────────────────────────────────────────────


def _shown(value: Any) -> str:
    """One value on a card, on one line, between « and ». An old value is
    cut, and a new value fits whole or the card does not show."""
    if _empty(value):
        return "(empty)"
    if isinstance(value, (dict, list)) and not (
            isinstance(value, list) and all(isinstance(v, str) for v in value)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    elif isinstance(value, list):
        text = ", ".join(value)
    else:
        text = str(value)
    text = one_line(strip_hidden(text)).replace("«", "").replace("»", "")
    return f"«{text}»"


def _old(value: Any) -> str:
    """A value that the row holds now, as :func:`_shown` gives it, cut to one
    short line. A long word is cut too, so an old value never vanishes."""
    shown = _shown(value)
    if len(shown) <= MAX_FIELD_CHARS:
        return shown
    first = textwrap.wrap(shown.strip("«»"), MAX_FIELD_CHARS - 5)
    return f"«{first[0] if first else ''}...»"


async def _decide(title: str, detail: str, note: str,
                  rows: list[tuple[str, str]]) -> str:
    """Show ONE card and wait, as ``org_admin_tools._decide`` does. The
    decision is ``approved``, ``revised``, ``stopped`` or ``no_card``. Raise
    :class:`_Refused` when the card does not fit."""
    from acb_skills import ask_tools

    try:
        context = str(card_body(note, rows))
    except CardWontFit:
        raise _Refused(_W_WONT_FIT) from None
    # The run's own member may answer, and no other (PZ-32). The member is the
    # run's, never an input.
    answer = await ask_tools.request_confirmation(
        title, detail, context, non_interactive_default="deny", close_on_steer=True,
        responder=_current_user_email() or None, fenced=True)
    if answer is True:
        return "approved"
    if answer is ask_tools.REVISED:
        return "revised"
    if not ask_tools.confirmation_channel_open():
        return "no_card"
    return "stopped"


def _not_saved(decision: str) -> dict[str, Any]:
    if decision == "no_card":
        return _refusal(_W_NO_CARD, decision=decision)
    return _refusal(_W_NOT_DONE.format(decision=decision), decision=decision)


# ── The tools that write ────────────────────────────────────────────────────


@annotate(read_only=False, idempotent=False, open_world=False)
async def update_my_profile(
    preferred_name: str | None = None, pronouns: str | None = None,
    location: str | None = None, timezone: str | None = None,
    working_hours: dict[str, Any] | None = None, bio: str | None = None,
    links: dict[str, Any] | None = None, languages: list[str] | None = None,
    interests: list[str] | None = None, domain: str | None = None,
    resume_summary: str | None = None, years_experience: int | None = None,
    max_concurrent_tasks: int | None = None, phone: str | None = None,
    emergency_contact: dict[str, Any] | None = None, birthday: str | None = None,
) -> dict[str, Any]:
    """Change fields of your OWN People record after one card. Send only the fields to change: "" or {} or [] clears one. It never changes your email, title, manager or status."""
    given = dict(locals())
    try:
        _member_headers()
        wanted = {field: _profile_value(field, given[field])
                  for field in SELF_PROFILE_FIELDS if given.get(field) is not None}
        if not wanted:
            return _refusal(_W_NONE)
        pid, person = await _own()
        changes = {f: v for f, v in wanted.items() if not _same(person.get(f), v)}
        if not changes:
            return {"ok": True, "saved": [], "note": _W_SAME}
        rows = [(f, f"{f}: {_old(person.get(f))} to {_shown(v)}") for f, v in changes.items()]
        note = _W_CARD_NOTE
        if PRIVATE_PROFILE_FIELDS & set(changes):
            note += " " + _W_PRIVATE
        decision = await _decide(
            "Save these changes to your People record?",
            f"{len(changes)} field{'s' if len(changes) != 1 else ''}: {', '.join(changes)}",
            note + " Approve to save. Reject to keep the record as it is.", rows)
        if decision != "approved":
            return _not_saved(decision)
        body = {f: v for f, v in changes.items() if f in SELF_PROFILE_FIELDS
                and f not in NEVER_SENT}
        sent = await _send("PATCH", f"/people/{pid}", body, act="change of your record")
    except _Refused as exc:
        return _answer(exc)
    if not sent.ok:
        return _write_fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "saved": sorted(body),
            "note": "Your People record is saved."}


def _skill_rows(raw: Any) -> list[dict[str, Any]]:
    """The skills as ``PUT /people/{id}/skills`` takes them. A bare word is a
    skill with no level. Raise :class:`_Refused` for a row that the card
    cannot show as it is sent."""
    if not isinstance(raw, list) or len(raw) > MAX_SKILL_ROWS:
        raise _Refused(_W_BAD.format(field="skills",
                                     why=f"Send a list of at most {MAX_SKILL_ROWS} skills."))
    out: list[dict[str, Any]] = []
    for n, item in enumerate(raw, start=1):
        row = {"skill": item} if isinstance(item, str) else item
        if not isinstance(row, dict) or set(row) - set(_SKILL_KEYS):
            raise _Refused(_W_BAD.format(
                field=f"skills #{n}", why="Send a word, or an object with the keys "
                + ", ".join(_SKILL_KEYS) + "."))
        clean: dict[str, Any] = {}
        for key in _SKILL_KEYS:
            value = row.get(key)
            if key in ("skill", "level", "evidence"):
                text = _text_arg(f"skills #{n} {key}", value, MAX_ITEM_CHARS) \
                    if value is not None else ""
                clean[key] = text or None
            elif value is None or (isinstance(value, (int, float))
                                   and not isinstance(value, bool)):
                clean[key] = value
            else:
                raise _Refused(_W_BAD.format(field=f"skills #{n} {key}", why="Send a number."))
        if not clean["skill"]:
            raise _Refused(_W_BAD.format(field=f"skills #{n}", why="The skill has no name."))
        clean["evidence"] = clean["evidence"] or "manual"
        out.append(clean)
    return out


def _skill_line(row: dict[str, Any]) -> str:
    parts = [str(row.get("level") or "no level")]
    if row.get("years") is not None:
        parts.append(f"{row['years']} years")
    if row.get("last_used_year") is not None:
        parts.append(f"last used {row['last_used_year']}")
    parts.append(f"evidence {row.get('evidence') or 'manual'}")
    return f"- {_shown(row.get('skill'))} ({_line(', '.join(parts), 120)})"


def _credential_rows(raw: Any) -> list[dict[str, Any]]:
    """The credentials as ``PUT /people/{id}/credentials`` takes them."""
    if not isinstance(raw, list) or len(raw) > MAX_CREDENTIAL_ROWS:
        raise _Refused(_W_BAD.format(
            field="credentials", why=f"Send a list of at most {MAX_CREDENTIAL_ROWS} rows."))
    out: list[dict[str, Any]] = []
    for n, row in enumerate(raw, start=1):
        if not isinstance(row, dict) or set(row) - set(_CREDENTIAL_KEYS):
            raise _Refused(_W_BAD.format(
                field=f"credentials #{n}", why="Send an object with the keys "
                + ", ".join(_CREDENTIAL_KEYS) + "."))
        clean: dict[str, Any] = {}
        for key in _CREDENTIAL_KEYS:
            value = row.get(key)
            if key in ("year_from", "year_to"):
                if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                    raise _Refused(_W_BAD.format(field=f"credentials #{n} {key}",
                                                 why="Send a year."))
                clean[key] = value
                continue
            limit = MAX_NOTE_CHARS if key == "detail" else MAX_SHORT_CHARS
            text = _text_arg(f"credentials #{n} {key}", value, limit) \
                if value is not None else ""
            clean[key] = text or None
        if not clean["kind"] or not clean["title"]:
            raise _Refused(_W_BAD.format(field=f"credentials #{n}",
                                         why="Each row needs a kind and a title."))
        out.append(clean)
    return out


def _credential_line(row: dict[str, Any]) -> str:
    text = f"- {_line(row.get('kind') or 'no kind', 40)}: {_shown(row.get('title'))}"
    if row.get("issuer"):
        text += f" by {_shown(row['issuer'])}"
    years = [str(row[k]) for k in ("year_from", "year_to") if row.get(k) is not None]
    if years:
        text += f", {' to '.join(years)}"
    if row.get("detail"):
        text += f", {_shown(row['detail'])}"
    return text


def _list_card(label: str, now: list[str], after: list[str]) -> list[tuple[str, str]]:
    """The card rows of a list that a PUT replaces whole: each row now, then
    each row after."""
    rows = [("now", f"{label} now ({len(now)}):")]
    rows += [(f"now{n}", line) for n, line in enumerate(now)] or [("now0", "- none")]
    rows.append(("after", f"{label} after ({len(after)}):"))
    rows += [(f"after{n}", line) for n, line in enumerate(after)] or [("after0", "- none")]
    return rows


async def _capability(pid: str) -> dict[str, Any]:
    """The member's own skills and credentials (``GET /people/{id}/skills``)."""
    reply = await _get(f"/people/{pid}/skills")
    if not reply.ok:
        raise _RouteRefused(_write_fail(reply))
    if not isinstance(reply.data, dict):
        raise _Refused(_W_SHAPE)
    return reply.data


def _now_rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = data.get(key)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def set_my_skills(skills: list[Any]) -> dict[str, Any]:
    """Replace the whole list of skills on your OWN People record after one card. Each item is a word, or {skill, level, years, last_used_year, evidence}. A skill left out is removed."""
    try:
        _member_headers()
        after = _skill_rows(skills)
        pid, _person = await _own()
        now = _now_rows(await _capability(pid), "skills")
        keys = ("skill", "level", "years", "last_used_year", "evidence")
        if [tuple(r.get(k) for k in keys) for r in now] == \
                [tuple(r.get(k) for k in keys) for r in after]:
            return {"ok": True, "count": len(now), "note": _W_SAME}
        decision = await _decide(
            "Replace your skills?",
            f"{len(now)} skills now, {len(after)} after. The new list replaces the whole list.",
            _W_CARD_NOTE + " Approve to save the list after. A skill that is not in it is "
            "removed. Reject to keep the skills as they are.",
            _list_card("Skills", [_skill_line(r) for r in now],
                       [_skill_line(r) for r in after]))
        if decision != "approved":
            return _not_saved(decision)
        sent = await _send("PUT", f"/people/{pid}/skills", {"rows": after},
                           act="change of your skills")
    except _Refused as exc:
        return _answer(exc)
    if not sent.ok:
        return _write_fail(sent, decision=decision)
    saved = _now_rows(sent.data if isinstance(sent.data, dict) else {}, "skills")
    return {"ok": True, "decision": decision, "count": len(saved),
            "note": "Your skills are saved."}


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def set_my_credentials(credentials: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the whole list of your professional credentials (education, certification, prior_role) on your OWN People record after one card. A row left out is removed."""
    try:
        _member_headers()
        after = _credential_rows(credentials)
        pid, _person = await _own()
        now = _now_rows(await _capability(pid), "credentials")
        if [tuple(r.get(k) for k in _CREDENTIAL_KEYS) for r in now] == \
                [tuple(r.get(k) for k in _CREDENTIAL_KEYS) for r in after]:
            return {"ok": True, "count": len(now), "note": _W_SAME}
        decision = await _decide(
            "Replace your credentials?",
            f"{len(now)} credentials now, {len(after)} after. The new list replaces the "
            "whole list.",
            _W_CARD_NOTE + " Approve to save the list after. A row that is not in it is "
            "removed. Reject to keep the credentials as they are.",
            _list_card("Credentials", [_credential_line(r) for r in now],
                       [_credential_line(r) for r in after]))
        if decision != "approved":
            return _not_saved(decision)
        sent = await _send("PUT", f"/people/{pid}/credentials", {"rows": after},
                           act="change of your credentials")
    except _Refused as exc:
        return _answer(exc)
    if not sent.ok:
        return _write_fail(sent, decision=decision)
    saved = _now_rows(sent.data if isinstance(sent.data, dict) else {}, "credentials")
    return {"ok": True, "decision": decision, "count": len(saved),
            "note": "Your credentials are saved."}


def _day(field: str, raw: Any) -> date:
    if not isinstance(raw, str):
        raise _Refused(_W_BAD.format(field=field, why="Send a date as YYYY-MM-DD."))
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise _Refused(_W_BAD.format(field=field, why="Send a date as YYYY-MM-DD.")) from None


def _absence_lines(row: dict[str, Any]) -> list[tuple[str, str]]:
    lines = [("dates", f"Dates: {_line(row.get('starts_on'), 20)} to "
                       f"{_line(row.get('ends_on'), 20)}"),
             ("kind", f"Kind: {_line(row.get('kind') or 'away', 20)}")]
    if row.get("hours_per_day") is not None:
        lines.append(("hours", f"Hours each day: {_line(row['hours_per_day'], 20)}"))
    lines.append(("note", f"Note: {_shown(row.get('note'))}"))
    return lines


@annotate(read_only=False, idempotent=False, open_world=False)
async def add_my_absence(
    starts_on: str, ends_on: str, kind: Literal["away", "holiday", "partial"] = "away",
    hours_per_day: float | None = None, note: str = "",
) -> dict[str, Any]:
    """Record one absence of your OWN after one card: the first and the last day (YYYY-MM-DD), the kind, and hours_per_day for a partial day only."""
    try:
        _member_headers()
        starts, ends = _day("starts_on", starts_on), _day("ends_on", ends_on)
        if ends < starts:
            raise _Refused(_W_BAD.format(field="ends_on",
                                         why="An absence cannot end before it starts."))
        if kind not in ABSENCE_KINDS:
            raise _Refused(_W_BAD.format(field="kind", why="Send " + ", ".join(ABSENCE_KINDS)
                                         + "."))
        if hours_per_day is not None and (
                isinstance(hours_per_day, bool) or not isinstance(hours_per_day, (int, float))
                or not 0 < hours_per_day <= 24):
            raise _Refused(_W_BAD.format(field="hours_per_day",
                                         why="Send a number above 0 and at most 24."))
        if hours_per_day is not None and kind != "partial":
            raise _Refused(_W_BAD.format(field="hours_per_day",
                                         why="Send it only for a partial absence."))
        text = _text_arg("note", note if note is not None else "", MAX_NOTE_CHARS) or None
        body = {"starts_on": starts.isoformat(), "ends_on": ends.isoformat(), "kind": kind,
                "hours_per_day": hours_per_day, "note": text}
        pid, _person = await _own()
        decision = await _decide(
            "Record this absence?", f"{kind}, {body['starts_on']} to {body['ends_on']}",
            _W_CARD_NOTE + " Approve to record this absence. Reject to record nothing.",
            _absence_lines(body))
        if decision != "approved":
            return _not_saved(decision)
        sent = await _send("POST", f"/people/{pid}/absences", body, act="absence")
    except _Refused as exc:
        return _answer(exc)
    if not sent.ok:
        return _write_fail(sent, decision=decision)
    made = sent.data if isinstance(sent.data, dict) else {}
    return {"ok": True, "decision": decision, "absence_id": _line(made.get("id") or "", 40),
            "note": "Your absence is recorded."}


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def remove_my_absence(absence_id: str) -> dict[str, Any]:
    """Remove one absence of your OWN, by its id, after one card. An id of another person's absence is refused."""
    try:
        _member_headers()
        try:
            aid = str(uuid.UUID(str(absence_id or "").strip()))
        except ValueError:
            return _refusal(_W_BAD_ABSENCE_ID)
        pid, _person = await _own()
        reply = await _get(f"/people/{pid}/absences")
        if not reply.ok:
            return _write_fail(reply)
        rows = reply.data.get("rows") if isinstance(reply.data, dict) else None
        if not isinstance(rows, list):
            return _refusal(_W_SHAPE)
        row = next((r for r in rows if isinstance(r, dict) and str(r.get("id")) == aid), None)
        if row is None:
            return _refusal(_W_NOT_MINE, status=404)
        decision = await _decide(
            "Remove this absence?",
            f"{_line(row.get('kind') or 'away', 20)}, {_line(row.get('starts_on'), 20)} to "
            f"{_line(row.get('ends_on'), 20)}",
            _W_CARD_NOTE + " Approve to remove this absence. Reject to keep it.",
            _absence_lines(row))
        if decision != "approved":
            return _not_saved(decision)
        sent = await _send("DELETE", f"/people/{pid}/absences/{aid}", act="removal")
    except _Refused as exc:
        return _answer(exc)
    if not sent.ok:
        return _write_fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "absence_id": aid,
            "note": "The absence is removed."}


#: The five self writes of P-5b-2 (task 3943).
PEOPLE_WRITE_TOOLS = (update_my_profile, set_my_skills, set_my_credentials, add_my_absence,
                      remove_my_absence)

#: Each tool of the ``people`` pack, in the order of its skill family.
PEOPLE_TOOLS = (people_directory, people_me, people_work, reach_preview, *PEOPLE_WRITE_TOOLS)
