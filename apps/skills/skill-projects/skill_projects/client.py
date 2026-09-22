"""The gateway client every Projects tool goes through.

Spec: ``project-docs/specs/projects_ai_chat.md`` §4.1 and §5.1.

Three refusals live here, and nowhere else, so a tool added later inherits
them without remembering to:

1. **No acting user, no call.** The gateway reads a bearer with no
   ``X-User-Email`` as the platform acting as itself and grants SERVICE_ACCESS
   (``acb_auth/deps.py`` §1b). So :func:`headers` raises when the per-run
   ContextVar holds no user, and the tool never reaches the wire.
2. **The manifest is the allowlist.** :func:`request` asks
   :func:`skill_projects.manifest.allowed` before it builds a request. A verb
   and path the manifest does not carry, or carries as class X, is refused
   with the manifest's own reason. That is what keeps the two hard deletes
   (D-PM-35) out of reach even for a tool that tries.
3. **An id is a UUID or it is refused.** ``httpx`` resolves ``..`` segments
   before a request leaves, so an unchecked id is a path traversal
   (``agent-crm/agents.py`` ``_record_uuid``). :func:`uuid_of` normalises or
   raises, and every tool passes its ids through it.

Copied from ``apps/agents/agent-crm/agents.py``, which proved the shape.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import httpx

from skill_projects.manifest import allowed

__all__ = [
    "GatewayRefusal",
    "data",
    "delete",
    "get",
    "headers",
    "patch",
    "post",
    "put",
    "request",
    "uuid_of",
]


class GatewayRefusal(RuntimeError):
    """A call the client refused, or the gateway refused. The message is what
    the agent relays to the member, so it is written for them."""


def gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def current_user_email() -> str:
    """The member this run acts for: the per-run ContextVar the executor
    binds, and nothing else. ``""`` when there is none, so :func:`headers`
    refuses instead of widening to everyone's data."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id

        return _get_memory_user_id() or ""
    except Exception:
        return ""


def internal_token() -> str:
    try:
        from acb_common import get_settings

        settings = get_settings()
        return (
            getattr(settings, "gateway_internal_token", "")
            or getattr(settings, "litellm_master_key", "")
            or "sk-local"
        )
    except Exception:
        return os.environ.get("LITELLM_MASTER_KEY", "sk-local")


def headers() -> dict[str, str]:
    """Internal bearer plus the acting member. The member is not optional."""
    user = current_user_email()
    if not user:
        raise GatewayRefusal(
            "No acting user for this run, so there is nobody to act as. "
            "Refusing to call the gateway as the platform itself."
        )
    return {
        "Authorization": f"Bearer {internal_token()}",
        "Content-Type": "application/json",
        "X-User-Email": user,
    }


def uuid_of(value: Any, what: str = "id") -> str:
    """The canonical UUID string for ``value``, or a refusal the member can read."""
    raw = str(value or "").strip()
    try:
        return str(UUID(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise GatewayRefusal(
            f"{what} must be a UUID from an earlier tool result (full_id: …). Got {raw[:60]!r}."
        ) from exc


def data(value: Any) -> str:
    """Fence a string other people wrote — a title, a name, a comment — so an
    instruction inside it reads as data. Guillemets are a firmer boundary than
    quotes, and any embedded ones are stripped so the fence stays unambiguous."""
    return "«" + str(value or "").replace("«", "").replace("»", "") + "»"


def _raise_if_error(resp: httpx.Response, method: str, path: str) -> None:
    if resp.status_code < 400:
        return
    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or "")
    except Exception:
        detail = (resp.text or "")[:200]
    if resp.status_code == 404:
        hint = "Not found, or not visible to you."
    elif resp.status_code == 403:
        hint = "Not permitted."
    else:
        hint = f"Failed ({resp.status_code})."
    raise GatewayRefusal(f"Projects {method} {path}: {hint}" + (f" {detail}" if detail else ""))


async def request(
    method: str,
    path: str,
    *,
    timeout: float = 30.0,
    **kwargs: Any,
) -> httpx.Response:
    """One gateway round-trip. The manifest is consulted before the request
    exists, so a refused call never builds a client."""
    ok, why = allowed(method, path)
    if not ok:
        raise GatewayRefusal(why)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method.upper(),
            f"{gateway_url()}{path}",
            headers=headers(),
            **kwargs,
        )
        _raise_if_error(resp, method.upper(), path)
        return resp


async def get(path: str, params: dict[str, Any] | None = None) -> Any:
    return (await request("GET", path, params=params or {})).json()


async def post(path: str, payload: dict[str, Any] | None = None) -> Any:
    resp = await request("POST", path, json=payload or {})
    return resp.json() if resp.content else {}


async def patch(path: str, payload: dict[str, Any]) -> Any:
    return (await request("PATCH", path, json=payload)).json()


async def put(path: str, payload: dict[str, Any] | None = None) -> Any:
    resp = await request("PUT", path, json=payload or {})
    return resp.json() if resp.content else {}


async def delete(path: str, params: dict[str, Any] | None = None) -> Any:
    resp = await request("DELETE", path, params=params or {})
    return resp.json() if resp.content else {}
