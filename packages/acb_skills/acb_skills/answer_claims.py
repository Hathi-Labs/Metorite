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
``acted``, ``not_acted`` or ``unknown``:

* ``not_acted``: the tool raised, or its result starts with a lead of
  :data:`NOT_ACTED_LEADS` (``email_chat_context.NO_ACT_LEADS`` and the leads
  of ``ask_tools.CARD_NOT_DONE_LEADS``), or it is a refusal of
  ``acb_common.tool_refusals``, or its head says that nothing was done.
* ``unknown``: the run holds no result for the call, or the result is empty.
  An unknown outcome abstains.
* ``acted``: each other result.

**The rules.** Each sentence of the answer gives at most one verdict for each
rule and kind:

* ``act_without_outcome`` (A): "I sent", "I have created", "we added". A
  first-person act of kind K in the past or the perfect, and no ``acted``
  call of K in the run.
* ``doubt_without_outcome`` (B): "it was not sent", "neither email went out".
  A negated act of kind K, and no ``not_acted`` call of K in the run.
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
from acb_skills.egress import CARD_BOUND_SEND_TOOLS, DELEGATION_TOOLS, HELD_CARD_SEND_TOOLS
from acb_skills.email_chat_context import NO_ACT_LEADS
from acb_skills.tool_packs import WRITE_KINDS

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
ABSTAIN_REASONS: tuple[str, ...] = (
    DELEGATED, RESUME, UNKNOWN_OUTCOME, EARLIER_TURN, TOO_LONG, LANG_SKIPPED, NO_TEXT,
)

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


def call_outcome(result: Any, exception: Any = None, *, found: bool = True) -> str:
    """``acted``, ``not_acted`` or ``unknown`` for one call of an act tool.

    *found* is False when the run holds no result for the call. A raise is
    ``not_acted``. An empty result is ``unknown``, and an unknown outcome
    abstains.
    """
    if not found:
        return UNKNOWN
    if exception:
        return NOT_ACTED
    text = _text_of(result).lstrip()
    if not text:
        return UNKNOWN
    if text.startswith(NOT_ACTED_LEADS) or is_refusal(text):
        return NOT_ACTED
    if _NOTHING_DONE.search(text[:RESULT_HEAD_CHARS]):
        return NOT_ACTED
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


def read_run(response: Any) -> tuple[list[tuple[int, str]], list[RunCall]]:
    """The text blocks and the calls of a whole run, in order.

    A block is the assistant text between two tool steps. Its place is the
    count of tool steps before it, and a call's place is its own step. So a
    call with a larger place than a block comes after that block.
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
        RunCall(name, place, call_outcome(*results[call_id]) if call_id in results
                else UNKNOWN)
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


def _kind_of(verb: str, scope: str, window: str) -> str | None:
    """The act kind of *verb*. *scope* is where its object noun may sit, and
    *window* is the object of an active verb."""
    if verb in _SEND_FORMS:
        return None if _CARD_OBJECT.search(window) else EMAIL_SEND
    if verb == "remind":
        return REMINDER_SET
    if verb in _REMIND_FORMS or verb in _CREATE_FORMS:
        # The nearest noun names the object: "created the task and set a
        # reminder" makes a task, "added a reminder to the task" a reminder.
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
    return None


def _any_kind(sentence: str) -> str | None:
    """The kind of the first act word of *sentence*, its object anywhere."""
    if _OUT.search(sentence):
        return EMAIL_SEND
    for match in re.finditer(rf"\b({_VERB_ALL})\b", sentence):
        kind = _kind_of(match.group(1), sentence, _window(sentence, match.end()))
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
            kind = _kind_of(match.group(1), sentence, _window(sentence, match.end()))
            if kind:
                return kind
    return None


def _act_kinds(sentence: str) -> list[str]:
    """The kind of each first-person act in the past or the perfect.

    The verb comes from the clause of the subject. Its object comes from the
    sentence after the verb, so "I've sent you a card" is no send of mail."""
    kinds: list[str] = []
    for subject in _SUBJECT.finditer(sentence):
        start = subject.end()
        clause = sentence[start:]
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
        kind = _kind_of(word, rest, _window(rest, verb.end()))
        if kind:
            return kind
        if word in _READ_FORMS:
            return READ
    if _PROMISE_PHRASE.search(rest) or _LATER.search(rest):
        return FOLLOWUP
    return None


def claims_of(text: str, at: int = 0) -> list[Claim]:
    """The claims of one block of answer text at the place *at*."""
    out: list[Claim] = []
    for sentence in sentences(text):
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
        for kind in _act_kinds(sentence):
            out.append(Claim(ACT_WITHOUT_OUTCOME, kind, at, back_ref))
        promise = _promise_kind(sentence)
        if promise:
            out.append(Claim(PROMISE_WITHOUT_FOLLOWUP, promise, at, back_ref))
    return out


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
    of_kind = [c for c in calls if ACT_TOOLS.get(c.name) == claim.kind]
    if claim.rule == ACT_WITHOUT_OUTCOME:
        if claim.back_ref:
            return ABSTAIN, EARLIER_TURN
        if any(c.outcome == ACTED for c in of_kind):
            return PASS, None
        return _abstain_or(claim.rule, of_kind, delegated=delegated, resumed=resumed)
    if claim.rule == DOUBT_WITHOUT_OUTCOME:
        if any(c.outcome == NOT_ACTED for c in of_kind):
            return PASS, None
        return _abstain_or(claim.rule, of_kind, delegated=delegated, resumed=resumed)
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
        for claim in claims_of(block, at):
            report.claims += 1
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
    """

    def __init__(self, agent: str = CHECKED_AGENT, delegations: Iterable[str] = ()) -> None:
        super().__init__(SOURCE_ID)
        self.agent = str(agent or "")
        self.delegations = frozenset(delegations) | DELEGATION_TOOLS

    async def after_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        """Check the run and log ONE line. It never raises: a fault logs
        ``answer_claims.failed``, and the providers after it still run."""
        try:
            blocks, calls = read_run(getattr(context, "response", None))
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
    "ACT_KINDS",
    "ACT_TOOLS",
    "ACT_WITHOUT_OUTCOME",
    "CHECKED_AGENT",
    "CHECKED_EVENT",
    "DOUBT_WITHOUT_OUTCOME",
    "EARLIER_TURN_DOUBT",
    "EMAIL_SEND",
    "EMAIL_SEND_TOOLS",
    "FAILED_EVENT",
    "FOLLOWUP",
    "FOLLOWUP_KINDS",
    "KINDS",
    "MAX_CHARS",
    "MODE",
    "NOT_ACTED",
    "NOT_ACTED_LEADS",
    "OUTCOMES",
    "PASS",
    "PROMISE_WITHOUT_FOLLOWUP",
    "READ",
    "REMINDER_SET",
    "RESUMED_KEY",
    "RULES",
    "SOURCE_ID",
    "TASK_CREATE",
    "TASK_UPDATE",
    "UNKNOWN",
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
    "sentences",
]
