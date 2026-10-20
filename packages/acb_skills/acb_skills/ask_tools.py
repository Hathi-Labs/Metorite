"""HITL elicitation tool — agent asks the user clarifying questions mid-stream.

Provides ``ask_questions`` which mirrors VS Code Copilot Chat's
``vscode_askQuestions`` tool.  The agent pauses execution, the Control Plane
renders an interactive card with structured questions, and the user's answers
are injected as the next user message so the agent can continue with context.

Design (VS Code parity)
-----------------------
- Accepts an array of question objects, each with a ``header``, ``question``
  text, optional ``options`` (single/multi-select), and optional freeform input.
- One question can be marked ``recommended`` to highlight the default choice.
- The tool emits a ``CUSTOM`` AG-UI event (``elicitation_requested``) into the
  active SSE queue; the frontend renders an ``ElicitationCard`` inline.
- The agent MUST stop after calling this tool — the user's response will
  arrive as the next chat message.

Usage by agents::

    await ask_questions(json.dumps({
        "questions": [
            {
                "header": "Target Environment",
                "question": "Should I deploy to staging or production?",
                "options": [
                    {"label": "Staging", "recommended": true},
                    {"label": "Production"}
                ]
            }
        ]
    }))
"""
from __future__ import annotations

import contextlib as _contextlib
import contextvars as _contextvars
import json as _json
import re as _re
from collections.abc import Iterator as _Iterator
from typing import Any

# ── A run with no card channel (WS-47 WAC-3) ──────────────────────────────────
#
# A run that the server starts for a channel with no cards (WhatsApp) has no
# person who can see a card. Path C below would still find the run's thread,
# push the card to a stream that nobody reads and wait an hour on it. So the
# caller of such a run opens :func:`refuse_cards`, and inside it every card
# tool here answers at once: ``request_confirmation`` denies, and
# ``ask_questions`` returns a fixed line. Nothing is pushed, nothing waits,
# and the tool writes nothing, because every class B tool writes only after
# an approval.
#
# A ContextVar, so it is per run. A task that the run starts copies it, and
# ``orchestrator.copilot_agent.carry_run_context`` carries it into the
# Copilot SDK's callbacks. The default is False, so every web run is exactly
# as before. Fence: ``tests/unit/test_wac_bot_run.py`` (A6).
_CARDS_REFUSED: _contextvars.ContextVar[bool] = _contextvars.ContextVar(
    "acb_cards_refused", default=False,
)

# WS-47 WAC-4 adds one exception, and ``request_confirmation`` asks it first:
# ``acb_skills.whatsapp_acts.answer_card`` parks an allowed act for the
# member's Confirm, or answers that Confirm. Every other card is denied here.

#: What ``ask_questions`` returns in a run with no card channel.
NO_CARD_QUESTIONS = (
    "This chat cannot show questions. Ask the member in plain text in your "
    "reply, and stop."
)


@_contextlib.contextmanager
def refuse_cards() -> _Iterator[None]:
    """Deny every card for the run that this scope holds (WS-47 WAC-3)."""
    token = _CARDS_REFUSED.set(True)
    try:
        yield
    finally:
        _CARDS_REFUSED.reset(token)


def cards_refused() -> bool:
    """True inside :func:`refuse_cards`: this run has no card channel."""
    return _CARDS_REFUSED.get()


def refuse_cards_for_run(refused: bool) -> _contextvars.Token[bool]:
    """Set the switch for a run whose scope is not a ``with`` block.

    ``executor.run_agent_stream`` is an async generator, so it holds the
    token from its start to its ``finally`` (review of PR #888, P2).
    *refused* is ``not shows_cards``. A stream that says it shows cards,
    inside a scope that refuses them, stays refused: a run never widens what
    its caller allowed. Give the token to :func:`release_cards_switch`.

    It is a ContextVar, so it covers the callers of ``run_agent_stream`` on
    MAF, whose tools run in the run's context. A Copilot SDK tool that runs
    in a JSON-RPC thread reads a fresh context, so it is NOT covered. The
    email chat route that passed ``shows_cards=False`` is gone (WS-17
    EM-G11f3). The chat pane of the Email app runs on ``/agent/run/stream``."""
    return _CARDS_REFUSED.set(bool(refused) or _CARDS_REFUSED.get())


def release_cards_switch(token: _contextvars.Token[bool]) -> None:
    """Undo :func:`refuse_cards_for_run`. A token from another context sets
    the default, so the switch never leaks into the next run on this task."""
    try:
        _CARDS_REFUSED.reset(token)
    except ValueError:
        _CARDS_REFUSED.set(False)


# Tools that PARK the turn waiting on a human. When a run's last tool is one of
# these, the turn ending with no closing assistant text is CORRECT, not a
# failure: the card these render IS the output, and (as the module docstring
# says) "the agent MUST stop after calling this tool".
#
# Anything reasoning about "the agent produced no text" must consult this set
# first, or it will report a normal question as a truncated answer — and tell
# the user to say "continue" when the right move is to answer the question.
#
# ``ask_user`` is the GitHub Copilot SDK's NATIVE elicitation tool (not defined
# in this module); it is listed because it parks the turn identically.
HITL_BLOCKING_TOOLS: frozenset[str] = frozenset({
    "ask_questions",
    "ask_user",
    "request_confirmation",
    # emit_generative_ui blocks only when the spec sets ``hitl: true``
    # (generative_ui_2 Phase 1) — but listing it unconditionally is correct
    # for every consumer of this set: the per-tool-timeout exemption is
    # harmless for the instant non-blocking emit, and a turn that ends on a
    # rendered UI card with no closing text is a legitimate ending (the card
    # IS the output), exactly like the other card tools above.
    "emit_generative_ui",
})


def is_hitl_blocking_tool(name: str) -> bool:
    """True if *name* is a tool that parks the turn awaiting user input."""
    return (name or "").strip().lower() in HITL_BLOCKING_TOOLS


# ── The text limits of a question card (owner report, 2026-10-13) ────────────
#
# The card cut the question at 200 characters, in the middle of a word and
# with no mark: "…should be in the same plac". The member could not tell
# that text was missing. Now a long text is cut at a word, with an ellipsis,
# and the question may run to 1000 characters, so a cut is rare and never
# silent. The executor's bridge (``orchestrator.executor``) cuts the header
# and the question with the same function. It passes the options on as the
# model wrote them, uncut, so no option loses a word there either.
# Fence: ``tests/unit/test_ask_questions_text.py``.

