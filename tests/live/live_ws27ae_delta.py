"""WS-27ae against a REAL Postgres. Two organizations, the real endpoints.

The delta-sync feed (P-27) and the small columns (P-28). What only a database
can answer, and why the hermetic suite cannot:

* **the tombstone TRIGGER fires on a cascade.** `tests/unit/_projects_fakes.py`
  models no foreign keys, so deleting a `pm_projects` row there leaves its tasks
  alone and writes no tombstones. Postgres takes both — and a project delete is
  exactly the case where a client is holding the most rows about to become
  ghosts. The fake can only prove the endpoint path.
* **the ROW COMPARISON binds.** `(t.updated_at, t.id) > (:since_ts,
  CAST(:since_id AS uuid))` mixes a `timestamptz` and a `uuid` in one tuple with
  a bound datetime and a bound str. asyncpg infers parameter types from the cast
  and has refused perfectly reasonable-looking binds before (WS-27k, WS-27r);
  a text mirror has no type system and would agree with either.
* **the horizon comes from Postgres's clock, not the gateway's**, and
  `now()` inside a transaction is TRANSACTION-start time — the whole reason the
  horizon exists. Only a real transaction can show that.
* **an organization can still be deleted.** MEASURED here first: with a plain
  FK and no guard, the AFTER DELETE trigger inserts a tombstone referencing the
  organization the same statement is removing, and `DELETE FROM organization`
  fails. The guard in migration 168 is the fix; this pins both halves.
* **and the whole point:** with two organizations holding tasks stamped at the
  same instants, does a feed read as A ever return one of B's rows or removals?

Run:
    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    uv run python tests/live/live_ws27ae.py

⚠️ ``seed()`` TRUNCATEs ``pm_projects CASCADE`` and DELETEs the tombstones of
the two test organizations. Scratch databases only.
"""
import asyncio
import os
import pathlib
import sys
from datetime import UTC, datetime, timedelta

os.environ["DATABASE_URL"] = os.environ.get(
    "WS27AE_DATABASE_URL",
    "postgresql+asyncpg://postgres@/ccae?host=/var/tmp&port=55432",
)
# Derived from THIS file, not hard-coded: run from a git worktree, an absolute
# `/home/user/Metorite/...` path silently imports the MAIN checkout's
# gateway and the script then reports on code that is not under test.
sys.path.insert(0, str(
    pathlib.Path(__file__).resolve().parents[2] / "apps" / "services" / "gateway"
))

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from gateway.db import get_db
from gateway.routes.projects import delta as pm_delta
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import views as pm_views
from sqlalchemy import text

ANA = "ana@alpha.example"
BEN = "ben@beta.example"

A_PROJ = "aeaeaeae-0000-4000-8000-0000000000a1"
B_PROJ = "aeaeaeae-0000-4000-8000-0000000000b1"
A_GONE_PROJ = "aeaeaeae-0000-4000-8000-0000000000a9"
A_STATUS = "aeaeaeae-0000-4000-8000-0000000000a2"
B_STATUS = "aeaeaeae-0000-4000-8000-0000000000b2"
A_TRIAGE = "aeaeaeae-0000-4000-8000-0000000000a7"
A_GONE_STATUS = "aeaeaeae-0000-4000-8000-0000000000a8"
A_VIEW = "aeaeaeae-0000-4000-8000-0000000000a6"

# Two alpha tasks stamped at the SAME instant — the boundary case.
A_ONE = "aeaeaeae-0000-4000-8000-0000000000a3"
A_TWO = "aeaeaeae-0000-4000-8000-0000000000a4"
A_DOOMED = "aeaeaeae-0000-4000-8000-0000000000a5"
A_CASCADED = "aeaeaeae-0000-4000-8000-0000000000aa"
# One beta task, stamped at the same instant as alpha's. Anything that tells
# them apart can only be the tenant.
B_ONE = "aeaeaeae-0000-4000-8000-0000000000b3"

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


