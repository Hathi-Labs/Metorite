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
import re
from typing import Any
from uuid import UUID

import httpx
from acb_common.tool_refusals import (
    DETAIL_MAX,
    GatewayRefusal,
    data,
    response_body,
    safe_detail,
    strip_kind_prefix,
)

from skill_projects.manifest import allowed, route_for

__all__ = [
    "DETAIL_MAX",
    "GatewayRefusal",
    "data",
    "delete",
    "get",
    "headers",
    "patch",
    "post",
    "post_file",
    "put",
    "request",
    "resolve_task_refs",
    "safe_detail",
    "uuid_of",
]


#: What the timeline says a chat write came through (D-PM-36). Bounded by the
#: gateway's ``ACTOR_VIA_PATTERN``; a value outside it is dropped, not refused.
ACTOR_VIA = "chat:projects-assistant"


# WS-46 P2: the refusal, the fence and the safe detail are the ONE seam of
# `acb_common.tool_refusals`, which the CRM and email tools share. This
# module re-exports them, so each old import keeps working.


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
        return (getattr(settings, "gateway_internal_token", "") or "").strip()
    except Exception:
        return os.environ.get("GATEWAY_INTERNAL_TOKEN", "").strip()


def headers() -> dict[str, str]:
    """Internal bearer plus the acting member. The member is not optional."""
    user = current_user_email()
    if not user:
        raise GatewayRefusal(
            "No acting user for this run, so there is nobody to act as. "
            "Refusing to call the gateway as the platform itself.",
            fixable=False,
        )
    return {
        "Authorization": f"Bearer {internal_token()}",
        "Content-Type": "application/json",
        "X-User-Email": user,
        # D-PM-36 — the activity row records that the assistant prepared the
        # write. `created_by` stays the member; this is `meta.via`.
        "X-Actor-Via": ACTOR_VIA,
    }


#: The read tool that gives each kind of id (HRD-2). A refusal names it, so the
#: model reads the id and does not guess one. The first word that matches wins.
_READ_TOOL_FOR: tuple[tuple[str, str], ...] = (
    ("comment", "task_detail"),
    ("activity", "task_detail"),
    ("attachment", "task_detail"),
    ("link", "task_detail"),
    ("ref_id", "task_detail"),
    ("reply_to", "task_detail"),
    ("task", "find_tasks"),
    ("report", "report_list"),
    ("cycle", "list_cycles"),
    ("reminder", "list_reminders"),
    ("decision", "my_decisions"),
    ("update", "list_project_updates"),
    ("template", "list_templates"),
    ("view", "project_views"),
    ("page", "project_authorities"),
    ("pin", "project_authorities"),
    ("grant", "project_access"),
    ("area", "my_areas"),
    ("notification", "notifications"),
    ("run", "waiting_agent_requests"),
    ("answer", "waiting_agent_requests"),
    ("tag", "vocabulary"),
    ("type", "vocabulary"),
    ("field", "vocabulary"),
    ("status", "vocabulary"),
    ("subject", "task_detail"),
)


def read_tool_for(what: str) -> str:
    """The read tool that prints the id named *what*."""
    word = str(what or "").lower()
    for part, tool in _READ_TOOL_FOR:
        if part in word:
            return tool
    return "projects_tree"