#: The longest header (a short label) the card keeps.
QUESTION_HEADER_MAX = 50
#: The longest question text the card keeps.
QUESTION_TEXT_MAX = 1000
#: The longest option label the card keeps.
OPTION_LABEL_MAX = 100
#: The longest option description the card keeps.
OPTION_DESCRIPTION_MAX = 200
#: The marks a cut text drops at its end, before the ellipsis: a space,
#: a comma and the like, and the en and em dashes (by code, not as glyphs).
_CUT_MARKS = " ,;:.-" + chr(0x2013) + chr(0x2014)
#: The mark at the end of a cut text.
ELLIPSIS = "…"


#: The shape of the id of a CRM question on a card (``crm_tools``). WS-53
#: CRM-Q33: ``rule:<token>`` is the confirm of a held business rule.
_CRM_QUESTION = _re.compile(
    r"^(revenue|purpose:[A-Za-z][A-Za-z0-9_]{0,99}"
    r"|key:[A-Za-z][A-Za-z0-9_]{0,99}:(amount|date|status|parent)"
    r"|rule:[0-9a-f]{16}"
    # WS-53 §18.16 part 4: the question of a term.
    r"|term:[A-Za-z][A-Za-z0-9_]{0,99}:[a-z0-9-]{1,40})$"
)
#: The shape of the key of an option of a CRM question card.
_CARD_KEY = _re.compile(r"^[@A-Za-z0-9_]{1,100}$")


def crm_card_tag(question: Any) -> str | None:
    """The id of the CRM question that a card asks, or None (WS-53
    CRM-Q32-8). Only a well-formed id passes, so no other text rides along."""
    raw = question.get("crm_question") if isinstance(question, dict) else None
    return raw if isinstance(raw, str) and _CRM_QUESTION.match(raw) else None


def card_option_key(option: Any) -> str | None:
    """The key of an option of a CRM question card, or None."""
    raw = option.get("key") if isinstance(option, dict) else None
    return raw if isinstance(raw, str) and _CARD_KEY.match(raw) else None


def clip_words(text: str, limit: int) -> str:
    """*text*, stripped, and cut at a word to at most *limit* characters.

    A text that fits comes back whole. A longer text is cut at the last
    space inside the limit, and ends with an ellipsis, so the member sees
    that text is missing. A text with no space inside the limit (one very
    long token) is cut at the limit, with the ellipsis. The result is never
    longer than *limit*.
    """
    t = str(text or "").strip()
    if len(t) <= limit:
        return t
    room = max(limit - len(ELLIPSIS), 0)
    head = t[:room]
    cut = head.rstrip()
    # The limit fell inside a word: go back to the space before it. Do so
    # only when that keeps most of the text, because a space near the start
    # would throw away almost all of it.
    inside_word = not t[room].isspace() and not head[-1:].isspace()
    space = cut.rfind(" ")
    if inside_word and space >= room // 2:
        cut = cut[:space]
    cut = cut.rstrip(_CUT_MARKS)
    return f"{cut}{ELLIPSIS}"


def _record_crm_pick(cleaned: list[dict], result: Any) -> None:
    """WS-53 CRM-Q33 fix round 1 (P0): hand the member's answer of a CRM
    question card to ``crm_tools.record_card_answer``. The answer is the
    gateway's, from the member's click, never the model's. Any fault records
    nothing, so a pick then saves nothing."""
    if not any(crm_card_tag(q) for q in cleaned):
        return
    # An empty answer is no pick. A decline ("Not now") goes on, so a
    # CRM card that offers the default can keep it (record_card_answer).
    if not is_declined(result) and not str(
            (result or {}).get("answer", "") if isinstance(result, dict) else "").strip():
        return
    try:
        from acb_skills.crm_tools import record_card_answer

        record_card_answer(cleaned, result)
    except Exception:  # noqa: BLE001 - a pick that is not recorded saves nothing
        return


# ── "Not now": the member declines a card (owner request, 2026-10-18) ────────
#
# The owner wrote: "if I don't want to answer a question for some reason, I
# should be allowed to turn off the card. The agent should take it as input
# that the user does not want to answer the question at the moment and might
# want to do something else later on."
#
# **The envelope.** The chat posts ``POST /agent/respond-input`` with
# ``{"declined": true, "answer": ""}``. The gateway refuses a decline that
# carries an answer, and a ``declined`` that is not a JSON boolean. The
# parked Future resolves with ``{"answer": "", "declined": True}``, and the
# run RESUMES: a decline is an answer, never a cancel. :func:`is_declined` is
# the one reader of it on the run side.
#
# **What the model reads.** :func:`declined_text`, in plain words. It names
# a default only where the question STATES one (:func:`question_defaults`):
# the "Use the default" option of a CRM card, or the card's own ``default``
# field. A recommended option is NOT a default (review of #1152). A decline
# must never become an answer: "Archive the 8 done tasks?", Yes recommended,
# Not now, must not lead to an archive. So a question with no stated default
# tells the model not to act on it, or on anything that depends on it. A CRM
# question card also gets the line that sends its ``@default``, which holds
# the question for the member for 24 hours (``crm_tools``), so the chat does
# not ask it again the next day either.
# Fence: ``tests/unit/test_question_decline.py``.

#: The key of the decline, in the respond-input body and in the result of a
#: parked Future.
DECLINED_KEY = "declined"

#: The key of the option of a CRM card that keeps the default
#: (``crm_tools.DEFAULT_KEY``). A test holds the two equal.
CRM_DEFAULT_KEY = "@default"


def is_declined(result: Any) -> bool:
    """True when a parked Future's *result* is the member's "Not now"."""
    return isinstance(result, dict) and result.get(DECLINED_KEY) is True


def _sentence(text: str) -> str:
    t = " ".join(str(text or "").split()).rstrip(" .")
    return f"{t}." if t else ""


def card_default(q: Any) -> str | None:
    """The ``default`` a model set on a question, cut for the card, or None.

    The ONE reader of the key for a card, so the tool's own paths (A and C)
    and the executor's bridge (B1) draw the same words, and the member SEES
    each default that a decline may name (review of #1152): the card draws
    "If you skip: <default>" under the question.
    """
    said = q.get("default") if isinstance(q, dict) else None
    if not isinstance(said, str) or not said.strip():
        return None
    return clip_words(said, OPTION_DESCRIPTION_MAX)


def stated_default(q: Any) -> str:
    """The default a question STATES, or ``""`` when it states none.

    Two sources only: the "Use the default" option of a CRM card, in the
    words of its description ("Billed, until a CRM admin answers"), and the
    question's own ``default`` field. A recommended option is never a
    default: a decline must never become an answer (review of #1152).
    """
    if not isinstance(q, dict):
        return ""
    options = [o for o in q.get("options") or [] if isinstance(o, dict)]
    kept = next((o for o in options if o.get("key") == CRM_DEFAULT_KEY), None)
    if kept is not None:
        return str(kept.get("description") or kept.get("label") or "").strip()
    # The same words the card draws ("If you skip: ..."), never more.
    return card_default(q) or ""


