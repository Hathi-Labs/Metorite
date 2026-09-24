"""CRM Assistant Agent — the read half + the write half (WS-26d).

A MAF agent that works the native CRM in conversation: find a lead, read the
pipeline, open a record, read what has happened to it — and, since WS-26d-write,
create a lead, move a deal, log what happened and convert a lead into a deal.
Every tool is a thin wrapper over the ``/crm/*`` gateway routes built by
WS-26a/b/c, called with the acting user's identity, so the agent inherits their
guarantees — the `feature:crm` gate, the status vocabulary, the org-wide
visibility rule (D-CRM-3), and the ≤100 page cap enforced in the list kernel
itself.

DOCTRINE — reads are free; **every write asks a human first, and fails CLOSED.**
The four write tools each ``await request_confirmation`` before any mutating
request is built, none of them passes ``non_interactive_default="approve"``, and
so a run with no delivery channel for the card writes nothing at all
(``acb_skills/ask_tools.py`` — HH-2, OWASP LLM06 excessive agency). Annotation is
not enforcement: ``@_annotate_risk(destructive=True)`` makes the permission layer
defer to that card (``permission_policy.py`` returns ``tool_destructive_defer``),
it does not raise one. So: annotate *and* confirm.

Three structural guards carry the rest, and all three live in the request path
rather than in the system prompt, because every argument here is LLM-filled from
a context full of counterparty-authored CRM text: ``_ALLOWED_METHODS`` bounds
the VERB (``GET``/``POST``/``PATCH`` — never ``DELETE``, never ``PUT``, because
no tool here removes or replaces a record), ``_entity_slug`` + ``_record_uuid``
bound the PATH, and the tools take NAMES where a person speaks names — a stage,
a lost reason — resolving them to ids against the CRM's own vocabulary instead
of asking the model to hold a UUID. Neither is a tidiness check: an unvalidated
``record_id`` is a path traversal, since httpx resolves ``..`` segments before
the request goes out (see ``_record_uuid``).

⚠️ **A write here can leave the building.** Per D-CRM-9 an agent-created row is
born ``zoho_dirty`` exactly like a human's, so with ``CRM_ZOHO_SYNC`` enabled it
queues for the live Zoho tenant. That is by design, and it is why the
confirmation gate is the fail-closed kind rather than the reversible-action kind.

The agent never touches Postgres. It has no engine, no session and no SQL: it
asks the gateway, as the person whose run it is. That is what keeps one
authorization rule — the route's — rather than two that can disagree. The path
guards are what keep it pointed at the routes that rule was written for. It also
never speaks to Zoho: the credential belongs to the sync engine (D-CRM-7/8), and
the outward hop is `sync_zoho`'s, broker-gated on its own terms.

Registered as a MAF agent (name "crm-assistant"); build_agents() is the Dynamic
Agent Loader entry point. Structure mirrors agent-email-assistant /
agent-whatsapp-assistant.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from acb_common import get_logger, get_settings

try:
    from acb_skills.tool_annotations import annotate as _annotate_risk
except ImportError:  # older platform without the annotations registry
    def _annotate_risk(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn
        return _wrap

_log = get_logger("agent.crm_assistant")


_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You are the CRM Assistant. Answer questions about leads, deals, "
    "contacts and organizations using the provided tools. You can also create "
    "a lead, move a deal, log an activity and convert a lead — each of those "
    "asks the person you are acting for to approve it first, and does nothing "
    "if they decline. You cannot delete a CRM record."
)


# ── Gateway access (user-scoped) ─────────────────────────────────────────────
# Mirrors agent-email-assistant: an internal bearer token + the acting user's
# email header, so every gateway call is judged against that person's access.

def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _current_user_email() -> str:
    """The user the agent acts for: the per-run ContextVar the executor binds,
    and nothing else.

    There was an ``ACB_AGENT_USER_EMAIL`` fallback here, justified by "the
    tool-callback context can drop ContextVars". It was one slot in a shared
    async process that no run ever cleared, so what it supplied to a run with no
    identity was the LAST run's user — and to a concurrent run, whichever tenant
    assigned it most recently. Under one-organization-per-user that email IS the
    tenant. Resolving to ``""`` instead makes :func:`_headers` refuse, which is
    the right answer rather than merely the safe one: a run nobody is attributed
    to has nothing to do, not everything."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id
        return _get_memory_user_id() or ""
    except Exception:
        return ""


def _internal_token() -> str:
    """The gateway's internal bearer token.

    ``gateway_internal_token`` IS a real Settings field
    (``acb_common/settings.py``) — it is the service-identity/LLM-key split, and
    it ships empty, which is why ``litellm_master_key`` is the fallback rather
    than the primary. The order below is load-bearing in that direction: on a
    box where the split HAS been provisioned, "simplifying" this to read
    ``litellm_master_key`` first sends the wrong token and 403s every CRM call.
    """
    settings = get_settings()
    return (
        getattr(settings, "gateway_internal_token", "")
        or getattr(settings, "litellm_master_key", "")
        or "sk-local"
    )


