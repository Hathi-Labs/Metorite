#!/usr/bin/env python3
"""Backfill the page links of Knowledge pages written before KB-2h (K-2).

Spec: ``project-docs/specs/knowledge_base.md`` §21.5 (KB-2h).

A page write keeps the pages that its body links to in ``kb_links``. A page
written before KB-2h has no such rows until its next write. This script gives
each page that names a page, and has no page-kind row, the rows that the write
path would make. It runs :func:`gateway.routes.knowledge.backlinks.backfill_org_links`,
which calls the write path's own ``sync_page_links``. A second run changes
nothing.

    uv run python scripts/backfill_kb_links.py --org <uuid>
    uv run python scripts/backfill_kb_links.py --all
    uv run python scripts/backfill_kb_links.py --org <uuid> --dry-run

Runs as the migration runner does, on the box, with ``DATABASE_URL`` of the
tenant database. Every read and write goes through ``tenant_session(org)``, so
FORCE RLS stamps and checks the tenant. Report the counts in the same message
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


async def _run(orgs: list[str], dry_run: bool) -> dict[str, int]:
    from gateway.routes.knowledge.backlinks import backfill_org_links

    total = {"seen": 0, "linked": 0, "rows": 0, "unchanged": 0}
    for org in orgs:
        got = await backfill_org_links(org, dry_run=dry_run)
        print(f"{org}: " + " ".join(f"{k}={v}" for k, v in got.items()))
        for k, v in got.items():
            total[k] += v
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", help="one organization id")
    ap.add_argument("--all", action="store_true", help="every organization")
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = ap.parse_args(argv)
    orgs = _orgs(args.all, args.org)
    if not orgs:
        print("Pass --org <uuid> or --all.")
        return 2
    total = asyncio.run(_run(orgs, args.dry_run))
    print("total: " + " ".join(f"{k}={v}" for k, v in total.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
