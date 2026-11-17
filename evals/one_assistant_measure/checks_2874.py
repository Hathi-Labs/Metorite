"""The checks of the seven defects of the owner chat of 2026-11-11 (task 2874).

Each check fails on the behaviour of 2026-11-11 and passes on the right one.
The ``tests/unit/test_one_assistant_measure_2874.py`` file holds both cases of each.

The checks read ``Observation.ordered``: EVERY tool call of the orchestrator's own
run in a turn, in order, as ``(name, arguments, result)``. The older fields of the
observation keep only the email tools or the work tools, and these checks need the
order and the tools outside the pack (a search of Projects tasks before an act).

Four of them read prose and are heuristics, as the four prose checks of ``checks.py``:
``no_promise``, ``no_doubt``, ``states_discard`` and ``result_has`` (the last reads a result,
not prose).
"""
from __future__ import annotations

import re
from typing import Any

from evals.one_assistant_measure import checks as ck

Call = tuple[str, dict[str, Any], str]

#: The tools that send mail or leave a mail in Drafts. A forward must be the only one.
ACT_TOOLS = frozenset({"forward_email", "compose_reply", "send_email", "draft_reply"})
#: A tool that only reads: a promise to read later is false when one ran in the turn.
READ_TOOLS = frozenset({"read_email", "read_thread", "query_inbox", "find_priority",
                        "narrow_and_read", "read_email_attachment", "find_contact",
                        "resolve_person", "find_follow_ups"})
#: "I'll check the thread": a promise of a later act. "I can check" is an offer, so it is legal.
_PROMISE = re.compile(
    r"\b(?:i['\u2019]ll|i will|i am going to|i[\u2019']m going to|let me|i shall)\s+"
    r"(?:\w+\s+){0,2}?(?:check|look|verify|review|read|confirm|find|search|go through)\b",
    re.IGNORECASE)
#: A doubt of a send that the tool confirmed ("Sent to ..."): the words of 2026-11-11.
_DOUBT = re.compile(
    r"\b(?:haven['\u2019]t seen|have not seen|no (?:delivery )?confirmation|not (?:been )?confirmed|"
    r"can(?:['\u2019]?t|not) (?:confirm|verify)|unable to (?:confirm|verify)|not sure (?:it|if|whether)|"
    r"cannot be sure|can['\u2019]t be sure|may not have|might not have|did not (?:get|see) a|"
    r"could(?:n['\u2019]t| not) (?:confirm|verify)|check (?:your )?sent folder)\b|"
    r"\bnot (?:certain|sure) (?:it|that)\b[^.]*\b(?:sent|delivered|went)\b",
    re.IGNORECASE)
#: The member pressed Discard, so the card is gone. These words say that it still waits.
_STILL_OPEN = re.compile(
    r"\b(?:up for your decision|until you press|still (?:open|waiting|pending)|waiting for you|"
    r"awaiting your|treat it as declined|has not been sent yet|not yet sent|ready for you|"
    r"to approve|awaiting your approval)\b", re.IGNORECASE)
_DISCARD = re.compile(
    r"\bdiscard(?:ed)?\b|\byou (?:cancelled|canceled|declined)\b|\bwon['\u2019]t send\b",
    re.IGNORECASE)


def ordered_calls_of(events: list[dict[str, Any]]) -> list[Call]:
    """Each tool call of the orchestrator's own run, in order: ``(name, args, result)``."""
    calls: dict[str, list[Any]] = {}
    order: list[str] = []
    for e in events:
        kind = e.get("type")
        cid = str(e.get("toolCallId") or "")
        if kind == "TOOL_CALL_START":
            calls[cid] = [str(e.get("toolCallName") or ""), e.get("args") or "", "", ""]
            order.append(cid)
        elif kind == "TOOL_CALL_ARGS" and cid in calls:
            calls[cid][3] += str(e.get("delta") or "")
        elif kind == "TOOL_CALL_RESULT" and cid in calls:
            calls[cid][2] = str(e.get("content") or "")
    return [(calls[c][0], ck._args(calls[c][1] or calls[c][3]), calls[c][2]) for c in order]


def _not_run(result: str) -> bool:
    """True when the call did not run: the tool is not in the request, or it refused first.
    ``ck.refused`` also reads "Nothing was sent" as a refusal, and that is the true answer of a
    reply that was only saved, so these checks use the narrower test."""
    from acb_skills.unknown_tool import MEMBER_LINE

    text = str(result).strip()
    return (text.startswith(ck.REFUSED_PREFIXES) or text == MEMBER_LINE
            or ck._UNKNOWN_FUNCTION.match(text) is not None
            or re.search(r"Requested function.*not found", text, re.DOTALL) is not None)


def _ran(calls: list[Call]) -> list[Call]:
    return [c for c in calls if not _not_run(c[2])]


