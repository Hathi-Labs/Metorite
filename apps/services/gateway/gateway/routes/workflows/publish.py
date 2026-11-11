"""Publish — compile the draft to an immutable version (spec F6, D3).

Publish is the platform-contract choke point (§3.2 rung 3): the graph must
validate against the live capability catalog (agents, ready modules), carry
no credential-shaped strings, and compile cleanly — or nothing is published.
Runs execute versions, never the draft, so editing never breaks in-flight
automations.

AUTHORITY (spec Q3): drafting and test-running need ``feature:workflows``, and
``feature:studio`` too where ``WORKFLOW_STUDIO_GATE_ORGS`` holds the org
(STU-J1-3, ``core.AUTHOR_GATE``) — a draft fires no triggers and its writes
are still broker-held. The four routes here add no ``feature:studio`` and no
``studio:publish`` (owner rule 2, task 1818): they keep the org tier of
``workflows:publish`` until J2 decides a private tier. Publishing
ARMS the thing: webhooks, cron ticks, and event bindings start running it
unattended against production systems. So every endpoint here — publish,
rollback, disable, enable — requires ``workflows:publish`` (seeded to owner/
admin/manager by migration 133). They share it because each one changes what
runs live just as surely as publish does.

This module is also where the spec R2 health window is reset: publish,
rollback, and enable each stamp ``health_since``, which is the instant the
auto-disable policy starts counting failures from (``service.py``).
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.workflows.core import (
    _log,
    _tenant_session,
    _uid,
    iso,
    load_workflow_or_404,
    parse_jsonb,
    router,
)
from gateway.routes.workflows.engine.graph import (
    GraphValidationError,
    compile_graph,
)
from sqlalchemy import text

#: The authority to make a workflow live (spec Q3). Named once so the route
#: gate and the catalog's capability probe can never drift apart.
PUBLISH_PERMISSION = "workflows:publish"


@router.post("/{workflow_id}/publish", dependencies=[require_permission(PUBLISH_PERMISSION)])
async def publish_workflow(
    workflow_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        graph = parse_jsonb(row.graph, {"nodes": [], "edges": []})
        ready_modules = (
            await db.execute(
                text("SELECT id FROM workflow_modules WHERE status = 'ready'"),
            )
        ).fetchall()
        from gateway.routes.workflows.catalog import known_agent_names

        try:
            from gateway.routes.workflows.tools import (
                destructive_action_names,
                tool_arg_schemas,
            )

            serialized = compile_graph(
                graph,
                known_modules={str(m.id) for m in ready_modules},
                known_agents=known_agent_names(),
                destructive_actions=destructive_action_names(),
                tool_schemas=tool_arg_schemas(),
            )
        except GraphValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "graph_invalid",
                    "issues": [i.as_dict() for i in exc.issues],
                },
            ) from exc

        version = int(row.latest_version or 0) + 1
        vrow = (
            await db.execute(
                text(
                    """INSERT INTO workflow_versions
                   (workflow_id, version, serialized, graph, published_by)
                   VALUES (:wid, :v, :serialized ::jsonb, :graph ::jsonb, :by)
                   RETURNING published_at"""
                ),
                {
                    "wid": workflow_id,
                    "v": version,
                    "serialized": json.dumps(serialized, default=str),
                    "graph": json.dumps(graph, default=str),
                    "by": _uid(user),
                },
            )
        ).fetchone()
        await db.execute(
            text(
                """UPDATE workflows SET status = 'published',
                       latest_version = :v, updated_at = now(),
                       disabled_reason = NULL, disabled_at = NULL,
                       health_since = now()
                   WHERE id = :id"""
            ),
            {"id": workflow_id, "v": version},
        )
    return {
        "workflow_id": workflow_id,
        "version": version,
        "published_at": iso(vrow.published_at),
        "status": "published",
    }


@router.post(
    "/{workflow_id}/versions/{version}/rollback",
    dependencies=[require_permission(PUBLISH_PERMISSION)],
)
async def rollback_workflow(
    workflow_id: str,
    version: int,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Rollback = republish an old version (spec F6): copy version N's
    immutable snapshot into a NEW version and make it live.

    Deliberately does NOT re-validate against the current catalog as a gate —
    a rollback is usually an incident response, and version N already passed
    publish validation when it shipped. Catalog drift since then (an agent
    renamed, a module retired) is surfaced as non-blocking ``warnings``
    instead. The draft edit-model (``workflows.graph``) is untouched: runs
    execute versions, never the draft, and unpublished edits must survive.
    """
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        vrow = (
            await db.execute(
                text(
                    "SELECT serialized, graph FROM workflow_versions "
                    "WHERE workflow_id = :wid AND version = :v"
                ),
                {"wid": workflow_id, "v": version},
            )
        ).fetchone()
        if vrow is None:
            raise HTTPException(status_code=404, detail=f"No version {version}")
        latest = int(row.latest_version or 0)
        if version == latest and row.status == "published":
            raise HTTPException(
                status_code=409, detail=f"v{version} is already the live version"
            )
        new_version = latest + 1
        await db.execute(
            text(
                """INSERT INTO workflow_versions
                   (workflow_id, version, serialized, graph, published_by)
                   VALUES (:wid, :v, :serialized ::jsonb, :graph ::jsonb, :by)"""
            ),
            {
                "wid": workflow_id,
                "v": new_version,
                "serialized": json.dumps(parse_jsonb(vrow.serialized, {}), default=str),
                "graph": json.dumps(parse_jsonb(vrow.graph, {}), default=str),
                "by": _uid(user),
            },
        )
        await db.execute(
            text(
                """UPDATE workflows SET status = 'published',
                       latest_version = :v, updated_at = now(),
                       disabled_reason = NULL, disabled_at = NULL,
                       health_since = now()
                   WHERE id = :id"""
            ),
            {"id": workflow_id, "v": new_version},
        )
        ready_modules = (
            await db.execute(
                text("SELECT id FROM workflow_modules WHERE status = 'ready'"),
            )
        ).fetchall()

    warnings: list[dict[str, Any]] = []
    try:
        from gateway.routes.workflows.catalog import known_agent_names
        from gateway.routes.workflows.engine.graph import validate_graph
        from gateway.routes.workflows.tools import (
                destructive_action_names,
                tool_arg_schemas,
            )

        warnings = [
            i.as_dict()
            for i in validate_graph(
                parse_jsonb(vrow.graph, {"nodes": [], "edges": []}),
                known_modules={str(m.id) for m in ready_modules},
                known_agents=known_agent_names(),
                destructive_actions=destructive_action_names(),
                tool_schemas=tool_arg_schemas(),
            )
        ]
    except Exception:  # warnings are advisory — never fail a rollback over them
        warnings = []
    return {
        "workflow_id": workflow_id,
        "version": new_version,
        "rolled_back_to": version,
        "status": "published",
        "warnings": warnings,
    }


