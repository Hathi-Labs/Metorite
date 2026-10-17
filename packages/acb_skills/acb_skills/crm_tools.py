"""CRM read tools for every agent (WS-53 CRM-C7).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 items 1 and 5.

Thirteen read tools over the gateway's ``/crm`` routes. This module is their ONE
implementation. ``crm-assistant`` imports them, and the injection seam
(``orchestrator._tool_injection``) offers them to every other agent as the
``crm`` skill family.

**Each read goes through a ``/crm`` route as the member.** A call carries the
internal bearer and the acting member's ``X-User-Email``, from the run's
ContextVar. With no member, :func:`_headers` refuses, because a bearer alone
reads as the platform itself. So the route's kill switch, the ``feature:crm``
gate, the access rule and the tenant all apply (§10.8). No tool opens a
database session and no tool takes SQL (``history_tools.py`` records why).

**Two refusals are answers, not errors.** A 404 with the bare ``Not Found`` of
the kill switch says that the CRM is off for this organization. A 403 says
that the CRM is not open to this member. Each tool returns that sentence, so
the agent can tell the member and stop.

**The path is bounded.** Each value that goes into a path is checked first:
the entity by :func:`_entity_slug`, an id by :func:`_record_uuid`, a module
name by :func:`_module_name` and a report kind by :func:`_report_kind`. The
result goes into the path under the name ``slug``, ``record``, ``mod`` or
``report``, and a status vocabulary from a fixed map under ``vocab``. ``tests/unit/test_crm_agent.py`` checks that by AST.

**A short read says so** (CRM-Q30b). When Zoho holds more records of an
entity or a module than the connected Zoho user can read (CRM-Q24), the
list tools and the aggregate add one line with both counts. One helper,
:func:`_short_note`, reads ``GET /crm/sync/short-reads`` once for each tool
call. A failed read adds nothing.

**An answer is text, 50 rows at most.** When more rows exist, it ends with
"More: call again with page=N". Every tool reads only, so each one carries
``read_only=True`` and ``open_world=False`` in
``tool_annotations.TOOL_ANNOTATIONS``. The one exception is ``crm_report``:
with ``make_report=true`` it makes a kept report from the route's data
(``projects_ai_chat.md`` §14.10.9), so it writes two files and carries
``read_only=False`` and ``idempotent=False``. It still sends only GET.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from uuid import UUID

import httpx
from acb_common.paging import page_line, paged, prefix_page

from acb_skills.tool_annotations import TOOL_ANNOTATIONS

#: The most rows one tool answer holds (the owner's D1, 2026-10-13).
MAX_ROWS = 50

#: What the member reads when the CRM is off for the organization.
CRM_OFF = (
    "The CRM is off for this organization, so there is nothing to read. "
    "An admin of the organization can turn it on."
)

#: What the member reads when the access rule refuses them.
CRM_CLOSED = (
    "The CRM is not open to you. An admin of your organization can give you "
    "access."
)

#: What the member reads in a shared room. There the run's identity is the
#: room's memory compartment (``room:<thread>``), not a person, and the
#: gateway must never read it as an email.
CRM_SHARED_ROOM = (
    "A shared room cannot read the CRM. Ask in your own chat."
)

#: The record types. The keys are the gateway's ``core.ENTITIES`` slugs.
ENTITY_LABELS: dict[str, str] = {
    "leads": "Lead",
    "deals": "Deal",
    "contacts": "Contact",
    "organizations": "Organization",
}

_ENTITY_ALIASES: dict[str, str] = {
    "lead": "leads", "deal": "deals", "opportunity": "deals",
    "opportunities": "deals", "contact": "contacts", "person": "contacts",
    "people": "contacts", "organization": "organizations",
    "organisation": "organizations", "organisations": "organizations",
    "org": "organizations", "orgs": "organizations",
    "account": "organizations", "accounts": "organizations",
    "company": "organizations", "companies": "organizations",
}

#: The status vocabulary of each entity that has one.
_STATUS_KIND: dict[str, str] = {"leads": "lead", "deals": "deal"}

#: ``GET /crm/reports/{kind}``. The kind is a key here, never caller text.
REPORT_KINDS: tuple[str, ...] = ("pipeline", "funnel", "win-loss", "owners")

#: The gateway's own module-name rule (``core._MODULE_NAME``).
_MODULE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")

#: The entity word of a mirrored module in the aggregate and the field list.
MODULE_PREFIX = "module:"

#: What the typed aggregate (CRM-Q25) can do with a field of each type.
_FIELD_USES: dict[str, str] = {
    **dict.fromkeys(("number", "decimal", "currency", "percent"),
                    "measure: sum, avg, min, max"),
    **dict.fromkeys(("select", "boolean", "lookup"), "group by value"),
    **dict.fromkeys(("date", "datetime"), "group by month, quarter or year"),
}


class _Refused(Exception):
    """The gateway said the CRM is off, or not open to this member."""


class _GatewayError(RuntimeError):
    """A failed read. ``code`` is the ``detail.code`` of the answer, when the
    gateway gave one (``catalogue_off``)."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# ── Transport ───────────────────────────────────────────────────────────────

def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _current_user_email() -> str:
    """The member of this run, from the run's ContextVar, and nothing else."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id
        return _get_memory_user_id() or ""
    except Exception:
        return ""


def _internal_token() -> str:
    """The internal bearer. The order is the one of ``agent-crm``."""
    from acb_common import get_settings

    settings = get_settings()
    return (
        getattr(settings, "gateway_internal_token", "")
        or getattr(settings, "litellm_master_key", "")
        or "sk-local"
    )


def _headers() -> dict[str, str]:
    """The bearer and the acting member. With no member, refuse.

    An identity with no ``@`` is not a member. In a shared room the run's
    identity is the room's compartment (``room:<thread>``), so the tool
    answers :data:`CRM_SHARED_ROOM` and sends nothing to the gateway.
    """
    user = _current_user_email()
    if not user:
        raise RuntimeError(
            "No acting user for this run, so there is nobody to act as — "
            "refusing to call the gateway as the platform itself."
        )
    if "@" not in user:
        raise _Refused(CRM_SHARED_ROOM)
    return {
        "Authorization": f"Bearer {_internal_token()}",
        "Content-Type": "application/json",
        "X-User-Email": user,
    }


def _detail_code(resp: Any) -> str | None:
    """The ``detail.code`` of an answer, or ``None``."""
    try:
        body = resp.json()
    except Exception:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return str(code) if code else None


def _detail(resp: Any) -> str:
    try:
        body = resp.json()
    except Exception:
        return (getattr(resp, "text", "") or "")[:200]
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("code") or "")
        return str(detail or "")
    return ""


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """One GET as the member. The only verb this module speaks."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            "GET", f"{_gateway_url()}{path}", headers=_headers(),
            params={k: v for k, v in (params or {}).items() if v is not None},
        )
    status = int(resp.status_code)
    if status < 400:
        return resp.json()
    detail = _detail(resp)
    if status == 403:
        raise _Refused(CRM_CLOSED)
    if status == 404 and detail in ("", "Not Found"):
        # The kill switch answers the bare "Not Found". A missing record
        # names itself ("Deal not found"), so it falls through below.
        raise _Refused(CRM_OFF)
    raise _GatewayError(
        f"CRM GET {path} failed ({status})" + (f": {detail}" if detail else ""),
        _detail_code(resp),
    )


