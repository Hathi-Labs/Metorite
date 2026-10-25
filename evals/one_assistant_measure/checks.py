"""The machine checks of the measure, and what a turn showed (WS-67 OA-5).

:func:`observe` turns one run of the harness into an :class:`Observation`
of the same shape for both arms. The email tool calls come from the
sub-run's events in the delegation arm, and from the orchestrator's own
events in the pack arm. :func:`check_turn` runs the checks of a turn on it.

Each check answers True (pass), False (fail) or None (it does not apply to
this arm). The style is the one of ``evals/orchestrator_cos/run.py``.

⚠️ Four checks read prose, and they are heuristics: ``no_name_claim``,
``no_reference_subject``, ``admits_unreadable`` and ``no_content_claim``.
Each one reads one sentence at a time. The report quotes each answer that
fails one, so a person can judge it.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from evals.one_assistant_measure.pack import DELEGATE_TOOL, EA, email_tool_names

#: The email tools that read one email, a thread or a file.
READS = frozenset({"read_email", "read_thread", "read_email_attachment"})
#: The email tools that read across the inbox, and take ``account_id``.
POOLED_READS = frozenset({"query_inbox", "find_priority", "narrow_and_read"})
#: The email tools that write: a mail, a draft, a rule, a label, a setting.
WRITES = frozenset({
    "compose_reply", "draft_reply", "send_email", "forward_email", "send_draft", "edit_draft",
    "schedule_send", "manage_inbox", "bulk_manage_inbox", "create_rule", "update_rule",
    "delete_rule", "create_label", "snooze_email", "unsubscribe_sender",
})


@dataclass
class Observation:
    """What one turn showed, in the same shape for both arms."""

    arm: str
    answer: str = ""
    #: Each email tool call of the turn: name, arguments, result.
    email_calls: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    #: Each task that the orchestrator gave the email assistant (delegation).
    tasks: list[str] = field(default_factory=list)
    #: Each delegation to the email assistant that the pack refused.
    refused: list[str] = field(default_factory=list)
    #: The value of each confirmation card of the turn.
    cards: list[dict[str, Any]] = field(default_factory=list)
    #: Each email gateway call of the turn: method, path, body or params.
    gateway: list[tuple[str, str, Any]] = field(default_factory=list)
    error: str | None = None


def _args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def email_calls_of(events: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
    """The email tool calls of a run: a sub-run's, or the orchestrator's own."""
    tools = email_tool_names() | {"narrow_and_read"}
    starts: dict[str, list[Any]] = {}
    order: list[str] = []
    for e in events:
        kind = e.get("type")
        cid = str(e.get("toolCallId") or "")
        if kind == "SUB_AGENT_TOOL_CALL_START" and e.get("agentName") == EA:
            key = f"s:{cid}:{len(order)}"
            starts[key] = [str(e.get("toolCallName") or ""), e.get("args") or "", "", ""]
            order.append(key)
        elif kind == "SUB_AGENT_TOOL_CALL_RESULT" and e.get("agentName") == EA:
            for key in reversed(order):
                if key.startswith(f"s:{cid}:") and not starts[key][2]:
                    starts[key][2] = str(e.get("content") or "")
                    break
        elif kind == "TOOL_CALL_START" and str(e.get("toolCallName") or "") in tools:
            key = f"t:{cid}"
            starts[key] = [str(e.get("toolCallName")), e.get("args") or "", "", ""]
            order.append(key)
        elif kind == "TOOL_CALL_ARGS" and f"t:{cid}" in starts:
            # The start event can hold the whole arguments, and the deltas
            # then repeat them. So the deltas count only when it holds none,
            # as the harness's ``_take`` reads them.
            starts[f"t:{cid}"][3] += str(e.get("delta") or "")
        elif kind == "TOOL_CALL_RESULT" and f"t:{cid}" in starts:
            starts[f"t:{cid}"][2] = str(e.get("content") or "")
    return [(starts[k][0], _args(starts[k][1] or starts[k][3]), starts[k][2]) for k in order]


def tasks_of(tool_calls: list[tuple[str, str]]) -> list[str]:
    """Each task that the orchestrator gave the email assistant, by any tool."""
    out: list[str] = []
    for name, raw in tool_calls:
        args = _args(raw)
        if name == DELEGATE_TOOL:
            out.append(str(args.get("task") or ""))
        elif name in ("call_agent", "call_agent_background", "delegate_to_agent") and \
                str(args.get("agent_name") or "") == EA:
            out.append(str(args.get("message") or ""))
        elif name == "call_agents_parallel":
            try:
                tasks = json.loads(args.get("tasks") or "[]")
            except (TypeError, ValueError):
                tasks = []
            out += [str(t.get("message") or t.get("task") or "") for t in tasks
                    if isinstance(t, dict) and t.get("agent") == EA]
    return out


# ── the prose helpers ────────────────────────────────────────────────────────


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _found(pattern: str, text: str) -> bool:
    return re.search(pattern, text or "", re.IGNORECASE) is not None


#: Words that say a name is only a file's name, a sample or a reference.
#: A sentence that holds one of them disclaims the name (fix round 1: a bare
#: "not" no longer counts here, see :func:`_negates_name`).
_DISCLAIMS = (
    r"\b(?:file ?names?|named|labels?|samples?|for reference|references?|not (?:the |a |any )?parts?"
    r"|only (?:a |the )?(?:names?|labels?|files?))\b"
)
_NOT = r"(?:not|n't|never|no)"
#: A word that says that the reader could not read the file.
_UNREADABLE = (
    r"\b(?:can(?:no|')t|could ?n[o']t|unable to|not able to|was not able to)\b(?:\s+\w+){0,3}?\s+"
    r"(?:read|open|extract|see)\b|\bno (?:readable |extractable |machine-readable )?text\b"
    r"|\bscann(?:ed|ing)\b|\bimage\b"
)
_CONTENT_VERB = r"\b(?:shows?|showing|contains?|depicts?|details?|illustrates?|includes?|specif(?:y|ies))\b"
_HEDGE = (r"\b(?:not|no|cannot|can't|couldn't|could not|unable|likely|probably|appears?|seems?|"
          r"suggests?|may|might|names?|named|titled?|labels?|guess)\b")
#: A file name in a sentence ("Gearbox_GB-40_rev3.pdf"). Quoting the name
#: is no claim, so :func:`_no_content_claim` takes it out first.
_FILE_NAME = r"[\w.\-]+\.(?:pdf|png|jpe?g|tiff?|docx?|xlsx?|pptx?)\b"
#: An indirect question about the file ("what the drawing shows"). It points
#: at the content and claims none of it.
_WHAT_CLAUSE = rf"\bwhat\b[^.,:;]{{0,60}}?{_CONTENT_VERB}"
#: An answer that says where a fact came from: the file name. A fact that the
#: answer says it took from the name is a guess said as one (OA-2).
_FROM_THE_NAME = (
    r"\b(?:file ?name|the name of the file|its name|subject and (?:the )?file ?name)s?\b"
    r"[^.]{0,40}\b(?:tells?|says?|suggests?|shows?|indicates?|implies|only)\b"
    r"|\bfrom (?:the|its) file ?name\b"
)
_REFERENCE_WORDS = r"\b(?:reference|finish|example|spec(?:ification)?s?|sheet|only|not|show)\b"


# ── the checks ───────────────────────────────────────────────────────────────


def _answer_all(obs: Observation, arg: Any) -> bool:
    return all(_found(p, obs.answer) for p in arg)


def _answer_some(obs: Observation, arg: Any) -> bool:
    patterns, least = arg
    return sum(_found(p, obs.answer) for p in patterns) >= least


def _called(obs: Observation, arg: Any) -> bool:
    return any(name in arg for name, _, _ in obs.email_calls)


def _read_mail(obs: Observation, arg: Any) -> bool:
    return any(name in READS and str(a.get("email_id") or "") == arg
               for name, a, _ in obs.email_calls)


def _all_inboxes(obs: Observation, _arg: Any) -> bool | None:
    pooled = [a for name, a, _ in obs.email_calls if name in POOLED_READS]
    if not pooled:
        return None
    return not any(str(a.get("account_id") or "").strip() for a in pooled)


def _reply_card(obs: Observation, arg: Any) -> bool:
    shown = any(str((c.get("reply") or {}).get("message_id") or "") == arg for c in obs.cards)
    acted = any(m == "POST" and p == "/email/reply-card/act"
                and str((b or {}).get("message_id") or "") == arg for m, p, b in obs.gateway)
    return shown and acted


def _sent_drafts(obs: Observation) -> list[str]:
    """The id of each draft that the turn sent: a held card, or a plain send."""
    out: list[str] = []
    held = ""
    for m, p, b in obs.gateway:
        if m == "POST" and p == "/email/held-card":
            held = str((b or {}).get("draft_id") or "")
        elif m == "POST" and p == "/email/held-card/act" and held:
            out.append(held)
        elif m == "POST" and p == "/email/drafts/send":
            out.append(str((b or {}).get("draft_id") or ""))
    return out


def _replied(obs: Observation, arg: Any) -> bool:
    """A reply to email *arg*: its reply card, or the send of a saved reply draft."""
    from evals.one_assistant_measure.mailbox import draft_id_for

    return _reply_card(obs, arg) or draft_id_for(arg) in _sent_drafts(obs)


def _reply_texts(obs: Observation) -> list[str]:
    """The text of each reply of the turn: what the model passed, and what a
    card showed. A send card shows the draft's text in its context."""
    out = [str(a.get("draft") or "") + "\n" + str(a.get("instruction") or "")
           for name, a, _ in obs.email_calls if name == "compose_reply"]
    out += [str((c.get("reply") or {}).get("body") or "") for c in obs.cards if c.get("reply")]
    out += [f"{c.get('detail') or ''}\n{c.get('context') or ''}" for c in obs.cards
            if not c.get("reply") and _found(r"\bsend\b", str(c.get("title") or ""))]
    return out


