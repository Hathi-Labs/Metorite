"""WS-41 I-2 — the import routes: gates, upload, storage, and no ``pm_*`` write.

Spec: ``project-docs/specs/project_import.md`` §7.2, §7.4 to §7.6 · D80.

The SQL itself is proved against a real Postgres by
``tests/live/live_ws41_import.py`` (R8). These tests pin what a fake CAN
prove: the order of the gates, what reaches the disk, what the response
hides, and that no statement writes a ``pm_*`` table other than the run's own.
"""

from __future__ import annotations

import hashlib
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
        if (
            sql.startswith("UPDATE pm_import_runs SET state = 'discarded'")
            and "CAST(:id AS uuid) AND organization_id" in sql
        ):
            row = self.runs.get(params["id"])
            if row is None or row["state"] not in ("uploaded", "planned"):
                return _Result([])
            row["state"] = "discarded"
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


async def _create(files: list[UploadFile], user: Any) -> dict[str, Any]:
    """The route as the wizard calls it: the two trailing fields match."""
    return await imports.create_import_run(
        files,
        user,
        expected_files=len(files),
        expected_bytes=sum(len(f.file.getvalue()) for f in files),
    )


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


def test_six_import_routes_are_mounted() -> None:
    assert {(r.path, tuple(sorted(r.methods))) for r in _import_routes()} == {
        ("/projects/import/runs", ("POST",)),
        ("/projects/import/runs", ("GET",)),
        ("/projects/import/runs/{run_id}", ("GET",)),
        ("/projects/import/runs/{run_id}/mapping", ("PUT",)),
        ("/projects/import/runs/{run_id}/apply", ("POST",)),
        ("/projects/import/runs/{run_id}/discard", ("POST",)),
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
    view = await _create([_upload(raw, "../../etc/clickup.csv")], _user())

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
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    matched = [p for p in view["plan"]["people"] if p["member"]]
    assert [(p["display_name"], p["member"]) for p in matched] == [("Person 1", "ann@acme.test")]


async def test_a_file_that_is_not_an_export_is_422(db: RecordingDB) -> None:
    with pytest.raises(HTTPException) as err:
        await _create([_upload(b"Name,Owner\nA,B\n", "people.csv")], _user())
    assert err.value.status_code == 422
    assert db.statements == []


async def test_a_file_over_the_cap_is_413(db: RecordingDB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imports, "MAX_FILE_BYTES", 100)
    with pytest.raises(HTTPException) as err:
        await _create([_upload(FIXTURE.read_bytes())], _user())
    assert err.value.status_code == 413


async def test_too_many_tasks_is_413(db: RecordingDB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imports, "MAX_TASKS", 100)
    with pytest.raises(HTTPException) as err:
        await _create([_upload(FIXTURE.read_bytes())], _user())
    assert err.value.status_code == 413


async def test_too_many_files_is_400(db: RecordingDB) -> None:
    files = [_upload(b"x") for _ in range(imports.MAX_FILES + 1)]
    with pytest.raises(HTTPException) as err:
        await _create(files, _user())
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
        await _create([_upload(FIXTURE.read_bytes())], _user())
    assert list(tmp_path.rglob("*.csv")) == []


# ── mapping ─────────────────────────────────────────────────────────────────


async def test_a_saved_mapping_replans_from_the_stored_file(db: RecordingDB) -> None:
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    mapping = ImportMapping(people={"name:person 1": None}, target=Target(name="From ClickUp"))
    again = await imports.save_import_mapping(view["id"], mapping, _user())
    assert again["mapping"]["target"]["name"] == "From ClickUp"
    assert all(p["member"] is None for p in again["plan"]["people"])


async def test_another_organizations_run_is_404(db: RecordingDB) -> None:
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["organization_id"] = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(HTTPException) as err:
        await imports.get_import_run(view["id"], _user())
    assert err.value.status_code == 404


async def test_a_run_past_planning_cannot_change(db: RecordingDB) -> None:
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["state"] = "applying"
    with pytest.raises(HTTPException) as err:
        await imports.save_import_mapping(view["id"], ImportMapping(), _user())
    assert err.value.status_code == 409


async def test_a_changed_file_on_disk_is_refused(db: RecordingDB, tmp_path: pathlib.Path) -> None:
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    stored = next(tmp_path.rglob("*.csv"))
    stored.write_bytes(stored.read_bytes() + b"\n")
    with pytest.raises(HTTPException) as err:
        await imports.save_import_mapping(view["id"], ImportMapping(), _user())
    assert err.value.status_code == 409


async def test_a_missing_file_is_410(db: RecordingDB, tmp_path: pathlib.Path) -> None:
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
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
    await _create([_upload(FIXTURE.read_bytes())], user)
    insert = next(p for s, p in db.statements if s.startswith("INSERT INTO pm_import_runs"))
    assert insert["who"] == "admin@acme.test"


async def test_a_new_upload_discards_the_open_run_and_its_files(
    db: RecordingDB,
    tmp_path: pathlib.Path,
) -> None:
    """§7.4 — one open run per organization, so the disk holds one upload per
    organization at most."""
    first = await _create([_upload(FIXTURE.read_bytes())], _user())
    second = await _create([_upload(FIXTURE.read_bytes())], _user())
    assert db.runs[first["id"]]["state"] == "discarded"
    assert db.runs[second["id"]]["state"] == "planned"
    folders = {p.parent.name for p in tmp_path.rglob("*.csv")}
    assert folders == {second["id"]}


async def test_another_organizations_open_run_is_not_discarded(db: RecordingDB) -> None:
    first = await _create([_upload(FIXTURE.read_bytes())], _user())
    db.runs[first["id"]]["organization_id"] = "22222222-2222-2222-2222-222222222222"
    await _create([_upload(FIXTURE.read_bytes())], _user())
    assert db.runs[first["id"]]["state"] == "planned"


async def test_a_run_that_moves_on_during_the_parse_is_not_dragged_back(
    db: RecordingDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load sees `planned`, the parse takes seconds, and in that window the
    run moves on. The guarded UPDATE must refuse, never write `planned`."""
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
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
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
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
        await _create([_upload(FIXTURE.read_bytes())], _user())
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
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
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
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["plan"] = json.dumps({"ready": False, "errors": ["x"]})
    with pytest.raises(HTTPException) as err:
        await imports.apply_import_run(view["id"], _user())
    assert err.value.status_code == 422 and started == []


async def test_apply_refuses_while_another_run_writes(
    db: RecordingDB,
    started: list[tuple[str, str]],
) -> None:
    first = await _create([_upload(FIXTURE.read_bytes())], _user())
    await imports.apply_import_run(first["id"], _user())
    second = await _create([_upload(FIXTURE.read_bytes())], _user())
    with pytest.raises(HTTPException) as err:
        await imports.apply_import_run(second["id"], _user())
    assert err.value.status_code == 409
    assert started == [(ORG, first["id"])]


async def test_apply_refuses_a_group_this_organization_does_not_have(
    db: RecordingDB,
    started: list[tuple[str, str]],
) -> None:
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
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
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
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


async def test_the_plan_asks_the_writer_whether_it_continues(
    db: RecordingDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-4: the review step says when a run goes into an earlier import's
    spaces. The plan asks the WRITER's own rule, with this file's hashes, on
    the upload and again on every saved mapping, so a second copy of the rule
    cannot drift from what the writer does."""
    from gateway.routes.projects import import_writer

    asked: list[tuple[str, str, list[str]]] = []

    async def continues(_db, _org, run_id, _bundle, mapping, hashes):  # type: ignore[no-untyped-def]
        asked.append((run_id, mapping.target.name or "", hashes))
        return mapping.target.name != "Elsewhere"

    monkeypatch.setattr(import_writer, "continues_earlier", continues)
    raw = FIXTURE.read_bytes()
    view = await _create([_upload(raw)], _user())
    assert view["plan"]["continues"] is True
    db.runs[view["id"]]["plan"] = json.dumps({**view["plan"], "inherited_from": "r0"})
    moved = await imports.save_import_mapping(
        view["id"], ImportMapping(target=Target(kind="new_space", name="Elsewhere")), _user()
    )
    assert moved["plan"]["continues"] is False and moved["plan"]["inherited_from"] == "r0"
    digest = hashlib.sha256(raw).hexdigest()
    assert [a[2] for a in asked] == [[digest], [digest]]
    assert {a[0] for a in asked} == {view["id"]}


def test_the_view_carries_progress_and_report_but_not_the_lease() -> None:
    """The I-4 review's P0: the view dropped both, so the Import step showed
    no progress, no report and no failure reason."""
    row = SimpleNamespace(
        id="r1",
        source="clickup",
        state="done",
        created_by="a@acme.test",
        created_at=None,
        updated_at=None,
        finished_at=None,
        files="[]",
        mapping="{}",
        plan="{}",
        progress=json.dumps({"cursor": 600, "lease": "secret", "node_ids": {"a": "b"}}),
        report=json.dumps({"tasks_written": 2423, "space_ids": ["s1"]}),
    )
    view = imports.run_view(row)
    assert view["progress"] == {"cursor": 600}
    assert view["report"]["space_ids"] == ["s1"]
    assert "secret" not in json.dumps(view)


# ── discard (I-6) ───────────────────────────────────────────────────────────


async def test_an_open_run_discards_with_nothing_to_undo(db: RecordingDB) -> None:
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    out = await imports.discard_import_run(view["id"], _user())
    assert out["discarded"] == {}
    assert any(
        sql.startswith("UPDATE pm_import_runs SET state = 'discarded'") for sql, _ in db.statements
    )


@pytest.mark.parametrize("state", ["applying", "discarded"])
async def test_a_running_or_discarded_run_is_409(db: RecordingDB, state: str) -> None:
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["state"] = state
    with pytest.raises(HTTPException) as err:
        await imports.discard_import_run(view["id"], _user())
    assert err.value.status_code == 409


async def test_a_refused_discard_is_409_and_names_what_stops_it(
    db: RecordingDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gateway.routes.projects import import_discard

    async def refuse(_db, _org, _row):  # type: ignore[no-untyped-def]
        raise import_discard.DiscardRefused("A member edited it.", [{"id": "t1", "title": "T"}])

    monkeypatch.setattr(import_discard, "discard_written_run", refuse)
    view = await _create([_upload(FIXTURE.read_bytes())], _user())
    db.runs[view["id"]]["state"] = "done"
    with pytest.raises(HTTPException) as err:
        await imports.discard_import_run(view["id"], _user())
    assert err.value.status_code == 409
    assert err.value.detail == {
        "message": "A member edited it.",
        "blocking": [{"id": "t1", "title": "T"}],
    }


# ── a body cut short (I-7) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "trailer",
    [
        {},
        {"expected_files": 2, "expected_bytes": None},
        {"expected_files": 1, "expected_bytes": 1},
        {"expected_files": 2, "expected_bytes": 0},
    ],
)
async def test_a_body_cut_short_is_refused_and_writes_nothing(
    db: RecordingDB, trailer: dict[str, Any]
) -> None:
    """The Next proxy cuts a body at 10 MiB, and the parser drops the cut part
    in silence. The trailing fields are how the route knows."""
    raw = FIXTURE.read_bytes()
    if trailer.get("expected_bytes") == 0:
        trailer = {"expected_files": 2, "expected_bytes": len(raw)}
    with pytest.raises(HTTPException) as err:
        await imports.create_import_run([_upload(raw)], _user(), **trailer)
    assert err.value.status_code == 400 and err.value.detail == imports.CUT_SHORT
    assert not any(sql.startswith("INSERT") for sql, _ in db.statements)


async def test_a_body_cut_before_its_first_file_ended_gets_the_same_reason(db: RecordingDB) -> None:
    """Measured through the real Next proxy: a 12 MB single file loses its
    whole part and the trailer, and FastAPI used to answer a bare 422."""
    with pytest.raises(HTTPException) as err:
        await imports.create_import_run(None, _user())
    assert err.value.status_code == 400 and err.value.detail == imports.CUT_SHORT