def question_defaults(questions: Any) -> list[tuple[str, str]]:
    """``(header, default)`` of each question of a card that STATES a
    default (:func:`stated_default`). A question with none is left out."""
    out: list[tuple[str, str]] = []
    for q in questions if isinstance(questions, list) else []:
        said = stated_default(q)
        if said:
            out.append((str(q.get("header") or "").strip(), said))
    return out


def _crm_questions_with_default(questions: Any) -> list[str]:
    """The ids of the CRM questions of a card that offer ``@default``."""
    out: list[str] = []
    for q in questions if isinstance(questions, list) else []:
        qid = crm_card_tag(q)
        if qid and any(isinstance(o, dict) and o.get("key") == CRM_DEFAULT_KEY
                       for o in q.get("options") or []):
            out.append(qid)
    return out


def declined_text(questions: Any = None, *, what: str = "question") -> str:
    """What the model reads when the member pressed "Not now" on a card.

    *questions* are the card's cleaned questions, so the text can name each
    default. *what* names the card: ``question`` or ``form``.
    """
    lines = [
        f"The member chose not to answer this {what} now.",
        "Do not ask it again in this reply.",
    ]
    qs = [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []
    found = question_defaults(qs)
    # A question with no stated default, or a card with no questions to read
    # (an ask_user prompt, a form): the member set it aside, so nothing that
    # needs its answer may happen. Never a guess, never the recommended one.
    open_left = not qs or len(found) < len(qs)
    if len(qs) == 1 and found:
        lines.append("Continue with what you can do without the answer.")
        lines.append(_sentence(f"Use the default that the question states: {found[0][1]}"))
    elif found:
        lines.extend(
            _sentence(f'For "{h or "the question"}", use the default that it states: {d}')
            for h, d in found
        )
    if open_left:
        which = f"this {what}" if not found else "a question with no stated default"
        lines.append(f"Do not act on {which}, or on anything that depends on it.")
        lines.append("Do only the work that does not depend on it, or stop and let the member lead.")
    for qid in _crm_questions_with_default(qs):
        lines.append(
            f'Send the default for the member: crm_answer_question(question_id="{qid}", '
            f'choice="{CRM_DEFAULT_KEY}"). Then Metorite does not ask it again for 24 hours.'
        )
    if found:
        lines.append("Tell the member in one line what you assumed.")
    if open_left:
        lines.append("Tell the member in one line what you did not do.")
    lines.append("The member can come back to it later.")
    return " ".join(lines)


def answer_text(result: Any, questions: Any = None) -> str:
    """The tool result of a question card, from its parked Future's result.

    The one place the three blocking paths of :func:`ask_questions` phrase
    an answer, so a decline reads the same on each of them.
    """
    if is_declined(result):
        return declined_text(questions)
    answer = str((result or {}).get("answer", "") or "").strip() if isinstance(result, dict) else ""
    if not answer:
        return "User provided an empty response."
    return f"User response: {answer}"


def _crm_follow_up(cleaned: list[dict]) -> tuple[float | None, list[dict]]:
    """The early park of a CRM follow-up card, and the events of the note
    that the answer left out (WS-53 §18.16 part 2). ``(None, [])`` for each
    other card. Never raises: a card is never lost to a CRM fault."""
    try:
        from acb_skills.crm_tools import follow_up_park_after, missing_note_events

        park = follow_up_park_after(cleaned)
        return (park, missing_note_events()) if park is not None else (None, [])
    except Exception:  # noqa: BLE001
        return None, []


async def ask_questions(questions: str) -> str:
    """Ask the user one or more clarifying questions before proceeding.

    The questions are rendered as interactive cards in the chat UI.  The user
    can select options, type freeform answers, or both.  Their answers will
    be sent as the next chat message for you to process.

    **Use this tool when:**
    - You need to disambiguate between multiple possible interpretations
    - The user's request is missing a required parameter
    - You want the user to choose from a known set of options
    - A decision has security, cost, or irreversibility implications

    **Do NOT use for:**
    - Simple yes/no confirmations (use your best judgment)
    - Questions you can answer yourself with a web search or tool call

    **After calling this tool, STOP and wait.**  The user's response will
    arrive as the next message in the conversation.

    Args:
        questions: JSON string. ``header`` and ``question`` are required;
            ``options`` is optional (each needs ``label``);
            ``allowFreeformInput`` defaults to true only when there are no
            options. Every question needs an answer. Set
            ``"required": false`` on one the user may skip. Set
            ``"default"`` only to what you use with no answer. Shape:
            {
              "questions": [
                {
                  "header": "Short label (max 50 chars)",
                  "question": "The full question text (aim for 200 chars)",
                  "multiSelect": false,
                  "allowFreeformInput": true,
                  "options": [
                    {"label": "Option A", "description": "What this means",
                     "recommended": true},
                    {"label": "Option B"}
                  ]
                }
              ]
            }

    Returns:
        ``"Questions displayed to the user. Waiting for response."``
        The agent MUST stop after receiving this and let the user answer.
    """
    if cards_refused():
        # WS-47 WAC-3: no card channel. Answer at once, push and park nothing.
        return NO_CARD_QUESTIONS
    try:
        data = _json.loads(questions)
    except (_json.JSONDecodeError, TypeError):
        return (
            "Error: questions must be valid JSON, e.g. "
            '\'{"questions":[{"header":"Confirm","question":"Proceed?"}]}\''
        )

    if not isinstance(data, dict):
        return "Error: questions must be a JSON object with a 'questions' key"

    qs = data.get("questions", [])
    if not isinstance(qs, list) or len(qs) == 0:
        return "Error: 'questions' must be a non-empty array"

    cleaned: list[dict] = []
    for i, q in enumerate(qs):
        if not isinstance(q, dict):
            return f"Error: question {i} is not an object"
        header = clip_words(str(q.get("header", f"Question {i + 1}")), QUESTION_HEADER_MAX)
        question = clip_words(str(q.get("question", "")), QUESTION_TEXT_MAX)
        if not question:
            return f"Error: question {i} has no question text"

        opts_raw = q.get("options", [])
        options: list[dict] = []
        if isinstance(opts_raw, list):
            for o in opts_raw:
                if isinstance(o, dict) and o.get("label"):
                    option = {
                        "label": clip_words(str(o["label"]), OPTION_LABEL_MAX),
                        "description": clip_words(str(o.get("description", "")), OPTION_DESCRIPTION_MAX) or None,
                        "recommended": bool(o.get("recommended", False)),
                    }
                    # WS-53 CRM-Q32-8: the key of an option of a CRM
                    # question card. Only when the card names its question.
                    if crm_card_tag(q) and card_option_key(o):
                        option["key"] = card_option_key(o)
                    options.append(option)

        has_options = len(options) > 0
        entry = {
            "header": header,
            "question": question,
            "multiSelect": bool(q.get("multiSelect", False)),
            "allowFreeformInput": bool(q.get(
                "allowFreeformInput", not has_options,
            )),
            "options": options if has_options else None,
        }
        # A question the member may skip never blocks the card's Submit
        # (owner report, 2026-10-17). The key goes only when it is False,
        # so every other card is exactly as before.
        if q.get("required") is False:
            entry["required"] = False
        # The default the question states, if any: what a "Not now" may use
        # (``stated_default``). The card draws it, so the member sees it. The
        # key goes only when the model set it.
        if card_default(q):
            entry["default"] = card_default(q)
        # WS-53 CRM-Q32-8: a CRM question card keeps the id of its question,
        # so a late answer of the member can be tied to it (the gateway).
        if crm_card_tag(q):
            entry["crm_question"] = crm_card_tag(q)
        cleaned.append(entry)

    # ── Emit elicitation event ──────────────────────────────────────────
    # Two paths, unified on the same _pending_user_input mechanism as the
    # Copilot SDK's native ask_user tool:
    #
    # Path A — MAF Tier 2 (blocking):  When _active_run_queue is set we
    #   are running inside the executor's instrumented batch path.  We
    #   create a Future, park on it, and return the user's answer directly
    #   as the tool result.  The agent truly blocks — same reliability as
    #   the Copilot SDK's native ask_user.  The frontend POSTs to
    #   /api/agent/respond-input to resolve the Future.
    #
    # Path B — Copilot SDK / standalone (non-blocking):  The elicitation
    #   card renders and the answer arrives as the next chat message.
    #   The agent must self-regulate (stop after the tool call).
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _active_run_queue,
            _pending_user_input,
        )
        queue = _active_run_queue.get(None)
    except Exception:  # noqa: BLE001
        queue = None

    # WS-53 §18.16 part 2: the follow-up card of a CRM answer comes AFTER
    # the answer, so it never holds the run. The wait parks the run at once,
    # and the member's later pick takes the late path of CRM-Q32-8.
    _park_after, _notes = _crm_follow_up(cleaned)

    if queue is not None:
        # ── Path A: blocking Future (MAF Tier 2) ─────────────────────
        import asyncio as _asyncio
        import uuid as _uuid

        _request_id = _uuid.uuid4().hex
        _loop = _asyncio.get_running_loop()
        _fut: "_asyncio.Future[dict[str, object]]" = _loop.create_future()
        _pending_user_input[_request_id] = _fut

        for _note in _notes:
            # The note of the answer goes before the card, because a parked
            # run never reaches the add before RUN_FINISHED.
            await queue.put(_note)
        await queue.put({
            "type": "CUSTOM",
            "name": "elicitation_requested",
            "value": {"questions": cleaned, "request_id": _request_id},
        })

        try:
            # Heartbeating wait (audit R3): touches the relay ACTIVE flag
            # between 60s slices so a long park doesn't lapse the stream.
            from orchestrator.executor import wait_user_future  # noqa: PLC0415
            _result = await wait_user_future(_fut, 3600, park_after=_park_after)
        except _asyncio.TimeoutError:
            _pending_user_input.pop(_request_id, None)
            return "User did not respond in time."

        _record_crm_pick(cleaned, _result)
        return answer_text(_result, cleaned)

    # ── Path B: Copilot SDK / standalone ──────────────────────────────
    # Two sub-paths:
    #
    # B1 — Executor-bridged blocking (preferred):  When the executor
    #   detected this ask_questions call and pre-created a Future in
    #   _pending_user_input (signalled via _active_elicitation_request_id
    #   ContextVar), we block on that Future.  The user's answer resolves
    #   the Future; we return the answer as the tool result; the LLM
    #   continues and produces text.  This prevents the "chat dies without
    #   output" bug that occurred when Path B returned immediately.
    #
    # B2 — Legacy non-blocking (fallback):  When no Future was pre-created
    #   (standalone use or executor bridge unavailable), emit the CUSTOM
    #   event (if queue available) and return immediately.  The answer
    #   arrives as the next chat message.
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _active_elicitation_request_id,
            _pending_user_input,
        )
        _req_id = _active_elicitation_request_id.get(None)
        # ── Briefly poll for the executor bridge ──────────────────
        # The executor may set this ContextVar concurrently with tool
        # execution. When it IS visible (native-MAF, and Copilot when the
        # context happens to carry over) B1 is the preferred path — its Future is
        # keyed to the tool_call_id for correct cleanup. But on the Copilot SDK
        # the tool runs in a thread-hopped context that usually RESETS this
        # ContextVar, so it stays None; a short poll then falls through to the
        # thread-hop-safe Path C below (resolve_relay_thread_id) rather than
        # stalling. 0.5s is ample — if it's going to appear it appears at once.
        if _req_id is None:
            import asyncio as _asyncio
            _deadline = _asyncio.get_running_loop().time() + 0.5
            while _req_id is None:
                await _asyncio.sleep(0.05)
                _req_id = _active_elicitation_request_id.get(None)
                if _asyncio.get_running_loop().time() >= _deadline:
                    break
        if _req_id:
            _fut = _pending_user_input.get(_req_id)
            if _fut is not None:
                # ── B1: blocking bridge ──────────────────────────
                import asyncio as _asyncio
                try:
                    from orchestrator.executor import (  # noqa: PLC0415
                        wait_user_future,
                    )
                    _result = await wait_user_future(_fut, 3600, park_after=_park_after)
                except _asyncio.TimeoutError:
                    return "User did not respond in time."
                _record_crm_pick(cleaned, _result)
                return answer_text(_result, cleaned)
    except Exception:  # noqa: BLE001
        pass

    # ── Path C: self-contained blocking (universal fallback) ──────────
    # Works when neither _active_run_queue (Tier 2) nor
    # _active_elicitation_request_id (Tier 1.5) is set — covers
    # Tier 1 MAF AG-UI streaming and standalone calls.  Pushes the
    # elicitation event directly to the Redis relay and blocks on
    # a self-created Future until the user answers.
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _pending_user_input,
            _push_sse_to_stream,
            resolve_relay_thread_id,
        )
        # resolve_relay_thread_id survives the Copilot SDK thread hop (the raw
        # _stream_relay_thread_id ContextVar does NOT) — so this blocking path now
        # engages for BOTH Copilot-SDK and native-MAF agents.
        _tid = resolve_relay_thread_id()
        if _tid:
            import asyncio as _asyncio
            import uuid as _uuid

            _request_id = _uuid.uuid4().hex
            _loop = _asyncio.get_running_loop()
            _fut: "_asyncio.Future[dict[str, object]]" = (
                _loop.create_future())
            _pending_user_input[_request_id] = _fut

            _payload = _json.dumps({
                "type": "CUSTOM",
                "name": "elicitation_requested",
                "value": {
                    "questions": cleaned,
                    "request_id": _request_id,
                },
            })
            _line = f"data: {_payload}\n\n"
            for _note in _notes:
                await _push_sse_to_stream(_tid, f"data: {_json.dumps(_note)}\n\n")
            await _push_sse_to_stream(_tid, _line)

            try:
                from orchestrator.executor import (  # noqa: PLC0415
                    wait_user_future,
                )
                _result = await wait_user_future(_fut, 3600, thread_id=_tid,
                                                 park_after=_park_after)
            except _asyncio.TimeoutError:
                _pending_user_input.pop(_request_id, None)
                return "User did not respond in time."
            finally:
                _pending_user_input.pop(_request_id, None)

            _record_crm_pick(cleaned, _result)
            return answer_text(_result, cleaned)
    except Exception:  # noqa: BLE001
        pass

    # ── B2: legacy non-blocking (last resort) ─────────────────────────
    try:
        from orchestrator.executor import _active_run_queue  # noqa: PLC0415
        queue_b = _active_run_queue.get(None)
        if queue_b is not None:
            await queue_b.put({
                "type": "CUSTOM",
                "name": "elicitation_requested",
                "value": {"questions": cleaned},
            })
    except Exception:  # noqa: BLE001
        pass

    return (
        "Questions displayed to the user. "
        "Waiting for response — do NOT continue until you receive "
        "the user's answers in the next message."
    )


