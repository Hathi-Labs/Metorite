"""WS-27n against a REAL Postgres.

Bulk edit's whole risk is what happens across a MIXED selection — tasks in
different projects, tasks the caller cannot see, a status name that exists in
one project and not another. A fake agrees with itself about all of them.
"""
import asyncio
import os
import sys
import uuid

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import bulk as bulk_mod  # noqa: E402
from gateway.routes.projects import tags as tags_mod  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from gateway.routes.projects.bulk import BulkIn  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
RAVI = "ravi@fracktal.in"
failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


async def project(db, name, statuses, *, grant=True):
    pid = str(uuid.uuid4())
    await db.execute(text(
        "INSERT INTO pm_projects (id, name, source, created_by) "
        "VALUES (CAST(:p AS uuid), :n, 'manual', :me)"),
        {"p": pid, "n": name, "me": ME})
    if grant:
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": pid, "s": ME})
    lanes = {}
    for i, (label, category) in enumerate(statuses, start=1):
        sid = str(uuid.uuid4())
        lanes[label] = sid
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, "
            "category, is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), "
            ":n, :i, :c, :d)"),
            {"s": sid, "p": pid, "n": label, "i": i, "c": category, "d": i == 1})
    return pid, lanes


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        # Ops knows "Done"; Firmware deliberately does NOT — a mixed selection
        # spanning both is the case this ticket is about.
        ops, ops_lanes = await project(db, "Ops", [("To do", "todo"), ("Done", "done")])
        fw, fw_lanes = await project(db, "Firmware", [("Open", "todo")])
        hidden, hidden_lanes = await project(
            db, "Secret", [("To do", "todo")], grant=False)

        made = {}
        n = 0
        for key, pid, lanes, lane in (
            ("ops1", ops, ops_lanes, "To do"),
            ("ops2", ops, ops_lanes, "To do"),
            ("ops3", ops, ops_lanes, "Done"),
            ("fw1", fw, fw_lanes, "Open"),
            ("hidden1", hidden, hidden_lanes, "To do"),
        ):
            n += 1
            tid = str(uuid.uuid4())
            made[key] = tid
            await db.execute(text(
                "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
                "title, source, created_by, task_number, tags) VALUES "
                "(CAST(:id AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
                "CAST(:s AS uuid), :t, 'manual', :me, :n, ARRAY[]::text[])"),
                {"id": tid, "p": pid, "s": lanes[lane], "t": key, "me": ME, "n": n})
        # ops2 already has Ravi, to prove a re-assert is not a change.
        await db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "VALUES (CAST(:t AS uuid), :a, :me)"),
            {"t": made["ops2"], "a": RAVI, "me": ME})
        await db.commit()
        return made
    finally:
        await db.close()


async def task_row(tid, user):
    return await tasks_mod.get_task(tid, user=user)


