"""WS-41 I-6 — discard one import run, against a real Postgres (R8).

Spec `project-docs/specs/project_import.md` §6.9 "Discard", §9 row I-6 · **D80**.

── What this proves ─────────────────────────────────────────────────────────

1. A discard removes exactly the run's tasks and nodes. The delta feed gets a
   tombstone for every task, and the run is `discarded`.
2. A member's edit on one of the run's tasks refuses the discard, names the
   task, and deletes nothing.
3. A member's node inside a space the run created refuses the discard.
4. An update run on top of an earlier run: the earlier run refuses while the
   later one stands. The later run's discard removes its comment on the
   earlier task, keeps its field updates, and leaves the earlier tasks. Then
   the earlier run discards cleanly. This is the trap the design exists for.
5. An open run is closed with nothing to undo.

It drives the REAL writer and the REAL discard, which commit, so it works in
fresh organizations and deletes them.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws41_discard.py
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_ws41_writer as w
from gateway.db import tenant_session
from gateway.routes.projects import import_discard, import_writer, imports
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.plan import ImportMapping
from sqlalchemy import text

check, one, rows = w.check, w.one, w.rows


#: One admin per organization: `app_user.email` is unique across tenants.
ADMINS: dict[str, str] = {}


async def run_import(org: str, bundle: object, raw: bytes) -> str:
    run_id, lease = await w.new_run(org, ADMINS[org], bundle, raw, ImportMapping())
    await import_writer.apply_run(org, run_id, lease)
    return run_id


async def load(org: str, run_id: str) -> object:
    async with tenant_session(org) as db:
        return (await db.execute(text(imports.LOAD_RUN_SQL), {"id": run_id, "org": org})).fetchone()


async def discard(
    org: str, run_id: str
) -> tuple[dict | None, import_discard.DiscardRefused | None]:
    """What the route does for a written run: one transaction, commit or not."""
    row = await load(org, run_id)
    try:
        async with tenant_session(org) as db:
            _saved, counts = await import_discard.discard_written_run(db, org, row)
        return counts, None
    except import_discard.DiscardRefused as refused:
        return None, refused


async def run_tasks(org: str, run_id: str) -> int:
    return int(
        await one(
            org,
            "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
            " AND origin->>'run_id' = :r",
            r=run_id,
        )
        or 0
    )


async def import_nodes(org: str) -> int:
    return int(
        await one(
            org,
            "SELECT count(*) FROM pm_projects WHERE organization_id = CAST(:org AS uuid) "
            " AND source = 'import'",
        )
        or 0
    )


def edited_export(raw: bytes) -> tuple[bytes, str, str]:
    """The fixture with one task renamed and one new comment on another."""
    table = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    col = {name: i for i, name in enumerate(table[0])}
    roots = [r for r in table[1:] if r[col["Parent ID"]] == "null"]
    a, b = roots[0][col["Task ID"]], roots[1][col["Task ID"]]
    for row in table[1:]:
        if row[col["Task ID"]] == a:
            row[col["Task Name"]] = "Renamed in ClickUp"
            # A status its list lacks, and a tag the space lacks: the later run
            # adds a lane and a tag to the EARLIER run's nodes, after that run
            # ended. The review's P1: they must not read as a member's work.
            row[col["Status"]] = "waiting on vendor"
            row[col["Tags"]] = "[vendor hold]"
        elif row[col["Task ID"]] == b:
            row[col["Comments"]] = json.dumps(
                [
                    {
                        "text": "A comment the later export adds\n",
                        "by": "person1@example.com",
                        "date": "9/27/2026, 10:00:00 AM GMT+5:30",
                        "assigned": False,
                        "resolved": "N/A",
                    }
                ]
            )
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(table)
    return out.getvalue().encode("utf-8"), a, b


async def main() -> None:
    raw = w.FIXTURE.read_bytes()
    bundle = clickup.parse([(w.FIXTURE.name, raw)])
    orgs: list[str] = []
    await catalog_fence()
    try:
        for i in range(3):
            admin = f"discard{i}.{w.TAG}@acme.test"
            org = await w.seed(f"d{i}", [admin])
            ADMINS[org] = admin
            orgs.append(org)
        await clean_discard(orgs[0], bundle, raw)
        await refusals(orgs[1], bundle, raw)
        await update_chain(orgs[2], bundle, raw)
    finally:
        for org in orgs:
            await w.drop(org)


async def clean_discard(org: str, bundle: object, raw: bytes) -> None:
    run = await run_import(org, bundle, raw)
    ids = [
        str(r.id)
        for r in await rows(
            org,
            "SELECT id FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
            " AND origin->>'run_id' = :r",
            r=run,
        )
    ]
    nodes = await import_nodes(org)
    counts, refused = await discard(org, run)
    check("1.1 a fresh import discards", refused is None, refused.message if refused else "")
    check(
        "1.2 it reports the run's tasks and nodes",
        counts is not None and counts["tasks"] == 2423 and counts["nodes"] == nodes == 62,
        json.dumps(counts),
    )
    check(
        "1.3 no task and no node of the run is left",
        await run_tasks(org, run) == 0 and await import_nodes(org) == 0,
    )
    stones = await one(
        org,
        "SELECT count(*) FROM pm_task_tombstones WHERE task_id = ANY(CAST(:ids AS uuid[]))",
        ids=ids,
    )
    check(
        "1.4 the delta feed gets a tombstone per task", stones == len(ids), f"{stones}/{len(ids)}"
    )
    state = await one(org, "SELECT state FROM pm_import_runs WHERE id = CAST(:r AS uuid)", r=run)
    check("1.5 the run is discarded", state == "discarded", str(state))
    _, again = await discard(org, run)
    check("1.6 a second discard is refused", again is not None)

    # An open run: nothing written, nothing to undo.
    planned, _lease = await w.new_run(org, ADMINS[org], bundle, raw, ImportMapping())
    async with tenant_session(org) as db:
        await db.execute(
            text("UPDATE pm_import_runs SET state = 'planned' WHERE id = CAST(:r AS uuid)"),
            {"r": planned},
        )
        closed = (
            await db.execute(text(imports.DISCARD_OPEN_SQL), {"id": planned, "org": org})
        ).fetchone()
    check(
        "5.1 an open run closes with nothing to undo",
        closed is not None and closed.state == "discarded",
    )


async def refusals(org: str, bundle: object, raw: bytes) -> None:
    run = await run_import(org, bundle, raw)
    task = (
        await rows(
            org,
            "SELECT id FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
            " AND origin->>'run_id' = :r LIMIT 1",
            r=run,
        )
    )[0].id
    async with tenant_session(org) as db:
        await db.execute(
            text(
                "UPDATE pm_tasks SET title = 'A member renamed it', updated_at = now() + interval '1 second' WHERE id = :t"
            ),
            {"t": task},
        )
    _, refused = await discard(org, run)
    check(
        "2.1 a member's edit refuses the discard, and names the task",
        refused is not None and any(b["id"] == str(task) for b in refused.blocking),
        refused.message if refused else "not refused",
    )
    check("2.2 the refusal deletes nothing", await run_tasks(org, run) == 2423)
    async with tenant_session(org) as db:
        # Put the task back the way the import left it, so the next rule is
        # the only one that can refuse.
        await db.execute(
            text(
                "UPDATE pm_tasks SET updated_at = (SELECT finished_at - interval '1 second' "
                "  FROM pm_import_runs WHERE id = CAST(:r AS uuid)) WHERE id = :t"
            ),
            {"r": run, "t": task},
        )
        await db.execute(
            text(
                "INSERT INTO pm_activities (task_id, type, body, created_by) "
                "VALUES (:t, 'comment', 'A member wrote this', :who)"
            ),
            {"t": task, "who": ADMINS[org]},
        )
    _, refused = await discard(org, run)
    check(
        "2.3 a member's comment refuses the discard too",
        refused is not None and any(b["id"] == str(task) for b in refused.blocking),
        refused.message if refused else "not refused",
    )
    async with tenant_session(org) as db:
        await db.execute(
            text("DELETE FROM pm_activities WHERE task_id = :t AND meta IS NULL"), {"t": task}
        )
        space = (
            await db.execute(
                text(
                    "SELECT id FROM pm_projects WHERE organization_id = CAST(:o AS uuid) "
                    " AND source = 'import' AND parent_project_id IS NULL LIMIT 1"
                ),
                {"o": org},
            )
        ).scalar()
        await db.execute(
            text(
                "INSERT INTO pm_projects (name, organization_id, parent_project_id, kind, created_by) "
                "VALUES ('A member made this', CAST(:o AS uuid), :p, 'project', :who)"
            ),
            {"o": org, "p": space, "who": ADMINS[org]},
        )
    _, refused = await discard(org, run)
    check(
        "3.1 a member's node inside an imported space refuses the discard",
        refused is not None and any(b["title"] == "A member made this" for b in refused.blocking),
        refused.message if refused else "not refused",
    )
    check("3.2 the refusal deletes nothing", await run_tasks(org, run) == 2423)

    # The review's P1: rows that CASCADE with a node, and the task's own
    # member rows, refuse the discard too. Each is added alone, then removed.
    async with tenant_session(org) as db:
        await db.execute(
            text(
                "DELETE FROM pm_projects WHERE name = 'A member made this' AND organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        )
        lst = (
            await db.execute(
                text(
                    "SELECT id FROM pm_projects WHERE organization_id = CAST(:o AS uuid) "
                    " AND source = 'import' AND parent_project_id IS NOT NULL AND kind = 'project' LIMIT 1"
                ),
                {"o": org},
            )
        ).scalar()
        await db.execute(
            text(
                "INSERT INTO pm_views (project_id, name, created_by) VALUES (:p, 'My board', :who)"
            ),
            {"p": lst, "who": ADMINS[org]},
        )
    _, refused = await discard(org, run)
    check(
        "3.3 a member's saved view on an imported list refuses the discard",
        refused is not None and any("a saved view" in b["title"] for b in refused.blocking),
        refused.message if refused else "not refused",
    )
    async with tenant_session(org) as db:
        await db.execute(text("DELETE FROM pm_views WHERE project_id = :p"), {"p": lst})
        await db.execute(
            text("INSERT INTO pm_task_personal (task_id, member_email) VALUES (:t, :who)"),
            {"t": task, "who": ADMINS[org]},
        )
    _, refused = await discard(org, run)
    check(
        "2.4 a member's personal triage of a task refuses the discard",
        refused is not None and any(b["id"] == str(task) for b in refused.blocking),
        refused.message if refused else "not refused",
    )
    async with tenant_session(org) as db:
        await db.execute(text("DELETE FROM pm_task_personal WHERE task_id = :t"), {"t": task})
    counts, refused = await discard(org, run)
    check(
        "3.4 with the member work gone, the same run discards",
        refused is None and counts is not None and counts["tasks"] == 2423,
        refused.message if refused else json.dumps(counts),
    )


async def catalog_fence() -> None:
    """Every table that CASCADES with a node is read by rule 3, or named as
    holding no member work. A new one fails here before it can be deleted in
    silence."""
    rows_ = await rows(
        str(uuid.UUID(int=0)),
        "SELECT DISTINCT cl.relname AS tbl FROM pg_constraint con "
        "  JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_class rt ON rt.oid = con.confrelid "
        " WHERE con.contype = 'f' AND rt.relname = 'pm_projects' AND con.confdeltype = 'c'",
    )
    found = {r.tbl for r in rows_} - {"pm_tasks", "pm_projects"}
    known = set(import_discard.NODE_TABLES) | set(import_discard.NODE_TABLES_IGNORED)
    check(
        "0.1 every table that cascades with a node is checked",
        found <= known,
        str(sorted(found - known)),
    )
    task_rows = await rows(
        str(uuid.UUID(int=0)),
        "SELECT DISTINCT cl.relname AS tbl FROM pg_constraint con "
        "  JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_class rt ON rt.oid = con.confrelid "
        " WHERE con.contype = 'f' AND rt.relname = 'pm_tasks' AND con.confdeltype = 'c'",
    )
    found = {r.tbl for r in task_rows} - {"pm_tasks"}
    known = set(import_discard.TASK_TABLES_CHECKED) | set(import_discard.TASK_TABLES_IGNORED)
    check(
        "0.2 every table that cascades with a task is checked",
        found <= known,
        str(sorted(found - known)),
    )


async def update_chain(org: str, bundle: object, raw: bytes) -> None:
    first = await run_import(org, bundle, raw)
    edited, a, _b = edited_export(raw)
    later = await run_import(org, clickup.parse([(w.FIXTURE.name, edited)]), edited)
    report = w.as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:r AS uuid)", r=later)
    )
    check(
        "4.1 the later export updates one task and adds one comment",
        report.get("tasks_updated") == 1 and report.get("comments_written") == 1,
        json.dumps({k: report.get(k) for k in ("tasks_updated", "comments_written")}),
    )
    _, refused = await discard(org, first)
    check(
        "4.2 the earlier run refuses while the later one stands",
        refused is not None and "later import" in refused.message,
        refused.message if refused else "not refused",
    )
    counts, refused = await discard(org, later)
    check(
        "4.3 the later run discards, keeps its update, and removes its comment",
        refused is None
        and counts is not None
        and counts["tasks"] == 0
        and counts["nodes"] == 0
        and counts["comments_elsewhere"] == 1
        and counts["updates_kept"] == 1,
        refused.message if refused else json.dumps(counts),
    )
    title = await one(
        org,
        "SELECT title FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
        " AND origin->>'external_id' = :a",
        a=a,
    )
    check(
        "4.4 the kept update stays on the earlier task", title == "Renamed in ClickUp", str(title)
    )
    check("4.5 the earlier run's tasks survive", await run_tasks(org, first) == 2423)
    residue = await one(
        org,
        "SELECT (SELECT count(*) FROM pm_task_statuses s JOIN pm_projects p ON p.id = s.project_id "
        "         WHERE p.organization_id = CAST(:org AS uuid) AND s.name = 'waiting on vendor') "
        "     + (SELECT count(*) FROM pm_tags WHERE organization_id = CAST(:org AS uuid) "
        "         AND lower(name) = 'vendor hold')",
    )
    check("4.5b the later run's lane and tag stay in the earlier nodes", residue == 2, str(residue))
    counts, refused = await discard(org, first)
    check(
        "4.6 then the earlier run discards cleanly",
        refused is None and counts is not None and counts["tasks"] == 2423,
        refused.message if refused else json.dumps(counts),
    )
    check("4.7 nothing of either run is left", await import_nodes(org) == 0)


if __name__ == "__main__":
    asyncio.run(main())
    width = max(len(n) for n, _, _ in w.results)
    failed = 0
    for name, ok, detail in w.results:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail if not ok else ''}")
    print(f"\n{len(w.results) - failed}/{len(w.results)} passed")
    sys.exit(1 if failed else 0)
