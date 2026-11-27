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
tools change the member's OWN record, and no other. Each sends a self route of
``selfservice.py`` (D-PC-15), which takes no person id and has no feature gate:

* :func:`update_my_profile` sends ``PATCH /people/me``.
* :func:`set_my_skills` sends ``PUT /people/me/skills``.
* :func:`set_my_credentials` sends ``PUT /people/me/credentials``.
* :func:`add_my_absence` sends ``POST /people/me/absences``.
* :func:`remove_my_absence` sends ``DELETE /people/me/absences/{aid}``.

The server finds the row of the caller when it writes, so no write can land on
another row, and a member without ``feature:people`` can still change their
own record. The route checks the field class (``fields.authorize_write``).
Each write shows ONE card with the exact change, and it sends only on
``approved``. A decline, a timeout, a chat with no card and a card that does
not fit send nothing. The profile write and the two list writes read the row
again after Approve, and they send nothing when it changed. The object fields
merge: the keys that the member gives go over the stored object, and each
other stored key stays. No tool sends ``email``, ``status``,
``manager_id`` or ``employee_id`` (D-PC-2): the body holds only the keys of
:data:`SELF_PROFILE_FIELDS`.

**The company work schedule (P-5b-4, task 3945).** :func:`set_work_schedule`
is not a self write. It edits the work schedule of the company
(``PUT /people/schedule``), which is the org policy and not the record of a
person. It reads ``GET /auth/me`` first, so a member without
``admin:members:manage`` gets plain words and no card. It then asks the route
for a ``dry_run``, and ONE card shows the impact that the server computed: who
moves, and the hours of the week before and after. Only ``approved`` sends,
and it sends ONE request. The tool reads the impact again after the answer,
and it sends nothing when that impact moved while the card was open.

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
    "set_work_schedule",
    "update_my_profile",
]

#: The gateway routes. A package must not import the gateway, so the paths
#: are text here. ``test_chat_parity.py`` reads them from this source.
#: The directory list is ``GET /people/`` (``routes/people/directory.py``).
DIRECTORY_PATH = "/people/"
#: The member's own row, and the member's own work (``selfservice.py``).
ME_PATH = "/people/me"
MY_WORK_PATH = "/people/me/work"
#: The self routes of the five writes (``selfservice.py``, D-PC-15). No path
#: holds a person id: the server finds the row of the caller when it writes,
#: so no write can land on another row, and no write needs ``feature:people``.
MY_ROW_PATH = "/people/me"
MY_SKILLS_PATH = "/people/me/skills"
MY_CREDENTIALS_PATH = "/people/me/credentials"
MY_ABSENCES_PATH = "/people/me/absences"
#: The work schedule of the company: read and replace (``schedule.py``).
SCHEDULE_PATH = "/people/schedule"
#: The read of the member's own resolved rights (``routes/admin/me.py``).
AUTH_ME_PATH = "/auth/me"
#: The right of ``PUT /people/schedule`` (``PEOPLE_WRITE_PERMISSION``).
MANAGE_RIGHT = "admin:members:manage"
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
#: The most keys of one schedule change, and the longest JSON of one.
MAX_CHANGE_KEYS = 12
MAX_CHANGE_CHARS = 3000
#: The most people that the card names as an example. The route gives ten.
MAX_CARD_EXAMPLES = 10

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
#: The keys that a write may set in each object field. ``working_hours`` is
#: the person's override of the work schedule: ``people_center_app.md`` §3.4a
#: (any subset of days, start, end, hours_per_day, shift, fraction), and
#: ``work_schedule.effective_schedule``, which reads timezone too. ``links``
#: and ``emergency_contact`` are the keys of the People app (``ProfilePanels.tsx``
#: ``FieldInput``, kinds ``links`` and ``contact``). A write MERGES the keys
#: that the member gives over the stored object, and keeps each other key.
OBJECT_KEYS: dict[str, tuple[str, ...]] = {
    "working_hours": ("days", "start", "end", "hours_per_day", "shift", "fraction",
                      "timezone"),
    "links": ("github", "linkedin", "website"),
    "emergency_contact": ("name", "relation", "phone"),
}
#: The words of the shape of each object field, for a refusal and the docs.
#: The shapes are the readers of ``work_schedule.py``: ``_clean_days``,
#: ``_clean_time``, ``_clean_hours``, ``_clean_str`` and ``_clean_fraction``.
OBJECT_SHAPES: dict[str, str] = {
    "working_hours": ("days is a list of weekday numbers from 1 (Monday) to 7 (Sunday). "
                      "start and end are times as HH:MM, 24-hour. hours_per_day is a "
                      "number above 0 and at most 24. fraction is a number from 0 to 1, "
                      "such as 0.5 for half time. shift is the name of a shift of the "
                      "organization, and timezone is a time zone such as Asia/Kolkata. "
                      "null clears one key."),
    "links": "github, linkedin and website are each one line of text. null clears one key.",
    "emergency_contact": ("name, relation and phone are each one line of text. null clears "
                          "one key."),
}
#: A time of ``working_hours``, as ``work_schedule._TIME_RE`` reads it.
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_NUMBERS = ("years_experience", "max_concurrent_tasks")
MAX_SHORT_CHARS = 200
MAX_LONG_CHARS = 2000
MAX_ITEM_CHARS = 100
MAX_ITEMS = 50
#: The highest number of a number field.
MAX_NUMBER = 1000
#: The most rows of one skills or credentials list (``person_skills.py``).
MAX_SKILL_ROWS = 200
MAX_CREDENTIAL_ROWS = 100
#: The vocabularies of a skill and a credential (``gateway/person_skills.py``
#: ``SKILL_LEVELS``, ``EVIDENCE`` and ``CREDENTIAL_KINDS``). A package must not
#: import the gateway, so they are text here. ``test_people_tools.py`` pins them.
SKILL_LEVELS: tuple[str, ...] = ("learning", "working", "proficient", "expert")
EVIDENCE: tuple[str, ...] = ("manual", "resume", "observed")
CREDENTIAL_KINDS: tuple[str, ...] = ("education", "certification", "prior_role")
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
_W_RECORD_MOVED = ("Your record changed while the card waited. Nothing was saved. "
                   "Ask again.")
