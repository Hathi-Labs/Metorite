"""Spike: the Copilot SDK fixed cost per code_task call, with no model call.

client.start() spawns the bundled CLI and handshakes; create_session sends the
session config; stop() tears the CLI down. code_session.py pays all three on
EVERY call, because each call builds a fresh MetoriteCopilotAgent.
"""

import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot import CopilotClient  # noqa: E402
from copilot.types import PermissionRequestResult  # noqa: E402


async def once() -> tuple[float, float, float]:
    ws = tempfile.mkdtemp(prefix="copilot_oh_")
    t0 = time.perf_counter()
    client = CopilotClient({"log_level": "error", "cwd": ws})
    await client.start()
    t1 = time.perf_counter()
    s = await client.create_session(
        {
            "working_directory": ws,
            "model": "gemini-flash-latest",
            "provider": {"type": "openai", "base_url": "http://127.0.0.1:9/v1", "api_key": "x"},
            "on_permission_request": lambda r, i: PermissionRequestResult(kind="approved"),
        }
    )
    t2 = time.perf_counter()
    await s.disconnect()
    await client.stop()
    t3 = time.perf_counter()
    return t1 - t0, t2 - t1, t3 - t2


async def main() -> None:
    rows = [await once() for _ in range(4)]
    for name, i in (("client.start (CLI spawn+handshake)", 0), ("create_session", 1), ("disconnect+stop", 2)):
        xs = [r[i] for r in rows]
        print(f"{name}: median {statistics.median(xs):.2f}s  all={[round(x, 2) for x in xs]}")


asyncio.run(main())
