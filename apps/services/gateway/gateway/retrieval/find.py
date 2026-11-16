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
* :data:`EDGES` is the closed list of the edge of a related hit of rung 4
  (FIND-3, §3.5). :func:`sort_links`, :func:`sort_mail`, :func:`lanes` and
  :func:`pick_related` sort the ids that the sources' ``related`` gave, and
  pick the records that their ``open`` gave.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterable
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


#: The fewest letters that a stem keeps after :func:`_stem` cuts an ending.
STEM_MIN = 4
#: The endings that :func:`_stem` cuts, the longest first. "es" goes only
#: after s, x, z, ch or sh, and "s" never after s or u.
_ENDINGS = ("ing", "ed", "es", "s")
_ES_AFTER = ("s", "x", "z", "ch", "sh")


def _stem(word: str) -> str:
    """*word* with one common ending cut, then a final "e", on both sides of
    a test (review of 2026-11-16, P2-c). The full text matched by stem, so
    "delays" holds "delayed", "printers" holds "printer", "shipping" holds
    "shipped" and "invoices" holds "invoice". Each cut keeps
    :data:`STEM_MIN` letters or more. Pure."""
    out = word
    for ending in _ENDINGS:
        stem = out[: -len(ending)]
        if not out.endswith(ending) or len(stem) < STEM_MIN:
            continue
        if (ending == "es" and not stem.endswith(_ES_AFTER)) or (
                ending == "s" and stem.endswith(("s", "u"))):
            continue
        out = stem
        break
    if out.endswith("e") and len(out) - 1 >= STEM_MIN:
        out = out[:-1]
    return out


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


def _alone(asked: list[str], broadened: list[dict[str, Any]] | None, *,
           query: str, first_zero: bool) -> tuple[int, bool] | None:
    """What the word *asked* found alone: its hits, and whether an app was
    skipped. From its entry of ``broadened``, or from the first pass when the
    member typed the word itself. ``None`` when it was never searched alone.

    The first pass searched the token as typed, and an app matches each typed
    token. So "Sharath's" or "Sharath." is no search of "sharath" alone, and
    the first pass counts only when the trimmed query IS the folded word
    (round 3, P2-2).
    """
    for entry in broadened or []:
        if isinstance(entry, dict) and name_match.words(entry.get("word")) == asked:
            hits = entry.get("hits")
            return (hits if isinstance(hits, int) else 0), bool(entry.get("skipped"))
    if len(asked) == 1 and (query or "").strip().casefold() == asked[0]:
        return (0 if first_zero else 1), False
    return None


def near_report(entries: list[dict[str, Any]], found: dict[str, list[Any]],
                broadened: list[dict[str, Any]] | None, *, query: str,
                first_zero: bool, skipped: list[str],
                limited: bool = False) -> list[dict[str, Any]]:
    """Each entry of the route's ``near``, with ``held`` (HRD-18b).

    ``held`` is true when the word has a hit of its own: the word alone found
    records, or a hit of the answer that matched the member's own words holds
    it in its title, hint or snippet. It is false only with proof: the word
    was searched alone in every app that was asked, no app was skipped, in
    the first pass (*skipped*) or alone, and it found nothing. A search that
    *limited* by a date (``after``) or to verified pages proves nothing,
    because an older or an unverified record may hold the word (round 3,
    P3-3). In each other case it is ``None``: not known. Only a false word
    may open the answer with "No record holds" (§3.6). Pure.
    """
    own = [f"{h.title} {h.hint} {h.snippet}" for hits in found.values() for h in hits
           if h.matched_by in OWN_RUNGS]
    out: list[dict[str, Any]] = []
    for entry in entries:
        asked = name_match.words(entry.get("word"))
        alone = _alone(asked, broadened, query=query, first_zero=first_zero)
        held: bool | None = None
        if (alone is not None and alone[0] > 0) or (
                len(asked) == 1 and any(coverage(asked, t) for t in own)):
            held = True
        elif alone is not None and not alone[1] and not skipped and not limited:
            held = False
        out.append({**entry, "held": held})
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


# ── Rung 4: related records (FIND-3, §3.1, §3.5, §4.3) ──────────────────────

