"""WhatsApp Assistant Agent.

A MAF agent that gives the founder conversational command of their WhatsApp
Business inbox: read the triage brief, see what dealers owe them, collapse a
noisy group into a paragraph, transcribe a voice note, and draft replies /
follow-up nudges in their WhatsApp voice.

Every tool is a thin, user-scoped wrapper over the ``/whatsapp/*`` gateway routes
built for the vertical, so the agent inherits all their guarantees:
deterministic-first classification, sentinel-on-failure drafting, the 24h-window
send regime, and official Cloud API only.

DOCTRINE — the companion DRAFTS, the founder SENDS. There is deliberately no
send tool here: a free-form reply is the founder's own tap (the human in the
loop), and one-to-many goes through the Action Broker's approval gate. So the
agent never speaks in their name unprompted. A change to the setup (P-4b, task
3961) shows one card first, and it sends no message.

Registered as a MAF agent (name "whatsapp-assistant"); build_agents() is the
Dynamic Agent Loader entry point. Structure mirrors agent-email-assistant.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from acb_common import get_logger, get_settings
from acb_common.card_body import CARD_CLIP, one_line
from acb_common.paging import prefix_limit, prefix_page
from acb_common.settings import internal_auth_headers
from acb_common.tool_refusals import GatewayRefusal

# H-236: every tool states ``open_world``. Each one is a read, a draft or a
# card-gated change over the gateway's own ``/whatsapp/*`` routes, and none
# sends (the DOCTRINE above), so each says ``open_world=False``. A run that a
# covered Projects run calls keeps them. A future send tool must say
# ``open_world=True``. A new write that shows a card also joins
# ``egress.HELD_OFF_OWN_WRITES``. A WhatsApp read marks no chat (task 4010), so
# for injected WhatsApp text the approval card is the only guard.
try:
    from acb_skills.tool_annotations import annotate as _annotate_risk
except ImportError:  # older platform without the annotations registry
    def _annotate_risk(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn
        return _wrap

_log = get_logger("agent.whatsapp_assistant")


_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
_INSTRUCTIONS_TEXT = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You are the WhatsApp Assistant. Help the user triage, understand, and "
    "draft replies to their WhatsApp Business messages using the provided tools."
)

#: The name this agent runs under, and the name ``NARROWING_AGENTS`` lists.
AGENT_NAME = "whatsapp-assistant"

#: WS-48 N4: the block of ``instructions.md`` that teaches ``narrow_and_read``.
#: Only a build that holds the tool keeps it (:func:`_instructions`), so the
#: prompt never names a tool that the agent does not hold.
_NARROW_START = "<!-- narrowing:start -->"
_NARROW_END = "<!-- narrowing:end -->"


def _instructions(with_narrowing: bool) -> str:
    """The instructions, with the narrowing block only when the tool is held."""
    text = _INSTRUCTIONS_TEXT
    start = text.find(_NARROW_START)
    end = text.find(_NARROW_END, start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return text
    tail = text[end + len(_NARROW_END):]
    if with_narrowing:
        return text[:start] + text[start + len(_NARROW_START):end] + tail
    return text[:start].rstrip("\n") + "\n\n" + tail.lstrip("\n")


#: The instructions of a build with no ``narrow_and_read`` (the flag off).
INSTRUCTIONS = _instructions(False)


# ── Gateway access (user-scoped) ─────────────────────────────────────────────
# Mirrors agent-email-assistant: an internal bearer token + the acting user's
# email header so every gateway call is scoped to the founder's own accounts.

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
        **internal_auth_headers(),
        "Content-Type": "application/json",
        "X-User-Email": user,
    }


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
    # One refusal of the one seam, and it keeps the status (tool_failure_log
    # section 4.2). It is a RuntimeError, as before.
    raise GatewayRefusal(
        f"WhatsApp {method} {path} failed ({resp.status_code})"
        + (f": {detail}" if detail else ""),
        status=resp.status_code,
    )


async def _request(
    method: str, path: str, *, timeout: float = 30.0, **kwargs: Any,
) -> httpx.Response:
    """Single gateway round-trip: URL + auth headers, fire, and normalize errors.
    All verb helpers delegate here so config lives in one place."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method, f"{_gateway_url()}{path}", headers=_headers(), **kwargs
        )
        _raise_if_error(resp, method, path)
        return resp


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    return (await _request("GET", path, params=params or {})).json()


async def _post(path: str, body: dict[str, Any] | None = None) -> Any:
    return (await _request("POST", path, timeout=90.0, json=body or {})).json()


# ── small helpers ─────────────────────────────────────────────────────────────

def _acct_hint(account_id: str | None) -> str:
    return f"?account_id={account_id}" if account_id else ""


async def _default_account_id() -> str | None:
    """First connected account, so single-number founders can omit account_id."""
    accts = await _get("/whatsapp/accounts")
    return str(accts[0]["id"]) if accts else None


# ── Read / triage tools ───────────────────────────────────────────────────────

@_annotate_risk(open_world=False)
async def list_whatsapp_accounts() -> str:
    """List the founder's connected WhatsApp Business numbers (id, number, name,
    sync status). Start here when a tool needs an account_id and none was given."""
    accts = await _get("/whatsapp/accounts")
    if not accts:
        return "No WhatsApp Business numbers are connected yet."
    lines = ["Connected WhatsApp numbers:"]
    for a in accts:
        lines.append(
            f"• {a.get('display_name') or a.get('phone_number')} "
            f"({a.get('phone_number')}) — id={a.get('id')}, "
            f"sync={a.get('sync_status', 'idle')}"
        )
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def whatsapp_brief(account_id: str | None = None) -> str:
    """The WhatsApp morning brief: how many need a reply / are waiting / are muted,
    the top chats that need the founder first, promises the founder made that
    aren't yet tasks, and what dealers owe the founder. Answers "what's on my
    WhatsApp?" / "anything urgent on WhatsApp?"."""
    data = await _get(f"/whatsapp/digest{_acct_hint(account_id)}")
    counts = data.get("counts", {}) or {}
    lines = [
        "WhatsApp brief — "
        f"{counts.get('needs_reply', 0)} need reply · "
        f"{counts.get('waiting', 0)} waiting · "
        f"{counts.get('groups', 0)} groups · "
        f"{counts.get('muted', 0)} muted",
    ]
    needs = data.get("needs_you", []) or []
    if needs:
        lines.append("\nNeeds you first:")
        for n in needs:
            intent = f" [{n['intent']}]" if n.get("intent") else ""
            lines.append(
                f"• {n.get('name', '?')}{intent}: {(n.get('snippet') or '')[:90]} "
                f"(chat_id={n.get('chat_id')})"
            )
    watch = data.get("commitment_watch", []) or []
    if watch:
        lines.append("\nYour open promises:")
        for c in watch:
            due = f" (due {c['due_hint']})" if c.get("due_hint") else ""
            flag = "" if c.get("has_task") else " — not yet a task"
            lines.append(f"• {c.get('text', '')[:90]}{due}{flag}")
    waiting = data.get("waiting_on", []) or []
    if waiting:
        lines.append("\nWaiting on them (nudgeable):")
        for w in waiting:
            due = f" (said {w['due_hint']})" if w.get("due_hint") else ""
            lines.append(
                f"• {w.get('text', '')[:90]}{due} (commitment_id={w.get('id')})")
    if not (needs or watch or waiting):
        lines.append("\nNothing needs you right now. Calm inbox. 🙏")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def list_whatsapp_chats(
    stream: str = "needs_reply", account_id: str | None = None, offset: int = 0,
) -> str:
    """List chats in a triage stream: 'needs_reply', 'waiting', 'groups', or
    'all'. Returns each chat's name, last snippet, and chat_id. 20 a page:
    offset reads the next."""
    # `/whatsapp/chats` has a LIMIT (at most 200) and no OFFSET. The tool sent
    # no limit before, so the route's default of 50 read as "every chat".
    start, size, ask = prefix_limit(offset, 20, default=20, cap=200)
    params: dict[str, Any] = {"stream": stream, "limit": ask}
    if account_id:
        params["account_id"] = account_id
    chats = await _get("/whatsapp/chats", params)
    if not chats:
        return f"No chats in the '{stream}' stream."
    page = prefix_page(chats, start, size, cap=200, asked=ask, noun="chats")
    if not page.items:
        return page.line
    lines = [f"Chats in '{stream}':"]
    for c in page.items:
        status = f" [{c['status']}]" if c.get("status") else ""
        win = "" if c.get("window_open", True) else " · window closed"
        lines.append(
            f"• {c.get('name') or c.get('wa_chat_id')}{status}{win}: "
            f"{(c.get('last_snippet') or '')[:80]} (chat_id={c.get('id')})"
        )
    if page.line:
        lines.append(page.line)
    return "\n".join(lines)


