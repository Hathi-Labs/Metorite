"""Custom Apps · lifecycle — list / create+scaffold / read / update / archive.

Creating an app writes its workspace at ``apps_root()/{slug}``: ``app.json``
(the RFC §4.1 manifest), a starter ``index.html``, and a best-effort
``git init`` + baseline commit (checkpoints ride the same repo later). The DB
row is authoritative for visibility/status; the workspace is authoritative for
the manifest (the DB copy is the scaffold-time fallback).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from acb_auth import UserContext
from fastapi import Depends, HTTPException
from gateway.routes.apps._common import (
    _log,
    _tenant_session,
    _uid,
    app_workspace,
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
    """Best-effort ``git init`` + baseline commit — never fails creation."""
    git_env = [
        "-c", "user.name=Metorite",
        "-c", "user.email=metorite@local",
    ]
    for args in (
        ["git", "init"],
        ["git", "add", "-A"],
        ["git", *git_env, "commit", "-m", "initial scaffold"],
    ):
        try:
            subprocess.run(
                args, cwd=str(workspace), capture_output=True,
                timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning(
                "apps.git_init_skipped",
                workspace=str(workspace), error=str(exc)[:120],
            )
            return


def _scaffold_workspace(
    workspace: Path, manifest: dict[str, Any], name: str,
) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "app.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    (workspace / "index.html").write_text(
        starter_index_html(name), encoding="utf-8",
    )
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
        target = new_workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (new_workspace / "app.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
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
        (workspace / "app.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
        )
    except OSError as exc:
        _log.warning("apps.manifest_sync_failed", error=str(exc)[:120])


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
    workspace = app_workspace(row)
    manifest = (
        read_workspace_manifest(workspace)
        or parse_db_manifest(row.manifest)
    )
    editable = can_edit(row, user, grants)
    summary = _to_summary(row, user, grants)
    return AppDetail(
        **summary.model_dump(),
        created_at=iso(row.created_at) or "",
        manifest=manifest,
        builder_session_id=row.builder_session_id,
        workspace_path=str(workspace) if editable else None,
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
        rows = (await db.execute(
            text("SELECT * FROM apps ORDER BY updated_at DESC"),
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
    async with _tenant_session() as db:
        taken = {
            r.slug for r in (await db.execute(
                text("SELECT slug FROM apps"),
            )).fetchall()
        }
        slug = generate_slug(name, taken)
        workspace = apps_root() / slug
        manifest = default_manifest(
            slug, name, body.icon.strip(), body.description.strip(),
        )
        await asyncio.get_event_loop().run_in_executor(
            None, _scaffold_workspace, workspace, manifest, name,
        )
        row = (await db.execute(
            text(
                """INSERT INTO apps
                   (slug, name, icon, description, owner_email,
                    manifest, workspace_path)
                   VALUES (:slug, :name, :icon, :description, :owner,
                           :manifest ::jsonb, :workspace_path)
                   RETURNING *"""
            ),
            {
                "slug": slug,
                "name": name,
                "icon": body.icon.strip(),
                "description": body.description.strip(),
                "owner": owner,
                "manifest": json.dumps(manifest),
                "workspace_path": str(workspace),
            },
        )).fetchone()
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
    async with _tenant_session() as db:
        source_row, _source_grants = await get_app_or_404(db, slug, user)
        source_workspace = await ensure_workspace(db, source_row)
        files = await asyncio.get_event_loop().run_in_executor(
            None, _read_workspace_files, source_workspace,
        )
        taken = {
            r.slug for r in (await db.execute(
                text("SELECT slug FROM apps"),
            )).fetchall()
        }
        name = f"{source_row.name} (fork)"
        new_slug = generate_slug(name, taken)
        new_workspace = apps_root() / new_slug

        manifest = dict(
            read_workspace_manifest(source_workspace)
            or parse_db_manifest(source_row.manifest)
            or {}
        )
        manifest["slug"] = new_slug
        manifest["name"] = name

        await asyncio.get_event_loop().run_in_executor(
            None, _fork_workspace, new_workspace, files, manifest,
        )

        row = (await db.execute(
            text(
                """INSERT INTO apps
                   (slug, name, icon, description, owner_email,
                    manifest, workspace_path)
                   VALUES (:slug, :name, :icon, :description, :owner,
                           :manifest ::jsonb, :workspace_path)
                   RETURNING *"""
            ),
            {
                "slug": new_slug,
                "name": name,
                "icon": source_row.icon or "",
                "description": source_row.description or "",
                "owner": owner,
                "manifest": json.dumps(manifest),
                "workspace_path": str(new_workspace),
            },
        )).fetchone()
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
    async with _tenant_session() as db:
        row, grants = await get_app_or_404(db, slug, user, edit=True)
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
    manifest_fields = {
        k: v for k, v in fields.items()
        if k in ("name", "icon", "description")
    }
    if manifest_fields:
        await asyncio.get_event_loop().run_in_executor(
            None, _sync_workspace_manifest, app_workspace(row), manifest_fields,
        )
    return _to_detail(row, user, grants)


@router.delete("/{slug}")
async def delete_app(
    slug: str,
    user: UserContext = Depends(require_app_author),
) -> dict[str, Any]:
    """Soft-archive (edit-gated); a second delete by the OWNER of an already
    archived app removes the row (cascades) and its workspace folder."""
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
        await db.execute(
            text("DELETE FROM apps WHERE id = :id"), {"id": str(row.id)},
        )
    # Remove the workspace folder — but only when it's really under apps_root
    # (a corrupt workspace_path must never delete arbitrary directories).
    workspace = app_workspace(row).resolve()
    root = apps_root().resolve()
    if workspace != root and workspace.is_relative_to(root):
        import shutil
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: shutil.rmtree(workspace, ignore_errors=True),
        )
    _log.info("apps.deleted", slug=slug, by=_uid(user))
    return {"slug": slug, "deleted": True}
