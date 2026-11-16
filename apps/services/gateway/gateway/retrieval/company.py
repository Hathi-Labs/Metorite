"""GET /search/company — one question to every app (WS-61 KR-1, ``knowledge_base.md`` §20.4).

One route, for agents and for later consumers. It is not a page a member
opens. It asks each source of :data:`gateway.retrieval.sources.REGISTRY` and
answers ``{query, hits, skipped}``. No chat model runs.

* **Meaning, when the org has it** (WS-61 KB-3c). With ``KNOWLEDGE_EMBED``
  on for the member's org, the route asks each source with ``meaning`` for
  ``hybrid``. Today that is Knowledge alone. Its route embeds the words
  once, within 0.8 s, and answers the lexical half when the door fails. So
  one search costs at most one embedding call and no chat model.

* **The member comes from the session.** The route takes ``q``, ``apps``,
  ``k`` and ``after`` (a date, CX-1) and nothing else, so no tenant and no
  member can come from input (R5, R11). Each source passes this request's
  member to its app.
* **Each source checks its own gate.** The route adds no visibility rule of
  its own. A member without an app gets nothing from it.
* **The sources run at once, four at most** (HRD-4, D-FIND-6). The pool
  budget is small (``routes/shell/search.py`` records why), so
  :data:`FIND_SESSIONS` caps the sources of one gateway process. A fifth source
  waits for a slot. Each source has :data:`SOURCE_TIMEOUT_S` from the time it
  starts, and the whole answer has :data:`TOTAL_BUDGET_S`. A source that
  fails, refuses or runs out of time is left out and named in ``skipped``, in
  the order of the registry. It never fails the call.
* **Fusion by rank.** The scores of two apps do not compare, so the answer
  takes every app's first hit, then every app's second. An app named in
  ``apps`` goes first, in the order named.

**Recency, behind ``CONTEXT_RECENCY``** (WS-61 CX-1, ``company_context.md``
§3, §4.1, §4.2 and §4.5). Each request reads the flag from the cached
settings, so a change of the env file needs a restart. With it on:

* each app's hits are sorted by :func:`score`, which lowers an old hit by
  the half-life of its app, and never below :data:`FLOOR`. Then the apps
  interleave as above;
* Knowledge ranks a page past its review date lower (``reviewed``), in
  place of an age decay;
* ``after`` drops each hit stamped before it. Email and WhatsApp filter in
  their own SQL. Each other app is asked for :func:`pool` hits, and the
  route filters them BEFORE it cuts the list to ``k``, so old top matches
  cannot hide a newer one;
* each hit carries ``stamp``, and the answer names the apps it
  ``searched``: each app that answered, with its switch open.

**Fusion and meaning, behind the same flag** (WS-KG KG-3b,
``knowledge_graph.md`` §9). With it on:

* the answer is ONE list, sorted by :func:`score` across every app, in place
  of :func:`_interleave`. That score is reciprocal rank fusion with k = 60,
  lowered by age. A tie goes to the app named first, then to the lower rank;
* Email also asks its vector half (``vectors``) and fuses it with its
  lexical half by the same rank sum (``kb_index.query.rrf_scores``). An org
  with no email vector gets the lexical answer, and no embed call is made;
* the route opens a turn (:mod:`gateway.retrieval.turn_vector`), so the
  query is embedded once, for every source that asks.

With it off, the route does not read ``after`` at all, not even its
length, and the answer is the one of KR-1, byte for byte.

**A zero is not proof, behind the same flag** (WS-61 KR-ZERO, task 2974).
Each app matches every word of the query, so "Aroh Attero" finds nothing
when no record holds both words. With the flag on:

* the route starts the query embed at its start, as one task
  (:func:`turn_vector.start`), when a source with meaning is asked. Its
  wait overlaps the keyword sources. Each source that needs the vector
  awaits that task inside its own time, and makes no call of its own;
* the answer says in ``meaning`` what became of that embed: ``ran``,
  ``timed_out``, ``failed`` or ``off``. The log line is
  ``search.company.embed ms=… outcome=…``;
* when every app that answered found nothing, and the query holds 2 to 5
  bare words, the route asks again with each word alone (:func:`alone_words`),
  inside :data:`BROADEN_BUDGET_S`. Only the apps that answered are asked,
  and with no vector half. ``broadened`` names each word and its hits, and
  ``hits`` holds the merged hits with no repeated id. A query with an
  operator (a quote, ``or`` or a leading ``-``) is not broadened. A stop
  word and a one-letter word are not searched alone.

**A near spelling, behind ``FIND_ORGS``** (HRD-18b, task 3226,
``find_engine.md`` §3.1 rung 3, §3.2 and §3.3). It acts only with
``CONTEXT_RECENCY`` on. When every app that answered found nothing, and the
query holds no operator:

* the route picks at most 3 words of 4 letters or more
  (:func:`find.near_words`) and asks each source that can ``near`` for the
  names near them, all at once, inside :data:`NEAR_LOOKUP_S`. Each name comes
  from a row that the member can open, and ``acb_common.name_match`` scores
  it in the app's own module. A late or failed lookup gives no name;
* each source takes its own best spelling of each word, and a source with
  no names takes the best spelling of all (``name_match.best_spelling``).
  The apps that answered are asked again, before the words alone of
  KR-ZERO: the query with that one word replaced, then the spelling alone.
  Email asks for the mail from that sender (``sender=``), because a full
  text word never matches inside an address;
* each hit found so says ``matched_by`` ``near``, with ``word`` and ``as``.
  A hit that two rungs found keeps the lower rung. ``near`` in the answer
  names each word, its spelling and its hits. The log line is
  ``search.company.near``, with no word.

**A gap, behind ``KNOWLEDGE_GAPS``** (WS-61 KB-9a, ``knowledge_base.md``
§8.4 door 3). When Knowledge answered with zero hits, the route hands the
words to ``routes/knowledge/gaps.schedule_gap``. That function checks the
flag, the member and the skip rules, and it adds the write to the route's
``BackgroundTasks``. So the write runs after the answer, in its own tenant
session, and it never fails the search. A Knowledge source that failed or
was skipped writes no gap. The answer itself does not change.
"""