#: The edge of a related hit (§3.5): a code constant from this closed list. It
#: holds no text of either record.
EDGES: tuple[str, ...] = (
    "contact_of", "lead_of", "deal_of", "company_of", "mail_from", "mail_to", "mail_domain",
)
#: The edges that end in a mail. Each starts at a key of :data:`KEYS`.
MAIL_EDGES: dict[str, str] = {"mail_from": "address", "mail_to": "address",
                              "mail_domain": "domain"}
#: The edges from a record to a record, each with the kind of record it ends at.
RECORD_EDGES: dict[str, str] = {"contact_of": "contact", "lead_of": "lead",
                                "deal_of": "deal", "company_of": "company"}
#: The keys that carry the step from a record to its mail: an address and a
#: web domain. A key is a transit value: no answer key, no ``from`` and no log
#: line holds one.
KEYS: tuple[str, ...] = ("address", "domain")
#: The kinds of hit whose links rung 4 follows (§3.1).
RELATED_KINDS = frozenset({"deal", "lead", "contact", "person", "company"})
#: The order in which the edges of one hit take its places, one of each edge
#: in a round. The mail comes first, then the lead and a contact. So a deal
#: with a lead, contacts and a company still shows its mail.
LANES: tuple[str, ...] = (
    "mail_from", "lead_of", "contact_of", "mail_to", "company_of", "mail_domain", "deal_of",
)

#: One candidate of a hit: the linked ref, its edge, and the record edge that
#: led to the key of a mail (``via``), or ``None``.
Candidate = tuple[str, str, str | None]


def split_ref(value: Any) -> tuple[str, str] | None:
    """``(kind, id)`` of a ref ``<kind>:<id>``, or ``None``. Pure."""
    if not isinstance(value, str):
        return None
    kind, sep, ident = value.partition(":")
    if not sep or not kind or not ident:
        return None
    return kind, ident


def kind_of(ref: Any) -> str:
    """The kind of a ref, or ``""``. Pure."""
    parts = split_ref(ref)
    return parts[0] if parts is not None else ""


def ref_of(hit: Any) -> str:
    """The ref of a hit: ``<kind>:<id>``, as ``full_id`` prints it."""
    return f"{hit.kind}:{hit.id}"


def related_chosen(hits: Iterable[Any], most: int) -> list[str]:
    """The refs of the first *most* hits that are a deal, lead, contact,
    person or company, in the order of the answer: the words, then the rung,
    then the score (§3.4). Pure."""
    out: list[str] = []
    for hit in hits:
        ref = ref_of(hit)
        if hit.kind in RELATED_KINDS and ref not in out:
            out.append(ref)
        if len(out) >= most:
            break
    return out


def _triple(value: Any) -> tuple[str, str, str] | None:
    """*value* as a triple of a ref, an edge or key, and a ref, else ``None``."""
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    left, edge, right = value
    if split_ref(left) is None or split_ref(right) is None or not isinstance(edge, str):
        return None
    return left, edge, right


def sort_links(
    chosen: list[str], triples: Iterable[Any],
) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[str]]]:
    """The record links of each chosen ref, and the keys of each record.

    A record link is ``(edge, ref)`` from a chosen ref, with an edge of
    :data:`RECORD_EDGES`, to a record of the kind of that edge. A key is an
    ``address:`` or ``domain:`` ref, under its own edge name, of a chosen ref
    or of a record that one links. Every other triple is dropped: an unknown
    edge, a record or key of the wrong kind, or a link from a ref that was
    not asked. Pure.
    """
    valid = [t for t in map(_triple, triples) if t is not None]
    asked = set(chosen)
    out: dict[str, list[tuple[str, str]]] = {ref: [] for ref in chosen}
    for left, edge, right in valid:
        if left in asked and RECORD_EDGES.get(edge) == kind_of(right) \
                and right not in asked and (edge, right) not in out[left]:
            out[left].append((edge, right))
    reached = asked | {ref for links in out.values() for _e, ref in links}
    keys: dict[str, list[str]] = {}
    for left, edge, right in valid:
        if edge in KEYS and kind_of(right) == edge and left in reached:
            mine = keys.setdefault(left, [])
            if right not in mine:
                mine.append(right)
    return out, keys


