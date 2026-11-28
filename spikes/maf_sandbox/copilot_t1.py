"""Spike: T1 through the GitHub Copilot SDK path, timed per phase.

Mirrors orchestrator/code_session.py: a fresh CopilotClient (bundled CLI,
stdio), one session with a BYOK provider and a working_directory, one
send_and_wait. The model is the same Gemini alias the MAF runs used, so the
difference is the framework, not the model. Runs UNSANDBOXED on the host
(the production default when copilot_sandbox_scope is unset).

Run with the MAIN repo venv (it has github_copilot_sdk):
  ../../../../.venv/Scripts/python.exe copilot_t1.py [model]
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keys import load_keys  # noqa: E402

from copilot import CopilotClient  # noqa: E402
from copilot.types import PermissionRequestResult  # noqa: E402

T1 = (
    "Here is sales.csv in the workspace. Write a Python script that summarises revenue by "
    "region and month, run it, and save the result as a markdown table to outputs/summary.md. "
    "Show me the table when done."
)


def make_csv(seed: int, rows: int, regions: list[str], months: list[str]) -> str:
    rnd = random.Random(seed)
    products = ["widget", "gadget", "gizmo", "doohickey"]
    lines = ["date,region,product,units,unit_price,revenue"]
    for _ in range(rows):
        m = rnd.choice(months)
        d = f"{m}-{rnd.randint(1, 28):02d}"
        units = rnd.randint(1, 40)
        price = rnd.choice([9.99, 14.5, 25.0, 49.0])
        lines.append(f"{d},{rnd.choice(regions)},{rnd.choice(products)},{units},{price},{units * price:.2f}")
    return "\n".join(lines) + "\n"


async def main() -> None:
    load_keys(verbose=False)
    model = sys.argv[1] if len(sys.argv) > 1 else "gemini-flash-latest"
    byok = not model.startswith("copilot:")
    ws = Path(tempfile.mkdtemp(prefix="copilot_t1_"))
    (ws / "sales.csv").write_text(make_csv(1, 50, ["north", "south", "east", "west"],
                                           ["2026-01", "2026-02", "2026-03"]), encoding="utf-8")
    counts: dict[str, int] = {}
    usage = {"in": 0, "out": 0}
    tool_names: list[str] = []
    marks: dict[str, float] = {}

    def on_event(ev) -> None:
        t = ev.type.value if hasattr(ev.type, "value") else str(ev.type)
        counts[t] = counts.get(t, 0) + 1
        if t == "assistant.usage":
            d = ev.data
            usage["in"] += int(getattr(d, "input_tokens", 0) or 0)
            usage["out"] += int(getattr(d, "output_tokens", 0) or 0)
        if t == "tool.execution_start":
            tool_names.append(str(getattr(ev.data, "tool_name", "?")))
        if t == "assistant.turn_start" and "first_turn" not in marks:
            marks["first_turn"] = time.perf_counter()

    def allow_all(request, invocation) -> PermissionRequestResult:
        return PermissionRequestResult(kind="approved")

    t0 = time.perf_counter()
    client = CopilotClient({"log_level": "error", "cwd": str(ws)})
    await client.start()
    t_start = time.perf_counter()
    cfg = {
        "working_directory": str(ws),
        "on_permission_request": allow_all,
        "system_message": {"mode": "append", "content": "Work only inside the working directory."},
    }
    if byok:
        cfg["model"] = model
        cfg["provider"] = {
            "type": "openai",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key": os.environ["GEMINI_API_KEY"],
        }
    else:
        cfg["model"] = model.split(":", 1)[1]
    session = await client.create_session(cfg)
    t_session = time.perf_counter()
    session.on(on_event)
    err = None
    try:
        final = await session.send_and_wait({"prompt": T1}, timeout=600)
        text = getattr(getattr(final, "data", None), "content", "") if final else ""
    except Exception as exc:  # noqa: BLE001
        text, err = "", f"{type(exc).__name__}: {str(exc)[:400]}"
    t_done = time.perf_counter()
    try:
        await session.destroy()
    except Exception:  # noqa: BLE001
        pass
    await client.stop()
    t_stop = time.perf_counter()
    out = ws / "outputs" / "summary.md"
    summary = {
        "task": f"t1-copilot-{model.replace(':', '-')}",
        "model": model,
        "byok": byok,
        "client_start_s": round(t_start - t0, 2),
        "create_session_s": round(t_session - t_start, 2),
        "first_turn_after_send_s": round(marks.get("first_turn", t_done) - t_session, 2),
        "send_and_wait_s": round(t_done - t_session, 1),
        "stop_s": round(t_stop - t_done, 2),
        "wall_s": round(t_stop - t0, 1),
        "turns": counts.get("assistant.turn_start", 0),
        "tool_calls": counts.get("tool.execution_start", 0),
        "tools": {n: tool_names.count(n) for n in sorted(set(tool_names))},
        "input_tokens": usage["in"],
        "output_tokens": usage["out"],
        "summary_md_exists": out.is_file(),
        "error": err,
        "final_text": (text or "")[:1200],
    }
    Path(__file__).resolve().parent.joinpath("runs").mkdir(exist_ok=True)
    Path(__file__).resolve().parent.joinpath("runs", f"{summary['task']}.summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "final_text"}))
    print("FINAL:", summary["final_text"][:800])
    print("events:", json.dumps(counts))
    shutil.rmtree(ws, ignore_errors=True)


asyncio.run(main())