from __future__ import annotations

import asyncio
import logging
import math
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger, name_match
from acb_common.name_match import NearName
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from gateway.retrieval import find, turn_vector
from gateway.retrieval.sources import REGISTRY, Hit, email_meaning_on, holds

logger = logging.getLogger(__name__)
#: The house logger, so the KR-ZERO lines reach the journal. They hold no word
#: of the query.
_events = get_logger("gateway.retrieval.company")

router = APIRouter(prefix="/search", tags=["search"])

#: Seconds one source may take.
SOURCE_TIMEOUT_S = 1.5
#: Seconds for the whole answer, all sources together.
TOTAL_BUDGET_S = 4.0
#: The length of the words, after the spaces are folded.
MIN_CHARS = 2
MAX_CHARS = 300
#: Hits for each app.
DEFAULT_K = 5
MAX_K = 10

#: Seconds the one query embed of a turn may take (KR-ZERO). The task starts
#: with the route, so the door gets most of the first pass, while each source
#: still waits at most its own limit. It ends before TOTAL_BUDGET_S.
EMBED_BUDGET_S = 3.0
#: Seconds for the second pass of a zero answer, each word alone (KR-ZERO).
#: The words share it equally, and each source keeps SOURCE_TIMEOUT_S.
BROADEN_BUDGET_S = 2.5
#: Seconds for the whole answer, both passes together (KR-ZERO). The second
#: pass stops here, so the worst case is 1.5 times TOTAL_BUDGET_S, not 2 times.
ANSWER_CAP_S = 6.0
#: The least time worth a second pass. Under it, the zero answer goes at once.
BROADEN_FLOOR_S = 0.2
#: A zero answer asks again only when the query holds this many bare words.
BROADEN_MIN_WORDS = 2
BROADEN_MAX_WORDS = 5
#: Seconds for the near lookup of rung 3 (HRD-18b, ``find_engine.md`` §3.2).
#: Each name source runs at once, in this time, inside the second pass.
NEAR_LOOKUP_S = 0.5
#: The most names that one source gives for one word of the near lookup.
NEAR_NAMES_K = 5
#: The English stop words of Postgres (``english.stop``). The full-text half
#: of each app drops them, so a stop word alone finds nothing useful.
_STOP_TEXT = (
    "i me my myself we our ours ourselves you your yours yourself yourselves he "
    "him his himself she her hers herself it its itself they them their theirs "
    "themselves what which who whom this that these those am is are was were be "
    "been being have has had having do does did doing a an the and but if or "
    "because as until while of at by for with about against between into "
    "through during before after above below to from up down in out on off over "
    "under again further then once here there when where why how all any both "
    "each few more most other some such no nor not only own same so than too "
    "very s t can will just don should now"
)
STOP_WORDS: frozenset[str] = frozenset(_STOP_TEXT.split(" "))
#: The quote marks that make a phrase. A query with one is not broadened.
_QUOTE_MARKS = ('"', "“", "”")
#: The marks taken off the two ends of a word before it is searched alone.
_EDGE_MARKS = ".,;:!?()[]{}"


