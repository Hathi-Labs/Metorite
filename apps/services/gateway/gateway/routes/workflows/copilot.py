"""The Workflow Copilot — chat-to-build inside the editor (spec F12→F14).

``POST /workflows/{id}/copilot`` takes a maker's instruction ("when a lead
webhook fires, classify it, clean the contacts, and make a ClickUp task"),
shortlists relevant capabilities through the SAME semantic search the palette
uses, and asks the LLM to emit a full edit-model graph plus any **new modules
the graph needs that don't exist yet**. New modules are validated (the Module
Studio AST rules), persisted to the library with ``auto_created`` provenance,
and their nodes are rewired from ``module_name`` to real ``module_id``s. The
resulting graph is validated (one named-issue repair round) and returned to
the client, which applies it to the canvas — the maker reviews, tests, and
saves; the copilot never writes the workflow row itself.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.workflows.core import (
    _log,
    _tenant_session,
    _uid,
    load_workflow_or_404,
    parse_jsonb,
    router,
)
from gateway.routes.workflows.engine.graph import validate_graph
from gateway.routes.workflows.engine.modules import validate_module_code
from gateway.routes.workflows.modules import _extract_json
from gateway.routes.workflows.search import search_catalog
from pydantic import BaseModel, Field
from sqlalchemy import text

COPILOT_TIER = "tier-balanced"
COPILOT_FALLBACK_TIER = "tier-fast"
COPILOT_MAX_TOKENS = 6000
SHORTLIST_LIMIT = 12

COPILOT_SYSTEM_PROMPT = """\
You are the Workflow Copilot for Metorite's visual workflow editor. The
maker chats; you edit their automation graph. You receive the current graph,
a shortlist of relevant capabilities (found by semantic search over the org's
agents, integration actions, and code modules), and the full name lists.

Respond with ONLY a JSON object (no prose outside it, no markdown fence):
{
  "reply": "1-3 sentences to the maker: what you changed and why",
  "graph": { "nodes": [...], "edges": [...] },          // FULL updated graph, or null if no change
  "new_modules": [                                       // only when a needed transform has no existing module
    { "name": "snake_case", "description": "one sentence",
      "code": "def run(inputs):\\n    ...",
      "input_schema": {...}, "output_schema": {...} }
  ]
}

Graph rules (a validator enforces them — violations bounce back to you):
- Node: {"id", "type", "position": {"x","y"}, "data": {"label", "config"}}.
  Types: trigger | agent | tool | module | condition | set | approval | wait |
  output.
- Exactly one trigger node; no cycles; a node has at most ONE incoming edge.
- Edges: {"id", "source", "target"}; edges out of a condition MUST carry
  "sourceHandle": "true" or "false".