def uuid_of(value: Any, what: str = "id") -> str:
    """The canonical UUID string for ``value``, or a refusal the member can read.

    A ``task:`` prefix on a task id is cut (``search_company`` prints it). A
    task number is not resolved here, because this gate is synchronous. The
    tool wrapper resolves it first, see :func:`resolve_task_refs`.
    """
    raw = strip_kind_prefix(
        value, *(("task",) if "task" in str(what).lower() else ()), read_tool=read_tool_for(what)
    )
    try:
        return str(UUID(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        tool = read_tool_for(what)
        number = " A task number needs find_tasks." if raw.lstrip("#").isdigit() else ""
        raise GatewayRefusal(
            f"{what} must be a UUID from an earlier tool result (full_id: …). "
            f"Got {raw[:60]!r}. {tool} gives it.{number}"
        ) from exc


_TASK_NUMBER = re.compile(r"^\s*#?\s*(\d{1,18})\s*$")


async def _task_uuid_of_number(number: int) -> str:
    """The id of task *number* in the member's org, through the search route.

    The route is the one ``find_tasks`` uses. It answers only tasks that the
    member may see, so a number outside their visibility is "not found" and
    never an id. No new database read exists here.
    """
    payload = await get("/projects/search", {"q": f"#{number}", "limit": 5})
    for row in (payload or {}).get("rows") or []:
        if str(row.get("task_number")) == str(number) and row.get("id"):
            return str(row["id"])
    raise GatewayRefusal(
        f"No task #{number} is visible to you. find_tasks searches by words or by number.",
        status=404,
    )


async def resolve_task_refs(value: Any) -> Any:
    """*value* with each task number (``244`` or ``#244``) swapped for its id.

    *value* is one task id, or a list of ids split by commas or lines. Text
    that is not a bare number comes back untouched, for :func:`uuid_of`.
    """
    if not isinstance(value, str):
        return value
    parts = re.split(r"([,\n]+)", value)
    out: list[str] = []
    changed = False
    for part in parts:
        match = _TASK_NUMBER.match(part)
        if match:
            out.append(await _task_uuid_of_number(int(match.group(1))))
            changed = True
        else:
            out.append(part)
    return "".join(out) if changed else value


def _template(method: str, path: str) -> str:
    """The manifest template of ``method path``, or ``""``."""
    route = route_for(method, path)
    return route.path if route is not None else ""


def _raise_if_error(resp: httpx.Response, method: str, path: str) -> None:
    if resp.status_code < 400:
        return
    body = response_body(resp)
    detail, fields = safe_detail(resp.status_code, body, member=current_user_email())
    if resp.status_code == 404:
        hint = "Not found, or not visible to you."
    elif resp.status_code == 403:
        hint = "Not permitted."
    else:
        hint = f"Failed ({resp.status_code})."
    raise GatewayRefusal(
        f"Projects {method} {path}: {hint}" + (f" {detail}" if detail else ""),
        status=resp.status_code,
        detail=detail,
        fields=fields,
        route=_template(method, path),
    )


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
        raise GatewayRefusal(why, fixable=False, route=_template(method, path))
    # Both refusals come BEFORE the client exists: the manifest's, and the
    # no-acting-user one `headers()` raises. A refused call builds nothing.
    # The caller's headers go first, so the identity headers win (IMG-2 review).
    sent = {**(kwargs.pop("headers", None) or {}), **headers()}
    if "files" in kwargs:
        # A multipart body sets its own Content-Type, with the boundary. The
        # JSON header would break the upload (WS-64 OP-6c).
        sent.pop("Content-Type", None)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method.upper(),
            f"{gateway_url()}{path}",
            headers=sent,
            **kwargs,
        )
        _raise_if_error(resp, method.upper(), path)
        return resp


async def get(path: str, params: dict[str, Any] | None = None) -> Any:
    return (await request("GET", path, params=params or {})).json()


async def post(
    path: str, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None
) -> Any:
    """A POST. ``params`` is the query string, for a route that reads a flag
    there (``include_subtasks`` on archive and complete, WS-46 P6)."""
    resp = await request("POST", path, json=payload or {}, params=params or {})
    return resp.json() if resp.content else {}


async def post_file(
    path: str,
    field: str,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> Any:
    """A multipart POST of one file (WS-64 OP-6c, ``attach_to_task``). The
    manifest check, the acting member and the refusals are ``request``'s."""
    resp = await request(
        "POST", path, timeout=60.0, files={field: (filename, content, content_type)}
    )
    return resp.json() if resp.content else {}


async def patch(
    path: str,
    payload: dict[str, Any],
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """A PATCH. ``params`` is the query string (``include_subtasks``, WS-46 P6).
    ``headers`` adds to the usual ones, for ``If-Match`` (IMG-2)."""
    return (
        await request("PATCH", path, json=payload, params=params or {}, headers=headers)
    ).json()


async def put(
    path: str, payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """A PUT. ``headers`` adds a request header, such as ``If-Match`` (SP-8a).
    The identity headers of :func:`headers` always win (``request``)."""
    resp = await request("PUT", path, json=payload or {}, headers=headers or {})
    return resp.json() if resp.content else {}


async def delete(path: str, params: dict[str, Any] | None = None) -> Any:
    resp = await request("DELETE", path, params=params or {})
    return resp.json() if resp.content else {}