async def crm_mode() -> str | None:
    """The CRM mode of the member's organization (``GET /crm/mode``).

    Not a tool. The orchestrator reads it at run assembly, as the member, to
    drop the record write tools in Mirror mode. It raises on any failure, so
    the caller decides what a failed read means.
    """
    body = await _get("/crm/mode")
    mode = body.get("mode") if isinstance(body, dict) else None
    return str(mode) if mode else None


def _answers_refusals(
    fn: Callable[..., Awaitable[str]],
) -> Callable[..., Awaitable[str]]:
    """A refusal of the gateway becomes the tool's answer."""
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return await fn(*args, **kwargs)
        except _Refused as refused:
            return str(refused)
    return wrapper


def _read_tool(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """Answer a refusal in text, and carry the tool's risk on the function.

    The risk lives in the platform's static table
    (``tool_annotations.TOOL_ANNOTATIONS``), as for every injected tool. A
    name with no entry there is a KeyError at import, never a tool with no
    ``open_world`` (H-236).
    """
    wrapped = _answers_refusals(fn)
    wrapped.__tool_risk__ = TOOL_ANNOTATIONS[fn.__name__]  # type: ignore[attr-defined]
    return wrapped


# ── Validators: every path segment goes through one ─────────────────────────

def _entity_slug(entity: str, *, allow_module: bool = False) -> str:
    """The route slug of a record type. With *allow_module*, also a mirrored
    module as ``module:<api_name>`` (CRM-Q25), its name checked by
    :func:`_module_name`. Only the aggregate and the field list take one."""
    raw = (entity or "").strip()
    if allow_module and raw.lower().startswith(MODULE_PREFIX):
        return MODULE_PREFIX + _module_name(raw[len(MODULE_PREFIX):])
    key = raw.lower()
    slug = _ENTITY_ALIASES.get(key, key)
    if slug not in ENTITY_LABELS:
        legal = ", ".join(ENTITY_LABELS)
        if allow_module:
            legal += ", or 'module:<api_name>' from crm_list_modules"
        raise RuntimeError(f"Unknown CRM record type {entity!r}. One of: {legal}.")
    return slug


def _entity_label(slug: str) -> str:
    """What an answer calls the entity: "Deal", or the module's API name."""
    return ENTITY_LABELS.get(slug) or slug[len(MODULE_PREFIX):]


# ── The short read (CRM-Q30b) ───────────────────────────────────────────────

#: The route of the short reads: the keys that Zoho holds more of than the
#: connected Zoho user can read (CRM-Q24), with the two counts.
SHORT_READS_PATH = "/crm/sync/short-reads"


def _short_line(row: dict[str, Any]) -> str | None:
    """The one line of a short read, or None for a row of a wrong shape."""
    key = str(row.get("key") or "")
    zoho, readable = row.get("zoho_count"), row.get("readable")
    if not key or not isinstance(zoho, int) or not isinstance(readable, int):
        return None
    name = key[len(MODULE_PREFIX):] if key.startswith(MODULE_PREFIX) else (
        f"{ENTITY_LABELS[key]}s" if key in ENTITY_LABELS else key)
    return (
        f"Note: Zoho holds {zoho:,} {name} records, and the connected Zoho user can "
        f"read {readable:,}. Totals may be short. An admin can widen that user's "
        "access in Zoho."
    )


async def _short_note(keys: frozenset[str] | None) -> str:
    """The short-read lines for *keys*, or for each module when *keys* is
    None, as a block to add to an answer. Empty when nothing is short.

    ONE read of the route for each tool call. The line is advice, so a read
    that fails, a refusal included, adds nothing and the tool answers as
    before (CRM-Q30b item 5)."""
    try:
        rows = await _get(SHORT_READS_PATH)
    except Exception:  # advice only: the answer stands without it
        return ""
    lines = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "")
        wanted = key.startswith(MODULE_PREFIX) if keys is None else key in keys
        line = _short_line(row) if wanted else None
        if line:
            lines.append(line)
    return "\n" + "\n".join(lines) if lines else ""


def _record_uuid(record_id: str) -> str:
    """The canonical UUID. A path can then hold no separator or dot segment."""
    try:
        return str(UUID(str(record_id).strip()))
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError(
            f"Invalid CRM record id {record_id!r}. A CRM id is a UUID — use an "
            "id that another CRM tool returned, not a name."
        ) from None


def _module_name(module: str) -> str:
    name = str(module or "").strip()
    if not _MODULE_NAME.match(name):
        raise RuntimeError(
            f"Invalid module name {module!r}. Use an api_name that "
            "crm_list_modules returned, for example 'Products'."
        )
    return name


def _report_kind(kind: str) -> str:
    key = str(kind or "").strip().lower().replace("_", "-")
    if key not in REPORT_KINDS:
        raise RuntimeError(
            f"Unknown report {kind!r}. One of: {', '.join(REPORT_KINDS)}."
        )
    return key


#: The most owners ``GET /crm/owners`` returns (its ``LIMIT 500``).
OWNERS_MAX = 500


def _page(page: int | None) -> int:
    return max(1, int(page or 1))


def _page_size(page_size: int | None, default: int = 25) -> int:
    return max(1, min(int(page_size or default), MAX_ROWS))


def _more(total: int, page: int, page_size: int) -> str:
    """The shared hint line (``acb_common.paging``), for a page-number tool."""
    start = (page - 1) * page_size
    shown = max(0, min(page_size, total - start))
    line = page_line(total, start, shown, page_size=page_size)
    return f"\n{line}" if line else ""


def _page_of(rows: list[Any], page: int | None, noun: str = "items") -> Any:
    """Page ``page`` of ``MAX_ROWS`` from a list the route sent whole."""
    number = _page(page)
    return paged(rows, (number - 1) * MAX_ROWS, MAX_ROWS, default=MAX_ROWS,
                 maximum=MAX_ROWS, noun=noun, page_size=MAX_ROWS)


# ── Formatting ──────────────────────────────────────────────────────────────

def _humanize(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def _title(slug: str, row: dict[str, Any]) -> str:
    if slug == "leads":
        return str(row.get("lead_name") or row.get("email") or "(unnamed lead)")
    if slug == "contacts":
        name = " ".join(
            str(p) for p in (row.get("first_name"), row.get("last_name")) if p
        ).strip()
        return name or str(row.get("email") or "(unnamed contact)")
    return str(row.get("name") or "(unnamed)")


def _money(row: dict[str, Any]) -> str:
    amount = row.get("amount")
    if amount in (None, ""):
        return ""
    return f"{row.get('currency') or 'INR'} {float(amount):,.0f}"


def _line(slug: str, row: dict[str, Any]) -> str:
    bits = [_title(slug, row)]
    for key in ("status_name", "organization_name", "title", "email"):
        if row.get(key):
            bits.append(str(row[key]))
    if _money(row):
        bits.append(_money(row))
    if row.get("expected_close_date"):
        bits.append(f"closes {row['expected_close_date']}")
    if row.get("owner_email"):
        bits.append(f"owner {row['owner_email']}")
    return f"- {' · '.join(bits)} (id={row.get('id')})"


def _option_label(defn: dict[str, Any], value: Any) -> str:
    """The label of a picklist value, or the value when no option names it."""
    options = defn.get("options") or []
    labels = {
        str(o.get("value")): str(o.get("label"))
        for o in options if isinstance(o, dict) and o.get("label")
    }
    if isinstance(value, list):
        return ", ".join(labels.get(str(v), str(v)) for v in value)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("label") or value)
    return labels.get(str(value), str(value))


