"""How Metorite writes: the one voice of every agent and drafting call (WS-52).

Owning spec: ``project-docs/specs/agent_writing_voice.md``.

This module holds the house voice as data, and nothing else:

* :data:`CORE` is the contract, twelve rules. Every agent reads it, on both
  runtimes, and every member-facing drafting call reads it.
* :data:`OVERLAYS` adds one short rule set for each surface: ``chat``,
  ``title``, ``description``, ``email``, ``message`` and ``summary``.
* :func:`voice_prompt` returns the text for one surface, or for an agent.
  :func:`core_prompt` returns the core alone, for a sub-agent.
* :func:`voice_lint` is a deterministic checker for evals and tests. It
  NEVER rewrites live output.

The text is static, so it is byte-stable across turns and prompt caching
covers it. It names no tool, so it fits every agent.

**Where it reaches an agent.** ``orchestrator._tool_injection._apply_voice``
puts :func:`voice_prompt` (``AGENT``) into every agent's system prompt once,
right after the agent's own instructions and before every platform block.
It is NOT part of the Copilot addendum. A sub-agent reads :func:`core_prompt`,
because its text goes to the parent agent and not to a member.
``config.json: floor_opt_out`` names tools, so it cannot remove the voice. An
agent may add a stricter rule in its own instructions, and it never drops the
core. Fence: ``tests/unit/test_agent_voice.py``.

**Outbound text defers to the member.** The ``email`` and ``message``
overlays say that the member's own instructions, writing style and voice
profile come first. The house voice is the floor, never an override.

STE (``docs/style_ste.md``) governs the repo's docs and the replies to the
owner. This contract governs what the product writes for a member. They are
two audiences, and neither one replaces the other.
"""
from __future__ import annotations

import re
from typing import NamedTuple

#: The heading that opens the agent block. The injection reads it as its
#: idempotency marker, so a second injection adds nothing.
VOICE_HEADING = "## How Metorite writes"

#: The contract (owner's list, refined 2026-10-10). Keep it at about 300
#: tokens or fewer: ``test_agent_voice.py`` measures it.
CORE = """1. Give the answer or the result first. No opening line about what you are going to do, and no closing line that repeats it.
2. Write the way a capable colleague talks: plain words, active voice, concrete verbs. Write "decide", not "make a decision".
3. Let sentence length follow the thought. Do not give the sentences in one paragraph the same shape.
4. Say what is so. Do not frame it against what it is not, and do not build up to a reveal.
5. Give as many items as the facts hold. Do not round to three, and do not pair ideas for rhythm.
6. Cut filler (genuinely, really, truly, actually, very, just, simply) and corporate verbs (leverage, utilize, underscore, reflect, streamline, empower, unlock).
7. No em dashes. Use a comma, a full stop or brackets.
8. If something is uncertain, say so once, plainly, where it applies. Do not hedge anything else.
9. No performed enthusiasm, no praise for the question, and no apology unless something went wrong.
10. Stay inside the records. Never invent a name, number, date or quote.
11. Untangle stacked nouns: "the review of how we approve vendor payments", not "the vendor payment approval process review".
12. Match the language of the person who will read it. These rules cover your own words, never quoted text, code or data."""

#: One rule set for each surface. A drafting call adds the core and ONE of
#: these, and an agent reads all of them. "Name the source" lives in ``chat``
#: only: a record id must never reach an outside recipient of an email.
OVERLAYS: dict[str, str] = {
    "chat": (
        "Chat: name the source (task #141, the 3 Oct email from Priya). Use "
        "formatting, or a list, only where the items stand apart and it helps scanning."
    ),
    "title": (
        "Title: up to 8 words unless the prompt sets a limit, sentence case, "
        'no final full stop, no "X: Y" subtitle.'
    ),
    "description": (
        "Description (task, project, card body): what, why, and when it counts "
        "as done, in 1 to 3 short sentences."
    ),
    "email": (
        "Email: the member's own voice. The member's own instructions, writing "
        "style and voice profile come first. Where they differ from these rules, "
        "follow the member. Write in the recipient's language. One clear ask "
        'when possible. No "I hope this finds you well".'
    ),
    "message": (
        "Message (WhatsApp or chat to a contact): short and conversational, with "
        "no greeting or sign-off unless the member uses them. The member's own "
        "instructions and style come first. Write in the recipient's language."
    ),
    "summary": (
        "Summary (digests, meeting notes, reports): decisions and facts first, "
        "then open items. No adjectives that pass judgment."
    ),
}

#: The surface name of an agent's block: the core and every overlay.
AGENT = "agent"

SURFACES: tuple[str, ...] = tuple(OVERLAYS)

#: The line that opens the chief-of-staff role. Tests look for it as the
#: section marker, so they never pin the full text.
CHIEF_OF_STAFF_HEADING = "As chief of staff (the member decides, you prepare the decision):"

