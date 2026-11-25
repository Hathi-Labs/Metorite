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
worst this agent can do is prepare words the founder reviews — never speak in
their name unprompted.

Registered as a MAF agent (name "whatsapp-assistant"); build_agents() is the
Dynamic Agent Loader entry point. Structure mirrors agent-email-assistant.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from acb_common import get_logger, get_settings
from acb_common.paging import prefix_limit, prefix_page
from acb_common.settings import internal_auth_headers

# H-236: every tool states ``open_world``. Each one is a read or a draft over
# the gateway's own ``/whatsapp/*`` routes, and none sends (the DOCTRINE
# above), so each says ``open_world=False``. A run that a covered Projects
# run calls keeps them. A future send tool must say ``open_world=True``.
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
    raise RuntimeError(
        f"WhatsApp {method} {path} failed ({resp.status_code})"
        + (f": {detail}" if detail else "")
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


# ── Settings, rules, calls and link reads (PAR-4a, task 3960) ───────────────
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
        lines.append(f"• {r.get('title')}{key}: {(r.get('body') or '')[:100]}")
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