#: The event that closes a confirmation card. ``answer`` is one of
#: :data:`CONFIRMATION_ANSWERS`. The client drops the card whose
#: ``request_id`` it names, live and on a replay. Fence:
#: ``tests/unit/test_genui_hitl.py`` (the ``confirmation_resolved`` tests).
CONFIRMATION_RESOLVED = "confirmation_resolved"
CONFIRMATION_ANSWERS: frozenset[str] = frozenset(
    {"APPROVE", "REJECT", "TIMEOUT", "CANCELLED"}
)


#: A confirmation card with ROWS (WS-46 P13 one-card). The member ticks the
#: rows the act covers, and the answer names them:
#: ``APPROVE {"rows": ["row-1", "row-3"]}``. A card with no rows keeps the
#: plain ``APPROVE``. The client builds the answer in
#: ``workbench/control_plane/src/lib/confirmationQueue.ts`` (``approveRows``),
#: and ``tests/unit/test_confirmation_rows.py`` holds the two in step.
ROWS_ANSWER_PREFIX = "APPROVE "
#: The most rows one card may carry. A card nobody reads is not consent.
MAX_CARD_ROWS = 100
_ROW_ID_MAX = 64
#: The longest row label the card draws. A tool whose label is its write
#: (a task title) refuses a longer one, because a cut card is not consent.
ROW_LABEL_MAX = 500
#: A hint carries every fact the row writes, so it is as long as a card body.
_ROW_HINT_MAX = 4000


