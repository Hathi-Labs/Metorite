#!/usr/bin/env python3
"""Backfill the embeddings of email (WS-KG KG-2, Metorite #787, D128).

Spec: ``project-docs/specs/company_context.md`` §12, row KG-2. The sweep of
the email scheduler embeds a few messages after each sync, and only when the
GLOBAL flag ``email_semantic_search_enabled`` is on. This script fills the old
mail of the orgs that you name. It does not need the flag, and it never turns
it on: it passes ``force=True`` to ``select_pending_embeddings``.

For each mailbox of each org it loops over three steps, each in its own
``tenant_session(org)``, and the model call holds NO session:

1. ``select_pending_embeddings(batch, force=True)`` reads the candidates.
2. ``compute_embeddings`` asks the embed door (the Router, ``tier-embed``).
   It runs as the mailbox owner, with ``module_slug="email"``, so the
   ``usage_event`` rows are separable.
3. ``write_embeddings`` upserts the vectors.

A pause follows each batch. The run of a mailbox stops after
:data:`MAX_FAILED_IN_A_ROW` batches in a row that wrote nothing. The upsert
keys on ``message_id`` and the SQL predicate checks ``content_hash``, so a
second run writes 0 rows. A stopped run keeps its committed batches.

    uv run python scripts/backfill_email_embeddings.py --estimate --org <uuid>
    uv run python scripts/backfill_email_embeddings.py --org <uuid> --org <uuid>
    uv run python scripts/backfill_email_embeddings.py --org <uuid> --batch 64 --pause 0.5

``--estimate`` prints the row count, the token estimate (``min(chars, 8000) / 4``
for each row) and the rate from ``model_rate_card``. It calls no embed. The rate
needs ``CUSTOMER_CONSOLE_DATABASE_URL``, and it reads ``unavailable`` without it.

It prints counts only, never mail text. Report the counts in the same message
as the run (CLAUDE.md §3a rule 2). Fence: ``tests/unit/test_email_embeddings_backfill_r8.py``.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

#: Batches in a row that write nothing, after which one mailbox stops.
MAX_FAILED_IN_A_ROW = 3
DEFAULT_BATCH = 64


async def _account_ids(org: str) -> list[str]:
    from gateway.db import tenant_session
    from sqlalchemy import text

    async with tenant_session(org) as db:
        rows = (await db.execute(text(
            "SELECT id FROM email_accounts ORDER BY created_at, id"))).fetchall()
    return [str(r.id) for r in rows]


async def _estimate(orgs: list[str]) -> dict[str, int]:
    from email_ingestion import email_embeddings as ee
    from gateway.db import tenant_session

    total = {"rows": 0, "tokens": 0}
    for org in orgs:
        rows = tokens = 0
        for acct in await _account_ids(org):
            async with tenant_session(org) as db:
                n, t = await ee.count_pending(db, acct)
            rows += n
            tokens += t
        print(f"{org}: rows={rows} tokens_estimate={tokens}", flush=True)
        total["rows"] += rows
        total["tokens"] += tokens
    return total


async def _run(orgs: list[str], batch: int, pause_s: float) -> dict[str, int]:
    from email_ingestion import email_embeddings as ee
    from gateway.db import tenant_session

    total = {"rows": 0, "tokens": 0, "failures": 0, "batches": 0}
    for org in orgs:
        got = {"rows": 0, "tokens": 0, "failures": 0, "batches": 0}
        for acct in await _account_ids(org):
            failed_in_a_row = 0
            while failed_in_a_row < MAX_FAILED_IN_A_ROW:
                async with tenant_session(org) as db:
                    pending = await ee.select_pending_embeddings(
                        db, acct, batch=batch, force=True)
                if pending is None:
                    break
                got["batches"] += 1
                # No session is open across the model call.
                vectors = await ee.compute_embeddings(pending)
                written = 0
                if vectors is not None:
                    async with tenant_session(org) as db:
                        written = await ee.write_embeddings(db, acct, pending, vectors)
                got["rows"] += written
                got["tokens"] += sum(len(t) for t in pending.texts) // 4 if written else 0
                got["failures"] += len(pending.message_ids) - written
                failed_in_a_row = 0 if written else failed_in_a_row + 1
                if pause_s > 0:
                    await asyncio.sleep(pause_s)
        print(f"{org}: " + " ".join(f"{k}={v}" for k, v in got.items()), flush=True)
        for k, v in got.items():
            total[k] += v
    return total


def _rate() -> str:
    """The rate of the embed model from ``model_rate_card``, or ``unavailable``."""
    url = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL")
    if not url:
        return "unavailable (CUSTOMER_CONSOLE_DATABASE_URL unset)"
    try:
        from gateway.kb_index.embed import EMBED_MODEL
        from sqlalchemy import create_engine, text

        engine = create_engine(url.replace("postgresql+asyncpg", "postgresql+psycopg"))
        try:
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT input_credits_per_1k FROM model_rate_card "
                    "WHERE model = :m AND effective_from <= now() "
                    "ORDER BY effective_from DESC LIMIT 1"), {"m": EMBED_MODEL}).fetchone()
        finally:
            engine.dispose()
    except Exception as exc:  # the rate is advice, never a reason to stop
        return f"unavailable ({type(exc).__name__})"
    if row is None:
        return f"no row for {EMBED_MODEL} (the Router draws no credit, D19.2)"
    return f"{row[0]} credits per 1k tokens for {EMBED_MODEL}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", action="append", default=[], help="an organization id (repeat)")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="messages in one embed call")
    ap.add_argument("--pause", type=float, default=0.5, help="seconds between batches")
    ap.add_argument("--estimate", action="store_true",
                    help="print the row count, the token estimate and the rate. Embeds nothing")
    args = ap.parse_args(argv)
    if not args.org:
        print("Pass --org <uuid> (repeat it for more orgs).")
        return 2
    if not 1 <= args.batch <= 64:
        print("--batch must be 1 to 64.")
        return 2
    if args.estimate:
        total = asyncio.run(_estimate(args.org))
        print("total: " + " ".join(f"{k}={v}" for k, v in total.items()))
        print(f"rate: {_rate()}")
        return 0
    total = asyncio.run(_run(args.org, args.batch, args.pause))
    print("total: " + " ".join(f"{k}={v}" for k, v in total.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