def _headers() -> dict[str, str]:
    """Internal bearer + the acting user, which is not optional.

    The gateway reads a bearer-matched call with no ``X-User-Email`` as the
    platform acting as ITSELF and grants SERVICE_ACCESS — every permission
    there is (``acb_auth/deps.py`` §1b). So omitting the header when the user
    was unknown did not leave the call "unscoped"; it widened it to everyone's
    data.

    CRM data is org-visible to `feature:crm` holders rather than owner-scoped
    (D-CRM-3), so the widening here is not "somebody else's records" — it is
    reaching the CRM at all on behalf of a run that nobody authorized, past a
    feature gate that exists to answer exactly that question. A run with nobody
    attributed has no question to answer. It has nothing to do, not everything.

    See ``docs/multiplayer/bff-identity.md``.
    """
    user = _current_user_email()
    if not user:
        raise RuntimeError(
            "No acting user for this run, so there is nobody to act as — "
            "refusing to call the gateway as the platform itself. Dispatch "
            "the run with user_email in its payload."
        )
    return {
        "Authorization": f"Bearer {_internal_token()}",
        "Content-Type": "application/json",
        "X-User-Email": user,
    }


#: The verbs this agent may speak, enforced HERE at the single round-trip
#: helper rather than by everyone remembering the rule.
#:
#: WS-26d-write widened this from ``{"GET"}`` — deliberately, and by exactly
#: two entries, together with the confirmation gate the read half said had to
#: come with them. ``DELETE`` and ``PUT`` are still absent and that absence is
#: load-bearing, not an oversight: **no tool here removes or replaces a CRM
#: record**, so a tool added later that reaches for one raises instead of
#: destroying data — which is the whole reason this check survived the
#: widening instead of being deleted along with the doctrine it used to carry.
_ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "POST", "PATCH"})


def _raise_if_error(resp: httpx.Response, method: str, path: str) -> None:
    """Turn a 4xx/5xx into a short user-facing error (the agent relays raised
    exceptions verbatim, so a raw httpx error reads badly)."""
    if resp.status_code < 400:
        return
    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or "")
    except Exception:  # non-JSON body
        detail = (resp.text or "")[:200]
    raise RuntimeError(
        f"CRM {method} {path} failed ({resp.status_code})"
        + (f": {detail}" if detail else "")
    )


async def _request(
    method: str, path: str, *, timeout: float = 30.0, **kwargs: Any,
) -> httpx.Response:
    """Single gateway round-trip: URL + auth headers, fire, normalize errors.

    Refuses any verb outside :data:`_ALLOWED_METHODS` before the request is
    built, so the verb doctrine cannot be broken by a caller passing a
    different method — including one this module grows later.
    """
    if method.upper() not in _ALLOWED_METHODS:
        raise RuntimeError(
            f"crm-assistant does not issue {method.upper()} {path}: its verbs "
            f"are {sorted(_ALLOWED_METHODS)}. Deleting or replacing a CRM "
            "record is not something this agent can do."
        )
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method, f"{_gateway_url()}{path}", headers=_headers(), **kwargs
        )
        _raise_if_error(resp, method, path)
        return resp


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    return (await _request("GET", path, params=params or {})).json()


# The two write verbs, and only the two. Each goes through ``_request``, so
# widening :data:`_ALLOWED_METHODS` is still the only way a verb reaches the
# wire — a helper that built its own client would be a second transport with a
# second set of rules. There is deliberately no ``_delete``/``_put``: a helper
# nobody needs is a helper somebody will find.

async def _post(path: str, payload: dict[str, Any] | None = None) -> Any:
    return (await _request("POST", path, json=payload or {})).json()


async def _patch(path: str, payload: dict[str, Any]) -> Any:
    return (await _request("PATCH", path, json=payload)).json()


# ── The four record types ────────────────────────────────────────────────────

#: URL slug → what a human calls one of them. Mirrors the gateway's own
#: ``routes/crm/core.ENTITIES`` keys, which are the only segments the API
#: serves.
ENTITY_LABELS: dict[str, str] = {
    "leads": "Lead",
    "deals": "Deal",
    "contacts": "Contact",
    "organizations": "Organization",
}

#: Singular and colloquial names a person (or an LLM) reaches for. Resolved to
#: a slug rather than refused, because "show me deal X" is the natural phrasing
#: and a 422 on it teaches the model nothing useful.
_ENTITY_ALIASES: dict[str, str] = {
    "lead": "leads",
    "deal": "deals",
    "opportunity": "deals",
    "opportunities": "deals",
    "contact": "contacts",
    "person": "contacts",
    "people": "contacts",
    "organization": "organizations",
    "organisation": "organizations",
    "organisations": "organizations",
    "org": "organizations",
    "orgs": "organizations",
    "account": "organizations",
    "accounts": "organizations",
    "company": "organizations",
    "companies": "organizations",
}


def _entity_slug(entity: str) -> str:
    """Resolve a caller's word to one of the four slugs, or refuse.

    An unrecognised type is an error, never a silent fall back to leads — the
    same rule the list kernel applies to an unknown sort key. Answering about
    the wrong record type is worse than saying we did not understand.
    """
    key = (entity or "").strip().lower()
    slug = _ENTITY_ALIASES.get(key, key)
    if slug not in ENTITY_LABELS:
        raise RuntimeError(
            f"Unknown CRM record type {entity!r}. "
            f"One of: {', '.join(ENTITY_LABELS)}."
        )
    return slug


