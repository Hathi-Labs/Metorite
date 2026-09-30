"""WS-41 I-8 — the admin's skips, renames and kept columns, written for real (R8).

Spec `project-docs/specs/project_import.md` §9 row I-8.

The unit suite proves `plan.choose` in memory. This proves the WRITER obeys it
against a real Postgres: a skipped list creates no node and no task, a subtask
of a skipped task stays out too, a renamed list lands under its new name, and a
kept column reaches the description while a left-out one does not. A second run
that takes the skip back then adds what the first left out, into the same tree.

It reuses the harness of `live_ws41_writer.py`, so it runs the same way::

    TENANT_LADDER_DATABASE_URL="postgresql+psycopg://acb:acb@127.0.0.1:5436/acb_tenant" \\
      uv run python tests/live/live_ws41_choices.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_ws41_writer as harness
from gateway.routes.projects import import_writer
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.plan import (
    ContainerChoice,
    ImportMapping,
    Target,
)

HEADER = (
    "Task ID,Task Link,Task Name,Status,Date Created,Date Created Text,"
    "Parent ID,Assignees,List Name,Space Name,Home Location ID,Comments,Sprint,Points"
)
WHEN = '1758612100413,"9/23/2025, 12:51:40 PM GMT+5:30"'
TAG = uuid.uuid4().hex[:6]


def _row(tid: str, list_id: str, parent: str = "null", sprint: str = "", points: str = "") -> str:
    return (
        f"{tid}{TAG},https://app.clickup.com/t/{tid},T {tid},to do,{WHEN},{parent},[],"
        f"L{list_id},S,{list_id}{TAG},[],{sprint},{points}"
    )


RAW = (
    "\n".join(
        [
            HEADER,
            _row("a1", "1", sprint="Sprint 12", points="3"),
            _row("a2", "1", sprint="Sprint 12"),
            _row("b1", "2", sprint="Sprint 13"),
            _row("a3", "1", parent=f"b1{TAG}"),
        ]
    )
    + "\n"
).encode()


async def main() -> None:
    bundle = clickup.parse([("choices.csv", RAW)])
    refs = {c.name: c.ref for c in bundle.containers}
    org = await harness.seed("i8", [harness.ADMIN])
    try:
        target = Target(kind="new_space", name=f"Choices {TAG}")
        first = ImportMapping(
            target=target,
            columns={"Sprint": "description"},
            containers={
                refs["L1"]: ContainerChoice(name="Launch plan"),
                refs["L2"]: ContainerChoice(skip=True),
            },
        )
        run_id, lease = await harness.new_run(org, harness.ADMIN, bundle, RAW, first)
        await import_writer.apply_run(org, run_id, lease)

        names = {
            r.name
            for r in await harness.rows(
                org, "SELECT name FROM pm_projects WHERE organization_id = CAST(:org AS uuid)"
            )
        }
        harness.check(
            "the renamed list lands under its new name", "Launch plan" in names, str(names)
        )
        harness.check("the skipped list creates no node", "L2" not in names, str(names))
        written = {
            str(r.ref)[: -len(TAG)]
            for r in await harness.rows(
                org,
                "SELECT origin->>'external_id' AS ref FROM pm_tasks "
                " WHERE organization_id = CAST(:org AS uuid)",
            )
        }
        harness.check(
            "only the kept list's own tasks are written", written == {"a1", "a2"}, str(written)
        )
        text = str(
            await harness.one(
                org,
                "SELECT description FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
                " AND origin->>'external_id' = :ref",
                ref=f"a1{TAG}",
            )
        )
        harness.check("a kept column reaches the description", "- Sprint: Sprint 12" in text, text)
        harness.check("a left-out column does not", "Points" not in text, text)

        # The skip taken back: the same export, the same target, into the same tree.
        second = ImportMapping(target=target, columns={"Sprint": "description"})
        again, lease = await harness.new_run(org, harness.ADMIN, bundle, RAW, second)
        await import_writer.apply_run(org, again, lease)
        written = {
            str(r.ref)[: -len(TAG)]
            for r in await harness.rows(
                org,
                "SELECT origin->>'external_id' AS ref FROM pm_tasks "
                " WHERE organization_id = CAST(:org AS uuid)",
            )
        }
        harness.check(
            "a second run adds what the first left out",
            written == {"a1", "a2", "a3", "b1"},
            str(written),
        )
        spaces = await harness.one(
            org,
            "SELECT count(*) FROM pm_projects WHERE organization_id = CAST(:org AS uuid) "
            " AND parent_project_id IS NULL",
        )
        harness.check("into the same space, not a second one", spaces == 1, str(spaces))
        parent = await harness.one(
            org,
            "SELECT p.origin->>'external_id' FROM pm_tasks t JOIN pm_tasks p ON p.id = t.parent_task_id "
            " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'external_id' = :ref",
            ref=f"a3{TAG}",
        )
        harness.check("the subtask lands under its parent", parent == f"b1{TAG}", str(parent))
    finally:
        await harness.drop(org)
    await other_lists_of_a_newer_export(refs, target_name=f"Newer {TAG}")

    failed = [r for r in harness.results if not r[1]]
    for name, ok, detail in harness.results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  [{detail[:300]}]"))
    print(f"\n{len(harness.results) - len(failed)}/{len(harness.results)} passed")
    sys.exit(1 if failed else 0)


async def other_lists_of_a_newer_export(refs: dict[str, str], target_name: str) -> None:
    """The I-8 review's P1: run 1 keeps list 1. A NEWER export of the same
    workspace (other bytes, so other hashes) keeps only list 2. It is still
    that export, so it must continue run 1's space, not make a second one."""
    newer = RAW + (_row("c1", "2") + "\n").encode()
    org = await harness.seed("i8b", [harness.ADMIN])
    try:
        target = Target(kind="new_space", name=target_name)
        first = ImportMapping(target=target, containers={refs["L2"]: ContainerChoice(skip=True)})
        bundle = clickup.parse([("choices.csv", RAW)])
        run_id, lease = await harness.new_run(org, harness.ADMIN, bundle, RAW, first)
        await import_writer.apply_run(org, run_id, lease)

        later = clickup.parse([("choices.csv", newer)])
        second = ImportMapping(target=target, containers={refs["L1"]: ContainerChoice(skip=True)})
        again, lease = await harness.new_run(org, harness.ADMIN, later, newer, second)
        await import_writer.apply_run(org, again, lease)

        spaces = await harness.one(
            org,
            "SELECT count(*) FROM pm_projects WHERE organization_id = CAST(:org AS uuid) "
            " AND parent_project_id IS NULL",
        )
        harness.check(
            "a newer export keeping other lists continues the same space", spaces == 1, str(spaces)
        )
        written = {
            str(r.ref)[: -len(TAG)]
            for r in await harness.rows(
                org,
                "SELECT origin->>'external_id' AS ref FROM pm_tasks "
                " WHERE organization_id = CAST(:org AS uuid)",
            )
        }
        harness.check(
            # a3 is b1's subtask, and the reader lands a subtask in its parent's list.
            "and it adds list 2's tasks beside list 1's",
            written == {"a1", "a2", "a3", "b1", "c1"},
            str(written),
        )
    finally:
        await harness.drop(org)


if __name__ == "__main__":
    asyncio.run(main())