def _fmt_message(m: dict[str, Any]) -> str:
    who = "You" if m.get("direction") == "out" else (m.get("sender_name") or "Them")
    body = m.get("body_text") or ""
    if not body and m.get("transcript_text"):
        body = f"🎙 “{m['transcript_text']}”"
    elif m.get("kind") not in ("text", None) and not body:
        body = f"[{m.get('kind')}]"
    return f"{who}: {body[:200]}"


@_annotate_risk(open_world=False)
async def read_whatsapp_chat(chat_id: str, limit: int = 20, offset: int = 0) -> str:
    """Read the newest `limit` messages of a chat (shown oldest→newest), including voice-note
    transcripts. Use before drafting a reply or answering a question about a
    conversation. offset counts back from the newest: offset=20 reads the 20
    before them."""
    # The route gives the newest LIMIT rows (at most 500), oldest first, and
    # has no OFFSET. So the page is cut from the newest end.
    start, size, ask = prefix_limit(offset, limit, default=20, cap=500)
    msgs = await _get(f"/whatsapp/chats/{chat_id}/messages", {"limit": ask})
    if not msgs:
        return "No messages in this chat."
    page = prefix_page(list(reversed(msgs)), start, size, cap=500, asked=ask,
                       noun="messages")
    if not page.items:
        return page.line
    lines = [_fmt_message(m) for m in reversed(page.items)]
    if page.line:
        lines.append(page.line)
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def search_whatsapp(query: str, account_id: str | None = None,
                          offset: int = 0) -> str:
    """Search the founder's WhatsApp history by meaning AND keyword (message
    bodies AND voice-note transcripts). Returns matches with chat_id + message_id.
    Semantic re-ranking applies when it's enabled; otherwise this is pure
    keyword search (recall is identical either way)."""
    # hybrid re-ranks by semantic closeness when enabled, and falls back to pure
    # lexical otherwise — so passing it is always safe.
    # `/whatsapp/search` has a LIMIT (at most 201) and no OFFSET.
    start, size, ask = prefix_limit(offset, 15, default=15, cap=200)
    params: dict[str, Any] = {"q": query, "hybrid": "true", "limit": ask}
    if account_id:
        params["account_id"] = account_id
    msgs = await _get("/whatsapp/search", params)
    if not msgs:
        return f"No messages match '{query}'."
    page = prefix_page(msgs, start, size, cap=200, asked=ask, noun="matches",
                       rest="Other matches", narrow="Use more exact words to reach them.")
    if not page.items:
        return page.line
    lines = [f"Matches for '{query}':"]
    for m in page.items:
        lines.append(
            f"• {_fmt_message(m)} (chat_id={m.get('chat_id')}, "
            f"message_id={m.get('id')})"
        )
    if page.line:
        lines.append(page.line)
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def whatsapp_waiting_on(account_id: str | None = None) -> str:
    """List the open promises OTHERS made to the founder (what to chase). Each
    carries a commitment_id you can pass to draft_waiting_on_nudge."""
    account_id = account_id or await _default_account_id()
    if not account_id:
        return "No WhatsApp number is connected."
    rows = await _get(
        "/whatsapp/commitments",
        {"account_id": account_id, "direction": "theirs", "status": "open"},
    )
    if not rows:
        return "Nobody owes you anything open on WhatsApp right now."
    lines = ["Waiting on them:"]
    for r in rows:
        due = f" (said {r['due_hint']})" if r.get("due_hint") else ""
        lines.append(
            f"• {r.get('text', '')[:110]}{due} "
            f"(commitment_id={r.get('id')}, chat_id={r.get('chat_id')})"
        )
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def whatsapp_my_commitments(account_id: str | None = None) -> str:
    """List the open promises the FOUNDER made (their word to keep). Each shows
    whether it's already been captured as a task."""
    account_id = account_id or await _default_account_id()
    if not account_id:
        return "No WhatsApp number is connected."
    rows = await _get(
        "/whatsapp/commitments",
        {"account_id": account_id, "direction": "ours", "status": "open"},
    )
    if not rows:
        return "You have no open promises on WhatsApp. Clean slate. 🙏"
    lines = ["Your open promises:"]
    for r in rows:
        due = f" (due {r['due_hint']})" if r.get("due_hint") else ""
        task = " — task captured" if r.get("task_id") else " — not yet a task"
        lines.append(f"• {r.get('text', '')[:110]}{due}{task}")
    return "\n".join(lines)


#: The most loops and promises ``/whatsapp/chats/{id}/context`` returns
#: (``limit=10`` and ``LIMIT 5`` in ``transport/context.py``).
_CONTEXT_LOOPS_MAX = 10
_CONTEXT_WAITING_MAX = 5


@_annotate_risk(open_world=False)
async def whatsapp_chat_context(chat_id: str) -> str:
    """The company standing behind a chat: who the contact is, their CRM/ERP
    link, open tasks/commitments, and what they owe the founder. The moat the
    phone app can't show."""
    ctx = await _get(f"/whatsapp/chats/{chat_id}/context")
    contact = ctx.get("contact") or {}
    lines = [
        f"Context for {contact.get('display_name') or contact.get('phone_number') or 'this chat'}:"
    ]
    if contact.get("phone_number"):
        lines.append(f"• Number: {contact['phone_number']}")
    if contact.get("category"):
        lines.append(f"• Category: {contact['category']}")
    entity = contact.get("entity")
    if entity:
        lines.append(
            f"• Linked: {entity.get('system')} {entity.get('kind')} #{entity.get('id')}")
    # The route already caps each list (10 loops, 5 promises). Print all it
    # sent: the tool cut the loops to 5 on top, with no notice.
    loops = ctx.get("open_loops", []) or []
    if loops:
        lines.append("• Open loops: " + "; ".join(
            lp.get("title", "") for lp in loops))
        if len(loops) >= _CONTEXT_LOOPS_MAX:
            lines.append(f"• That is the first {len(loops)} open loops. "
                         "More may exist.")
    waiting = ctx.get("waiting_on", []) or []
    if waiting:
        lines.append("• They owe you: " + "; ".join(
            f"{w.get('text', '')[:60]} (commitment_id={w.get('id')})"
            for w in waiting))
        if len(waiting) >= _CONTEXT_WAITING_MAX:
            lines.append(f"• That is the first {len(waiting)}. "
                         "whatsapp_waiting_on lists every open promise.")
    stats = ctx.get("stats", {}) or {}
    lines.append(f"• History: {stats.get('message_count', 0)} messages")
    return "\n".join(lines)


