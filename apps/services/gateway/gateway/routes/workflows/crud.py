"""Workflow CRUD — the edit-model lifecycle (spec F1, §6).

Workflows are org-internal: any member holding the ``workflows`` feature sees
and edits every workflow (v1 — per-workflow ACLs are an open question, spec
Q3 covers publish rights). ``owner_email`` is attribution, not access.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.workflows.core import (
    HOOK_PATH,
    MAX_GRAPH_BYTES,
    _tenant_session,
    _uid,
    hook_url,
    iso,
    load_triggers,
    load_workflow_or_404,
    new_hook_token,
    parse_jsonb,
    router,
)
from gateway.routes.workflows.engine.graph import validate_graph
from pydantic import BaseModel, Field
from sqlalchemy import text

TRIGGER_KINDS = ("manual", "api", "schedule", "webhook", "event")


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class TriggerSpec(BaseModel):
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    graph: dict[str, Any] | None = None
    variables: dict[str, Any] | None = None
    triggers: list[TriggerSpec] | None = None


def _row_summary(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description or "",
        "owner_email": row.owner_email,
        "status": row.status,
        "latest_version": row.latest_version,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        # Why a workflow is off — written by the human who hit Disable or by
        # the R2 auto-disable policy (migration 134). ``getattr`` so a gateway
        # running ahead of its migration degrades to "no reason" instead of
        # 500-ing the whole gallery.
        "disabled_reason": getattr(row, "disabled_reason", None),
        "disabled_at": iso(getattr(row, "disabled_at", None)),
    }


@router.get("")
async def list_workflows(
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    async with _tenant_session() as db:
        rows = (
            await db.execute(
                text(
                    """SELECT w.*,
                      (SELECT status FROM workflow_runs r
                        WHERE r.workflow_id = w.id
                        ORDER BY r.started_at DESC LIMIT 1) AS last_run_status,
                      (SELECT started_at FROM workflow_runs r
                        WHERE r.workflow_id = w.id
                        ORDER BY r.started_at DESC LIMIT 1) AS last_run_at,
                      (SELECT count(*) FROM workflow_triggers t
                        WHERE t.workflow_id = w.id AND t.enabled) AS trigger_count
                 FROM workflows w ORDER BY w.updated_at DESC"""
                )
            )
        ).fetchall()
    return [
        {
            **_row_summary(r),
            "last_run_status": r.last_run_status,
            "last_run_at": iso(r.last_run_at),
            "trigger_count": int(r.trigger_count or 0),
        }
        for r in rows
    ]


@router.post("", status_code=201)
async def create_workflow(
    body: WorkflowCreate,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        row = (
            await db.execute(
                text(
                    """INSERT INTO workflows (name, description, owner_email, hook_token)
                   VALUES (:name, :description, :owner, :token)
                   RETURNING *"""
                ),
                {
                    "name": body.name.strip(),
                    "description": body.description.strip(),
                    "owner": _uid(user),
                    "token": new_hook_token(),
                },
            )
        ).fetchone()
    return {
        **_row_summary(row),
        "graph": parse_jsonb(row.graph, {}),
        "variables": parse_jsonb(row.variables, {}),
        "triggers": [],
    }


@router.post("/{workflow_id}/duplicate", status_code=201)
async def duplicate_workflow(
    workflow_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Copy a workflow into a fresh DRAFT (spec F1).

    Everything editable travels: graph, variables, triggers (kind/config/
    enabled — safe to copy verbatim because triggers only fire for PUBLISHED
    workflows, and the copy starts as a draft with no versions). The webhook
    hook token is always regenerated: it is a credential, never cloned.
    """
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        triggers = await load_triggers(db, workflow_id)
        name = f"{row.name} (copy)"[:120]
        new = (
            await db.execute(
                text(
                    """INSERT INTO workflows
                       (name, description, owner_email, hook_token, graph, variables)
                   VALUES (:name, :description, :owner, :token,
                           :graph ::jsonb, :variables ::jsonb)
                   RETURNING *"""
                ),
                {
                    "name": name,
                    "description": row.description or "",
                    "owner": _uid(user),
                    "token": new_hook_token(),
                    "graph": json.dumps(parse_jsonb(row.graph, {}), default=str),
                    "variables": json.dumps(parse_jsonb(row.variables, {}), default=str),
                },
            )
        ).fetchone()
        for t in triggers:
            await db.execute(
                text(
                    """INSERT INTO workflow_triggers (workflow_id, kind, config, enabled)
                       VALUES (:wid, :kind, :config ::jsonb, :enabled)"""
                ),
                {
                    "wid": str(new.id),
                    "kind": t["kind"],
                    "config": json.dumps(t["config"], default=str),
                    "enabled": t["enabled"],
                },
            )
    return {
        **_row_summary(new),
        "graph": parse_jsonb(new.graph, {}),
        "variables": parse_jsonb(new.variables, {}),
        "triggers": [
            # id/last_fired_at belong to the source's triggers, not the copy's.
            {k: v for k, v in t.items() if k not in ("id", "last_fired_at")}
            for t in triggers
        ],
    }


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        triggers = await load_triggers(db, workflow_id)
        versions = (
            await db.execute(
                text(
                    "SELECT version, published_by, published_at "
                    "FROM workflow_versions WHERE workflow_id = :id "
                    "ORDER BY version DESC LIMIT 20"
                ),
                {"id": workflow_id},
            )
        ).fetchall()
    return {
        **_row_summary(row),
        "graph": parse_jsonb(row.graph, {"nodes": [], "edges": []}),
        "variables": parse_jsonb(row.variables, {}),
        "hook_token": row.hook_token,
        # The gateway names the URL rather than letting the browser assemble
        # one from its own origin — external callers must hit the gateway
        # directly, not the control-plane proxy (see core.hook_url).
        "hook_url": hook_url(row.hook_token),
        "hook_path": HOOK_PATH.format(token=row.hook_token or ""),
        "triggers": triggers,
        "versions": [
            {
                "version": v.version,
                "published_by": v.published_by,
                "published_at": iso(v.published_at),
            }
            for v in versions
        ],
    }