def _reply_text(obs: Observation, arg: Any) -> bool:
    texts = _reply_texts(obs)
    return bool(texts) and all(any(_found(p, t) for t in texts) for p in arg)


def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def _verbatim(obs: Observation, arg: Any) -> bool:
    want = _flat(arg)
    return any(want in _flat(t) for t in _reply_texts(obs))


def _cc(obs: Observation, arg: Any) -> bool:
    want = str(arg).lower()
    for name, a, _ in obs.email_calls:
        if name in ("compose_reply", "edit_draft", "send_email") and \
                any(want == str(x).lower() for x in a.get("cc") or []):
            return True
    return any(m == "PUT" and any(want == str(x).lower() for x in (b or {}).get("cc") or [])
               for m, _, b in obs.gateway)


def _sent_draft(obs: Observation, arg: Any) -> bool:
    for m, p, b in obs.gateway:
        if m != "POST":
            continue
        if p == "/email/held-card/act":
            return True
        if p == "/email/drafts/send" and str((b or {}).get("draft_id") or "") == arg:
            return True
    return False


def _rule_in(obs: Observation, arg: Any) -> bool:
    return any(m == "POST" and p == "/email/rules" and str((b or {}).get("account_id")) == arg
               for m, p, b in obs.gateway)


def _no_write(obs: Observation, _arg: Any) -> bool:
    return not any(name in WRITES for name, _, _ in obs.email_calls)