@router.post("/{workflow_id}/disable", dependencies=[require_permission(PUBLISH_PERMISSION)])
async def disable_workflow(
    workflow_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Stop all triggers firing without deleting anything (spec R2).

    The reason is recorded the same way the auto-disable policy records its
    own, so the gallery answers "why is this off?" identically whether a human
    or the platform switched it off.
    """
    async with _tenant_session() as db:
        await load_workflow_or_404(db, workflow_id)
        reason = f"Disabled by {_uid(user)}"
        await db.execute(
            text(
                """UPDATE workflows SET status = 'disabled',
                       disabled_reason = :reason, disabled_at = now(),
                       updated_at = now()
                   WHERE id = :id"""
            ),
            {"id": workflow_id, "reason": reason},
        )
    return {"workflow_id": workflow_id, "status": "disabled", "disabled_reason": reason}


@router.post("/{workflow_id}/enable", dependencies=[require_permission(PUBLISH_PERMISSION)])
async def enable_workflow(
    workflow_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Put a disabled workflow back on its existing live version (spec R2).

    The recovery half of disable — and of the auto-disable policy, which is
    only a defensible thing to ship because getting back out of it is one
    click. Publishing would also re-enable, but it would mint a pointless new
    version when the fix was upstream (a credential rotated, an API back up)
    and nothing in the graph changed.

    Resetting ``health_since`` is what makes re-enabling stick: the failures
    that tripped the policy are still the newest rows in ``workflow_runs``, so
    without a fresh window the very next failure would re-disable immediately.
    It carries the same authority as publish because it arms the automation
    just as surely.
    """
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        if not row.latest_version:
            raise HTTPException(
                status_code=409,
                detail="Workflow has never been published — publish it instead",
            )
        if row.status == "published":
            return {
                "workflow_id": workflow_id,
                "status": "published",
                "version": int(row.latest_version),
                "already_live": True,
            }
        await db.execute(
            text(
                """UPDATE workflows SET status = 'published',
                       disabled_reason = NULL, disabled_at = NULL,
                       health_since = now(), updated_at = now()
                   WHERE id = :id"""
            ),
            {"id": workflow_id},
        )
    _log.info("workflows.re_enabled", workflow_id=workflow_id, by=_uid(user))
    return {
        "workflow_id": workflow_id,
        "status": "published",
        "version": int(row.latest_version),
        "already_live": False,
    }
