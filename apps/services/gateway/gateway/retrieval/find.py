"""How a hit says how it matched (WS-FIND FIND-2, ``find_engine.md`` §3.3 to §3.5).

Pure functions and one flag reader. No SQL and no model call. With
``FIND_ORGS`` empty, the route never reaches this module, so its answer stays
the answer of KR-ZERO, byte for byte.

* :func:`content_words` is the fold of §3.3. "Sharath's" gives ``sharath``.
* :func:`coverage` counts the content words that a hit holds in its title,
  hint or snippet.
* :func:`rung_of` gives each ``matched_by`` its place in the ladder of §3.1.
* :func:`near_words` picks the words of the near lookup of rung 3 (HRD-18b,
  §3.3), and :func:`asked_form` gives a word as the member typed it.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from acb_common import get_settings, name_match

#: The ``matched_by`` values of §3.5, from the closed list. The lower the
#: number, the lower the rung of §3.1, and the earlier the hit comes on a tie.
RUNGS: dict[str, int] = {
    "exact": 1,
    "words": 1,
    "word": 2,
    "near": 3,
    "related": 4,
    "meaning": 5,
    "chat": 6,
    "outside": 7,
}

#: The stop words of Postgres (``english.stop``), the list of KR-ZERO. A stop
#: word is not a content word.
_STOP = frozenset(
    "i me my myself we our ours ourselves you your yours yourself yourselves he "
    "him his himself she her hers herself it its itself they them their theirs "
    "themselves what which who whom this that these those am is are was were be "
    "been being have has had having do does did doing a an the and but if or "
    "because as until while of at by for with about against between into "
    "through during before after above below to from up down in out on off over "
    "under again further then once here there when where why how all any both "
    "each few more most other some such no nor not only own same so than too "
    "very s t can will just don should now".split()
)

_PLURAL = re.compile(r"(?<=[a-z0-9]{3})s$")
#: One token of the query as the member typed it: letters and digits only.
_TOKEN = re.compile(r"[^\W_]+")


def find_allowed(organization_id: str | None) -> bool:
    """True when ``FIND_ORGS`` holds the org, or holds ``*``. Default OFF.

    The one reader of the setting. The org comes from the session, never from
    request input (R5). A reload applies a change (``docs/flag_reload.md``).
    """
    if not organization_id:
        return False
    raw = str(getattr(get_settings(), "find_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or str(organization_id).strip().lower() in listed


def content_words(query: str) -> list[str]:
    """The content words of *query*, in order, folded to lower case (§3.3).

    No stop word, no one-letter word and no final ``'s``, so "Sharath's"
    gives ``sharath``. A repeated word counts once. Pure.
    """
    out: list[str] = []
    for word in name_match.words(query):
        if len(word) < 2 or word in _STOP or word in out:
            continue
        out.append(word)
    return out


def _stem(word: str) -> str:
    """A plural folded to its singular, so "printers" holds "printer"."""
    return _PLURAL.sub("", word)


def coverage(words: list[str], text: str) -> int:
    """How many of *words* appear in *text*, as whole words (§3.4). Pure."""
    held = {_stem(w) for w in name_match.words(text)}
    return sum(1 for word in words if _stem(word) in held)


def rung_of(matched_by: str | None) -> int:
    """The rung number of a ``matched_by`` value. An unknown value is last."""
    return RUNGS.get(matched_by or "", len(RUNGS) + 1)


def missed(found: dict[str, list[Any]], words: list[str]) -> bool:
    """True when a pass is a miss (§3.3, D-FIND-4): its best hit holds fewer
    than all the content words. Zero hits is one kind of miss. Each hit is
    stamped already, so ``covers`` counts its words. Pure."""
    best = max((h.covers or 0 for hits in found.values() for h in hits), default=0)
    return best < len(words)


def held(found: dict[str, list[Any]], words: list[str]) -> set[str]:
    """The content words that some hit of *found* holds, in its title, hint
    or snippet, as :func:`coverage` counts them (§3.3). Pure."""
    out: set[str] = set()
    for hits in found.values():
        for hit in hits:
            text = f"{hit.title} {hit.hint} {hit.snippet}"
            out |= {w for w in words if w not in out and coverage([w], text)}
    return out


#: The rungs whose hit matched the member's own words, not a near spelling.
OWN_RUNGS = frozenset({"exact", "words", "word"})


def near_report(entries: list[dict[str, Any]], found: dict[str, list[Any]],
                broadened: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Each entry of the route's ``near``, with ``held`` (HRD-18b).

    ``held`` is true when the word has a hit of its own: the word alone found
    records (``broadened``), or a hit of the answer that matched the member's
    own words holds it in its title, hint or snippet. Only a word with no
    such hit may open the answer with "No record holds" (§3.6). Pure.
    """
    own = [f"{h.title} {h.hint} {h.snippet}" for hits in found.values() for h in hits
           if h.matched_by in OWN_RUNGS]
    out: list[dict[str, Any]] = []
    for entry in entries:
        asked = name_match.words(entry.get("word"))
        alone = any(
            isinstance(b, dict) and name_match.words(b.get("word")) == asked and b.get("hits")
            for b in broadened or []
        )
        text = len(asked) == 1 and any(coverage(asked, t) for t in own)
        out.append({**entry, "held": bool(alone or text)})
    return out