def bare_words(words: str) -> list[str] | None:
    """The words of *words*, or ``None`` when it holds a search operator.

    The operators of ``websearch_to_tsquery``: a quote mark, the word ``or``
    and a word that starts with ``-``. Pure (KR-ZERO).
    """
    if any(mark in words for mark in _QUOTE_MARKS):
        return None
    tokens = words.split()
    if any(t.lower() == "or" or t.startswith("-") for t in tokens):
        return None
    return tokens


def alone_words(words: str) -> list[str]:
    """The words to search alone after a zero answer, in order (KR-ZERO).

    Empty unless *words* holds 2 to 5 bare words. A stop word, a word of one
    letter and a repeated word are left out. Pure.
    """
    tokens = bare_words(words)
    if tokens is None or not BROADEN_MIN_WORDS <= len(tokens) <= BROADEN_MAX_WORDS:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        word = token.strip(_EDGE_MARKS)
        low = word.lower()
        if len(word) < 2 or low in STOP_WORDS or low in seen:
            continue
        seen.add(low)
        out.append(word)
    return out

#: The constant of the rank term of :func:`score` (§4.1).
RANK_K = 60
#: The lowest share of its score that age leaves a hit (§4.1). An old exact
#: match drops behind a new one, and stays in the list.
FLOOR = 0.35
#: The half-life of each app, in days (§4.1). An app with no entry does not
#: decay: Knowledge ranks by review state (§4.2), and a person does not age.
#: The eval of CX-2 may change these numbers, and nothing else may.
TAU_DAYS: dict[str, float] = {
    "email": 90.0,
    "whatsapp": 60.0,
    "tasks": 180.0,
    "crm": 180.0,
    "artifacts": 180.0,
    "chat": 120.0,
}


def recency_enabled() -> bool:
    """True when ``CONTEXT_RECENCY`` is on. Default off.

    The one reader is in ``acb_skills``, so the route, the tool and the
    persona read one flag. It reads the cached settings, so a change of the
    env file takes effect after a restart.
    """
    from acb_skills.company_search_tools import context_recency_enabled

    return context_recency_enabled()


def _instant(value: str | None) -> datetime | None:
    """ISO text as an aware instant, or ``None``. A date is its midnight UTC."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _superseded(hit: Hit) -> float:
    """The factor of a record that another record replaces (§4.4).

    Always 1.0 here. CX-1b sets 0.5 for a superseded record.
    """
    return 1.0


def score(hit: Hit, rank: int, now: datetime) -> float:
    """The score of §4.1 for the hit at *rank* (from 1) of its own app.

    ``1 / (60 + rank) * max(FLOOR, exp(-age / TAU)) * SUPERSEDED``, with the
    age in days of the hit's ``stamp``. An app with no half-life, and a hit
    with no stamp, take a decay of 1.0. Pure: *now* is the clock.
    """
    decay = 1.0
    tau = TAU_DAYS.get(hit.app)
    stamped = _instant(hit.stamp)
    if tau and stamped is not None:
        age = max(0.0, (now - stamped).total_seconds() / 86400.0)
        decay = max(FLOOR, math.exp(-age / tau))
    return 1.0 / (RANK_K + rank) * decay * _superseded(hit)


def by_recency(hits: list[Hit], now: datetime) -> list[Hit]:
    """One app's hits, sorted by :func:`score`. A tie keeps the app's order."""
    scored = [(score(h, rank, now), rank, h) for rank, h in enumerate(hits, start=1)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [h for _s, _r, h in scored]


#: The most characters of ``after``, checked only with the flag on.
AFTER_MAX_CHARS = 40
#: The most hits an app is asked for when ``after`` filters after the read.
#: Each app's own search accepts it: tasks 50, CRM a page, artifacts 200,
#: and Knowledge 20, its route's own maximum.
POOL_MAX = 20


def pool(k: int) -> int:
    """The hits to ask an app for when ``after`` filters after the read."""
    return max(k, min(4 * k, POOL_MAX))


def _after(value: str | None) -> datetime | None:
    """``after`` as an aware instant. A bad value is a 422 (§4.5).

    Called only with the flag on. The route declares no constraint on the
    parameter, so with the flag off no value of it is ever refused.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    if len(raw) > AFTER_MAX_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"'after' is a date of at most {AFTER_MAX_CHARS} characters.",
        )
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="'after' is a date, for example 2026-10-01.",
        ) from None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _kept(hit: Hit, after: datetime) -> bool:
    """False when the hit's stamp is before *after*. No stamp is kept."""
    stamped = _instant(hit.stamp)
    return stamped is None or stamped >= after


def _order(apps: str | None) -> list[str]:
    """The source keys to ask, the named ones first. An unknown key is a 422."""
    named = [a.strip().lower() for a in (apps or "").split(",") if a.strip()]
    unknown = [a for a in named if a not in REGISTRY]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown app {unknown[0]!r}. One of: {', '.join(REGISTRY)}.",
        )
    first = list(dict.fromkeys(named))
    return first + [key for key in REGISTRY if key not in first]


