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
  ``k``, ``after`` (a date, CX-1), ``verified``, ``explain`` and
  ``current_chat`` (HRD-21), and no tenant and no member can come from input
  (R5, R11). ``current_chat`` only takes a chat away. Each source passes this
  request's member to its app.
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
``CONTEXT_RECENCY`` on. With it, the second pass starts on a MISS of
D-FIND-4 (:func:`_missed`): the best hit holds fewer than all the content
words, and zero hits is one kind of miss. The words alone run on that miss
too, and the hits of the first pass stay (:func:`_keep_first`). On a miss,
for a query with no operator:

* the route picks at most 3 words of 4 letters or more, first the words
  that no hit holds (:func:`find.near_words`, :func:`find.held`), and asks
  each source that can ``near`` for the
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
  names each word, its spelling, its hits and ``held``: true when the word
  has a hit of its own (:func:`find.near_report`). The log line is
  ``search.company.near``, with no word.

**Related records, behind ``FIND_ORGS``** (FIND-3, task 3240,
``find_engine.md`` §3.1 rung 4, §3.5, §4.3 and §6). After every other pass,
:func:`_related_step` follows the links of the first 5 hits that are a deal,
lead, contact, person or company. The sources' ``related`` give ids and keys
(an address, a web domain), and their ``open`` gives the hits that the member
can open. Each related hit says ``matched_by`` ``related``, with ``from``
(the hit it came from), ``edge`` and ``via`` from the closed list
``find.EDGES``. They go to the answer key ``related``, never to ``hits``: at
most 3 for each hit, 15 in all, inside :data:`RELATED_BUDGET_S`. A record that
the member cannot open leaves no trace. The log line is
``search.company.related``, with counts only.

**The honest zero, the explain mode and the log line, behind ``FIND_ORGS``**
(FIND-4, task 3241, ``find_engine.md`` §3.6 and §3.7). Each value comes from
a closed list of :mod:`gateway.retrieval.find`:

* ``skipped_why`` names the reason of each app of ``skipped``: timed out,
  failed or refused. Its presence marks the answer of the flag for the tool;
* ``none_has_all`` is true only with proof that no record holds all the words
  (:func:`_none_has_all`): the answer misses, and one word found nothing
  alone and passes ``find.proves_absence``. The member typed it as it folds,
  no hit and no related record shows it, and the near lookup ran and gave no
  name of the word itself. A low ``covers``, or a first pass with no hit, is
  no proof. While ``find.ABSENCE_CLAIMS`` is off, it is never true;
* a zero answer gets ``where_else``: at most 3 entries, the skipped apps, the
  past chats while the search did not ask them (HRD-21), then the apps that
  are not open to the member. No entry names a record;
* ``explain`` runs each rung with no early stop and answers the trace of
  :func:`_explained` in place of the hits and the related records;
* each search writes one ``search.company.ladder`` line: the hits, the ms of
  each rung, ``miss`` and ``explain``. It holds no word, no name, no address
  and no id.

**The past chats, behind ``CHAT_SEARCH_ORGS``** (WS-FIND HRD-21, task 3225,
``find_engine.md`` §3.1 rung 6, §6 and D-FIND-5). The ``chat`` source reads the
member's own solo chats with Mira, never the current chat:

* the route asks it only when its switch is open for the member and the
  request names the current chat (:func:`_chat_current`). Else the source is
  not in the order at all, and the answer is the one of before HRD-21, byte
  for byte. With no current chat, no past chat shows;
* it answers in pass 1 only. Its hits leave ``found`` before the miss rule,
  the words alone and the near step, and they join the one list again before
  :func:`by_fusion`. With ``FIND_ORGS`` on, each says ``matched_by`` ``chat``;
* "Where else" names the past chats only when the search did not ask them,
  and never names ``chat`` as an app that is not open. The trace gives rung 6
  one row (``find.trace_rows``).

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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NamedTuple

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
#: The most of the second pass that the near step takes, lookup and asks,
#: when the words alone follow it. They keep the rest (HRD-18b review, P2-a).
NEAR_SHARE = 1 / 3
#: Seconds for rung 4, the related records (FIND-3, ``find_engine.md`` §3.1
#: and §3.2). The step starts after the merge, and it also stops at
#: ANSWER_CAP_S. What finished by then answers. A late read gives nothing,
#: and it is not named in ``skipped``.
RELATED_BUDGET_S = 1.0
#: The hits whose links rung 4 follows: the first of the answer that are a
#: deal, lead, contact, person or company.
RELATED_HITS = 5
#: The most related records of one hit, and of one answer.
RELATED_PER_HIT = 3
RELATED_MAX = 15
#: The tokens of the related block (``knowledge_graph.md`` §7.1). The count is
#: the characters of the printed lines divided by 4. No tokenizer runs.
RELATED_TOKENS = 1500
#: The characters that a printed line adds to the fields of a related hit:
#: the marks, the date words, the bracket of §3.5 and ``full_id``.
RELATED_LINE_CHARS = 120
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
#: ``find.typed_alone`` takes the same marks off a typed token (FIND-4).
_EDGE_MARKS = find.EDGE_MARKS


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


