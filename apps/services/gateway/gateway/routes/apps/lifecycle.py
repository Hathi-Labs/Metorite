"""Custom Apps · lifecycle — list / create+scaffold / read / update / archive.

Creating an app writes its workspace at ``apps_root()/{app id}``: ``app.json``
(the RFC §4.1 manifest), a starter ``index.html``, and a best-effort
``git init`` + baseline commit (checkpoints ride the same repo later). The DB
row is authoritative for visibility/status; the workspace is authoritative for
the manifest (the DB copy is the scaffold-time fallback).

⚠️ A new app (create, fork) reserves its slug with its INSERT BEFORE any file
write (task 2429). The folder of a new app is ``apps_root()/<app id>``, never
``apps_root()/<slug>`` (WS-STU A3b-1, task 1310). So two orgs never share a
folder, also when the slug becomes unique for each org only (A3b-2). An app
made before A3b-1 keeps its old folder, because each reader takes the stored
``apps.workspace_path``. FORCE RLS shows only the rows of the caller's org, so
a read of the slugs cannot see the slug of another org. See ``_reserve_app``.
Fences: ``tests/unit/test_app_create_slug_r8.py`` and
``tests/unit/test_apps_slug_per_org_r8.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from acb_auth import UserContext
from acb_skills import safe_open
from fastapi import Depends, HTTPException
from gateway.routes.apps._common import (
    _log,
    _tenant_session,
    _uid,
    app_workspace,
    app_workspace_or_none,
    apps_root,
    can_edit,
    can_view,
    default_manifest,
    generate_slug,
    get_app_or_404,
    iso,
    load_live_version_scopes,
    parse_db_manifest,
    publish_app_activity,
    read_workspace_manifest,
    require_app_author,
    require_app_user,
    role_for,
    router,
)
from gateway.routes.apps.durability import (
    _read_workspace_files,
    ensure_workspace,
    sync_workspace_best_effort,
)
from pydantic import BaseModel
from sqlalchemy import text

VISIBILITIES = ("private", "people", "org")


# ── Models ───────────────────────────────────────────────────────────────────

class AppSummary(BaseModel):
    slug: str
    name: str
    icon: str = ""
    description: str = ""
    status: str = "draft"
    visibility: str = "private"
    live_version: int | None = None
    owner_email: str
    updated_at: str = ""
    role: str = "use"          # the caller's relationship: own | edit | use
    pinned: bool = False       # this viewer's own app_pins bookmark
    is_template: bool = False  # flagged as a starting point in the templates gallery
    # This-month AI usage (app_audit, kind='ai') — the same aggregate
    # GET /{slug}/usage computes, minus budget_tokens (that needs a manifest
    # read per app; too expensive to pay for every card in the list).
    month_cost_usd: float = 0.0
    month_calls: int = 0


class AppDetail(AppSummary):
    created_at: str = ""
    manifest: dict[str, Any] = {}
    builder_session_id: str | None = None
    # Only present for editors — viewers never learn server paths.
    workspace_path: str | None = None
    # Consent (RFC §4.8) — computed only by GET /{slug}; left unset (None) by
    # create/patch/list so those paths don't pay for the extra queries.
    # ``live_scopes``/``live_scope_set_hash`` come from the LIVE app_versions
    # row, never the draft `manifest` above (a viewer must consent to what
    # was actually published, not whatever the builder has since drafted).
    needs_consent: bool | None = None
    live_scopes: list[str] | None = None
    live_scope_set_hash: str | None = None


class AppCreate(BaseModel):
    name: str
    description: str = ""
    icon: str = ""


class AppPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    visibility: str | None = None
    is_template: bool | None = None


# ── Scaffold (sync — run in an executor) ─────────────────────────────────────

def starter_index_html(name: str) -> str:
    """The single-file starter app: on-brand via the platform's PRE-INJECTED
    design system, with a commented ``window.cc`` usage example (the bridge
    + the ``--cc-*`` tokens and ``.cc-*`` block-kit classes below are
    injected by the run/preview frame itself — ``lib/theme/sandbox-frame.ts``'s
    ``buildSrcDoc``, the exact same styling every Metorite report and
    generative-UI card already uses. Never redeclare these — see RFC §4.1
    and ``apps/agents/agent-app-builder/instructions.md``'s Design section).
    """
    safe = name.replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{safe}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; }}
    body {{
      font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 2rem;
    }}
    main.cc-card {{ max-width: 34rem; padding: 2rem 2.5rem; }}
    h1 {{ font-size: 1.4rem; margin-bottom: 0.5rem; }}
    h1::before {{ content: "◆ "; color: var(--cc-accent); }}
    p {{ color: var(--cc-muted); }}
  </style>
</head>
<body>
  <!-- .cc-card is one of the platform's pre-styled, on-brand blocks (see
       the Design section of your instructions) — no colors/borders/radius
       to hand-write, it already matches Metorite. -->
  <main class="cc-card">
    <h1>{safe}</h1>
    <p>Built in the Metorite Workshop — describe changes in the build chat.</p>
  </main>
  <script>
    // The platform injects `window.cc` into this frame (identity, storage, AI).
    // Everything is brokered + audited server-side; this app holds no secrets.
    //
    // const me = await cc.user();                       // {{ email, role }}
    // const rows = await cc.storage.table("items").list();
    // await cc.storage.table("items").upsert({{ key: "1", value: {{ done: false }} }});
    // const reply = await cc.ai.complete("Summarize: ...");
  </script>
</body>
</html>
"""


def _git_init(workspace: Path) -> None:
    """Best-effort ``git init`` + baseline commit — never fails creation.

    Through ``durability._git``, the one host git of an app dir, with its
    safe flags and its config check (§7.5 rule A, WS-43h).
    """
    from gateway.routes.apps.durability import _GIT_IDENT, _git

    for args in (
        ("init",),
        ("add", "-A"),
        (*_GIT_IDENT, "commit", "-m", "initial scaffold"),
    ):
        if _git(workspace, *args) is None:
            _log.warning("apps.git_init_skipped", workspace=str(workspace))
            return


def _scaffold_workspace(
    workspace: Path, manifest: dict[str, Any], name: str,
) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    safe_open.write_bytes(
        workspace, "app.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )
    safe_open.write_bytes(workspace, "index.html", starter_index_html(name).encode("utf-8"))
    _git_init(workspace)


def _fork_workspace(
    new_workspace: Path,
    files: dict[str, tuple[str, str]],
    manifest: dict[str, Any],
) -> None:
    """Copy a source app's SOURCE FILES into a fresh workspace — never its
    runtime data/sharing/history (those stay DB-side, keyed to the source
    app_id, and simply aren't touched). ``app.json`` is overwritten with the
    corrected slug/name after the copy (the source's copy still names
    itself). Fresh ``git init`` — the new app gets its own history, not the
    source's, matching ``_scaffold_workspace``'s convention for every other
    newly-created app."""
    new_workspace.mkdir(parents=True, exist_ok=True)
    for rel, (content, _sha) in files.items():
        # The safe opener refuses a link at any depth (WS-43h review P0).
        safe_open.write_bytes(new_workspace, rel, content.encode("utf-8"))
    safe_open.replace_bytes(
        new_workspace, "app.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )
    _git_init(new_workspace)


def _sync_workspace_manifest(workspace: Path, fields: dict[str, Any]) -> None:
    """Best-effort mirror of name/icon/description edits into app.json so the
    DB row and manifest don't drift on a PATCH."""
    manifest = read_workspace_manifest(workspace)
    if manifest is None:
        return
    manifest.update(fields)
    try:
        safe_open.replace_bytes(
            workspace, "app.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        )
    except (OSError, ValueError) as exc:
        _log.warning("apps.manifest_sync_failed", error=str(exc)[:120])


# ── A new app: reserve the slug, then claim the folder (task 2429) ──────────

#: The INSERT of a new app. It runs BEFORE any file write, so it reserves the
#: slug. A taken slug makes no row and raises nothing, so the session stays
#: usable, and the next slug is tried. The row commits with the session, so a
#: failure after it gives the slug back.
#:
#: The conflict clause names no column (A3b-1). While ``apps_slug_key`` (slug
#: alone) and ``apps_org_slug_key`` (org and slug) both exist, a bare
#: ``ON CONFLICT DO NOTHING`` skips a conflict on either key. A clause that
#: names ``(slug)`` fails once A3b-2 drops ``apps_slug_key``. Fence:
#: ``test_no_apps_insert_names_on_conflict_slug``.
_RESERVE_SQL = (
    """INSERT INTO apps
       (id, slug, name, icon, description, owner_email,
        manifest, workspace_path)
       VALUES (CAST(:id AS uuid), :slug, :name, :icon, :description, :owner,
               :manifest ::jsonb, :workspace_path)
       ON CONFLICT DO NOTHING
       RETURNING *"""
)

#: The tries of a sequential slug (``name``, ``name-2`` …), when another org
#: holds it (until A3b-2).
SLUG_TRIES = 10
#: Then the tries of a slug with a random suffix, so a common name never runs
#: out (the ``packages_store.SLUG_HEX`` pattern).
HEX_TRIES = 3
#: The hex digits of the random suffix.
SLUG_HEX = 8

_NO_SLUG = (
    "Could not pick a free short name for this app. "
    "Try a slightly different name."
)

#: Gives back the slug of a row whose folder another writer took. The row is
#: this request's own, and it is not committed.
_UNRESERVE_SQL = "DELETE FROM apps WHERE id = :id"


def _new_app_id() -> uuid.UUID:
    """The id of a new app. The folder of the app is named by it."""
    return uuid.uuid4()


def _random_hex() -> str:
    return secrets.token_hex(SLUG_HEX // 2)


def _slug_candidates(name: str, taken: set[str]) -> Iterator[str]:
    """The slugs to try for *name*: first ``name``, ``name-2`` … up to
    :data:`SLUG_TRIES`, then :data:`HEX_TRIES` slugs with a random suffix."""
    for _ in range(SLUG_TRIES):
        slug = generate_slug(name, taken)
        taken.add(slug)
        yield slug
    stem = generate_slug(name)[:48 - 1 - SLUG_HEX].rstrip("-")
    for _ in range(HEX_TRIES):
        yield f"{stem}-{_random_hex()}"


async def _reserve_app(
    db: Any,
    name: str,
    *,
    owner: str,
    icon: str,
    description: str,
    manifest_for: Callable[[str], dict[str, Any]],
) -> tuple[Any, Path, dict[str, Any]]:
    """Insert the row of a new app, then make its folder. Answer the row, the
    folder and the manifest. Write no file in the folder before this returns.

    The folder is ``apps_root()/<app id>`` (A3b-1). The id is new for each
    try, so the folder never depends on the slug, and a folder on disk never
    changes the slug. Two orgs can therefore never share a folder.

    The read below sees only the slugs of the caller's org (FORCE RLS), so the
    sequential slugs skip only those. Until A3b-2 drops ``apps_slug_key``, a
    slug that another org holds still makes no row on the INSERT. The create
    then takes the next slug, in the same shape as a slug of the own org: a
    normal answer, never a 500, and nothing that names the other org. A3b-2
    removes this last sign, because then only the own org's slugs block.

    A concurrent INSERT of the same slug waits on the unique key until this
    session ends. When the folder of the new id is there already, the row
    goes, and the next slug is tried with a new id.
    """
    root = apps_root()
    taken = {
        r.slug for r in (await db.execute(
            text("SELECT slug FROM apps"),
        )).fetchall()
    }
    for slug in _slug_candidates(name, taken):
        app_id = _new_app_id()
        workspace = root / str(app_id)
        manifest = manifest_for(slug)
        row = (await db.execute(text(_RESERVE_SQL), {
            "id": str(app_id),
            "slug": slug,
            "name": name,
            "icon": icon,
            "description": description,
            "owner": owner,
            "manifest": json.dumps(manifest),
            "workspace_path": str(workspace),
        })).fetchone()
        if row is None:
            continue
        # A direct call, with no await: a cancel can then never come between
        # the mkdir and the return, so the caller always knows the folder.
        if _claim_folder(workspace):
            return row, workspace, manifest
        await db.execute(text(_UNRESERVE_SQL), {"id": str(row.id)})
    raise HTTPException(status_code=409, detail=_NO_SLUG)


def _claim_folder(workspace: Path) -> bool:
    """Make the folder of a new app. Answer False for a folder that is there
    already, and never write into it.

    Only the request that made the folder writes it, or removes it after a
    failure."""
    try:
        workspace.mkdir()
    except FileExistsError:
        return False
    return True


def _make_writable(path: str) -> None:
    """Set the owner write bit on *path* and on its parent folder. Follow no link."""
    for target in (path, os.path.dirname(path)):
        try:
            mode = os.lstat(target).st_mode
            if not stat.S_ISLNK(mode):
                os.chmod(target, stat.S_IMODE(mode) | stat.S_IRWXU)
        except OSError:
            pass


def _remove_folder(folder: Path) -> bool:
    """Remove *folder* and all below it. Answer True when nothing is left.

    A read-only file or folder gets the owner write bit, and the removal
    tries it once more (task 2462). A failure that stays is logged with the
    folder and the error, and never with the contents of a file. ``rmtree``
    follows no link below *folder*, and a link at *folder* is only unlinked.
    """
    failures: list[str] = []

    def give_up(_func: Any, path: str, exc: BaseException) -> None:
        if not isinstance(exc, FileNotFoundError):
            failures.append(f"{type(exc).__name__}: {exc}"[:160])

    def retry_once(_func: Any, path: str, exc: BaseException) -> None:
        if isinstance(exc, FileNotFoundError):
            return
        _make_writable(path)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, onexc=give_up)
            else:
                os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as again:
            give_up(_func, path, again)

    try:
        if folder.is_symlink():
            folder.unlink()
        else:
            shutil.rmtree(folder, onexc=retry_once)
    except FileNotFoundError:
        pass
    except OSError as exc:
        give_up(None, str(folder), exc)
    if failures or os.path.lexists(folder):
        _log.warning(
            "apps.folder_remove_failed", folder=str(folder),
            errors=len(failures), error=failures[0] if failures else "left",
        )
        return False
    return True


class _NewFolder:
    """The folder that a create or a fork made, and its removal on a failure.

    Only a folder that :func:`_claim_folder` made in this request comes here.
    A cancel (a restart during the request) does not stop the executor
    thread that writes the folder. So :meth:`discard` never removes the
    folder under a write that runs. The writer removes it when it stops, and
    a write that has not started never starts (task 2462).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._busy = False
        self._discard = False

    def write(self, fn: Callable[..., None], *args: Any) -> None:
        """Run ``fn(*args)``. The executor thread calls this."""
        with self._lock:
            if self._discard:
                return
            self._busy = True
        try:
            fn(*args)
        finally:
            with self._lock:
                self._busy = False
                discard = self._discard
            if discard:
                _remove_folder(self.path)

    def discard(self) -> None:
        """Remove the folder now, or when the write that runs stops.

        It never awaits, so a cancelled task can call it."""
        with self._lock:
            self._discard = True
            if self._busy:
                return
        _remove_folder(self.path)


# ── The tombstone of a deleted app (task 2462) ──────────────────────────────

#: The name prefix of the folder of a deleted app, until its removal. A name
#: that starts with a dot is never a slug and never an app id, and
#: ``clean_app_rel`` refuses it, so no route reaches a tombstone as an app.
_TOMBSTONE_PREFIX = ".deleted-"

#: The name of a tombstone that a failed restore could not put back. It
#: starts with a dot too, and :func:`_sweep_tombstones` never matches it.
_RESTORE_FAILED_PREFIX = ".restore-failed-"

#: A tombstone older than this is a leftover of a delete that a restart
#: stopped after its commit. The next hard delete removes it.
_TOMBSTONE_MAX_AGE_S = 3600


def _tombstone_for(root: Path, app_id: str) -> Path:
    """The tombstone of app *app_id*: ``<root>/.deleted-<id>-<unix time>``."""
    return root / f"{_TOMBSTONE_PREFIX}{app_id}-{int(time.time())}"


def _sweep_tombstones(root: Path) -> None:
    """Remove each tombstone in *root* that is older than the maximum age.

    A newer tombstone can belong to a delete that is still open, which puts
    the folder back when its session rolls back. So the sweep keeps it."""
    now = time.time()
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(_TOMBSTONE_PREFIX):
            continue
        stamp = entry.name.rsplit("-", 1)[-1]
        if not stamp.isdigit() or now - int(stamp) < _TOMBSTONE_MAX_AGE_S:
            continue
        _log.warning("apps.tombstone_left", name=entry.name)
        _remove_folder(Path(entry.path))


# ── Row → model ──────────────────────────────────────────────────────────────

def _to_summary(
    row: Any, user: UserContext, grants: list[tuple[str, str]],
    *,
    pinned: bool = False,
    usage: tuple[float, int] | None = None,
) -> AppSummary:
    cost, calls = usage or (0.0, 0)
    return AppSummary(
        slug=row.slug,
        name=row.name,
        icon=row.icon or "",
        description=row.description or "",
        status=row.status,
        visibility=row.visibility,
        live_version=row.live_version,
        owner_email=row.owner_email,
        updated_at=iso(row.updated_at) or "",
        role=role_for(row, user, grants),
        pinned=pinned,
        is_template=bool(row.is_template),
        month_cost_usd=cost,
        month_calls=calls,
    )


def _to_detail(
    row: Any, user: UserContext, grants: list[tuple[str, str]],
    *,
    needs_consent: bool | None = None,
    live_scopes: list[str] | None = None,
    live_scope_set_hash: str | None = None,
) -> AppDetail:
    # A read: a row with no folder answers from the stored manifest, never
    # from the working directory (WS-STU A3). Fence: test_studio_packages_store.py.
    workspace = app_workspace_or_none(row)
    manifest = (
        (read_workspace_manifest(workspace) if workspace is not None else None)
        or parse_db_manifest(row.manifest)
    )
    editable = can_edit(row, user, grants)
    summary = _to_summary(row, user, grants)
    return AppDetail(
        **summary.model_dump(),
        created_at=iso(row.created_at) or "",
        manifest=manifest,
        builder_session_id=row.builder_session_id,
        workspace_path=str(workspace) if editable and workspace is not None else None,
        needs_consent=needs_consent,
        live_scopes=live_scopes,
        live_scope_set_hash=live_scope_set_hash,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[AppSummary])
async def list_apps(
    user: UserContext = Depends(require_app_user),
) -> list[AppSummary]:
    """Every app the caller can see, newest-updated first."""
    async with _tenant_session() as db:
        # A Studio package (format 'manifest', WS-STU A3) is not an App
        # Workshop app. NULL reads as code. Fence: test_studio_packages_r8.py.
        rows = (await db.execute(
            text("SELECT * FROM apps WHERE format IS DISTINCT FROM 'manifest' "
                 "ORDER BY updated_at DESC"),
        )).fetchall()
        grant_rows = (await db.execute(
            text("SELECT app_id, subject, role FROM app_grants"),
        )).fetchall()
        pin_rows = (await db.execute(
            text("SELECT app_id FROM app_pins WHERE user_email = :email"),
            {"email": _uid(user)},
        )).fetchall()
        # One aggregate query for every app's this-month AI usage, mirroring
        # runtime.py's _month_ai_usage but batched — an N+1 per-app query
        # here would mean one extra round-trip per card on every gallery load.
        usage_rows = (await db.execute(
            text(
                """SELECT app_id,
                          COALESCE(SUM(cost_usd), 0) AS cost,
                          COUNT(*) AS calls
                   FROM app_audit
                   WHERE kind = 'ai' AND at >= date_trunc('month', now())
                   GROUP BY app_id"""
            ),
        )).fetchall()
    by_app: dict[str, list[tuple[str, str]]] = {}
    for g in grant_rows:
        by_app.setdefault(str(g.app_id), []).append((g.subject, g.role))
    pinned_ids = {str(p.app_id) for p in pin_rows}
    usage_by_app = {
        str(u.app_id): (float(u.cost or 0), int(u.calls or 0)) for u in usage_rows
    }
    out: list[AppSummary] = []
    for row in rows:
        grants = by_app.get(str(row.id), [])
        if can_view(row, user, grants):
            out.append(_to_summary(
                row, user, grants,
                pinned=str(row.id) in pinned_ids,
                usage=usage_by_app.get(str(row.id)),
            ))
    return out


@router.post("", response_model=AppDetail)
async def create_app(
    body: AppCreate,
    user: UserContext = Depends(require_app_author),
) -> AppDetail:
    """Create the row + scaffold the workspace (manifest, starter, git)."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    owner = _uid(user)
    icon = body.icon.strip()
    description = body.description.strip()
    loop = asyncio.get_event_loop()
    made: _NewFolder | None = None
    try:
        async with _tenant_session() as db:
            # The row reserves the slug BEFORE any file write (task 2429).
            row, workspace, manifest = await _reserve_app(
                db, name, owner=owner, icon=icon, description=description,
                manifest_for=lambda s: default_manifest(
                    s, name, icon, description),
            )
            made = _NewFolder(workspace)
            await loop.run_in_executor(
                None, made.write, _scaffold_workspace, workspace, manifest, name,
            )
    except BaseException as exc:
        # The row rolled back, so the folder that this request made goes too.
        # A cancel (a restart) removes it at once, with no await, because the
        # task may get no further turn (task 2462).
        if made is not None:
            if isinstance(exc, Exception):
                await loop.run_in_executor(None, made.discard)
            else:
                made.discard()
        raise
    slug = row.slug
    # Durability: mirror the fresh scaffold into app_files right away so a
    # brand-new draft survives a disk loss (best-effort, never fails create).
    await sync_workspace_best_effort(row)
    _log.info("apps.created", slug=slug, owner=owner)
    publish_app_activity(slug, user=owner, action="created")
    return _to_detail(row, user, [])


@router.post("/{slug}/fork", response_model=AppDetail)
async def fork_app(
    slug: str,
    user: UserContext = Depends(require_app_author),
) -> AppDetail:
    """Duplicate *slug*'s current SOURCE FILES into a brand-new app, owned
    by the caller — view access is enough (no edit grant required), the same
    "duplicate a shared team app as your own starting point" use case every
    surveyed fork/remix feature supports.

    Copies workspace files only. Deliberately NOT copied: app_data (runtime
    storage — the fork starts empty), app_grants (sharing resets — the
    forker is sole owner), app_audit (usage history stays with the
    original), app_versions (the fork is an unpublished, private draft with
    no publish history of its own).
    """
    owner = _uid(user)
    loop = asyncio.get_event_loop()
    made: _NewFolder | None = None
    try:
        async with _tenant_session() as db:
            source_row, _source_grants = await get_app_or_404(db, slug, user)
            source_workspace = await ensure_workspace(db, source_row)
            files = await loop.run_in_executor(
                None, _read_workspace_files, source_workspace,
            )
            name = f"{source_row.name} (fork)"
            source_manifest = dict(
                read_workspace_manifest(source_workspace)
                or parse_db_manifest(source_row.manifest)
                or {}
            )

            def manifest_for(new_slug: str) -> dict[str, Any]:
                return {**source_manifest, "slug": new_slug, "name": name}

            # The row reserves the slug BEFORE any file write (task 2429).
            row, new_workspace, manifest = await _reserve_app(
                db, name, owner=owner, icon=source_row.icon or "",
                description=source_row.description or "",
                manifest_for=manifest_for,
            )
            made = _NewFolder(new_workspace)
            await loop.run_in_executor(
                None, made.write, _fork_workspace, new_workspace, files, manifest,
            )
    except BaseException as exc:
        # The row rolled back, so the folder that this request made goes too.
        # A cancel (a restart) removes it at once, with no await, because the
        # task may get no further turn (task 2462).
        if made is not None:
            if isinstance(exc, Exception):
                await loop.run_in_executor(None, made.discard)
            else:
                made.discard()
        raise
    new_slug = row.slug
    await sync_workspace_best_effort(row)
    _log.info("apps.forked", source_slug=slug, slug=new_slug, owner=owner)
    publish_app_activity(new_slug, user=owner, action="created")
    return _to_detail(row, user, [])


@router.get("/{slug}", response_model=AppDetail)
async def get_app(
    slug: str,
    user: UserContext = Depends(require_app_user),
) -> AppDetail:
    """The single detail read — also computes the consent disclosure fields
    (RFC §4.8), which is why this is the only caller of ``_to_detail`` that
    passes them: create/patch/list never need the extra queries.
    """
    async with _tenant_session() as db:
        row, grants = await get_app_or_404(db, slug, user)
        needs_consent: bool | None = None
        live_scopes: list[str] | None = None
        live_scope_set_hash: str | None = None
        if row.live_version is not None:
            live = await load_live_version_scopes(
                db, str(row.id), row.live_version,
            )
            if live is not None:
                live_scopes, live_scope_set_hash = live
                if can_edit(row, user, grants):
                    # Owners/editors implicitly know what they built — this
                    # disclosure step is viewer-facing only, distinct from
                    # the admin scope-review gate publish.py already runs.
                    needs_consent = False
                else:
                    consent_row = (await db.execute(
                        text(
                            "SELECT consented_scope_hash FROM app_grants "
                            "WHERE app_id = :app_id AND subject = :subject"
                        ),
                        {"app_id": str(row.id), "subject": _uid(user)},
                    )).fetchone()
                    consented_hash = (
                        consent_row.consented_scope_hash if consent_row else None
                    ) or ""
                    needs_consent = consented_hash != live_scope_set_hash
    return _to_detail(
        row, user, grants,
        needs_consent=needs_consent,
        live_scopes=live_scopes,
        live_scope_set_hash=live_scope_set_hash,
    )


@router.patch("/{slug}", response_model=AppDetail)
async def patch_app(
    slug: str,
    body: AppPatch,
    user: UserContext = Depends(require_app_author),
) -> AppDetail:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "visibility" in fields and fields["visibility"] not in VISIBILITIES:
        raise HTTPException(status_code=422, detail="Invalid visibility")
    if "name" in fields and not str(fields["name"]).strip():
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    manifest_fields = {
        k: v for k, v in fields.items()
        if k in ("name", "icon", "description")
    }
    async with _tenant_session() as db:
        row, grants = await get_app_or_404(db, slug, user, edit=True)
        # The folder BEFORE the UPDATE: a row with no folder answers 409 and
        # the session rolls back, so no write commits (WS-STU A3).
        workspace = app_workspace(row) if manifest_fields else None
        if fields:
            sets = ", ".join(f"{k} = :{k}" for k in fields)
            await db.execute(
                text(
                    f"UPDATE apps SET {sets}, updated_at = now() "
                    "WHERE id = :id"
                ),
                {**fields, "id": str(row.id)},
            )
        # Re-read inside the SAME transaction (H2: the wrapper owns the one
        # commit, on clean exit) — the UPDATE above is visible to it.
        row = (await db.execute(
            text("SELECT * FROM apps WHERE id = :id"), {"id": str(row.id)},
        )).fetchone()
    if workspace is not None:
        await asyncio.get_event_loop().run_in_executor(
            None, _sync_workspace_manifest, workspace, manifest_fields,
        )
    return _to_detail(row, user, grants)


#: Clear the bound folder of each chat of this org that points into the
#: folder of a deleted app (task 2462). RLS scopes it to the caller's org.
#: ``left`` and not ``LIKE``, so a ``_`` or ``%`` in a slug matches only itself.
_UNBIND_SESSIONS_SQL = (
    "UPDATE chat_session SET workspace_path = NULL "
    "WHERE workspace_path IN (:stored, :resolved) "
    "OR left(workspace_path, length(:stored_prefix)) = :stored_prefix "
    "OR left(workspace_path, length(:resolved_prefix)) = :resolved_prefix"
)


def _unbind_params(stored: Path, resolved: Path) -> dict[str, str]:
    """The folder of the app as the row stored it and as it resolves, each
    also as a prefix that ends with the path separator."""
    return {
        "stored": str(stored),
        "resolved": str(resolved),
        "stored_prefix": str(stored).rstrip(os.sep) + os.sep,
        "resolved_prefix": str(resolved).rstrip(os.sep) + os.sep,
    }


def _move_to_tombstone(workspace: Path, tomb: Path) -> bool:
    """Rename the app folder to its tombstone. Answer False when no folder is
    there. Any other failure answers 500, so the session rolls back and the
    row stays (task 2462). A link at *workspace* moves as a link."""
    try:
        os.rename(workspace, tomb)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _log.warning(
            "apps.tombstone_failed", folder=str(workspace),
            error=f"{type(exc).__name__}: {exc}"[:160],
        )
        raise HTTPException(
            status_code=500, detail="The app folder could not be removed.",
        ) from None
    return True


def _restore_tombstone(tomb: Path, workspace: Path) -> None:
    """Put the folder back after a delete that did not commit.

    The rename back can fail, for example when a read rebuilt the folder in
    the meantime (``ensure_workspace``). The row still exists, so the
    tombstone then holds the only full copy. It gets a
    ``.restore-failed-`` name, which the sweep never removes, and an operator
    recovers it by hand. When that rename fails too, the tombstone stays."""
    try:
        os.rename(tomb, workspace)
        return
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"[:160]
    kept = tomb.with_name(_RESTORE_FAILED_PREFIX + tomb.name[len(_TOMBSTONE_PREFIX):])
    try:
        os.rename(tomb, kept)
    except OSError as again:
        kept = tomb
        error = f"{error}; keep: {type(again).__name__}: {again}"[:240]
    _log.error(
        "apps.tombstone_restore_failed", tombstone=kept.name,
        folder=str(workspace), error=error,
    )


def _remove_tombstone(root: Path, tomb: Path | None) -> None:
    """Remove the tombstone of this delete, then each old leftover."""
    if tomb is not None:
        _remove_folder(tomb)
    _sweep_tombstones(root)


@router.delete("/{slug}")
async def delete_app(
    slug: str,
    user: UserContext = Depends(require_app_author),
) -> dict[str, Any]:
    """Soft-archive (edit-gated); a second delete by the OWNER of an already
    archived app removes the row (cascades) and its workspace folder. The
    hard delete also unbinds each chat of the org from that folder (task 2462).

    ⚠️ The folder moves to a tombstone BEFORE the commit, and the tombstone
    goes after it (task 2462). The commit frees the slug. An app made before
    A3b-1 has its folder at ``apps_root()/<slug>``, so a removal of that path
    after the commit could remove a folder that another org made there. A
    delete that does not commit renames the tombstone back."""
    root = apps_root().resolve()
    tomb: Path | None = None
    workspace: Path | None = None
    try:
        async with _tenant_session() as db:
            row, _grants = await get_app_or_404(db, slug, user, edit=True)
            if row.status != "archived":
                await db.execute(
                    text(
                        "UPDATE apps SET status = 'archived', updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": str(row.id)},
                )
                # Early return is a clean exit — the wrapper commits the UPDATE.
                return {"slug": slug, "status": "archived"}
            if row.owner_email != user.email:
                raise HTTPException(
                    status_code=403, detail="Only the owner can hard-delete",
                )
            # The folder BEFORE the DELETE: a row with no folder answers 409
            # and the session rolls back, so nothing is deleted (WS-STU A3).
            stored = app_workspace(row)
            workspace = stored.resolve()
            await db.execute(
                text("DELETE FROM apps WHERE id = :id"), {"id": str(row.id)},
            )
            # Task 2462: the slug is free after the commit. Unbind every chat
            # of this org that points into the folder, for every member, in
            # the same transaction as the DELETE.
            await db.execute(
                text(_UNBIND_SESSIONS_SQL), _unbind_params(stored, workspace))
            # Only a folder really under apps_root moves (a corrupt
            # workspace_path must never touch arbitrary directories). A direct
            # call, with no await, so no cancel comes between the rename and
            # the assignment.
            if workspace != root and workspace.is_relative_to(root):
                candidate = _tombstone_for(root, str(row.id))
                if _move_to_tombstone(workspace, candidate):
                    tomb = candidate
    except BaseException:
        if tomb is not None and workspace is not None:
            _restore_tombstone(tomb, workspace)
        raise
    await asyncio.get_event_loop().run_in_executor(
        None, _remove_tombstone, root, tomb,
    )
    _log.info("apps.deleted", slug=slug, by=_uid(user))
    return {"slug": slug, "deleted": True}