# ⚠️ `bind_tenant` returns a RESET TOKEN and `release_tenant` needs it back;
# calling it with nothing raises, which is how the first run of this script
# failed. Every block below binds into `_token` and releases with it, so no
# check can leak a tenant into the next one — the property the two-organization
# assertions rest on.
def user(email: str, *, features: str = "feature:projects") -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access([features]),
    )


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        # ⚠️ TRUNCATE fires no ROW triggers and `pm_task_tombstones` has no FK
        # to `pm_projects` (deliberately — a tombstone outlives its project), so
        # the truncate above leaves yesterday's tombstones behind.
        await db.execute(text(
            "DELETE FROM pm_task_tombstones WHERE organization_id IN "
            "(SELECT id FROM organization WHERE slug IN ('ae_alpha','ae_beta'))"
        ))
        await db.execute(
            text("DELETE FROM app_user WHERE email = ANY(:e)"),
            {"e": [ANA, BEN]},
        )
        await db.execute(
            text("DELETE FROM organization WHERE slug IN ('ae_alpha','ae_beta')")
        )
        orgs = {}
        for slug in ("ae_alpha", "ae_beta"):
            row = (await db.execute(text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"), {"s": slug})).fetchone()
            orgs[slug] = str(row.id)
        for email, slug in ((ANA, "ae_alpha"), (BEN, "ae_beta")):
            await db.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": orgs[slug]})

        for pid, name, slug, owner in (
            (A_PROJ, "Alpha delivery", "ae_alpha", ANA),
            (A_GONE_PROJ, "Alpha, about to be deleted", "ae_alpha", ANA),
            (B_PROJ, "Beta delivery", "ae_beta", BEN),
        ):
            await db.execute(text(
                "INSERT INTO pm_projects (id, name, source, created_by, "
                "organization_id) VALUES (CAST(:p AS uuid), :n, 'manual', :who, "
                "CAST(:o AS uuid))"),
                {"p": pid, "n": name, "who": owner, "o": orgs[slug]})
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:p AS uuid), 'org', :who)"),
                {"p": pid, "who": owner})
        # ⚠️ organization_id deliberately NOT supplied below — migration 161's
        # trigger must derive it from the parent, as it does everywhere else.
        for sid, pid, name, category in (
            (A_STATUS, A_PROJ, "To do", "todo"),
            (A_TRIAGE, A_PROJ, "Inbox", "triage"),
            (A_GONE_STATUS, A_GONE_PROJ, "To do", "todo"),
            (B_STATUS, B_PROJ, "To do", "todo"),
        ):
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:s AS uuid), "
                "CAST(:p AS uuid), :n, 10, :c, :d)"),
                {"s": sid, "p": pid, "n": name, "c": category,
                 "d": category == "todo"})
        # Everything stamped an hour ago: comfortably outside the feed's
        # horizon, so nothing below is withheld for the wrong reason.
        for tid, pid, sid, number, title in (
            (A_ONE, A_PROJ, A_STATUS, 1, "Alpha one"),
            (A_TWO, A_PROJ, A_STATUS, 2, "Alpha two"),
            (A_DOOMED, A_PROJ, A_STATUS, 3, "Alpha, about to be deleted"),
            (A_CASCADED, A_GONE_PROJ, A_GONE_STATUS, 1, "Alpha, cascaded away"),
            (B_ONE, B_PROJ, B_STATUS, 1, "Beta one"),
        ):
            await db.execute(text(
                "INSERT INTO pm_tasks (id, project_id, root_project_id, "
                "status_id, title, task_number, created_by, created_at, "
                "updated_at) VALUES (CAST(:t AS uuid), CAST(:p AS uuid), "
                "CAST(:p AS uuid), CAST(:s AS uuid), :ti, :n, :who, "
                "now() - interval '1 hour', now() - interval '1 hour')"),
                {"t": tid, "p": pid, "s": sid, "ti": title, "n": number,
                 "who": ANA})
        await db.execute(text(
            "INSERT INTO pm_views (id, project_id, name, view_type, config, "
            "created_by) VALUES (CAST(:v AS uuid), CAST(:p AS uuid), 'Board', "
            "'board', '{}'::jsonb, :who)"),
            {"v": A_VIEW, "p": A_PROJ, "who": ANA})
        await db.commit()
        return orgs
    finally:
        await db.close()


async def stamp_of(task_id: str):
    db = await get_db()
    try:
        row = (await db.execute(text(
            "SELECT updated_at FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": task_id})).fetchone()
        return row.updated_at if row else None
    finally:
        await db.close()


async def backdate(*task_ids: str):
    """Push rows back outside the horizon so the next read can see them."""
    db = await get_db()
    try:
        await db.execute(
            text(
                "UPDATE pm_tasks SET updated_at = now() - interval '30 minutes' "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": list(task_ids)},
        )
        await db.execute(text(
            "UPDATE pm_task_tombstones SET deleted_at = now() - "
            "interval '30 minutes' WHERE deleted_at > now() - interval '1 hour'"
        ))
        await db.commit()
    finally:
        await db.close()


def epoch_cursor() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).isoformat()


