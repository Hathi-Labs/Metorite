"""The claim check of a chat answer (HRD-13, task 2992). Shadow only.

Spec: ``project-docs/specs/chat_failure_modes.md`` §4.4 rows D1 and D2, §6
row HRD-13, and §8 D-HRD-6 (D217): the check never changes an answer.

An answer can say that an act happened when no tool did it ("I sent the
email"). It can doubt an act that did happen, or promise a later act that
nothing will do ("I'll get back to you"). This module reads the WHOLE run of
the orchestrator at its end and logs ONE line, ``answer_claims.checked``, with
counts only. It calls no model and changes nothing that the member reads.

**The seam.** ``orchestrator._tool_injection._apply_claim_check`` puts ONE
:class:`ClaimCheckProvider` on the per-run view of the agent ``orchestrator``
while ``CHAT_CLAIM_CHECK_ORGS`` names the org of the run. The provider adds no
middleware. MAF 1.19 calls :meth:`ClaimCheckProvider.after_run` once for each
``agent.run``, after the stream ends, with ``context.response``. The response
holds each model step, each tool call and each tool result of the run, in
order. So "I'll check the thread" followed by ``read_thread`` in the same run
reads as kept, never as a broken promise.

**The outcome of a call.** Each call of a tool of :data:`ACT_TOOLS` is
``acted``, ``not_acted`` or ``unknown``. The rating FAILS CLOSED: ``acted``
needs positive evidence.

* ``not_acted``: the tool raised, or its result starts with a lead of
  :data:`NOT_ACTED_LEADS` (``email_chat_context.NO_ACT_LEADS`` and the leads
  of ``ask_tools.CARD_NOT_DONE_LEADS``), or with the head of a template of
  :data:`NOT_ACTED_TEMPLATES` (the held and withheld answers of ``egress``,
  the hand-off refusals of ``tool_packs``, and ``PACK_CALL_REFUSED`` that the
  seam passes in). A refusal of ``acb_common.tool_refusals``, a "Not
  forwarded." lead and a head that says that nothing was done count too.
* ``unknown``: the run holds no result, the result is empty, or the HRD-3
  guard says that the call "may have changed part of it". A result of an act
  tool that starts with no head of :data:`ACTED_HEADS` is ``unknown`` too. An
  unknown outcome abstains.
* ``acted``: the result starts with a success head of its own tool, as its
  source writes it (:data:`ACTED_HEADS`). A tool with no head never rates
  ``acted``, so its claims abstain.

**The rules.** Each sentence of the answer gives at most one verdict for each
rule and kind:

* ``act_without_outcome`` (A): "I sent", "I have created", "we added two
  tasks". A first-person act of kind K in the past or the perfect, and no
  ``acted`` call of K in the run. A make word gives ``task.create`` only when
  its object IS a new task. "added a comment to the task" or "added the task
  to the Q3 cycle" abstains (``unknown_kind``), so a claim never takes a wrong
  kind. A time word of an earlier turn ("yesterday", "already", "on Monday")
  abstains (``earlier_turn``) when the run holds no acted call of K.
* ``doubt_without_outcome`` (B): "it was not sent", "neither email went out".
  A negated act of kind K. Fix round 1 (PROPOSED to the owner): it fires ONLY
  against an ``acted`` call of K in the run, the act that it contradicts. A
  ``not_acted`` call of K passes it. With no call of K it abstains
  (``no_call``).
* ``promise_without_followup`` (C): "I'll ... later", "I'll follow up", "get
  back to you", "let you know", "I'll check the thread". It passes when the
  run made a reminder or a task, when a call of its act kind ran, or when any
  tool call comes after it in the run.
* ``earlier_turn_doubt``: a doubt with a back reference to an earlier turn
  ("your earlier message said it was sent, and that was wrong"). Its outcome
  is in an earlier run by construction, so it ALWAYS fires, also for a TRUE
  retraction. Its count is a count of retractions, not of false claims. §8 of
  the spec puts it to the owner as a question.

A question, a condition on the member ("once you approve", "if you") and
quoted text give no verdict. Quoted text is a line that starts with ``>``, a
code fence, a quoted span, or the rest of a paragraph after "wrote:" or
"says:". A bare passive ("your earlier message said it was sent") is no claim
of A. A text over :data:`MAX_CHARS` characters, or a text whose letters are
mostly not Latin, is skipped as a whole.

**Abstain.** A verdict that would fire abstains when the run cannot see the
act. ``delegated``: the run called a delegation tool, and the calls of the
sub-run stay inside it (``executor._run_sub_agent_streaming``). ``resume``:
the stored call of a late Approve ran before the model with no middleware
(orchestrator contract 22b), and the executor marks the run with
:data:`RESUMED_KEY`. ``unknown_outcome``: the only call of the kind has no
outcome. ``earlier_turn``: a claim of A that points at an earlier turn.
``no_call``: a doubt with no call of its kind. ``unknown_kind``: an act word
whose object is not a new task or reminder. ``long_sentence``: a sentence over
:data:`MAX_SENTENCE_CHARS` characters, which the check does not read, so the
cost of a run stays bounded on the event loop.

**The log.** ONE line for each run: the mode, the agent, the count of each
verdict and of each call outcome, the act kinds, the rule ids, the act tool
names, the abstain reasons and a length bucket. Each value is a count or a
name from a closed set. The line never holds the answer, a sentence, a hash
of a sentence, a recipient, an id or a result. A fault logs
``answer_claims.failed`` with the class of the error, and the run goes on.

Fence: ``tests/unit/test_answer_claims.py``.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from acb_common import get_logger
from acb_common.tool_refusals import is_refusal
from agent_framework import ContextProvider

from acb_skills.ask_tools import CARD_NOT_DONE_LEADS
from acb_skills.egress import (
    CARD_BOUND_SEND_TOOLS,
    DELEGATION_TOOLS,
    EGRESS_HELD_MAIL_ANSWER,
    EGRESS_HELD_MAIL_ANSWER_CARDS,
    EGRESS_HELD_PACK_ANSWER,
    EGRESS_HELD_PACK_ANSWER_CARDS,
    EGRESS_HELD_PACK_ANSWER_NO_WRITES,
    EGRESS_HELD_PACK_ANSWER_NO_WRITES_CONTINUE,
    EGRESS_HELD_PACK_ANSWER_OTHER,
    EGRESS_WITHHELD_ANSWER,
    HELD_CARD_SEND_TOOLS,
)
from acb_skills.email_chat_context import NO_ACT_LEADS
from acb_skills.pack_tool_guard import failure_text
from acb_skills.tool_packs import (
    HANDED_FAILED_WRITE_REFUSED,
    HANDED_WRITE_REFUSED,
    PACK_ONLY_REFUSED,
    WRITE_KINDS,
)

_log = get_logger("acb_skills.answer_claims")

SOURCE_ID = "chat_claim_check"
#: D-HRD-6: the check logs and never changes an answer.
MODE = "shadow"
#: The one agent whose run gets the check.
CHECKED_AGENT = "orchestrator"
#: The key of the run binding that marks a continuation run (RESUME-2a).
#: ``executor.run_agent_stream`` sets it right after ``_resume_prologue``.
RESUMED_KEY = "resumed_call"
#: A longer answer is skipped, and the log gives its length bucket only.
MAX_CHARS = 8_000
#: The head of a tool result that the "nothing was done" test reads.
RESULT_HEAD_CHARS = 300

CHECKED_EVENT = "answer_claims.checked"
FAILED_EVENT = "answer_claims.failed"

# ── The outcome of a call ───────────────────────────────────────────────────

ACTED = "acted"
NOT_ACTED = "not_acted"
UNKNOWN = "unknown"
OUTCOMES: tuple[str, ...] = (ACTED, NOT_ACTED, UNKNOWN)

# ── The verdicts ────────────────────────────────────────────────────────────

ACT_WITHOUT_OUTCOME = "act_without_outcome"
DOUBT_WITHOUT_OUTCOME = "doubt_without_outcome"
PROMISE_WITHOUT_FOLLOWUP = "promise_without_followup"
EARLIER_TURN_DOUBT = "earlier_turn_doubt"
RULES: tuple[str, ...] = (
    ACT_WITHOUT_OUTCOME, DOUBT_WITHOUT_OUTCOME, PROMISE_WITHOUT_FOLLOWUP, EARLIER_TURN_DOUBT,
)
PASS = "pass"
ABSTAIN = "abstain"

DELEGATED = "delegated"
RESUME = "resume"
UNKNOWN_OUTCOME = "unknown_outcome"
EARLIER_TURN = "earlier_turn"
TOO_LONG = "too_long"
LANG_SKIPPED = "lang_skipped"
NO_TEXT = "no_text"
#: Fix round 1. A doubt with no call of its kind in the run (rule B).
NO_CALL = "no_call"
#: Fix round 1. A sentence over :data:`MAX_SENTENCE_CHARS` is not read.
LONG_SENTENCE = "long_sentence"
#: Fix round 1. An act word about a task or a reminder whose object is not a
#: new one ("added a comment to the task"). Never a wrong kind.
UNKNOWN_KIND = "unknown_kind"
ABSTAIN_REASONS: tuple[str, ...] = (
    DELEGATED, RESUME, UNKNOWN_OUTCOME, EARLIER_TURN, TOO_LONG, LANG_SKIPPED, NO_TEXT,
    NO_CALL, LONG_SENTENCE, UNKNOWN_KIND,
)
#: A longer sentence is skipped, so the cost of a sentence stays bounded.
MAX_SENTENCE_CHARS = 600
#: The most characters after a first-person subject that the act rule reads.
CLAUSE_CHARS = 120

# ── The act table (REUSED: tool_packs.WRITE_KINDS and the egress send maps) ─

TASK_CREATE = "task.create"
TASK_UPDATE = "task.update"
REMINDER_SET = "reminder.set"
EMAIL_SEND = "email.send"
#: The two kinds of a promise that names no act: a read ("I'll check the
#: thread") and a later step ("I'll get back to you").
READ = "read"
FOLLOWUP = "followup"
ACT_KINDS: tuple[str, ...] = (TASK_CREATE, TASK_UPDATE, REMINDER_SET, EMAIL_SEND)
KINDS: tuple[str, ...] = (*ACT_KINDS, READ, FOLLOWUP)

#: The Projects kinds of slice 1, from ``tool_packs.WRITE_KINDS``.
PROJECTS_ACT_KINDS: tuple[str, ...] = (TASK_CREATE, TASK_UPDATE, REMINDER_SET)
#: The email sends: the card-bound sends of ``egress.CARD_BOUND_SEND_TOOLS``,
#: and the two sends of ``egress.HELD_CARD_SEND_TOOLS``. The other keys of
#: that map (an unsubscribe, a settings write) send no mail.
_HELD_SENDS = frozenset({"send_email", "forward_email"})
EMAIL_SEND_TOOLS: frozenset[str] = (
    frozenset(CARD_BOUND_SEND_TOOLS) | (frozenset(HELD_CARD_SEND_TOOLS) & _HELD_SENDS)
)
#: Each act tool of slice 1, with its kind. No calendar tool yet.
ACT_TOOLS: Mapping[str, str] = MappingProxyType({
    **{tool: kind for tool, kind in WRITE_KINDS.items() if kind in PROJECTS_ACT_KINDS},
    **{tool: EMAIL_SEND for tool in sorted(EMAIL_SEND_TOOLS)},
})
#: A reminder or a task that the run made keeps any promise (D2).
FOLLOWUP_KINDS: frozenset[str] = frozenset({REMINDER_SET, TASK_CREATE})

#: The first words of a result that did not act. REUSED: the email leads
#: (``email_chat_context.NO_ACT_LEADS``) and the lead of each card outcome
#: that did nothing (``ask_tools.CARD_NOT_DONE_LEADS``), with its colon.
NOT_ACTED_LEADS: tuple[str, ...] = tuple(dict.fromkeys((
    *NO_ACT_LEADS, *(f"{lead}:" for lead in CARD_NOT_DONE_LEADS),
)))

#: Fix round 1 (P1-a). The answer of a call that the platform held or
#: refused. Each is the REAL constant, and its first sentence is the head
#: (:func:`template_heads`). ``_tool_injection.PACK_CALL_REFUSED`` lives in the
#: orchestrator, which this package never imports, so the seam passes it to
#: :class:`ClaimCheckProvider`.
NOT_ACTED_TEMPLATES: tuple[str, ...] = (
    EGRESS_WITHHELD_ANSWER,
    EGRESS_HELD_MAIL_ANSWER,
    EGRESS_HELD_MAIL_ANSWER_CARDS,
    EGRESS_HELD_PACK_ANSWER,
    EGRESS_HELD_PACK_ANSWER_CARDS,
    EGRESS_HELD_PACK_ANSWER_NO_WRITES,
    EGRESS_HELD_PACK_ANSWER_NO_WRITES_CONTINUE,
    EGRESS_HELD_PACK_ANSWER_OTHER,
    HANDED_WRITE_REFUSED,
    HANDED_FAILED_WRITE_REFUSED,
    PACK_ONLY_REFUSED,
)

#: Fix round 1. "Not forwarded.", "Not scheduled.": the email tools lead a
#: refusal with "Not" and the participle of their act (``agent-email-assistant``).
_NOT_PARTICIPLE = re.compile(r"^Not (?:[a-z]+ed|shown|sent|run|done)\b")

#: Fix round 1. The HRD-3 guard text of a write that raised: "It may have
#: changed part of it." It starts with ``FAILED``, but the act may have run,
#: so it rates ``unknown``. The phrase comes from the guard itself.
_GUARD_TEXT = failure_text(RuntimeError(), read_only=False)
PARTIAL_WRITE = _GUARD_TEXT.split("). ", 1)[1].split(". ", 1)[0] + "."

#: Fix round 1 (P1-a). The success head of each act tool, as its source
#: writes it. ``acted`` needs one of them. A fence pins each head to the home
#: module of its tool. An act tool with no entry never rates ``acted``.
ACTED_HEADS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "send_email": ("Sent", "Replied to"),
    "compose_reply": ("Sent to",),
    "forward_email": ("Forwarded",),
    "send_draft": ("Draft sent",),
    "schedule_send": ("Scheduled",),
    "create_task": ("Created:",),
    # P-1b (task 3922): the task from a mail (``skill_my_tasks.core._made``).
    "my_tasks_capture_from_mail": ("Made one task from the mail:",),
    "update_task": ("Updated:",),
    "complete": ("Done:",),
    "set_reminder": ("Reminder set",),
})

#: A sentence that says that nothing was done ("Nothing was sent or saved.",
#: "Nothing is written until the member taps Confirm.").
_NOTHING_DONE = re.compile(
    r"\b(?:nothing|no (?:email|mail|message|task|reminder)s?)\s+"
    r"(?:was|is|has been|got|were)\s+(?:yet\s+)?"
    r"(?:created|sent|done|changed|written|saved|set|made|added|updated|scheduled)\b",
    re.IGNORECASE,
)


# ── The flag ────────────────────────────────────────────────────────────────

def claim_check_on(org: object = None) -> bool:
    """True when ``CHAT_CLAIM_CHECK_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant of the run. The value is a comma list
    of org ids, or ``*`` for each one. Empty means off, the default. It reads
    the settings on each call, so a flag reload (``docs/flag_reload.md``)
    applies a change with no restart. It fails closed.
    """
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_claim_check_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    if "*" in entries:
        return True
    if org is None:
        try:
            from acb_common.db import current_tenant

            org = current_tenant()
        except Exception:
            return False
    return str(org or "").strip().lower() in entries


# ── The outcome of one call ─────────────────────────────────────────────────

def _text_of(result: Any) -> str:
    """The text of a tool result in any shape that MAF gives."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        return "\n".join(_text_of(item) for item in result)
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    return str(result)


