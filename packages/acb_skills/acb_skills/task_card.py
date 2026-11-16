"""The card of a Projects ``create_task`` that the member edits (EAC-1).

Spec: ``project-docs/specs/projects_ai_chat.md`` §32 (task 2493, slice EAC-1,
task 3468). Dark behind ``PROJECT_EDIT_CARD_ORGS`` (empty, off).

**What it is.** The card of a new task shows its values as a form. The member
changes a value, then presses Approve. The answer carries the values as
``APPROVE {"edit": {...}}``, and the tool writes them. A plain ``APPROVE``
approves the values of the card as the assistant wrote them.

**Four parts, and each is ONE copy.**

* :func:`create_task_form` builds the form that the card draws, from the
  values that the tool resolved before the card. :func:`clean_task_edit_form`
  checks a form, and ``ask_tools.request_confirmation`` calls it.
* :func:`parse_task_edit_answer` is the ONE parser of the ``edit`` key. It is
  strict, and it fails closed. :data:`TASK_EDIT_KEYS` is the whitelist, and
  each key is required. An extra key, a missing key, a wrong type, a value
  that is too long, a date that is not ``YYYY-MM-DD``, a key twice and a
  ``reply`` or ``rows`` key each read as NO answer. Nothing is trimmed and
  nothing is cut to fit. The responder comes from the session (the gateway
  takes it from the session of ``/agent/respond-input``), never from the
  answer. The answer names no id: the tool keeps the project of the card.
  It resolves a CHANGED lane or person again after the answer, by exact name
  only. Each value that the member did not change stays as the card showed it.
* :func:`is_task_edit_answer` is the ONE shape test of an edit answer, and it
  reads no value. ``gateway.chat_recovery.card_resend`` asks it for a late
  answer (below).
* :func:`task_edit_card_on` is the ONE reader of ``PROJECT_EDIT_CARD_ORGS``.
  It reads the settings on each call, so a flag reload (SIGHUP,
  ``docs/flag_reload.md``) applies a change. The org is the bound tenant of
  the run, never request input (R5, R11). It fails closed.

**What stays as before.** With the flag off, in a WhatsApp run, in a run with
no card channel and in a held chat, the card of ``create_task`` is the card of
before, byte for byte. A repeating task keeps the card of before too, because
its rule reads the due date.

**A late answer** (the run of the card ended, and the card parked or died).
A plain late ``APPROVE`` writes the original values. The HRD-22 grant takes
the same card by its digest (``late_approve``), and RESUME-2a resumes the
stored call (``stored_call``). Both take the exact word ``APPROVE`` only. An
edit answer to such a card writes nothing. The late route sends
:data:`LATE_EDIT_ANSWER` in its place, with no value, and the line asks the
member to show the card again. The client line that the edits were not used
is EAC-2.

This module does no I/O and never imports ``skill_projects``: the tool calls
it, not the other way. Fences (R7): ``tests/unit/test_task_edit_card_answer.py``,
``tests/unit/test_task_edit_card_late.py`` and
``tests/unit/test_task_edit_card_r8.py`` (R8).
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

#: The kind of the one form of EAC-1: the card of a new task.
CREATE_TASK = "create_task"

#: The head of an answer that carries a body, as on every other card.
EDIT_ANSWER_PREFIX = "APPROVE "

#: The one key of the body of an edit answer. It never reuses ``reply``.
EDIT_KEY = "edit"

#: The keys of the ``edit`` object, exactly. Each one is required, so an
#: answer states each value that the write takes. EAC-3 adds the project.
TASK_EDIT_KEYS: frozenset[str] = frozenset({
    "due", "start", "status", "important", "leveraged", "description", "assignees",
})

#: The longest status name that the form takes.
STATUS_MAX = 200
#: The longest description that the form takes. A longer one is refused,
#: never cut. A task whose description is longer keeps the card of before.
DESCRIPTION_MAX = 20_000
#: The longest assignee (an address or ``agent:<name>``) that the form takes.
ASSIGNEE_MAX = 320
#: The most assignees that the form takes.
MAX_ASSIGNEES = 50

#: The longest answer that the form can honestly send: the prefix, the JSON
#: frame, and each text at its limit with each character escaped (six
#: characters for one). A longer answer is refused before the parse.
MAX_TASK_EDIT_ANSWER = len(EDIT_ANSWER_PREFIX) + 1024 + 6 * (
    DESCRIPTION_MAX + STATUS_MAX + 2 * 10 + MAX_ASSIGNEES * (ASSIGNEE_MAX + 4))

#: A date of the form: ASCII digits only, so a digit of another script fails.
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

#: The control characters that a description may hold: a line break and a tab.
_TEXT_CONTROLS = frozenset("\n\r\t")


# ── The flag ─────────────────────────────────────────────────────────────────

def task_edit_card_on(org: object = None) -> bool:
    """True when ``PROJECT_EDIT_CARD_ORGS`` names *org*. THE reader.

    The value is a comma list of organization ids, or ``*`` for each one.
    Empty means off, the default: the card of ``create_task`` is the card of
    before. It reads the settings at each call, so a flag reload applies a
    change with no restart. It parses the list with the shared
    ``assistant_profile._parse_orgs``, as ``reply_card.edit_card_on`` does.
    *org* defaults to the bound tenant of the run. No request field reaches
    this function (R5). It fails closed: a fault, or a frame with no tenant
    under a list of ids, reads as off."""
    try:
        from acb_common import get_settings
        from acb_common.assistant_profile import _parse_orgs

        orgs = _parse_orgs(str(getattr(get_settings(), "project_edit_card_orgs", "") or ""),
                           star=True)
        if not orgs or "*" in orgs:
            return bool(orgs)
        if org is None:
            from acb_common.db import current_tenant

            org = current_tenant()
        return str(org or "").strip().lower() in orgs
    except Exception:
        return False


# ── The values: one check for the form and for the answer ────────────────────

def _is_date(value: Any) -> bool:
    """True for ``""`` or an exact ``YYYY-MM-DD`` date. No space is trimmed."""
    if not isinstance(value, str):
        return False
    if value == "":
        return True
    if not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _one_line(value: Any, limit: int, *, empty: bool) -> bool:
    """True for a text of one line, at most *limit* characters, with no
    control character. *empty* says whether ``""`` is legal. No space is
    trimmed, and a text of spaces only counts as empty."""
    if not isinstance(value, str) or len(value) > limit:
        return False
    if not value.strip():
        return empty and value == ""
    return not any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in value)


def _text(value: Any, limit: int) -> bool:
    """True for a text of at most *limit* characters with no control
    character but a line break and a tab."""
    if not isinstance(value, str) or len(value) > limit:
        return False
    return not any(unicodedata.category(ch) == "Cc" and ch not in _TEXT_CONTROLS
                   for ch in value)


def _assignees(value: Any) -> bool:
    """True for a list of at most :data:`MAX_ASSIGNEES` people, each a text of
    one line, and none twice (case does not count)."""
    if not isinstance(value, list) or len(value) > MAX_ASSIGNEES:
        return False
    if not all(_one_line(a, ASSIGNEE_MAX, empty=False) for a in value):
        return False
    folded = [a.strip().lower() for a in value]
    return len(set(folded)) == len(folded)


def _values_ok(values: Any) -> bool:
    """True when *values* holds exactly :data:`TASK_EDIT_KEYS`, each of its
    type and in its limits. The form and the answer share this one check."""
    if not isinstance(values, dict) or set(values) != TASK_EDIT_KEYS:
        return False
    important = values["important"]
    return (
        _is_date(values["due"])
        and _is_date(values["start"])
        and _one_line(values["status"], STATUS_MAX, empty=True)
        and (important is None or type(important) is bool)
        and type(values["leveraged"]) is bool
        and _text(values["description"], DESCRIPTION_MAX)
        and _assignees(values["assignees"])
    )


# ── The form ─────────────────────────────────────────────────────────────────

def clean_task_edit_form(form: Any) -> dict[str, Any]:
    """The edit form of a card, checked, as a new dict.

    The form is ``{kind, project_id, values}``. ``kind`` is
    :data:`CREATE_TASK`. ``project_id`` is the project of the card, so the
    form can list its statuses. The answer never carries it, and the tool
    never reads it back. ``values`` holds :data:`TASK_EDIT_KEYS`. A bad form
    is a programming error of the calling tool, so it raises ``ValueError``
    before any card is drawn."""
    if not isinstance(form, Mapping) or set(form) != {"kind", "project_id", "values"}:
        raise ValueError("an edit form is {kind, project_id, values}")
    if form["kind"] != CREATE_TASK:
        raise ValueError("an edit form edits a new task only")
    pid = form["project_id"]
    try:
        canonical = str(uuid.UUID(str(pid)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("an edit form names its project by id") from None
    if pid != canonical:
        raise ValueError("an edit form names its project by its canonical id")
    values = form["values"]
    if not _values_ok(values):
        raise ValueError("an edit form holds each value of the card, in its limits")
    return {
        "kind": CREATE_TASK,
        "project_id": canonical,
        "values": {**values, "assignees": list(values["assignees"])},
    }


def create_task_form(
    *,
    project_id: str,
    due: str,
    start: str,
    status: str,
    important: bool | None,
    leveraged: bool,
    description: str,
    assignees: list[str],
) -> dict[str, Any] | None:
    """The form of the card of a new task, or None when a value does not fit.

    Each value is the value that the tool resolved before the card, so the
    form shows what a plain Approve writes. ``status`` is the name of the
    lane, or ``""`` for the default. ``important`` is None when the task is
    not judged. A value outside the limits of the form gives None, and the
    card of before shows. It never raises."""
    form = {
        "kind": CREATE_TASK,
        "project_id": project_id,
        "values": {
            "due": due,
            "start": start,
            "status": status,
            "important": important,
            "leveraged": leveraged,
            "description": description,
            "assignees": list(assignees),
        },
    }
    try:
        return clean_task_edit_form(form)
    except ValueError:
        return None


# ── The answer ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaskEdit:
    """The member's answer to the edit card of a new task.

    Each value is exactly as the member's browser sent it. ``responder`` is
    the session member who answered (the gateway's, never the answer's). It
    is truthy, so a tool reads it as an approve."""

    due: str
    start: str
    status: str
    important: bool | None
    leveraged: bool
    description: str
    assignees: tuple[str, ...]
    responder: str

    def values(self) -> dict[str, Any]:
        """The values of the answer, keyed as :data:`TASK_EDIT_KEYS`."""
        return {
            "due": self.due,
            "start": self.start,
            "status": self.status,
            "important": self.important,
            "leveraged": self.leveraged,
            "description": self.description,
            "assignees": list(self.assignees),
        }


def _no_twin_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object whose keys are each there once. A key twice is no answer:
    which of the two did the member send?"""
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("a key twice")
        out[k] = v
    return out