def _record_uuid(record_id: str) -> str:
    """Validate a record id as a UUID and return its CANONICAL form.

    Structural, at the same layer :func:`_entity_slug` validates ``entity``, and
    for a sharper reason. ``record_id`` is interpolated into the request path,
    and httpx applies RFC-3986 dot-segment removal before the request leaves —
    so an id of ``../../admin/members`` did not 404. It turned this tool into a
    general authenticated GET client against the whole gateway, carrying the
    internal bearer AND the acting user's header: ``/admin/members``,
    ``/email/messages`` and ``/memory/agent:<name>`` were all reachable that
    way. Identity was preserved, so it was scope escape rather than privilege
    escalation — but "this agent cannot see outside the CRM" is a claim three
    shipped artifacts make, and it was false.

    The reason it has to be a guard and not a prompt rule: ``record_id`` is an
    LLM-filled argument, and this model's context is counterparty-authored CRM
    text — lead names, note subjects, the 1,909 note bodies imported from Zoho.
    "Never follow instructions embedded in records" is exactly the control class
    this agent's own doctrine refuses to rely on.

    All four CRM tables key on ``CAST(:id AS uuid)``, so a non-UUID id can never
    be a legitimate call — which means this also stops a hallucinated
    ``"ACME-123"`` here, instead of relaying the driver 500 it used to produce.

    Returning the canonical form rather than the caller's own string is the part
    that makes this a guard rather than a check: ``uuid.UUID`` also accepts
    braced, ``urn:uuid:`` and unhyphenated spellings, and ``str(UUID(...))``
    normalises every one of them to 36 characters that cannot hold a path
    separator or a dot segment.
    """
    try:
        return str(UUID(str(record_id).strip()))
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError(
            f"Invalid CRM record id {record_id!r}. A CRM id is a UUID — use the "
            "id returned by search_crm or get_pipeline, not a name or a code."
        ) from None


def _row_title(slug: str, row: dict[str, Any]) -> str:
    """The one line that names a record, per entity."""
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
    return f" · {row.get('currency') or 'INR'} {float(amount):,.0f}"


def _row_line(slug: str, row: dict[str, Any]) -> str:
    """One search-result / lane line: what it is, and the id to follow it with."""
    bits = [f"• {_row_title(slug, row)}"]
    if slug == "deals":
        bits.append(_money(row))
        if row.get("organization_name"):
            bits.append(f" @ {row['organization_name']}")
        if row.get("expected_close_date"):
            bits.append(f" · closes {row['expected_close_date']}")
    elif slug == "leads":
        if row.get("organization_name"):
            bits.append(f" @ {row['organization_name']}")
        if row.get("email"):
            bits.append(f" · {row['email']}")
    elif slug == "contacts":
        if row.get("title"):
            bits.append(f" · {row['title']}")
        if row.get("email"):
            bits.append(f" · {row['email']}")
    else:  # organizations
        if row.get("industry"):
            bits.append(f" · {row['industry']}")
        if row.get("website"):
            bits.append(f" · {row['website']}")
    if row.get("owner_email"):
        bits.append(f" · owner {row['owner_email']}")
    bits.append(f" (id={row.get('id')})")
    return "".join(bits)


# ── Read tools ───────────────────────────────────────────────────────────────

@_annotate_risk(read_only=True, idempotent=True)
async def search_crm(
    query: str, entity: str | None = None, limit: int = 10,
) -> str:
    """Search the CRM for leads, deals, contacts or organizations by name, email
    or company. Pass entity='leads'|'deals'|'contacts'|'organizations' to search
    one type, or omit it to search all four. Returns each match with the id you
    pass to get_record / get_timeline.

    This is substring (ILIKE) matching over each type's own searchable columns —
    lead name/email/company, deal name and next step, contact names and email,
    organization name/email/website — not semantic search. Converted leads are
    excluded, matching the app's own lead list."""
    slugs = [_entity_slug(entity)] if entity else list(ENTITY_LABELS)
    capped = max(1, min(int(limit or 10), 25))
    sections: list[str] = []
    total = 0
    for slug in slugs:
        data = await _get(f"/crm/{slug}", {"q": query, "page_size": capped})
        rows = (data or {}).get("rows") or []
        found = int((data or {}).get("total") or len(rows))
        total += found
        if not rows:
            continue
        header = f"{ENTITY_LABELS[slug]}s ({found}"
        header += f", showing {len(rows)})" if found > len(rows) else ")"
        sections.append(
            header + ":\n" + "\n".join(_row_line(slug, r) for r in rows)
        )
    if not sections:
        scope = ENTITY_LABELS[slugs[0]].lower() + "s" if entity else "the CRM"
        return f"Nothing in {scope} matches '{query}'."
    return f"Matches for '{query}' ({total}):\n\n" + "\n\n".join(sections)


