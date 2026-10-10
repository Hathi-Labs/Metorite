"""How Metorite writes: the one voice of every agent and drafting call (WS-52).

Owning spec: ``project-docs/specs/agent_writing_voice.md``.

This module holds the house voice as data, and nothing else:

* :data:`CORE` is the contract, twelve rules. Every agent reads it, on both
  runtimes, and every member-facing drafting call reads it.
* :data:`OVERLAYS` adds one short rule set for each surface: ``chat``,
  ``title``, ``description``, ``email`` and ``summary``.
* :func:`voice_prompt` returns the text for one surface, or for an agent.
* :func:`voice_lint` is a deterministic checker for evals and tests. It
  NEVER rewrites live output.

The text is static, so it is byte-stable across turns and prompt caching
covers it. It names no tool, so it fits every agent.

**Where it reaches an agent.** ``orchestrator._tool_injection`` puts
:func:`voice_prompt` (``AGENT``) into every agent's system prompt once, right
after the agent's own instructions: the Copilot addendum
(``_build_injected_tools_addendum``) and the native MAF instructions
(``_with_voice``). ``config.json: floor_opt_out`` names tools, so it cannot
remove the voice. An agent may add a stricter rule in its own instructions,
and it never drops the core. Fence: ``tests/unit/test_agent_voice.py``.

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
10. Stay inside the records. Name the source (task #141, the 3 Oct email from Priya). Never invent a name, number, date or quote.
11. Untangle stacked nouns: "the review of how we approve vendor payments", not "the vendor payment approval process review".
12. Match the member's language. Use a list only when the items stand apart. These rules cover your own words, never quoted text, code or data."""

#: One rule set for each surface. A drafting call adds the core and ONE of
#: these, and an agent reads all of them.
OVERLAYS: dict[str, str] = {
    "chat": "Chat: the answer in the first sentence. Formatting only where it helps scanning.",
    "title": 'Title: up to 8 words, sentence case, no final full stop, no "X: Y" subtitle.',
    "description": (
        "Description (task, project, card body): what, why, and when it counts "
        "as done, in 1 to 3 short sentences."
    ),
    "email": (
        "Email: the member's own voice. One clear ask when possible. "
        'No "I hope this finds you well".'
    ),
    "summary": (
        "Summary (digests, meeting notes, reports): decisions and facts first, "
        "then open items. No adjectives that pass judgment."
    ),
}

#: The surface name of an agent's block: the core and every overlay.
AGENT = "agent"

SURFACES: tuple[str, ...] = tuple(OVERLAYS)


def voice_prompt(surface: str = AGENT) -> str:
    """The voice for *surface*, as system-prompt text.

    ``AGENT`` (the default) gives the heading, the core and every overlay,
    because an agent writes chat replies, titles, descriptions, emails and
    summaries. A surface name gives the core and that one overlay, for a
    direct drafting call. An unknown name raises ``KeyError``, so a typo
    fails in a test and never ships a prompt with no overlay.
    """
    if surface == AGENT:
        overlays = "\n".join(f"- {text}" for text in OVERLAYS.values())
        return f"{VOICE_HEADING}\n{CORE}\nBy surface:\n{overlays}"
    overlay = OVERLAYS[surface]
    return f"{VOICE_HEADING}\n{CORE}\n{overlay}"


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
    "AGENT", "CORE", "CORPORATE_STEMS", "FILLER_WORDS", "Finding", "OVERLAYS",
    "SURFACES", "VOICE_HEADING", "voice_lint", "voice_prompt",
]
