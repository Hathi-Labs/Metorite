"""The checkers of the email-to-Projects eval.

Each checker is a pure function of :class:`Evidence`: the ordered calls of the
run, the final answer, the searches and the mails that the fake found, the
writes that the fake took, and the cards. So a unit test builds a WRONG
evidence by hand and sees the rule fail (R7).

The rules, from the task (Metorite 2976):

* ``alone_search``: each name of the case is searched alone, in its own query.
* ``read_before_write``: a write waits for the read of each mail that it relies
  on, and for the read of each mail that a search found for its project.
* ``update_names_source``: each status update names its source mail.
* ``card_per_write``: each write went through one approved card.
* ``no_false_none``: the answer never says "no emails" for a name that the
  mailbox holds, and a "none found" names the names that hold no mail.

Case rules: ``facts_in_write`` (a write carries a fact of the mail, not a
one-line stub), ``refusal_honest`` (the refusal is said, and no claim of a
post), ``says_what_was_searched`` (a "none found" names the search).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from evals.email_to_projects import fixtures as fx
from evals.email_to_projects.gateway import mails_for, words_of

READ_TOOLS = frozenset({"read_email", "read_thread"})
WRITE_TOOLS = frozenset({"create_project", "post_project_update"})
#: Part of the refusal text of the real tool, ``skill_projects.inbox.UPDATES_OFF``.
UPDATES_OFF = "not on for this organization"

#: A sentence that says no record exists.
_NONE = re.compile(
    r"\b(?:no|any|zero)\s+(?:matching\s+)?(?:e-?mails?|mails?|messages?|records?|threads?)\b"
    r"|\bnone\s+found\b|\bfound\s+(?:no|nothing)\b|\bnothing\s+(?:found|turned up)\b"
    r"|\bcould\s*n[o']t\s+find\b|\bdid\s+not\s+find\b|\bno\s+trace\b"
    # Verifier F1 (2026-11-12): the softer phrasings of "no mail".
    r"|\bnothing\s+(?:came\s+up|showed\s+up|matched|about|on|for)\b"
    r"|\bno\s+(?:results?|hits?|matches)\b|\bcame\s+(?:back\s+)?empty\b"
    r"|\breturned\s+(?:nothing|no\b)|\bempty\s+(?:result|search)\b",
    re.IGNORECASE,
)
#: A sentence that claims a change. A sentence with a negation is not a claim.
_CLAIM = re.compile(r"\b(?:posted|updated|set|changed|created|added)\b", re.IGNORECASE)
_NEGATED = re.compile(
    r"\b(?:not|n't|no|nothing|none|refus\w*|unable|cannot|can't|couldn't|didn't|"
    r"without|until|if|unless|want me|shall i|should i|would you)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Call:
    """One tool call of the run, in order: the orchestrator's or a sub-run's."""

    agent: str
    name: str
    args: dict[str, Any]
    result: str = ""


@dataclass
class Evidence:
    case_id: str
    names: tuple[str, ...]
    calls: list[Call]
    answer: str
    #: The query of each company search the fake took, and the mails it found.
    searches: list[str] = field(default_factory=list)
    found: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    #: Each write the fake took: ``(method, path, body)``.
    writes: list[tuple[str, str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    #: The name of each node the fake holds, by id.
    node_names: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    #: The mailbox of the run: ``(id, subject)`` of each mail.
    mailbox: tuple[tuple[str, str], ...] = tuple((m.id, m.subject) for m in fx.MAILS)


@dataclass(frozen=True)
class Rule:
    name: str
    passed: bool
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.name, "pass": self.passed, "why": self.why}


# ── the events become calls ──────────────────────────────────────────────────


def _args(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def calls_of(events: list[dict[str, Any]]) -> list[Call]:
    """The tool calls of the stream in the order they STARTED, with their results."""
    rows: dict[str, list[Any]] = {}
    order: list[str] = []
    for e in events:
        kind, cid = e.get("type"), str(e.get("toolCallId") or "")
        agent = str(e.get("agentName") or "orchestrator")
        key = f"{agent}:{cid}"
        if kind in ("TOOL_CALL_START", "SUB_AGENT_TOOL_CALL_START"):
            rows[key] = [agent, str(e.get("toolCallName") or ""), str(e.get("args") or ""), ""]
            order.append(key)
        elif kind == "TOOL_CALL_ARGS" and key in rows:
            if not rows[key][2]:  # a START frame that carries the args is whole
                rows[key][2] = str(e.get("delta") or "")
        elif kind in ("TOOL_CALL_RESULT", "SUB_AGENT_TOOL_CALL_RESULT") and key in rows:
            rows[key][3] = str(e.get("content") or "")
    return [Call(r[0], r[1], _args(r[2]), r[3]) for r in (rows[k] for k in order)]


# ── helpers ──────────────────────────────────────────────────────────────────


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _hold_mail(name: str) -> bool:
    return bool(mails_for(name))


def _all_names() -> set[str]:
    return {*fx.EXISTING, fx.NEW_PRODUCT, fx.NEW_CHILD, fx.NO_MAIL}


def _writes_of(ev: Evidence) -> list[tuple[int, Call]]:
    """``(index, call)`` of each write call that the model made."""
    return [(i, c) for i, c in enumerate(ev.calls) if c.name in WRITE_TOOLS]


def _text_of(call: Call) -> str:
    return " ".join(str(v) for v in call.args.values())


def _write_project(ev: Evidence, call: Call) -> str:
    """The project NAME that a write is about."""
    if call.name == "create_project":
        return str(call.args.get("name") or "")
    return ev.node_names.get(str(call.args.get("project_id") or ""), "")


def _mail_id_of(args: dict[str, Any]) -> str:
    return str(args.get("email_id") or args.get("message_id") or args.get("id") or "")


# ── the rules ────────────────────────────────────────────────────────────────


def alone_search(ev: Evidence) -> Rule:
    """Each name is searched alone: a query with its words and no other name's."""
    bad = []
    for name in ev.names:
        own = set(words_of(name))
        others = set().union(*(set(words_of(n)) for n in _all_names() if n != name))
        if not any(own <= set(words_of(q)) and not (set(words_of(q)) & others)
                   for q in ev.searches):
            bad.append(name)
    return Rule("alone_search", not bad, f"never searched alone: {bad}" if bad else "")


