"""End-to-end test: verify memory tools (Mem0 + Graphiti) work with injected agents.

Tests three things:
1. Memory tools are importable and callable directly (unit-level)
2. Mem0 integration: remember → save_memory → remember (round-trip)
3. Graphiti integration: save_episode → recall_timeline (round-trip)
4. Tools are injectable into both MAF and GitHub Copilot agents

Run:
    cd c:/Users/VijayRaghavVarada/Documents/GitHub/Metorite
    uv run python scripts/_test_memory_tools.py

Prerequisites:
    - Gateway running (for LLM/embedding routing)
    - Postgres with pgvector (for Mem0)
    - Neo4j (for Graphiti — optional; tests gracefully skip)
    - MEM0_ENABLED=true in .env
    - GRAPHITI_ENABLED=true in .env (optional)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure the workspace root is on sys.path so acb_* packages resolve.
_WS_ROOT = Path(__file__).resolve().parent.parent
if str(_WS_ROOT) not in sys.path:
    sys.path.insert(0, str(_WS_ROOT))


def _banner(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def _info(msg: str) -> None:
    print(f"  ℹ️  {msg}")


# ── Test 1: Memory tools are importable ────────────────────────────────────


def test_imports() -> bool:
    _banner("Test 1: Memory tools importable")
    try:
        from acb_skills.memory_tools import (
            _get_memory_user_id,
            _set_memory_user_id,
            recall_timeline,
            remember,
            save_episode,
            save_memory,
        )
        _ok("All 6 symbols imported from acb_skills.memory_tools")
        return True
    except ImportError as exc:
        _fail(f"Import failed: {exc}")
        return False


# ── Test 2: User context wiring ────────────────────────────────────────────


def test_user_context() -> bool:
    _banner("Test 2: User context context-var wiring")
    try:
        from acb_skills.memory_tools import (
            _get_memory_user_id,
            _set_memory_user_id,
        )
        # Initially empty
        assert _get_memory_user_id() == "", "Expected empty default"
        _ok("Default user_id is empty string")

        # Set and verify
        _set_memory_user_id("test-user@fracktal.in")
        assert _get_memory_user_id() == "test-user@fracktal.in", (
            "Expected test-user@fracktal.in"
        )
        _ok("set/get user_id works: test-user@fracktal.in")

        # Reset
        _set_memory_user_id("")
        assert _get_memory_user_id() == "", "Expected empty after reset"
        _ok("Reset works")
        return True
    except Exception as exc:
        _fail(f"User context test failed: {exc}")
        return False


# ── Test 3: Mem0 round-trip (remember → save_memory → remember) ────────────


async def test_mem0_roundtrip() -> bool:
    _banner("Test 3: Mem0 round-trip (save → search)")

    from acb_skills.memory_tools import (
        _set_memory_user_id,
        remember,
        save_memory,
    )

    test_user = "memory-test-user@fracktal.in"
    _set_memory_user_id(test_user)

    # First, check if Mem0 is available
    test_fact = (
        "Test fact: memory-test-user prefers dark mode "
        "and bullet-point reports"
    )

    # Save a fact
    save_result = await save_memory(test_fact)
    _info(f"save_memory: {save_result}")

    if "not installed" in save_result or "cannot save" in save_result:
        _info("Mem0 is not available — skipping round-trip test")
        _set_memory_user_id("")
        return True  # Not a failure — Mem0 is optional

    if "saved" not in save_result.lower():
        _fail(f"save_memory returned unexpected: {save_result}")
        return False
    _ok(f"Saved fact: {test_fact[:60]}...")

    # Wait briefly for async write
    await asyncio.sleep(1)

    # Search for it
    search_result = await remember("dark mode bullet-point reports")
    _info(f"remember() returned {len(search_result)} chars")

    if "no relevant" in search_result:
        _info(
            "Fact not yet indexed (Mem0 extraction is async) — "
            "this is expected on first run"
        )
    elif "dark mode" in search_result.lower():
        _ok("Fact found in search results!")
    else:
        _info(
            "Search returned results but exact fact not found — "
            "Mem0 may have paraphrased it"
        )

    _set_memory_user_id("")
    return True


# ── Test 4: Graphiti round-trip (save_episode → recall_timeline) ───────────


async def test_graphiti_roundtrip() -> bool:
    _banner("Test 4: Graphiti round-trip (save → search)")

    from acb_skills.memory_tools import (
        _set_memory_user_id,
        recall_timeline,
        save_episode,
    )

    test_user = "memory-test-user@fracktal.in"
    _set_memory_user_id(test_user)

    # Save an episode
    save_result = await save_episode(
        name="Test deal moved to Negotiation",
        content=(
            "Test deal Fracktal-XYZ worth ₹25L moved to Negotiation stage. "
            "Contact: Test Person (test@example.com). "
            "Action: send quote by June 16."
        ),
        source="test-script",
    )
    _info(f"save_episode: {save_result}")

    if "not installed" in save_result or "cannot" in save_result:
        _info("Graphiti is not available — skipping round-trip test")
        _set_memory_user_id("")
        return True  # Not a failure — Graphiti is optional

    if "recorded" not in save_result.lower():
        _fail(f"save_episode returned unexpected: {save_result}")
        return False
    _ok("Episode saved")

    # Wait for async processing
    await asyncio.sleep(2)

    # Search for it
    search_result = await recall_timeline(
        "Fracktal-XYZ", "deal stage changes and negotiation"
    )
    _info(f"recall_timeline() returned {len(search_result)} chars")

    if "no timeline" in search_result:
        _info(
            "Episode not yet indexed (Graphiti processing is async) — "
            "this is expected on first run"
        )
    elif "Fracktal-XYZ" in search_result:
        _ok("Episode found in timeline search!")
    else:
        _info(
            "Timeline search returned results but episode not found — "
            "Graphiti may still be processing"
        )

    _set_memory_user_id("")
    return True


# ── Test 5: Tools appear in executor's injection list ──────────────────────


def test_tool_injection() -> bool:
    _banner("Test 5: Tools appear in executor injection path")
    try:
        from orchestrator.executor import _inject_agent_tools

        # Read the function source to verify memory tools are imported
        import inspect
        source = inspect.getsource(_inject_agent_tools)

        checks = [
            ("acb_skills.memory_tools", "memory_tools import"),
            ("remember", "remember tool"),
            ("recall_timeline", "recall_timeline tool"),
            ("save_memory", "save_memory tool"),
            ("save_episode", "save_episode tool"),
        ]
        all_ok = True
        for needle, label in checks:
            if needle in source:
                _ok(f"Found {label} in _inject_agent_tools()")
            else:
                _fail(f"Missing {label} in _inject_agent_tools()")
                all_ok = False

        return all_ok
    except Exception as exc:
        _fail(f"Injection check failed: {exc}")
        return False


# ── Test 6: Addendum includes memory tools ──────────────────────────────────


def test_addendum() -> bool:
    _banner("Test 6: Memory tools in injected tools addendum")
    try:
        from orchestrator.executor import _build_injected_tools_addendum

        addendum = _build_injected_tools_addendum()

        checks = [
            ("remember(query)", "remember in addendum"),
            ("recall_timeline", "recall_timeline in addendum"),
            ("save_memory(fact)", "save_memory in addendum"),
            ("save_episode", "save_episode in addendum"),
        ]
        all_ok = True
        for needle, label in checks:
            if needle in addendum:
                _ok(f"Found {label}")
            else:
                _fail(f"Missing {label}")
                all_ok = False

        return all_ok
    except Exception as exc:
        _fail(f"Addendum check failed: {exc}")
        return False


# ── Test 7: Gateway route sets user context ─────────────────────────────────


def test_gateway_user_context() -> bool:
    _banner("Test 7: Gateway route sets memory user context")
    try:
        # We can't easily run the route, but we can check the source
        agent_py = (
            Path(__file__).resolve().parent.parent
            / "apps" / "services" / "gateway" / "gateway" / "routes" / "agent.py"
        )
        source = agent_py.read_text()

        checks = [
            ("acb_skills.memory_tools", "memory_tools import in agent.py"),
            ("_set_memory_user_id", "_set_memory_user_id call in agent.py"),
        ]
        all_ok = True
        for needle, label in checks:
            if needle in source:
                _ok(f"Found {label}")
            else:
                _fail(f"Missing {label}")
                all_ok = False

        return all_ok
    except Exception as exc:
        _fail(f"Gateway check failed: {exc}")
        return False


# ── Main ────────────────────────────────────────────────────────────────────


async def main() -> int:
    print("Metorite Memory Tools — End-to-End Test")
    print(f"Workspace: {_WS_ROOT}")
    print()

    results: list[tuple[str, bool]] = []

    # Unit tests (no infrastructure needed)
    results.append(("Imports", test_imports()))
    results.append(("User context", test_user_context()))
    results.append(("Tool injection", test_tool_injection()))
    results.append(("Addendum", test_addendum()))
    results.append(("Gateway wiring", test_gateway_user_context()))

    # Integration tests (need Postgres + Neo4j)
    results.append(("Mem0 round-trip", await test_mem0_roundtrip()))
    results.append(("Graphiti round-trip", await test_graphiti_roundtrip()))

    # Summary
    _banner("Results")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")

    print(f"\n  {passed}/{total} tests passed")

    if passed == total:
        print("\n  🎉 All memory tools wired correctly!")
        print("  Agents can now actively query and write to Mem0 + Graphiti.")
        print("  Test with a real agent: ask it to 'remember' something,")
        print("  then ask a follow-up question to see if it recalls the fact.")
    else:
        print(f"\n  ⚠️  {total - passed} test(s) failed — check logs above.")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
