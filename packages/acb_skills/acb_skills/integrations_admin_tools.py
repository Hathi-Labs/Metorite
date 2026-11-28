"""The Integrations tools of the ``org-admin`` pack (PAR-6a, Metorite task 3109).

Spec: ``project-docs/specs/chat_parity.md`` section 6.3, rung 3, row 3. Seven
tools let a member do from the chat what the Integrations app does with a
click. Each tool calls the gateway over HTTP as the member of the run.

Reads, with no card:

* :func:`read_integrations` reads the connections (``GET /integrations/connections``),
  the service list (``GET /integrations/status``), the plugins
  (``GET /integrations/plugins``), the GitHub account
  (``GET /integrations/github/account``) or the skill catalog
  (``GET /integrations/skills``).
* :func:`read_agent_access` reads the member's own agent connections
  (``GET /agent-access/connections``), every agent connection of the org
  (``GET /integrations/agent-connections``) or the app requests that wait for
  the member (``GET /agent-access/requests``). It lists the requests only.
* :func:`probe_integration` tests one service (``GET /integrations/test``) or
  one MCP server (``POST /integrations/mcp/{name}/test``) with the stored
  credentials. It never shows a credential.
* :func:`discover_api_setup` asks the model and a web search for the setup of
  an API (``POST /integrations/discover``). It is a proposal, and it saves
  nothing.

Writes, each after ONE card:

* :func:`revoke_agent_connection` revokes one of the member's own agent
  connections (``DELETE /agent-access/connections/{id}``), or any agent
  connection of the org (``DELETE /integrations/agent-connections/{id}``).
* :func:`disconnect_integration` disconnects one org connection
  (``POST /integrations/connections/{id}/disconnect``), removes one plugin
  (``DELETE /integrations/plugins/{id}``) or disconnects GitHub
  (``DELETE /integrations/github``).
* :func:`set_mcp_server` turns the always-confirm switch of one MCP server on,
  turns its read-only-key switch off, or sets its description
  (``PATCH /integrations/mcp/{name}``).

**The chat never lowers the safety of an MCP server.** ``always_confirm`` off
and ``read_only_key`` on are refused with plain words, before a card or a
request. After MCP-NC1, always confirm is the only card that a tool of a server
shows in a member chat. A key that only reads skips that card for each tool
(``permission_policy.decide``, step 3b). The server's own text reaches the
chat, so a chat that the server wrote to must not take its own card away. A
person lowers the safety on the Integrations page (PAR-6b, task 3932).

**The gateway decides.** Each tool sends the internal bearer and
``X-User-Email`` of the run's member, as ``org_admin_tools`` does. No tool takes
an org or a caller argument (R5). The guard of each route stays the only check.

**No card for a member without the right.** A write of the org reads
``GET /auth/me`` first. A member without ``integrations:manage`` gets plain
words and no card. Each write then reads its row. A row that is gone, a row of
another org and a member connection each get plain words and no card. After
the card, a 403, a 404 or a 409 of the route comes back as plain words.

**The race rule.** A write reads its row again after ``approved``. It sends
nothing when the row changed while the card was open. Only ``approved`` sends,
and it sends ONE request.

**Text from outside is data.** A plugin manifest, an MCP server, an outside
agent and the discovery model write names and descriptions. Each read puts its
rows in ONE ``UntrustedFrame`` block, after ``strip_hidden``. A card fences
such a value in « ».

**The held chat.** The two probes and the discovery leave the org, so each
says ``open_world=True``, as ``web_search`` does. A chat that read outside
mail holds them off. ``org-admin`` is in ``egress.HELD_WRITE_OWNERS``, so a
held chat holds off the three writes too, while ``HELD_CARD_WRITES_ORGS``
holds the org.

**Not here.** No tool sends the decision of an app request
(``/agent-access/requests/{id}/decision``): the chat calls as the member, so
it would approve its own change. The card of STU-B2c-2c is that surface. No
tool changes the grant of an agent connection, installs a plugin from a URL,
saves a custom API or adds an MCP server: those wait for the owner (PAR-6b).

Fences: ``tests/unit/test_integrations_admin_tools.py`` and the R8 suite
``tests/unit/test_integrations_admin_tools_r8.py``.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from textwrap import wrap
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from acb_common.card_body import CardWontFit, card_body
from acb_common.paging import paged
from acb_common.settings import internal_auth_headers

from acb_skills.hidden_text import body_hidden_problem, strip_hidden
from acb_skills.memory_tools import run_member_address
from acb_skills.tool_annotations import annotate

__all__ = [
    "INTEGRATIONS_ADMIN_TOOLS",
    "disconnect_integration",
    "discover_api_setup",
    "probe_integration",
    "read_agent_access",
    "read_integrations",
    "revoke_agent_connection",
    "set_mcp_server",
]

#: The gateway routes. A package must not import the gateway, so the paths
#: are text here. ``test_chat_parity.py`` reads them from this source, so a
#: path with an id in it is a literal f-string at its call.
CONNECTIONS_PATH = "/integrations/connections"
STATUS_PATH = "/integrations/status"
PLUGINS_PATH = "/integrations/plugins"
GITHUB_ACCOUNT_PATH = "/integrations/github/account"
GITHUB_PATH = "/integrations/github"
SKILLS_PATH = "/integrations/skills"
MCP_PATH = "/integrations/mcp"
TEST_PATH = "/integrations/test"
DISCOVER_PATH = "/integrations/discover"
ORG_AGENTS_PATH = "/integrations/agent-connections"
MY_AGENTS_PATH = "/agent-access/connections"
APP_REQUESTS_PATH = "/agent-access/requests"
#: The read of the member's own resolved rights (``routes/admin/me.py``).
ME_PATH = "/auth/me"

#: The right of each write of the org (``routes/integrations.py``).
MANAGE_RIGHT = "integrations:manage"

#: The kinds of :func:`read_integrations` and :func:`read_agent_access`.
INTEGRATION_KINDS = ("connections", "status", "plugins", "github", "skills")
AGENT_KINDS = ("mine", "org", "requests")
#: The kinds of :func:`disconnect_integration`.
DISCONNECT_KINDS = ("connection", "plugin", "github")

#: The most rows of one page.
MAX_ROWS = 50
#: The most characters of a name, and of a description, in a result.
MAX_NAME_CHARS = 80
MAX_TEXT_CHARS = 400
#: The most characters of the text of a route in a refusal.
MAX_DETAIL_CHARS = 400
#: The most characters of a discovery query. The UI field is one line.
MAX_QUERY_CHARS = 200
#: The longest description that ``PATCH /integrations/mcp/{name}`` stores
#: (``integrations.MCP_DESCRIPTION_MAX``).
MAX_MCP_DESCRIPTION = 400

#: The shape of an MCP server name (``gateway.connections.mcp.NAME_RE``).
_MCP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$")
#: The shape of a service id of the test route: a key of the setup guides.
_SERVICE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: The shape of a connection id of the console: ``<provider>:<key>``. A colon
#: splits it, and no character of it is a path or query character.
_CONNECTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?::[A-Za-z0-9_.-]+)+$")
#: A run of characters that can be a key or a token, in a probe detail.
_TOKENISH = re.compile(r"[A-Za-z0-9_\-]{24,}")
#: A query string, in a probe detail. A key can ride in one.
_QUERY = re.compile(r"\?\S*")

_NO_MEMBER = "There is no member for this run, so the Integrations tools cannot act."
_SHARED_ROOM = "A shared room cannot use the Integrations tools. Ask in your own chat."
_RIGHTS_UNREAD = "Your rights could not be read, so nothing changed. Try again."
_NO_RIGHT = ("You do not hold the right to {what} in your organization ({right}), so no card "
             "shows and nothing changed. Ask an admin who holds that right.")
_UNREACHED = "The Integrations routes could not be reached ({kind}). Try again."
_MAYBE = ("The {act} may have worked. Read the Integrations list again before you try "
          "again.")
_SHAPE = "The Integrations route answered in a shape that this tool cannot read."
_OFF = ("This part of Integrations is not on for your organization, or you cannot open "
        "it.")
_FORBIDDEN = ("You do not hold the right that this needs in your organization. Ask an admin "
              "who manages Integrations.")
_CHANGED = ("The {what} changed while the card was open, so nothing changed. Read it "
            "again, and ask again.")
_NO_CARD = ("This chat shows no card, so nothing changed. Do this on the Integrations "
            "page.")
_NOT_DONE = "Nothing changed. The decision was {decision}."
_WONT_FIT = ("This change does not fit on one card, so nothing changed. Do it on the "
             "Integrations page.")
_BAD_KIND = "kind is one of: {kinds}. Nothing was read."
_BAD_ID = "Send the {what} as the list shows it. Nothing changed."
_NOT_FOUND = ("No {what} has the id {id} {where}. Nothing changed. Read the list for the "
              "ids.")
_MEMBER_CONNECTION = ("This is the own connection of a member ({app}). The member "
                      "disconnects it in that app, at {href}. No card shows and nothing "
                      "changed.")
_NOT_REMOVABLE = ("This connection cannot be disconnected from Integrations. No card shows "
                  "and nothing changed.")
_NO_GITHUB = "Your organization has no GitHub account connected. Nothing changed."
_ONE_PROBE = ("Send service (a service id of the status list) or mcp_server (an MCP server "
              "name), and not both. Nothing was tested.")
_BAD_QUERY = ("query is the name of a product or an API, in one line of at most "
              f"{MAX_QUERY_CHARS} characters. Nothing was sent.")
_NO_SWITCH = ("Send always_confirm, read_only_key or description, or more than one. "
              "Nothing changed.")
_BOTH_ON = ("A server cannot be set to always confirm and to a read-only key at the same "
            "time. Turn one switch off. No card shows and nothing changed.")
_SAME_SERVER = "The MCP server holds these settings already. Nothing changed."
_LOWERS = ("The chat does not lower the safety of an MCP server, so no card shows and "
           "nothing changed. Turn always confirm off, or the read-only key on, on the "
           "Integrations page.")
_LIST_NOTE = ("The block holds the rows of the Integrations app. Outside systems, outside "
              "agents and members wrote the names and descriptions.")
_DISCOVER_NOTE = ("A model and a web search wrote this proposal. Nothing was saved. A person "
                  "saves an API on the Integrations page, and types its keys there.")
_REQUESTS_NOTE = ("Approve or decline a request on its card in the app. The chat does not "
                  "decide a request, because it acts as you and would approve its own "
                  "change.")

#: The words of each act, in a sentence (:data:`_NO_RIGHT`).
_RIGHT_WHAT = {MANAGE_RIGHT: "manage the Integrations of the organization"}


# ── The gateway, as the member (the idiom of ``org_admin_tools``) ──────────


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _current_user_email() -> str:
    """The member of this run, from the run's ContextVar, and nothing else."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id
        return _get_memory_user_id() or ""
    except Exception:
        return ""