def _meaning_allowed(user: UserContext) -> bool:
    """True when ``KNOWLEDGE_EMBED`` names the member's org (KB-3c).

    The org comes from the session's ``UserContext``, never from the query.
    The flag module is read at call time: it lives under ``routes``.
    """
    from gateway.routes.knowledge import flags

    return flags.knowledge_embed_allowed(user.organization_id)


def _note_gap(background: BackgroundTasks | None, user: UserContext, words: str,
              knowledge_hits: int | None) -> None:
    """Hand a Knowledge miss to the gap write (KB-9a). It never raises.

    The gap module is read at call time: it lives under ``routes``, and it
    opens the session, so this package stays free of SQL.
    """
    if background is None or knowledge_hits != 0:
        return
    try:
        from gateway.routes.knowledge import gaps

        gaps.schedule_gap(background, user, words, knowledge_hits)
    except Exception as exc:
        logger.warning("search.company gap skipped", extra={"error": type(exc).__name__})


def _interleave(order: list[str], found: dict[str, list[Hit]]) -> list[Hit]:
    """Every app's first hit, then every app's second, in *order*."""
    out: list[Hit] = []
    depth = max((len(h) for h in found.values()), default=0)
    for rank in range(depth):
        for key in order:
            hits = found.get(key) or []
            if rank < len(hits):
                out.append(hits[rank])
    return out


def by_fusion(order: list[str], found: dict[str, list[Hit]], now: datetime,
              *, covered: bool = False) -> list[Hit]:
    """ONE list for every app, sorted by :func:`score` (KG-3b).

    Each app's list is ranked from 1 in the order it arrived. A tie goes to
    the app that comes first in *order*, then to the lower rank. Pure.

    *covered* (FIND-2, ``find_engine.md`` §3.4) puts two keys before the
    score: the hit that holds more of the query's words, then the lower rung.
    """
    place = {key: i for i, key in enumerate(order)}
    scored = [
        (score(h, rank, now), place.get(key, len(place)), rank, h)
        for key, hits in found.items()
        for rank, h in enumerate(hits, start=1)
    ]
    if covered:
        scored.sort(key=lambda item: (
            -(item[3].covers or 0), find.rung_of(item[3].matched_by),
            -item[0], item[1], item[2],
        ))
    else:
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [h for _s, _p, _r, h in scored]


def _never_open(user: UserContext, key: str) -> bool:
    """True when *key* was never open for *user*, so "ran out of time" would be
    false for it (WS-STU C5a, KR-ZERO review of 2026-11-14).

    Only the ``studio`` source today: while ``STUDIO_SEARCH_ORGS`` holds no org
    it is dark, and a member who holds ``feature:studio`` must get the answer of
    a member who does not, with the flag off and on. crm, artifacts and
    knowledge keep their KR-1 behaviour here until their owners decide the same
    for them (a closed switch of theirs is named "skipped" when the time is
    out). Fence: ``test_company_search_studio_off.py``."""
    return key == "studio" and not REGISTRY[key].reaches(user)


#: The most sources one gateway process asks at once (D-FIND-6, HRD-4). One
#: process has 7 async sessions and 2 more, and a search must leave room for
#: every other request. A fifth source waits for a slot.
FIND_SESSIONS = 4

#: One semaphore for each running event loop, so the cap holds for the whole
#: process, and a test that starts a new loop gets a fresh one.
_SLOTS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _slots() -> asyncio.Semaphore:
    """The semaphore of :data:`FIND_SESSIONS` slots for the running loop."""
    loop = asyncio.get_running_loop()
    slots = _SLOTS.get(loop)
    if slots is None:
        slots = _SLOTS[loop] = asyncio.Semaphore(FIND_SESSIONS)
    return slots