def _custom_lines(custom: Any, defs: list[dict[str, Any]]) -> list[str]:
    """Each custom value under its label, never its raw key."""
    if not isinstance(custom, dict):
        return []
    by_key = {str(d.get("key")): d for d in defs if isinstance(d, dict)}
    lines = []
    for key, value in custom.items():
        if value in (None, "", [], {}):
            continue
        defn = by_key.get(str(key), {})
        label = str(defn.get("label") or _humanize(str(key)))
        lines.append(f"- {label}: {_option_label(defn, value)}")
    return lines


def _filters(
    *, q: str | None, status: str | None, owner: str | None,
    pipeline_id: str | None, created_from: str | None, created_to: str | None,
    updated_from: str | None, updated_to: str | None, close_from: str | None,
    close_to: str | None, custom: dict[str, str] | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "q": q, "owner": owner, "created_from": created_from,
        "created_to": created_to, "updated_from": updated_from,
        "updated_to": updated_to, "close_from": close_from, "close_to": close_to,
    }
    if pipeline_id:
        params["pipeline_id"] = _record_uuid(pipeline_id)
    for key, value in (custom or {}).items():
        params[f"custom.{key}"] = value
    params["_status"] = status
    return params


async def _resolve_status(slug: str, params: dict[str, Any]) -> str | None:
    """A status given by id or by name, as the ``status_id`` the route wants."""
    status = params.pop("_status", None)
    if not status:
        return None
    try:
        return str(UUID(str(status)))
    except ValueError:
        pass
    kind = _STATUS_KIND.get(slug)
    if kind is None:
        raise RuntimeError(f"{slug} have no status. Drop the status filter.")
    vocab = kind
    rows = await _get(f"/crm/statuses/{vocab}")
    wanted = str(status).strip().lower()
    found = [r for r in rows or [] if str(r.get("name", "")).strip().lower() == wanted]
    if len(found) != 1:
        names = ", ".join(str(r.get("name")) for r in rows or [])
        raise RuntimeError(
            f"'{status}' names {'no' if not found else 'more than one'} "
            f"{kind} status. The statuses are: {names}."
        )
    return str(found[0].get("id"))


# ── The tools ───────────────────────────────────────────────────────────────

@_read_tool
async def crm_list_records(
    entity: str,
    q: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    pipeline_id: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    close_from: str | None = None,
    close_to: str | None = None,
    custom: dict[str, str] | None = None,
    page: int = 1,
    page_size: int = 25,
) -> str:
    """List CRM records: entity is 'leads', 'deals', 'contacts' or
    'organizations'. Filter by q (text in names, emails and phones), status
    (a stage name or id), owner (an email, or 'me'), pipeline_id (deals),
    created_from/created_to, updated_from/updated_to, close_from/close_to
    (deals; dates as YYYY-MM-DD) and custom ({field key: value}, keys from
    crm_list_fields). Returns at most 50 rows with their ids."""
    slug = _entity_slug(entity)
    size, number = _page_size(page_size), _page(page)
    params = _filters(
        q=q, status=status, owner=owner, pipeline_id=pipeline_id,
        created_from=created_from, created_to=created_to,
        updated_from=updated_from, updated_to=updated_to,
        close_from=close_from, close_to=close_to, custom=custom,
    )
    params["status_id"] = await _resolve_status(slug, params)
    params.update(page=number, page_size=size)
    data = await _get(f"/crm/{slug}", params)
    rows = (data or {}).get("rows") or []
    total = int((data or {}).get("total") or len(rows))
    note = await _short_note(frozenset({slug}))
    if not rows:
        return f"No {slug} match these filters.{note}"
    head = f"{ENTITY_LABELS[slug]}s: {total} match, page {number}."
    return "\n".join([head, *(_line(slug, r) for r in rows)]) + _more(
        total, number, size,
    ) + note


@_read_tool
async def crm_get_record(entity: str, record_id: str) -> str:
    """Read one CRM record in full, with each custom field under its label and
    each picklist value as its label. entity is 'leads', 'deals', 'contacts'
    or 'organizations', and record_id is an id from another CRM tool."""
    slug = _entity_slug(entity)
    record = _record_uuid(record_id)
    row = await _get(f"/crm/{slug}/{record}")
    defs = await _get(f"/crm/fields/{slug}")
    if not isinstance(row, dict):
        return f"{ENTITY_LABELS[slug]} {record} returned no fields."
    lines = [f"{ENTITY_LABELS[slug]}: {_title(slug, row)} (id={row.get('id')})"]
    for key, value in row.items():
        if key in ("id", "custom") or value in (None, "", [], {}):
            continue
        lines.append(f"- {_humanize(key)}: {value}")
    extra = _custom_lines(row.get("custom"), defs if isinstance(defs, list) else [])
    if extra:
        lines.append("Custom fields:")
        lines.extend(extra)
    return "\n".join(lines)


@_read_tool
async def crm_list_fields(entity: str, page: int = 1) -> str:
    """The fields of one record type, or of a module as 'module:<api_name>':
    each label, its key, its type, what crm_aggregate can do with it, and its
    picklist options. The key goes in the custom filter of crm_list_records,
    and in field, group_field and field_filters of crm_aggregate. A retired
    field is marked: records keep its values, and no filter takes its key."""
    slug = _entity_slug(entity, allow_module=True)
    label = _entity_label(slug)
    # WS-53 CRM-C4a: a row with `is_system` overlays a fixed column, so it is
    # never offered as a custom key (crm_platform.md §20.7 item 11). The
    # primary filter is the route's: `GET /crm/fields/{entity}` reads
    # `AND NOT is_system` (`fields._FIELDS_SQL`). This one is a second guard.
    defs = [
        d for d in (await _get(f"/crm/fields/{slug}") or [])
        if isinstance(d, dict) and not d.get("is_system")
    ]
    if not defs:
        if slug.startswith(MODULE_PREFIX):
            return f"{label} has no field definitions yet."
        return f"{label}s have no custom fields."
    shown = _page_of(defs, page, "fields")
    if not shown.items:
        return shown.line
    lines = [f"{label} fields ({len(defs)}):"]
    for d in shown.items:
        options = [
            str(o.get("label")) for o in d.get("options") or []
            if isinstance(o, dict) and o.get("label") and not o.get("retired")
        ]
        use = _FIELD_USES.get(str(d.get("type")))
        lines.append(
            f"- {d.get('label')} (key {d.get('key')}, {d.get('type')})"
            + (" - retired" if d.get("retired") else "")
            + (f" [{use}]" if use and not d.get("retired") else "")
            # Every option: a filter needs the exact label, and the old cut
            # at 20 hid the rest with no notice.
            + (f": {', '.join(options)}" if options else "")
        )
    if shown.line:
        lines.append(shown.line)
    return "\n".join(lines)


