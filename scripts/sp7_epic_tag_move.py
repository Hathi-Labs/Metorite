#!/usr/bin/env python3
"""SP-7: give each top-level task tagged "Epic" the Epic type, once.

Spec: ``project-docs/specs/project_management_app.md`` §13, slice SP-7.
Code: ``gateway.routes.projects.epic_tag_move``. The tag stays. A task with a
parent, or in a project with no epic type, is skipped and listed.

    uv run python scripts/sp7_epic_tag_move.py --org <uuid> --dry-run
    uv run python scripts/sp7_epic_tag_move.py --all --dry-run
    uv run python scripts/sp7_epic_tag_move.py --org <uuid>

Run it dry first, and put the counts on the task. A customer organization
needs the owner's OK on its count. Runs as the migration runner does, with
``DATABASE_URL`` of the tenant database. Report the counts in the same message
as the run (CLAUDE.md §3a rule 2).
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def _orgs(all_orgs: bool, org: str | None) -> list[str]:
    from acb_graph import get_session
    from sqlalchemy import text

    if org:
        return [org]
    if not all_orgs:
        return []
    with get_session() as s:
        rows = s.execute(text("SELECT id FROM organization ORDER BY created_at")).fetchall()
    return [str(r[0]) for r in rows]


async def _run(orgs: list[str], dry_run: bool) -> int:
    from gateway.routes.projects.epic_tag_move import move_org

    moved = 0
    for org in orgs:
        got = await move_org(org, dry_run=dry_run)
        print(f"{org}: found={got['found']} moved={got['moved']} "
              f"skipped={len(got['skipped'])}")
        for s in got["skipped"]:
            print(f"  skipped #{s['task_number']} {s['title']!r}: {s['reason']}")
        moved += got["moved"]
    return moved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", help="one organization id")
    ap.add_argument("--all", action="store_true", help="every organization")
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = ap.parse_args(argv)
    if args.all and not args.dry_run:
        print("--all needs --dry-run. Put the counts on the task, then run each org with --org.")
        return 2
    orgs = _orgs(args.all, args.org)
    if not orgs:
        print("Pass --org <uuid> or --all.")
        return 2
    total = asyncio.run(_run(orgs, args.dry_run))
    print(f"total moved{' (dry run)' if args.dry_run else ''}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
