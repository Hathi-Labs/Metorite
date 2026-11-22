#!/usr/bin/env python
"""Integration test: identity chain end-to-end (M2.7 — WBS 1.7).

Validates the full auth flow from Next.js proxy headers to gateway identity
resolution and chat-session scoping.  Requires a running gateway + Postgres.

Usage:
    uv run python tests/integration/test_auth_identity_chain.py

Pre-requisites:
    - Gateway running at GATEWAY_URL (default http://127.0.0.1:8000)
    - GATEWAY_INTERNAL_TOKEN set in .env. The LLM key is not service
      identity, so there is no fallback to LITELLM_MASTER_KEY (WS-24b).
    - Postgres accessible (chat session scoping tests write to DB)

What it tests:
    1. /health              — public (no auth required)
    2. Bearer + user headers → real identity on /copilot/chat
    3. Bearer only          → agent identity (legacy)
    4. Chat session scoping — sessions created by user A invisible to user B
    5. SSO-only (no Bearer) → domain enforcement
"""
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass

import httpx

# ═══════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════

GATEWAY_URL = os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8000")
INTERNAL_TOKEN = os.environ.get("GATEWAY_INTERNAL_TOKEN", "")

USER_A_EMAIL = "alice@fracktal.in"
USER_A_ROLE = "employee"
USER_B_EMAIL = "bob@fracktal.in"
USER_B_ROLE = "executive"


@dataclass
class Result:
    name: str
    passed: bool
    detail: str = ""


results: list[Result] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append(Result(name, passed, detail))
    status = "PASS" if passed else "FAIL"
    print(f"  {status}  {name}")
    if detail:
        print(f"        {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def bearer_headers(email: str | None = None, role: str | None = None) -> dict:
    """Build headers with Bearer token + optional user identity."""
    h = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}
    if email:
        h["X-User-Email"] = email
    if role:
        h["X-User-Role"] = role
    return h


def sso_headers(email: str, role: str = "employee") -> dict:
    """Build headers with SSO user identity only (no Bearer)."""
    return {"X-User-Email": email, "X-User-Role": role}


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════

async def test_health_public() -> None:
    """GET /health must succeed without any auth headers."""
    name = "health endpoint is public"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{GATEWAY_URL}/health")
            ok = r.status_code == 200
            record(name, ok, f"status={r.status_code}" if not ok else "")
    except Exception as exc:
        record(name, False, str(exc))


async def test_chat_sessions_list_scoped_to_user() -> None:
    """GET /chat/sessions must return only sessions owned by the caller."""
    name = "chat sessions scoped to user email"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Create a session as user A
            session_id = f"test-session-{uuid.uuid4().hex[:8]}"
            r = await client.post(
                f"{GATEWAY_URL}/chat/sessions",
                json={
                    "id": session_id,
                    "agent_name": "orchestrator",
                    "title": "Alice's test session",
                },
                headers=bearer_headers(USER_A_EMAIL, USER_A_ROLE),
            )
            if r.status_code != 200:
                record(name, False, f"create session failed: {r.status_code} {r.text}")
                return

            # List sessions as user A → should see the session
            r = await client.get(
                f"{GATEWAY_URL}/chat/sessions",
                headers=bearer_headers(USER_A_EMAIL, USER_A_ROLE),
            )
            sessions_a = r.json()
            found_by_a = any(s["id"] == session_id for s in sessions_a)

            # List sessions as user B → should NOT see user A's session
            r = await client.get(
                f"{GATEWAY_URL}/chat/sessions",
                headers=bearer_headers(USER_B_EMAIL, USER_B_ROLE),
            )
            sessions_b = r.json()
            found_by_b = any(s["id"] == session_id for s in sessions_b)

            # Cleanup
            await client.delete(
                f"{GATEWAY_URL}/chat/sessions/{session_id}",
                headers=bearer_headers(USER_A_EMAIL, USER_A_ROLE),
            )

            passed = found_by_a and not found_by_b
            detail = (
                f"user_A sees session={found_by_a}, user_B sees session={found_by_b}"
            )
            record(name, passed, detail if not passed else "")
    except Exception as exc:
        record(name, False, str(exc))