def _no_constant(_name: str) -> Any:
    """``NaN`` and ``Infinity`` are not JSON. They are no answer."""
    raise ValueError("not JSON")


#: What :func:`_decoded` gives for a body that is no JSON by its guards.
_NOT_JSON = object()


def _decoded(body: str) -> Any:
    """The JSON value of the body of an answer, or :data:`_NOT_JSON`. The ONE
    decode of an answer body, with the guards of the parser: no key twice, no
    ``NaN`` or ``Infinity``, no nesting past the depth that Python allows, and
    no text after the value. The parser and the shape test both call it, so
    they read the same key (``"ed\\u0069t"`` is ``edit``)."""
    try:
        return json.loads(body, object_pairs_hook=_no_twin_keys, parse_constant=_no_constant)
    except (TypeError, ValueError, RecursionError):
        return _NOT_JSON


def parse_task_edit_answer(answer: Any, responder: Any) -> TaskEdit | None:
    """The member's edit of a new task, or None when *answer* is no edit.

    THE parser of the ``edit`` key (EAC-1). Strict, and it fails closed. The
    answer is ``APPROVE`` and one JSON object with the one key ``edit``. That
    object holds exactly :data:`TASK_EDIT_KEYS`, each of its type and in its
    limits (:func:`_values_ok`). An extra key (a ``project_id``, a ``files``,
    a ``reply``), a missing key, a key twice, a wrong type, a text over its
    limit, a date that is not ``YYYY-MM-DD`` and an answer over
    :data:`MAX_TASK_EDIT_ANSWER` read as no answer. Nothing is trimmed and
    nothing is cut to fit.

    *responder* is the session member who answered, which the gateway sets.
    An answer with no session member is no answer. A plain ``APPROVE`` is no
    edit either: the card gate reads it as the approve of the original
    values, before it calls this."""
    who = responder.strip().lower() if isinstance(responder, str) else ""
    if "@" not in who:
        return None
    text = answer if isinstance(answer, str) else ""
    if len(text) > MAX_TASK_EDIT_ANSWER:
        return None
    text = text.strip()
    if text[:len(EDIT_ANSWER_PREFIX)].upper() != EDIT_ANSWER_PREFIX:
        return None
    body = _decoded(text[len(EDIT_ANSWER_PREFIX):])
    if not isinstance(body, dict) or set(body) != {EDIT_KEY}:
        return None
    values = body[EDIT_KEY]
    if not _values_ok(values):
        return None
    return TaskEdit(
        due=values["due"],
        start=values["start"],
        status=values["status"],
        important=values["important"],
        leveraged=values["leveraged"],
        description=values["description"],
        assignees=tuple(values["assignees"]),
        responder=who,
    )


