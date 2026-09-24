"""Email Assistant Agent.

A MAF agent that checks the inbox, categorizes mail, manages automation rules,
takes inbox actions, and drafts context-aware replies — handing off to the
sales / task-manager agents and reading memory when an email needs their
context. Modeled on inbox-zero's (elie222/inbox-zero) assistant tool surface.

Registered as a MAF agent (name "email-assistant"); build_agents() is the
Dynamic Agent Loader entry point.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from acb_common import get_logger, get_settings

try:
    from acb_skills.tool_annotations import annotate as _annotate_risk
except ImportError:  # older platform without the annotations registry
    def _annotate_risk(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn
        return _wrap

_log = get_logger("agent.email_assistant")


async def _confirm_destructive(title: str, detail: str, context: str = "") -> bool:
    """Fail-closed HITL gate for a destructive or outward-facing tool.

    Returns True only when the user explicitly approves. Declining — OR running
    with no interactive stream to deliver the card — returns False, so an
    automated/headless caller can never silently trash mail, unsubscribe, purge
    a mailbox, or email a digest. Mirrors the confirm-before-send pattern the two
    send_* tools already use (HH-2)."""
    from acb_skills.ask_tools import request_confirmation  # noqa: PLC0415
    return await request_confirmation(title=title, detail=detail, context=context)


_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You are the Email Assistant. Help the user check, categorize, and "
    "reply to their email using the provided tools."
)


# ── Gateway access (user-scoped) ─────────────────────────────────────────────

def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _current_user_email() -> str:
    """The user the agent is acting for: the per-run ContextVar the executor
    binds, and nothing else.

    There was an ``ACB_AGENT_USER_EMAIL`` fallback here, justified by "the
    Copilot SDK runs tool callbacks in a context that can drop ContextVars". It
    was one slot in a shared async process that no run ever cleared, so what it
    supplied to a run with no identity was the LAST run's user — and to a
    concurrent run, whichever tenant assigned it most recently. Under
    one-organization-per-user that email IS the tenant. Resolving to ``""``
    instead makes :func:`_headers` refuse, which is the right answer rather than
    merely the safe one: a run nobody is attributed to has nothing to do, not
    everything."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id  # noqa: PLC0415
        return _get_memory_user_id() or ""
    except Exception:  # noqa: BLE001
        return ""


def _internal_token() -> str:
    """The gateway's internal bearer token.

    ``gateway_internal_token`` IS a real Settings field
    (``acb_common/settings.py``) — the service-identity/LLM-key split — and it
    ships empty, which is why ``litellm_master_key`` is the fallback rather than
    the primary. The order below is load-bearing in that direction: on a box
    where the split HAS been provisioned, "simplifying" this to read
    ``litellm_master_key`` first sends the wrong token and 403s every call.
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

    Failing closed is also the more correct answer here rather than merely the
    safer one: every endpoint these tools reach is inherently per-person, so a
    run with nobody attributed has no inbox, no chats and no task list to act
    on. It has nothing to do, not everything.

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


def _raise_if_error(resp: httpx.Response, method: str, path: str) -> None:
    """Surface gateway errors as a concise, user-facing message.

    The agent relays a tool's raised exception to the user, so a raw
    ``httpx.HTTPStatusError`` (status line + URL + a help link) reads badly.
    Turn 4xx/5xx into a short ``RuntimeError`` with the gateway's own
    ``detail``/``error`` message instead.
    """
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
        f"Email {method} {path} failed ({resp.status_code})"
        + (f": {detail}" if detail else "")
    )


async def _request(
    method: str,
    path: str,
    *,
    timeout: float = 30.0,
    **kwargs: Any,
) -> httpx.Response:
    """Single gateway round-trip: build the URL + auth headers, fire the
    request, and turn any 4xx/5xx into a concise user-facing error.

    All the verb helpers below delegate here so the client config, headers, and
    error handling live in exactly one place.  (Still one client per call — a
    shared pooled client is a possible perf follow-up, pending confirmation that
    the agent always runs on a single long-lived event loop.)
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method, f"{_gateway_url()}{path}", headers=_headers(), **kwargs
        )
        _raise_if_error(resp, method, path)
        return resp


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    return (await _request("GET", path, params=params or {})).json()


async def _post(path: str, body: dict[str, Any]) -> Any:
    return (await _request("POST", path, timeout=60.0, json=body)).json()


async def _patch(path: str, body: dict[str, Any]) -> Any:
    return (await _request("PATCH", path, json=body)).json()


async def _delete(path: str) -> Any:
    resp = await _request("DELETE", path)
    # DELETEs commonly return 204 with no body.
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


# ── Read / triage tools ──────────────────────────────────────────────────────

async def _account_labels() -> dict[str, str]:
    """Map ``account_id`` → human label, for tagging cross-account results.

    Used by the tools whose ``account_id`` is optional: when none is given the
    gateway spans ALL of the user's accounts, so results from different inboxes
    get mixed together with no way to tell them apart. Tagging each line with
    its account fixes that for multi-account users (a no-op for single-account).
    """
    try:
        accounts = await _get("/email/accounts")
        return {
            str(a.get("id")): (a.get("label") or a.get("email_address") or "")
            for a in (accounts or [])
            if a.get("id")
        }
    except Exception:
        return {}


async def list_accounts() -> str:
    """List the user's connected email accounts (id, address, unread count) —
    also answers "how many unread do I have?" via the per-account + total."""
    accounts = await _get("/email/accounts")
    if not accounts:
        return "No email accounts are connected."
    total = sum(a.get("unread_count", 0) for a in accounts)
    lines = [f"Connected accounts ({total} unread total):"]
    for a in accounts:
        lines.append(
            f"• {a.get('label') or a.get('email_address')} "
            f"({a.get('email_address')}) — id={a.get('id')}, "
            f"{a.get('unread_count', 0)} unread"
        )
    return "\n".join(lines)


async def search_emails(
    query: str, folder: str = "inbox", account_id: str | None = None
) -> str:
    """Search emails (subject/body/sender). Returns matches with their ids."""
    params: dict[str, Any] = {"query": query, "folder": folder}
    if account_id:
        params["account_id"] = account_id
    data = await _get("/email/messages", params)
    emails = data.get("emails", [])
    total = data.get("total", 0)
    labels = {} if account_id else await _account_labels()
    multi = len(labels) > 1
    lines = [f"Found {total} emails matching '{query}' in {folder}:"]
    for e in emails[:10]:
        frm = e.get("from_address", {}) or {}
        acct = f" [{labels.get(str(e.get('account_id')), '?')}]" if multi else ""
        lines.append(
            f"• id={e.get('id')}{acct} | {frm.get('name') or frm.get('email')}: "
            f"{e.get('subject', '(no subject)')} — {(e.get('snippet') or '')[:90]}"
        )
    if total > 10:
        lines.append(f"… and {total - 10} more")
    return "\n".join(lines)


def _fmt_recipients(lst: Any) -> str:
    """A list of {name, email} → "Name <email>, …" for display."""
    out: list[str] = []
    for it in lst or []:
        if not isinstance(it, dict):
            continue
        nm, em = (it.get("name") or "").strip(), (it.get("email") or "").strip()
        val = f"{nm} <{em}>" if nm and em else (em or nm)
        if val:
            out.append(val)
    return ", ".join(out)


async def read_email(email_id: str, full: bool = False) -> str:
    """Fetch one email by id — sender, To/Cc, subject, attachments, and body.

    Set ``full=true`` to pull the COMPLETE, untruncated body straight from the
    provider — use it when the normal read shows a cut-off body (long emails are
    capped in local storage) and you need the whole text to summarize, answer a
    detailed question, or draft an accurate reply."""
    if full:
        e = await _get(f"/email/messages/{email_id}/full-body")
        body = (e.get("body_text") or "").strip()
        if not body:
            html = (e.get("body_html") or "").strip()
            body = re.sub(r"<[^>]+>", " ", html) if html else ""
        if not body:
            return "(The provider returned an empty body for this email.)"
        return (
            f"Subject: {e.get('subject', '(no subject)')}\n"
            f"From: {e.get('from', '')}\n---\n{body[:12000]}"
        )
    e = await _get(f"/email/messages/{email_id}")
    frm = e.get("from_address", {}) or {}
    you_sent = " (you sent)" if (e.get("folder") or "").lower() == "sent" else ""
    lines = [f"From: {frm.get('name')} <{frm.get('email')}>{you_sent}"]
    to = _fmt_recipients(e.get("to_addresses"))
    cc = _fmt_recipients(e.get("cc_addresses"))
    if to:
        lines.append(f"To: {to}")
    if cc:
        lines.append(f"Cc: {cc}")
    lines.append(f"Subject: {e.get('subject', '(no subject)')}")
    lines.append(f"Date: {e.get('received_at', '')}")
    atts = [a for a in (e.get("attachments") or []) if isinstance(a, dict)]
    if atts:
        names = ", ".join(
            f"{a.get('filename') or 'file'} ({a.get('mime_type') or ''})"
            for a in atts)
        lines.append(f"Attachments: {names}")
    return "\n".join(lines) + "\n---\n" + (e.get("body_text") or "")[:4000]


