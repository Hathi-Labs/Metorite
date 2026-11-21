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

**Arming one trigger (STB-6c, ``ai_studio.md`` E13c).** The owner decided on
2026-11-13 that arming a trigger on a published workflow needs
``workflows:publish`` (D-number pending). Two doors obey it:

* ``POST /{id}/triggers/{kind}/state`` arms or disarms ONE trigger, under the
  same gate as the four routes above.
* A Save (``PUT /{id}``) calls :func:`refuse_live_arming`. Without the right,
  a Save that arms a trigger gets 403 and writes nothing. The rules:

  1. The rule covers each workflow that has a version: a published one and a
     disabled one. An enable writes only the status, so a trigger that a
     Save armed while the workflow was off goes live with no second look.
     Only a draft that was never published is free of the rule. Its first
     publish needs the right, and the editor saves before it publishes.
  2. A Save replaces the triggers as a whole. A schedule, webhook or event
     trigger that is on in the Save and not on before it is armed. So an
     add, a turn-on, and a config change of a trigger that is on (a new
     cron, a new filter, a new or removed webhook secret) each arm.
  3. Each copy counts. A second trigger with the same config arms.

  A turn-off, a removal, a manual or api trigger, and an unchanged Save arm
  nothing, and need no right.

The scans and the hook door read ``t.enabled AND w.status = 'published'``, and
they run only for an org on ``WORKFLOW_UNATTENDED_ORGS`` (``unattended.py``).
So the route answers ``live: true`` only when all three hold.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterable
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.workflows import unattended
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
from gateway.routes.workflows.service import UNATTENDED_TRIGGERS
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import text

#: The authority to make a workflow live (spec Q3). Named once so the route
#: gate and the catalog's capability probe can never drift apart.
PUBLISH_PERMISSION = "workflows:publish"

#: The trigger kinds that have a state. A scan or the hook door reads the
#: ``enabled`` flag of each one. A ``manual`` or an ``api`` trigger starts a
#: run only when a member or an agent asks, and no door reads its flag.
ARMABLE_KINDS = UNATTENDED_TRIGGERS

#: The 403 of a Save that would arm a trigger without the right.
ARMING_REFUSAL = (
    "Only a member who may publish workflows can turn on a trigger of a "
    "workflow that has been published. Save it with the trigger off, or ask "
    "a publisher."
)


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


# ── Arming one trigger (STB-6c) ──────────────────────────────────────────────


def _armed(triggers: Iterable[tuple[str, Any, bool]]) -> Counter[tuple[str, str]]:
    """The enabled triggers of :data:`ARMABLE_KINDS`, one key for each copy.

    The kind and the config identify a trigger, as in the E3 rule of
    ``crud._studio_event_keys``.
    """
    return Counter(
        (str(kind), json.dumps(config or {}, sort_keys=True, default=str))
        for kind, config, enabled in triggers
        if enabled and kind in ARMABLE_KINDS
    )


def arms_a_live_trigger(
    old: Iterable[tuple[str, Any, bool]], new: Iterable[tuple[str, Any, bool]]
) -> bool:
    """True when *new* holds an enabled armable trigger that *old* does not hold.

    Each item is ``(kind, config, enabled)``. A Save replaces the triggers as a
    whole, so an add, a turn-on, a config change of an enabled trigger and a
    second copy of one each arm a trigger. A turn-off, a removal and a
    ``manual`` or ``api`` trigger arm nothing.
    """
    return bool(_armed(new) - _armed(old))