- config by type — agent: {"agent": <registered name>, "message": "..."};
  tool: {"action": <registered action>, "args": {...}};
  module: {"module_id": <existing id>} or {"module_name": <name>} for a module
  you define in new_modules; condition: {"left","op","right"} with ops
  equals|not_equals|contains|not_contains|gt|gte|lt|lte|is_empty|not_empty|truthy;
  set: {"assignments": {...}}; approval: {"message": "what the human is
  approving"} — the run pauses there until an operator approves in the
  approvals inbox; wait: {"seconds": <positive number>} — pause before
  continuing (use it for "wait a day then follow up"; max 30 days);
  output: {"value": ...}.
- Reference upstream data with {{trigger.field}}, {{vars.name}}, {{node_id.field}}.
  An agent node's output is {{<id>.result}}; a condition's is {{<id>.branch}}.
- Only use agent names and tool actions from the provided lists — never invent.
- Any tool action marked "write": true MUST have an approval node somewhere
  upstream of it (the validator rejects the graph otherwise).
- Never put credentials or secrets in any config.
- Lay out top-to-bottom: x around 360 for the spine, y += ~120 per node;
  condition branches diverge left/right (x -220 / +220). Keep existing node
  positions when you keep the node.

New-module rules (a mechanical validator rejects violations):
- def run(inputs): one dict in, dict out. NO imports, classes, async, yield,
  underscore attributes, dunders, or: eval exec compile open input __import__
  globals locals vars getattr setattr delattr hasattr dir type object super.
- Pure computation only. Prefer an EXISTING module from the shortlist over
  creating a near-duplicate.

If the request is unclear or needs a capability that does not exist and is not
a pure transform, say so in "reply" and return "graph": null.
"""


class CopilotRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


async def _call_copilot(messages: list[dict[str, str]]) -> dict[str, Any]:
    """One LLM round → parsed JSON. Module-level seam so tests can stub it."""
    try:
        from acb_llm import acompletion_with_fallback

        resp, used_model = await acompletion_with_fallback(
            model=COPILOT_TIER,
            fallback_model=COPILOT_FALLBACK_TIER,
            messages=messages,
            max_tokens=COPILOT_MAX_TOKENS,
            temperature=0.2,
            source="workflows",
        )
    except Exception as exc:
        _log.warning("workflows.copilot_failed", error=str(exc)[:200])
        raise HTTPException(status_code=502, detail="Copilot is unavailable") from exc
    try:
        content = resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        content = ""
    parsed = _extract_json(content)
    if not isinstance(parsed, dict) or "reply" not in parsed:
        raise HTTPException(
            status_code=502,
            detail="The copilot returned an unparseable result — try rephrasing",
        )
    parsed["_model"] = used_model
    return parsed


def resolve_module_nodes(
    graph: dict[str, Any],
    name_to_id: dict[str, str],
) -> list[str]:
    """Rewrite module nodes referencing ``module_name`` to real ids, in place.

    Returns the names that could not be resolved.
    """
    unresolved: list[str] = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "module":
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        name = str(config.get("module_name") or "").strip()
        if not name:
            continue
        module_id = name_to_id.get(name)
        if module_id:
            config["module_id"] = module_id
            config.pop("module_name", None)
        else:
            unresolved.append(name)
    return unresolved


async def _save_module(
    db: Any,
    module: dict[str, Any],
    *,
    user_email: str,
    prompt: str,
    model: str,
) -> str | None:
    """Persist one copilot-generated module as ``ready``; None on name clash
    with different code (the existing module wins — never overwrite)."""
    name = str(module.get("name") or "").strip()[:80]
    existing = (
        await db.execute(
            text("SELECT id, code FROM workflow_modules WHERE name = :name"),
            {"name": name},
        )
    ).fetchone()
    if existing is not None:
        return str(existing.id)  # reuse — the copilot referenced it by name
    row = (
        await db.execute(
            text(
                """INSERT INTO workflow_modules
                   (name, description, code, input_schema, output_schema,
                    provenance, status, created_by)
                   VALUES (:name, :description, :code, :input_schema ::jsonb,
                           :output_schema ::jsonb, :provenance ::jsonb,
                           'ready', :by)
                   RETURNING id"""
            ),
            {
                "name": name,
                "description": str(module.get("description") or "")[:2000],
                "code": str(module.get("code") or ""),
                "input_schema": json.dumps(module.get("input_schema") or {}, default=str),
                "output_schema": json.dumps(module.get("output_schema") or {}, default=str),
                "provenance": json.dumps(
                    {
                        "generated_by": "workflow-copilot",
                        "auto_created": True,
                        "prompt": prompt[:2000],
                        "model": model,
                        "requested_by": user_email,
                    },
                    default=str,
                ),
                "by": user_email,
            },
        )
    ).fetchone()
    return str(row.id) if row is not None else None


def _module_problems(new_modules: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    seen_names: set[str] = set()
    for module in new_modules:
        name = str(module.get("name") or "").strip()
        if not name:
            problems.append("a new_modules entry has no name")
            continue
        if name in seen_names:
            problems.append(f"duplicate new module name '{name}'")
        seen_names.add(name)
        violations = validate_module_code(str(module.get("code") or ""))
        problems.extend(f"module '{name}' line {v.line}: {v.message}" for v in violations)
    return problems


async def _capability_context(message: str, db: Any) -> dict[str, Any]:
    """The copilot's working set: search shortlist + full name lists."""
    shortlist = (await search_catalog(message, limit=SHORTLIST_LIMIT))["results"]
    from gateway.routes.workflows.catalog import _agent_entries, known_agent_names
    from gateway.routes.workflows.tools import list_tools

    agents = {a["name"]: a.get("description", "") for a in _agent_entries()}
    tools = {
        s.action: {"description": s.description, "args": s.args_schema, "write": s.destructive}
        for s in list_tools()
    }
    modules = (
        await db.execute(
            text(
                "SELECT id, name, description, input_schema, output_schema "
                "FROM workflow_modules WHERE status = 'ready'"
            ),
        )
    ).fetchall()
    return {
        "shortlist": shortlist,
        "agent_names": sorted(known_agent_names() | set(agents)),
        "agents_brief": {k: v[:120] for k, v in list(agents.items())[:25]},
        "tools": tools,
        "modules": [
            {
                "id": str(m.id),
                "name": m.name,
                "description": m.description or "",
                "input_schema": parse_jsonb(m.input_schema, {}),
                "output_schema": parse_jsonb(m.output_schema, {}),
            }
            for m in modules
        ],
    }


@router.post("/{workflow_id}/copilot")
async def workflow_copilot(
    workflow_id: str,
    body: CopilotRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        graph = parse_jsonb(row.graph, {"nodes": [], "edges": []})
        context = await _capability_context(body.message, db)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": COPILOT_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "CURRENT GRAPH:\n"
                + json.dumps(graph, default=str)
                + "\n\nCAPABILITIES (semantic shortlist for this request):\n"
                + json.dumps(context["shortlist"], default=str)
                + "\n\nAll agent names: "
                + ", ".join(context["agent_names"])
                + "\nAgent summaries: "
                + json.dumps(context["agents_brief"], default=str)
                + "\nAll tool actions: "
                + json.dumps(context["tools"], default=str)
                + "\nExisting ready modules: "
                + json.dumps(context["modules"], default=str)
            ),
        },
    ]
    for turn in body.history:
        role = str(turn.get("role") or "")
        content = str(turn.get("content") or "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:6000]})
    messages.append({"role": "user", "content": body.message})

    result = await _call_copilot(messages)
    result, created = await _apply_round(
        result,
        workflow_id,
        body.message,
        _uid(user),
    )

    # One named-issue repair round when the emitted graph doesn't validate.
    if result.get("_problems"):
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    {k: v for k, v in result.items() if not k.startswith("_")},
                    default=str,
                ),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    "That response has problems the validator rejected:\n- "
                    + "\n- ".join(result["_problems"][:12])
                    + "\nEmit the corrected JSON object (full graph, same contract)."
                ),
            }
        )
        result = await _call_copilot(messages)
        result, created_2 = await _apply_round(
            result,
            workflow_id,
            body.message,
            _uid(user),
        )
        created = {**created, **created_2}

    return {
        "reply": str(result.get("reply") or ""),
        "graph": result.get("graph") if isinstance(result.get("graph"), dict) else None,
        "created_modules": [{"id": mid, "name": name} for name, mid in sorted(created.items())],
        "issues": result.get("_issues", []),
        "problems": result.get("_problems", []),
        "shortlist": context["shortlist"],
    }