def sort_mail(keys: Iterable[str], triples: Iterable[Any]) -> dict[str, list[tuple[str, str]]]:
    """The mail of each key, as ``(edge, ref)``, in the order given.

    A mail edge must start at a key of its own kind: ``mail_from`` and
    ``mail_to`` at an address, ``mail_domain`` at a domain. Each other triple
    is dropped. Pure.
    """
    asked = set(keys)
    out: dict[str, list[tuple[str, str]]] = {}
    for left, edge, right in (t for t in map(_triple, triples) if t is not None):
        if left not in asked or MAIL_EDGES.get(edge) != kind_of(left) or kind_of(right) in KEYS:
            continue
        mine = out.setdefault(left, [])
        if (edge, right) not in mine:
            mine.append((edge, right))
    return out


def only_opened(
    chosen: list[str], links: dict[str, list[tuple[str, str]]], keys: dict[str, list[str]],
    opened: set[str],
) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[str]]]:
    """The links to the records of *opened* only, and the keys of a chosen
    ref or of an opened record only. So the mail of a record that the member
    cannot open is never asked, and no "mail from a contact of" names a
    contact that the member cannot see. Pure."""
    kept = {ref: [(e, r) for e, r in links.get(ref, []) if r in opened] for ref in chosen}
    owners = set(chosen) | opened
    return kept, {owner: mine for owner, mine in keys.items() if owner in owners}


def key_refs(chosen: list[str], links: dict[str, list[tuple[str, str]]],
             keys: dict[str, list[str]]) -> list[str]:
    """Every key that a chosen ref reaches, its own first. Pure."""
    out: list[str] = []
    for ref in chosen:
        for owner in [ref, *(linked for _e, linked in links.get(ref, []))]:
            out += [k for k in keys.get(owner, []) if k not in out]
    return out


def lanes(ref: str, links: dict[str, list[tuple[str, str]]], keys: dict[str, list[str]],
          mail: dict[str, list[tuple[str, str]]]) -> dict[str, list[Candidate]]:
    """The candidates of the chosen *ref* in each lane of :data:`LANES`.

    A record lane holds the records that *ref* links. A mail lane holds the
    mail of the keys of *ref* itself first, then the mail of the keys of each
    record that it links, in the order of :data:`LANES` (the lead before a
    contact). Pure.
    """
    out: dict[str, list[Candidate]] = {lane: [] for lane in LANES}
    linked = links.get(ref, [])
    for edge, record in linked:
        out[edge].append((record, edge, None))
    owners: list[tuple[str, str | None]] = [(ref, None)]
    owners += sorted(((record, edge) for edge, record in linked),
                     key=lambda pair: LANES.index(pair[1]))
    for owner, via in owners:
        for key in keys.get(owner, []):
            for edge, record in mail.get(key, []):
                out[edge].append((record, edge, via))
    return out


def pick_related(
    chosen: list[str], lanes_of: dict[str, dict[str, list[Candidate]]],
    opened: dict[str, Any], shown: set[str], per_hit: int,
    kept: Callable[[Any], bool],
) -> dict[str, list[tuple[Any, str, str | None]]]:
    """The records of each chosen ref, at most *per_hit*, after ``open``.

    Each round takes the next candidate of each lane, in the order of
    :data:`LANES`. A candidate that the member cannot open (not in *opened*),
    a record of the answer's hits (*shown*), a record that an earlier ref
    took, and a record that *kept* refuses (``after``) take no place. So a
    record that the member cannot open leaves the answer as if it did not
    exist. Pure.
    """
    taken: set[str] = set()
    out: dict[str, list[tuple[Any, str, str | None]]] = {}
    for ref in chosen:
        lanes_now = lanes_of.get(ref, {})
        queues = [list(lanes_now.get(lane, [])) for lane in LANES]
        mine: list[tuple[Any, str, str | None]] = []
        while len(mine) < per_hit and any(queues):
            for queue in queues:
                if len(mine) >= per_hit:
                    break
                while queue:
                    record, edge, via = queue.pop(0)
                    hit = opened.get(record)
                    if hit is None or record in shown or record in taken or not kept(hit):
                        continue
                    taken.add(record)
                    mine.append((hit, edge, via))
                    break
        out[ref] = mine
    return out
