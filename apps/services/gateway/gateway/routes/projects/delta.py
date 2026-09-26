"""Projects · delta — the sync feed for agents and mobile (WS-27ae / P-27).

Spec: ``project-docs/specs/project_management_app.md`` §9.2 (WS-27ae) and
§11.27 (the as-built). Research: ``plane_pm_research_2026-08.md`` §3.7 / P-27.

    GET /projects/delta/tasks   → {rows, removed, cursor, has_more, snapshot}

*"Give me what changed since X"*, so a phone or an agent does not re-pull a
board of six hundred tasks to learn that one moved lane.

Three things about this endpoint are load-bearing, and each of them is a trap
the naive version falls into.

**1. It reports REMOVALS, because ``updated_at > :since`` cannot.**
A row that is deleted stops appearing, and a client that merges what it receives
keeps the ghost forever — silently, and for as long as the client lives. So
``removed[]`` is a first-class half of the answer and it has two sources:

⚠️ *Corrected 2026-08-10.* This paragraph used to claim "Plane's feed has exactly
this hole". It does not — Plane ships a separate deleted-ids endpoint filtered by
``updated_at``. Ours is still the better shape (a keyset cursor, a tombstone
TRIGGER so a CASCADE is recorded, and removals delivered in-band rather than from
a second endpoint a client must remember to poll), but the comparison as written
was false, and a reviewer who checked it would rightly have doubted every other
claim in this file.

* **hard deletes** — migration 168's ``pm_task_tombstones``, written by an
  ``AFTER DELETE`` trigger on ``pm_tasks`` so a project CASCADE is recorded as
  faithfully as a ``DELETE /projects/tasks/{id}``;
* **fell out of scope** — a task that changed *and* no longer satisfies the
  feed's own predicate (it was archived, it moved into ``triage``, it moved to a
  project outside the requested subtree). It still exists, but it is no longer
  part of what the caller asked for, and a client that only merges rows would
  keep rendering it in a lane it left.

⚠️ **One removal shape is NOT expressible and is documented rather than
pretended away: losing VISIBILITY.** If a grant is revoked, or a task moves to a
project the caller cannot see, the row simply stops matching the visibility
clause — there is no record anywhere that it used to match, and manufacturing
one would mean storing a per-member history of everything anybody could ever
see. A client MUST therefore reconcile with a full pull periodically
(:data:`RECONCILE_AFTER`, reported on every response so the client does not have
to hard-code our policy). ``test_projects_delta.py`` pins this both ways: that
removals ARE reported for the two expressible shapes, and that a revoked grant
produces silence — so the day somebody "fixes" it, the documentation and the
test move together.

**2. The cursor is a KEYSET, not a timestamp.**
``updated_at > :since`` with a client-supplied second loses every row that
shares the boundary second with a delivered one — and re-sending them instead
(``>=``) makes a page of ties loop forever. The cursor is the composite
``(updated_at, id)`` of the LAST ROW ACTUALLY DELIVERED, compared with SQL's row
comparison, so the boundary is exact in both directions: nothing is skipped and
nothing repeats. A bare ISO timestamp is still accepted for a client's first
call and is treated as inclusive of that instant (it pairs with the zero UUID),
because re-delivering is safe and skipping is not.

**3. There is a HORIZON, because ``now()`` is transaction-start time.**
A writer whose transaction began before this read commits afterwards with an
``updated_at`` stamped at ITS start — which can be earlier than a row this read
already delivered, and therefore behind the cursor forever. The feed refuses to
look at anything newer than ``now() - HORIZON_LAG_SECONDS``, so the cursor can
only be dragged into that window by a write transaction that stayed open longer
than the lag. That is a stated, bounded property rather than a guarantee: with
the default it is "a request that held its transaction open for more than five
seconds while another request committed after it". Every write path in this
package is one short request, and the residual is named in §11.27 rather than
left for somebody to discover.

**R11.** The tenant is the session's, resolved through ``resolve_visibility``
like every other read here. Nothing about the caller's identity or organization
comes from the query string.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    TaskModel,
    _tenant_session,
    load_visible_project,
    resolve_visibility,
    router,
    row_to_dict,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.filters import (
    attach_assignees,
    attach_parent_context,
    attach_relation_counts,
)
from sqlalchemy import text

#: How far back from ``now()`` the feed refuses to look. See rule 3 above: this
#: is the width of the window in which a concurrent writer's transaction-start
#: stamp could land behind an already-delivered cursor.
#:
#: Interpolated into our own SQL as a literal rather than bound: it is an int
#: this module owns, never caller text, and ``CAST(:x AS interval)`` has the
#: same asyncpg problem ``filters.parse_when`` documents for ``timestamptz``.
HORIZON_LAG_SECONDS = 5

#: One page of the feed. Larger than the board's page size because a sync client
#: wants throughput rather than a screen, and bounded for the reason
#: ``views.MAX_POSITIONS`` is: a single request that can drain the table is a
#: denial-of-service surface rather than a feature.
DEFAULT_DELTA_LIMIT = 200
MAX_DELTA_LIMIT = 500

#: What the endpoint tells every client about the visibility hole above. Hours,
#: reported on the wire so the policy lives in ONE place — the day it changes,
#: clients follow without a release.
RECONCILE_AFTER = "PT24H"

#: The id half of a cursor built from a bare timestamp. Sorts below every real
#: UUID, so ``(ts, id) > (ts, ZERO)`` includes everything stamped at exactly
#: ``ts`` — inclusive, which is the safe direction.
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
#: The id half of the cursor emitted when the scan is EXHAUSTED. Sorts above
#: every real UUID, so the next call starts strictly after the horizon it was
#: built from and re-reads nothing.
_MAX_UUID = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def encode_cursor(when: datetime, task_id: str) -> str:
    """``(updated_at, id)`` → the opaque string a client sends back."""
    return f"{when.isoformat()}|{task_id}"


def parse_cursor(raw: str | None) -> tuple[datetime, str] | None:
    """A client's ``since`` → the keyset point to compare against, or ``None``.

    Accepts both shapes on purpose:

    * ``2026-08-10T09:00:00Z|3f2c…`` — a cursor we emitted. Exact.
    * ``2026-08-10T09:00:00Z`` — a bare instant, which is what a client has on
      its very first call (or after a clock-based reconcile). Paired with the
      zero UUID, so it is INCLUSIVE of that instant: re-delivering a row is
      idempotent for an upsert client, skipping one is not.

    A malformed value is a **422**, never a silent fall back to "everything" —
    a typo'd cursor quietly re-pulling the whole board is the failure that looks
    like a performance problem for a month.
    """
    if raw is None or not raw.strip():
        return None
    text_value = raw.strip()
    stamp, _, ident = text_value.rpartition("|")
    if not stamp:
        stamp, ident = text_value, _ZERO_UUID
    try:
        when = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"'{raw}' is not a valid delta cursor. Send back the "
                   f"`cursor` from a previous response, or an ISO 8601 "
                   f"timestamp, e.g. 2026-08-31T17:00:00Z.",
        ) from None
    try:
        ident = str(UUID(ident.strip()))
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"'{raw}' is not a valid delta cursor: '{ident}' is not a "
                   f"task id.",
        ) from None
    # Naive means UTC, the frame `filters.parse_when` already established for
    # this package rather than whatever the connection's TimeZone happens to be.
    return (when if when.tzinfo else when.replace(tzinfo=UTC)), ident


@router.get("/delta/tasks")
async def delta_tasks(
    user: UserContext = Depends(get_current_user),
    since: str | None = None,
    limit: int = DEFAULT_DELTA_LIMIT,
    project_id: str | None = None,
    include_subtree: bool = True,
    include_archived: bool = False,
    include_triage: bool = False,
) -> dict:
    """What changed since ``since``, and what is no longer there.

    ⚠️ **The path is ``/projects/delta/tasks``, not ``/projects/tasks/delta``.**
    The second would be shadowed by ``/projects/tasks/{task_id}``, which is
    registered first and would match ``delta`` as an id — the route-ordering
    trap ``routes/workflows/__init__.py`` documents and this package's
    ``__init__`` claims immunity from precisely because every path here is a
    literal that cannot collide.

    Omitting ``since`` is a **snapshot**: the whole visible set, one page at a
    time, with a cursor to continue from. That is the same code path rather than
    a second one, so a client's first sync and its thousandth agree by
    construction about what "the set" is.
    """
    if limit < 1 or limit > MAX_DELTA_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {MAX_DELTA_LIMIT}; got {limit}.",
        )
    cursor = parse_cursor(since)

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        if project_id:
            # ⚠️ BEFORE the streams, not inside the "did anything change"
            # branch. Seeing the project is required to filter by it, so an
            # unreadable id must be 404 — and a 404 that only appears when
            # something happens to have changed is not a refusal, it is a
            # coincidence. An empty feed reads as "nothing changed", which is
            # the answer a sync client acts on.
            await load_visible_project(db, vis, project_id)
        # The horizon is read from the DATABASE's clock, once, and then BOUND
        # into both streams. Three reasons, in order of how much they cost:
        # the two streams must share one horizon or the merge is over two
        # different windows; the exhausted-scan cursor below has to be able to
        # name it; and the gateway's clock is not the one `now()` uses.
        # Interpolating our own int constant into our own SQL is safe —
        # `CAST(:x AS interval)` is not, for `filters.parse_when`'s asyncpg
        # reason.
        horizon = _aware((await db.execute(
            text(
                f"SELECT (now() - interval '{HORIZON_LAG_SECONDS} seconds') "
                f"AS delta_horizon"
            ),
        )).fetchone().delta_horizon)
        params: dict[str, Any] = {**vis.params, "horizon": horizon}
        keyset = ""
        if cursor is not None:
            params["since_ts"], params["since_id"] = cursor
            keyset = (
                " AND (%s, %s) > (:since_ts, CAST(:since_id AS uuid))"
            )

        # ── Stream A: tasks whose own row changed ──────────────────────────
        #
        # NO project scoping, NO archived filter, NO triage exclusion. That is
        # deliberate and it is what makes `removed[]` able to say anything at
        # all: a task that moved out of the requested subtree, or was archived,
        # or was parked in triage, must still be SEEN here so it can be
        # partitioned into `removed` below. Filtering it out at this step is
        # exactly the naive feed's blind spot.
        changed = (await db.execute(
            text(
                "SELECT t.id, t.updated_at FROM pm_tasks t"
                f" WHERE {task_visibility_clause(vis)}"
                " AND t.updated_at <= :horizon"
                + (keyset % ("t.updated_at", "t.id") if keyset else "")
                + " ORDER BY t.updated_at ASC, t.id ASC LIMIT :limit"
            ),
            {**params, "limit": limit},
        )).fetchall()

        # ── Stream B: tombstones ───────────────────────────────────────────
        #
        # Scoped by the SAME project-visibility closure the tasks are, joined
        # through the tombstone's own recorded `project_id`. Without it, "task
        # 9f3… is gone" would be readable by every member of the tenant
        # regardless of grants — a weak leak, but a leak through the one
        # surface built to be polled continuously.
        #
        # ⚠️ THE SECOND ARM IS NOT BELT-AND-BRACES, and it was found by
        # `tests/live/live_ws27ae_delta.py` rather than reasoned about: deleting a
        # PROJECT cascades to its tasks, so the tombstones it leaves name a
        # project row that no longer exists — and the closure, which resolves
        # grants through `pm_projects`, can never match one. With the first arm
        # alone, the single deletion that strands the MOST rows on a client is
        # the one deletion the feed cannot report. The tenant predicate above
        # still bounds it, so what a member can learn from the extra arm is the
        # uuid and the instant of a task in a project of their own company that
        # has since been deleted — no title, no content. That is the trade this
        # takes, deliberately, against a permanent ghost on every device.
        #
        # ⚠️ Its only fence is `tests/live/live_ws27ae_delta.py`. Measured: deleting
        # this arm leaves the whole hermetic suite GREEN (the fake models no
        # foreign keys, so nothing there can produce an orphaned tombstone) and
        # turns the live check red. Do not read a green `pytest` as cover for
        # this line.
        gone = (await db.execute(
            text(
                "SELECT tb.task_id, tb.deleted_at"
                " FROM pm_task_tombstones tb"
                " WHERE tb.organization_id = CAST(:vis_org AS uuid)"
                f" AND ({vis.project_clause('tb.project_id')}"
                "      OR NOT EXISTS (SELECT 1 FROM pm_projects p_gone"
                "                     WHERE p_gone.id = tb.project_id))"
                " AND tb.deleted_at <= :horizon"
                + (keyset % ("tb.deleted_at", "tb.task_id") if keyset else "")
                + " ORDER BY tb.deleted_at ASC, tb.task_id ASC LIMIT :limit"
            ),
            {**params, "limit": limit},
        )).fetchall()

        # ── Merge, then page ───────────────────────────────────────────────
        #
        # Both streams are ordered and both were filtered `> cursor`, so the
        # first `limit` of their merged prefixes IS the first `limit` of the
        # union — the standard k-way merge argument, and the reason this is not
        # one `UNION ALL` statement: two simple statements the fake can read
        # beat one clever one it cannot.
        entries = sorted(
            [
                *((_aware(r.updated_at), str(r.id), "row") for r in changed),
                *((_aware(r.deleted_at), str(r.task_id), "gone") for r in gone),
            ],
            key=lambda e: (e[0], e[1]),
        )
        # ⚠️ `len(entries) > limit` ALONE IS WRONG, and wrong in the direction
        # that loses data: a stream that came back holding exactly `limit` rows
        # may have more behind it, and if the other stream is empty the merged
        # list is exactly `limit` long. Reading that as "exhausted" would jump
        # the cursor to the horizon and skip everything after this page —
        # silently, and only under a load big enough to fill a page.
        has_more = (
            len(entries) > limit
            or len(changed) >= limit
            or len(gone) >= limit
        )
        entries = entries[:limit]

        row_ids = [ident for _, ident, kind in entries if kind == "row"]
        removed = [ident for _, ident, kind in entries if kind == "gone"]

        # ── The feed predicate, over exactly the ids stream A returned ─────
        #
        # Everything a normal `/projects/tasks` read would apply. Whatever does
        # NOT come back changed but is no longer part of what the caller asked
        # for, which is a removal from the client's point of view.
        page_rows: list[dict] = []
        if row_ids:
            clauses = [
                task_visibility_clause(vis),
                "t.id = ANY(CAST(:ids AS uuid[]))",
            ]
            scoped = dict(params)
            scoped["ids"] = row_ids
            if project_id:
                if include_subtree:
                    clauses.append(
                        "t.project_id IN ("
                        "  WITH RECURSIVE sub AS ("
                        "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
                        "    UNION ALL"
                        "    SELECT p.id FROM pm_projects p JOIN sub s"
                        "      ON p.parent_project_id = s.id"
                        "  ) SELECT id FROM sub)"
                    )
                else:
                    clauses.append("t.project_id = CAST(:pid AS uuid)")
                scoped["pid"] = project_id
            if not include_archived:
                clauses.append("t.archived_at IS NULL")
            if not include_triage:
                clauses.append(triage_exclusion_clause())
            rows = (await db.execute(
                text(
                    "SELECT t.* FROM pm_tasks t WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY t.updated_at ASC, t.id ASC"
                ),
                scoped,
            )).fetchall()
            page_rows = [row_to_dict(r, TaskModel) for r in rows]
            # The same attachers the list endpoint uses, so a synced card can
            # draw an owner and a progress count without an N+1 per row.
            await attach_assignees(db, page_rows)
            await attach_relation_counts(db, page_rows, vis)
            # D-PM-38. The board merges these rows into the list's, so they
            # carry the same `parent` the list gives, or a synced card would
            # lose its "↳ Parent" line.
            await attach_parent_context(db, vis, page_rows)
            kept = {str(r["id"]) for r in page_rows}
            removed.extend(ident for ident in row_ids if ident not in kept)

        # ── The cursor ─────────────────────────────────────────────────────
        #
        # Advanced to the LAST DELIVERED entry, never to `now()`. When the scan
        # is exhausted the horizon itself is safe — every row at or below it has
        # been delivered — and advancing there is what stops an idle client
        # re-scanning the same page forever.
        if entries:
            last_ts, last_id, _ = entries[-1]
            next_cursor = encode_cursor(last_ts, last_id)
        else:
            next_cursor = since if since else None
        if not has_more:
            next_cursor = encode_cursor(horizon, _MAX_UUID)

        return {
            "rows": page_rows,
            # Sorted so a response is stable to compare, and de-duplicated
            # because a task can in principle appear in both streams (deleted
            # after being edited inside one page).
            "removed": sorted(set(removed)),
            "cursor": next_cursor,
            "has_more": has_more,
            # `snapshot` says which KIND of answer this is: without a cursor the
            # rows are the whole visible set, not a delta, and a client must
            # replace rather than merge — the distinction that decides whether
            # its local store is allowed to keep anything it did not receive.
            "snapshot": cursor is None,
            # The visibility hole, on the wire. See the module docstring.
            "reconcile_after": RECONCILE_AFTER,
        }


def _aware(value: Any) -> datetime:
    """A stamp from either stream as an aware instant.

    asyncpg returns ``datetime``; the hermetic fake may hold an ISO string. Both
    are normalised here rather than at four call sites, and a naive value is
    read as UTC — the frame this package works in.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
