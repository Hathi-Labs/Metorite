"""The node palette catalog — served, never hard-coded (spec F3, D7).

Integrations come from ``acb_skills``'s registry with a live availability
probe, tools from the workflow tool registry, modules from the org module
library. The palette can only offer what the platform has.

**One AI step (WFC-2, D223 proposed, ``ai_studio.md`` §14).** The palette, the
search and the Copilot offer ONE agent step, "Ask Mira": the ``orchestrator``
agent of the live registry, under a member label. No specialist and no dynamic
agent is offered. The OFFER shrinks and the VALIDATION does not:
``known_agent_names()`` still holds every registry name and every dynamic
name, so an old graph that names a specialist still validates, publishes and
runs (R6 expand). Fence: ``tests/unit/test_workflows_catalog_offer.py``.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.workflows.core import _log, _tenant_session, iso, router
from gateway.routes.workflows.tools import list_tools
from sqlalchemy import text

#: The one agent that a workflow offers (WFC-2). The runtime name stays
#: ``orchestrator``, so a node runs the same agent as before. The member sees
#: the label only.
ASK_MIRA_AGENT = "orchestrator"
ASK_MIRA_LABEL = "Ask Mira"
#: Member words. No product name of another vendor, and no model or tier name.
ASK_MIRA_DESCRIPTION = (
    "Give Mira, the Metorite assistant, a message about the data of this run. "
    "Mira can sort, sum up, decide or draft text. The answer goes to the next step."
)
#: The agent names that a member or the Copilot may put in a NEW node.
OFFERED_AGENTS = frozenset({ASK_MIRA_AGENT})

#: Static metadata for the non-capability node types (Logic / Output).
NODE_TYPE_META = [
    {
        "type": "trigger",
        "category": "trigger",
        "label": "Trigger",
        "description": "Where the workflow starts: manual, webhook, schedule, or event.",
    },
    {
        "type": "agent",
        "category": "agent",
        "label": ASK_MIRA_LABEL,
        "description": ASK_MIRA_DESCRIPTION,
    },
    {
        "type": "tool",
        "category": "tool",
        "label": "Integration action",
        "description": "Invoke a typed integration action or HTTP request.",
    },
    {
        "type": "module",
        "category": "module",
        "label": "Module",
        "description": "Run a Module Studio code module (pure transform).",
    },
    {
        "type": "condition",
        "category": "logic",
        "label": "Condition",
        "description": "Branch on a comparison — true/false handles.",
    },
    {
        "type": "set",
        "category": "logic",
        "label": "Set variables",
        "description": "Assign values into the run's variables.",
    },
    {
        "type": "approval",
        "category": "logic",
        "label": "Human approval",
        "description": (
            "Pause the run until a human approves it in the approvals inbox. "
            "Required upstream of any write action."
        ),
    },
    {
        "type": "wait",
        "category": "logic",
        "label": "Wait",
        "description": (
            "Pause for a duration before continuing — seconds to days. "
            "Long waits survive restarts; the scheduler resumes them."
        ),
    },
    {
        "type": "pm_task",
        "category": "action",
        "label": "Update task (Projects)",
        "description": (
            "Set fields on a task in the Projects app — title, description, "
            "importance, dates, or its status by lane name. An internal write: "
            "no approval node required."
        ),
    },
    {
        "type": "pm_lifecycle",
        "category": "action",
        "label": "Lifecycle sweep (Projects)",
        "description": (
            "Archive long-closed tasks and close stale ones, per each root "
            "project's lifecycle policy (archive/close months and timezone "
            "live on the project — projects with no policy are untouched). "
            "Config-free; pair it with a schedule trigger. An internal "
            "write: no approval node required."
        ),
    },
    {
        "type": "output",
        "category": "output",
        "label": "Output",
        "description": "Yield the run's result.",
    },
]

#: The `pm.*` topics a workflow can bind an event trigger to (WS-27f).
#: Served rather than typed from memory — an editor offering a topic the app
#: does not emit is a trigger that silently never fires (D7, the same rule the
#: agent and integration lists already follow).
PM_EVENT_TOPICS = [
    {"source": "projects", "event_type": "pm.task.created", "label": "Task created"},
    {"source": "projects", "event_type": "pm.task.updated", "label": "Task updated"},
    {
        "source": "projects",
        "event_type": "pm.task.status_changed",
        "label": "Task status changed",
    },
    {"source": "projects", "event_type": "pm.task.assigned", "label": "Task assigned"},
    {"source": "projects", "event_type": "pm.task.moved", "label": "Task moved"},
    {"source": "projects", "event_type": "pm.task.deleted", "label": "Task deleted"},
    {
        "source": "projects",
        "event_type": "pm.task.comment_added",
        "label": "Comment added to a task",
    },
    {"source": "projects", "event_type": "pm.project.created", "label": "Project created"},
    {"source": "projects", "event_type": "pm.project.updated", "label": "Project updated"},
    {"source": "projects", "event_type": "pm.project.moved", "label": "Project moved"},
    {"source": "projects", "event_type": "pm.project.deleted", "label": "Project deleted"},
]

CONDITION_OPS = [
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "gt",
    "gte",
    "lt",
    "lte",
    "is_empty",
    "not_empty",
    "truthy",
]


def known_agent_names() -> set[str]:
    """Every runnable agent name (static registry + dynamic), best-effort.

    Import is deferred and failure-tolerant: catalog and publish validation
    degrade to skipping the agent-existence check rather than breaking.
    """
    names: set[str] = set()
    try:
        from gateway.routes.agent import _AGENT_REGISTRY

        names.update(str(a.get("name")) for a in _AGENT_REGISTRY if a.get("name"))
    except Exception:
        pass
    try:
        from gateway.routes.agent import _load_dynamic_agents

        names.update(str(a.get("name")) for a in _load_dynamic_agents() if a.get("name"))
    except Exception:
        pass
    return names


def _agent_entries() -> list[dict[str, Any]]:
    """The agents that a workflow OFFERS: Ask Mira only (WFC-2).

    The entry needs the live registry, so the palette never offers an agent
    that the runtime cannot resolve. A specialist of ``_AGENT_REGISTRY`` and
    each dynamic agent stay out of the offer. Their names stay in
    ``known_agent_names()``, so an old graph keeps working. The text of a
    dynamic agent is member-written, so it never reaches the palette, the
    search or the Copilot prompt.
    """
    try:
        from gateway.routes.agent import _AGENT_REGISTRY

        registered = {str(a.get("name")) for a in _AGENT_REGISTRY if a.get("name")}
    except Exception as exc:
        _log.warning("workflows.catalog_agents_failed", error=str(exc)[:120])
        return []
    return [
        {
            "name": name,
            "label": ASK_MIRA_LABEL,
            "description": ASK_MIRA_DESCRIPTION,
            "tags": [],
            "integrations": [],
        }
        for name in sorted(OFFERED_AGENTS & registered)
    ]


def _offered_integrations() -> list[dict[str, Any]]:
    """The integration rows that the palette and the search show (owner Q7).

    A row with no registered action has nothing to put on the canvas, so the
    offer hides it. The data seam stays whole: ``_integration_entries`` keeps
    every registered service, and a row comes back when an action names it.
    """
    return [row for row in _integration_entries() if row["actions"]]


def _integration_entries() -> list[dict[str, Any]]:
    """All registered integrations + whether credentials resolve right now."""
    try:
        from acb_common import get_settings
        from acb_skills.integrations import build_integrations, list_registered

        services = list_registered()
        resolved, unavailable = build_integrations([], services, get_settings())
    except Exception as exc:
        _log.warning("workflows.catalog_integrations_failed", error=str(exc)[:120])
        return []
    tools_by_integration: dict[str, list[dict[str, Any]]] = {}
    for spec in list_tools():
        if spec.integration:
            tools_by_integration.setdefault(spec.integration, []).append(_tool_entry(spec))
    return [
        {
            "service": service,
            "available": service in resolved,
            "unavailable_reason": unavailable.get(service),
            "actions": tools_by_integration.get(service, []),
        }
        for service in sorted(services)
    ]


def _tool_entry(spec: Any) -> dict[str, Any]:
    from gateway.routes.workflows.engine.tool_args import parse_args_schema

    return {
        "action": spec.action,
        "label": spec.label,
        "description": spec.description,
        "integration": spec.integration,
        # Raw declaration (kept for anything reading the contract verbatim)
        # plus the parsed form the editor renders fields from — the browser
        # must not re-implement the mini-language, or the two drift.
        "args_schema": spec.args_schema,
        "args": [a.as_dict() for a in parse_args_schema(spec.args_schema)],
        "read_only": spec.read_only,
        "destructive": spec.destructive,
        "open_world": spec.open_world,
    }


@router.get("/catalog")
async def get_catalog(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        modules = (
            await db.execute(
                text(
                    "SELECT id, name, description, input_schema, output_schema, "
                    "status, updated_at FROM workflow_modules "
                    "WHERE status != 'disabled' ORDER BY name"
                ),
            )
        ).fetchall()
    from gateway.routes.workflows.core import parse_jsonb

    return {
        "node_types": NODE_TYPE_META,
        "condition_ops": CONDITION_OPS,
        "event_topics": PM_EVENT_TOPICS,
        "agents": _agent_entries(),
        "integrations": _offered_integrations(),
        "tools": [_tool_entry(s) for s in list_tools()],
        "modules": [
            {
                "id": str(m.id),
                "name": m.name,
                "description": m.description or "",
                "input_schema": parse_jsonb(m.input_schema, {}),
                "output_schema": parse_jsonb(m.output_schema, {}),
                "status": m.status,
                "updated_at": iso(m.updated_at),
            }
            for m in modules
        ],
    }
