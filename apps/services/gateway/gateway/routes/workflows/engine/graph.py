"""Edit-model validation + compile to the flat run-model DAG (RFC §4, §6).

The edit-model is React-Flow-native JSON persisted verbatim:

    {"nodes": [{id, type, position, data: {label, config, outputs?}}, …],
     "edges": [{id, source, target, sourceHandle?, label?}, …]}

Publish compiles it to the serialized run-model executed by ``runner``:

    {"version": 1, "entry": "<trigger node id>",
     "blocks": [{id, type, label, config}, …],
     "connections": [{source, target, handle}, …]}

Validation is design-time and exhaustive — the run time assumes a valid DAG.
v1 shape limits (spec §8 Slice 1): exactly one trigger, no cycles, no fan-in
(a node has at most one incoming edge; condition branches diverge and end
separately). Fan-out from a node is allowed (parallel branches).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from gateway.routes.workflows.engine.handlers import MAX_WAIT_SECONDS
from gateway.routes.workflows.engine.templating import (
    RESERVED_ROOTS,
    collect_refs,
)
from gateway.routes.workflows.engine.tool_args import check_args

SERIALIZED_VERSION = 1

NODE_TYPES = frozenset(
    {
        "trigger",
        "agent",
        "tool",
        "module",
        "condition",
        "set",
        "approval",
        "wait",
        "output",
        # WS-27f / §13 U1 — the first node type that acts on an INTERNAL app.
        # Not a `tool`: tools reach external systems through the Integration
        # Registry and carry the write-class approval gate, which a task moving
        # to Done must not need.
        "pm_task",
        # WS-27z — the Projects lifecycle sweep. Internal like `pm_task`, and
        # deliberately CONFIG-FREE: the whole policy (windows, timezone,
        # which projects) lives in `pm_projects` columns, so there is nothing
        # here for `_validate_node_config` to require.
        "pm_lifecycle",
    }
)

#: Node types whose outgoing edges carry branch handles.
BRANCHING_TYPES = frozenset({"condition"})

#: What a ``pm_task`` node may set.
#:
#: **Deliberately a copy** of `routes/projects/automation.PATCHABLE_FIELDS`
#: (plus `status`), not an import: this engine is transport-free and must not
#: acquire an import edge into an app package to validate a graph. The house
#: answer to a copied vocabulary is a both-ways fence, and there is one —
#: `test_projects_automation.py` fails if either side grows a field the other
#: lacks, in either direction.
PM_TASK_FIELDS = frozenset(
    {
        "title", "description", "importance", "leveraged", "due_at",
        "start_date", "estimate_mins", "status",
    }
)

#: Very conservative "this looks like a secret" patterns — publish refuses a
#: graph containing one anywhere in node config (platform contract rung 3:
#: secrets live in the Integration Registry, never in workflow JSON).
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


@dataclass(slots=True)
class GraphIssue:
    """One design-time validation finding, addressable to a node/edge."""

    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.node_id:
            out["node_id"] = self.node_id
        if self.edge_id:
            out["edge_id"] = self.edge_id
        return out


class GraphValidationError(Exception):
    """Raised by ``compile_graph`` when the graph has blocking issues."""

    def __init__(self, issues: list[GraphIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.code}: {i.message}" for i in issues[:5]))


@dataclass(slots=True)
class _Parsed:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)


def _parse(graph: dict[str, Any]) -> tuple[_Parsed, list[GraphIssue]]:
    issues: list[GraphIssue] = []
    parsed = _Parsed()
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        issues.append(GraphIssue("malformed", "graph must have nodes[] and edges[]"))
        return parsed, issues
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id"):
            issues.append(GraphIssue("malformed_node", "node without an id"))
            continue
        node_id = str(node["id"])
        if node_id in parsed.nodes:
            issues.append(GraphIssue("duplicate_node", f"duplicate node id '{node_id}'", node_id))
            continue
        if node_id in RESERVED_ROOTS:
            issues.append(
                GraphIssue(
                    "reserved_id",
                    f"'{node_id}' is a reserved name",
                    node_id,
                )
            )
            continue
        parsed.nodes[node_id] = node
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        parsed.edges.append(edge)
    return parsed, issues


def _node_type(node: dict[str, Any]) -> str:
    return str(node.get("type") or "")


def _node_config(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get("data")
    if isinstance(data, dict) and isinstance(data.get("config"), dict):
        return data["config"]
    return {}


def _upstream_map(
    parsed: _Parsed,
) -> dict[str, set[str]]:
    """node id → the set of node ids reachable walking edges backwards."""
    parents: dict[str, set[str]] = {nid: set() for nid in parsed.nodes}
    for edge in parsed.edges:
        src, tgt = str(edge.get("source")), str(edge.get("target"))
        if src in parsed.nodes and tgt in parsed.nodes:
            parents[tgt].add(src)
    upstream: dict[str, set[str]] = {}

    def walk(nid: str, seen: set[str]) -> set[str]:
        if nid in upstream:
            return upstream[nid]
        if nid in seen:  # cycle — reported separately
            return set()
        seen.add(nid)
        acc: set[str] = set()
        for parent in parents.get(nid, ()):
            acc.add(parent)
            acc |= walk(parent, seen)
        upstream[nid] = acc
        return acc

    for nid in parsed.nodes:
        walk(nid, set())
    return upstream


def validate_graph(
    graph: dict[str, Any],
    *,
    known_modules: set[str] | None = None,
    known_agents: set[str] | None = None,
    destructive_actions: set[str] | None = None,
    tool_schemas: dict[str, dict[str, str]] | None = None,
) -> list[GraphIssue]:
    """Design-time validation. Returns ALL issues (empty list = publishable).

    ``known_modules`` / ``known_agents``, when provided, let publish verify
    referenced capabilities actually exist; ``destructive_actions`` names the
    write-class tool actions that REQUIRE a Human-approval node upstream
    (spec success criterion #5); ``tool_schemas`` maps each action to its
    declared arguments so a tool node missing a required one is caught here
    rather than at 3am inside a published run. The pure structural checks
    never need any of them (the editor calls this without context for live
    badges).
    """
    parsed, issues = _parse(graph)
    if issues:
        return issues

    triggers = [n for n, node in parsed.nodes.items() if _node_type(node) == "trigger"]
    if not triggers:
        issues.append(GraphIssue("no_trigger", "the graph needs a trigger node"))
    for nid in triggers[1:]:
        issues.append(GraphIssue("multiple_triggers", "only one trigger per workflow", nid))

    edge_keys = _validate_edges(parsed, issues)
    _validate_topology(parsed, edge_keys, triggers, issues)

    upstream = _upstream_map(parsed)
    for nid, node in parsed.nodes.items():
        _validate_node(
            nid,
            node,
            upstream.get(nid, set()),
            issues,
            known_modules=known_modules,
            known_agents=known_agents,
        )
        if tool_schemas is not None and _node_type(node) == "tool":
            _validate_tool_args(nid, node, tool_schemas, issues)
        # Write-class actions need a human gate upstream (platform contract
        # rung 3 — the runtime broker gates them too; this makes the intent
        # explicit in the graph before anything publishes).
        if destructive_actions and _node_type(node) == "tool":
            action = str(_node_config(node).get("action") or "").strip()
            if action in destructive_actions and not any(
                _node_type(parsed.nodes[u]) == "approval"
                for u in upstream.get(nid, set())
                if u in parsed.nodes
            ):
                issues.append(
                    GraphIssue(
                        "write_without_approval",
                        f"'{action}' writes to an external system — add a "
                        "Human approval node upstream of it",
                        nid,
                    )
                )
    return issues


def _validate_tool_args(
    nid: str,
    node: dict[str, Any],
    tool_schemas: dict[str, dict[str, str]],
    issues: list[GraphIssue],
) -> None:
    """A tool node must supply the arguments its action declares.

    An unknown action is left alone: ``_validate_node`` already reports the
    missing/unknown action, and stacking "and also every argument is wrong"
    on top of that buries the one issue the maker can act on.
    """
    config = _node_config(node)
    action = str(config.get("action") or "").strip()
    schema = tool_schemas.get(action)
    if not action or schema is None:
        return
    for problem in check_args(schema, config.get("args"), action=action):
        issues.append(GraphIssue("tool_args", problem, nid))


def _validate_edges(
    parsed: _Parsed,
    issues: list[GraphIssue],
) -> set[tuple[str, str, str]]:
    """Edge sanity: endpoints exist, no duplicates, branch handles present."""
    edge_keys: set[tuple[str, str, str]] = set()
    for edge in parsed.edges:
        src, tgt = str(edge.get("source")), str(edge.get("target"))
        handle = str(edge.get("sourceHandle") or "")
        edge_id = str(edge.get("id") or f"{src}->{tgt}")
        if src not in parsed.nodes or tgt not in parsed.nodes:
            issues.append(
                GraphIssue("dangling_edge", "edge references a missing node", edge_id=edge_id)
            )
            continue
        if (src, tgt, handle) in edge_keys:
            issues.append(GraphIssue("duplicate_edge", "duplicate edge", edge_id=edge_id))
            continue
        edge_keys.add((src, tgt, handle))
        src_type = _node_type(parsed.nodes[src])
        if src_type in BRANCHING_TYPES and handle not in ("true", "false"):
            issues.append(
                GraphIssue(
                    "missing_branch_handle",
                    "edges out of a condition must be the true or false handle",
                    edge_id=edge_id,
                )
            )
        if _node_type(parsed.nodes[tgt]) == "trigger":
            issues.append(
                GraphIssue("edge_into_trigger", "nothing can flow into a trigger", edge_id=edge_id)
            )
    return edge_keys


def _validate_topology(
    parsed: _Parsed,
    edge_keys: set[tuple[str, str, str]],
    triggers: list[str],
    issues: list[GraphIssue],
) -> None:
    """Shape limits (v1): no fan-in, no cycles, everything trigger-reachable."""
    incoming: dict[str, int] = dict.fromkeys(parsed.nodes, 0)
    adjacency: dict[str, list[str]] = {nid: [] for nid in parsed.nodes}
    for src, tgt, _handle in edge_keys:
        adjacency[src].append(tgt)
        incoming[tgt] += 1

    for nid, count in incoming.items():
        if count > 1:
            issues.append(
                GraphIssue(
                    "fan_in",
                    "a node may have only one incoming edge (v1)",
                    nid,
                )
            )

    white, gray, black = 0, 1, 2
    color = dict.fromkeys(parsed.nodes, white)

    def dfs(nid: str) -> bool:
        color[nid] = gray
        for nxt in adjacency[nid]:
            if color[nxt] == gray:
                return True
            if color[nxt] == white and dfs(nxt):
                return True
        color[nid] = black
        return False

    if any(dfs(nid) for nid in parsed.nodes if color[nid] == white):
        issues.append(
            GraphIssue("cycle", "the graph contains a cycle (loops arrive in a later slice)")
        )

    if len(triggers) == 1:
        reachable = {triggers[0]}
        stack = [triggers[0]]
        while stack:
            for nxt in adjacency[stack.pop()]:
                if nxt not in reachable:
                    reachable.add(nxt)
                    stack.append(nxt)
        for nid in parsed.nodes:
            if nid not in reachable:
                issues.append(
                    GraphIssue("unreachable", "node is not connected to the trigger", nid)
                )


def _validate_node(
    nid: str,
    node: dict[str, Any],
    upstream: set[str],
    issues: list[GraphIssue],
    *,
    known_modules: set[str] | None,
    known_agents: set[str] | None,
) -> None:
    """Per-node checks: type, {{ref}} roots, secrets, required config."""
    ntype = _node_type(node)
    config = _node_config(node)
    if ntype not in NODE_TYPES:
        issues.append(GraphIssue("unknown_type", f"unknown node type '{ntype}'", nid))
        return
    # {{ref}} roots must be a reserved root or an upstream node id.
    legal_roots = set(RESERVED_ROOTS) | upstream
    for ref in sorted(collect_refs(config)):
        root = ref.split(".", 1)[0]
        if root not in legal_roots:
            issues.append(
                GraphIssue(
                    "unresolved_ref",
                    f"'{{{{{ref}}}}}' does not resolve to the trigger, vars, or an upstream node",
                    nid,
                )
            )
    # Secret-shaped strings never publish.
    for text_val in _flatten_strings(config):
        if any(p.search(text_val) for p in SECRET_PATTERNS):
            issues.append(
                GraphIssue(
                    "secret_in_config",
                    "node config contains a credential-shaped string — use the Integration Registry",
                    nid,
                )
            )
            break
    _validate_node_config(
        nid,
        ntype,
        config,
        issues,
        known_modules=known_modules,
        known_agents=known_agents,
    )


def _validate_node_config(
    nid: str,
    ntype: str,
    config: dict[str, Any],
    issues: list[GraphIssue],
    *,
    known_modules: set[str] | None,
    known_agents: set[str] | None,
) -> None:
    """Per-type required-config checks."""
    if ntype == "agent":
        agent = str(config.get("agent") or "").strip()
        if not agent:
            issues.append(GraphIssue("missing_config", "agent node needs an agent name", nid))
        elif known_agents is not None and agent not in known_agents:
            issues.append(GraphIssue("unknown_agent", f"agent '{agent}' is not registered", nid))
    elif ntype == "tool":
        if not str(config.get("action") or "").strip():
            issues.append(GraphIssue("missing_config", "tool node needs an action", nid))
    elif ntype == "module":
        module_id = str(config.get("module_id") or "").strip()
        if not module_id:
            issues.append(GraphIssue("missing_config", "module node needs a module", nid))
        elif known_modules is not None and module_id not in known_modules:
            issues.append(
                GraphIssue("unknown_module", "module does not exist or is not ready", nid)
            )
    elif ntype == "condition":
        if not str(config.get("op") or "").strip():
            issues.append(GraphIssue("missing_config", "condition node needs an operator", nid))
    elif ntype == "set" and not isinstance(config.get("assignments"), dict):
        issues.append(GraphIssue("missing_config", "set node needs assignments", nid))
    elif ntype == "wait":
        _validate_wait_config(nid, config, issues)
    elif ntype == "pm_task":
        _validate_pm_task_config(nid, config, issues)


def _validate_pm_task_config(
    nid: str, config: dict[str, Any], issues: list[GraphIssue]
) -> None:
    """A task node needs a task to act on and something to do to it.

    Both checks land at PUBLISH rather than at run time, which is the whole
    point of the gate: an automation whose target is missing should fail while
    the maker is looking at the canvas, not silently at 3am against a task that
    turns out not to exist.

    Field NAMES are checked here; field values are not, because a value may be
    a ``{{ref}}`` that only resolves during a run.
    """
    if not str(config.get("task_id") or "").strip():
        issues.append(
            GraphIssue("missing_config", "task node needs a task id", nid)
        )
    fields = config.get("fields")
    if not isinstance(fields, dict) or not fields:
        issues.append(
            GraphIssue(
                "missing_config",
                "task node needs at least one field to set",
                nid,
            )
        )
        return
    unknown = sorted(k for k in fields if k not in PM_TASK_FIELDS)
    if unknown:
        issues.append(
            GraphIssue(
                "unknown_field",
                f"task node cannot set {unknown} — one of {sorted(PM_TASK_FIELDS)}",
                nid,
            )
        )


def _validate_wait_config(nid: str, config: dict[str, Any], issues: list[GraphIssue]) -> None:
    """A wait needs a positive duration. ``{{refs}}`` are resolved at RUN
    time, so a templated duration can only be checked for presence here."""
    raw = config.get("seconds")
    if isinstance(raw, str) and "{{" in raw:
        return
    try:
        seconds = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        issues.append(GraphIssue("missing_config", "wait node needs a duration", nid))
        return
    if seconds <= 0:
        issues.append(GraphIssue("missing_config", "wait duration must be positive", nid))
    elif seconds > MAX_WAIT_SECONDS:
        issues.append(
            GraphIssue(
                "missing_config",
                f"wait duration exceeds the {MAX_WAIT_SECONDS // 86400}-day maximum",
                nid,
            )
        )


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _flatten_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _flatten_strings(v)]
    return []


def compile_graph(
    graph: dict[str, Any],
    *,
    known_modules: set[str] | None = None,
    known_agents: set[str] | None = None,
    destructive_actions: set[str] | None = None,
    tool_schemas: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Compile the edit-model to the serialized run-model, or raise.

    Raises :class:`GraphValidationError` carrying every issue when the graph
    is not publishable.
    """
    issues = validate_graph(
        graph,
        known_modules=known_modules,
        known_agents=known_agents,
        destructive_actions=destructive_actions,
        tool_schemas=tool_schemas,
    )
    if issues:
        raise GraphValidationError(issues)
    parsed, _ = _parse(graph)
    entry = next(nid for nid, node in parsed.nodes.items() if _node_type(node) == "trigger")
    blocks = []
    for nid, node in parsed.nodes.items():
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        blocks.append(
            {
                "id": nid,
                "type": _node_type(node),
                "label": str(data.get("label") or nid),
                "config": _node_config(node),
            }
        )
    connections = [
        {
            "source": str(e.get("source")),
            "target": str(e.get("target")),
            "handle": str(e.get("sourceHandle") or "") or None,
        }
        for e in parsed.edges
    ]
    return {
        "version": SERIALIZED_VERSION,
        "entry": entry,
        "blocks": blocks,
        "connections": connections,
    }
