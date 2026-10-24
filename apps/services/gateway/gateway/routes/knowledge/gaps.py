"""Knowledge gaps: the questions that Knowledge could not answer (WS-61 KB-9a).

Spec: ``project-docs/specs/knowledge_base.md`` §8.4 door 3, §9, §12 and §13.

    GET  /knowledge/gaps?status=&limit=        the gaps the caller may see
    POST /knowledge/gaps/{id}/dismiss          close a gap (knowledge:manage)
    POST /knowledge/gaps/{id}/answered         close a gap with a page {page_id}
    POST /knowledge/gaps/{id}/reopen           undo a dismiss (knowledge:manage)

**The write.** ``GET /search/company`` (``gateway/retrieval/company.py``)
calls :func:`schedule_gap` after it asked every source. When Knowledge
answered with zero hits, the gate is open and the question passes the skip
rules, :func:`schedule_gap` adds :func:`record_gap` to the route's
``BackgroundTasks``. So the search answers first, and the write runs after
it, in its own tenant session through the one seam. :func:`record_gap`
never raises. No model runs.

* **The gate.** ``KNOWLEDGE_GAPS`` names the org (``flags.knowledge_gaps_allowed``),
  and the caller is a member: an email address with an ``@`` and an org. A
  service call (``system:internal``) records nothing.
* **The skip rules** (:func:`skip_reason`). Fewer than :data:`MIN_WORDS`
  words, a URL, an email address, a uuid, or a question in which more than
  half of the words hold a digit.
* **The stored text** (:func:`mask_text`). The question is masked BEFORE it
  is normalised and cut, so ``norm_query`` and ``sample_query`` both hold the
  masked text. An email address, a phone number, an amount and a long number
  become ``[email]``, ``[phone]``, ``[amount]`` and ``[number]``. So two
  questions that differ only in a figure are one gap.
* **The normal form** (:func:`normalize`). NFKC, lower case, each character
  that is not a letter, a mark or a digit as a space (a vowel sign is a
  mark, so Hindi and Tamil words stay whole), the spaces folded, the ends cut.
* **The window.** ``ask_count`` counts the different askers of one window.
  A window runs :data:`WINDOW_DAYS` days from its FIRST ask. An ask after
  that starts a new window with one asker, however recent the last ask is.
  ``askers`` keeps :data:`MAX_ASKERS` addresses. When it is full, the count
  stops at :data:`MAX_ASKERS` too, so the count never exceeds the list.
* **Reopen.** A gap that reads ``answered`` whose page is gone
  (``answered_page_id`` is NULL after the page delete) opens again at the
  next ask. A ``dismissed`` gap stays dismissed, by design. A manager
  reopens it with ``POST /gaps/{id}/reopen``.

**Who sees a gap.** A holder of ``knowledge:manage`` sees each gap of the
org. A member sees the gaps that the member asked. A gap stores no space,
so the ``kb_gap`` card goes to a holder of ``knowledge:manage`` only
(:func:`my_day_rows`). No answer carries ``askers``.

**How a gap closes.** Dismiss needs ``knowledge:manage``. "I wrote it" needs
a gap the caller sees, and a live page that the caller sees and can write
(``core.load_page`` and ``core.space_level``). A publish closes no gap.

The tenant and the member come from the session (R5, R11). Each statement
also names the org, so a session with row level security off still reaches
one organization only. Fences: ``tests/unit/test_kb_gaps.py`` and, against a
real database, ``tests/unit/test_kb_gaps_r8.py``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import quote

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from gateway.routes.knowledge import core, flags
from gateway.routes.knowledge.core import router
from pydantic import BaseModel, Field
from sqlalchemy import text

_log = get_logger("gateway.knowledge.gaps")

#: A question with fewer words is too short to be a gap.
MIN_WORDS = 3
#: The longest normal form and the longest stored wording, in characters.
MAX_QUERY = 300
#: The most addresses ``askers`` keeps. The CHECK of the migration holds it too.
MAX_ASKERS = 20
#: The window of ``ask_count``, and the age of a card's last ask, in days.
WINDOW_DAYS = 30
#: The askers that make a gap a My Day card.
CARD_ASKERS = 2
#: The rows of one list, by default and at most.
DEFAULT_LIMIT, MAX_LIMIT = 50, 200
#: The statuses of a gap. The CHECK of the migration holds the same.
STATUSES: tuple[str, ...] = ("open", "answered", "dismissed")

_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|in|io|org|net|dev|ai|co)(?:/\S*)?\b",
                  re.IGNORECASE)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_UUID = re.compile(r"\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b",
                   re.IGNORECASE)
_AMOUNT = re.compile(
    r"(?:[$\u20b9\u20ac\u00a3]|\b(?:rs|inr|usd|eur|gbp)\b\.?)\s*\d[\d,.]*"
    r"|\b\d[\d,.]*\s*(?:rs|inr|usd|eur|gbp|rupees|dollars)\b",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<![\w.])\+?\d(?:[ ()-]?\d){8,}(?!\w)")
_LONG_NUMBER = re.compile(r"\d{5,}")
_SPACES = re.compile(r"\s+")

#: The Gaps view of the Knowledge app.
GAPS_HREF = "/knowledge/gaps"


def write_href(gap_id: str) -> str:
    """The link that opens the Gaps view with the New page form for one gap.

    The ONE builder of this link: the ``kb_gap`` card of My Day calls it.
    The page reads ``write`` back in ``app/knowledge/lib/gaps.ts``.
    """
    return f"{GAPS_HREF}?write={quote(str(gap_id), safe='')}"


_GAP_COLUMNS = (
    "g.id, g.sample_query, g.ask_count, g.first_asked_at, g.last_asked_at, "
    "g.status, g.answered_page_id, g.dismissed_by, g.dismissed_at"
)


# ── The normal form and the skip rules (pure) ───────────────────────────────

def mask_text(question: str | None) -> str:
    """The question with its personal figures masked, for the stored text.

    An email address becomes ``[email]``, an amount (a currency sign or word
    beside a figure) ``[amount]``, a phone number ``[phone]`` and any other
    run of five digits or more ``[number]``. Short figures such as a year
    stay. The order matters: an amount first, so ``Rs 85,000`` is one amount.
    CX-3 may later replace this function with its shared mask.
    """
    out = str(question or "")
    out = _EMAIL.sub("[email]", out)
    out = _AMOUNT.sub("[amount]", out)
    # A run with a space, bracket or dash in it is a phone number. A run
    # with none is a plain number, and a short run stays.
    out = _PHONE.sub(lambda m: "[phone]" if re.search(r"\D", m.group().lstrip("+"))
                     else "[number]", out)
    return _LONG_NUMBER.sub("[number]", out)


def normalize(question: str | None) -> str:
    """The normal form of a question: one rule, so one question is one gap.

    NFKC, then lower case. A character stays when its Unicode category is a
    letter (L), a number (N) or a mark (M). Every other character becomes a
    space. The marks matter: a vowel sign of Hindi or Tamil is a mark, and
    without it two different words read as one. The spaces fold to one, and
    the ends are cut. At most :data:`MAX_QUERY` characters, cut at a word.
    """
    folded = unicodedata.normalize("NFKC", str(question or "")).lower()
    kept = "".join(
        ch if unicodedata.category(ch)[0] in "LNM" else " " for ch in folded
    )
    flat = _SPACES.sub(" ", kept).strip()
    if len(flat) <= MAX_QUERY:
        return flat
    cut = flat[:MAX_QUERY]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


def skip_reason(question: str | None) -> str | None:
    """Why a question is no gap, or ``None`` when it is one.

    The rules read the RAW words for a URL, an email address and a uuid,
    because the normal form takes their marks away. The word count reads the
    normal form, and the digit rule reads the words as typed.
    """
    raw = str(question or "")
    if _EMAIL.search(raw):
        return "email"
    if _URL.search(raw):
        return "url"
    if _UUID.search(raw):
        return "id"
    if len(normalize(raw).split()) < MIN_WORDS:
        return "short"
    # The words as typed, so "PO-4411" stays one word that holds a digit.
    typed = raw.split()
    if sum(1 for w in typed if any(c.isdigit() for c in w)) * 2 > len(typed):
        return "number"
    return None


def sample_of(question: str | None) -> str:
    """The first wording, on one line, at most :data:`MAX_QUERY` characters."""
    flat = _SPACES.sub(" ", str(question or "")).strip()
    return flat[:MAX_QUERY]


def is_member(user: UserContext) -> bool:
    """True when *user* is a person of an org: an address and a tenant.

    ``system:internal`` and an agent role are not members. So a service call
    or a batch writes no gap.
    """
    email = core.viewer_email(user)
    if not email or "@" not in email or email.startswith("system:"):
        return False
    if getattr(user, "is_agent", False):
        return False
    return bool(str(user.organization_id or "").strip())


def gap_for(user: UserContext, question: str | None) -> tuple[str, str] | None:
    """``(norm_query, sample_query)`` when this ask may write a gap, else ``None``.

    It checks the flag, the member and the skip rules, in that order. It
    reads no database. The skip rules read the RAW question. Both items are
    made from the MASKED question (:func:`mask_text`).
    """
    if not flags.knowledge_gaps_allowed(user.organization_id):
        return None
    if not is_member(user):
        return None
    if skip_reason(question) is not None:
        return None
    masked = mask_text(question)
    norm = normalize(masked)
    if not norm:
        return None
    return norm, sample_of(masked) or norm


def schedule_gap(
    background: BackgroundTasks | None, user: UserContext, question: str,
    knowledge_hits: int | None,
) -> bool:
    """Add the gap write to *background* when Knowledge found nothing.

    *knowledge_hits* is the count that the Knowledge source answered, or
    ``None`` when the route did not ask it or it failed. Only ``0`` writes.
    It returns True when it scheduled a write. It never raises, so a fault
    here never fails the search.
    """
    try:
        if background is None or knowledge_hits != 0:
            return False
        found = gap_for(user, question)
        if found is None:
            return False
        norm, sample = found
        background.add_task(
            record_gap, str(user.organization_id), core.viewer_email(user), norm, sample,
        )
        return True
    except Exception as exc:  # a search must never fail on a gap
        _log.warning("knowledge.gap_schedule_failed", error=type(exc).__name__)
        return False


# ── The write (a background job) ────────────────────────────────────────────

#: One statement. The CASEs read the OLD row, so a new window, a known asker
#: and a full ``askers`` each give the right count and list.
#: The window runs from the FIRST ask, so a steady asker cannot hold one
#: window open for ever. ``my_day_rows`` still reads ``last_asked_at``,
#: because the freshness of a card is about the last ask.
_STALE = f"g.first_asked_at < now() - interval '{WINDOW_DAYS} days'"
UPSERT_GAP_SQL = (
    "INSERT INTO kb_gaps AS g (organization_id, norm_query, sample_query, askers, ask_count) "
    "VALUES (CAST(:org AS uuid), :norm, :sample, ARRAY[CAST(:me AS text)], 1) "
    "ON CONFLICT (organization_id, norm_query) DO UPDATE SET "
    f"askers = CASE WHEN {_STALE} THEN ARRAY[CAST(:me AS text)] "
    "WHEN CAST(:me AS text) = ANY(g.askers) THEN g.askers "
    f"WHEN coalesce(array_length(g.askers, 1), 0) >= {MAX_ASKERS} THEN g.askers "
    "ELSE array_append(g.askers, CAST(:me AS text)) END, "
    f"ask_count = CASE WHEN {_STALE} THEN 1 "
    "WHEN CAST(:me AS text) = ANY(g.askers) THEN g.ask_count "
    f"WHEN coalesce(array_length(g.askers, 1), 0) >= {MAX_ASKERS} THEN g.ask_count "
    "ELSE g.ask_count + 1 END, "
    f"first_asked_at = CASE WHEN {_STALE} THEN now() ELSE g.first_asked_at END, "
    "status = CASE WHEN g.status = 'answered' AND g.answered_page_id IS NULL "
    "THEN 'open' ELSE g.status END, "
    "last_asked_at = now() "
    "RETURNING g.ask_count, g.status"
)


async def record_gap(organization_id: str, member: str, norm: str, sample: str) -> int:
    """Write one ask of a gap. A background job: it never raises.

    It opens its own tenant session for *organization_id*, through the one
    seam. It returns the ``ask_count`` after the write, or 0 on a failure.
    The ask that brings an open gap to :data:`CARD_ASKERS` bumps the feed
    cache of the org, so the card shows on the next read.
    """
    try:
        async with core._tenant_session(organization_id) as db:
            row = (await db.execute(text(UPSERT_GAP_SQL), {
                "org": organization_id, "norm": norm, "sample": sample, "me": member,
            })).first()
        count = int(row.ask_count) if row is not None else 0
        if row is not None and row.status == "open" and count == CARD_ASKERS:
            await _bump(organization_id)
        return count
    except Exception as exc:  # a background job must not raise
        _log.warning("knowledge.gap_write_failed", organization_id=organization_id,
                     error=type(exc).__name__)
        return 0


async def _bump(organization_id: str) -> None:
    """The org's feed cache, after a commit. It waits briefly and never raises."""
    from gateway.routes.shell import cache

    await cache.bump_org_safe(organization_id)