@_read_tool
async def crm_list_pipelines() -> str:
    """The deal pipelines, each with its stages in order: the stage type, the
    probability that a weighted forecast uses, and the stage id."""
    pipelines = await _get("/crm/pipelines")
    if not pipelines:
        return "The CRM has no deal pipeline yet."
    lines = []
    for p in pipelines:
        lines.append(
            f"Pipeline {p.get('name')}" + (" (default)" if p.get("is_default") else "")
            + f" (id={p.get('id')}):"
        )
        for s in p.get("stages") or []:
            prob = s.get("probability")
            lines.append(
                f"- {s.get('name')} · {s.get('type')}"
                + (f" · {prob}%" if prob is not None else "")
                + f" (id={s.get('id')})"
            )
    return "\n".join(lines)


@_read_tool
async def crm_deal_contacts(deal_id: str, page: int = 1) -> str:
    """The contacts on one deal (a deal id from crm_list_records), primary
    first, each with its role on the deal."""
    record = _record_uuid(deal_id)
    data = await _get(f"/crm/deals/{record}/contacts")
    rows = (data or {}).get("rows") or []
    if not rows:
        return "This deal has no contacts."
    shown = _page_of(rows, page, "contacts")
    if not shown.items:
        return shown.line
    lines = [f"Deal contacts ({len(rows)}):"]
    for r in shown.items:
        c = r.get("contact") or {}
        bits = [_title("contacts", c)]
        if r.get("role"):
            bits.append(f"role {r['role']}")
        if r.get("is_primary"):
            bits.append("primary")
        for key in ("title", "email", "phone"):
            if c.get(key):
                bits.append(str(c[key]))
        lines.append(f"- {' · '.join(bits)} (id={c.get('id')})")
    if shown.line:
        lines.append(shown.line)
    return "\n".join(lines)


@_read_tool
async def crm_list_owners(page: int = 1) -> str:
    """Who can own a CRM record: each member, and each owner who is not a
    member. Use an email from here in the owner filter."""
    number = _page(page)
    owners = (await _get("/crm/owners") or {}).get("owners") or []
    if not owners:
        return "The CRM has no owners yet."
    # The route returns at most OWNERS_MAX owners and no count, so the page
    # says when that cap is hit.
    shown = prefix_page(owners, (number - 1) * MAX_ROWS, MAX_ROWS, cap=OWNERS_MAX,
                        noun="owners", page_size=MAX_ROWS)
    lines = [f"Owners: {len(owners)}, page {number}."]
    for o in shown.items:
        lines.append(
            f"- {o.get('name') or o.get('email')} · {o.get('email')}"
            + ("" if o.get("member") else " · not a member")
        )
    if shown.line:
        lines.append(shown.line)
    return "\n".join(lines)


# ── crm_report as a kept report (projects_ai_chat.md §14.10.9) ─────────────
#
# Code builds the spec from the route's own data, so every number is the
# route's and the model copies none. A stage name, a lost reason or an owner
# goes into a table cell or a chart label, never into a text field, because
# make_report's markdown-lite check reads the text fields only.

#: The title word of each report kind.
_REPORT_TITLES: dict[str, str] = {
    "pipeline": "Pipeline", "funnel": "Funnel", "win-loss": "Win and loss",
    "owners": "Owners",
}
#: The kinds whose route sums ``crm_deals.amount``. The route carries no
#: currency: it adds the amounts of every deal currency into one figure
#: (``gateway/routes/crm/reports.py``, HANDOFF H-333). So the INR label is the
#: label the text answer uses, and the report says how the sum was made.
_MONEY_KINDS = frozenset({"pipeline", "win-loss", "owners"})
CURRENCY_NOTE = "Amounts are summed across deal currencies."
#: A bar chart holds 12 bars, and a table visual 20 rows.
REPORT_CHART_BARS = 12
REPORT_TABLE_ROWS = 20


def _rupees(value: Any) -> int:
    """An amount in whole rupees, as the text answer prints it."""
    return round(float(value or 0))


def _cut_note(shown: int, total: int, noun: str) -> str:
    return f"The {noun} shows the first {shown} of {total}." if total > shown else ""


def _stage_bars(stages: list[dict[str, Any]], key: str, title: str,
                unit: str = "") -> tuple[dict[str, Any], str]:
    """A bar chart of one figure for each stage, and its cut note."""
    shown = paged(stages, 0, REPORT_CHART_BARS, default=REPORT_CHART_BARS,
                  maximum=REPORT_CHART_BARS).items
    chart: dict[str, Any] = {
        "kind": "chart", "type": "bar", "title": title,
        "labels": [str(s["status"]["name"]) for s in shown],
        "values": [_rupees(s[key]) if unit else s[key] for s in shown],
    }
    if unit:
        chart["unit"] = unit
    return chart, _cut_note(len(shown), len(stages), "chart")


def _chart_section(heading: str, chart: dict[str, Any], note: str) -> dict[str, Any]:
    section: dict[str, Any] = {"heading": heading, "visual": chart}
    if note:
        section["text"] = note
    return section


def _table_section(heading: str, title: str, columns: list[str],
                   rows: list[list[Any]], text: str = "") -> dict[str, Any]:
    """A section with one table of at most 20 rows, and its cut note."""
    shown = paged(rows, 0, REPORT_TABLE_ROWS, default=REPORT_TABLE_ROWS,
                  maximum=REPORT_TABLE_ROWS).items
    words = " ".join(t for t in (text, _cut_note(len(shown), len(rows), "table")) if t)
    section: dict[str, Any] = {"heading": heading}
    if words:
        section["text"] = words
    if shown:
        section["visual"] = {"kind": "table", "title": title, "columns": columns,
                             "rows": shown}
    elif not words:
        section["text"] = "The report has no rows."
    return section


def _pipeline_spec(data: dict[str, Any]) -> dict[str, Any]:
    stages = [s for s in data.get("stages") or [] if isinstance(s, dict)]
    count, amount, weighted = data["count"], data["amount"], data["weighted"]
    sections: list[dict[str, Any]] = []
    if stages:
        sections.append(_chart_section(
            "Value by stage", *_stage_bars(stages, "amount", "Value by stage (INR)", "INR")))
    sections.append(_table_section(
        "Stages", "Each open stage", ["Stage", "Deals", "Value (INR)", "Weighted (INR)"],
        [[str(s["status"]["name"]), s["count"], _rupees(s["amount"]), _rupees(s["weighted"])]
         for s in stages]))
    return {
        "summary": (f"**{count} open deals** worth INR {_rupees(amount):,}, weighted "
                    f"INR {_rupees(weighted):,}, in {len(stages)} stages."),
        "kpis": [
            {"label": "Open deals", "value": count},
            {"label": "Pipeline value", "value": _rupees(amount), "unit": "INR"},
            {"label": "Weighted forecast", "value": _rupees(weighted), "unit": "INR"},
            {"label": "Open stages", "value": len(stages)},
        ],
        "sections": sections,
    }