class _Asked(NamedTuple):
    """What one ask of one source gave (HRD-4, FIND-4)."""

    #: ``ok``, ``skip`` or ``quiet``.
    state: str
    hits: list[Hit] | None = None
    #: Why a ``skip``: a reason of ``find.SKIP_REASONS``.
    why: str | None = None
    #: The time from the slot to the answer, in ms.
    ms: int = 0
    #: False when the source had no time left at its slot.
    ran: bool = True


def _ms_since(loop: asyncio.AbstractEventLoop, started: float) -> int:
    """The ms from *started* to now on the loop's clock."""
    return max(0, round((loop.time() - started) * 1000))


def _failure(exc: BaseException) -> str:
    """The reason of ``find.SKIP_REASONS`` for an error of one read (FIND-4)."""
    if isinstance(exc, TimeoutError):
        return "timed_out"
    if isinstance(exc, HTTPException):
        return "refused"
    return "failed"


async def _ask_one(
    user: UserContext, key: str, words: str, per_app: int, *,
    hybrid: bool, options: dict[str, Any], since: datetime | None,
    deadline: float, timed: bool,
) -> _Asked:
    """Ask one source for a slot, then for its hits (HRD-4).

    Its state is ``ok`` with the hits, ``skip`` or ``quiet``. A source that
    was never open and has no time left is ``quiet``: it is not named in
    ``skipped``. Its time starts when it takes a slot, not before. A ``skip``
    says why (FIND-4): ``timed_out`` (also with no time left at its slot),
    ``refused`` (the app's ``HTTPException``) or ``failed``.
    """
    loop = asyncio.get_running_loop()
    async with _slots():
        started = loop.time()
        left = deadline - started
        if left <= 0.05:
            if _never_open(user, key):
                return _Asked("quiet", ran=False)
            return _Asked("skip", why="timed_out", ran=False)
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
            return _Asked("skip", why="refused", ms=_ms_since(loop, started))
        except TimeoutError:
            logger.info("search.company source timed out", extra={"source": key})
            return _Asked("skip", why="timed_out", ms=_ms_since(loop, started))
        except Exception as exc:
            # The error's TYPE only. A database error's text can carry the
            # query's parameters, which hold the member's words and address.
            logger.warning(
                "search.company source failed",
                extra={"source": key, "error": type(exc).__name__},
            )
            return _Asked("skip", why="failed", ms=_ms_since(loop, started))
    return _Asked("ok", list(hits), ms=_ms_since(loop, started))


async def _ask_each(
    user: UserContext, words: str, order: list[str], per_app: int, *,
    hybrid: bool, options: dict[str, Any], since: datetime | None,
    deadline: float, timed: bool, notes: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, list[Hit]], list[str], int | None]:
    """Ask each source of *order* once, at once, before *deadline*.

    At most :data:`FIND_SESSIONS` sources run together, and each has its own
    :data:`SOURCE_TIMEOUT_S` from the moment it starts, so a slow source does
    not hold a fast one. It returns the hits of each app that answered, the
    apps it skipped, and the hits Knowledge answered before ``after`` filtered
    them (KB-9a). Both lists follow *order*, whatever finishes first.
    *timed* gives each source its true time left as ``budget_s`` (KG-3b).

    *notes* (FIND-4) keeps, for each source, its outcome, its hits and its
    time, with :func:`find.add_note`. ``None`` keeps nothing.
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
    for key, asked in zip(order, answers, strict=True):
        if asked.state == "skip":
            skipped.append(key)
            find.add_note(notes, key, asked.why or "failed", ms=asked.ms, ran=asked.ran)
        elif asked.state == "ok":
            hits = asked.hits
            assert hits is not None
            if key == "knowledge":
                knowledge_hits = len(hits)
            if since is not None:
                hits = [h for h in hits if _kept(h, since)]
            found[key] = hits[:per_app]
            find.add_note(notes, key, "ok" if found[key] else "empty",
                          hits=len(found[key]), ms=asked.ms)
        else:
            find.add_note(notes, key, "closed", ran=False)
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
    tag: list[str] | None = None, notes: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[Hit]]]:
    """Ask the apps of *asked* again with each word alone, before *until*.

    Each word takes an equal share of the time left, and an unused share
    goes to the next word. No vector half runs. It returns what each word
    found (``broadened``) and the merged hits of each app (KR-ZERO). *tag* is
    the content words of the query, set only with ``FIND_ORGS`` on (FIND-2).
    *notes* keeps what each source did over every word (FIND-4).
    """
    loop = asyncio.get_running_loop()
    lexical = {k: v for k, v in options.items() if k not in ("vectors", "budget_s")}
    report: list[dict[str, Any]] = []
    passes: list[dict[str, list[Hit]]] = []
    for i, word in enumerate(alone):
        share = max(0.0, until - loop.time()) / (len(alone) - i)
        found, skipped, _knowledge = await _ask_each(
            user, word, asked, per_app, hybrid=False, options=dict(lexical),
            since=since, deadline=loop.time() + share, timed=False, notes=notes,
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
    Email for the mail from that sender, and *rest*, the other words of the
    query, still filter that mail. *word* is the content word that the
    spelling *spelling* stands for."""

    q: str
    keys: tuple[str, ...]
    word: str
    spelling: str
    sender: str | None = None
    rest: str = ""