# ── The routes ──────────────────────────────────────────────────────────────

_GATED = [Depends(flags.require_knowledge_gaps)]


class GapAnswered(BaseModel):
    page_id: str = Field(..., min_length=1, max_length=64)


def _gap_dict(row: Any, user: UserContext) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(row.id),
        "question": row.sample_query,
        "ask_count": int(row.ask_count),
        "first_asked_at": core._iso(row.first_asked_at),
        "last_asked_at": core._iso(row.last_asked_at),
        "status": row.status,
        "answered_page": (
            {"id": str(row.page_id), "title": row.page_title}
            if getattr(row, "page_id", None) is not None else None
        ),
        "asked_by_me": bool(getattr(row, "asked_by_me", False)),
        "dismissed_at": core._iso(row.dismissed_at),
    }
    if core.is_manager(user):
        out["dismissed_by"] = row.dismissed_by
    return out


def _seen_clause(user: UserContext) -> str:
    """The gaps the caller sees: each gap for a manager, else the caller's own."""
    base = "g.organization_id = CAST(:kb_org AS uuid)"
    if core.is_manager(user):
        return base
    return f"{base} AND :kb_viewer = ANY(g.askers)"


async def _read_gaps(
    db: Any, user: UserContext, *, gap_id: str | None = None,
    status: str | None = None, limit: int = DEFAULT_LIMIT,
) -> list[Any]:
    """The gaps the caller sees, with the answering page when the caller sees it."""
    vis, params = core.visible_pages_clause(user, "p")
    where = [_seen_clause(user)]
    query: dict[str, Any] = {**params, "limit": limit}
    if gap_id is not None:
        where.append("g.id = CAST(:gap AS uuid)")
        query["gap"] = gap_id
    if status is not None:
        where.append("g.status = :status")
        query["status"] = status
    return (await db.execute(
        text(f"SELECT {_GAP_COLUMNS}, p.id AS page_id, p.title AS page_title, "
             ":kb_viewer = ANY(g.askers) AS asked_by_me "
             "FROM kb_gaps g "
             f"LEFT JOIN kb_pages p ON p.id = g.answered_page_id AND {vis} "
             f"WHERE {' AND '.join(where)} "
             "ORDER BY g.ask_count DESC, g.last_asked_at DESC, g.id DESC LIMIT :limit"),
        query,
    )).fetchall()


