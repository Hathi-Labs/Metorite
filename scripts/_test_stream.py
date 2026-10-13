"""Test the Tier 1.5 streaming path — shows every tool call with args and result."""
import asyncio
import json
import subprocess

import httpx


async def test() -> None:
    token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    prompt = (
        "Write count.ps1 containing: 1..5 | ForEach-Object { Write-Host $_ }. "
        "Save the file then run it. Show the output."
    )
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            "http://localhost:8000/agent/run/stream",
            json={"agent": "crm-assistant", "payload": {"message": prompt}},
            headers={"Authorization": f"Bearer {token}"},
        )
        print("HTTP", resp.status_code)
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            t = ev.get("type", "")
            if t == "TOOL_CALL_START":
                print(f"\nTOOL_START  name={ev.get('toolCallName')}  id={ev.get('toolCallId', '')[:24]}")
            elif t == "TOOL_CALL_ARGS":
                print(f"  ARGS: {ev.get('delta', '')[:200]}")
            elif t == "TOOL_CALL_RESULT":
                print(f"  RESULT: {str(ev.get('content', ''))[:120]}")
            elif t == "TEXT_MESSAGE_CONTENT":
                print(ev.get("delta", ""), end="", flush=True)
            elif t in ("RUN_FINISHED", "RUN_ERROR"):
                print(f"\n--- {t} ---")


asyncio.run(test())