async def _near_lookup(
    user: UserContext, words: list[str], keys: list[str], until: float,
    notes: dict[str, dict[str, Any]] | None = None, answered: set[str] | None = None,
) -> dict[str, dict[str, list[NearName]]]:
    """The names near *words*, from each source of *keys* that can ``near``.

    The sources run at once, each inside :data:`NEAR_LOOKUP_S` and *until*.
    A source that fails, refuses or runs late gives no name. It is not named
    in ``skipped``, because its search still answered. The log line holds
    the source key and the error type, and no word. *notes* keeps the
    outcome and the time of each lookup, with no hit: a name is not a record
    (FIND-4). *answered* gets the key of each source whose lookup answered in
    time, with no error.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(NEAR_LOOKUP_S, until - loop.time())
    able = [key for key in keys if REGISTRY[key].near is not None]
    if deadline - loop.time() <= 0.05 or not able:
        return {}

    async def one(key: str) -> tuple[str, dict[str, list[NearName]]]:
        # Each lookup holds one session, so it takes a slot of FIND_SESSIONS
        # as a source does (HRD-4). Its time runs to the one deadline.
        async with _slots():
            started = loop.time()
            left = deadline - started
            if left <= 0.05:
                find.add_note(notes, key, "timed_out", ran=False)
                return key, {}
            try:
                got = await asyncio.wait_for(
                    REGISTRY[key].near_names(user, words, NEAR_NAMES_K), left,
                )
            except Exception as exc:
                logger.info("search.company near lookup skipped",
                            extra={"source": key, "error": type(exc).__name__})
                find.add_note(notes, key, _failure(exc), ms=_ms_since(loop, started))
                return key, {}
        got = got if isinstance(got, dict) else {}
        # A name is no hit: the asks of the step decide `ok`.
        find.add_note(notes, key, "empty", ms=_ms_since(loop, started))
        if answered is not None:
            # FIND-4: this source's names are complete evidence for the proof.
            answered.add(key)
        return key, got

    return {key: got for key, got in await asyncio.gather(*(one(key) for key in able)) if got}


def near_asks(
    words_of: list[str], chosen: list[str], names: dict[str, dict[str, list[NearName]]],
    asked: list[str],
) -> list[NearAsk]:
    """The asks of the near step: ONE for each word of *chosen*, so at most
    ``name_match.NEAR_MAX_WORDS`` (HRD-18b review of 2026-11-16, P2-a). Pure.

    The ask is the whole query, *words_of*, with that one word replaced by
    the best spelling of all the sources (``name_match.best_spelling``: the
    score, then the raw trigrams), to each app of *asked* (round 3, P2-1).
    An app with ``senders`` (Email) reads the mail from its own best sender
    in the same ask (``sender``), because a full-text word never matches
    inside an address. The other words (``rest``) still filter that mail,
    so an old order of the sender comes back, and his newest chatter does
    not.
    """
    out: list[NearAsk] = []
    for word in chosen:
        every = name_match.best_spelling(
            word, [n for by_word in names.values() for n in by_word.get(word, [])])
        if every is None or not asked:
            continue
        sender = next((
            mine.spelling for key in asked if REGISTRY[key].senders
            and (mine := name_match.best_spelling(word, names.get(key, {}).get(word, [])))
        ), None)
        out.append(NearAsk(
            " ".join(find.spelled(words_of, word, every.spelling)), tuple(asked), word,
            every.spelling, sender=sender, rest=" ".join(w for w in words_of if w != word),
        ))
    return out


def _missed(found: dict[str, list[Hit]], words_of: list[str] | None) -> bool:
    """True when the first pass missed, so the second pass may run.

    Off (*words_of* is None), the rule of KR-ZERO: an app answered, and every
    app that answered found nothing. On (``FIND_ORGS``), the rule of D-FIND-4
    (``find_engine.md`` §3.3): the best hit holds fewer than all the content
    words. Zero hits is one kind of miss. Pure.
    """
    if not found:
        return False
    if words_of is None:
        return not any(found.values())
    return find.missed(found, words_of)


def _keep_first(found: dict[str, list[Hit]], wide: dict[str, list[Hit]],
                words_of: list[str] | None, order: list[str],
                per_app: int) -> dict[str, list[Hit]]:
    """The hits after the words alone. Off, *wide* replaces *found*, as in
    KR-ZERO, because the first pass found nothing. On, the first pass may hold
    hits (a miss of D-FIND-4), so :func:`_merge_near` adds *wide* to them.
    On a first pass with no hit, that is *wide*, byte for byte. Pure."""
    if words_of is None:
        return wide
    return _merge_near(found, [wide], order, per_app)


def _first_in_order(hit: Hit) -> tuple[int, int]:
    """The two keys of §3.4 that come before the score: more words first,
    then the lower rung."""
    return -(hit.covers or 0), find.rung_of(hit.matched_by)


def _wins_a_tie(new: Hit, kept: Hit) -> bool:
    """True when *new* replaces *kept*, two copies of one record that two
    rungs found (HRD-18b review of 2026-11-16, P2-d).

    The copy that holds more words wins, and on a tie the lower rung: the
    order of §3.4. The text of §3.5 says the lower rung alone. The owner is
    asked (a3). To go back to §3.5, compare ``find.rung_of`` here only.
    """
    return _first_in_order(new) < _first_in_order(kept)


def _merge_near(merged: dict[str, list[Hit]], passes: list[dict[str, list[Hit]]],
                order: list[str], per_app: int) -> dict[str, list[Hit]]:
    """*merged* with the hits of *passes* added, with no repeated id (HRD-18b).

    A hit that two passes found keeps the copy of :func:`_wins_a_tie`, in the
    place of its first copy: the copy that holds more words, then the lower
    rung. So a mail from «Sharat» that holds "3D printer" keeps ``near``,
    3 of 3 words, over "the word 3D alone", 2 of 3. Each app's list is cut to
    *per_app* in the same order (§3.4). Pure. No pass gives *merged* itself,
    so an answer with no near ask is the answer of FIND-2, byte for byte.
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
                elif _wins_a_tie(hit, hits[place[hit.id]]):
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
    since: datetime | None, until: float, after_asks: int, miss: bool,
    notes: dict[str, dict[str, Any]] | None = None,
    weighed: list[dict[str, Any]] | None = None,
    looked: dict[str, bool] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, list[Hit]]]]:
    """Rung 3 of the second pass (HRD-18b, ``find_engine.md`` §3.2).

    It runs only with ``FIND_ORGS`` on (*words_of* set), when the first pass
    (*found*) missed (*miss*, D-FIND-4: its best hit holds fewer than all the
    content words), for a query with no operator, and with
    :data:`BROADEN_FLOOR_S` left. Else it gives ``([], [])`` and asks nothing.

    The near lookup over each source the member holds (*order*), for the
    words of :func:`find.near_words`: first the words that no hit of *found*
    holds. Then the asks of :func:`near_asks` to the apps that answered, one
    for each word. When the words alone follow (*after_asks*), the whole step
    takes at most :data:`NEAR_SHARE` of the time left before *until*, so the
    words alone keep the rest (review of 2026-11-16, P2-a). No vector half
    runs. It returns ``near`` for the answer and the stamped hits of each ask.

    FIND-4: *notes* keeps what each source did in the lookup and the asks,
    and *weighed* gets the spellings of :func:`find.weighed`, for the trace.
    *looked* gets one bool for each word of the lookup: true when each near
    source of *order* answered, and no name is the word itself
    (:func:`find.named_itself`). It holds no name, and no line logs it. A word
    with no entry was not looked up, so it proves nothing.
    """
    loop = asyncio.get_running_loop()
    if (not words_of or not miss or bare_words(words) is None
            or until - loop.time() < BROADEN_FLOOR_S):
        return [], []
    asked = list(found)
    started = loop.time()
    if after_asks:
        until = started + (until - started) * NEAR_SHARE
    chosen = find.near_words(words, words_of, find.held(found, words_of))
    answered: set[str] = set()
    names = await _near_lookup(user, chosen, order, until, notes, answered) if chosen else {}
    if weighed is not None:
        weighed += find.weighed(chosen, names, words)
    if looked is not None:
        complete = {key for key in order if REGISTRY[key].near is not None} <= answered
        looked.update({word: complete and not find.named_itself(word, names)
                       for word in chosen})
    asks = near_asks(words_of, chosen, names, asked) if names else []
    lexical = {k: v for k, v in options.items() if k not in ("vectors", "budget_s")}
    passes: list[dict[str, list[Hit]]] = []
    #: Each word and spelling, and the records that they found, with no repeat.
    counts: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for i, ask in enumerate(asks):
        share = max(0.0, until - loop.time()) / (len(asks) - i)
        extra = dict(lexical)
        if ask.sender:
            # Email: the mail from the sender that holds the other words.
            extra["sender"] = ask.sender
            extra["sender_words"] = ask.rest
        got, _skipped, _knowledge = await _ask_each(
            user, ask.q, list(ask.keys), per_app, hybrid=False, options=extra,
            since=since, deadline=loop.time() + share, timed=False, notes=notes,
        )
        typed = find.asked_form(words, ask.word)
        # A spelling that was tried and found nothing still names its word.
        counts.setdefault((typed, ask.spelling), set())
        for key, hits in got.items():
            # Email read the mail of its own best sender, so its hits name it.
            spelt = ask.sender if ask.sender and REGISTRY[key].senders else ask.spelling
            got[key] = [find.stamped(h, words_of, "near", word=typed, as_=spelt) for h in hits]
            counts.setdefault((typed, spelt), set()).update((key, h.id) for h in got[key])
        passes.append(got)
    report = [{"word": typed, "as": spelling, "hits": len(records)}
              for (typed, spelling), records in counts.items()]
    _events.info(
        "search.company.near", words=len(chosen), sources=len(names), asks=len(asks),
        hits=sum(len(records) for records in counts.values()),
        ms=round((loop.time() - started) * 1000),
    )
    return report, passes