def _negates_name(s: str, names: Any) -> bool:
    """True when a negation in *s* applies to one of *names*.

    "not CB6 and CB7" and "CB6 and CB7 are not the parts" deny the name. In
    "It is for CB6 and CB7, not anything else" the "not" denies something
    else, so it does not count (fix round 1)."""
    for n in names:
        name = re.escape(n)
        if re.search(rf"\b{_NOT}\b[^.,;:]{{0,30}}\b{name}\b", s, re.I) or re.search(
                rf"\b{name}\b[^.,;:]{{0,40}}\b(?:is|are|was|were|ca|do|does)\s*{_NOT}\b", s, re.I):
            return True
    return False


def _no_name_claim(obs: Observation, arg: Any) -> bool:
    """No sentence names a file's name as content, unless it disclaims it."""
    for s in sentences(obs.answer):
        if any(re.search(rf"\b{re.escape(n)}\b", s, re.I) for n in arg) \
                and not _found(_DISCLAIMS, s) and not _negates_name(s, arg):
            return False
    return True


def _no_reference_subject(obs: Observation, arg: Any) -> bool:
    """No sentence makes the reference file's item the thing to quote."""
    for s in sentences(obs.answer):
        if any(n.lower() in s.lower() for n in arg) and _found(r"\bquote\b|\bunits?\b|\b40\b", s) \
                and not _found(_REFERENCE_WORDS, s):
            return False
    return True