def _funnel_spec(data: dict[str, Any]) -> dict[str, Any]:
    stages = [s for s in data.get("stages") or [] if isinstance(s, dict)]
    unmatched = [u for u in data.get("unmatched") or [] if isinstance(u, dict)]
    sections: list[dict[str, Any]] = []
    if stages:
        sections.append(_chart_section(
            "Deals that entered each stage",
            *_stage_bars(stages, "entered", "Deals that entered each stage")))
    sections.append(_table_section(
        "Conversion by stage", "Conversion and dwell by stage",
        ["Stage", "Entered", "Advanced", "Conversion %", "Median dwell (days)", "Samples"],
        [[str(s["status"]["name"]), s["entered"], s["advanced"], s["conversion_forward"],
          "unknown" if s["median_dwell_days"] is None else s["median_dwell_days"],
          s["dwell_samples"]] for s in stages],
        "A median dwell is over the samples in its row."))
    kpis = [
        {"label": "Deals", "value": data["deals"]},
        {"label": "Stages", "value": len(stages)},
        {"label": "Stage moves read", "value": data["transitions"]},
    ]
    if unmatched:
        kpis.append({"label": "Unknown stage names", "value": len(unmatched)})
        sections.append(_table_section(
            "Stage names that match no stage", "Names in the stage log",
            ["Name in the log", "Deals", "Mentions"],
            [[str(u["name"]), u["deals"], u["mentions"]] for u in unmatched],
            "These names are in the stage log, and no current stage has them."))
    return {
        "summary": (f"The funnel covers **{data['deals']} deals** and "
                    f"{data['transitions']} stage moves, in {len(stages)} stages."),
        "kpis": kpis,
        "sections": sections,
    }


def _win_loss_spec(data: dict[str, Any]) -> dict[str, Any]:
    won, lost = data["won"], data["lost"]
    kpis: list[dict[str, Any]] = [
        {"label": "Won", "value": won},
        {"label": "Lost", "value": lost},
        {"label": "Win rate", "value": data["win_rate"], "unit": "%"},
        {"label": "Won value", "value": _rupees(data["won_amount"]), "unit": "INR"},
        {"label": "Lost value", "value": _rupees(data["lost_amount"]), "unit": "INR"},
    ]
    if data.get("average_cycle_days") is not None:
        kpis.append({"label": "Average cycle", "value": data["average_cycle_days"],
                     "unit": "days"})
    kpis.append({"label": "Closed with no date", "value": data["closed_without_date"]})
    closed: dict[str, Any] = {"heading": "Won and lost"}
    if won + lost > 0:
        closed["visual"] = {"kind": "chart", "type": "donut", "title": "Deals won and lost",
                            "labels": ["Won", "Lost"], "values": [won, lost],
                            "tones": ["green", "red"]}
    else:
        closed["text"] = "No deal closed in this window."
    reasons = [r for r in data.get("lost_reasons") or [] if isinstance(r, dict)]
    sections = [closed]
    if reasons:
        sections.append(_table_section(
            "Lost reasons", "Lost deals by reason", ["Reason", "Deals", "Value (INR)"],
            [[str(r["label"]), r["deals"], _rupees(r["amount"])] for r in reasons]))
    return {
        "period": f"The last {data['window_days']} days, since {data['since']}",
        "summary": (f"**{won} won** and {lost} lost since {data['since']}. "
                    f"The win rate is {data['win_rate']}%."),
        "kpis": kpis,
        "sections": sections,
    }


def _owners_spec(data: dict[str, Any]) -> dict[str, Any]:
    owners = [o for o in data.get("owners") or [] if isinstance(o, dict)]
    omitted = int(data.get("omitted") or 0)
    window = data["window_days"]
    kpis: list[dict[str, Any]] = [{"label": "Owners in the report", "value": len(owners)}]
    if omitted:
        kpis.append({"label": "Owners not in the report", "value": omitted})
    kpis.append({"label": "Won window", "value": window, "unit": "days"})
    text = f"Won amounts cover the last {window} days."
    if omitted:
        text += f" {omitted} more owners are not in the report."
    return {
        "summary": f"**{len(owners)} owners**, ranked by their deals in play. {text}",
        "kpis": kpis,
        "sections": [_table_section(
            "Owners", "Each owner", ["Owner", "Open deals", "Weighted (INR)", "Won (INR)"],
            [[str(o.get("owner_email") or "Unassigned"), o["open_deals"],
              _rupees(o["weighted"]), _rupees(o["won_amount"])] for o in owners],
            text)],
    }


_REPORT_SPECS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "pipeline": _pipeline_spec, "funnel": _funnel_spec, "win-loss": _win_loss_spec,
    "owners": _owners_spec,
}


def _crm_report_spec(report: str, data: dict[str, Any],
                     today: date | None = None) -> dict[str, Any]:
    """The ``make_report`` spec of one CRM report, built by code from the data
    of ``GET /crm/reports/{report}``."""
    day = (today or date.today()).strftime("%d %b %Y")
    return {"title": f"CRM report · {_REPORT_TITLES[report]}",
            **_REPORT_SPECS[report](data),
            "date": day,
            "sources": [f"CRM {report}, {day}",
                        *([CURRENCY_NOTE] if report in _MONEY_KINDS else [])]}


# read_only=False, idempotent=False in TOOL_ANNOTATIONS (§14.10.9): with
# make_report=true it writes two files and draws a card, so an edited turn
# must not run it again unseen. It still sends only GET.
@_read_tool
async def crm_report(kind: str, page: int = 1, make_report: bool = False) -> str:
    """A CRM report: kind 'pipeline' (forecast by open stage), 'funnel'
    (entered, conversion and median dwell by stage), 'win-loss' (the last 90
    days) or 'owners' (each owner's open deals, weighted and won). For a
    report the member keeps, pass make_report=true, only when you hold
    make_report: code then makes the report card and its PDF from the same
    numbers, with no number from you."""
    report = _report_kind(kind)
    data = await _get(f"/crm/reports/{report}") or {}
    if report == "pipeline":
        lines = [
            f"- {s['status']['name']}: {s['count']} deals · INR {s['amount']:,.0f}"
            f" · weighted INR {s['weighted']:,.0f}"
            for s in data.get("stages") or []
        ]
        lines.append(
            f"Total: {data.get('count', 0)} deals · INR {data.get('amount', 0):,.0f}"
            f" · weighted INR {data.get('weighted', 0):,.0f}"
        )
    elif report == "funnel":
        lines = [
            f"- {s['status']['name']}: entered {s['entered']} · advanced "
            f"{s['advanced']} ({s['conversion_forward']}%) · median dwell "
            f"{s['median_dwell_days'] if s['median_dwell_days'] is not None else 'unknown'}"
            f" days over {s['dwell_samples']} samples"
            for s in data.get("stages") or []
        ]
    elif report == "win-loss":
        lines = [
            f"Since {data.get('since')}: won {data.get('won')}, lost "
            f"{data.get('lost')}, win rate {data.get('win_rate')}%.",
            f"Won INR {data.get('won_amount', 0):,.0f}, lost INR "
            f"{data.get('lost_amount', 0):,.0f}. Closed with no date: "
            f"{data.get('closed_without_date', 0)}.",
            *(f"- Lost reason {r['label']}: {r['deals']} deals"
              for r in data.get("lost_reasons") or []),
        ]
    else:
        lines = [
            f"- {o.get('owner_email') or 'Unassigned'}: {o['open_deals']} open · "
            f"weighted INR {o['weighted']:,.0f} · won INR {o['won_amount']:,.0f}"
            for o in data.get("owners") or []
        ]
    shown = _page_of(lines, page, "lines")
    tail = [shown.line] if shown.line else []
    if report == "owners" and int(data.get("omitted") or 0):
        # The route keeps the biggest owners and counts the rest.
        tail.append(f"{int(data['omitted'])} more owners are not in the report.")
    from acb_skills.reports import by_code

    outcome = await by_code(lambda: _crm_report_spec(report, data), make_report,
                            tool="crm_report",
                            say="Say in one or two lines what the report shows.")
    return f"CRM report {report}:\n" + "\n".join([*shown.items, *tail, *outcome.lines])


