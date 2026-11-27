"""The Agent Registry and its office view, from the chat (PAR-10 part a, task 3970).

Spec: ``project-docs/specs/chat_parity.md``, wave P-6. The Agent Registry
(``/agents``) lists the agents, shows the skill switches and the self-mutation
queue of an agent, and picks the avatar of an agent. Its office view
(``/observability``) shows the roster, the runs, the cost and the activity.
Seven tools do the same from the chat:

* :func:`agent_registry_list` reads the agents (``GET /agent``).
* :func:`agent_registry_skills` reads the skill switches of one agent
  (``GET /agent/{name}/skills``).
* :func:`agent_registry_config` reads the ``config.json`` of an agent
  repository on GitHub, as the Add Agent form does (``GET /agent/config``).
* :func:`agent_registry_mutations` reads the self-mutation queue
  (``GET /agent/mutations``), or the diff of one pending commit
  (``GET /agent/mutations/pending/{id}/diff``).
* :func:`agent_registry_office` reads the roster, the cost, the recent
  activity or the avatars (``GET /observability/...``).
* :func:`agent_registry_runs` reads the run history (``GET /observability/runs``).
* :func:`agent_registry_set_avatar` gives an agent a character of the library
  (``PUT /observability/avatars/{name}``), with the body that the avatar
  picker sends.

The rules:

* **The route decides.** Each tool sends the route of the UI over HTTP as the
  verified member of the run (``chat_pin_tools._member``): the internal
  bearer and ``X-User-Email``, and no org. So the gate of each route applies.
  The list shows only the agents that the member may run, and the mutation
  queue needs ``agents:manage`` and an org that may review. No tool opens a
  database session or writes a Redis key.
* **No member and no org from the model (R5).** No tool takes an org, a
  member or an address argument. An agent name is only a key: a tool that
  names one agent finds it in the member's own list first.
* **No more than the UI.** The routes take any member, and only the page
  checks its feature. So each tool that only a page reads checks the
  feature of that page in ``GET /auth/me`` first, and fails closed:
  ``feature:agents`` for the skills, the config, the queue and the avatar
  write, and ``feature:observability`` for the roster, the cost, the
  activity and the runs. The list and the avatars need no feature, because
  each app reads them. The config read takes an ``owner/repo`` slug of
  GitHub only. It never sends a path on the server. A skill read and an
  avatar write name only an agent of the member's list. The cost read leaves
  out the cost of each person, which the page does not show.
* **No secret reaches the model.** A key that names a secret is left out
  (``secret_shapes.is_secret_key``). Each text loses each credential shape of
  the one list (``secret_shapes``). A route error loses each link, key and
  path (``tool_refusals.scrub_text``). A local path, a sprite and a repo URL
  are not shown.
* **Text of other people is data.** Agent names, descriptions, commit
  messages, diffs, run errors and config files come back in ONE
  ``UntrustedFrame`` block, with no hidden character. A read with rows marks
  the chat as one that read outside text (``egress.mark_untrusted_answer``).
* **Each read pages.** A list goes through ``acb_common.paging``. A diff
  pages by line. Each object inside a value keeps 20 keys, and a marker says
  how many went. A framed block holds at most :data:`MAX_BLOCK_CHARS`
  characters, and a cut ends with :data:`CUT_MARK`.
* **The card.** The avatar write shows ONE card where ``chat_pin_tools._must_ask``
  says so, and always when the agent has a custom picture that the write
  removes. Only an approve sends. After the approve the tool reads the avatars
  again, and sends nothing when the avatar changed.
* **Dark.** The tools are the pack ``agent-registry``, owned by ``org-admin``.
  It is in no ``FOLLOWS`` entry, so it is off until the owner names it in the
  pack list of the box (``tool_packs.packs_on``).

Fence: ``tests/unit/test_agent_registry_read_tools.py``.
"""

from __future__ import annotations

import json
import re
import textwrap
import uuid
from typing import Any
from urllib.parse import quote

import httpx
from acb_common import get_logger
from acb_common.card_body import one_line
from acb_common.paging import paged, prefix_limit, prefix_page
from acb_common.secret_shapes import (
    REDACTION_BEARER,
    REDACTION_SHAPES,
    SECRET_SHAPES,
    is_secret_field_key,
    is_secret_key,
)
from acb_common.settings import internal_auth_headers
from acb_common.tool_refusals import safe_detail, scrub_text

from acb_skills import chat_pin_tools as _pin
from acb_skills.company_search_tools import _gateway_url
from acb_skills.hidden_text import strip_hidden
from acb_skills.tool_annotations import annotate

__all__ = [
    "AGENT_REGISTRY_TOOLS",
    "agent_registry_config",
    "agent_registry_list",
    "agent_registry_mutations",
    "agent_registry_office",
    "agent_registry_runs",
    "agent_registry_set_avatar",
    "agent_registry_skills",
]

_log = get_logger("acb_skills.agent_registry_tools")

#: The gateway routes. A package must not import the gateway, so the paths
#: are text here. ``test_chat_parity.py`` reads them from this source.
AGENTS_PATH = "/agent"
CONFIG_PATH = "/agent/config"
MUTATIONS_PATH = "/agent/mutations"
ROSTER_PATH = "/observability/roster"
COST_PATH = "/observability/cost"
ACTIVITY_PATH = "/observability/activity/recent"
AVATARS_PATH = "/observability/avatars"
RUNS_PATH = "/observability/runs"
#: The resolved rights of the member (``gateway.routes.admin.me.get_me``).
ME_PATH = "/auth/me"

