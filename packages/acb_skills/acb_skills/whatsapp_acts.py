"""A confirmed write from WhatsApp: park the act, then run it on Confirm (WS-47 WAC-4).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §14.

A WhatsApp run cannot wait on a card. It sends its reply only after the run
ends, and it holds its thread's lock while it runs (§14.2). So an allowed act
PARKS, and the member's next message settles it:

1. **The call is named at the injection seam.** ``orchestrator._tool_injection``
   wraps every tool that it gives an agent. Inside a WhatsApp run with writes
   on, the wrapper opens :func:`tool_call` around the call, so the card gate
   knows which tool asks and with which arguments. Outside one it does
   nothing, so a web run is unchanged.
2. **The card gate answers "not yet".** ``ask_tools.request_confirmation``
   asks :func:`answer_card` first. For an act of :data:`ALLOWED_ACTS`, with no
   rows, it stores a :class:`ParkedAct` on the run and answers False. So the
   tool writes nothing, and :func:`parked_line` tells the model why. Every
   other card gets None here, and ``refuse_cards`` then denies it as before.
3. **The run sends the buttons.** ``bot_run`` stores the act in
   ``whatsapp_pending_acts`` and sends :func:`summary` and
   :func:`confirm_buttons` after the model's reply. Only code builds these
   buttons, and ``whatsapp_ui`` refuses the two titles.
4. **Confirm runs the stored call once.** ``whatsapp_channel/acts.py`` calls
   the same tool with the same arguments inside :func:`confirming`. The card
   gate then answers "yes" to exactly ONE card, and only when the card that
   the tool builds again equals the stored card text. Any other card gets no.

**The card binds what it does not show.** A card names a project by its name
when the member gave a name. So before its card a tool may call
:func:`card_facts` with the ids it resolved (the project, the assignees). The
stored card text holds them, so a Confirm after another project took that
name gets "no". The member's summary does not change.

**One act per run.** A second allowed card in the same run is refused. A rows
card, a form and every tool outside :data:`ALLOWED_ACTS` stay refused (§14.4).
One exception: the email acts of H-391 (:data:`EMAIL_ACTS`, spec §18) park
with their rows and their reply form, and a reply code approves them. The
section at the end of this module holds them.

Fences: ``tests/unit/test_wac_writes.py`` and ``tests/unit/test_wac_writes_r8.py``.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from acb_skills.whatsapp_ui import BODY_MAX, OutMessage, current_run

#: The acts that may go by button: the tool name, and the module that
#: defines it. A tool of the same name in another module is not the act.
#: Each one is class B, asks ONE card, and sends no rows (§14.4). WAC-4b
#: adds the time block: a private task with a block, and a block (or any
#: other field of ``set_my_overlay``) on a task that exists. One of those
#: fields is a board write: INBOX, NEXT or WAITING on a finished task
#: reopens it (D77), and its card says so. Each is one card the web undoes. The
#: Calendar is the scheduled time of a task (D52-D54), so this is the
#: Calendar's write. The web chat already had both tools.
ALLOWED_ACTS: dict[str, str] = {
    "create_task": "skill_projects.writes",
    "create_personal_task": "skill_projects.writes",
    "set_my_overlay": "skill_projects.writes",
}

#: The two button titles. A tap arrives as its title (``inbound.tap_text``).
CONFIRM = "Confirm"
CANCEL = "Cancel"

#: What a parked tool tells the model in place of "Cancelled".
PARKED_TEXT = (
    "Not done yet. Nothing is written until the member taps Confirm. After "
    "your reply, Metorite sends the member a summary with Confirm and Cancel "
    "buttons. Tell the member in one line that it is ready for their "
    "Confirm. Add no buttons of your own, do not say that it is done, and "
    "stop."
)

#: The body of the Confirm and Cancel buttons. ``bot_run`` names the expiry.
CONFIRM_BODY = "Tap Confirm to make this change, or Cancel. This request ends in 10 minutes."


@dataclass
class ToolCall:
    """The tool call that runs now in a WhatsApp run with writes on."""

    name: str
    module: str
    #: The keyword arguments, or None when the call had positional ones,
    #: which no code can store and run again.
    arguments: dict[str, Any] | None
    #: True once this call's card was parked.
    parked: bool = False
    #: The ids that the card binds and does not show (:func:`card_facts`).
    facts: dict[str, Any] | None = None
    #: The agent of the run that makes the call, from the run binding and
    #: never from input. An email act parks only for the email assistant.
    agent: str = ""
    #: What the tool tells the model once its card parked (:func:`parked_line`).
    parked_text: str = ""
    #: Arguments that the Confirm run takes in place of the model's own
    #: (:func:`pin_arguments`): the text that the card showed, for example.
    pins: dict[str, Any] | None = None
    #: Why an email card of this call did not park (H-391), or "".
    refused_text: str = ""


@dataclass(frozen=True)
class ParkedAct:
    """One act that waits for the member's Confirm."""

    tool: str
    arguments: dict[str, Any]
    #: :func:`card_text` of the card, the text that Confirm must match.
    card: str
    title: str
    detail: str
    context: str
    #: ``button`` (Confirm and Cancel) or ``code`` (an email act, H-391).
    kind: str = "button"
    #: The tick-box rows of an email card, as ``ask_tools.clean_card_rows``
    #: gave them, or None.
    rows: list[dict[str, Any]] | None = None
    #: The reply form of an email card (``ask_tools.clean_reply_form``), or None.
    reply: dict[str, Any] | None = None