_W_MOVED = ("Your {what} changed while the card waited. Nothing was saved. Ask again.")
_W_BAD_ABSENCE_ID = "absence_id is the id of one of your absences. Nothing was removed."
_W_NOT_MINE = ("None of your absences has that id, so nothing was removed. Read your "
               "absences in the People app for the ids.")
_W_CARD_NOTE = "Your own People record. The email address never changes here."
_W_PRIVATE = ("Phone, emergency contact and birthday are private. Only you and an admin with "
              "the right admin:members:manage see them.")


# ── The gateway, as the member (the idiom of ``org_admin_tools``) ───────────


_BAD_CHANGES = ("changes is an object with the fields of the work schedule to change, such as "
                "working_days, hours_per_day, start and end. At most {n} fields. "
                "Nothing was saved.")
_NO_MANAGE = ("You do not hold the right to change the work schedule of your organization "
              "(admin:members:manage), so no card shows and nothing was saved. Ask an admin "
              "who holds that right.")
_RIGHTS_UNREAD = "Your rights could not be read, so nothing was saved. Try again."
_SCHEDULE_UNREAD = "The work schedule could not be read, so nothing was saved. Try again."
_SAME_SCHEDULE = "The work schedule is as you ask already. Nothing was saved."
_SCHEDULE_CHANGED = ("The impact of this change moved while the card waited. Nothing was "
                     "saved. Ask again, to see the new impact.")
_SCHEDULE_NOTE = ("Approve to replace the work schedule of the whole company. Every "
                  "contracted hours figure and load bar moves at once. Reject to leave the "
                  "schedule as it is.")


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


async def _own_row() -> dict[str, Any]:
    """The member's own row, read from ``GET /people/me`` as the member, for
    the old values of the card only. No write takes an id from it: each write
    sends a ``/people/me`` route, and the server finds the row of the caller
    when it writes (``selfservice._my_row``). Raise :class:`_Refused` with
    plain words when the directory has no row for the member."""
    reply = await _send("GET", ME_PATH)
    if not reply.ok:
        raise _RouteRefused(_write_fail(reply))
    data = reply.data if isinstance(reply.data, dict) else {}
    person = data.get("person")
    if data.get("state") != "resolved" or not isinstance(person, dict):
        words = _route_words(reply) or "The directory has no row for you."
        raise _Refused(_W_NO_ROW.format(detail=words))
    return person


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