# ── Understanding tools (AI) ──────────────────────────────────────────────────

@_annotate_risk(open_world=False)
async def summarize_whatsapp_group(chat_id: str) -> str:
    """Collapse a noisy group chat into one paragraph: what was discussed, the
    sentiment, whether the founder was addressed, and the points worth their eye.
    Use for a group the founder can't scroll through."""
    s = await _post(f"/whatsapp/groups/{chat_id}/summarize")
    lines = [f"{s.get('summary', '')}"]
    if s.get("mentions_you"):
        lines.append("⚠ You were addressed here — worth a look.")
    if s.get("sentiment"):
        lines.append(f"Sentiment: {s['sentiment']}.")
    for p in s.get("key_points", []) or []:
        lines.append(f"• {p}")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def list_whatsapp_group_summaries(needs_you: bool = False) -> str:
    """List cached group summaries, the ones that need the founder first. Set
    needs_you=true to see only groups where the founder was addressed."""
    params = {"needs_you": "true"} if needs_you else {}
    rows = await _get("/whatsapp/groups/summaries", params)
    if not rows:
        return "No group summaries yet — summarize a group first."
    lines = ["Group summaries:"]
    for g in rows:
        flag = " ⚠ needs you" if g.get("mentions_you") else ""
        lines.append(
            f"• {g.get('name', '?')}{flag}: {(g.get('summary') or '')[:140]} "
            f"(chat_id={g.get('chat_id')})"
        )
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def transcribe_whatsapp_voice_note(message_id: str) -> str:
    """Transcribe a voice note by its message_id and fold it into triage (so a
    spoken promise becomes a real commitment). Returns the transcript."""
    r = await _post(f"/whatsapp/messages/{message_id}/transcribe")
    return f"Transcript: “{r.get('transcript_text', '')}”"


# ── Drafting tools (never sends) ──────────────────────────────────────────────

@_annotate_risk(open_world=False)
async def draft_whatsapp_reply(chat_id: str) -> str:
    """Draft a reply to a chat in the founder's WhatsApp voice (short, warm, in
    the thread's language). This is a DRAFT — the founder reviews and sends it in
    the app; this tool never sends."""
    r = await _post(f"/whatsapp/chats/{chat_id}/draft")
    return (
        f"Suggested reply ({r.get('language', 'en')}):\n{r.get('draft_text', '')}"
        "\n\n(Draft only — review and send it from the WhatsApp composer.)"
    )


@_annotate_risk(open_world=False)
async def draft_waiting_on_nudge(commitment_id: str) -> str:
    """Draft a gentle follow-up to chase something someone owes the founder,
    keyed on a commitment_id from whatsapp_waiting_on / whatsapp_brief. A DRAFT —
    the founder reviews and sends it; this tool never sends."""
    r = await _post(f"/whatsapp/commitments/{commitment_id}/nudge")
    return (
        f"Suggested nudge ({r.get('language', 'en')}):\n{r.get('nudge_text', '')}"
        "\n\n(Draft only — review and send it from the WhatsApp composer.)"
    )


# ── Settings, rules, calls and link reads (P-4a, task 3960) ───────────────
# Each tool reads one gateway route as the member and sends nothing. The routes
# check that the account is the member's own.

async def _account_or_default(account_id: str | None) -> str | None:
    return account_id or await _default_account_id()


_NO_ACCOUNT = "No WhatsApp Business number is connected yet."


@_annotate_risk(open_world=False)
async def whatsapp_streams(account_id: str | None = None) -> str:
    """Count the chats in each triage stream: needs reply, waiting, groups, all
    and snoozed. Answers "how many chats wait for me?"."""
    params = {"account_id": account_id} if account_id else None
    d = await _get("/whatsapp/streams", params)
    return (
        "WhatsApp streams — "
        f"{d.get('needs_reply', 0)} need reply · {d.get('waiting', 0)} waiting · "
        f"{d.get('groups', 0)} groups · {d.get('all', 0)} in all · "
        f"{d.get('snoozed', 0)} snoozed"
    )


@_annotate_risk(open_world=False)
async def whatsapp_pulse(account_id: str | None = None, days: int = 7) -> str:
    """The health of the WhatsApp inbox over the last `days` (1 to 90): the
    number of messages in and out, the reply speed, who waited longest, the
    load by intent and the busiest chats."""
    params: dict[str, Any] = {"days": max(1, min(int(days), 90))}
    if account_id:
        params["account_id"] = account_id
    d = await _get("/whatsapp/pulse", params)
    resp = d.get("response") or {}
    lines = [
        f"WhatsApp pulse, last {d.get('window_days', params['days'])} days: "
        f"{d.get('inbound', 0)} in · {d.get('outbound', 0)} out · "
        f"{d.get('active_chats', 0)} active chats",
        f"Replies: {resp.get('replied', 0)} · median {resp.get('median_minutes')} min"
        f" · p90 {resp.get('p90_minutes')} min",
    ]
    for w in (d.get("waiting_longest") or []):
        lines.append(
            f"• waiting {w.get('waited_hours')}h: {w.get('name')} "
            f"(chat_id={w.get('chat_id')})")
    for i in (d.get("by_intent") or []):
        lines.append(f"• intent {i.get('key')}: {i.get('count')}")
    for b in (d.get("busiest") or []):
        lines.append(f"• busiest {b.get('name')}: {b.get('count')} (chat_id={b.get('chat_id')})")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def list_whatsapp_labels(account_id: str | None = None) -> str:
    """List the WhatsApp labels of the founder's numbers, with the number of
    chats that carry each."""
    params = {"account_id": account_id} if account_id else None
    rows = await _get("/whatsapp/labels", params)
    if not rows:
        return "No WhatsApp labels."
    lines = ["WhatsApp labels:"]
    for r in rows:
        lines.append(
            f"• {r.get('name')} — {r.get('chat_count', 0)} chats "
            f"(label id={r.get('wa_label_id')})")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def list_whatsapp_categories(account_id: str | None = None) -> str:
    """List the chat categories of a number with their policies: notify,
    auto-reply and draft. Uses the first number when account_id is empty."""
    aid = await _account_or_default(account_id)
    if not aid:
        return _NO_ACCOUNT
    rows = await _get("/whatsapp/categories", {"account_id": aid})
    if not rows:
        return "No categories for this number."
    lines = ["WhatsApp categories:"]
    for r in rows:
        lines.append(
            f"• {r.get('name')} — notify={r.get('notify_policy')}, "
            f"auto_reply={r.get('auto_reply_policy')}, draft={r.get('draft_policy')}"
            f" (category id={r.get('id')})")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def list_whatsapp_templates(
    account_id: str | None = None, approved_only: bool = False,
) -> str:
    """List the message templates of a number. approved_only keeps the ones that
    can send now. This tool never sends a template."""
    aid = await _account_or_default(account_id)
    if not aid:
        return _NO_ACCOUNT
    rows = await _get(
        "/whatsapp/templates", {"account_id": aid, "approved_only": bool(approved_only)})
    if not rows:
        return "No WhatsApp templates for this number."
    lines = ["WhatsApp templates:"]
    for r in rows:
        lines.append(
            f"• {r.get('name')} ({r.get('language')}, {r.get('category')}, "
            f"{r.get('meta_status')}): {(r.get('body') or '')[:100]}")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def list_whatsapp_saved_replies(account_id: str | None = None) -> str:
    """List the saved replies of a number, with each shortcut."""
    aid = await _account_or_default(account_id)
    if not aid:
        return _NO_ACCOUNT
    rows = await _get("/whatsapp/saved-replies", {"account_id": aid})
    if not rows:
        return "No saved replies for this number."
    lines = ["Saved replies:"]
    for r in rows:
        key = f" {r['shortcut']}" if r.get("shortcut") else ""
        lines.append(
            f"• {r.get('title')}{key}: {(r.get('body') or '')[:100]} "
            f"(reply id={r.get('id')})")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def whatsapp_rules_preview(
    account_id: str | None = None, within_office_hours: bool = True, limit: int = 25,
) -> str:
    """Dry-run the auto-reply rules over the recent chats that need a reply, and
    say what each rule would do. Nothing is sent."""
    aid = await _account_or_default(account_id)
    if not aid:
        return _NO_ACCOUNT
    d = await _get("/whatsapp/rules/preview", {
        "account_id": aid, "within_office_hours": bool(within_office_hours),
        "limit": max(1, min(int(limit), 200))})
    items = d.get("items") or []
    if not items:
        return "No chat needs a reply, so no rule would act."
    summary = ", ".join(f"{n} {k}" for k, n in (d.get("summary") or {}).items())
    lines = [f"Rule preview (nothing sent): {summary}"]
    for i in items:
        appr = " · needs approval" if i.get("requires_approval") else ""
        lines.append(
            f"• {i.get('name')}: {i.get('action')} — {i.get('reason')}{appr} "
            f"(chat_id={i.get('chat_id')})")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def list_whatsapp_calls(account_id: str | None = None) -> str:
    """List the calls that the WhatsApp bridge tracks for a number, live and
    recent. This tool never places or answers a call."""
    aid = await _account_or_default(account_id)
    if not aid:
        return _NO_ACCOUNT
    d = await _get("/whatsapp/calls", {"account_id": aid})
    if not d.get("bridge_reachable", True):
        return "The WhatsApp bridge does not answer, so the call list is not known."
    calls = d.get("calls") or []
    if not calls:
        return "No calls are tracked for this number."
    lines = ["WhatsApp calls:"]
    for c in calls:
        lines.append(
            f"• {c.get('direction')} {c.get('kind')} with {c.get('peer')} — "
            f"{c.get('phase')}, started {c.get('started_at')} "
            f"(call_id={c.get('call_id')})")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def whatsapp_call_events(call_id: str, account_id: str | None = None) -> str:
    """Read the event timeline of one call: signalling, phases and media. Use it
    to find why a call failed."""
    aid = await _account_or_default(account_id)
    if not aid:
        return _NO_ACCOUNT
    d = await _get(f"/whatsapp/calls/{quote(str(call_id), safe='')}/events", {"account_id": aid})
    if not d.get("bridge_reachable", True):
        return "The WhatsApp bridge does not answer, so the call timeline is not known."
    events = d.get("events") or []
    if not events:
        return "No events for this call."
    call = d.get("call") or {}
    lines = [f"Call {call_id}: {call.get('phase')} {call.get('end_reason') or ''}".rstrip()]
    for e in events:
        lines.append(f"• {e.get('at')} {e.get('kind')}: {(e.get('detail') or '')[:120]}")
    return "\n".join(lines)


