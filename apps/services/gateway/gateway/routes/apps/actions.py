"""Custom Apps · actions — manifest-declared dispatch (RFC §4.4/§4.7).

A *different* surface from ``tools.py``'s ``cc.tools`` proxy. ``cc.tools`` is
the app's OWN internal bridge to an external integration, callable only from
inside the app's sandboxed frame. **Actions** are the reverse direction: a
small set of named, declared, typed capabilities an app owner explicitly
exposes so OTHER callers — people via the API, and platform agents via the
orchestrator's tool system (a parallel effort) — can call INTO the app's data
and logic directly, without any HTML/JS involved. Same app, two doors.

Manifest shape (``app.json``, alongside the existing ``scopes`` array — RFC
§4.1/§4.7)::

    "actions": [
      {"name": "get_low_stock", "kind": "storage.list", "table": "spools",
       "description": "...", "readonly": true},
      {"name": "get_spool", "kind": "storage.get", "table": "spools",
       "params": {"key": {"type": "string"}}, "readonly": true},
      {"name": "log_usage", "kind": "storage.set", "table": "spools",
       "params": {"key": {...}, "value": {...}}, "readonly": false},
      {"name": "reorder", "kind": "tool.call", "tool": "clickup.create_task",
       "readonly": false}
    ]

Deliberately four mechanical kinds — no filter/expression language, the same
"small fixed vocabulary" stance ``tools.py``'s scope-constraint grammar
already takes. (Supersedes the illustrative ``storage.query``/``storage.mutate``
sketch in the RFC's original §4.7 draft; ``storage.list``/``storage.get``/
``storage.set``/``tool.call`` is the shipped v1 vocabulary.)

* ``storage.list`` — no params; every shared row of ``table`` as ``[{key,
  value}]`` (the ``app_data`` shared partition, ``user_scope = ''`` — actions
  have no notion of "the calling agent's own user_scope").
* ``storage.get``  — ``params.key`` (str); ``{key, value}`` or ``None``.
* ``storage.set``  — ``params.key`` (str) + ``params.value`` (any JSON);
  writes the shared partition, same semantics as
  ``cc.storage.table(t).set(key, value)``.
* ``tool.call``    — wraps a tool this app already declared a ``tool:<name>``
  scope for (an action can never grant MORE than the app's own scopes already
  allow) — reuses ``tools.py``'s ``find_declared_tool_scope``/
  ``merge_tool_args``/``_TOOL_REGISTRY``/``_broker_action_name``/``propose``/
  ``submit`` wholesale; no second copy of the ClickUp-calling logic.

``readonly`` is never trusted from the manifest (informational only, not a
security input): ``storage.list``/``storage.get`` are hardcoded ``True``,
``storage.set`` hardcoded ``False``, ``tool.call`` derived from the wrapped
tool's REAL ``_TOOL_REGISTRY[...].read_only`` — a manifest claiming ``true``
for an actually-destructive tool does not skip the broker.

Authority/execution split (the real safety design here):

* readonly                       → executes for any viewer (person or agent),
                                    no gate.
* ``storage.set`` (not readonly) → auto-applies for any viewer too — it only
                                    touches the app's OWN sandboxed storage
                                    (``open_world=False``), and declaring the
                                    action in a reviewed, published manifest
                                    is already the owner's up-front approval
                                    (the same publish-time review that gates
                                    org+write-scope apps, ``app.publish_review``).
* ``tool.call`` (not readonly)   → reaches an EXTERNAL system
                                    (``open_world=True``). There is no
                                    synchronous confirm-toast possible for a
                                    bare API/agent caller (unlike ``cc.tools``,
                                    which has a viewer physically in the app's
                                    frame). So: a PERSON caller who can also
                                    edit the app auto-applies (owner/editor
                                    testing their own app, ``AuthorityTier.
                                    AUTONOMOUS`` through the same broker path
                                    ``tools.py`` already wired). Every other
                                    caller — any other person, or ANY agent —
                                    always proposes at ``AuthorityTier.SUGGEST``
                                    (unconditionally ``NEEDS_APPROVAL`` per
                                    ``decide_disposition``'s own table). No
                                    bypass, ever, for an unattended caller.

Manifest scopes/actions are read off the LIVE (published+reviewed) version,
never the draft workspace's ``app.json`` — exactly like ``cc.tools`` (see
``tools.py``'s module docstring for why): a builder's in-progress edits must
never silently change what an agent or API caller can already invoke.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext, UserRole
from action_broker import AuthorityTier, propose, submit
from fastapi import Depends, HTTPException

# ⚠️ H4, DELIBERATELY NOT H2 (`saas_multitenancy_handover.md`): this module's
# four `_get_db()` sites (`execute_app_action` + the `_run_storage_*` helpers
# it dispatches to) stay on the UNBOUND seam on purpose. `execute_app_action`
# is the package's one dual-audience entry point: the HTTP route below calls
# it with a member's request identity (tenant bound by `_with_resolved_access`),
# but the orchestrator's agent tools call the SAME function in-process during
# an agent run (`orchestrator/app_tools.py::_make_action_tool`) with
# `UserContext(email=<agent>, role=AGENT)` — no request, no bound tenant.
# Converting these sites to the ambient `tenant_session()` would raise
# `TenantUnbound` for every agent-invoked app action, and letting the agent
# path inherit an ambient tenant is exactly what the H2 runbook forbids for
# non-request callers. H4 threads the EXPLICIT tenant (the app row's
# organization) through the dispatch — `tenant_session(org_id)` — for both
# audiences. The H2 ratchet in `test_db_engine_seam.py` carries these sites
# as named, counted exemptions.
from gateway.routes.apps._common import (
    MAX_STORAGE_ROWS_PER_APP,
    MAX_STORAGE_VALUE_BYTES,
    TABLE_NAME_RE,
    _get_db,
    _log,
    _tenant_session,
    _uid,
    can_edit,
    get_app_or_404,
    record_app_audit_in,
    require_app_user,
    router,
)
from gateway.routes.apps.tools import (
    _TOOL_REGISTRY,
    _broker_action_name,
    _load_live_manifest,
    find_declared_tool_scope,
    merge_tool_args,
)
from pydantic import BaseModel
from sqlalchemy import text

ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ACTION_KINDS = frozenset({"storage.list", "storage.get", "storage.set", "tool.call"})


# ── Manifest lookup (pure) ───────────────────────────────────────────────────

def _find_action(manifest: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The manifest's ``actions`` entry named *name*, or ``None`` when the
    manifest has no ``actions`` array or no entry with that name."""
    actions = (manifest or {}).get("actions")
    if not isinstance(actions, list):
        return None
    for entry in actions:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return None