def clean_card_rows(rows: list[dict] | None) -> list[dict] | None:
    """The rows of a card, checked and clipped, or ``None`` for a card with none.

    Each row is ``{id, label, hint?, checked?}``. An id is a short string that
    is unique on the card. A bad row is a programming error of the calling
    tool, so it raises ``ValueError`` before any card is drawn.
    """
    if rows is None:
        return None
    if not isinstance(rows, list) or not rows or len(rows) > MAX_CARD_ROWS:
        raise ValueError(f"rows is a list of 1 to {MAX_CARD_ROWS} rows")
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        rid = str((row or {}).get("id") or "").strip() if isinstance(row, dict) else ""
        if not rid or len(rid) > _ROW_ID_MAX or rid in seen:
            raise ValueError("each row needs its own short id")
        seen.add(rid)
        out.append({
            "id": rid,
            "label": str(row.get("label") or rid).strip()[:ROW_LABEL_MAX],
            "hint": str(row.get("hint") or "").strip()[:_ROW_HINT_MAX],
            "checked": row.get("checked", True) is not False,
        })
    return out


#: The longest answer a rows card can honestly send: the prefix, the JSON
#: frame, and every row id of the largest card, each quoted, with a comma
#: and a space. A longer answer is refused before the parse.
MAX_ROWS_ANSWER = len(ROWS_ANSWER_PREFIX) + len('{"rows": []}') + MAX_CARD_ROWS * (
    _ROW_ID_MAX + 4
)


def ticked_rows(answer: str, offered: frozenset[str]) -> frozenset[str] | None:
    """The row ids an answer approves, or ``None`` when it approves nothing.

    Only ``APPROVE {"rows": [...]}`` approves a rows card. The ids must be a
    non-empty subset of the card's own ids. A forged id, a list that is
    empty, an answer that is not JSON, and a plain ``APPROVE`` each approve
    NOTHING: the whole answer is refused, never trimmed to the ids that fit.

    The answer is bounded before the parse (PR #691 review): it is at most
    :data:`MAX_ROWS_ANSWER` characters, and it names no more ids than the
    card offered.
    """
    text = str(answer or "").strip()
    if not text.upper().startswith(ROWS_ANSWER_PREFIX):
        return None
    if len(text) > MAX_ROWS_ANSWER:
        return None
    try:
        body = _json.loads(text[len(ROWS_ANSWER_PREFIX):])
    except (TypeError, ValueError, RecursionError):
        return None
    ids = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        return None
    if len(ids) > len(offered):
        return None
    ticked = frozenset(ids)
    if not ticked <= offered:
        return None
    return ticked


# ── The reply form of a confirmation card (EM-CR, email_app_master_plan §18) ──
#
# ``request_confirmation(..., reply=form)`` draws the ONE confirmation card with
# a reply form in its body: the mail it answers, the recipients the gateway
# derived for Reply and for Reply all, a toggle, the editable body, and Send,
# Save to Drafts and Discard. Its answer is ``acb_skills.reply_card``'s
# ``APPROVE {"reply": {...}}``. A client that does not know the form draws a
# plain card, and its plain ``APPROVE`` is no reply answer, so nothing acts.

#: The longest text of one field of the form, but the body.
_REPLY_FIELD_MAX = 500
#: The longest excerpt of the mail the card answers.
_REPLY_EXCERPT_MAX = 1200


def _clip_line(value: Any, limit: int = _REPLY_FIELD_MAX) -> str:
    """One line of the form, cut at a word with an ellipsis (:func:`clip_words`)."""
    return clip_words(" ".join(str(value or "").split()), limit)


