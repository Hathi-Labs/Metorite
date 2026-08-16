"""WS-27l against a REAL Postgres.

Custom fields are almost entirely JSONB semantics — the `-` operator, the `?`
operator, the GIN index, and whether asyncpg will encode a dict at all. A fake
re-implements those in Python and can only agree with itself.
"""
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import activities as acts_mod  # noqa: E402
from gateway.routes.projects import custom_fields as cf_mod  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from gateway.routes.projects.core import Page, TaskIn  # noqa: E402
from gateway.routes.projects.custom_fields import FieldIn  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
        await db.execute(text(
            "INSERT INTO pm_projects (id, name, source, created_by) "
            "VALUES (CAST(:id AS uuid), 'Ops', 'manual', :me)"), {"id": pid, "me": ME})
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": pid, "s": ME})
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
            "is_default) VALUES (CAST(:id AS uuid), CAST(:p AS uuid), 'To do', 1, "
            "'todo', true)"), {"id": sid, "p": pid})
        ids = []
        for n, title in enumerate(["Fix the extruder", "Ship the firmware"], start=1):
            tid = str(uuid.uuid4())
            ids.append(tid)
            await db.execute(text(
                "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
                "title, source, created_by, task_number) VALUES (CAST(:id AS uuid), "
                "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :t, 'manual', "
                ":me, :n)"), {"id": tid, "p": pid, "s": sid, "t": title, "me": ME, "n": n})
        await db.commit()
        return pid, ids
    finally:
        await db.close()


async def raw(sql, **params):
    db = await get_db()
    try:
        return (await db.execute(text(sql), params)).fetchall()
    finally:
        await db.close()


