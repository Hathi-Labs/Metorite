"""Automation · inbox chat — the email AI chat + quick actions (SSE).

The ``/ai/chat`` streaming endpoint, ``/ai/quick-action``, and the context
builder they share. Moved out of ``replyzero.py`` (2.3 split): chat is a
consumer OF thread status, not part of deciding it — the ~390 lines of SSE
plumbing finally leave the Reply Zero authority module.
"""

from __future__ import annotations

import json
import os
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from gateway.routes.email.core import (
    _tenant_session,
    _log,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text


class AIChatRequest(BaseModel):
    messages: list[dict[str, str]]
    account_id: str | None = None
    email_context_id: str | None = None
    session_id: str | None = None  # stable per conversation (thread continuity)


class QuickActionRequest(BaseModel):
    action: str  # 'summarize' | 'find_urgent' | 'draft_reply' | 'unsubscribe'
    account_id: str | None = None
    email_id: str | None = None


async def _build_chat_context(
    user_id: str, account_id: str | None, email_context_id: str | None,
    is_first_turn: bool,
) -> tuple[str | None, list[str]]:
    """Resolve which account the assistant should act on (defaulting to the user's
    only account) and assemble the background context it receives via
    ``memory_context``: an account hint, an inbox snapshot on the first turn, and
    the email currently open in the reader. Returns ``(account_id, context_parts)``.
    Best-effort — never raises.

    ⚠️ D-EM-4 (EM-T2c item 1): the resolved id is ALWAYS a mailbox of
    ``user_id``, or None. ``account_id`` comes from the request body, so an id
    the member does not own is dropped, never kept. Both members can be in one
    organization, so row level security does not hide the other mailbox. The
    snapshot below reads by ``account_id`` alone and trusts this resolution.
    Fence: ``tests/unit/test_email_chat_context_owner.py`` (R8)."""
    parts: list[str] = []
    resolved: str | None = None
    try:
        async with _tenant_session() as db:
            accounts = (await db.execute(text(
                "SELECT id, email_address FROM email_accounts WHERE user_id = :uid "
                "ORDER BY created_at"
            ), {"uid": user_id})).fetchall()
            acc_map = {str(a.id): a.email_address for a in accounts}
            if account_id and account_id in acc_map:
                resolved = account_id
            elif len(accounts) == 1:
                resolved = str(accounts[0].id)

            # The user's OWN addresses — so the model never reports the user as a
            # sender / "someone who emails you"; mail from these was sent BY the user.
            own_addrs = ", ".join(sorted(acc_map.values()))
            if own_addrs:
                parts.append(
                    "## You (the account owner)\n"
                    f"You are acting on behalf of the user, whose own email "
                    f"address(es) are: {own_addrs}. NEVER report any of these as a "
                    "sender, a top sender, or someone who emails the user — messages "
                    "from them were sent BY the user.")

            if resolved and resolved in acc_map:
                parts.append(
                    "## Email account\n"
                    f"Act on account id={resolved} ({acc_map[resolved]}). Pass this "
                    "account_id to tools unless the user names another account.")
            elif len(accounts) > 1:
                listing = "; ".join(
                    f"{a.email_address} (id={a.id})" for a in accounts)
                parts.append(
                    "## Email accounts\nThe user has several accounts: " + listing
                    + ". Ask which one before account-scoped actions.")

            if resolved and is_first_turn:
                tot = (await db.execute(text(
                    "SELECT count(*) AS total, "
                    "count(*) FILTER (WHERE is_read = false) AS unread "
                    "FROM email_messages WHERE account_id = :aid "
                    "AND LOWER(folder) = 'inbox'"
                ), {"aid": resolved})).fetchone()
                nr = (await db.execute(text(
                    "SELECT count(*) AS c FROM email_thread_status "
                    "WHERE account_id = :aid AND status = 'NEEDS_REPLY'"
                ), {"aid": resolved})).fetchone()
                cats = (await db.execute(text(
                    "SELECT category, count(*) AS c FROM email_senders "
                    "WHERE account_id = :aid AND category IS NOT NULL "
                    "GROUP BY category ORDER BY c DESC LIMIT 6"
                ), {"aid": resolved})).fetchall()
                cat_str = ", ".join(
                    f"{r.category}: {r.c}" for r in cats) or "not categorized yet"
                parts.append(
                    "## Inbox snapshot\n"
                    f"- Inbox: {tot.total if tot else 0} messages, "
                    f"{tot.unread if tot else 0} unread\n"
                    f"- Needs reply (Reply Zero): {nr.c if nr else 0}\n"
                    f"- Sender categories: {cat_str}\n"
                    "To answer questions about the WHOLE inbox use query_inbox "
                    "(filter by date/category/sender/read-state), "
                    "find_priority(kind=important|needs_reply|urgent), or "
                    "get_account_overview; read_email for one email's full content.")

            if email_context_id:
                row = (await db.execute(text(
                    "SELECT em.subject, em.body_text, em.from_address, em.received_at "
                    "FROM email_messages em "
                    "JOIN email_accounts ea ON em.account_id = ea.id "
                    "WHERE em.id = :id AND ea.user_id = :uid"
                ), {"id": email_context_id, "uid": user_id})).fetchone()
                if row:
                    frm = row.from_address if isinstance(row.from_address, dict) \
                        else json.loads(row.from_address or "{}")
                    parts.append(
                        f"## Email open in the reader (id={email_context_id})\n"
                        f"From: {frm.get('name') or ''} <{frm.get('email') or ''}>\n"
                        f"Subject: {row.subject or ''}\n"
                        f"Date: {row.received_at}\n\n"
                        f"{(row.body_text or '')[:5000]}")
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.chat_context_failed", error=str(exc)[:160])
    return resolved, parts


@router.post("/ai/chat")
async def ai_chat(
    req: AIChatRequest,
    user: UserContext = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """AI assistant chat — streams SSE events from the email assistant agent.

    Routes through the orchestrator's run_agent_stream with the email-assistant
    agent, translating AG-UI protocol events into the frontend SSE format
    (type: 'start' / 'content' / 'done').
    """
    import uuid

    user_id: str = getattr(user, "email", "") or "anonymous"
    run_id = str(uuid.uuid4())

    # ── Set user context for memory tools ──
    # Deliberately the BARE user email, NOT an account-scoped key: the agent
    # reuses this same ContextVar as its X-User-Email gateway-auth identity (see
    # agents._current_user_email), so a "#acct:" suffix would break every tool
    # call. The drafting pipeline's per-account Mem0 scope (email_memory_scope)
    # lives on the gateway side only — the chat agent's free-form remember()
    # intentionally reads the user-global namespace.
    try:
        from acb_skills.memory_tools import _set_memory_user_id  # noqa: PLC0415
        _set_memory_user_id(user_id)
    except ImportError:
        pass

    # Build the agent payload — the last user message + optional email context
    last_user_msg = ""
    if req.messages:
        for m in reversed(req.messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

    # The agent's context is passed through the channels the orchestrator actually
    # consumes: `messages` (multi-turn history, replayed into the MAF message list)
    # and `memory_context` (injected into the agent's instructions every turn).
    # A plain `conversation_history` string key would be silently dropped.
    is_first_turn = sum(
        1 for m in (req.messages or []) if m.get("role") == "user") <= 1

    account_id, ctx_parts = await _build_chat_context(
        user_id, req.account_id, req.email_context_id, is_first_turn)

    payload: dict[str, Any] = {
        "message": last_user_msg or "Help me with my email",
        "user_query": last_user_msg,
        "messages": req.messages or [],
        "memory_context": "\n\n".join(ctx_parts),
        "account_id": account_id,
        "email_context_id": req.email_context_id,
        "user_email": user_id,
        # Attribute this agent run to the email app on the observability bus
        # (executor reads event_payload["source"]; defaults to "chat" otherwise).
        "source": "email",
    }

    # ── Resolve which LiteLLM tier the email CHAT should use (per-account) ──
    # The chat panel uses its own chat_model setting (default tier-powerful — a
    # strong tool-caller, matching _DEFAULT_TASK_MODELS["chat"]), independent of
    # rule evaluation and draft writing.  This fallback only applies when there
    # is no account_id or the per-account lookup fails. It reads the RESOLVED id,
    # never req.account_id: the body may name a mailbox of another member
    # (D-EM-4, EM-T2c item 2).
    chat_model = "tier-powerful"
    if account_id:
        try:
            from gateway.routes.email.automation.assistant import _account_models  # noqa: PLC0415
            async with _tenant_session() as _mdb:
                chat_model = (await _account_models(_mdb, account_id))["chat"]
        except Exception:  # noqa: BLE001
            pass

    # ── Run the agent through the orchestrator ──
    from orchestrator.executor import run_agent_stream  # noqa: PLC0415

    # A stable session id keeps the agent's thread (memory) continuous across the
    # turns of one conversation; fall back to the per-request run id.
    thread_key = req.session_id or run_id
    agent_gen = run_agent_stream(
        "email-assistant",
        payload,
        run_id=run_id,
        thread_id=f"email-chat:{user_id}:{thread_key}",
        model=chat_model,
    )

    async def event_stream():
        """Translate AG-UI protocol events to frontend SSE format."""
        # Set the memory/user ContextVar HERE — this generator runs in Starlette's
        # streaming context (not the handler body), so the agent's tools and
        # memory see the right user only if it's set inside the stream.
        try:
            from acb_skills.memory_tools import _set_memory_user_id  # noqa: PLC0415
            _set_memory_user_id(user_id)
        except Exception:  # noqa: BLE001
            pass

        yield f"data: {json.dumps({'type': 'start'})}\n\n"

        content_buffer: list[str] = []
        # toolCallId → friendly tool name + accumulated args JSON, so the result
        # event can be labelled and carry the structured input (for rich cards).
        tool_names: dict[str, str] = {}
        tool_args: dict[str, str] = {}
        try:
            async for sse_line in agent_gen:
                if not sse_line.startswith("data: "):
                    continue
                try:
                    evt = json.loads(sse_line[len("data: "):])
                except json.JSONDecodeError:
                    continue

                evt_type = evt.get("type", "")
                if evt_type in (
                    "TEXT_MESSAGE_CONTENT",
                    "REASONING_MESSAGE_CONTENT",
                    "THINKING_TEXT_MESSAGE_CONTENT",
                ):
                    delta = str(evt.get("delta", "") or evt.get("content", ""))
                    if delta:
                        content_buffer.append(delta)
                        yield f"data: {json.dumps({'type': 'content', 'text': delta})}\n\n"

                elif evt_type == "TOOL_CALL_START":
                    # Surface tool activity so the chat can render AG-UI cards
                    # (searched inbox / read email / drafted reply / created rule).
                    name = str(evt.get("toolCallName")
                               or evt.get("tool_call_name") or "tool")
                    tid = str(evt.get("toolCallId") or "")
                    tool_names[tid] = name
                    tool_evt = {"type": "tool", "phase": "start",
                                "id": tid, "name": name}
                    yield f"data: {json.dumps(tool_evt)}\n\n"

                elif evt_type == "TOOL_CALL_ARGS":
                    tid = str(evt.get("toolCallId") or "")
                    tool_args[tid] = tool_args.get(tid, "") + str(
                        evt.get("delta") or evt.get("args") or "")

                elif evt_type in ("TOOL_CALL_END", "TOOL_CALL_RESULT"):
                    tid = str(evt.get("toolCallId") or "")
                    result = str(evt.get("content") or evt.get("result") or "")
                    parsed_args: Any = None
                    raw_args = tool_args.get(tid, "")
                    if raw_args:
                        try:
                            parsed_args = json.loads(raw_args)
                        except Exception:  # noqa: BLE001
                            parsed_args = None
                    tool_evt = {
                        "type": "tool", "phase": "result", "id": tid,
                        "name": tool_names.get(tid, "tool"),
                        "result": result[:2000],
                        "args": parsed_args,
                        "success": evt.get("success") is not False,
                    }
                    yield f"data: {json.dumps(tool_evt)}\n\n"

                elif evt_type in ("RUN_FINISHED", "done"):
                    # Capture any TOOL_CALL_RESULT that might be the final answer
                    # when the agent returns a tool result as its last message
                    result_text = evt.get("result") or ""
                    if result_text:
                        content_buffer.append(result_text)
                        yield f"data: {json.dumps({'type': 'content', 'text': result_text})}\n\n"

                elif evt_type == "RUN_ERROR":
                    error_msg = evt.get("error", "Agent encountered an error")
                    yield f"data: {json.dumps({'type': 'error', 'error': error_msg})}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return

        except Exception as exc:  # noqa: BLE001
            _log.error("email.ai_chat_stream_error", error=str(exc))
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

        # ── Save episode to knowledge graph ──
        full_response = "".join(content_buffer)
        if full_response and last_user_msg:
            try:
                from acb_memory import add_episode  # noqa: PLC0415
                background_tasks.add_task(
                    add_episode,
                    name=f"email-assistant:{user_id[:20]}",
                    content=f"Q: {last_user_msg[:300]}\nA: {full_response[:500]}",
                    source_description="email-assistant",
                    group_id=user_id,
                )
            except ImportError:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/ai/quick-action")
async def quick_action(
    req: QuickActionRequest,
    user: UserContext = Depends(get_current_user),
):
    """Trigger a quick AI action (summarize, find urgent, draft reply).

    Calls the email-assistant agent's tool functions directly for fast,
    non-streaming responses to common email workflows.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "email_agent",
        os.path.join(
            os.path.dirname(__file__),
            "..", "..", "..", "..", "..", "..",  # automation → repo apps/
            "agents", "agent-email-assistant", "agents.py",
        ),
    )
    agent_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_mod)

    # Scope the agent's gateway tool calls to the current user.
    try:
        from acb_skills.memory_tools import _set_memory_user_id  # noqa: PLC0415
        _set_memory_user_id(user.email or "")
    except ImportError:
        pass

    try:
        if req.action == "summarize":
            result = await agent_mod.search_emails(
                query="unread",
                folder="inbox",
                account_id=req.account_id,
            )
        elif req.action == "find_urgent":
            result = await agent_mod.find_urgent(account_id=req.account_id)
        elif req.action == "draft_reply":
            if not req.email_id or not req.account_id:
                raise HTTPException(
                    status_code=400,
                    detail="email_id and account_id are required for draft_reply",
                )
            result = await agent_mod.draft_reply(
                email_id=req.email_id, account_id=req.account_id,
            )
        elif req.action == "unsubscribe":
            result = await agent_mod.suggest_unsubscribes(account_id=req.account_id)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown action: {req.action}. Supported: summarize, find_urgent, draft_reply, unsubscribe",
            )

        return {"action": req.action, "result": result, "ok": True}

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _log.error("email.quick_action_error", action=req.action, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Quick action '{req.action}' failed: {str(exc)}",
        )