async def test_bearer_with_user_headers_resolves_real_identity() -> None:
    """Bearer + X-User-Email → gateway must resolve to the real user, not agent."""
    name = "Bearer + user headers resolves real identity"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Use /chat/sessions as a proxy to check identity resolution.
            # Create a session, then try to PATCH it as a different user.
            session_id = f"test-iden-{uuid.uuid4().hex[:8]}"
            r = await client.post(
                f"{GATEWAY_URL}/chat/sessions",
                json={"id": session_id, "agent_name": "orchestrator"},
                headers=bearer_headers(USER_A_EMAIL, USER_A_ROLE),
            )
            if r.status_code != 200:
                record(name, False, f"create failed: {r.status_code}")
                return

            # PATCH as user B — should 404 because session is owned by user A
            r = await client.patch(
                f"{GATEWAY_URL}/chat/sessions/{session_id}",
                json={"title": "Hijacked!"},
                headers=bearer_headers(USER_B_EMAIL, USER_B_ROLE),
            )
            # Session ownership check: B should NOT be able to patch A's session.
            patched_by_b = r.status_code == 200

            # PATCH as user A — should succeed
            r = await client.patch(
                f"{GATEWAY_URL}/chat/sessions/{session_id}",
                json={"title": "Alice's legit update"},
                headers=bearer_headers(USER_A_EMAIL, USER_A_ROLE),
            )
            patched_by_a = r.status_code == 200

            # Cleanup
            await client.delete(
                f"{GATEWAY_URL}/chat/sessions/{session_id}",
                headers=bearer_headers(USER_A_EMAIL, USER_A_ROLE),
            )

            passed = patched_by_a and not patched_by_b
            detail = (
                f"user_A patch={patched_by_a}, user_B patch={patched_by_b}"
                if not passed else ""
            )
            record(name, passed, detail)
    except Exception as exc:
        record(name, False, str(exc))


async def test_bearer_only_retains_agent_role() -> None:
    """Bearer token without user headers → system:internal + agent role."""
    name = "Bearer-only retains agent role (legacy compat)"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Create a session with Bearer-only (agent role).
            # The session should be scoped to "system:internal".
            session_id = f"test-agent-{uuid.uuid4().hex[:8]}"
            r = await client.post(
                f"{GATEWAY_URL}/chat/sessions",
                json={"id": session_id, "agent_name": "orchestrator"},
                headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
            )
            # Cleanup
            await client.delete(
                f"{GATEWAY_URL}/chat/sessions/{session_id}",
                headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
            )
            record(name, r.status_code == 200, f"status={r.status_code}" if r.status_code != 200 else "")
    except Exception as exc:
        record(name, False, str(exc))


async def test_sso_without_bearer_rejects_non_fracktal() -> None:
    """SSO headers (no Bearer) with non-fracktal.in email → anonymous."""
    name = "SSO-only rejects non-fracktal.in domain"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Create a session with non-fracktal email via SSO-only (no Bearer).
            # The session should be scoped to "default" (email stripped to None).
            session_id = f"test-dom-{uuid.uuid4().hex[:8]}"
            r = await client.post(
                f"{GATEWAY_URL}/chat/sessions",
                json={"id": session_id, "agent_name": "orchestrator"},
                headers=sso_headers("hacker@gmail.com", "executive"),
            )
            # Should succeed but session scoped to "default" (not hacker@gmail.com)
            if r.status_code == 200:
                # Verify the session was created under "default"
                r2 = await client.get(
                    f"{GATEWAY_URL}/chat/sessions",
                    headers={"X-User-Email": ""},  # triggers anonymous path
                )
                sessions = r2.json()
                found = any(s["id"] == session_id for s in sessions)
                # Cleanup
                await client.delete(
                    f"{GATEWAY_URL}/chat/sessions/{session_id}",
                    headers={"X-User-Email": ""},
                )
                record(name, found, "session scoped to 'default' as expected" if found else "session not found in default scope")
            else:
                record(name, False, f"create failed: {r.status_code}")
    except Exception as exc:
        record(name, False, str(exc))


async def test_gateway_unreachable() -> bool:
    """Quick pre-flight: is the gateway responding?"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{GATEWAY_URL}/health")
            return r.status_code == 200
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

async def main() -> int:
    print(f"Gateway: {GATEWAY_URL}")
    print(f"Internal token: {'***' if INTERNAL_TOKEN else '(empty — Bearer disabled)'}")
    print()

    # Pre-flight
    reachable = await test_gateway_unreachable()
    if not reachable:
        print(f"ERROR: Gateway at {GATEWAY_URL} is not reachable.")
        print("Start it with: cd apps/services/gateway && uv run uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload")
        print()
        # Still run the tests that don't need gateway (none in this script)
        return 1

    print("Running identity-chain integration tests...\n")

    await test_health_public()
    await test_bearer_only_retains_agent_role()
    await test_bearer_with_user_headers_resolves_real_identity()
    await test_chat_sessions_list_scoped_to_user()
    await test_sso_without_bearer_rejects_non_fracktal()

    # Summary
    print()
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    print(f"{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {len(results)} total")
    print(f"{'='*60}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