#: What the next run reads in place of an edit answer whose run ended (review
#: P2). A parked or dead card writes nothing on an edit answer: only the word
#: ``APPROVE`` takes a late grant (``late_approve.grant_from_offer``) or a
#: stored call. So the answer goes as this line, with NO value in it. The form
#: holds text that the model wrote, and the member's words are not facts until
#: a live card takes them. ``gateway.chat_recovery.card_resend`` sends it, and
#: the chat draws the same line.
LATE_EDIT_ANSWER = (
    "I changed this card and pressed Approve, but the run had ended. "
    "Nothing was written. Please show the card again."
)

#: An answer head that a body can follow: ``APPROVE`` and white space.
_APPROVE_HEAD = re.compile(r"(?i)APPROVE\s")


def is_task_edit_answer(answer: Any) -> bool:
    """True when *answer* is an edit answer by its shape. It reads no value.

    THE rule: the answer is at most :data:`MAX_TASK_EDIT_ANSWER` characters,
    it starts with ``APPROVE`` (any case) and white space, and its body
    decodes (:func:`_decoded`, the decode of the parser) to an object that
    HAS the key ``edit``. The decode comes first, so an escaped key counts.
    Any other body is no edit answer: no JSON, not an object, no ``edit`` key,
    text after the value, a key twice, ``NaN``, too deep or too long. The
    parser refuses each of those too, so no such body is an edit answer
    anywhere. Whenever :func:`parse_task_edit_answer` takes an answer, this
    says True. An object with the key and bad values says True too."""
    raw = answer if isinstance(answer, str) else ""
    if len(raw) > MAX_TASK_EDIT_ANSWER:
        return False
    text = raw.strip()
    if not _APPROVE_HEAD.match(text):
        return False
    body = _decoded(text[len("APPROVE"):])
    return isinstance(body, dict) and EDIT_KEY in body