async def _ask_one(
    user: UserContext, key: str, words: str, per_app: int, *,
    hybrid: bool, options: dict[str, Any], since: datetime | None,
    deadline: float, timed: bool,
) -> tuple[str, list[Hit] | None]:
    """Ask one source for a slot, then for its hits (HRD-4).

    It returns ``("ok", hits)``, ``("skip", None)`` or ``("quiet", None)``. A
    source that was never open and has no time left is ``quiet``: it is not
    named in ``skipped``. Its time starts when it takes a slot, not before.
    """
    loop = asyncio.get_running_loop()
    async with _slots():
        left = deadline - loop.time()
        if left <= 0.05:
            return ("quiet", None) if _never_open(user, key) else ("skip", None)
        try:
            # With `after`, an app that cannot filter by date is asked for a
            # pool, and the filter runs before the cut to `per_app`.
            source = REGISTRY[key]
            ask = pool(per_app) if since is not None and not source.dated else per_app
            allowed = min(SOURCE_TIMEOUT_S, left)
            # Each source has its own copy: the sources run at once.
            mine = dict(options)
            if timed:
                # KG-3b: the vector half of a source gets the true time left.
                mine["budget_s"] = allowed
            hits = await asyncio.wait_for(
                source.find(user, words, ask, hybrid=hybrid, **mine),
                allowed,
            )
        except HTTPException:
            # The app's own refusal: a query too short for it, an access rule.
            return "skip", None
        except TimeoutError:
            logger.info("search.company source timed out", extra={"source": key})
            return "skip", None
        except Exception as exc:
            # The error's TYPE only. A database error's text can carry the
            # query's parameters, which hold the member's words and address.
            logger.warning(
                "search.company source failed",
                extra={"source": key, "error": type(exc).__name__},
            )
            return "skip", None
    return "ok", list(hits)


async def _ask_each(
    user: UserContext, words: str, order: list[str], per_app: int, *,
    hybrid: bool, options: dict[str, Any], since: datetime | None,
    deadline: float, timed: bool,
) -> tuple[dict[str, list[Hit]], list[str], int | None]:
    """Ask each source of *order* once, at once, before *deadline*.

    At most :data:`FIND_SESSIONS` sources run together, and each has its own
    :data:`SOURCE_TIMEOUT_S` from the moment it starts, so a slow source does
    not hold a fast one. It returns the hits of each app that answered, the
    apps it skipped, and the hits Knowledge answered before ``after`` filtered
    them (KB-9a). Both lists follow *order*, whatever finishes first.
    *timed* gives each source its true time left as ``budget_s`` (KG-3b).
    """
    answers = await asyncio.gather(*(
        _ask_one(
            user, key, words, per_app, hybrid=hybrid, options=options,
            since=since, deadline=deadline, timed=timed,
        )
        for key in order
    ))
    found: dict[str, list[Hit]] = {}
    skipped: list[str] = []
    knowledge_hits: int | None = None
    for key, (state, hits) in zip(order, answers, strict=True):
        if state == "skip":
            skipped.append(key)
        elif state == "ok":
            assert hits is not None
            if key == "knowledge":
                knowledge_hits = len(hits)
            if since is not None:
                hits = [h for h in hits if _kept(h, since)]
            found[key] = hits[:per_app]
    return found, skipped, knowledge_hits


def _meaning_wanted(user: UserContext, order: list[str], hybrid: bool) -> bool:
    """True when a source of *order* will ask for the query's vector (KR-ZERO).

    Knowledge asks when ``KNOWLEDGE_EMBED`` names the org (*hybrid*). Email
    asks when ``EMAIL_SEMANTIC_SEARCH_ENABLED`` is on. The member holds each
    app of *order* already.
    """
    return bool(hybrid and "knowledge" in order) or ("email" in order and email_meaning_on())


def _start_embed(user: UserContext, words: str) -> None:
    """Start the one embed of this turn, with :data:`EMBED_BUDGET_S` (KR-ZERO).

    The org comes from the session. The door module is read at call time, as
    each source reads its app.
    """
    from gateway.kb_index import query as kb_query

    org = user.organization_id
    turn_vector.start(
        org, words,
        lambda: kb_query.turn_embed(org, words, timeout=EMBED_BUDGET_S),
        budget_s=EMBED_BUDGET_S,
    )


def _merge(order: list[str], passes: list[dict[str, list[Hit]]],
           per_app: int) -> dict[str, list[Hit]]:
    """The hits of each app over every word, with no repeated id (KR-ZERO).

    Each app takes the first hit of each word, then the second, and keeps
    at most *per_app*. Pure.
    """
    merged: dict[str, list[Hit]] = {}
    for key in order:
        lists = [found.get(key) or [] for found in passes]
        out: list[Hit] = []
        seen: set[str] = set()
        for rank in range(max((len(hits) for hits in lists), default=0)):
            for hits in lists:
                if rank < len(hits) and hits[rank].id not in seen:
                    seen.add(hits[rank].id)
                    out.append(hits[rank])
        merged[key] = out[:per_app]
    return merged