def read_before_write(ev: Evidence) -> Rule:
    """A write follows the read of each mail it names and of each mail found for its project."""
    problems = []
    found_for: dict[str, set[str]] = {}
    for query, ids in ev.found:
        for name in _all_names():
            if set(words_of(name)) <= set(words_of(query)):
                found_for.setdefault(name, set()).update(ids)
    for at, call in _writes_of(ev):
        read = {_mail_id_of(c.args) for c in ev.calls[:at] if c.name in READ_TOOLS}
        text = _text_of(call)
        named = {i for i, subject in ev.mailbox if i in text or subject in text}
        owed = named | found_for.get(_write_project(ev, call), set())
        missing = sorted(owed - read)
        if missing:
            problems.append(f"{call.name} {_write_project(ev, call)!r} before the read of {missing}")
    return Rule("read_before_write", not problems, "; ".join(problems))


def update_names_source(ev: Evidence) -> Rule:
    """Each status update names the id or the exact subject of a mail that holds its project."""
    problems = []
    for _at, call in _writes_of(ev):
        if call.name != "post_project_update":
            continue
        name = _write_project(ev, call)
        text = _text_of(call)
        if not any(m.id in text or m.subject in text for m in mails_for(name)):
            problems.append(f"the update on {name!r} names no source mail")
    return Rule("update_names_source", not problems, "; ".join(problems))


def card_per_write(ev: Evidence) -> Rule:
    """Each write that the fake took has one approved card, and no card stands alone."""
    approved = [c for c in ev.cards if str(c.get("answer") or "").startswith("APPROVE")]
    ok = len(ev.writes) == len(approved) == len(ev.cards)
    return Rule("card_per_write", ok,
                "" if ok else f"{len(ev.writes)} writes, {len(approved)} approved of {len(ev.cards)} cards")


def no_false_none(ev: Evidence) -> Rule:
    """The answer says "none found" only for a name that holds no mail, and names it."""
    problems = []
    for sentence in _sentences(ev.answer):
        if not _NONE.search(sentence):
            continue
        said = [n for n in _all_names() if n.lower() in sentence.lower()]
        if not said:
            problems.append(f"a none claim that names no one: {sentence[:100]!r}")
        problems += [f"says none for {n!r}, and the mailbox holds mail for it"
                     for n in said if _hold_mail(n)]
    return Rule("no_false_none", not problems, "; ".join(problems))


# ── the case rules ───────────────────────────────────────────────────────────


def facts_in_write(ev: Evidence, name: str, facts: tuple[str, ...]) -> Rule:
    """The create of *name* carries at least one fact of its mails, so it is not a one-line stub."""
    texts = [_text_of(c) for _i, c in _writes_of(ev)
             if c.name == "create_project" and str(c.args.get("name")) == name]
    ok = bool(texts) and any(f.lower() in t.lower() for t in texts for f in facts)
    return Rule(f"facts_in_write:{name}", ok,
                "" if ok else f"no create of {name!r} carries one of {facts}")


def wrote(ev: Evidence, names: tuple[str, ...], tool: str = "create_project") -> Rule:
    done = {_write_project(ev, c) for _i, c in _writes_of(ev) if c.name == tool}
    missing = [n for n in names if n not in done]
    return Rule(f"wrote:{tool}", not missing, f"no {tool} for {missing}" if missing else "")


def wrote_nothing(ev: Evidence) -> Rule:
    return Rule("wrote_nothing", not ev.writes and not ev.cards,
                f"{len(ev.writes)} writes, {len(ev.cards)} cards" if ev.writes or ev.cards else "")


def refusal_honest(ev: Evidence) -> Rule:
    """The answer says the update is not on, and claims no post."""
    problems = []
    if UPDATES_OFF not in ev.answer.lower():
        problems.append(f"the answer does not say {UPDATES_OFF!r}")
    for sentence in _sentences(ev.answer):
        if _CLAIM.search(sentence) and not _NEGATED.search(sentence):
            problems.append(f"a claim of a change: {sentence[:100]!r}")
    if ev.writes:
        problems.append(f"{len(ev.writes)} writes went through")
    return Rule("refusal_honest", not problems, "; ".join(problems))


def says_what_was_searched(ev: Evidence, name: str) -> Rule:
    """A "none found" for *name* says that it searched, and for what."""
    text = ev.answer.lower()
    ok = "search" in text and name.lower() in text and bool(_NONE.search(ev.answer))
    return Rule("says_what_was_searched", ok,
                "" if ok else f"the answer lacks a none claim that names the search of {name!r}")


def common(ev: Evidence) -> list[Rule]:
    return [alone_search(ev), read_before_write(ev), update_names_source(ev),
            card_per_write(ev), no_false_none(ev)]


def passed(rules: list[Rule]) -> bool:
    return all(r.passed for r in rules)