async def _one_gap(db: Any, user: UserContext, gap_id: str | None) -> Any:
    """The gap if the caller sees it, else a 404."""
    gid = core.parse_id(gap_id)
    if gid is None:
        raise core.not_found()
    rows = await _read_gaps(db, user, gap_id=gid, limit=1)
    if not rows:
        raise core.not_found()
    return rows[0]


def _closed(row: Any) -> HTTPException:
    return HTTPException(status_code=409, detail=f"This question is already {row.status}.")


@router.get("/gaps", dependencies=_GATED)
async def list_gaps(
    status: str = Query("open", max_length=20),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The gaps the caller sees, the most asked first.

    ``scope`` says what the list holds: ``org`` for a manager, ``mine`` for
    a member, so the client knows which view it draws.
    """
    if status not in STATUSES:
        raise HTTPException(
            status_code=422, detail=f"Unknown status '{status}'. One of: {list(STATUSES)}.",
        )
    async with core._tenant_session() as db:
        rows = await _read_gaps(db, user, status=status, limit=limit)
    return {
        "gaps": [_gap_dict(r, user) for r in rows],
        "scope": "org" if core.is_manager(user) else "mine",
    }


@router.post("/gaps/{gap_id}/dismiss", dependencies=_GATED)
async def dismiss_gap(
    gap_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Close an open gap with no page. It needs ``knowledge:manage``."""
    if not core.is_manager(user):
        raise core.forbidden("Dismissing a question needs knowledge:manage.")
    async with core._tenant_session() as db:
        row = await _one_gap(db, user, gap_id)
        if row.status != "open":
            raise _closed(row)
        await db.execute(
            text("UPDATE kb_gaps SET status = 'dismissed', dismissed_by = :me, "
                 "dismissed_at = now() WHERE organization_id = CAST(:org AS uuid) "
                 "AND id = CAST(:gap AS uuid) AND status = 'open'"),
            {"me": core.viewer_email(user), "org": core.org_of(user), "gap": str(row.id)},
        )
        row = await _one_gap(db, user, str(row.id))
    await _bump(core.org_of(user))
    return _gap_dict(row, user)


@router.post("/gaps/{gap_id}/reopen", dependencies=_GATED)
async def reopen_gap(
    gap_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Undo a dismiss: a ``dismissed`` gap reads ``open`` again.

    It needs ``knowledge:manage``. An open or answered gap answers 409.
    """
    if not core.is_manager(user):
        raise core.forbidden("Reopening a question needs knowledge:manage.")
    async with core._tenant_session() as db:
        row = await _one_gap(db, user, gap_id)
        if row.status != "dismissed":
            raise _closed(row)
        await db.execute(
            text("UPDATE kb_gaps SET status = 'open', dismissed_by = NULL, "
                 "dismissed_at = NULL WHERE organization_id = CAST(:org AS uuid) "
                 "AND id = CAST(:gap AS uuid) AND status = 'dismissed'"),
            {"org": core.org_of(user), "gap": str(row.id)},
        )
        row = await _one_gap(db, user, str(row.id))
    await _bump(core.org_of(user))
    return _gap_dict(row, user)


@router.post("/gaps/{gap_id}/answered", dependencies=_GATED)
async def answer_gap(
    gap_id: str,
    body: GapAnswered,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Close an open gap with a page: "I wrote it".

    The caller must see the gap, and see a live page that the caller can
    write. A page the caller cannot see answers 404, never 403.
    """
    async with core._tenant_session() as db:
        row = await _one_gap(db, user, gap_id)
        if row.status != "open":
            raise _closed(row)
        page = await core.load_page(db, user, body.page_id)
        if page is None:
            raise core.not_found()
        if page.state == "archived":
            raise HTTPException(status_code=409, detail="The page is archived.")
        space = (await db.execute(
            text(f"SELECT {core.SPACE_COLUMNS} FROM kb_spaces s "
                 "WHERE s.organization_id = CAST(:org AS uuid) AND s.id = CAST(:sid AS uuid)"),
            {"org": core.org_of(user), "sid": str(page.space_id)},
        )).first()
        level = await core.space_level(db, user, space) if space is not None else None
        if not core.can_write_page(level):
            raise core.forbidden("Answering with this page needs a write grant on its space.")
        await db.execute(
            text("UPDATE kb_gaps SET status = 'answered', "
                 "answered_page_id = CAST(:page AS uuid) "
                 "WHERE organization_id = CAST(:org AS uuid) "
                 "AND id = CAST(:gap AS uuid) AND status = 'open'"),
            {"page": str(page.id), "org": core.org_of(user), "gap": str(row.id)},
        )
        row = await _one_gap(db, user, str(row.id))
    await _bump(core.org_of(user))
    return _gap_dict(row, user)


# ── The member purge ────────────────────────────────────────────────────────

async def scrub_member(db: Any, organization_id: str, email: str) -> None:
    """Take a purged member's address out of every gap of the org.

    ``routes/admin/members.purge_member_rows`` calls it in the purge's own
    transaction. The gap stays, because the question is the org's. The
    count stays too: it is a number, and it names nobody. ``dismissed_by``
    loses the address as well.
    """
    params = {"org": str(organization_id), "email": str(email or "").strip().lower()}
    await db.execute(
        text("UPDATE kb_gaps SET askers = array_remove(askers, CAST(:email AS text)) "
             "WHERE organization_id = CAST(:org AS uuid) "
             "AND CAST(:email AS text) = ANY(askers)"),
        params,
    )
    await db.execute(
        text("UPDATE kb_gaps SET dismissed_by = NULL "
             "WHERE organization_id = CAST(:org AS uuid) "
             "AND lower(dismissed_by) = CAST(:email AS text)"),
        params,
    )


# ── My Day (the `kb_gap` card, §9) ──────────────────────────────────────────

async def my_day_rows(user: UserContext, limit: int) -> list[dict[str, Any]]:
    """The open gaps of the org that make a ``kb_gap`` card, the most asked first.

    A gap makes a card at :data:`CARD_ASKERS` askers or more, with a last
    ask less than :data:`WINDOW_DAYS` days old. Only a holder of
    ``knowledge:manage`` gets rows. One tenant session. The provider in
    ``routes/shell/feed.py`` checks the flags first.
    """
    if not core.is_manager(user):
        return []
    async with core._tenant_session() as db:
        rows = (await db.execute(
            text("SELECT g.id, g.sample_query, g.ask_count, g.last_asked_at FROM kb_gaps g "
                 "WHERE g.organization_id = CAST(:org AS uuid) AND g.status = 'open' "
                 "AND g.ask_count >= :askers "
                 f"AND g.last_asked_at >= now() - interval '{WINDOW_DAYS} days' "
                 "ORDER BY g.ask_count DESC, g.last_asked_at DESC, g.id DESC LIMIT :limit"),
            {"org": core.org_of(user), "askers": CARD_ASKERS, "limit": limit},
        )).fetchall()
    return [
        {"id": str(r.id), "question": r.sample_query, "ask_count": int(r.ask_count),
         "last_asked_at": r.last_asked_at}
        for r in rows
    ]