#: The kinds of a card that can carry an edit form: a confirmation card, and
#: a card whose kind the late route does not know.
_EDIT_CARD_KINDS = frozenset({"confirmation", ""})


def is_late_edit_answer(kind: Any, card: Any, answer: Any) -> bool:
    """True when the late answer *answer* of a card whose run ended is an
    edit answer of an edit card. ``gateway.chat_recovery.card_resend`` asks
    it, and then sends :data:`LATE_EDIT_ANSWER` in place of the answer.

    The card is a confirmation card, or its kind is not known. When the card
    value is known (*card* is not empty), it carries an edit form. The answer
    passes :func:`is_task_edit_answer`. An ``ask_user`` card, a plain card
    and a note that only names ``"edit"`` keep the member's words."""
    if str(kind or "") not in _EDIT_CARD_KINDS:
        return False
    if isinstance(card, Mapping) and card and card.get(EDIT_KEY) is None:
        return False
    return is_task_edit_answer(answer)


def changed_values(form: Mapping[str, Any], edit: TaskEdit) -> dict[str, Any]:
    """The values that *edit* changes on *form*, by key. Empty when the member
    changed nothing. *form* is the tool's own form, never an echo of the
    answer. A list of people compares in order and as sent."""
    shown = form.get("values") if isinstance(form, Mapping) else None
    if not isinstance(shown, Mapping):
        return {}
    sent = edit.values()
    return {k: sent[k] for k in sorted(TASK_EDIT_KEYS) if sent[k] != shown.get(k)}


__all__ = [
    "ASSIGNEE_MAX",
    "CREATE_TASK",
    "DESCRIPTION_MAX",
    "EDIT_ANSWER_PREFIX",
    "EDIT_KEY",
    "LATE_EDIT_ANSWER",
    "MAX_ASSIGNEES",
    "MAX_TASK_EDIT_ANSWER",
    "STATUS_MAX",
    "TASK_EDIT_KEYS",
    "TaskEdit",
    "changed_values",
    "clean_task_edit_form",
    "create_task_form",
    "is_late_edit_answer",
    "is_task_edit_answer",
    "parse_task_edit_answer",
    "task_edit_card_on",
]