#: The longest address the form takes. A longer one is refused, never cut.
_REPLY_ADDRESS_MAX = 320


def _clean_recipients(value: Any) -> dict[str, list[str]]:
    """The To and Cc lists of one choice. An address is never cut and a list
    is never shortened, because the card must show each recipient in full.
    A list that is too long, or an address that is too long, raises
    ``ValueError`` before any card is drawn."""
    from acb_skills.reply_card import MAX_REPLY_RECIPIENTS

    v = value if isinstance(value, dict) else {}
    out: dict[str, list[str]] = {}
    for name in ("to", "cc"):
        items = v.get(name) if isinstance(v.get(name), list) else []
        if len(items) > MAX_REPLY_RECIPIENTS:
            raise ValueError("a reply form names too many recipients")
        clean = [" ".join(str(a or "").split()) for a in items]
        if any(len(a) > _REPLY_ADDRESS_MAX for a in clean):
            raise ValueError("a reply form names an address that is too long")
        out[name] = [a for a in clean if a]
    return out


def clean_reply_form(form: dict | None) -> dict | None:
    """The reply form of a card, checked and clipped, or None for no form.

    The recipients are the gateway's, for Reply and for Reply all. The card
    shows them in full and never edits them. A form with no Reply recipient
    is a programming error of the calling tool, so it raises ``ValueError``
    before any card is drawn. The body is the draft, up to
    ``reply_card.MAX_REPLY_BODY``, cleaned of each hidden character
    (``reply_card.clean_reply_body``), so the card shows all of it.
    ``message_id`` names the email, and the answer echoes it. Each
    ``notices`` line says what the card must not hide: a Reply-To that is
    not the sender, and the text that the send adds.
    """
    if form is None:
        return None
    from acb_skills.reply_card import MAX_REPLY_BODY, clean_reply_body

    if not isinstance(form, dict):
        raise ValueError("reply is a dict")
    recipients = form.get("recipients") if isinstance(form.get("recipients"), dict) else {}
    reply = _clean_recipients(recipients.get("reply"))
    reply_all = _clean_recipients(recipients.get("reply_all"))
    if not reply["to"] or not reply_all["to"]:
        raise ValueError("a reply form needs a recipient")
    original = form.get("original") if isinstance(form.get("original"), dict) else {}
    message_id = " ".join(str(form.get("message_id") or "").split())
    if not message_id or len(message_id) > 200:
        raise ValueError("a reply form names the email it answers")
    notices = form.get("notices") if isinstance(form.get("notices"), list) else []
    if len(notices) > 4:
        raise ValueError("a reply form has at most four notices")
    body = clean_reply_body(str(form.get("body") or ""))
    if len(body) > MAX_REPLY_BODY:
        # Never cut: the card must show each character that the send carries.
        raise ValueError("a reply draft is too long for the card")
    return {
        "message_id": message_id,
        "mailbox": _clip_line(form.get("mailbox")),
        "subject": _clip_line(form.get("subject")),
        "original": {
            "from": _clip_line(original.get("from")),
            "subject": _clip_line(original.get("subject")),
            "received": _clip_line(original.get("received"), 64),
            "excerpt": str(original.get("excerpt") or "").strip()[:_REPLY_EXCERPT_MAX],
        },
        "recipients": {"reply": reply, "reply_all": reply_all},
        "reply_all": form.get("reply_all") is True,
        "body": body,
        "note": _clip_line(form.get("note")),
        # A notice names addresses, so it is never cut: an over-long one fails.
        "notices": [_whole_line(n) for n in notices if str(n or "").strip()],
    }


def _whole_line(value: Any) -> str:
    """One notice line, whole. A line that does not fit is a programming error
    of the calling tool, because a cut line could hide an address."""
    line = " ".join(str(value or "").split())
    if len(line) > 1000:
        raise ValueError("a reply form notice is too long")
    return line


class _Revised:
    """The member wrote in the chat while the card waited (:data:`REVISED`)."""

    __slots__ = ()

    def __bool__(self) -> bool:  # no approval, for each caller that tests truth
        return False

    def __repr__(self) -> str:
        return "REVISED"


#: What :func:`request_confirmation` gives when ``close_on_steer`` is on and
#: the member wrote in the chat while the card waited. It is falsy, so a
#: caller that only asks "approved?" reads it as no. The member's words reach
#: the model on the tool's result (``orchestrator.steer.decorate_tool_result``),
#: so the agent can change the mail and show a new card. Without this, the
#: words waited behind the card, and the card kept the old text.
REVISED = _Revised()

#: The closing answer of a card that a steer closed.
REVISED_ANSWER = "REVISED"

#: How often a waiting card looks for a new steer, in seconds.
_STEER_POLL_S = 0.5


def _steer_marks(thread_id: str) -> frozenset[int] | None:
    """The waiting steer notes of *thread_id*, or None with no steer seam."""
    if not thread_id:
        return None
    try:
        from orchestrator.steer import guidance_marks  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - no orchestrator: nothing can steer
        return None
    return guidance_marks(thread_id)


async def _close_on_new_steer(fut: Any, thread_id: str, before: frozenset[int]) -> None:
    """Answer *fut* as revised when a NEW steer for *thread_id* arrives.

    The card closes as the member writes, and the run goes on. Only a note
    that is not in *before* counts, so a note that waited before the card
    cannot close each new card at once. The caller reads *before* before the
    card can reach the member, so a fast answer is never in it."""
    import asyncio as _asyncio

    while not fut.done():
        await _asyncio.sleep(_STEER_POLL_S)
        now = _steer_marks(thread_id) or frozenset()
        if (now - before) and not fut.done():
            fut.set_result({"answer": REVISED_ANSWER, "revised": True})
            return


class ReplyAnswer:
    """The member's answer to a reply card: the exact text, and its parse.

    ``answer`` is what the member's browser posted. The tool hands it to the
    gateway unchanged, and the gateway checks it against its own record
    (``reply_card.take_reply_answer``). ``responder`` is the gateway's
    session email of the member who answered.
    """

    __slots__ = ("answer", "choice", "request_id", "responder", "thread_id")

    def __init__(self, request_id: str, answer: str, choice: Any, responder: str,
                 thread_id: str = "") -> None:
        self.request_id = request_id
        self.answer = answer
        self.choice = choice
        self.responder = responder
        #: The thread that owns the card: the key of the gateway's record.
        self.thread_id = thread_id


def _was_parked(request_id: str) -> bool:
    """True when the run parked this card (WS-51 S2). It must stay open."""
    try:
        from orchestrator.pending_ask import was_parked  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    return was_parked(request_id)


def _resolved_event(request_id: str, answer: str) -> dict:
    return {
        "type": "CUSTOM",
        "name": CONFIRMATION_RESOLVED,
        "value": {"request_id": request_id, "answer": answer},
    }


