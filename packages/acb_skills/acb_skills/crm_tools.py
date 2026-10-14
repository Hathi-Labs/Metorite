"""CRM read tools for every agent (WS-53 CRM-C7).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 items 1 and 5.

Twelve read tools over the gateway's ``/crm`` routes. This module is their ONE
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

**An answer is text, 50 rows at most.** When more rows exist, it ends with
"More: call again with page=N". Every tool reads only, so each one carries
``read_only=True`` and ``open_world=False`` in
``tool_annotations.TOOL_ANNOTATIONS``.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Awaitable, Callable
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


class _Refused(Exception):
    """The gateway said the CRM is off, or not open to this member."""


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
    raise RuntimeError(
        f"CRM GET {path} failed ({status})" + (f": {detail}" if detail else "")
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

def _entity_slug(entity: str) -> str:
    key = (entity or "").strip().lower()
    slug = _ENTITY_ALIASES.get(key, key)
    if slug not in ENTITY_LABELS:
        raise RuntimeError(
            f"Unknown CRM record type {entity!r}. One of: {', '.join(ENTITY_LABELS)}."
        )
    return slug


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
    if not rows:
        return f"No {slug} match these filters."
    head = f"{ENTITY_LABELS[slug]}s: {total} match, page {number}."
    return "\n".join([head, *(_line(slug, r) for r in rows)]) + _more(
        total, number, size,
    )


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
    """The fields of one record type: each label, its key (for the custom
    filter of crm_list_records), its type and its picklist options. A retired
    field is marked: records keep its values, and no filter takes its key."""
    slug = _entity_slug(entity)
    # WS-53 CRM-C4a: a row with `is_system` overlays a fixed column, so it is
    # never offered as a custom key (crm_platform.md §20.7 item 11). The
    # primary filter is the route's: `GET /crm/fields/{entity}` reads
    # `AND NOT is_system` (`fields._FIELDS_SQL`). This one is a second guard.
    defs = [
        d for d in (await _get(f"/crm/fields/{slug}") or [])
        if isinstance(d, dict) and not d.get("is_system")
    ]
    if not defs:
        return f"{ENTITY_LABELS[slug]}s have no custom fields."
    shown = _page_of(defs, page, "fields")
    if not shown.items:
        return shown.line
    lines = [f"{ENTITY_LABELS[slug]} fields ({len(defs)}):"]
    for d in shown.items:
        options = [
            str(o.get("label")) for o in d.get("options") or []
            if isinstance(o, dict) and o.get("label") and not o.get("retired")
        ]
        lines.append(
            f"- {d.get('label')} (key {d.get('key')}, {d.get('type')})"
            + (" - retired" if d.get("retired") else "")
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


@_read_tool
async def crm_report(kind: str, page: int = 1) -> str:
    """A CRM report: kind 'pipeline' (forecast by open stage), 'funnel'
    (entered, conversion and median dwell by stage), 'win-loss' (the last 90
    days) or 'owners' (each owner's open deals, weighted and won)."""
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
    return f"CRM report {report}:\n" + "\n".join([*shown.items, *tail])


@_read_tool
async def crm_aggregate(
    entity: str,
    group_by: str,
    measure: str = "count",
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
) -> str:
    """Count or sum CRM records by one group. measure is 'count',
    'sum_amount' (deals) or 'weighted' (deals: amount times the effective
    probability, open and ongoing stages only). group_by is 'status',
    'owner', 'pipeline', 'source', 'created_month', 'updated_month' or
    'close_month'. Takes the filters of crm_list_records. The server
    computes every number; report them as given."""
    slug = _entity_slug(entity)
    params = _filters(
        q=q, status=status, owner=owner, pipeline_id=pipeline_id,
        created_from=created_from, created_to=created_to,
        updated_from=updated_from, updated_to=updated_to,
        close_from=close_from, close_to=close_to, custom=custom,
    )
    params["status_id"] = await _resolve_status(slug, params)
    params.update(entity=slug, group_by=group_by, measure=measure)
    data = await _get("/crm/aggregate", params) or {}
    groups = data.get("groups") or []
    money = measure != "count"

    def fmt(value: float) -> str:
        return f"INR {value:,.0f}" if money else f"{value:,.0f}"

    shown = _page_of(groups, page, "groups")
    lines = [f"{ENTITY_LABELS[slug]}s, {measure} by {group_by}:"]
    lines.extend(f"- {g['label']}: {fmt(float(g['value']))}" for g in shown.items)
    lines.append(f"Total: {fmt(float(data.get('total') or 0))}.")
    if shown.line:
        lines.append(shown.line)
    if int(data.get("omitted") or 0):
        # Past the route's MAX_GROUPS. Only a narrower filter reaches them.
        lines.append(f"({int(data['omitted'])} smaller groups are past the route's "
                     "limit. The total counts them. Narrow the filter to see them.)")
    return "\n".join(lines)


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


@_read_tool
async def crm_list_modules(page: int = 1) -> str:
    """The other Zoho modules this CRM mirrors (Products, Quotes, custom
    modules), with a record count. A Native CRM has none."""
    modules = await _get("/crm/modules")
    if not modules:
        return "This CRM mirrors no other module."
    shown = _page_of(list(modules), page, "modules")
    if not shown.items:
        return shown.line
    return "Modules:\n" + "\n".join(
        f"- {m.get('label')} (api_name {m.get('api_name')}): {m.get('count', 0)} records"
        for m in shown.items
    ) + (f"\n{shown.line}" if shown.line else "")


@_read_tool
async def crm_list_module_records(
    module: str, q: str | None = None, page: int = 1, page_size: int = 25,
) -> str:
    """The records of one mirrored module (an api_name from crm_list_modules),
    with their ids. q matches the record name."""
    mod = _module_name(module)
    size, number = _page_size(page_size), _page(page)
    data = await _get(f"/crm/modules/{mod}/records", {
        "q": q, "page": number, "page_size": size,
    }) or {}
    rows = data.get("rows") or []
    total = int(data.get("total") or len(rows))
    if not rows:
        return f"No {mod} records match."
    lines = [f"{mod}: {total} records, page {number}."]
    lines.extend(
        f"- {r.get('name') or '(unnamed)'}"
        + (f" · owner {r['owner_email']}" if r.get("owner_email") else "")
        + f" (id={r.get('id')})"
        for r in rows
    )
    return "\n".join(lines) + _more(total, number, size)


@_read_tool
async def crm_get_module_record(module: str, record_id: str, page: int = 1) -> str:
    """One record of a mirrored module (a module from crm_list_modules and an
    id from crm_list_module_records), each field under its label."""
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
)

CRM_TOOL_NAMES: frozenset[str] = frozenset(fn.__name__ for fn in CRM_TOOLS)

__all__ = [
    "CRM_CLOSED", "CRM_OFF", "CRM_SHARED_ROOM", "CRM_TOOLS", "CRM_TOOL_NAMES", "MAX_ROWS",
    "crm_mode", *(fn.__name__ for fn in CRM_TOOLS),
]