@_annotate_risk(read_only=True, idempotent=True)
async def get_pipeline(owner: str | None = None, per_lane: int = 5) -> str:
    """The deal pipeline as a board: every stage in order, how many deals sit in
    it and what they are worth, plus the most recently moved deals in each.
    Pass owner='someone@example.com' to see one person's deals. Answers "how is
    the pipeline looking?" / "what's in negotiation?".

    Counts and ₹ totals cover the WHOLE stage, not just the deals listed."""
    params: dict[str, Any] = {"per_lane": max(1, min(int(per_lane or 5), 25))}
    if owner:
        params["owner"] = owner
    data = await _get("/crm/pipeline", params)
    lanes = (data or {}).get("lanes") or []
    if not lanes:
        return "The pipeline has no stages configured yet."
    scope = f" for {owner}" if owner else ""
    out = [f"Deal pipeline{scope}:"]
    grand_count = 0
    grand_amount = 0.0
    for lane in lanes:
        status = lane.get("status") or {}
        count = int(lane.get("count") or 0)
        amount = float(lane.get("amount") or 0)
        grand_count += count
        grand_amount += amount
        kind = status.get("type") or "open"
        out.append(
            f"\n{status.get('name', '?')} [{kind}] — {count} deal"
            f"{'' if count == 1 else 's'} · INR {amount:,.0f}"
        )
        for row in lane.get("rows") or []:
            out.append(_row_line("deals", row))
    out.append(
        f"\nTotal: {grand_count} deal{'' if grand_count == 1 else 's'} · "
        f"INR {grand_amount:,.0f} across {len(lanes)} stages."
    )
    return "\n".join(out)


@_annotate_risk(read_only=True, idempotent=True)
async def get_record(entity: str, record_id: str) -> str:
    """Read one CRM record in full — entity is 'leads', 'deals', 'contacts' or
    'organizations' and record_id is its id (from search_crm or get_pipeline).
    Use this before answering a question about a specific deal or person."""
    slug = _entity_slug(entity)
    record = _record_uuid(record_id)
    row = await _get(f"/crm/{slug}/{record}")
    if not isinstance(row, dict):
        return f"{ENTITY_LABELS[slug]} {record} returned no fields."
    lines = [f"{ENTITY_LABELS[slug]}: {_row_title(slug, row)} (id={row.get('id')})"]
    # Print every populated field: this is the "open the record" tool, so
    # choosing a subset here would silently hide whichever column the question
    # was about. Ids are kept — they are what the follow-up tool call needs.
    for key, value in row.items():
        if key == "id" or value in (None, "", [], {}):
            continue
        lines.append(f"• {key}: {value}")
    if slug == "leads" and row.get("converted_deal_id"):
        lines.append(
            "⚠ This lead is converted — its deal is "
            f"id={row['converted_deal_id']}."
        )
    return "\n".join(lines)


#: ``email_thread_status.status`` → the words a human uses for it. A mirror of
#: ``workbench/control_plane/src/app/crm/lib/timeline.ts::threadStatusLabel``,
#: including its rule for a value neither side recognises: show it verbatim
#: rather than drop it. The email app owns this vocabulary and may grow it.
_THREAD_STATUS_LABELS: dict[str, str] = {
    "NEEDS_REPLY": "Needs reply",
    "AWAITING": "Awaiting reply",
    "FYI": "FYI",
    "DONE": "Done",
}


def _email_line(thread: dict[str, Any]) -> str:
    """One ``email_thread`` entry as prose: sender, subject, status.

    ⚠️ **Never the snippet or any body text — D-CRM-12.** The timeline join is
    caller-scoped, so this is the asking person's own mail; but an agent answer
    does not stay with the asker, it lands in a chat transcript, and a ROOM has
    other participants who can read it. Sender+subject says a conversation
    exists; a snippet publishes its CONTENT to everybody present. The `/crm`
    UI's `EmailEntry` deliberately DOES show the snippet — the payload is
    unchanged and a browser has an audience of one. This is the one place where
    what the screen may show and what the agent may say have to differ.
    """
    name, address = thread.get("from_name"), thread.get("from_email")
    both = f"{name} <{address}>" if name and address else ""
    who = both or name or address or "unknown sender"
    raw_status = str(thread.get("status") or "").strip()
    status = _THREAD_STATUS_LABELS.get(raw_status.upper(), raw_status)
    return (
        f"email from {who}: {thread.get('subject') or '(no subject)'}"
        + (f" [{status}]" if status else "")
    )