class _Patch(dict):
    """The keys of one object field that the member gives. A value None
    clears its key. :func:`_merged` lays it over the stored object."""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _object_arg(field: str, raw: Any) -> _Patch | None:
    """The keys that *raw* changes, as a :class:`_Patch`, or None for ``{}``,
    which clears the whole field. Each text is one line, so the value sent is
    the value that the card shows. An empty text clears its key, as the
    People app does. Raise :class:`_Refused` for an unknown key or a bad
    shape, with words that name the allowed keys."""
    allowed = OBJECT_KEYS[field]
    why = f"The keys are {', '.join(allowed)}. {OBJECT_SHAPES[field]}"
    if not isinstance(raw, dict):
        raise _Refused(_W_BAD.format(field=field, why=f"Send an object. {why}"))
    if not raw:
        return None
    unknown = [str(k) for k in raw if k not in allowed]
    if unknown:
        raise _Refused(_W_BAD.format(
            field=field, why=f"This tool does not set the key {', '.join(unknown)}. {why}"))
    problem = body_hidden_problem(raw)
    if problem is not None:
        raise _Refused(f"{field}: {problem} Nothing was saved.")
    return _Patch({key: _object_value(field, key, raw[key], why)
                   for key in allowed if key in raw})


def _object_value(field: str, key: str, value: Any, why: str) -> Any:
    """One key of an object field as the write sends it, or None to clear it."""
    bad = _Refused(_W_BAD.format(field=f"{field}.{key}", why=why))
    if value is None:
        return None
    if key == "days":
        if not isinstance(value, list) or not value or any(
                isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 7
                for d in value):
            raise bad
        return sorted(set(value))
    if key == "hours_per_day":
        if not _number(value) or not 0 < value <= 24:
            raise bad
        return value
    if key == "fraction":
        if not _number(value) or not 0 <= value <= 1:
            raise bad
        return value
    if not isinstance(value, str):
        raise bad
    text = _text_arg(f"{field}.{key}", value, MAX_SHORT_CHARS)
    if text and key in ("start", "end") and not _TIME.fullmatch(text):
        raise bad
    return text or None


