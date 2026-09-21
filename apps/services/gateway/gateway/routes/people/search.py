"""People Center · capability search — "who should do this?" (WS-28d).

Spec: ``project-docs/specs/people_center_app.md`` §5.5 · D-PC-13.

    GET /people/search?q=…   → ranked people, each naming WHY it matched

Three signals, most defensible first, each labelled on the result:

1. **Stated skills** — word-boundary match on the structured rows (WS-28h),
   weighted by level and recency. Deterministic.
2. **Résumé evidence** — a hit in the parsed CV text, QUOTING the line, because
   a claim with its evidence beside it can be argued with.
3. **Capability vector** — cosine on ``capability_embedding``, for what the
   first two miss. Best-effort: semantic off → the signal is absent, never an
   error.

⚠️ **There is no LLM ranking prompt in this module, and that is deliberate.**
§5.5 EVAL-LOCKS the ranking prompt — a change to one needs the eval, not a
review — and the way to build the search *without* touching that lock is to
rank by arithmetic whose every component is named, weighted by constants
declared below, and SHOWN on the result. A ranking whose reasoning is hidden
cannot be argued with; this one can be checked line by line. If an LLM ranker
is ever added, it goes through the eval first.

⚠️ **This is a suggester: it never assigns** (D-PC-13, same rule as D-PM-10).
The module contains no INSERT, UPDATE or DELETE, and the test suite greps it
to keep that true structurally.

**Gated on ``admin:members:read`` on top of ``feature:people``** — it is a
skills query by definition, and the oracle rule (§4.2) applies to the whole
surface: matching on a column a caller may not read would turn the search box
into an oracle for it.
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.people.core import (
    _tenant_session,
    can_read_hr_fields,
    compute_load,
    router,
)
from gateway.work_schedule import (
    contracted_hours_per_week,
    load_policy,
    person_schedule,
)
from pydantic import BaseModel
from sqlalchemy import text

#: The weights. Constants with names, not a prompt — the whole ranking can be
#: recomputed by hand from what the result shows.
LEVEL_WEIGHT: dict[str | None, float] = {
    "expert": 2.0, "proficient": 1.5, "working": 1.0, "learning": 0.5,
    None: 1.0,   # unassessed counts as a plain hit, never as nothing
}
#: A skill unused for years is a different answer (§3.3). Unknown recency is
#: NOT decayed — most rows predate the column, and punishing missing data
#: would rank people by form-filling.
RECENCY_FRESH_YEARS = 2
RECENCY_STALE_YEARS = 5
RECENCY_FRESH = 1.0
RECENCY_AGING = 0.75
RECENCY_STALE = 0.5
#: The person's primary field named in the query — worth two plain skill hits,
#: the same ratio `_match_capability` has used since WS-24.
DOMAIN_BONUS = 2.0
RESUME_BONUS = 1.0
#: Cosine lands in [0,1]; x3 makes a strong semantic match comparable to a
#: couple of skill hits without letting it drown the defensible signals.
SEMANTIC_SCALE = 3.0

MAX_RESULTS = 12
MIN_QUERY_CHARS = 2


class SearchResult(BaseModel):
    person_id: str
    name: str
    #: The assignee value — what §6.4's "Assign to…" hands to the task flow.
    #: Directory tier: the directory already shows every colleague's address.
    email: str | None = None
    title: str | None = None
    department: str | None = None
    avatar: str | None = None
    score: float
    #: Which signals matched, each with its own contribution — the argument
    #: for the ranking, not just its output.
    signals: list[dict[str, Any]]
    #: How loaded they are (§5.5): a perfect match at 45/40 hours is the wrong
    #: answer, and the reader decides that, not the ranker.
    load: dict[str, Any] | None = None
    contracted_hours: float | None = None
    #: Away now / this week, and anything that makes assignment moot.
    away: dict[str, Any] | None = None
    timezone: str | None = None
    warnings: list[str] = []


class SearchResponse(BaseModel):
    q: str
    rows: list[SearchResult]
    total: int
    #: Whether the semantic signal ran at all — "no vector matches" and
    #: "vectors are off" must not read identically.
    semantic_available: bool


def _tokens(q: str) -> list[str]:
    return [t for t in re.split(r"[^\w+#./-]+", q.lower()) if len(t) >= 2]


def _recency_weight(last_used_year: int | None, this_year: int) -> float:
    if last_used_year is None:
        return RECENCY_FRESH
    age = this_year - last_used_year
    if age <= RECENCY_FRESH_YEARS:
        return RECENCY_FRESH
    if age <= RECENCY_STALE_YEARS:
        return RECENCY_AGING
    return RECENCY_STALE


def skill_pattern(name: str) -> str:
    """THE word-boundary for a skill name, written once. ``\\b`` is wrong for
    this vocabulary ('c++', 'c#', 'node.js'), so the boundary is "not another
    skill character" on both sides — and WS-28m's "declared but never used on a
    task" check has to agree with this matcher exactly, or a skill could count
    as matched by the ranker and unused by the coverage panel at once."""
    return rf"(?<![a-z0-9+#./]){re.escape(name)}(?![a-z0-9+#./])"


def score_skills(query: str, skill_rows: list[dict[str, Any]],
                 this_year: int) -> tuple[float, list[dict[str, Any]]]:
    """Signal 1 — pure, so WS-28j3's suggester can call it with a task's text
    instead of a typed query and get the same answer (§5.7.4: 'a second ranker
    would be a second answer to "who is good at this"')."""
    hay = query.lower()
    matched: list[dict[str, Any]] = []
    total = 0.0
    for row in skill_rows:
        name = (row.get("skill") or "").strip().lower()
        if len(name) < 2:
            continue
        if not re.search(skill_pattern(name), hay):
            continue
        level = row.get("level")
        weight = (LEVEL_WEIGHT.get(level, 1.0)
                  * _recency_weight(row.get("last_used_year"), this_year))
        total += weight
        matched.append({
            "kind": "skill", "skill": row.get("skill"), "level": level,
            "last_used_year": row.get("last_used_year"),
            "evidence": row.get("evidence"), "points": round(weight, 2),
        })
    return round(total, 2), matched


def resume_quote(parsed_text: str, tokens: list[str]) -> str | None:
    """The line of the CV that mentions the query — evidence, quotable.

    The FIRST matching line, capped: a quote exists to be checked against the
    document, not to reproduce it.
    """
    if not parsed_text:
        return None
    for line in parsed_text.splitlines():
        low = line.lower()
        if any(t in low for t in tokens):
            clean = " ".join(line.split()).strip()
            if clean:
                return clean[:240]
    return None


@router.get("/search", response_model=SearchResponse)
async def search_people(
    q: str, user: UserContext = Depends(get_current_user),
) -> SearchResponse:
    """The single box: *"Who can help with extruder firmware?"* (§5.5)."""
    if not can_read_hr_fields(user):
        raise HTTPException(
            status_code=403,
            detail="Capability search needs admin:members:read — it is a "
                   "skills query by definition.")
    query = (q or "").strip()
    if len(query) < MIN_QUERY_CHARS:
        raise HTTPException(
            status_code=400, detail="Say what you need help with.")
    tokens = _tokens(query)

    from datetime import date, timedelta
    this_year = date.today().year
    today = date.today()
    sunday = today + timedelta(days=7 - today.isoweekday())

    async with _tenant_session() as db:
        people = (await db.execute(text(
            "SELECT id, name, title, department, avatar, timezone, domain, "
            "       email, end_date, working_hours "
            "  FROM people WHERE status = 'active'"))).fetchall()
        skills = (await db.execute(text(
            "SELECT person_id, skill, level, years, last_used_year, evidence "
            "  FROM people_skills"))).fetchall()
        by_person: dict[str, list[dict[str, Any]]] = {}
        for row in skills:
            by_person.setdefault(str(row.person_id), []).append({
                "skill": row.skill, "level": row.level,
                "last_used_year": row.last_used_year,
                "evidence": row.evidence})

        # Signal 2 — one query for the CVs that mention any token. ILIKE per
        # token ORed together; the quote is extracted in Python where the line
        # structure survives.
        resume_rows: list[Any] = []
        if tokens:
            clauses = " OR ".join(
                f"parsed_text ILIKE :t{i}" for i in range(len(tokens)))
            try:
                resume_rows = (await db.execute(text(
                    "SELECT DISTINCT ON (person_id) person_id, parsed_text "
                    "  FROM people_resumes "
                    f" WHERE {clauses} "
                    " ORDER BY person_id, uploaded_at DESC"),
                    {f"t{i}": f"%{t}%" for i, t in enumerate(tokens)},
                )).fetchall()
            except Exception:
                resume_rows = []
        cv_by_person = {str(r.person_id): r.parsed_text or ""
                        for r in resume_rows}

        # Signal 3 — best-effort; keyed by lowercased NAME (its own contract).
        from gateway.routes.tasks.capability import semantic_scores
        semantic = await semantic_scores(db, query)

        policy = await load_policy(db)

        scored: list[tuple[float, Any, list[dict[str, Any]]]] = []
        for person in people:
            pid = str(person.id)
            signals: list[dict[str, Any]] = []
            skill_pts, matched = score_skills(
                query, by_person.get(pid, []), this_year)
            signals.extend(matched)
            total = skill_pts

            if person.domain and re.search(
                    rf"\b{re.escape(str(person.domain).lower())}\b",
                    query.lower()):
                total += DOMAIN_BONUS
                signals.append({"kind": "domain", "domain": person.domain,
                                "points": DOMAIN_BONUS})

            quote = resume_quote(cv_by_person.get(pid, ""), tokens)
            if quote:
                total += RESUME_BONUS
                signals.append({"kind": "resume", "quote": quote,
                                "points": RESUME_BONUS})

            sim = semantic.get((person.name or "").strip().lower())
            if sim is not None and sim > 0:
                points = round(sim * SEMANTIC_SCALE, 2)
                total += points
                signals.append({"kind": "semantic", "cosine": round(sim, 3),
                                "points": points})

            if total > 0:
                scored.append((round(total, 2), person, signals))

        scored.sort(key=lambda item: (-item[0], str(item[1].name)))
        top = scored[:MAX_RESULTS]

        # The costly per-person reads happen for the TOP rows only.
        results: list[SearchResult] = []
        for total, person, signals in top:
            pid = str(person.id)
            schedule = person_schedule(policy, person)
            load = await compute_load(db, getattr(person, "email", None))
            away_row = (await db.execute(text(
                "SELECT kind, ends_on FROM people_absences "
                " WHERE person_id = CAST(:pid AS uuid) "
                "   AND starts_on <= :sunday AND ends_on >= :today "
                " ORDER BY (kind = 'partial'), ends_on DESC LIMIT 1"),
                {"pid": pid, "today": today, "sunday": sunday})).fetchone()

            warnings: list[str] = []
            if away_row is not None:
                warnings.append(
                    f"Away ({away_row.kind}) until {away_row.ends_on.isoformat()}")
            end = getattr(person, "end_date", None)
            if end is not None and (end - today).days <= 30:
                warnings.append(f"Engagement ends {end.isoformat()}")

            results.append(SearchResult(
                person_id=pid, name=person.name,
                email=getattr(person, "email", None), title=person.title,
                department=person.department, avatar=person.avatar,
                score=total, signals=signals, load=load,
                contracted_hours=contracted_hours_per_week(schedule),
                away=({"kind": away_row.kind,
                       "until": away_row.ends_on.isoformat()}
                      if away_row is not None else None),
                timezone=getattr(person, "timezone", None),
                warnings=warnings,
            ))

    return SearchResponse(q=query, rows=results, total=len(results),
                          semantic_available=bool(semantic))
