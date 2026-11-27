"""The four write tools of the ``notes`` pack (task 4117, PAR-4e-1).

Spec: ``project-docs/specs/chat_parity.md`` section 6.4. A member changes the
Note Taker setup from the chat. Each tool writes the member's own rows only:

* :func:`notes_update_settings` sends ``PUT /notes/settings``.
* :func:`notes_set_copilot_instructions` sends ``PUT /notes/copilot/instructions``.
* :func:`notes_add_glossary_term` sends ``POST /notes/glossary``.
* :func:`notes_delete_glossary_term` sends ``DELETE /notes/glossary/{id}``.

**One card for each write.** The tool reads first, shows ONE card with the old
and the new value, and writes only after the member of the run approves it.
After the approve it reads again, and it writes nothing when the value that the
card showed changed. A decline, a timeout, a steer and a chat with no card
write nothing. A text that does not fit whole on one card gets no card.

**The settings route replaces the whole row.** So the tool reads the settings,
changes only the fields that the member named, and sends every other field as
it read it.

**An all-default read is not proof.** The settings read of the gateway never
raises: on a fault, and on a session that sees no row, it answers with the
defaults. A write built on that answer would erase the saved row. So the tool
writes nothing when every value that it read is a default. A member who never
changed a setting makes the first change in the Notes app. Task 3966
(PR 2940) binds that read to the tenant. Until it merges, a deployment that
enforces row security answers with defaults, and this tool writes nothing.

**The server decides each right.** Each tool calls the gateway over HTTP as the
member of the run. No tool takes an org, a caller or an address argument (R5).
A term id must be one UUID, and it goes through ``urllib.parse.quote``.

**A held chat.** The tools say ``read_only=False`` and the owner of the pack is
``org-admin``, so ``egress.HELD_WRITE_OWNERS`` holds them off. No list changed.

**No stored text in a result.** The card shows the stored values. A result names
the fields and the ids only.

Fence: ``tests/unit/test_notes_write_tools.py``.
"""

from __future__ import annotations

import textwrap
import uuid
from typing import Any, Literal
from urllib.parse import quote

import httpx
from acb_common.card_body import CardWontFit, card_body, one_line
from acb_common.settings import internal_auth_headers

from acb_skills import notes_read_tools as reads
from acb_skills.hidden_text import hidden_problem, strip_hidden
from acb_skills.tool_annotations import annotate

__all__ = [
    "NOTES_WRITE_TOOLS",
    "notes_add_glossary_term",
    "notes_delete_glossary_term",
    "notes_set_copilot_instructions",
    "notes_update_settings",
]

#: The routes that the tools read and write.
SETTINGS_PATH = "/notes/settings"
INSTRUCTIONS_PATH = "/notes/copilot/instructions"
GLOSSARY_PATH = "/notes/glossary"

#: The limits of the routes (``settings._MAX_INSTRUCTIONS``, ``add_term``). The
#: routes cut a long instruction in silence, so the tool refuses it.
MAX_INSTRUCTIONS = 2000
MAX_TERM = 120
#: The longest bot name that the tool sends.
MAX_BOT_NAME = 100
#: The longest old value on a card.
MAX_OLD_CHARS = 300

#: The default of each field of ``NotesSettings``. A read that holds exactly
#: these values may be a failed read. ``test_notes_write_tools.py`` holds this
#: table equal to the model of the gateway.
SETTINGS_DEFAULTS: dict[str, Any] = {
    "copilot_instructions": "",
    "copilot_default_on": False,
    "copilot_sensitivity": "normal",
    "live_transcription": "auto",
    "template_instructions": {},
    "default_template": None,
    "bot_name": None,
    "auto_dispatch_tasks": True,
    "auto_dispatch_emails": True,
    "auto_dispatch_docs": True,
}
_SWITCHES = ("copilot_default_on", "auto_dispatch_tasks", "auto_dispatch_emails",
             "auto_dispatch_docs")
_CHOICES = {"copilot_sensitivity": ("low", "normal", "high"),
            "live_transcription": ("auto", "always", "never")}
#: A line break that is not a new line. The card could not show it.
_OTHER_BREAKS = (chr(0x2028), chr(0x2029))

_NO_MEMBER = "There is no member for this run, so nothing was saved."
_SHARED_ROOM = "A shared room cannot change your Note Taker. Ask in your own chat."
_UNREACHED = "The Notes routes could not be reached ({kind}). Nothing was saved. Try again."
_MAYBE = "The {act} may have been saved. Read it again before you try again."
_SHAPE = "The Notes route answered in a shape that this tool cannot read. Nothing was saved."
_NO_CARD = "This chat shows no card, so nothing was saved. Make the change in the Notes app."
_NOT_DONE = "Nothing was saved. The decision was {decision}."
_WONT_FIT = ("This change does not fit on one card, so nothing was saved. Make it in the "
             "Notes app, or change less at a time.")
