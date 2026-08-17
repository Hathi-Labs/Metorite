"""Copilot SDK session sandbox — container lifecycle (BO-7 phase 2).

Containerizes ONLY the `copilot` CLI binary (Dockerfile.copilot-sandbox) as a
TCP JSON-RPC server, using the SDK's own ``cli_url`` transport
(``copilot/client.py``'s ``_connect_via_tcp``). All host-side orchestration —
``MetoriteCopilotAgent``, event streaming, permission handling — is
unchanged; a caller just points ``CopilotClient`` at this container's socket
instead of a local subprocess. See ``code_session.py`` / ``executor.py`` for
the two wired call sites (``code_task``, the App Workshop app-builder).

Hardening mirrors ``mutation.py``'s ``_run_mutation_sandbox`` (the platform's
existing precedent): ``--cap-drop ALL --cap-add DAC_OVERRIDE`` (the
container's default root user must write the host ``acb``-user-owned bind
mount — see ``deploy/hostinger/acb-gateway.service``'s ``User=acb``),
``--security-opt no-new-privileges``, and configurable memory/cpu/pids
ceilings. Published only to loopback, on an ephemeral host port.

Two lifecycle shapes:
- Per-call spawn+stop (``code_task``) — ``spawn_copilot_sandbox()`` /
  ``stop_copilot_sandbox()`` wrap one session.
- Sticky per-thread reuse (interactive app-builder chat, where a fresh
  container on every turn would pay full spawn latency every message) —
  ``get_or_spawn_sticky()`` keyed by ``thread_id``, idle-reaped opportunistically
  on later calls (``copilot_sandbox_idle_ttl_seconds``). In-memory registry:
  same single-process assumption as the executor's existing
  ``_pending_user_input`` — a multi-worker-process gateway would need this in
  Redis instead.

Never raises: every public function degrades to ``None``/no-op plus a logged
warning on ANY failure (docker missing, image missing, port never comes up,
timeout) so callers always have a clean fallback to the existing in-process
path — a broken sandbox must never break a call.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acb_common import get_logger

_log = get_logger("orchestrator.copilot_sandbox")

_CONTAINER_PREFIX = "cc-copilot-"
# Fixed in-container mount point — public: callers need it to set both
# `working_directory` in the session config AND `permission_check_root` in
# _WRITE_ARTIFACT_CONTEXT so the containment check compares against the path
# the sandboxed CLI actually reports (see permission_policy._workspace_root).
CONTAINER_WORKSPACE = "/workspace/repo"
_CONTAINER_STATE_DIR = "/root/.copilot"
_PORT_POLL_INTERVAL_SECONDS = 0.2


@dataclass
class CopilotSandboxHandle:
    """A running sandbox container, ready to hand to ``CopilotClient({"cli_url": ...})``."""

    name: str
    cli_url: str
    workspace: str
    label: str
    thread_id: str | None = None


@dataclass
class _StickyEntry:
    handle: CopilotSandboxHandle
    last_used: float


# thread_id -> sticky entry. Single-process, in-memory (see module docstring).
_STICKY: dict[str, _StickyEntry] = {}
_STICKY_LOCK = asyncio.Lock()


def _state_dir(settings: Any) -> str:
    configured = str(getattr(settings, "copilot_sandbox_state_dir", "") or "").strip()
    if configured:
        Path(configured).mkdir(parents=True, exist_ok=True)
        return configured
    clone_dir = str(getattr(settings, "agents_clone_dir", "") or "/tmp/acb_agents")
    d = Path(clone_dir) / ".copilot-sandbox-state"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


async def _run(*args: str, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a subprocess to completion; never raises. Returns (rc, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            out.decode(errors="replace").strip(),
            err.decode(errors="replace").strip(),
        )
    except Exception as exc:
        return -1, "", str(exc)


async def _wait_tcp_ready(host: str, port: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0,
            )
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return True
        except Exception:
            await asyncio.sleep(_PORT_POLL_INTERVAL_SECONDS)
    return False


async def spawn_copilot_sandbox(
    *,
    workspace: str,
    label: str,
    settings: Any,
    extra_mounts: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
    thread_id: str | None = None,
) -> CopilotSandboxHandle | None:
    """Spawn a hardened ``copilot`` CLI sandbox container bound to *workspace*.

    ``extra_env`` is for non-secret, call-site-specific config only (e.g. the
    App Workshop app-builder's T2 vendor-cache path) — never credentials; BYOK
    travels in the ``create_session`` RPC payload the host sends after
    connecting, not container env (see Dockerfile.copilot-sandbox).

    Returns ``None`` (and logs a warning) on any failure — Docker unavailable,
    image missing, the container never opens its port in time, etc. Callers
    must treat ``None`` as "fall back to the existing in-process path", never
    as a hard error.
    """
    image = str(getattr(settings, "copilot_sandbox_image", "acb-copilot-sandbox:latest"))
    container_port = int(getattr(settings, "copilot_sandbox_port", 41041))
    mem_limit = str(getattr(settings, "copilot_sandbox_memory_limit", "768m"))
    cpu_limit = str(getattr(settings, "copilot_sandbox_cpu_limit", "1"))
    pids_limit = int(getattr(settings, "copilot_sandbox_pids_limit", 256))
    ready_timeout = float(getattr(settings, "copilot_sandbox_ready_timeout_seconds", 8.0))

    name = f"{_CONTAINER_PREFIX}{label}-{uuid.uuid4().hex[:12]}"

    docker_cmd = [
        "docker", "run",
        "-d", "--rm",
        "--name", name,
        "--cap-drop", "ALL",
        "--cap-add", "DAC_OVERRIDE",
        "--security-opt", "no-new-privileges",
        "--memory", mem_limit,
        "--cpus", cpu_limit,
        "--pids-limit", str(pids_limit),
        "-p", f"127.0.0.1::{container_port}",
        "-v", f"{workspace}:{CONTAINER_WORKSPACE}",
        "-v", f"{_state_dir(settings)}:{_CONTAINER_STATE_DIR}",
    ]
    for host_path, container_path in (extra_mounts or {}).items():
        docker_cmd += ["-v", f"{host_path}:{container_path}:ro"]
    for key, value in (extra_env or {}).items():
        docker_cmd += ["-e", f"{key}={value}"]
    docker_cmd += [image, "--port", str(container_port), "--log-level", "info"]

    rc, _out, err = await _run(*docker_cmd, timeout=30.0)
    if rc != 0:
        _log.warning(
            "copilot_sandbox.spawn_failed", label=label, error=err[-500:],
        )
        return None

    # Resolve the host-assigned ephemeral port.
    rc, out, err = await _run(
        "docker", "port", name, f"{container_port}/tcp", timeout=10.0,
    )
    if rc != 0 or not out.strip():
        _log.warning("copilot_sandbox.port_resolve_failed", label=label, error=err[-500:])
        await _docker_rm(name)
        return None
    host_port_line = out.strip().splitlines()[-1]
    try:
        host_port = int(host_port_line.rsplit(":", 1)[-1])
    except ValueError:
        _log.warning("copilot_sandbox.port_parse_failed", label=label, raw=host_port_line)
        await _docker_rm(name)
        return None

    if not await _wait_tcp_ready("127.0.0.1", host_port, timeout=ready_timeout):
        _log.warning("copilot_sandbox.not_ready", label=label, timeout=ready_timeout)
        await _docker_rm(name)
        return None

    _log.info("copilot_sandbox.spawned", label=label, name=name, host_port=host_port)
    return CopilotSandboxHandle(
        name=name,
        cli_url=f"127.0.0.1:{host_port}",
        workspace=workspace,
        label=label,
        thread_id=thread_id,
    )


async def _docker_rm(name: str) -> None:
    await _run("docker", "rm", "-f", name, timeout=15.0)


async def stop_copilot_sandbox(handle: CopilotSandboxHandle | None) -> None:
    """Best-effort stop of a sandbox container. Never raises."""
    if handle is None:
        return
    rc, _out, err = await _run("docker", "stop", "-t", "5", handle.name, timeout=15.0)
    if rc != 0:
        _log.warning("copilot_sandbox.stop_failed_killing", name=handle.name, error=err[-300:])
        await _docker_rm(handle.name)


async def get_or_spawn_sticky(
    *,
    thread_id: str,
    workspace: str,
    label: str,
    settings: Any,
    extra_mounts: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> CopilotSandboxHandle | None:
    """Reuse a live sandbox for *thread_id*, or spawn a fresh one.

    Opportunistically reaps other idle entries on every call (see module
    docstring — no background task, bounded staleness by call cadence).
    """
    idle_ttl = float(getattr(settings, "copilot_sandbox_idle_ttl_seconds", 600))
    now = time.monotonic()

    async with _STICKY_LOCK:
        entry = _STICKY.get(thread_id)
        if entry is not None:
            entry.last_used = now
            return entry.handle

        stale = [
            tid for tid, e in _STICKY.items()
            if tid != thread_id and (now - e.last_used) > idle_ttl
        ]

    for tid in stale:
        async with _STICKY_LOCK:
            e = _STICKY.pop(tid, None)
        if e is not None:
            _log.info("copilot_sandbox.idle_reaped", thread_id=tid, name=e.handle.name)
            await stop_copilot_sandbox(e.handle)

    handle = await spawn_copilot_sandbox(
        workspace=workspace, label=label, settings=settings,
        extra_mounts=extra_mounts, extra_env=extra_env, thread_id=thread_id,
    )
    if handle is None:
        return None

    async with _STICKY_LOCK:
        _STICKY[thread_id] = _StickyEntry(handle=handle, last_used=now)
    return handle


async def sweep_orphaned_sandboxes() -> None:
    """Remove any leftover ``cc-copilot-*`` containers from a prior crashed
    gateway process. Best-effort; called once at gateway startup."""
    rc, out, _err = await _run(
        "docker", "ps", "-aq", "--filter", f"name={_CONTAINER_PREFIX}", timeout=15.0,
    )
    if rc != 0 or not out.strip():
        return
    ids = [line.strip() for line in out.splitlines() if line.strip()]
    if not ids:
        return
    _log.info("copilot_sandbox.sweeping_orphans", count=len(ids))
    await _run("docker", "rm", "-f", *ids, timeout=30.0)