async def read_thread(
    email_id: str = "", thread_id: str = "", account_id: str | None = None,
) -> str:
    """Read an ENTIRE email conversation in ONE call — every message's sender,
    date and body, oldest first.

    PREFER THIS over calling read_email repeatedly to gather a thread's context:
    one call returns the whole chain. Pass the open email's id (its thread is
    resolved automatically) or a thread_id directly."""
    tid = (thread_id or "").strip()
    acct = account_id
    if not tid:
        if not email_id:
            return "Provide an email_id or a thread_id to read a thread."
        head = await _get(f"/email/messages/{email_id}")
        tid = (head.get("thread_id") or "").strip()
        acct = acct or head.get("account_id")
        if not tid:
            return await read_email(email_id)  # standalone message, no thread
    params: dict[str, Any] = {"thread_id": tid, "page_size": "50"}
    if acct:
        params["account_id"] = str(acct)
    data = await _get("/email/messages", params)
    msgs = data.get("emails", [])
    if not msgs:
        return "No messages found in that thread."
    subject = next(
        (m.get("subject") for m in msgs if m.get("subject")), "(no subject)")
    out = [f"Thread: {subject} — {len(msgs)} message(s), oldest first:"]
    for i, e in enumerate(msgs[:25], 1):
        frm = e.get("from_address", {}) or {}
        you = " (you sent)" if (e.get("folder") or "").lower() == "sent" else ""
        body = (e.get("body_text") or e.get("snippet") or "").strip()
        out.append(
            f"[{i}] From: {frm.get('name')} <{frm.get('email')}>{you}  "
            f"Date: {e.get('received_at', '')}\n"
            f"    {body[:1500]}"
        )
    if len(msgs) > 25:
        out.append(f"… and {len(msgs) - 25} earlier message(s) omitted.")
    return "\n\n".join(out)


async def find_urgent(account_id: str | None = None) -> str:
    """Find emails that look urgent / need attention soon."""
    params: dict[str, Any] = {
        "query": "urgent OR deadline OR ASAP OR action required OR by EOD",
        "page_size": "20",
    }
    if account_id:
        params["account_id"] = account_id
    data = await _get("/email/messages", params)
    emails = data.get("emails", [])
    if not emails:
        return "No urgent emails found."
    # Tag each result with its account when the query spans multiple accounts
    # (no account_id given) so cross-account results aren't ambiguous.
    labels = {} if account_id else await _account_labels()
    multi = len(labels) > 1
    lines = ["Urgent / needs attention:"]
    for e in emails[:10]:
        frm = e.get("from_address", {}) or {}
        acct = f" [{labels.get(str(e.get('account_id')), '?')}]" if multi else ""
        lines.append(
            f"• id={e.get('id')}{acct} | {frm.get('name') or frm.get('email')}: "
            f"{e.get('subject', '(no subject)')}"
        )
    return "\n".join(lines)


async def find_needs_reply(account_id: str) -> str:
    """List threads whose latest message is inbound and awaiting your reply."""
    data = await _get(
        "/email/reply-zero",
        {"account_id": account_id, "type": "needs_reply", "limit": "30"},
    )
    threads = data.get("threads", [])
    if not threads:
        return "Nothing needs a reply — inbox zero!"
    lines = ["Needs reply:"]
    for t in threads[:15]:
        lines.append(
            f"• id={t.get('message_id')} | {t.get('from')}: {t.get('subject')}"
        )
    return "\n".join(lines)


async def find_priority(account_id: str, kind: str = "needs_reply") -> str:
    """Surface the emails that most need attention, by ``kind``:

      • ``needs_reply`` (default) — threads whose latest message is inbound and
        awaiting your reply (Reply Zero). Use for "what do I need to reply to?".
      • ``important`` — the ranked "what should I check?" list (needs-reply +
        unread + high-importance + starred + personal/support senders, minus
        newsletters/marketing/notifications/cold email).
      • ``urgent`` — mail that reads as time-sensitive (deadline / ASAP / EOD…).

    Results render as an interactive card. For a categorized breakdown across
    departments/projects, gather ids here and pass them to present_email_groups.
    """
    k = (kind or "needs_reply").strip().lower()
    if k in ("important", "priority", "check"):
        return await get_important_emails(account_id)
    if k in ("urgent", "time_sensitive", "urgent_or_important"):
        return await find_urgent(account_id)
    # Default and any unknown value → needs-reply (the most common ask).
    return await find_needs_reply(account_id)


async def get_account_overview(account_id: str) -> str:
    """High-level snapshot: totals, read-rate, top senders, sender categories."""
    overview = await _get(
        "/email/analytics/overview", {"account_id": account_id, "days": "30"}
    )
    cats = await _get("/email/senders/categories", {"account_id": account_id})
    t = overview.get("totals", {})
    lines = [
        f"Account overview (30d): {t.get('total', 0)} messages, "
        f"{t.get('unread', 0)} unread, "
        f"{round(t.get('read_rate', 0) * 100)}% read.",
        "Top senders: "
        + ", ".join(
            f"{s.get('name') or s.get('email')} ({s.get('count')})"
            for s in overview.get("top_senders", [])[:5]
        ),
    ]
    counts = cats.get("counts", {})
    if counts:
        lines.append(
            "Sender categories: "
            + ", ".join(f"{k}: {v}" for k, v in counts.items())
        )
    return "\n".join(lines)


async def query_inbox(
    account_id: str,
    query: str | None = None,
    folder: str = "inbox",
    days: int | None = None,
    sender_category: str | None = None,
    from_email: str | None = None,
    unread_only: bool = False,
    starred_only: bool = False,
    has_attachments: bool | None = None,
    importance: str | None = None,
    sort: str = "newest",
    limit: int = 25,
) -> str:
    """Search and filter the inbox to answer questions spanning MANY emails.

    Use this for inbox-wide questions — "sales-related emails in the last month",
    "unread mail from Acme", "marketing emails this week", "starred emails with
    attachments". Combine any of the filters:
      query: full-text over subject/body/sender (e.g. "sales", "invoice")
      days: only mail received in the last N days (use 30 for "last month")
      sender_category: the sender's category — one of Newsletter, Marketing,
        Receipt, Calendar, Notification, Cold Email, Personal, Support
      from_email: substring of the sender's address
      unread_only / starred_only / has_attachments / importance (high|normal|low)
      sort: newest | oldest | importance
    Returns matching emails with ids; call read_email for an id's full content.
    """
    params: dict[str, Any] = {
        "folder": folder, "account_id": account_id, "sort": sort,
        "page_size": str(max(1, min(limit, 100))),
    }
    if query:
        params["query"] = query
    if days:
        from datetime import datetime, timedelta, timezone
        params["received_after"] = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
    if sender_category:
        params["sender_category"] = sender_category
    if from_email:
        params["from_email"] = from_email
    if unread_only:
        params["is_read"] = "false"
    if starred_only:
        params["is_starred"] = "true"
    if has_attachments is not None:
        params["has_attachments"] = "true" if has_attachments else "false"
    if importance:
        params["importance"] = importance
    data = await _get("/email/messages", params)
    emails = data.get("emails", [])
    total = data.get("total", 0)
    if not emails:
        return "No emails matched those filters."
    shown = emails[:limit]
    lines = [f"Found {total} emails ({query or 'filtered'}); showing {len(shown)}:"]
    for e in shown:
        frm = e.get("from_address", {}) or {}
        flags = []
        if not e.get("is_read"):
            flags.append("unread")
        if e.get("is_starred"):
            flags.append("star")
        if e.get("has_attachments"):
            flags.append("attachment")
        flag = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"• id={e.get('id')} | {(e.get('received_at') or '')[:10]} | "
            f"{frm.get('name') or frm.get('email')}: "
            f"{e.get('subject', '(no subject)')}{flag} — "
            f"{(e.get('snippet') or '')[:80]}"
        )
    if total > len(shown):
        lines.append(f"… and {total - len(shown)} more (refine filters or raise limit)")
    return "\n".join(lines)


async def get_important_emails(account_id: str, days: int = 30) -> str:
    """The emails that most need attention — answers "what are the most important
    emails I need to check?".

    Ranks recent inbox threads by needs-reply status, unread, high importance,
    starred, and personal/support senders; excludes newsletters, marketing,
    notifications and cold email so the list stays high-signal."""
    data = await _get(
        "/email/priority",
        {"account_id": account_id, "days": days, "limit": 20},
    )
    emails = data.get("emails", [])
    if not emails:
        return "Nothing pressing — no high-priority emails to check right now."
    lines = ["Most important emails to check:"]
    for e in emails:
        lines.append(
            f"• id={e.get('message_id')} | {e.get('from')}: "
            f"{e.get('subject')} — ({e.get('reason')})"
        )
    return "\n".join(lines)