_MOVED = "Your {what} changed while the card waited. Nothing was saved. Ask again."
_BAD = "{field}: {why} Nothing was saved."
_NONE_NAMED = "Name at least one setting to change. Nothing was saved."
_SAME = "Your Note Taker holds this already. Nothing was saved."
_UNPROVEN = ("Every setting that the Notes route answered with is a default. This tool "
             "cannot tell your saved settings from a failed read, so nothing was saved. "
             "Make this change in the Notes app settings.")
_BAD_TERM_ID = ("term_id is the id of one glossary term, as notes_glossary shows it. "
                "Nothing was removed.")
_NOT_MINE = ("No term of your glossary has that id, so nothing was removed. Read "
             "notes_glossary for the ids.")
_CARD_NOTE = "Your own Note Taker setup. No other member changes."

#: What a switch can do when it is on. The card says it.
_EFFECTS: dict[str, str] = {
    "auto_dispatch_emails": (
        "With auto-dispatch of emails on, Notes can send an email from your mailbox to a "
        "meeting attendee after it writes the notes, with no further approval. From a "
        "Gmail mailbox it makes a draft only."),
    "auto_dispatch_tasks": (
        "With auto-dispatch of tasks on, Notes can create tasks after it writes the notes, "
        "with no further approval."),
    "auto_dispatch_docs": (
        "With auto-dispatch of documents on, Notes can write a draft document after it "
        "writes the notes, with no further approval."),
    "copilot_default_on": "With the copilot on, it starts in your new meetings.",
}
_LIVE_COST = "Live transcription costs money for each minute of a meeting."


class _Refused(Exception):
    """A refusal that is an answer: its text goes to the model."""

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.result: dict[str, Any] = {"ok": False, "error": message, **extra}


def _headers() -> dict[str, str]:
    """The bearer and the acting member. With no member, or in a shared room,
    refuse before any request."""
    user = reads._current_user_email()
    if not user:
        raise _Refused(_NO_MEMBER)
    if "@" not in user:
        raise _Refused(_SHARED_ROOM)
    return {**internal_auth_headers(), "X-User-Email": user}


def _route_fail(status: int, data: Any) -> _Refused:
    """The refusal of a Notes route in plain words."""
    raw = data.get("detail") if isinstance(data, dict) else None
    detail = reads._line(raw) if isinstance(raw, str) else ""
    if status == 401:
        return _Refused("Your session is not signed in, so nothing was saved.", status=401)
    marker = "missing permission '"
    if status == 403 and marker in detail:
        right = detail.split(marker, 1)[1].split("'", 1)[0] or "the right that this needs"
        return _Refused(f"You do not hold {right} in your organization, so nothing was saved. "
                        "Ask an admin of your organization.", status=403)
    words = detail or f"The Notes route answered with status {status}."
    return _Refused(f"Nothing was saved. {words}", status=status)


async def _send(method: str, path: str, body: Any = None, act: str = "") -> Any:
    """One request as the member, and its JSON (or None). *act* names a write,
    and a read gives none. Raise :class:`_Refused` on each failure."""
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                method, f"{reads._gateway_url()}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise _Refused(_MAYBE.format(act=act) if act
                       else _UNREACHED.format(kind=type(exc).__name__)) from None
    try:
        data = resp.json()
    except Exception:
        data = None
    if not 200 <= resp.status_code < 300:
        raise _route_fail(resp.status_code, data)
    return data


# ── The values and the card ─────────────────────────────────────────────────


def _clean(field: str, raw: Any, limit: int, *, lines: bool = False) -> str:
    """*raw* as the route stores it (trimmed). Raise :class:`_Refused` for a
    value that the card cannot show as it is sent."""
    if not isinstance(raw, str):
        raise _Refused(_BAD.format(field=field, why="Send text."))
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    problem = hidden_problem(text, field)
    if problem is not None:
        raise _Refused(f"{problem} Nothing was saved.")
    if "«" in text or "»" in text:
        raise _Refused(_BAD.format(
            field=field, why="The text holds « or », and the card cannot show it."))
    if any(mark in text for mark in _OTHER_BREAKS) or (not lines and "\n" in text):
        why = "Send plain lines." if lines else "Send one line."
        raise _Refused(_BAD.format(field=field, why=why))
    if len(text) > limit:
        raise _Refused(_BAD.format(field=field, why=f"Send at most {limit} characters."))
    return text