async def _broaden(
    user: UserContext, alone: list[str], asked: list[str], per_app: int, *,
    options: dict[str, Any], since: datetime | None, until: float,
    tag: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[Hit]]]:
    """Ask the apps of *asked* again with each word alone, before *until*.

    Each word takes an equal share of the time left, and an unused share
    goes to the next word. No vector half runs. It returns what each word
    found (``broadened``) and the merged hits of each app (KR-ZERO). *tag* is
    the content words of the query, set only with ``FIND_ORGS`` on (FIND-2).
    """
    loop = asyncio.get_running_loop()
    lexical = {k: v for k, v in options.items() if k not in ("vectors", "budget_s")}
    report: list[dict[str, Any]] = []
    passes: list[dict[str, list[Hit]]] = []
    for i, word in enumerate(alone):
        share = max(0.0, until - loop.time()) / (len(alone) - i)
        found, skipped, _knowledge = await _ask_each(
            user, word, asked, per_app, hybrid=False, options=dict(lexical),
            since=since, deadline=loop.time() + share, timed=False,
        )
        if tag is not None:
            # FIND-2: each hit says which word found it, and how many it holds.
            found = {
                key: [find.stamped(h, tag, "word", word=word) for h in hits]
                for key, hits in found.items()
            }
        passes.append(found)
        report.append({
            "word": word, "hits": sum(len(hits) for hits in found.values()),
            "skipped": skipped,
        })
    return report, _merge(asked, passes, per_app)


# ── Rung 3: the near spelling (HRD-18b) ─────────────────────────────────────

@dataclass(frozen=True)
class NearAsk:
    """One ask of the near step: *q* to the apps of *keys*. *sender* asks
    Email for the mail from that sender. *word* is the content word that the
    spelling *spelling* stands for."""

    q: str
    keys: tuple[str, ...]
    word: str
    spelling: str
    sender: str | None = None


async def _near_lookup(
    user: UserContext, words: list[str], keys: list[str], until: float,
) -> dict[str, dict[str, list[NearName]]]:
    """The names near *words*, from each source of *keys* that can ``near``.

    The sources run at once, each inside :data:`NEAR_LOOKUP_S` and *until*.
    A source that fails, refuses or runs late gives no name. It is not named
    in ``skipped``, because its search still answered. The log line holds
    the source key and the error type, and no word.
    """
    loop = asyncio.get_running_loop()
    allowed = min(NEAR_LOOKUP_S, until - loop.time())
    able = [key for key in keys if REGISTRY[key].near is not None]
    if allowed <= 0.05 or not able:
        return {}

    async def one(key: str) -> tuple[str, dict[str, list[NearName]]]:
        try:
            got = await asyncio.wait_for(
                REGISTRY[key].near_names(user, words, NEAR_NAMES_K), allowed,
            )
        except Exception as exc:
            logger.info("search.company near lookup skipped",
                        extra={"source": key, "error": type(exc).__name__})
            return key, {}
        return key, got if isinstance(got, dict) else {}

    return {key: got for key, got in await asyncio.gather(*(one(key) for key in able)) if got}


def near_asks(
    words_of: list[str], chosen: list[str], names: dict[str, dict[str, list[NearName]]],
    asked: list[str],
) -> list[NearAsk]:
    """The asks of the near step, in order (HRD-18b). Pure.

    For each word of *chosen*: each app of *asked* takes the best spelling of
    its own names, else the best spelling of all. Each spelling gives the
    query with that one word replaced, then the spelling alone. Email, with
    a spelling of its own senders, then asks for the mail from that sender:
    with the other words, then alone.
    """
    out: list[NearAsk] = []
    seen: set[tuple[str, tuple[str, ...], str | None]] = set()

    def add(ask: NearAsk) -> None:
        mark = (ask.q, ask.keys, ask.sender)
        if ask.keys and mark not in seen:
            seen.add(mark)
            out.append(ask)

    for word in chosen:
        own = {
            key: best for key in asked
            if (best := name_match.best_spelling(word, names.get(key, {}).get(word, [])))
        }
        every = name_match.best_spelling(
            word, [n for by_word in names.values() for n in by_word.get(word, [])])
        groups: dict[str, list[str]] = {}
        spelt: dict[str, str] = {}
        by_sender: list[tuple[str, str]] = []
        for key in asked:
            best = own.get(key) or every
            if best is None:
                continue
            if key in own and REGISTRY[key].senders:
                by_sender.append((key, best.spelling))
                continue
            low = best.spelling.lower()
            spelt.setdefault(low, best.spelling)
            groups.setdefault(low, []).append(key)
        for low, keys in groups.items():
            if len(words_of) > 1:
                replaced = " ".join(find.spelled(words_of, word, low))
                add(NearAsk(replaced, tuple(keys), word, spelt[low]))
            add(NearAsk(low, tuple(keys), word, spelt[low]))
        for key, spelling in by_sender:
            rest = " ".join(w for w in words_of if w != word)
            add(NearAsk(rest, (key,), word, spelling, sender=spelling))
            add(NearAsk("", (key,), word, spelling, sender=spelling))
    return out


