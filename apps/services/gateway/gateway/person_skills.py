"""Structured skills & credentials — the store, and the ONE projection (WS-28h).

Spec: ``project-docs/specs/people_center_app.md`` §3.3 · **D-PC-6**.

A leaf module outside both route packages, like :mod:`gateway.work_schedule`
and for the same reason: ``routes/people/*`` imports from ``routes/tasks/*``
and never the reverse, and BOTH need to write here — the People routes own the
editing surface, the tasks-side résumé ingest writes parsed rows.

**The child table is the source; ``people.skills[]`` is the cache.** Four
live consumers read the array (the GIN index, ``_match_capability``,
``fetch_people_for_clarify``, the directory filters) and R6 forbids breaking
them, so every write path here ends in :func:`project` — inside the caller's
transaction, so the array can never be observed disagreeing with the table.
A second place that rewrote the array would be a second answer to "what does
this person know", which is exactly the drift D-PC-6 exists to prevent.

**Projection order is ``created_at`` then name — which, measured, means
alphabetical within a batch**: rows inserted in one transaction all share
``now()`` as their ``created_at``, so the tie-break decides. The array's order
was never load-bearing (every consumer treats it as a set); what a projection
must be is a *function* — the same table twice yields the same array twice, or
the fence comparing them flaps. Found by ``live_ws28h.py``, whose first run
expected insertion order and was corrected by the database.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

#: How well. NULL on a row means "not assessed" — which is honest, and distinct
#: from 'learning'. Mirrored from migration 176's CHECK; a fifth word accepted
#: here would be a save that fails at Postgres.
SKILL_LEVELS: tuple[str, ...] = ("learning", "working", "proficient", "expert")

#: On what evidence. 'manual' = the person (or an admin) typed it — the value
#: the existing ``skills_source`` map already carries; 'resume' = the parser
#: found it in the CV; 'observed' = derived from shipped work (no writer yet —
#: admitted so its arrival needs no migration).
EVIDENCE: tuple[str, ...] = ("manual", "resume", "observed")

CREDENTIAL_KINDS: tuple[str, ...] = ("education", "certification", "prior_role")

#: The most skills one person may carry. Nobody has three hundred skills; a
#: payload that claims to is an import gone wrong, and refusing it beats
#: letting it flatten into an unreadable array.
MAX_SKILLS = 200
MAX_CREDENTIALS = 100


# ── Validation — refuse what the database would refuse, in words ─────────────


def validate_skill(row: dict[str, Any], *, index: int) -> dict[str, Any]:
    skill = str(row.get("skill") or "").strip()
    if not skill:
        raise HTTPException(
            status_code=400, detail=f"Skill #{index + 1} has no name.")
    level = row.get("level")
    if level is not None and level not in SKILL_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown level '{level}' for '{skill}'. "
                   f"One of: {list(SKILL_LEVELS)}, or leave it unset.")
    years = row.get("years")
    if years is not None:
        try:
            years = float(years)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"'{skill}': years must be a number.") from exc
        if not 0 <= years <= 60:
            raise HTTPException(
                status_code=400,
                detail=f"'{skill}': years must be between 0 and 60.")
    last_used = row.get("last_used_year")
    if last_used is not None:
        try:
            last_used = int(last_used)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"'{skill}': last_used_year must be a year.") from exc
        if not 1970 <= last_used <= 2100:
            raise HTTPException(
                status_code=400,
                detail=f"'{skill}': last_used_year must be a real year.")
    evidence = row.get("evidence") or "manual"
    if evidence not in EVIDENCE:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown evidence '{evidence}' for '{skill}'. "
                   f"One of: {list(EVIDENCE)}.")
    return {"skill": skill, "level": level, "years": years,
            "last_used_year": last_used, "evidence": evidence}


def validate_skills(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The whole payload — including the duplicate the UNIQUE index would catch,
    refused here with the name instead of a constraint string at 3am."""
    if len(rows) > MAX_SKILLS:
        raise HTTPException(
            status_code=400,
            detail=f"{len(rows)} skills is more than one person can carry "
                   f"(max {MAX_SKILLS}). This looks like an import mistake.")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        row = validate_skill(raw, index=index)
        key = row["skill"].lower()
        if key in seen:
            raise HTTPException(
                status_code=400,
                detail=f"'{row['skill']}' appears twice. One row per skill — "
                       "it carries the level and the years.")
        seen.add(key)
        out.append(row)
    return out