#: The measures whose numbers are rupees whatever the field.
_MONEY_MEASURES = frozenset({"sum_amount", "weighted"})

#: What the answer calls the measure over every group.
_OVERALL: dict[str, str] = {
    "avg": "Average over all records", "min": "Least over all records",
    "max": "Greatest over all records",
}


#: The fixed groups of the aggregate that take a month in the CRM zone.
_ZONED_GROUPS = ("created_month", "updated_month")


def _number(value: float) -> str:
    """A number with at most two decimals, and no trailing zero."""
    text = f"{value:,.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


@_read_tool
async def crm_aggregate(
    entity: str,
    group_by: str | None = None,
    measure: str = "count",
    field: str | None = None,
    group_field: str | None = None,
    bucket: str | None = None,
    q: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    pipeline_id: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    close_from: str | None = None,
    close_to: str | None = None,
    custom: dict[str, str] | None = None,
    field_filters: dict[str, str] | None = None,
    page: int = 1,
) -> str:
    """Count, sum or average CRM records by one group. The server computes
    every number: report them as given. entity is 'leads', 'deals',
    'contacts', 'organizations' or 'module:<api_name>' (crm_list_modules).
    measure is 'count', 'sum_amount' or 'weighted' (deals: amount times the
    effective probability, open and ongoing stages only), or 'sum', 'avg',
    'min' or 'max' of the number field named by field. Give group_by or
    group_field. group_by is 'status', 'owner', 'pipeline', 'source',
    'created_month', 'updated_month' or 'close_month' (a module: 'owner',
    'created_month', 'updated_month'). group_field is a picklist, boolean,
    lookup or date field; a date needs bucket 'month', 'quarter' or 'year'.
    Field keys and what each can do come from crm_list_fields. A core entity
    takes the filters of crm_list_records. A module takes field_filters:
    {key: value}, or {'key.from': date, 'key.to': date} for a date field.
    On a module, 'sum_amount' sums its amount, and field, group_field and
    field_filters also take the key words '@amount', '@date', '@status' and
    '@parent' (crm_list_modules names the key fields)."""
    slug = _entity_slug(entity, allow_module=True)
    params = _filters(
        q=q, status=status, owner=owner, pipeline_id=pipeline_id,
        created_from=created_from, created_to=created_to,
        updated_from=updated_from, updated_to=updated_to,
        close_from=close_from, close_to=close_to, custom=custom,
    )
    params["status_id"] = await _resolve_status(slug, params)
    for key, value in (field_filters or {}).items():
        params[f"field.{key}"] = value
    params.update(
        entity=slug, group_by=group_by, measure=measure, field=field,
        group_field=group_field, bucket=bucket,
    )
    data = await _get("/crm/aggregate", params) or {}
    groups = data.get("groups") or []
    module = slug.startswith(MODULE_PREFIX)
    # On a module the amount is a field, so only a currency field is rupees.
    money = (measure in _MONEY_MEASURES and not module) or data.get("field_type") == "currency"

    def fmt(value: Any) -> str:
        if value is None:
            return "no value"
        if money:
            return f"INR {float(value):,.0f}"
        return f"{float(value):,.0f}" if measure == "count" else _number(float(value))

    # CRM-Q31-2 item 6: the line names the label of the real field, and
    # "the amount" when the field came from the key word or sum_amount.
    keys = data.get("keys") or {}
    name = data.get("field_label") or field or data.get("field")
    if field or data.get("field"):
        word = "sum" if measure == "sum_amount" else measure
        what = f"{word} of {name}" + (" (the amount)" if "@amount" in keys else "")
    else:
        what = measure
    grouped = data.get("group_field_label") or group_field
    by = group_by or f"{grouped}" + (f" ({bucket})" if bucket else "")
    shown = _page_of(groups, page, "groups")
    plural = f"{ENTITY_LABELS[slug]}s" if slug in ENTITY_LABELS else _entity_label(slug)
    note = await _short_note(frozenset({slug}))
    if (field or data.get("field")) and data.get("values") == 0:
        # CRM-Q25c item 3: an empty field has no total, and 0 would be false.
        return f"{plural}, {what} by {by}: no values recorded for {field or data.get('field')}.{note}"
    lines = [f"{plural}, {what} by {by}:"]
    lines.extend(f"- {g['label']}: {fmt(g.get('value'))}" for g in shown.items)
    lines.append(f"{_OVERALL.get(measure, 'Total')}: {fmt(data.get('total'))}.")
    if shown.line:
        lines.append(shown.line)
    omitted = int(data.get("omitted") or 0)
    if omitted:
        # Past the route's 50 largest groups. The total counts them.
        lines.append(
            f"Other ({omitted} smaller groups): {fmt(data.get('other'))}. "
            "The total counts them. Narrow the filter to see them."
        )
    zone = data.get("zone")
    if zone and (group_by in _ZONED_GROUPS or data.get("group_field_type") == "datetime"):
        # CRM-Q25b: the route took each month on the clock of the CRM zone.
        # A `date` bucket takes no zone, so the line would be false there.
        lines.append(f"Months count in the CRM time zone, {zone}.")
    if slug in ENTITY_LABELS and data.get("total") == 0:
        empty = await _empty_core_line(slug)
        if empty:
            lines.append(empty)
    return "\n".join(lines) + note


#: CRM-Q31-2 item 7: what the modules that fit an empty core entity hold.
_CORE_HOLDS: dict[str, str] = {
    "deals": "sales", "organizations": "customers", "contacts": "contacts",
    "leads": "leads",
}


