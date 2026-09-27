"""Projects · file import — upload an export, see the dry run, confirm the mapping.

Spec: ``project-docs/specs/project_import.md`` §7.1 to §7.6 · decision **D80**
(amends D52.2) · board **WS-41** slice I-2.

    POST /projects/import/runs                 upload files → a run with its plan
    GET  /projects/import/runs                 this organization's runs
    GET  /projects/import/runs/{run_id}        one run
    PUT  /projects/import/runs/{run_id}/mapping  save the choices → a new plan

**Nothing here writes a ``pm_*`` row.** This slice stores the upload and the
plan. The writer is slice I-3, and until it lands no route can move a run
past ``planned``. The parse and the plan are the pure modules under
``importer/``; this module does the HTTP, the disk and the four reads the
plan needs.

Three gates, in this order, on every route:

1. **The flag** ``PROJECTS_IMPORT``, default OFF (§7.5). Off, every route
   answers 404, so a dark feature says nothing about itself.
2. **The permission** ``admin:access:manage``, the one the CRM's Zoho import
   uses (§7.4, §11 Q-2). An import creates a whole space with an org grant,
   which is an admin's act. ⚠️ This is the FIRST Projects route that calls
   ``require_permission`` (H-121 records that the others gate on visibility
   alone).
3. **The tenant** comes from the caller's session through
   ``require_organization`` and the request-bound ``tenant_session``. Never
   from the upload or the body (R5 (e)).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException, UploadFile
from gateway.routes.projects.core import (
    _TRUTHY,
    _log,
    _tenant_session,
    actor,
    load_visible_project,
    require_organization,
    resolve_visibility,
    router,
)
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.bundle import ImportBundle
from gateway.routes.projects.importer.plan import ImportMapping, build_plan
from gateway.routes.projects.importer.text import decode, read_csv
from sqlalchemy import text

#: §7.5 — ship dark. Read at call time, like ``PROJECTS_ORG_VOCABULARIES``.
IMPORT_FLAG = "PROJECTS_IMPORT"
#: §7.4 — an admin's act.
IMPORT_PERMISSION = "admin:access:manage"
#: §7.2 — where uploads wait until the run ends.
IMPORT_DIR_ENV = "PROJECT_IMPORT_DIR"
DEFAULT_IMPORT_DIR = "data/project_imports"

#: §7.4 limits.
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 5
MAX_TASKS = 20_000

#: A run the admin may still change. ``applying`` and later belong to I-3.
EDITABLE_STATES = frozenset({"uploaded", "planned"})

#: Source slug → adapter. One entry per tool; §9 adds the rest, one per slice.
ADAPTERS = {clickup.SOURCE: clickup}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")

# ── the SQL ─────────────────────────────────────────────────────────────────
#
# Module constants, so ``tests/live/live_ws41_import.py`` runs these exact
# statements against a real Postgres (R8). Every statement names the tenant
# explicitly as well as through RLS: the policy is the second lock, not the
# only one.

INSERT_RUN_SQL = (
    "INSERT INTO pm_import_runs "
    "  (id, organization_id, created_by, source, state, files, mapping, plan) "
    "VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :who, :source, 'planned', "
    "        CAST(:files AS jsonb), CAST(:mapping AS jsonb), CAST(:plan AS jsonb)) "
    "RETURNING *"
)
LIST_RUNS_SQL = (
    "SELECT id, source, state, created_by, created_at, updated_at, finished_at, "
    "       plan->'summary' AS summary "
    "  FROM pm_import_runs "
    " WHERE organization_id = CAST(:org AS uuid) "
    " ORDER BY created_at DESC LIMIT 50"
)
LOAD_RUN_SQL = (
    "SELECT * FROM pm_import_runs "
    " WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid)"
)
#: Guarded by state. The route checks the state when it loads the run, then
#: parses for seconds with no session open. A run that moved on in that window
#: (to ``applying`` in I-3, or ``discarded`` by a newer upload) must not be
#: dragged back to ``planned``: that could start a second writer in one tree.
SAVE_MAPPING_SQL = (
    "UPDATE pm_import_runs "
    "   SET mapping = CAST(:mapping AS jsonb), plan = CAST(:plan AS jsonb), "
    "       state = 'planned', updated_at = now() "
    " WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "   AND state IN ('uploaded', 'planned') "
    "RETURNING *"
)
#: §7.4 — one open run per organization. A new upload discards the open one,
#: and its files go with it. Without this, an admin could fill the shared disk
#: one 250 MB upload at a time, and every tenant would go down with it.
SUPERSEDE_OPEN_RUNS_SQL = (
    "UPDATE pm_import_runs "
    "   SET state = 'discarded', updated_at = now(), finished_at = now() "
    " WHERE organization_id = CAST(:org AS uuid) "
    "   AND state IN ('uploaded', 'planned') AND id <> CAST(:id AS uuid) "
    "RETURNING id"
)


def import_enabled() -> bool:
    return (os.environ.get(IMPORT_FLAG) or "").strip().lower() in _TRUTHY


async def require_import_enabled() -> None:
    """404 while the flag is off: a dark feature does not announce itself."""
    if not import_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


_GATES = [Depends(require_import_enabled), require_permission(IMPORT_PERMISSION)]


def import_dir() -> Path:
    return Path(os.environ.get(IMPORT_DIR_ENV) or DEFAULT_IMPORT_DIR)


# ── the routes ──────────────────────────────────────────────────────────────


@router.post("/import/runs", status_code=201, dependencies=_GATES)
async def create_import_run(
    files: list[UploadFile],
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload one or more export files. Parse them, plan with the proposals,
    and store the run. Writes no ``pm_*`` row.

    A new upload DISCARDS the organization's open run, if any (§7.4). The
    admin works on one import at a time, and the disk holds one per
    organization at most."""
    # Lowercased: the column CHECKs it, and a mixed-case session email would
    # otherwise turn every upload into a 500 (watchers.py does the same).
    email = actor(user).strip().lower()
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one export file.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Upload at most {MAX_FILES} files at once.")

    uploads: list[tuple[str, bytes]] = []
    for upload in files:
        raw = await upload.read(MAX_FILE_BYTES + 1)
        if not raw:
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'A file'} is empty.")
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename or 'A file'} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB. "
                "Export one space at a time.",
            )
        uploads.append((_safe_name(upload.filename), raw))

    source = detect_source(uploads)
    bundle = await _parse(source, uploads)

    run_id = str(uuid.uuid4())
    organization_id: str | None = None
    try:
        async with _tenant_session() as db:
            vis = await resolve_visibility(db, user)
            organization_id = require_organization(vis)
            mapping = ImportMapping()
            facts = await _facts(db, bundle, mapping, vis, organization_id)
            plan = build_plan(bundle, mapping, **facts)

            stored = _store(organization_id, run_id, uploads)
            superseded = [
                str(r.id)
                for r in (
                    await db.execute(
                        text(SUPERSEDE_OPEN_RUNS_SQL), {"org": organization_id, "id": run_id}
                    )
                ).fetchall()
            ]
            row = (
                await db.execute(
                    text(INSERT_RUN_SQL),
                    {
                        "id": run_id,
                        "org": organization_id,
                        "who": email,
                        "source": source,
                        "files": json.dumps(stored),
                        "mapping": mapping.model_dump_json(),
                        "plan": json.dumps(plan, default=str),
                    },
                )
            ).fetchone()
        # The session committed on exit. Only now are the old files orphans.
    except BaseException:
        # Covers the commit too: a failed commit leaves no row, so no file.
        if organization_id is not None:
            _discard_files(organization_id, run_id)
        raise
    assert organization_id is not None  # set before any row could exist
    for old in superseded:
        _discard_files(organization_id, old)
    _log.info(
        "projects.import.run_created",
        run_id=run_id,
        source=source,
        tasks=len(bundle.tasks),
        superseded=len(superseded),
    )
    return run_view(row)