def refuse_live_arming(
    user: UserContext,
    latest_version: int | None,
    old: Iterable[tuple[str, Any, bool]],
    new: Iterable[tuple[str, Any, bool]],
) -> None:
    """403 when a Save arms a trigger of a workflow that has a version, and the
    caller does not hold :data:`PUBLISH_PERMISSION`.

    ``crud.update_workflow`` calls it before it writes. The rule covers a
    published AND a disabled workflow. An enable of a disabled workflow
    writes only its status, so it would make an armed trigger live with no
    second look. Only a draft that was never published passes. Its first
    publish needs the right, and the editor saves before it publishes.
    The check reads the right only when the Save arms a trigger, so every
    other Save is unchanged.
    """
    if not latest_version or not arms_a_live_trigger(old, new):
        return
    if user.has_permission(PUBLISH_PERMISSION):
        return
    raise HTTPException(status_code=403, detail=ARMING_REFUSAL)


class TriggerState(BaseModel):
    """The body of ``POST /{id}/triggers/{kind}/state``.

    ``trigger_id`` is the ``id`` of one trigger in ``GET /workflows/{id}``. It
    is needed only when the workflow holds more than one trigger of the kind.
    A Save gives each trigger a new id, so a stale id gets 404 and arms
    nothing.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    trigger_id: uuid.UUID | None = None


def _pick_trigger(rows: list[Any], kind: str, trigger_id: uuid.UUID | None) -> Any:
    """The one trigger of *kind* that the request names, or a plain refusal."""
    of_kind = [r for r in rows if r.kind == kind]
    if trigger_id is not None:
        for r in of_kind:
            if str(r.id).lower() == str(trigger_id).lower():
                return r
        raise HTTPException(
            status_code=404,
            detail=(
                f"This workflow has no {kind} trigger with that id. A save gives "
                "each trigger a new id, so read the workflow again."
            ),
        )
    if not of_kind:
        raise HTTPException(status_code=404, detail=f"This workflow has no {kind} trigger.")
    if len(of_kind) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This workflow has {len(of_kind)} {kind} triggers. Name one with "
                "trigger_id, from the triggers of the workflow."
            ),
        )
    return of_kind[0]


#: The write of one trigger. Each names the workflow and the kind too, so a
#: trigger of another workflow or kind never changes.
_SET_STATE_SQL = (
    "UPDATE workflow_triggers SET enabled = :on "
    "WHERE id = :tid AND workflow_id = :wid AND kind = :kind RETURNING id"
)
#: A schedule that turns on loses its old baseline. The next scan sets a new
#: one (``scheduler._claim_baseline``), so no catch-up tick fires.
_ARM_SCHEDULE_SQL = (
    "UPDATE workflow_triggers SET enabled = :on, last_fired_at = NULL "
    "WHERE id = :tid AND workflow_id = :wid AND kind = :kind RETURNING id"
)


def _state_note(enabled: bool, status: str | None, org_listed: bool) -> str:
    """One plain sentence on what the new state does."""
    if not enabled:
        return "The trigger is off. It starts nothing."
    if status == "published" and org_listed:
        return "The trigger is on, and the workflow is published, so the trigger is live."
    if status == "published":
        return (
            "The trigger is armed, but this organization does not run workflows "
            "without a person yet. It starts nothing until that is turned on."
        )
    if status == "disabled":
        return (
            "The trigger is on, but the workflow is disabled. It starts nothing "
            "until the workflow is enabled."
        )
    return (
        "The trigger is on, but the workflow is not published. It starts nothing "
        "until the workflow is published."
    )


@router.post(
    "/{workflow_id}/triggers/{kind}/state",
    dependencies=[require_permission(PUBLISH_PERMISSION)],
)
async def set_trigger_state(
    workflow_id: str,
    kind: str,
    body: TriggerState,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Arm or disarm ONE trigger of a workflow, and leave the others alone.

    * **The gate** is ``workflows:publish``, as for the four routes above
      (owner decision of 2026-11-13). The org is the bound session's, so the
      workflow of another org is 404 under RLS.
    * **The kind** must be one of :data:`ARMABLE_KINDS`. A ``manual`` or an
      ``api`` trigger is always available, so it gets 422.
    * **One trigger.** With one trigger of the kind, no ``trigger_id`` is
      needed. With two or more, the body must name one, or the answer is 409.
    * **The E3 rule.** Only the author of the workflow may turn on a Studio
      record trigger (``crud._refuse_foreign_studio_trigger``). A turn-off is
      open to each holder of the right.
    * **A draft or a disabled workflow.** The route writes the flag, and the
      answer says ``live: false`` with a ``note``. The scans read
      ``t.enabled AND w.status = 'published'``, so nothing starts until a
      publish or an enable.
    * **An org off ``WORKFLOW_UNATTENDED_ORGS``.** No door starts a run there
      (``unattended.org_allowed``, the one reader). So ``live`` is false, and
      the ``note`` says that the org does not run workflows without a person.
    * **A schedule** that turns on loses its old ``last_fired_at``. The next
      scan sets a new baseline, as for a new schedule. So it never fires a
      catch-up tick for the time it was off.

    The answer is the new state of the trigger. It never holds the config,
    because a webhook config holds its secret, and it never holds the hook
    token.
    """
    # A lazy import: crud registers the `/{workflow_id}` routes, and the
    # package `__init__` fixes the order of registration.
    from gateway.routes.workflows.crud import (
        TRIGGER_KINDS,
        TriggerSpec,
        _is_author,
        _refuse_foreign_studio_trigger,
    )

    if kind not in TRIGGER_KINDS:
        raise HTTPException(status_code=422, detail=f"Unknown trigger kind '{kind}'")
    if kind not in ARMABLE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"A {kind} trigger is always available, so it has no on or off "
                "state. Only a schedule, webhook or event trigger has one."
            ),
        )
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        # Lock every trigger of the workflow, then the workflow row: the same
        # order as a Save, so the two cannot deadlock.
        rows = (
            await db.execute(
                text(
                    "SELECT id, kind, config, enabled FROM workflow_triggers "
                    "WHERE workflow_id = :wid ORDER BY created_at, id FOR UPDATE"
                ),
                {"wid": workflow_id},
            )
        ).fetchall()
        target = _pick_trigger(rows, kind, body.trigger_id)
        changed = bool(target.enabled) != body.enabled
        if changed and body.enabled and not _is_author(row, user):
            await _refuse_foreign_studio_trigger(
                db,
                workflow_id,
                [
                    TriggerSpec(
                        kind=r.kind,
                        config=parse_jsonb(r.config, {}) or {},
                        enabled=True if r.id == target.id else bool(r.enabled),
                    )
                    for r in rows
                ],
            )
        if changed:
            sql = _ARM_SCHEDULE_SQL if kind == "schedule" and body.enabled else _SET_STATE_SQL
            done = (
                await db.execute(
                    text(sql),
                    {"on": body.enabled, "tid": target.id, "wid": workflow_id, "kind": kind},
                )
            ).fetchone()
            if done is None:  # the row lock makes this unreachable. It fails closed.
                raise HTTPException(
                    status_code=409,
                    detail="The triggers of this workflow changed. Read the workflow again.",
                )
            await db.execute(
                text("UPDATE workflows SET updated_at = now() WHERE id = :id"),
                {"id": workflow_id},
            )
    if changed:
        _log.info(
            "workflows.trigger_state",
            workflow_id=workflow_id,
            trigger_id=str(target.id),
            kind=kind,
            enabled=body.enabled,
            by=_uid(user),
        )
    # The org of the row that RLS let this session read. The doors ask the
    # same list for the same org before they start a run.
    org_listed = unattended.org_allowed(str(row.organization_id or ""))
    return {
        "workflow_id": workflow_id,
        "trigger_id": str(target.id),
        "kind": kind,
        "enabled": body.enabled,
        "changed": changed,
        "workflow_status": row.status,
        "live": bool(
            body.enabled and row.status == "published" and row.latest_version and org_listed
        ),
        "note": _state_note(body.enabled, row.status, org_listed),
    }
