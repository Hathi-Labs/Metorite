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

from skill_projects.manifest import allowed, route_for

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


#: What the timeline says a chat write came through (D-PM-36). Bounded by the
#: gateway's ``ACTOR_VIA_PATTERN``; a value outside it is dropped, not refused.
ACTOR_VIA = "chat:projects-assistant"


class GatewayRefusal(RuntimeError):
    """A call the client refused, or the gateway refused. The message is what
    the agent relays to the member, so it is written for them.

    WS-46 P2: a refusal of the GATEWAY also carries its ``status``, its
    ``detail`` (already made safe by :func:`safe_detail`) and the request
    ``fields`` a 422 names. ``skill_projects.refusals`` builds the model's
    text from those three, never from the message, so the route path in the
    message does not reach the model. A refusal of the client itself has
    ``status`` ``None``: its message is text this package wrote.

    ``fixable`` is ``False`` for a refusal that no argument can fix: the
    manifest refused the route, or the run has no acting member. The model
    then reads a neutral "Next:" line, not "fix the argument". ``route`` is
    the manifest template of the refused call (``/projects/tasks/{task_id}``)
    for the log line ``projects.tool_refused``, never the concrete path."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
        fields: tuple[str, ...] = (),
        fixable: bool = True,
        route: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.fields = fields
        self.fixable = fixable
        self.route = route


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
    quotes, and any embedded ones are stripped so the fence stays unambiguous.

    Line breaks collapse to one space, and that is not cosmetic. The cards
    parse the tool output LINE BY LINE (``- #<n> «title»`` then ``full_id:``),
    so a status name carrying a newline could forge a row that renders as a
    real, clickable task. Inside one line the fence holds."""
    text = str(value or "").replace("«", "").replace("»", "")
    return "«" + " ".join(text.split()) + "»"


#: The longest gateway detail the model may read. A 422 that lists ten
#: fields still fits, and a long body is cut.
DETAIL_MAX = 300

#: Words that only an exception from the database layer or from Python
#: carries. A detail that holds one is dropped whole, because the rest of
#: it can name a query, a table or a host (WS-46 P2).
_LEAK_MARKERS = (
    "traceback (most recent call last)",
    'file "',
    "psycopg",
    "sqlalchemy",
    "asyncpg",
    "[sql:",
    "background on this error",
    "violates ",
    "detail:  key (",
)
#: A URL or a DSN (``postgresql://user:pass@host/db``), in any scheme.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+")
#: A bearer value, and the key shapes the platform issues or holds.
_SECRET = re.compile(
    r"(?i)\bbearer\s+\S+"
    r"|\b(?:sk|pk|rk|ghp|gho|ghs|xox[abprs]|cc_live|cc_depl)[-_][A-Za-z0-9_\-]{6,}"
    r"|\beyJ[\w-]+\.[\w-]+\.[\w-]+"
)
#: An email address. Only the acting member's own address stays.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
#: An absolute path on a server, or on a Windows box.
_PATH = re.compile(r"(?:/(?:opt|home|srv|var)/|\b[A-Za-z]:\\)\S*")


def _template(method: str, path: str) -> str:
    """The manifest template of ``method path``, or ``""``."""
    route = route_for(method, path)
    return route.path if route is not None else ""


def _field_of(loc: Any) -> str:
    """The request field a FastAPI validation error points at, or ``""``."""
    if not isinstance(loc, (list, tuple)):
        return ""
    names = [str(part) for part in loc if isinstance(part, str)]
    names = [n for n in names if n not in ("body", "query", "path", "header")]
    return names[-1] if names else ""


def safe_detail(status: int, body: Any, member: str = "") -> tuple[str, tuple[str, ...]]:
    """The gateway's own words from a refused response, and the fields a 422
    names. Only text the route wrote, and only the safe part of it.

    * A string ``detail`` is the route's sentence (``HTTPException``).
    * A list ``detail`` is FastAPI's 422. Each row becomes ``field: msg``.
      The row's ``input``, ``ctx`` and ``url`` are dropped.
    * A dict ``detail`` gives its ``message``, or its ``error`` code, and
      the names in its ``fields`` (``required_fields_missing``).
    * A body that is not JSON is not the route's words, so it gives nothing.
    * A 5xx other than 503 gives nothing. The 503 that the gateway writes
      for an outage (``gateway.main._tenant_unbound``) is safe text.

    Every result then loses any URL, DSN, bearer, key, JWT and absolute
    path, and each email address except *member*'s own (the acting member).
    It is then cut to :data:`DETAIL_MAX`. A detail that names a stack, a query or the
    database layer is dropped whole (:data:`_LEAK_MARKERS`).
    """
    if status >= 500 and status != 503:
        return "", ()
    raw: Any = (body.get("detail") or body.get("error")) if isinstance(body, dict) else None
    fields: list[str] = []
    if isinstance(raw, list):
        parts = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            field = _field_of(row.get("loc"))
            msg = str(row.get("msg") or "").strip()
            if field:
                fields.append(field)
            if msg:
                parts.append(f"{field}: {msg}" if field else msg)
        text = ". ".join(parts)
    elif isinstance(raw, dict):
        text = str(raw.get("message") or raw.get("error") or "")
        for row in raw.get("fields") or []:
            if isinstance(row, dict) and row.get("name"):
                fields.append(str(row["name"]))
    elif isinstance(raw, str):
        text = raw
    else:
        text = ""
    named = tuple(dict.fromkeys(fields))
    if any(marker in text.lower() for marker in _LEAK_MARKERS):
        return "", named
    text = _URL.sub("<link removed>", text)
    text = _SECRET.sub("<secret removed>", text)
    own = member.strip().lower()
    text = _EMAIL.sub(
        lambda m: m.group(0) if own and m.group(0).lower() == own else "<address removed>",
        text,
    )
    text = _PATH.sub("<path removed>", text)
    text = " ".join(text.split())
    if len(text) > DETAIL_MAX:
        text = text[: DETAIL_MAX - 1].rstrip() + "…"
    return text, named


def _raise_if_error(resp: httpx.Response, method: str, path: str) -> None:
    if resp.status_code < 400:
        return
    try:
        body: Any = resp.json()
    except Exception:
        body = None
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
    sent = headers()
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


async def patch(path: str, payload: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
    """A PATCH. ``params`` is the query string (``include_subtasks``, WS-46 P6)."""
    return (await request("PATCH", path, json=payload, params=params or {})).json()


async def put(path: str, payload: dict[str, Any] | None = None) -> Any:
    resp = await request("PUT", path, json=payload or {})
    return resp.json() if resp.content else {}


async def delete(path: str, params: dict[str, Any] | None = None) -> Any:
    resp = await request("DELETE", path, params=params or {})
    return resp.json() if resp.content else {}
