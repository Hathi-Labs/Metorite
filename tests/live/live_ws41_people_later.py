"""WS-41 — people added AFTER an import get their tasks on the next run (R8).

Spec `project-docs/specs/project_import.md` §6.2 and §6.9.

Measured on production 2026-09-30: the organization's People directory holds
one person, so 50 of the export's 51 people have no member and their tasks
land unassigned. The owner's way forward is to import now, add the team to
People, and upload the same export again. This proves that second run
ASSIGNS the tasks, by the three-way rule: the member touched nothing, so the
file's answer wins. It also proves a member's own later assignment survives.

    TENANT_LADDER_DATABASE_URL="postgresql+psycopg://acb:acb@127.0.0.1:5436/acb_tenant" \\
      uv run python tests/live/live_ws41_people_later.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_ws41_writer as harness
from gateway.routes.projects import import_writer, imports
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.plan import ImportMapping, Target
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

HEADER = (
    "Task ID,Task Link,Task Name,Status,Date Created,Date Created Text,"
    "Parent ID,Assignees,List Name,Space Name,Home Location ID,Comments"
)
WHEN = '1758612100413,"9/23/2025, 12:51:40 PM GMT+5:30"'
TAG = uuid.uuid4().hex[:6]
MEMBER = f"priya.{TAG}@acme.test"
OTHER = f"omar.{TAG}@acme.test"


def _row(tid: str, who: str) -> str:
    return (
        f"{tid}{TAG},https://app.clickup.com/t/{tid},T {tid},to do,{WHEN},null,"
        f'"{who}",L1,S,1{TAG},[]'
    )


RAW = (
    "\n".join([HEADER, _row("p1", "[Priya Rao]"), _row("p2", "[Priya Rao]"), _row("o1", "[]")])
    + "\n"
).encode()


async def add_person(org: str, name: str, email: str) -> None:
    eng = create_async_engine(harness.os.environ["DATABASE_URL"])
    async with eng.begin() as db:
        await db.execute(
            text(
                "INSERT INTO people (name, email, status, organization_id) "
                "VALUES (:n, :e, 'active', CAST(:o AS uuid))"
            ),
            {"n": name, "e": email, "o": org},
        )
    await eng.dispose()


async def assignees(org: str, tid: str) -> list[str]:
    rows = await harness.rows(
        org,
        "SELECT a.assignee FROM pm_task_assignees a JOIN pm_tasks t ON t.id = a.task_id "
        " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'external_id' = :ref",
        ref=f"{tid}{TAG}",
    )
    return sorted(str(r.assignee) for r in rows)


async def main() -> None:
    bundle = clickup.parse([("people.csv", RAW)])
    org = await harness.seed("people", [harness.ADMIN])
    try:
        # Saved the way the wizard saved it before the fix: "unassigned" for
        # every person with no member, chosen or not (the I-9 review's P0).
        mapping = ImportMapping(
            target=Target(kind="new_space", name=f"People {TAG}"),
            people={p.ref: None for p in bundle.people},
        )
        run_id, lease = await harness.new_run(org, harness.ADMIN, bundle, RAW, mapping)
        await import_writer.apply_run(org, run_id, lease)
        harness.check(
            "with nobody in People, the task lands unassigned", await assignees(org, "p1") == []
        )
        text_before = str(
            await harness.one(
                org,
                "SELECT description FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
                " AND origin->>'external_id' = :ref",
                ref=f"p1{TAG}",
            )
        )
        harness.check(
            "and it keeps the ClickUp name",
            "Assigned in ClickUp to: Priya Rao" in text_before,
            text_before,
        )

        # A member assigns someone to p2 by hand before the team is added.
        await add_person(org, "Omar Ali", OTHER)
        async with harness.tenant_session(org) as db:
            await db.execute(
                text(
                    "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
                    "SELECT id, :who, :who FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
                    " AND origin->>'external_id' = :ref"
                ),
                {"who": OTHER, "org": org, "ref": f"p2{TAG}"},
            )

        # The team joins People. The same export is uploaded again.
        await add_person(org, "Priya Rao", MEMBER)
        # The re-upload's mapping comes from the upload route's OWN inheritance.
        async with harness.tenant_session(org) as db:
            inherited, _ = await imports._inherited_mapping(db, org, bundle)
            directory = (await imports._facts(db, bundle, inherited, None, org))["directory"]
        inherited.people = imports.usable_people(inherited.people, directory)
        harness.check(
            "the inherited mapping no longer pins Priya",
            inherited.people == {},
            str(inherited.people),
        )
        again, lease = await harness.new_run(org, harness.ADMIN, bundle, RAW, inherited)
        await import_writer.apply_run(org, again, lease)
        got = await assignees(org, "p1")
        harness.check("the next run assigns the task to the new member", got == [MEMBER], str(got))
        text_after = str(
            await harness.one(
                org,
                "SELECT description FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
                " AND origin->>'external_id' = :ref",
                ref=f"p1{TAG}",
            )
        )
        harness.check(
            "and the ClickUp-name line goes", "Assigned in ClickUp to" not in text_after, text_after
        )
        got2 = await assignees(org, "p2")
        harness.check("a member's own assignment is kept", OTHER in got2, str(got2))
        text_p2 = str(
            await harness.one(
                org,
                "SELECT description FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
                " AND origin->>'external_id' = :ref",
                ref=f"p2{TAG}",
            )
        )
        harness.check(
            "that task keeps Priya's ClickUp name, since she was not added",
            "Assigned in ClickUp to: Priya Rao" in text_p2,
            text_p2,
        )
        spaces = await harness.one(
            org,
            "SELECT count(*) FROM pm_projects WHERE organization_id = CAST(:org AS uuid) "
            " AND parent_project_id IS NULL",
        )
        harness.check("into the same space", spaces == 1, str(spaces))
    finally:
        await harness.drop(org)

    failed = [r for r in harness.results if not r[1]]
    for name, ok, detail in harness.results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  [{detail[:300]}]"))
    print(f"\n{len(harness.results) - len(failed)}/{len(harness.results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
