#!/usr/bin/env python3
"""Backfill the float8 copy of each Studio number (WS-STU STU-C2c).

Spec: ``project-docs/specs/ai_studio.md`` §14, row C2c. Migration 432 adds
``studio_record_hot.v_num_f8`` and a trigger that fills it for each new row and
each changed ``v_num``. A row from before 432 keeps NULL until this script
fills it. The store honours ``STUDIO_NUM_F8_ORGS`` for an org only when the
org has no row without its copy. So the flag does nothing for an org until this
script reports ``remaining=0`` for it. The records are right at every step.

It fills one batch for each organization in one short transaction through
``tenant_session(org)``, with :func:`gateway.studio.objects_store.fill_num_f8_batch`,
then sleeps. A second run changes nothing. A stopped
run keeps its committed batches, and the next run takes the rest.

    uv run python scripts/backfill_studio_num_f8.py --org <uuid>
    uv run python scripts/backfill_studio_num_f8.py --all
    uv run python scripts/backfill_studio_num_f8.py --all --batch 500 --pause 0.5

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


async def _fill(org: str, batch: int, pause_s: float) -> dict[str, int]:
    from acb_common.db import tenant_session
    from gateway.studio import objects_store as store

    got = {"batches": 0, "filled": 0, "remaining": 0}
    after = store.NUM_F8_START
    while True:
        async with tenant_session(org) as db:
            keys = await store.fill_num_f8_batch(db, after, batch)
        if not keys:
            break
        got["batches"] += 1
        got["filled"] += len(keys)
        after = max(keys)
        if pause_s > 0:
            await asyncio.sleep(pause_s)
    async with tenant_session(org) as db:
        got["remaining"] = await store.count_num_f8_left(db)
    return got


async def _run(orgs: list[str], batch: int, pause_s: float) -> dict[str, int]:
    total = {"batches": 0, "filled": 0, "remaining": 0}
    for org in orgs:
        got = await _fill(org, batch, pause_s)
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
    return 0 if total["remaining"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
