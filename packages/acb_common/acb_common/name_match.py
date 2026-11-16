"""The ONE matcher of a name as people say it (HRD-18a, ``chat_failure_modes.md`` F1).

A member types a name the way they say it: "Sharath" for «Sharat», "Swagata"
for «Sougata», "nuropeak" for «Neuropeak». An exact or a substring match
misses each of these. This module holds the one rule of a near spelling, so
the CRM search, the colleague lookup (``resolve_person``) and the contact
lookup (``find_contact``) agree on what is near.

**One judge.** :func:`name_score` folds each word (:func:`fold`) and compares
the trigram sets of the two words, built the way ``pg_trgm`` builds them.
When both words have :data:`SOUND_MIN_LETTERS` letters or more, it also
compares their sound keys (:func:`sound`), at :data:`SOUND_WEIGHT`.
:data:`NEAR_SCORE` is the floor of a near name.

**SQL only finds candidates.** The CRM search cannot fold a column without an
index or a migration. So it reads candidates with ``pg_trgm``
``strict_word_similarity`` at the low floor :data:`NEAR_CANDIDATE_SIMILARITY`,
at most :data:`NEAR_CANDIDATES` rows of each table. Then :func:`name_score`
scores each candidate, and only a name at :data:`NEAR_SCORE` or more is near.
So the CRM search never calls near a name that the two lookups call far.
The reverse is not true yet: a candidate must first share raw trigrams. So a
pair whose raw letters differ a lot (Laxmi and Lakshmi, Puja and Pooja) is
near in the two lookups and not yet in the CRM list's own near pass. The
near step of the company search (HRD-18b, task 3226) reads a vocabulary, so
it finds these pairs.

**A vocabulary in Python** (HRD-18b, task 3226). The near lookup of the
company search reads the names that the member can open, at most
:data:`NEAR_VOCABULARY` rows of each table, and :func:`near_names` scores
each of their words here. No raw SQL floor stands between a name and the
judge, so Laxmi finds «Lakshmi» and Puja finds «Pooja». :func:`best_spelling`
picks the spelling that the search then asks again with.

**A near match is never a match.** Each caller marks it near, and the model
asks the member to confirm it. So a few pairs of different names may stay
near: Kamal and Kamala, Lalit and Lalita, Pavan and Pavani, Hema and Hima,
Dinesh and Dhanesh, Venkat and Venkatesh. The member answers the question,
and nothing acts on a near name alone. Kiran and Karan stay apart.

It imports the standard library only, so the gateway and the skills can
import it. It opens no connection. Fence: ``tests/unit/test_name_variants.py``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from typing import Any, NamedTuple

#: The spelling variants that one sound has, in the order :func:`fold` applies
#: them. Each pair maps the left text to the right text, inside a word. The
#: first three run before the rest: "x" becomes "ksh" before "sh" becomes
#: "s", and "ow" becomes "ou" before "w" becomes "v".
VARIANTS: tuple[tuple[str, str], ...] = (
    ("x", "ksh"),
    ("iya", "ya"),
    ("ow", "ou"),
    ("th", "t"),
    ("sh", "s"),
    ("bh", "b"),
    ("dh", "d"),
    ("gh", "g"),
    ("kh", "k"),
    ("ph", "f"),
    ("w", "v"),
    ("aa", "a"),
    ("ee", "i"),
    ("e", "i"),
    ("oo", "u"),
    ("ou", "o"),
)

#: The floor of :func:`name_score` for a near name. The fixed pairs of the
#: fence score 0.9 or more, and no pair of the fence's other names reaches it.
NEAR_SCORE = 0.6

#: The floor of ``pg_trgm`` ``strict_word_similarity(word, column)`` for a
#: CANDIDATE of the CRM near pass. :func:`name_score` then judges each
#: candidate. It is low on purpose: "Swagata" in «Sougata Maji» is 0.33 in
#: SQL, and 0.9 here.
NEAR_CANDIDATE_SIMILARITY = 0.3

#: The most candidates that the CRM near pass reads from one table, nearest
#: first by ``strict_word_similarity``.
NEAR_CANDIDATES = 200

#: A word needs this many letters before it may match nearly. A shorter
#: word has too few trigrams, so nearly every name would be near it.
NEAR_MIN_LETTERS = 4

#: A longer word gets no near pass. No name is that long, and the cost of a
#: trigram measure grows with the length of the word.
NEAR_MAX_CHARS = 40

#: A query of more words than this gets no near pass. It bounds the cost of
#: the SQL, and a long query is a phrase, not a name.
NEAR_MAX_WORDS = 3

#: Both words need this many letters before their sound keys count. A short
#: sound key matches too many names ("kiran" and "karan" share one).
SOUND_MIN_LETTERS = 6

#: What a sound key match is worth, against a fold match of 1.0. A name whose
#: fold matches ranks above a name whose sound only matches.
SOUND_WEIGHT = 0.9

#: The most rows of one table that a near lookup reads (HRD-18b). Each app
#: reads its newest rows first, so the cap drops the oldest names.
NEAR_VOCABULARY = 2000

_SPLIT = re.compile(r"[^0-9a-z]+")
#: One token of a name as the record spells it: letters and digits only.
_TOKEN = re.compile(r"[^\W_]+")
_DOUBLE = re.compile(r"(.)\1+")
#: A ``v`` or ``y`` between a consonant and a vowel: the glide of "Swagata".
_GLIDE = re.compile(r"(?<=[b-df-hj-np-tv-z])[vy](?=[aeiou])")
_VOWELS = re.compile(r"[aeiou]+")


@lru_cache(maxsize=8192)
def _words(text: str) -> tuple[str, ...]:
    out = unicodedata.normalize("NFKD", text)
    out = "".join(ch for ch in out if not unicodedata.combining(ch)).casefold()
    return tuple(w for w in _SPLIT.split(out) if w)


def words(text: Any) -> list[str]:
    """*text* as its words: NFKD, no combining mark, casefold, letters and
    digits only. "Neuropeak - Snowflake" gives ``["neuropeak", "snowflake"]``."""
    return list(_words(str(text or "")))


@lru_cache(maxsize=8192)
def fold(word: str) -> str:
    """One word with its spelling variants folded (:data:`VARIANTS`), each
    double letter made single, and a final ``h`` dropped.

    "Sharath" and "Sharat" both give ``sarat``. "Laxmi" and "Lakshmi" both
    give ``laksmi``. The input is one word of :func:`words`, so it is lower
    case already.
    """
    out = word
    for left, right in VARIANTS:
        out = out.replace(left, right)
    out = _DOUBLE.sub(r"\1", out)
    if len(out) > 2 and out.endswith("h"):
        out = out[:-1]
    return out


def sound(folded: str) -> str:
    """The sound key of a folded word: each glide after a consonant drops,
    and each run of vowels is one ``a``. "svagata" and "sogata" both give
    ``sagata``. Coarser than :func:`fold`, so it counts for long words only."""
    return _VOWELS.sub("a", _GLIDE.sub("", folded))


@lru_cache(maxsize=8192)
def trigrams(word: str) -> frozenset[str]:
    """The trigrams of one word, as ``pg_trgm`` makes them: two spaces before
    the word and one after."""
    padded = f"  {word} "
    return frozenset(padded[i:i + 3] for i in range(len(padded) - 2))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


@lru_cache(maxsize=65536)
def word_score(asked: str, other: str) -> float:
    """How near two words of :func:`words` are, from 0 to 1.

    The trigram overlap of their folds. When both words have
    :data:`SOUND_MIN_LETTERS` letters or more, the overlap of their sound keys
    counts too, at :data:`SOUND_WEIGHT`. A word of one letter is an initial:
    it scores 1 against a word that starts with it.
    """
    if not asked or not other:
        return 0.0
    if len(asked) == 1:
        return 1.0 if other.startswith(asked) else 0.0
    left, right = fold(asked), fold(other)
    if left == right:
        return 1.0
    score = _jaccard(trigrams(left), trigrams(right))
    if min(len(asked), len(other)) >= SOUND_MIN_LETTERS:
        keyed = _jaccard(trigrams(sound(left)), trigrams(sound(right)))
        score = max(score, SOUND_WEIGHT * keyed)
    return score


def name_score(asked: Any, name: Any) -> float:
    """How near a name is to what the member asked for, from 0 to 1.

    Each asked word takes its best :func:`word_score` against the words of
    *name*, and the score is the mean of those. "Swagata" against
    «Sougata Maji» scores 0.9. "Sharath Kumar" against «Sharat
    Satyanarayana» scores 0.5, because nothing there is near "Kumar".
    """
    asked_words = _words(str(asked or ""))
    name_words = _words(str(name or ""))
    if not asked_words or not name_words:
        return 0.0
    best = [max(word_score(a, n) for n in name_words) for a in asked_words]
    return sum(best) / len(best)


def is_near(asked: Any, name: Any) -> bool:
    """True when *name* is a near spelling of *asked* (:data:`NEAR_SCORE`)."""
    return name_score(asked, name) >= NEAR_SCORE


def near_eligible(word: str) -> bool:
    """True when one word of a query may match nearly: it has
    :data:`NEAR_MIN_LETTERS` letters or more, and :data:`NEAR_MAX_CHARS`
    characters at most. "3D", "2024" and a pasted hash never do."""
    return (len(word) <= NEAR_MAX_CHARS
            and sum(ch.isalpha() for ch in word) >= NEAR_MIN_LETTERS)


def near_query(query_words: list[str]) -> bool:
    """True when a query of these words gets a near pass: at most
    :data:`NEAR_MAX_WORDS` words, and one of them :func:`near_eligible`."""
    return 0 < len(query_words) <= NEAR_MAX_WORDS and any(
        near_eligible(w) for w in query_words
    )


class NearName(NamedTuple):
    """One name that a near lookup found (HRD-18b, ``find_engine.md`` §4.3)."""

    #: The whole name, as the record holds it.
    name: str
    #: :func:`word_score` of the asked word against :attr:`spelling`.
    score: float
    #: The word of the name that is near, as the record spells it: one token
    #: of letters and digits, at most :data:`NEAR_MAX_CHARS` characters.
    spelling: str


def same_word(left: str, right: str) -> bool:
    """True when two words of :func:`words` are one word, or one word and its
    plural. "printer" and "printers" are one word, so neither is a near
    spelling of the other."""
    if left == right:
        return True
    return left.rstrip("s") == right.rstrip("s") and abs(len(left) - len(right)) == 1


def _raw(asked: str, other: str) -> float:
    """The trigram overlap of two words with no fold. It only breaks a tie of
    :func:`word_score`: "Sharat" holds more of "sharath" than "Sarath" does."""
    return _jaccard(trigrams(asked), trigrams(other))


def _as_spelled(name: str, word: str) -> str:
    """The token of *name* that gives *word*, as the record spells it."""
    for token in _TOKEN.findall(name):
        if _words(token) == (word,):
            return token[:NEAR_MAX_CHARS]
    return word[:NEAR_MAX_CHARS]


def near_names(asked: Any, names: Iterable[Any], k: int) -> list[NearName]:
    """The names near to one asked word, one name for each near spelling.

    Each distinct word of *names* is scored once with :func:`word_score`. A
    word at :data:`NEAR_SCORE` or more is a spelling, and the first name that
    holds it stands for it, so the caller gives the newest names first. The
    asked word itself counts too, so a caller can tell exact from near. At
    most *k*, nearest first, then by raw nearness. Pure, so a caller may run
    it in a thread.
    """
    ask = _words(str(asked or ""))
    if len(ask) != 1 or not near_eligible(ask[0]) or k <= 0:
        return []
    word = ask[0]
    scores: dict[str, float] = {}
    found: dict[str, tuple[float, str]] = {}
    for raw in names:
        name = str(raw or "").strip()
        for part in _words(name):
            if part not in scores:
                scores[part] = word_score(word, part) if len(part) > 1 else 0.0
            if scores[part] >= NEAR_SCORE and part not in found:
                found[part] = (scores[part], name)
    ranked = sorted(found.items(), key=lambda item: (-item[1][0], -_raw(word, item[0]), item[0]))
    return [NearName(name, score, _as_spelled(name, part))
            for part, (score, name) in ranked[:k]]


def near_lookup(asked: Iterable[str], names: Iterable[Any], k: int) -> dict[str, list[NearName]]:
    """:func:`near_names` for each asked word over one list of names. Pure."""
    pool = [str(n or "") for n in names]
    return {word: near_names(word, pool, k) for word in asked}


def best_spelling(asked: Any, found: Iterable[NearName]) -> NearName | None:
    """The nearest name whose spelling is not the asked word itself (HRD-18b).

    A spelling that :func:`same_word` calls the asked word is no near
    spelling. A tie of score goes to the raw nearness, then to the spelling.
    ``None`` when no name is near.
    """
    ask = _words(str(asked or ""))
    if len(ask) != 1:
        return None
    word = ask[0]
    best: tuple[float, float, str] | None = None
    pick: NearName | None = None
    for item in found:
        spelled = _words(item.spelling)
        if len(spelled) != 1 or same_word(word, spelled[0]) or item.score < NEAR_SCORE:
            continue
        key = (item.score, _raw(word, spelled[0]), spelled[0])
        if best is None or (key[0], key[1]) > (best[0], best[1]) or (
                (key[0], key[1]) == (best[0], best[1]) and key[2] < best[2]):
            best, pick = key, item
    return pick
