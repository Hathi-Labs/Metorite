"""Spike T4, deterministic half: hostile paths straight at the tools, no model.

The model can refuse or get creative; this proves what the tools themselves do.
"""

import asyncio
import os

from agent_run import ContainerFileStore
from keys import load_keys
from sandbox import DockerSandbox, cleanup_all

HOSTILE = [
    "C:/Users/VijayRaghavVarada/Documents/Github/CommandCenter/.env",
    "/etc/passwd",
    "../../.env",
    "a/../../etc/passwd",
    "\\\\?\\C:\\Windows\\win.ini",
    "link_to_passwd",  # symlink inside the workspace -> /etc/passwd
    "link_dir/passwd",  # directory symlink -> /etc
]


async def main() -> None:
    load_keys(verbose=False)
    sbx = DockerSandbox("maf-spike-ws-t4direct")
    sbx.start()
    store = ContainerFileStore({"sbx": sbx})
    # Agent-planted symlinks, as a hostile script would do:
    print(sbx.exec("ln -s /etc/passwd link_to_passwd; ln -s /etc link_dir; ls -la").for_model())
    for path in HOSTILE:
        try:
            out = await store.read_file(path)
            verdict = f"READ {len(out or '')} chars" if out is not None else "None (not found)"
        except Exception as exc:  # noqa: BLE001
            verdict = f"REFUSED {type(exc).__name__}: {exc}"
        print(f"read_file({path!r}) -> {verdict}")
    for path in ["../escape.txt", "/tmp/x.txt", "link_dir/evil"]:
        try:
            await store.write_file(path, "x")
            verdict = "WROTE"
        except Exception as exc:  # noqa: BLE001
            verdict = f"REFUSED {type(exc).__name__}: {exc}"
        print(f"write_file({path!r}) -> {verdict}")
    # What a shell command can see of the host
    print(sbx.exec("cat /etc/passwd | head -2; env | cut -d= -f1 | sort | tr '\\n' ' '").for_model())
    key = os.environ.get("GEMINI_API_KEY", "")
    envdump = sbx.exec("env").output
    print("host key visible in sandbox env:", bool(key) and key in envdump)
    sbx.stop()
    cleanup_all()


asyncio.run(main())
