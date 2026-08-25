"""Custom Apps → orchestrator tools (RFC §4.7, Phase 3b).

Granting an app to ``agents:*`` or ``agent:<name>`` (``app_grants.subject`` —
the sharing mechanism in ``gateway.routes.apps.grants``) makes its declared
``actions`` (see ``gateway.routes.apps.actions``'s module docstring for the
manifest schema) callable as ordinary platform tools. Injected through
``_inject_agent_tools()``'s SAFE pipeline (scope filtering +
``_gate_injected_tool``'s ``permission_policy.decide()`` gate) — never the
``_load_specialist_agents_as_tools()`` whole-agent-as-delegate path in
``agents.py``, which appends tools directly to ``Agent(tools=...)`` before
injection ever runs and so never reaches the permission gate at all (a
pre-existing gap this deliberately does not replicate).

Each action becomes one dynamically-built plain async tool function named
``app_<slug>_<action>``, calling
``gateway.routes.apps.actions.execute_app_action`` in-process — the SAME
function the HTTP route uses, so an agent calling a tool and a person
hitting the API behave identically (queueing a ``tool.call`` for review on
any non-owner caller, auditing, everything) — with
``UserContext(email=agent_name, role=UserRole.AGENT)``, the existing
convention ``can_view``/``can_edit`` (``gateway/routes/apps/_common.py``)
already consume for agent principals: ``can_view`` reconstructs
``f"agent:{email}"`` itself to match a stored ``agent:<name>`` grant, so
``email`` must be the BARE agent name, never pre-prefixed.
"""
from __future__ import annotations

import inspect
import json
from typing import Any

from acb_common import get_logger

_log = get_logger("orchestrator.app_tools")

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str, "number": float, "integer": int,
    "boolean": bool, "object": dict, "array": list,
}


def _granted_live_apps(agent_name: str) -> list[tuple[str, dict[str, Any]]]:
    """``(slug, live_manifest)`` for every LIVE app granted to *agent_name*,
    directly (``agent:<name>``) or via the ``agents:*`` wildcard.

    A best-effort SYNC Postgres lookup — mirrors ``executor.py``'s
    ``_get_stored_session_id``, both already making a plain blocking
    ``acb_graph`` call from this same injection path. ``_inject_agent_tools``
    is a plain sync function called directly on the event loop, so this must not
    be ``async``; any failure here must never block tool injection for every
    other agent.

    WS-29 acb_graph slice 7: ``apps``/``app_grants``/``app_versions`` are all
    FORCE-RLS'd after phase-4 promotion, so an UNBOUND ``get_session()`` read
    returns ZERO rows and the agent silently loses every granted app. Behind
    ``ACB_GRAPH_TENANT_BIND`` this binds the run's tenant so the join RETURNS
    ROWS. The opener is resolved on the event-loop frame (this runs on the loop,
    no worker-thread hop). flag OFF → the unbound ``get_session`` (byte-identical);
    flag ON + no resolvable org → fail closed (no granted apps), never an unbound
    read on a FORCE-RLS'd catalog.
    """
    try:
        from sqlalchemy import text  # noqa: PLC0415

        from orchestrator.executor import _graph_session_opener_current
        _open = _graph_session_opener_current()
        if _open is None:
            _log.warning("app_tools.grant_lookup_skipped_no_org", agent=agent_name)
            return []
        with _open() as s:
            rows = s.execute(
                text(
                    """SELECT DISTINCT a.slug, v.manifest
                       FROM apps a
                       JOIN app_grants g ON g.app_id = a.id
                       JOIN app_versions v
                         ON v.app_id = a.id AND v.version = a.live_version
                       WHERE a.status = 'live' AND a.live_version IS NOT NULL
                         AND g.subject IN ('agents:*', :subject)"""
                ),
                {"subject": f"agent:{agent_name}"},
            ).fetchall()
    except Exception:  # noqa: BLE001 — best-effort, never blocks injection
        _log.warning("app_tools.grant_lookup_failed", agent=agent_name)
        return []

    out: list[tuple[str, dict[str, Any]]] = []
    for r in rows:
        manifest = r.manifest
        if isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except ValueError:
                continue
        if isinstance(manifest, dict):
            out.append((r.slug, manifest))
    return out