def _admits_unreadable(obs: Observation, _arg: Any) -> bool:
    return _found(_UNREADABLE, obs.answer)


def _no_content_claim(obs: Observation, arg: Any) -> bool:
    """No sentence states what an unread file holds, as a fact.

    *arg* holds patterns of the facts that only the file's name gives, for
    example its model and its revision. Each sentence loses its file names
    and its indirect questions ("what the drawing shows") first. Then it
    fails in either of two ways, unless it hedges (fix round 1):

    * it states a fact of *arg*, and the answer nowhere says that the fact
      came from the file name ("The drawing is a gearbox, model GB-40,
      revision 3." fails, with no content verb at all), or
    * it says that the file shows or holds something ("The drawing shows
      the GB-40 gearbox.").
    """
    from_the_name = _found(_FROM_THE_NAME, obs.answer)
    for raw in sentences(obs.answer):
        s = re.sub(_WHAT_CLAUSE, " ", re.sub(_FILE_NAME, " ", raw, flags=re.I), flags=re.I)
        if _found(_HEDGE, s):
            continue
        if any(_found(p, s) for p in arg) and not from_the_name:
            return False
        if _found(_CONTENT_VERB, s) and _found(r"\b(?:drawing|file|pdf|attachment|scan)s?\b", s):
            return False
    return True


def _task_no_guess(obs: Observation, arg: Any) -> bool | None:
    """Delegation only: no task names the guess (OA-1, the live half)."""
    if obs.arm != "delegation":
        return None
    return not any(re.search(rf"\b{re.escape(n)}\b", t, re.I) for t in obs.tasks for n in arg)


def _no_email_delegation(obs: Observation, _arg: Any) -> bool | None:
    """Pack only: the turn handed no task to an email agent."""
    if obs.arm != "pack":
        return None
    return not obs.refused and not obs.tasks


KINDS: dict[str, Callable[[Observation, Any], bool | None]] = {
    "answer_all": _answer_all,
    "answer_some": _answer_some,
    "called": _called,
    "read_mail": _read_mail,
    "all_inboxes": _all_inboxes,
    "reply_card": _reply_card,
    "replied": _replied,
    "reply_text": _reply_text,
    "verbatim": _verbatim,
    "cc": _cc,
    "sent_draft": _sent_draft,
    "rule_in": _rule_in,
    "no_write": _no_write,
    "no_name_claim": _no_name_claim,
    "no_reference_subject": _no_reference_subject,
    "admits_unreadable": _admits_unreadable,
    "no_content_claim": _no_content_claim,
    "task_no_guess": _task_no_guess,
    "no_email_delegation": _no_email_delegation,
}


def check_turn(turn: Any, obs: Observation) -> dict[str, bool | None]:
    """Each check of *turn* on *obs*, plus ``ran`` and ``no_email_delegation``."""
    out: dict[str, bool | None] = {"ran": obs.error is None}
    for c in turn.checks:
        out[c.name] = KINDS[c.kind](obs, c.arg)
    out["no_email_delegation"] = _no_email_delegation(obs, None)
    return out


def passed(result: dict[str, bool | None]) -> bool:
    return all(v is not False for v in result.values())