def _stored_object(value: Any) -> dict[str, Any]:
    """A stored object field as a dict. The row can hold it as JSON text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _merged(stored: Any, patch: _Patch) -> dict[str, Any] | None:
    """*patch* laid over the stored object. Each stored key that the member
    did not name stays. A key given as None leaves. None when nothing stays."""
    out = _stored_object(stored)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out or None


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
    if isinstance(value, (dict, list)):
        # Exact JSON of the value that the write sends, in the same key order.
        # ensure_ascii escapes each non-ASCII and control character (a hidden
        # character, «, », a new line), and a run of spaces stays as it is. So
        # the text between the « » marks has no raw mark, holds one line, and
        # json.loads gives back the value sent, byte for byte. A stored value
        # of a merged object is never cleaned here, so it shows as it is.
        return "«" + json.dumps(value, ensure_ascii=True, separators=(",", ":"),
                                default=str) + "»"
    text = one_line(strip_hidden(str(value))).replace("«", "").replace("»", "")
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
    """Change fields of your OWN People record after one card. Send only the fields to change: "" or [] clears one. For working_hours, links and emergency_contact send only the keys to change: the tool keeps each other stored key, null clears one key, and {} clears the whole field. working_hours keys: days (weekday numbers 1 to 7), start and end (HH:MM), hours_per_day (above 0, at most 24), fraction (0 to 1), shift (a shift name), timezone. links keys: github, linkedin, website. emergency_contact keys: name, relation, phone. It never changes your email, title, manager or status."""
    given = dict(locals())
    try:
        _member_headers()
        wanted = {field: _profile_value(field, given[field])
                  for field in SELF_PROFILE_FIELDS if given.get(field) is not None}
        if not wanted:
            return _refusal(_W_NONE)
        person = await _own_row()
        wanted = {f: _merged(person.get(f), v) if isinstance(v, _Patch) else v
                  for f, v in wanted.items()}
        changes = {f: v for f, v in wanted.items() if not _same(person.get(f), v)}
        if not changes:
            return {"ok": True, "saved": [], "note": _W_SAME}
        rows = [(f, f"{f}: {_old(person.get(f))} to {_shown(v)}") for f, v in changes.items()]
        note = _W_CARD_NOTE
        if PRIVATE_PROFILE_FIELDS & set(changes):
            note += " " + _W_PRIVATE
        decision = await _decide(
            "Save these changes to your People record?",
            f"{_count(len(changes), 'field')}: {', '.join(changes)}",
            note + " Approve to save. Reject to keep the record as it is.", rows)
        if decision != "approved":
            return _not_saved(decision)
        again = await _own_row()
        if any(not _same(again.get(f), person.get(f)) for f in changes):
            return _refusal(_W_RECORD_MOVED, decision=decision)
        body = {f: v for f, v in changes.items() if f in SELF_PROFILE_FIELDS
                and f not in NEVER_SENT}
        sent = await _send("PATCH", MY_ROW_PATH, body, act="change of your record")
    except _Refused as exc:
        return _answer(exc)
    if not sent.ok:
        return _write_fail(sent, decision=decision)
    note = "Your People record is saved."
    shift = (wanted.get("working_hours") or {}).get("shift") \
        if isinstance(given.get("working_hours"), dict) \
        and given["working_hours"].get("shift") else None
    if shift and "working_hours" in body:
        note += await _shift_note(str(shift))
    return {"ok": True, "decision": decision, "saved": sorted(body), "note": note}


async def _shift_note(shift: str) -> str:
    """One sentence when the shift name took no effect, else "".

    ``work_schedule._find_shift`` ignores a name that matches no shift of the
    org policy, and a member cannot read the policy (``GET /people/schedule``
    needs ``feature:people``), so the tool cannot refuse the name before the
    card. After the save, it reads the effective schedule of ``GET
    /people/me`` (``person.schedule.shift``). A failed read adds nothing."""
    try:
        person = await _own_row()
    except _Refused:
        return ""
    schedule = person.get("schedule")
    if not isinstance(schedule, dict):
        return ""
    took = str(schedule.get("shift") or "").strip().lower()
    if took == shift.strip().lower():
        return ""
    return (f" The shift name {_shown(shift)} matches no shift of your organisation, so "
            "your schedule did not change. Ask an admin for the shift names.")


def _skill_rows(raw: Any) -> list[dict[str, Any]]:
    """The skills as the member gave them, each with only the keys given. A
    bare word is a name alone, and :func:`_merge_skills` fills the rest. Raise :class:`_Refused` for a row that the card
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
            if key not in row and key != "skill":
                continue  # Not given: :func:`_merge_skills` keeps the value of the row.
            value = row.get(key)
            where = f"skills #{n} {key}"
            if key in ("skill", "level", "evidence"):
                text = _text_arg(where, value, MAX_ITEM_CHARS) if value is not None else ""
                vocabulary = SKILL_LEVELS if key == "level" else EVIDENCE
                if text and key != "skill" and text not in vocabulary:
                    raise _Refused(_W_BAD.format(
                        field=where, why="Send " + ", ".join(vocabulary) + "."))
                clean[key] = text or None
            elif value is None:
                clean[key] = None
            elif (key == "years" and _number(value) and 0 <= value <= 60) or (
                    key == "last_used_year" and isinstance(value, int)
                    and not isinstance(value, bool) and 1970 <= value <= 2100):
                clean[key] = value
            else:
                raise _Refused(_W_BAD.format(field=where, why=(
                    "Send a number from 0 to 60." if key == "years"
                    else "Send a year from 1970 to 2100.")))
        if not clean["skill"]:
            raise _Refused(_W_BAD.format(field=f"skills #{n}", why="The skill has no name."))
        out.append(clean)
    return out


def _merge_skills(given: list[dict[str, Any]], now: list[dict[str, Any]]
                  ) -> list[dict[str, Any]]:
    """The full rows that the PUT sends. A key that the member did not give
    keeps the value of the same skill now (the name folds case), so a bare
    word never drops a level, the years or the evidence. A new skill takes no
    level, and the evidence ``manual``."""
    have = {str(r.get("skill") or "").lower(): r for r in now}
    out: list[dict[str, Any]] = []
    for row in given:
        old = have.get(row["skill"].lower(), {})
        full = {key: row[key] if key in row else old.get(key) for key in _SKILL_KEYS}
        full["skill"] = row["skill"]
        full["evidence"] = full["evidence"] or "manual"
        out.append(full)
    return out