def validate_credential(row: dict[str, Any], *, index: int) -> dict[str, Any]:
    kind = row.get("kind")
    if kind not in CREDENTIAL_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Credential #{index + 1}: unknown kind '{kind}'. "
                   f"One of: {list(CREDENTIAL_KINDS)}.")
    title = str(row.get("title") or "").strip()
    if not title:
        raise HTTPException(
            status_code=400,
            detail=f"Credential #{index + 1} has no title.")
    years: dict[str, int | None] = {}
    for field in ("year_from", "year_to"):
        value = row.get(field)
        if value is None:
            years[field] = None
            continue
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"'{title}': {field} must be a year.") from exc
        if not 1950 <= value <= 2100:
            raise HTTPException(
                status_code=400,
                detail=f"'{title}': {field} must be a real year.")
        years[field] = value
    if (years["year_from"] is not None and years["year_to"] is not None
            and years["year_to"] < years["year_from"]):
        raise HTTPException(
            status_code=400,
            detail=f"'{title}' ends before it starts. Check the years.")
    return {
        "kind": kind, "title": title,
        "issuer": (str(row.get("issuer") or "").strip() or None),
        "detail": (str(row.get("detail") or "").strip() or None),
        "source": row.get("source") if row.get("source") in ("manual", "resume")
        else "manual",
        **years,
    }


def validate_credentials(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) > MAX_CREDENTIALS:
        raise HTTPException(
            status_code=400,
            detail=f"{len(rows)} credentials (max {MAX_CREDENTIALS}). "
                   "This looks like an import mistake.")
    return [validate_credential(r, index=i) for i, r in enumerate(rows)]


# ── Reads ────────────────────────────────────────────────────────────────────


async def fetch_skills(db: Any, person_id: str) -> list[dict[str, Any]]:
    rows = (await db.execute(text(
        "SELECT id, skill, level, years, last_used_year, evidence "
        "  FROM people_skills WHERE person_id = CAST(:pid AS uuid) "
        " ORDER BY created_at, lower(skill)"), {"pid": person_id})).fetchall()
    return [
        {"id": str(r.id), "skill": r.skill, "level": r.level,
         "years": r.years, "last_used_year": r.last_used_year,
         "evidence": r.evidence}
        for r in rows
    ]


async def fetch_credentials(db: Any, person_id: str) -> list[dict[str, Any]]:
    rows = (await db.execute(text(
        "SELECT id, kind, title, issuer, year_from, year_to, detail, source "
        "  FROM people_credentials WHERE person_id = CAST(:pid AS uuid) "
        " ORDER BY year_from DESC NULLS LAST, created_at"),
        {"pid": person_id})).fetchall()
    return [
        {"id": str(r.id), "kind": r.kind, "title": r.title, "issuer": r.issuer,
         "year_from": r.year_from, "year_to": r.year_to, "detail": r.detail,
         "source": r.source}
        for r in rows
    ]


# ── Writes — every one ends in project(), same transaction ───────────────────


async def replace_skills(db: Any, person_id: str, rows: list[dict[str, Any]],
                         actor: str) -> list[dict[str, Any]]:
    """The editor's write: the payload IS the person's skills, whole.

    Delete-and-insert rather than a diff: the panel edits the whole list, a
    diff would preserve rows the caller deliberately removed, and the table is
    small by construction (:data:`MAX_SKILLS`).
    """
    values = validate_skills(rows)
    await db.execute(text(
        "DELETE FROM people_skills WHERE person_id = CAST(:pid AS uuid)"),
        {"pid": person_id})
    for value in values:
        await db.execute(text(
            "INSERT INTO people_skills "
            "  (person_id, skill, level, years, last_used_year, evidence, "
            "   updated_by) "
            "VALUES (CAST(:pid AS uuid), :skill, :level, :years, "
            "        :last_used_year, :evidence, :by)"),
            {"pid": person_id, "by": actor, **value})
    await project(db, person_id, actor)
    return await fetch_skills(db, person_id)


async def sync_from_array(db: Any, person_id: str, skills: list[str],
                          actor: str) -> None:
    """Reconcile the table to a FLAT list — the legacy write path, kept honest.

    ``PATCH {skills: [...]}`` predates this table and stays supported (the
    directory editor sends it, and refusing it would break a shipped surface).
    Under D-PC-6 the table is the source, so the flat write becomes: keep the
    structured row (level, years, evidence and all) for every skill that
    remains, insert the new ones as bare 'manual' rows, delete the removed —
    then project. A flat save must never silently strip the level somebody set
    through the structured editor; losing data through the older of two doors
    is the classic two-door defect.
    """
    wanted: dict[str, str] = {}
    for skill in skills or []:
        name = str(skill or "").strip()
        if name and name.lower() not in wanted:
            wanted[name.lower()] = name

    existing = (await db.execute(text(
        "SELECT lower(skill) AS key FROM people_skills "
        " WHERE person_id = CAST(:pid AS uuid)"), {"pid": person_id})).fetchall()
    have = {r.key for r in existing}

    gone = have - set(wanted)
    if gone:
        await db.execute(text(
            "DELETE FROM people_skills "
            " WHERE person_id = CAST(:pid AS uuid) "
            "   AND lower(skill) = ANY(:gone)"),
            {"pid": person_id, "gone": sorted(gone)})
    for key, name in wanted.items():
        if key not in have:
            await db.execute(text(
                "INSERT INTO people_skills "
                "  (person_id, skill, evidence, updated_by) "
                "VALUES (CAST(:pid AS uuid), :skill, 'manual', :by)"),
                {"pid": person_id, "skill": name, "by": actor})
    await project(db, person_id, actor)


