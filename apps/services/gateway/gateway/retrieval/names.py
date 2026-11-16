"""GET /search/names — one name in every app (WS-FIND FIND-5, ``find_engine.md`` §5.2).

The route of the core tool ``resolve_name``. It asks each source of
:data:`gateway.retrieval.sources.REGISTRY` that has the ``named`` ability for
the records that hold one name: a person of the People directory, a contact,
lead or company of the CRM, and a sender of the member's mail. No chat model
runs, and no module here writes SQL.

* **Behind ``FIND_ORGS``.** An org that the flag does not name gets 404,
  before any check of the input, and no source is asked.
* **The member comes from the session** (R5, R11). The route takes ``name``
  and ``kinds`` and nothing else. Each source passes this request's member to
  its app, and asks nothing of an app that the member does not reach.
* **The lookups of rung 3** (HRD-18b). Each source runs two reads at once:
  ``Source.named_records`` with the name, which calls the app's own search,
  and ``Source.near_names``, the near lookup of rung 3. Then it asks
  ``named_records`` once more, with each word that has a near spelling
  replaced by the source's best spelling (``name_match.best_spelling``). So a
  near name is the same in the ladder and here.
* **The name test is code** (:func:`holds`). A record is ``exact`` when its
  stored name holds each word of the name, one stored word for each, and
  ``near`` when it holds each word of the near spelling. A record that the
  search matched by another field, such as an address or a linked contact,
  gives no row. People and Email match one phrase (``Source.names_word``), so
  they get one word, the longest, and the name test checks the rest.
* **One deadline, four slots.** Every read of every source stops at
  :data:`NAMES_BUDGET_S` from the start, and each read takes a slot of
  ``company.FIND_SESSIONS``. A source whose name read fails, refuses or runs
  late gives no row, and ``skipped`` names it with a reason of
  :data:`REASONS`, never with the text of the error. A source whose near read
  fails keeps its exact rows, and ``near_skipped`` names it.
* **The verdict** is one of :data:`VERDICTS` (:func:`verdict_of`). ``one``:
  one exact record that is no mail sender, no near row, and every app
  answered in full. ``partial``: the same, but an app did not answer in full.
  ``several``: two exact records or more, or one beside near rows. ``near``:
  near rows only, or a mail sender alone. ``none``: no row. ``full`` names
  each app whose page was full.
* **Different records stay different rows.** The lead, the contact and the
  sender of one person are three rows. At most :data:`ROWS_PER_KIND` rows of
  each kind and :data:`ROWS_MAX` in all, the exact rows first. ``cut`` is
  true when a cap left a row out.
* **Logs hold counts and a time only**: no name, no address and no id.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger, name_match
from acb_skills.hidden_text import strip_hidden
from fastapi import APIRouter, Depends, HTTPException

from gateway.retrieval import company, find, sources
from gateway.retrieval.sources import Named

logger = logging.getLogger(__name__)
#: The house logger, so the line reaches the journal. It holds no word.
_events = get_logger("gateway.retrieval.names")

router = APIRouter(prefix="/search", tags=["search"])

#: Seconds for every read of every source, together (``find_engine.md`` §7).
NAMES_BUDGET_S = 1.0
#: The records that one read asks an app for. A full page may hide a record.
NAMES_POOL = 20
#: The most rows of one kind, and of one answer.
ROWS_PER_KIND = 5
ROWS_MAX = 15
#: The length of the name, after the spaces are folded.
NAME_MIN = 2
NAME_MAX = 60
#: The most characters of a stored name in a row.
NAME_CHARS = 80
#: The most characters of ``kinds``.
KINDS_MAX_CHARS = 100

#: The kinds of record that hold a name, in the order of the rows. The CRM
#: gives contact, lead and company, People gives person, and Email gives
#: sender. No source gives a member yet (``resolve_person`` reads them).
KINDS: tuple[str, ...] = ("person", "contact", "lead", "company", "sender")
#: How a row matched.
MATCHES: tuple[str, ...] = ("exact", "near")
#: The verdicts of an answer.
VERDICTS: tuple[str, ...] = ("one", "partial", "several", "near", "none")
#: Why a source gave no row: it ran out of time, it failed, or the app refused
#: the member (a 401 or a 403, such as the CRM access rule).
REASONS: tuple[str, ...] = ("timed_out", "failed", "refused")
#: The statuses of an app's refusal of the member. Any other status of an
#: ``HTTPException`` (a query that the app does not take) is ``failed``.
REFUSED_STATUSES = frozenset({401, 403})
#: The statuses of a person that a row may say: the closed list of the People
#: directory (``tasks.core.PEOPLE_STATUSES``). The fence holds the two equal.
#: A copy, because this package imports no app module at load.
PERSON_STATUSES: tuple[str, ...] = ("active", "contractor", "alumni", "invited")

#: The 422 of a name out of bounds. Fixed text: it never holds the input.
NAME_RULE = (f"Give one name of {NAME_MIN} to {NAME_MAX} characters and at most "
             f"{name_match.NEAR_MAX_WORDS} words.")
#: The 422 of an unknown kind. Fixed text: it never holds the input.
KINDS_RULE = f"The kinds are {', '.join(KINDS[:-1])} and {KINDS[-1]}."

#: One token of a name: letters and digits.
_TOKEN = re.compile(r"[^\W_]+")


def find_allowed(user: UserContext) -> bool:
    """True when ``FIND_ORGS`` names the member's org: ``find.find_allowed``,
    the one reader. The org comes from the session (R5)."""
    return find.find_allowed(user.organization_id)


def typed_name(raw: Any) -> str:
    """The name with its spaces folded, or a 422 of :data:`NAME_RULE`.

    One name: :data:`NAME_MIN` to :data:`NAME_MAX` characters, no control
    character, and 1 to ``name_match.NEAR_MAX_WORDS`` words of at most
    ``name_match.NEAR_MAX_CHARS`` characters each. Pure."""
    words = " ".join(str(raw or "").split())
    asked = name_match.words(words)
    if (not NAME_MIN <= len(words) <= NAME_MAX
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in words)
            or not 1 <= len(asked) <= name_match.NEAR_MAX_WORDS
            or any(len(w) > name_match.NEAR_MAX_CHARS for w in asked)):
        raise HTTPException(status_code=422, detail=NAME_RULE)
    return words


def wanted_kinds(raw: str | None) -> tuple[str, ...]:
    """The kinds of a comma list, in the order of :data:`KINDS`, or every
    kind. An unknown kind is a 422 of :data:`KINDS_RULE`. Pure."""
    text = str(raw or "")
    named = [k.strip().lower() for k in text.split(",") if k.strip()]
    if len(text) > KINDS_MAX_CHARS or any(k not in KINDS for k in named):
        raise HTTPException(status_code=422, detail=KINDS_RULE)
    return tuple(k for k in KINDS if k in named) if named else KINDS


def record_words(record: Named) -> list[str]:
    """The words of a record's stored name, then each word of its
    :attr:`Named.also` that the name does not hold already. So a sender
    «Sharath» <sharath@…> has ONE word ``sharath``, never two."""
    words = name_match.words(record.name)
    return [*words, *(w for w in name_match.words(record.also) if w not in words)]


def holds(words: list[str], asked: list[str]) -> bool:
    """True when *words* hold each word of *asked*, ONE stored word for each
    asked word (§5.2, fix round P1). A full word takes a stored word that is
    the same word or its plural (``name_match.same_word``). Then each word of
    one letter takes a stored word that starts with it, of those left. A
    stored word serves one asked word only, so "Sharath S" is no exact match
    for «Sharath», and "Sharath Sharath" is none for one «Sharath». A score
    never decides it: «Sharat» and "Sharath" fold to one word. An ask of
    initials only ("S K") holds nothing: an initial is no name. Pure."""
    if not any(len(word) > 1 for word in asked):
        return False
    left = list(words)
    # The full words first, so an initial never takes the word of a full one.
    for word in sorted(asked, key=lambda w: len(w) == 1):
        if len(word) == 1:
            place = next((i for i, w in enumerate(left) if w.startswith(word)), None)
        else:
            place = next((i for i, w in enumerate(left) if name_match.same_word(word, w)), None)
        if place is None:
            return False
        left.pop(place)
    return True


def best_spellings(asked: list[str], names: Any) -> dict[str, str]:
    """For each word of *asked* with a near spelling among *names* (one
    source's near lookup), that spelling as the record spells it: one token.
    ``name_match.best_spelling`` picks it, so a spelling that is the word
    itself never counts. Pure."""
    found = names if isinstance(names, dict) else {}
    out: dict[str, str] = {}
    for word in asked:
        best = name_match.best_spelling(word, list(found.get(word) or []))
        if best is not None and len(name_match.words(best.spelling)) == 1:
            out[word] = str(best.spelling)
    return out


def respelled(typed: str, spellings: dict[str, str]) -> str:
    """*typed* with each token of a word of *spellings* replaced by its
    spelling. "Sharath S" with «Sharat» gives "Sharat S". Pure."""
    def swap(token: re.Match[str]) -> str:
        word = name_match.words(token.group(0))
        return spellings.get(word[0], token.group(0)) if len(word) == 1 else token.group(0)

    return _TOKEN.sub(swap, typed)


def stored_name(value: Any) -> str:
    """A stored name for a row: no hidden character (``hidden_text``, the one
    rule), one space between words, and at most :data:`NAME_CHARS`."""
    return " ".join(strip_hidden(str(value or "")).split())[:NAME_CHARS].rstrip()


def spelled_as(record: Named, spellings: dict[str, str]) -> str:
    """The near spellings that *record* holds, each as the record spells it:
    tokens of letters and digits, with one space between them."""
    text = f"{record.name} {record.also}"
    out: list[str] = []
    for spelling in spellings.values():
        word = name_match.words(spelling)
        token = find.asked_form(text, word[0]) if len(word) == 1 else ""
        if token and _TOKEN.fullmatch(token) and token not in out:
            out.append(token)
    return " ".join(out)


def longest_token(texts: list[str]) -> str:
    """The longest token of letters and digits in *texts*, the first on a tie.
    An app that matches one phrase (``Source.names_word``) is asked with it.
    Pure."""
    tokens = [t for text in texts for t in _TOKEN.findall(text)]
    return max(tokens, key=len) if tokens else ""


def reason_of(exc: BaseException) -> str:
    """The closed reason of a failed read: the app's refusal of the member,
    the deadline or any other fault."""
    if isinstance(exc, HTTPException) and exc.status_code in REFUSED_STATUSES:
        return "refused"
    if isinstance(exc, TimeoutError):
        return "timed_out"
    return "failed"


async def _timed[T](make: Callable[[], Awaitable[T]], deadline: float) -> T:
    """One read, inside a slot of ``company.FIND_SESSIONS`` and before
    *deadline*. No time left in the slot: ``TimeoutError``, and no call."""
    loop = asyncio.get_running_loop()
    async with company._slots():
        left = deadline - loop.time()
        if left <= 0.05:
            raise TimeoutError
        return await asyncio.wait_for(make(), left)


@dataclass
class SourceAnswer:
    """What one source gave: its exact records, its near records with the
    spellings that they hold, and why a read failed."""

    key: str
    exact: list[Named] = field(default_factory=list)
    near: list[tuple[Named, str]] = field(default_factory=list)
    #: True when the name read or the near read gave a full page, so more
    #: records may match.
    more: bool = False
    #: A reason of :data:`REASONS` when the name read failed. The source gives
    #: no row then.
    skipped: str | None = None
    #: A reason when the near lookup or the near read failed.
    near_skipped: str | None = None


def _note(key: str, part: str, exc: BaseException) -> None:
    """The log line of a failed read: the source, the part and the error
    TYPE. A database error's text can hold the name."""
    logger.info("search.names read skipped",
                extra={"source": key, "part": part, "error": type(exc).__name__})


async def ask_source(user: UserContext, key: str, typed: str, deadline: float) -> SourceAnswer:
    """The name read and the near lookup of one source at once, then the near
    read with its best spellings. Each read stops at *deadline*.

    An app that matches its text as ONE phrase (``Source.names_word``: People
    and Email) gets one word: the longest word of the name, and for the near
    read the longest near spelling. :func:`holds` then checks every word. So
    "Kumar Sharath" finds «Sharath Kumar» there too (fix round, P2-a). The
    CRM matches word by word, so it gets the whole name."""
    source = sources.REGISTRY[key]
    asked = name_match.words(typed)
    eligible = [w for w in asked if name_match.near_eligible(w)]
    exact_query = longest_token([typed]) if source.names_word else typed

    async def lookup() -> Any:
        if not eligible or source.near is None:
            return {}
        return await _timed(
            lambda: source.near_names(user, eligible, company.NEAR_NAMES_K), deadline)

    got = await asyncio.gather(
        _timed(lambda: source.named_records(user, exact_query, NAMES_POOL), deadline),
        lookup(), return_exceptions=True,
    )
    first, names = got
    for item in got:
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            raise item  # a cancel ends the request
    if isinstance(first, BaseException):
        _note(key, "name", first)
        return SourceAnswer(key, skipped=reason_of(first))
    records, more = first
    answer = SourceAnswer(key, exact=[r for r in records if holds(record_words(r), asked)],
                          more=bool(more))
    if isinstance(names, BaseException):
        _note(key, "near", names)
        answer.near_skipped = reason_of(names)
        return answer
    spellings = best_spellings(eligible, names)
    query = respelled(typed, spellings)
    if not spellings or query == typed:
        return answer
    if source.names_word:
        query = longest_token(list(spellings.values()))
    spelled = [name_match.words(spellings[w])[0] if w in spellings else w for w in asked]
    try:
        again, near_more = await _timed(
            lambda: source.named_records(user, query, NAMES_POOL), deadline)
    except Exception as exc:
        _note(key, "near", exc)
        answer.near_skipped = reason_of(exc)
        return answer
    # A full page of the near read may hide a near record too, so the app
    # reads as "full", and one exact record is no sure `one`.
    answer.more = answer.more or bool(near_more)
    for record in again:
        held = record_words(record)
        if holds(held, spelled) and not holds(held, asked):
            answer.near.append((record, spelled_as(record, spellings)))
    return answer


def _row(key: str, record: Named, match: str, spelt: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "app": key, "kind": record.kind, "id": record.id,
        "name": stored_name(record.name) or "(no name)", "href": record.href, "match": match,
    }
    if spelt:
        row["as"] = spelt
    if record.kind == "person" and record.state in PERSON_STATUSES:
        # Fix round, P3-c: the directory shows each status, and FIND-3's
        # `open` shows active people only. The row says which.
        row["status"] = record.state
    return row


def rows_of(answers: list[SourceAnswer], kinds: tuple[str, ...]) -> tuple[list[dict[str, Any]], int]:
    """Every row of *answers* of the kinds of *kinds*, the exact rows first,
    then by kind, then in the order of the sources. One row for each record.
    It also gives the count of exact records. Pure."""
    exact: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for answer in answers:
        for record in answer.exact:
            key = (answer.key, record.kind, record.id)
            if record.kind in kinds and key not in seen:
                seen.add(key)
                exact.append(_row(answer.key, record, "exact"))
    for answer in answers:
        for record, spelt in answer.near:
            key = (answer.key, record.kind, record.id)
            if record.kind in kinds and key not in seen:
                seen.add(key)
                near.append(_row(answer.key, record, "near", spelt))

    def place(row: dict[str, Any]) -> int:
        return KINDS.index(row["kind"]) if row["kind"] in KINDS else len(KINDS)

    # `sorted` is stable, so a tie keeps the order of the sources.
    return sorted(exact, key=place) + sorted(near, key=place), len(exact)


def capped(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """At most :data:`ROWS_PER_KIND` rows of each kind and :data:`ROWS_MAX` in
    all, in order. Pure."""
    out: list[dict[str, Any]] = []
    per: dict[str, int] = {}
    for row in rows:
        if len(out) >= ROWS_MAX:
            break
        if per.get(row["kind"], 0) >= ROWS_PER_KIND:
            continue
        per[row["kind"]] = per.get(row["kind"], 0) + 1
        out.append(row)
    return out


def verdict_of(exact: int, near: int, *, complete: bool, sure: bool) -> str:
    """The verdict of :data:`VERDICTS` (fix round, P2-c). Pure.

    *exact* counts the exact records and *near* the near rows. *complete* is
    False when an app was skipped, gave a full page, did not try its near
    spellings, or a cap cut a row. *sure* is False when the one exact record
    is a mail sender: a sender writes its own name, so it is no known person.

    ``one`` only for ONE exact record, no near row, *sure* and *complete*.
    One exact record beside near rows is ``several``: the near «Sharat» may
    be the man the member means (``find_engine.md`` §1.1). A sender alone is
    ``near``: no sure match."""
    if exact == 0:
        return "near" if near else "none"
    if exact > 1 or near:
        return "several"
    if not sure:
        return "near"
    return "one" if complete else "partial"


@router.get("/names")
async def resolve_names(
    # Plain defaults: FastAPI checks nothing on them, so an org off FIND_ORGS
    # gets 404 before any check of the input.
    name: str = "",
    kinds: str | None = None,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The records that hold one name, in every app this member reaches."""
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not find_allowed(user):
        raise HTTPException(status_code=404, detail="Not Found")
    typed = typed_name(name)
    wanted = wanted_kinds(kinds)
    # An app the member does not reach is not asked, and it is not "skipped":
    # the member has no such app, or its switch is closed.
    asked = [
        key for key, source in sources.REGISTRY.items()
        if source.named is not None and set(source.names) & set(wanted) and source.reaches(user)
    ]
    loop = asyncio.get_running_loop()
    began = loop.time()
    deadline = began + NAMES_BUDGET_S
    answers = list(await asyncio.gather(*(ask_source(user, key, typed, deadline) for key in asked)))
    every, exact = rows_of(answers, wanted)
    rows = capped(every)
    skipped = [{"app": a.key, "reason": a.skipped} for a in answers if a.skipped]
    near_skipped = [{"app": a.key, "reason": a.near_skipped} for a in answers
                    if a.near_skipped and not a.skipped]
    full = [a.key for a in answers if a.more and not a.skipped]
    complete = not (skipped or near_skipped or full or len(rows) < len(every))
    sure = any(r["match"] == "exact" and r["kind"] != "sender" for r in every)
    verdict = verdict_of(exact, len(every) - exact, complete=complete, sure=sure)
    _events.info(
        "search.names", sources=len(asked), rows=len(rows), exact=exact,
        near=len(every) - exact, skipped=len(skipped), near_skipped=len(near_skipped),
        ms=round((loop.time() - began) * 1000),
    )
    return {
        "name": typed,
        "kinds": list(wanted),
        "verdict": verdict,
        "rows": rows,
        "searched": [a.key for a in answers if not a.skipped],
        "skipped": skipped,
        "near_skipped": near_skipped,
        # Fix round, P3-b: each app whose name read or near read gave a full
        # page. A record past the page may hold the name, so a zero there
        # proves nothing.
        "full": full,
        "cut": len(rows) < len(every),
    }