#: The feature of each page. The routes take any member, but the pages are
#: for a member with the feature. So a tool that only a page reads needs it.
REGISTRY_FEATURE = "agents"
OFFICE_FEATURE = "observability"

#: The longest wait for a route, in seconds. The config read waits on GitHub.
TIMEOUT_S = 15.0
#: The rows of one page, by default and at most.
DEFAULT_ROWS = 25
MAX_ROWS = 50
#: The lines of one diff page, by default and at most, and the longest line.
DIFF_LINES = 200
MAX_DIFF_LINES = 400
MAX_DIFF_LINE_CHARS = 300
#: The longest text of one value, of a commit message and of a route error.
MAX_FIELD_CHARS = 300
MAX_LONG_CHARS = 2000
MAX_ERROR_CHARS = 300
#: The most items of one list inside a value, and the deepest nesting.
MAX_LIST_ITEMS = 20
MAX_DEPTH = 4
#: The most keys of one object inside a value. The keys after it go, and one
#: marker key says how many went.
MAX_KEYS = MAX_LIST_ITEMS
MORE_KEY = "(more)"
#: The longest JSON text of one framed block. A longer text is cut, and the
#: cut ends with :data:`CUT_MARK`, so the model never reads a cut as whole.
MAX_BLOCK_CHARS = 20000
CUT_MARK = "... [CUT: the result was longer than {limit} characters. Ask for less.]"
#: The LIMIT caps of the routes: ``/agent/mutations`` and ``/observability/runs``
#: clamp at 200, and ``/observability/activity/recent`` at 500.
MUTATIONS_CAP = 200
RUNS_CAP = 200
ACTIVITY_CAP = 500
#: The days of the cost read, as the route clamps them.
MIN_DAYS = 1
MAX_DAYS = 90
#: The run statuses that the route filters by (``observability._RUN_STATUSES``).
RUN_STATUSES = ("running", "completed", "error", "cancelled")
#: The most hours of the run filter: one year. The route sends the value to
#: ``make_interval(hours => :sh)``, whose argument is a 32-bit integer, so a
#: larger value fails there and the page reads it as no runs.
MAX_SINCE_HOURS = 24 * 366

#: The name rule of an agent, a copy of ``observability._AGENT_NAME_RE``. The
#: fence holds the two equal.
AGENT_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_AGENT_NAME = re.compile(AGENT_NAME_PATTERN)
#: A GitHub ``owner/repo`` slug. The tool sends nothing else to the config route.
_REPO_SLUG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")

#: The characters of the avatar library, a copy of the keys of
#: ``CHARACTER_LIBRARY`` in ``app/observability/character-library.generated.ts``.
#: The fence holds the two equal.
CHARACTERS: tuple[str, ...] = (
    "data-f", "designer-f", "designer-m", "engineer-f", "engineer-m", "exec-f", "exec-m",
    "finance-f", "finance-m", "hr-f", "hr-m", "marketing-f", "marketing-m", "ops-f", "ops-m",
    "product-f", "product-m", "sales-f", "sales-m", "test-coder", "orchestrator",
    "apis-config", "sales", "task-manager", "email-assistant", "reconciler", "delivery",
    "billing", "strategy",
)

_OFFICE_VIEWS = ("roster", "cost", "activity", "avatars")

#: Each credential shape of the one list (``acb_common.secret_shapes``).
_SECRET_RX = (REDACTION_BEARER, *REDACTION_SHAPES, *(rx for _, rx in SECRET_SHAPES))
#: The user and the password of a link (``https://user:pass@host``).
_LINK_LOGIN = re.compile(r"(://)[^/\s:@]{1,256}:[^/\s@]{1,512}@")
#: An assignment in a line of code or config: the key, then ``=`` or ``:``.
_ASSIGNMENT = re.compile(r"^(\W{0,40}?)([A-Za-z_][\w.-]{0,80})([\"']?\s{0,8}[:=]\s{0,8})(.+)$")
#: A value that holds no secret: a number, a switch, null, or an empty text.
_PLAIN_VALUE = re.compile(r"^(?:-?\d{1,20}(?:\.\d{1,20})?|true|false|True|False|null|None|"
                          r"\"\"|''|\{|\[)?$")
_REMOVED = "<secret removed>"

_ON_WHATSAPP = ("Nothing was done. The Agent Registry belongs to the Metorite web app. Open "
                "the assistant there to use it.")
_NO_MEMBER = ("Nothing was done. Metorite could not confirm who asked in this chat. Use the "
              "Agent Registry page.")
_SHARED_ROOM = ("Nothing was done. Each person in a shared chat sees the answer. Ask in your "
                "own chat.")
_BAD_AGENT = ("Send the name of an agent as agent_registry_list shows it. Nothing was "
              "done.")
_NOT_LISTED = ("Your Agent Registry holds no agent with that name. Read agent_registry_list "
               "for the names. Nothing was done.")
_BAD_REPO = ("Send a GitHub repository as owner/repo or as its github.com link. The tool "
             "reads no path on the server. Nothing was read.")
_BAD_ID = ("Send the id of a pending commit as agent_registry_mutations shows it. Nothing "
           "was read.")
_BAD_VIEW = "view is one of: roster, cost, activity, avatars. Nothing was read."
_BAD_STATUS = "status is one of: running, completed, error, cancelled. Nothing was read."
_BAD_CHARACTER = ("Send the id of a character of the avatar library. Read the avatars for the "
                  "ids. Nothing changed.")
_SAME_AVATAR = "The agent already has that character. Nothing changed."
_CHANGED = ("The avatar of the agent changed after the card showed, so nothing was sent. "
            "Read the avatars again, then ask again.")