_PLACEHOLDER = re.compile(r"\\\{[a-z_]+\\\}")


def template_heads(templates: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    """The head of each template as a pattern: its first sentence, with each
    ``{name}`` or ``{tool}`` read as one word. "{name} is off now." matches
    "send_email is off now."."""
    heads: list[re.Pattern[str]] = []
    for template in templates:
        first = str(template).split(". ", 1)[0].rstrip(".") + "."
        heads.append(re.compile("^" + _PLACEHOLDER.sub(r"\\S+", re.escape(first))))
    return tuple(heads)


#: The heads of :data:`NOT_ACTED_TEMPLATES`.
NOT_ACTED_HEADS: tuple[re.Pattern[str], ...] = template_heads(NOT_ACTED_TEMPLATES)


def _not_acted(text: str, extra: Sequence[re.Pattern[str]] = ()) -> bool:
    """True when *text* is a known answer of a call that did not act."""
    if text.startswith(NOT_ACTED_LEADS) or is_refusal(text) or _NOT_PARTICIPLE.match(text):
        return True
    if any(head.match(text) for head in (*NOT_ACTED_HEADS, *extra)):
        return True
    return bool(_NOTHING_DONE.search(text[:RESULT_HEAD_CHARS]))


def call_outcome(result: Any, exception: Any = None, *, found: bool = True,
                 tool: str = "", extra: Sequence[re.Pattern[str]] = ()) -> str:
    """``acted``, ``not_acted`` or ``unknown`` for one call.

    *found* is False when the run holds no result for the call. *tool* names
    the tool. *extra* holds more not-acted heads, from the seam. For a tool of
    :data:`ACT_TOOLS` it FAILS CLOSED: ``acted`` needs a head of
    :data:`ACTED_HEADS`, and each other result is ``unknown``. A tool outside
    the table rates ``acted`` for each result that is no known refusal.
    """
    if not found:
        return UNKNOWN
    if exception:
        return NOT_ACTED
    text = _text_of(result).lstrip()
    if not text:
        return UNKNOWN
    if is_refusal(text) and PARTIAL_WRITE in text[:RESULT_HEAD_CHARS]:
        # HRD-3: the write raised, and it may have changed part of it.
        return UNKNOWN
    if _not_acted(text, extra):
        return NOT_ACTED
    if tool in ACT_TOOLS:
        return ACTED if text.startswith(ACTED_HEADS.get(tool, ())) else UNKNOWN
    return ACTED


@dataclass(frozen=True)
class RunCall:
    """One tool call of the run: its name, its place and its outcome."""

    name: str
    at: int
    outcome: str


def _role(message: Any) -> str:
    role = getattr(message, "role", "")
    return str(getattr(role, "value", role) or "")


def _result_of(content: Any) -> tuple[Any, Any]:
    result = getattr(content, "result", None)
    if not isinstance(result, str):
        items = getattr(content, "items", None)
        if items:
            result = items
    return result, getattr(content, "exception", None)


def read_run(response: Any, extra: Sequence[re.Pattern[str]] = (),
             ) -> tuple[list[tuple[int, str]], list[RunCall]]:
    """The text blocks and the calls of a whole run, in order.

    A block is the assistant text between two tool steps. Its place is the
    count of tool steps before it, and a call's place is its own step. So a
    call with a larger place than a block comes after that block. *extra*
    holds more not-acted heads for :func:`call_outcome`.
    """
    blocks: list[tuple[int, str]] = []
    raw_calls: list[tuple[str, str, int]] = []
    results: dict[str, tuple[Any, Any]] = {}
    buf: list[str] = []
    buf_at = 0
    at = 0
    for message in getattr(response, "messages", None) or ():
        role = _role(message)
        if buf and buf[-1] != "\n":
            buf.append("\n")
        for content in getattr(message, "contents", None) or ():
            kind = getattr(content, "type", "")
            if kind == "text" and role == "assistant":
                if not buf:
                    buf_at = at
                buf.append(str(getattr(content, "text", "") or ""))
                continue
            if kind not in ("function_call", "function_result"):
                continue
            if buf:
                blocks.append((buf_at, "".join(buf)))
                buf = []
            at += 1
            call_id = str(getattr(content, "call_id", "") or "")
            if kind == "function_call":
                raw_calls.append((call_id, str(getattr(content, "name", "") or ""), at))
            else:
                results[call_id] = _result_of(content)
    if buf:
        blocks.append((buf_at, "".join(buf)))
    calls = [
        RunCall(name, place, call_outcome(*results[call_id], tool=name, extra=extra)
                if call_id in results else UNKNOWN)
        for call_id, name, place in raw_calls
    ]
    return blocks, calls


# ── The sentences of an answer ──────────────────────────────────────────────

_FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_QUOTED_SPAN = re.compile(r"\"[^\"\n]{0,500}\"|«[^»\n]{0,500}»")
_WROTE = re.compile(r"\b(?:wrote|says)\s*:", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
_SPLIT = re.compile(r"(?<=[.!?])\s+")
#: The curly quotes of a model answer, as plain ones. By code point, so the
#: source holds no character that looks like another.
_PLAIN_QUOTES = str.maketrans({
    chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"',
})


def _unquoted(text: str) -> str:
    """*text* with each quoted part taken out (an exclusion of the rules)."""
    text = text.translate(_PLAIN_QUOTES)
    text = _FENCE.sub("\n", text)
    text = _INLINE_CODE.sub(" ", text)
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        lines = [line for line in paragraph.split("\n") if not line.lstrip().startswith(">")]
        kept = "\n".join(lines)
        cut = _WROTE.search(kept)
        if cut:
            kept = kept[:cut.start()]
        paragraphs.append(kept)
    return _QUOTED_SPAN.sub(" ", "\n\n".join(paragraphs))


def sentences(text: str) -> list[str]:
    """The sentences of *text*, after the quoted parts are out, lowercase,
    with plain apostrophes."""
    out: list[str] = []
    for line in _unquoted(text).split("\n"):
        line = _BULLET.sub("", line)
        for part in _SPLIT.split(line):
            sentence = " ".join(part.translate(_PLAIN_QUOTES).split()).lower()
            if sentence:
                out.append(sentence)
    return out


# ── The words of an act ─────────────────────────────────────────────────────

_SEND_FORMS = frozenset({
    "sent", "emailed", "mailed", "forwarded", "replied", "resent",
    "send", "email", "mail", "forward", "reply", "resend",
})
_CREATE_FORMS = frozenset({
    "created", "added", "made", "logged", "captured",
    "create", "add", "make", "log", "capture",
})
_REMIND_FORMS = frozenset({
    "set", "created", "added", "scheduled", "made",
    "create", "add", "schedule", "make",
})
_UPDATE_FORMS = frozenset({
    "updated", "changed", "moved", "assigned", "reassigned", "renamed", "rescheduled",
    "edited", "completed", "closed", "deferred", "marked",
    "update", "change", "move", "assign", "reassign", "rename", "reschedule",
    "edit", "complete", "close", "defer", "mark",
})
_PAST = frozenset({
    "sent", "emailed", "mailed", "forwarded", "replied", "resent",
    "created", "added", "made", "logged", "captured", "set", "scheduled",
    "updated", "changed", "moved", "assigned", "reassigned", "renamed", "rescheduled",
    "edited", "completed", "closed", "deferred", "marked",
})
_ALL_FORMS = _SEND_FORMS | _CREATE_FORMS | _REMIND_FORMS | _UPDATE_FORMS | {"remind"}
_READ_FORMS = frozenset({
    "check", "look", "read", "review", "search", "find", "fetch", "dig", "confirm",
    "verify", "investigate", "ask", "go through", "pull up",
})


def _alt(words: Iterable[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_VERB_ALL = _alt(_ALL_FORMS)
_VERB_PAST = _alt(_PAST)
_TASK_NOUN = re.compile(r"\b(?:sub)?tasks?\b|\bto-?dos?\b|\baction items?\b")
_REMINDER_NOUN = re.compile(r"\breminders?\b")
_DONE_WORD = re.compile(r"\b(?:done|complete|completed|finished)\b")
#: A send of a card is no send of mail ("I sent you a card to approve").
_CARD_OBJECT = re.compile(r"\b(?:cards?|approval|for review|invites?)\b")
_CLAUSE_END = re.compile(r"[,;:.!?]|\b(?:but|so|because|while)\b")


def _window(sentence: str, end: int) -> str:
    """The words after a verb, to the end of its clause, 60 characters at most."""
    rest = sentence[end:end + 60]
    cut = _CLAUSE_END.search(rest)
    return rest[:cut.start()] if cut else rest


#: Fix round 1 (P1-b). The kind of an act word about a task or a reminder
#: whose object is not a new one ("added a comment to the task"). A claim of
#: this kind abstains (``unknown_kind``), so it never takes a wrong kind.
UNCLEAR = "?"
#: The object right after a verb: a determiner, at most two words, then the
#: noun. "a new task", "two tasks", "the task", but not "a comment to the
#: task" or "the urgent tag to the task".
_OBJECT = re.compile(
    r"^\s*(?P<det>(?:a|an|the|one|two|three|four|five|six|seven|eight|nine|ten|\d+|"
    r"new|your|my|our|this|that|these|those|both|some|several|another)\s+)?"
    r"(?:[a-z0-9'#-]+\s+){0,2}?"
    r"(?P<noun>(?:sub)?tasks?|to-?dos?|action items?|reminders?)\b(?P<after>.*)$"
)
_DEFINITE = frozenset({"the", "your", "my", "our", "this", "that", "these", "those"})
#: "added the task to the Q3 cycle" moves a task that exists.
_MOVED = re.compile(r"^\s*(?:to|into|onto|under)\b")


def _kind_of(verb: str, scope: str, window: str) -> str | None:
    """The act kind of *verb*, :data:`UNCLEAR`, or None.

    *window* holds the words after the verb, so its object. *scope* is where
    the subject of a passive may sit ("the task was not created"). A make
    word gives ``task.create`` only when its object IS a new task.
    """
    if verb in _SEND_FORMS:
        return None if _CARD_OBJECT.search(window) else EMAIL_SEND
    if verb == "remind":
        return REMINDER_SET
    if verb in _REMIND_FORMS or verb in _CREATE_FORMS:
        obj = _OBJECT.match(window)
        if obj:
            det = (obj.group("det") or "").strip()
            if obj.group("noun").startswith("reminder"):
                if verb in _REMIND_FORMS:
                    return REMINDER_SET
            elif verb in _CREATE_FORMS:
                if det in _DEFINITE and _MOVED.match(obj.group("after")):
                    return UNCLEAR
                return TASK_CREATE
        elif not window.strip():
            # A passive: its subject is the object ("the task was not created").
            task, reminder = _TASK_NOUN.search(scope), _REMINDER_NOUN.search(scope)
            if reminder and verb in _REMIND_FORMS and (not task or reminder.start() < task.start()):
                return REMINDER_SET
            if task and verb in _CREATE_FORMS:
                return TASK_CREATE
    if verb in _UPDATE_FORMS and (
        _TASK_NOUN.search(scope)
        or (verb in ("mark", "marked") and _DONE_WORD.search(scope))
    ):
        return TASK_UPDATE
    if (verb in _CREATE_FORMS or verb in _REMIND_FORMS) and (
        _TASK_NOUN.search(window) or _REMINDER_NOUN.search(window)
    ):
        return UNCLEAR
    return None


def _known(kind: str | None) -> str | None:
    return None if kind == UNCLEAR else kind


def _any_kind(sentence: str) -> str | None:
    """The kind of the first act word of *sentence*, its object anywhere."""
    if _OUT.search(sentence):
        return EMAIL_SEND
    for match in re.finditer(rf"\b({_VERB_ALL})\b", sentence):
        kind = _known(_kind_of(match.group(1), sentence, _window(sentence, match.end())))
        if kind:
            return kind
    return None


# ── The patterns of the four rules ──────────────────────────────────────────

_QUESTION_HEAD = re.compile(
    r"^(?:shall|should|can|could|may|would) (?:i|we)\b|^do you (?:want|need|like)\b"
    r"|^would you like\b|^want me to\b|^did you want\b|^is it ok\b"
)
#: A condition: a sentence that starts with "if", or a clause that waits on
#: the member ("once you approve"). "When I checked, ..." is a time, not a
#: condition.
_CONDITION = re.compile(
    r"^(?:if|unless|in case)\b"
    r"|\b(?:if|once|when|unless|after|until|as soon as)\s+(?:you|your)\b"
)
#: A first-person subject. Its clause follows it.
_SUBJECT = re.compile(r"\b(?:i|we)\b")
_LEAD_PAST = re.compile(
    r"^(?:'ve|\s+have|\s+had)?"
    r"(?:\s+(?:just|already|also|now|then|successfully|finally|both))*"
    rf"\s+({_VERB_PAST})\b"
)
_AND_PAST = re.compile(rf"(?:,|\band\b|\bthen\b)\s+(?:(?:also|then|just)\s+)?({_VERB_PAST})\b")
#: A word that starts another subject or a report, so the clause of the
#: first person ends there.
_OTHER_CLAUSE = re.compile(
    r"[;:]|\b(?:but|so|because|that|who|which|said|says|told|asked|wrote|he|she|they|you)\b"
)
#: A negated act: "did not send", "it was not sent", "has not been created".
#: A bare "not" after a modal or "to" is an intent, never a doubt.
_NEGATED = re.compile(
    r"\b(?:did not|didn't|could not|couldn't|was not able to|wasn't able to|"
    r"were not able to|weren't able to|was unable to|were unable to|failed to|"
    r"has not|hasn't|have not|haven't|had not|hadn't|was not|wasn't|were not|weren't|"
    r"is not|isn't|are not|aren't|"
    r"(?<!will )(?<!shall )(?<!do )(?<!does )(?<!would )(?<!should )(?<!can )"
    r"(?<!must )(?<!may )(?<!might )(?<!to )not|never)"
    rf"(?:\s+(?:yet|actually|successfully|even|been|get|got))*\s+({_VERB_ALL})\b"
)
#: "Nothing was sent", "no email was sent", "neither was created".
_NONE_DONE = re.compile(
    r"\b(?:nothing|none(?: of [a-z' ]{1,30}?)?|neither(?: [a-z']+)?|no [a-z']+(?: [a-z']+)?)\s+"
    r"(?:was|were|has been|have been|had been|got|is|are)\s+(?:yet\s+)?"
    rf"({_VERB_PAST})\b"
)
#: Mail that did not go out: "neither email went out", "it did not go out".
_OUT = re.compile(
    r"\b(?:nothing|none(?: of [a-z' ]{1,30}?)?|neither(?: [a-z']+){0,2}|no [a-z']+(?: [a-z']+)?)\s+"
    r"(?:went|has gone|have gone|had gone|goes|go)\s+out\b"
    r"|\b(?:did not|didn't|has not|hasn't|have not|haven't|had not|hadn't|never)\s+"
    r"(?:go|gone|went)\s+out\b"
)
_BACK_REF = re.compile(
    r"\bearlier (?:message|answer|reply|mail|email|note)s?\b"
    r"|\bmy (?:last|previous|earlier) (?:message|answer|reply)\b"
    r"|\bprevious (?:message|answer|reply)\b|\bpreviously\b"
    r"|\bi (?:said|told you|wrote|reported|claimed)\b"
    r"|\bthat was (?:wrong|not true|incorrect|a mistake)\b|\bthat wasn't true\b"
    r"|\bearlier,? i\b|\bbefore,? i (?:said|told)\b"
)
_RETRACT = re.compile(r"\b(?:wrong|incorrect|not true|untrue|a mistake|mistaken|in error)\b")
_WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
#: Fix round 1. A time word that places an act of rule A in an earlier turn
#: ("I sent it yesterday", "I've already created the task"). Rule A only.
_EARLIER_A = re.compile(
    r"\b(?:earlier|this morning|yesterday|already|before|previously|ago|the other day|"
    rf"last (?:week|month|night|time|{_WEEKDAY})|on {_WEEKDAY})\b"
)
_FUTURE = re.compile(
    r"\b(?:(?:i|we)(?:'ll|\s+will|\s+shall|'m going to|\s+am going to|'re going to|"
    r"\s+are going to)|let me|let us|let's)\b"
)
_ASK_TO_TELL = re.compile(r"\blet (?:me|us)\s+know\b")
_NOT_NOW = re.compile(r"\s+(?:not|never)\b")
_PROMISE_VERB = re.compile(rf"\b({_VERB_ALL}|{_alt(_READ_FORMS)})\b")
_PROMISE_PHRASE = re.compile(
    r"\bfollow[- ]up\b|\bget back to you\b|\blet you know\b"
    r"|\bkeep you (?:posted|updated|informed)\b|\bcircle back\b|\bcheck back\b"
)
_LATER = re.compile(r"\b(?:later|tomorrow|shortly|soon|next week|in a (?:bit|while|moment))\b")


@dataclass(frozen=True)
class Claim:
    """One claim of a sentence: the rule it would fire, its kind and its place."""

    rule: str
    kind: str
    at: int
    back_ref: bool = False


def _excluded(sentence: str) -> bool:
    """A question or a condition on the member gives no verdict."""
    return (sentence.rstrip("\"')]* ").endswith("?")
            or bool(_QUESTION_HEAD.search(sentence))
            or bool(_CONDITION.search(sentence)))


def _doubt_kind(sentence: str) -> str | None:
    """The kind of the negated act of *sentence*, or None."""
    if _OUT.search(sentence):
        return EMAIL_SEND
    for pattern in (_NEGATED, _NONE_DONE):
        for match in pattern.finditer(sentence):
            kind = _known(_kind_of(match.group(1), sentence, _window(sentence, match.end())))
            if kind:
                return kind
    return None


def _act_kinds(sentence: str) -> list[str]:
    """The kind of each first-person act in the past or the perfect, or
    :data:`UNCLEAR`.

    The verb comes from the clause of the subject, at most
    :data:`CLAUSE_CHARS` characters (fix round 1, P2: the cost stays linear).
    Its object comes from the sentence after the verb, so "I've sent you a
    card" is no send of mail."""
    kinds: list[str] = []
    for subject in _SUBJECT.finditer(sentence):
        start = subject.end()
        clause = sentence[start:start + CLAUSE_CHARS]
        stop = _OTHER_CLAUSE.search(clause)
        if stop:
            clause = clause[:stop.start()]
        lead = _LEAD_PAST.match(clause)
        if not lead:
            continue
        verbs = [lead, *_AND_PAST.finditer(clause, lead.end())]
        for match in verbs:
            window = _window(sentence, start + match.end())
            kind = _kind_of(match.group(1), window, window)
            if kind and kind not in kinds:
                kinds.append(kind)
    return kinds


#: The words right after "I'll" or "let me" that may name what it promises.
PROMISE_WORDS = 5


def _promise_kind(sentence: str) -> str | None:
    """The kind of the promise of *sentence*: an act kind, ``read`` or
    ``followup``. None when the sentence holds no promise."""
    sentence = _ASK_TO_TELL.sub(" ", sentence)
    future = _FUTURE.search(sentence)
    if not future:
        return None
    rest = sentence[future.end():]
    if _NOT_NOW.match(rest):
        # "I will not send it" says what will not happen. It is no promise.
        return None
    for verb in _PROMISE_VERB.finditer(rest):
        if len(rest[:verb.start()].split()) >= PROMISE_WORDS:
            break
        word = verb.group(1)
        kind = _known(_kind_of(word, rest, _window(rest, verb.end())))
        if kind:
            return kind
        if word in _READ_FORMS:
            return READ
    if _PROMISE_PHRASE.search(rest) or _LATER.search(rest):
        return FOLLOWUP
    return None


def scan(text: str, at: int = 0) -> tuple[list[Claim], set[str]]:
    """The claims of one block of answer text at the place *at*, and the
    reasons of each sentence that it skipped."""
    out: list[Claim] = []
    skipped: set[str] = set()
    for sentence in sentences(text):
        if len(sentence) > MAX_SENTENCE_CHARS:
            # Fix round 1 (P2): a sentence this long is no answer sentence,
            # and its read must not block the event loop.
            skipped.add(LONG_SENTENCE)
            continue
        if _excluded(sentence):
            continue
        back_ref = bool(_BACK_REF.search(sentence))
        doubt = _doubt_kind(sentence)
        if back_ref and (doubt or _RETRACT.search(sentence)):
            kind = doubt or _any_kind(sentence)
            if kind:
                # One verdict for the sentence, however many cues it holds.
                out.append(Claim(EARLIER_TURN_DOUBT, kind, at, back_ref=True))
                continue
        if doubt:
            out.append(Claim(DOUBT_WITHOUT_OUTCOME, doubt, at, back_ref))
        # Rule A only: a time word places the act in an earlier turn. It
        # never feeds earlier_turn_doubt.
        earlier = back_ref or bool(_EARLIER_A.search(sentence))
        for kind in _act_kinds(sentence):
            out.append(Claim(ACT_WITHOUT_OUTCOME, kind, at, earlier))
        promise = _promise_kind(sentence)
        if promise:
            out.append(Claim(PROMISE_WITHOUT_FOLLOWUP, promise, at, back_ref))
    return out, skipped


def claims_of(text: str, at: int = 0) -> list[Claim]:
    """The claims of one block of answer text at the place *at*."""
    return scan(text, at)[0]


# ── The verdicts of a run ───────────────────────────────────────────────────

def length_bucket(chars: int) -> str:
    """The bucket of an answer length. The log holds it, never the length."""
    for top, name in ((0, "0"), (200, "1-200"), (1_000, "201-1000"),
                      (4_000, "1001-4000"), (MAX_CHARS, "4001-8000")):
        if chars <= top:
            return name
    return "8001+"


def mostly_latin(text: str) -> bool:
    """False when most letters of *text* are not Latin (``lang_skipped``)."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return True
    other = sum(1 for ch in letters if not (ord(ch) <= 0x024F or 0x1E00 <= ord(ch) <= 0x1EFF))
    return other * 2 <= len(letters)


@dataclass
class Report:
    """The verdicts of ONE run: counts and names from closed sets only."""

    verdicts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(
        (PASS, ABSTAIN, *RULES), 0))
    outcomes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    kinds: set[str] = field(default_factory=set)
    rules: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    abstain: set[str] = field(default_factory=set)
    claims: int = 0
    calls: int = 0
    delegations: int = 0
    length: str = "0"

    def fields(self, agent: str) -> dict[str, Any]:
        """The fields of the one log line."""
        return {
            "mode": MODE, "agent": agent,
            "claims": self.claims,
            "passed": self.verdicts[PASS], "abstained": self.verdicts[ABSTAIN],
            **{rule: self.verdicts[rule] for rule in RULES},
            "calls": self.calls, "delegations": self.delegations,
            **{outcome: self.outcomes[outcome] for outcome in OUTCOMES},
            "kinds": sorted(self.kinds), "rules": sorted(self.rules),
            "tools": sorted(self.tools), "abstain": sorted(self.abstain),
            "length": self.length,
        }


def _abstain_or(rule: str, of_kind: Sequence[RunCall], *, delegated: bool,
                resumed: bool) -> tuple[str, str | None]:
    if resumed:
        return ABSTAIN, RESUME
    if delegated:
        return ABSTAIN, DELEGATED
    if any(c.outcome == UNKNOWN for c in of_kind):
        return ABSTAIN, UNKNOWN_OUTCOME
    return rule, None


def judge(claim: Claim, calls: Sequence[RunCall], *, delegated: bool = False,
          resumed: bool = False) -> tuple[str, str | None]:
    """The verdict of one claim against the calls of its run, and the reason
    of an abstain."""
    if claim.rule == EARLIER_TURN_DOUBT:
        return EARLIER_TURN_DOUBT, None
    if claim.kind == UNCLEAR:
        return ABSTAIN, UNKNOWN_KIND
    of_kind = [c for c in calls if ACT_TOOLS.get(c.name) == claim.kind]
    if claim.rule == ACT_WITHOUT_OUTCOME:
        # An acted call first: "I've already sent it" with a send passes.
        if any(c.outcome == ACTED for c in of_kind):
            return PASS, None
        if claim.back_ref:
            return ABSTAIN, EARLIER_TURN
        return _abstain_or(claim.rule, of_kind, delegated=delegated, resumed=resumed)
    if claim.rule == DOUBT_WITHOUT_OUTCOME:
        # Fix round 1 (PROPOSED to the owner): a doubt fires only against an
        # acted call of its kind, the act that it contradicts (D1).
        if any(c.outcome == NOT_ACTED for c in of_kind):
            return PASS, None
        if any(c.outcome == ACTED for c in of_kind):
            return claim.rule, None
        verdict, reason = _abstain_or(claim.rule, of_kind, delegated=delegated,
                                      resumed=resumed)
        return (verdict, reason) if verdict == ABSTAIN else (ABSTAIN, NO_CALL)
    if any(ACT_TOOLS.get(c.name) in FOLLOWUP_KINDS and c.outcome == ACTED for c in calls):
        return PASS, None
    if of_kind or any(c.at > claim.at for c in calls):
        return PASS, None
    return _abstain_or(claim.rule, (), delegated=delegated, resumed=resumed)


def check_run(blocks: Sequence[tuple[int, str]], calls: Sequence[RunCall], *,
              delegations: Iterable[str] = (), resumed: bool = False) -> Report:
    """The report of a whole run: its text *blocks* and its *calls*."""
    report = Report()
    names = frozenset(delegations) | DELEGATION_TOOLS
    report.calls = len(calls)
    report.delegations = sum(1 for c in calls if c.name in names)
    for c in calls:
        if c.name in ACT_TOOLS:
            report.tools.add(c.name)
            report.outcomes[c.outcome] += 1
    text = "\n".join(block for _at, block in blocks)
    report.length = length_bucket(len(text))
    if not text.strip():
        report.abstain.add(NO_TEXT)
        return report
    if len(text) > MAX_CHARS:
        report.abstain.add(TOO_LONG)
        return report
    if not mostly_latin(text):
        report.abstain.add(LANG_SKIPPED)
        return report
    delegated = report.delegations > 0
    for at, block in blocks:
        claims, skipped = scan(block, at)
        report.abstain |= skipped
        for claim in claims:
            report.claims += 1
            if claim.kind != UNCLEAR:
                report.kinds.add(claim.kind)
            verdict, reason = judge(claim, calls, delegated=delegated, resumed=resumed)
            report.verdicts[verdict] += 1
            if verdict in RULES:
                report.rules.add(verdict)
            if reason:
                report.abstain.add(reason)
    return report


def _resumed() -> bool:
    """True on a continuation run (:data:`RESUMED_KEY`). Never raises."""
    try:
        from acb_skills.write_artifact import artifact_context

        return bool(artifact_context().get(RESUMED_KEY))
    except Exception:
        return False


class ClaimCheckProvider(ContextProvider):
    """Reads the whole run of ONE ``agent.run`` at its end, and logs.

    ``_tool_injection._apply_claim_check`` attaches it to the run's own view of
    the orchestrator (``agent_with_providers``), never to a shared agent
    object. It adds no middleware and changes no message. *delegations* holds
    the names of the run's delegation tools, from the agent's own tools.
    *not_acted* holds more not-acted templates from the seam, such as
    ``_tool_injection.PACK_CALL_REFUSED``.
    """

    def __init__(self, agent: str = CHECKED_AGENT, delegations: Iterable[str] = (),
                 not_acted: Iterable[str] = ()) -> None:
        super().__init__(SOURCE_ID)
        self.agent = str(agent or "")
        self.delegations = frozenset(delegations) | DELEGATION_TOOLS
        self.not_acted = tuple(not_acted)
        self.extra_heads = template_heads(self.not_acted)

    async def after_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        """Check the run and log ONE line. It never raises: a fault logs
        ``answer_claims.failed``, and the providers after it still run."""
        try:
            blocks, calls = read_run(getattr(context, "response", None), self.extra_heads)
            report = check_run(blocks, calls, delegations=self.delegations,
                               resumed=_resumed())
            _log.info(CHECKED_EVENT, **report.fields(self.agent))
        except Exception as exc:
            with contextlib.suppress(Exception):  # a log never costs the run
                _log.warning(FAILED_EVENT, agent=self.agent, error=type(exc).__name__)


__all__ = [
    "ABSTAIN",
    "ABSTAIN_REASONS",
    "ACTED",
    "ACTED_HEADS",
    "ACT_KINDS",
    "ACT_TOOLS",
    "ACT_WITHOUT_OUTCOME",
    "CHECKED_AGENT",
    "CHECKED_EVENT",
    "CLAUSE_CHARS",
    "DOUBT_WITHOUT_OUTCOME",
    "EARLIER_TURN_DOUBT",
    "EMAIL_SEND",
    "EMAIL_SEND_TOOLS",
    "FAILED_EVENT",
    "FOLLOWUP",
    "FOLLOWUP_KINDS",
    "KINDS",
    "LONG_SENTENCE",
    "MAX_CHARS",
    "MAX_SENTENCE_CHARS",
    "MODE",
    "NOT_ACTED",
    "NOT_ACTED_HEADS",
    "NOT_ACTED_LEADS",
    "NOT_ACTED_TEMPLATES",
    "NO_CALL",
    "OUTCOMES",
    "PARTIAL_WRITE",
    "PASS",
    "PROMISE_WITHOUT_FOLLOWUP",
    "READ",
    "REMINDER_SET",
    "RESUMED_KEY",
    "RULES",
    "SOURCE_ID",
    "TASK_CREATE",
    "TASK_UPDATE",
    "UNCLEAR",
    "UNKNOWN",
    "UNKNOWN_KIND",
    "Claim",
    "ClaimCheckProvider",
    "Report",
    "RunCall",
    "call_outcome",
    "check_run",
    "claim_check_on",
    "claims_of",
    "judge",
    "length_bucket",
    "mostly_latin",
    "read_run",
    "scan",
    "sentences",
    "template_heads",
]