# ── Rung 4: related records (FIND-3) ────────────────────────────────────────

async def _ask_sources(
    user: UserContext, refs: list[str], until: float, *, opening: bool,
    notes: dict[str, dict[str, Any]] | None = None,
) -> list[Any]:
    """Ask each source that can ``related`` (or ``open``, with *opening*)
    for its refs of *refs*, at once, before *until* (FIND-3).

    Each read holds one session, so it takes a slot of FIND_SESSIONS, as a
    source does (HRD-4). A source that fails, refuses or runs late gives
    nothing. It is not named in ``skipped``, and the log line holds its key
    and the error type only: no id, no address and no word. *notes* keeps the
    outcome and the time of each read, with no hit (FIND-4): the step counts
    the records it kept.
    """
    loop = asyncio.get_running_loop()
    able = [
        key for key, source in REGISTRY.items()
        if (source.open if opening else source.related) is not None
        and any(find.kind_of(r) in (source.kinds if opening else source.takes) for r in refs)
    ]
    if not able or until - loop.time() <= 0.05:
        return []

    async def one(key: str) -> list[Any]:
        async with _slots():
            started = loop.time()
            left = until - started
            if left <= 0.05:
                find.add_note(notes, key, "timed_out", ran=False)
                return []
            source = REGISTRY[key]
            try:
                got = list(await asyncio.wait_for(
                    source.open_refs(user, refs) if opening else source.related_refs(user, refs),
                    left,
                ))
            except Exception as exc:
                logger.info("search.company related skipped",
                            extra={"source": key, "error": type(exc).__name__})
                find.add_note(notes, key, _failure(exc), ms=_ms_since(loop, started))
                return []
            find.add_note(notes, key, "empty", ms=_ms_since(loop, started))
            return got

    return [item for got in await asyncio.gather(*(one(key) for key in able)) for item in got]