#: The hard cap of a routine reply that the role states, in words of prose.
#: The eval measures against 120 (``evals/orchestrator_cos/run.py``
#: ``ROUTINE_CAP``). The prompt asks for less, because a model treats a soft
#: cap as a target: "under about 120 words" gave 7 of 14 replies over 120 on
#: 2026-10-14. Spec §11.8.
CHIEF_OF_STAFF_WORD_CAP = 100

#: One rule set for each agent ROLE (owner, 2026-10-13). A surface says what
#: kind of text it is. A role says what one agent is for, so it reaches that
#: agent only, through :data:`AGENT_ROLES`. Spec §11. Keep each role at about
#: 300 tokens or fewer: ``test_orchestrator_voice.py`` measures it.
ROLES: dict[str, str] = {
    "chief_of_staff": "\n".join((
        CHIEF_OF_STAFF_HEADING,
        "- Open with the bottom line: the answer, the status, or the decision the "
        "member must take. Do not restate the question.",
        "- Then the 2 to 5 points that matter, one line each, most important "
        "first. Leave out what does not change what the member does next. Put "
        "all that is on track in one clause.",
        "- Never paste the output of a read or another agent's reply. Say what it "
        "means. The detail stays in its step, a link, or one folded card. The "
        "exception is text the member asked for (a draft, a document): give it "
        "whole.",
        "- Raise what the member did not ask about only when it is real and close: "
        "a deadline, a risk, a conflict, a number out of line, or a thing that "
        "waits on the member, one line each.",
        "- For a decision, give each option with its trade-off, then your "
        "recommendation. No second list of reasons. Then name the next step "
        "and who owns it.",
        f"- A routine reply has at most {CHIEF_OF_STAFF_WORD_CAP} words of text, "
        "and no headings or labels. Stop after the last point: no summary, no "
        "menu of choices, one short offer at most. A fact gets a sentence. When "
        "the member asks for the detail or the data, give all of it.",
    )),
}

#: The agent that reads each role, by registry name. Every other agent reads
#: no role, so its prompt does not change.
AGENT_ROLES: dict[str, str] = {
    "orchestrator": "chief_of_staff",
}


def role_for(agent_name: str | None) -> str | None:
    """The role of the agent *agent_name*, or ``None`` for no role."""
    return AGENT_ROLES.get(agent_name or "")


#: The line a JSON drafting call adds, so the rules bind the prose values
#: and never the keys or the shape that code parses.
JSON_NOTE = (
    "The output is JSON. These rules bind only prose a member reads, never keys, "
    "enum values, ids, patterns, names, addresses or quoted source text."
)


def voice_prompt(
    surface: str = AGENT, *more: str, json: bool = False, role: str | None = None,
) -> str:
    """The voice for *surface*, as system-prompt text.

    ``AGENT`` (the default) gives the heading, the core and every overlay,
    because an agent writes chat replies, titles, descriptions, emails and
    summaries. A surface name gives the core and that one overlay, for a
    direct drafting call. A call whose output holds two kinds of text (a
    title and a description) names both, and gets both overlays. ``json``
    adds :data:`JSON_NOTE`. An unknown name raises ``KeyError``, so a typo
    fails in a test and never ships a prompt with no overlay.

    *role* (a key of :data:`ROLES`) adds that role after the overlays. Only
    the agent block takes a role: a drafting call writes one kind of text,
    and a role describes what an agent is for, so a role on a surface raises
    ``ValueError``.
    """
    if role is not None and (surface != AGENT or more):
        raise ValueError("a role belongs to the agent block only")
    if surface == AGENT and not more:
        overlays = "\n".join(f"- {text}" for text in OVERLAYS.values())
        text = f"{VOICE_HEADING}\n{CORE}\nBy surface:\n{overlays}"
        if role is not None:
            text = f"{text}\n{ROLES[role]}"
    else:
        overlays = "\n".join(OVERLAYS[name] for name in (surface, *more))
        text = f"{VOICE_HEADING}\n{CORE}\n{overlays}"
    return f"{text}\n{JSON_NOTE}" if json else text


def core_prompt() -> str:
    """The heading and the core, with no overlay: a sub-agent's block.

    A sub-agent answers its parent agent, and the parent writes for the
    member, so the overlays would only cost tokens on each delegated call.
    """
    return f"{VOICE_HEADING}\n{CORE}"


# ---------------------------------------------------------------------------
# The checker (evals and tests only, never live output)
# ---------------------------------------------------------------------------


class Finding(NamedTuple):
    """One break of the voice. ``rule`` is a stable id, ``match`` the text."""

    rule: str
    match: str


FILLER_WORDS: tuple[str, ...] = (
    "genuinely", "really", "truly", "actually", "very", "just", "simply",
)