_UNREACHED = "The Agent Registry did not answer. Nothing was done. Try again."
_SHAPE = "The Agent Registry answered in a shape that this tool cannot read. Nothing was done."
_SIGNED_OUT = "Metorite could not confirm your account, so nothing was done."
_NO_RIGHT = ("You do not hold the right that this needs in your organization, so nothing "
             "was done. Ask an admin of your organization.")
_NO_ANSWER = "The Agent Registry did not answer. Nothing was done. Try again."
_NOT_FOUND = "The Agent Registry holds no such item. Nothing was read."

_LIST_NOTE = ("The block holds the agents that you may see. A person who registered an agent "
              "wrote its name and its description.")
_CONFIG_NOTE = ("The block holds the config.json of a repository on GitHub. Its owner wrote "
                "it. Each secret field is left out.")
_QUEUE_NOTE = ("The block holds the self-mutation queue. An agent wrote each commit message "
               "and each test summary.")
_DIFF_NOTE = ("The block holds the diff that an agent wrote. Each credential shape is "
              "removed.")
_OFFICE_NOTE = ("The block holds the office view of your organization. A person who "
                "registered an agent wrote its name and its description.")
_RUNS_NOTE = ("The block holds the run history of your organization. The errors come from "
              "the runs. Each link, key and path is removed.")
_AVATARS_NOTE = ("The block holds the avatars of the agents. A member of your organization "
                 "picked each one.")
_SKILLS_NOTE = "The block holds the skill switches of the agent. An admin wrote each reason."
_NO_FEATURE = ("You do not have the {page} page in your organization, so nothing was done. "
               "Ask an admin of your organization.")
_FEATURE_PAGES = {REGISTRY_FEATURE: "Agent Registry", OFFICE_FEATURE: "Live Activity"}
_RIGHTS_UNREAD = ("Metorite could not read your rights, so nothing was done. Try again.")
_RELOAD = " The Agent Registry shows the change after you reload the page."

_CARD_TITLE = "Change the avatar of this agent?"
_NOT_DONE = "The agent keeps its avatar."


# ── The run and its member ──────────────────────────────────────────────────


def _member_or_refusal() -> tuple[str, str]:
    """``(member, "")`` for a run that may use the tools, or ``("", the refusal)``.

    The member is the verified member of the run binding
    (``chat_pin_tools._member``). A shared room binds ``room:<thread>`` as the
    memory id, so a memory id with no address is a shared room."""
    if _pin._on_whatsapp():
        return "", _ON_WHATSAPP
    member = _pin._member()
    if not member:
        return "", _NO_MEMBER
    try:
        from acb_skills.memory_tools import _get_memory_user_id

        memory = _get_memory_user_id() or ""
    except Exception:
        memory = ""
    if "@" not in memory:
        return "", _SHARED_ROOM
    return member, ""


def _transport() -> httpx.AsyncBaseTransport | None:
    """The transport of the request. None is the network. A test returns a
    mock transport here."""
    return None