async def main():
    pid, (t1, t2) = await seed()
    user = UserContext(email=ME, role="member")

    # ── Definitions ────────────────────────────────────────────────────────
    customer = await cf_mod.create_field(
        pid, FieldIn(name="Customer PO #", field_type="text"), user=user)
    check("a key is derived from the name", customer["field_key"], "customer_po")

    region = await cf_mod.create_field(
        pid, FieldIn(name="Region", field_type="select", options=["EU", "IN", "EU"]),
        user=user)
    check("options are deduped on the way in", region["options"], ["EU", "IN"])

    budget = await cf_mod.create_field(
        pid, FieldIn(name="Budget", field_type="number"), user=user)
    tags = await cf_mod.create_field(
        pid, FieldIn(name="Teams", field_type="multi_select",
                     options=["ops", "eng"]), user=user)

    listed = await cf_mod.list_fields(pid, user=user)
    check("all four definitions come back", listed["total"], 4)

    try:
        await cf_mod.create_field(
            pid, FieldIn(name="Region", field_type="text"), user=user)
        check("a duplicate key is refused", "no error", "409")
    except Exception as exc:
        check("a duplicate key is refused", getattr(exc, "status_code", None), 409)

    # ── Values ─────────────────────────────────────────────────────────────
    after = await tasks_mod.patch_task(t1, TaskIn(custom_fields={
        "customer_po": "  PO-1234  ", "region": "EU", "budget": 2500,
        "teams": ["ops", "ops", "eng"],
    }), user=user)
    check("values round-trip through jsonb as real types", after["custom_fields"],
          {"customer_po": "PO-1234", "region": "EU", "budget": 2500,
           "teams": ["ops", "eng"]})

    after = await tasks_mod.patch_task(t1, TaskIn(custom_fields={"budget": 3000}),
                                       user=user)
    check("a patch MERGES rather than replacing",
          sorted(after["custom_fields"]), ["budget", "customer_po", "region", "teams"])
    check("and the merged key is the new value", after["custom_fields"]["budget"], 3000)

    after = await tasks_mod.patch_task(t1, TaskIn(custom_fields={"region": None}),
                                       user=user)
    check("an explicit null REMOVES the key", "region" in after["custom_fields"], False)

    untouched = await tasks_mod.get_task(t2, user=user)
    check("a task nobody set values on is {} not null",
          untouched["custom_fields"], {})

    for label, patch in (
        ("an unknown key", {"custmer": "x"}),
        ("a string in a number field", {"budget": "3000"}),
        ("a boolean in a number field", {"budget": True}),
        ("an option that is not offered", {"region": "US"}),
        ("a bare string in a multi-select", {"teams": "ops"}),
    ):
        try:
            await tasks_mod.patch_task(t2, TaskIn(custom_fields=patch), user=user)
            check(f"{label} is refused", "no error", "422")
        except Exception as exc:
            check(f"{label} is refused", getattr(exc, "status_code", None), 422)

    fresh = await tasks_mod.get_task(t2, user=user)
    check("a refused patch wrote nothing at all", fresh["custom_fields"], {})

    # ── The list endpoint carries them ─────────────────────────────────────
    listed_tasks = await tasks_mod.list_tasks(
        user=user, project_id=pid, page=Page(page=1, page_size=100))
    by_id = {r["id"]: r for r in listed_tasks.rows}
    check("the LIST carries custom values, not only the single read",
          by_id[t1]["custom_fields"]["customer_po"], "PO-1234")
    check("and an empty one is still an object on the list",
          by_id[t2]["custom_fields"], {})

    # ── Timeline and revert ────────────────────────────────────────────────
    timeline = await acts_mod.get_timeline(t1, user=user, page=Page(page=1, page_size=50))
    field_changes = [a for a in timeline["rows"] if a["type"] == "field_change"]
    check("a custom edit lands on the timeline as a field_change",
          len(field_changes) >= 3, True)
    latest = field_changes[0]
    check("no new activity type was invented", latest["type"], "field_change")
    check("the change names the custom key",
          latest["meta"]["changes"][0]["field"], "custom.region")

    reverted = await acts_mod.revert_change(latest["id"], user=user)
    check("a custom field is revertible", reverted["reverted"], ["custom.region"])
    back = await tasks_mod.get_task(t1, user=user)
    check("and the value came back", back["custom_fields"].get("region"), "EU")
    check("without disturbing its neighbours",
          back["custom_fields"]["budget"], 3000)

    # ── Definition edits ───────────────────────────────────────────────────
    try:
        await cf_mod.patch_field(region["id"], FieldIn(field_type="text"), user=user)
        check("a type change is refused while values exist", "no error", "409")
    except Exception as exc:
        check("a type change is refused while values exist",
              getattr(exc, "status_code", None), 409)

    try:
        await cf_mod.patch_field(region["id"], FieldIn(options=["IN"]), user=user)
        check("dropping an option in use is refused", "no error", "409")
    except Exception as exc:
        check("dropping an option in use is refused",
              getattr(exc, "status_code", None), 409)

    widened = await cf_mod.patch_field(
        region["id"], FieldIn(options=["EU", "IN", "US"]), user=user)
    check("but ADDING an option is fine", widened["options"], ["EU", "IN", "US"])

    renamed = await cf_mod.patch_field(
        customer["id"], FieldIn(name="Customer PO"), user=user)
    check("the label is editable", renamed["name"], "Customer PO")
    check("and the key did not move with it", renamed["field_key"], "customer_po")

    try:
        await cf_mod.patch_field(customer["id"], FieldIn(field_key="po"), user=user)
        check("the key itself is refused", "no error", "422")
    except Exception as exc:
        check("the key itself is refused", getattr(exc, "status_code", None), 422)

    # ── Delete strips values ───────────────────────────────────────────────
    gone = await cf_mod.delete_field(budget["id"], user=user)
    check("delete reports how many values it cleared",
          gone["cascaded"], {"values_cleared": 1})
    stripped = await tasks_mod.get_task(t1, user=user)
    check("the value is actually gone from the task",
          "budget" in stripped["custom_fields"], False)
    check("and the other values survived",
          sorted(stripped["custom_fields"]), ["customer_po", "region", "teams"])

    # ── The index is usable ────────────────────────────────────────────────
    hit = await raw(
        "SELECT count(*) AS n FROM pm_tasks "
        "WHERE root_project_id = CAST(:p AS uuid) "
        "  AND custom_fields @> CAST(:probe AS jsonb)",
        p=pid, probe='{"region": "EU"}')
    check("a containment query finds the task", hit[0].n, 1)

    # The planner will seq-scan a two-row table whatever indexes exist, so the
    # honest claim is that the index is THERE and is the right kind — not that
    # this particular query chose it.
    idx = await raw(
        "SELECT indexdef FROM pg_indexes "
        "WHERE tablename = 'pm_tasks' AND indexname = 'idx_pm_tasks_custom_fields'")
    check("the containment index exists", len(idx), 1)
    check("and it is a GIN index over custom_fields",
          "USING gin (custom_fields jsonb_path_ops)" in idx[0].indexdef, True)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