def _old(value: Any) -> str:
    """A stored value on one short line, between « and ». It is data."""
    if reads._empty(value):
        return "(empty)"
    if isinstance(value, bool):
        return "on" if value else "off"
    text = one_line(strip_hidden(str(value))).replace("«", "").replace("»", "")
    if len(text) > MAX_OLD_CHARS:
        text = textwrap.shorten(text, MAX_OLD_CHARS, placeholder="...")
    return f"«{text}»"


def _new(value: Any) -> str:
    """A new one-line value on a card, whole."""
    if value is None or value == "":
        return "(empty)"
    if isinstance(value, bool):
        return "on" if value else "off"
    return f"«{value}»"


def _block(label: str, key: str, old: Any, new: str) -> list[tuple[str, str]]:
    """The rows of a long text: the old text on one short line, and the new
    text line by line and whole. Each line has a label, so no text can forge a
    line of the card. The keys are not in ``LONG_TEXT_KEYS``, so no cut
    happens."""
    rows = [(key, f"{label}, now: {_old(old)}")]
    if not new:
        return [*rows, (key, f"{label}, new: (empty)")]
    return rows + [(key, f"{label}, new line {n}: «{line}»")
                   for n, line in enumerate(new.split("\n"), start=1)]


async def _decide(title: str, detail: str, note: str, rows: list[tuple[str, str]]) -> None:
    """Show ONE card and wait, as ``people_tools._decide`` does. Return only on
    an approve, and raise :class:`_Refused` for each other answer."""
    from acb_skills import ask_tools

    try:
        context = str(card_body(note, rows))
    except CardWontFit:
        raise _Refused(_WONT_FIT) from None
    # The run's own member may answer, and no other (PZ-32). The member is the
    # run's, never an input.
    answer = await ask_tools.request_confirmation(
        title, detail, context, non_interactive_default="deny", close_on_steer=True,
        responder=reads._current_user_email() or None, fenced=True)
    if answer is True:
        return
    if answer is ask_tools.REVISED:
        raise _Refused(_NOT_DONE.format(decision="revised"), decision="revised")
    if not ask_tools.confirmation_channel_open():
        raise _Refused(_NO_CARD, decision="no_card")
    raise _Refused(_NOT_DONE.format(decision="stopped"), decision="stopped")


def _done(note: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "decision": "approved", "note": note, **extra}


# ── The settings ────────────────────────────────────────────────────────────


async def _settings() -> tuple[dict[str, Any], dict[str, str]]:
    """``(the stored settings, {template key: label})``. Raise
    :class:`_Refused` when the answer cannot be the base of a write."""
    data = await _send("GET", SETTINGS_PATH)
    stored = data.get("settings") if isinstance(data, dict) else None
    if not isinstance(stored, dict) or not set(SETTINGS_DEFAULTS) <= set(stored):
        raise _Refused(_SHAPE)
    if all(stored[k] == v for k, v in SETTINGS_DEFAULTS.items()):
        raise _Refused(_UNPROVEN)
    labels = {str(t["key"]): str(t.get("label") or t["key"])
              for t in data.get("templates") or [] if isinstance(t, dict) and t.get("key")}
    return stored, labels


def _template_key(field: str, raw: Any, labels: dict[str, str]) -> str:
    key = str(raw or "").strip()
    if key not in labels:
        raise _Refused(_BAD.format(
            field=field, why="Send a template key that notes_templates shows."))
    return key


def _named(given: dict[str, Any]) -> dict[str, Any]:
    """The fields that the member named, each checked, before any request."""
    out: dict[str, Any] = {}
    for field, raw in given.items():
        if raw is None:
            continue
        if field in _SWITCHES:
            if not isinstance(raw, bool):
                raise _Refused(_BAD.format(field=field, why="Send true or false."))
            out[field] = raw
        elif field in _CHOICES:
            if raw not in _CHOICES[field]:
                raise _Refused(_BAD.format(
                    field=field, why="Send " + ", ".join(_CHOICES[field]) + "."))
            out[field] = raw
        elif field == "bot_name":
            out[field] = _clean(field, raw, MAX_BOT_NAME) or None
        elif field == "default_template":
            out[field] = str(raw).strip() or None
        else:
            if not isinstance(raw, dict) or not raw:
                raise _Refused(_BAD.format(
                    field=field, why="Send an object of template key and text."))
            out[field] = {str(k).strip(): _clean(f"{field}.{k}", v, MAX_INSTRUCTIONS,
                                                 lines=True) for k, v in raw.items()}
    if not out:
        raise _Refused(_NONE_NAMED)
    return out


