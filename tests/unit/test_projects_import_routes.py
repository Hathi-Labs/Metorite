"""WS-41 I-2 — the import routes: gates, upload, storage, and no ``pm_*`` write.

Spec: ``project-docs/specs/project_import.md`` §7.2, §7.4 to §7.6 · D80.

The SQL itself is proved against a real Postgres by
``tests/live/live_ws41_import.py`` (R8). These tests pin what a fake CAN
prove: the order of the gates, what reaches the disk, what the response
hides, and that no statement writes a ``pm_*`` table other than the run's own.
"""

from __future__ import annotations

import io
import json
import pathlib
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from gateway.routes.projects import imports
from gateway.routes.projects.importer.plan import ImportMapping, Target

FIXTURE = pathlib.Path(__file__).parent / "import_fixtures" / "clickup_workspace.csv"
ORG = "11111111-1111-1111-1111-111111111111"
ADMIN = "admin@acme.test"


class RecordingDB:
    """Answers the handful of statements the import routes send, and records
    every one. Rows live in ``self.runs``."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.directory = [SimpleNamespace(email="ann@acme.test", name="Person 1")]
        self.groups: set[str] = set()

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        self.statements.append((sql, params))
        if sql.startswith("INSERT INTO pm_import_runs"):
            row = {
                "id": params["id"],
                "organization_id": params["org"],
                "created_by": params["who"],
                "source": params["source"],
                "state": "planned",
                "files": params["files"],
                "mapping": params["mapping"],
                "plan": params["plan"],
                "created_at": None,
                "updated_at": None,
                "finished_at": None,
            }
            self.runs[params["id"]] = row
            return _Result([SimpleNamespace(**row)])
        if sql.startswith("SELECT * FROM pm_import_runs"):
            row = self.runs.get(params["id"])
            ok = row is not None and row["organization_id"] == params["org"]
            return _Result([SimpleNamespace(**row)] if ok else [])
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            return _Result([])
        if sql.startswith("SELECT 1 FROM org_group"):
            return _Result([SimpleNamespace(x=1)] if params["slug"] in self.groups else [])
        if sql.startswith("SELECT id FROM pm_import_runs") and "'applying'" in sql:
            busy = [
                r
                for r in self.runs.values()
                if r["organization_id"] == params["org"]
                and r["id"] != params["id"]
                and r["state"] == "applying"
            ]
            return _Result([SimpleNamespace(id=r["id"]) for r in busy])
        if sql.startswith("UPDATE pm_import_runs SET state = 'applying'"):
            row = self.runs.get(params["id"])
            if row is None or row["state"] != "planned":
                return _Result([])
            row["state"] = "applying"
            return _Result([SimpleNamespace(**row)])
        if sql.startswith("UPDATE pm_import_runs SET state = 'discarded'"):
            gone = [
                r
                for r in self.runs.values()
                if r["organization_id"] == params["org"]
                and r["id"] != params["id"]
                and r["state"] in ("uploaded", "planned")
            ]
            for r in gone:
                r["state"] = "discarded"
            return _Result([SimpleNamespace(id=r["id"]) for r in gone])
        if sql.startswith("UPDATE pm_import_runs"):
            row = self.runs[params["id"]]
            # The route's guard is in the SQL; the fake honours it.
            assert "state IN ('uploaded', 'planned')" in sql
            if row["state"] not in ("uploaded", "planned"):
                return _Result([])
            row.update(mapping=params["mapping"], plan=params["plan"], state="planned")
            return _Result([SimpleNamespace(**row)])
        if "FROM people" in sql:
            return _Result(self.directory)
        return _Result([])


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def fetchall(self) -> list[Any]:
        return self.rows

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> RecordingDB:
    fake = RecordingDB()

    @asynccontextmanager
    async def session(organization_id: str | None = None) -> Any:
        yield fake

    async def visibility(_db: Any, _user: Any) -> Any:
        return SimpleNamespace(organization_id=ORG)

    monkeypatch.setattr(imports, "_tenant_session", session)
    monkeypatch.setattr(imports, "resolve_visibility", visibility)
    monkeypatch.setenv(imports.IMPORT_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(imports.IMPORT_FLAG, "1")
    return fake


def _user() -> Any:
    return SimpleNamespace(email=ADMIN, has_permission=lambda p: True)


def _upload(raw: bytes, name: str = "export.csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(raw), filename=name)


# ── the gates ───────────────────────────────────────────────────────────────


def _import_routes() -> list[Any]:
    from gateway.routes.projects import router

    return [r for r in router.routes if r.path.startswith("/projects/import/")]


def _own_deps(route: Any) -> list[Any]:
    """The route's own dependencies, after the router's feature gate."""
    from gateway.routes.projects import router

    return [d.dependency for d in route.dependencies[len(router.dependencies) :]]


def test_five_import_routes_are_mounted() -> None:
    assert {(r.path, tuple(sorted(r.methods))) for r in _import_routes()} == {
        ("/projects/import/runs", ("POST",)),
        ("/projects/import/runs", ("GET",)),
        ("/projects/import/runs/{run_id}", ("GET",)),
        ("/projects/import/runs/{run_id}/mapping", ("PUT",)),
        ("/projects/import/runs/{run_id}/apply", ("POST",)),
    }


def test_every_import_route_checks_the_flag_then_the_permission() -> None:
    for route in _import_routes():
        deps = _own_deps(route)
        assert len(deps) == 2, route.path
        assert deps[0] is imports.require_import_enabled, route.path
        assert deps[1].__qualname__.startswith("require_permission"), route.path


@pytest.mark.parametrize("value", ["", "0", "off", "false", None])
async def test_the_flag_off_answers_404(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(imports.IMPORT_FLAG, raising=False)
    else:
        monkeypatch.setenv(imports.IMPORT_FLAG, value)
    with pytest.raises(HTTPException) as err:
        await imports.require_import_enabled()
    assert err.value.status_code == 404


async def test_the_flag_on_lets_the_request_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(imports.IMPORT_FLAG, "on")
    assert await imports.require_import_enabled() is None


async def test_a_member_without_the_admin_permission_gets_403() -> None:
    route = next(r for r in _import_routes() if "POST" in r.methods)
    check = _own_deps(route)[1]
    member = SimpleNamespace(email="m@acme.test", has_permission=lambda p: False)
    with pytest.raises(HTTPException) as err:
        await check(member)
    assert err.value.status_code == 403
    assert imports.IMPORT_PERMISSION in err.value.detail


# ── upload ──────────────────────────────────────────────────────────────────


async def test_an_upload_stores_the_file_and_plans_without_writing_pm_rows(
    db: RecordingDB,
    tmp_path: pathlib.Path,
) -> None:
    raw = FIXTURE.read_bytes()
    view = await imports.create_import_run([_upload(raw, "../../etc/clickup.csv")], _user())

    assert view["state"] == "planned" and view["source"] == "clickup"
    assert view["plan"]["summary"]["tasks"] == 2423 and view["plan"]["ready"]
    # The response never names a server path.
    assert view["files"] == [
        {"name": "clickup.csv", "bytes": len(raw), "sha256": view["files"][0]["sha256"]}
    ]
    assert "disk_name" not in json.dumps(view)
    # The file sits under <dir>/<org>/<run>/, and nowhere else.
    stored = list(tmp_path.rglob("*.csv"))
    assert [p.relative_to(tmp_path).parts[:2] for p in stored] == [(ORG, view["id"])]
    assert stored[0].read_bytes() == raw
    # The tenant is the caller's, never the request's.
    insert = next(p for s, p in db.statements if s.startswith("INSERT INTO pm_import_runs"))
    assert insert["org"] == ORG and insert["who"] == ADMIN
    # §8 — the plan writes nothing. The only write is the run itself.
    writes = [s for s, _ in db.statements if re.match(r"(INSERT|UPDATE|DELETE)\b", s)]
    assert writes and all("pm_import_runs" in s for s in writes)


async def test_the_directory_proposal_reaches_the_plan(db: RecordingDB) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    matched = [p for p in view["plan"]["people"] if p["member"]]
    assert [(p["display_name"], p["member"]) for p in matched] == [("Person 1", "ann@acme.test")]


async def test_a_file_that_is_not_an_export_is_422(db: RecordingDB) -> None:
    with pytest.raises(HTTPException) as err:
        await imports.create_import_run([_upload(b"Name,Owner\nA,B\n", "people.csv")], _user())
    assert err.value.status_code == 422
    assert db.statements == []


async def test_a_file_over_the_cap_is_413(db: RecordingDB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imports, "MAX_FILE_BYTES", 100)
    with pytest.raises(HTTPException) as err:
        await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    assert err.value.status_code == 413


async def test_too_many_tasks_is_413(db: RecordingDB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imports, "MAX_TASKS", 100)
    with pytest.raises(HTTPException) as err:
        await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    assert err.value.status_code == 413


async def test_too_many_files_is_400(db: RecordingDB) -> None:
    files = [_upload(b"x") for _ in range(imports.MAX_FILES + 1)]
    with pytest.raises(HTTPException) as err:
        await imports.create_import_run(files, _user())
    assert err.value.status_code == 400


async def test_a_failed_insert_leaves_no_file(db: RecordingDB, tmp_path: pathlib.Path) -> None:
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("database down")

    real = db.execute

    async def execute(statement: Any, params: Any = None) -> Any:
        if str(statement).lstrip().startswith("INSERT INTO pm_import_runs"):
            return await boom()
        return await real(statement, params)

    db.execute = execute  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    assert list(tmp_path.rglob("*.csv")) == []


# ── mapping ─────────────────────────────────────────────────────────────────


async def test_a_saved_mapping_replans_from_the_stored_file(db: RecordingDB) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    mapping = ImportMapping(people={"name:person 1": None}, target=Target(name="From ClickUp"))
    again = await imports.save_import_mapping(view["id"], mapping, _user())
    assert again["mapping"]["target"]["name"] == "From ClickUp"
    assert all(p["member"] is None for p in again["plan"]["people"])


async def test_another_organizations_run_is_404(db: RecordingDB) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["organization_id"] = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(HTTPException) as err:
        await imports.get_import_run(view["id"], _user())
    assert err.value.status_code == 404


async def test_a_run_past_planning_cannot_change(db: RecordingDB) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["state"] = "applying"
    with pytest.raises(HTTPException) as err:
        await imports.save_import_mapping(view["id"], ImportMapping(), _user())
    assert err.value.status_code == 409


async def test_a_changed_file_on_disk_is_refused(db: RecordingDB, tmp_path: pathlib.Path) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    stored = next(tmp_path.rglob("*.csv"))
    stored.write_bytes(stored.read_bytes() + b"\n")
    with pytest.raises(HTTPException) as err:
        await imports.save_import_mapping(view["id"], ImportMapping(), _user())
    assert err.value.status_code == 409


async def test_a_missing_file_is_410(db: RecordingDB, tmp_path: pathlib.Path) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    next(tmp_path.rglob("*.csv")).unlink()
    with pytest.raises(HTTPException) as err:
        await imports.save_import_mapping(view["id"], ImportMapping(), _user())
    assert err.value.status_code == 410


@pytest.mark.parametrize("run_id", ["not-a-uuid", "../x", ""])
async def test_a_bad_run_id_is_404(db: RecordingDB, run_id: str) -> None:
    with pytest.raises(HTTPException) as err:
        await imports.get_import_run(run_id, _user())
    assert err.value.status_code == 404


# ── the migration ───────────────────────────────────────────────────────────


def test_the_migration_forces_rls_and_keys_the_import_origin() -> None:
    sql = (
        pathlib.Path(__file__).resolve().parents[2]
        / "infra"
        / "postgres"
        / "219_pm_import_runs.sql"
    ).read_text(
        encoding="utf-8",
    )
    assert "FORCE  ROW LEVEL SECURITY" in sql and "pm_import_runs_tenant_isolation" in sql
    assert "organization_id UUID NOT NULL REFERENCES organization" in sql
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_tasks_import_origin\s+ON pm_tasks "
        r"\(organization_id, \(origin->>'source'\), \(origin->>'external_id'\)\)\s+"
        r"WHERE origin->>'kind' = 'import'",
        sql,
    )
    for state in ("uploaded", "planned", "applying", "done", "failed", "discarded"):
        assert f"'{state}'" in sql


# ── review fixes (I-2 review and verification, 2026-09-27) ──────────────────


async def test_a_mixed_case_session_email_is_stored_lowercased(db: RecordingDB) -> None:
    """The column CHECKs lowercase. A mixed-case email was a 500."""
    user = SimpleNamespace(email="Admin@Acme.TEST", has_permission=lambda p: True)
    await imports.create_import_run([_upload(FIXTURE.read_bytes())], user)
    insert = next(p for s, p in db.statements if s.startswith("INSERT INTO pm_import_runs"))
    assert insert["who"] == "admin@acme.test"


async def test_a_new_upload_discards_the_open_run_and_its_files(
    db: RecordingDB,
    tmp_path: pathlib.Path,
) -> None:
    """§7.4 — one open run per organization, so the disk holds one upload per
    organization at most."""
    first = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    second = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    assert db.runs[first["id"]]["state"] == "discarded"
    assert db.runs[second["id"]]["state"] == "planned"
    folders = {p.parent.name for p in tmp_path.rglob("*.csv")}
    assert folders == {second["id"]}


async def test_another_organizations_open_run_is_not_discarded(db: RecordingDB) -> None:
    first = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    db.runs[first["id"]]["organization_id"] = "22222222-2222-2222-2222-222222222222"
    await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    assert db.runs[first["id"]]["state"] == "planned"


async def test_a_run_that_moves_on_during_the_parse_is_not_dragged_back(
    db: RecordingDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load sees `planned`, the parse takes seconds, and in that window the
    run moves on. The guarded UPDATE must refuse, never write `planned`."""
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    real_parse = imports._parse

    async def slow_parse(source: str, uploads: Any) -> Any:
        db.runs[view["id"]]["state"] = "applying"
        return await real_parse(source, uploads)

    monkeypatch.setattr(imports, "_parse", slow_parse)
    with pytest.raises(HTTPException) as err:
        await imports.save_import_mapping(view["id"], ImportMapping(), _user())
    assert err.value.status_code == 409
    assert db.runs[view["id"]]["state"] == "applying"