async def _apply_round(
    result: dict[str, Any],
    workflow_id: str,
    prompt: str,
    user_email: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate + persist one copilot response round.

    Adds ``_problems`` (repairable — sent back to the model) and ``_issues``
    (residual validator findings, shown to the maker) onto *result*; returns
    the name→id map of modules created this round.
    """
    problems: list[str] = []
    created: dict[str, str] = {}
    new_modules = result.get("new_modules")
    new_modules = new_modules if isinstance(new_modules, list) else []
    problems.extend(_module_problems(new_modules))

    graph = result.get("graph")
    if graph is not None and not isinstance(graph, dict):
        problems.append("graph must be an object with nodes[] and edges[]")
        graph = None

    if not problems and (new_modules or graph is not None):
        async with _tenant_session() as db:
            for module in new_modules:
                module_id = await _save_module(
                    db,
                    module,
                    user_email=user_email,
                    prompt=prompt,
                    model=str(result.get("_model") or ""),
                )
                if module_id:
                    created[str(module.get("name"))] = module_id
            existing = (
                await db.execute(
                    text("SELECT id, name FROM workflow_modules WHERE status = 'ready'"),
                )
            ).fetchall()
        name_to_id = {r.name: str(r.id) for r in existing} | created

        if graph is not None:
            unresolved = resolve_module_nodes(graph, name_to_id)
            problems.extend(
                f"module node references unknown module '{n}' — define it in new_modules"
                for n in unresolved
            )
            from gateway.routes.workflows.catalog import known_agent_names
            from gateway.routes.workflows.tools import (
                destructive_action_names,
                tool_arg_schemas,
            )

            issues = validate_graph(
                graph,
                known_modules=set(name_to_id.values()),
                known_agents=known_agent_names() or None,
                destructive_actions=destructive_action_names(),
                tool_schemas=tool_arg_schemas(),
            )
            problems.extend(
                f"{i.code}: {i.message}" + (f" (node {i.node_id})" if i.node_id else "")
                for i in issues
            )
            result["_issues"] = [i.as_dict() for i in issues]

    result["_problems"] = problems
    result["graph"] = graph
    return result, created
