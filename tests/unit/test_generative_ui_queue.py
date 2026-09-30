"""Regression: emit_generative_ui must reach the chat stream for Copilot agents.

The GitHub-Copilot SDK dispatches tool callables from its JSON-RPC read thread
via asyncio.run_coroutine_threadsafe, which runs the coroutine with a FRESH
context — so the executor's `_active_run_queue` ContextVar is invisible inside
a tool invoked by a Copilot agent, and emit_generative_ui failed with
"no active run stream to render into".

The fix routes the queue through a plain module registry (`_RUN_QUEUES`) keyed
by session id, resolved via `resolve_run_queue`, so it survives the thread hop.
These tests simulate the fresh-context case (ContextVar unset) explicitly.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest
from orchestrator import executor as ex

# The acb_skills package re-exports the `write_artifact` FUNCTION under that
# name, which shadows the submodule; import the module object explicitly.
wa = importlib.import_module("acb_skills.write_artifact")


@pytest.fixture(autouse=True)
def _clean_registry():
    ex._RUN_QUEUES.clear()
    wa.bind_artifact_context()
    yield
    ex._RUN_QUEUES.clear()
    wa.bind_artifact_context()


async def test_resolve_prefers_contextvar_then_registry():
    q_ctx: asyncio.Queue = asyncio.Queue()
    q_reg: asyncio.Queue = asyncio.Queue()
    ex._register_run_queue("sess-1", q_reg)

    # ContextVar set → wins.
    tok = ex._active_run_queue.set(q_ctx)
    try:
        assert ex.resolve_run_queue("sess-1") is q_ctx
    finally:
        ex._active_run_queue.reset(tok)

    # ContextVar unset (the Copilot fresh-context case) → registry by key.
    assert ex.resolve_run_queue("sess-1") is q_reg


async def test_resolve_never_guesses_the_single_active_run():
    # H-201 (§21.16): a tool with no key, or another run's key, gets NOTHING.
    # The old "exactly one run is live, so it is this one" fallback sent the
    # event of a frame with no run into that one run's stream.
    q: asyncio.Queue = asyncio.Queue()
    ex._register_run_queue("only-run", q)
    assert ex.resolve_run_queue(None) is None
    assert ex.resolve_run_queue("wrong-key") is None
    assert ex.resolve_run_queue("only-run") is q


async def test_resolve_none_when_no_run():
    assert ex.resolve_run_queue("sess-x") is None


async def test_emit_generative_ui_reaches_registry_queue_copilot_case():
    # Simulate the Copilot path: queue is ONLY in the registry, ContextVar unset.
    q: asyncio.Queue = asyncio.Queue()
    ex._register_run_queue("copilot-sess", q)
    wa.derive_artifact_context(session_id="copilot-sess")

    spec = '{"type":"card","props":{"title":"July 2026 - Monthly Forecast"}}'
    result = await wa.emit_generative_ui(spec)

    assert result == {"ok": True}
    event = q.get_nowait()
    assert event["type"] == "CUSTOM"
    assert event["name"] == "generative_ui"
    assert event["value"]["props"]["title"] == "July 2026 - Monthly Forecast"


async def test_emit_generative_ui_still_errors_with_no_active_run():
    # No registry entry, no ContextVar → the honest error is preserved.
    wa.derive_artifact_context(session_id="ghost")
    result = await wa.emit_generative_ui('{"type":"text","props":{"text":"hi"}}')
    assert result["ok"] is False
    assert "no active run stream" in result["error"]


async def test_emit_generative_ui_rejects_bad_json():
    q: asyncio.Queue = asyncio.Queue()
    ex._register_run_queue("s", q)
    wa.derive_artifact_context(session_id="s")
    result = await wa.emit_generative_ui("{not json")
    assert result["ok"] is False
    assert "valid JSON" in result["error"]
    assert q.empty()  # nothing pushed on a parse failure


async def test_register_unregister_lifecycle():
    q: asyncio.Queue = asyncio.Queue()
    ex._register_run_queue("k", q)
    assert "k" in ex._RUN_QUEUES
    ex._unregister_run_queue("k")
    assert "k" not in ex._RUN_QUEUES
    # Idempotent + falsy-key safe.
    ex._unregister_run_queue("k")
    ex._register_run_queue(None, q)
    assert None not in ex._RUN_QUEUES