async def feed_checks(orgs):
    """The two-tenant proof, and the boundary the cursor exists for."""
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        snapshot = await pm_delta.delta_tasks(user=user(ANA))
    finally:
        release_tenant(_token)
    ids = {r["id"] for r in snapshot["rows"]}
    check("alpha's snapshot holds alpha's four tasks",
          ids, {A_ONE, A_TWO, A_DOOMED, A_CASCADED})
    check("alpha's snapshot holds NONE of beta's", B_ONE in ids, False)
    check("a snapshot says so", snapshot["snapshot"], True)
    check("an exhausted snapshot reports no more", snapshot["has_more"], False)

    _token = bind_tenant(orgs["ae_beta"])
    try:
        theirs = await pm_delta.delta_tasks(user=user(BEN))
    finally:
        release_tenant(_token)
    check("beta's snapshot holds only beta's task",
          {r["id"] for r in theirs["rows"]}, {B_ONE})

    # ── The boundary. Four rows share one instant (seeded `now() - 1 hour`
    # inside ONE transaction, so `now()` gave all four the same stamp — which
    # is exactly the tie a bare-timestamp cursor cannot page through).
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        seen: list[str] = []
        cursor = epoch_cursor()
        for _ in range(6):
            page = await pm_delta.delta_tasks(
                user=user(ANA), since=cursor, limit=1,
            )
            seen.extend(r["id"] for r in page["rows"])
            cursor = page["cursor"]
            if not page["has_more"]:
                break
    finally:
        release_tenant(_token)
    check("paging one row at a time over a shared instant sees each ONCE",
          sorted(seen), sorted([A_ONE, A_TWO, A_DOOMED, A_CASCADED]))
    check("…and exactly once each", len(seen), len(set(seen)))

    # The cursor after an exhausted scan must return nothing, not the page
    # again. This is the assertion that fails if the horizon cursor is wrong.
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        idle = await pm_delta.delta_tasks(user=user(ANA), since=cursor)
    finally:
        release_tenant(_token)
    check("an exhausted cursor returns nothing", idle["rows"], [])


async def horizon_checks(orgs):
    """A row written NOW is withheld; the same row an hour old is not."""
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        db = await get_db()
        try:
            await db.execute(
                text(
                    "UPDATE pm_tasks SET updated_at = now() "
                    "WHERE id = CAST(:t AS uuid)"
                ),
                {"t": A_ONE},
            )
            await db.commit()
        finally:
            await db.close()
        fresh = await pm_delta.delta_tasks(user=user(ANA), since=epoch_cursor())
        check("a row written this second is withheld by the horizon",
              A_ONE in {r["id"] for r in fresh["rows"]}, False)
    finally:
        release_tenant(_token)

    await backdate(A_ONE)
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        later = await pm_delta.delta_tasks(user=user(ANA), since=epoch_cursor())
        check("…and delivered once it is older than the lag",
              A_ONE in {r["id"] for r in later["rows"]}, True)
    finally:
        release_tenant(_token)


async def satellite_checks(orgs):
    """A satellite write must move the PARENT's own row."""
    before = await stamp_of(A_TWO)
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        await pm_tasks.set_assignees(
            A_TWO, pm_tasks.AssigneesIn(assignees=[BEN]), user=user(ANA),
        )
    finally:
        release_tenant(_token)
    check("assigning bumps the task's own updated_at",
          await stamp_of(A_TWO) > before, True)

    before = await stamp_of(A_ONE)
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        link = await pm_tasks.create_link(
            A_ONE,
            pm_tasks.LinkIn(target_task_id=A_TWO, link_type="blocks"),
            user=user(ANA),
        )
    finally:
        release_tenant(_token)
    check("linking bumps the SOURCE", await stamp_of(A_ONE) > before, True)

    target_before = await stamp_of(A_TWO)
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        await pm_tasks.delete_link(A_ONE, str(link["id"]), user=user(ANA))
    finally:
        release_tenant(_token)
    check("unlinking bumps the TARGET too — the end nothing named",
          await stamp_of(A_TWO) > target_before, True)

    # And the bump reaches the FEED, which is the only reason it matters.
    await backdate(A_ONE, A_TWO)
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        page = await pm_delta.delta_tasks(
            user=user(ANA),
            since=(datetime.now(UTC) - timedelta(minutes=45)).isoformat(),
        )
    finally:
        release_tenant(_token)
    check("the bumped tasks appear in a delta since before the bump",
          {A_ONE, A_TWO} <= {r["id"] for r in page["rows"]}, True)