async def _empty_core_line(slug: str) -> str | None:
    """The empty core line (section 18.13.4): the core entity holds no
    records, and the catalogue names the modules with a fitting purpose.
    ``None`` when the catalogue is off, the entity holds records, or no
    module fits. Only a Mirror CRM has a module, so a Native CRM never gets
    the line."""
    try:
        body = await _get("/crm/catalogue")
    except _GatewayError:
        # The line is a hint. The total above is the answer, so a failed
        # read of the catalogue never fails the tool.
        return None
    entry = next((e for e in (body.get("entries") if isinstance(body, dict) else None) or []
                  if isinstance(e, dict) and e.get("kind") == "entity"
                  and e.get("entity") == slug), None)
    if entry is None or int(entry.get("records") or 0) > 0:
        return None
    alike = [str(label) for label in entry.get("alike") or []]
    if not alike:
        return None
    verb = "holds" if len(alike) == 1 else "hold"
    return (f"{entry.get('label')} holds no records. {_names(alike)} {verb} "
            f"{_CORE_HOLDS.get(slug, 'records')}.")


@_read_tool
async def crm_stage_dwell(days: int | None = None, page: int = 1) -> str:
    """How long deals sit in each stage (median and 90th percentile days),
    and the open deals past their stage's limit. Pass days to use one limit
    for every stage instead."""
    number = _page(page)
    data = await _get("/crm/aggregate/stage-dwell", {
        "days": days, "page": number, "page_size": MAX_ROWS,
    }) or {}
    if data.get("reason"):
        return str(data["reason"])
    lines = ["Days in each deal stage:"]
    for s in data.get("stages") or []:
        if not s.get("samples"):
            lines.append(f"- {s['name']}: no measured departures yet")
            continue
        lines.append(
            f"- {s['name']}: median {s['median_days']} · p90 {s['p90_days']} "
            f"over {s['samples']} departures"
            + (f" · limit {s['max_dwell_days']}" if s.get("max_dwell_days") else "")
        )
    total = int(data.get("overdue_total") or 0)
    lines.append(f"Deals past their limit: {total}.")
    for d in data.get("overdue") or []:
        lines.append(
            f"- {d['name']} · {d['stage']} · {d['days_in_stage']} days "
            f"(limit {d['limit_days']})"
            + (f" · owner {d['owner_email']}" if d.get("owner_email") else "")
            + f" (id={d['id']})"
        )
    return "\n".join(lines) + _more(total, number, MAX_ROWS)


#: The ``detail.code`` of ``GET /crm/catalogue`` for an org outside
#: ``CRM_CATALOGUE_ORGS`` (CRM-Q31-1). The tool then answers as before.
CATALOGUE_OFF = "catalogue_off"

#: What a line calls each key part of an entry.
_PART_WORDS: dict[str, str] = {
    "amount": "Amount", "date": "Date", "status": "Status", "parent": "Customer",
}

#: The money block: each money purpose, and what its records hold.
_MONEY_WORDS: tuple[tuple[str, str], ...] = (
    ("invoice", "billed in"),
    ("payment_in", "received in"),
    ("payment_out", "paid out in"),
    ("receivable", "owed in"),
    ("deal", "open sales in"),
    ("contract", "contracts in"),
)

#: The last line of a catalogue answer.
CATALOGUE_TOTAL_HINT = (
    "For a total, call crm_aggregate on the module with measure 'sum_amount', "
    "and group_field '@date' with a bucket."
)


def _records_word(count: int) -> str:
    if count <= 0:
        return "no records"
    return f"{count:,} record" + ("" if count == 1 else "s")


def _names(labels: list[str]) -> str:
    """``A``, ``A and B`` or ``A, B and C``."""
    if len(labels) < 2:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _entry_line(entry: dict[str, Any]) -> str:
    """One line of the catalogue (§18.13.4): the label, the entity or module
    word, the purpose and the record count, then the key fields by label and
    key, then the links."""
    word = (f"entity {entry.get('entity')}" if entry.get("kind") == "entity"
            else str(entry.get("entity")))
    head = [str(entry["purpose"])] if entry.get("purpose") else []
    head.append(_records_word(int(entry.get("records") or 0)))
    parts = [f"- {entry.get('label')} ({word}): {', '.join(head)}."]
    fields = entry.get("fields") or {}
    waiting = set(entry.get("open") or ())
    parent = fields.get("parent") if isinstance(fields.get("parent"), dict) else None
    for part, name in _PART_WORDS.items():
        if part in fields:
            found = fields[part]
            if not isinstance(found, dict):
                parts.append(f"{name}: no single field.")
                continue
            target = f" -> {found['target']}" if part == "parent" and found.get("target") else ""
            parts.append(f"{name} {found.get('label')} ({found.get('key')}{target}).")
        elif part in waiting:
            parts.append(f"{name}: not known.")
    links = [
        link for link in entry.get("links") or []
        if isinstance(link, dict) and not (parent and link.get("key") == parent.get("key"))
    ]
    if links:
        parts.append("Links: " + ", ".join(
            f"{link.get('key')} -> {link.get('target')}" for link in links) + ".")
    return " ".join(parts)


def _money_block(entries: list[dict[str, Any]]) -> str | None:
    """Where the money is, from the purposes, by code. Only an entry that
    holds records counts. ``None`` when no entry holds money."""
    clauses = []
    for purpose, words in _MONEY_WORDS:
        labels = [str(e.get("label")) for e in entries
                  if e.get("purpose") == purpose and int(e.get("records") or 0) > 0]
        if labels:
            clauses.append(f"{words} {_names(labels)}.")
    if not clauses:
        return None
    rest = " ".join(c[0].upper() + c[1:] for c in clauses[1:])
    return "Money: " + clauses[0] + (f" {rest}" if rest else "")


def _catalogue_answer(body: Any, page: int | None) -> str:
    entries = [e for e in (body.get("entries") if isinstance(body, dict) else None) or []
               if isinstance(e, dict)]
    shown = _page_of(entries, page, "entries")
    if not shown.items:
        return shown.line or "The CRM catalogue is empty."
    lines = ["CRM catalogue (what each entity and module is for):"]
    lines.extend(_entry_line(e) for e in shown.items)
    if shown.line:
        lines.append(shown.line)
    money = _money_block(entries)
    if money:
        lines.append(money)
    lines.append(CATALOGUE_TOTAL_HINT)
    return "\n".join(lines)


@_read_tool
async def crm_list_modules(page: int = 1) -> str:
    """The other Zoho modules this CRM mirrors (Products, Quotes, custom
    modules), with a record count. A Native CRM has none. crm_aggregate and
    crm_list_fields take a module as entity 'module:<api_name>'. When the
    catalogue is on, it also says what each entity and module is for, its
    key fields, and where the money is."""
    try:
        body = await _get("/crm/catalogue")
    except _GatewayError as exc:
        if exc.code != CATALOGUE_OFF:
            raise
        return await _modules_answer(page)
    # CRM-Q30b: the short-read lines of the modules, as the old answer has.
    return _catalogue_answer(body, page) + await _short_note(None)