def _action_risk(slug: str, action: dict[str, Any]) -> dict[str, bool]:
    """Risk hints for one action — DERIVED, never the manifest's own
    (untrusted) ``readonly`` field. Mirrors ``actions.py``'s own authority
    derivation: ``storage.list``/``storage.get`` are read-only by
    construction; ``storage.set`` touches only the app's own sandboxed
    storage (never open-world); ``tool.call`` inherits the REAL wrapped
    tool's registered risk from ``_TOOL_REGISTRY`` (an unrecognised/removed
    tool is treated as destructive + open-world — fail conservative, not
    permissive).
    """
    kind = action.get("kind")
    if kind in ("storage.list", "storage.get"):
        return {"read_only": True, "destructive": False,
                "idempotent": True, "open_world": False}
    if kind == "storage.set":
        return {"read_only": False, "destructive": False,
                "idempotent": False, "open_world": False}
    try:
        from gateway.routes.apps.tools import _TOOL_REGISTRY  # noqa: PLC0415
        spec = _TOOL_REGISTRY.get(str(action.get("tool") or ""))
    except ImportError:
        spec = None
    if spec is None:
        return {"read_only": False, "destructive": True,
                "idempotent": False, "open_world": True}
    return {"read_only": spec.read_only, "destructive": spec.destructive,
            "idempotent": False, "open_world": spec.open_world}


def _make_action_tool(slug: str, action: dict[str, Any], agent_name: str) -> Any:
    """One manifest ``actions`` entry → one plain async tool function.

    A FACTORY called once per iteration (never an inline loop-body closure)
    so each returned function captures its OWN ``slug``/``action``/``name``
    — the classic Python closure-in-loop bug otherwise leaves every tool
    silently calling whichever action was built LAST.
    """
    name = str(action["name"])
    kind = str(action["kind"])
    description = str(action.get("description") or f"{kind} on {slug}")
    params: dict[str, Any] = action.get("params") or {}
    tool_name = f"app_{slug}_{name}".replace("-", "_")

    async def _run(**kwargs: Any) -> str:
        from acb_auth import UserContext, UserRole  # noqa: PLC0415
        from gateway.routes.apps.actions import execute_app_action  # noqa: PLC0415
        user = UserContext(email=agent_name, role=UserRole.AGENT)
        try:
            result = await execute_app_action(slug, name, kwargs, user)
        except Exception as exc:  # noqa: BLE001 — report to the agent, don't raise
            return f"App action {tool_name!r} failed: {exc}"
        if result.get("queued"):
            return (
                f"Sent for human review (action_id={result.get('action_id')}) "
                "— this action reaches an external system and needs approval."
            )
        return str(result.get("result"))

    _run.__name__ = tool_name
    _run.__doc__ = f"[Custom App: {slug}] {description}"

    # Explicit synthetic signature from the action's declared params — NOT
    # auto-derived from _run's own (**kwargs) implementation signature, so
    # the schema the LLM sees actually names the app's real parameters.
    sig_params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    for pname, pschema in params.items():
        ptype = _JSON_TYPE_MAP.get((pschema or {}).get("type", "string"), str)
        annotations[pname] = ptype
        sig_params.append(inspect.Parameter(
            pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ptype,
        ))
    annotations["return"] = str
    _run.__annotations__ = annotations
    _run.__signature__ = inspect.Signature(sig_params, return_annotation=str)

    try:
        from acb_skills.tool_annotations import TOOL_ANNOTATIONS  # noqa: PLC0415
        # Registered at build time, same as any other injected tool (HH-2).
        # Like _build_registry_block's own cache (see its docstring), the
        # addendum's cached risk_summary_block() text may lag a newly
        # granted app until process restart / cache eviction — an accepted,
        # pre-existing tradeoff in this injection path, not new here.
        TOOL_ANNOTATIONS[tool_name] = _action_risk(slug, action)
    except ImportError:
        pass

    return _run


def load_app_action_tools(agent_name: str) -> list[Any]:
    """Every callable tool for Custom Apps granted to *agent_name*, directly
    or via the ``agents:*`` wildcard. Best-effort per action: a malformed
    manifest entry is skipped, never raised — one bad app must not break
    tool injection for every agent.
    """
    from gateway.routes.apps.actions import ACTION_KINDS, ACTION_NAME_RE  # noqa: PLC0415

    tools: list[Any] = []
    for slug, manifest in _granted_live_apps(agent_name):
        actions = manifest.get("actions")
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            name = action.get("name")
            kind = action.get("kind")
            if not isinstance(name, str) or not ACTION_NAME_RE.fullmatch(name):
                continue
            if kind not in ACTION_KINDS:
                continue
            try:
                tools.append(_make_action_tool(slug, action, agent_name))
            except Exception:  # noqa: BLE001
                _log.warning(
                    "app_tools.build_failed", slug=slug, action=name,
                )
    if tools:
        _log.info(
            "app_tools.loaded", agent=agent_name, count=len(tools),
            tools=[getattr(t, "__name__", "?") for t in tools],
        )
    return tools
