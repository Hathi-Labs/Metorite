#!/usr/bin/env python3
"""Import the company org chart + resume capabilities into people.

Reads the seed snapshot (infra/seed/hr/) — a copy of agent-project-manager's
agent-data/hr_structure.json + resume_profiles.json — merges each person's
org-chart skills with their resume-extracted skills, and upserts one
people row per person (keyed by unique name). Idempotent: re-run any time
the snapshot is refreshed.

Usage:
    .venv/bin/python scripts/import_hr_people.py [--seed-dir infra/seed/hr]
Env:
    DATABASE_URL (default: the acb-postgres compose default)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The source this importer owns. Part of `source_key`, so a row hand-added in
#: the People Center is never overwritten by a snapshot re-import.
SOURCE = "agent-project-manager"


def source_key(name: str) -> str:
    """The importer's upsert key — per SOURCE, not per person.

    Migration 148 dropped `UNIQUE(name)` from `people` (People Center P-1):
    two real people may share a name, and the old constraint said they could
    not. This script still needs *some* stable key or a re-import would insert a
    second copy of everyone, and `ON CONFLICT (name)` now has no constraint to
    infer — it would fail outright.

    `source_key` is the honest one. The HR snapshot is a JSON object keyed by
    name, so names are unique **within that file** whether or not they are
    unique among humans; this key claims exactly that and nothing more.
    """
    return f"{SOURCE}:{name.strip().lower()}"


UPSERT = """
INSERT INTO people
    (name, email, role, department, team, reports_to, status, skills,
     resume_summary, years_experience, domain,
     capacity_hours_per_week, current_load_hours_per_week,
     available_hours_per_week, clickup_user_id, source, source_key,
     synced_at, updated_at)
VALUES
    (:name, :email, :role, :department, :team, :reports_to, :status, :skills,
     :resume_summary, :years_experience, :domain,
     :capacity, :load, :available, :clickup_user_id, :source, :source_key,
     now(), now())
ON CONFLICT (source_key) WHERE source_key IS NOT NULL DO UPDATE SET
    name = EXCLUDED.name,
    email = COALESCE(EXCLUDED.email, people.email),
    role = EXCLUDED.role,
    department = EXCLUDED.department,
    team = EXCLUDED.team,
    reports_to = EXCLUDED.reports_to,
    status = EXCLUDED.status,
    skills = EXCLUDED.skills,
    resume_summary = COALESCE(EXCLUDED.resume_summary, people.resume_summary),
    years_experience = COALESCE(EXCLUDED.years_experience, people.years_experience),
    domain = COALESCE(EXCLUDED.domain, people.domain),
    capacity_hours_per_week = EXCLUDED.capacity_hours_per_week,
    current_load_hours_per_week = EXCLUDED.current_load_hours_per_week,
    available_hours_per_week = EXCLUDED.available_hours_per_week,
    clickup_user_id = COALESCE(EXCLUDED.clickup_user_id, people.clickup_user_id),
    synced_at = now(), updated_at = now()
"""


def build_rows(hr: dict, resumes: dict) -> list[dict]:
    """Flatten the org chart and merge resume skills (public + testable)."""
    # Resume profiles are keyed by person name (see agent-data/INDEX.md);
    # index by lowercase name AND email for tolerant matching.
    by_name: dict[str, dict] = {}
    by_email: dict[str, dict] = {}
    for p in resumes.get("profiles", []) or []:
        if p.get("name"):
            by_name[p["name"].strip().lower()] = p
        if p.get("email"):
            by_email[p["email"].strip().lower()] = p

    rows: list[dict] = []
    for dept in hr.get("departments", []) or []:
        head = dept.get("head")
        for team in dept.get("teams", []) or []:
            for m in team.get("members", []) or []:
                name = (m.get("name") or "").strip()
                if not name:
                    continue
                prof = (
                    by_name.get(name.lower())
                    or by_email.get((m.get("email") or "").strip().lower())
                    or {}
                )
                # Merge skills: org chart first, then resume extras (deduped,
                # case-insensitively, order-preserving).
                skills: list[str] = []
                for s in (m.get("skills") or []) + (prof.get("skills") or []):
                    s = (s or "").strip()
                    if s and s.lower() not in (x.lower() for x in skills):
                        skills.append(s)
                rows.append({
                    "name": name,
                    "source": SOURCE,
                    "source_key": source_key(name),
                    "email": m.get("email") or prof.get("email"),
                    "role": m.get("role"),
                    "department": dept.get("name"),
                    "team": team.get("name"),
                    # the department head reports to no one within their dept
                    "reports_to": head if head and head != name else None,
                    "status": m.get("status") or "active",
                    "skills": skills,
                    "resume_summary": (prof.get("experience_summary") or None),
                    "years_experience": prof.get("years_experience"),
                    "domain": (
                        prof.get("domain")
                        if prof.get("domain") not in (None, "", "Unknown")
                        else None
                    ),
                    "capacity": m.get("capacity_hours_per_week"),
                    "load": m.get("current_load_hours_per_week"),
                    "available": m.get("available_hours_per_week"),
                    "clickup_user_id": (
                        str(m["clickup_user_id"])
                        if m.get("clickup_user_id") is not None else None
                    ),
                })

    # Migration 148 put a partial UNIQUE on lower(email). Two snapshot members
    # carrying the same address would now fail the whole import on a unique
    # violation, so the duplicate is dropped to NULL here — at the source, where
    # the row is still identifiable — rather than discovered as a psql error.
    # First occurrence keeps the address; the person still imports, they just
    # arrive without an email until somebody says which one is theirs.
    seen_emails: set[str] = set()
    for row in rows:
        address = (row.get("email") or "").strip().lower()
        if not address:
            row["email"] = None
            continue
        if address in seen_emails:
            row["email"] = None
            continue
        seen_emails.add(address)
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", default=str(REPO_ROOT / "infra" / "seed" / "hr"))
    args = ap.parse_args()
    seed = Path(args.seed_dir)

    hr = json.loads((seed / "hr_structure.json").read_text())
    resumes_file = seed / "resume_profiles.json"
    resumes = json.loads(resumes_file.read_text()) if resumes_file.exists() else {}
    rows = build_rows(hr, resumes)
    print(f"{len(rows)} people in {hr.get('company', 'the org chart')}")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://acb:acb@localhost:5432/acb"
    )
    if "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        for r in rows:
            await conn.execute(text(UPSERT), r)
        count = (await conn.execute(
            text("SELECT count(*) FROM people"))).scalar()
    await engine.dispose()
    print(f"upserted {len(rows)} → people now has {count} rows")


if __name__ == "__main__":
    asyncio.run(main())