async def _modules_answer(page: int | None) -> str:
    """The answer of ``crm_list_modules`` with the catalogue off: the
    answer of each org before CRM-Q31-1, byte for byte."""
    modules = await _get("/crm/modules")
    if not modules:
        return "This CRM mirrors no other module."
    shown = _page_of(list(modules), page, "modules")
    if not shown.items:
        return shown.line
    return "Modules:\n" + "\n".join(
        f"- {m.get('label')} (api_name {m.get('api_name')}): {m.get('count', 0)} records"
        for m in shown.items
    ) + (f"\n{shown.line}" if shown.line else "") + (
        "\nTo total a module, call crm_aggregate with entity 'module:<api_name>'."
    ) + await _short_note(None)


@_read_tool
async def crm_list_module_records(
    module: str, q: str | None = None, page: int = 1, page_size: int = 25,
) -> str:
    """The records of one mirrored module (an api_name from crm_list_modules),
    with their ids, and the key and type of each field that crm_aggregate can
    measure or group. q matches the record name."""
    mod = _module_name(module)
    size, number = _page_size(page_size), _page(page)
    data = await _get(f"/crm/modules/{mod}/records", {
        "q": q, "page": number, "page_size": size,
    }) or {}
    rows = data.get("rows") or []
    total = int(data.get("total") or len(rows))
    if not rows:
        return f"No {mod} records match.{await _short_note(frozenset({MODULE_PREFIX + mod}))}"
    lines = [f"{mod}: {total} records, page {number}."]
    lines.extend(
        f"- {r.get('name') or '(unnamed)'}"
        + (f" · owner {r['owner_email']}" if r.get("owner_email") else "")
        + f" (id={r.get('id')})"
        for r in rows
    )
    lines.append(_aggregate_fields(mod, data.get("field_defs") or []))
    return "\n".join(lines) + _more(total, number, size) + await _short_note(
        frozenset({MODULE_PREFIX + mod}))


def _aggregate_fields(mod: str, defs: list[Any]) -> str:
    """One line: the key and type of each field crm_aggregate takes (CRM-Q25)."""
    usable = [
        f"{d.get('key')} ({d.get('type')})" for d in defs
        if isinstance(d, dict) and str(d.get("type")) in _FIELD_USES
    ]
    if not usable:
        return f"crm_aggregate on module:{mod} can count and group by owner or month."
    more = f", and {len(usable) - MAX_ROWS} more" if len(usable) > MAX_ROWS else ""
    return (
        f"Fields for crm_aggregate on module:{mod}: {', '.join(usable[:MAX_ROWS])}"
        f"{more}. crm_list_fields says what each can do."
    )


@_read_tool
async def crm_get_module_record(module: str, record_id: str, page: int = 1) -> str:
    """One record of a mirrored module (a module from crm_list_modules and an
    id from crm_list_module_records), each field under its label. The field
    keys and types are in crm_list_fields('module:<api_name>')."""
    mod = _module_name(module)
    record = _record_uuid(record_id)
    data = await _get(f"/crm/modules/{mod}/records/{record}") or {}
    row = data.get("record") or {}
    defs = data.get("field_defs") or []
    lines = [f"{mod}: {row.get('name') or '(unnamed)'} (id={row.get('id')})"]
    if row.get("owner_email"):
        lines.append(f"- Owner: {row['owner_email']}")
    shown = _page_of(_custom_lines(row.get("fields"), defs), page, "fields")
    lines.extend(shown.items)
    if shown.line:
        lines.append(shown.line)
    return "\n".join(lines)


#: What the member reads when the route answers ``degraded`` (CRM-Q27): the
#: org is not on ``CRM_NOTE_SEARCH_ORGS``, or the embed door gave no vector.
SEARCH_OFF = (
    "Search by meaning is off for this CRM, or it is not available now. "
    "Find records by name with crm_list_records and q instead."
)

#: The line after each search answer (§18.11.1: numbers come from SQL).
SEARCH_NUMBERS = (
    "These are notes, not numbers. A count, a sum or a total must come from "
    "crm_aggregate, never from these notes."
)

#: The most hits of one search (the route's ``limit``), and the longest query.
SEARCH_MAX = 20
SEARCH_QUERY_CHARS = 500


def _note_parent(parent: Any) -> str:
    """The record a note hangs off, in words."""
    parent = parent if isinstance(parent, dict) else {}
    slug = str(parent.get("entity") or "")
    if slug in ENTITY_LABELS and parent.get("record_id"):
        return f"{ENTITY_LABELS[slug]} id={parent['record_id']}"
    if parent.get("module") and parent.get("module_record_id"):
        return f"{parent['module']} record id={parent['module_record_id']}"
    return "no record"


@_read_tool
async def crm_search_notes(query: str, limit: int = 10) -> str:
    """Find CRM notes by meaning: the notes whose text is nearest to query,
    each with its note id, its record and a short snippet. limit is 1 to 20.
    It finds text only. A count, a sum or any other number must come from
    crm_aggregate, never from these notes."""
    words = " ".join(str(query or "").split())
    if not words:
        raise RuntimeError("Give a query: the words to look for in the CRM notes.")
    if len(words) > SEARCH_QUERY_CHARS:
        raise RuntimeError(
            f"The query is too long. Use at most {SEARCH_QUERY_CHARS} characters.")
    size = max(1, min(int(limit or 10), SEARCH_MAX))
    data = await _get("/crm/notes/search", {"q": words, "limit": size}) or {}
    if data.get("degraded"):
        return SEARCH_OFF
    hits = [h for h in (data.get("hits") or []) if isinstance(h, dict)]
    if not hits:
        return f"No CRM note matches that meaning.\n{SEARCH_NUMBERS}"
    # A snippet is text that a person wrote, so it reaches the model as data
    # inside the ONE frame of `untrusted.py`.
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    lines = [
        f"CRM notes nearest to the query ({len(hits)}):",
        frame.note("Each snippet below is the text of a CRM note."),
    ]
    for hit in hits:
        at = str(hit.get("at") or "")[:10]
        lines.append(
            f"- note id={hit.get('activity_id')} · {_note_parent(hit.get('parent'))}"
            + (f" · {at}" if at else "")
        )
        lines.append(frame.wrap("CRM NOTE", str(hit.get("snippet") or "")))
    lines.append(SEARCH_NUMBERS)
    return "\n".join(lines)


#: Every tool, in one list. The skill family ``crm`` holds these names.
CRM_TOOLS: tuple[Callable[..., Awaitable[str]], ...] = (
    crm_list_records,
    crm_get_record,
    crm_list_fields,
    crm_list_pipelines,
    crm_deal_contacts,
    crm_list_owners,
    crm_report,
    crm_aggregate,
    crm_stage_dwell,
    crm_list_modules,
    crm_list_module_records,
    crm_get_module_record,
    crm_search_notes,
)

CRM_TOOL_NAMES: frozenset[str] = frozenset(fn.__name__ for fn in CRM_TOOLS)

__all__ = [
    "CRM_CLOSED", "CRM_OFF", "CRM_SHARED_ROOM", "CRM_TOOLS", "CRM_TOOL_NAMES", "MAX_ROWS",
    "SEARCH_NUMBERS", "SEARCH_OFF", "SHORT_READS_PATH", "crm_mode", *(fn.__name__ for fn in CRM_TOOLS),
]