def _count(n: int, noun: str) -> str:
    """*n* and the noun in the right form: "1 skill", "2 skills"."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _whole(value: Any) -> str:
    """A short value of a vocabulary or a number, whole, on one line."""
    return one_line(strip_hidden(str(value)))


def _skill_line(row: dict[str, Any]) -> str:
    """One skill on the card, whole. The level and the evidence are words of
    the server's vocabulary, so no line is ever cut."""
    parts = [_whole(row.get("level") or "no level")]
    years = row.get("years")
    if years is not None:
        parts.append(f"{_whole(years)} {'year' if years == 1 else 'years'}")
    if row.get("last_used_year") is not None:
        parts.append(f"last used {_whole(row['last_used_year'])}")
    parts.append(f"evidence {_whole(row.get('evidence') or 'manual')}")
    return f"- {_shown(row.get('skill'))} ({', '.join(parts)})"


def _credential_rows(raw: Any) -> list[dict[str, Any]]:
    """The credentials as ``PUT /people/me/credentials`` takes them
    (``skills.py`` ``CredentialIn``, which has no ``source``)."""
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
                if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                          or not 1950 <= value <= 2100):
                    raise _Refused(_W_BAD.format(field=f"credentials #{n} {key}",
                                                 why="Send a year from 1950 to 2100."))
                clean[key] = value
                continue
            limit = MAX_NOTE_CHARS if key == "detail" else MAX_SHORT_CHARS
            text = _text_arg(f"credentials #{n} {key}", value, limit) \
                if value is not None else ""
            if key == "kind" and text and text not in CREDENTIAL_KINDS:
                raise _Refused(_W_BAD.format(field=f"credentials #{n} kind",
                                             why="Send " + ", ".join(CREDENTIAL_KINDS) + "."))
            clean[key] = text or None
        if not clean["kind"] or not clean["title"]:
            raise _Refused(_W_BAD.format(field=f"credentials #{n}",
                                         why="Each row needs a kind and a title."))
        if clean["year_from"] is not None and clean["year_to"] is not None \
                and clean["year_to"] < clean["year_from"]:
            raise _Refused(_W_BAD.format(field=f"credentials #{n}",
                                         why="year_to cannot be before year_from."))
        out.append(clean)
    return out


def _credential_line(row: dict[str, Any]) -> str:
    text = f"- {_whole(row.get('kind') or 'no kind')}: {_shown(row.get('title'))}"
    if row.get("issuer"):
        text += f" by {_shown(row['issuer'])}"
    start, end = row.get("year_from"), row.get("year_to")
    if start is not None and end is not None:
        text += f", {_whole(start)} to {_whole(end)}"
    elif start is not None:
        text += f", from {_whole(start)}"
    elif end is not None:
        text += f", to {_whole(end)}"
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


async def _capability() -> dict[str, Any]:
    """The member's own skills and credentials (``GET /people/me/skills``)."""
    reply = await _get(MY_SKILLS_PATH)
    if not reply.ok:
        raise _RouteRefused(_write_fail(reply))
    if not isinstance(reply.data, dict):
        raise _Refused(_W_SHAPE)
    return reply.data


