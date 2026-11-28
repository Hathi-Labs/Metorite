"""Spike: does HyperlightExecuteCodeTool run on this Windows box, and how fast?

Run: spikes/maf_sandbox/.venv/Scripts/python.exe spikes/maf_sandbox/hyperlight_probe.py
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path


def _texts(contents) -> str:
    out = []
    for c in contents:
        t = getattr(c, "type", None)
        if t == "text":
            out.append(c.text)
        elif t == "error":
            out.append(f"ERROR: {c.message} :: {getattr(c, 'error_details', '')}")
        elif t == "data":
            out.append(f"FILE {c.additional_properties.get('path')} ({len(c.uri or '')} uri chars)")
        else:
            out.append(f"<{t}>")
    return "\n".join(out)


async def main() -> None:
    from agent_framework_hyperlight import HyperlightExecuteCodeTool

    ws = Path(tempfile.mkdtemp(prefix="hl_ws_"))
    (ws / "data.csv").write_text("region,revenue\nnorth,10\nsouth,20\n", encoding="utf-8")

    t0 = time.perf_counter()
    tool = HyperlightExecuteCodeTool(workspace_root=ws)
    print(f"construct: {time.perf_counter() - t0:.3f}s")

    probes = {
        "trivial": "print(1 + 1)",
        "stdlib": "import json, csv, statistics, re, datetime; print('stdlib ok', statistics.mean([1,2,3]))",
        "read_input": "print(open('/input/data.csv').read())",
        "write_output": "open('/output/out.txt','w').write('hello'); print('wrote')",
        "list_root": "import os; print(os.listdir('/'))",
        "host_escape": "print(open('C:/Windows/win.ini').read()[:50])",
        "etc_passwd": "print(open('/etc/passwd').read()[:50])",
        "subprocess": "import subprocess; print(subprocess.run(['echo','hi'],capture_output=True))",
        "socket": "import socket; s=socket.create_connection(('example.com',80),timeout=3); print('connected')",
        "urllib": "import urllib.request; print(urllib.request.urlopen('http://example.com', timeout=3).status)",
        "pandas": "import pandas; print(pandas.__version__)",
        "pip": "import pip; print('pip present')",
        "state_leak_set": "import builtins; builtins.LEAK = 42; print('set')",
        "state_leak_get": "import builtins; print(getattr(builtins, 'LEAK', 'gone'))",
        "cpu_loop": "s=0\nfor i in range(3_000_000): s+=i\nprint(s)",
    }
    for name, code in probes.items():
        t = time.perf_counter()
        try:
            res = await tool.invoke(arguments={"code": code})
            body = _texts(res if isinstance(res, list) else [res])
        except Exception as exc:  # noqa: BLE001
            body = f"EXC {type(exc).__name__}: {exc}"
        dt = time.perf_counter() - t
        print(f"--- {name}: {dt:.3f}s\n{body[:400]}")

    # Network allowlist probe
    t = time.perf_counter()
    tool2 = HyperlightExecuteCodeTool(allowed_domains=["https://example.com"])
    try:
        res = await tool2.invoke(arguments={"code": "import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=5).status)"})
        body = _texts(res if isinstance(res, list) else [res])
    except Exception as exc:  # noqa: BLE001
        body = f"EXC {type(exc).__name__}: {exc}"
    print(f"--- allowlist example.com (first call incl. build): {time.perf_counter() - t:.3f}s\n{body[:400]}")


if __name__ == "__main__":
    asyncio.run(main())