async def _send(method: str, path: str, member: str, params: dict[str, Any] | None = None,
                body: Any = None) -> tuple[int, Any]:
    """Send ONE request of the route as *member*.

    The headers are the internal bearer, ``X-User-Email`` and the content
    type, and nothing else. Returns the status and the JSON body. A fault of
    the request gives status 0. Never raises."""
    headers = {**internal_auth_headers(), "X-User-Email": member,
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(base_url=_gateway_url(), transport=_transport(),
                                     timeout=TIMEOUT_S) as client:
            resp = await client.request(method, path, headers=headers, params=params or None,
                                        json=body)
    except Exception as exc:
        _log.warning("agent_registry_tool.request_failed", error=type(exc).__name__)
        return 0, None
    try:
        data = resp.json()
    except Exception:
        data = None
    return int(resp.status_code), data


# ── The plain words ─────────────────────────────────────────────────────────


def _refusal(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _scrub(text: str) -> str:
    """*text* with each credential shape of the one list removed, the login of
    a link removed, and no hidden character."""
    out = strip_hidden(text)
    out = _LINK_LOGIN.sub(r"\1" + _REMOVED + "@", out)
    for rx in _SECRET_RX:
        out = rx.sub(_REMOVED, out)
    return out


def _scrub_line(line: str) -> str:
    """One line of code or config, with each credential shape removed, and the
    value of a key that names a secret removed."""
    out = _scrub(line)
    hit = _ASSIGNMENT.match(out)
    if hit and is_secret_key(hit.group(2)) and _REMOVED not in hit.group(4):
        value = hit.group(4).strip().strip(",;").strip()
        # A number, a switch or an empty value holds no secret (``max_tokens: 100``).
        if not _PLAIN_VALUE.match(value):
            out = hit.group(1) + hit.group(2) + hit.group(3) + _REMOVED
    return out


def _cut(text: str, width: int) -> str:
    """*text* cut at *width* characters, with its spaces kept. A cut ends with "..."."""
    if len(text) <= width:
        return text
    cut = textwrap.wrap(text, width, max_lines=1, placeholder="...",
                        replace_whitespace=False, drop_whitespace=False)
    return cut[0] if cut else "..."


def _line(value: Any, width: int = MAX_FIELD_CHARS) -> str:
    """*value* as one clean line of at most *width* characters."""
    text = one_line(_scrub(str(value if value is not None else "")))
    if len(text) <= width:
        return text
    return textwrap.shorten(text, width=width, placeholder="...")


def _long(value: Any, width: int = MAX_LONG_CHARS) -> str:
    """A long text with its line breaks, clean, cut at *width* characters."""
    text = "\n".join(_scrub_line(x) for x in str(value or "").splitlines()).strip()
    return _cut(text, width)


def _error(value: Any) -> str:
    """The text of a run error: each link, key, address and path removed
    (``tool_refusals.scrub_text``), one line."""
    return scrub_text(_scrub(str(value or "")), limit=MAX_ERROR_CHARS)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _fold(value: Any, depth: int = 0) -> Any:
    """*value* with each text clean and on one line, each list paged, and each
    key that names a secret, and each field with no value, left out. The key
    rule is the narrow one (``is_secret_field_key``), so ``tokens`` of a cost
    stays. A config file takes the wide rule (:func:`_clean_config`)."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _line(value)
    if depth >= MAX_DEPTH:
        return _deep_line(value)
    if isinstance(value, dict):
        kept = [(k, v) for k, v in value.items()
                if not _empty(v) and not is_secret_field_key(k)]
        return _capped(kept, lambda v: _fold(v, depth + 1))
    if isinstance(value, (list, tuple)):
        page = paged(list(value), 0, MAX_LIST_ITEMS, default=MAX_LIST_ITEMS,
                     maximum=MAX_LIST_ITEMS)
        out = [_fold(v, depth + 1) for v in page.items]
        if page.total > len(page.items):
            out.append(f"(+{page.total - len(page.items)} more)")
        return out
    return _line(value)


def _no_secret_keys(value: Any) -> Any:
    """*value* with each key that names a secret (the wide rule,
    ``is_secret_key``) removed in the WHOLE subtree. Run it before any dump,
    so no depth lets a secret key through."""
    if isinstance(value, dict):
        return {k: _no_secret_keys(v) for k, v in value.items() if not is_secret_key(k)}
    if isinstance(value, (list, tuple)):
        return [_no_secret_keys(v) for v in value]
    return value


def _deep_line(value: Any) -> str:
    """A value past :data:`MAX_DEPTH` as one line. The secret keys of the whole
    subtree go first, then the dump, then the scrub of the full text
    (:func:`_line`), and the cut comes last."""
    return _line(json.dumps(_no_secret_keys(value), ensure_ascii=False, default=str))


def _capped(pairs: list[tuple[Any, Any]], clean: Any) -> dict[str, Any]:
    """The first :data:`MAX_KEYS` pairs as an object, each value through
    *clean*. Only those values are cleaned. When more pairs exist, the key
    :data:`MORE_KEY` says how many went."""
    page = paged(pairs, 0, MAX_KEYS, default=MAX_KEYS, maximum=MAX_KEYS)
    out = {_line(k, 64): clean(v) for k, v in page.items}
    if page.total > len(page.items):
        out[MORE_KEY] = f"+{page.total - len(page.items)} more keys"
    return out


def _pick(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """The named fields of one row, folded, with no empty value. A field that
    the list does not name never passes."""
    if not isinstance(row, dict):
        return {}
    return {k: _fold(row[k]) for k in fields if not _empty(row.get(k))}


def _block(label: str, rows: Any) -> tuple[str, Any]:
    """``(the framed block, the frame)``: ONE ``UntrustedFrame`` block."""
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    text = _cap_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str))
    return frame.wrap(label, text), frame


def _cap_text(text: str) -> str:
    """*text* cut at :data:`MAX_BLOCK_CHARS`, with :data:`CUT_MARK` at the end."""
    if len(text) <= MAX_BLOCK_CHARS:
        return text
    return str(text)[:MAX_BLOCK_CHARS] + CUT_MARK.format(limit=MAX_BLOCK_CHARS)


async def _mark_read() -> None:
    """Mark the chat of the run: it read text that other people wrote. The
    tenant and the session come from the run binding only."""
    from acb_skills.egress import mark_untrusted_answer

    await mark_untrusted_answer()


async def _framed(label: str, rows: Any, key: str, note: str, **extra: Any) -> dict[str, Any]:
    if not _empty(rows):
        await _mark_read()
    block, frame = _block(label, rows)
    return {"ok": True, key: block, **extra, "note": frame.note(note)}


def _fail(status: int, body: Any, member: str) -> dict[str, Any]:
    """The refusal of a route in plain words. A route sentence loses each
    link, key, address and path (``tool_refusals.safe_detail``)."""
    if status == 0:
        return _refusal(_UNREACHED)
    if status == 401:
        return _refusal(_SIGNED_OUT, status=401)
    # A hidden character inside a key hides the key from the scrub of
    # ``safe_detail``. So each text of the body loses its hidden characters
    # first, and the detail takes the scrub of the shape list again after.
    detail, _fields = safe_detail(status, _unhidden(body), member, limit=MAX_ERROR_CHARS)
    detail = _scrub(detail)
    if status == 403:
        return _refusal(f"{_NO_RIGHT} {detail}".strip(), status=403)
    if detail:
        return _refusal(f"Nothing was done. {detail}", status=status)
    return _refusal(_NO_ANSWER, status=status)


async def _feature_refusal(member: str, feature: str) -> dict[str, Any] | None:
    """The plain refusal of a member without ``feature:<feature>``, or None.

    It reads ``GET /auth/me``, the resolved rights of the server, so the tool
    keeps no rule of its own. The routes of the Agent Registry and of its
    office view take any member, and only the page checks the feature. So
    the tool checks it too, and is no wider than the page. A failed read
    refuses, so it fails closed."""
    status, body = await _send("GET", ME_PATH, member)
    features = body.get("features") if isinstance(body, dict) else None
    if status != 200 or not isinstance(features, list):
        return _refusal(_RIGHTS_UNREAD)
    if feature not in features:
        return _refusal(_NO_FEATURE.format(page=_FEATURE_PAGES[feature]), status=403)
    return None


def _unhidden(value: Any) -> Any:
    """*value* with each text in it free of hidden characters."""
    if isinstance(value, str):
        return strip_hidden(value)
    if isinstance(value, dict):
        return {_unhidden(k): _unhidden(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_unhidden(v) for v in value]
    return value


def _q(value: str) -> str:
    """*value* as one path segment."""
    return quote(value, safe="")


def _agent_name(raw: Any) -> str:
    """The agent name as the route reads it (``strip().lower()``), or ""."""
    name = str(raw or "").strip().lower()
    return name if _AGENT_NAME.match(name) else ""


async def _agents(member: str) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """The agents of the member's list, or ``(None, the refusal)``."""
    status, body = await _send("GET", AGENTS_PATH, member)
    if status != 200:
        return None, _fail(status, body, member)
    if not isinstance(body, list):
        return None, _refusal(_SHAPE)
    return [a for a in body if isinstance(a, dict) and isinstance(a.get("name"), str)], None


async def _listed(member: str, raw: Any) -> tuple[str, dict[str, Any] | None]:
    """``(name, None)`` for an agent of the member's list, or ``("", the refusal)``."""
    name = _agent_name(raw)
    if not name:
        return "", _refusal(_BAD_AGENT)
    agents, refused = await _agents(member)
    if agents is None:
        return "", refused
    if not any(a.get("name") == name for a in agents):
        return "", _refusal(_NOT_LISTED)
    return name, None


# ── agent_registry_list ─────────────────────────────────────────────────────


_AGENT_FIELDS = ("name", "display_name", "description", "tags", "status", "agent_runtime",
                 "integrations", "optional_integrations", "repo_name", "dynamic",
                 "tier_routed", "behind_by")


def _agent_row(a: dict[str, Any]) -> dict[str, Any]:
    """One agent of the list. A local path, a repo URL and the text of a
    failed install stay out."""
    row = _pick(a, _AGENT_FIELDS)
    deps = a.get("dep_status")
    if isinstance(deps, dict):
        row["dependencies_ok"] = deps.get("ok") is True
        if not _empty(deps.get("needs_system_packages")):
            row["needs_system_packages"] = _fold(deps.get("needs_system_packages"))
    return row


@annotate(read_only=True, idempotent=True, open_world=False)
async def agent_registry_list(offset: int = 0, limit: int = DEFAULT_ROWS) -> dict[str, Any]:
    """List the agents of the Agent Registry that you may see, with the name, the description and the runtime of each.

    Args:
        offset: the first row of the page.
        limit: the rows of one page.
    """
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    agents, why = await _agents(member)
    if agents is None:
        return why or _refusal(_NO_ANSWER)
    page = paged(agents, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="agents")
    return await _framed("AGENT REGISTRY", [_agent_row(a) for a in page.items], "agents",
                         _LIST_NOTE, total=page.total, shown=len(page.items), page=page.line)


# ── agent_registry_skills ───────────────────────────────────────────────────


@annotate(read_only=True, idempotent=True, open_world=False)
async def agent_registry_skills(agent: str) -> dict[str, Any]:
    """Read the skill switches of one agent: which skill families an admin turned off, and why.

    Args:
        agent: the name of the agent, as agent_registry_list shows it.
    """
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    no_page = await _feature_refusal(member, REGISTRY_FEATURE)
    if no_page is not None:
        return no_page
    name, bad = await _listed(member, agent)
    if bad is not None:
        return bad
    status, body = await _send("GET", f"/agent/{_q(name)}/skills", member)
    if status == 404:
        return _refusal(_NOT_LISTED, status=404)
    if status != 200:
        return _fail(status, body, member)
    if not isinstance(body, dict) or not isinstance(body.get("settings"), list):
        return _refusal(_SHAPE)
    rows = [_pick(s, ("family", "enabled", "reason", "set_by", "set_at"))
            for s in body["settings"] if isinstance(s, dict)]
    page = paged(rows, 0, MAX_ROWS, default=MAX_ROWS, maximum=MAX_ROWS, noun="switches")
    shown = {"agent": name, "switches": page.items,
             "disabled_families": _fold(body.get("disabled_families") or []),
             "always_on": _fold(sorted((body.get("non_toggleable") or {}).keys())
                                if isinstance(body.get("non_toggleable"), dict) else [])}
    return await _framed("AGENT SKILLS", shown, "skills", _SKILLS_NOTE, total=page.total,
                         page=page.line)


# ── agent_registry_config ───────────────────────────────────────────────────


def _repo_slug(raw: Any) -> str:
    """The ``owner/repo`` slug of a GitHub repository, or "". A path on the
    server, a link to another host or a slug with more parts gives ""."""
    text = str(raw or "").strip().rstrip("/")
    for head in ("https://github.com/", "http://github.com/", "github.com/"):
        if text.lower().startswith(head):
            text = text[len(head):]
            break
    text = text.removesuffix(".git")
    return text if _REPO_SLUG.match(text) else ""


def _clean_config(value: Any, depth: int = 0) -> Any:
    """A config file with each secret key left out and each text clean. Lists
    page, each object keeps :data:`MAX_KEYS` keys, and nesting stops at
    :data:`MAX_DEPTH`."""
    if isinstance(value, dict) and depth < MAX_DEPTH:
        kept = [(k, v) for k, v in value.items() if not is_secret_key(k)]
        return _capped(kept, lambda v: _clean_config(v, depth + 1))
    if isinstance(value, str):
        return _long(value, MAX_FIELD_CHARS)
    if depth >= MAX_DEPTH and isinstance(value, (dict, list, tuple)):
        return _deep_line(value)
    return _fold(value, depth)


@annotate(read_only=True, idempotent=True, open_world=True)
async def agent_registry_config(repo: str) -> dict[str, Any]:
    """Read the config.json of an agent repository on GitHub, as the Add Agent form reads it. It registers nothing.

    Args:
        repo: the repository as owner/repo, or its github.com link.
    """
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    slug = _repo_slug(repo)
    if not slug:
        return _refusal(_BAD_REPO)
    no_page = await _feature_refusal(member, REGISTRY_FEATURE)
    if no_page is not None:
        return no_page
    status, body = await _send("GET", CONFIG_PATH, member, params={"repo": slug})
    if status == 404:
        return {"ok": True, "found": False, "repo": slug,
                "answer": "The repository has no config.json that Metorite can read."}
    if status != 200:
        return _fail(status, body, member)
    if not isinstance(body, dict):
        return _refusal(_SHAPE)
    # The secret keys of the whole file go first, at each depth, before any
    # fold or dump.
    return await _framed("AGENT CONFIG", _clean_config(_no_secret_keys(body)), "config",
                         _CONFIG_NOTE,
                         found=True, repo=slug)


# ── agent_registry_mutations ────────────────────────────────────────────────


_MUTATION_FIELDS = ("type", "id", "pending_commit_id", "agent", "run_id", "commit_sha",
                    "status", "reviewed_by", "reviewed_at", "at", "mutation_mode", "pr_url",
                    "target_path")


def _mutation_row(r: dict[str, Any]) -> dict[str, Any]:
    row = _pick(r, _MUTATION_FIELDS)
    for key in ("commit_message", "test_summary"):
        if not _empty(r.get(key)):
            row[key] = _long(r[key], MAX_FIELD_CHARS)
    return row


def _commit_id(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) != 36:
        return ""
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return ""


async def _diff(member: str, raw_id: Any, line_offset: int) -> dict[str, Any]:
    cid = _commit_id(raw_id)
    if not cid:
        return _refusal(_BAD_ID)
    status, body = await _send("GET", f"/agent/mutations/pending/{_q(cid)}/diff", member)
    if status == 404:
        return _refusal(_NOT_FOUND, status=404)
    if status != 200:
        return _fail(status, body, member)
    if not isinstance(body, dict):
        return _refusal(_SHAPE)
    lines = [_cut(_scrub_line(x), MAX_DIFF_LINE_CHARS)
             for x in str(body.get("diff_text") or "").splitlines()]
    page = paged(lines, line_offset, DIFF_LINES, default=DIFF_LINES, maximum=MAX_DIFF_LINES,
                 noun="diff lines", param="line_offset")
    shown = {**_pick(body, ("id", "agent_name", "commit_sha", "status")),
             "commit_message": _long(body.get("commit_message"), MAX_FIELD_CHARS),
             "test_summary": _long(body.get("test_summary"), MAX_FIELD_CHARS),
             "diff": "\n".join(page.items)}
    return await _framed("PENDING COMMIT DIFF", shown, "commit", _DIFF_NOTE,
                         diff_lines=page.total, page=page.line)


@annotate(read_only=True, idempotent=True, open_world=False)
async def agent_registry_mutations(mutation_id: str = "", offset: int = 0,
                                   limit: int = DEFAULT_ROWS,
                                   line_offset: int = 0) -> dict[str, Any]:
    """Read the self-mutation queue of the agents, newest first, or the diff of one pending commit. It approves nothing.

    Args:
        mutation_id: the id of one pending commit, for its diff. Empty reads the queue.
        offset: the first row of the queue page.
        limit: the rows of one queue page.
        line_offset: the first line of the diff page.
    """
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    wants_diff = bool(str(mutation_id or "").strip())
    if wants_diff and not _commit_id(mutation_id):
        return _refusal(_BAD_ID)
    no_page = await _feature_refusal(member, REGISTRY_FEATURE)
    if no_page is not None:
        return no_page
    if wants_diff:
        return await _diff(member, mutation_id, line_offset)
    start, size, ask = prefix_limit(offset, limit, default=DEFAULT_ROWS, cap=MUTATIONS_CAP,
                                    maximum=MAX_ROWS)
    status, body = await _send("GET", MUTATIONS_PATH, member, params={"limit": ask})
    if status != 200:
        return _fail(status, body, member)
    if not isinstance(body, list):
        return _refusal(_SHAPE)
    rows = [r for r in body if isinstance(r, dict)]
    page = prefix_page(rows, start, size, cap=MUTATIONS_CAP, asked=ask, noun="mutations")
    return await _framed("MUTATION QUEUE", [_mutation_row(r) for r in page.items], "mutations",
                         _QUEUE_NOTE, shown=len(page.items), page=page.line)


# ── agent_registry_office ───────────────────────────────────────────────────


_ROSTER_FIELDS = ("kind", "name", "display_name", "description", "runtime", "status",
                  "active_runs", "last_ts", "source")
_EVENT_FIELDS = ("ts", "kind", "phase", "agent", "model", "tier", "user", "thread_id",
                 "run_id", "source", "status", "tool", "duration_ms", "tokens", "cost_usd",
                 "delegated_by")
_COST_DAY_FIELDS = ("date", "cost", "tokens", "calls")


def _avatar_of(entry: Any) -> dict[str, Any]:
    """An avatar of the store: the library character and whether a custom
    picture exists. The picture itself is never shown."""
    if not isinstance(entry, dict):
        return {}
    cfg = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    out: dict[str, Any] = {"character": _line(cfg.get("libraryId") or "default", 64)}
    if entry.get("sprite"):
        out["custom_picture"] = True
    return out


def _roster_row(a: dict[str, Any]) -> dict[str, Any]:
    row = _pick(a, _ROSTER_FIELDS)
    if a.get("avatar"):
        row["avatar"] = _avatar_of(a.get("avatar"))
    return row


async def _roster(member: str, offset: int, limit: int) -> dict[str, Any]:
    status, body = await _send("GET", ROSTER_PATH, member)
    if status != 200:
        return _fail(status, body, member)
    rows = body.get("agents") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return _refusal(_SHAPE)
    page = paged([r for r in rows if isinstance(r, dict)], offset, limit,
                 default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="agents")
    return await _framed("OFFICE ROSTER", [_roster_row(r) for r in page.items], "roster",
                         _OFFICE_NOTE, total=page.total, shown=len(page.items), page=page.line)


async def _cost(member: str, days: Any) -> dict[str, Any]:
    try:
        n = max(MIN_DAYS, min(int(days), MAX_DAYS))
    except (TypeError, ValueError):
        n = 7
    status, body = await _send("GET", COST_PATH, member, params={"days": n})
    if status != 200:
        return _fail(status, body, member)
    if not isinstance(body, dict):
        return _refusal(_SHAPE)
    day_rows = [_pick(d, _COST_DAY_FIELDS) for d in (body.get("days") or [])
                if isinstance(d, dict)]
    day_page = paged(day_rows, 0, MAX_DAYS, default=MAX_DAYS, maximum=MAX_DAYS, noun="days")
    # The page of the office view shows the totals, the days, each model and
    # each source. The cost of each person (``by_instance``) stays out.
    shown = {"days_read": n, "totals": _fold(body.get("totals") or {}),
             "days": day_page.items, "by_model": _fold(body.get("by_model") or {}),
             "by_source": _fold(body.get("by_source") or {}),
             "by_agent": _fold(body.get("by_agent") or {}), "currency": "USD"}
    # A person who registered an agent wrote its name, a key of ``by_agent``.
    return await _framed("AI COST", shown, "cost", _OFFICE_NOTE)


async def _activity(member: str, offset: int, limit: int) -> dict[str, Any]:
    start, size, ask = prefix_limit(offset, limit, default=DEFAULT_ROWS, cap=ACTIVITY_CAP,
                                    maximum=MAX_ROWS)
    status, body = await _send("GET", ACTIVITY_PATH, member, params={"limit": ask})
    if status != 200:
        return _fail(status, body, member)
    rows = body.get("events") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return _refusal(_SHAPE)
    # The route gives the newest events oldest first. The tool shows them
    # newest first, so a next page goes further back.
    newest = [e for e in reversed(rows) if isinstance(e, dict)]
    page = prefix_page(newest, start, size, cap=ACTIVITY_CAP, asked=ask, noun="events")
    return await _framed("OFFICE ACTIVITY", [_pick(e, _EVENT_FIELDS) for e in page.items],
                         "events", _OFFICE_NOTE, shown=len(page.items), page=page.line)


async def _avatars(member: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The avatar store of the org, or ``(None, the refusal)``."""
    status, body = await _send("GET", AVATARS_PATH, member)
    if status != 200:
        return None, _fail(status, body, member)
    found = body.get("avatars") if isinstance(body, dict) else None
    if not isinstance(found, dict):
        return None, _refusal(_SHAPE)
    return found, None


@annotate(read_only=True, idempotent=True, open_world=False)
async def agent_registry_office(view: str = "roster", days: int = 7, offset: int = 0,
                                limit: int = DEFAULT_ROWS) -> dict[str, Any]:
    """Read the office view of your organization: the roster of agents, the AI cost, the recent activity or the avatars.

    Args:
        view: roster, cost, activity or avatars.
        days: the days of the cost read, 1 to 90.
        offset: the first row of the page.
        limit: the rows of one page.
    """
    kind = str(view or "roster").strip().lower()
    if kind not in _OFFICE_VIEWS:
        return _refusal(_BAD_VIEW)
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    # Each member reads the avatars (``AgentAvatar`` draws them in each app).
    # Only the Live Activity page reads the other three views.
    if kind != "avatars":
        no_page = await _feature_refusal(member, OFFICE_FEATURE)
        if no_page is not None:
            return no_page
    if kind == "roster":
        return await _roster(member, offset, limit)
    if kind == "cost":
        return await _cost(member, days)
    if kind == "activity":
        return await _activity(member, offset, limit)
    found, why = await _avatars(member)
    if found is None:
        return why or _refusal(_NO_ANSWER)
    rows = [{"agent": _line(k, 64), **_avatar_of(v)} for k, v in sorted(found.items())]
    page = paged(rows, offset, limit, default=DEFAULT_ROWS, maximum=MAX_ROWS, noun="avatars")
    # A member writes the character of an avatar, so the rows are data.
    return await _framed("AGENT AVATARS", page.items, "avatars", _AVATARS_NOTE,
                         total=page.total, page=page.line, characters=list(CHARACTERS))


# ── agent_registry_runs ─────────────────────────────────────────────────────


_RUN_FIELDS = ("run_id", "thread_id", "agent", "user", "model", "status", "started_at",
               "ended_at", "duration_ms", "total_tokens", "tool_count", "error_type")


def _run_row(r: dict[str, Any]) -> dict[str, Any]:
    row = _pick(r, _RUN_FIELDS)
    if not _empty(r.get("error_message")):
        row["error_message"] = _error(r.get("error_message"))
    return row


@annotate(read_only=True, idempotent=True, open_world=False)
async def agent_registry_runs(agent: str = "", status: str = "", since_hours: int = 0,
                              offset: int = 0, limit: int = DEFAULT_ROWS) -> dict[str, Any]:
    """Read the run history of the agents of your organization, newest first. It shows no message text.

    Args:
        agent: only the runs of this agent. Empty reads each agent.
        status: running, completed, error or cancelled. Empty reads each status.
        since_hours: only the runs of the last hours. 0 reads each run.
        offset: the first row of the page.
        limit: the rows of one page.
    """
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    params: dict[str, Any] = {}
    if str(agent or "").strip():
        name = _agent_name(agent)
        if not name:
            return _refusal(_BAD_AGENT)
        params["agent"] = name
    state = str(status or "").strip().lower()
    if state:
        if state not in RUN_STATUSES:
            return _refusal(_BAD_STATUS)
        params["status"] = state
    try:
        hours = int(since_hours or 0)
    except (TypeError, ValueError):
        hours = 0
    if hours > 0:
        params["since_hours"] = min(hours, MAX_SINCE_HOURS)
    no_page = await _feature_refusal(member, OFFICE_FEATURE)
    if no_page is not None:
        return no_page
    start, size, ask = prefix_limit(offset, limit, default=DEFAULT_ROWS, cap=RUNS_CAP,
                                    maximum=MAX_ROWS)
    params["limit"] = ask
    code, body = await _send("GET", RUNS_PATH, member, params=params)
    if code != 200:
        return _fail(code, body, member)
    rows = body.get("runs") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return _refusal(_SHAPE)
    page = prefix_page([r for r in rows if isinstance(r, dict)], start, size, cap=RUNS_CAP,
                       asked=ask, noun="runs")
    return await _framed("RUN HISTORY", [_run_row(r) for r in page.items], "runs", _RUNS_NOTE,
                         shown=len(page.items), page=page.line)


# ── agent_registry_set_avatar ───────────────────────────────────────────────


def _snapshot(entry: Any) -> tuple[Any, ...]:
    """What the card of an avatar must still match when the member answers."""
    if not isinstance(entry, dict):
        return (None,)
    cfg = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    return (json.dumps(cfg, sort_keys=True, default=str), bool(entry.get("sprite")))


async def _approved(detail: str, member: str) -> bool:
    """Show ONE card, bound to *member*, and wait. True only on an approve."""
    from acb_skills import ask_tools

    answer = await ask_tools.request_confirmation(
        _CARD_TITLE, detail, non_interactive_default="deny", responder=member)
    return answer is True


def _not_done() -> dict[str, Any]:
    """The answer of a card that did nothing, from the one outcome table."""
    from acb_skills import ask_tools

    return _refusal(ask_tools.card_not_done_text(_NOT_DONE))


@annotate(read_only=False, idempotent=True, open_world=False)
async def agent_registry_set_avatar(agent: str, character: str) -> dict[str, Any]:
    """Give one agent a character of the avatar library, as the avatar picker of the Agent Registry does.

    Args:
        agent: the name of the agent, as agent_registry_list shows it.
        character: the id of a library character, as the avatars view lists it.
    """
    member, refused = _member_or_refusal()
    if refused:
        return _refusal(refused)
    pick = str(character or "").strip()
    if pick not in CHARACTERS:
        return _refusal(_BAD_CHARACTER)
    # Only the avatar picker of the Agent Registry page writes an avatar.
    no_page = await _feature_refusal(member, REGISTRY_FEATURE)
    if no_page is not None:
        return no_page
    name, bad = await _listed(member, agent)
    if bad is not None:
        return bad
    found, why = await _avatars(member)
    if found is None:
        return why or _refusal(_NO_ANSWER)
    before = found.get(name)
    now = _avatar_of(before).get("character", "default") if before else "default"
    custom = bool(isinstance(before, dict) and before.get("sprite"))
    if now == pick and not custom:
        return _refusal(_SAME_AVATAR)
    # The picker sends the character and no picture, so a custom picture goes.
    # That loss always shows the card.
    if custom or await _pin._must_ask():
        detail = (f"The agent {_line(name, 64)} changes from {_line(now, 64)} to {pick}. "
                  "Each member of your organization sees the new avatar.")
        if custom:
            detail += " The custom picture of this agent goes, and you cannot undo it here."
        if not await _approved(detail, member):
            return _not_done()
        again, why = await _avatars(member)
        if again is None:
            return {**(why or _refusal(_NO_ANSWER)), "decision": "approved"}
        if _snapshot(again.get(name)) != _snapshot(before):
            return _refusal(_CHANGED, decision="approved")
    code, body = await _send("PUT", f"/observability/avatars/{_q(name)}", member,
                             body={"config": {"libraryId": pick}, "sprite": None})
    _log.info("agent_registry_tool.avatar_sent", status=code)
    if code != 200:
        return _fail(code, body, member)
    return {"ok": True, "agent": name, "character": pick,
            "answer": f"The agent {name} has the character {pick} now." + _RELOAD}


#: Each tool of the ``agent-registry`` pack, in the order of its skill family.
AGENT_REGISTRY_TOOLS = (
    agent_registry_list, agent_registry_skills, agent_registry_config,
    agent_registry_mutations, agent_registry_office, agent_registry_runs,
    agent_registry_set_avatar,
)
