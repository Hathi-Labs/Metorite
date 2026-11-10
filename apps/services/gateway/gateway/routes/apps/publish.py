"""Custom Apps · publish — immutable versions, rollback, bundle serving.

Publish snapshots the workspace's entry file into an ``app_versions`` row
(bundle + sha256 + scope-set hash) and repoints ``apps.live_version``;
rollback just repoints (Apps Script's deployment model, RFC §4.8). The bundle
endpoint serves draft (editors) or published (viewers) HTML with sandbox CSP
headers as defense-in-depth — the frontend still renders via srcDoc.

Publish-time admin review gate (§4.8, Phase 2a): an org-visible publish that
declares a WRITE tool scope (any ``tool:*`` scope naming a non-read-only
:class:`~gateway.routes.apps.tools.ToolSpec`) queues an
``app.publish_review`` Action Broker proposal instead of going live
immediately — one row in the existing Approvals inbox. Re-review is keyed to
the scope SET (``scope_set_hash``), not the version: a later publish with a
previously-approved scope set skips review (Apps Script's rule — this is what
keeps iteration friction near zero while keeping consent honest).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from acb_auth import UserContext
from acb_skills import safe_open
from action_broker import AuthorityTier, propose, submit
from fastapi import Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from gateway.routes.apps import tools as _tools
from gateway.routes.apps._common import (
    _log,
    _tenant_session,
    _uid,
    app_workspace,
    can_edit,
    check_bundle_size,
    clean_app_rel,
    get_app_or_404,
    iso,
    manifest_scopes,
    parse_db_manifest,
    publish_app_activity,
    read_workspace_manifest,
    record_app_audit,
    require_app_author,
    require_app_user,
    router,
    scope_set_hash,
)
from gateway.routes.apps._conformance import scan_bundle
from gateway.routes.apps.durability import (
    ensure_workspace,
    sync_workspace_best_effort,
)
from pydantic import BaseModel
from sqlalchemy import text

VISIBILITIES = ("private", "people", "org")

# Sent on every bundle response: even a direct visit renders sandboxed, and
# nothing sniffs the payload into another content type.
_BUNDLE_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-forms",
    "X-Content-Type-Options": "nosniff",
}


class PublishRequest(BaseModel):
    notes: str = ""
    visibility: str | None = None


class RollbackRequest(BaseModel):
    version: int


class VersionEntry(BaseModel):
    version: int
    release_notes: str = ""
    published_by: str = ""
    published_at: str = ""


def _write_scopes(manifest: dict[str, Any]) -> list[str]:
    """Manifest scopes naming a non-read-only app tool — the ones that force
    org-publish admin review (§4.8). Reads ``_tools`` (the module, not a
    snapshotted import) so a monkeypatched ``_tools._TOOL_REGISTRY`` in tests
    is honoured."""
    out: list[str] = []
    for raw in manifest_scopes(manifest):
        parsed = _tools.parse_tool_scope(raw)
        if parsed is None:
            continue
        spec = _tools._TOOL_REGISTRY.get(parsed[0])
        if spec is not None and not spec.read_only:
            out.append(raw)
    return out


def _read_entry_bundle(row: Any) -> tuple[bytes, dict[str, Any]]:
    """The workspace's current entry file bytes + the effective manifest."""
    workspace = app_workspace(row)
    manifest = (
        read_workspace_manifest(workspace)
        or parse_db_manifest(row.manifest)
    )
    entry = str(manifest.get("entry") or "index.html")
    rel = clean_app_rel(entry)
    try:
        # The safe opener refuses a link at any depth (WS-43h review P0).
        data = safe_open.read_bytes(workspace, rel)
    except (OSError, ValueError):
        data = None
    if data is None:
        raise HTTPException(
            status_code=422, detail=f"Entry file missing: {entry}",
        )
    return check_bundle_size(data), manifest