def _printed_chars(hit: Hit) -> int:
    """The characters that the tool prints for one related hit (§7.1)."""
    return (len(hit.title) + len(hit.hint) + len(hit.href) + len(hit.snippet)
            + len(hit.kind) + len(hit.id) + RELATED_LINE_CHARS)


def _within_budget(
    chosen: list[str], picked: dict[str, list[tuple[Hit, str, str | None]]],
    words_of: list[str],
) -> list[Hit]:
    """The related hits that fit :data:`RELATED_MAX` and
    :data:`RELATED_TOKENS`, each stamped with ``from``, ``edge`` and ``via``.

    The budget takes the first record of each hit, then the second, so each
    hit keeps a place. It stops at the first record that does not fit. The
    list keeps the order of the hits, then of the picks. Pure.
    """
    kept: set[tuple[str, int]] = set()
    chars = 0
    for rank in range(RELATED_PER_HIT):
        for ref in chosen:
            mine = picked.get(ref, [])
            if rank >= len(mine):
                continue
            cost = _printed_chars(mine[rank][0])
            if len(kept) >= RELATED_MAX or chars + cost > RELATED_TOKENS * 4:
                return _stamp_related(chosen, picked, kept, words_of)
            kept.add((ref, rank))
            chars += cost
    return _stamp_related(chosen, picked, kept, words_of)


def _stamp_related(
    chosen: list[str], picked: dict[str, list[tuple[Hit, str, str | None]]],
    kept: set[tuple[str, int]], words_of: list[str],
) -> list[Hit]:
    """The kept picks in the order of the hits, each matched by ``related``."""
    return [
        find.stamped(hit, words_of, "related", from_=ref, edge=edge, via=via)
        for ref in chosen
        for rank, (hit, edge, via) in enumerate(picked.get(ref, []))
        if (ref, rank) in kept
    ]


async def _related_step(
    user: UserContext, shown: list[Hit], words_of: list[str], *,
    since: datetime | None, until: float,
    notes: dict[str, dict[str, Any]] | None = None,
) -> list[Hit]:
    """Rung 4 (FIND-3, ``find_engine.md`` §3.1, §3.2, §4.3 and §6).

    It follows the links of the first :data:`RELATED_HITS` hits of the
    answer (*shown*) that are a deal, lead, contact, person or company:

    1. each source that can ``related`` gives the record links and the keys
       (an address, a web domain) of those hits, from stored ids;
    2. each source opens the linked records. A link to a record that no
       ``open`` returns is dropped, with its keys;
    3. Email gives the mail of the keys that are left, in the mailboxes that
       the member can read, and opens the mails;
    4. each hit keeps at most :data:`RELATED_PER_HIT` records that are not a
       hit of the answer, the answer at most :data:`RELATED_MAX` and
       :data:`RELATED_TOKENS`. A record stamped before ``after`` is dropped.

    A ref that no ``open`` returns is dropped before the cut, so it leaves no
    trace (``knowledge_graph.md`` §6). Each read stops at *until*. What
    finished answers, with no error and no claim. It never changes the hits,
    ``near`` or ``broadened``. The log line holds counts and a time only.
    *notes* keeps what each source did, and the records it gave (FIND-4).
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    chosen = find.related_chosen(shown, RELATED_HITS)
    if not chosen or until - started < BROADEN_FLOOR_S:
        return []
    links, keys = find.sort_links(
        chosen, await _ask_sources(user, chosen, until, opening=False, notes=notes))
    linked = list(dict.fromkeys(ref for owner in chosen for _e, ref in links[owner]))
    records = await _ask_sources(user, linked, until, opening=True, notes=notes)
    links, keys = find.only_opened(chosen, links, keys, {find.ref_of(h) for h in records})
    asked_keys = find.key_refs(chosen, links, keys)
    mail = find.sort_mail(asked_keys, await _ask_sources(
        user, asked_keys, until, opening=False, notes=notes))
    mails = list(dict.fromkeys(ref for key in asked_keys for _e, ref in mail.get(key, [])))
    opened = {find.ref_of(h): h for h in [
        *records, *await _ask_sources(user, mails, until, opening=True, notes=notes)]}
    picked = find.pick_related(
        chosen, {ref: find.lanes(ref, links, keys, mail) for ref in chosen}, opened,
        {find.ref_of(h) for h in shown}, RELATED_PER_HIT,
        lambda hit: since is None or _kept(hit, since),
    )
    related = _within_budget(chosen, picked, words_of)
    for app in dict.fromkeys(h.app for h in related):
        find.add_note(notes, app, "ok", hits=sum(1 for h in related if h.app == app))
    _events.info("search.company.related", hits=len(chosen), related=len(related),
                 ms=round((loop.time() - started) * 1000))
    return related


async def _add_related(
    answer: dict[str, Any], user: UserContext, shown: list[Hit], words_of: list[str], *,
    since: datetime | None, until: float,
    notes: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Put the hits of :func:`_related_step` in ``answer["related"]``. With
    none, the answer gets no ``related`` key (FIND-3)."""
    related = await _related_step(user, shown, words_of, since=since, until=until,
                                  notes=notes)
    if related:
        answer["related"] = [h.as_dict(stamp=True, find=True) for h in related]