@router.get("/import/runs", dependencies=_GATES)
async def list_import_runs(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        organization_id = require_organization(vis)
        rows = (await db.execute(text(LIST_RUNS_SQL), {"org": organization_id})).fetchall()
    return {
        "runs": [
            {
                "id": str(r.id),
                "source": r.source,
                "state": r.state,
                "created_by": r.created_by,
                "created_at": _iso(r.created_at),
                "updated_at": _iso(r.updated_at),
                "finished_at": _iso(r.finished_at),
                "summary": _json(r.summary),
            }
            for r in rows
        ]
    }


@router.get("/import/runs/{run_id}", dependencies=_GATES)
async def get_import_run(
    run_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        row = await _load_run(db, run_id, require_organization(vis))
    return run_view(row)


@router.put("/import/runs/{run_id}/mapping", dependencies=_GATES)
async def save_import_mapping(
    run_id: str,
    mapping: ImportMapping,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save the admin's choices and plan again. The plan is recomputed from the
    stored files, so it always matches what apply will read.

    Three steps, and the parse holds NO session: a large file takes seconds,
    and a pooled connection idle in a transaction for that long is the lock
    queue ``apply_migrations.sh`` records an outage from."""
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        organization_id = require_organization(vis)
        row = await _load_run(db, run_id, organization_id)
    if row.state not in EDITABLE_STATES:
        raise HTTPException(status_code=409, detail=_not_editable(row.state))

    bundle = await _parse(row.source, _read_files(organization_id, run_id, _json(row.files)))

    async with _tenant_session() as db:
        facts = await _facts(db, bundle, mapping, vis, organization_id)
        plan = build_plan(bundle, mapping, **facts)
        saved = (
            await db.execute(
                text(SAVE_MAPPING_SQL),
                {
                    "id": run_id,
                    "org": organization_id,
                    "mapping": mapping.model_dump_json(),
                    "plan": json.dumps(plan, default=str),
                },
            )
        ).fetchone()
    if saved is None:
        # The run moved on while the file was parsed.
        raise HTTPException(status_code=409, detail=_not_editable("no longer open"))
    return run_view(saved)


def _not_editable(state: str) -> str:
    return f"This import is {state} and cannot change. Upload the file again to start over."


# ── parse ───────────────────────────────────────────────────────────────────


def detect_source(uploads: list[tuple[str, bytes]]) -> str:
    """Name the tool from the first file's header. Every file of one run must
    come from one tool."""
    found: set[str] = set()
    for name, raw in uploads:
        header = read_csv(decode(raw[:65536])[0].split("\n", 1)[0] + "\n").header
        matches = [slug for slug, adapter in ADAPTERS.items() if adapter.looks_like(header)]
        if not matches:
            raise HTTPException(
                status_code=422,
                detail=f"{name} is not an export this importer reads. "
                "Upload a ClickUp workspace export (Settings → Imports / Exports → Export).",
            )
        found.add(matches[0])
    if len(found) > 1:
        raise HTTPException(status_code=422, detail="Upload files from one tool per import.")
    return found.pop()


async def _parse(source: str, uploads: list[tuple[str, bytes]]) -> ImportBundle:
    adapter = ADAPTERS.get(source)
    if adapter is None:
        raise HTTPException(status_code=422, detail=f"Unknown source {source!r}.")
    try:
        # A 50 MB file is seconds of CPU. Off the event loop, so one upload
        # does not stall every other request on this worker.
        bundle: ImportBundle = await asyncio.to_thread(adapter.parse, uploads)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if len(bundle.tasks) > MAX_TASKS:
        raise HTTPException(
            status_code=413,
            detail=f"The export holds {len(bundle.tasks)} tasks. One import takes at most "
            f"{MAX_TASKS}. Export one space at a time.",
        )
    return bundle


# ── the four reads the plan needs ───────────────────────────────────────────

#: Active members of THIS organization (§6.2 rule 3). The explicit tenant
#: predicate is the first lock, and RLS the second: `app_user.email` is
#: globally unique, and the picker must never offer another customer's person.
DIRECTORY_SQL = (
    "SELECT lower(email) AS email, coalesce(name, '') AS name FROM people "
    " WHERE status = 'active' AND coalesce(email, '') <> '' "
    "   AND organization_id = CAST(:org AS uuid)"
)
EXISTING_SQL = (
    "SELECT origin->>'external_id' AS ref FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) "
    "   AND origin->>'kind' = 'import' AND origin->>'source' = :source "
    "   AND origin->>'external_id' = ANY(:refs)"
)
#: §11 Q-7 — the pre-D52 importer wrote ClickUp ids here. Read only, for the
#: skip. Nothing writes this column (D52.3).
LEGACY_SQL = (
    "SELECT clickup_id AS ref FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) AND clickup_id = ANY(:refs)"
)


async def _facts(
    db: Any,
    bundle: ImportBundle,
    mapping: ImportMapping,
    vis: Any,
    organization_id: str,
) -> dict[str, Any]:
    refs = [t.ref for t in bundle.tasks]
    directory = {
        str(r.email): str(r.name)
        for r in (await db.execute(text(DIRECTORY_SQL), {"org": organization_id})).fetchall()
    }
    existing = {
        str(r.ref)
        for r in (
            await db.execute(
                text(EXISTING_SQL),
                {"org": organization_id, "source": bundle.source, "refs": refs},
            )
        ).fetchall()
    }
    legacy: set[str] = set()
    if bundle.source == clickup.SOURCE:
        legacy = {
            str(r.ref)
            for r in (
                await db.execute(
                    text(LEGACY_SQL),
                    {"org": organization_id, "refs": refs},
                )
            ).fetchall()
        }
    target_ok = True
    if mapping.target.kind == "existing" and mapping.target.project_id:
        target_ok = await _may_import_into(db, vis, mapping.target.project_id)
    return {
        "directory": directory,
        "existing_refs": existing,
        "legacy_refs": legacy,
        "target_ok": target_ok,
    }


async def _may_import_into(db: Any, vis: Any, project_id: str) -> bool:
    """An existing target must be a SPACE the admin can see, and not a
    private tree (§5.3)."""
    try:
        uuid.UUID(project_id)
        row = await load_visible_project(db, vis, project_id)
    except (ValueError, HTTPException):
        return False
    return (
        getattr(row, "parent_project_id", None) is None
        and getattr(row, "personal_owner", None) is None
        and getattr(row, "archived_at", None) is None
    )


async def _load_run(db: Any, run_id: str, organization_id: str) -> Any:
    try:
        uuid.UUID(run_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail="Import not found") from err
    row = (await db.execute(text(LOAD_RUN_SQL), {"id": run_id, "org": organization_id})).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return row


# ── disk ────────────────────────────────────────────────────────────────────


def _safe_name(name: str | None) -> str:
    base = Path(name or "export.csv").name
    return (_SAFE_NAME.sub("_", base).strip(" .") or "export.csv")[:120]


def _run_dir(organization_id: str, run_id: str) -> Path:
    # Both parts are UUIDs the server chose, so the path cannot climb out.
    return import_dir() / str(uuid.UUID(organization_id)) / str(uuid.UUID(run_id))


def _store(
    organization_id: str, run_id: str, uploads: list[tuple[str, bytes]]
) -> list[dict[str, Any]]:
    folder = _run_dir(organization_id, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored = []
    for index, (name, raw) in enumerate(uploads):
        disk_name = f"{index:02d}-{name}"
        (folder / disk_name).write_bytes(raw)
        stored.append(
            {
                "name": name,
                "disk_name": disk_name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return stored


def _read_files(organization_id: str, run_id: str, files: Any) -> list[tuple[str, bytes]]:
    folder = _run_dir(organization_id, run_id)
    out = []
    for entry in files or []:
        path = folder / Path(str(entry.get("disk_name", ""))).name
        try:
            raw = path.read_bytes()
        except OSError as err:
            raise HTTPException(
                status_code=410,
                detail="The uploaded files for this import are gone. Upload again.",
            ) from err
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            raise HTTPException(
                status_code=409, detail="An uploaded file changed on disk. Upload again."
            )
        out.append((str(entry.get("name") or path.name), raw))
    return out


def _discard_files(organization_id: str, run_id: str) -> None:
    shutil.rmtree(_run_dir(organization_id, run_id), ignore_errors=True)


# ── the response ────────────────────────────────────────────────────────────


def run_view(row: Any) -> dict[str, Any]:
    """A run as the client sees it. The disk path never leaves the server."""
    files = [
        {"name": f.get("name"), "bytes": f.get("bytes"), "sha256": f.get("sha256")}
        for f in (_json(row.files) or [])
    ]
    return {
        "id": str(row.id),
        "source": row.source,
        "state": row.state,
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "finished_at": _iso(row.finished_at),
        "files": files,
        "mapping": _json(row.mapping),
        "plan": _json(row.plan),
    }


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