async def present_email_groups(groups_json: str) -> str:
    """Render an INTERACTIVE, CATEGORIZED board of emails in the chat — use this
    whenever you're presenting emails split into named categories (e.g. HR,
    Finance, R&D; or Urgent / This-week / FYI; or per-project / per-sender).

    This is the categorized counterpart to the flat list card: instead of one
    undifferentiated list, the UI shows each group as its own titled, collapsible
    section whose rows are fully interactive (open, archive, mark-read,
    categorize). It lets YOU decide the categories and which emails go in each,
    so the interactive board matches the breakdown you're describing in prose.

    Pass ``groups_json``: a JSON array of groups, each an object with:
      • ``title``     (str, required) — the category name, e.g. "Finance"
      • ``email_ids`` (list[str], required) — the message ids in this group
        (the ``id=…`` values from find_needs_reply / get_important_emails /
        query_inbox / search_emails results)
      • ``note``      (str, optional) — a short caption for the group

    Example::

        present_email_groups('[
          {"title": "HR", "email_ids": ["a1b2", "c3d4"],
           "note": "onboarding + leave requests"},
          {"title": "Finance", "email_ids": ["e5f6"]},
          {"title": "R&D", "email_ids": ["g7h8", "i9j0"]}
        ]')

    Gather the ids first (find_needs_reply / get_important_emails / query_inbox),
    decide the categories, then call this ONCE with every group. An id may appear
    in only one group; ids you don't own are skipped. Keep your prose summary
    short — this board carries the categorized list, so don't also print it as a
    markdown table."""
    try:
        parsed = json.loads(groups_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return (
            "Couldn't parse groups_json — it must be a JSON array of "
            f"{{title, email_ids, note?}} objects. ({exc})"
        )
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list) or not parsed:
        return "No groups given. Pass a non-empty JSON array of groups."

    # Normalise groups, preserving order and dropping duplicate ids across the
    # whole board (an email belongs to one category).
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    all_ids: list[str] = []
    for g in parsed:
        if not isinstance(g, dict):
            continue
        title = str(g.get("title") or "").strip() or "Untitled"
        note = str(g.get("note") or "").strip()
        ids_in = g.get("email_ids") or g.get("ids") or []
        ids: list[str] = []
        for i in ids_in if isinstance(ids_in, list) else []:
            sid = str(i).strip()
            if sid and sid not in seen:
                seen.add(sid)
                ids.append(sid)
                all_ids.append(sid)
        groups.append({"title": title, "note": note, "ids": ids})

    if not all_ids:
        return "No email ids in any group — nothing to show."

    # One batched, side-effect-free lookup for every row's label.
    meta: dict[str, dict[str, Any]] = {}
    try:
        res = await _post("/email/messages/summaries", {"ids": all_ids})
        for s in res.get("summaries", []):
            meta[str(s.get("id"))] = s
    except Exception:  # noqa: BLE001 — fall back to id-only rows on lookup failure
        pass

    total = sum(len(g["ids"]) for g in groups)
    out: list[str] = [
        f"Categorized emails — {total} across {len(groups)} group(s):"
    ]
    for g in groups:
        rows: list[str] = []
        for sid in g["ids"]:
            m = meta.get(sid)
            if m:
                sender = m.get("from") or "(unknown sender)"
                subject = m.get("subject") or "(no subject)"
                rows.append(f"• id={sid} | {sender}: {subject}")
            else:
                # Metadata missing (foreign/unknown id) — still render the row.
                rows.append(f"• id={sid} | (unknown sender): (unavailable)")
        header = f"## {g['title']} ({len(g['ids'])})"
        if g["note"]:
            header += f" — {g['note']}"
        out.append(header)
        out.extend(rows)
    return "\n".join(out)


# ── Inbox action tools ───────────────────────────────────────────────────────

