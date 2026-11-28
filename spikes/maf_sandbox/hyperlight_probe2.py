"""Spike: what stdlib and network surface does the Hyperlight Python guest have?

Also measures the rebuild cost when the workspace changes (the cache key
includes a signature of the workspace tree).
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

GUEST_SURVEY = r"""
import sys, os
print('version', sys.version.replace('\n',' '))
print('platform', sys.platform)
print('path', sys.path)
mods = []
for p in sys.path:
    try:
        for n in os.listdir(p):
            mods.append(n)
    except Exception as e:
        pass
print('n_entries', len(mods))
print('builtin', sorted(sys.builtin_module_names))
want = ['csv','json','re','datetime','collections','statistics','math','sqlite3','zipfile','http','urllib','ssl','socket','subprocess','threading','decimal','io','pathlib','tempfile','shutil','glob','textwrap','string','itertools','functools','typing','dataclasses']
ok=[]; bad=[]
for w in want:
    try:
        __import__(w); ok.append(w)
    except Exception as e:
        bad.append(w + ':' + type(e).__name__)
print('ok', ok)
print('missing', bad)
print('globals', [k for k in dir(__builtins__) if not k.startswith('_')][-15:])
print('has call_tool', 'call_tool' in dir(__builtins__) or 'call_tool' in globals())
"""


def _texts(contents) -> str:
    out = []
    for c in contents:
        t = getattr(c, "type", None)
        if t == "text":
            out.append(c.text)
        elif t == "error":
            out.append(f"ERROR: {c.message} :: {getattr(c, 'error_details', '')}")
        else:
            out.append(f"<{t}>")
    return "\n".join(out)


async def main() -> None:
    from agent_framework_hyperlight import HyperlightExecuteCodeTool

    ws = Path(tempfile.mkdtemp(prefix="hl_ws2_"))
    (ws / "a.txt").write_text("a", encoding="utf-8")
    tool = HyperlightExecuteCodeTool(workspace_root=ws, allowed_domains=["https://example.com"])

    t = time.perf_counter()
    res = await tool.invoke(arguments={"code": GUEST_SURVEY})
    print(f"--- survey (cold): {time.perf_counter() - t:.3f}s\n{_texts(res)}")

    t = time.perf_counter()
    res = await tool.invoke(arguments={"code": "print('warm')"})
    print(f"--- warm: {time.perf_counter() - t:.3f}s {_texts(res)}")

    # Change the workspace -> new signature -> new sandbox build?
    (ws / "b.txt").write_text("b", encoding="utf-8")
    t = time.perf_counter()
    res = await tool.invoke(arguments={"code": "import os; print(os.listdir('/input'))"})
    print(f"--- after workspace change: {time.perf_counter() - t:.3f}s {_texts(res)}")

    # Does /output persist into /input for the next call? (No: output is a temp dir.)
    res = await tool.invoke(arguments={"code": "open('/output/x.txt','w').write('x'); print('ok')"})
    res = await tool.invoke(arguments={"code": "import os; print(os.listdir('/input'), os.listdir('/output'))"})
    print(f"--- output persistence: {_texts(res)}")

    # Write into /input?
    res = await tool.invoke(arguments={"code": "open('/input/new.txt','w').write('n'); print('wrote input')"})
    print(f"--- write /input: {_texts(res)} ; host sees new.txt: {(ws / 'new.txt').exists()}")

    # Infinite loop: is there a timeout?
    t = time.perf_counter()
    try:
        res = await asyncio.wait_for(tool.invoke(arguments={"code": "while True: pass"}), timeout=20)
        print(f"--- infinite loop returned: {_texts(res)}")
    except asyncio.TimeoutError:
        print(f"--- infinite loop: no internal timeout, host wait_for fired after {time.perf_counter() - t:.1f}s")
        # The worker thread is still spinning inside the guest. close() would
        # block on it forever, so hard-exit.
        import os

        os._exit(0)

    tool._registry.close()  # noqa: SLF001 - private; there is no public close()


if __name__ == "__main__":
    asyncio.run(main())