def _live_on(mode: str, copilot_on: bool) -> bool:
    """True when the settings pay for live transcription. A mirror of
    ``settings.wants_live_transcription`` of the gateway, which a test pins."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    return bool(copilot_on)


def _starts_live_cost(stored: dict[str, Any], changes: dict[str, Any]) -> bool:
    """True when *changes* turn live transcription from off to on."""
    after = {**stored, **changes}
    old, new = (_live_on(s["live_transcription"], s["copilot_default_on"])
                for s in (stored, after))
    return new and not old


def _dropped_templates(stored: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """The stored template texts that each save removes: the route keeps only
    the templates that it ships."""
    return {k: v for k, v in (stored["template_instructions"] or {}).items() if k not in labels}


# destructive: "" erases the bot name or a template text, and each save removes
# the text of a template that no longer ships.
@annotate(read_only=False, destructive=True, idempotent=True, open_world=False)
async def notes_update_settings(
    copilot_default_on: bool | None = None,
    copilot_sensitivity: Literal["low", "normal", "high"] | None = None,
    live_transcription: Literal["auto", "always", "never"] | None = None,
    default_template: str | None = None, bot_name: str | None = None,
    template_instructions: dict[str, str] | None = None,
    auto_dispatch_tasks: bool | None = None, auto_dispatch_emails: bool | None = None,
    auto_dispatch_docs: bool | None = None,
) -> dict[str, Any]:
    """Change your own Note Taker settings after one card. Send only the settings to change: every other one stays. default_template and bot_name: "" clears. template_instructions: {template key: text} for only the templates to change, and "" removes your own text for that template. The standing copilot instructions have their own tool, notes_set_copilot_instructions."""
    given = dict(locals())
    try:
        _headers()
        wanted = _named(given)
        stored, labels = await _settings()
        if wanted.get("default_template") is not None:
            wanted["default_template"] = _template_key(
                "default_template", wanted["default_template"], labels)
        asked = wanted.get("template_instructions")
        if asked is not None:
            merged = dict(stored["template_instructions"] or {})
            for key, value in asked.items():
                _template_key("template_instructions", key, labels)
                if value:
                    merged[key] = value
                else:
                    merged.pop(key, None)
            wanted["template_instructions"] = merged
        changes = {f: v for f, v in wanted.items() if v != stored[f]}
        if not changes:
            return {"ok": True, "saved": [], "note": _SAME}

        rows: list[tuple[str, str]] = []
        for field, value in changes.items():
            if field != "template_instructions":
                rows.append((field, f"{field}: {_old(stored[field])} to {_new(value)}"))
                continue
            before = stored[field] or {}
            for key in asked or {}:
                if before.get(key) != value.get(key):
                    rows += _block(f"Instructions for the template {key}", field,
                                   before.get(key), value.get(key, ""))
        dropped = _dropped_templates(stored, labels)
        rows += [("template_instructions",
                  f"This save removes your text for the template {_old(key)}, which no "
                  f"longer ships: {_old(words)}") for key, words in dropped.items()]
        live_cost = _starts_live_cost(stored, changes)
        note = [_CARD_NOTE]
        note += [words for field, words in _EFFECTS.items() if changes.get(field) is True]
        if live_cost:
            note.append(_LIVE_COST)
        if "bot_name" in changes:
            note.append("The people in a call see the bot name.")
        note.append("Each other setting stays as it is. Approve to save. Reject to keep "
                    "the settings as they are.")
        await _decide(
            "Save these Note Taker settings?", f"Settings: {', '.join(changes)}",
            " ".join(note), rows)

        # The route replaces the whole row. So the body starts from a second
        # read, and a named setting that moved while the card waited stops it.
        again, labels = await _settings()
        # A move of another field can also change what the card said.
        if (any(again[f] != stored[f] for f in changes)
                or _starts_live_cost(again, changes) != live_cost
                or _dropped_templates(again, labels) != dropped):
            raise _Refused(_MOVED.format(what="Note Taker settings"), decision="approved")
        await _send("PUT", SETTINGS_PATH, {**again, **changes}, act="change of your settings")
    except _Refused as exc:
        return exc.result
    return _done("Your Note Taker settings are saved.", saved=sorted(changes))


# ── The standing instructions of the copilot ────────────────────────────────


async def _instructions() -> str:
    data = await _send("GET", INSTRUCTIONS_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("instructions"), str):
        raise _Refused(_SHAPE)
    return data["instructions"]


@annotate(read_only=False, destructive=True, idempotent=True, open_world=False)
async def notes_set_copilot_instructions(instructions: str) -> dict[str, Any]:
    """Replace your own standing instructions for the meeting copilot after one card. Send the whole new text, of at most 2000 characters. "" clears them. Read notes_copilot_instructions first when you add to them."""
    try:
        _headers()
        text = _clean("instructions", instructions, MAX_INSTRUCTIONS, lines=True)
        now = await _instructions()
        if text == now:
            return {"ok": True, "saved": [], "note": _SAME}
        await _decide(
            "Replace your copilot instructions?" if text else "Clear your copilot instructions?",
            f"{len(text)} characters in place of {len(now)}",
            _CARD_NOTE + " The copilot reads this text in each of your meetings. The new "
            "text replaces the whole old text. Approve to save. Reject to keep the old text.",
            _block("Copilot instructions", "instructions", now, text))
        if await _instructions() != now:
            raise _Refused(_MOVED.format(what="copilot instructions"), decision="approved")
        await _send("PUT", INSTRUCTIONS_PATH, {"instructions": text},
                    act="change of your instructions")
    except _Refused as exc:
        return exc.result
    return _done("Your copilot instructions are saved." if text
                 else "Your copilot instructions are cleared.", saved=["instructions"])


# ── The glossary ────────────────────────────────────────────────────────────


async def _glossary() -> list[dict[str, str]]:
    data = await _send("GET", GLOSSARY_PATH)
    if not isinstance(data, list):
        raise _Refused(_SHAPE)
    return [{"id": str(r.get("id") or ""), "term": str(r.get("term") or "")}
            for r in data if isinstance(r, dict)]


def _same_word(rows: list[dict[str, str]], term: str) -> dict[str, str] | None:
    """The row that holds *term* with any case. The route keeps one."""
    return next((r for r in rows if r["term"].lower() == term.lower()), None)


@annotate(read_only=False, idempotent=True, open_world=False)
async def notes_add_glossary_term(term: str) -> dict[str, Any]:
    """Add one term of at most 120 characters to your own transcript glossary after one card, so the transcript spells it right. A term that differs only in case takes the new spelling."""
    try:
        _headers()
        word = _clean("term", term, MAX_TERM)
        if not word:
            raise _Refused(_BAD.format(field="term", why="Send the term."))
        old = _same_word(await _glossary(), word)
        if old is not None and old["term"] == word:
            return {"ok": True, "saved": [], "term_id": reads._line(old["id"], 40),
                    "note": _SAME}
        rows = [("term", f"Term: «{word}»")]
        if old is not None:
            rows.append(("term_now", f"It replaces the spelling {_old(old['term'])}"))
        await _decide(
            "Change the spelling of this glossary term?" if old else "Add this glossary term?",
            "1 term", _CARD_NOTE + " The transcript of your meetings uses the glossary. "
            "Approve to save. Reject to save nothing.", rows)
        if _same_word(await _glossary(), word) != old:
            raise _Refused(_MOVED.format(what="glossary"), decision="approved")
        made = await _send("POST", GLOSSARY_PATH, {"term": word}, act="term")
    except _Refused as exc:
        return exc.result
    made = made if isinstance(made, dict) else {}
    return _done("The term is in your glossary.",
                 term_id=reads._line(made.get("id") or "", 40))


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def notes_delete_glossary_term(term_id: str) -> dict[str, Any]:
    """Remove one term from your own transcript glossary by its id, after one card. notes_glossary gives the ids."""
    try:
        _headers()
        try:
            tid = str(uuid.UUID(str(term_id or "").strip()))
        except ValueError:
            return {"ok": False, "error": _BAD_TERM_ID}
        row = next((r for r in await _glossary() if r["id"] == tid), None)
        if row is None:
            raise _Refused(_NOT_MINE, status=404)
        await _decide(
            "Remove this glossary term?", "1 term",
            _CARD_NOTE + " Approve to remove this term. Reject to keep it.",
            [("term", f"Term: {_old(row['term'])}"), ("id", f"Term id: {tid}")])
        # The route answers 204 for an id that it does not hold, so read again.
        if row not in await _glossary():
            raise _Refused(_MOVED.format(what="glossary"), decision="approved")
        await _send("DELETE", "/notes/glossary/{}".replace("{}", quote(tid, safe="")),
                    act="removal")
    except _Refused as exc:
        return exc.result
    return _done("The term is removed from your glossary.", term_id=tid)


#: The writes of the ``notes`` pack, in the order of its skill family.
NOTES_WRITE_TOOLS = (
    notes_update_settings, notes_set_copilot_instructions, notes_add_glossary_term,
    notes_delete_glossary_term,
)