async def main():
    t = await seed()
    user = UserContext(email=ME, role="member")
    B = lambda **kw: BulkIn(**kw)  # noqa: E731

    # ── Shape refused before anything is written ───────────────────────────
    for label, payload in (
        ("an empty selection", B(task_ids=[], patch={"importance": 1})),
        ("a request that asks for nothing", B(task_ids=[t["ops1"]])),
        ("status_id instead of status",
         B(task_ids=[t["ops1"]], patch={"status_id": str(uuid.uuid4())})),
        ("an unknown field", B(task_ids=[t["ops1"]], patch={"colour": "red"})),
    ):
        try:
            await bulk_mod.bulk_edit(payload, user=user)
            check(f"{label} is refused", "no error", "422")
        except Exception as exc:
            check(f"{label} is refused", getattr(exc, "status_code", None), 422)

    before = await task_row(t["ops1"], user)
    check("and nothing was written by any of them", before["importance"], None)

    # ── The happy path across one project ──────────────────────────────────
    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["ops2"]], patch={"importance": 3}), user=user)
    check("both tasks changed", out["applied"], 2)
    check("nothing failed", out["failed"], [])
    check("importance really landed",
          (await task_row(t["ops1"], user))["importance"], 3)

    # ── Already-in-state is SKIPPED, not a phantom edit ────────────────────
    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["ops2"]], patch={"importance": 3}), user=user)
    check("re-applying the same value changes nothing", out["applied"], 0)
    check("and says why", sorted({s["reason"] for s in out["skipped"]}), ["unchanged"])

    # ── A status NAME resolved per task's own project ──────────────────────
    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["fw1"]], patch={"status": "Done"}), user=user)
    check("the task whose project HAS the lane moved", out["applied"], 1)
    check("and the one whose project does not is reported per task",
          [f["task_id"] for f in out["failed"]], [t["fw1"]])
    check("with the lanes that project actually has",
          "Open" in out["failed"][0]["reason"], True)
    check("the mover really moved",
          (await task_row(t["ops1"], user))["completed_at"] is not None, True)
    check("and the other was left exactly as it was",
          (await task_row(t["fw1"], user))["completed_at"], None)

    # ── An invisible task is SKIPPED, not an error, and not a leak ─────────
    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops2"], t["hidden1"]], patch={"importance": 1}), user=user)
    check("the visible one was edited", out["applied"], 1)
    check("the invisible one is skipped as not_found",
          [s for s in out["skipped"] if s["task_id"] == t["hidden1"]],
          [{"task_id": t["hidden1"], "reason": "not_found"}])
    check("and the batch did not fail", out["failed"], [])

    db = await get_db()
    try:
        untouched = (await db.execute(text(
            "SELECT importance FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": t["hidden1"]})).scalar()
        check("the invisible task was genuinely not written", untouched, None)
    finally:
        await db.close()

    # ── Assignees ADD, not replace ─────────────────────────────────────────
    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["ops2"]], assignees_add=["Priya@Fracktal.IN"]),
        user=user)
    check("both got the new assignee", out["applied"], 2)
    ops2 = await task_row(t["ops2"], user)
    check("and the one that already had somebody KEPT them",
          sorted(ops2["assignees"]), [ME, RAVI])

    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops2"]], assignees_add=[RAVI]), user=user)
    check("re-asserting an existing assignee is not a change", out["applied"], 0)

    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["ops2"]], assignees_remove=[ME]), user=user)
    check("remove takes them off", out["applied"], 2)
    check("leaving the others",
          (await task_row(t["ops2"], user))["assignees"], [RAVI])

    # ── One notification per person per batch, not one per task ────────────
    db = await get_db()
    try:
        await db.execute(text("DELETE FROM pm_notifications"))
        await db.commit()
    finally:
        await db.close()

    await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["ops2"], t["ops3"]], assignees_add=[RAVI]),
        user=user)
    db = await get_db()
    try:
        rows = (await db.execute(text(
            "SELECT recipient, excerpt FROM pm_notifications WHERE recipient = :r"),
            {"r": RAVI})).fetchall()
        check("three tasks assigned rings ONCE", len(rows), 1)
        check("and the bell says how many", "other task" in (rows[0].excerpt or ""), True)
    finally:
        await db.close()

    # ── Tags go through the registry ───────────────────────────────────────
    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["ops2"]], tags_add=["Bug", "  needs   review "]),
        user=user)
    check("tags applied to both", out["applied"], 2)
    check("normalised on the way in",
          sorted((await task_row(t["ops1"], user))["tags"]), ["Bug", "needs review"])

    registered = await tags_mod.list_tags(
        str((await task_row(t["ops1"], user))["root_project_id"]), user=user)
    check("and REGISTERED, so bulk is not a second door into the array",
          sorted(r["name"] for r in registered["rows"]), ["Bug", "needs review"])

    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"]], tags_add=["BUG"]), user=user)
    check("a differently-cased tag is not a second tag", out["applied"], 0)

    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["ops2"]], tags_remove=["bug"]), user=user)
    check("remove matches case-insensitively", out["applied"], 2)
    check("and leaves the rest",
          (await task_row(t["ops1"], user))["tags"], ["needs review"])

    # ── Everything at once, which is the actual re-triage ──────────────────
    out = await bulk_mod.bulk_edit(
        B(task_ids=[t["ops1"], t["ops2"]], patch={"status": "Done", "importance": 0},
          assignees_add=[ME], tags_add=["triaged"]),
        user=user)
    check("one request does the whole re-triage", out["applied"], 2)
    by_task = {r["task_id"]: sorted(r["changed"]) for r in out["results"]}
    # ops2 was still in "To do", so it moves; ops1 was already "Done" from the
    # earlier check, so its status legitimately does NOT appear.
    check("the task that moved reports every axis it touched", by_task[t["ops2"]],
          ["assignees", "importance", "status", "tags"])
    check("the one already in that lane reports the rest, without a phantom move",
          by_task[t["ops1"]], ["assignees", "importance", "tags"])
    check("and the status name comes back so the UI need not re-read",
          out["results"][0].get("status"), "Done")

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