@_annotate_risk(open_world=False)
async def whatsapp_call_diagnostics(account_id: str | None = None) -> str:
    """Say if a number can place a call, and what is missing when it cannot."""
    aid = await _account_or_default(account_id)
    if not aid:
        return _NO_ACCOUNT
    d = await _get("/whatsapp/calls/diagnostics", {"account_id": aid})
    if not d.get("bridge_reachable", True):
        return f"The WhatsApp bridge does not answer. {d.get('verdict') or ''}".strip()
    return (
        f"Call readiness: {d.get('verdict') or 'no verdict'}\n"
        f"session={d.get('session_exists')} · logged_in={d.get('logged_in')} · "
        f"connected={d.get('connected')} · caller_ready={d.get('caller_ready')} · "
        f"active_calls={d.get('active_calls', 0)}"
    )


@_annotate_risk(open_world=False)
async def whatsapp_connection_info() -> str:
    """Say how the WhatsApp connection is set up: the webhook URL and if
    one-click signup is on. It never shows the verify token."""
    d = await _get("/whatsapp/connection/info")
    url = d.get("webhook_url") or "not set (the public domain is unknown)"
    return (
        f"Webhook callback URL: {url}\n"
        f"Embedded signup: {'on' if d.get('embedded_signup') else 'off'} · "
        f"Graph version: {d.get('graph_version')}"
    )


@_annotate_risk(open_world=False)
async def whatsapp_link_status() -> str:
    """Say if the member's phone is linked to chat on WhatsApp, and the state of
    each link. It never issues a code."""
    d = await _get("/me/whatsapp-link")
    if not d.get("enabled"):
        return "Chat on WhatsApp is not set up on this deployment."
    links = d.get("links") or []
    if not links:
        return f"No phone is linked. The business number is {d.get('display_number')}."
    lines = [f"Chat on WhatsApp links (business number {d.get('display_number')}):"]
    for k in links:
        hint = f" phone ending {k['phone_hint']}" if k.get("phone_hint") else ""
        lines.append(f"• {k.get('organization_name')}: {k.get('status')}{hint}")
    return "\n".join(lines)


# ── Changes, each on one card (P-4b, task 3961) ───────────────────────────
# Each tool reads first, shows ONE card, and sends ONE write only after the
# member approves it. None sends a message. A deny, a timeout, a closed card,
# a run with no card and a run with no person present write nothing, because
# ``request_confirmation`` answers no for each. No tool takes a member or an
# org: the gateway scopes each route to the member of the run. Fence:
# ``tests/unit/test_whatsapp_write_tools.py``.
#
# ⚠️ A WhatsApp read does NOT mark the chat as one that read outside text. So for
# text that a stranger puts in a WhatsApp message, the approval card is the
# only guard. The hold (``acb_skills.egress.HELD_OFF_OWN_WRITES``) drops these
# tools only after another read marked the chat, such as an email read, or in a
# delegated ``no_egress`` run. A run under a covered run keeps them until a read
# marks the chat. Follow-up: task 4010.

#: The limits of ``transport/saved_replies.py``. The route cuts a longer text,
#: so the tool refuses it: the card shows the text that the route saves.
_TITLE_MAX = 80
_BODY_MAX = 4096
_SHORTCUT_MAX = 32
_SHORTCUT_DROP = re.compile(r"[^a-z0-9_]")
#: ``transport/snooze.py``: the furthest wake time, in days.
_SNOOZE_MAX_DAYS = 400
#: ``transport/chats.py``: the most chats of one read.
_CHATS_MAX = 200
#: The longest task title that ``transport/capture.py`` makes.
_TASK_TITLE_MAX = 200
#: ``automation/categories.py``: each policy, its card label and its values.
_POLICIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "notify_policy": ("Notify", ("instant", "digest", "mention_only", "never")),
    "auto_reply_policy": ("Auto-reply", ("never", "holding", "answer_from_system")),
    "draft_policy": ("AI drafts", ("always", "on_intent", "never")),
}
#: ``automation/categories.py::default_categories``: the name, the three
#: policies and the minutes before a silent chat escalates.
_DEFAULT_CATEGORIES: tuple[tuple[str, str, str, str, int | None], ...] = (
    ("VIP", "instant", "never", "always", 120),
    ("New customer", "digest", "holding", "on_intent", 1440),
    ("Pending payment", "instant", "never", "always", 240),
    ("Dealer groups", "mention_only", "never", "on_intent", None),
    ("Family & personal", "instant", "never", "never", None),
    ("Noise", "never", "never", "never", None),
)
#: What the policies do. Only ``automation/rules.py`` reads them, for the rule
#: preview, which sends nothing. A fence holds that claim.
_POLICY_NOTE = (
    "Today only the rule preview reads these policies. Metorite sends no "
    "automatic reply from them.\n"
    "The values holding and answer_from_system permit an automatic reply with "
    "no card, when automatic replies run.\n"
    "VIP and Family & personal never auto-send."
)

