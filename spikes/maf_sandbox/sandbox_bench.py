"""Spike: latency and containment probes for the Docker sandbox (no model)."""

from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path

from sandbox import DockerSandbox, cleanup_all, ensure_egress_proxy

SCRATCH = Path(tempfile.gettempdir()) / "maf_spike_bench"


def timed(sbx: DockerSandbox, cmd: str, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        r = sbx.exec(cmd, record=False)
        out.append(time.perf_counter() - t)
        assert r.exit_code == 0, r.for_model()
    return out


def fmt(xs: list[float]) -> str:
    return f"median {statistics.median(xs) * 1000:.0f} ms, min {min(xs) * 1000:.0f}, max {max(xs) * 1000:.0f} (n={len(xs)})"


def probe(sbx: DockerSandbox, label: str, cmd: str, timeout_s: int | None = None) -> None:
    r = sbx.exec(cmd, timeout_s=timeout_s, record=False)
    print(f"--- {label}\n{r.for_model()[:600]}")


def main() -> None:
    cleanup_all()
    SCRATCH.mkdir(exist_ok=True)

    # Cold starts
    colds = []
    inits = []
    for i in range(3):
        s = DockerSandbox(f"maf-spike-ws-cold{i}")
        colds.append(s.start())
        inits.append(s.volume_init_s)
        s.stop()
    print("new volume create + chown:", fmt(inits))
    print("cold start (run -d + first exec):", fmt(colds))
    s = DockerSandbox("maf-spike-ws-cold0")
    print(f"cold start on an existing volume: {s.start():.2f}s")
    s.stop()

    sbx = DockerSandbox("maf-spike-ws-main")
    sbx.start()
    sbx.fs('cat > "$p"', "hello.py", stdin="print('hi from workspace')\n")
    print("warm exec `true`:", fmt(timed(sbx, "true", 10)))
    print("warm exec `python -c pass`:", fmt(timed(sbx, "python -c pass", 10)))
    print("warm exec `python hello.py`:", fmt(timed(sbx, "python hello.py", 5)))

    probe(sbx, "identity", "id; pwd; ls -la /workspace | head -5")
    probe(sbx, "root fs read-only", "touch /usr/x 2>&1; touch /etc/x 2>&1; echo done")
    probe(sbx, "tmp writable, capped", "df -h /tmp | tail -1; head -c 300M /dev/zero > /tmp/big 2>&1; ls -la /tmp/big; rm -f /tmp/big")
    probe(sbx, "host files invisible", "ls /c /mnt /host /run/desktop 2>&1; cat /workspace/../../.env 2>&1; ls /; mount | grep -v -e proc -e cgroup -e sys | head")
    probe(sbx, "network none", "python -c \"import urllib.request;print(urllib.request.urlopen('https://pypi.org',timeout=5).status)\" 2>&1 | tail -1")
    probe(sbx, "dns none", "getent hosts pypi.org || echo 'no dns'")
    probe(sbx, "timeout kill", "echo start; sleep 100; echo never", timeout_s=3)
    probe(sbx, "pids limit (fork bomb, bounded)", "python - <<'EOF'\nimport os\nn=0\ntry:\n    for i in range(2000):\n        if os.fork()==0:\n            import time; time.sleep(5); os._exit(0)\n        n+=1\nexcept OSError as e:\n    print('fork stopped at', n, e)\nEOF", timeout_s=20)
    probe(sbx, "memory limit", "python -c \"x=bytearray(3*1024**3); print('allocated')\" ; echo exit=$?", timeout_s=30)
    probe(sbx, "output cap", "yes 0123456789 | head -c 5000000", timeout_s=20)
    probe(sbx, "capabilities", "grep Cap /proc/self/status")
    probe(sbx, "pip with net none", "pip install --quiet tabulate 2>&1 | tail -2", timeout_s=40)
    sbx.stop()

    # Allow-list mode
    t = ensure_egress_proxy(["pypi.org", "pythonhosted.org"])
    print(f"proxy start: {t:.2f}s")
    sbx2 = DockerSandbox("maf-spike-ws-allow", network="allowlist")
    print(f"allowlist sandbox cold start: {sbx2.start():.2f}s")
    probe(sbx2, "allowlist: pip install tabulate", "pip install --quiet tabulate 2>&1 | tail -3; python -c 'import tabulate;print(tabulate.__version__)'", timeout_s=90)
    probe(sbx2, "allowlist: example.com denied", "python -c \"import urllib.request;print(urllib.request.urlopen('https://example.com',timeout=5).status)\" 2>&1 | tail -1")
    probe(sbx2, "allowlist: direct (no proxy) denied", "env -u HTTPS_PROXY -u https_proxy python -c \"import urllib.request;print(urllib.request.urlopen('https://pypi.org',timeout=5).status)\" 2>&1 | tail -1")
    probe(sbx2, "allowlist: raw socket to 1.1.1.1:443", "python -c \"import socket;socket.create_connection(('1.1.1.1',443),timeout=5);print('connected')\" 2>&1 | tail -1")
    sbx2.stop()
    import subprocess

    print("--- proxy log\n" + subprocess.run(["docker", "logs", "maf-spike-egress-proxy"], capture_output=True, text=True).stdout)
    print("cleanup:", cleanup_all())


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup_all()