async def test_the_parse_runs_with_no_session_open(
    db: RecordingDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large parse inside a transaction pins a pooled connection."""
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    open_sessions = 0

    @asynccontextmanager
    async def counting(organization_id: str | None = None) -> Any:
        nonlocal open_sessions
        open_sessions += 1
        try:
            yield db
        finally:
            open_sessions -= 1

    real_parse = imports._parse
    seen: list[int] = []

    async def parse(source: str, uploads: Any) -> Any:
        seen.append(open_sessions)
        return await real_parse(source, uploads)

    monkeypatch.setattr(imports, "_tenant_session", counting)
    monkeypatch.setattr(imports, "_parse", parse)
    await imports.save_import_mapping(view["id"], ImportMapping(), _user())
    assert seen == [0]


async def test_a_failed_commit_leaves_no_file(
    db: RecordingDB,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    @asynccontextmanager
    async def failing_commit(organization_id: str | None = None) -> Any:
        yield db
        raise RuntimeError("commit failed")

    monkeypatch.setattr(imports, "_tenant_session", failing_commit)
    with pytest.raises(RuntimeError):
        await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    assert list(tmp_path.rglob("*.csv")) == []


# ── apply (I-3) ─────────────────────────────────────────────────────────────


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    from gateway.routes.projects import import_writer

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(import_writer, "start", lambda org, run, lease: calls.append((org, run)))
    return calls


async def test_apply_starts_the_writer_for_the_callers_organization(
    db: RecordingDB,
    started: list[tuple[str, str]],
) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    applied = await imports.apply_import_run(view["id"], _user())
    assert applied["state"] == "applying"
    assert started == [(ORG, view["id"])]
    # The per-organization lock comes before the busy check and the start.
    order = [s.split(" ")[0:3] for s, _ in db.statements[-3:]]
    assert order[0][:2] == ["SELECT", "pg_advisory_xact_lock(hashtextextended('pm_import:'"]


async def test_apply_refuses_a_plan_that_is_not_ready(
    db: RecordingDB,
    started: list[tuple[str, str]],
) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["plan"] = json.dumps({"ready": False, "errors": ["x"]})
    with pytest.raises(HTTPException) as err:
        await imports.apply_import_run(view["id"], _user())
    assert err.value.status_code == 422 and started == []


async def test_apply_refuses_while_another_run_writes(
    db: RecordingDB,
    started: list[tuple[str, str]],
) -> None:
    first = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    await imports.apply_import_run(first["id"], _user())
    second = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    with pytest.raises(HTTPException) as err:
        await imports.apply_import_run(second["id"], _user())
    assert err.value.status_code == 409
    assert started == [(ORG, first["id"])]


async def test_apply_refuses_a_group_this_organization_does_not_have(
    db: RecordingDB,
    started: list[tuple[str, str]],
) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["mapping"] = json.dumps({"grant": "group:eng"})
    with pytest.raises(HTTPException) as err:
        await imports.apply_import_run(view["id"], _user())
    assert err.value.status_code == 422
    db.groups.add("eng")
    assert (await imports.apply_import_run(view["id"], _user()))["state"] == "applying"


async def test_a_finished_run_cannot_apply_again(
    db: RecordingDB,
    started: list[tuple[str, str]],
) -> None:
    view = await imports.create_import_run([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["state"] = "done"
    with pytest.raises(HTTPException) as err:
        await imports.apply_import_run(view["id"], _user())
    assert err.value.status_code == 409 and started == []


def test_an_inherited_mapping_is_case_blind() -> None:
    """The I-3b verifier's NF2: a mixed-case hand mapping was dropped on the
    next upload, and the person then lost their tasks in silence."""
    directory = {"m4@acme.test": "Four"}
    people = {"name:a": "M4@Acme.TEST", "name:b": None, "name:c": "gone@acme.test"}
    assert imports.usable_people(people, directory) == {"name:a": "m4@acme.test", "name:b": None}
