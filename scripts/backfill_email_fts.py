#!/usr/bin/env python3
"""Backfill the stored search vector of each email (WS-KG KG-3a).

Spec: ``project-docs/specs/company_context.md`` §12, row KG-3a. Migration 314
adds ``email_messages.fts`` and a trigger that fills it for each new row and
each changed row. A row from before 314 keeps NULL until this script fills it.
The search reads ``COALESCE(em.fts, <the expression>)``, so it gives the same
answer before, during and after the run.

It runs :func:`gateway.routes.email.transport.search.backfill_fts` for each
organization. That function fills one batch of rows in one short transaction
through ``tenant_session(org)``, then sleeps. A second run changes nothing. A
stopped run keeps its committed batches, and the next run takes the rest.

    uv run python scripts/backfill_email_fts.py --org <uuid>
    uv run python scripts/backfill_email_fts.py --all
    uv run python scripts/backfill_email_fts.py --all --batch 500 --pause 0.5

Run it on the box with ``DATABASE_URL`` of the tenant database. It prints
counts only, never a row. Report the counts in the same message as the run
(CLAUDE.md §3a rule 2).
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


async def _run(orgs: list[str], batch: int, pause_s: float) -> dict[str, int]:
    from gateway.routes.email.transport.search import backfill_fts

    total = {"batches": 0, "filled": 0}
    for org in orgs:
        got = await backfill_fts(org, batch=batch, pause_s=pause_s)
        print(f"{org}: " + " ".join(f"{k}={v}" for k, v in got.items()), flush=True)
        for k, v in got.items():
            total[k] += v
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", help="one organization id")
    ap.add_argument("--all", action="store_true", help="every organization")
    ap.add_argument("--batch", type=int, default=1000, help="rows in one transaction")
    ap.add_argument("--pause", type=float, default=0.2, help="seconds between batches")
    args = ap.parse_args(argv)
    if args.batch < 1:
        print("--batch must be 1 or more.")
        return 2
    orgs = _orgs(args.all, args.org)
    if not orgs:
        print("Pass --org <uuid> or --all.")
        return 2
    total = asyncio.run(_run(orgs, args.batch, args.pause))
    print("total: " + " ".join(f"{k}={v}" for k, v in total.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
