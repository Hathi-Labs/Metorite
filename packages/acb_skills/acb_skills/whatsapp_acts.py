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

Fences: ``tests/unit/test_wac_writes.py`` and ``tests/unit/test_wac_writes_r8.py``.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from acb_skills.whatsapp_ui import OutMessage, current_run

#: The acts that may go by button: the tool name, and the module that
#: defines it. A tool of the same name in another module is not the act.
#: The first slice is ``create_task`` only (class B, one card, no rows).
ALLOWED_ACTS: dict[str, str] = {"create_task": "skill_projects.writes"}

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
    """:data:`PARKED_TEXT` when the running tool's card was just parked."""
    call = _CALL.get()
    return PARKED_TEXT if call is not None and call.parked else None


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
