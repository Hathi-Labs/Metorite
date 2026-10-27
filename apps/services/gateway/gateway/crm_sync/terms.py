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
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

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


__all__ = [
    "ALIASES_BUCKET",
    "CLOSE_SHARE",
    "GENERAL_WORDS",
    "KIND_TERM",
    "MAX_CANDIDATES",
    "MONEY_TYPES",
    "TAX_PART_WORDS",
    "TAX_WORDS",
    "TERMS_BUCKET",
    "TERM_ANSWERS_BUCKET",
    "TERM_PREFIX",
    "WHY",
    "Candidate",
    "Pick",
    "TermState",
    "alias_lines",
    "candidates",
    "close",
    "decide",
    "rule_pick",
    "slug",
    "term_id",
    "term_state",
    "term_words",
    "value_matches",
    "words",
]