_NOT_CHANGED = "Nothing was changed."
_UNSURE = ("The change may or may not have happened. Read it again before another "
           "try.")
_NO_ONE = ("No single member is acting in this run, so nothing was changed. Ask "
           "in your own chat.")
_TOO_LONG_FOR_CARD = (
    "This change does not fit whole on one card, so nothing was changed. Do it "
    "on the WhatsApp page in the app.")


def _uuid_arg(value: Any) -> str | None:
    """*value* as one canonical id, or None. A path never holds free text."""
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        return None


def _bad_id(name: str, source: str) -> str:
    return f"{name} must be an id from {source}. {_NOT_CHANGED}"


def _hidden(**texts: str) -> str | None:
    """The refusal of a character that no person sees, in a text to be saved."""
    from acb_skills.hidden_text import hidden_problem

    for label, value in texts.items():
        problem = hidden_problem(value, label)
        if problem:
            return f"{problem} {_NOT_CHANGED}"
    return None


def _visible(value: Any) -> str:
    """A stored text with no character that no person sees."""
    from acb_skills.hidden_text import strip_hidden

    return strip_hidden(str(value or ""))


def _shown(value: Any, limit: int = 80) -> str:
    """A stored name as one short line of a card."""
    line = one_line(_visible(value))
    return line if len(line) <= limit else f"{line[: limit - 3]}..."


async def _card(title: str, detail: str, context: str, unchanged: str) -> str | None:
    """Show ONE card. None means that the member approved it. Else the answer
    of a card that did nothing, from the one table of card outcomes. A text that
    the card would cut gets no card."""
    if len(title) > 120 or len(detail) > 500 or len(context) > CARD_CLIP:
        return _TOO_LONG_FOR_CARD
    from acb_skills import ask_tools

    answer = await ask_tools.request_confirmation(
        title, detail, context, non_interactive_default="deny",
        # The member's chat message closes the card, and nothing is written.
        # Only the member of the run may answer it (PZ-32).
        close_on_steer=True, responder=_current_user_email(),
    )
    if answer is True:
        return None
    if ask_tools.is_revised(answer):
        return ask_tools.card_outcome_text(ask_tools.CLOSED_BY_YOUR_MESSAGE, unchanged)
    return ask_tools.card_not_done_text(unchanged)


async def _write(
    method: str, path: str, body: dict[str, Any] | None = None,
) -> tuple[httpx.Response | None, str | None]:
    """ONE write, after the approve of its card: ``(response, None)``, or
    ``(None, answer)``. A 4xx changed nothing. A 5xx or a lost connection may
    have changed it, and the answer says so. Nothing is sent a second time."""
    extra: dict[str, Any] = {} if body is None else {"json": body}
    try:
        return await _request(method, path, **extra), None
    except GatewayRefusal as exc:
        status = exc.status or 500
        lead = _NOT_CHANGED if status < 500 else _UNSURE
        return None, f"{lead} {exc}"
    except httpx.HTTPError as exc:
        return None, f"{_UNSURE} The gateway did not answer ({type(exc).__name__})."


