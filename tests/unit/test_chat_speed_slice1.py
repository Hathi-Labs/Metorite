"""Chat speed slice 1 fences: bounded Mem0 fan-out and a clean cancel."""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


async def test_mem0_searches_never_exceed_the_slot_count() -> None:
    from gateway import memory_preread

    running = 0
    peak = 0

    async def _search() -> str:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return "x"

    await asyncio.gather(*(memory_preread._limited(_search()) for _ in range(12)))
    assert peak <= memory_preread._MEM0_SLOTS


async def test_a_cancelled_load_releases_the_loaded_agent(monkeypatch) -> None:
    from orchestrator import executor

    started = threading.Event()
    proceed = threading.Event()
    cleaned: list[bool] = []

    class _Loaded:
        def cleanup(self) -> None:
            cleaned.append(True)

    def _slow_load(name: str, **_kw: Any) -> _Loaded:
        started.set()
        proceed.wait(5)
        return _Loaded()

    monkeypatch.setattr(executor, "load_agent", _slow_load)
    task = asyncio.ensure_future(executor._load_agent_off_loop("a"))
    while not started.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    proceed.set()
    for _ in range(200):
        if cleaned:
            break
        await asyncio.sleep(0.01)
    assert cleaned == [True]