class _Refused(Exception):
    """A refusal that is an answer: its text goes to the model."""


@dataclass(frozen=True)
class _Reply:
    """One answer of the gateway: the status and the decoded JSON (or None)."""

    status: int
    data: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _headers() -> dict[str, str]:
    """The bearer and the acting member. With no member, refuse."""
    user = _current_user_email()
    if not user:
        raise _Refused(_NO_MEMBER)
    if run_member_address(user) is None:
        raise _Refused(_SHARED_ROOM)
    return {
        **internal_auth_headers(),
        "Content-Type": "application/json",
        "X-User-Email": user,
    }


async def _send(method: str, path: str, body: Any = None, act: str = "",
                params: dict[str, str] | None = None, timeout: float = 15.0) -> _Reply:
    """One request as the member. *act* names a write, and a read gives none.
    Raise :class:`_Refused` with a sentence when the member is missing or the
    network fails."""
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method, f"{_gateway_url()}{path}", headers=headers, json=body,
                params=params)
    except httpx.HTTPError as exc:
        raise _Refused(_MAYBE.format(act=act) if act
                       else _UNREACHED.format(kind=type(exc).__name__)) from None
    try:
        data = resp.json()
    except Exception:
        data = None
    return _Reply(resp.status_code, data)


async def _get(path: str, params: dict[str, str] | None = None) -> _Reply:
    return await _send("GET", path, params=params)