@router.post("/{slug}/publish")
async def publish_app(
    slug: str,
    body: PublishRequest,
    user: UserContext = Depends(require_app_author),
) -> dict[str, Any]:
    """Snapshot the draft as the next immutable version and go live."""
    if body.visibility is not None and body.visibility not in VISIBILITIES:
        raise HTTPException(status_code=422, detail="Invalid visibility")
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        await ensure_workspace(db, row)
        data, manifest = await asyncio.get_event_loop().run_in_executor(
            None, _read_entry_bundle, row,
        )
        # Platform-contract conformance (RFC §4.0 layer 3): deviations the
        # sandbox would break at runtime block the publish with a message
        # naming the platform-native alternative.
        errors, warnings = scan_bundle(data.decode("utf-8", errors="replace"))
        if errors:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "conformance_failed",
                    "errors": errors,
                    "warnings": warnings,
                },
            )
        version = (await db.execute(
            text(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v "
                "FROM app_versions WHERE app_id = :app_id"
            ),
            {"app_id": str(row.id)},
        )).scalar_one()

        # Publish-time admin review gate (§4.8): an org-visible publish that
        # declares a WRITE tool scope needs review UNLESS this exact scope
        # SET was already approved on a previous version of this app.
        scope_hash = scope_set_hash(manifest_scopes(manifest))
        write_scopes = _write_scopes(manifest)
        effective_visibility = body.visibility or row.visibility
        needs_review = bool(write_scopes) and effective_visibility == "org"
        if needs_review:
            approved_before = (await db.execute(
                text(
                    "SELECT 1 FROM app_versions WHERE app_id = :app_id "
                    "AND scope_set_hash = :hash AND review_status = 'approved' "
                    "LIMIT 1"
                ),
                {"app_id": str(row.id), "hash": scope_hash},
            )).fetchone()
            if approved_before is not None:
                needs_review = False

        await db.execute(
            text(
                """INSERT INTO app_versions
                   (app_id, version, manifest, bundle_html, bundle_sha256,
                    release_notes, scope_set_hash, published_by, review_status)
                   VALUES (:app_id, :version, :manifest ::jsonb, :bundle,
                           :sha256, :notes, :scope_hash, :published_by,
                           :review_status)"""
            ),
            {
                "app_id": str(row.id),
                "version": version,
                "manifest": json.dumps(manifest),
                "bundle": data.decode("utf-8", errors="replace"),
                "sha256": hashlib.sha256(data).hexdigest(),
                "notes": body.notes.strip(),
                "scope_hash": scope_hash,
                "published_by": _uid(user),
                "review_status": "pending" if needs_review else "auto",
            },
        )
        if not needs_review:
            # The app keeps serving whatever was live before (or stays fully
            # unpublished on a first-ever publish) until the review lands.
            sets = "live_version = :version, status = 'live', updated_at = now()"
            params: dict[str, Any] = {"version": version, "id": str(row.id)}
            if body.visibility is not None:
                sets += ", visibility = :visibility"
                params["visibility"] = body.visibility
            await db.execute(text(f"UPDATE apps SET {sets} WHERE id = :id"), params)
    # ^ block exit commits — the version row is durable BEFORE the review
    # proposal below is submitted, preserving the pre-H2 ordering.

    if needs_review:
        # SUGGEST always → NEEDS_APPROVAL, so this unconditionally queues —
        # a standing consent rule, not gated by ACTION_BROKER_ENFORCE.
        proposal = propose(
            actor=f"app:{slug}", action="app.publish_review",
            target=f"app:{slug}:v{version}",
            payload={
                "app_id": str(row.id), "version": version,
                "scopes": write_scopes, "notes": body.notes,
                "visibility": effective_visibility,
            },
            authority=AuthorityTier.SUGGEST, destructive=True,
        )
        await submit(proposal)

    # Durability: the published draft is the one most worth mirroring
    # (best-effort — the version row above is already committed).
    await sync_workspace_best_effort(row)
    review = "pending" if needs_review else "auto"
    await record_app_audit(
        app_id=str(row.id), user_email=_uid(user), kind="publish",
        app_version=version,
        detail={"notes": body.notes.strip()[:500],
                "visibility": body.visibility, "review": review},
    )
    publish_app_activity(
        slug, user=_uid(user), action="publish", version=version, review=review,
    )
    _log.info(
        "apps.published", slug=slug, version=version, by=_uid(user), review=review,
    )
    return {"version": version, "warnings": warnings, "review": review}


