"""WS-39 S3a-server-2 — the jsonb BIND, against the real driver (R8).

Board WS-39 slice S3a-server-2 · decision D53 · migration 188.

── Why this file exists, when `live_ws39_s3a_bind.py` already covers instants ─

188 adds three more `timestamptz` columns (`delegated_at`, `expected_by`,
`last_nudged_at`), and its sibling file already proves the allow-list is
load-bearing for those. This file exists for the column that is NOT an instant:
`waiting_on`, the first **jsonb** column on `pm_task_personal`.

That first-ness is the whole risk. `_upsert_personal` built its statement by
hand — a bare `:{column}` placeholder per value, bound through
`coerce_write_values` — and that is correct for exactly as long as the overlay
holds no jsonb. A jsonb column needs two things the hand-rolled form skips, and
they fail in two DIFFERENT ways:

  1. the `CAST(... AS jsonb)` in the statement. Without it Postgres is handed
     text for a jsonb column and refuses the parameter outright;
  2. the `json.dumps` before binding. Without it a bare Python `dict` reaches
     asyncpg, which has no codec for it and cannot even encode the argument.

Both are supplied by the shared seam (`core._placeholder` / `core._bindable`),
which is why the fix was to make `_upsert_personal` USE the seam rather than to
special-case `waiting_on` in it. A parallel implementation of an existing seam
is the CLAUDE.md §5 defect, and it is also how the next jsonb column repeats
this bug.

A hermetic fake sees none of it: it stores whatever object it is handed and
answers with the same object, so `waiting_on == {...}` passes whether the value
ever survived a round trip or not.

── How to run ───────────────────────────────────────────────────────────────

    LIVE_DSN="postgresql+asyncpg://<u>:<p>@<host>:<port>/<db>" \
        uv run python tests/live/live_ws39_s3a_server2_bind.py

── Result, 2026-08-25, PostgreSQL 16 (tenant-scratch), SQLAlchemy 2 + asyncpg

    dumps + CAST:        OK           {'name': 'Priya', ...} (driver: dict)
    bare dict + CAST:    REFUSED   <-- asyncpg DataError: invalid input
    dumps, NO cast:      ACCEPTED
    NULL through CAST:   OK  (None) <-- clearing a delegation works
    expected_by string:  REFUSED   <-- asyncpg DataError: invalid input

⚠️ **Two of these contradict what this file predicted before it was run, and
the measurements win.** Recorded rather than quietly edited away, because the
difference changes which line of the fix is load-bearing:

  * `dumps, NO cast` was expected to be REFUSED. It is **ACCEPTED** — this
    dialect resolves the parameter type from the target column, so the explicit
    CAST is *defensive*, not required. `_placeholder` still earns its place
    (`insert_row`/`update_row` have used it since WS-27l, and the cast is what
    makes the statement say what it means), but it is NOT the line that would
    have broken.
  * the round trip hands back a **dict**, not the string `core.from_jsonb`'s
    docstring describes. See the note at that assertion.

So the one genuinely load-bearing half is `_bindable`: a bare dict is refused
outright, and that IS a 500 on the first real request. The hermetic fake agrees
with either, which is the whole reason this file exists (R8).
"""

import asyncio
import datetime
import json
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ["LIVE_DSN"]


def _line(label: str, exc: Exception) -> str:
    return f"{label:<20} REFUSED   <-- {type(exc).__name__}: {str(exc).splitlines()[0][:90]}"


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.begin() as db:
        await db.execute(text(
            "CREATE TEMP TABLE bindcheck188 "
            "(id int, doc jsonb, expected_by timestamptz)"))

        payload = {"name": "Priya", "email": "priya@fracktal.in"}

        # 1. What the seam produces: json.dumps + an explicit cast.
        await db.execute(
            text("INSERT INTO bindcheck188 (id, doc) "
                 "VALUES (1, CAST(:doc AS jsonb))"),
            {"doc": json.dumps(payload)},
        )
        back = (await db.execute(
            text("SELECT doc FROM bindcheck188 WHERE id = 1"))).scalar()
        # ⚠️ MEASURED 2026-08-25, and it corrects a premise stated in the tree.
        # `core.from_jsonb`'s docstring says "Raw `text()` over asyncpg returns
        # jsonb as a **string** — there is no declared column type to decode
        # against". Against THIS stack (SQLAlchemy 2 + asyncpg, PostgreSQL 16)
        # it comes back as a **dict**: the dialect registers a jsonb codec on
        # the connection, so the decode happens below SQLAlchemy regardless of
        # what the statement declares.
        #
        # The code is right either way and the helper stays: `from_jsonb`
        # passes a non-str through untouched, which is exactly why it survives
        # a driver that disagrees with the comment above it. What is NOT safe is
        # deleting the call because "asyncpg already decodes it" — that holds
        # for this dialect, and `routes/crm/core.py` records the other
        # behaviour. Assert the VALUE, not the wire type; the type is the
        # driver's business and it has changed once already.
        decoded = json.loads(back) if isinstance(back, str) else back
        assert decoded == payload, f"round trip changed the value: {decoded!r}"
        print(f"{'dumps + CAST:':<20} OK           {decoded} "
              f"(driver handed back {type(back).__name__})")

        # 2. A bare dict — what binding without `_bindable` produces.
        try:
            await db.execute(
                text("INSERT INTO bindcheck188 (id, doc) "
                     "VALUES (2, CAST(:doc AS jsonb))"),
                {"doc": payload},
            )
            print(f"{'bare dict + CAST:':<20} ACCEPTED  <-- json.dumps would be cosmetic")
        except Exception as exc:
            print(_line("bare dict + CAST:", exc))

        # 3. Serialized, but no cast — what binding without `_placeholder`
        #    produces. Different failure, same 500.
        try:
            await db.execute(
                text("INSERT INTO bindcheck188 (id, doc) VALUES (3, :doc)"),
                {"doc": json.dumps(payload)},
            )
            print(f"{'dumps, NO cast:':<20} ACCEPTED  <-- the CAST would be cosmetic")
        except Exception as exc:
            print(_line("dumps, NO cast:", exc))

        # 4. NULL through the cast — clearing a delegation. It must NOT be
        #    turned into the jsonb literal `null`, which is a value and would
        #    keep the task on the Waiting list forever.
        await db.execute(
            text("INSERT INTO bindcheck188 (id, doc) "
                 "VALUES (4, CAST(:doc AS jsonb))"),
            {"doc": None},
        )
        cleared = (await db.execute(
            text("SELECT doc FROM bindcheck188 WHERE id = 4"))).scalar()
        assert cleared is None, f"clearing produced {cleared!r}, not SQL NULL"
        print(f"{'NULL through CAST:':<20} OK  (None) <-- clearing works")

        # 5. 188's three new instants, same allow-list rule as 187's four.
        await db.execute(
            text("UPDATE bindcheck188 SET expected_by = :e WHERE id = 1"),
            {"e": datetime.datetime(2026, 9, 8, 17, 0, tzinfo=datetime.UTC)},
        )
        try:
            await db.execute(
                text("UPDATE bindcheck188 SET expected_by = :e WHERE id = 1"),
                {"e": "2026-09-08T17:00:00+00:00"},
            )
            print(f"{'expected_by string:':<20} ACCEPTED  <-- allow-list cosmetic")
        except Exception as exc:
            print(_line("expected_by string:", exc))

    await eng.dispose()


asyncio.run(main())
