"""The org's own words for its figures and its values (WS-53 §18.16 parts 4 and 6).

Spec: ``project-docs/specs/crm_platform.md`` §18.16.

PURE functions. This module reads no database and calls no model. The
route ``routes/crm/terms.py`` and the aggregate read the inputs and call it.

**Generic by construction (owner, 2026-10-19).** No org name, field name,
term or value lives here. A term matches the labels and the keys of the
org's own fields. A value alias comes from the org's own distinct values.
``tests/unit/test_crm_answer_consistency.py`` greps this file for the words
of the incident.

**A term (part 4).** A member says a figure in the org's words: a fee, a
billing or a charge with a short code before it. :func:`term_words` keeps the
words that tell one field from another and drops the general words of money
(:data:`GENERAL_WORDS`). :func:`candidates` gives the money fields of one
module whose label or key holds each such word, the most filled first. A
field that holds a tax part, or that the arithmetic marks as including GST,
is no candidate while another one is left.

**Who decides (the confidence rule).** In this order, the first wins:

1. an admin's answer of the term question;
2. a business rule that names the term and exactly one candidate;
3. the member's own pick (a proposal of the question);
4. the pick of another member, as the proposed org answer;
5. a single candidate.

With none of these, the term is open. The chat tool then reads a total for
each of the first candidates, and asks the member FIRST only when the
totals differ by more than :data:`CLOSE_SHARE` (``crm_tools``).

**A value alias (part 6).** A filter value with case or short-form variants
counts each of them. :func:`value_matches` takes the distinct values of the
field: the same value in another case, a short form that is the start of
exactly one longer value (3 letters or more), and the initials of a value of
two or more words.

**A link (SEM-2a).** :func:`link_field` maps a word of the model to the
stored key of one field. A wrong link is worse than a refusal, so only a
certain name links: the exact key, an admin's answer of the term, the key in
any case, or the folded label, in that order. Two or more fields at one of
these steps give each key and pick none. A near fit by the words of
:func:`_hit` is a suggestion only, which the caller can name in a refusal.
The caller passes only the fields that the member may use, and the caller
applies :func:`close` to the totals of two money fields. The word never goes
into the result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from gateway.crm_sync.catalogue import SUM_KEY_JOIN

#: The kind and the id prefix of a term question in the CRM-Q32 store.
KIND_TERM = "term"
TERM_PREFIX = "term:"
#: The cursor-row buckets of ``zoho:questions``: the terms that the chat
#: asked (question id → ``{"module", "word"}``), and an admin's answers
#: (question id → ``{"field"}``).
TERMS_BUCKET = "terms"
TERM_ANSWERS_BUCKET = "term_answers"
#: The bucket of the seeded value aliases: ``<Module>.<key>.<value>`` → the
#: list of the stored values that one filter value counts.
ALIASES_BUCKET = "aliases"

#: The share within which two totals read as one answer (owner, 2026-10-19).
#: Over it, two readings give materially different numbers, and the chat
#: asks first.
CLOSE_SHARE = 0.05
#: The most candidates of one term: the choices of its question.
MAX_CANDIDATES = 3
#: The longest slug of a term in a question id.
SLUG_MAX = 40

#: The field types that can hold a figure of money.
MONEY_TYPES: tuple[str, ...] = ("currency", "number")

#: The general words of money and of a period. They never tell one field
#: from another, so a term keeps only its other words.
GENERAL_WORDS = frozenset({
    "a", "an", "the", "of", "for", "and", "in", "on", "to", "per", "by", "with", "my",
    "our", "all", "total", "totals", "amount", "amounts", "value", "values", "sum",
    "charge", "charges", "charged", "fee", "fees", "billing", "billed", "bill", "bills",
    "invoice", "invoices", "invoiced", "revenue", "income", "earning", "earnings",
    "collection", "collections", "collected", "payment", "payments", "paid", "basic",
    "base", "net", "gross", "grand", "sub", "month", "monthly", "year", "yearly",
    "annual", "rs", "inr", "price", "cost", "costs", "money", "figure", "number",
})

#: The words of a tax part of an amount. A field that holds one is no
#: candidate while another candidate is left.
TAX_WORDS = frozenset({"gst", "cgst", "sgst", "igst", "utgst", "tds", "vat", "tax", "cess"})
#: The words of a field that holds a tax PART alone. A total "with tax"
#: stays beside the amount fields, so the arithmetic can compare the two.
TAX_PART_WORDS = TAX_WORDS - {"tax"}

_WORD = re.compile(r"[a-z0-9]+")
#: A key splits at "_" and at each change from lower case to upper case.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def words(text: Any) -> list[str]:
    """The lower-case words of *text*, in order."""
    return _WORD.findall(_CAMEL.sub(" ", str(text or "")).lower())


def term_words(term: str) -> list[str]:
    """The words of *term* that tell one field from another. A term of
    general words only keeps them all, so it still matches."""
    found = list(dict.fromkeys(words(term)))
    kept = [w for w in found if w not in GENERAL_WORDS]
    return kept or found


def slug(term: str) -> str:
    """The slug of a term in a question id: its words, with "-"."""
    return "-".join(words(term))[:SLUG_MAX].strip("-")


def term_id(module: str, term: str) -> str:
    return f"{TERM_PREFIX}{module}:{slug(term)}"


def _hit(word: str, field_words: Sequence[str]) -> bool:
    """True when a field word is *word*, or starts with it and is at most 2
    letters longer (a short code and its plural or its letter of kind)."""
    return any(w == word or (len(word) >= 2 and w.startswith(word) and len(w) <= len(word) + 2)
               for w in field_words)


@dataclass(frozen=True)
class Candidate:
    key: str
    label: str
    fill: float | None = None
    evidence: tuple[str, ...] = ()


def _fill(fills: Mapping[str, float] | None, key: str) -> float | None:
    if not fills or key not in fills:
        return None
    try:
        return float(fills[key])
    except (TypeError, ValueError):
        return None


def candidates(fields: Sequence[Any], fills: Mapping[str, float] | None, term: str,
               evidence: Mapping[str, Sequence[str]] | None = None) -> list[Candidate]:
    """The money fields of one module that *term* names, the most filled
    first, at most :data:`MAX_CANDIDATES`. *fields* have ``key``, ``label``
    and ``type``. A field with no value is never one. A tax part, and a
    field that the arithmetic marks as including GST, drop while another
    candidate is left."""
    wanted = term_words(term)
    if not wanted:
        return []
    found: list[Candidate] = []
    for f in fields:
        if str(getattr(f, "type", "")) not in MONEY_TYPES:
            continue
        key, label = str(getattr(f, "key", "")), str(getattr(f, "label", "") or "")
        fw = list(dict.fromkeys([*words(label), *words(key)]))
        if not all(_hit(w, fw) for w in wanted):
            continue
        fill = _fill(fills, key)
        if fill is not None and fill <= 0:
            continue
        found.append(Candidate(key=key, label=label or key, fill=fill,
                               evidence=tuple((evidence or {}).get(key, ()))))
    plain = [c for c in found if not (TAX_WORDS & set(words(c.label)) | TAX_WORDS
                                      & set(words(c.key)))
             and "includes_gst" not in c.evidence]
    pool = plain or found
    pool.sort(key=lambda c: (-(c.fill if c.fill is not None else 0.0), c.key))
    return pool[:MAX_CANDIDATES]


def _norm(text: Any) -> str:
    return " ".join(words(text))


def rule_pick(rules: Sequence[str], term: str, cands: Sequence[Candidate]) -> str | None:
    """The key of the one candidate that a business rule names for *term*,
    or None. The rule must hold each word of the term, and the label or the
    key of exactly one candidate."""
    wanted = term_words(term)
    for rule in rules:
        said = _norm(rule)
        if not all(re.search(rf"\b{re.escape(w)}\b", said) for w in wanted):
            continue
        named = [c.key for c in cands
                 if (_norm(c.label) and re.search(rf"\b{re.escape(_norm(c.label))}\b", said))
                 or re.search(rf"\b{re.escape(_norm(c.key))}\b", said)]
        if len(set(named)) == 1:
            return named[0]
    return None


@dataclass(frozen=True)
class Pick:
    """What decides a term: ``by`` is ``admin``, ``rule``, ``me``,
    ``member``, ``single`` or None (open), and ``field`` the key."""

    by: str | None
    field: str | None
    cands: tuple[Candidate, ...] = ()
    why: str = ""


#: What a note says for each way that a term was decided.
WHY: Mapping[str, str] = {
    "admin": "a CRM admin set it",
    "rule": "a business rule names it",
    "me": "you chose it",
    "member": "a member chose it, and a CRM admin can confirm it",
    "single": "it is the only field that matches",
    "close": "the matching fields give totals within 5% of each other",
}


def decide(cands: Sequence[Candidate], *, answer: str | None = None,
           rules: Sequence[str] = (), term: str = "",
           mine: str | None = None, others: Sequence[str] = ()) -> Pick:
    """Who decides the term, in the order of the module docstring. A pick
    that names no candidate is ignored, so a gone field never decides."""
    keys = {c.key for c in cands}
    cands_t = tuple(cands)
    if answer in keys:
        return Pick("admin", answer, cands_t, WHY["admin"])
    ruled = rule_pick(rules, term, cands)
    if ruled is not None:
        return Pick("rule", ruled, cands_t, WHY["rule"])
    if mine in keys:
        return Pick("me", mine, cands_t, WHY["me"])
    for other in reversed(list(others)):
        if other in keys:
            return Pick("member", other, cands_t, WHY["member"])
    if len(cands) == 1:
        return Pick("single", cands[0].key, cands_t, WHY["single"])
    return Pick(None, None, cands_t, "")


def close(totals: Sequence[float | None]) -> bool:
    """True when each total is within :data:`CLOSE_SHARE` of the largest."""
    found = [abs(float(t)) for t in totals if t is not None]
    if len(found) < 2:
        return True
    top = max(found)
    return top == 0 or (top - min(found)) <= CLOSE_SHARE * top


# ── Value aliases (part 6) ──────────────────────────────────────────────────

#: The shortest short form that counts as the start of a longer value.
SHORT_MIN = 3
#: The longest value that reads as a code when it is asked for itself.
SHORT_MAX = 4


def _initials(text: str) -> str:
    parts = text.split()
    return "".join(p[0] for p in parts) if len(parts) >= 2 else ""


def _expands(short: str, long: str) -> bool:
    """True when *short* is a short form of *long*: its start (3 letters or
    more, and no space), or the initials of a value of two or more words."""
    if short == long or not short or " " in short:
        return False
    if len(short) >= SHORT_MIN and long.startswith(short):
        return True
    return len(short) >= 2 and short == _initials(long)


def value_matches(asked: str, distinct: Sequence[tuple[str, int]]) -> list[str]:
    """The stored values that the filter value *asked* counts, the most
    records first: each value equal to it in another case, and each short
    form of it. A short form counts only when it is the short form of ONE
    longer value, so a code that two values share counts for neither. With
    no stored value like it, the list holds *asked* alone."""
    norm = {v: _norm(v) for v, _n in distinct}
    classes = sorted(set(norm.values()))
    target = _norm(asked)
    longer = [c for c in classes if _expands(target, c)]
    if len(longer) == 1 and (target not in classes or len(target) <= SHORT_MAX):
        # A code asked for reads the one value that it stands for. A whole
        # value that starts a longer one keeps itself.
        target = longer[0]

    def short_of(c: str) -> bool:
        owners = [x for x in classes if _expands(c, x)]
        return owners == [target]

    kept = {c for c in classes if c == target or short_of(c)}
    found = [v for v, _n in sorted(distinct, key=lambda d: (-d[1], d[0])) if norm[v] in kept]
    return found or [asked]


@dataclass
class TermState:
    """The term buckets of ``zoho:questions``."""

    asked: dict[str, dict[str, str]] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)


def term_state(state: Mapping[str, Any] | None) -> TermState:
    """The asked terms and the admin's answers in a cursor state. An entry
    that does not parse is left out."""
    out = TermState()
    raw = (state or {}).get(TERMS_BUCKET)
    for qid, entry in (raw.items() if isinstance(raw, Mapping) else ()):
        if (isinstance(entry, Mapping) and str(qid).startswith(TERM_PREFIX)
                and isinstance(entry.get("module"), str) and isinstance(entry.get("word"), str)):
            out.asked[str(qid)] = {"module": entry["module"], "word": entry["word"]}
    raw = (state or {}).get(TERM_ANSWERS_BUCKET)
    for qid, entry in (raw.items() if isinstance(raw, Mapping) else ()):
        value = entry.get("field") if isinstance(entry, Mapping) else None
        if isinstance(value, str) and value:
            out.answers[str(qid)] = value
    return out


def alias_lines(state: Mapping[str, Any] | None) -> list[tuple[str, str, list[str]]]:
    """The seeded aliases in a cursor state: ``(Module.key, value, values)``."""
    raw = (state or {}).get(ALIASES_BUCKET)
    out = []
    for name, values in (raw.items() if isinstance(raw, Mapping) else ()):
        if isinstance(values, list) and len(values) > 1 and all(isinstance(v, str) for v in values):
            head, _sep, value = str(name).rpartition(".")
            out.append((head, value, list(values)))
    return out


# ── The link of a word to a field (SEM-2a) ──────────────────────────────────

#: The longest word that :func:`link_field` reads. The route
#: ``routes/crm/terms.py`` takes a term of the same length (its own
#: ``TERM_MAX``), and ``tests/unit/test_crm_query_link.py`` checks that the
#: two agree. This module imports no route, so it keeps its own copy.
TERM_MAX = 80
#: The first character of a key word of a module scope (``@amount``). A
#: stored key never starts with it.
KEY_WORD_MARK = "@"
#: The steps of :func:`link_field` that link a field, in the order of the
#: ladder. Each is a certain name. The ``near`` step only suggests.
LINK_STEPS: tuple[str, ...] = ("exact", "term", "case", "label")
#: The shortest word that the ``near`` step reads. A shorter word ("in",
#: "id", "no") is part of too many field names to suggest one.
NEAR_MIN = 3
#: The word that ``%`` reads as in a folded label, so "GST %" and
#: "GST Amount" fold apart, and "gst" folds to neither.
PERCENT_WORD = "percent"


@dataclass(frozen=True)
class Link:
    """What :func:`link_field` found for one word.

    ``keys`` holds stored field keys only, never the word: none, one, or up
    to :data:`MAX_CANDIDATES`. ``step`` names the step that decided:
    ``exact``, ``term``, ``case`` or ``label`` for one field (a link),
    ``ambiguous`` for two or more fields at one of those steps, ``near`` for
    the fields that the words of the word fit (a suggestion), ``none`` for
    no field, and ``passthrough`` for a key word or a sum of keys that the
    caller reads on its old path.

    ``linked`` is True only for a link (:data:`LINK_STEPS`). Only then may
    the caller run the key. The keys of ``ambiguous`` and ``near`` are for
    the caller to name, never to run. ``more`` counts the further fields of
    the deciding step past the keys, so a caller reads ``more > 0`` as
    "possibly more". ``money`` is a hint: True when each key is a field of
    :data:`MONEY_TYPES`. SEM-2b decides the money rule of :func:`close` from
    the measure types of the aggregate, without ``percent``."""

    keys: tuple[str, ...] = ()
    step: str = "none"
    money: bool = False
    linked: bool = False
    more: int = 0


def _fold(text: Any) -> str:
    """A label in one form: lower case, with each character that is not a
    letter or a digit dropped. ``%`` reads as :data:`PERCENT_WORD` first.
    Apart from ``%``, it is the rule of ``fold_module`` in
    ``routes/crm/modules.py`` (CRM-Q34-12). This module imports no route, so
    it keeps its own copy."""
    text = str(text or "").replace("%", f" {PERCENT_WORD} ")
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _own_term(word: str) -> bool:
    """True when the term question of *word* belongs to *word* alone. Its
    slug must be shorter than :data:`SLUG_MAX`, because the slug is cut there,
    and two long words can share one cut slug. :func:`words` must keep each
    letter and digit of it, because it drops a letter that is not ASCII, and
    then "<other script> fee" reads the answer of "fee"."""
    return (len("-".join(words(word))) < SLUG_MAX
            and not any(ch.isalnum() and not ch.isascii() for ch in word))


def _stored(fields: Iterable[Any]) -> list[tuple[str, str, str]]:
    """``(key, label, type)`` of each stored field, once for each key, in the
    order of *fields*. A key word (``@amount``) and a sum of keys are aliases
    of stored fields, so they are left out. A field with no label reads its
    key as its label."""
    out: dict[str, tuple[str, str, str]] = {}
    for f in fields:
        key = str(getattr(f, "key", "") or "")
        if not key or key in out or key.startswith(KEY_WORD_MARK) or SUM_KEY_JOIN in key:
            continue
        label = str(getattr(f, "label", "") or "") or key
        out[key] = (key, label, str(getattr(f, "type", "") or ""))
    return list(out.values())


def _link(found: Sequence[tuple[str, str, str]], step: str) -> Link:
    """The result of the fields that one step found. At a step of
    :data:`LINK_STEPS`, one field is the link, and two or more give
    ``ambiguous``. The ``near`` step never links. At most
    :data:`MAX_CANDIDATES` keys show, and ``more`` counts the rest."""
    if len(found) > 1 and step in LINK_STEPS:
        step = "ambiguous"
    shown = found[:MAX_CANDIDATES]
    return Link(keys=tuple(k for k, _label, _type in shown), step=step,
                money=all(t in MONEY_TYPES for _key, _label, t in shown),
                linked=step in LINK_STEPS, more=len(found) - len(shown))


def link_field(fields: Iterable[Any], word: str, *, module: str = "",
               answers: Mapping[str, str] | None = None) -> Link:
    """The stored field that *word* names, or the fields it can mean.

    *fields* have ``key``, ``label`` and ``type``, as :func:`candidates`
    reads them. The caller passes only the fields that the member may use, so
    a hidden field is a target only when the caller passes it. This function
    reads no ``visible`` flag.

    A key word (it starts with ``@``) and a sum of keys (it holds ``+``) give
    ``passthrough``, and the caller keeps its old path. A word longer than
    :data:`TERM_MAX`, or with no letter and no digit, gives ``none``. Then the
    first step that finds a field decides:

    1. ``exact``: the stored key;
    2. ``term``: the admin's answer of the term question of *word* in
       *module*, first as in :func:`decide`. *answers* is
       ``term_state(...).answers``, which the caller read. With no *module*
       or no *answers*, or a word that :func:`_own_term` refuses, the step
       does not run;
    3. ``case``: the key in any case;
    4. ``label``: the label, folded by :func:`_fold`;
    5. ``near``: each word of *word* of :data:`NEAR_MIN` letters or more hits
       a word of the label or of the key (:func:`_hit`). It suggests and
       never links.

    One field at steps 1 to 4 is the link (``linked``). Two or more give
    ``ambiguous`` and pick none. The result never holds *word*, and the
    function reads no database, no setting and no clock, and logs nothing."""
    asked = word if isinstance(word, str) else ""
    trimmed = asked.strip()
    if trimmed.startswith(KEY_WORD_MARK) or SUM_KEY_JOIN in trimmed:
        return Link(step="passthrough")
    if len(asked) > TERM_MAX or not any(ch.isalnum() for ch in asked):
        return Link()
    pool = _stored(fields)
    found = [f for f in pool if f[0] == trimmed]
    if found:
        return _link(found, "exact")
    if module and answers and words(trimmed) and _own_term(trimmed):
        picked = answers.get(term_id(module, trimmed))
        found = [f for f in pool if f[0] == picked]
        if found:
            return _link(found, "term")
    case = trimmed.casefold()
    found = [f for f in pool if f[0].casefold() == case]
    if found:
        return _link(found, "case")
    folded = _fold(trimmed)
    found = [f for f in pool if folded and _fold(f[1]) == folded]
    if found:
        return _link(found, "label")
    wanted = [w for w in dict.fromkeys(words(trimmed)) if len(w) >= NEAR_MIN]
    if wanted:
        found = [f for f in pool
                 if all(_hit(w, list(dict.fromkeys([*words(f[1]), *words(f[0])])))
                        for w in wanted)]
        if found:
            return _link(found, "near")
    return Link()


__all__ = [
    "ALIASES_BUCKET",
    "CLOSE_SHARE",
    "GENERAL_WORDS",
    "KEY_WORD_MARK",
    "KIND_TERM",
    "LINK_STEPS",
    "MAX_CANDIDATES",
    "MONEY_TYPES",
    "NEAR_MIN",
    "PERCENT_WORD",
    "TAX_PART_WORDS",
    "TAX_WORDS",
    "TERMS_BUCKET",
    "TERM_ANSWERS_BUCKET",
    "TERM_MAX",
    "TERM_PREFIX",
    "WHY",
    "Candidate",
    "Link",
    "Pick",
    "TermState",
    "alias_lines",
    "candidates",
    "close",
    "decide",
    "link_field",
    "rule_pick",
    "slug",
    "term_id",
    "term_state",
    "term_words",
    "value_matches",
    "words",
]