def confirmation_channel_open() -> bool:
    """True when this run has a channel that can show a card to a member.

    It reads the two channels that :func:`request_confirmation` tries: path A
    (``_active_run_queue``) and path C (the relay thread). A tool reads it
    AFTER a denial, to tell a refusal from a run with no live chat (WS-17
    EM-T13a). A tool may also read it BEFORE a card, but only to choose a
    narrower act that writes less: ``draft_reply`` of the email assistant
    returns an unsaved draft when no card can show (WS-17 EM-G11c1). It
    never approves a write. ``request_confirmation`` stays the one gate,
    and it fails closed, so a drift here can change a message or skip a
    write, and never adds one.
    A run inside :func:`refuse_cards` has no channel (WS-47 WAC-3).
    """
    if cards_refused():
        return False
    try:
        from orchestrator.executor import (
            _active_run_queue,
            resolve_relay_thread_id,
        )
        if _active_run_queue.get(None) is not None:
            return True
        return bool(resolve_relay_thread_id())
    except Exception:
        return False


def _whatsapp_run() -> bool:
    """True for a run of the WhatsApp channel, which draws no card. A fault
    reads True, so a reply form fails closed (EM-CR)."""
    try:
        from acb_skills.whatsapp_ui import current_run
    except ImportError:
        return False
    try:
        return current_run() is not None
    except Exception:
        return True


def reply_card_channel_open() -> bool:
    """True when this run can show the reply card (EM-CR): a card channel
    (:func:`confirmation_channel_open`) and no WhatsApp run. The reply tool
    asks it BEFORE it drafts, so a run with no card drafts, saves and sends
    nothing."""
    return confirmation_channel_open() and not _whatsapp_run()


def _whatsapp_answer(title: str, detail: str, context: str,
                     rows: list[dict] | None) -> bool | frozenset[str] | None:
    """``acb_skills.whatsapp_acts.answer_card``, or None (WS-47 WAC-4).

    A card with rows gets an empty set, never an approval. A fault here is
    no answer, so the card falls to the gate, which denies inside
    ``refuse_cards``. It fails closed.
    """
    try:
        from acb_skills.whatsapp_acts import answer_card
        answer = answer_card(title, detail, context, rows)
    except Exception:
        return None
    if answer is None:
        return None
    return frozenset() if rows is not None else answer