@_annotate_risk(destructive=True)
async def manage_inbox(
    action: str,
    message_ids: list[str],
    account_id: str | None = None,
    folder: str | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> str:
    """Apply an action to one or more messages — the single "act on messages"
    tool (state, folder, and labels).

    Args:
        action: archive | trash | read | unread | star | unstar | move | label
        message_ids: ids of the messages to act on
        account_id: optional account scope
        folder: destination for ``action="move"`` (an existing folder/label —
            e.g. "Archive" or a custom folder; create it with create_label).
        add_labels / remove_labels: label NAMES to add/remove for
            ``action="label"`` (syncs to the provider).
    """
    if action == "move":
        if not folder:
            return "Provide a `folder` to move messages to."
        results = await asyncio.gather(
            *(_patch(f"/email/messages/{mid}", {"folder": folder})
              for mid in message_ids),
            return_exceptions=True,
        )
        n = sum(1 for r in results if not isinstance(r, BaseException))
        failed = len(message_ids) - n
        note = f" ({failed} failed)" if failed else ""
        return f"Moved {n} message(s) to '{folder}'{note}."
    if action == "label":
        if not add_labels and not remove_labels:
            return "Provide add_labels and/or remove_labels for action='label'."
        patch: dict[str, Any] = {}
        if add_labels:
            patch["add_labels"] = add_labels
        if remove_labels:
            patch["remove_labels"] = remove_labels
        results = await asyncio.gather(
            *(_patch(f"/email/messages/{mid}", dict(patch))
              for mid in message_ids),
            return_exceptions=True,
        )
        n = sum(1 for r in results if not isinstance(r, BaseException))
        bits = []
        if add_labels:
            bits.append(f"+{', '.join(add_labels)}")
        if remove_labels:
            bits.append(f"-{', '.join(remove_labels)}")
        failed = len(message_ids) - n
        note = f" ({failed} failed)" if failed else ""
        return f"Updated labels on {n} message(s){note}: {' '.join(bits)}."
    # Trashing is the one destructive branch here (archive/read/star are
    # reversible in a click). Confirm it, fail-closed.
    if action == "trash":
        n = len(message_ids)
        if not await _confirm_destructive(
            title=f"Move {n} message{'s' if n != 1 else ''} to Trash?",
            detail="They leave the inbox and go to the Trash folder.",
        ):
            return "Cancelled — nothing was moved to Trash."
    body: dict[str, Any] = {"action": action, "message_ids": message_ids}
    if account_id:
        body["account_id"] = account_id
    res = await _post("/email/messages/bulk", body)
    return f"{action}: affected {res.get('affected', 0)} message(s)."


async def draft_reply(email_id: str, account_id: str, save: bool = False) -> str:
    """Draft a context-aware reply to an email. Set save=true to also create a
    provider draft in the user's Drafts folder."""
    res = await _post(
        "/email/draft-reply",
        {"account_id": account_id, "message_id": email_id, "create_draft": save},
    )
    note = " (saved to Drafts)" if res.get("created") else ""
    return f"Draft{note}:\n\n{res.get('draft', '')}"


# ── Sender categorization tools ──────────────────────────────────────────────

async def categorize_senders(account_id: str) -> str:
    """Re-project sender categories from the labels the user's rules applied.

    NOT a classifier — it only rolls up existing rule labels, so it cannot
    categorize a sender whose mail the rules never labelled. To categorize MORE
    mail, use auto_categorize_inbox (projects learned patterns onto the
    leftovers) or run the rules over past mail.
    """
    await _post("/email/senders/categorize", {"account_id": account_id, "limit": 100})
    return (
        "Re-projecting sender categories from your rules' labels in the "
        "background. This only rolls up categorization that already exists — if "
        "senders are still uncategorized afterwards, their mail was never "
        "labelled by a rule; try auto_categorize_inbox."
    )


async def auto_categorize_inbox(account_id: str, apply: bool = False) -> str:
    """Categorize uncategorized inbox mail from patterns already learned.

    Projects the user's learned patterns and their per-sender / per-domain label
    history onto inbox mail the rules never reached. Runs no classifier of its
    own, so anything it cannot justify is reported as needing a rules run.

    Call with apply=False first to preview; only apply=True writes labels.
    """
    data = await _post(
        "/email/cleanup/auto-categorize",
        {"account_id": account_id, "limit": 500, "dry_run": not apply},
    )
    if apply:
        return (
            "Auto-categorize started in the background. Check the email cleaner "
            "or the assistant history in a moment for what was applied."
        )
    n = data.get("categorized", 0)
    if not n:
        return (
            f"Nothing to auto-categorize: scanned {data.get('scanned', 0)} "
            f"uncategorized email(s) and none matched a learned pattern or a "
            f"sender/domain with a consistent label history. These need the "
            f"rules to actually run over them."
        )
    by_cat = ", ".join(
        f"{v} {k}" for k, v in (data.get("by_category") or {}).items()
    )
    left = data.get("no_evidence", 0)
    return (
        f"{n} uncategorized email(s) can be categorized from existing "
        f"patterns ({by_cat})."
        + (f" {left} more have no matching pattern and need a rules run." if left
           else "")
        + " Call again with apply=true to apply."
    )


async def get_sender_categories(account_id: str) -> str:
    """Show the category vocabulary and how many senders fall in each."""
    data = await _get("/email/senders/categories", {"account_id": account_id})
    counts = data.get("counts", {})
    if not counts:
        return (
            "No senders categorized yet — the rules have not labelled this "
            "mailbox's mail. Install/run rules, or try auto_categorize_inbox. "
            f"Categories: {', '.join(data.get('categories', []))}."
        )
    return "Sender categories:\n" + "\n".join(
        f"• {k}: {v}" for k, v in counts.items()
    )


# ── Rule / automation tools ──────────────────────────────────────────────────

async def get_rules_and_settings(account_id: str) -> str:
    """List the account's automation rules and assistant settings."""
    rules = (await _get("/email/rules", {"account_id": account_id})).get("rules", [])
    settings_obj = await _get(
        "/email/assistant/settings", {"account_id": account_id}
    )
    lines = [f"{len(rules)} rule(s):"]
    for r in rules:
        actions = ", ".join(
            a["type"] + (f":{a['label']}" if a.get("label") else "")
            for a in r.get("actions", [])
        )
        state = "auto" if r.get("automated") else "manual"
        on = "on" if r.get("enabled") else "off"
        lines.append(
            f"• id={r.get('id')} | {r.get('name')} [{state}/{on}] — "
            f"if: {r.get('instructions') or '(static)'} → {actions}"
        )
    lines.append(
        f"\nSettings: auto-run={settings_obj.get('auto_run')}, "
        f"cold-blocker={settings_obj.get('cold_email_blocker')}, "
        f"about set={'yes' if settings_obj.get('about') else 'no'}."
    )
    return "\n".join(lines)


async def create_rule(
    account_id: str,
    name: str,
    instructions: str = "",
    action_type: str = "LABEL",
    label: str | None = None,
    automated: bool = True,
    from_pattern: str | None = None,
    to_pattern: str | None = None,
    subject_pattern: str | None = None,
    body_pattern: str | None = None,
    conditional_operator: str = "OR",
    run_on_threads: bool = False,
    forward_to: str | None = None,
    draft_subject: str | None = None,
    draft_content: str | None = None,
    second_action_type: str | None = None,
    second_action_label: str | None = None,
) -> str:
    """Create an automation rule (inbox-zero parity — full condition + action set).

    Conditions (all optional; combined with ``conditional_operator``):
        instructions: plain-English condition the AI matches mail against.
        from_pattern / to_pattern / subject_pattern / body_pattern: literal
            substrings matched deterministically (no LLM).
        conditional_operator: "AND" (all conditions) or "OR" (any). Default OR.
        run_on_threads: also evaluate replies in a thread, not just new mail.

    Actions:
        action_type: ARCHIVE | LABEL | MARK_READ | STAR | MARK_SPAM | TRASH |
                     MOVE_FOLDER | REPLY | FORWARD | DRAFT_EMAIL | CALL_WEBHOOK.
        label: label/folder name for LABEL / MOVE_FOLDER.
        forward_to: recipient for a FORWARD action.
        draft_subject / draft_content: subject + body for REPLY / DRAFT_EMAIL
            (omit draft_content to let the AI write the body).
        second_action_type / second_action_label: optional 2nd action (e.g.
            LABEL + ARCHIVE). For 3+ actions, call update_rule afterwards.
        automated: true = apply automatically; false = propose for approval.
    """
    def _mk_action(a_type: str, a_label: str | None = None) -> dict[str, Any]:
        a: dict[str, Any] = {"type": a_type}
        if a_label:
            a["label"] = a_label
        if a_type == "FORWARD" and forward_to:
            a["to_address"] = forward_to
        if a_type in ("REPLY", "DRAFT_EMAIL"):
            if draft_subject:
                a["subject"] = draft_subject
            if draft_content:
                a["content"] = draft_content
                a["content_manual"] = True
        return a

    actions = [_mk_action(action_type, label)]
    if second_action_type:
        actions.append(_mk_action(second_action_type, second_action_label))
    rule = {
        "account_id": account_id,
        "name": name,
        "instructions": instructions or None,
        "enabled": True,
        "automated": automated,
        "run_on_threads": run_on_threads,
        "conditional_operator": (
            "AND" if str(conditional_operator).upper() == "AND" else "OR"
        ),
        "from_pattern": from_pattern,
        "to_pattern": to_pattern,
        "subject_pattern": subject_pattern,
        "body_pattern": body_pattern,
        "actions": actions,
    }
    res = await _post("/email/rules", rule)
    return f"Created rule '{name}' (id={res.get('id')})."


async def delete_rule(account_id: str, rule_id: str) -> str:
    """Delete an automation rule permanently. Confirm with the user first —
    disabling (update_rule_state) is reversible; deleting is not."""
    await _delete(f"/email/rules/{rule_id}")
    return f"Deleted rule {rule_id}."


async def run_rules(
    account_id: str,
    scope: str = "new",
    dry_run: bool = True,
    days: int = 7,
    limit: int = 20,
    include_read: bool = True,
) -> str:
    """Run the automation rules over inbox mail, by ``scope``:

      • ``new`` (default) — recent UNPROCESSED mail. ``dry_run=true`` previews
        matches (nothing changes); ``dry_run=false`` applies them. ``limit``
        caps how many messages.
      • ``past`` — PAST mail from the last ``days`` days (inbox-zero "Process
        past emails"): applies matched rules + drafts. ``include_read=false``
        limits it to unread mail.

    Either way results stream into the History tab."""
    if (scope or "new").strip().lower() == "past":
        start = (date.today() - timedelta(days=max(1, days))).isoformat()
        res = await _post("/email/rules/process-past", {
            "account_id": account_id, "start_date": start,
            "is_test": False, "include_read": include_read,
        })
        n = res.get("count", 0)
        if not n:
            return "No emails found in that range to process."
        return (
            f"Processing {n} past email(s) from the last {days} day(s) — applied "
            "actions stream into the History tab."
        )
    await _post(
        "/email/rules/run",
        {"account_id": account_id, "limit": limit, "dry_run": dry_run},
    )
    mode = "Previewing" if dry_run else "Applying"
    return (
        f"{mode} rules over up to {limit} recent message(s); results appear in "
        "the History tab."
    )


async def update_rule(
    account_id: str,
    rule_id: str,
    instructions: str | None = None,
    from_pattern: str | None = None,
    subject_pattern: str | None = None,
    add_action_type: str | None = None,
    add_action_label: str | None = None,
    enabled: bool | None = None,
) -> str:
    """Edit an existing rule's conditions/actions, or enable/disable it.

    Use this to FIX a rule that mis-classifies mail — e.g. tighten its
    plain-English ``instructions``, add a literal ``from_pattern`` /
    ``subject_pattern``, attach another action, or turn the rule on/off. Only
    the fields you pass change; everything else on the rule is preserved.

    Args:
        instructions: new plain-English condition the AI matches mail against.
        from_pattern: literal sender substring to match (e.g. "@vendor.com").
        subject_pattern: literal subject substring to match.
        add_action_type: ARCHIVE | LABEL | MARK_READ | STAR | MARK_SPAM | TRASH |
                         MOVE_FOLDER | DRAFT_EMAIL | REPLY | FORWARD.
        add_action_label: label/folder for an added LABEL / MOVE_FOLDER action.
        enabled: set true/false to enable or disable the rule (reversible —
            prefer this over delete_rule when the user wants to pause a rule).
    """
    rules = (await _get("/email/rules", {"account_id": account_id})).get("rules", [])
    rule = next((r for r in rules if r.get("id") == rule_id), None)
    if not rule:
        return f"Rule {rule_id} not found."
    if instructions is not None:
        rule["instructions"] = instructions
    if from_pattern is not None:
        rule["from_pattern"] = from_pattern
    if subject_pattern is not None:
        rule["subject_pattern"] = subject_pattern
    if enabled is not None:
        rule["enabled"] = enabled
    if add_action_type:
        action: dict[str, Any] = {"type": add_action_type}
        if add_action_label:
            action["label"] = add_action_label
        rule.setdefault("actions", []).append(action)
    await _patch(f"/email/rules/{rule_id}", rule)
    if enabled is not None and instructions is None and from_pattern is None \
            and subject_pattern is None and not add_action_type:
        return f"Rule '{rule.get('name')}' is now {'enabled' if enabled else 'disabled'}."
    return f"Updated rule '{rule.get('name')}'."


async def learn_rule_pattern(
    account_id: str, rule_id: str, sender: str = "", exclude: bool = False,
    subject_keyword: str = "",
) -> str:
    """Teach the matcher a deterministic learned pattern for a rule.

    Provide ``sender`` (an email/domain) and/or ``subject_keyword`` (a phrase
    that appears in the subject) — at least one is required.
    ``exclude=false`` → ALWAYS apply this rule to matching mail.
    ``exclude=true``  → NEVER apply this rule to matching mail.
    Use when the user says "emails from X (or about Y) should / shouldn't be
    labelled Z". This persists and short-circuits future classification (no
    LLM needed).
    """
    if not sender and not subject_keyword:
        return ("Provide at least a sender (email/domain) or a subject_keyword "
                "(phrase in the subject) to learn from.")
    body = {
        "account_id": account_id,
        "sender": sender,
        "subject_keyword": subject_keyword or None,
        "expected": "none" if exclude else rule_id,
        "matched_rule_ids": [rule_id] if exclude else [],
    }
    await _post("/email/rules/feedback", body)
    signal = " / ".join(
        s for s in [sender and f"from {sender}",
                    subject_keyword and f'about "{subject_keyword}"'] if s
    ) or "matching"
    verb = "no longer match" if exclude else "always match"
    return f"Learned: emails {signal} will {verb} that rule."


async def update_assistant_settings(
    account_id: str,
    about: str | None = None,
    signature: str | None = None,
    auto_run: bool | None = None,
    cold_email_blocker: str | None = None,
    personal_instructions: str | None = None,
    writing_style: str | None = None,
    draft_replies: bool | None = None,
    draft_confidence: str | None = None,
    follow_up_awaiting_days: int | None = None,
    follow_up_needs_reply_days: int | None = None,
    follow_up_auto_draft: bool | None = None,
    digest_frequency: str | None = None,
    digest_categories: list[str] | None = None,
    digest_day_of_week: int | None = None,
    digest_time_of_day: str | None = None,
    digest_send_to_email: bool | None = None,
    multi_rule_execution: bool | None = None,
    sensitive_data_protection: bool | None = None,
    rule_model: str | None = None,
    draft_model: str | None = None,
    chat_model: str | None = None,
) -> str:
    """Update assistant settings. Only the fields you pass change; every other
    setting is preserved.

    Args:
        about: free-text context about the user (used when drafting).
        signature: signature appended to drafted replies.
        auto_run: run rules automatically on new mail.
        cold_email_blocker: OFF | LABEL | ARCHIVE.
        personal_instructions: global rules the assistant ALWAYS follows
            (e.g. "Never commit to dates without checking with me.").
        writing_style: tone/length/style guide for drafted replies.
        draft_replies: auto-draft replies for emails that need one.
        draft_confidence: how sure the AI must be before drafting —
            ALL_EMAILS | STANDARD | HIGH_CONFIDENCE.
        follow_up_awaiting_days: remind/label when THEY haven't replied after N
            days (0 disables). Pairs with find_follow_ups.
        follow_up_needs_reply_days: remind/label when YOU haven't replied after N
            days (0 disables).
        follow_up_auto_draft: when on, follow-up scans also draft a nudge.
        digest_frequency: scheduled digest cadence — OFF | DAILY | WEEKLY.
        digest_categories: rule names (+ "Cold Emails") to include; [] = all.
        digest_day_of_week: 0=Sun … 6=Sat (used when digest is WEEKLY).
        digest_time_of_day: "HH:MM" 24h, account-local, the digest is sent.
        digest_send_to_email: email the digest to the account address.
        multi_rule_execution: allow more than one rule per email.
        sensitive_data_protection: skip auto-drafting on sensitive-looking mail.
        rule_model / draft_model / chat_model: LiteLLM tier or model id for rule
            classification / draft writing / the chat panel (e.g. "tier-fast",
            "tier-balanced", "tier-powerful").
    """
    # Start from the current settings so a PUT preserves EVERY field this tool
    # doesn't explicitly change.
    cur = await _get("/email/assistant/settings", {"account_id": account_id})
    body: dict[str, Any] = dict(cur)
    body["account_id"] = account_id

    def setif(key: str, val: Any) -> None:
        if val is not None:
            body[key] = val

    setif("about", about)
    setif("signature", signature)
    setif("auto_run", auto_run)
    setif("cold_email_blocker", cold_email_blocker)
    setif("personal_instructions", personal_instructions)
    setif("writing_style", writing_style)
    setif("draft_replies", draft_replies)
    setif("draft_confidence", draft_confidence)
    setif("follow_up_awaiting_days", follow_up_awaiting_days)
    setif("follow_up_needs_reply_days", follow_up_needs_reply_days)
    setif("follow_up_auto_draft", follow_up_auto_draft)
    setif("digest_frequency", digest_frequency)
    setif("digest_categories", digest_categories)
    setif("digest_day_of_week", digest_day_of_week)
    setif("digest_time_of_day", digest_time_of_day)
    setif("digest_send_to_email", digest_send_to_email)
    setif("multi_rule_execution", multi_rule_execution)
    setif("sensitive_data_protection", sensitive_data_protection)
    setif("rule_model", rule_model)
    setif("draft_model", draft_model)
    setif("chat_model", chat_model)
    await _patch_settings(body)
    return "Assistant settings updated."


async def list_knowledge(account_id: str) -> str:
    """List the account's knowledge-base entries (reference snippets the
    assistant draws on when drafting replies)."""
    entries = (await _get(
        "/email/knowledge", {"account_id": account_id}
    )).get("entries", [])
    if not entries:
        return "Knowledge base is empty."
    return "Knowledge base:\n" + "\n".join(
        f"• {e.get('title')}: {(e.get('content') or '')[:80]}" for e in entries
    )


async def save_knowledge(
    account_id: str,
    title: str,
    content: str,
    knowledge_id: str | None = None,
) -> str:
    """Create or update a knowledge-base entry the assistant draws on when
    drafting replies — e.g. pricing, FAQs, policies, boilerplate, product facts.

    Omit ``knowledge_id`` to add a new entry (overwrites any with the same
    title); pass an id from list_knowledge to edit that entry in place."""
    if knowledge_id:
        entries = (await _get(
            "/email/knowledge", {"account_id": account_id}
        )).get("entries", [])
        entry = next((e for e in entries if e.get("id") == knowledge_id), None)
        if not entry:
            return f"Knowledge entry {knowledge_id} not found."
        body = dict(entry)
        body["account_id"] = account_id
        body["title"] = title
        body["content"] = content
        await _patch(f"/email/knowledge/{knowledge_id}", body)
        return f"Updated knowledge entry '{title}'."
    await _post("/email/knowledge", {
        "account_id": account_id, "title": title, "content": content,
    })
    return f"Saved knowledge entry '{title}'."


async def generate_writing_style(account_id: str) -> str:
    """Analyze the user's recent sent emails and save a writing-style guide the
    assistant follows when drafting. Use when the user asks you to learn or match
    their writing style."""
    res = await _post(
        f"/email/assistant/writing-style/generate?account_id={account_id}", {}
    )
    style = res.get("writing_style", "")
    if style:
        return f"Derived and saved this writing style:\n{style}"
    return "Could not derive a writing style yet (no sent mail to analyze)."


@_annotate_risk(destructive=True)
async def install_default_rules(account_id: str, reset: bool = False) -> str:
    """Install the recommended default rule set: To Reply, FYI, Newsletter,
    Marketing, Calendar, Receipt, Notification, Cold Email.

    ``reset=false`` (default) adds the defaults, skipping any the user already
    has. ``reset=true`` first DELETES all existing rules and reinstalls the
    defaults fresh — destructive, so always confirm with the user first."""
    if reset:
        if not await _confirm_destructive(
            title="Delete all rules and reinstall the defaults?",
            detail="Every existing rule (including ones you customised) is "
                   "deleted first, then the default set is installed fresh.",
        ):
            return "Cancelled — your rules were left unchanged."
        res = await _post(f"/email/rules/reset?account_id={account_id}", {})
        installed = res.get("installed", [])
        return (
            f"Reset rules: reinstalled {len(installed)} default rule(s) "
            f"({', '.join(installed)})."
        )
    res = await _post(
        f"/email/rules/install-presets?account_id={account_id}", {}
    )
    installed = res.get("installed", [])
    if not installed:
        return "The default rules are already installed."
    return (
        f"Installed {len(installed)} default rule(s): {', '.join(installed)}."
    )


async def _patch_settings(body: dict[str, Any]) -> Any:
    return (await _request("PUT", "/email/assistant/settings", json=body)).json()


async def find_follow_ups(account_id: str) -> str:
    """Scan NOW for threads waiting too long for a reply, label them "Follow-up",
    and — when follow-up auto-draft is on — draft nudges. Use when the user asks
    to "find follow-ups", "chase replies", or "draft follow-ups".

    Respects the configured reminder windows; if none are set, ask the user how
    many days to wait and set them via update_assistant_settings first."""
    res = await _post("/email/follow-ups/scan", {"account_id": account_id})
    if not res.get("configured"):
        return (
            "Follow-up reminder windows aren't set yet. Ask the user how many "
            "days to wait before nudging (when they haven't replied, and when "
            "you haven't), set them with update_assistant_settings "
            "(follow_up_awaiting_days / follow_up_needs_reply_days), then scan "
            "again."
        )
    scanned = res.get("scanned", 0)
    if not scanned:
        return "No threads are waiting past the reminder windows — all current."
    drafted = res.get("drafted", 0)
    note = f", drafted {drafted} nudge(s)" if drafted else ""
    return (
        f"Found {scanned} follow-up(s); labelled {res.get('labeled', 0)} "
        f'"Follow-up"{note}. Drafts (if any) are in the Drafts folder for review.'
    )


async def suggest_unsubscribes(account_id: str | None = None) -> str:
    """Surface likely newsletters/subscriptions to consider unsubscribing from."""
    params: dict[str, Any] = {"folder": "inbox", "limit": "200"}
    if account_id:
        params["account_id"] = account_id
    data = await _get("/email/senders", params)
    senders = [
        s for s in data.get("senders", [])
        if s.get("unsubscribe_link") or s.get("read_rate", 1) < 0.4
    ]
    if not senders:
        return "No obvious newsletters to unsubscribe from."
    lines = ["Unsubscribe candidates (low read-rate / has unsubscribe link):"]
    for s in senders[:10]:
        lines.append(
            f"• {s.get('name') or s.get('email')} — {s.get('count')} emails, "
            f"{round(s.get('read_rate', 0) * 100)}% read"
        )
    return "\n".join(lines)


# ── Labels / folders / send ──────────────────────────────────────────────────

async def list_labels(account_id: str) -> str:
    """List the user-applicable label/folder names on the account."""
    labels = await _get(f"/email/accounts/{account_id}/labels")
    if not labels:
        return "No user labels on this account yet."
    # Labels are {name, color} dicts; surface just the names.
    names = [
        (lbl.get("name") if isinstance(lbl, dict) else lbl) for lbl in labels
    ]
    return "Labels: " + ", ".join(n for n in names if n)


async def create_label(account_id: str, name: str) -> str:
    """Create (or reuse) a label/folder on the account."""
    res = await _post(f"/email/accounts/{account_id}/folders", {"name": name})
    return f"Label/folder ready: {res.get('name', name)}."


def _attachment_refs(attachments: list[str] | None) -> list[dict[str, Any]]:
    """Parse attachment specs into workspace-artifact refs for /email/send.

    Each spec is either ``"outputs/file.pdf"`` (a file in the email assistant's
    own workspace — e.g. one you created with write_artifact) or
    ``"<agent>:outputs/file.pdf"`` (a file produced by another agent, e.g.
    ``"sales-assistant:outputs/quote.pdf"``)."""
    refs: list[dict[str, Any]] = []
    for item in attachments or []:
        s = (item or "").strip()
        if not s:
            continue
        head = s.split(":", 1)[0]
        if ":" in s and "/" not in head and "\\" not in head:
            agent, path = s.split(":", 1)
            refs.append({"agent": agent.strip(), "path": path.strip()})
        else:
            refs.append({"path": s})
    return refs


@_annotate_risk(destructive=True, open_world=True)
async def send_email(
    account_id: str,
    body: str,
    to: list[str] | None = None,
    subject: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to_email_id: str | None = None,
    attachments: list[str] | None = None,
) -> str:
    """Send an email immediately — a new message OR a reply. Outward-facing —
    ALWAYS confirm the recipients and body with the user before calling this.

    To REPLY to an email, pass ``reply_to_email_id`` (the original's local id);
    the recipient and 'Re:' subject are derived from it automatically (and it's
    threaded), so you can omit ``to`` and ``subject``. To send a NEW message,
    pass ``to`` and ``subject``. (To leave a reply in Drafts instead of sending,
    use draft_reply.)

    Args:
        body: plain-text body.
        to: recipient address(es) — required for a new message; derived from the
            original for a reply if omitted.
        subject: subject line — derived as 'Re: …' for a reply if omitted.
        cc / bcc: optional carbon-copy recipients.
        reply_to_email_id: local id of a message this is a reply to (threads it,
            and derives to/subject when those are omitted).
        attachments: workspace artifact paths to attach. Each is either
            ``"outputs/file.pdf"`` (a file you made with write_artifact) or
            ``"<agent>:outputs/file.pdf"`` for a sub-agent's file (e.g.
            ``"sales-assistant:outputs/quote.pdf"``). Use list_artifacts to see
            what's available; write_artifact to create one first.
    """
    to = list(to or [])
    # Reply mode: fill missing recipient / subject from the original message.
    if reply_to_email_id and (not to or not subject):
        orig = await _get(f"/email/messages/{reply_to_email_id}")
        frm = orig.get("from_address", {}) or {}
        if not to:
            addr = frm.get("email", "")
            if addr:
                to = [addr]
        if not subject:
            s = orig.get("subject", "") or ""
            subject = s if s.lower().startswith("re:") else f"Re: {s}"
    if not to:
        return "No recipient — pass `to`, or `reply_to_email_id` to reply."
    subject = subject or ""

    payload: dict[str, Any] = {
        "account_id": account_id,
        "to": to,
        "subject": subject,
        "body_text": body,
    }
    if cc:
        payload["cc"] = cc
    if bcc:
        payload["bcc"] = bcc
    if reply_to_email_id:
        payload["reply_to_message_id"] = reply_to_email_id
    refs = _attachment_refs(attachments)
    if refs:
        payload["artifacts"] = refs
    # Confirm-before-send: park on a HITL card so the user approves the actual
    # send (outward-facing + irreversible). Fails CLOSED when there's no
    # interactive stream to deliver the card (HH-2) — automated callers get
    # "Send cancelled" instead of a silent send.
    from acb_skills.ask_tools import request_confirmation  # noqa: PLC0415
    _cc_note = f", cc {', '.join(cc)}" if cc else ""
    verb = "reply" if reply_to_email_id else "email"
    if not await request_confirmation(
        title=f"Send this {verb}?",
        detail=f"To {', '.join(to)}{_cc_note} · Subject: {subject or '(none)'}",
        context=body,
    ):
        return f"Send cancelled — the {verb} was not sent."
    res = await _post("/email/send", payload)
    note = f" with {len(refs)} attachment(s)" if refs else ""
    lead = "Replied to" if reply_to_email_id else "Sent email to"
    return f"{lead} {', '.join(to)}{note} (id={res.get('id', '')})."


# ── Attachments / artifacts ──────────────────────────────────────────────────

async def list_artifacts(agent_name: str = "email-assistant") -> str:
    """List files available to attach to emails. Defaults to your own workspace;
    pass another agent (e.g. 'sales-assistant', 'task-manager') to see files a
    sub-agent produced. Attach any file by passing its path in ``attachments`` —
    for another agent's file use '<agent_name>:<path>' directly (no copy step
    needed). Create new files with write_artifact."""
    data = await _get("/agent/artifacts", {"agent": agent_name})
    arts = [a for a in data.get("artifacts", []) if not a.get("is_dir")]
    if not arts:
        return f"No files in {agent_name}'s workspace yet."
    own = agent_name == "email-assistant"
    lines = [f"Files in {agent_name}'s workspace:"]
    for a in arts[:30]:
        p = a.get("path")
        spec = p if own else f"{agent_name}:{p}"
        size = a.get("size", 0)
        lines.append(f"• {spec}  ({size} bytes, {a.get('mime_type', '')})")
    lines.append("Attach any of these by passing its path in `attachments`.")
    return "\n".join(lines)


@_annotate_risk(destructive=True, open_world=True)
async def send_draft(account_id: str, draft_id: str) -> str:
    """Send an existing draft natively (Drafts → Sent, no duplicate). Shows a
    confirmation card before sending."""
    from acb_skills.ask_tools import request_confirmation  # noqa: PLC0415
    if not await request_confirmation(
        title="Send this draft?",
        detail="Send the saved draft now? (Drafts → Sent)",
    ):
        return "Send cancelled — the draft was not sent."
    await _post(
        "/email/drafts/send",
        {"account_id": account_id, "draft_id": draft_id},
    )
    return "Draft sent."


# ── Knowledge base (edit/remove) ─────────────────────────────────────────────

async def delete_knowledge(account_id: str, knowledge_id: str) -> str:
    """Delete a knowledge-base entry by id."""
    await _delete(f"/email/knowledge/{knowledge_id}")
    return f"Deleted knowledge entry {knowledge_id}."


# ── Unsubscribe / cold senders ───────────────────────────────────────────────

@_annotate_risk(destructive=True, open_world=True)
async def unsubscribe_sender(
    account_id: str,
    email: str,
    name: str | None = None,
    unsubscribe_link: str | None = None,
) -> str:
    """Actually unsubscribe from a sender and archive its existing mail.

    Performs a real server-side one-click unsubscribe (RFC 8058) for an https
    List-Unsubscribe target, or sends the unsubscribe email for a mailto: one.
    If there's no usable link or the request fails, the sender is blocked
    instead (future mail auto-archived via a provider filter). Use after
    suggest_unsubscribes once the user confirms."""
    # Outward-facing: it fires a real one-click request or SENDS an unsubscribe
    # email, and archives existing mail. Confirm, fail-closed.
    if not await _confirm_destructive(
        title=f"Unsubscribe from {email}?",
        detail="Sends a real unsubscribe request (or blocks the sender) and "
               "archives their existing mail.",
    ):
        return f"Cancelled — still subscribed to {email}."
    res = await _post("/email/unsubscribe", {
        "account_id": account_id,
        "email": email,
        "name": name,
        "unsubscribe_link": unsubscribe_link,
    })
    archived = res.get("archived", 0)
    if res.get("ok"):
        verb = ("Sent an unsubscribe email for" if res.get("method") == "mailto"
                else "Unsubscribed from")
        return (f"{verb} {email}; archived {archived} existing message(s). "
                "The sender should stop emailing you.")
    return (
        f"Couldn't auto-unsubscribe from {email} (no one-click link), so I "
        f"blocked it instead — future mail is auto-archived and {archived} "
        "existing message(s) were archived."
    )


async def keep_newsletter(account_id: str, email: str) -> str:
    """Keep receiving a sender's mail (undo an unsubscribe / mark approved)."""
    await _post("/email/newsletters", {
        "account_id": account_id, "email": email, "status": "APPROVED",
    })
    return f"Keeping {email} — marked approved."


async def list_cold_senders(account_id: str) -> str:
    """List senders flagged by the cold-email blocker."""
    data = await _get("/email/cold-senders", {"account_id": account_id})
    senders = data.get("cold_senders", [])
    if not senders:
        return "No cold senders flagged."
    lines = ["Cold senders:"]
    for s in senders[:20]:
        lines.append(f"• {s.get('from_email')} [{s.get('status')}]")
    return "\n".join(lines)


async def set_cold_sender(
    account_id: str, from_email: str, is_cold: bool = True
) -> str:
    """Flag a sender as cold (is_cold=true) or clear the flag (is_cold=false,
    'this sender is NOT cold')."""
    status = "AI_LABELED_COLD" if is_cold else "USER_REJECTED_COLD"
    await _post("/email/cold-senders", {
        "account_id": account_id, "from_email": from_email, "status": status,
    })
    verb = "flagged as cold" if is_cold else "cleared (not cold)"
    return f"{from_email} {verb}."


async def set_sender_status(account_id: str, email: str, status: str) -> str:
    """Set how a sender is treated, by ``status``:

      • ``cold``     — flag as a cold/unsolicited sender (the blocker handles it).
      • ``not_cold`` — clear the cold flag ("this sender is NOT cold").
      • ``keep``     — keep receiving their mail / approve them (undo a block or
        unsubscribe; also called 'approved').

    (To actually unsubscribe + archive a newsletter, use unsubscribe_sender.)"""
    s = (status or "").strip().lower()
    if s in ("cold", "is_cold"):
        return await set_cold_sender(account_id, email, is_cold=True)
    if s in ("not_cold", "notcold", "clear", "not cold"):
        return await set_cold_sender(account_id, email, is_cold=False)
    if s in ("keep", "approved", "approve", "keep_newsletter"):
        return await keep_newsletter(account_id, email)
    return "status must be one of: cold | not_cold | keep."


# ── Reply Zero ───────────────────────────────────────────────────────────────

async def mark_thread_done(
    account_id: str, thread_id: str, done: bool = True
) -> str:
    """Mark a Reply-Zero thread done (handled) or reopen it (done=false)."""
    await _post("/email/reply-zero/resolve", {
        "account_id": account_id, "thread_id": thread_id, "done": done,
    })
    return f"Thread {'marked done' if done else 'reopened'}."


async def reclassify_reply_zero(account_id: str) -> str:
    """Rebuild Reply Zero (To Reply / Awaiting / FYI) with the current rules.
    Runs in the background; check find_needs_reply afterwards."""
    await _post("/email/reply-zero/reclassify", {"account_id": account_id})
    return "Reply Zero is reclassifying in the background."


# ── Rules history (approve / reject / undo) ──────────────────────────────────

async def list_rule_history(account_id: str, limit: int = 15) -> str:
    """List recent rule executions (what rules did to which mail), including
    PENDING items awaiting approval and APPLIED items you can undo."""
    data = await _get(
        "/email/rules/history", {"account_id": account_id, "limit": limit}
    )
    history = data.get("history", [])
    if not history:
        return "No rule history yet."
    lines = ["Recent rule activity:"]
    for h in history[:limit]:
        acts = ", ".join(h.get("actions", []))
        lines.append(
            f"• id={h.get('id')} [{h.get('status')}] {h.get('rule_name')}: "
            f"{(h.get('subject') or '')[:50]} → {acts}"
        )
    return "\n".join(lines)


async def resolve_execution(execution_id: str, decision: str) -> str:
    """Act on a rule execution from list_rule_history:

      • ``approve`` — apply a PENDING execution's actions.
      • ``reject``  — discard a PENDING execution (no actions taken).
      • ``undo``    — reverse an already-APPLIED execution's actions.
    """
    d = (decision or "").strip().lower()
    if d == "approve":
        res = await _post(f"/email/rules/history/{execution_id}/approve", {})
        return f"Approved — applied: {', '.join(res.get('actions', []))}."
    if d == "reject":
        await _post(f"/email/rules/history/{execution_id}/reject", {})
        return "Rejected — no actions taken."
    if d == "undo":
        res = await _post(f"/email/rules/history/{execution_id}/undo", {})
        return f"Undone: reversed {', '.join(res.get('reversed', []))}."
    return "decision must be one of: approve | reject | undo."


# ── Digest ───────────────────────────────────────────────────────────────────

def _fmt_digest_sections(sections: Any) -> str:
    """Render digest sections as readable bullets instead of a raw JSON blob.

    Handles the common shapes: a list of {title/name, count, items|highlights}
    section objects, or a {section: entries} mapping. Falls back to a compact
    JSON snippet only when the shape is unrecognized."""
    lines: list[str] = []

    def _one(title: str, count: Any, items: Any) -> None:
        head = f"• {title}"
        if isinstance(count, int):
            head += f" ({count})"
        lines.append(head)
        for it in (items or [])[:5]:
            if isinstance(it, dict):
                label = (it.get("subject") or it.get("title")
                         or it.get("from") or it.get("name") or "")
                if label:
                    lines.append(f"    – {str(label)[:80]}")
            elif it:
                lines.append(f"    – {str(it)[:80]}")

    if isinstance(sections, list):
        for s in sections:
            if isinstance(s, dict):
                _one(str(s.get("title") or s.get("name") or "Section"),
                     s.get("count"), s.get("items") or s.get("highlights"))
    elif isinstance(sections, dict):
        for key, val in sections.items():
            if isinstance(val, list):
                _one(str(key), len(val), val)
            elif isinstance(val, dict):
                _one(str(key), val.get("count"),
                     val.get("items") or val.get("highlights"))
            else:
                lines.append(f"• {key}: {val}")
    if not lines:
        return json.dumps(sections, default=str)[:800]
    return "\n".join(lines)


@_annotate_risk(destructive=True, open_world=True)
async def digest(account_id: str, period: str = "day", send: bool = False) -> str:
    """Preview OR send the inbox digest for ``period`` ('day' | 'week').

    ``send=false`` (default) previews the digest — counts and highlights per
    section. ``send=true`` emails it to the account now. Confirm before sending."""
    if send:
        if not await _confirm_destructive(
            title="Email the digest now?",
            detail=f"Sends the {period} inbox digest to your own mailbox.",
        ):
            return "Cancelled — the digest was not sent."
        res = await _post(
            "/email/digest/send", {"account_id": account_id, "period": period}
        )
        return f"Digest ({period}) sent to {res.get('to', 'your inbox')}."
    data = await _get(
        "/email/digest", {"account_id": account_id, "period": period}
    )
    sections = data.get("sections", data)
    body = _fmt_digest_sections(sections)
    return f"Digest ({period}):\n{body}"


# ── Account sync ─────────────────────────────────────────────────────────────

@_annotate_risk(destructive=True)
async def sync_account(
    account_id: str, full: bool = False, purge: bool = False
) -> str:
    """Pull mail for the account from the provider now.

    Default (``full=false``) is a fast incremental sync. ``full=true`` forces a
    COMPLETE re-sync; add ``purge=true`` to delete local mail first (for
    stale/corrupt local data — confirm purge with the user). ``purge`` implies a
    full re-sync."""
    if purge and not await _confirm_destructive(
        title="Purge and re-download this mailbox?",
        detail="Deletes all locally-stored mail for the account first, then "
               "re-fetches it from the provider. Use only for stale/corrupt "
               "local data.",
    ):
        return "Cancelled — nothing was purged."
    if full or purge:
        res = await _post(
            f"/email/accounts/{account_id}/resync?purge="
            f"{'true' if purge else 'false'}", {},
        )
        n = res.get("messages_synced")
        return f"Re-synced{' (purged)' if purge else ''}: {n} message(s)."
    await _post("/email/sync", {"account_id": account_id})
    return "Sync started — new mail will appear shortly."


# ── Learned draft patterns ───────────────────────────────────────────────────

async def list_patterns(account_id: str, kind: str = "draft") -> str:
    """List the assistant's LEARNED patterns, by ``kind``:

      • ``draft`` (default) — writing preferences learned from how you edit its
        drafts (tone/length/phrasing).
      • ``rule``  — sender/subject pins learned for RULES from your Fix
        corrections ("mail from X should / shouldn't match rule Z").

    Forget any pattern with forget_pattern(pattern_id, kind)."""
    k = (kind or "draft").strip().lower()
    if k in ("rule", "rules", "classification"):
        data = await _get("/email/rules/patterns", {"account_id": account_id})
        patterns = data.get("patterns", [])
        if not patterns:
            return "No learned rule patterns yet."
        lines = ["Learned rule patterns (from your Fix corrections):"]
        for p in patterns[:30]:
            verb = "never match" if p.get("exclude") else "always match"
            rule = p.get("rule_name") or p.get("rule_id") or "a rule"
            lines.append(
                f"• id={p.get('id')} [{p.get('pattern_type')}={p.get('value')}] "
                f"{verb} '{rule}'"
            )
        return "\n".join(lines)
    data = await _get("/email/learned-patterns", {"account_id": account_id})
    patterns = data.get("patterns", [])
    if not patterns:
        return "No learned draft patterns yet."
    lines = ["Learned draft preferences:"]
    for p in patterns[:20]:
        scope = p.get("scope_value") or p.get("scope_type") or "global"
        lines.append(f"• id={p.get('id')} [{scope}] {p.get('pattern')}")
    return "\n".join(lines)


async def forget_pattern(pattern_id: str, kind: str = "draft") -> str:
    """Forget a learned pattern by id. ``kind`` selects which store it's from:
    ``draft`` (writing preferences) or ``rule`` (rule-classification pins) —
    matching the id you got from list_patterns(kind=…)."""
    k = (kind or "draft").strip().lower()
    if k in ("rule", "rules", "classification"):
        await _delete(f"/email/rules/patterns/{pattern_id}")
        return f"Forgot rule pattern {pattern_id}."
    await _delete(f"/email/learned-patterns/{pattern_id}")
    return f"Forgot learned pattern {pattern_id}."


async def list_senders(
    account_id: str | None = None,
    view: str = "top",
    folder: str = "inbox",
    limit: int = 25,
) -> str:
    """Look at who emails you, by ``view``:

      • ``top`` (default) — the biggest senders by volume (with unread count and
        whether a one-click unsubscribe exists). "Who emails me most?".
      • ``categories`` — the sender-category vocabulary and how many senders fall
        in each. Empty means the rules have not labelled this mailbox's mail yet
        — the fix is rules (or auto_categorize_inbox), not re-running the rollup.
      • ``unsubscribe`` — likely newsletters/subscriptions to consider cutting
        (low read-rate or an unsubscribe link).
      • ``cold`` — senders flagged by the cold-email blocker.

    Act on a sender with set_sender_status (cold / not cold / keep) or
    unsubscribe_sender.
    """
    v = (view or "top").strip().lower()
    if v in ("categories", "category"):
        return await get_sender_categories(account_id or "")
    if v in ("unsubscribe", "unsubscribes", "newsletters"):
        return await suggest_unsubscribes(account_id)
    if v in ("cold", "cold_senders"):
        return await list_cold_senders(account_id or "")
    params: dict[str, Any] = {
        "folder": folder, "limit": str(max(1, min(limit, 200))),
    }
    if account_id:
        params["account_id"] = account_id
    data = await _get("/email/senders", params)
    senders = data.get("senders", []) if isinstance(data, dict) else (data or [])
    if not senders:
        return "No senders found."
    lines = ["Top senders by volume:"]
    for s in senders[:limit]:
        rr = s.get("read_rate")
        rr_str = f", {round((rr or 0) * 100)}% read" if rr is not None else ""
        unsub = " [has unsubscribe]" if s.get("unsubscribe_link") else ""
        lines.append(
            f"• {s.get('name') or s.get('email')} <{s.get('email')}> — "
            f"{s.get('count', 0)} emails, {s.get('unread', 0)} unread"
            f"{rr_str}{unsub}"
        )
    return "\n".join(lines)


async def create_rules_from_prompt(account_id: str, prompt: str) -> str:
    """Create automation rule(s) from a PLAIN-ENGLISH description (inbox-zero's
    natural-language rule flow) — e.g. "Label anything from my bank as Finance
    and archive it", or describe several rules at once. The AI turns the
    description into structured rules and creates them. Confirm the description
    with the user first; afterwards summarize what was created. For precise
    single-rule control (specific conditions/actions), prefer create_rule."""
    res = await _post(
        "/email/rules/generate", {"account_id": account_id, "prompt": prompt}
    )
    created = res.get("created", []) or []
    if not created:
        return (
            "Couldn't turn that into a rule: "
            f"{res.get('error', 'try rephrasing the description.')}"
        )
    names = ", ".join(f"'{c.get('name', '?')}' (id={c.get('id')})" for c in created)
    return f"Created {len(created)} rule(s): {names}."


async def test_rule_match(
    account_id: str,
    email_id: str | None = None,
    subject: str | None = None,
    from_email: str | None = None,
    body: str | None = None,
) -> str:
    """Preview which automation rule WOULD match an email, without applying
    anything. Pass an `email_id`, or a pasted sample (`subject` / `from_email` /
    `body`). Use to debug why mail is (or isn't) being labelled/handled as
    expected before tweaking a rule with update_rule / learn_rule_pattern."""
    payload: dict[str, Any] = {"account_id": account_id}
    if email_id:
        payload["email_id"] = email_id
    if subject:
        payload["subject"] = subject
    if from_email:
        payload["from_email"] = from_email
    if body:
        payload["body"] = body
    res = await _post("/email/rules/test", payload)
    if not res.get("matched"):
        return res.get("reason") or "No rule matched this email."
    rule = res.get("rule", {}) or {}
    actions = ", ".join(
        a.get("type", "") + (f":{a['label']}" if a.get("label") else "")
        for a in res.get("actions", [])
    )
    return (
        f"Matched rule '{rule.get('name')}' (id={rule.get('id')}): "
        f"{res.get('reason', '')} → {actions or '(no actions)'}"
    )


# ── Tool registry ────────────────────────────────────────────────────────────

# Tools attached to the MAF agent. call_agent / remember / save_memory /
# web_search are injected by the executor, so the agent can hand off to the
# sales / task-manager agents and read memory without listing them here.
_TOOLS = [
    # Read / triage
    list_accounts,
    query_inbox,
    read_email,
    read_thread,
    find_priority,
    get_account_overview,
    present_email_groups,
    # Inbox actions
    manage_inbox,
    list_labels,
    create_label,
    # Drafting / sending
    draft_reply,
    send_email,
    send_draft,
    # Attachments / artifacts
    list_artifacts,
    # Senders / categorization
    categorize_senders,
    auto_categorize_inbox,
    list_senders,
    # Rules + history
    get_rules_and_settings,
    create_rule,
    create_rules_from_prompt,
    update_rule,
    delete_rule,
    run_rules,
    test_rule_match,
    learn_rule_pattern,
    install_default_rules,
    list_rule_history,
    resolve_execution,
    # Assistant config
    update_assistant_settings,
    list_knowledge,
    save_knowledge,
    delete_knowledge,
    generate_writing_style,
    list_patterns,
    forget_pattern,
    # Follow-ups / reply zero
    find_follow_ups,
    mark_thread_done,
    reclassify_reply_zero,
    # Unsubscribe / cold senders
    unsubscribe_sender,
    set_sender_status,
    # Digest
    digest,
    # Account sync
    sync_account,
]


def _register_agent_tools() -> dict[str, Any]:
    """Tool map for the gateway's direct quick-action calls (importlib path)."""
    return {fn.__name__: fn for fn in _TOOLS}


# ── MAF agent factory (Dynamic Agent Loader entry point) ─────────────────────

def _llm_provider() -> dict[str, Any]:
    """BYOK provider config pointing at the gateway's /v1 (litellm SDK).

    Prefer the gateway's real key from Settings (``litellm_master_key``) over a
    bare ``sk-local`` fallback — an unauthenticated /v1 call makes the Copilot
    SDK drop to a NATIVE Copilot session (402). The executor also re-applies this
    provider for Copilot-SDK agents, but keep it correct here for any path that
    doesn't (e.g. direct quick-action tool calls)."""
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
    """Construct the Email Assistant as a NATIVE MAF agent backed by the LiteLLM
    gateway.

    A pure tool+instructions assistant doesn't need the GitHub Copilot SDK's
    shell/file/git/session machinery (that's for coding agents). So we use
    agent_framework's native ``Agent`` with an OpenAI-compatible client pointed
    at the gateway's ``/v1``. The gateway resolves the ``tier-balanced`` alias to
    the configured provider model (DeepSeek), so the agent always runs on the
    chosen LiteLLM tier — never native GitHub Copilot. Imported lazily so the
    module still loads where the optional deps differ.

    Use ``OpenAIChatCompletionClient`` (the Chat Completions client), NOT
    ``OpenAIChatClient`` — the latter targets OpenAI's *Responses* API
    (``client.responses.create`` → ``POST /v1/responses``), which the gateway's
    ``v1_compat`` shim does not implement, so it 404s with
    ``{'detail': 'Not Found'}``. The gateway only serves ``/v1/chat/completions``
    (same client the orchestrator MAF agent uses)."""
    from agent_framework import Agent  # noqa: PLC0415
    from agent_framework.openai import OpenAIChatCompletionClient  # noqa: PLC0415
    from acb_llm.attribution import attributed_openai

    prov = _llm_provider()  # {type, base_url=…/v1, api_key=gateway master key}
    client = OpenAIChatCompletionClient(
        model=os.environ.get("EMAIL_AGENT_MODEL", "tier-balanced"),
        # Stamp identity so the gateway (v1_compat) attributes this agent's model
        # calls + cost to it on the observability bus. Fail-soft (absent header →
        # source="chat", no agent). See specs/observability_e2.md Phase 6.2.
        # Usage slice 1: `attributed_openai` adds the member, app and run to
        # EVERY request, from the run context. A fixed header cannot, because
        # one client serves everyone who chats with this agent.
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": "email-assistant", "X-CC-Source": "chat"},
        ),
    )
    return [
        Agent(
            client=client,
            instructions=INSTRUCTIONS,
            name="email-assistant",
            description=(
                "Reads, triages, categorizes, automates, and drafts email; "
                "manages rules, follow-ups, and the knowledge base."
            ),
            tools=list(_TOOLS),
        )
    ]


__all__ = ["build_agents", "INSTRUCTIONS", "_register_agent_tools"]
