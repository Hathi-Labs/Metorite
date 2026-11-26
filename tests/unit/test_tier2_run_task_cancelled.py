"""Task 851: the Tier-2 run task stops when its reader goes away.

``executor.py`` starts the Tier-2 run as ``run_task = asyncio.create_task(...)``
and drains its queue in a generator. A generator that closes (the member left,
or the request was cancelled) raised ``GeneratorExit`` at the ``yield`` and left
the task running. The drain loop now cancels it on any exit that is not a
normal end.

The generator is too large to drive in a unit test, so the first test reads the
source (R7: it fails if the cancel goes away). The second test shows the
asyncio behaviour on a small copy of the same shape.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "apps/services/orchestrator/orchestrator/executor.py"


def test_the_drain_loop_cancels_the_run_task_on_any_abnormal_exit() -> None:
    text = SRC.read_text(encoding="utf-8")
    start = text.index("run_task = asyncio.create_task(_run_task())")
    block = text[start:start + 3500]
    assert "except BaseException:" in block
    tail = block[block.index("except BaseException:"):]
    assert "if not run_task.done():" in tail[:200]
    assert "run_task.cancel()" in tail[:300]
    assert "raise" in tail[:400]


def test_the_same_shape_cancels_the_task_when_the_generator_closes() -> None:
    state = {"cancelled": False}

    async def work() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def gen():
        run_task = asyncio.create_task(work())
        queue: asyncio.Queue = asyncio.Queue()
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.05)
                    yield ev
                except asyncio.TimeoutError:
                    yield "tick"
        except BaseException:
            if not run_task.done():
                run_task.cancel()
            raise

    async def main() -> None:
        g = gen()
        assert await g.__anext__() == "tick"
        await g.aclose()
        await asyncio.sleep(0.05)

    asyncio.run(main())
    assert state["cancelled"] is True
