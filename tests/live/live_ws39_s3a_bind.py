"""WS-39 S3a — the timestamptz BIND, against the real driver (R8).

Board WS-39 slice S3a · decisions D53 + D54 · migration 187.

── Why this file exists ─────────────────────────────────────────────────────

Migration 187 adds four `timestamptz` columns to `pm_task_personal`. The
gateway writes them through bare `text()`, which declares no column types to
asyncpg — so `routes/projects/core.coerce_write_values` has to convert an ISO
string to a `datetime` first, keyed off the `TIMESTAMP_COLUMNS` allow-list.

Adding columns without adding them to that list is a ONE-LINE omission that a
hermetic suite cannot see: the fake stores whatever it is handed and every
assertion still passes. This is the exact class the board records as "five live
bugs shipped green that way".

It was not hypothetical here. Building S3a produced BOTH halves of it:

  * the four new columns were missing from `TIMESTAMP_COLUMNS`, so every
    write of a scheduled block would have 500'd on a real database;
  * `my_calendar` fed its `start`/`end` window through `coerce_write_values`,
    which keys off COLUMN names — and those two are bind PARAMETERS, so they
    passed through as strings and every calendar read would have 500'd.

Both are fixed. This script is what proves the fix was needed, and it is the
cheapest possible reproduction: one temp table, three binds.

── How to run ───────────────────────────────────────────────────────────────

    LIVE_DSN="postgresql+asyncpg://<u>:<p>@<host>:<port>/<db>" \
        uv run python tests/live/live_ws39_s3a_bind.py

── Result, 2026-08-24, pgvector/pgvector:pg16, asyncpg ──────────────────────

    datetime bind: OK
    string bind:   REFUSED   <-- asyncpg.exceptions.DataError: invalid input for query
    string range:  REFUSED   <-- asyncpg.exceptions.DataError: invalid input for query

The second and third lines are the point: a string does NOT silently work, so
the allow-list is load-bearing rather than cosmetic, on the write path AND on
the range comparison.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import asyncio, datetime, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ["LIVE_DSN"]

async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.begin() as db:
        await db.execute(text(
            "CREATE TEMP TABLE bindcheck (id int, at timestamptz)"))
        # 1. a real datetime — the shape coerce_write_values produces
        await db.execute(text("INSERT INTO bindcheck VALUES (1, :at)"),
                         {"at": datetime.datetime(2026, 9, 1, 9, 0,
                                                  tzinfo=datetime.timezone.utc)})
        print("datetime bind: OK")
        # 2. a bare ISO string — the shape a missing allow-list entry produces
        try:
            await db.execute(text("INSERT INTO bindcheck VALUES (2, :at)"),
                             {"at": "2026-09-01T09:00:00+00:00"})
            print("string bind:   ACCEPTED  <-- allow-list would be cosmetic")
        except Exception as exc:
            print(f"string bind:   REFUSED   <-- {type(exc).__name__}: "
                  f"{str(exc).splitlines()[0][:110]}")
        # 3. the range comparison the calendar window performs
        try:
            rows = (await db.execute(
                text("SELECT count(*) FROM bindcheck WHERE at >= :s AND at < :e"),
                {"s": "2026-09-01T00:00:00+00:00", "e": "2026-09-08T00:00:00+00:00"},
            )).scalar()
            print(f"string range:  ACCEPTED (count={rows})")
        except Exception as exc:
            print(f"string range:  REFUSED   <-- {type(exc).__name__}: "
                  f"{str(exc).splitlines()[0][:110]}")
    await eng.dispose()

asyncio.run(main())