def _decode_value(value: Any) -> Any:
    """Mirror ``runtime.py``'s ``_row_out``: a JSON-encoded string column
    decodes back to its native value; anything else passes through."""
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return json.loads(value)
    return value


def _require_key(args: dict[str, Any]) -> str:
    key = args.get("key") if isinstance(args, dict) else None
    if not isinstance(key, str) or not key:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_args", "message": "key (string) required"},
        )
    return key


# ── storage.* execution (DB-touching, own session per call) ────────────────

async def _run_storage_list(app_id: str, table: str) -> list[dict[str, Any]]:
    db = await _get_db()
    try:
        rows = (await db.execute(
            text(
                """SELECT key, value FROM app_data
                   WHERE app_id = :app_id AND table_name = :table
                     AND user_scope = ''
                   ORDER BY key"""
            ),
            {"app_id": app_id, "table": table},
        )).fetchall()
    finally:
        await db.close()
    return [{"key": r.key, "value": _decode_value(r.value)} for r in rows]


async def _run_storage_get(
    app_id: str, table: str, key: str,
) -> dict[str, Any] | None:
    db = await _get_db()
    try:
        rec = (await db.execute(
            text(
                """SELECT key, value FROM app_data
                   WHERE app_id = :app_id AND table_name = :table
                     AND key = :key AND user_scope = ''"""
            ),
            {"app_id": app_id, "table": table, "key": key},
        )).fetchone()
    finally:
        await db.close()
    if rec is None:
        return None
    return {"key": rec.key, "value": _decode_value(rec.value)}