# ── The honest zero and the explain trace (FIND-4) ──────────────────────────

@dataclass
class _Ladder:
    """What the rungs of one search did (FIND-4): the notes of each source in
    each rung (:func:`find.add_note`), the ms of each rung, and the near
    spellings that the lookup weighed. It holds counts, keys and the near
    words that the member typed, and nothing else. The route fills it on each
    path, and only the path of ``FIND_ORGS`` reads it."""

    #: The explain mode of §3.7: set only with the flag on.
    explain: bool = False
    notes: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {rung: {} for rung in find.TRACE_RUNGS})
    ms: dict[str, int] = field(default_factory=lambda: dict.fromkeys(find.TRACE_RUNGS, 0))
    weighed: list[dict[str, Any]] = field(default_factory=list)
    #: For each word of the near lookup: true when the lookup was complete
    #: and gave no name of the word itself (review round 3). No name.
    looked: dict[str, bool] = field(default_factory=dict)

    def skip_reasons(self, skipped: list[str]) -> dict[str, str]:
        """The reason of each app of the first pass's *skipped*."""
        first = self.notes[find.TRACE_RUNGS[0]]
        return {key: first[key]["outcome"] for key in skipped
                if first.get(key, {}).get("outcome") in find.SKIP_REASONS}


async def _find_tail(
    answer: dict[str, Any], user: UserContext, ladder: _Ladder, *, shown: list[Hit],
    found: dict[str, list[Hit]], words_of: list[str], skipped: list[str],
    answered: list[str], limited: bool, hybrid: bool, since: datetime | None, until: float,
    near: list[dict[str, Any]], first_zero: bool, chat_asked: bool = False,
) -> dict[str, Any]:
    """The end of an answer with ``FIND_ORGS`` on: rung 4 (FIND-3), then the
    log line, and the honest zero or the trace (FIND-4).

    *chat_asked* is true when this search asked the ``chat`` source (HRD-21).
    Only then does "Where else" leave out the line of the past chats.

    FIND-3: rung 4 runs after every other pass, so a related record never
    counts in the hits, the coverage, ``near`` or ``broadened``. The log line
    holds the hits, the ms of each rung, ``miss`` (the miss of D-FIND-4 on
    the hits of the final answer) and ``explain``, so the zero rate can leave
    out an explain call. It holds no word, no name and no id (§3.7).

    ``held`` of *near* and ``none_has_all`` use one proof
    (:func:`find.proves_absence`). It reads the text of every hit and every
    related record, so both are set after rung 4 (FIND-4 review round 3).
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    await _add_related(answer, user, shown, words_of, since=since, until=until,
                       notes=ladder.notes["related"])
    ladder.ms["related"] = _ms_since(loop, started)
    why = ladder.skip_reasons(skipped)
    miss = bool(find.missed(found, words_of))
    _events.info(
        "search.company.ladder", hits=len(shown), exact_ms=ladder.ms["exact"],
        word_ms=ladder.ms["word"], near_ms=ladder.ms["near"],
        related_ms=ladder.ms["related"], miss=miss, explain=ladder.explain,
    )
    if ladder.explain:
        return _explained(answer, user, skipped, why, hybrid, ladder)
    shown_text = find.shown_texts(found, answer.get("related"))
    if near:
        answer["near"] = find.near_report(
            near, found, answer.get("broadened"), query=answer["query"],
            first_zero=first_zero, skipped=skipped, limited=limited,
            related=answer.get("related"), looked=ladder.looked)
    answer["skipped_why"] = why
    answer["none_has_all"] = _none_has_all(
        user, answer["query"], answered, skipped, answer.get("broadened"), limited=limited,
        miss=miss, shown=shown_text, looked=ladder.looked)
    if not shown and not answer.get("related"):
        answer["where_else"] = find.where_else(
            skipped, why, chat_named=chat_asked, not_open=_not_open(user))
    return answer


def _none_has_all(user: UserContext, words: str, answered: list[str], skipped: list[str],
                  broadened: list[dict[str, Any]] | None, *, limited: bool, miss: bool,
                  shown: list[str], looked: dict[str, bool] | None) -> bool:
    """True only with proof that no record the member can open holds all the
    words (``find_engine.md`` §3.6, FIND-4).

    The proof is :func:`find.word_absent`: one word, searched alone in each
    app that answered, found nothing, nothing was skipped or limited, and the
    word passes :func:`find.proves_absence`. That is, the member typed it as
    it folds, no hit and no related record shows it (*shown*), and the near
    lookup gave no name of the word itself (*looked*). An app that is open to
    the member must have answered, and the query has no operator. A low
    ``covers`` is no proof, and a first pass with no hit is no proof either,
    because People matches the whole query as one phrase.

    The answer must also *miss* (FIND-4 review, P1): no hit shows every word.
    """
    return bool(
        miss and bare_words(words) is not None
        and any(REGISTRY[key].reaches(user) for key in answered)
        and find.word_absent(broadened, query=words, skipped=skipped, limited=limited,
                             shown=shown, looked=looked)
    )


def _not_open(user: UserContext) -> list[str]:
    """The apps that are not open to the member, for "Where else" (FIND-4).

    A source that was never open (a dark ``studio``, :func:`_never_open`) is
    not named, so a member who holds ``feature:studio`` reads the same lines
    as one who does not. The past chats have their own line (``past_chats``,
    HRD-21), so ``chat`` is never named here."""
    return [key for key in REGISTRY
            if key != find.CHAT_KEY and not REGISTRY[key].reaches(user)
            and not _never_open(user, key)]


#: The most characters of ``current_chat`` (HRD-21): the cap of the chat
#: tool's own read of its thread (``acb_skills.company_search_tools``).
CURRENT_CHAT_MAX = 200


def _chat_current(user: UserContext, current_chat: str | None) -> str | None:
    """The id of the chat that the search runs in, when the route asks the
    ``chat`` source, else ``None`` (HRD-21, ``find_engine.md`` §6, D-FIND-5).

    The route asks the source only when its switch is open for the member
    (``Source.reaches``: ``feature:chat`` and ``CHAT_SEARCH_ORGS``) and the
    request names the current chat, in at most :data:`CURRENT_CHAT_MAX`
    characters. So a closed switch reads no value of the parameter, and with
    no current chat no past chat shows (fail closed). The id only takes a
    chat away: it never names a chat to read, a member or an org.
    """
    source = REGISTRY.get(find.CHAT_KEY)
    if source is None or not source.reaches(user):
        return None
    here = (current_chat or "").strip()
    if not here or len(here) > CURRENT_CHAT_MAX:
        return None
    return here


def _options(recency: bool, since: datetime | None, current: str | None) -> dict[str, Any]:
    """The options of each ask. Off, none, as before CX-1. *current* goes to
    the ``chat`` source only when the route asks it (HRD-21). A source that
    does not declare an option ignores it. Pure."""
    options: dict[str, Any] = {"after": since, "reviewed": True, "vectors": True} if recency else {}
    if current is not None:
        options["current"] = current
    return options


def _rejoined(found: dict[str, list[Hit]], past: list[Hit] | None) -> dict[str, list[Hit]]:
    """*found* with the past chats of pass 1 again, when the route asked them
    (HRD-21). ``None`` leaves *found* as it is. Pure."""
    if past is None:
        return found
    return {**found, find.CHAT_KEY: past}


def _flags_off(user: UserContext, hybrid: bool) -> list[str]:
    """The names of ``find.FLAGS`` that are off for the member's org, from
    each flag's own reader (§3.7). *hybrid* is the answer of
    :func:`_meaning_allowed`."""
    on = {
        "KNOWLEDGE_EMBED": hybrid,
        "EMAIL_SEMANTIC_SEARCH_ENABLED": email_meaning_on(),
        "CHAT_SEARCH_ORGS": find.chat_search_named(user.organization_id),
    }
    return [name for name in find.FLAGS if not on[name]]


def _explained(answer: dict[str, Any], user: UserContext, skipped: list[str],
               why: dict[str, str], hybrid: bool, ladder: _Ladder) -> dict[str, Any]:
    """The answer of the explain mode (``find_engine.md`` §3.7, FIND-4).

    No hit and no related record: the trace takes their place. The trace
    holds a row for each rung and source, the near spellings that the lookup
    weighed, the outcome of the embed and the flags that are off. It holds no
    title, no snippet, no id, no link and no word of the query but the near
    words that the member typed.
    """
    closed = {key for key in REGISTRY if not REGISTRY[key].reaches(user)}
    return {
        "query": answer["query"],
        "hits": [],
        "searched": answer["searched"],
        "skipped": skipped,
        "skipped_why": why,
        "meaning": answer["meaning"],
        "trace": {
            "rungs": find.trace_rows(ladder.notes, list(REGISTRY), closed),
            "near": ladder.weighed,
            "meaning": answer["meaning"],
            "flags_off": _flags_off(user, hybrid),
        },
    }


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
    # FIND-4. A plain default, read only with FIND_ORGS on. Set, each rung
    # runs, and the answer is the trace of §3.7 with no hit.
    explain: bool = False,
    # HRD-21. A plain default, read only when the `chat` source is open for
    # the member: the id of the chat that the search runs in. It only takes
    # that chat away from the past chats (D-FIND-5).
    current_chat: str | None = None,
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
    # HRD-21: the past chats are asked only with their switch open and the
    # current chat named. Else the source is not asked at all, so the answer
    # is the one of before HRD-21, byte for byte.
    current = _chat_current(user, current_chat)
    # An app the member does not hold is not asked, and it is not "skipped":
    # the member has no such app. Each source checks the feature again.
    order = [key for key in _order(apps) if holds(user, REGISTRY[key].feature)
             and (key != find.CHAT_KEY or current is not None)]
    hybrid = _meaning_allowed(user)
    recency = recency_enabled()
    # Off, `after` is not read at all, as before CX-1.
    since = _after(after) if recency else None
    options = _options(recency, since, current)
    if recency:
        turn_vector.begin_turn()
    only_verified = bool(recency and verified)
    if only_verified:
        options["verified_only"] = True
    # FIND-2: with the flag on, each hit says how it matched. It acts only with
    # CONTEXT_RECENCY on, so the flag-off answer below is untouched.
    find_on = bool(recency and find.find_allowed(user.organization_id))
    # FIND-4: `explain` is read only with the flag on. The notes of the rungs
    # reach the answer and the log line only with the flag on.
    ladder = _Ladder(explain=bool(find_on and explain))

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
            since=since, deadline=deadline, timed=recency, notes=ladder.notes["exact"],
        )
    finally:
        if recency:
            meaning = turn_vector.outcome()
            if meaning != turn_vector.MEANING_OFF:
                _events.info("search.company.embed", ms=turn_vector.embed_ms(),
                             outcome=meaning)
            turn_vector.end_turn()
    ladder.ms["exact"] = _ms_since(loop, began)

    # A filtered search that finds nothing says nothing about a gap (KB-AI-4).
    # An explain call asks the same words again for its trace, so it gets no
    # task list and records no second gap (FIND-4).
    _note_gap(None if ladder.explain else background, user, words,
              None if only_verified else knowledge_hits)

    words_of = find.content_words(words) if find_on else None
    if words_of is not None:
        # HRD-21: a past chat matched by rung 6, `chat`, and every other
        # pass-1 hit by rung 1. A stamp keeps its lower rung, so this comes first.
        found = {key: [find.stamped(h, words_of, find.first_rung(key)) for h in hits]
                 for key, hits in found.items()}
    if not recency:
        return {
            "query": words,
            "hits": [h.as_dict() for h in _interleave(order, found)],
            "skipped": skipped,
        }
    # HRD-21: rung 6 answers in pass 1 only (`find_engine.md` §3.1). The past
    # chats take no part in the miss rule, the words alone or the near step,
    # so those see the records as before. The chats join the merge below.
    past = found.pop(find.CHAT_KEY, None)
    # KR-ZERO: every app that answered found nothing. Ask again, each word alone.
    # With FIND_ORGS on, the miss of D-FIND-4 (§3.3): the best hit holds fewer
    # than all the content words, so a hit of 1 word of 3 asks again too.
    broadened: list[dict[str, Any]] | None = None
    first_zero = not any(found.values())
    #: The apps that answered the first pass (FIND-4).
    answered = list(found)
    # FIND-4: the explain mode runs each rung, with no early stop.
    miss = _missed(found, words_of) or ladder.explain
    alone = alone_words(words) if miss else []
    if words_of is not None and alone:
        # The fold of §3.3: "Sharath's" is searched as "sharath".
        alone = words_of
    until = min(loop.time() + BROADEN_BUDGET_S, began + ANSWER_CAP_S)
    # HRD-18b: rung 3 on a miss, with FIND_ORGS on. Its asks run before the
    # words alone, and share the time of the second pass with them.
    started = loop.time()
    near, near_passes = await _near_step(
        user, words, words_of, order, found, per_app, options=options,
        since=since, until=until, after_asks=len(alone), miss=miss,
        notes=ladder.notes["near"], weighed=ladder.weighed, looked=ladder.looked,
    )
    ladder.ms["near"] = _ms_since(loop, started)
    if alone and until - loop.time() >= BROADEN_FLOOR_S:
        started = loop.time()
        broadened, wide = await _broaden(
            user, alone, list(found), per_app, options=options, since=since, until=until,
            tag=words_of, notes=ladder.notes["word"],
        )
        # Off, the words alone answer, as in KR-ZERO. On, a first pass that
        # missed may hold hits, and they stay, each at its lower rung.
        found = _keep_first(found, wide, words_of, order, per_app)
        _events.info(
            "search.company.broaden", words=len(alone),
            hits=sum(len(hits) for hits in wide.values()),
            ms=round((loop.time() - started) * 1000),
        )
        ladder.ms["word"] = _ms_since(loop, started)
    found = _rejoined(_merge_near(found, near_passes, order, per_app), past)
    now = datetime.now(UTC)
    shown = by_fusion(order, found, now, covered=words_of is not None)
    answer: dict[str, Any] = {
        "query": words,
        "hits": [h.as_dict(stamp=True, find=words_of is not None) for h in shown],
        "searched": [key for key in order if key in found and REGISTRY[key].reaches(user)],
        "skipped": skipped,
        "meaning": meaning,
    }
    if broadened is not None:
        answer["broadened"] = broadened
    limited = since is not None or only_verified
    if near:
        # The key keeps its place. `_find_tail` sets `held` after rung 4, so a
        # related record that shows a word counts too (FIND-4 review round 3).
        answer["near"] = near
    if words_of is None:
        return answer
    return await _find_tail(
        answer, user, ladder, shown=shown, found=found, words_of=words_of, skipped=skipped,
        answered=answered, limited=limited, hybrid=hybrid, since=since,
        until=min(loop.time() + RELATED_BUDGET_S, began + ANSWER_CAP_S),
        near=near, first_zero=first_zero, chat_asked=current is not None,
    )