@dataclass
class Confirming:
    """The "yes" for ONE card at Confirm. :func:`answer_card` fills it."""

    card: str
    used: bool = False
    matched: bool = False
    facts: dict[str, Any] | None = None


_CALL: ContextVar[ToolCall | None] = ContextVar("whatsapp_tool_call", default=None)
_CONFIRMING: ContextVar[Confirming | None] = ContextVar("whatsapp_confirming", default=None)


def _reset(var: ContextVar[Any], token: Any) -> None:
    """Reset *var*. A reset from another context clears it, so it never leaks."""
    try:
        var.reset(token)
    except ValueError:
        var.set(None)


@contextlib.contextmanager
def tool_call(fn: Callable[..., Any], args: tuple[Any, ...],
              kwargs: dict[str, Any]) -> Iterator[None]:
    """Name the tool call *fn* for the card gate. The injection seam opens it.

    It does nothing outside a WhatsApp run with writes on, so every other run
    is exactly as before. A nested call (a tool that an agent runs inside a
    ``call_agent``) opens its own, so the card gate sees the innermost call.
    """
    run = current_run()
    if run is None or not run.writes:
        yield
        return
    call = ToolCall(
        name=str(getattr(fn, "__name__", "") or ""),
        module=str(getattr(fn, "__module__", "") or ""),
        arguments=None if args else dict(kwargs),
        agent=_run_agent(),
    )
    token = _CALL.set(call)
    try:
        yield
    finally:
        _reset(_CALL, token)


def card_text(title: str, detail: str, context: str,
              facts: dict[str, Any] | None = None) -> str:
    """The one text of a card: its title, detail, context and facts, as JSON.

    The card gate passes the values it has already clipped, so the text at
    Confirm is built the same way as the text at the ask. *facts* are the
    ids that the card binds and does not show (:func:`card_facts`).
    """
    return json.dumps([title, detail, context, facts or {}], ensure_ascii=False,
                      sort_keys=True, default=str)


def card_facts(**facts: Any) -> None:
    """Bind ids that the NEXT card of this call does not show (WAC-4).

    A tool calls it just before its card. At the ask it goes on the running
    call, and at Confirm on the "yes", so both card texts hold the same ids.
    Anywhere else it does nothing, so a web run is unchanged.
    """
    state = _CONFIRMING.get()
    if state is not None:
        state.facts = dict(facts)
        return
    call = _CALL.get()
    if call is not None:
        call.facts = dict(facts)