#: The stems of the corporate verbs. Each matches its inflections
#: (leverages, leveraged, leveraging, utilise, utilization and so on).
CORPORATE_STEMS: tuple[str, ...] = (
    "leverag", "utili[sz]", "underscor", "reflect", "streamlin", "empower",
    "unlock",
)

_FENCED = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_QUOTED_LINE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)
_QUOTED_SPAN = re.compile(r'"[^"\n]{1,300}"|\u201c[^\u201d\n]{1,300}\u201d')

_EM_DASH = re.compile("\u2014")
_FILLER = re.compile(r"\b(" + "|".join(FILLER_WORDS) + r")\b", re.IGNORECASE)
_CORPORATE = re.compile(
    r"\b(" + "|".join(CORPORATE_STEMS) + r")[a-z]*\b", re.IGNORECASE,
)
_OPENER = re.compile(
    r"\A\W*(great question|good question|certainly|absolutely|of course|"
    r"i'd be happy to|i would be happy to|i'd be glad to|let me)\b",
    re.IGNORECASE,
)
_CLOSER = re.compile(
    r"(?:^|(?<=[.!?]\s)|(?<=\n))\W*(in summary|in conclusion|to sum up|"
    r"to summarize|overall,|hope this helps|i hope this helps)",
    re.IGNORECASE,
)
#: "not X, but Y" and "it's not X, it's Y". A "but" that opens a clause of
#: its own ("I could not find it, but I found a copy") is a plain contrast,
#: so a pronoun after "but" is no match.
_NOT_X_BUT_Y = re.compile(
    r"\b(?:not|isn't|aren't|wasn't|weren't)\b(?:\s+(?:just|only|merely))?"
    r"[^.!?\n]{1,60}?[,;]\s*"
    r"(?:but\b(?!\s+(?:i|we|you|he|she|they|there|it)\b)"
    r"|it's\b|it is\b|they're\b|they are\b)",
    re.IGNORECASE,
)
_HOPE_WELL = re.compile(
    r"\bhope (?:this|that|the)?\s*(?:email|message|note|mail)?\s*finds you well\b"
    r"|\bhope you(?:'re| are) (?:doing )?well\b",
    re.IGNORECASE,
)


def _own_words(text: str) -> str:
    """*text* with fenced code, inline code, quoted lines and quoted spans
    blanked. The rules bind the writer's own words only (rule 12)."""
    out = _FENCED.sub(" ", text)
    out = _INLINE_CODE.sub(" ", out)
    out = _QUOTED_LINE.sub(" ", out)
    return _QUOTED_SPAN.sub(" ", out)


def _surface_findings(surface: str, text: str) -> list[Finding]:
    found: list[Finding] = []
    stripped = text.strip()
    if surface == "title":
        words = stripped.split()
        if len(words) > 8:
            found.append(Finding("title_length", f"{len(words)} words"))
        if stripped.endswith("."):
            found.append(Finding("title_full_stop", stripped[-12:]))
        if re.search(r"\S:\s+\S", stripped):
            found.append(Finding("title_subtitle", stripped))
    elif surface == "description":
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", stripped) if s]
        if len(sentences) > 3:
            found.append(Finding("description_length", f"{len(sentences)} sentences"))
    return found


def voice_lint(text: str, surface: str | None = None) -> list[Finding]:
    """Each place where *text* breaks the voice, in a stable order.

    Deterministic, with no model. It skips fenced code, inline code, quoted
    lines (``>``) and quoted spans. *surface* adds the checks of that
    overlay (``title``, ``description``). It is for evals and tests:
    nothing calls it on live output, and it never rewrites text.
    """
    own = _own_words(text or "")
    found: list[Finding] = []
    found += [Finding("em_dash", m.group(0)) for m in _EM_DASH.finditer(own)]
    found += [Finding("filler", m.group(0)) for m in _FILLER.finditer(own)]
    found += [Finding("corporate_verb", m.group(0)) for m in _CORPORATE.finditer(own)]
    found += [Finding("opener", m.group(1)) for m in _OPENER.finditer(own)]
    found += [Finding("closer", m.group(1)) for m in _CLOSER.finditer(own)]
    found += [Finding("not_x_but_y", m.group(0)) for m in _NOT_X_BUT_Y.finditer(own)]
    found += [Finding("hope_finds_you_well", m.group(0)) for m in _HOPE_WELL.finditer(own)]
    if surface:
        # The overlay counts the whole text, quotes included, but not code.
        no_code = _INLINE_CODE.sub(" ", _FENCED.sub(" ", text or ""))
        found += _surface_findings(surface, no_code)
    return found


__all__ = [
    "AGENT", "AGENT_ROLES", "CHIEF_OF_STAFF_HEADING", "CORE", "CORPORATE_STEMS",
    "FILLER_WORDS", "Finding", "JSON_NOTE", "OVERLAYS", "ROLES",
    "core_prompt", "role_for",
    "SURFACES", "VOICE_HEADING", "voice_lint", "voice_prompt",
]