@router.post("/{slug}/rollback")
async def rollback_app(
    slug: str,
    body: RollbackRequest,
    user: UserContext = Depends(require_app_author),
) -> dict[str, Any]:
    """Repoint the live version at an existing snapshot."""
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        exists = (await db.execute(
            text(
                "SELECT 1 FROM app_versions "
                "WHERE app_id = :app_id AND version = :version"
            ),
            {"app_id": str(row.id), "version": body.version},
        )).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Version not found")
        await db.execute(
            text(
                "UPDATE apps SET live_version = :version, updated_at = now() "
                "WHERE id = :id"
            ),
            {"version": body.version, "id": str(row.id)},
        )
    await record_app_audit(
        app_id=str(row.id), user_email=_uid(user), kind="rollback",
        app_version=body.version, detail={"to_version": body.version},
    )
    publish_app_activity(
        slug, user=_uid(user), action="rollback", version=body.version,
    )
    return {"version": body.version}


@router.get("/{slug}/versions", response_model=list[VersionEntry])
async def list_app_versions(
    slug: str,
    user: UserContext = Depends(require_app_user),
) -> list[VersionEntry]:
    """Version history, newest first (view-gated — feeds the info popover)."""
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        rows = (await db.execute(
            text(
                """SELECT version, release_notes, published_by, published_at
                   FROM app_versions WHERE app_id = :app_id
                   ORDER BY version DESC"""
            ),
            {"app_id": str(row.id)},
        )).fetchall()
    return [
        VersionEntry(
            version=r.version,
            release_notes=r.release_notes or "",
            published_by=r.published_by,
            published_at=iso(r.published_at) or "",
        )
        for r in rows
    ]


@router.get("/{slug}/bundle")
async def get_app_bundle(
    slug: str,
    version: str = Query("live", description="draft | live | <int>"),
    track: int = Query(0, description="1 = record an 'open' audit row"),
    user: UserContext = Depends(require_app_user),
) -> HTMLResponse:
    """The HTML the app frame renders. ``draft`` (editors only) serves the
    current workspace entry file; ``live``/N (viewers) serve the immutable
    snapshot. ``track=1`` marks a real open (the run page) so preview
    refreshes don't flood the audit trail."""
    async with _tenant_session() as db:
        row, grants = await get_app_or_404(db, slug, user)
        if version == "draft":
            if not can_edit(row, user, grants):
                raise HTTPException(
                    status_code=403, detail="Edit access required for drafts",
                )
            await ensure_workspace(db, row)
            data, _manifest = await asyncio.get_event_loop().run_in_executor(
                None, _read_entry_bundle, row,
            )
            return HTMLResponse(
                data.decode("utf-8", errors="replace"),
                headers=_BUNDLE_HEADERS,
            )
        if version == "live":
            if row.live_version is None:
                raise HTTPException(
                    status_code=404, detail="App has no live version",
                )
            wanted = int(row.live_version)
        else:
            try:
                wanted = int(version)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422, detail="version must be draft|live|<int>",
                ) from exc
        vrow = (await db.execute(
            text(
                "SELECT version, bundle_html FROM app_versions "
                "WHERE app_id = :app_id AND version = :version"
            ),
            {"app_id": str(row.id), "version": wanted},
        )).fetchone()
        if vrow is None:
            raise HTTPException(status_code=404, detail="Version not found")
    if track:
        await record_app_audit(
            app_id=str(row.id), user_email=_uid(user), kind="open",
            app_version=vrow.version, detail={"requested": version},
        )
    return HTMLResponse(vrow.bundle_html, headers=_BUNDLE_HEADERS)