async def removal_checks(orgs):
    """The deletion answer, both paths — and the one Postgres alone can show."""
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        await pm_tasks.delete_task(A_DOOMED, user=user(ANA))
    finally:
        release_tenant(_token)

    # ⚠️ The path the hermetic fake CANNOT model: a project CASCADE. No
    # endpoint touched this task; Postgres took it with its project, and the
    # trigger is the only thing that could have recorded it.
    db = await get_db()
    try:
        await db.execute(
            text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
            {"p": A_GONE_PROJ},
        )
        await db.commit()
    finally:
        await db.close()
    db = await get_db()
    try:
        rows = (await db.execute(
            text(
                "SELECT task_id, task_number, organization_id, project_id "
                "FROM pm_task_tombstones WHERE task_id = ANY(CAST(:i AS uuid[])) "
                "ORDER BY task_id"
            ),
            {"i": [A_DOOMED, A_CASCADED]},
        )).fetchall()
    finally:
        await db.close()
    check("both deletes left a tombstone", len(rows), 2)
    check("the cascaded task was tombstoned by the TRIGGER, not the endpoint",
          A_CASCADED in {str(r.task_id) for r in rows}, True)
    check("every tombstone is stamped with the task's own tenant",
          {str(r.organization_id) for r in rows}, {orgs["ae_alpha"]})
    check("the human id survives the row",
          sorted(int(r.task_number) for r in rows), [1, 3])

    await backdate()
    _token = bind_tenant(orgs["ae_alpha"])
    try:
        page = await pm_delta.delta_tasks(user=user(ANA), since=epoch_cursor())
    finally:
        release_tenant(_token)
    check("the feed reports BOTH deletions as removals",
          {A_DOOMED, A_CASCADED} <= set(page["removed"]), True)
    check("a deleted task is not also served as a row",
          A_DOOMED in {r["id"] for r in page["rows"]}, False)

    # ⚠️ The cascaded tombstone names a project that no longer exists, so the
    # project-visibility closure cannot match it. Stated here rather than
    # discovered: the client learns about it in the SAME page only because the
    # closure is evaluated per row and a missing project matches nothing…
    check("…and the removal set is reported inside the tenant only",
          all(t not in page["removed"] for t in (B_ONE,)), True)

    _token = bind_tenant(orgs["ae_beta"])
    try:
        theirs = await pm_delta.delta_tasks(user=user(BEN), since=epoch_cursor())
    finally:
        release_tenant(_token)
    check("BETA is told nothing about alpha's deletions",
          set(theirs["removed"]) & {A_DOOMED, A_CASCADED}, set())


async def triage_and_archive_checks(orgs):
    """A task that LEAVES the feed's scope is a removal, not silence."""
    db = await get_db()
    try:
        await db.execute(
            text(
                "UPDATE pm_tasks SET status_id = CAST(:s AS uuid), "
                "updated_at = now() - interval '30 minutes' "
                "WHERE id = CAST(:t AS uuid)"
            ),
            {"s": A_TRIAGE, "t": A_TWO},
        )
        await db.commit()
    finally:
        await db.close()

    _token = bind_tenant(orgs["ae_alpha"])
    try:
        hidden = await pm_delta.delta_tasks(user=user(ANA), since=epoch_cursor())
        asked = await pm_delta.delta_tasks(
            user=user(ANA), since=epoch_cursor(), include_triage=True,
        )
    finally:
        release_tenant(_token)
    check("a task parked in triage is reported REMOVED, not merely absent",
          A_TWO in set(hidden["removed"]), True)
    check("…and never leaks into the rows",
          A_TWO in {r["id"] for r in hidden["rows"]}, False)
    check("…while include_triage brings it back as a row",
          A_TWO in {r["id"] for r in asked["rows"]}, True)