@_annotate_risk(read_only=True, idempotent=True)
async def get_timeline(entity: str, record_id: str, limit: int = 20) -> str:
    """What has happened to a CRM record, newest first: logged notes, calls,
    meetings and tasks, every status change with how long it sat in the
    previous stage, and email threads with this record's people. A deal also
    inherits the timeline of the lead it came from, labelled as such. Use this
    to answer "what's the story with this deal?".

    ⚠️ The email entries are the ASKING PERSON's own mail — the CRM is shared
    but a mailbox is not, so somebody else looking at the same record may see
    different threads and somebody with no mailbox connected sees none. Never
    report "there has been no email" as a fact about the record. Each thread is
    given as sender, subject and status only, never its contents (D-CRM-12) —
    do not speculate about what an email said."""
    slug = _entity_slug(entity)
    record = _record_uuid(record_id)
    capped = max(1, min(int(limit or 20), 100))
    data = await _get(f"/crm/{slug}/{record}/timeline", {"limit": capped})
    entries = (data or {}).get("entries") or []
    if not entries:
        return f"No activity recorded on this {ENTITY_LABELS[slug].lower()} yet."
    out = [f"{ENTITY_LABELS[slug]} timeline ({len(entries)} entries, newest first):"]
    for entry in entries:
        inherited = " [from its lead]" if entry.get("origin") == "lead" else ""
        when = entry.get("at") or "?"
        if entry.get("kind") == "status_change":
            change = entry.get("status_change") or {}
            dwell = change.get("dwell_seconds")
            held = (
                f" after {round(int(dwell) / 86400, 1)}d in the previous stage"
                if dwell is not None else ""
            )
            out.append(
                f"• {when}{inherited} — status: "
                f"{change.get('from_status') or '—'} → "
                f"{change.get('to_status') or '?'}"
                f"{held} (by {change.get('changed_by') or 'unknown'})"
            )
            continue
        if entry.get("kind") == "email_thread":
            # A third branch, not a fall-through. The dispatch used to be
            # binary — "not a status change" meant "an activity" — so an email
            # entry rendered as `email_thread: (no subject)` with the sender,
            # subject, snippet and status all dropped. Since `_timeline` merges
            # every source and THEN truncates, a mail-heavy deal answered
            # "what's the story with this deal?" with twenty blank rows and no
            # history at all.
            out.append(
                f"• {when}{inherited} — "
                f"{_email_line(entry.get('email_thread') or {})}"
            )
            continue
        act = entry.get("activity") or {}
        body = (act.get("body") or "").strip().replace("\n", " ")
        out.append(
            f"• {when}{inherited} — {act.get('type') or entry.get('kind')}: "
            f"{act.get('subject') or '(no subject)'}"
            + (f" — {body[:160]}" if body else "")
            + (f" (by {act['created_by']})" if act.get("created_by") else "")
        )
    return "\n".join(out)


# ── Write tools (WS-26d-write) ───────────────────────────────────────────────
#
# Four rules, and every one of them is somebody's incident:
#
# 1. **Confirm first, fail closed.** Each tool awaits ``request_confirmation``
#    before it builds a mutating request, and none of them passes
#    ``non_interactive_default="approve"`` — that opt-out is reserved for
#    reversible actions, and a CRM row that queues for the live Zoho tenant
#    (D-CRM-9) is not one. A run with no channel to deliver the card therefore
#    writes NOTHING, rather than writing unattended.
# 2. **A tool never asks the model for a UUID it could look up.** The stage and
#    the lost reason are given by NAME and resolved here against the CRM's own
#    vocabulary, because "Negotiation" is what a person says and a UUID is what
#    an LLM hallucinates. An unknown name comes back as the list of real ones —
#    a refusal that teaches, not an opaque 422 relayed from the route.
# 3. **A tool never invents vocabulary.** Resolution reads; it never creates a
#    stage or a lost reason to make a request succeed.
# 4. **Identity is never an argument.** ``create_lead`` takes no
#    ``owner_email``: the route defaults it to the acting user server-side, so
#    ownership is derived from who is running rather than from what the model
#    typed — the same control class as ``_record_uuid``.
#
# Reads MAY precede the confirmation and do: a card that says "move deal
# 8f3c-… to <a stage name we have not checked exists>" is not consent, it is a
# rubber stamp. What must never precede it is a WRITE, which is what
# ``test_crm_agent_write.py`` asserts per tool.

#: What a person may log by hand. Mirrors ``activities.LOGGABLE_TYPES``:
#: ``status_change`` and ``system`` are the platform's to write, and a
#: hand-written one would be a funnel event with no transition behind it.
_LOGGABLE_TYPES: tuple[str, ...] = ("note", "call", "meeting", "task")

#: The line every confirmation card carries. D-CRM-9 is an owner decision that
#: agent writes queue for Zoho exactly like human ones, so the person being
#: asked to approve is told where this can end up — the flag's state is the
#: gateway's business, but the possibility is theirs to weigh.
_ZOHO_NOTE = (
    "Note: CRM changes queue for the Zoho tenant when the sync is enabled "
    "(D-CRM-9)."
)


#: ``request_confirmation`` truncates ``context`` at 4000 characters
#: (``ask_tools.py``). Mirrored here so the truncation is OURS and visible,
#: rather than the card silently losing whatever happened to be last.
_CARD_CONTEXT_LIMIT = 4000

_TRUNCATED = "… [truncated on this card; the full text is what gets written]"


def _fields_block(payload: dict[str, Any]) -> str:
    """The payload as the confirmation card's preformatted body.

    Rendered from the payload actually about to be sent, never re-typed from
    the arguments: a card that shows something other than what goes on the
    wire is worse than no card, because it buys a signature for the wrong act.

    ⚠️ **The fixed line goes FIRST, and the variable-length part is budgeted to
    fit under it.** ``request_confirmation`` clips ``context`` at 4000
    characters, so appending the Zoho note last meant a 4KB note body silently
    dropped exactly the warning the person was owed — while the card also
    stopped matching the wire, which is the failure this function's second
    paragraph says is worse than showing no card at all. Truncation still
    happens; it just happens where a reader can see it, and it says so.
    """
    body = "\n".join(f"{key}: {value}" for key, value in payload.items())
    budget = _CARD_CONTEXT_LIMIT - len(_ZOHO_NOTE) - 1
    if len(body) <= budget:
        return f"{_ZOHO_NOTE}\n{body}"
    keep = max(0, budget - len(_TRUNCATED) - 1)
    return f"{_ZOHO_NOTE}\n{body[:keep]}\n{_TRUNCATED}"