def _stored_arguments(arguments: dict[str, Any]) -> dict[str, Any] | None:
    """*arguments* as JSON gives them back, or None when JSON cannot hold them."""
    try:
        return json.loads(json.dumps(arguments, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return None


def answer_card(title: str, detail: str, context: str,
                rows: list[dict[str, Any]] | None) -> bool | None:
    """The WhatsApp profile's answer to a card, or None to leave it to the gate.

    * At Confirm (:func:`confirming`): True for the FIRST card when its text
      equals the stored text, else False. Every later card gets False.
    * In a WhatsApp run with writes on, for an act of :data:`ALLOWED_ACTS`
      with no rows: park it and answer False ("not yet"). One act a run.
    * Else None: ``refuse_cards`` denies, as it did before WAC-4.

    It never approves a card with rows.
    """
    state = _CONFIRMING.get()
    if state is not None:
        if state.used:
            return False
        state.used = True
        state.matched = (rows is None
                         and card_text(title, detail, context, state.facts) == state.card)
        return state.matched
    run = current_run()
    if run is None or not run.writes or rows is not None or run.parked is not None:
        return None
    call = _CALL.get()
    if (call is None or call.arguments is None
            or ALLOWED_ACTS.get(call.name) != call.module):
        return None
    arguments = _stored_arguments(call.arguments)
    if arguments is None:
        return None
    run.parked = ParkedAct(tool=call.name, arguments=arguments,
                           card=card_text(title, detail, context, call.facts),
                           title=title, detail=detail, context=context)
    call.parked = True
    return False


def parked_line() -> str | None:
    """What the tool tells the model when its card was just parked, or None.

    :data:`PARKED_TEXT` for a Confirm button, and :data:`EMAIL_PARKED_TEXT`
    for an email act that waits for its reply code (H-391)."""
    call = _CALL.get()
    if call is None or not call.parked:
        return None
    return call.parked_text or PARKED_TEXT


@contextlib.contextmanager
def confirming(card: str) -> Iterator[Confirming]:
    """Answer "yes" to ONE card whose text is *card* (the Confirm of §14.3).

    Reset on the way out, so the "yes" never reaches another call.
    """
    state = Confirming(card=card)
    token = _CONFIRMING.set(state)
    try:
        yield state
    finally:
        _reset(_CONFIRMING, token)


def _plain(value: str) -> str:
    """A card value as the phone shows it: no «fence» marks.

    The web client draws a fenced value as a token. WhatsApp shows text only.
    """
    return value.replace("«", "").replace("»", "")


def summary(act: ParkedAct) -> str:
    """What the member reads before Confirm: the card, in WhatsApp text."""
    head = f"*{_plain(act.title)}*"
    parts = [head, _plain(act.detail), _plain(act.context)]
    return "\n\n".join(p for p in parts if p.strip())


def confirm_buttons(act_id: str | None = None) -> OutMessage:
    """The Confirm and Cancel buttons. Only code builds them (§14.3).

    The ids are ``b1`` and ``b2``, as on every button, and no code reads them:
    the server binds the act, never the phone. *act_id* names the act that
    this part offers, so ``bot_run`` can mark it offered once Meta took it.
    """
    interactive = {
        "type": "button",
        "body": {"text": CONFIRM_BODY},
        "action": {"buttons": [
            {"type": "reply", "reply": {"id": "b1", "title": CONFIRM}},
            {"type": "reply", "reply": {"id": "b2", "title": CANCEL}},
        ]},
    }
    return OutMessage("interactive", f"{CONFIRM_BODY}\n[Buttons: {CONFIRM} | {CANCEL}]",
                      interactive=interactive, act_id=act_id)


def is_word(message: str, word: str) -> bool:
    """True when *message* is exactly *word*, in any case, trimmed (§14.3)."""
    return " ".join(str(message or "").split()).casefold() == word.casefold()


# ── WAC-P4: the Confirm of one egress call after an untrusted answer ────────
#
# Spec §17.3 (WAC-P4). After the email assistant answers once, the thread is
# marked (``acb_skills.egress``, EM-G11d), and each later egress call needs
# the member's yes. The web chat asks with a card. A WhatsApp run cannot
# wait for a card, so the call PARKS as an act, exactly as a WAC-4 write
# does, and the member's Confirm button answers it:
#
# 1. **Ask.** ``_tool_injection._gate_injected_tool`` calls
#    :func:`answer_egress` where the web asks its card. In a WhatsApp run
#    with writes on, the call parks (one act a run), and the tool returns
#    :data:`EGRESS_PARKED_TEXT`. Nothing reaches outside.
# 2. **Confirm.** ``whatsapp_channel/acts.settle`` checks the act exactly as
#    a WAC-4 act: the phone, the thread, the link row, the button message
#    and the 10 minutes. It then gives ``bot_run`` an ``Approved``, and
#    ``bot_run`` runs the model again inside :func:`approving`.
# 3. **The one yes.** In that run, :func:`answer_egress` approves the FIRST
#    call whose tool and arguments equal the stored act. Any other call
#    parks again, so the member sees its own summary. The yes ends with the
#    run, and a call from a run with no WhatsApp profile is never approved.
#
# A run with no member and no channel (a batch or a scheduled run) has no
# WhatsApp profile, so its call is refused as before.

#: The tool name of a WAC-P4 act starts with this. ``acts.act_function``
#: never runs such an act: the Confirm runs the model again instead.
EGRESS_ACT_PREFIX = "egress__"

#: The two answers of :func:`answer_egress` that are not None.
EGRESS_APPROVED = "approved"
EGRESS_PARKED = "parked"

#: What a parked egress call tells the model.
EGRESS_PARKED_TEXT = (
    "Not done yet. This chat read an answer from the email assistant, so a "
    "call that reaches outside Metorite waits for the member's Confirm. After "
    "your reply, Metorite sends the member a summary with Confirm and Cancel "
    "buttons. Tell the member in one line that it waits for their Confirm. "
    "Add no buttons of your own, do not say that it ran, and stop."
)

#: What an egress call tells the model when the member could not see it
#: whole on WhatsApp (review P1-1): its recipients and attachments do not fit.
EGRESS_TOO_LONG = "too_long"
EGRESS_TOO_LONG_TEXT = (
    "{tool} did not run. This chat read an answer from the email assistant, so "
    "the member must approve this call, and its recipients and attachments are "
    "too long to show on WhatsApp. Tell the member to make this change in the "
    "Metorite web app. Do not ask again for the same call."
)

#: The card of a parked egress call, as the web card says it.
EGRESS_TITLE = "Run {tool}? It reaches outside Metorite"
EGRESS_DETAIL = ("This chat read an answer from the email assistant, which reads "
                 "mail from outside. A mail could ask for this call.")
#: The most characters of the call's view on WhatsApp: the body limit of an
#: interactive message. The view comes from ``egress.egress_call_view``, the
#: one builder that the web card uses too. The stored card text binds ALL
#: the arguments.
EGRESS_VIEW_CHARS = BODY_MAX

#: The tool name shape of ``whatsapp_pending_acts`` (migration 242).
_ACT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass
class Approving:
    """The yes for ONE egress call at Confirm (:func:`approving`)."""

    card: str
    used: bool = False
    closed: bool = False


_APPROVING: ContextVar[Approving | None] = ContextVar("whatsapp_approving", default=None)


def egress_act_name(tool: str) -> str | None:
    """The stored tool name of an egress act of *tool*, or None when the
    table cannot hold it (the call is then refused, as before)."""
    name = f"{EGRESS_ACT_PREFIX}{tool}"
    return name if _ACT_NAME.match(name) else None


def is_egress_act(name: str) -> bool:
    """True for the stored tool name of a WAC-P4 act."""
    return str(name or "").startswith(EGRESS_ACT_PREFIX)


def egress_tool(name: str) -> str:
    """The tool of a stored egress act name."""
    return str(name)[len(EGRESS_ACT_PREFIX):] if is_egress_act(name) else str(name)


def egress_card_text(tool: str, arguments: dict[str, Any]) -> str:
    """The one text that a Confirm must match: the tool and ALL its arguments."""
    return json.dumps(["egress", str(tool), arguments], ensure_ascii=False,
                      sort_keys=True, default=str)


def answer_egress(tool: str, arguments: dict[str, Any] | None) -> str | None:
    """The WhatsApp answer to the untrusted-answer card of one egress call.

    * :data:`EGRESS_APPROVED`: inside :func:`approving`, in a WhatsApp run,
      for the first call whose tool and arguments equal the stored act.
    * :data:`EGRESS_PARKED`: in a WhatsApp run with writes on and no act
      parked yet. The call is stored on the run, and it does not run.
    * :data:`EGRESS_TOO_LONG`: the same run, but the recipients and the
      attachments do not fit in one WhatsApp view. Nothing parks.
    * None: no WhatsApp form. The caller refuses, as before WAC-P4.
    """
    run = current_run()
    if run is None:
        return None
    stored = _stored_arguments(dict(arguments or {}))
    state = _APPROVING.get()
    if (state is not None and not state.used and not state.closed
            and stored is not None and egress_card_text(tool, stored) == state.card):
        state.used = True
        return EGRESS_APPROVED
    name = egress_act_name(str(tool or ""))
    if not run.writes or run.parked is not None or stored is None or name is None:
        return None
    from acb_skills.egress import egress_call_view

    # Review P1-1: the recipients, the subject and the attachments come
    # first and whole. A model's argument order never hides them.
    view = egress_call_view(tool, stored, EGRESS_VIEW_CHARS)
    if view is None:
        return EGRESS_TOO_LONG
    run.parked = ParkedAct(tool=name, arguments=stored,
                           card=egress_card_text(tool, stored),
                           title=EGRESS_TITLE.format(tool=tool),
                           detail=EGRESS_DETAIL, context=view)
    return EGRESS_PARKED


@contextlib.contextmanager
def approving(card: str) -> Iterator[Approving]:
    """Approve ONE egress call whose card text is *card* (WAC-P4 Confirm).

    The yes closes on the way out, so a task of the run that outlives it can
    never use it."""
    state = Approving(card=card)
    token = _APPROVING.set(state)
    try:
        yield state
    finally:
        state.closed = True
        _reset(_APPROVING, token)


# ── H-391: an email act, approved with a reply code ─────────────────────────
#
# Spec ``whatsapp_assistant_channel.md`` §18 and ``email_app_master_plan.md``
# §21.13. A channel changes delivery, never function (owner rule,
# 2026-10-10). So the email writes of the chat work from WhatsApp too: an
# archive, a label, a move, a reply, a send of a draft, a schedule, a
# forward and a new mail. The flow is the WAC-4 park, with three changes:
#
# 1. **Ask.** In a WhatsApp run with writes on, the card of one tool of
#    :data:`EMAIL_ACTS`, called by the email assistant, parks. A card with
#    rows (a held send card, §21) and a reply form park too, which a WAC-4
#    act never does. The stored card text holds every row and every field
#    that the send uses (:func:`email_card_text`).
# 2. **The summary.** ``bot_run`` sends ONE text that holds everything the
#    card shows: the act, the mailbox, each person with a number and the
#    class mark, the subject and the whole text, split over parts when it is
#    long and never cut (:func:`code_summary_parts`). It ends with a short
#    code. The member replies "YES <code>", or "NO".
# 3. **The approval.** ``whatsapp_channel/acts.settle`` checks the phone,
#    the code, the expiry and the link, and claims the act. ``bot_run`` then
#    runs the model again inside :func:`approving_email`. The FIRST email
#    card whose text equals the stored text gets the yes, with the people
#    that the member kept. A held card and a reply card record the answer
#    with ``reply_card.record_reply_answer``, as the web's respond-input
#    does, so the gateway's act route sends exactly the recorded rows. Any
#    other card parks again, so the member sees its own summary.
#
# Fences: ``tests/unit/test_wac_email_confirm.py`` and
# ``tests/unit/test_wac_email_confirm_r8.py``.

#: The agent whose card may park as an email act.
EMAIL_AGENT = "email-assistant"

#: The email tools that may park on WhatsApp (H-391). Each one asks ONE card.
#: Reads stay as they are.
EMAIL_ACTS: frozenset[str] = frozenset({
    "manage_inbox",      # archive, label, move, read, star, trash, spam
    "compose_reply",     # a reply: the reply form
    "send_draft",        # a send of a draft
    "schedule_send",     # a send of a draft later
    "forward_email",     # a forward with its files
    "send_email",        # a new mail
})

#: What a parked email tool tells the model.
EMAIL_PARKED_TEXT = (
    "Not done yet. Nothing is sent or changed until the member replies with "
    "the code that Metorite sends them. After your reply, Metorite sends the "
    "member the whole summary and the code. Tell the member in one line that "
    "it waits for their code. Do not repeat the summary, do not write a code, "
    "do not say that it is done, and stop."
)

#: The symbols of a reply code: no 0, O, 1, I or L, which a reader confuses.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4
#: A code works this long after its summary went out.
CODE_TTL_S = 15 * 60

#: The words that approve, and the words that cancel.
_YES_WORDS = frozenset({"YES", "SEND", "APPROVE"})
_NO_WORDS = frozenset({"NO", "CANCEL"})
_DROP_WORD = "WITHOUT"
_ADD_WORD = "WITH"
#: A line number has 1 to 3 digits, so it never looks like a code.
_NUMBER = re.compile(r"^[0-9]{1,3}$")
_CODE = re.compile(rf"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$")


def _run_agent() -> str:
    """The agent of the run on this frame, from its bound context, or ""."""
    try:
        from acb_skills.write_artifact import artifact_context

        return str((artifact_context() or {}).get("agent_name") or "")
    except Exception:
        return ""


#: What an email tool tells the model when another act already waits.
EMAIL_WAITING_TEXT = (
    "Nothing changed. Another change already waits for the member's code in "
    "this chat, and one change waits at a time. Tell the member that you do "
    "this one when they ask again after they answer the code."
)


#: What an email tool tells the model when its card is too long to show
#: whole on WhatsApp. A summary never shows less than the act sends.
EMAIL_TOO_LONG_TEXT = (
    "Nothing was sent or changed. The card of this act is too long to show "
    "whole on WhatsApp, and a summary never shows less than the act sends. "
    "Offer to shorten the text, and show the act again."
)


def waiting_line() -> str | None:
    """Why the email card of this call did not park, or None.

    :data:`EMAIL_TOO_LONG_TEXT` for a card that the gate had to cut, and
    :data:`EMAIL_WAITING_TEXT` when another act of the run already waits.
    Only for an email act of a WhatsApp run with writes on."""
    run = current_run()
    call = _CALL.get()
    if (run is None or not run.writes or call is None or call.parked
            or call.name not in EMAIL_ACTS or call.agent != EMAIL_AGENT):
        return None
    if call.refused_text:
        return call.refused_text
    return EMAIL_WAITING_TEXT if run.parked is not None else None


def pin_arguments(**pins: Any) -> None:
    """Pin arguments of the running call for its approval run (H-391).

    ``compose_reply`` pins the text that its card showed, so the approval run
    shows the same text again and does not write a new one. Outside a
    WhatsApp run it does nothing."""
    call = _CALL.get()
    if call is not None:
        call.pins = {**(call.pins or {}), **pins}


# ── The card text and its people ────────────────────────────────────────────

#: The fields of a reply form that the send uses or that the card shows. The
#: draft id, the note and the excerpt of the original are not in the text,
#: so an approval run that writes the same reply matches.
_REPLY_VIEW_KEYS = ("message_id", "mailbox", "subject", "recipients", "ticked",
                    "classes", "marks", "reply_all", "body", "notices")


def _reply_view(reply: dict[str, Any] | None) -> dict[str, Any] | None:
    if reply is None:
        return None
    view = {k: reply.get(k) for k in _REPLY_VIEW_KEYS}
    original = reply.get("original") if isinstance(reply.get("original"), dict) else {}
    view["original"] = {k: original.get(k) for k in ("from", "subject", "received")}
    return view


def email_card_text(title: str, detail: str, context: str,
                    rows: list[dict[str, Any]] | None,
                    reply: dict[str, Any] | None,
                    facts: dict[str, Any] | None = None) -> str:
    """The one text of an email card: its words, its rows and its form."""
    return json.dumps(["email", title, detail, context, rows,
                       _reply_view(reply), facts or {}],
                      ensure_ascii=False, sort_keys=True, default=str)


@dataclass(frozen=True)
class Person:
    """One numbered line of an email card: a recipient or a file."""

    number: int
    #: The row id (a rows card) or the address (a reply form).
    key: str
    #: ``to``, ``cc``, ``bcc`` or ``file``.
    kind: str
    #: What the member reads, for example "Cc: kiran@example.com".
    label: str
    #: True when the card starts with it ticked.
    ticked: bool
    #: ``org``, ``sent``, ``thread``, ``new``, or "" for no class.
    cls: str = ""
    #: The server's words for the person, for example "from this thread".
    mark: str = ""
    #: A look-alike warning, or "".
    warning: str = ""


def _reply_people(reply: dict[str, Any]) -> list[Person]:
    choice = "reply_all" if reply.get("reply_all") else "reply"
    lists = (reply.get("recipients") or {}).get(choice) or {}
    ticked = (reply.get("ticked") or {}).get(choice)
    classes = reply.get("classes") or {}
    marks = reply.get("marks") or {}
    out: list[Person] = []
    for kind in ("to", "cc"):
        on = ({str(a).lower() for a in (ticked.get(kind) or [])}
              if isinstance(ticked, dict) else None)
        for addr in lists.get(kind) or []:
            low = str(addr).lower()
            out.append(Person(
                number=len(out) + 1, key=str(addr), kind=kind,
                label=f"{'To' if kind == 'to' else 'Cc'}: {addr}",
                ticked=True if on is None else low in on,
                cls=str(classes.get(low) or ""), mark=str(marks.get(low) or "")))
    return out


def _row_people(rows: list[dict[str, Any]]) -> list[Person]:
    return [Person(number=i, key=str(r.get("id") or ""),
                   kind=str(r.get("kind") or ""), label=str(r.get("label") or ""),
                   ticked=r.get("checked", True) is not False,
                   cls=str(r.get("class") or ""), warning=str(r.get("warning") or ""))
            for i, r in enumerate(rows, start=1)]


def card_people(rows: list[dict[str, Any]] | None,
                reply: dict[str, Any] | None) -> list[Person]:
    """The numbered people of an email card, in the order the summary shows."""
    if reply is not None:
        return _reply_people(reply)
    if rows is not None:
        return _row_people(rows)
    return []


def people_of_card_text(card: str) -> list[Person]:
    """:func:`card_people` of a stored :func:`email_card_text`. A text that is
    not an email card holds no people."""
    try:
        parts = json.loads(card)
    except (TypeError, ValueError):
        return []
    if not isinstance(parts, list) or len(parts) != 7 or parts[0] != "email":
        return []
    rows = parts[4] if isinstance(parts[4], list) else None
    reply = parts[5] if isinstance(parts[5], dict) else None
    return card_people(rows, reply)


def kept_people(people: list[Person], without: frozenset[int],
                include: frozenset[int]) -> list[Person]:
    """The people that the act covers: each one ticked at the start, minus
    the numbers in *without*, plus the numbers in *include*."""
    return [p for p in people
            if p.number not in without and (p.ticked or p.number in include)]


#: The answers of :func:`choice_problem`.
CHOICE_NO_LIST = "no_list"
CHOICE_BAD_NUMBER = "bad_number"
CHOICE_NOBODY = "nobody"


def choice_problem(people: list[Person], without: frozenset[int],
                   include: frozenset[int]) -> str | None:
    """Why the member's WITH and WITHOUT cannot apply to this card, or None.

    A card with no list takes no number. A number must name a line of the
    card, and no number may be in both lists. A card with To lines must keep
    one of them."""
    if not without and not include:
        return None
    if not people:
        return CHOICE_NO_LIST
    numbers = {p.number for p in people}
    if not (without | include) <= numbers or without & include:
        return CHOICE_BAD_NUMBER
    kept = kept_people(people, without, include)
    if any(p.kind == "to" for p in people) and not any(p.kind == "to" for p in kept):
        return CHOICE_NOBODY
    return None


# ── The member's reply ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class CodeAnswer:
    """A reply that approves or cancels an email act."""

    yes: bool
    #: The code, in upper case, or "" when the reply holds none.
    code: str
    without: frozenset[int] = frozenset()
    include: frozenset[int] = frozenset()
    #: False when the reply holds anything else, or a code twice.
    plain: bool = True


def parse_code_answer(message: str) -> CodeAnswer | None:
    """The member's reply to a code summary, or None for any other message.

    "YES 7K3Q", "send 7K3Q without 2", "YES 7K3Q WITH 3, 4" and "NO". The
    first word decides. A reply that starts with YES and holds anything that
    the grammar does not know is not plain, so it approves nothing."""
    text = str(message or "").upper().replace(",", " ").replace("&", " ")
    words = [w.strip(".!") for w in text.split()]
    words = [w for w in words if w and w != "AND"]
    if not words or (words[0] not in _YES_WORDS and words[0] not in _NO_WORDS):
        return None
    yes = words[0] in _YES_WORDS
    code, plain, mode = "", True, ""
    drop: set[int] = set()
    add: set[int] = set()
    for word in words[1:]:
        if word in (_DROP_WORD, _ADD_WORD):
            mode = word
        elif _NUMBER.match(word) and mode:
            (drop if mode == _DROP_WORD else add).add(int(word))
        elif _CODE.match(word) and not code:
            code = word
        else:
            plain = False
    if not yes and (drop or add):
        plain = False
    return CodeAnswer(yes=yes, code=code, without=frozenset(drop),
                      include=frozenset(add), plain=plain)


def code_tokens(message: str) -> set[str]:
    """Each word of *message* that has the shape of a code, in upper case."""
    return {w for w in re.findall(r"[A-Za-z0-9]+", str(message or "").upper())
            if _CODE.match(w)}


# ── The summary ─────────────────────────────────────────────────────────────

#: What the summary says after a line that starts unticked.
UNTICKED_NOTE = "gets nothing unless you add it with WITH"


def _class_words(cls: str) -> str:
    try:
        from acb_skills.held_card import class_mark
    except ImportError:  # pragma: no cover - acb_skills ships whole
        return ""
    return class_mark(cls) if cls else ""


def _person_line(p: Person) -> str:
    bits = [f"{p.number}. {_plain(p.label)}"]
    marks = [m for m in (p.mark, _class_words(p.cls)) if m]
    if marks:
        bits.append(f"({'; '.join(dict.fromkeys(marks))})")
    if not p.ticked:
        bits.append(f"[{UNTICKED_NOTE}]")
    if p.warning:
        bits.append(f"WARNING: {_plain(p.warning)}")
    return " ".join(bits)


def _reply_lines(reply: dict[str, Any]) -> list[str]:
    original = reply.get("original") if isinstance(reply.get("original"), dict) else {}
    lines = []
    if reply.get("mailbox"):
        lines.append(f"From: {_plain(str(reply['mailbox']))}")
    lines.append(f"Subject: {_plain(str(reply.get('subject') or '(no subject)'))}")
    if original.get("from") or original.get("subject"):
        lines.append(f"It answers: {_plain(str(original.get('from') or ''))} · "
                     f"{_plain(str(original.get('subject') or ''))}")
    lines.append("Reply all" if reply.get("reply_all") else "Reply to the sender")
    lines += [f"Note: {_plain(str(n))}" for n in reply.get("notices") or []]
    return lines


def code_instruction(code: str, has_people: bool) -> str:
    """The last block of the summary: how to approve, change and cancel."""
    lines = [f"Reply YES {code} to approve this, or NO to cancel."]
    if has_people:
        lines.append(f"To leave out a line, add WITHOUT and its number: "
                     f"YES {code} WITHOUT 2.")
        lines.append(f"A line marked [{UNTICKED_NOTE}] gets nothing unless you "
                     f"add WITH and its number: YES {code} WITH 3.")
    lines.append(f"The code works once, for {CODE_TTL_S // 60} minutes, and only "
                 "from this phone. Type it as a new message.")
    return "\n".join(lines)


def _summary_body(act: ParkedAct) -> str:
    people = card_people(act.rows, act.reply)
    head = [f"*{_plain(act.title)}*"]
    if act.detail.strip():
        head.append(_plain(act.detail))
    blocks = ["\n".join(head)]
    if act.reply is not None:
        blocks.append("\n".join(_reply_lines(act.reply)))
    if people:
        blocks.append("\n".join(["People and files, by number:"]
                                + [_person_line(p) for p in people]))
    if act.reply is not None:
        blocks.append("Text:\n" + str(act.reply.get("body") or ""))
    if act.context.strip():
        blocks.append(_plain(act.context))
    return "\n\n".join(blocks)


def code_summary(act: ParkedAct, code: str) -> str:
    """Everything the card shows, as one WhatsApp text, and the code last.

    No line is cut: the whole text of the mail is in it. ``bot_run`` splits a
    long summary over parts with :func:`code_summary_parts`."""
    has_people = bool(card_people(act.rows, act.reply))
    return f"{_summary_body(act)}\n\n{code_instruction(code, has_people)}"


#: Room for the part mark "(10/10)" and its line break.
_PART_MARK_ROOM = 16


def code_summary_parts(act: ParkedAct, code: str, limit: int) -> list[str]:
    """:func:`code_summary` in parts of at most *limit* characters.

    It never drops a character of the body. A part ends at a line break when
    it can, else at a space, else at the limit. The code block is whole in
    the last part, so the part that Meta takes last is the one that offers
    the act. A summary of more than one part numbers each part."""
    whole = code_summary(act, code)
    if len(whole) <= limit:
        return [whole]
    body = _summary_body(act)
    tail = code_instruction(code, bool(card_people(act.rows, act.reply)))
    room = limit - _PART_MARK_ROOM
    pieces: list[str] = []
    rest = body
    while rest:
        if len(rest) <= room:
            pieces.append(rest)
            break
        cut = rest.rfind("\n", 1, room)
        if cut <= 0:
            cut = rest.rfind(" ", 1, room)
        if cut <= 0:
            cut = room
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if pieces and len(pieces[-1]) + 2 + len(tail) <= room:
        pieces[-1] = f"{pieces[-1]}\n\n{tail}"
    else:
        pieces.append(tail)
    total = len(pieces)
    return [f"({i}/{total})\n{p}" for i, p in enumerate(pieces, start=1)]


# ── The park and the approval ───────────────────────────────────────────────


#: The answer of :func:`answer_email_card` that leaves the card to the gate.
NOT_EMAIL = object()


@dataclass
class EmailApproval:
    """The yes for ONE email card at the member's code (:func:`approving_email`).

    The org, the member and the thread come from ``bot_run``, which took
    them from the phone's link row (R5). Never from the message."""

    card: str
    without: frozenset[int]
    include: frozenset[int]
    org: str
    member: str
    thread: str
    used: bool = False
    closed: bool = False


_EMAIL_APPROVING: ContextVar[EmailApproval | None] = ContextVar(
    "whatsapp_email_approving", default=None)


@contextlib.contextmanager
def approving_email(card: str, *, without: frozenset[int] = frozenset(),
                    include: frozenset[int] = frozenset(), org: str,
                    member: str, thread: str) -> Iterator[EmailApproval]:
    """Approve ONE email card whose text is *card*, with the people that the
    member kept. The yes closes on the way out."""
    state = EmailApproval(card=card, without=frozenset(without),
                          include=frozenset(include), org=str(org),
                          member=str(member).strip().lower(), thread=str(thread))
    token = _EMAIL_APPROVING.set(state)
    try:
        yield state
    finally:
        state.closed = True
        _reset(_EMAIL_APPROVING, token)


def _not_yet(rows: Any, reply: Any, with_answer: bool) -> Any:
    """What the card gate gives a tool whose card parked: no approval."""
    if reply is not None or with_answer:
        return None
    return frozenset() if rows is not None else False


async def answer_email_card(title: str, detail: str, context: str,
                            rows: list[dict[str, Any]] | None,
                            reply: dict[str, Any] | None,
                            request_id: str | None, with_answer: bool,
                            cut: bool = False) -> Any:
    """The WhatsApp answer to an email card, or :data:`NOT_EMAIL`.

    * Inside :func:`approving_email`: the yes for the FIRST card whose text
      equals the stored text (:func:`_email_yes`).
    * In a WhatsApp run with writes on, for a tool of :data:`EMAIL_ACTS` that
      the email assistant calls: park the card (one act a run) and give no
      approval.
    * Else :data:`NOT_EMAIL`, and the card gate goes on as before.

    *cut* is True when the gate cut the title, the detail or the context of
    the card. Such a card never parks, because its summary would show less
    than the act sends (:data:`EMAIL_TOO_LONG_TEXT`).
    """
    call = _CALL.get()
    facts = call.facts if call is not None else None
    text = email_card_text(title, detail, context, rows, reply, facts)
    state = _EMAIL_APPROVING.get()
    if state is not None and not state.used and not state.closed and text == state.card:
        state.used = True
        return await _email_yes(state, rows, reply, request_id, with_answer)
    run = current_run()
    if run is None or not run.writes or run.parked is not None:
        return NOT_EMAIL
    if (call is None or call.arguments is None or call.name not in EMAIL_ACTS
            or call.agent != EMAIL_AGENT):
        return NOT_EMAIL
    if reply is not None and not str(reply.get("body") or "").strip():
        # A reply with no text has nothing to approve: no summary, no code.
        return NOT_EMAIL
    if cut:
        call.refused_text = EMAIL_TOO_LONG_TEXT
        return _not_yet(rows, reply, with_answer)
    arguments = _stored_arguments({**call.arguments, **(call.pins or {})})
    if arguments is None:
        return NOT_EMAIL
    run.parked = ParkedAct(tool=call.name, arguments=arguments, card=text,
                           title=title, detail=detail, context=context,
                           kind="code", rows=rows, reply=reply)
    call.parked = True
    call.parked_text = EMAIL_PARKED_TEXT
    return _not_yet(rows, reply, with_answer)


async def _record(state: EmailApproval, request_id: str, answer: str) -> bool:
    """Record the answer as respond-input does for a web card, so the act
    route takes it once (``reply_card.take_reply_answer``)."""
    from acb_skills.reply_card import record_reply_answer

    return await record_reply_answer(state.org, request_id, state.member, answer,
                                     state.thread)


async def _email_yes(state: EmailApproval, rows: list[dict[str, Any]] | None,
                     reply: dict[str, Any] | None, request_id: str | None,
                     with_answer: bool) -> Any:
    """The card gate's yes for the approved email card.

    A plain card gets True. A rows card gets the ids of the kept rows, or a
    ``RowsAnswer`` whose answer is recorded. A reply form gets a
    ``ReplyAnswer`` that sends to the kept people, also recorded. A failed
    record is no approval, so the act route sends nothing."""
    import uuid

    from acb_skills import ask_tools

    if rows is None and reply is None:
        return True
    kept = kept_people(card_people(rows, reply), state.without, state.include)
    if rows is not None:
        ids = [p.key for p in kept]
        if not with_answer:
            return frozenset(ids)
        if not request_id or not ids:
            return None
        answer = ask_tools.ROWS_ANSWER_PREFIX + json.dumps({"rows": ids})
        if not await _record(state, request_id, answer):
            return None
        return ask_tools.RowsAnswer(request_id, answer, frozenset(ids), state.member,
                                    state.thread)
    from acb_skills.reply_card import REPLY_ANSWER_PREFIX, parse_reply_answer

    payload = {"reply": {
        "action": "send", "reply_all": bool((reply or {}).get("reply_all")),
        "body": str((reply or {}).get("body") or ""),
        "to": [p.key for p in kept if p.kind == "to"],
        "cc": [p.key for p in kept if p.kind == "cc"],
        "message_id": str((reply or {}).get("message_id") or ""),
    }}
    answer = REPLY_ANSWER_PREFIX + json.dumps(payload, ensure_ascii=False)
    choice = parse_reply_answer(answer)
    rid = request_id or uuid.uuid4().hex
    if choice is None or not await _record(state, rid, answer):
        return None
    return ask_tools.ReplyAnswer(rid, answer, choice, state.member, state.thread)
