"""The workflow tool registry — typed integration actions as nodes (spec F3).

Every tool node dispatches through this registry; there is no other path to an
external system from a workflow (platform contract §3.2). Each spec carries
the standing risk annotations (read_only/destructive/idempotent/open_world —
root AGENTS.md harness rule #1); **write-class actions route through the
Action Broker**, so disposition/approval semantics are the broker's policy,
not the engine's (fail closed: no handler → refused).

The catalog is served from here + the integrations registry, so the palette
can only ever offer what the platform actually has (spec D7): integrations
without a registered action appear in the catalog as available-but-actionless.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger
from gateway.routes.workflows.engine.handlers import NodeExecutionError
from gateway.routes.workflows.engine.tool_args import check_args, parse_args_schema

_log = get_logger("gateway.workflows.tools")

HTTP_TIMEOUT_SECS = 30.0
HTTP_MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class WorkflowToolSpec:
    action: str  # e.g. "zoho.create_lead"
    label: str
    description: str
    integration: str | None  # Integration Registry service, if any
    read_only: bool
    destructive: bool
    open_world: bool
    args_schema: dict[str, str] = field(default_factory=dict)
    handler: Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]] | None = None


_TOOL_REGISTRY: dict[str, WorkflowToolSpec] = {}


def register_tool(spec: WorkflowToolSpec) -> None:
    _TOOL_REGISTRY[spec.action] = spec


def destructive_action_names() -> set[str]:
    """Write-class actions — publish requires an approval node upstream of
    any node using one (spec success criterion #5)."""
    return {spec.action for spec in _TOOL_REGISTRY.values() if spec.destructive}


def list_tools() -> list[WorkflowToolSpec]:
    return list(_TOOL_REGISTRY.values())


def tool_arg_schemas() -> dict[str, dict[str, str]]:
    """``{action: args_schema}`` for the graph validator.

    Passed as data rather than imported, because ``engine.graph`` must not
    depend on this module — the engine is transport-free and this registry sits
    above it.
    """
    return {spec.action: dict(spec.args_schema) for spec in _TOOL_REGISTRY.values()}


def tool_arg_specs(action: str) -> list[dict[str, Any]]:
    """Parsed arguments for one action — what the editor renders fields from."""
    spec = _TOOL_REGISTRY.get(action)
    return [a.as_dict() for a in parse_args_schema(spec.args_schema if spec else None)]


async def execute_tool(
    action: str,
    args: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Run one tool action. Unknown action → refused (fail closed)."""
    spec = _TOOL_REGISTRY.get(action)
    if spec is None or spec.handler is None:
        raise NodeExecutionError(f"tool '{action}' has no registered handler — it cannot run")
    # Publish already checked this, but a run can reach here from a draft Test,
    # a copilot-authored graph, or a version published before the argument was
    # added. Refusing here beats handing a malformed payload to an integration
    # — or to the Action Broker, which would file a proposal for a write that
    # cannot succeed.
    problems = check_args(spec.args_schema, args, action=action)
    if problems:
        raise NodeExecutionError("; ".join(problems))
    return await spec.handler(args, actor)


# ── http.request — the generic escape valve, guarded ─────────────────────────


def _assert_public_host(host: str) -> None:
    """SSRF guard: refuse loopback/private/link-local/metadata targets."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise NodeExecutionError(f"cannot resolve host '{host}'") from None
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise NodeExecutionError(f"host '{host}' resolves to a non-public address")


async def _http_request(args: dict[str, Any], actor: str) -> dict[str, Any]:
    import httpx

    url = str(args.get("url") or "").strip()
    method = str(args.get("method") or "GET").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        raise NodeExecutionError(f"unsupported HTTP method '{method}'")
    if not url.startswith(("http://", "https://")):
        raise NodeExecutionError("url must be http(s)")
    parsed = httpx.URL(url)
    _assert_public_host(parsed.host)
    headers = args.get("headers") if isinstance(args.get("headers"), dict) else {}
    headers = {str(k): str(v) for k, v in headers.items()}
    headers.setdefault("User-Agent", "Metorite-Workflows/1.0")
    body = args.get("body")
    kwargs: dict[str, Any] = {}
    if body is not None and method not in ("GET", "HEAD"):
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["content"] = str(body)
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECS,
        follow_redirects=False,
    ) as client:
        try:
            resp = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise NodeExecutionError(f"HTTP request failed: {exc}") from exc
    raw = resp.content[:HTTP_MAX_RESPONSE_BYTES]
    try:
        payload: Any = resp.json() if raw else None
    except ValueError:
        payload = raw.decode(errors="replace")
    return {
        "status": resp.status_code,
        "ok": 200 <= resp.status_code < 300,
        "body": payload,
        "content_type": resp.headers.get("content-type", ""),
    }


# ── Broker-gated writes ──────────────────────────────────────────────────────


def _broker_write(
    action: str,
    *,
    target_field: str,
    destructive: bool = True,
) -> Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]:
    """A write-class tool = an Action Broker proposal (constraint #4).

    The broker computes the disposition (SUGGEST_APPLY + destructive → holds
    for approval) and only its registered handler ever performs the provider
    write. The node's output says exactly what happened.
    """

    async def _run(args: dict[str, Any], actor: str) -> dict[str, Any]:
        try:
            from action_broker.broker import AuthorityTier, propose, submit
        except Exception as exc:  # pragma: no cover - broker is a gateway dep
            raise NodeExecutionError("action broker unavailable") from exc
        target = str(args.get(target_field) or args.get("target") or action)
        proposal = propose(
            actor=actor,
            action=action,
            target=target,
            payload=dict(args),
            authority=AuthorityTier.SUGGEST_APPLY,
            destructive=destructive,
        )
        result = await submit(proposal)
        return {
            "disposition": (proposal.disposition.value if proposal.disposition else "unknown"),
            **(result if isinstance(result, dict) else {"result": result}),
        }

    return _run


async def _notify(args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Post a message onto the activity feed (the v1 'send notification')."""
    message = str(args.get("message") or "").strip()
    if not message:
        raise NodeExecutionError("notify needs a message")
    try:
        from acb_common import publish_activity

        publish_activity(
            kind="workflow",
            agent=actor,
            source="workflows",
            action="notify",
            status=message[:500],
        )
    except Exception:
        pass
    return {"sent": True, "message": message[:500]}


register_tool(
    WorkflowToolSpec(
        action="http.request",
        label="HTTP request",
        description="Call an external HTTP API (public hosts only; no stored credentials).",
        integration=None,
        read_only=False,
        destructive=False,
        open_world=True,
        args_schema={
            "url": "string|Full https:// URL. Public hosts only.",
            "method": "string|GET, POST, PUT, PATCH, DELETE or HEAD.",
            "headers": "object?|Extra request headers.",
            "body": "object?|JSON body. Ignored for GET and HEAD.",
        },
        handler=_http_request,
    )
)

# ⚠️ The `clickup.create_task` workflow tool was REMOVED 2026-08-24 (D52,
# board WS-39 S1). It proposed an outward write to a connector that no longer
# exists, so a workflow node built on it could only ever fail on approval.

register_tool(
    WorkflowToolSpec(
        action="notify.activity",
        label="Post notification",
        description="Post a message to the Metorite activity feed.",
        integration=None,
        read_only=False,
        destructive=False,
        open_world=False,
        args_schema={"message": "string|Text posted to the activity feed."},
        handler=_notify,
    )
)