def _act_kept(obs: ck.Observation, arg: Any) -> bool:
    """*arg* is ``(tool, email_id)``: that tool ran on that email, and no other act of
    :data:`ACT_TOOLS` ran. A forward that turns into a new mail or a reply fails (defect 1)."""
    tool, email_id = arg
    ran = _ran(obs.ordered)
    own = any(n == tool and str(a.get("email_id") or a.get("reply_to_email_id") or "") == email_id
              for n, a, _ in ran)
    other = any(n in ACT_TOOLS and n != tool for n, _, _ in obs.ordered)
    return own and not other


def _no_promise(obs: ck.Observation, _arg: Any) -> bool:
    """The answer promises no later check, unless a read ran in this turn (defect 2).
    "I can check it" is an offer and passes.

    Limit: when a read ran in the turn, any answer passes. A promise of a FURTHER check after
    that read ("I'll also look at the file") cannot be told from "I checked" by a pattern
    without false fails, so the check leaves it."""
    if any(n in READ_TOOLS for n, _, _ in _ran(obs.ordered)):
        return True
    return _PROMISE.search(obs.answer) is None


def _no_doubt(obs: ck.Observation, _arg: Any) -> bool:
    """The answer doubts no send. A tool's "Sent to" is the confirmation (defect 3).
    An empty answer fails: it says nothing about the send."""
    return bool(obs.answer.strip()) and _DOUBT.search(obs.answer) is None


def _states_discard(obs: ck.Observation, _arg: Any) -> bool:
    """The answer says that the member discarded the card, and not that it waits (defect 4)."""
    return bool(_DISCARD.search(obs.answer)) and _STILL_OPEN.search(obs.answer) is None


def _done_direct(obs: ck.Observation, arg: Any) -> bool:
    """``mark_thread_done`` ran, and no tool outside the email pack ran before it (defect 5).

    *arg* is the email id, or None. A search of Projects tasks or the company search before
    the act is the behaviour of 2026-11-11 (5 task searches, 1 company search, then a question)."""
    from evals.one_assistant_measure.run import pack_tool_names

    allowed = pack_tool_names() | {ck.LOAD_TOOL, ck.FIND_TOOL}
    for name, args, result in obs.ordered:
        if name == "mark_thread_done":
            return not _not_run(result) and (
                arg is None or str(args.get("email_id") or "") == arg)
        if name not in allowed:
            return False
    return False


_NOBODY = re.compile(r"\bNobody matches\b", re.IGNORECASE)


def _tried_closest(obs: ck.Observation, arg: Any) -> bool:
    """``find_contact`` ran with the typed name in this turn (defect 6). *arg* is a pattern of
    the name. It does NOT enforce the order: ``find_contact`` may run before or after
    ``resolve_person`` said nobody. Such a call is the retry that the member needs."""
    nobody = False
    for name, args, result in obs.ordered:
        if name == "resolve_person" and _NOBODY.search(result):
            nobody = True
        elif name == "find_contact" and re.search(arg, str(args.get("name") or ""), re.IGNORECASE):
            return True
    del nobody
    return False


def _no_write_before_question(obs: ck.Observation, _arg: Any) -> bool:
    """No mail-writing tool runs before the member is asked (case 6). The tool says to ask the
    member with ``ask_questions`` when the match is only the closest spelling. The harness
    answers a question with "go on without it" (``harness.NO_ANSWER``), so a write AFTER an
    ``ask_questions`` call is the harness's doing and does not fail this check."""
    for name, _, _ in obs.ordered:
        if name == "ask_questions":
            return True
        if name in ck.WRITES:
            return False
    return True


def _reply_by_compose(obs: ck.Observation, arg: Any) -> bool:
    """A reply goes through ``compose_reply`` on the email *arg*, and ``send_email`` is not
    called for it (defect 7: the call was refused with "use compose_reply", a wasted round)."""
    composed = any(n == "compose_reply" and str(a.get("email_id") or "") == arg
                   for n, a, _ in _ran(obs.ordered))
    wasted = any(n == "send_email" and (a.get("reply_to_email_id") or a.get("reply_all"))
                 for n, a, _ in obs.ordered)
    return composed and not wasted


def _result_has(obs: ck.Observation, arg: Any) -> bool:
    """A tool result of the turn matches the pattern *arg*: the proof that a send or a
    forward really ran, so a doubt check is not vacuous."""
    return any(re.search(arg, r) for _, _, r in obs.ordered)


KINDS = {
    "act_kept": _act_kept,
    "no_promise": _no_promise,
    "no_doubt": _no_doubt,
    "states_discard": _states_discard,
    "done_direct": _done_direct,
    "tried_closest": _tried_closest,
    "no_write_before_question": _no_write_before_question,
    "reply_by_compose": _reply_by_compose,
    "result_has": _result_has,
}