async def merge_from_resume(db: Any, person_id: str,
                            skills: list[str],
                            credentials: list[dict[str, Any]],
                            actor: str) -> list[str]:
    """The parser's write: ADD what the CV shows, never remove what a human put.

    A résumé is evidence for what it contains and silent about everything else
    — somebody's Rust does not disappear because their CV predates it. Skills
    already present keep their row untouched (a human's level assessment
    outranks a re-parse); credentials are deduplicated on (kind, title, issuer)
    case-insensitively, because re-uploading a CV must not double a degree.

    Returns the names actually added, for the ingest response's
    ``added_skills``.
    """
    existing = (await db.execute(text(
        "SELECT lower(skill) AS key FROM people_skills "
        " WHERE person_id = CAST(:pid AS uuid)"), {"pid": person_id})).fetchall()
    have = {r.key for r in existing}
    added: list[str] = []
    for skill in skills or []:
        name = str(skill or "").strip()
        if not name or name.lower() in have:
            continue
        have.add(name.lower())
        added.append(name)
        await db.execute(text(
            "INSERT INTO people_skills "
            "  (person_id, skill, evidence, updated_by) "
            "VALUES (CAST(:pid AS uuid), :skill, 'resume', :by)"),
            {"pid": person_id, "skill": name, "by": actor})

    if credentials:
        rows = (await db.execute(text(
            "SELECT kind, lower(title) AS title, lower(COALESCE(issuer, '')) "
            "       AS issuer FROM people_credentials "
            " WHERE person_id = CAST(:pid AS uuid)"), {"pid": person_id})
        ).fetchall()
        seen = {(r.kind, r.title, r.issuer) for r in rows}
        for cred in validate_credentials(credentials):
            key = (cred["kind"], cred["title"].lower(),
                   (cred["issuer"] or "").lower())
            if key in seen:
                continue
            seen.add(key)
            await db.execute(text(
                "INSERT INTO people_credentials "
                "  (person_id, kind, title, issuer, year_from, year_to, "
                "   detail, source, created_by) "
                "VALUES (CAST(:pid AS uuid), :kind, :title, :issuer, "
                "        :year_from, :year_to, :detail, 'resume', :by)"),
                {"pid": person_id, "by": actor,
                 **{k: cred[k] for k in ("kind", "title", "issuer",
                                         "year_from", "year_to", "detail")}})

    await project(db, person_id, actor)
    return added


async def replace_credentials(db: Any, person_id: str,
                              rows: list[dict[str, Any]],
                              actor: str) -> list[dict[str, Any]]:
    """Credentials carry no projection — nothing else reads them yet — but they
    take the same whole-list replace shape as skills so the two panels behave
    identically."""
    values = validate_credentials(rows)
    await db.execute(text(
        "DELETE FROM people_credentials "
        " WHERE person_id = CAST(:pid AS uuid)"), {"pid": person_id})
    for value in values:
        await db.execute(text(
            "INSERT INTO people_credentials "
            "  (person_id, kind, title, issuer, year_from, year_to, detail, "
            "   source, created_by) "
            "VALUES (CAST(:pid AS uuid), :kind, :title, :issuer, :year_from, "
            "        :year_to, :detail, :source, :by)"),
            {"pid": person_id, "by": actor, **value})
    return await fetch_credentials(db, person_id)


async def project(db: Any, person_id: str, actor: str) -> None:
    """Rewrite ``people.skills`` + ``skills_source`` from the table.

    THE one place the array is derived (D-PC-6). Runs on the caller's session,
    inside the caller's transaction — commit lands the table and its projection
    together or neither, so no reader can catch them disagreeing.
    """
    rows = (await db.execute(text(
        "SELECT skill, evidence FROM people_skills "
        " WHERE person_id = CAST(:pid AS uuid) "
        " ORDER BY created_at, lower(skill)"), {"pid": person_id})).fetchall()
    skills = [r.skill for r in rows]
    source = {r.skill: r.evidence for r in rows}
    await db.execute(text(
        "UPDATE people SET skills = :skills, "
        "       skills_source = CAST(:source AS JSONB), "
        "       updated_by = :by, updated_at = now() "
        " WHERE id = CAST(:pid AS uuid)"),
        {"skills": skills, "source": json.dumps(source), "by": actor,
         "pid": person_id})
