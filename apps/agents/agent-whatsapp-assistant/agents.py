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

import httpx
from acb_common import get_logger, get_settings

_log = get_logger("agent.whatsapp_assistant")


_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You are the WhatsApp Assistant. Help the user triage, understand, and "
    "draft replies to their WhatsApp Business messages using the provided tools."
)


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


async def list_whatsapp_chats(
    stream: str = "needs_reply", account_id: str | None = None,
) -> str:
    """List chats in a triage stream: 'needs_reply', 'waiting', 'groups', or
    'all'. Returns each chat's name, last snippet, and chat_id."""
    params: dict[str, Any] = {"stream": stream}
    if account_id:
        params["account_id"] = account_id
    chats = await _get("/whatsapp/chats", params)
    if not chats:
        return f"No chats in the '{stream}' stream."
    lines = [f"Chats in '{stream}' ({len(chats)}):"]
    for c in chats[:20]:
        status = f" [{c['status']}]" if c.get("status") else ""
        win = "" if c.get("window_open", True) else " · window closed"
        lines.append(
            f"• {c.get('name') or c.get('wa_chat_id')}{status}{win}: "
            f"{(c.get('last_snippet') or '')[:80]} (chat_id={c.get('id')})"
        )
    if len(chats) > 20:
        lines.append(f"… and {len(chats) - 20} more")
    return "\n".join(lines)


def _fmt_message(m: dict[str, Any]) -> str:
    who = "You" if m.get("direction") == "out" else (m.get("sender_name") or "Them")
    body = m.get("body_text") or ""
    if not body and m.get("transcript_text"):
        body = f"🎙 “{m['transcript_text']}”"
    elif m.get("kind") not in ("text", None) and not body:
        body = f"[{m.get('kind')}]"
    return f"{who}: {body[:200]}"


async def read_whatsapp_chat(chat_id: str, limit: int = 20) -> str:
    """Read the recent messages of a chat (oldest→newest), including voice-note
    transcripts. Use before drafting a reply or answering a question about a
    conversation."""
    msgs = await _get(f"/whatsapp/chats/{chat_id}/messages", {"limit": limit})
    if not msgs:
        return "No messages in this chat."
    return "\n".join(_fmt_message(m) for m in msgs)


async def search_whatsapp(query: str, account_id: str | None = None) -> str:
    """Search the founder's WhatsApp history by meaning AND keyword (message
    bodies AND voice-note transcripts). Returns matches with chat_id + message_id.
    Semantic re-ranking applies when it's enabled; otherwise this is pure
    keyword search (recall is identical either way)."""
    # hybrid re-ranks by semantic closeness when enabled, and falls back to pure
    # lexical otherwise — so passing it is always safe.
    params: dict[str, Any] = {"q": query, "hybrid": "true"}
    if account_id:
        params["account_id"] = account_id
    msgs = await _get("/whatsapp/search", params)
    if not msgs:
        return f"No messages match '{query}'."
    lines = [f"Matches for '{query}' ({len(msgs)}):"]
    for m in msgs[:15]:
        lines.append(
            f"• {_fmt_message(m)} (chat_id={m.get('chat_id')}, "
            f"message_id={m.get('id')})"
        )
    if len(msgs) > 15:
        lines.append(f"… and {len(msgs) - 15} more")
    return "\n".join(lines)


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
    loops = ctx.get("open_loops", []) or []
    if loops:
        lines.append("• Open loops: " + "; ".join(
            lp.get("title", "") for lp in loops[:5]))
    waiting = ctx.get("waiting_on", []) or []
    if waiting:
        lines.append("• They owe you: " + "; ".join(
            f"{w.get('text', '')[:60]} (commitment_id={w.get('id')})"
            for w in waiting[:5]))
    stats = ctx.get("stats", {}) or {}
    lines.append(f"• History: {stats.get('message_count', 0)} messages")
    return "\n".join(lines)


# ── Understanding tools (AI) ──────────────────────────────────────────────────

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


async def transcribe_whatsapp_voice_note(message_id: str) -> str:
    """Transcribe a voice note by its message_id and fold it into triage (so a
    spoken promise becomes a real commitment). Returns the transcript."""
    r = await _post(f"/whatsapp/messages/{message_id}/transcribe")
    return f"Transcript: “{r.get('transcript_text', '')}”"


# ── Drafting tools (never sends) ──────────────────────────────────────────────

async def draft_whatsapp_reply(chat_id: str) -> str:
    """Draft a reply to a chat in the founder's WhatsApp voice (short, warm, in
    the thread's language). This is a DRAFT — the founder reviews and sends it in
    the app; this tool never sends."""
    r = await _post(f"/whatsapp/chats/{chat_id}/draft")
    return (
        f"Suggested reply ({r.get('language', 'en')}):\n{r.get('draft_text', '')}"
        "\n\n(Draft only — review and send it from the WhatsApp composer.)"
    )


async def draft_waiting_on_nudge(commitment_id: str) -> str:
    """Draft a gentle follow-up to chase something someone owes the founder,
    keyed on a commitment_id from whatsapp_waiting_on / whatsapp_brief. A DRAFT —
    the founder reviews and sends it; this tool never sends."""
    r = await _post(f"/whatsapp/commitments/{commitment_id}/nudge")
    return (
        f"Suggested nudge ({r.get('language', 'en')}):\n{r.get('nudge_text', '')}"
        "\n\n(Draft only — review and send it from the WhatsApp composer.)"
    )


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
    # Drafting (never sends)
    draft_whatsapp_reply,
    draft_waiting_on_nudge,
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
    """Construct the WhatsApp Assistant as a NATIVE MAF agent backed by the
    LiteLLM gateway (same pattern as agent-email-assistant: agent_framework
    ``Agent`` + ``OpenAIChatCompletionClient`` pointed at the gateway's ``/v1``,
    so it runs on the configured LiteLLM tier, never native GitHub Copilot).
    Imported lazily so the module still loads where the optional deps differ."""
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        model=os.environ.get("WHATSAPP_AGENT_MODEL", "tier-balanced"),
        api_key=prov["api_key"],
        base_url=prov["base_url"],
        default_headers={"X-CC-Agent": "whatsapp-assistant", "X-CC-Source": "chat"},
    )
    return [
        Agent(
            client=client,
            instructions=INSTRUCTIONS,
            name="whatsapp-assistant",
            description=(
                "Triages, understands, and drafts WhatsApp Business messages — "
                "the morning brief, waiting-on chases, group summaries, voice-note "
                "transcription, and reply/nudge drafts. Drafts only; never sends."
            ),
            tools=list(_TOOLS),
        )
    ]


__all__ = ["INSTRUCTIONS", "_register_agent_tools", "build_agents"]
