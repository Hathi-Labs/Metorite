"""WS-41 I-2 — the import run table and the plan's reads, against a real Postgres (R8).

Spec `project-docs/specs/project_import.md` §6.1, §6.2, §6.9, §7.1 · **D80** ·
board WS-41.

── Why this file exists ─────────────────────────────────────────────────────

`routes/projects/imports.py` binds JSONB through `CAST(:x AS jsonb)`, a list
through `= ANY(:refs)`, and reads `origin->>'…'` against a partial unique
index. A hermetic fake agrees with every one of those whatever the SQL says.
So this script runs the module's OWN statements — imported, not retyped — and
reads the rows back.

Two organizations, so the tenant predicate is asked, not assumed. The run
table's FORCE policy is checked under a role that does NOT bypass RLS, because
the owner role bypasses it and would pass anything (the "owner bypasses RLS"
trap in the prod-verification notes).

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws41_import.py

It reads `LIVE_DSN`, or derives an asyncpg DSN from
`TENANT_LADDER_DATABASE_URL`. Everything runs in one transaction that is
ROLLED BACK, so the scratch database is left as it was found.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from gateway.routes.projects import imports
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.plan import ImportMapping, build_plan
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("LIVE_DSN") or os.environ["TENANT_LADDER_DATABASE_URL"].replace(
    "+psycopg",
    "+asyncpg",
)
ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATION = ROOT / "infra" / "postgres" / "219_pm_import_runs.sql"
FIXTURE = ROOT / "tests" / "unit" / "import_fixtures" / "clickup_workspace.csv"

ADMIN = "admin.ws41@acme.test"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def _body(sql: str) -> str:
    """The migration without its own BEGIN/COMMIT, so it runs inside ours."""
    return "\n".join(line for line in sql.splitlines() if line.strip() not in ("BEGIN;", "COMMIT;"))


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()
        try:
            await _run(db)
        finally:
            await outer.rollback()
    await eng.dispose()


async def _run(db) -> None:
    # ── 0. the migration, applied twice ─────────────────────────────────
    raw = await db.get_raw_connection()
    for attempt in (1, 2):
        try:
            await raw.driver_connection.execute(_body(MIGRATION.read_text(encoding="utf-8")))
            check(f"0.{attempt} migration 219 applies (pass {attempt})", True)
        except Exception as exc:
            check(
                f"0.{attempt} migration 219 applies (pass {attempt})",
                False,
                f"{type(exc).__name__}: {exc}",
            )
            return
    rls = (
        await db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = 'pm_import_runs'::regclass"
            )
        )
    ).fetchone()
    check("0.3 RLS is enabled AND forced", bool(rls and rls[0] and rls[1]), str(rls))
    idx = (
        await db.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_pm_tasks_import_origin'")
        )
    ).scalar()
    check(
        "0.4 the origin index is UNIQUE and partial",
        bool(idx) and "UNIQUE" in idx and "WHERE" in idx,
        str(idx),
    )

    # ── seed: two organizations ─────────────────────────────────────────
    orgs = []
    for label in ("a", "b"):
        orgs.append(
            str(
                (
                    await db.execute(
                        text(
                            "INSERT INTO organization (slug, display_name) VALUES (:s, :n) RETURNING id"
                        ),
                        {
                            "s": f"live-ws41-{label}-{uuid.uuid4().hex[:8]}",
                            "n": f"WS-41 live {label}",
                        },
                    )
                ).scalar_one()
            )
        )
    org, other = orgs
    for email, name, status, o in (
        ("person1@example.com", "Person 1", "active", org),
        ("person2@example.com", "Person 2", "active", org),
        ("gone@example.com", "Gone", "alumni", org),
        ("person1@example.com", "Person 1", "active", other),
        ("stranger@example.com", "Stranger", "active", other),
    ):
        await db.execute(
            text(
                "INSERT INTO people (name, email, status, organization_id) "
                "VALUES (:n, :e, :s, CAST(:o AS uuid))"
            ),
            {"n": name, "e": email, "s": status, "o": o},
        )

    # A task the pre-D52 importer wrote, and one a file import wrote.
    pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO pm_projects (id, organization_id, name, source, created_by, owns_statuses) "
            "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), 'WS-41 live', 'manual', :me, true)"
        ),
        {"id": pid, "o": org, "me": ADMIN},
    )
    await db.execute(
        text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, category, is_default) "
            "VALUES (CAST(:id AS uuid), CAST(:p AS uuid), 'To do', 1, 'todo', true)"
        ),
        {"id": sid, "p": pid},
    )
    bundle = clickup.parse([(FIXTURE.name, FIXTURE.read_bytes())])
    legacy_ref, imported_ref = bundle.tasks[0].ref, bundle.tasks[1].ref
    for n, (clickup_id, origin) in enumerate(
        (
            (f"{legacy_ref}", None),
            (
                None,
                {"kind": "import", "source": "clickup", "external_id": imported_ref, "run_id": "r"},
            ),
        ),
        start=1,
    ):
        await db.execute(
            text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, status_id, "
                "  title, source, created_by, task_number, clickup_id, origin) "
                "VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
                "  CAST(:s AS uuid), :t, 'import', :me, :n, :c, CAST(:origin AS jsonb))"
            ),
            {
                "o": org,
                "p": pid,
                "s": sid,
                "t": f"seed {n}",
                "me": ADMIN,
                "n": n,
                "c": clickup_id,
                "origin": json.dumps(origin) if origin else None,
            },
        )

    # ── 1. the unique index refuses a second import of one task ─────────
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, status_id, "
                    "  title, source, created_by, task_number, origin) "
                    "VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
                    "  CAST(:s AS uuid), 'dup', 'import', :me, 3, CAST(:origin AS jsonb))"
                ),
                {
                    "o": org,
                    "p": pid,
                    "s": sid,
                    "me": ADMIN,
                    "origin": json.dumps(
                        {"kind": "import", "source": "clickup", "external_id": imported_ref}
                    ),
                },
            )
        check("1.1 a second task with one import origin is refused", False, "inserted")
    except Exception as exc:
        check(
            "1.1 a second task with one import origin is refused",
            "uq_pm_tasks_import_origin" in str(exc),
            type(exc).__name__,
        )

    # ── 2. the plan's reads, with the module's own SQL ──────────────────
    refs = [t.ref for t in bundle.tasks]
    directory = {
        r.email: r.name
        for r in (await db.execute(text(imports.DIRECTORY_SQL), {"org": org})).fetchall()
    }
    check(
        "2.1 the directory is this organization's ACTIVE members only",
        directory == {"person1@example.com": "Person 1", "person2@example.com": "Person 2"},
        str(directory),
    )
    existing = {
        r.ref
        for r in (
            await db.execute(
                text(imports.EXISTING_SQL), {"org": org, "source": "clickup", "refs": refs}
            )
        ).fetchall()
    }
    check(
        "2.2 the import-origin read finds the imported task",
        existing == {imported_ref},
        str(existing),
    )
    legacy = {
        r.ref
        for r in (await db.execute(text(imports.LEGACY_SQL), {"org": org, "refs": refs})).fetchall()
    }
    check(
        "2.3 the clickup_id read finds the old importer's task", legacy == {legacy_ref}, str(legacy)
    )
    none_there = (
        await db.execute(
            text(imports.EXISTING_SQL), {"org": other, "source": "clickup", "refs": refs}
        )
    ).fetchall()
    check("2.4 another organization sees neither", none_there == [], str(none_there))

    plan = build_plan(bundle, ImportMapping(), directory, existing, legacy)
    check(
        "2.5 the plan skips both and writes the rest",
        plan["skip"]["total"] == 2 and plan["to_write"]["tasks"] == 2421,
        json.dumps(plan["skip"]),
    )

    # ── 3. the run row round-trips through the module's SQL ─────────────
    run_id = str(uuid.uuid4())
    row = (
        await db.execute(
            text(imports.INSERT_RUN_SQL),
            {
                "id": run_id,
                "org": org,
                "who": ADMIN,
                "source": "clickup",
                "files": json.dumps(
                    [{"name": "x.csv", "disk_name": "00-x.csv", "bytes": 1, "sha256": "0"}]
                ),
                "mapping": ImportMapping().model_dump_json(),
                "plan": json.dumps(plan, default=str),
            },
        )
    ).fetchone()
    view = imports.run_view(row)
    check(
        "3.1 insert returns the run, JSONB decoded",
        view["state"] == "planned" and view["plan"]["summary"]["tasks"] == 2423,
        view["state"],
    )
    loaded = (await db.execute(text(imports.LOAD_RUN_SQL), {"id": run_id, "org": org})).fetchone()
    check("3.2 load by id within the organization", loaded is not None)
    cross = (await db.execute(text(imports.LOAD_RUN_SQL), {"id": run_id, "org": other})).fetchone()
    check("3.3 load by id from another organization finds nothing", cross is None)
    listed = (await db.execute(text(imports.LIST_RUNS_SQL), {"org": org})).fetchall()
    check(
        "3.4 the list carries the summary",
        len(listed) == 1 and listed[0].summary["tasks"] == 2423,
        str(len(listed)),
    )
    saved = (
        await db.execute(
            text(imports.SAVE_MAPPING_SQL),
            {
                "id": run_id,
                "org": org,
                "mapping": json.dumps({"grant": "group:eng"}),
                "plan": json.dumps({"ready": False}),
            },
        )
    ).fetchone()
    check(
        "3.5 save mapping updates in place",
        saved is not None and saved.mapping == {"grant": "group:eng"},
    )
    try:
        async with db.begin_nested():
            await db.execute(
                text("UPDATE pm_import_runs SET state = 'bogus' WHERE id = CAST(:id AS uuid)"),
                {"id": run_id},
            )
        check("3.6 an unknown state is refused", False, "updated")
    except Exception as exc:
        check(
            "3.6 an unknown state is refused",
            "pm_import_runs_state_known" in str(exc),
            type(exc).__name__,
        )

    # ── 4. the FORCE policy, under a role that does not bypass RLS ──────
    role = f"ws41_live_{uuid.uuid4().hex[:8]}"
    await db.execute(text(f"CREATE ROLE {role} NOLOGIN NOBYPASSRLS"))
    await db.execute(text(f"GRANT SELECT ON pm_import_runs TO {role}"))
    await db.execute(text(f"SET LOCAL ROLE {role}"))
    await db.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": other})
    seen_other = (
        await db.execute(
            text("SELECT count(*) FROM pm_import_runs WHERE id = CAST(:id AS uuid)"), {"id": run_id}
        )
    ).scalar()
    await db.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
    seen_own = (
        await db.execute(
            text("SELECT count(*) FROM pm_import_runs WHERE id = CAST(:id AS uuid)"), {"id": run_id}
        )
    ).scalar()
    await db.execute(text("RESET ROLE"))
    check("4.1 RLS hides the run from another tenant", seen_other == 0, f"other={seen_other}")
    check("4.2 RLS shows the run to its own tenant", seen_own == 1, f"own={seen_own}")


if __name__ == "__main__":
    asyncio.run(main())
    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail if not ok else ''}")
    print(f"\n{len(results) - failed}/{len(results)} passed")
    sys.exit(1 if failed else 0)
