"""Spike: the containment probes that the fork-bomb probe starved in run 1."""

import time

from sandbox import DockerSandbox, cleanup_all
from sandbox_bench import probe

s = DockerSandbox("maf-spike-ws-b2")
s.start()
probe(s, "memory limit", "python -c \"x=bytearray(3*1024**3); print('allocated')\" ; echo exit=$?", timeout_s=30)
probe(s, "output cap", "yes 0123456789 | head -c 5000000", timeout_s=20)
probe(s, "capabilities", "grep -e Cap -e NoNewPrivs /proc/self/status")
probe(s, "pip with net none", "pip install --quiet tabulate 2>&1 | tail -2", timeout_s=40)
probe(
    s,
    "fork bomb leaves orphans",
    "python -c \"import os,time\nfor i in range(300):\n  try:\n    pid=os.fork()\n  except OSError as e:\n"
    "    print('stopped',i,e); break\n  if pid==0: time.sleep(8); os._exit(0)\"",
    timeout_s=20,
)
probe(s, "next exec right after", "echo alive")
time.sleep(9)
probe(s, "next exec 9s later", "echo alive")
s.stop()
cleanup_all()
