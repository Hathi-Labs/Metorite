"""Spike: a per-run Docker sandbox for agent shell commands.

One warm container per run: `docker run -d` once, then `docker exec` per
command. Only the run's workspace is mounted (at /workspace). Root FS is
read-only apart from /workspace and a size-capped /tmp tmpfs. Non-root,
all capabilities dropped, no-new-privileges, CPU/memory/pids limits, a
wall-clock timeout per command, and output capped inside the container.

Workspace storage. The plan was a host bind mount of the run folder. On this
box Docker Desktop refuses every host bind mount ("mkdir /run/desktop/mnt/
host/c: file exists", a known Docker Desktop state that only a restart
clears, and a restart would kill other sessions' containers). So the
workspace is a named Docker volume, and the agent's file tools go THROUGH
the container (`ContainerFileStore`). That is the stronger design anyway:
the host never resolves an agent-supplied path, and it works unchanged
against a remote Docker host.

Network modes:
  none      -- `--network none` (default). No interface but loopback.
  allowlist -- the sandbox joins an `--internal` Docker network that has no
               route out. A proxy container on that network (and on the
               default bridge) forwards CONNECT/HTTP only to allow-listed
               domains. HTTP(S)_PROXY points at it.
  open      -- default bridge network. Full egress. For comparison only.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

LABEL = "maf-spike=1"
INTERNAL_NET = "maf-spike-internal"
PROXY_NAME = "maf-spike-egress-proxy"
PROXY_PORT = 3128
HERE = Path(__file__).resolve().parent


def _docker(
    *args: str, timeout: float = 120, check: bool = True, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        ["docker", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(f"docker {args[0]} failed ({cp.returncode}): {cp.stderr.strip()[:600]}")
    return cp


def ensure_egress_proxy(allow_domains: list[str]) -> float:
    """Start (or restart) the allow-list proxy. Returns seconds taken."""
    t0 = time.perf_counter()
    nets = _docker("network", "ls", "--format", "{{.Name}}").stdout.split()
    if INTERNAL_NET not in nets:
        _docker("network", "create", "--internal", "--label", LABEL, INTERNAL_NET)
    _docker("rm", "-f", PROXY_NAME, check=False)
    source = (HERE / "egress_proxy.py").read_text(encoding="utf-8")
    _docker(
        "run", "-d", "--name", PROXY_NAME, "--label", LABEL,
        "--network", INTERNAL_NET,
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "65534:65534", "--memory", "128m", "--pids-limit", "64",
        "-e", f"ALLOW_DOMAINS={','.join(allow_domains)}",
        "python:3.12-slim", "python", "-u", "-c", source,
    )
    # Second leg: the default bridge, so the proxy (only) can reach out.
    _docker("network", "connect", "bridge", PROXY_NAME)
    return time.perf_counter() - t0


@dataclass
class ExecResult:
    exit_code: int
    output: str
    duration_s: float
    timed_out: bool
    truncated: bool
    total_bytes: int

    def for_model(self) -> str:
        head = f"[exit_code={self.exit_code}"
        if self.timed_out:
            head += " TIMED OUT (killed)"
        if self.truncated:
            head += f" output truncated, {self.total_bytes} bytes total"
        head += f" {self.duration_s:.2f}s]"
        return f"{head}\n{self.output}"


@dataclass
class DockerSandbox:
    volume: str  # named volume that holds /workspace (persists across sessions)
    image: str = "python:3.12-slim"
    network: str = "none"  # none | allowlist | open
    cpus: str = "1"
    memory: str = "1g"
    pids: int = 256
    tmp_size: str = "256m"
    default_timeout_s: int = 60
    max_output_bytes: int = 12_000
    name: str = field(default_factory=lambda: f"maf-spike-run-{uuid.uuid4().hex[:8]}")
    exec_log: list[ExecResult] = field(default_factory=list)
    cold_start_s: float | None = None
    volume_init_s: float = 0.0

    def ensure_volume(self) -> None:
        if _docker("volume", "inspect", self.volume, check=False).returncode == 0:
            return
        t0 = time.perf_counter()
        _docker("volume", "create", "--label", LABEL, self.volume)
        # A fresh volume's root is root:root 0755. Hand it to the sandbox uid.
        _docker(
            "run", "--rm", "--label", LABEL, "--network", "none",
            "-v", f"{self.volume}:/workspace", "alpine:3", "chown", "1000:1000", "/workspace",
        )
        self.volume_init_s = time.perf_counter() - t0

    def start(self) -> float:
        self.ensure_volume()
        env_args: list[str] = []
        if self.network == "none":
            net_args = ["--network", "none"]
        elif self.network == "allowlist":
            net_args = ["--network", INTERNAL_NET]
            proxy = f"http://{PROXY_NAME}:{PROXY_PORT}"
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env_args += ["-e", f"{k}={proxy}"]
        elif self.network == "open":
            net_args = ["--network", "bridge"]
        else:
            raise ValueError(self.network)
        t0 = time.perf_counter()
        _docker(
            "run", "-d", "--init", "--name", self.name, "--label", LABEL,
            *net_args,
            "--read-only",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={self.tmp_size}",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "1000:1000",
            "--cpus", self.cpus, "--memory", self.memory, "--memory-swap", self.memory,
            "--pids-limit", str(self.pids),
            "-e", "HOME=/tmp",
            "-e", "PYTHONUSERBASE=/workspace/.local",
            "-e", "PIP_USER=1",
            "-e", "PIP_CACHE_DIR=/tmp/pip-cache",
            "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1",
            "-e", "PIP_ROOT_USER_ACTION=ignore",
            "-e", "PATH=/workspace/.local/bin:/usr/local/bin:/usr/bin:/bin",
            *env_args,
            # volume-nocopy: otherwise Docker copies the image's (root-owned)
            # /workspace ownership onto an empty volume and undoes the chown.
            "--mount", f"type=volume,src={self.volume},dst=/workspace,volume-nocopy",
            "-w", "/workspace",
            self.image, "sleep", "infinity",
        )
        # First exec proves the container is actually ready.
        self.exec("true", timeout_s=30, record=False)
        self.cold_start_s = time.perf_counter() - t0
        return self.cold_start_s

    def exec(self, command: str, timeout_s: int | None = None, record: bool = True) -> ExecResult:
        timeout_s = int(timeout_s or self.default_timeout_s)
        tag = uuid.uuid4().hex[:8]
        out = f"/tmp/.o-{tag}"
        half = self.max_output_bytes // 2
        # The command arrives as $1, so no host-side quoting of agent text.
        # Output goes to the size-capped tmpfs, which also caps a runaway writer.
        wrapper = (
            f'timeout -s KILL {timeout_s} sh -c "$1" > {out} 2>&1; ec=$?; '
            f"sz=$(wc -c < {out}); "
            f'if [ "$sz" -le {self.max_output_bytes} ]; then cat {out}; '
            f"else head -c {half} {out}; printf '\\n...[truncated]...\\n'; tail -c {half} {out}; fi; "
            f'rm -f {out}; printf "\\n__SBX__ %s %s\\n" "$ec" "$sz"'
        )
        t0 = time.perf_counter()
        try:
            cp = _docker("exec", self.name, "sh", "-c", wrapper, "_", command, timeout=timeout_s + 15, check=False)
            raw = cp.stdout + (cp.stderr or "")
        except subprocess.TimeoutExpired:
            raw = "host-side timeout\n__SBX__ 137 0\n"
        dt = time.perf_counter() - t0
        body, _, trailer = raw.rpartition("__SBX__ ")
        try:
            ec_s, sz_s = trailer.split()[:2]
            ec, sz = int(ec_s), int(sz_s)
        except ValueError:
            ec, sz, body = -1, len(raw), raw
        res = ExecResult(
            exit_code=ec,
            output=body.rstrip("\n"),
            duration_s=dt,
            timed_out=ec in (137, 124) and dt >= timeout_s - 0.5,
            truncated=sz > self.max_output_bytes,
            total_bytes=sz,
        )
        if record:
            self.exec_log.append(res)
        return res

    # --- raw file primitives used by ContainerFileStore (run as uid 1000) ---
    _GUARD = (
        'p=$(realpath -m -- "/workspace/$1"); '
        'case "$p" in /workspace|/workspace/*) ;; *) echo "path escapes /workspace" >&2; exit 99;; esac; '
    )

    def fs(self, script: str, rel: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return _docker(
            "exec", "-i", self.name, "sh", "-c", self._GUARD + script, "_", rel,
            stdin=stdin, check=False, timeout=60,
        )

    def snapshot_to(self, host_dir: Path) -> None:
        """Copy /workspace out to the host (for inspecting results)."""
        host_dir.mkdir(parents=True, exist_ok=True)
        _docker("cp", f"{self.name}:/workspace/.", str(host_dir), check=False, timeout=120)

    def stop(self) -> None:
        _docker("rm", "-f", self.name, check=False)


def cleanup_all() -> list[str]:
    """Remove every container, volume and network this spike created (by label)."""
    ids = _docker("ps", "-aq", "--filter", f"label={LABEL}").stdout.split()
    if ids:
        _docker("rm", "-f", *ids, check=False)
    vols = _docker("volume", "ls", "-q", "--filter", f"label={LABEL}").stdout.split()
    if vols:
        _docker("volume", "rm", "-f", *vols, check=False)
    nets = _docker("network", "ls", "-q", "--filter", f"label={LABEL}").stdout.split()
    if nets:
        _docker("network", "rm", *nets, check=False)
    return ids + vols + nets


if __name__ == "__main__":
    print("cleanup:", cleanup_all())