async def _run_storage_set(
    app_id: str, table: str, key: str, value: Any, updated_by: str,
) -> dict[str, Any]:
    """Shared-partition write, quota + size clamped the same as
    ``runtime.py``'s ``put_storage_row`` (this bypasses that HTTP endpoint —
    an action dispatch is not a viewer PUT — but the platform storage limits
    still apply)."""
    encoded = json.dumps(value, default=str)
    if len(encoded.encode("utf-8")) > MAX_STORAGE_VALUE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Value too large. Maximum is {MAX_STORAGE_VALUE_BYTES} bytes.",
        )
    db = await _get_db()
    try:
        exists = (await db.execute(
            text(
                """SELECT 1 FROM app_data
                   WHERE app_id = :app_id AND table_name = :table
                     AND key = :key AND user_scope = ''"""
            ),
            {"app_id": app_id, "table": table, "key": key},
        )).fetchone()
        if exists is None:
            count = (await db.execute(
                text("SELECT COUNT(*) FROM app_data WHERE app_id = :app_id"),
                {"app_id": app_id},
            )).scalar_one()
            if int(count) >= MAX_STORAGE_ROWS_PER_APP:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "storage_quota_exhausted",
                        "rows": int(count), "cap": MAX_STORAGE_ROWS_PER_APP,
                    },
                )
        rec = (await db.execute(
            text(
                """INSERT INTO app_data
                   (app_id, table_name, key, value, user_scope, updated_by)
                   VALUES (:app_id, :table, :key, :value ::jsonb, '', :by)
                   ON CONFLICT (app_id, table_name, key, user_scope)
                   DO UPDATE SET value = EXCLUDED.value,
                                 updated_by = EXCLUDED.updated_by,
                                 updated_at = now()
                   RETURNING key, value"""
            ),
            {"app_id": app_id, "table": table, "key": key,
             "value": encoded, "by": updated_by},
        )).fetchone()
        await db.commit()
    finally:
        await db.close()
    return {"key": rec.key, "value": _decode_value(rec.value)}


# ── Manifest → resolved call (raises 404 for anything unusable) ────────────

@dataclass(frozen=True)
class _ResolvedAction:
    kind: str
    readonly: bool
    table: str = ""
    tool_name: str = ""
    merged_args: dict[str, Any] = field(default_factory=dict)


def _unusable(action_name: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"error": "unknown_action", "action": action_name},
    )


def _resolve_action(
    manifest: dict[str, Any], action_name: str, args: dict[str, Any],
) -> _ResolvedAction:
    """Look up *action_name* in *manifest* and validate it into a
    ``_ResolvedAction`` — 404 ``unknown_action`` for anything structurally
    wrong (missing, bad name, unknown kind, an undeclared ``tool.call``
    target, a malformed ``table``). Never trusts the manifest's own
    ``readonly`` field (see module docstring)."""
    action = _find_action(manifest, action_name)
    if action is None:
        raise _unusable(action_name)

    name = str(action.get("name") or "")
    kind = str(action.get("kind") or "")
    if not ACTION_NAME_RE.fullmatch(name) or kind not in ACTION_KINDS:
        raise _unusable(action_name)

    if kind == "tool.call":
        tool_name = str(action.get("tool") or "")
        spec = _TOOL_REGISTRY.get(tool_name)
        constraints = (
            find_declared_tool_scope(manifest, tool_name) if spec else None
        )
        # An action can never grant more than the app's own scopes already
        # allow — no declared `tool:<name>` scope means this action, however
        # it reads in the manifest, is simply unusable.
        if spec is None or constraints is None:
            raise _unusable(action_name)
        return _ResolvedAction(
            kind=kind, readonly=spec.read_only,  # NEVER the manifest's field
            tool_name=tool_name, merged_args=merge_tool_args(args, constraints),
        )

    table = str(action.get("table") or "")
    if not TABLE_NAME_RE.fullmatch(table):
        raise _unusable(action_name)
    return _ResolvedAction(
        kind=kind, readonly=kind != "storage.set",   # hardcoded, not trusted
        table=table,
    )


# ── Immediate execution: readonly (any kind) + storage.set (own data) ──────

async def _run_immediate(
    resolved: _ResolvedAction, args: dict[str, Any], app_id: str, email: str,
) -> Any:
    if resolved.kind == "storage.list":
        return await _run_storage_list(app_id, resolved.table)
    if resolved.kind == "storage.get":
        return await _run_storage_get(app_id, resolved.table, _require_key(args))
    if resolved.kind == "storage.set":
        key = _require_key(args)
        if not isinstance(args, dict) or "value" not in args:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_args", "message": "value required"},
            )
        return await _run_storage_set(app_id, resolved.table, key, args["value"], email)
    return await _TOOL_REGISTRY[resolved.tool_name].handler(resolved.merged_args)


# ── tool.call, not readonly: reaches an external system ────────────────────