def _names(rows: Any, field: str) -> list[str]:
    """Each row's human-facing name, verbatim — spacing and casing preserved."""
    return [
        str(row.get(field)).strip()
        for row in rows or []
        if isinstance(row, dict) and str(row.get(field) or "").strip()
    ]


def _matches_by_name(rows: Any, field: str, wanted: str) -> list[dict[str, Any]]:
    """EVERY case-insensitive match for a vocabulary name — never just the first.

    Case-insensitive because "negotiation" and "Negotiation" are the same lane
    to everybody except a string comparison, and the model is repeating what
    somebody said in chat rather than copying the settings grid.

    ⚠️ **Returning a list rather than the first hit is the whole point.**
    Postgres UNIQUE is case-SENSITIVE, so "Closed Won" and "Closed won" can
    genuinely coexist — and the Zoho importer's ``ensure_status`` mints unseen
    lanes by name, which is exactly how a case variant appears without anybody
    deciding to create one. A first-match lookup would silently pick whichever
    the query happened to order first and move the deal into a lane nobody
    named. Two lanes with the same spoken name is a question for a human, not
    a coin toss, so the caller refuses and lists them verbatim.
    """
    target = str(wanted or "").strip().lower()
    if not target:
        return []
    return [
        row for row in rows or []
        if isinstance(row, dict)
        and str(row.get(field) or "").strip().lower() == target
    ]


def _name_list(rows: Any, field: str) -> str:
    """Every valid name, for a refusal that tells the caller what to say next."""
    found = _names(rows, field)
    return ", ".join(found) if found else "(none are configured)"


def _quoted_names(rows: Any, field: str) -> str:
    """Names with quotes around them — for an ambiguity refusal, where the
    difference between two candidates may be nothing but casing or padding and
    an unquoted list would read like the same word printed twice."""
    return ", ".join(f"'{name}'" for name in _names(rows, field))


@_annotate_risk(destructive=True, idempotent=False)
async def create_lead(
    lead_name: str,
    email: str | None = None,
    phone: str | None = None,
    organization_name: str | None = None,
    description: str | None = None,
) -> str:
    """Create a new lead in the CRM — a person or company somebody has just
    heard from and wants tracked. Pass their name, and whatever else is known:
    email, phone, the company they are from, and a description of what they
    want.

    The person you are acting for is asked to approve before anything is
    created, and nothing is created if they decline. The new lead is OWNED by
    them — you cannot create a lead on somebody else's behalf; if it should be
    somebody else's, say so and let them reassign it in the CRM.

    Use search_crm first: creating a second row for a lead the CRM already has
    is the single most common way a CRM gets worse."""
    name = str(lead_name or "").strip()
    if not name:
        return (
            "A lead needs a name. Tell me who this is — a person, or the "
            "company they are from — and I will create it."
        )
    # `source` is provenance, and "agent" is the truth about this row. It is
    # one of the four values the CHECK constraint allows, so it is also the
    # difference between a create and a 422.
    payload: dict[str, Any] = {"lead_name": name, "source": "agent"}
    for key, value in (
        ("email", email),
        ("phone", phone),
        ("organization_name", organization_name),
        ("description", description),
    ):
        cleaned = str(value or "").strip()
        if cleaned:
            payload[key] = cleaned

    from acb_skills.ask_tools import request_confirmation
    _where = f" at {payload['organization_name']}" if organization_name else ""
    if not await request_confirmation(
        title="Create this CRM lead?",
        detail=f"{name}{_where}" + (f" · {payload['email']}" if email else ""),
        context=_fields_block(payload),
    ):
        return f"Cancelled — no lead was created for {name}."
    row = await _post("/crm/leads", payload)
    created = row if isinstance(row, dict) else {}
    return (
        f"Created lead {created.get('lead_name') or name} "
        f"(id={created.get('id')}), owned by "
        f"{created.get('owner_email') or 'you'}."
    )