def spelled(words: list[str], word: str, spelling: str) -> list[str]:
    """*words* with *word* replaced by *spelling*, both folded as
    :func:`content_words` folds them (HRD-18b). Pure."""
    asked = name_match.words(word)
    near = name_match.words(spelling)
    if len(asked) != 1 or len(near) != 1:
        return list(words)
    return [near[0] if w == asked[0] else w for w in words]


def stamped(hit: Any, words: list[str], matched_by: str, **extra: Any) -> Any:
    """*hit* with ``matched_by``, ``covers`` and ``of`` set (§3.5).

    A hit that already says how it matched keeps its lower rung. Pure: the
    hit is frozen, so this gives a new one. A ``near`` hit counts its
    spelling ``as_`` for its ``word`` (§3.4, HRD-18b).
    """
    if getattr(hit, "matched_by", None) and rung_of(hit.matched_by) <= rung_of(matched_by):
        matched_by, extra = hit.matched_by, {}
    counted = words
    if matched_by == "near" and extra.get("word") and extra.get("as_"):
        counted = spelled(words, extra["word"], extra["as_"])
    covers = coverage(counted, f"{hit.title} {hit.hint} {hit.snippet}")
    return dataclasses.replace(
        hit, matched_by=matched_by, covers=covers, of=len(words), **extra,
    )


def asked_form(query: str, word: str) -> str:
    """*word*, a content word of *query*, as the member typed it: "Sharath"
    for ``sharath`` in "Sharath's printer". Pure."""
    for token in _TOKEN.findall(query or ""):
        if name_match.words(token) == [word]:
            return token[:name_match.NEAR_MAX_CHARS]
    return word


def near_words(query: str, words: list[str], held: set[str]) -> list[str]:
    """The words of the near lookup of rung 3, in order (§3.3, HRD-18b).

    First the words that no hit holds, then the words that the member wrote
    with a capital letter, then the longest. Each word passes
    ``name_match.near_eligible``, so it has 4 letters or more. At most
    ``name_match.NEAR_MAX_WORDS``. Pure.
    """
    capital = {
        w for token in _TOKEN.findall(query or "") if token[:1].isupper()
        for w in name_match.words(token)
    }
    eligible = [w for w in words if name_match.near_eligible(w)]
    # `sorted` is stable, so a tie keeps the order of the query.
    ranked = sorted(eligible, key=lambda w: (w in held, w not in capital, -len(w)))
    return ranked[:name_match.NEAR_MAX_WORDS]