def _first_in_order(hit: Hit) -> tuple[int, int]:
    """The two keys of §3.4 that come before the score: more words first,
    then the lower rung."""
    return -(hit.covers or 0), find.rung_of(hit.matched_by)


def _merge_near(merged: dict[str, list[Hit]], passes: list[dict[str, list[Hit]]],
                order: list[str], per_app: int) -> dict[str, list[Hit]]:
    """*merged* with the hits of *passes* added, with no repeated id (HRD-18b).

    A hit that two passes found keeps the lower rung of ``find.RUNGS``
    (§3.5), in the place of its first copy, as ``find.stamped`` does. So a
    mail that "3D" alone found too says "the word 3D alone", even when its
    near copy holds more words. Each app's list is cut to *per_app*, the hit
    with more words first (§3.4). Pure. No pass gives *merged* itself, so an
    answer with no near ask is the answer of FIND-2, byte for byte.
    """
    if not passes:
        return merged
    out: dict[str, list[Hit]] = {}
    for key in order:
        hits = list(merged.get(key) or [])
        place = {h.id: i for i, h in enumerate(hits)}
        lists = [found.get(key) or [] for found in passes]
        for rank in range(max((len(found) for found in lists), default=0)):
            for found in lists:
                if rank >= len(found):
                    continue
                hit = found[rank]
                if hit.id not in place:
                    place[hit.id] = len(hits)
                    hits.append(hit)
                elif find.rung_of(hit.matched_by) < find.rung_of(hits[place[hit.id]].matched_by):
                    hits[place[hit.id]] = hit
        if len(hits) > per_app:
            # `sorted` is stable, so a tie keeps the order of the passes.
            hits = sorted(hits, key=_first_in_order)
        if key in merged or hits:
            out[key] = hits[:per_app]
    return out


async def _near_step(
    user: UserContext, words: str, words_of: list[str] | None, order: list[str],
    found: dict[str, list[Hit]], per_app: int, *, options: dict[str, Any],
    since: datetime | None, until: float, after_asks: int,
) -> tuple[list[dict[str, Any]], list[dict[str, list[Hit]]]]:
    """Rung 3 of the second pass (HRD-18b, ``find_engine.md`` §3.2).

    It runs only with ``FIND_ORGS`` on (*words_of* set), when every app that
    answered the first pass (*found*) found nothing, for a query with no
    operator, and with :data:`BROADEN_FLOOR_S` left. Else it gives
    ``([], [])`` and asks nothing.

    The near lookup over each source the member holds (*order*), then the
    asks of :func:`near_asks` to the apps that answered. Each ask takes an
    equal share of the time left before *until*, counting the *after_asks*
    asks of the words alone that follow. No vector half runs. It returns
    ``near`` for the answer and the stamped hits of each ask.
    """
    loop = asyncio.get_running_loop()
    if (not words_of or not found or any(found.values()) or bare_words(words) is None
            or until - loop.time() < BROADEN_FLOOR_S):
        return [], []
    asked = list(found)
    started = loop.time()
    chosen = find.near_words(words, words_of, set())
    names = await _near_lookup(user, chosen, order, until) if chosen else {}
    asks = near_asks(words_of, chosen, names, asked) if names else []
    lexical = {k: v for k, v in options.items() if k not in ("vectors", "budget_s")}
    passes: list[dict[str, list[Hit]]] = []
    #: Each word and spelling, and the records that they found, with no repeat.
    counts: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for i, ask in enumerate(asks):
        share = max(0.0, until - loop.time()) / (len(asks) - i + after_asks)
        extra = dict(lexical)
        if ask.sender:
            extra["sender"] = ask.sender
        got, _skipped, _knowledge = await _ask_each(
            user, ask.q, list(ask.keys), per_app, hybrid=False, options=extra,
            since=since, deadline=loop.time() + share, timed=False,
        )
        typed = find.asked_form(words, ask.word)
        got = {
            key: [find.stamped(h, words_of, "near", word=typed, as_=ask.spelling) for h in hits]
            for key, hits in got.items()
        }
        passes.append(got)
        counts.setdefault((typed, ask.spelling), set()).update(
            (key, h.id) for key, hits in got.items() for h in hits)
    report = [{"word": typed, "as": spelling, "hits": len(records)}
              for (typed, spelling), records in counts.items()]
    _events.info(
        "search.company.near", words=len(chosen), sources=len(names), asks=len(asks),
        hits=sum(len(records) for records in counts.values()),
        ms=round((loop.time() - started) * 1000),
    )
    return report, passes