def _json_dict(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _number_line(account: dict[str, Any]) -> str:
    name = _shown(account.get("display_name") or account.get("phone_number"))
    return f"Number: {name} ({_shown(account.get('phone_number'))})"


async def _own_accounts(account_id: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """The member's numbers, or the one that *account_id* names, or a refusal."""
    accounts = await _get("/whatsapp/accounts") or []
    if not accounts:
        return [], f"{_NO_ACCOUNT} {_NOT_CHANGED}"
    if not account_id:
        return list(accounts), None
    want = _uuid_arg(account_id)
    found = [a for a in accounts if want and _uuid_arg(a.get("id")) == want]
    if not found:
        return [], f"No WhatsApp number of yours has that account_id. {_NOT_CHANGED}"
    return found, None


async def _write_account(account_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """The number that a new row goes to. With no id, the default number, as
    the page picks it. The card names it."""
    accounts, refusal = await _own_accounts(account_id)
    if refusal:
        return None, refusal
    return next((a for a in accounts if a.get("is_default")), accounts[0]), None


async def _find_row(
    path: str, row_id: str, account_id: str | None, noun: str,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """``(account, row, None)`` for the row *row_id* of one of the member's
    numbers, or a refusal. The routes list only the member's own rows."""
    accounts, refusal = await _own_accounts(account_id)
    if refusal:
        return {}, {}, refusal
    for account in accounts:
        rows = await _get(path, {"account_id": str(account.get("id"))})
        for row in rows or []:
            if _uuid_arg(row.get("id")) == row_id:
                return account, row, None
    where = "that number" if account_id else "your numbers"
    return {}, {}, f"No {noun} with that id is on {where}. {_NOT_CHANGED}"


def _saved_shortcut(raw: str) -> str | None:
    """The shortcut as ``saved_replies.normalize_shortcut`` saves it."""
    slug = _SHORTCUT_DROP.sub("", raw.strip().lower().lstrip("/"))
    return f"/{slug[:_SHORTCUT_MAX]}" if slug else None


@_annotate_risk(read_only=False, destructive=False, idempotent=False, open_world=False)
async def create_whatsapp_saved_reply(
    title: str, body: str, shortcut: str | None = None, account_id: str | None = None,
) -> str:
    """Add a saved reply to a number, after one card. It sends no message.

    title holds at most 80 characters, and body at most 4096. shortcut is
    optional, for example /price. Uses the default number when account_id is
    empty."""
    if "@" not in _current_user_email():
        return _NO_ONE
    name = one_line(title or "")
    text = str(body or "").strip()
    raw_key = str(shortcut or "").strip()
    if not name or not text:
        return f"A saved reply needs a title and a text. {_NOT_CHANGED}"
    if len(name) > _TITLE_MAX:
        return f"A title holds at most {_TITLE_MAX} characters. {_NOT_CHANGED}"
    if len(text) > _BODY_MAX:
        return f"A saved reply holds at most {_BODY_MAX} characters. {_NOT_CHANGED}"
    refusal = _hidden(title=name, body=text, shortcut=raw_key)
    if refusal:
        return refusal
    key = _saved_shortcut(raw_key) if raw_key else None
    if raw_key and key is None:
        return f"A shortcut uses letters, numbers and _. {_NOT_CHANGED}"
    account, refusal = await _write_account(account_id)
    if account is None:
        return refusal or _NOT_CHANGED
    context = "\n".join([
        _number_line(account),
        f"Title: {name}",
        f"Shortcut: {key or '(none)'}",
        "",
        "The full text:",
        text,
    ])
    unchanged = "No saved reply was added."
    refusal = await _card(
        "Add this saved reply?",
        f"Adds \"{name}\" to the saved replies of this number. It sends no message.",
        context, unchanged)
    if refusal:
        return refusal
    resp, failed = await _write("POST", "/whatsapp/saved-replies", {
        "account_id": str(account.get("id")), "title": name, "body": text,
        "shortcut": key,
    })
    if resp is None:
        return failed or _UNSURE
    new_id = _json_dict(resp).get("id")
    return f"Added the saved reply \"{name}\" (reply id={new_id}). No message was sent."


def _reply_snapshot(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("title"), row.get("body"), row.get("shortcut"))


@_annotate_risk(read_only=False, destructive=True, idempotent=False, open_world=False)
async def delete_whatsapp_saved_reply(reply_id: str, account_id: str | None = None) -> str:
    """Delete one saved reply for good, after one card that shows its text.
    Take reply_id from list_whatsapp_saved_replies. It sends no message."""
    if "@" not in _current_user_email():
        return _NO_ONE
    rid = _uuid_arg(reply_id)
    if rid is None:
        return _bad_id("reply_id", "list_whatsapp_saved_replies")
    account, row, refusal = await _find_row(
        "/whatsapp/saved-replies", rid, account_id, "saved reply")
    if refusal:
        return refusal
    name = _shown(row.get("title")) or "(no title)"
    body = str(row.get("body") or "")
    lines = [
        _number_line(account),
        f"Title: {name}",
        f"Shortcut: {_shown(row.get('shortcut')) or '(none)'}",
    ]
    if _visible(body) != body:
        lines.append("The text also holds characters that you cannot see.")
    lines += ["", "The full text that you lose:", _visible(body)]
    unchanged = f"The saved reply \"{name}\" was not deleted."
    refusal = await _card(
        "Delete this saved reply?",
        f"You lose the saved reply \"{name}\", its shortcut and its text. There is "
        "no undo.",
        "\n".join(lines), unchanged)
    if refusal:
        return refusal
    try:
        _, now, gone = await _find_row(
            "/whatsapp/saved-replies", rid, str(account.get("id")), "saved reply")
    except (RuntimeError, httpx.HTTPError) as exc:
        return f"{_NOT_CHANGED} The reply could not be read again: {exc}"
    if gone or _reply_snapshot(now) != _reply_snapshot(row):
        return f"The saved reply changed while the card was open. {_NOT_CHANGED}"
    resp, failed = await _write("DELETE", f"/whatsapp/saved-replies/{rid}")
    if resp is None:
        return failed or _UNSURE
    return f"Deleted the saved reply \"{name}\". No message was sent."


def _policy_snapshot(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in _POLICIES)


@_annotate_risk(read_only=False, destructive=False, idempotent=True, open_world=False)
async def update_whatsapp_category(
    category_id: str,
    notify_policy: str | None = None,
    auto_reply_policy: str | None = None,
    draft_policy: str | None = None,
    account_id: str | None = None,
) -> str:
    """Change the policies of one chat category, after one card. Take
    category_id from list_whatsapp_categories. notify_policy: instant, digest,
    mention_only or never. auto_reply_policy: never, holding or
    answer_from_system. draft_policy: always, on_intent or never. Give only
    the policies to change. It sends no message."""
    if "@" not in _current_user_email():
        return _NO_ONE
    cid = _uuid_arg(category_id)
    if cid is None:
        return _bad_id("category_id", "list_whatsapp_categories")
    asked = {
        "notify_policy": notify_policy,
        "auto_reply_policy": auto_reply_policy,
        "draft_policy": draft_policy,
    }
    wanted: dict[str, str] = {}
    for field, value in asked.items():
        if value is None or not str(value).strip():
            continue
        word = str(value).strip().lower()
        if word not in _POLICIES[field][1]:
            return (f"{field} must be one of: {', '.join(_POLICIES[field][1])}. "
                    f"{_NOT_CHANGED}")
        wanted[field] = word
    if not wanted:
        return f"Name at least one policy to change. {_NOT_CHANGED}"
    account, row, refusal = await _find_row(
        "/whatsapp/categories", cid, account_id, "category")
    if refusal:
        return refusal
    name = _shown(row.get("name")) or "(no name)"
    changes = {k: v for k, v in wanted.items() if row.get(k) != v}
    if not changes:
        return f"The category \"{name}\" holds these policies already. {_NOT_CHANGED}"
    lines = [_number_line(account), f"Category: {name}"]
    for field, value in changes.items():
        lines.append(
            f"{_POLICIES[field][0]}: {_shown(row.get(field))} becomes {value}")
    lines += ["", _POLICY_NOTE]
    unchanged = f"The category \"{name}\" was not changed."
    refusal = await _card(
        "Change this chat category?",
        f"Changes the policies of the category \"{name}\". It sends no message.",
        "\n".join(lines), unchanged)
    if refusal:
        return refusal
    try:
        _, now, gone = await _find_row(
            "/whatsapp/categories", cid, str(account.get("id")), "category")
    except (RuntimeError, httpx.HTTPError) as exc:
        return f"{_NOT_CHANGED} The category could not be read again: {exc}"
    if gone or _policy_snapshot(now) != _policy_snapshot(row):
        return f"The category changed while the card was open. {_NOT_CHANGED}"
    resp, failed = await _write("PATCH", f"/whatsapp/categories/{cid}", changes)
    if resp is None:
        return failed or _UNSURE
    done = ", ".join(f"{k}={v}" for k, v in changes.items())
    return f"Changed the category \"{name}\": {done}. No message was sent."


@_annotate_risk(read_only=False, destructive=False, idempotent=True, open_world=False)
async def seed_whatsapp_categories(account_id: str | None = None) -> str:
    """Add the default chat categories that a number does not have, after one
    card. A category that exists stays as it is. Uses the default number when
    account_id is empty. It sends no message."""
    if "@" not in _current_user_email():
        return _NO_ONE
    account, refusal = await _write_account(account_id)
    if account is None:
        return refusal or _NOT_CHANGED
    aid = str(account.get("id"))
    rows = await _get("/whatsapp/categories", {"account_id": aid}) or []
    have = {str(r.get("name")) for r in rows}
    missing = [d for d in _DEFAULT_CATEGORIES if d[0] not in have]
    if not missing:
        return f"This number has each default category already. {_NOT_CHANGED}"
    lines = [_number_line(account), f"Adds {len(missing)} categories:"]
    for name, notify, auto, draft, minutes in missing:
        escalate = f"{minutes // 60}h" if minutes else "never"
        lines.append(
            f"- {name}: notify {notify}, auto-reply {auto}, AI drafts {draft}, "
            f"escalate {escalate}")
    kept = (f"Categories that the number has now: {len(have)}. They stay as they "
            "are.") if have else "The number has no category now."
    lines += [kept, "", _POLICY_NOTE]
    unchanged = "No category was added."
    refusal = await _card(
        "Add the default chat categories?",
        f"Adds {len(missing)} categories with their policies to this number. It "
        "sends no message.",
        "\n".join(lines), unchanged)
    if refusal:
        return refusal
    # The answer says what THIS write added, so the names that the number has
    # must still be the names of the card.
    try:
        again = await _get("/whatsapp/categories", {"account_id": aid}) or []
    except (RuntimeError, httpx.HTTPError) as exc:
        return f"{_NOT_CHANGED} The categories could not be read again: {exc}"
    if {str(r.get("name")) for r in again} != have:
        return f"The categories changed while the card was open. {_NOT_CHANGED}"
    resp, failed = await _write(
        "POST", f"/whatsapp/accounts/{aid}/categories/bootstrap", {})
    if resp is None:
        return failed or _UNSURE
    try:
        after = resp.json()
        added = [str(r.get("name")) for r in after if str(r.get("name")) not in have]
    except (ValueError, TypeError, AttributeError):
        return f"{_UNSURE} The route answered in a shape that this tool cannot read."
    if not added:
        return "No category was added. The number had each default category."
    return f"Added {len(added)} categories: {', '.join(added)}. No message was sent."


def _wake_time(until: str) -> tuple[datetime | None, str | None]:
    """*until* as one future instant with a zone, or a refusal. It mirrors
    ``snooze.parse_snooze_until``, and it also refuses a time with no zone,
    because the route would read it as UTC."""
    shape = ("until must be an ISO-8601 time with a time zone, for example "
             f"2026-12-01T09:00:00+05:30. {_NOT_CHANGED}")
    raw = str(until or "").strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(raw)
        if when.tzinfo is None:
            return None, shape
        when = when.replace(microsecond=0)
        utc = when.astimezone(UTC)
    except (ValueError, OverflowError):
        return None, shape
    now = datetime.now(UTC)
    if utc <= now:
        return None, f"The wake time must be in the future. {_NOT_CHANGED}"
    if (utc - now).days > _SNOOZE_MAX_DAYS:
        return None, (f"The wake time is more than {_SNOOZE_MAX_DAYS} days away. "
                      f"{_NOT_CHANGED}")
    return when, None


async def _chat_facts(chat_id: str) -> tuple[str, bool, str] | None:
    """``(name, is_group, provider)`` of one chat of the member, or None when
    the member has no chat with this id that holds a message. The search route
    reads only the member's own numbers, one number at a time here."""
    for account in await _get("/whatsapp/accounts") or []:
        rows = await _get("/whatsapp/search", {
            "chat_id": chat_id, "account_id": str(account.get("id")), "limit": 1})
        if rows:
            break
    else:
        return None
    name = _shown(rows[0].get("chat_name"))
    if not name:
        ctx = await _get(f"/whatsapp/chats/{chat_id}/context")
        contact = ctx.get("contact") or {}
        name = _shown(contact.get("display_name") or contact.get("phone_number"))
    is_group = rows[0].get("chat_kind") == "group"
    if not name:
        name = "(a chat with no name)"
    elif is_group:
        name = f"{name} (group)"
    return name, is_group, str(account.get("provider") or "")


#: The providers of a Cloud API number. Its webhook binds the organization, so
#: the status sweep runs and a new inbound message clears the snooze
#: (``transport/webhook.py``, ``automation/replyzero.py``). The bridge push
#: routes bind none, so for them the card makes no such promise.
_CLOUD_PROVIDERS = ("cloud_api", "cloud")
_WAKES_LINE = "A new message from them wakes the chat sooner."


async def _snoozed_chats() -> list[dict[str, Any]]:
    return await _get(
        "/whatsapp/chats", {"stream": "snoozed", "limit": _CHATS_MAX}) or []


@_annotate_risk(read_only=False, destructive=False, idempotent=True, open_world=False)
async def snooze_whatsapp_chat(chat_id: str, until: str) -> str:
    """Snooze one chat until a time, after one card. The chat leaves the triage
    streams until then. until is ISO-8601 with a time zone, for example
    2026-12-01T09:00:00+05:30. It sends no message."""
    if "@" not in _current_user_email():
        return _NO_ONE
    cid = _uuid_arg(chat_id)
    if cid is None:
        return _bad_id("chat_id", "list_whatsapp_chats")
    when, refusal = _wake_time(until)
    if when is None:
        return refusal or _NOT_CHANGED
    facts = await _chat_facts(cid)
    if facts is None:
        return f"No chat of yours with that id holds a message. {_NOT_CHANGED}"
    name, _is_group, provider = facts
    utc = when.astimezone(UTC)
    lines = [
        f"Chat: {name}",
        f"Wakes at: {when.isoformat(sep=' ')}",
        f"In UTC: {utc.isoformat(sep=' ')}",
    ]
    was = next((c for c in await _snoozed_chats() if _uuid_arg(c.get("id")) == cid), None)
    if was:
        lines.append(
            f"It is snoozed now until {_shown(was.get('snoozed_until'))}. This "
            "time replaces that one.")
    wakes = f"{_WAKES_LINE} " if provider in _CLOUD_PROVIDERS else ""
    lines += ["", f"{wakes}Nothing is sent to them."]
    unchanged = f"The chat \"{name}\" was not snoozed."
    refusal = await _card(
        "Snooze this chat?",
        f"Hides \"{name}\" from your triage streams until the wake time. It sends "
        "no message.",
        "\n".join(lines), unchanged)
    if refusal:
        return refusal
    resp, failed = await _write(
        "POST", f"/whatsapp/chats/{cid}/snooze", {"until": utc.isoformat()})
    if resp is None:
        return failed or _UNSURE
    return f"Snoozed \"{name}\" until {utc.isoformat(sep=' ')}. No message was sent."


@_annotate_risk(read_only=False, destructive=False, idempotent=True, open_world=False)
async def unsnooze_whatsapp_chat(chat_id: str) -> str:
    """Wake one snoozed chat now, after one card. It returns to the triage
    streams. It sends no message."""
    if "@" not in _current_user_email():
        return _NO_ONE
    cid = _uuid_arg(chat_id)
    if cid is None:
        return _bad_id("chat_id", "list_whatsapp_chats")
    snoozed = await _snoozed_chats()
    was = next((c for c in snoozed if _uuid_arg(c.get("id")) == cid), None)
    if was is None:
        # The route gives at most _CHATS_MAX chats. A full list can hide an
        # older chat, so it proves nothing, and no card shows.
        if len(snoozed) >= _CHATS_MAX:
            return (f"This tool reads at most {_CHATS_MAX} snoozed chats, and this "
                    "chat is not in them. It cannot confirm that the chat is "
                    f"snoozed. {_NOT_CHANGED}")
        return f"No snoozed chat of yours has that id. {_NOT_CHANGED}"
    name = _shown(was.get("name") or was.get("wa_chat_id")) or "(a chat with no name)"
    until = _shown(was.get("snoozed_until"))
    lines = [f"Chat: {name}"]
    if until:
        lines.append(f"Snoozed until: {until}")
    lines += ["", "The chat returns to your triage streams now. Nothing is sent to "
                  "them."]
    unchanged = f"The chat \"{name}\" was not woken."
    refusal = await _card(
        "Wake this chat now?",
        f"Ends the snooze of \"{name}\". It sends no message.",
        "\n".join(lines), unchanged)
    if refusal:
        return refusal
    resp, failed = await _write("POST", f"/whatsapp/chats/{cid}/unsnooze", {})
    if resp is None:
        return failed or _UNSURE
    return f"Woke \"{name}\". It is back in your triage streams. No message was sent."


async def _message(chat_id: str, message_id: str) -> dict[str, Any] | None:
    """One message of one chat of the member, or None. A read only."""
    rows = await _get(
        f"/whatsapp/chats/{chat_id}/messages", {"around": message_id, "window": 0})
    return next(
        (m for m in rows or [] if _uuid_arg(m.get("id")) == message_id), None)


def _task_title(body: str, sender: str, chat: str) -> str:
    """The title that ``capture.derive_title`` makes of a message."""
    clean = re.sub(r"\s+", " ", body or "").strip()
    who = (sender or chat or "someone").strip()
    if clean:
        more = "…" if len(clean) > 80 else ""
        return f"WhatsApp · {who}: {clean[:80]}{more}"[:_TASK_TITLE_MAX]
    return f"WhatsApp · reply to {who}"[:_TASK_TITLE_MAX]


@_annotate_risk(read_only=False, destructive=False, idempotent=True, open_world=False)
async def capture_whatsapp_task(
    chat_id: str, message_id: str, title: str | None = None,
) -> str:
    """Turn one WhatsApp message into a task in the member's own inbox, after
    one card. Take chat_id and message_id from search_whatsapp. title is
    optional. A message with an open task makes no second task. It sends no
    message."""
    if "@" not in _current_user_email():
        return _NO_ONE
    cid = _uuid_arg(chat_id)
    if cid is None:
        return _bad_id("chat_id", "search_whatsapp")
    mid = _uuid_arg(message_id)
    if mid is None:
        return _bad_id("message_id", "search_whatsapp")
    msg = await _message(cid, mid)
    if msg is None:
        return f"No message of that chat has that id. {_NOT_CHANGED}"
    body = str(msg.get("body_text") or "")
    refusal = _hidden(title=str(title or ""))
    if refusal:
        return refusal
    # The task keeps the first 500 characters as they are, so the card must
    # show each one of them. It is one text, and not a list.
    description = f"{body[:500]}"
    if _visible(description) != description:
        return ("The message holds a character that you cannot see, so no card "
                f"can show it. Make the task on the WhatsApp page. {_NOT_CHANGED}")
    # One line each: a name with a new line must not forge a line of the card.
    # The title that goes is the title that the card shows.
    sender = one_line(_visible(msg.get("sender_name")))
    chat = one_line(_visible(msg.get("chat_name")))
    name = one_line(title or "") or _task_title(_visible(body), sender, chat)
    if len(name) > _TASK_TITLE_MAX:
        return f"A task title holds at most {_TASK_TITLE_MAX} characters. {_NOT_CHANGED}"
    outbound = msg.get("direction") == "out"
    context = "\n".join([
        f"Chat: {_shown(chat) or '(a chat with no name)'}",
        f"From: {'you' if outbound else _shown(sender) or 'them'}",
        f"Sent at: {_shown(msg.get('sent_at')) or '(not known)'}",
        f"Task title: {name}",
        "A new task can start the workflows of your organization.",
        "",
        "Task description (the first 500 characters of the message):",
        description or "(none, because the message holds no text)",
    ])
    unchanged = "No task was made."
    refusal = await _card(
        "Make a task from this message?",
        "Adds one task to your own inbox. A message with an open task makes no "
        "second task. This tool sends no message.",
        context, unchanged)
    if refusal:
        return refusal
    try:
        again = await _message(cid, mid)
    except (RuntimeError, httpx.HTTPError) as exc:
        return f"{_NOT_CHANGED} The message could not be read again: {exc}"
    # An unchanged text holds no new hidden character, so one compare covers both.
    if again is None or str(again.get("body_text") or "") != body:
        return f"The message changed while the card was open. {_NOT_CHANGED}"
    resp, failed = await _write(
        "POST", "/whatsapp/capture-task", {"message_id": mid, "title": name})
    if resp is None:
        return failed or _UNSURE
    data = _json_dict(resp)
    item, made = data.get("item_id"), data.get("created")
    if not item or not isinstance(made, bool):
        return f"{_UNSURE} The route answered in a shape that this tool cannot read."
    saved = _shown(data.get("title"), _TASK_TITLE_MAX)
    if not made:
        return (f"No new task was made. An open task holds this message already: "
                f"\"{saved}\" (task id={item}).")
    return (f"Added the task \"{saved}\" to your inbox (task id={item}). This tool "
            "sent no message.")


# ── MAF agent factory (Dynamic Agent Loader entry point) ─────────────────────

_TOOLS = [
    # Read / triage
    list_whatsapp_accounts,
    whatsapp_brief,
    list_whatsapp_chats,
    read_whatsapp_chat,
    search_whatsapp,
    whatsapp_waiting_on,
    whatsapp_my_commitments,
    whatsapp_chat_context,
    # Understanding
    summarize_whatsapp_group,
    list_whatsapp_group_summaries,
    transcribe_whatsapp_voice_note,
    # Settings, rules, calls and link (read only)
    whatsapp_streams,
    whatsapp_pulse,
    list_whatsapp_labels,
    list_whatsapp_categories,
    list_whatsapp_templates,
    list_whatsapp_saved_replies,
    whatsapp_rules_preview,
    list_whatsapp_calls,
    whatsapp_call_events,
    whatsapp_call_diagnostics,
    whatsapp_connection_info,
    whatsapp_link_status,
    # Drafting (never sends)
    draft_whatsapp_reply,
    draft_waiting_on_nudge,
    # Changes, each on one card (never sends)
    create_whatsapp_saved_reply,
    delete_whatsapp_saved_reply,
    update_whatsapp_category,
    seed_whatsapp_categories,
    snooze_whatsapp_chat,
    unsnooze_whatsapp_chat,
    capture_whatsapp_task,
]


# ── WS-48 N4: narrow, pick, read (data_narrowing_pipeline.md §9 N4) ─────────


def _narrow_source() -> Any:
    """The WhatsApp adapter of ``narrow_source.py``, on THIS module's ``_get``.

    It is loaded by path under a name of its own. A bare ``import
    narrow_source`` would take whichever agent's adapter loaded first, because
    each agent dir that holds one goes on ``sys.path`` (``acb_skills.loader``).
    The getter reads ``_get`` at each call, so it is the one client of this
    agent (``_request`` and ``_headers``), and no second one exists.
    """
    import importlib.util

    path = Path(__file__).with_name("narrow_source.py")
    spec = importlib.util.spec_from_file_location("agent_whatsapp_assistant_narrow_source", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no narrow_source.py beside {__file__}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def _get_for_adapter(path: str, params: dict[str, Any] | None = None) -> Any:
        return await _get(path, params)

    return module.WhatsAppNarrowSource(get=_get_for_adapter)


def _narrow_tool() -> Any | None:
    """``narrow_and_read`` when ``NARROWING_AGENTS`` names this agent, else ``None``.

    ``narrow_tool_for`` is the ONE door (WS48-F4). It reads the flag, and the
    tool reads it again at each call. A fault here builds no tool, so the
    agent keeps every other tool.
    """
    try:
        from acb_skills.narrowing import narrow_tool_for, narrowing_on
    except ImportError:  # an older platform with no narrowing seam
        return None
    if not narrowing_on(AGENT_NAME):
        return None
    try:
        return narrow_tool_for(AGENT_NAME, _narrow_source())
    except Exception as exc:  # never break the agent build
        _log.warning("whatsapp_assistant.narrow_tool_failed", error_type=type(exc).__name__)
        return None


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
    """Construct the WhatsApp Assistant as a NATIVE MAF agent backed by the
    LiteLLM gateway (same pattern as agent-email-assistant: agent_framework
    ``Agent`` + ``OpenAIChatCompletionClient`` pointed at the gateway's ``/v1``,
    so it runs on the configured LiteLLM tier, never native GitHub Copilot).
    Imported lazily so the module still loads where the optional deps differ."""
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient
    from acb_llm.attribution import attributed_openai

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        model=os.environ.get("WHATSAPP_AGENT_MODEL", "tier-balanced"),
        # Usage slice 1: `attributed_openai` adds the member, app and run to
        # EVERY request, from the run context. A fixed header cannot, because
        # one client serves everyone who chats with this agent.
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": "whatsapp-assistant", "X-CC-Source": "chat"},
        ),
    )
    narrow = _narrow_tool()
    tools = [*_TOOLS, narrow] if narrow is not None else list(_TOOLS)
    return [
        Agent(
            client=client,
            instructions=_instructions(narrow is not None),
            name=AGENT_NAME,
            description=(
                "Triages, understands, and drafts WhatsApp Business messages — "
                "the morning brief, waiting-on chases, group summaries, voice-note "
                "transcription, and reply/nudge drafts. Drafts only; never sends."
            ),
            tools=tools,
        )
    ]


__all__ = ["INSTRUCTIONS", "_register_agent_tools", "build_agents"]