@_annotate_risk(destructive=True, idempotent=False)
async def update_deal_status(
    deal_id: str, stage: str, lost_reason: str | None = None,
) -> str:
    """Move a deal to a different pipeline stage. Give the stage by NAME, the
    way it reads on the board ('Negotiation', 'Closed Won') — get_pipeline
    lists them. If the stage is a lost one you must also pass lost_reason,
    again by name; the CRM refuses a lost deal with no reason recorded, and
    that rule is the point of the field rather than an obstacle to it.

    The person you are acting for approves the move before it happens, and the
    card names the deal and the stage it is going to. Moving a deal writes its
    stage history — how long it sat where — so a wrong move is visible
    afterwards rather than silent."""
    record = _record_uuid(deal_id)
    wanted = str(stage or "").strip()
    if not wanted:
        return (
            "Which stage should this deal move to? Name it the way it reads "
            "on the board — get_pipeline lists every stage in order."
        )
    # Read before asking: a confirmation card that names a deal nobody can
    # open, or a stage that does not exist, is consent bought under a
    # misdescription. Both of these are GETs — nothing has been written yet.
    deal = await _get(f"/crm/deals/{record}")
    statuses = await _get("/crm/statuses/deal")
    found = _matches_by_name(statuses, "name", wanted)
    if not found:
        return (
            f"There is no deal stage called '{wanted}', so nothing was moved. "
            f"The stages are: {_name_list(statuses, 'name')}."
        )
    if len(found) > 1:
        return (
            f"'{wanted}' matches more than one deal stage — "
            f"{_quoted_names(found, 'name')} — so nothing was moved. Ask which "
            "one is meant, or have the duplicate renamed in the CRM's settings."
        )
    target = found[0]
    lane = str(target.get("name") or wanted)
    payload: dict[str, Any] = {"status_id": str(target.get("id") or "")}
    if str(target.get("type") or "").strip().lower() == "lost":
        # A lost-type stage needs a reason on the way IN (the route 422s
        # without one), so resolve it here rather than relaying that error —
        # and resolve it by name, against the reasons that exist. Inventing
        # one, or creating one to make this call succeed, is never right: the
        # list is a deliberate vocabulary somebody curated.
        reasons = await _get("/crm/lost-reasons")
        picked = _matches_by_name(reasons, "label", lost_reason or "")
        if not picked:
            said = str(lost_reason or "").strip()
            return (
                f"'{lane}' is a lost stage, so it needs a lost reason and "
                + (f"'{said}' is not one of them. " if said else "none was given. ")
                + "Nothing was moved. Valid reasons: "
                + f"{_name_list(reasons, 'label')}."
            )
        if len(picked) > 1:
            return (
                f"'{lost_reason}' matches more than one lost reason — "
                f"{_quoted_names(picked, 'label')} — so nothing was moved. Ask "
                "which one is meant, or have the duplicate renamed."
            )
        payload["lost_reason_id"] = str(picked[0].get("id") or "")

    title = _row_title("deals", deal if isinstance(deal, dict) else {})
    from acb_skills.ask_tools import request_confirmation
    if not await request_confirmation(
        title=f"Move this deal to {lane}?",
        detail=f"{title} → {lane}",
        context=_fields_block({"deal": title, "stage": lane, **payload}),
    ):
        return f"Cancelled — {title} was not moved to {lane}."
    await _patch(f"/crm/deals/{record}", payload)
    return f"Moved {title} to {lane}."


@_annotate_risk(destructive=True, idempotent=False)
async def log_activity(
    entity: str,
    record_id: str,
    activity_type: str,
    subject: str,
    body: str | None = None,
) -> str:
    """Record that something happened to a CRM record — a note, a call, a
    meeting or a task. entity is 'leads', 'deals', 'contacts' or
    'organizations' and record_id is its id (from search_crm or get_pipeline).
    subject is the one line that will show on the timeline; body is the detail.

    The person you are acting for approves before anything is written, and the
    entry is attributed to them. Log what actually happened, in their words
    where you have them — a timeline is read later by somebody deciding what to
    do next, and an invented detail is worse there than a missing one."""
    slug = _entity_slug(entity)
    record = _record_uuid(record_id)
    # Strip BEFORE defaulting, not after: `"  "` is an unstated type, not a
    # type called "  ", and the two spellings must not disagree.
    kind = str(activity_type or "").strip().lower() or "note"
    if kind not in _LOGGABLE_TYPES:
        return (
            f"'{activity_type}' is not something you can log. One of: "
            f"{', '.join(_LOGGABLE_TYPES)}. (Stage changes are written by the "
            "pipeline itself — move the deal with update_deal_status instead.)"
        )
    line = str(subject or "").strip()
    if not line:
        return (
            "An activity needs a subject — the one line that will show on the "
            "timeline. Nothing was logged."
        )
    payload: dict[str, Any] = {"type": kind, "subject": line}
    detail = str(body or "").strip()
    if detail:
        payload["body"] = detail

    # Read the record before asking, for the same reason `update_deal_status`
    # does — and here the reason is sharper, because this tool has no other
    # variable on its card. `search_crm` routinely returns two deals whose
    # names differ by a word, and if the model picks the wrong id the card is
    # BYTE-IDENTICAL to the right one: same type, same subject, same body. The
    # approver would have had nothing to check, and there is no delete tool to
    # take the note back off the wrong record — which by D-CRM-9 is by then
    # queued for the live Zoho tenant. One GET buys the one fact that makes
    # this card checkable.
    row = await _get(f"/crm/{slug}/{record}")
    target = _row_title(slug, row if isinstance(row, dict) else {})

    from acb_skills.ask_tools import request_confirmation
    label = ENTITY_LABELS[slug].lower()
    if not await request_confirmation(
        title=f"Log this {kind} on {target}?",
        detail=f"{target} ({label}) · {kind}: {line}",
        context=_fields_block({label: target, **payload}),
    ):
        return f"Cancelled — nothing was logged on {target}: {line}"
    await _post(f"/crm/{slug}/{record}/activities", payload)
    return (
        f"Logged {kind} on {target}: {line}"
        + (" (with detail)" if detail else "")
    )