async def request_confirmation(
    title: str, detail: str = "", context: str = "",
    non_interactive_default: str = "deny",
    rows: list[dict] | None = None,
    fenced: bool | None = None,
    reply: dict | None = None,
    close_on_steer: bool | None = None,
) -> bool | frozenset[str] | ReplyAnswer | _Revised | None:
    """Emit a HITL confirmation card and BLOCK until the user approves/rejects.

    Renders a ``ConfirmationCard`` inline in the chat with Approve / Reject
    buttons and parks the agent on a Future (same blocking mechanism as
    :func:`ask_questions`).  Use this to gate an outward-facing or irreversible
    action — e.g. before actually sending an email.

    Args:
        title: short card heading, e.g. ``"Send this email?"``.
        detail: one-line summary, e.g. ``"To a@b.com · Subject: Hi"``.
        context: longer preformatted body shown in a scrollable block
            (e.g. the email body the user is about to send).
        non_interactive_default: what happens when there is NO channel to
            deliver the card (a non-interactive/automated run).  The default
            ``"deny"`` FAILS CLOSED — destructive actions never auto-approve
            without a human (HH-2, OWASP LLM06 excessive agency).  Pass
            ``"approve"`` ONLY for reversible actions where automation must
            proceed unattended.
        rows: optional ``[{id, label, hint, checked}]``. The card draws one
            checkbox per row, ticked when ``checked``, and Approve then
            names the ticked ids (:func:`ticked_rows`). With no rows the
            card, its event and its answer are exactly as before.
        fenced: ``True`` when the card's text wraps member values in
            «guillemets» (``skill_projects.client.data``). The client then
            draws each fenced value as a token, without the marks. With no
            flag the client draws the text exactly as sent, so an email body
            that holds a real « » keeps it. ``None`` leaves the event as it
            was.
        reply: optional reply form (:func:`clean_reply_form`, EM-CR). The
            card draws the form, and its Approve carries the member's choice
            and body. Neither ``rows`` nor the WhatsApp profile goes with it:
            a run with no card, a WhatsApp run included, gets ``None`` at
            once, whatever ``non_interactive_default`` says.
        close_on_steer: close the card when the member writes in the chat
            while it waits, and give :data:`REVISED`. On by default for a
            reply form. A send card turns it on, so "make it shorter" changes
            the mail instead of waiting behind the old card.

    Returns:
        With no ``rows``: ``True`` if the user approved, ``False`` if they
        rejected or did not respond.  When no delivery channel exists,
        returns ``True`` only if ``non_interactive_default="approve"`` was
        explicitly passed.

        With ``rows``: the ``frozenset`` of the ticked row ids, each one an
        id the card offered. It is EMPTY, and so falsy, when the member did
        not approve, or when the answer named an id the card did not offer.

        Inside :func:`refuse_cards` (WS-47 WAC-3) it denies at once, before
        any channel is tried, whatever ``non_interactive_default`` says.
        One exception comes first (WS-47 WAC-4): the WhatsApp profile parks
        an allowed act and answers False, or answers the Confirm of a parked
        act (``acb_skills.whatsapp_acts.answer_card``).

        With ``reply``: a :class:`ReplyAnswer` when the member pressed Send
        or Save to Drafts, and ``None`` for Discard, a timeout, an answer
        that is not a reply answer, and a run with no card.
    """
    _title = str(title or "Confirm action").strip()[:120]
    _detail = str(detail or "").strip()[:500]
    _context = str(context or "").strip()[:4000]
    _rows = clean_card_rows(rows)
    _reply = clean_reply_form(reply)
    if _reply is not None:
        if _rows is not None:
            raise ValueError("a card has rows or a reply form, never both")
        # No WhatsApp profile and no default: a reply needs the card.
        if cards_refused() or _whatsapp_run():
            return None
    # WS-47 WAC-4: the WhatsApp profile answers first. It parks an allowed
    # act ("not yet"), or answers the Confirm of a parked one. None leaves
    # the card to the gate below. It never approves a card with rows.
    _profile = _whatsapp_answer(_title, _detail, _context, _rows)
    if _profile is not None:
        return _profile
    if cards_refused():
        # A deny, never an approve: a channel with no card is no consent.
        return frozenset() if _rows is not None else False
    _offered = frozenset(r["id"] for r in _rows or [])
    _steer_closes = (_reply is not None) if close_on_steer is None else bool(close_on_steer)

    def _event(request_id: str) -> dict:
        value = {
            "title": _title,
            "detail": _detail,
            "context": _context,
            "request_id": request_id,
        }
        if _rows is not None:
            value["rows"] = _rows
        if _reply is not None:
            value["reply"] = _reply
        if fenced is not None:
            value["fenced"] = bool(fenced)
        return {"type": "CUSTOM", "name": "confirmation_requested", "value": value}

    _replied: list[ReplyAnswer] = []
    #: The thread that owns each card of this call (EM-CR): the key of the
    #: gateway's record of a reply answer.
    _card_thread: dict[str, str] = {}

    def _verdict(result: dict, rid: str = "") -> tuple[str, frozenset[str] | None]:
        """The card's closing answer, and the ticked ids of a rows card."""
        said = str(result.get("answer", "")).strip()
        if result.get("revised") is True:
            return REVISED_ANSWER, None
        if _reply is not None:
            from acb_skills.reply_card import parse_reply_answer

            # The exact text, not the stripped one: the gateway's record holds
            # the digest of what the browser posted.
            raw = str(result.get("answer", ""))
            choice = parse_reply_answer(raw)
            if choice is None:
                return "REJECT", None
            _replied.append(ReplyAnswer(rid, raw, choice,
                                        str(result.get("responder") or ""),
                                        _card_thread.get(rid, "")))
            return "APPROVE", None
        if _rows is None:
            return ("APPROVE" if said.upper() == "APPROVE" else "REJECT"), None
        ticked = ticked_rows(said, _offered)
        return ("APPROVE" if ticked else "REJECT"), ticked

    def _steer_baseline() -> tuple[str, frozenset[int]] | None:
        """The thread and its waiting notes, read BEFORE the card is sent."""
        if not _steer_closes:
            return None
        try:
            from orchestrator.executor import resolve_relay_thread_id  # noqa: PLC0415
            tid = resolve_relay_thread_id() or ""
        except Exception:  # noqa: BLE001
            return None
        marks = _steer_marks(tid)
        return None if marks is None else (tid, marks)

    async def _block_on(_fut, _rid, _pending, _publish,
                        _baseline=None) -> bool | frozenset[str] | ReplyAnswer | _Revised | None:
        """Park on the answer, then say on the stream that the card is closed.

        A model can call card-gated tools in parallel, so one stream can carry
        many ``confirmation_requested`` cards at once, and a replay sends all
        of them again. With no closing event, the client cannot tell an
        answered card from a waiting one. It showed the answered card again
        (2026-10-06). ``_publish`` sends ``confirmation_resolved`` on the
        stream that carried the card, on every way out: an answer, a timeout
        and a cancel.
        """
        import asyncio as _asyncio
        import contextlib as _contextlib
        # Any other failure is no approval, so it closes the card as a REJECT.
        _answer = "REJECT"
        _ticked: frozenset[str] | None = None
        _watch = None
        if _baseline is not None:
            _watch = _asyncio.ensure_future(_close_on_new_steer(_fut, *_baseline))
        try:
            from orchestrator.executor import wait_user_future  # noqa: PLC0415
            _result = await wait_user_future(_fut, 3600)
            _answer, _ticked = _verdict(_result, _rid)
        except _asyncio.CancelledError:
            _answer = "CANCELLED"
            raise
        except TimeoutError:
            _answer = "TIMEOUT"
        finally:
            if _watch is not None:
                _watch.cancel()
            _pending.pop(_rid, None)
            # Best effort: the run's end clears a blocking card too. A PARKED
            # card (WS-51 S2) is not closed: it stays open for a late answer,
            # which a new run takes (``orchestrator.pending_ask``).
            with _contextlib.suppress(Exception):
                if not _was_parked(_rid):
                    await _publish(_resolved_event(_rid, _answer))
        if _answer == REVISED_ANSWER:
            return frozenset() if _rows is not None else REVISED
        if _reply is not None:
            return _replied[0] if _answer == "APPROVE" and _replied else None
        if _rows is not None:
            return _ticked or frozenset()
        return _answer == "APPROVE"

    # ── Path A: _active_run_queue (native MAF / Tier 2 blocking) ──────────
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _active_run_queue,
            _pending_user_input,
        )
        queue = _active_run_queue.get(None)
    except Exception:  # noqa: BLE001
        queue = None
    if queue is not None:
        import asyncio as _asyncio
        import uuid as _uuid

        _rid = _uuid.uuid4().hex
        _fut = _asyncio.get_running_loop().create_future()
        _pending_user_input[_rid] = _fut
        _card_thread[_rid] = str(getattr(_pending_user_input, "owner_of", lambda _r: "")(_rid)
                                 or "")
        _base = _steer_baseline()
        await queue.put(_event(_rid))
        return await _block_on(_fut, _rid, _pending_user_input, queue.put, _base)

    # ── Path C: Redis relay (blocking on BOTH Copilot-SDK and native MAF) ──
    # resolve_relay_thread_id survives the Copilot SDK thread hop that resets the
    # _stream_relay_thread_id ContextVar. WITHOUT this, a Copilot agent's
    # request_confirmation found no thread_id, skipped this path, and fell through
    # to the non-interactive default below — silently DENYING the action without
    # ever showing the user the confirmation card. This is the safety gate for
    # send_email etc., so blocking must work on the Copilot path too.
    try:
        from orchestrator.executor import (  # noqa: PLC0415
            _pending_user_input,
            _push_sse_to_stream,
            resolve_relay_thread_id,
        )
        _tid = resolve_relay_thread_id()
        if _tid:
            import asyncio as _asyncio
            import uuid as _uuid

            _rid = _uuid.uuid4().hex
            _fut = _asyncio.get_running_loop().create_future()
            _pending_user_input[_rid] = _fut
            _card_thread[_rid] = str(_tid)
            _line = f"data: {_json.dumps(_event(_rid))}\n\n"
            _base = _steer_baseline()
            await _push_sse_to_stream(_tid, _line)

            async def _publish(ev: dict, _t: str = _tid) -> None:
                await _push_sse_to_stream(_t, f"data: {_json.dumps(ev)}\n\n")

            return await _block_on(_fut, _rid, _pending_user_input, _publish, _base)
    except Exception:  # noqa: BLE001
        pass

    # No delivery channel (non-interactive run) — fail CLOSED unless the
    # caller explicitly opted a reversible action into auto-approval. A reply
    # form never auto-approves: there is no body the member submitted.
    if _reply is not None:
        return None
    approved = str(non_interactive_default).strip().lower() == "approve"
    if _rows is not None:
        return frozenset(r["id"] for r in _rows if r["checked"]) if approved else frozenset()
    return approved