async def column_checks(orgs):
    """The small columns, and that something reads each of them."""
    db = await get_db()
    try:
        row = (await db.execute(text(
            "SELECT column_name, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = 'pm_task_types' "
            "AND column_name = 'is_epic'"))).fetchone()
        check("is_epic exists", row is not None, True)
        check("is_epic is NOT NULL with a constant default",
              (row.is_nullable, row.column_default) if row else None,
              ("NO", "false"))
        # The backfill: every type the OLD seed-name rule matched is stamped, so
        # `core.is_epic_type` reading either arm gives the same answer.
        mismatched = (await db.execute(text(
            "SELECT count(*) FROM pm_task_types "
            "WHERE is_system AND name = 'Epic' AND NOT is_epic"))).scalar()
        check("no seeded Epic was left unstamped by the backfill",
              int(mismatched or 0), 0)
    finally:
        await db.close()

    _token = bind_tenant(orgs["ae_alpha"])
    try:
        await pm_views.set_view_state(
            A_VIEW,
            pm_views.UserStateIn(config={
                "collapsed_lanes": ["done"], "group_by": "assignee",
                # Dropped by the overlay normaliser: a per-person filter would
                # make one shared view mean two different sets of tasks.
                "filters": {"assignee": BEN},
            }),
            user=user(ANA),
        )
        mine = await pm_views.list_views(A_PROJ, user=user(ANA))
    finally:
        release_tenant(_token)
    state = mine["rows"][0]["user_state"]
    check("the per-user overlay round-trips through JSONB",
          state, {"group_by": "assignee", "collapsed_lanes": ["done"]})

    db = await get_db()
    try:
        row = (await db.execute(text(
            "SELECT organization_id FROM pm_view_user_state "
            "WHERE view_id = CAST(:v AS uuid)"), {"v": A_VIEW})).fetchone()
        check("migration 161's trigger filled the overlay's tenant",
              str(row.organization_id) if row else None, orgs["ae_alpha"])
    finally:
        await db.close()

    # A colleague in ANOTHER tenant must see nothing of it — and the write
    # itself must refuse, because the view is not theirs to arrange.
    _token = bind_tenant(orgs["ae_beta"])
    try:
        try:
            await pm_views.get_view_state(A_VIEW, user=user(BEN))
            check("another tenant cannot read the overlay's view at all",
                  "no exception", "HTTPException 404")
        except Exception as exc:
            check("another tenant cannot read the overlay's view at all",
                  f"{type(exc).__name__} {getattr(exc, 'status_code', '')}",
                  "HTTPException 404")
    finally:
        release_tenant(_token)


async def organization_delete_check(orgs):
    """⚠️ The measured one. Without migration 168's guard this RAISES.

    Deleting an organization cascades to `pm_projects` → `pm_tasks`, firing the
    tombstone trigger, which would insert a row referencing the organization the
    same statement has already removed. The first version of this migration made
    an organization undeletable; the guard is why it does not.
    """
    db = await get_db()
    try:
        row = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES ('ae_doomed','ae_doomed') RETURNING id"))).fetchone()
        org = str(row.id)
        await db.execute(text(
            "INSERT INTO pm_projects (id, name, source, created_by, "
            "organization_id) VALUES (gen_random_uuid(), 'Doomed', 'manual', "
            ":who, CAST(:o AS uuid))"), {"who": ANA, "o": org})
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, "
            "category, is_default) SELECT gen_random_uuid(), id, 'To do', 10, "
            "'todo', true FROM pm_projects WHERE organization_id = "
            "CAST(:o AS uuid)"), {"o": org})
        await db.execute(text(
            "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
            "title, task_number, created_by) SELECT gen_random_uuid(), p.id, "
            "p.id, s.id, 'doomed', 1, :who FROM pm_projects p JOIN "
            "pm_task_statuses s ON s.project_id = p.id WHERE p.organization_id "
            "= CAST(:o AS uuid)"), {"who": ANA, "o": org})
        await db.commit()
    finally:
        await db.close()

    db = await get_db()
    try:
        await db.execute(
            text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
            {"o": org},
        )
        await db.commit()
        check("an organization is still deletable with the trigger in place",
              "deleted", "deleted")
    except Exception as exc:  # pragma: no cover — the failure this pins
        check("an organization is still deletable with the trigger in place",
              type(exc).__name__, "deleted")
        print(f"     {exc}")
    finally:
        await db.close()


async def main():
    orgs = await seed()
    await feed_checks(orgs)
    await horizon_checks(orgs)
    await satellite_checks(orgs)
    await removal_checks(orgs)
    await triage_and_archive_checks(orgs)
    await column_checks(orgs)
    await organization_delete_check(orgs)
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
