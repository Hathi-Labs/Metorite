"""The tool index and the lexical ranker (WS-67 TS-1).

``specs/one_assistant_context.md`` section 4.2.9, "The index and the ranker".
This is the TS-1 seam. ``tool_packs.FindTools`` (TS-2a) passes the candidate
tools to :func:`rank`. ``_tool_injection._turn_with_skills`` calls
:func:`match_skill_descriptions` only (TOF-1c).

The index holds each pack tool once. An entry holds the name of the tool, its
pack slug, the label and summary of its family, the ``when`` words of its pack
and the first line of its description. All of that text is first-party text
from this repo. No MCP text, no skill text and no member text enters the index
until TS-9. A caller may pass ``descriptions``, a name to text map from the
tools that the owner built. The index reads a description only for a name that
a pack holds. It drops any other name and its text.

The ranker is pure. It reads no file, no clock and no random source. The same
input gives the same order, and a tie breaks by tool name. ``allow`` is a set
of tool names or pack slugs that the CALLER already filtered. The ranker never
decides permission.

**The skill description match (TOF-1c).** :func:`match_skill_descriptions`
reads a SEPARATE corpus: the descriptions of the published Studio skills that
the member may load. It uses that text for scores only. The text never enters
the index, and :func:`rank`, :func:`build_index` and :func:`default_index`
never read it. Only a skill id and a count leave the function. The owner
accepted this corpus under D222 decision 6 on 2026-11-16 (task 3412).

Fence: ``tests/unit/test_tool_search_index.py``.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from acb_skills.hidden_text import strip_hidden
from acb_skills.skill_families import SKILL_FAMILIES, pack_slugs
from acb_skills.tool_packs import PACKS, _plain, _word_re

#: The weight of a match in each field. A name counts most.
W_NAME = 6.0
W_FAMILY = 3.0
W_WHEN = 2.0
W_DESC = 1.5
#: A prefix match scores this part of a whole-word match.
PREFIX_PART = 0.5
#: The extra score when the query holds the whole tool name as a phrase.
PHRASE_BONUS = 3.0
#: The longest description line that the index keeps.
DESC_MAX = 200

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "any",
        "are",
        "at",
        "be",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "get",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "show",
        "that",
        "the",
        "their",
        "there",
        "this",
        "to",
        "us",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)


@dataclass(frozen=True)
class ToolEntry:
    """One tool in the index."""

    name: str
    pack: str
    label: str
    summary: str
    when: str
    description: str


def _stem(word: str) -> str:
    """A light stem: "reminders" and "reminder" both give "remind"."""
    for tail in ("ers", "er", "ing", "ed", "es", "s"):
        if len(word) - len(tail) >= 4 and word.endswith(tail) and not word.endswith("ss"):
            return word[: -len(tail)]
    return word


def _tokens(text: str) -> tuple[str, ...]:
    """The stems of the words of *text*. A "_" ends a word."""
    return tuple(_stem(word) for word in _TOKEN.findall(_plain(text)))


def _first_line(text: object) -> str:
    """The first non-empty line of *text*, cut at DESC_MAX."""
    for line in str(text or "").splitlines():
        if line.strip():
            return " ".join(line.split())[:DESC_MAX]
    return ""


def build_index(
    descriptions: Mapping[str, str] | None = None,
) -> tuple[ToolEntry, ...]:
    """The index of the pack tools, in registry order, each name once.

    A tool in two families keeps its first entry. *descriptions* maps a tool
    name to its description. A name that no pack holds is dropped.
    """
    given = descriptions or {}
    seen: set[str] = set()
    entries: list[ToolEntry] = []
    for slug in pack_slugs():
        pack = PACKS.get(slug)
        family = SKILL_FAMILIES[slug]
        for name in family["tools"]:
            if name in seen:
                continue
            seen.add(name)
            entries.append(
                ToolEntry(
                    name=name,
                    pack=slug,
                    label=str(family.get("label", "")),
                    summary=str(family.get("summary", "")),
                    when=pack.when if pack is not None else "",
                    description=_first_line(given.get(name)),
                )
            )
    return tuple(entries)


@functools.lru_cache(maxsize=1)
def default_index() -> tuple[ToolEntry, ...]:
    """The index with no descriptions, built once for the process."""
    return build_index()


def _field_score(token: str, words: tuple[str, ...], weight: float) -> float:
    """The best score of *token* against *words*: whole word, or prefix."""
    if token in words:
        return weight
    if len(token) >= 3 and any(w.startswith(token) for w in words):
        return weight * PREFIX_PART
    return 0.0


def _score(query: str, tokens: tuple[str, ...], entry: ToolEntry) -> float:
    name = _tokens(entry.name)
    family = _tokens(entry.label + " " + entry.summary)
    when = _tokens(entry.when)
    desc = _tokens(entry.description)
    total = 0.0
    for token in tokens:
        total += _field_score(token, name, W_NAME)
        total += _field_score(token, family, W_FAMILY)
        total += _field_score(token, when, W_WHEN)
        total += _field_score(token, desc, W_DESC)
    if total and _word_re(entry.name.replace("_", " ")).search(_plain(query)):
        total += PHRASE_BONUS
    return total


def rank(
    query: str,
    index: tuple[ToolEntry, ...] | None = None,
    *,
    limit: int = 5,
    allow: Collection[str] | None = None,
) -> list[tuple[str, str, float]]:
    """The best *limit* tools for *query*, as (tool name, pack slug, score).

    Only a score above 0 returns. A tie breaks by tool name. *allow* is a set
    of tool names or pack slugs. None means no narrowing. An empty set gives
    an empty list.
    """
    tokens = tuple(dict.fromkeys(t for t in _tokens(query) if t not in _STOP))
    if not tokens or limit <= 0:
        return []
    entries = default_index() if index is None else index
    scored = []
    for entry in entries:
        if allow is not None and entry.name not in allow and entry.pack not in allow:
            continue
        score = _score(query, tokens, entry)
        if score > 0:
            scored.append((-score, entry.name, entry.pack))
    scored.sort()
    return [(name, pack, -neg) for neg, name, pack in scored[:limit]]


# ── The skill description match (TOF-1c) ─────────────────────────────────────
# ``specs/ai_studio_rows/TOF-1c.md``. A separate corpus, used for scores only:
# no text of it enters the index, and no text of it leaves the function.

#: The fewest distinct whole-word stems that the turn and a description must
#: share. A prefix gives no credit.
DESC_MATCH_MIN = 2
#: The most distinct content stems of the turn that the match reads, from the
#: start. So a long turn adds no work to the scores.
TURN_STEMS_MAX = 32
#: The words that the match never counts: the stop words of :func:`rank` and
#: "use", because each description of the F6a offer starts "Use this".
_DESC_SKIP = _STOP | frozenset({"use"})


def _turn_stems(words: object) -> frozenset[str]:
    """The first :data:`TURN_STEMS_MAX` distinct content stems of *words*."""
    found: dict[str, None] = {}
    for word in _TOKEN.finditer(_plain(strip_hidden(str(words or "")))):
        stem = _stem(word.group())
        if stem in _DESC_SKIP or stem in found:
            continue
        found[stem] = None
        if len(found) >= TURN_STEMS_MAX:
            break
    return frozenset(found)


def _desc_stems(text: object) -> frozenset[str]:
    """The content stems of *text*, cut at :data:`DESC_MAX` on a word end."""
    plain = _plain(strip_hidden(str(text or "")))
    if len(plain) > DESC_MAX:
        plain = plain[: DESC_MAX + 1].rsplit(" ", 1)[0]
    return frozenset(t for t in _tokens(plain) if t not in _DESC_SKIP)


def match_skill_descriptions(
    words: str, texts: Mapping[str, str],
) -> tuple[str, int] | None:
    """TOF-1c. The one skill whose description the member's *words* name, as
    (skill id, hits), or None.

    *texts* maps a skill id to its description. The caller passes only the
    skills that the member may load. *hits* is the number of distinct
    whole-word stems that the turn and the description share. The turn gives
    its first :data:`TURN_STEMS_MAX` content stems. A description gives the
    stems of its first :data:`DESC_MAX` characters. The skill with the most
    hits wins when it has at least :data:`DESC_MATCH_MIN`. A tie at the top
    gives None.

    Pure: no I/O, no clock and no random source. Only an id and a count leave
    the function, never a word of the text. The order of *texts* never
    changes the answer."""
    turn = _turn_stems(words)
    if len(turn) < DESC_MATCH_MIN:
        return None
    best: list[str] = []
    top = 0
    for skill_id, text in texts.items():
        hits = len(turn & _desc_stems(text))
        if hits < DESC_MATCH_MIN or hits < top:
            continue
        if hits > top:
            best, top = [], hits
        best.append(str(skill_id))
    return (best[0], top) if len(best) == 1 else None