@_annotate_risk(destructive=True, idempotent=False)
async def convert_lead(
    lead_id: str,
    deal_name: str | None = None,
    amount: float | None = None,
    expected_close_date: str | None = None,
) -> str:
    """Turn a qualified lead into a deal: the CRM creates the contact and the
    organization from the lead's details (reusing existing ones where the email
    or company name already matches) and opens a deal linked to both. Pass
    deal_name, amount and expected_close_date (YYYY-MM-DD) if they are known.

    This is a one-way step — the lead leaves the lead list and the live
    conversation moves to the deal — so the person you are acting for approves
    it first, and nothing happens if they decline.

    A lead that has already been converted is not converted again: it keeps a
    link to the deal it became, and that deal is where the conversation is."""
    record = _record_uuid(lead_id)
    # Read the lead before asking anybody to approve anything: the card should
    # name the lead, and an already-converted lead is a refusal rather than a
    # question. §8 B6 — "converted" keys on `converted_deal_id` and never on
    # `converted_at`: deleting the deal SET-NULLs the link and the lead becomes
    # convertible again, while the timestamp survives as history, so a
    # timestamp check would strand that lead as un-convertible forever.
    lead = await _get(f"/crm/leads/{record}")
    row = lead if isinstance(lead, dict) else {}
    title = _row_title("leads", row)
    already = row.get("converted_deal_id")
    if already:
        return (
            f"{title} has already been converted — the live conversation is on "
            f"deal id={already}. Nothing was changed."
        )
    deal: dict[str, Any] = {}
    if str(deal_name or "").strip():
        deal["name"] = str(deal_name).strip()
    if amount is not None and str(amount).strip():
        deal["amount"] = float(amount)
    if str(expected_close_date or "").strip():
        deal["expected_close_date"] = str(expected_close_date).strip()
    payload: dict[str, Any] = {"deal": deal} if deal else {}

    from acb_skills.ask_tools import request_confirmation
    if not await request_confirmation(
        title="Convert this lead into a deal?",
        detail=f"{title} → {deal.get('name') or title}",
        context=_fields_block({"lead": title, **deal}),
    ):
        return f"Cancelled — {title} was not converted."
    result = await _post(f"/crm/leads/{record}/convert", payload)
    made = result if isinstance(result, dict) else {}
    new_deal = made.get("deal") or {}
    contact = made.get("contact") or {}
    organization = made.get("organization") or {}
    parts = [
        f"Converted {title} into deal "
        f"{new_deal.get('name') or deal.get('name') or title} "
        f"(id={new_deal.get('id')})"
    ]
    if contact.get("id"):
        parts.append(f"contact id={contact['id']}")
    if organization.get("id"):
        parts.append(f"organization id={organization['id']}")
    return " · ".join(parts) + "."


# ── MAF agent factory (Dynamic Agent Loader entry point) ─────────────────────

_TOOLS = [
    search_crm,
    get_pipeline,
    get_record,
    get_timeline,
    create_lead,
    update_deal_status,
    log_activity,
    convert_lead,
]


def _register_agent_tools() -> dict[str, Any]:
    """Tool map for the gateway's direct quick-action calls (importlib path)."""
    return {fn.__name__: fn for fn in _TOOLS}


def _llm_provider() -> dict[str, Any]:
    """BYOK provider config pointing at the gateway's /v1 (litellm SDK)."""
    settings = get_settings()
    base_url = (
        os.environ.get("LITELLM_BASE_URL", "")
        or getattr(settings, "litellm_base_url", "")
        or "http://127.0.0.1:8080"
    ).rstrip("/")
    api_key = (
        os.environ.get("LITELLM_MASTER_KEY", "")
        or getattr(settings, "litellm_master_key", "")
        or "sk-local"
    )
    return {"type": "openai", "base_url": f"{base_url}/v1", "api_key": api_key}


def build_agents() -> list[Any]:
    """Construct the CRM Assistant as a NATIVE MAF agent backed by the LiteLLM
    gateway (same pattern as agent-email-assistant: agent_framework ``Agent`` +
    ``OpenAIChatCompletionClient`` pointed at the gateway's ``/v1``, so it runs
    on the configured LiteLLM tier, never native GitHub Copilot).

    Use ``OpenAIChatCompletionClient``, NOT ``OpenAIChatClient`` — the latter
    targets OpenAI's *Responses* API, which the gateway's ``v1_compat`` shim
    does not implement. Imported lazily so the module still loads where the
    optional deps differ."""
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient
    from acb_llm.attribution import attributed_openai

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        model=os.environ.get("CRM_AGENT_MODEL", "tier-balanced"),
        # Stamp identity so v1_compat attributes this agent's model calls + cost
        # to it on the observability bus (specs/observability_e2.md §6.2).
        # Usage slice 1: `attributed_openai` adds the member, app and run to
        # EVERY request, from the run context. A fixed header cannot, because
        # one client serves everyone who chats with this agent.
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": "crm-assistant", "X-CC-Source": "chat"},
        ),
    )
    return [
        Agent(
            client=client,
            instructions=INSTRUCTIONS,
            name="crm-assistant",
            description=(
                "Works the CRM — finds leads, deals, contacts and "
                "organizations, reads the deal pipeline by stage with its ₹ "
                "totals, opens a record in full, and reads a record's history "
                "of notes, calls, meetings and stage changes. It can also "
                "create a lead, move a deal to another stage, log a note, "
                "call, meeting or task, and convert a lead into a deal — every "
                "one of those asks the person it is acting for to approve "
                "first, and does nothing if nobody is there to ask. It never "
                "deletes a CRM record."
            ),
            tools=list(_TOOLS),
        )
    ]


__all__ = ["INSTRUCTIONS", "_register_agent_tools", "build_agents"]