@router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    if body.graph is not None:
        encoded = json.dumps(body.graph, default=str)
        if len(encoded.encode()) > MAX_GRAPH_BYTES:
            raise HTTPException(status_code=413, detail="Graph too large")
    if body.triggers is not None:
        _validate_trigger_specs(body.triggers)
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        sets, params = [], {"id": workflow_id}
        if body.name is not None:
            sets.append("name = :name")
            params["name"] = body.name.strip()
        if body.description is not None:
            sets.append("description = :description")
            params["description"] = body.description.strip()
        if body.graph is not None:
            sets.append("graph = :graph ::jsonb")
            params["graph"] = json.dumps(body.graph, default=str)
        if body.variables is not None:
            sets.append("variables = :variables ::jsonb")
            params["variables"] = json.dumps(body.variables, default=str)
        if sets:
            sets.append("updated_at = now()")
            await db.execute(
                text(f"UPDATE workflows SET {', '.join(sets)} WHERE id = :id"),
                params,
            )
        if body.triggers is not None:
            # Triggers are replaced wholesale, but `last_fired_at` is not part
            # of the maker's document — it is the scheduler's dedup state. Drop
            # it on every save and a cron would be re-armed each time anyone
            # touched the canvas, which both loses the "already fired this
            # tick" guarantee and (before the baseline fix) parked the schedule
            # in a state that never fires again. Carry it across for triggers
            # that did not actually change; a schedule whose cron or timezone
            # was edited is deliberately re-armed from now.
            previous = {
                _trigger_identity(r.kind, parse_jsonb(r.config, {}) or {}): r.last_fired_at
                for r in (
                    await db.execute(
                        text(
                            "SELECT kind, config, last_fired_at "
                            "FROM workflow_triggers WHERE workflow_id = :id"
                        ),
                        {"id": workflow_id},
                    )
                ).fetchall()
            }
            await db.execute(
                text("DELETE FROM workflow_triggers WHERE workflow_id = :id"),
                {"id": workflow_id},
            )
            for trig in body.triggers:
                await db.execute(
                    text(
                        """INSERT INTO workflow_triggers
                           (workflow_id, kind, config, enabled, last_fired_at)
                           VALUES (:wid, :kind, :config ::jsonb, :enabled, :last_fired)"""
                    ),
                    {
                        "wid": workflow_id,
                        "kind": trig.kind,
                        "config": json.dumps(trig.config, default=str),
                        "enabled": trig.enabled,
                        "last_fired": previous.get(
                            _trigger_identity(trig.kind, trig.config)
                        ),
                    },
                )
        # No mid-block commit: the wrapper owns the one transaction (a commit
        # here would end it and drop the tenant GUC for the reads below). The
        # re-reads see this transaction's own writes, same rows as before.
        row = await load_workflow_or_404(db, workflow_id)
        triggers = await load_triggers(db, workflow_id)
    return {
        **_row_summary(row),
        "graph": parse_jsonb(row.graph, {"nodes": [], "edges": []}),
        "variables": parse_jsonb(row.variables, {}),
        "triggers": triggers,
    }


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: str,
    user: UserContext = Depends(get_current_user),
) -> None:
    async with _tenant_session() as db:
        await load_workflow_or_404(db, workflow_id)
        await db.execute(
            text("DELETE FROM workflows WHERE id = :id"),
            {"id": workflow_id},
        )