@router.get("/company")
async def company_search(
    q: str = Query(..., min_length=MIN_CHARS, max_length=MAX_CHARS),
    apps: str | None = Query(None, max_length=200),
    k: int = Query(DEFAULT_K, ge=1, le=MAX_K),
    user: UserContext = Depends(get_current_user),
    # A plain default: FastAPI checks nothing on it, so with the flag off no
    # value is refused, and a direct call that leaves it out gets None.
    after: str | None = None,
    # KB-AI-4. A plain default, read only with the flag on, as `after`. Set,
    # Knowledge answers pages with an owner and a review date in the future.
    verified: bool = False,
    # KB-9a. FastAPI gives the route's own tasks, never a request value. A
    # direct call that leaves it out gets None and records no gap.
    background: BackgroundTasks = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Records that match the words, from every app this member holds."""
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    words = " ".join((q or "").split())
    if not MIN_CHARS <= len(words) <= MAX_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"Give {MIN_CHARS} to {MAX_CHARS} characters to find.",
        )
    per_app = max(1, min(int(k), MAX_K))
    # An app the member does not hold is not asked, and it is not "skipped":
    # the member has no such app. Each source checks the feature again.
    order = [key for key in _order(apps) if holds(user, REGISTRY[key].feature)]
    hybrid = _meaning_allowed(user)
    recency = recency_enabled()
    # Off, `after` is not read at all, as before CX-1.
    since = _after(after) if recency else None
    options: dict[str, Any] = {"after": since, "reviewed": True, "vectors": True} if recency else {}
    if recency:
        turn_vector.begin_turn()
    only_verified = bool(recency and verified)
    if only_verified:
        options["verified_only"] = True

    loop = asyncio.get_running_loop()
    began = loop.time()
    deadline = began + TOTAL_BUDGET_S
    meaning = turn_vector.MEANING_OFF
    if recency and _meaning_wanted(user, order, hybrid):
        # KR-ZERO: the embed runs while the keyword sources run.
        _start_embed(user, words)
    try:
        # KB-9a: `knowledge_hits` is the hits Knowledge answered, before
        # `after` filters them. None when the route did not ask Knowledge,
        # or Knowledge failed.
        found, skipped, knowledge_hits = await _ask_each(
            user, words, order, per_app, hybrid=hybrid, options=options,
            since=since, deadline=deadline, timed=recency,
        )
    finally:
        if recency:
            meaning = turn_vector.outcome()
            if meaning != turn_vector.MEANING_OFF:
                _events.info("search.company.embed", ms=turn_vector.embed_ms(),
                             outcome=meaning)
            turn_vector.end_turn()

    # A filtered search that finds nothing says nothing about a gap (KB-AI-4).
    _note_gap(background, user, words, None if only_verified else knowledge_hits)

    # FIND-2: with the flag on, each hit says how it matched. It acts only with
    # CONTEXT_RECENCY on, so the flag-off answer below is untouched.
    words_of = find.content_words(words) if recency and find.find_allowed(user.organization_id) else None
    if words_of is not None:
        found = {key: [find.stamped(h, words_of, "exact") for h in hits]
                 for key, hits in found.items()}
    if not recency:
        return {
            "query": words,
            "hits": [h.as_dict() for h in _interleave(order, found)],
            "skipped": skipped,
        }
    # KR-ZERO: every app that answered found nothing. Ask again, each word alone.
    broadened: list[dict[str, Any]] | None = None
    alone = alone_words(words) if found and not any(found.values()) else []
    if words_of is not None and alone:
        # The fold of §3.3: "Sharath's" is searched as "sharath".
        alone = words_of
    until = min(loop.time() + BROADEN_BUDGET_S, began + ANSWER_CAP_S)
    # HRD-18b: rung 3 on a zero answer, with FIND_ORGS on. Its asks run before
    # the words alone, and share the time of the second pass with them.
    near, near_passes = await _near_step(
        user, words, words_of, order, found, per_app, options=options,
        since=since, until=until, after_asks=len(alone),
    )
    if alone and until - loop.time() >= BROADEN_FLOOR_S:
        started = loop.time()
        broadened, found = await _broaden(
            user, alone, list(found), per_app, options=options, since=since, until=until,
            tag=words_of,
        )
        _events.info(
            "search.company.broaden", words=len(alone),
            hits=sum(len(hits) for hits in found.values()),
            ms=round((loop.time() - started) * 1000),
        )
    found = _merge_near(found, near_passes, order, per_app)
    now = datetime.now(UTC)
    answer: dict[str, Any] = {
        "query": words,
        "hits": [h.as_dict(stamp=True, find=words_of is not None)
                 for h in by_fusion(order, found, now, covered=words_of is not None)],
        "searched": [key for key in order if key in found and REGISTRY[key].reaches(user)],
        "skipped": skipped,
        "meaning": meaning,
    }
    if broadened is not None:
        answer["broadened"] = broadened
    if near:
        answer["near"] = near
    return answer