def _now_rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = data.get(key)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _keys(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[tuple[Any, ...]]:
    """The values of *keys* in each row, in order: what a card shows."""
    return [tuple(r.get(k) for k in keys) for r in rows]


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def set_my_skills(skills: list[Any]) -> dict[str, Any]:
    """Replace the whole list of skills on your OWN People record after one card. Each item is a word, or {skill, level, years, last_used_year, evidence}. A skill left out is removed."""
    try:
        _member_headers()
        given = _skill_rows(skills)
        now = _now_rows(await _capability(), "skills")
        after = _merge_skills(given, now)
        if _keys(now, _SKILL_KEYS) == _keys(after, _SKILL_KEYS):
            return {"ok": True, "count": len(now), "note": _W_SAME}
        decision = await _decide(
            "Replace your skills?",
            f"{_count(len(now), 'skill')} now, {len(after)} after. The new list replaces "
            "the whole list.",
            _W_CARD_NOTE + " Approve to save the list after. A skill that is not in it is "
            "removed. Reject to keep the skills as they are.",
            _list_card("Skills", [_skill_line(r) for r in now],
                       [_skill_line(r) for r in after]))
        if decision != "approved":
            return _not_saved(decision)
        if _keys(_now_rows(await _capability(), "skills"), _SKILL_KEYS) != \
                _keys(now, _SKILL_KEYS):
            return _refusal(_W_MOVED.format(what="skills"), decision=decision)
        sent = await _send("PUT", MY_SKILLS_PATH, {"rows": after},
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
        now = _now_rows(await _capability(), "credentials")
        if _keys(now, _CREDENTIAL_KEYS) == _keys(after, _CREDENTIAL_KEYS):
            return {"ok": True, "count": len(now), "note": _W_SAME}
        decision = await _decide(
            "Replace your credentials?",
            f"{_count(len(now), 'credential')} now, {len(after)} after. The new list "
            "replaces the whole list.",
            _W_CARD_NOTE + " Approve to save the list after. A row that is not in it is "
            "removed, and each row is saved as your own entry, also a row from your CV. "
            "Reject to keep the credentials as they are.",
            _list_card("Credentials", [_credential_line(r) for r in now],
                       [_credential_line(r) for r in after]))
        if decision != "approved":
            return _not_saved(decision)
        if _keys(_now_rows(await _capability(), "credentials"), _CREDENTIAL_KEYS) != \
                _keys(now, _CREDENTIAL_KEYS):
            return _refusal(_W_MOVED.format(what="credentials"), decision=decision)
        sent = await _send("PUT", MY_CREDENTIALS_PATH, {"rows": after},
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
    lines = [("dates", f"Dates: {_whole(row.get('starts_on'))} to "
                       f"{_whole(row.get('ends_on'))}"),
             ("kind", f"Kind: {_whole(row.get('kind') or 'away')}")]
    if row.get("hours_per_day") is not None:
        lines.append(("hours", f"Hours each day: {_whole(row['hours_per_day'])}"))
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
                not _number(hours_per_day) or not 0 < hours_per_day <= 24
                or round(hours_per_day, 2) != hours_per_day):
            raise _Refused(_W_BAD.format(
                field="hours_per_day",
                why="Send a number above 0 and at most 24, with at most 2 decimals."))
        if hours_per_day is not None and kind != "partial":
            raise _Refused(_W_BAD.format(field="hours_per_day",
                                         why="Send it only for a partial absence."))
        text = _text_arg("note", note if note is not None else "", MAX_NOTE_CHARS) or None
        body = {"starts_on": starts.isoformat(), "ends_on": ends.isoformat(), "kind": kind,
                "hours_per_day": hours_per_day, "note": text}
        decision = await _decide(
            "Record this absence?", f"{kind}, {body['starts_on']} to {body['ends_on']}",
            _W_CARD_NOTE + " Approve to record this absence. Reject to record nothing.",
            _absence_lines(body))
        if decision != "approved":
            return _not_saved(decision)
        sent = await _send("POST", MY_ABSENCES_PATH, body, act="absence")
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
        reply = await _get(MY_ABSENCES_PATH)
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
            [("id", f"Absence id: {aid}"), *_absence_lines(row)])
        if decision != "approved":
            return _not_saved(decision)
        sent = await _send("DELETE", f"/people/me/absences/{aid}", act="removal")
    except _Refused as exc:
        return _answer(exc)
    if not sent.ok:
        return _write_fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "absence_id": aid,
            "note": "The absence is removed."}


# ── The work schedule of the company (P-5b-4, task 3945) ────────────────────


def _changes_arg(raw: Any) -> dict[str, Any] | None:
    """The change as an object of at most :data:`MAX_CHANGE_KEYS` fields, or
    None. A JSON text of an object is read too."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or not raw or len(raw) > MAX_CHANGE_KEYS:
        return None
    if not all(isinstance(k, str) and 0 < len(k) <= 64 for k in raw):
        return None
    if len(json.dumps(raw, ensure_ascii=False, default=str)) > MAX_CHANGE_CHARS:
        return None
    return raw


async def _dry_run(policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(the policy as the route reads it, its impact)``. It writes nothing."""
    reply = await _send("PUT", SCHEDULE_PATH, {"policy": policy, "dry_run": True})
    if not reply.ok:
        raise _RouteRefused(_write_fail(reply))
    data = reply.data if isinstance(reply.data, dict) else {}
    read, impact = data.get("policy"), data.get("impact")
    if not isinstance(read, dict) or not isinstance(impact, dict) \
            or not all(type(impact.get(k)) is int for k in ("changed", "unchanged")):
        raise _Refused(_W_SHAPE)
    return read, impact


def _hours(value: Any) -> str:
    return f"{value:g}" if type(value) in (int, float) else "unknown"


