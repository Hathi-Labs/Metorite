#!/usr/bin/env python
"""E2E memory test: MAF orchestrator + Copilot SDK named agent.

Verifies the full memory cycle:
  1. Turn 1 → agent responds → facts extracted post-stream to Mem0
  2. Turn 2 (same session) → agent has session continuity
  3. New session → agent gets Mem0 memory injection from Turn 1

Usage:
  uv run python scripts/_test_memory_e2e.py [--skip-copilot] [--skip-orchestrator]

Requires:
  GATEWAY_BASE_URL (default http://127.0.0.1:8080)
  GATEWAY_INTERNAL_TOKEN (or LITELLM_MASTER_KEY)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any

import httpx

GATEWAY = os.environ.get("E2E_GATEWAY_URL", os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8080"))
TOKEN = os.environ.get("E2E_TOKEN", os.environ.get("GATEWAY_INTERNAL_TOKEN", os.environ.get("LITELLM_MASTER_KEY", "sk-local-dev-change-me")))
# The memory endpoints scope by identity, not just by token: a personal scope
# needs the platform to say WHO it is acting for (gateway/routes/memory.py
# _authorize_scope). The token alone reaches shared scopes only.
E2E_ACTOR = os.environ.get("E2E_ACTOR", "e2e-memory-test@fracktal.in")
AUTH = {"Authorization": f"Bearer {TOKEN}", "X-User-Email": E2E_ACTOR}

PASS = 0
FAIL = 0

def ok(label: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {label}")

def bad(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  ❌ {label}")
    if detail:
        print(f"     {detail}")


async def stream_agent_chat(
    client: httpx.AsyncClient,
    agent_name: str,
    message: str,
    history: list[dict[str, str]] | None = None,
    thread_id: str | None = None,
    think_mode: str = "auto",
) -> tuple[str, str]:
    """Send a chat message to the gateway and collect the full response.

    Returns (assistant_text, thread_id).
    """
    tid = thread_id or str(uuid.uuid4())
    payload: dict[str, Any] = {
        "agent": agent_name,
        "payload": {
            "mode": "chat",
            "message": message,
            "messages": history or [],
            "think_mode": think_mode,
        },
        "thread_id": tid,
    }

    assistant_text = ""
    async with client.stream(
        "POST",
        f"{GATEWAY}/agent/run/stream",
        json=payload,
        headers={**AUTH, "Content-Type": "application/json"},
        timeout=httpx.Timeout(300.0),
    ) as resp:
        if resp.status_code != 200:
            body = await resp.aread()
            raise RuntimeError(f"Gateway returned {resp.status_code}: {body[:500]}")
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:].strip()
            if not raw:
                continue
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = evt.get("type")
            if t == "TEXT_MESSAGE_CONTENT":
                assistant_text += evt.get("delta", "")
            elif t in ("RUN_ERROR",):
                bad(f"Agent error: {evt.get('message', '')}")
                raise RuntimeError(evt.get("message", "Unknown error"))
            elif t == "RUN_FINISHED":
                break
    return assistant_text, tid


async def orchestrator_chat(
    client: httpx.AsyncClient,
    message: str,
    history: list[dict[str, str]] | None = None,
    thread_id: str | None = None,
) -> tuple[str, str]:
    """Send a message via /copilot/chat (MAF orchestrator)."""
    tid = thread_id or str(uuid.uuid4())
    messages = (history or []) + [{"role": "user", "content": message}]
    payload = {
        "thread_id": tid,
        "messages": messages,
        "think_mode": "auto",
    }
    assistant_text = ""
    async with client.stream(
        "POST",
        f"{GATEWAY}/copilot/chat",
        json=payload,
        headers={**AUTH, "Content-Type": "application/json"},
        timeout=httpx.Timeout(300.0),
    ) as resp:
        if resp.status_code != 200:
            body = await resp.aread()
            raise RuntimeError(f"Gateway returned {resp.status_code}: {body[:500]}")
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:].strip()
            if not raw:
                continue
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = evt.get("type")
            if t == "TEXT_MESSAGE_CONTENT":
                assistant_text += evt.get("delta", "")
            elif t in ("RUN_ERROR", "RUN_FAILED"):
                bad(f"Orchestrator error: {evt.get('message', evt.get('error', ''))}")
                raise RuntimeError(str(evt.get("message", evt.get("error", "Unknown"))))
            elif t in ("RUN_FINISHED", "done"):
                break
    return assistant_text, tid


async def check_memories(client: httpx.AsyncClient, user_id: str) -> list[dict]:
    """Check what memories Mem0 has stored for a user."""
    resp = await client.get(
        f"{GATEWAY}/memory/{user_id}",
        headers=AUTH,
        timeout=httpx.Timeout(10.0),
    )
    if resp.status_code != 200:
        return []
    return resp.json()


# ---------------------------------------------------------------------------
# Test: Orchestrator (MAF agent) — two-turn context
# ---------------------------------------------------------------------------

async def test_orchestrator_context(client: httpx.AsyncClient) -> None:
    print("\n── Orchestrator (MAF) two-turn context ──")

    tid = str(uuid.uuid4())
    history: list[dict[str, str]] = []

    # Turn 1: tell the agent a personal fact
    msg1 = "My name is Vijay and I prefer project updates in bullet-point format."
    text1, _ = await orchestrator_chat(client, msg1, history=history, thread_id=tid)
    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": text1})

    ok(f"Turn 1 response ({len(text1)} chars)")
    if len(text1) < 10:
        bad("Turn 1 too short — may indicate model failure")

    # Wait briefly for post-stream memory extraction
    await asyncio.sleep(3)

    # Turn 2: ask the agent something that requires remembering Turn 1
    msg2 = "What format do I prefer for project updates?"
    text2, _ = await orchestrator_chat(client, msg2, history=history, thread_id=tid)

    ok(f"Turn 2 response ({len(text2)} chars)")
    lowered = text2.lower()
    if "bullet" in lowered or "bullet-point" in lowered or "bullet point" in lowered:
        ok("Turn 2 references bullet-point preference from Turn 1")
    else:
        bad("Turn 2 did not reference bullet-point preference",
            f"Response: {text2[:200]}")

    # Turn 3: ask about name
    msg3 = "What's my name?"
    text3, _ = await orchestrator_chat(client, msg3, history=history, thread_id=tid)

    if "vijay" in text3.lower():
        ok("Turn 3 remembers name 'Vijay' from Turn 1")
    else:
        bad("Turn 3 did not remember name",
            f"Response: {text3[:200]}")


# ---------------------------------------------------------------------------
# Test: Named Copilot SDK agent — two-turn context
# ---------------------------------------------------------------------------

async def test_copilot_agent_context(client: httpx.AsyncClient) -> None:
    print("\n── Copilot SDK agent two-turn context ──")

    tid = str(uuid.uuid4())
    history: list[dict[str, str]] = []

    # Turn 1: tell the agent a specific fact
    msg1 = (
        "Remember this: my company Fracktal Works has 12 employees "
        "and our biggest client is Acme Corp. I'm telling you this "
        "so you remember it for our conversation."
    )
    text1, _ = await stream_agent_chat(
        client, "crm-assistant", msg1, history=history, thread_id=tid,
    )
    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": text1})

    ok(f"Turn 1 response ({len(text1)} chars)")
    if len(text1) < 10:
        bad("Turn 1 too short")

    await asyncio.sleep(3)

    # Turn 2: ask about facts from Turn 1 — requires session continuity
    msg2 = "How many employees does Fracktal Works have?"
    text2, _ = await stream_agent_chat(
        client, "crm-assistant", msg2, history=history, thread_id=tid,
    )

    ok(f"Turn 2 response ({len(text2)} chars)")
    if "12" in text2:
        ok("Turn 2 correctly references employee count from Turn 1")
    else:
        bad("Turn 2 did not remember employee count",
            f"Response: {text2[:300]}")

    # Turn 3: ask about client
    msg3 = "Who is our biggest client?"
    text3, _ = await stream_agent_chat(
        client, "crm-assistant", msg3, history=history, thread_id=tid,
    )

    if "acme" in text3.lower():
        ok("Turn 3 correctly references client name from Turn 1")
    else:
        bad("Turn 3 did not remember client name",
            f"Response: {text3[:300]}")


# ---------------------------------------------------------------------------
# Test: Cross-session Mem0 memory (requires prior extraction)
# ---------------------------------------------------------------------------

async def test_cross_session_memory(client: httpx.AsyncClient) -> None:
    print("\n── Cross-session Mem0 memory ──")

    user_id = E2E_ACTOR   # must match the asserted identity

    # First session: deposit facts
    tid1 = str(uuid.uuid4())
    msg1 = (
        "My name is E2E Test User. I work from the Bangalore office. "
        "I always want reports sorted by priority, highest first. "
        "My preferred communication channel is email."
    )
    text1, _ = await orchestrator_chat(client, msg1, thread_id=tid1)

    ok(f"Session 1 deposit ({len(text1)} chars)")

    # Manually trigger Mem0 extraction (mirrors what the Next.js proxy does).
    # In production, route.ts calls this automatically after the stream ends.
    conv = [
        {"role": "user", "content": msg1},
        {"role": "assistant", "content": text1},
    ]
    print("  Triggering Mem0 extraction...")
    try:
        r = await client.post(
            f"{GATEWAY}/memory/{user_id}/add",
            json={"messages": conv, "agent_id": "orchestrator"},
            headers=AUTH,
            timeout=httpx.Timeout(30),
        )
        print(f"  Extraction status: {r.status_code} - {r.text[:100]}")
    except Exception as e:
        print(f"  Extraction error: {e}")

    # Wait for async extraction to complete
    print("  Waiting 5s for Mem0 processing...")
    await asyncio.sleep(5)

    # Check Mem0 has stored facts
    memories = await check_memories(client, user_id)
    if len(memories) > 0:
        ok(f"Mem0 has {len(memories)} memories stored")
        for m in memories[:3]:
            print(f"     • {m.get('memory', str(m))[:100]}")
    else:
        bad("Mem0 has 0 memories — extraction may not have fired")

    # Second session: new thread_id — relies solely on Mem0
    tid2 = str(uuid.uuid4())
    msg2 = "What do you know about my work preferences?"
    text2, _ = await orchestrator_chat(client, msg2, thread_id=tid2)

    ok(f"Session 2 response ({len(text2)} chars)")
    lowered = text2.lower()
    checks = [
        ("bangalore", "Bangalore office"),
        ("priority", "priority sorting"),
        ("email", "email preference"),
    ]
    for keyword, label in checks:
        if keyword in lowered:
            ok(f"Recalls: {label}")
        else:
            bad(f"Missing: {label}", f"Response: {text2[:300]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print(f"Gateway: {GATEWAY}")
    print(f"Token:   {TOKEN[:10]}...")

    skip_copilot = "--skip-copilot" in sys.argv
    skip_orchestrator = "--skip-orchestrator" in sys.argv

    async with httpx.AsyncClient() as client:
        # Quick health check
        try:
            r = await client.get(f"{GATEWAY}/health", timeout=5.0)
            if r.status_code == 200:
                ok("Gateway healthy")
            else:
                bad(f"Gateway returned {r.status_code}")
                return
        except Exception as e:
            bad(f"Gateway unreachable: {e}")
            return

        if not skip_orchestrator:
            try:
                await test_orchestrator_context(client)
            except Exception as e:
                bad(f"Orchestrator test failed: {e}")

        if not skip_copilot:
            try:
                await test_copilot_agent_context(client)
            except Exception as e:
                bad(f"Copilot agent test failed: {e}")

        try:
            await test_cross_session_memory(client)
        except Exception as e:
            bad(f"Cross-session memory test failed: {e}")

    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