@router.post("/{workflow_id}/validate")
async def validate_workflow(
    workflow_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Design-time validation for editor badges (spec F4, §3.2 rung 3)."""
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        ready_modules = (
            await db.execute(
                text("SELECT id FROM workflow_modules WHERE status = 'ready'"),
            )
        ).fetchall()
    from gateway.routes.workflows.catalog import known_agent_names
    from gateway.routes.workflows.tools import (
                destructive_action_names,
                tool_arg_schemas,
            )

    issues = validate_graph(
        parse_jsonb(row.graph, {"nodes": [], "edges": []}),
        known_modules={str(m.id) for m in ready_modules},
        known_agents=known_agent_names(),
        destructive_actions=destructive_action_names(),
        tool_schemas=tool_arg_schemas(),
    )
    return {"ok": not issues, "issues": [i.as_dict() for i in issues]}


def _trigger_identity(kind: str, config: dict[str, Any]) -> tuple[str, str]:
    """What makes two saves "the same trigger" for fire-state purposes.

    For a schedule that is the cron expression and its timezone: change either
    and the maker meant a different schedule, so it re-arms from now rather
    than inheriting a baseline that could fire an unexpected catch-up tick.
    Other kinds carry no scheduler state, so the kind alone is enough.
    """
    if kind == "schedule":
        cron = str(config.get("cron") or "").strip()
        timezone = str(config.get("timezone") or "UTC").strip() or "UTC"
        return (kind, f"{cron}@{timezone}")
    return (kind, "")


def _validate_trigger_specs(triggers: list[TriggerSpec]) -> None:
    """422 on malformed trigger bindings (kind vocabulary + per-kind config)."""
    for trig in triggers:
        if trig.kind not in TRIGGER_KINDS:
            raise HTTPException(status_code=422, detail=f"Unknown trigger kind '{trig.kind}'")
        if trig.kind == "schedule":
            cron = str(trig.config.get("cron") or "").strip()
            timezone = str(trig.config.get("timezone") or "UTC").strip() or "UTC"
            if not _timezone_is_valid(timezone):
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown timezone '{timezone}' — use an IANA name like Asia/Kolkata",
                )
            if not cron or not _cron_is_valid(cron, timezone):
                raise HTTPException(
                    status_code=422,
                    detail="Schedule triggers need a valid cron expression",
                )
        if trig.kind == "event" and not str(trig.config.get("source") or "").strip():
            raise HTTPException(
                status_code=422,
                detail="Event triggers need a source (e.g. zoho)",
            )


def _cron_is_valid(expr: str, timezone: str = "UTC") -> bool:
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(expr, timezone=timezone)
        return True
    except (ValueError, TypeError):
        return False


def _timezone_is_valid(name: str) -> bool:
    """IANA zone names only — the same vocabulary APScheduler resolves.

    Validated here rather than at fire time because a bad zone discovered by
    the scanner is a schedule that silently never runs; discovered at save
    time it is a 422 the maker can act on.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