def _impact_snap(policy: dict[str, Any], impact: dict[str, Any]) -> str:
    """What the card must still match when the member answers."""
    return json.dumps([policy, impact], sort_keys=True, ensure_ascii=False, default=str)


def _schedule_rows(now: dict[str, Any], policy: dict[str, Any],
                   impact: dict[str, Any]) -> list[tuple[str, str]]:
    """The rows of the card: each field that moves, then the impact."""
    rows: list[tuple[str, str]] = [
        (key, f"{_line(key, 48)}: {_old(now.get(key))} to {_shown(policy.get(key))}")
        for key in sorted(set(now) | set(policy)) if now.get(key) != policy.get(key)]
    rows.append(("week", f"Company week: {_hours(impact.get('hours_before'))} hours "
                         f"to {_hours(impact.get('hours_after'))} hours."))
    rows.append(("moves", f"People whose hours change: {impact.get('changed')}. People with "
                          f"no change: {impact.get('unchanged')}."))
    examples = impact.get("examples")
    shown = paged(examples if isinstance(examples, list) else [], 0, MAX_CARD_EXAMPLES,
                  default=MAX_CARD_EXAMPLES, maximum=MAX_CARD_EXAMPLES, noun="examples")
    for i, ex in enumerate(shown.items):
        if isinstance(ex, dict):
            rows.append((f"example{i}",
                         f"Example: {_shown(_line(ex.get('name') or 'unnamed', 60))} "
                         f"{_hours(ex.get('before'))} hours to {_hours(ex.get('after'))} hours"))
    return rows


@annotate(read_only=False, idempotent=False, open_world=False)
async def set_work_schedule(changes: dict[str, Any]) -> dict[str, Any]:
    """Change the work schedule of your company (working days, hours, clock), after one card that shows who it moves. It needs the admin right to manage members."""
    wanted = _changes_arg(changes)
    if wanted is None:
        return _refusal(_BAD_CHANGES.format(n=MAX_CHANGE_KEYS))
    try:
        me = await _send("GET", AUTH_ME_PATH)
        caps = me.data.get("capabilities") if isinstance(me.data, dict) else None
        if not me.ok or not isinstance(caps, list):
            return _refusal(_RIGHTS_UNREAD)
        if MANAGE_RIGHT not in caps:
            return _refusal(_NO_MANAGE, status=403)
        current = await _send("GET", SCHEDULE_PATH)
        if not current.ok:
            return _write_fail(current)
        now = current.data.get("policy") if isinstance(current.data, dict) else None
        if not isinstance(now, dict):
            return _refusal(_SCHEDULE_UNREAD)
        # The route replaces the whole policy, so the change goes onto the
        # policy of now. A field that the member leaves out stays as it is.
        asked = {**now, **wanted}
        policy, impact = await _dry_run(asked)
        if policy == now:
            return {"ok": True, "note": _SAME_SCHEDULE}
        decision = await _decide(
            "Change the work schedule of the company?", "work schedule", _SCHEDULE_NOTE,
            _schedule_rows(now, policy, impact))
        if decision != "approved":
            return _not_saved(decision)
        again, again_impact = await _dry_run(asked)
        if _impact_snap(again, again_impact) != _impact_snap(policy, impact):
            return _refusal(_SCHEDULE_CHANGED, decision=decision)
        sent = await _send("PUT", SCHEDULE_PATH, {"policy": policy, "dry_run": False},
                           act="change of the work schedule")
    except _Refused as exc:
        return _answer(exc)
    if not sent.ok:
        return _write_fail(sent, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    saved = data.get("saved") is True
    return {"ok": saved, "decision": decision, "policy": policy,
            "people_changed": impact.get("changed"),
            "hours_before": impact.get("hours_before"),
            "hours_after": impact.get("hours_after"),
            "note": ("The work schedule changed." if saved
                     else "The route saved nothing. Read the schedule again.")}


#: The five self writes of P-5b-2 (task 3943).
PEOPLE_WRITE_TOOLS = (update_my_profile, set_my_skills, set_my_credentials, add_my_absence,
                      remove_my_absence)

#: Each tool of the ``people`` pack, in the order of its skill family.
PEOPLE_TOOLS = (people_directory, people_me, people_work, reach_preview, *PEOPLE_WRITE_TOOLS,
                set_work_schedule)