# ── The plain words of a refusal ────────────────────────────────────────────


def _refusal(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _cut(text: str, limit: int) -> str:
    """*text* as one line of at most *limit* characters. A cut line ends with
    "...". A long word is cut too, so a name never vanishes."""
    lines = wrap(" ".join(text.split()), limit - 3)
    if not lines:
        return ""
    return lines[0] + ("..." if len(lines) > 1 else "")


def _detail(reply: _Reply) -> str:
    """The text of a refusal of the route. A dict detail gives its message."""
    raw = reply.data.get("detail") if isinstance(reply.data, dict) else None
    if isinstance(raw, dict):
        raw = raw.get("message") or raw.get("detail")
    if isinstance(raw, str):
        return _cut(strip_hidden(raw), MAX_DETAIL_CHARS)
    return ""


def _code(reply: _Reply) -> str:
    raw = reply.data.get("detail") if isinstance(reply.data, dict) else None
    return str(raw.get("code") or "") if isinstance(raw, dict) else ""


def _fail(reply: _Reply, *, write: bool = False, **extra: Any) -> dict[str, Any]:
    """The refusal of a route in plain words. A write ends with "Nothing changed."."""
    tail = " Nothing changed." if write else ""
    detail = _detail(reply)
    if reply.status == 401:
        return _refusal("Your session is not signed in." + tail, status=401, **extra)
    if reply.status == 403 and (not detail or detail.startswith("Forbidden")):
        return _refusal(_FORBIDDEN + tail, status=403, **extra)
    if reply.status == 404 and detail in ("", "Not Found"):
        return _refusal(_OFF + tail, status=404, **extra)
    if reply.status == 409 and _code(reply) == "member_connection":
        raw = reply.data["detail"]
        href = str(raw.get("setup_href") or "/integrations")
        return _refusal(_MEMBER_CONNECTION.format(app="its own app", href=href),
                        status=409, **extra)
    if detail:
        if write and "Nothing changed" not in detail:
            detail = f"{detail}{tail}"
        return _refusal(detail, status=reply.status, **extra)
    return _refusal(f"The Integrations route answered with status {reply.status}.{tail}",
                    status=reply.status, **extra)


async def _right_refusal(right: str) -> dict[str, Any] | None:
    """The plain refusal of a member without *right*, or None.

    It reads ``GET /auth/me``, the resolved rights of the server, so the tool
    keeps no rule of its own. A failed read refuses, so it fails closed."""
    reply = await _get(ME_PATH)
    caps = reply.data.get("capabilities") if isinstance(reply.data, dict) else None
    if not reply.ok or not isinstance(caps, list):
        return _refusal(_RIGHTS_UNREAD)
    if right not in caps:
        return _refusal(_NO_RIGHT.format(what=_RIGHT_WHAT[right], right=right), status=403)
    return None


# ── The text of other people ────────────────────────────────────────────────


def _text(value: Any, limit: int = MAX_NAME_CHARS) -> str:
    """Outside text as one line with no hidden character, cut."""
    text = strip_hidden(value) if isinstance(value, str) else ""
    return _cut(text, limit)


def _fence(value: Any) -> str:
    """A value of another person or system on a card, between « and »."""
    return "«" + _text(value).replace("«", "").replace("»", "") + "»"


def _host(url: Any) -> str:
    """The host of a URL. No scheme, user, port, path or query."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _masked(text: Any) -> str:
    """A probe detail with each query string and each run that can be a key
    replaced. A provider message can echo a URL with its key."""
    if not isinstance(text, str):
        return ""
    out = _QUERY.sub("?[hidden]", strip_hidden(text))
    out = _TOKENISH.sub("[hidden]", out)
    return _cut(out, 200)


def _frame(label: str, data: Any, note: str) -> tuple[str, str]:
    """``(block, note)``: *data* as JSON in ONE ``UntrustedFrame`` block."""
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    block = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return frame.wrap(label, block), frame.note(note)


# ── The arguments ───────────────────────────────────────────────────────────


def _uuid_arg(raw: Any) -> str | None:
    """*raw* as a UUID in its plain form, or None."""
    if not isinstance(raw, str):
        return None
    try:
        return str(uuid.UUID(raw.strip()))
    except ValueError:
        return None


def _bool_arg(raw: Any) -> bool | str | None:
    """``True``, ``False``, ``None`` (not sent), or ``"bad"``."""
    if raw is None or isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
        return raw.strip().lower() == "true"
    return "bad"


# ── The reads of a row before a card ────────────────────────────────────────


def _list_of(reply: _Reply, key: str | None = None) -> list[dict[str, Any]] | None:
    data = reply.data.get(key) if key and isinstance(reply.data, dict) else reply.data
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict)]


async def _row(path: str, key: str | None, field: str, value: str,
               ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(row, None)`` of the row whose *field* is *value* in the list of *path*,
    ``(None, None)`` when no row has it, or ``(None, refusal)``."""
    reply = await _get(path)
    if not reply.ok:
        return None, _fail(reply, write=True)
    rows = _list_of(reply, key)
    if rows is None:
        return None, _refusal(_SHAPE + " Nothing changed.")
    return next((r for r in rows if str(r.get(field, "")) == value), None), None


# ── The card ────────────────────────────────────────────────────────────────


async def _decide(title: str, detail: str, note: str,
                  rows: list[tuple[str, str]]) -> str:
    """Show ONE card and wait. The decision is ``approved``, ``revised`` (the
    member wrote in the chat), ``stopped`` or ``no_card``. Raise
    :class:`_Refused` when the card does not fit."""
    from acb_skills import ask_tools

    try:
        context = str(card_body(note, rows))
    except CardWontFit:
        raise _Refused(_WONT_FIT) from None
    # The run's own member may answer, and no other (PZ-32). The member is the
    # run's, never an input.
    answer = await ask_tools.request_confirmation(
        title, detail, context, non_interactive_default="deny", close_on_steer=True,
        responder=_current_user_email() or None, fenced=True)
    if answer is True:
        return "approved"
    if answer is ask_tools.REVISED:
        return "revised"
    if not ask_tools.confirmation_channel_open():
        return "no_card"
    return "stopped"


def _not_done(decision: str) -> dict[str, Any]:
    if decision == "no_card":
        return _refusal(_NO_CARD, decision=decision)
    return _refusal(_NOT_DONE.format(decision=decision), decision=decision)


# ── The reads ───────────────────────────────────────────────────────────────


def _connection_row(r: dict[str, Any]) -> dict[str, Any]:
    raw = r.get("detail") if isinstance(r.get("detail"), dict) else {}
    detail = {str(k): _text(v, MAX_TEXT_CHARS) for k, v in raw.items()}
    return {
        "id": _text(r.get("id"), 200), "label": _text(r.get("label")),
        "kind": _text(r.get("kind")), "provider": _text(r.get("provider")),
        "app": _text(r.get("owning_app")), "scope": _text(r.get("scope")),
        "owner": _text(r.get("owner"), 254), "status": _text(r.get("status")),
        "last_error": _text(r.get("last_error")),
        "can_disconnect": r.get("can_disconnect") is True, "detail": detail,
    }


def _status_row(r: dict[str, Any]) -> dict[str, Any]:
    def names(key: str) -> list[str]:
        raw = r.get(key)
        return [_text(v.get("key") if isinstance(v, dict) else v) for v in raw] \
            if isinstance(raw, list) else []

    return {
        "service": _text(r.get("service")), "label": _text(r.get("label")),
        "configured": r.get("configured") is True, "storage": _text(r.get("storage")),
        "category": _text(r.get("category")), "custom": r.get("is_custom") is True,
        "description": _text(r.get("description"), 200),
        "missing_keys": names("missing_keys"), "stored_keys": names("db_keys"),
    }


def _plugin_row(r: dict[str, Any]) -> dict[str, Any]:
    tools = r.get("tools_generated")
    return {
        "id": _text(r.get("id")), "name": _text(r.get("name")), "label": _text(r.get("label")),
        "description": _text(r.get("description"), MAX_TEXT_CHARS),
        "enabled": r.get("enabled") is True, "version": _text(r.get("version")),
        "tools": len(tools) if isinstance(tools, list) else 0,
        "auth_type": _text(r.get("auth_type")), "manifest_host": _host(r.get("manifest_url")),
    }


def _github_row(d: dict[str, Any]) -> dict[str, Any]:
    scopes = d.get("scopes")
    return {
        "connected": d.get("connected") is True, "status": _text(d.get("status")),
        "login": _text(d.get("login")), "connected_by": _text(d.get("connected_by"), 254),
        "scopes": [_text(s) for s in scopes] if isinstance(scopes, list) else [],
        "app_available": d.get("app_available") is True,
    }


def _family_row(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": _text(f.get("slug")), "label": _text(f.get("label")),
        "tools": f.get("tool_count") if isinstance(f.get("tool_count"), int) else 0,
        "core": f.get("core") is True, "tokens": f.get("token_cost"),
        **({"owner": _text(f.get("owner"))} if f.get("owner") else {}),
    }


def _agent_family_row(a: dict[str, Any]) -> dict[str, Any]:
    fams = a.get("families")
    off = a.get("disabled_families")
    return {
        "agent": _text(a.get("name")),
        "families": [_text(s) for s in fams] if isinstance(fams, list) else [],
        "turned_off": [_text(s) for s in off] if isinstance(off, list) else [],
        "all_families": a.get("all_families") is True,
    }


def _agent_row(r: dict[str, Any], *, org: bool) -> dict[str, Any]:
    scopes = r.get("scopes")
    out = {
        "id": _text(r.get("id")), "agent": _text(r.get("client_name")),
        "origin": _text(r.get("client_origin"), 200),
        "scopes": [_text(s) for s in scopes] if isinstance(scopes, list) else [],
        "kind": _text(r.get("credential")), "created_at": _text(r.get("created_at"), 40),
        "last_used_at": _text(r.get("last_used_at"), 40),
        "expires_at": _text(r.get("expires_at"), 40),
    }
    if org:
        out["member"] = _text(r.get("member_email"), 254)
    return out


def _request_row(r: dict[str, Any]) -> dict[str, Any]:
    card = r.get("card") if isinstance(r.get("card"), dict) else {}
    return {
        "request_id": _text(r.get("request_id")), "state": _text(r.get("state")),
        "app": _text(r.get("app")), "op": _text(r.get("op")), "class": _text(r.get("class")),
        "subject": _text(r.get("subject"), 200), "title": _text(card.get("title"), 200),
        "via": _text(r.get("client_via")), "created_at": _text(r.get("created_at"), 40),
        "expires_at": _text(r.get("expires_at"), 40),
    }


def _page_out(label: str, rows: list[dict[str, Any]], offset: Any, limit: Any,
              noun: str, note: str = _LIST_NOTE, **extra: Any) -> dict[str, Any]:
    page = paged(rows, offset, limit, default=MAX_ROWS, maximum=MAX_ROWS, noun=noun)
    block, said = _frame(label, page.items, note)
    return {"ok": True, "rows": block, "total": page.total, "shown": len(page.items),
            "page": page.line, "note": said, **extra}


async def _read_kind(kind: str, offset: Any, limit: Any) -> dict[str, Any]:
    if kind == "connections":
        reply = await _get(CONNECTIONS_PATH)
        rows = _list_of(reply, "connections") if reply.ok else None
        if not reply.ok:
            return _fail(reply)
        if rows is None:
            return _refusal(_SHAPE)
        gone = reply.data.get("unavailable") if isinstance(reply.data, dict) else None
        return _page_out("INTEGRATIONS CONNECTIONS", [_connection_row(r) for r in rows],
                         offset, limit, "connections",
                         unavailable=[_text(s) for s in gone] if isinstance(gone, list) else [])
    if kind == "status":
        reply = await _get(STATUS_PATH)
        rows = _list_of(reply) if reply.ok else None
        if not reply.ok:
            return _fail(reply)
        if rows is None:
            return _refusal(_SHAPE)
        return _page_out("INTEGRATIONS SERVICES", [_status_row(r) for r in rows],
                         offset, limit, "services")
    if kind == "plugins":
        reply = await _get(PLUGINS_PATH)
        rows = _list_of(reply) if reply.ok else None
        if not reply.ok:
            return _fail(reply)
        if rows is None:
            return _refusal(_SHAPE)
        return _page_out("INTEGRATIONS PLUGINS", [_plugin_row(r) for r in rows],
                         offset, limit, "plugins")
    if kind == "github":
        reply = await _get(GITHUB_ACCOUNT_PATH)
        if not reply.ok:
            return _fail(reply)
        if not isinstance(reply.data, dict):
            return _refusal(_SHAPE)
        block, said = _frame("GITHUB ACCOUNT", _github_row(reply.data), _LIST_NOTE)
        return {"ok": True, "rows": block, "note": said}
    reply = await _get(SKILLS_PATH)
    if not reply.ok:
        return _fail(reply)
    families = _list_of(reply, "families")
    agents = _list_of(reply, "agents")
    if families is None or agents is None:
        return _refusal(_SHAPE)
    total = reply.data.get("total_injected_tokens")
    # Each agent row is short, and the list is the agents of the registry, so
    # it goes whole. The families page.
    return _page_out("SKILL FAMILIES", [_family_row(f) for f in families], offset, limit,
                     "families", agents=[_agent_family_row(a) for a in agents],
                     total_injected_tokens=total if isinstance(total, int) else None)


@annotate(read_only=True, idempotent=True, open_world=False)
async def read_integrations(
    kind: Literal["connections", "status", "plugins", "github", "skills"] = "connections",
    offset: int = 0, limit: int = MAX_ROWS,
) -> dict[str, Any]:
    """Read the Integrations of your organization: connections, services, plugins, the GitHub account, or the skill catalog."""
    want = str(kind or "").strip().lower()
    if want not in INTEGRATION_KINDS:
        return _refusal(_BAD_KIND.format(kinds=", ".join(INTEGRATION_KINDS)))
    try:
        return await _read_kind(want, offset, limit)
    except _Refused as exc:
        return _refusal(str(exc))


@annotate(read_only=True, idempotent=True, open_world=False)
async def read_agent_access(
    kind: Literal["mine", "org", "requests"] = "mine",
    offset: int = 0, limit: int = MAX_ROWS,
) -> dict[str, Any]:
    """Read your own agent connections, every agent connection of the organization, or the app requests that wait for you."""
    want = str(kind or "").strip().lower()
    if want not in AGENT_KINDS:
        return _refusal(_BAD_KIND.format(kinds=", ".join(AGENT_KINDS)))
    try:
        if want == "requests":
            reply = await _get(APP_REQUESTS_PATH)
            rows = _list_of(reply, "rows") if reply.ok else None
            if not reply.ok:
                return _fail(reply)
            if rows is None:
                return _refusal(_SHAPE)
            more = reply.data.get("more") if isinstance(reply.data, dict) else 0
            return _page_out("APP REQUESTS", [_request_row(r) for r in rows], offset, limit,
                             "requests", note=f"{_LIST_NOTE} {_REQUESTS_NOTE}",
                             not_listed=more if isinstance(more, int) else 0)
        reply = await _get(ORG_AGENTS_PATH if want == "org" else MY_AGENTS_PATH)
    except _Refused as exc:
        return _refusal(str(exc))
    rows = _list_of(reply, "connections") if reply.ok else None
    if not reply.ok:
        return _fail(reply)
    if rows is None:
        return _refusal(_SHAPE)
    return _page_out("AGENT CONNECTIONS", [_agent_row(r, org=want == "org") for r in rows],
                     offset, limit, "connections")


# ── The probes and the proposal: each leaves the org ────────────────────────


@annotate(read_only=True, idempotent=True, open_world=True)
async def probe_integration(service: str = "", mcp_server: str = "") -> dict[str, Any]:
    """Test one service or one MCP server of your organization with its stored keys. It never shows a key."""
    svc = str(service or "").strip().lower()
    srv = str(mcp_server or "").strip().lower()
    if bool(svc) == bool(srv):
        return _refusal(_ONE_PROBE)
    if svc and not _SERVICE.fullmatch(svc):
        return _refusal(_BAD_ID.format(what="service id"))
    if srv and not _MCP_NAME.fullmatch(srv):
        return _refusal(_BAD_ID.format(what="MCP server name"))
    try:
        if srv:
            reply = await _send("POST", f"/integrations/mcp/{quote(srv, safe='')}/test",
                                timeout=30.0)
        else:
            reply = await _send("GET", TEST_PATH, params={"service": svc}, timeout=30.0)
    except _Refused as exc:
        return _refusal(str(exc))
    if not reply.ok:
        return _fail(reply)
    if not isinstance(reply.data, dict):
        return _refusal(_SHAPE)
    out: dict[str, Any] = {"ok": True, "passed": reply.data.get("ok") is True}
    if srv:
        out["mcp_server"] = srv
        # The route gives a short code, never a header or a provider message.
        out["error"] = _text(reply.data.get("error"))
        tools = reply.data.get("tools")
        if isinstance(tools, int):
            out["tools"] = tools
    else:
        out["service"] = svc
        out["detail"] = _masked(reply.data.get("detail"))
    return out


@annotate(read_only=True, idempotent=False, open_world=True)
async def discover_api_setup(query: str) -> dict[str, Any]:
    """Ask the model and a web search how to connect an API, by product name. It proposes, and saves nothing."""
    text = query.strip() if isinstance(query, str) else ""
    if not text or len(text) > MAX_QUERY_CHARS or "\n" in text:
        return _refusal(_BAD_QUERY)
    hidden = body_hidden_problem({"query": text})
    if hidden:
        return _refusal(f"{hidden} Nothing was sent.")
    try:
        reply = await _send("POST", DISCOVER_PATH, {"query": text}, timeout=90.0)
    except _Refused as exc:
        return _refusal(str(exc))
    if not reply.ok:
        return _fail(reply)
    found = _list_of(reply, "results")
    if found is None:
        return _refusal(_SHAPE)
    rows = []
    for r in found:
        env = r.get("env_vars")
        rows.append({
            "service_id": _text(r.get("service_id")), "label": _text(r.get("label")),
            "category": _text(r.get("category")),
            "description": _text(r.get("description"), 200),
            "setup_url": _text(r.get("setup_url"), 200),
            "docs_url": _text(r.get("docs_url"), 200),
            "instructions": _text(r.get("instructions"), MAX_TEXT_CHARS),
            "keys": [{"key": _text(v.get("key")), "label": _text(v.get("label")),
                      "secret": v.get("sensitive") is True}
                     for v in env if isinstance(v, dict)] if isinstance(env, list) else [],
        })
    block, said = _frame("API SETUP PROPOSAL", rows, _DISCOVER_NOTE)
    return {"ok": True, "rows": block, "found": len(rows), "note": said,
            "web_search": reply.data.get("web_enhanced") is True}


# ── The writes ──────────────────────────────────────────────────────────────


def _agent_snap(row: dict[str, Any]) -> tuple[Any, ...]:
    """What the card of an agent connection must still match. The token times
    move with each refresh, so they are out."""
    scopes = row.get("scopes")
    return (row.get("member_email"), row.get("client_origin"), row.get("credential"),
            tuple(scopes) if isinstance(scopes, list) else ())


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def revoke_agent_connection(
    connection_id: str, whose: Literal["mine", "org"] = "mine",
) -> dict[str, Any]:
    """Revoke one agent connection after one card: one of your own, or (with whose=org) any of your organization."""
    cid = _uuid_arg(connection_id)
    if cid is None:
        return _refusal(_BAD_ID.format(what="connection id"))
    mine = str(whose or "mine").strip().lower() != "org"
    path = MY_AGENTS_PATH if mine else ORG_AGENTS_PATH
    where = "in your own agent connections" if mine else "in your organization"
    try:
        if not mine:
            refused = await _right_refusal(MANAGE_RIGHT)
            if refused is not None:
                return refused
        row, problem = await _row(path, "connections", "id", cid)
        if problem is not None:
            return problem
        if row is None:
            return _refusal(_NOT_FOUND.format(what="live agent connection", id=cid,
                                              where=where), status=404)
        scopes = row.get("scopes") if isinstance(row.get("scopes"), list) else []
        rows = [
            ("agent", f"Agent: {_fence(row.get('client_origin'))} "
                      f"{_fence(row.get('client_name'))}"),
            ("access", "Access: " + (", ".join(_text(s) for s in scopes) or "none")),
            ("used", f"Last used: {_text(row.get('last_used_at'), 40) or 'never'}"),
        ]
        if not mine:
            rows.insert(1, ("member", f"Member: {_text(row.get('member_email'), 254)}"))
        decision = await _decide(
            f"Revoke the agent connection of {_fence(row.get('client_origin'))}?",
            "revoke the connection",
            "Approve to revoke this connection. The agent loses its access at once, and "
            "each of its tokens stops working. Reject to keep it.", rows)
        if decision != "approved":
            return _not_done(decision)
        again, problem = await _row(path, "connections", "id", cid)
        if problem is not None:
            return {**problem, "decision": decision}
        if again is None or _agent_snap(again) != _agent_snap(row):
            return _refusal(_CHANGED.format(what="connection"), decision=decision)
        if mine:
            sent = await _send("DELETE", f"/agent-access/connections/{cid}", act="revoke")
        else:
            sent = await _send("DELETE", f"/integrations/agent-connections/{cid}",
                               act="revoke")
    except _Refused as exc:
        return _refusal(str(exc))
    if not sent.ok:
        return _fail(sent, write=True, decision=decision)
    return {"ok": True, "decision": decision, "connection_id": cid, "revoked": True,
            "note": "The agent connection is revoked. Its tokens no longer work."}


def _connection_snap(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("status"), row.get("scope"), row.get("owner"), row.get("kind"),
            row.get("provider"), row.get("can_disconnect"))


_DISCONNECT_NOTES = {
    "mcp": ("Approve to remove this MCP server and its stored headers. Each agent that "
            "uses it loses its tools. Reject to keep it."),
    "api_key": ("Approve to delete the stored keys of this service. Each agent that uses "
                "them stops reaching it. Reject to keep them."),
}
_DISCONNECT_NOTE = ("Approve to disconnect this connection. Metorite stops reading it, and "
                    "an admin connects it again to undo this. Reject to keep it.")


async def _disconnect_connection(target: str) -> dict[str, Any]:
    if not _CONNECTION_ID.fullmatch(target) or len(target) > 200:
        return _refusal(_BAD_ID.format(what="connection id"))
    refused = await _right_refusal(MANAGE_RIGHT)
    if refused is not None:
        return refused
    row, problem = await _row(CONNECTIONS_PATH, "connections", "id", target)
    if problem is not None:
        return problem
    if row is None:
        return _refusal(_NOT_FOUND.format(what="connection", id=target,
                                          where="in your organization"), status=404)
    if row.get("scope") != "org":
        return _refusal(_MEMBER_CONNECTION.format(
            app=_text(row.get("owning_app")),
            href=_text(row.get("setup_href"), 200) or "/integrations"), status=409)
    if row.get("can_disconnect") is not True:
        return _refusal(_NOT_REMOVABLE, status=409)
    kind = str(row.get("kind") or "")
    rows = [
        ("connection", f"Connection: {_fence(row.get('label'))} ({_text(row.get('provider'))})"),
        ("app", f"App: {_text(row.get('owning_app'))}. Status: {_text(row.get('status'))}."),
    ]
    if row.get("owner"):
        rows.append(("owner", f"Connected by: {_text(row.get('owner'), 254)}"))
    decision = await _decide(f"Disconnect {_fence(row.get('label'))}?", "disconnect",
                             _DISCONNECT_NOTES.get(kind, _DISCONNECT_NOTE), rows)
    if decision != "approved":
        return _not_done(decision)
    again, problem = await _row(CONNECTIONS_PATH, "connections", "id", target)
    if problem is not None:
        return {**problem, "decision": decision}
    if again is None or _connection_snap(again) != _connection_snap(row):
        return _refusal(_CHANGED.format(what="connection"), decision=decision)
    sent = await _send("POST", f"/integrations/connections/{quote(target, safe=':')}/disconnect",
                       act="disconnect")
    if not sent.ok:
        return _fail(sent, write=True, decision=decision)
    return {"ok": True, "decision": decision, "connection_id": target, "disconnected": True,
            "note": "The connection is disconnected."}


def _plugin_snap(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("name"), row.get("version"), row.get("enabled"), row.get("updated_at"))


async def _remove_plugin(target: str) -> dict[str, Any]:
    pid = _uuid_arg(target)
    if pid is None:
        return _refusal(_BAD_ID.format(what="plugin id"))
    refused = await _right_refusal(MANAGE_RIGHT)
    if refused is not None:
        return refused
    row, problem = await _row(PLUGINS_PATH, None, "id", pid)
    if problem is not None:
        return problem
    if row is None:
        return _refusal(_NOT_FOUND.format(what="plugin", id=pid,
                                          where="in your organization"), status=404)
    tools = row.get("tools_generated")
    count = len(tools) if isinstance(tools, list) else 0
    rows = [
        ("plugin", f"Plugin: {_fence(row.get('label') or row.get('name'))}, "
                   f"version {_text(row.get('version')) or 'none'}"),
        ("tools", f"Tools: {count}"),
    ]
    decision = await _decide(f"Remove the plugin {_fence(row.get('name'))}?", "remove",
                             "Approve to remove this plugin and its tools. Reject to keep it.",
                             rows)
    if decision != "approved":
        return _not_done(decision)
    again, problem = await _row(PLUGINS_PATH, None, "id", pid)
    if problem is not None:
        return {**problem, "decision": decision}
    if again is None or _plugin_snap(again) != _plugin_snap(row):
        return _refusal(_CHANGED.format(what="plugin"), decision=decision)
    sent = await _send("DELETE", f"/integrations/plugins/{pid}", act="removal")
    if not sent.ok:
        return _fail(sent, write=True, decision=decision)
    return {"ok": True, "decision": decision, "plugin_id": pid, "removed": True,
            "note": "The plugin is removed."}


def _github_snap(data: dict[str, Any]) -> tuple[Any, ...]:
    return (data.get("connected"), data.get("status"), data.get("login"),
            data.get("connected_by"))


async def _github_account() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    reply = await _get(GITHUB_ACCOUNT_PATH)
    if not reply.ok:
        return None, _fail(reply, write=True)
    if not isinstance(reply.data, dict):
        return None, _refusal(_SHAPE + " Nothing changed.")
    return reply.data, None


def _github_page(url: Any) -> str:
    """The installation page of the GitHub App that the route answers, for the
    member's own browser, or "". Only an https page of github.com passes, with
    no user, port, query or fragment, so no token rides in it
    (``github_app.installation_settings_url`` builds no query today)."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return ""
    if (parts.scheme != "https" or parts.hostname != "github.com" or parts.username
            or parts.password or port is not None or not parts.path.startswith("/")):
        return ""
    path = parts.path
    if not re.fullmatch(r"(?:/[A-Za-z0-9_.-]+)+", path) or ".." in path:
        return ""
    return f"https://github.com{path}"


async def _disconnect_github() -> dict[str, Any]:
    refused = await _right_refusal(MANAGE_RIGHT)
    if refused is not None:
        return refused
    account, problem = await _github_account()
    if problem is not None or account is None:
        return problem or _refusal(_SHAPE)
    if account.get("connected") is not True:
        return _refusal(_NO_GITHUB, status=404)
    rows = [("account", f"GitHub account: {_fence(account.get('login'))}"),
            ("by", f"Connected by: {_text(account.get('connected_by'), 254) or 'unknown'}")]
    decision = await _decide(
        "Disconnect GitHub?", "disconnect GitHub",
        "Approve to delete the stored GitHub connection of the organization. Agents stop "
        "reading the repositories. Metorite cannot revoke a pasted token at GitHub, so the "
        "answer names the page where you revoke it. Reject to keep it.", rows)
    if decision != "approved":
        return _not_done(decision)
    again, problem = await _github_account()
    if problem is not None or again is None:
        return {**(problem or _refusal(_SHAPE)), "decision": decision}
    if _github_snap(again) != _github_snap(account):
        return _refusal(_CHANGED.format(what="GitHub connection"), decision=decision)
    sent = await _send("DELETE", GITHUB_PATH, act="disconnect")
    if not sent.ok:
        return _fail(sent, write=True, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    out = {"ok": True, "decision": decision, "disconnected": True,
           "note": _text(data.get("note"), MAX_TEXT_CHARS) or "GitHub is disconnected."}
    page = _github_page(data.get("installation_url"))
    if page:
        out["installation_url"] = page
    return out


@annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def disconnect_integration(
    kind: Literal["connection", "plugin", "github"], target: str = "",
) -> dict[str, Any]:
    """Disconnect one org connection, remove one plugin, or disconnect GitHub, after one card."""
    want = str(kind or "").strip().lower()
    if want not in DISCONNECT_KINDS:
        return _refusal(_BAD_KIND.format(kinds=", ".join(DISCONNECT_KINDS)))
    name = str(target or "").strip()
    try:
        if want == "github":
            return await _disconnect_github()
        if want == "plugin":
            return await _remove_plugin(name)
        return await _disconnect_connection(name)
    except _Refused as exc:
        return _refusal(str(exc))


def _server_snap(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("always_confirm"), row.get("read_only_key"), row.get("description"),
            row.get("enabled"), row.get("updated_at"))


def _switch_word(value: Any) -> str:
    return "on" if value is True else "off"


def _mcp_card(row: dict[str, Any], body: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    """``(note, rows)`` of the card of a change of an MCP server. The body
    never lowers safety (:func:`_mcp_body` refuses that), so the card warns of
    nothing."""
    rows = [("server", f"Server: {_text(row.get('name'))} {_fence(row.get('label'))}")]
    if "always_confirm" in body:
        rows.append(("confirm", f"Always confirm: {_switch_word(row.get('always_confirm'))} "
                                f"to {_switch_word(body['always_confirm'])}"))
    if "read_only_key" in body:
        rows.append(("readonly", f"Read-only key: {_switch_word(row.get('read_only_key'))} "
                                 f"to {_switch_word(body['read_only_key'])}"))
    if "description" in body:
        rows.append(("description", "Description after: " + (body["description"] or "none")))
    return "Approve to save this change. Reject to leave the server as now.", rows


def _mcp_body(always_confirm: Any, read_only_key: Any,
              description: Any) -> dict[str, Any] | str:
    """The body of the PATCH, or the words of a refusal.

    ``always_confirm`` false and ``read_only_key`` true lower safety, so each
    is refused here, before a card or a request (:data:`_LOWERS`)."""
    body: dict[str, Any] = {}
    for key, raw in (("always_confirm", always_confirm), ("read_only_key", read_only_key)):
        value = _bool_arg(raw)
        if value == "bad":
            return f"{key} is true or false. Nothing changed."
        if value is not None:
            body[key] = value
    if body.get("always_confirm") is False or body.get("read_only_key") is True:
        return _LOWERS
    if description is not None:
        if not isinstance(description, str) or len(description.strip()) > MAX_MCP_DESCRIPTION:
            return (f"description is one text of at most {MAX_MCP_DESCRIPTION} characters. "
                    "Nothing changed.")
        hidden = body_hidden_problem({"description": description})
        if hidden:
            return f"{hidden} Nothing changed."
        body["description"] = " ".join(description.split())
    return body if body else _NO_SWITCH


@annotate(read_only=False, idempotent=False, open_world=False)
async def set_mcp_server(
    name: str, always_confirm: bool | None = None, read_only_key: bool | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Turn always confirm on, turn the read-only key off, or set the description of one MCP server, after one card. The chat never turns always confirm off or the read-only key on."""
    server = str(name or "").strip().lower()
    if not _MCP_NAME.fullmatch(server):
        return _refusal(_BAD_ID.format(what="MCP server name"))
    body = _mcp_body(always_confirm, read_only_key, description)
    if isinstance(body, str):
        return _refusal(body)
    try:
        refused = await _right_refusal(MANAGE_RIGHT)
        if refused is not None:
            return refused
        row, problem = await _row(MCP_PATH, None, "name", server)
        if problem is not None:
            return problem
        if row is None:
            return _refusal(_NOT_FOUND.format(what="MCP server", id=server,
                                              where="in your organization"), status=404)
        after_confirm = body.get("always_confirm", row.get("always_confirm") is True)
        after_readonly = body.get("read_only_key", row.get("read_only_key") is True)
        if after_confirm and after_readonly:
            return _refusal(_BOTH_ON, status=422)
        if all(row.get(k) == v for k, v in body.items()):
            return {"ok": True, "name": server, "note": _SAME_SERVER}
        note, rows = _mcp_card(row, body)
        decision = await _decide(f"Change the MCP server {server}?", "change the server",
                                 note, rows)
        if decision != "approved":
            return _not_done(decision)
        again, problem = await _row(MCP_PATH, None, "name", server)
        if problem is not None:
            return {**problem, "decision": decision}
        if again is None or _server_snap(again) != _server_snap(row):
            return _refusal(_CHANGED.format(what="MCP server"), decision=decision)
        sent = await _send("PATCH", f"/integrations/mcp/{quote(server, safe='')}", body,
                           act="change")
    except _Refused as exc:
        return _refusal(str(exc))
    if not sent.ok:
        return _fail(sent, write=True, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    return {"ok": True, "decision": decision, "name": server,
            **{k: data[k] for k in ("always_confirm", "read_only_key") if k in data},
            "note": "The MCP server is changed."}


#: Each tool of PAR-6a, in the order of its skill family. The ``org-admin``
#: pack holds them after the member tools of ``org_admin_tools``.
INTEGRATIONS_ADMIN_TOOLS = (
    read_integrations, read_agent_access, probe_integration, discover_api_setup,
    revoke_agent_connection, disconnect_integration, set_mcp_server,
)