def _broker_authority(user: UserContext, row: Any, grants: list[tuple[str, str]]) -> AuthorityTier:
    """A person who can also edit this app is treated like an owner testing
    their own app (auto-apply). Every other caller — any other person, or
    ANY agent — always proposes at ``SUGGEST`` (unconditionally
    ``NEEDS_APPROVAL``): there is no synchronous human-confirm-toast possible
    for a bare API/agent caller, so no bypass is offered."""
    is_person = getattr(user, "role", None) is not UserRole.AGENT
    if is_person and can_edit(row, user, grants):
        return AuthorityTier.AUTONOMOUS
    return AuthorityTier.SUGGEST


async def _run_via_broker(
    slug: str, resolved: _ResolvedAction, authority: AuthorityTier, email: str,
) -> dict[str, Any]:
    proposal = propose(
        actor=f"app:{slug}:{email}", action=_broker_action_name(resolved.tool_name),
        target=f"app:{slug}", payload={"args": resolved.merged_args},
        authority=authority, destructive=True,
    )
    try:
        return await submit(proposal)
    except Exception as exc:  # the broker doesn't guard handler exceptions
        return {"ok": False, "error": str(exc)[:500]}


# ── The one dispatch function (RFC §4.7 — HTTP + in-process share this) ────

async def execute_app_action(
    slug: str, action_name: str, args: dict[str, Any], user: UserContext,
) -> dict[str, Any]:
    """Run one manifest-declared action against the app's LIVE manifest.

    Called by both ``call_app_action`` (below) and — a parallel effort — an
    orchestrator-side tool wrapper, so this is the ONE execution path (the
    same principle ``tools.py`` already established): no HTTP loopback, no
    behavioral drift between "a person hit the API" and "an agent called the
    tool". ``user`` may be a real person (``EMPLOYEE``/``EXECUTIVE``) or an
    agent principal (``UserContext(email=<agent_name>, role=UserRole.AGENT)``
    — the AGENT-role convention ``can_view``/``can_edit`` already consume).
    """
    db = await _get_db()
    try:
        row, grants = await get_app_or_404(db, slug, user)
        manifest = await _load_live_manifest(db, row)
    finally:
        await db.close()

    resolved = _resolve_action(manifest, action_name, args)
    app_id = str(row.id)
    email = _uid(user)
    # The audit row goes to the org of the app row (H4 shape, task 1317),
    # never to request input. An agent run binds no tenant, so the ambient
    # `tenant_session()` would raise there. `app_audit` has FORCE RLS, so an
    # unbound insert writes no row as a role without BYPASSRLS.
    async def _audit(detail: dict[str, Any]) -> None:
        try:
            async with _tenant_session(str(row.organization_id)) as adb:
                await record_app_audit_in(
                    adb, app_id=app_id, user_email=email, kind="action",
                    app_version=row.live_version,
                    detail={"action": action_name, "kind": resolved.kind, **detail},
                )
        except Exception as exc:  # best-effort, like every app audit row
            _log.warning("apps.audit_failed", kind="action", error=str(exc)[:160])

    if resolved.readonly or resolved.kind == "storage.set":
        try:
            result = await _run_immediate(resolved, args, app_id, email)
        except HTTPException:
            raise
        except Exception as exc:
            await _audit({"ok": False, "error": str(exc)[:500]})
            raise HTTPException(status_code=502, detail={"error": str(exc)[:500]}) from exc
        await _audit({"ok": True})
        return {"result": result}

    authority = _broker_authority(user, row, grants)
    outcome = await _run_via_broker(slug, resolved, authority, email)

    if outcome.get("status") == "pending":
        await _audit({"queued": True, "action_id": outcome.get("action_id")})
        return {"queued": True, "action_id": outcome.get("action_id")}
    if outcome.get("ok"):
        await _audit({"ok": True})
        return {"result": outcome.get("result")}

    await _audit({"ok": False, "error": outcome.get("error")})
    raise HTTPException(
        status_code=502,
        detail={"error": outcome.get("error") or "action call failed"},
    )


# ── The route ─────────────────────────────────────────────────────────────

class ActionCallRequest(BaseModel):
    args: dict[str, Any] = {}


@router.post("/{slug}/actions/{action_name}")
async def call_app_action(
    slug: str,
    action_name: str,
    body: ActionCallRequest,
    user: UserContext = Depends(require_app_user),
) -> Any:
    """Thin HTTP wrapper — all the logic lives in ``execute_app_action`` so
    the orchestrator-side caller (a parallel effort) gets identical behavior
    without an HTTP loopback."""
    return await execute_app_action(slug, action_name, body.args, user)
