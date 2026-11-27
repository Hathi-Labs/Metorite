"""The sandbox broker — the ONE module that runs ``docker`` for a sandbox.

WS-43c. Spec ``project-docs/specs/maf_coding_engine.md`` §7.1 (D83).

A tool asks the broker for a container and for an exec, and never calls
``docker`` itself. The broker owns the Docker socket and every container of
the coding sandbox: one container per (organization, agent, thread), with no
network, a read-only root, a non-root uid, no capabilities, CPU, memory and
pids limits, and ONE read-write mount, the run's own working dir.

**Dark by construction.** ``acquire()`` refuses unless ``MAF_CODING_SCOPE``
names the bound agent's target for the bound organization, and the scope is
empty by default. The callers are ``acb_skills.sandbox_tools``: for
projects-assistant under ``projects:<org>`` (WS-43d, §16.3), only when
:func:`covers` is true, for the ``code_task`` session under ``code_task:<org>``
(WS-43e), and for app-builder under ``app_builder:<org>`` (WS-43h, §7.8). The
startup sweep is the one part that always runs.

**The tenant comes from the run binding, never from input (R5).** The
organization is ``executor._current_run_org()``. The agent, the thread, the
store key and the working dir come from the run's own artifact context
(``acb_skills.write_artifact.artifact_context()``), which the executor binds
server-side. ``acquire()`` takes no argument at all, so no caller can name a
tenant or a dir. The broker then computes the mount source itself, and refuses
when the bound working dir is not that source.

**No fallback to the host, ever (§7.1 rule 14).** When the broker cannot start
or reach a container it raises :class:`SandboxUnavailable`. No code here runs a
command on the host. This is the opposite of ``copilot_sandbox.py``.

**Single process.** The registry is in memory, as in ``copilot_sandbox.py``.
A gateway with more than one worker process needs it in Redis, through the
tenant-prefix wrapper (R5c). The spec lists that as a non-goal.

Fences (R7):

- WS43-F1 ``tests/unit/test_sandbox_broker_seam.py`` — no other module starts
  a ``docker`` process, apart from a legacy list that only shrinks.
- WS43-F2 ``tests/unit/test_sandbox_broker_argv.py`` — the ``docker run`` flags.
- WS43-F3 ``tests/unit/test_sandbox_broker_tenant.py`` — the tenant, the
  roots, the scope, the caps and the eviction.
- WS43-F4 ``tests/unit/test_sandbox_exec_hygiene.py`` — pipefail, the kill of
  every stray process, the restart, the timeout, the cap and the disk checks.
- SBX-F21 ``tests/unit/test_sandbox_remote_backend.py`` — the runtime
  (SBX-8), the ``docker-remote`` backend and one org per remote box (SBX-15).
- WS43-F9 ``tests/unit/test_sandbox_network_grant.py`` — the egress grant,
  the revoke, the network of each grant, the sweep and the dark default
  (WS-43g1, WS-43g2a).
  WS43-F5 ``tests/unit/test_sandbox_egress_proxy.py`` fences the proxy itself.

**Egress (WS-43g1, §7.3).** A container starts on ``--network none``. Only
:meth:`SandboxBroker.grant_egress` moves it, after a member approved it, onto
the ``--internal`` network of that one grant (WS-43g2a), where the one proxy
(``sandbox/egress_proxy.py``) is its only way out. With
``sandbox_egress_enabled`` off (the default) a grant refuses, and the broker
makes no proxy and no egress network. ``docker-remote`` gets no grant.

**The runtime and the backend (SBX-8, SBX-15, ``agent_sandbox.md`` §4.3 and
§4.6).** ``sandbox_runtime`` is ``runc`` (the default) or ``runsc`` (gVisor).
``sandbox_backend`` is ``docker`` (the default, the local daemon) or
``docker-remote``. The remote backend sends every ``docker`` call to the
daemon at ``sandbox_docker_host``, through ``DOCKER_HOST`` of the ``docker``
child only. That daemon cannot see a path of this box, so a remote container
mounts nothing. Its ``/workspace`` is a tmpfs. Before each command the broker
copies the working dir in, as a tar on the stdin of ``docker exec -i``. After
the command it copies ``/workspace`` back out, as a tar on the stdout of
``docker exec``, and writes each changed file through the safe opener. Only
the ``code_task`` target runs there. Outside dev (``acb_env``) the remote
backend refuses ``runc``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from acb_common import get_logger, get_settings
from acb_common.child_env import docker_env
from acb_common.secret_shapes import REDACTION_BEARER, REDACTION_SHAPES

_log = get_logger("orchestrator.sandbox_broker")

# ── Names ────────────────────────────────────────────────────────────────────

CONTAINER_PREFIX = "mtr-sbx-"
LABEL_SANDBOX = "metorite.sandbox"
LABEL_ORG = "metorite.org"
LABEL_AGENT = "metorite.agent"
LABEL_THREAD = "metorite.thread"
#: A fresh id per container start. The broker finds and removes a start
#: by this label or by its container id, NEVER by the name, so a late
#: removal cannot kill a newer container of the same name.
LABEL_START = "metorite.start"

WORKSPACE_TARGET = "/workspace"
GIT_COVER_TARGET = "/workspace/.git"
#: The nested mounts of the ``projects`` target (§16.3, D86, H-227). They lie
#: inside ``/workspace``, and no other target gets them.
OUTPUTS_TARGET = "/workspace/outputs"
INPUTS_TARGET = "/workspace/inputs"
RUN_DATA_TARGET = "/workspace/.run"
#: The skills of the working dir. A ``projects`` container sees an empty cover
#: here, with only the run member's own skill folders mounted on it.
SKILLS_TARGET = "/workspace/agent-data/skills"
SKILLS_MOUNTPOINT = "agent-data/skills"
#: The placeholder dir in the working dir that ``/workspace/.run`` mounts on.
#: The host makes it, so Docker never makes it as root.
RUN_DATA_MOUNTPOINT = ".run"
#: The tenant dir's partition marker (``agent_paths._INSTANCE_MARKER``). The
#: gateway's write-through reads it, so the ``projects`` target covers it with
#: a read-only mount of itself, and no container can rewrite it.
INSTANCE_MARKER = ".cc-instance"
INSTANCE_MARKER_TARGET = "/workspace/.cc-instance"

#: The agent whose target is ``app_builder``, and the one whose target is
#: ``projects``. Every other agent is ``code_task`` (§7.7 condition 1, §16.3).
APP_BUILDER_AGENT = "app-builder"
APP_BUILDER_TARGET = "app_builder"
PROJECTS_AGENT = "projects-assistant"
PROJECTS_TARGET = "projects"
COMPUTE_TARGET = "compute"
MAF_CODING_TARGETS = frozenset(
    {"code_task", APP_BUILDER_TARGET, PROJECTS_TARGET, COMPUTE_TARGET}
)

#: The ``compute`` target (SBX-18, ``agent_sandbox.md`` section 0c, rung 6a').
#: It runs Python over data the chat already holds: no network, no secret, no
#: mount, no write to Metorite and no saved tool. It passes the ONE scope seam:
#: ``compute:<org>`` in ``MAF_CODING_SCOPE`` (the sealed gate, a restart) AND
#: the org in ``compute_run_orgs`` (a ``*_orgs`` flag, so a SIGHUP reloads it,
#: the hot kill switch). Fence SBX-F26,
#: ``tests/unit/test_sandbox_broker_compute.py``.
COMPUTE_AGENT = "compute"
#: The tmpfs is owned by the run's uid, or the non-root user cannot write it.
COMPUTE_WORKSPACE_TMPFS = "/workspace:rw,nosuid,size=64m"
COMPUTE_OUT_TARGET = "/workspace/out"
COMPUTE_CPUS = "1"
COMPUTE_MEMORY = "1g"
COMPUTE_PIDS = 64
COMPUTE_MIN_TIMEOUT_S = 1
COMPUTE_MAX_TIMEOUT_S = 60
COMPUTE_STREAM_CAP = 256 * 1024
COMPUTE_MAX_FILES = 5
COMPUTE_MAX_FILE_BYTES = 1024 * 1024
COMPUTE_MAX_TOTAL_BYTES = 3 * 1024 * 1024
COMPUTE_MAX_INPUT_BYTES = 8 * 1024 * 1024
COMPUTE_OUT_SUFFIXES = frozenset({"csv", "png", "json", "txt"})
_COMPUTE_INPUT_NAME = re.compile(r"^(main\.py|inputs/tr_[0-9a-f]{8}\.txt)$")
_COMPUTE_OUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

#: The container runtimes (SBX-8). ``runc`` adds no flag, so a default start
#: keeps the argv of before. ``runsc`` is gVisor.
RUNTIME_RUNC = "runc"
RUNTIME_RUNSC = "runsc"
RUNTIMES = frozenset({RUNTIME_RUNC, RUNTIME_RUNSC})

#: The backends (SBX-15, ``agent_sandbox.md`` §4.4 and §4.6). ``e2b`` is SBX-10
#: and is not built, so it refuses like any unknown value.
BACKEND_DOCKER = "docker"
BACKEND_REMOTE = "docker-remote"
BACKENDS = frozenset({BACKEND_DOCKER, BACKEND_REMOTE})
#: The targets that the remote backend runs. ``code_task`` needs only its own
#: working dir, so a copy in and out carries it. ``projects`` needs the nested
#: thread mounts, the skill cover and the partition marker, and
#: ``app_builder`` needs the build copy and the T2 vendor cache. Each of those
#: is a path of this box, which a remote daemon cannot see, so both refuse.
REMOTE_TARGETS = frozenset({"code_task"})
#: The schemes of ``sandbox_docker_host``. A local socket reaches the daemon
#: of this box, so it is allowed in dev only, for a test of the backend.
REMOTE_SCHEMES = frozenset({"ssh", "tcp"})
LOCAL_SCHEMES = frozenset({"unix", "npipe"})

#: Copies the working dir INTO a remote container (SBX-15). It empties
#: ``/workspace``, then unpacks the tar on stdin as the container's own user.
#: ``docker cp`` cannot do it: it refuses a ``--read-only`` root, it cannot
#: see a tmpfs, and under gVisor it cannot see the sandbox's own file system.
PUSH_SCRIPT = (
    "chmod -R u+rwX /workspace 2>/dev/null; "
    "find /workspace -mindepth 1 -delete && "
    "exec tar -x -f - -C /workspace --no-same-owner --no-same-permissions"
)
#: Copies ``/workspace`` OUT of a remote container, as a tar on stdout. A hard
#: link goes as a file, and a ``.git`` at any depth stays behind.
#:
#: Both directions use the GNU format, which keeps each name as its raw bytes.
#: So no locale of the container can translate a name on the way. The host
#: then reads a byte that is not UTF-8 as a surrogate, and refuses the name.
PULL_SCRIPT = (
    "cd /workspace && exec tar -c -f - --format=gnu --hard-dereference "
    "--exclude=.git ."
)
REMOTE_COPY_FAILED = (
    "The files that this command changed could not be copied back from the "
    "sandbox, so those changes are lost. Run the command again."
)
#: One organization per remote box. It names no other organization.
ONE_ORG_BUSY = (
    "The sandbox box runs the code of another organization now, and it serves "
    "one organization at a time. Try again when that run finishes."
)

#: The two read-only mounts of the ``app_builder`` target (§7.8, WS-43h). They
#: come from code, never from a caller. The build scripts are a copy that the
#: broker makes, so no container ever sees the agent dir or a clone.
APP_BUILDER_BUILD_TARGET = "/opt/app-builder/build"
T2_VENDOR_TARGET = "/opt/t2-vendor"
#: The variable that ``build_t2.mjs`` reads for the vendor cache.
T2_VENDOR_ENV = "CUSTOM_APPS_T2_VENDOR_DIR"
#: The variable that ``build_t2.mjs`` reads for the source of ``@cc/ui``.
T2_UI_ENV = "CUSTOM_APPS_T2_UI_SOURCE"
#: Each file of the build copy, by its path in the repo, and no other file
#: (§7.8). ``artifactUi.tsx`` is ``@cc/ui``: ``build_t2.mjs`` finds it from
#: its own path in the repo, and the copy has no repo around it (review P1).
APP_BUILDER_BUILD_SOURCES: dict[str, str] = {
    "build_t2.mjs": "apps/agents/agent-app-builder/build/build_t2.mjs",
    "install_t2_deps.mjs": "apps/agents/agent-app-builder/build/install_t2_deps.mjs",
    "artifactUi.tsx": "workbench/control_plane/src/lib/artifactUi.tsx",
}
APP_BUILDER_BUILD_FILES = tuple(APP_BUILDER_BUILD_SOURCES)

#: The system part of ``PATH``. The thread's ``.local/bin`` goes LAST, so a
#: package one thread installs never shadows a system tool (§7.1 rule 6).
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

#: The keep-alive process. ``--init`` makes ``docker-init`` PID 1, and this is
#: its one child. The kill sweep spares exactly these two PIDs.
KEEPALIVE = ("sleep", "infinity")

RESTART_MESSAGE = (
    "The sandbox restarted. /tmp is empty now. The command did not run again. "
    "You can run the command again."
)
SURVIVOR_MESSAGE = (
    "A process outlived the command, so the sandbox restarted. /tmp is empty now."
)

# ── Egress (WS-43g1, §7.3, task 4009) ────────────────────────────────────────

#: The one proxy container. It is the only way out of a granted sandbox.
EGRESS_PROXY_NAME = "mtr-egress-proxy"
#: The port that the proxy listens on, inside its container. It is never
#: published on the host (no ``-p``).
EGRESS_PROXY_PORT = 3128
#: The proxy runs as ``nobody`` (§7.3, "The proxy").
EGRESS_PROXY_UID = 65534
#: The network of one grant: this prefix and a digest of the org, the
#: container name and the grant key (WS-43g2a, task 4070).
EGRESS_NET_PREFIX = "mtr-egress-"
#: The Linux bridge of that network. ``host_firewall.sh`` matches
#: ``mtr-eg-+``. An interface name holds at most 15 characters.
EGRESS_BRIDGE_PREFIX = "mtr-eg-"
#: ``proxy`` on the proxy container, ``1`` on each egress network.
LABEL_EGRESS = "metorite.egress"
#: The run of the grant that owns an egress network (WS-43g2a).
LABEL_EGRESS_RUN = "metorite.egress.run"
#: The door read of the proxy waits the card wait plus this (§7.3, "The door
#: wait"). The proxy refuses a value below :data:`DOOR_READ_TIMEOUT_MIN_S`.
DOOR_READ_TIMEOUT_EXTRA_S = 30
DOOR_READ_TIMEOUT_MIN_S = 90
#: The targets that may get a grant. ``projects`` and ``compute`` never do:
#: a covered run sends nothing off the platform (H-236, §16.3).
EGRESS_TARGETS = frozenset({"code_task", APP_BUILDER_TARGET})
#: The door name of ``agent_sandbox.md`` §12.3. The proxy forwards it to
#: ``sandbox_egress_door_upstream`` (done-when 9).
DOOR_HOST = "metorite-api.sandbox"
EGRESS_BIN_TARGET = "/opt/mtr-egress"
EGRESS_GRANTS_TARGET = "/etc/mtr-egress"
EGRESS_GRANTS_FILE = "grants.json"
EGRESS_MAX_HOSTS = 16
EGRESS_OFF = "Network access is off on this platform."

_MIB = 1024 * 1024
_HEALTH_TTL_SECONDS = 60.0
_HOST_GRACE_SECONDS = 15.0
_RUN_TIMEOUT_SECONDS = 60.0
#: SBX-15c: how long the reaper looks for the container of a ``docker run``
#: that timed out. The daemon can make it after the CLI died.
_LATE_START_WINDOW_SECONDS = 600.0
_SMALL_TIMEOUT_SECONDS = 30.0
_STDERR_KEEP = 8192
_READ_CHUNK = 65536

_DIGEST_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_CPUS = re.compile(r"^\d+(\.\d+)?$")
_MEMORY = re.compile(r"^\d+[bkmg]?$")
#: A ``docker exec`` that failed in Docker, not in the command. The command's
#: own stderr goes to stdout inside the container (``2>&1`` in the wrapper),
#: so the CLI's stderr holds only Docker's words.
_DOCKER_FAILURE = re.compile(
    r"Error response from daemon|OCI runtime|is not running|No such container|"
    r"procReady|cannot exec|is restarting|unable to start container process",
    re.IGNORECASE,
)

# ── Errors ───────────────────────────────────────────────────────────────────


class SandboxError(RuntimeError):
    """Base of every broker error. A tool answers with ``str(exc)``."""


class SandboxRefused(SandboxError):
    """The broker refuses: no tenant, a bad dir, out of scope, or a limit."""


class SandboxBusy(SandboxError):
    """A cap is full, and no container that the rules may stop is idle."""


class SandboxUnavailable(SandboxError):
    """Docker cannot start or reach the container. There is no host fallback."""


# ── The scope (MAF_CODING_SCOPE) ─────────────────────────────────────────────


def parse_maf_coding_scope(raw: str) -> frozenset[tuple[str, str]]:
    """``{(target, org)}`` from a comma list of ``<target>:<org>`` entries.

    A target is ``code_task``, ``app_builder``, ``projects`` (D86, §16.3) or
    ``compute`` (SBX-18).
    An org is one organization id, or ``*`` for every organization. Raises
    :class:`ValueError` on an unknown target or an entry with no org, so a
    typo never turns on a target that nobody named.
    """
    entries: set[tuple[str, str]] = set()
    for part in (raw or "").split(","):
        item = part.strip()
        if not item:
            continue
        target, sep, org = item.partition(":")
        target, org = target.strip(), org.strip()
        if not sep or not org or any(ch.isspace() for ch in org):
            raise ValueError(f"MAF_CODING_SCOPE entry {item!r} is not <target>:<org>")
        if target not in MAF_CODING_TARGETS:
            raise ValueError(
                f"MAF_CODING_SCOPE entry {item!r} names an unknown target "
                f"{target!r}. The targets are {sorted(MAF_CODING_TARGETS)}."
            )
        entries.add((target, org))
    return frozenset(entries)


def maf_coding_scope_allows(target: str, org: str) -> bool:
    """True when ``MAF_CODING_SCOPE`` holds *target* for *org*.

    Reads the setting on each call, so a flip needs no restart. An invalid
    value fails closed: it turns every target off and logs one line.
    """
    raw = str(getattr(get_settings(), "maf_coding_scope", "") or "")
    try:
        entries = parse_maf_coding_scope(raw)
    except ValueError as exc:
        _log.warning("sandbox_broker.scope_invalid", error=str(exc)[:300])
        return False
    return bool(org) and ((target, org) in entries or (target, "*") in entries)


def target_for_agent(agent: str) -> str:
    """``app_builder`` for app-builder, ``projects`` for projects-assistant,
    ``compute`` for the broker's own ``compute`` name, and ``code_task`` for
    every other agent. ``compute`` is in no scope, so a real agent that bears
    that name gets no container from :meth:`SandboxBroker.acquire`."""
    if agent == COMPUTE_AGENT:
        return COMPUTE_TARGET
    if agent == APP_BUILDER_AGENT:
        return APP_BUILDER_TARGET
    if agent == PROJECTS_AGENT:
        return PROJECTS_TARGET
    return "code_task"


# ── The runtime and the backend (SBX-8, SBX-15) ──────────────────────────────


def production_mode(settings: Any) -> bool:
    """True unless ``acb_env`` is ``dev``. An absent value reads as production."""
    return str(getattr(settings, "acb_env", "prod") or "prod").strip().lower() != "dev"


def sandbox_backend(settings: Any) -> str:
    """``docker`` or ``docker-remote``. Any other value fails closed.

    It raises :class:`SandboxUnavailable`, which every ``docker`` call and the
    startup sweep already handle as "Docker cannot be reached". So a typo
    never sends a container to the local daemon.
    """
    raw = str(getattr(settings, "sandbox_backend", BACKEND_DOCKER) or BACKEND_DOCKER)
    backend = raw.strip().lower()
    if backend not in BACKENDS:
        raise SandboxUnavailable(
            f"The sandbox backend {backend[:40]!r} is not one that this broker runs. "
            f"The backends are {sorted(BACKENDS)}. No sandbox starts."
        )
    return backend


def redact_docker_host(value: str) -> str:
    """The scheme and the host of a daemon address, and nothing more.

    The user, the port and the path stay out, so a log line or an error text
    never holds them.
    """
    try:
        parts = urlsplit(str(value or "").strip())
        host = parts.hostname
    except ValueError:
        return "(invalid)"
    scheme = parts.scheme or "?"
    return f"{scheme}://{host or 'local'}"


def remote_docker_host(settings: Any) -> str:
    """``sandbox_docker_host``, checked. Raises :class:`SandboxUnavailable`.

    It must be ``ssh://`` or ``tcp://`` with a host. A local socket
    (``unix://``, ``npipe://``) reaches the daemon of this box, so it is
    allowed in dev only. An empty value never falls back to the local daemon.
    """
    raw = str(getattr(settings, "sandbox_docker_host", "") or "").strip()
    if not raw:
        raise SandboxUnavailable(
            "The docker-remote backend has no sandbox_docker_host, so no sandbox starts."
        )
    shown = redact_docker_host(raw)
    if any(ch.isspace() or ord(ch) < 32 for ch in raw):
        raise SandboxUnavailable(
            f"The sandbox_docker_host {shown} holds a space or a control character."
        )
    try:
        parts = urlsplit(raw)
        host = parts.hostname
    except ValueError:
        raise SandboxUnavailable("The sandbox_docker_host is not a valid address.") from None
    scheme = parts.scheme.lower()
    production = production_mode(settings)
    if scheme in REMOTE_SCHEMES and host:
        if production and _is_loopback(host):
            raise SandboxUnavailable(
                f"The sandbox_docker_host {shown} is this box. Outside dev the "
                "daemon must be on the sandbox box."
            )
        if production and scheme == "tcp":
            remote_cert_dir(settings, required=True)
        return raw
    if scheme in LOCAL_SCHEMES and not production:
        return raw
    raise SandboxUnavailable(
        f"The sandbox_docker_host {shown} is not a remote daemon. Use ssh:// or "
        "tcp:// with a host. A local socket is for dev only."
    )


def _is_loopback(host: str) -> bool:
    """True for ``localhost``, 127.0.0.0/8, ``::1``, a mapped one, or 0.0.0.0."""
    import ipaddress

    name = host.strip().strip("[]").rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return address.is_loopback or address.is_unspecified


def remote_cert_dir(settings: Any, *, required: bool) -> str | None:
    """``sandbox_docker_cert_path`` as an existing dir, or ``None`` when unset.

    With *required* (``tcp://`` outside dev), an unset or missing dir raises
    :class:`SandboxUnavailable`: a plain ``tcp://`` daemon has no TLS and no
    client certificate.
    """
    raw = str(getattr(settings, "sandbox_docker_cert_path", "") or "").strip()
    if raw and Path(raw).is_dir():
        return raw
    if required:
        raise SandboxUnavailable(
            "A tcp:// sandbox daemon needs TLS with a client certificate outside "
            "dev. Set sandbox_docker_cert_path to the dir of that certificate."
        )
    return None


def sandbox_runtime(settings: Any, backend: str) -> str:
    """The runtime of a start: ``runc`` or ``runsc``. Raises :class:`SandboxRefused`.

    An unknown value refuses. On the remote backend outside dev, ``runc``
    refuses too: production runs member code under gVisor only (§4.6, §13).
    """
    raw = str(getattr(settings, "sandbox_runtime", RUNTIME_RUNC) or RUNTIME_RUNC)
    runtime = raw.strip().lower()
    if runtime not in RUNTIMES:
        raise SandboxRefused(
            f"The sandbox runtime {runtime[:40]!r} is not known. The runtimes are "
            f"{sorted(RUNTIMES)}. No sandbox starts."
        )
    if backend == BACKEND_REMOTE and runtime != RUNTIME_RUNSC and production_mode(settings):
        raise SandboxRefused(
            "The docker-remote backend runs runsc (gVisor) only outside dev, and "
            "sandbox_runtime is runc. No sandbox starts."
        )
    return runtime


def _daemon_names() -> dict[str, str | None]:
    """The daemon names of the ``docker`` child. Empty on the local backend.

    On ``docker-remote`` it is the checked ``DOCKER_HOST``, with
    ``DOCKER_CONTEXT`` left out, so a context of this box cannot win. A
    ``tcp://`` daemon with a certificate dir gets ``DOCKER_TLS_VERIFY=1`` and
    ``DOCKER_CERT_PATH``. Any other remote daemon gets neither, so a TLS value
    of this box never mixes in. Only :class:`DockerCLI` passes it, so no other
    child ever gets the values.
    """
    settings = get_settings()
    if sandbox_backend(settings) != BACKEND_REMOTE:
        return {}
    host = remote_docker_host(settings)
    cert = None
    if urlsplit(host).scheme.lower() == "tcp":
        cert = remote_cert_dir(settings, required=production_mode(settings))
    return {
        "DOCKER_HOST": host,
        "DOCKER_CONTEXT": None,
        "DOCKER_TLS_VERIFY": "1" if cert else None,
        "DOCKER_CERT_PATH": cert,
    }


def _host_paths(settings: Any) -> list[str]:
    """The paths of this box that a docker CLI error can name, longest first.

    The cert dir of a ``tcp://`` daemon, and the home dir of this process (an
    ssh key, ``known_hosts``). A path with no segment (``/``) never counts.
    """
    found: set[str] = set()
    cert = str(getattr(settings, "sandbox_docker_cert_path", "") or "").strip()
    with contextlib.suppress(RuntimeError, KeyError, OSError):
        home = str(Path.home())
        if home:
            found.add(home)
    if cert:
        found.add(cert)
    paths = {p.rstrip("/\\") for p in found}
    return sorted((p for p in paths if p.strip("/\\")), key=len, reverse=True)


#: SBX-15c review P3: the forms in which the docker CLI and ssh name a port or
#: a dialled address. Only the text for the model passes through them.
_PORT_WORDS = re.compile(r"(?i)\b(port\s+)\d{1,5}\b")
_DIAL = re.compile(r"(?i)\b(dial\s+(?:tcp|unix)\S*\s+)\S+")


def _scrub(text: str, *, model: bool = False) -> str:
    """*text* with the daemon address cut to its scheme and host, and with no
    path or credential of this box.

    An ssh error names ``user@host``, or ``ssh -l <user> -p <port>``, and the
    broker logs Docker's stderr. The user and the port go. SBX-15c: the
    stderr of a compute run reaches the model too, so each host path of
    :func:`_host_paths` and each credential shape of the one list
    (``acb_common.secret_shapes``) go as well. With *model* (the stderr that
    the model reads), the host of the sandbox box and each port go too.
    """
    if not text:
        return text
    settings = get_settings()
    out = _scrub_address(text, str(getattr(settings, "sandbox_docker_host", "") or "").strip())
    for path in _host_paths(settings):
        # Only the whole path: ``/root`` and ``/root.`` go, ``/rootfs`` and
        # ``/root.bak`` stay.
        out = re.sub(re.escape(path) + r"(?![\w-]|\.[\w-])", "(host path)", out)
    for rx in (REDACTION_BEARER, *REDACTION_SHAPES):
        out = rx.sub("(secret)", out)
    if model:
        # The model also gets no host of the sandbox box and no port.
        raw = str(getattr(settings, "sandbox_docker_host", "") or "").strip()
        with contextlib.suppress(ValueError):
            host = urlsplit(raw).hostname if raw else None
            if host:
                out = re.sub(rf"(?<![\w.-]){re.escape(host)}(?![\w-]|\.[\w-])", "(sandbox box)", out)
        out = _PORT_WORDS.sub(r"\1(port)", out)
        out = _DIAL.sub(r"\1(sandbox box)", out)
    return out


def _scrub_address(text: str, raw: str) -> str:
    """*text* with the daemon address *raw* cut to its scheme and host."""
    if not raw:
        return text
    out = text.replace(raw, redact_docker_host(raw))
    try:
        parts = urlsplit(raw)
        user, port, host = parts.username, parts.port, parts.hostname
    except ValueError:
        user, port, host = None, None, None
    if user:
        out = out.replace(f"{user}@", "")
        out = re.sub(rf"(-l\s+){re.escape(user)}(?=\s|$)", r"\1(user)", out)
    if port:
        out = re.sub(rf"(-p\s+){port}(?=\s|$)", r"\1(port)", out)
        if host:
            # Only the port of the host token. A bare `:22` elsewhere (a
            # time, a line number) stays as it is (review P3).
            for token in (f"[{host}]:{port}", f"{host}:{port}"):
                out = out.replace(token, token.rsplit(":", 1)[0])
    return out


# ── The run binding ──────────────────────────────────────────────────────────


def _digest(*values: str, length: int = 16) -> str:
    return hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()[:length]


@dataclass(frozen=True)
class RunBinding:
    """The bound run's tenant, agent, thread, store key and working dir."""

    org: str
    agent: str
    thread: str
    instance: str
    workspace: Path
    #: The run's member, from the run binding (R5). The ``projects`` target
    #: mounts only this member's skill folders.
    member: str = ""
    #: This member's own skill folders, as :meth:`SandboxBroker.acquire` read
    #: them (``agent_paths.own_skill_names``). A container whose set differs
    #: is not reused, so a new skill shows on the next command.
    skills: tuple[str, ...] = ()

    @property
    def thread_hash(self) -> str:
        return _digest(self.thread)

    @property
    def target(self) -> str:
        return target_for_agent(self.agent)

    @property
    def thread_slug(self) -> str:
        """The folder name of this thread's outputs (§16.3). Raises ``ValueError``."""
        from acb_skills.agent_paths import thread_slug

        return thread_slug(self.thread)

    @property
    def outputs_rel(self) -> str:
        """``outputs/<thread slug>``, relative to the working dir."""
        return f"outputs/{self.thread_slug}"

    @property
    def inputs_rel(self) -> str:
        """``inputs/<thread slug>``, the thread's own uploads (H-227). Raises ``ValueError``."""
        from acb_skills.agent_paths import thread_inputs_rel

        return thread_inputs_rel(self.thread)

    @property
    def run_data_rel(self) -> str:
        """``.run-data/<org slug>/<thread slug>``, relative to the state root."""
        from acb_skills.agent_paths import run_data_rel

        return run_data_rel(self.org, self.thread)

    @property
    def run_data(self) -> Path:
        """The run-data dir of this thread, outside every kept folder (§16.3)."""
        return _real_state_root() / self.run_data_rel

    @property
    def name(self) -> str:
        """``mtr-sbx-`` plus 16 hex digits of a SHA-256 of the three keys."""
        return CONTAINER_PREFIX + _digest(self.org, self.agent, self.thread)

    def labels(self) -> dict[str, str]:
        return {
            LABEL_SANDBOX: "1",
            LABEL_ORG: self.org,
            LABEL_AGENT: self.agent,
            LABEL_THREAD: self.thread_hash,
        }


def _bound_org() -> str | None:
    """The run's tenant, from the run binding. Takes no argument (R5)."""
    from orchestrator.executor import _current_run_org

    org = _current_run_org()
    return str(org).strip() if org else None


def _custom_apps_root() -> Path:
    from orchestrator.executor import _custom_apps_root as root

    return root()


def _app_dir_of_org(path: Path, org: str) -> bool:
    """True when *path* lies in the folder of an app of *org* (task 2462).

    A dir under the Custom Apps root can be the folder of an app of another
    org, because a deleted slug is free again. The ONE check is
    ``executor._app_workspace_of_org``, read at call time.
    """
    from orchestrator import executor

    return executor._app_workspace_of_org(path, org)


def app_builder_build_source() -> Path | None:
    """The repo checkout that holds the agent's ``build/`` dir, or ``None`` (§7.8).

    It is found from this module's own path, never from input or a setting.
    The broker copies only :data:`APP_BUILDER_BUILD_SOURCES` out of it.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "apps" / "agents" / "agent-app-builder" / "build"
        if candidate.is_dir() and not _is_link(candidate):
            return parent
    return None


def ensure_app_git(workspace: Path) -> None:
    """The app dir holds a real ``.git`` dir before a container starts (§7.5 rule A).

    WS-43h (review P2). ``.git`` gets the empty read-only cover only when it
    exists at the start. With no ``.git``, a container could write
    ``.git/config`` and ``.gitattributes``, and the next host checkpoint
    would run a planted filter on the host. So a ``.git`` that is a link or a
    file is refused, and a missing one is made before the start. It fails
    closed: no repo, no container.

    The broker starts no process but ``docker`` (WS43-F1). The ``git init``
    is ``durability.init_app_repo``, the one host git of an app dir.
    """
    git_dir = workspace / ".git"
    if _is_link(git_dir) or (git_dir.exists() and not git_dir.is_dir()):
        raise SandboxRefused("The .git of the app dir is not a real dir, so no sandbox starts.")
    if git_dir.is_dir():
        return
    try:
        from gateway.routes.apps.durability import init_app_repo
    except ImportError as exc:
        raise SandboxUnavailable(
            "The app dir has no git repo, and no code here can make one. No sandbox starts."
        ) from exc
    if not init_app_repo(workspace) or _is_link(git_dir) or not git_dir.is_dir():
        raise SandboxUnavailable(
            "The app dir has no git repo, and git init failed. No sandbox starts."
        )


def _t2_vendor_source() -> Path | None:
    """The T2 vendor cache as a real path, or ``None`` when the deploy made none.

    ``gateway.routes.apps._common.t2_vendor_dir`` is the ONE place that names
    it. The deploy makes it, outside every mount root, and no container can
    write it.
    """
    try:
        from gateway.routes.apps._common import t2_vendor_dir
    except ImportError:
        return None
    try:
        real = Path(t2_vendor_dir()).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return real if real.is_dir() else None


def _real_state_root() -> Path:
    """``state_root()`` with every link resolved, as a mount source needs it."""
    from acb_skills.agent_paths import state_root

    return state_root().resolve()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _check_strictly_under(path: Path, root: Path) -> Path:
    """The real path of *path*, which must lie strictly below *root*.

    Each part below *root* must be a real dir, never a link. Raises
    :class:`SandboxRefused` otherwise.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise SandboxRefused("The working dir of this run is not a clean absolute path.")
    try:
        rel = path.relative_to(root)
    except ValueError:
        raise SandboxRefused(
            "The working dir of this run lies outside the sandbox roots."
        ) from None
    if not rel.parts:
        raise SandboxRefused("The working dir of this run is a sandbox root itself.")
    cur = root
    for part in rel.parts:
        cur = cur / part
        if _is_link(cur):
            raise SandboxRefused("The working dir of this run has a link in its path.")
    if not path.is_dir():
        raise SandboxRefused("The working dir of this run does not exist.")
    real, real_root = path.resolve(strict=True), root.resolve()
    if real == real_root or not real.is_relative_to(real_root):
        raise SandboxRefused("The working dir of this run resolves outside the sandbox roots.")
    return real


def _mount_source_for(agent: str, instance: str, org: str, workspace: str) -> Path:
    """The mount source, computed from the binding, as a real path.

    A run with a store key mounts ``state/<agent>/<slug of key>``. A tenant
    key must be this run's own ``o:<org>``. app-builder may also mount its app
    dir under the Custom Apps root, when an app of this run's org owns that
    dir (task 2462). Anything else is refused.
    """
    from acb_skills.agent_paths import (
        agent_state_dir,
        is_tenant_instance,
        state_root,
        tenant_instance,
    )

    raw = Path(workspace)
    if instance:
        if is_tenant_instance(instance) and instance != tenant_instance(org):
            raise SandboxRefused(
                "The working dir of this run belongs to another organization."
            )
        if raw != agent_state_dir(agent, instance):
            raise SandboxRefused("The working dir of this run is not its own state dir.")
        return _check_strictly_under(raw, state_root())
    if agent == APP_BUILDER_AGENT:
        real = _check_strictly_under(raw, _custom_apps_root())
        if not _app_dir_of_org(real, org):
            raise SandboxRefused(
                "The working dir of this run is not an app of its organization."
            )
        return real
    raise SandboxRefused("This run has no store key, so it has no sandbox working dir.")


def read_run_binding() -> RunBinding:
    """The bound run, read from the run binding alone. Raises SandboxRefused."""
    org = _bound_org()
    if not org:
        raise SandboxRefused("This run has no organization, so no sandbox can start.")
    from acb_skills.agent_paths import InvalidAgentName, require_agent_name
    from acb_skills.write_artifact import artifact_context

    ctx = artifact_context()
    agent = str(ctx.get("agent_name") or "")
    thread = str(ctx.get("session_id") or "")
    workspace = str(ctx.get("workspace_root") or "")
    instance = str(ctx.get("instance") or "")
    if not (agent and thread and workspace):
        raise SandboxRefused("No run is bound on this frame, so no sandbox can start.")
    try:
        require_agent_name(agent)
    except InvalidAgentName:
        raise SandboxRefused("The bound agent name is not valid.") from None
    source = _mount_source_for(agent, instance, org, workspace)
    return RunBinding(
        org=org, agent=agent, thread=thread, instance=instance, workspace=source,
        member=str(ctx.get("member") or ""),
    )


def read_compute_binding() -> RunBinding:
    """The bound run as a ``compute`` binding (SBX-18). Raises SandboxRefused.

    The org, the member and the thread come from the run binding alone (R5).
    The agent becomes ``compute``, so the target, the container name and the
    labels derive from (org, ``compute``, thread).
    """
    from dataclasses import replace

    return replace(read_run_binding(), agent=COMPUTE_AGENT)


def compute_enabled(org: str | None) -> bool:
    """True when ``MAF_CODING_SCOPE`` holds ``compute:<org>`` (or ``compute:*``)
    AND ``compute_run_orgs`` names *org*, or ``*``.

    The scope is the sealed gate and does not reload. The flag is the kill
    switch. ``""`` is off for all. The value is a comma or space list. A missing or
    empty *org* is never enabled. Reads the setting on each call, so a SIGHUP
    reload needs no restart.
    """
    who = str(org or "").strip()
    if not who or who == "*":
        return False
    if not maf_coding_scope_allows(COMPUTE_TARGET, who):
        return False
    settings = get_settings()
    raw = str(getattr(settings, "compute_run_orgs", "") or "")
    items = {item for item in re.split(r"[,\s]+", raw) if item}
    if str(getattr(settings, "self_serve_signup_enabled", "") or "").strip().lower() == "true":
        # Anyone can sign up, so a wildcard would give compute to a stranger.
        try:
            scope = parse_maf_coding_scope(str(getattr(settings, "maf_coding_scope", "") or ""))
        except ValueError:
            return False
        if "*" in items or (COMPUTE_TARGET, "*") in scope:
            return False
    return "*" in items or who in items


def _production_mode(settings: Any) -> bool:
    """True outside dev: staging counts as production. The one rule is
    :func:`production_mode` (SBX-15), and this name stays for SBX-18."""
    return production_mode(settings)


def _compute_production_guard(settings: Any) -> None:
    """Refuse in production unless the settings name a remote gVisor backend.

    This is the early check. The real one is in ``_compute_start``, which
    checks the argv it is about to run and the org admit of that start. It
    reads the backend and the runtime through the SBX-15 readers, so a bad
    value refuses too.
    """
    if not _production_mode(settings):
        return
    try:
        backend = sandbox_backend(settings)
        runtime = sandbox_runtime(settings, backend)
    except SandboxError as exc:
        raise SandboxRefused(
            "Compute runs in production only on a remote gVisor backend."
        ) from exc
    if backend != BACKEND_REMOTE or runtime != RUNTIME_RUNSC:
        raise SandboxRefused(
            "Compute runs in production only on a remote gVisor backend."
        )


# ── The mounts (§7.1 rule 5) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Mount:
    """One bind mount. ``--mount`` refuses a missing source, unlike ``-v``."""

    source: Path
    target: str
    readonly: bool

    def args(self) -> list[str]:
        spec = f"type=bind,source={self.source},target={self.target}"
        return ["--mount", spec + (",readonly" if self.readonly else "")]


def _check_source_text(path: Path) -> None:
    text = str(path)
    if any(ch in text for ch in (",", '"', "\n", "\r", "\x00")):
        raise SandboxRefused("A mount source has a character that a mount spec cannot hold.")
    if path.name == "docker.sock" or "docker.sock" in text:
        raise SandboxRefused("No container may mount the Docker socket.")


def _git_entries(root: Path, *, include_root: bool) -> list[Path]:
    """Every ``.git`` entry below *root*, at any depth, with no link followed."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        for name in (*dirnames, *filenames):
            if name != ".git":
                continue
            if here == root and not include_root:
                continue
            found.append(here / name)
    return found


def _check_readonly_source(source: Path) -> None:
    """A read-only source must be a real dir with no ``.git`` at any depth."""
    _check_source_text(source)
    if not source.is_absolute() or _is_link(source) or not source.is_dir():
        raise SandboxRefused("A read-only mount source must be a real dir.")
    if source.resolve() != source:
        raise SandboxRefused("A read-only mount source has a link in its path.")
    if _git_entries(source, include_root=True):
        raise SandboxRefused(
            "A read-only mount source holds a .git entry, which can hold a token."
        )


@dataclass(frozen=True)
class GitCover:
    """The two empty sources that cover ``/workspace/.git``."""

    empty_dir: Path
    empty_file: Path


def _git_cover_mount(ws: Path, cover: GitCover) -> Mount | None:
    """The empty read-only mount on ``/workspace/.git``, when a root ``.git`` exists.

    A dir gets the empty dir, and a gitfile gets the empty file. A link is
    refused. With no root ``.git`` there is no real ``.git`` to hide.
    """
    root_git = ws / ".git"
    if _is_link(root_git):
        raise SandboxRefused("The .git of the working dir is a link.")
    if root_git.is_dir():
        return Mount(cover.empty_dir, GIT_COVER_TARGET, readonly=True)
    if root_git.exists():
        return Mount(cover.empty_file, GIT_COVER_TARGET, readonly=True)
    return None


def prepare_projects_dirs(binding: RunBinding) -> None:
    """Make the thread's output and upload folders, the ``.run`` placeholder
    and the run-data dir.

    The ``projects`` target only (§16.3). Each dir is made with the safe
    opener, so no part of its path can be a link, just before the start. A
    plain file or a link in the way raises :class:`SandboxRefused`.
    """
    from acb_skills import safe_open

    try:
        safe_open.ensure_dir(binding.workspace, binding.outputs_rel)
        safe_open.ensure_dir(binding.workspace, binding.inputs_rel)
        # The empty dirs that the alias mounts stand on (projects_mounts).
        safe_open.ensure_dir(binding.workspace, f"{binding.outputs_rel}/{binding.thread_slug}")
        safe_open.ensure_dir(binding.workspace, f"{binding.inputs_rel}/{binding.thread_slug}")
        safe_open.ensure_dir(binding.workspace, RUN_DATA_MOUNTPOINT)
        # The mount point of the skills cover (projects_mounts).
        safe_open.ensure_dir(binding.workspace, SKILLS_MOUNTPOINT)
        safe_open.ensure_dir(_real_state_root(), binding.run_data_rel)
        marker = binding.instance.encode("utf-8")
        if safe_open.read_bytes(binding.workspace, INSTANCE_MARKER, limit=4096) != marker:
            safe_open.write_bytes(binding.workspace, INSTANCE_MARKER, marker)
    except (safe_open.UnsafePath, FileExistsError, NotADirectoryError) as exc:
        raise SandboxRefused(
            "A dir of this thread's sandbox is not a real dir, so no sandbox starts."
        ) from exc
    except ValueError as exc:
        raise SandboxRefused("This thread's id cannot name a sandbox folder.") from exc


def _real_dir(path: Path) -> Path:
    """*path*, when it is a real dir whose real path is itself."""
    _check_source_text(path)
    if _is_link(path) or not path.is_dir() or path.resolve() != path:
        raise SandboxRefused("A sandbox dir of this thread is not a real dir.")
    return path


def _own_skill_mounts(binding: RunBinding) -> list[Mount]:
    """A read-only mount of each of the run member's own skill folders.

    Each one is checked again at the start: a real dir reached with no link,
    whose marker still names this member. A folder that fails is left out.
    """
    from acb_skills.agent_paths import SKILL_MINE, SKILLS_REL, skill_owner

    ws = binding.workspace
    root = ws / SKILLS_MOUNTPOINT
    if _is_link(ws / "agent-data") or _is_link(root):
        return []
    mounts = []
    for name in binding.skills:
        folder = root / name
        try:
            _check_source_text(folder)
        except SandboxRefused:
            continue
        if _is_link(folder) or not folder.is_dir() or folder.resolve() != folder:
            continue
        if skill_owner(ws, f"{SKILLS_REL}/{name}", binding.member) != SKILL_MINE:
            continue
        mounts.append(Mount(folder, f"{SKILLS_TARGET}/{name}", readonly=True))
    return mounts


def projects_mounts(binding: RunBinding, skill_cover: Path) -> list[Mount]:
    """The nested mounts of the ``projects`` target (§16.3, H-227).

    - ``outputs/<thread slug>/`` at ``/workspace/outputs``, read-write, over
      the shared ``outputs/``. So a container sees only its own thread's
      outputs, and never the parent folder or another thread's folder.
    - ``inputs/<thread slug>/`` at ``/workspace/inputs``, READ-ONLY, over the
      shared ``inputs/`` (H-227). So a container sees only the files that
      were attached in its own thread. It never sees another member's upload
      or a file in the flat ``inputs/``. These two covers are the only nested
      mounts that the broker allows within its own workspace.
    - Each cover again, at ``/workspace/outputs/<thread slug>`` and
      ``/workspace/inputs/<thread slug>`` (the PR #616 verifier). The upload
      message and the cards name a file as ``inputs/<thread slug>/x``, and
      the model copies that path into a command. So that path reaches the
      same file. The alias of the upload folder is READ-ONLY too. Each alias
      stands on an empty dir of the same name in the thread's folder, which
      :func:`prepare_projects_dirs` makes. No file is ever written there,
      because a write through the alias lands in the folder itself.
    - The run-data dir at ``/workspace/.run``, in this thread's container
      only. It lies under ``state_root()/.run-data``, outside every kept
      folder, so the blob store never holds it.
    - The partition marker ``.cc-instance`` at itself, READ-ONLY. The
      gateway's write-through and fault-in read it, so a container must not
      rewrite it.
    - *skill_cover* at ``/workspace/agent-data/skills``, READ-ONLY. It holds
      only an empty dir for each of the run member's own skills
      (:meth:`SandboxBroker.skill_cover`). Then each of those skill folders at
      its own path, READ-ONLY. So a container never sees a skill of another
      member: its text, its scripts or its name (WS-43v).

    :func:`prepare_projects_dirs` makes the dirs. This checks them again.
    """
    ws = binding.workspace
    try:
        outputs = _real_dir(ws / binding.outputs_rel)
        inputs = _real_dir(ws / binding.inputs_rel)
        slug = binding.thread_slug
        _real_dir(outputs / slug)
        _real_dir(inputs / slug)
        _real_dir(ws / RUN_DATA_MOUNTPOINT)
        _real_dir(ws / SKILLS_MOUNTPOINT)
        run_data = _real_dir(binding.run_data)
    except ValueError as exc:
        if isinstance(exc, SandboxError):
            raise
        raise SandboxRefused("This thread's id cannot name a sandbox folder.") from exc
    marker = ws / INSTANCE_MARKER
    _check_source_text(marker)
    if _is_link(marker) or not marker.is_file() or marker.resolve() != marker:
        raise SandboxRefused("The partition marker of the working dir is not a real file.")
    return [
        Mount(outputs, OUTPUTS_TARGET, readonly=False),
        Mount(inputs, INPUTS_TARGET, readonly=True),
        Mount(outputs, f"{OUTPUTS_TARGET}/{slug}", readonly=False),
        Mount(inputs, f"{INPUTS_TARGET}/{slug}", readonly=True),
        Mount(run_data, RUN_DATA_TARGET, readonly=False),
        Mount(marker, INSTANCE_MARKER_TARGET, readonly=True),
        Mount(skill_cover, SKILLS_TARGET, readonly=True),
        *_own_skill_mounts(binding),
    ]


def mount_list(
    binding: RunBinding,
    cover: GitCover,
    readonly_mounts: Sequence[tuple[Path, str]] = (),
    skill_cover: Path | None = None,
) -> list[Mount]:
    """THE one function that builds the mounts of a container.

    Every start and every restart calls it, and so do a grant and a revoke
    (WS-43g1), because each of them recreates the container through
    :meth:`SandboxBroker._restart`. So no recreate can miss the cover on
    ``/workspace/.git``, or the covers of a thread's output and upload folders.

    - The run's working dir at ``/workspace``, read-write. For the
      ``projects`` target it is READ-ONLY (review P1, fix round 1): every
      thread of one organization mounts the same dir, so a thread that could
      write it could plant code that another member's thread runs beside that
      member's run data. Its only writable paths are its own output folder
      and its run data, two of the nested mounts below.
    - A ``.git`` at the root of the working dir is covered by an empty
      read-only bind mount, so the container can neither read nor write it.
    - A ``.git`` deeper in the working dir is refused, and so is a root
      ``.git`` that is a link.
    - The ``projects`` target adds its nested mounts
      (:func:`projects_mounts`). No other target gets them. It needs
      *skill_cover*, and with none no container starts.
    - *readonly_mounts* come from code, never from a caller. The one list is
      :meth:`SandboxBroker.app_builder_mounts` (WS-43h). A source with
      ``.git`` at any depth is refused.
    """
    if binding.target == COMPUTE_TARGET:
        return []  # SBX-18: the compute target mounts nothing.
    ws = binding.workspace
    _check_source_text(ws)
    if not ws.is_dir() or _is_link(ws) or ws.resolve() != ws:
        raise SandboxRefused("The working dir is not a real dir.")
    if _git_entries(ws, include_root=False):
        raise SandboxRefused(
            "The working dir holds a .git below its root. Only a root .git is allowed."
        )
    shared = binding.target == PROJECTS_TARGET
    mounts = [Mount(ws, WORKSPACE_TARGET, readonly=shared)]
    git_cover = _git_cover_mount(ws, cover)
    if git_cover is not None:
        mounts.append(git_cover)
    if binding.target == PROJECTS_TARGET:
        if skill_cover is None:
            raise SandboxRefused("A projects container needs the cover of the skills folder.")
        _check_readonly_source(skill_cover)
        mounts += projects_mounts(binding, skill_cover)
    for source, target in readonly_mounts:
        _check_readonly_source(source)
        if not target.startswith("/") or target.startswith(WORKSPACE_TARGET):
            raise SandboxRefused("A read-only mount target must lie outside /workspace.")
        mounts.append(Mount(source, target, readonly=True))
    return mounts


# ── The docker run arguments (§7.1 rules 3 and 6) ────────────────────────────


@dataclass(frozen=True)
class Limits:
    cpus: str
    memory: str
    pids: int
    tmpfs_mb: int

    @classmethod
    def from_settings(cls, settings: Any) -> Limits:
        limits = cls(
            cpus=str(getattr(settings, "sandbox_cpus", "1")).strip(),
            memory=str(getattr(settings, "sandbox_memory", "1g")).strip().lower(),
            pids=int(getattr(settings, "sandbox_pids_limit", 256)),
            tmpfs_mb=int(getattr(settings, "sandbox_tmpfs_mb", 256)),
        )
        if not (_CPUS.match(limits.cpus) and _MEMORY.match(limits.memory)):
            raise SandboxRefused("The sandbox CPU or memory setting is not valid.")
        if limits.pids <= 0 or limits.tmpfs_mb <= 0:
            raise SandboxRefused("The sandbox pids or tmpfs setting is not valid.")
        return limits


def pinned_image(settings: Any) -> str:
    """``sandbox_image``, which must be an immutable reference (§7.2)."""
    image = str(getattr(settings, "sandbox_image", "") or "").strip()
    if not image:
        raise SandboxUnavailable("No sandbox image is set, so no sandbox can start.")
    if not (_DIGEST_REF.match(image) or _IMAGE_ID.match(image)):
        raise SandboxRefused(
            "The sandbox image must be pinned by a digest or an image id, not a tag."
        )
    return image


def sandbox_env(thread_hash: str) -> dict[str, str]:
    """The whole container environment. It holds no secret (§7.1 rule 6)."""
    local = f"{WORKSPACE_TARGET}/.local/{thread_hash}"
    return {
        "HOME": "/tmp",
        "PIP_USER": "1",
        "PIP_CACHE_DIR": "/tmp/pip-cache",
        "PYTHONUSERBASE": local,
        "PATH": f"{SYSTEM_PATH}:{local}/bin",
    }


# ── Egress: the settings, the names and the proxy argv (WS-43g1, §7.3) ───────


def egress_enabled(settings: Any) -> bool:
    """``sandbox_egress_enabled``. Off by default, and off on a bad value."""
    return getattr(settings, "sandbox_egress_enabled", False) is True


def egress_allow_entries(settings: Any) -> tuple[str, ...]:
    """The entries of ``sandbox_egress_allow_hosts``, lower case, in order."""
    raw = str(getattr(settings, "sandbox_egress_allow_hosts", "") or "")
    entries: list[str] = []
    for part in raw.split(","):
        entry = part.strip().lower()
        if entry and entry not in entries:
            entries.append(entry)
    return tuple(entries)


def egress_door_upstream(settings: Any) -> str:
    """``sandbox_egress_door_upstream`` when it is ``<ip>:<port>``, else ``""``.

    A name gives ``""``: the proxy does no DNS lookup for the door
    (done-when 9), so only an address can be the upstream.
    """
    raw = str(getattr(settings, "sandbox_egress_door_upstream", "") or "").strip()
    if not raw:
        return ""
    import ipaddress

    if raw.startswith("["):
        host, sep, port = raw[1:].partition("]:")
    else:
        host, sep, port = raw.rpartition(":")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return ""
    if not sep or not port.isdigit() or not 0 < int(port) < 65536:
        return ""
    return raw


def egress_network_name(org: str, container: str, key: str) -> str:
    """``mtr-egress-`` and 16 hex digits of the org, the container and the key.

    One network for each grant (WS-43g2a). *key* is :attr:`EgressGrant.key`,
    the SHA-256 of the token, so the name holds no token.
    """
    return EGRESS_NET_PREFIX + _digest(org, container, key)


def egress_bridge_name(org: str, container: str, key: str) -> str:
    """``mtr-eg-`` and 8 hex digits of the same digest: 15 characters, the
    Linux limit. Two live grants with the same 8 digits fail closed, because
    ``docker network create`` refuses a bridge name that is in use."""
    return EGRESS_BRIDGE_PREFIX + _digest(org, container, key, length=8)


def door_read_timeout(settings: Any) -> int:
    """The door read of the proxy: the card wait plus 30 s (§7.3, "The door wait").

    SBX-3a (task 3802) adds ``sandbox_approval_timeout_seconds``. Until then
    the default of 300 holds.
    """
    wait = getattr(settings, "sandbox_approval_timeout_seconds", 300)
    try:
        return int(wait) + DOOR_READ_TIMEOUT_EXTRA_S
    except (TypeError, ValueError):
        return 0  # below the floor, so the proxy never starts


def egress_proxy_source() -> Path:
    """The proxy script in the repo. The broker copies it into its state dir."""
    return Path(__file__).resolve().parent.parent / "sandbox" / "egress_proxy.py"


def check_grant_hosts(hosts: Sequence[str], settings: Any) -> tuple[str, ...]:
    """The hosts of a grant, each an entry of the allowlist. Raises SandboxRefused.

    A grant names an entry of ``sandbox_egress_allow_hosts`` exactly, so a
    grant can never be wider than the list. The door name passes only while
    ``sandbox_egress_door_upstream`` holds an address.
    """
    if isinstance(hosts, str) or not hosts:
        raise SandboxRefused("A grant names at least one host.")
    allowed = egress_allow_entries(settings)
    names: list[str] = []
    for raw in hosts:
        name = str(raw).strip().lower()
        if name == DOOR_HOST:
            if not egress_door_upstream(settings):
                raise SandboxRefused(
                    "The door has no upstream address on this platform, so no grant names it."
                )
        elif name not in allowed:
            raise SandboxRefused(f"The host {name[:100]!r} is not on the egress allowlist.")
        if name not in names:
            names.append(name)
    if len(names) > EGRESS_MAX_HOSTS:
        raise SandboxRefused(f"A grant names at most {EGRESS_MAX_HOSTS} hosts.")
    return tuple(names)


@dataclass(frozen=True)
class EgressGrant:
    """One approved grant (§7.3, "The grant"). The token never reaches a log."""

    token: str = field(repr=False)
    org: str
    run: str
    agent: str
    approver: str
    hosts: tuple[str, ...]
    expires_at: float
    network: str

    @property
    def key(self) -> str:
        """The SHA-256 of the token. The grants file holds this, never the token."""
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()

    def record(self) -> dict[str, Any]:
        return {
            "org": self.org, "run": self.run, "agent": self.agent,
            "approver": self.approver, "hosts": list(self.hosts),
            "expires_at": self.expires_at,
        }


def egress_env(grant: EgressGrant) -> dict[str, str]:
    """The proxy settings of a granted container. They carry the grant token.

    Both cases, because curl reads only the lower-case ``http_proxy``. They
    never go in the argv: :func:`write_egress_env_file` puts them in a file
    that ``docker run --env-file`` reads (review round 1, 2026-11-26).
    """
    url = f"http://grant:{grant.token}@{EGRESS_PROXY_NAME}:{EGRESS_PROXY_PORT}"
    return {"HTTP_PROXY": url, "HTTPS_PROXY": url, "http_proxy": url, "https_proxy": url}


def write_egress_env_file(folder: Path, start_id: str, grant: EgressGrant) -> Path:
    """Write the proxy settings for ONE start, mode 0600, in a 0700 folder.

    The token then stays out of the ``docker run`` argv, which any user of
    the host can read in the process list. The caller removes the file once
    ``docker run`` returns.
    """
    if not re.fullmatch(r"[0-9a-f]{32}", start_id):
        raise SandboxRefused("The start id is not valid.")
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    path = folder / f"{start_id}.env"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        for key, value in egress_env(grant).items():
            handle.write(f"{key}={value}\n")
    return path


def build_proxy_argv(
    *, image: str, bin_dir: Path, grants_dir: Path, door_read_timeout_s: int,
) -> list[str]:
    """The ``docker run`` arguments of the proxy (§7.3, "The proxy").

    uid 65534, a read-only root, no capability, 128 MiB and 64 pids, on the
    default bridge. No ``-p``: only a container on an egress network reaches
    it. Both mounts are read-only: the script, and the grants dir.
    *door_read_timeout_s* is the upstream read of the door (WS-43g2a).
    """
    return [
        "run", "-d", "--rm", "--name", EGRESS_PROXY_NAME,
        "--label", f"{LABEL_SANDBOX}=1",
        "--label", f"{LABEL_EGRESS}=proxy",
        "--network", "bridge",
        # Defence in depth: the proxy never routes a packet between its
        # networks, so a sandbox reaches only the proxy's own port.
        "--sysctl", "net.ipv4.ip_forward=0",
        "--sysctl", "net.ipv6.conf.all.forwarding=0",
        "--read-only",
        "--user", f"{EGRESS_PROXY_UID}:{EGRESS_PROXY_UID}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--init",
        "--cpus", "1",
        "--memory", "128m",
        "--memory-swap", "128m",
        "--pids-limit", "64",
        *Mount(bin_dir, EGRESS_BIN_TARGET, readonly=True).args(),
        *Mount(grants_dir, EGRESS_GRANTS_TARGET, readonly=True).args(),
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        image,
        "python3", "-I", f"{EGRESS_BIN_TARGET}/egress_proxy.py",
        "--grants", f"{EGRESS_GRANTS_TARGET}/{EGRESS_GRANTS_FILE}",
        "--listen", f"0.0.0.0:{EGRESS_PROXY_PORT}",
        "--door-read-timeout", str(door_read_timeout_s),
    ]


def proxy_door_read_timeout(cmd: Sequence[str]) -> str | None:
    """The ``--door-read-timeout`` value in the command of a running proxy."""
    items = list(cmd)
    for i, item in enumerate(items[:-1]):
        if item == "--door-read-timeout":
            return str(items[i + 1])
    return None


def _check_run_inputs(
    *,
    binding: RunBinding,
    mounts: Sequence[Mount],
    uid: int,
    gid: int,
    start_id: str,
    runtime: str,
    workspace_tmpfs_mb: int | None,
    egress: EgressGrant | None = None,
) -> None:
    """Refuse the inputs of :func:`build_run_argv` that no start may use."""
    if egress is not None and (
        binding.target not in EGRESS_TARGETS or workspace_tmpfs_mb is not None
        or egress.org != binding.org
        or egress.network != egress_network_name(binding.org, binding.name, egress.key)
    ):
        raise SandboxRefused("This container cannot take an egress grant.")
    if uid == 0 or gid == 0:
        raise SandboxRefused("The sandbox never runs as uid 0 or gid 0.")
    if not re.fullmatch(r"[0-9a-f]{32}", start_id):
        raise SandboxRefused("The start id is not valid.")
    if runtime not in RUNTIMES:
        raise SandboxRefused("The sandbox runtime is not known, so no sandbox starts.")
    if workspace_tmpfs_mb is not None and (mounts or workspace_tmpfs_mb <= 0):
        raise SandboxRefused(
            "A remote sandbox takes no bind mount and needs a workspace size."
        )
    if binding.target == COMPUTE_TARGET:
        if mounts:
            raise SandboxRefused("A compute container takes no mount.")
        if workspace_tmpfs_mb is not None:
            # A compute container has its own /workspace tmpfs. Two would clash.
            raise SandboxRefused("A compute container takes no second workspace.")


def build_run_argv(
    *,
    binding: RunBinding,
    mounts: Sequence[Mount],
    uid: int,
    gid: int,
    image: str,
    limits: Limits,
    start_id: str,
    runtime: str = RUNTIME_RUNC,
    workspace_tmpfs_mb: int | None = None,
    egress: EgressGrant | None = None,
    egress_env_file: Path | None = None,
) -> list[str]:
    """The ``docker run`` arguments, with no ``docker`` in front.

    ``--read-only`` always comes with the ``/tmp`` tmpfs. The image sets
    ``HOME=/tmp``, and tools such as matplotlib fail with no writable home.

    *runtime* ``runsc`` adds ``--runtime=runsc`` (SBX-8). ``runc`` adds no
    flag, and any other value refuses. *workspace_tmpfs_mb* is the remote
    backend (SBX-15): ``/workspace`` is a tmpfs of that size, owned by the
    container's user, and no bind mount may come with it.

    *egress* (WS-43g1, §7.3) puts the container on the ``--internal``
    network of its own grant in place of ``none`` (WS-43g2a). The proxy settings with the
    grant token come from *egress_env_file* (``--env-file``), never from the
    argv. Every other flag and mount stays the same.
    """
    if (egress is None) != (egress_env_file is None):
        raise SandboxRefused("An egress grant and its env file come together.")
    _check_run_inputs(
        binding=binding, mounts=mounts, uid=uid, gid=gid, start_id=start_id,
        runtime=runtime, workspace_tmpfs_mb=workspace_tmpfs_mb, egress=egress,
    )
    compute = binding.target == COMPUTE_TARGET
    if compute:
        limits = Limits(COMPUTE_CPUS, COMPUTE_MEMORY, COMPUTE_PIDS, limits.tmpfs_mb)
    argv = ["run", "-d", "--rm", "--name", binding.name]
    for key, value in {**binding.labels(), LABEL_START: start_id}.items():
        argv += ["--label", f"{key}={value}"]
    if runtime == RUNTIME_RUNSC:
        argv.append("--runtime=runsc")
    argv += [
        "--network", egress.network if egress is not None else "none",
        "--read-only",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,size={limits.tmpfs_mb}m",
        *(
            ["--tmpfs", f"{COMPUTE_WORKSPACE_TMPFS},uid={uid},gid={gid},mode=0700"]
            if compute else []
        ),
        "--user", f"{uid}:{gid}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--init",
        "--cpus", limits.cpus,
        "--memory", limits.memory,
        "--memory-swap", limits.memory,
        "--pids-limit", str(limits.pids),
        "--workdir", WORKSPACE_TARGET,
    ]
    if workspace_tmpfs_mb is not None:
        # `exec`: a tmpfs is noexec by default, and the bind mount it stands
        # in for is not. A script in `.local/<thread>/bin` must still run.
        argv += [
            "--tmpfs",
            f"{WORKSPACE_TARGET}:rw,nosuid,nodev,exec,size={workspace_tmpfs_mb}m,"
            f"uid={uid},gid={gid},mode=0700",
        ]
    for mount in mounts:
        argv += mount.args()
    env = sandbox_env(binding.thread_hash)
    if compute:
        # No user site, no pip, no `.local`: the only code is the stdlib and
        # the image's own packages. No secret is here.
        env = {
            "HOME": "/tmp",
            "PATH": SYSTEM_PATH,
            "MPLBACKEND": "Agg",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
        }
    if binding.target == PROJECTS_TARGET:
        # Every thread of one organization mounts the same working dir, so
        # another thread could plant a package in this thread's `.local`
        # user site, and a script here would import it beside this run's own
        # data. This track has no network and so no install: no user site.
        env["PYTHONNOUSERSITE"] = "1"
        # No implicit import from the cwd (`/workspace`) or a script's dir:
        # `python3 -c` and `python3 x.py` put only the system paths on
        # sys.path, so a module that another member's run wrote in a shared
        # folder is never imported by name (review P1, fix round 1).
        env["PYTHONSAFEPATH"] = "1"
    if binding.target == APP_BUILDER_TARGET:
        # WS-43h (§7.8): `build_t2.mjs` reads the vendor cache here. The
        # value is the fixed container path of the read-only mount, never a
        # host path, and it holds no secret. `@cc/ui` is in the build copy.
        env[T2_VENDOR_ENV] = T2_VENDOR_TARGET
        env[T2_UI_ENV] = f"{APP_BUILDER_BUILD_TARGET}/artifactUi.tsx"
    for key, value in env.items():
        argv += ["--env", f"{key}={value}"]
    if egress_env_file is not None:
        argv += ["--env-file", str(egress_env_file)]
    return [*argv, image, *KEEPALIVE]


def _process_ids() -> tuple[int, int]:
    """The uid and gid of the gateway process, which the container runs as.

    The files on the bind mount then keep the gateway user as owner. Windows
    has no uid, so a dev box gets 1000:1000, and Docker Desktop maps the file
    owners itself. Production is Linux.
    """
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return 1000, 1000
    return int(getuid()), int(getgid())


# ── Exec scripts (§7.1 rule 9) ───────────────────────────────────────────────

#: Runs one command: ``timeout -s KILL`` around ``bash -o pipefail``, with the
#: command's stderr on stdout. ``$1`` is the timeout and ``$2`` the command, so
#: the command is one argv entry and no host shell ever reads it.
EXEC_WRAPPER = 'exec timeout -s KILL "$1" bash -o pipefail -c "$2" 2>&1'

#: Prints every PID except PID 1 and itself. At start, the one PID it prints
#: is the keep-alive.
PID_PROBE = (
    'self=$$; for p in /proc/[0-9]*; do pid=${p#/proc/}; '
    'if [ "$pid" != 1 ] && [ "$pid" != "$self" ]; then echo "$pid"; fi; done'
)

#: Kills every process except init, the keep-alive and itself, in ANY state,
#: then checks again. A child that called ``setsid`` or forked twice dies too,
#: because the sweep walks ``/proc`` and not a process group.
#:
#: ⚠️ A PID counts as gone only when EVERY task under ``/proc/<pid>/task`` is a
#: zombie (Z) or dead (X). A leader that left by ``syscall(SYS_exit)`` shows Z
#: while a worker thread still runs, so a check of the leader alone lets the
#: worker write after the sweep said "clean" (review P1-a).
#: Exit 0 prints ``clean``. Exit 3 prints the PIDs that survived.
KILL_SWEEP = """keep="$1"; self=$$; n=0
while :; do
  left=""
  for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    case "$pid" in 1|"$keep"|"$self") continue ;; esac
    kill -9 "$pid" 2>/dev/null
    for t in "$p"/task/[0-9]*; do
      st=""
      while read -r k v _; do
        if [ "$k" = "State:" ]; then st="$v"; break; fi
      done 2>/dev/null < "$t/status"
      if [ -n "$st" ] && [ "$st" != "Z" ] && [ "$st" != "X" ]; then
        left="$left $pid"
        break
      fi
    done
  done
  if [ -z "$left" ]; then echo clean; exit 0; fi
  n=$((n+1))
  if [ "$n" -ge 40 ]; then echo "survivors:$left"; exit 3; fi
  sleep 0.05
done"""


# ── The docker CLI: the ONE place a docker process starts (WS43-F1) ─────────


@dataclass(frozen=True)
class DockerResult:
    rc: int
    stdout: str
    stderr: str
    #: SBX-15c: the CLI ran past its timeout and the broker killed it. The
    #: daemon may still finish the request.
    timed_out: bool = False


@dataclass(frozen=True)
class StreamResult:
    rc: int | None
    output: str
    total_bytes: int
    truncated: bool
    stderr: str
    host_timed_out: bool


@dataclass(frozen=True)
class CollectResult:
    rc: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    truncated: bool


class CappedOutput:
    """Keeps the first *head* bytes and the last *tail* bytes of a stream.

    The memory it holds is bounded, whatever the command prints.
    """

    def __init__(self, head: int, tail: int) -> None:
        self._head_cap, self._tail_cap = max(0, head), max(0, tail)
        self._head = bytearray()
        self._tail = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self._head_cap - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        if chunk and self._tail_cap:
            self._tail += chunk
            if len(self._tail) > self._tail_cap:
                del self._tail[: len(self._tail) - self._tail_cap]

    @property
    def truncated(self) -> bool:
        return self.total > len(self._head) + len(self._tail)

    def text(self) -> str:
        head = self._head.decode("utf-8", errors="replace")
        tail = self._tail.decode("utf-8", errors="replace")
        if not self.truncated:
            return head + tail
        marker = (
            f"\n... [output cut: {self.total} bytes in total. This shows the first "
            f"{len(self._head)} and the last {len(self._tail)} bytes] ...\n"
        )
        return head + marker + tail


#: The ``stdin`` value that asks ``DockerCLI._spawn`` for a pipe, which
#: :meth:`DockerCLI.transfer` then feeds itself.
_OPEN_STDIN = b""


async def _feed_stdin(
    writer: asyncio.StreamWriter | None, source: bytes | BinaryIO | None,
) -> None:
    """Write *source* to the stdin of a ``docker`` process in chunks, then close it.

    A process that ended early breaks the pipe. That stops the feed, and the
    process's own rc tells the caller.
    """
    if writer is None:
        return
    try:
        if isinstance(source, bytes | bytearray | memoryview):
            view = memoryview(source)
            for start in range(0, len(view), _READ_CHUNK):
                writer.write(bytes(view[start:start + _READ_CHUNK]))
                await writer.drain()
        elif source is not None:
            while chunk := await asyncio.to_thread(source.read, _READ_CHUNK):
                writer.write(chunk)
                await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


class DockerCLI:
    """Runs the ``docker`` CLI. The only process starts in this module.

    A timeout OR a cancel kills the CLI process and reaps it, so a cancelled
    run never leaves a ``docker`` process behind (review P1-b).
    """

    def _binary(self) -> str:
        path = shutil.which("docker")
        if not path:
            raise SandboxUnavailable("Docker is not available on this box.")
        return path

    async def _spawn(
        self, args: Sequence[str], stdin: bytes | None = None,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            self._binary(), *args,
            stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # SBX-15: DOCKER_HOST of the remote backend, for this child only. A
            # bad backend setting raises SandboxUnavailable before any start.
            env=docker_env(extra=_daemon_names()),
        )

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(BaseException):
            await asyncio.shield(proc.wait())

    async def run(
        self, args: Sequence[str], *, timeout: float, stdin: bytes | None = None,
    ) -> DockerResult:
        """Run one short docker command to its end. *stdin* feeds ``exec -i``."""
        proc = await (self._spawn(args) if stdin is None else self._spawn(args, stdin))
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
        except TimeoutError:
            await self._kill(proc)
            return DockerResult(
                -1, "", f"docker {args[0] if args else ''} timed out", timed_out=True,
            )
        except BaseException:
            await self._kill(proc)
            raise
        return DockerResult(
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", errors="replace"),
            _scrub(err.decode("utf-8", errors="replace")),
        )

    async def collect(
        self, args: Sequence[str], *, timeout: float, cap: int,
    ) -> CollectResult:
        """Run a docker command and keep up to *cap* raw bytes of each stream.

        The memory it holds is bounded. A stream past the cap reads on and
        drops the rest, and ``truncated`` says so. SBX-18 uses it.
        """
        proc = await self._spawn(args)
        out, err = bytearray(), bytearray()
        total = [0, 0]

        async def pump(reader: asyncio.StreamReader | None, sink: bytearray, idx: int) -> None:
            if reader is None:
                return
            while chunk := await reader.read(_READ_CHUNK):
                total[idx] += len(chunk)
                room = cap - len(sink)
                if room > 0:
                    sink.extend(chunk[:room])

        timed_out = False
        rc: int | None
        try:
            await asyncio.wait_for(
                asyncio.gather(pump(proc.stdout, out, 0), pump(proc.stderr, err, 1)),
                timeout=timeout,
            )
            rc = await proc.wait()
        except TimeoutError:
            timed_out = True
            await self._kill(proc)
            rc = None
        except BaseException:
            await self._kill(proc)
            raise
        # SBX-15c: the compute run gives this stderr to the model. A dropped
        # ssh link puts the daemon address in it, so it is scrubbed here.
        return CollectResult(
            rc=rc, stdout=bytes(out),
            stderr=_scrub(err.decode("utf-8", errors="replace"), model=True).encode("utf-8"),
            timed_out=timed_out,
            truncated=total[0] > cap or total[1] > cap,
        )

    async def stream(
        self, args: Sequence[str], *, timeout: float, head: int, tail: int,
    ) -> StreamResult:
        """Run ``docker exec`` and keep a capped copy of its output."""
        proc = await self._spawn(args)
        capped = CappedOutput(head, tail)
        err = bytearray()

        async def pump(reader: asyncio.StreamReader | None, sink: Callable[[bytes], None]) -> None:
            if reader is None:
                return
            while chunk := await reader.read(_READ_CHUNK):
                sink(chunk)

        def keep_err(chunk: bytes) -> None:
            if len(err) < _STDERR_KEEP:
                err.extend(chunk[: _STDERR_KEEP - len(err)])

        host_timed_out = False
        try:
            await asyncio.wait_for(
                asyncio.gather(pump(proc.stdout, capped.feed), pump(proc.stderr, keep_err)),
                timeout=timeout,
            )
            rc: int | None = await proc.wait()
        except TimeoutError:
            host_timed_out = True
            await self._kill(proc)
            rc = None
        except BaseException:
            await self._kill(proc)
            raise
        return StreamResult(
            rc=rc,
            output=capped.text(),
            total_bytes=capped.total,
            truncated=capped.truncated,
            stderr=_scrub(err.decode("utf-8", errors="replace")),
            host_timed_out=host_timed_out,
        )

    async def transfer(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        source: bytes | BinaryIO | None = None,
        sink: BinaryIO | None = None,
        limit: int = 0,
    ) -> DockerResult:
        """Run one ``docker exec`` that moves bytes, as raw bytes (SBX-15).

        *source* goes to the stdin of the process (``docker exec -i``), from
        bytes or a binary file in chunks. Its stdout goes to *sink*, and more
        than *limit* bytes kills the process and answers rc ``-1``. The
        ``DockerResult`` holds no stdout. A timeout or a cancel kills the CLI
        process and reaps it, as :meth:`run` does.
        """
        # Any bytes value opens the stdin pipe. `_feed_stdin` then writes the
        # source in chunks, so a large copy never sits in memory twice.
        proc = await self._spawn(args, None if source is None else _OPEN_STDIN)
        err = bytearray()
        over = False

        async def take() -> None:
            nonlocal over
            reader = proc.stdout
            if reader is None:
                return
            total = 0
            while chunk := await reader.read(_READ_CHUNK):
                total += len(chunk)
                if limit and total > limit:
                    over = True
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    return
                if sink is not None:
                    await asyncio.to_thread(sink.write, chunk)

        async def keep_err() -> None:
            reader = proc.stderr
            if reader is None:
                return
            while chunk := await reader.read(_READ_CHUNK):
                if len(err) < _STDERR_KEEP:
                    err.extend(chunk[: _STDERR_KEEP - len(err)])

        try:
            await asyncio.wait_for(
                asyncio.gather(_feed_stdin(proc.stdin, source), take(), keep_err()),
                timeout=timeout,
            )
            rc = await proc.wait()
        except TimeoutError:
            await self._kill(proc)
            return DockerResult(
                -1, "", f"docker {args[0] if args else ''} timed out", timed_out=True,
            )
        except BaseException:
            await self._kill(proc)
            raise
        if over:
            await self._kill(proc)
            return DockerResult(-1, "", f"the output of docker is over its limit of {limit} bytes")
        return DockerResult(
            rc if rc is not None else -1, "", _scrub(err.decode("utf-8", errors="replace")),
        )


# ── Handles and results ──────────────────────────────────────────────────────


@dataclass(eq=False)
class SandboxHandle:
    """One container of the bound run. Get it from :meth:`SandboxBroker.acquire`.

    ``lock`` belongs to the MOUNT SOURCE, not to the container. Two threads of
    one organization on one shared agent mount the same ``o:<org>`` dir in two
    containers, and they share one lock (review P2-b). ``ready`` is set when
    the start ends, with success or not.
    """

    binding: RunBinding
    lock: asyncio.Lock
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    leases: int = 0
    started_at: float = 0.0
    last_used: float = 0.0
    init_pid: int = 1
    keepalive_pid: int | None = None
    container_id: str = ""
    start_id: str = ""
    starting: bool = True
    failed: bool = False
    removed: bool = False
    #: The run that used this container ended and its run data is gone, so
    #: the ``/workspace/.run`` mount points at a deleted dir. The next acquire
    #: of the thread starts a fresh container (§16.3, data hygiene).
    stale: bool = False
    over_quota: bool = False
    workspace_bytes: int = 0
    workspace_files: int = 0
    network: str = "none"
    #: WS-43g1: the live grant of this container, or ``None``. A start and a
    #: restart put the container on the grant's network (§7.1 rule 11).
    egress: EgressGrant | None = None
    #: SBX-15: the container runs on the remote daemon, with no mount. Each
    #: exec copies the working dir in first and copies it back out after.
    remote: bool = False
    #: The files of the working dir that the last copy in sent, relative and
    #: with ``/``. The copy out deletes on the host only a file of this set
    #: that the container removed.
    pushed: frozenset[str] = frozenset()
    #: The names that the last copy in did not send (:func:`unsent_note`).
    unsent: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.binding.name

    @property
    def org(self) -> str:
        return self.binding.org

    @property
    def agent(self) -> str:
        return self.binding.agent

    @property
    def workspace(self) -> Path:
        return self.binding.workspace


@dataclass(frozen=True)
class ExecResult:
    """What one exec gives back. ``output`` is capped (§7.1 rule 9)."""

    exit_code: int | None
    output: str
    output_bytes: int
    truncated: bool
    duration_s: float
    timed_out: bool = False
    restarted: bool = False
    message: str = ""


# ── The remote copy (SBX-15) ─────────────────────────────────────────────────
#
# A remote daemon cannot see a path of this box. So before each command the
# broker sends the working dir as a tar on the stdin of ``docker exec -i``
# (:data:`PUSH_SCRIPT`), and after the command it reads ``/workspace`` back as
# a tar on the stdout of ``docker exec`` (:data:`PULL_SCRIPT`). Both run as
# the container's own user, under the dir lock of the exec. The archive that
# comes back is input from the container: it takes regular files and dirs
# only, with clean names, within the size caps, and each host write goes
# through the safe opener. Nothing is written until the whole archive parsed.

_TRANSFER_TIMEOUT_SECONDS = 120.0
#: A copy holds this much in memory, and spills to a temp file in the
#: broker's state dir past it.
_SPOOL_MEMORY = 8 * _MIB


@dataclass(frozen=True)
class PullResult:
    """What one copy out changed on the host."""

    written: int
    deleted: int
    skipped: int


@dataclass(frozen=True)
class HostTree:
    """The entries of a working dir that the copy in may send, and the rest."""

    dirs: list[str]
    files: list[str]
    #: Names that the copy in does not send, because :func:`_pulled_name`
    #: cannot read them back the same: a backslash, or bytes that are not
    #: UTF-8. A dir here takes its whole subtree with it.
    unsent: list[str]


def _host_tree(workspace: Path, *, max_entries: int) -> HostTree:
    """The dirs and files of the working dir, relative with ``/``, sorted.

    No link is followed or listed, and a ``.git`` at any depth stays out: no
    cover hides it on this backend, and it can hold a token. A name that the
    copy out would not read back as itself is not sent (review P1). Else
    ``a\\b`` reads the bytes of ``a/b``, and the copy out deletes ``a/b``.
    More than *max_entries* raises :class:`SandboxRefused`.
    """
    dirs: list[str] = []
    files: list[str] = []
    unsent: list[str] = []
    for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
        here = Path(dirpath)
        keep: list[str] = []
        for name in dirnames:
            path = here / name
            if name == ".git" or _is_link(path):
                continue
            rel = path.relative_to(workspace).as_posix()
            if _pulled_name(rel) != rel:
                unsent.append(rel)
                continue
            keep.append(name)
            dirs.append(rel)
        dirnames[:] = sorted(keep)
        for name in filenames:
            if name == ".git":
                continue
            path = here / name
            rel = path.relative_to(workspace).as_posix()
            if _pulled_name(rel) != rel:
                unsent.append(rel)
                continue
            try:
                mode = os.lstat(path).st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode):
                files.append(rel)
        if len(dirs) + len(files) > max_entries:
            raise SandboxRefused(
                f"The working dir holds more than {max_entries} entries, so the "
                "remote sandbox cannot take it."
            )
    return HostTree(sorted(dirs), sorted(files), sorted(unsent))


def shown_name(rel: str) -> str:
    """*rel* as printable text: a byte that is not UTF-8 shows as ``\\xNN``."""
    raw = rel.encode("utf-8", errors="surrogateescape")
    return raw.decode("utf-8", errors="backslashreplace")[:120]


def unsent_note(unsent: Sequence[str]) -> str:
    """The line that tells the model which names the copy in did not send."""
    if not unsent:
        return ""
    names = ", ".join(shown_name(rel) for rel in unsent[:5])
    more = f" and {len(unsent) - 5} more" if len(unsent) > 5 else ""
    return (
        f"{len(unsent)} name(s) of the working dir did not go into the sandbox, "
        "because each holds a backslash or a byte that is not UTF-8. Rename "
        f"them to use them: {names}{more}."
    )


@dataclass(frozen=True)
class PushResult:
    """What one copy in sent, and what it did not send."""

    sent: frozenset[str]
    unsent: tuple[str, ...]


def write_push_archive(
    workspace: Path, sink: BinaryIO, *, max_bytes: int, max_entries: int,
) -> PushResult:
    """Write the tar of the copy in to *sink*. Returns what it holds.

    Each file is read with the safe opener. A file over the size cap of the
    remote workspace raises :class:`SandboxRefused`, so no command runs on a
    part of the dir.
    """
    from acb_skills import safe_open

    tree = _host_tree(workspace, max_entries=max_entries)
    dirs, files = tree.dirs, tree.files
    sent: set[str] = set()
    total = 0
    # GNU format: each name goes as its raw UTF-8 bytes (see PULL_SCRIPT).
    with tarfile.open(fileobj=sink, mode="w", format=tarfile.GNU_FORMAT, encoding="utf-8") as tar:
        for rel in dirs:
            info = tarfile.TarInfo(rel)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
        for rel in files:
            try:
                fh = safe_open.open_read(workspace, rel)
            except (safe_open.UnsafePath, OSError):
                continue
            if fh is None:
                continue
            with fh:
                st = os.fstat(fh.fileno())
                total += st.st_size
                if total > max_bytes:
                    raise SandboxRefused(
                        f"The working dir holds more than {max_bytes // _MIB} MB, the "
                        "size of the remote sandbox workspace. Delete files, then "
                        "run the command again."
                    )
                info = tarfile.TarInfo(rel)
                info.size = st.st_size
                info.mtime = int(st.st_mtime)
                info.mode = 0o755 if st.st_mode & stat.S_IXUSR else 0o644
                tar.addfile(info, fh)
            sent.add(rel)
    return PushResult(frozenset(sent), tuple(tree.unsent))


def _pulled_name(name: str) -> str | None:
    """The clean relative name of a member of the copy out, or ``None``.

    ``None`` for the root ``.``, a name with ``..``, an empty part, a
    backslash, a NUL, bytes that are not UTF-8, and a ``.git`` part.
    """
    text = name
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    if not text or text == "." or text.startswith("/"):
        return None
    if "\\" in text or "\x00" in text:
        return None
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None
    parts = text.split("/")
    if any(p in ("", ".", "..", ".git") for p in parts):
        return None
    return text


def _put_dir(workspace: Path, rel: str, pushed: frozenset[str]) -> None:
    """Make the dir *rel*. A host file in its way goes only when the copy in sent it.

    Any other file in the way raises ``FileExistsError``, so the caller skips
    the entry and deletes nothing that the copy in did not send.
    """
    from acb_skills import safe_open

    parts = rel.split("/")
    for depth in range(1, len(parts) + 1):
        here = "/".join(parts[:depth])
        path = workspace / here
        if path.exists() and not path.is_dir() and not _is_link(path):
            if here not in pushed:
                raise FileExistsError(here)
            safe_open.unlink(workspace, here)
    safe_open.ensure_dir(workspace, rel)


def _member_chunks(tar: tarfile.TarFile, member: tarfile.TarInfo) -> Iterable[bytes]:
    """The bytes of *member* in chunks. A short member raises :class:`SandboxRefused`."""
    fh = tar.extractfile(member)
    left = member.size
    while left > 0:
        chunk = fh.read(min(_READ_CHUNK, left)) if fh is not None else b""
        if not chunk:
            raise SandboxRefused("A file of the sandbox came back short.")
        left -= len(chunk)
        yield chunk


def _host_digest(workspace: Path, rel: str) -> tuple[int, str] | None:
    """``(size, sha256)`` of the host file *rel*, read in chunks, or ``None``."""
    from acb_skills import safe_open

    try:
        fh = safe_open.open_read(workspace, rel)
    except (safe_open.UnsafePath, OSError):
        return None
    if fh is None:
        return None
    digest = hashlib.sha256()
    size = 0
    with fh:
        while chunk := fh.read(_READ_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _put_file(
    workspace: Path, rel: str, tar: tarfile.TarFile, member: tarfile.TarInfo,
    pushed: frozenset[str],
) -> bool:
    """Write *member* at *rel* through the safe opener. ``False`` when it was the same.

    No file sits in memory whole (review P2). A first pass hashes the member
    in chunks, and a file with the same size and hash is left alone, so its
    mtime stays. A changed file streams into a temp file in its own dir, and
    a rename puts it in place (``safe_open.replace_stream``).

    A host dir at *rel* raises ``FileExistsError``: the copy out never
    removes a dir, so it deletes nothing that the copy in did not send.
    """
    from acb_skills import safe_open

    parent = rel.rpartition("/")[0]
    if parent:
        _put_dir(workspace, parent, pushed)
    path = workspace / rel
    if path.is_dir() and not _is_link(path):
        raise FileExistsError(rel)
    digest = hashlib.sha256()
    for chunk in _member_chunks(tar, member):
        digest.update(chunk)
    changed = _host_digest(workspace, rel) != (member.size, digest.hexdigest())
    if changed:

        def fill(fh: BinaryIO) -> None:
            for chunk in _member_chunks(tar, member):
                fh.write(chunk)

        safe_open.replace_stream(workspace, rel, fill)
    executable = bool(member.mode & 0o100)
    if os.name != "nt":
        fh = safe_open.open_read(workspace, rel)
        if fh is not None:
            with fh:
                mode = os.fstat(fh.fileno()).st_mode & 0o7777
                want = (mode | 0o100) if executable else (mode & ~0o111)
                if want != mode:
                    os.fchmod(fh.fileno(), want)
                    changed = True
    return changed


def _read_pull_members(
    tar: tarfile.TarFile, *, max_bytes: int, max_entries: int,
) -> tuple[list[str], list[tuple[str, tarfile.TarInfo]], int]:
    """``(dirs, files, skipped)`` of the whole archive, read before any write.

    More than *max_entries* entries or *max_bytes* of file data raises
    :class:`SandboxRefused`. A link, a device, a FIFO and a bad name count
    as skipped. A member of a short archive raises ``tarfile.ReadError``.
    """
    dirs: list[str] = []
    files: list[tuple[str, tarfile.TarInfo]] = []
    skipped = total = count = 0
    while (member := tar.next()) is not None:
        count += 1
        if count > max_entries:
            raise SandboxRefused("The sandbox gave back too many files.")
        rel = _pulled_name(member.name)
        if rel is None:
            if member.name.strip("./"):
                skipped += 1
        elif member.isdir():
            dirs.append(rel)
        elif member.isreg():
            total += member.size
            if total > max_bytes:
                raise SandboxRefused("The sandbox gave back more bytes than its workspace holds.")
            files.append((rel, member))
        else:
            skipped += 1
    return dirs, files, skipped


def apply_pull_archive(
    workspace: Path,
    source: BinaryIO,
    *,
    pushed: frozenset[str],
    max_bytes: int,
    max_entries: int,
) -> PullResult:
    """Apply the tar of the copy out to the working dir on the host.

    It reads the whole archive first: regular files and dirs only, with
    clean names, at most *max_bytes* of file data and *max_entries* entries.
    A link, a device, a FIFO and a bad name are skipped. Any error raises,
    and then nothing is written. Then each changed file goes through the
    safe opener, and a file that the copy in sent and the container removed
    is deleted. No other host file is deleted.
    """
    from acb_skills import safe_open

    with tarfile.open(
        fileobj=source, mode="r:", encoding="utf-8", errors="surrogateescape",
    ) as tar:
        dirs, files, skipped = _read_pull_members(
            tar, max_bytes=max_bytes, max_entries=max_entries,
        )
        written = 0
        for rel in dirs:
            try:
                _put_dir(workspace, rel, pushed)
            except (safe_open.UnsafePath, OSError):
                skipped += 1
        for rel, member in files:
            try:
                if _put_file(workspace, rel, tar, member, pushed):
                    written += 1
            except (safe_open.UnsafePath, OSError):
                skipped += 1
    deleted = 0
    # A sent file that is a dir in the container now went in `_put_dir`.
    present = {rel for rel, _member in files} | set(dirs)
    for rel in sorted(pushed - present):
        if _pulled_name(rel) != rel:
            # The second guard of review P1: a name that the copy out cannot
            # read back is never "removed by the container".
            skipped += 1
            continue
        try:
            if safe_open.unlink(workspace, rel):
                deleted += 1
        except (safe_open.UnsafePath, OSError):
            skipped += 1
    return PullResult(written=written, deleted=deleted, skipped=skipped)


# ── Host-side measures ───────────────────────────────────────────────────────


def _dir_usage(root: Path) -> tuple[int, int]:
    """``(bytes, entries)`` below *root*, with no link followed.

    ``bytes`` is the apparent size of every file. ``entries`` counts every
    file, dir and link, because each one takes an inode on the host.
    """
    total = entries_seen = 0
    stack = [root]
    while stack:
        here = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for entry in entries:
            entries_seen += 1
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total, entries_seen


def _free_disk_mb(path: Path) -> int:
    """Free space, in MiB, of the file system that holds *path*."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return int(shutil.disk_usage(probe).free // _MIB)


def _ensure_empty_dir(path: Path) -> Path:
    """*path* as an empty real dir. Content that appeared there is removed."""
    if _is_link(path) or (path.exists() and not path.is_dir()):
        raise SandboxUnavailable("The .git cover dir is not a real dir.")
    if path.is_dir() and any(path.iterdir()):
        shutil.rmtree(path)
    path.mkdir(exist_ok=True)
    return path.resolve()


def _ensure_empty_file(path: Path) -> Path:
    """*path* as an empty real file."""
    if _is_link(path) or (path.exists() and not path.is_file()):
        raise SandboxUnavailable("The .git cover file is not a real file.")
    if not path.exists() or path.stat().st_size:
        path.write_bytes(b"")
    return path.resolve()


def _require_thread_slug(binding: RunBinding) -> None:
    """Refuse a thread id that cannot name a folder the routes recognise (§16.3)."""
    try:
        binding.thread_slug  # noqa: B018 — the property raises on a bad id
    except ValueError as exc:
        raise SandboxRefused(
            "This thread's id cannot name a sandbox folder, so no sandbox starts."
        ) from exc


def _remove_run_data(rel: str) -> bool:
    """Delete one run-data dir with the safe opener. ``False`` when absent."""
    from acb_skills import safe_open

    root = _real_state_root()
    if not root.is_dir():
        return False
    return safe_open.remove_tree(root, rel)


def remove_all_run_data() -> bool:
    """Delete every run-data dir. The startup sweep calls it (§16.3, §7.1 rule 13)."""
    from acb_skills.agent_paths import RUN_DATA_DIR

    return _remove_run_data(RUN_DATA_DIR)


def check_not_nested(source: Path, sandbox_dirs: Iterable[Path]) -> None:
    """Refuse *source* when another sandbox dir is its ancestor or descendant.

    The link check of a source and the mount by Docker are two steps. A
    container that writes a parent or a child of another container's source
    can swap a part of that path for a link between the two steps, and the
    bind mount then follows the link to any host dir (review P1-c). The same
    dir for two containers is allowed: they share one lock (P2-b).
    """
    for other in sandbox_dirs:
        if other == source:
            continue
        if source.is_relative_to(other) or other.is_relative_to(source):
            raise SandboxRefused(
                "The working dir nests with another sandbox dir, so a container "
                "could swap a part of its path."
            )


@dataclass(frozen=True)
class ComputeResult:
    """What :func:`run_compute` gives back (SBX-18).

    ``exit_code`` is ``None`` when the run timed out or a kill ended it.
    ``stdout`` and ``stderr`` are raw text, capped by the broker at 256 KB.
    ``files`` are the validated outputs of ``/workspace/out``: flat name to bytes.
    """

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    files: dict[str, bytes]
    duration_ms: int


def build_compute_tar(files: dict[str, bytes]) -> bytes:
    """The in-memory tar of the inputs, with ``inputs/`` and ``out/`` dirs.

    Raises :class:`SandboxRefused` for a name outside ``main.py`` and
    ``inputs/tr_<8 hex>.txt``, or for an input past the size cap.
    """
    total = 0
    for name, data in files.items():
        if not isinstance(name, str) or not _COMPUTE_INPUT_NAME.fullmatch(name):
            raise SandboxRefused("A compute input name is not allowed.")
        if not isinstance(data, bytes):
            raise SandboxRefused("A compute input must be bytes.")
        total += len(data)
    if total > COMPUTE_MAX_INPUT_BYTES:
        raise SandboxRefused("The compute inputs are too large.")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for dirname in ("inputs", "out"):
            info = tarfile.TarInfo(dirname)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(info)
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(files[name]), 0o644
            tar.addfile(info, io.BytesIO(files[name]))
    return buf.getvalue()


def parse_compute_tar(raw: bytes) -> dict[str, bytes]:
    """The valid outputs of a ``tar -c`` stream, read in memory. Never extracts.

    Keeps a regular file with a flat name and a suffix of csv, png, json or
    txt. Skips every other member: a link, a dir, a nested name, ``..``, an
    absolute name. Keeps at most 5 files, 1 MB each and 3 MB in all. A stream
    that does not parse gives no file.
    """
    files: dict[str, bytes] = {}
    total = 0
    skipped = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            for member in tar:
                name = member.name[2:] if member.name.startswith("./") else member.name
                if name in ("", "."):
                    continue
                suffix = name.rpartition(".")[2].lower() if "." in name else ""
                if (
                    not member.isreg()
                    or not _COMPUTE_OUT_NAME.fullmatch(name)
                    or ".." in name
                    or suffix not in COMPUTE_OUT_SUFFIXES
                    or member.size > COMPUTE_MAX_FILE_BYTES
                    or len(files) >= COMPUTE_MAX_FILES
                    or total + member.size > COMPUTE_MAX_TOTAL_BYTES
                    or name in files
                ):
                    skipped += 1
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    skipped += 1
                    continue
                data = handle.read(COMPUTE_MAX_FILE_BYTES + 1)
                if len(data) != member.size:
                    skipped += 1
                    continue
                files[name] = data
                total += len(data)
    except (tarfile.TarError, EOFError, OSError, ValueError):
        _log.warning("sandbox_broker.compute_tar_unreadable", kept=len(files))
    if skipped:
        _log.info("sandbox_broker.compute_outputs_skipped", count=skipped)
    return files


def _oldest_idle(handles: Iterable[SandboxHandle]) -> SandboxHandle | None:
    idle = [
        h for h in handles
        if h.leases == 0 and not h.starting and not h.lock.locked()
    ]
    return min(idle, key=lambda h: h.last_used) if idle else None


# ── The broker ───────────────────────────────────────────────────────────────


class SandboxBroker:
    """Owns the Docker socket and every sandbox container (§7.1)."""

    def __init__(
        self,
        docker: DockerCLI | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._docker = docker or DockerCLI()
        self._clock = clock
        #: WS-43g1: the expiry of a grant is wall time, because the proxy is
        #: another process and reads it from the grants file.
        self._wall = wall_clock
        #: WS-43g1: every live grant, by the SHA-256 of its token.
        self._grants: dict[str, EgressGrant] = {}
        #: WS-43g1: one change at a time of the networks and the proxy.
        self._egress_lock = asyncio.Lock()
        #: WS-43g2a: the network of each forgotten grant, to the container
        #: that was on it. :meth:`_flush_networks` removes each one, so the
        #: live egress networks are never more than the live grants (§7.3).
        self._spent_networks: dict[str, str] = {}
        self._live: dict[str, SandboxHandle] = {}
        #: The ``compute`` containers (SBX-18). A separate registry, so the
        #: compute caps and the ``sandbox_max_*`` caps never share a count.
        self._compute_live: dict[str, SandboxHandle] = {}
        self._registry_lock = asyncio.Lock()
        self._dir_locks: dict[Path, asyncio.Lock] = {}
        self._dirs: set[Path] | None = None
        self._reaper: asyncio.Task[None] | None = None
        self._startup: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        #: Every run-data dir this process made, keyed by (working dir, thread),
        #: as a path relative to the real state root. ``end_run`` deletes it.
        self._run_data: dict[tuple[Path, str], str] = {}
        #: Docker health for ``covers()``: the last answer and when it came.
        self._docker_ok: bool | None = None
        self._docker_checked = 0.0
        self._probe: asyncio.Task[None] | None = None
        #: SBX-15: every remote start holds it from the one-org admit until
        #: `docker run` returns (:meth:`_start_gate`).
        self._remote_start_lock = asyncio.Lock()
        #: SBX-15: remote containers whose `docker rm -f` failed. The reaper
        #: runs while this holds an entry, and removes each orphan.
        self._remote_orphans: set[str] = set()
        #: SBX-15c: ``start:<id>`` entries of a ``docker run`` that timed out,
        #: and the clock time until which the reaper looks for that container.
        #: An empty lookup keeps such an entry until then.
        self._late_starts: dict[str, float] = {}

    # ── settings and state ──────────────────────────────────────────────────

    @staticmethod
    def _settings() -> Any:
        return get_settings()

    def state_dir(self) -> Path:
        """The broker's own dir. It must lie outside every mount root."""
        from acb_skills.agent_paths import state_root

        settings = self._settings()
        raw = str(getattr(settings, "sandbox_state_dir", "") or "").strip()
        path = Path(raw) if raw else Path(settings.agents_clone_dir) / "sandbox-broker"
        real = path.resolve()
        for root in (state_root(), _custom_apps_root()):
            real_root = root.resolve()
            if real == real_root or real.is_relative_to(real_root):
                raise SandboxUnavailable(
                    "The sandbox state dir lies inside a mount root, so the broker stops."
                )
        return path

    def _dir_list_file(self) -> Path:
        return self.state_dir() / "sandbox_dirs.txt"

    def git_cover(self) -> GitCover:
        """The empty dir and the empty file that cover ``/workspace/.git``."""
        base = self.state_dir()
        base.mkdir(parents=True, exist_ok=True)
        return GitCover(
            empty_dir=_ensure_empty_dir(base / "git-cover-dir"),
            empty_file=_ensure_empty_file(base / "git-cover-file"),
        )

    def _skill_covers_dir(self) -> Path:
        return self.state_dir() / "skill-covers"

    def skill_cover(self, binding: RunBinding) -> Path:
        """The cover of ``/workspace/agent-data/skills`` for one start (WS-43v).

        A dir that holds one EMPTY dir for each of the run member's own skill
        folders and nothing else. The own folders are mounted on those empty
        dirs. The dir is named by the container and the skill set, so a start
        never changes a cover that a running container mounts. The startup
        sweep removes every cover.

        Two first starts of one thread can build the same cover at once. Each
        builds a temp dir and renames it into place, so neither sees the
        other's half-made dir. Any file-system error is
        :class:`SandboxUnavailable`, which ``run_command`` reports as a failed
        sandbox and never as a crash.
        """
        try:
            return self._build_skill_cover(binding)
        except OSError as exc:
            _log.warning("sandbox_broker.skill_cover_failed", error=str(exc)[:200])
            raise SandboxUnavailable(
                "The cover of the skills folder could not be made. Try again."
            ) from exc

    def _build_skill_cover(self, binding: RunBinding) -> Path:
        base = self._skill_covers_dir()
        base.mkdir(parents=True, exist_ok=True)
        if _is_link(base):
            raise SandboxUnavailable("The skill cover dir is not a real dir.")
        path = base / _digest(binding.name, *binding.skills, length=24)
        want = set(binding.skills)

        def clean() -> bool:
            if _is_link(path) or (path.exists() and not path.is_dir()):
                raise SandboxUnavailable("A skill cover is not a real dir.")
            if not path.is_dir():
                return False
            have = {e.name: e for e in os.scandir(path)}
            return set(have) == want and all(
                e.is_dir(follow_symlinks=False) and not any(os.scandir(e.path))
                for e in have.values()
            )

        if clean():
            return path.resolve()
        tmp = base / f".tmp-{uuid.uuid4().hex}"
        tmp.mkdir()
        try:
            for name in binding.skills:
                (tmp / name).mkdir()
            if path.is_dir() and not clean():
                shutil.rmtree(path, ignore_errors=True)
            try:
                os.rename(tmp, path)
            except OSError:
                # Another start renamed its cover into place first.
                if not clean():
                    raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return path.resolve()

    def _lock_for(self, source: Path) -> asyncio.Lock:
        """The one lock of a mount source, shared by every container on it."""
        lock = self._dir_locks.get(source)
        if lock is None:
            lock = self._dir_locks[source] = asyncio.Lock()
        return lock

    def _spawn_background(self, coro: Any) -> asyncio.Task[Any]:
        """A task that a cancel of the caller does not stop."""
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def settle(self) -> None:
        """Wait for every background removal. Tests call it.

        It waits on the tasks that are still pending. A done task leaves the
        set in a loop callback, and ``gather`` of done tasks gives the loop no
        turn, so a loop on the set alone would spin.
        """
        while pending := [t for t in self._background if not t.done()]:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)

    # ── the sandbox-dir list (§7.1 rule 5, §7.5 rule A) ─────────────────────

    def _load_dirs(self) -> set[Path]:
        if self._dirs is None:
            try:
                text = self._dir_list_file().read_text(encoding="utf-8")
            except FileNotFoundError:
                text = ""
            self._dirs = {Path(line) for line in text.splitlines() if line.strip()}
        return self._dirs

    def _sandbox_dirs(self) -> set[Path]:
        """Every dir that a container mounts now, or mounted."""
        return set(self._load_dirs()) | {h.workspace for h in self._live.values()}

    def _record_dir(self, path: Path) -> None:
        """Append *path* to the list BEFORE a container mounts it."""
        dirs = self._load_dirs()
        if path in dirs:
            return
        listing = self._dir_list_file()
        listing.parent.mkdir(parents=True, exist_ok=True)
        with listing.open("a", encoding="utf-8") as fh:
            fh.write(f"{path}\n")
            fh.flush()
            os.fsync(fh.fileno())
        dirs.add(path)

    def trim_dir_list(self) -> int:
        """Keep only the listed dirs that still exist. Startup calls it."""
        listing = self._dir_list_file()
        self._dirs = None
        kept = sorted(str(d) for d in self._load_dirs() if d.is_dir())
        if listing.exists():
            tmp = listing.with_suffix(".tmp")
            tmp.write_text("".join(f"{d}\n" for d in kept), encoding="utf-8")
            os.replace(tmp, listing)
        self._dirs = {Path(d) for d in kept}
        return len(kept)

    def is_sandbox_dir(self, path: str | os.PathLike[str]) -> bool:
        """True when *path* lies in a dir that a container mounts now, or did.

        A path that does not resolve answers ``True``, so host git skips it.

        A dir list that cannot be read (WS-43e, review P2-1) answers ``True``
        while ``MAF_CODING_SCOPE`` names any target, so host git fails closed
        when a container may run. With an empty scope, no container can start
        (``acquire()`` refuses), so it answers ``False`` with an error line. A
        bad ``sandbox_state_dir`` then never stops the push guard and the
        commit scan of every run on a box that runs no sandbox.
        """
        try:
            real = Path(path).resolve()
        except (OSError, RuntimeError):
            return True
        try:
            dirs = self._sandbox_dirs()
        except (OSError, SandboxError) as exc:
            scoped = bool(str(getattr(self._settings(), "maf_coding_scope", "") or "").strip())
            _log.error(
                "sandbox_broker.dir_list_unreadable", error=str(exc)[:300], fail_closed=scoped,
            )
            return scoped
        return any(real == d or real.is_relative_to(d) for d in dirs)

    def refuse_if_sandbox_dir(self, path: str | os.PathLike[str]) -> None:
        """Raise when host git would run on a dir that a container could write."""
        if self.is_sandbox_dir(path):
            raise SandboxRefused(
                f"Host git refuses {str(path)[:200]!r}: a sandbox container "
                "mounts this dir, or mounted it."
            )

    # ── coverage (§7.7) ─────────────────────────────────────────────────────

    def covers(self, agent: str, org: str) -> bool:
        """True when every code path of *agent* in *org* runs in this broker.

        §7.7 and §16.3. The ``code_task`` and ``app_builder`` targets answer
        ``False`` for every agent until WS-43f is built. A report, a log line
        or a WS-3a check must not count an agent as covered only because its
        ``code_task`` runs in the broker.

        The ``projects`` target (projects-assistant, D86) is true when all
        three hold:

        1. ``MAF_CODING_SCOPE`` holds ``projects:<org>`` or ``projects:*``.
        2. The broker is healthy (:meth:`healthy`).
        3. The agent holds no shell tool outside the broker: the D85 seam
           withholds ``code_task``, ``run_script`` and ``install_dependency``
           (:func:`_host_shell_withheld`). A true answer here never gives them
           back (:func:`lifts_shell_block`).

        *org* is the run's own tenant, from the run binding. ``*`` is never an
        organization here, so it is never covered.
        """
        if not agent or not org or org == "*":
            return False
        if target_for_agent(agent) != PROJECTS_TARGET:
            return False
        if not maf_coding_scope_allows(PROJECTS_TARGET, org):
            return False
        if not self._target_runs_here(PROJECTS_TARGET):
            return False
        if not self.healthy():
            return False
        return _host_shell_withheld(agent)

    def _backend(self) -> str:
        """The backend of this broker now. Raises :class:`SandboxUnavailable`."""
        return sandbox_backend(self._settings())

    def _target_runs_here(self, target: str) -> bool:
        """False when the backend cannot run *target*, or its value is bad (SBX-15)."""
        try:
            backend = self._backend()
        except SandboxError:
            return False
        return backend != BACKEND_REMOTE or target in REMOTE_TARGETS

    def _config_ok(self) -> bool:
        """The backend, its daemon address and the runtime all check out."""
        settings = self._settings()
        try:
            backend = sandbox_backend(settings)
            if backend == BACKEND_REMOTE:
                remote_docker_host(settings)
            sandbox_runtime(settings, backend)
        except SandboxError:
            return False
        return True

    def healthy(self) -> bool:
        """§7.7 condition 4: Docker answers, and the free-space floor holds.

        The image must be pinned too, or no container can start, and the
        backend, its daemon address and the runtime must check out (SBX-15).
        On ``docker-remote`` the probe asks the remote daemon. The Docker
        answer is cached for ``_HEALTH_TTL_SECONDS``. A stale answer starts a
        probe in the background and is used until the probe ends. Before the
        first answer, the broker is not healthy, so a flip fails closed.
        """
        settings = self._settings()
        try:
            pinned_image(settings)
        except SandboxError:
            return False
        if not self._config_ok():
            return False
        try:
            if self.free_disk_mb() < int(getattr(settings, "sandbox_min_free_disk_mb", 5120)):
                return False
        except OSError:
            return False
        if self._docker_ok is None or self._clock() - self._docker_checked > _HEALTH_TTL_SECONDS:
            self._schedule_probe()
        return bool(self._docker_ok)

    def _note_docker(self, ok: bool) -> None:
        self._docker_ok = ok
        self._docker_checked = self._clock()

    def _schedule_probe(self) -> None:
        if self._probe is not None and not self._probe.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._probe = loop.create_task(self.probe_docker())

    async def probe_docker(self) -> bool:
        """Ask Docker for its server version. Records and returns the answer."""
        try:
            result = await self._docker.run(
                ["version", "--format", "{{.Server.Version}}"], timeout=_SMALL_TIMEOUT_SECONDS,
            )
            ok = result.rc == 0 and bool(result.stdout.strip())
        except (SandboxError, OSError):
            ok = False
        self._note_docker(ok)
        return ok

    # ── checks ──────────────────────────────────────────────────────────────

    def free_disk_mb(self) -> int:
        from acb_skills.agent_paths import state_root

        return _free_disk_mb(state_root())

    def _check_free_disk(self) -> None:
        floor = int(getattr(self._settings(), "sandbox_min_free_disk_mb", 5120))
        free = self.free_disk_mb()
        if free < floor:
            raise SandboxRefused(
                f"The box has {free} MB free, below the floor of {floor} MB. "
                "The sandbox runs nothing until space is free."
            )

    def _quota(self) -> tuple[int, int]:
        settings = self._settings()
        return (
            int(getattr(settings, "sandbox_workspace_quota_mb", 2048)),
            int(getattr(settings, "sandbox_workspace_max_files", 100_000)),
        )

    async def measure_workspace(self, handle: SandboxHandle) -> int:
        """Measure the whole working dir, and set the quota flag.

        The quota bounds the bytes AND the count of entries, so a flood of
        small files cannot use up the inodes of the host file system.
        """
        used, entries = await asyncio.to_thread(_dir_usage, handle.workspace)
        if handle.binding.target == PROJECTS_TARGET:
            # The run-data dir lies outside the working dir, and the same
            # quota bounds it (§16.3).
            more_used, more_entries = await asyncio.to_thread(
                _dir_usage, handle.binding.run_data,
            )
            used, entries = used + more_used, entries + more_entries
        quota_mb, max_files = self._quota()
        handle.workspace_bytes, handle.workspace_files = used, entries
        handle.over_quota = used > quota_mb * _MIB or entries > max_files
        return used

    @staticmethod
    def quota_exceeded(handle: SandboxHandle) -> bool:
        """True after an exec left the working dir past its quota.

        The file tools (WS-43d) refuse a write while this is true, and still
        allow a delete, so the agent can free space.
        """
        return handle.over_quota

    async def _check_quota(self, handle: SandboxHandle) -> None:
        if not handle.over_quota:
            return
        await self.measure_workspace(handle)
        if handle.over_quota:
            quota_mb, max_files = self._quota()
            raise SandboxRefused(
                f"The working dir is over its quota of {quota_mb} MB or "
                f"{max_files} files. Delete files to free space, then run the "
                "command again."
            )

    def _clamp_timeout(self, timeout_s: float) -> int:
        ceiling = int(getattr(self._settings(), "sandbox_exec_max_timeout_seconds", 300))
        try:
            value = int(timeout_s)
        except (TypeError, ValueError):
            value = ceiling
        return max(1, min(value, ceiling))

    def _require_live(self, handle: SandboxHandle) -> None:
        if handle.removed or self._live.get(handle.name) is not handle:
            raise SandboxRefused("This sandbox was stopped. Acquire it again.")
        if handle.failed:
            raise SandboxUnavailable("This sandbox did not start.")

    async def _wait_ready(self, handle: SandboxHandle) -> None:
        if handle.starting:
            await handle.ready.wait()
        self._require_live(handle)

    # ── acquire / release ───────────────────────────────────────────────────

    async def _await_startup(self) -> None:
        task = self._startup
        if task is not None and not task.done():
            with contextlib.suppress(Exception):
                await asyncio.shield(task)

    async def acquire(self) -> SandboxHandle:
        """The container of the bound run. It starts one when needed.

        Takes no argument: the tenant, the agent, the thread and the dir all
        come from the run binding (§7.1 rule 2). Raises
        :class:`SandboxRefused`, :class:`SandboxBusy` or
        :class:`SandboxUnavailable`, and never falls back to the host.

        Cancel-safe (review P1-b): an error or a cancel at any step after the
        claim unregisters the new handle, or gives back the lease of a reused
        one. No handle stays ``starting`` and no slot of a cap stays taken.
        """
        binding = read_run_binding()
        if binding.target == COMPUTE_TARGET:
            raise SandboxRefused("The compute target starts only through run_compute.")
        if not maf_coding_scope_allows(target_for_agent(binding.agent), binding.org):
            raise SandboxRefused(
                "MAF_CODING_SCOPE does not name this target for this organization, "
                "so the sandbox starts nothing."
            )
        backend = self._backend()
        self._check_target(binding, backend)
        if binding.target == PROJECTS_TARGET:
            _require_thread_slug(binding)
            binding = await self._with_own_skills(binding)
        await self._await_startup()
        self._check_free_disk()
        if not self._reusable(binding, remote=backend == BACKEND_REMOTE):
            await self._preflight(binding)
        handle, victims, fresh = await self._claim(binding, remote=backend == BACKEND_REMOTE)
        if not fresh:
            return await self._join(handle)
        try:
            if victims:
                await asyncio.shield(self._spawn_background(self._remove_all(victims)))
            await self._start(handle)
        except BaseException:
            self._abandon(handle)
            raise
        handle.starting = False
        handle.ready.set()
        self._ensure_reaper()
        return handle

    async def _with_own_skills(self, binding: RunBinding) -> RunBinding:
        """*binding* with the run member's own skill folders, read under the dir lock."""
        from dataclasses import replace

        from acb_skills.agent_paths import own_skill_names

        async with self._lock_for(binding.workspace):
            names = await asyncio.to_thread(own_skill_names, binding.workspace, binding.member)
        return replace(binding, skills=names)

    @staticmethod
    def _same_mounts(held: SandboxHandle, binding: RunBinding) -> bool:
        """True when *held* was started with the mounts that *binding* needs."""
        return (
            held.workspace == binding.workspace and not held.failed and not held.stale
            and held.binding.skills == binding.skills
        )

    def _reusable(self, binding: RunBinding, *, remote: bool = False) -> bool:
        held = self._live.get(binding.name)
        return held is not None and held.remote == remote and self._same_mounts(held, binding)

    @staticmethod
    def _check_target(binding: RunBinding, backend: str) -> None:
        """Refuse a target that the backend cannot run (SBX-15)."""
        if backend == BACKEND_REMOTE and binding.target not in REMOTE_TARGETS:
            raise SandboxRefused(
                f"The docker-remote sandbox runs the {sorted(REMOTE_TARGETS)} target "
                f"only. The {binding.target} target needs a mount of this box, so "
                "no sandbox starts."
            )

    def _remote_size_mb(self) -> int:
        """The size of the ``/workspace`` tmpfs of a remote container."""
        try:
            value = int(getattr(self._settings(), "sandbox_remote_workspace_mb", 512))
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            raise SandboxRefused("The sandbox_remote_workspace_mb setting is not valid.")
        return value

    def _remote_entries(self) -> int:
        return max(1, self._quota()[1])

    async def _preflight(self, binding: RunBinding) -> None:
        """Check every input of a start BEFORE a container is evicted for it.

        The image, the limits, the uid, the backend and the runtime, the state
        dir and the mounts. On ``docker-remote`` it also checks that the
        working dir fits the remote workspace. A start that would fail must
        not cost another run its idle container.
        """
        settings = self._settings()
        pinned_image(settings)
        Limits.from_settings(settings)
        uid, gid = _process_ids()
        if uid == 0 or gid == 0:
            raise SandboxRefused("The sandbox never runs as uid 0 or gid 0.")
        if int(getattr(settings, "sandbox_max_per_org", 2)) > int(
            getattr(settings, "sandbox_max_total", 4)
        ):
            raise SandboxRefused(
                "sandbox_max_per_org is above sandbox_max_total, so one organization "
                "could take every slot. No sandbox starts until the settings agree."
            )
        backend = sandbox_backend(settings)
        self._check_target(binding, backend)
        sandbox_runtime(settings, backend)
        await self._mounts_for(binding)
        if backend == BACKEND_REMOTE:
            remote_docker_host(settings)
            await asyncio.to_thread(
                self._check_remote_fits, binding.workspace, self._remote_size_mb() * _MIB,
            )

    def _check_remote_fits(self, workspace: Path, max_bytes: int) -> None:
        """Refuse a working dir that the remote workspace cannot hold."""
        total = 0
        for rel in _host_tree(workspace, max_entries=self._remote_entries()).files:
            with contextlib.suppress(OSError):
                total += os.lstat(workspace / rel).st_size
        if total > max_bytes:
            raise SandboxRefused(
                f"The working dir holds more than {max_bytes // _MIB} MB, the size "
                "of the remote sandbox workspace. Delete files, then try again."
            )

    async def _claim(
        self, binding: RunBinding, *, remote: bool = False,
    ) -> tuple[SandboxHandle, list[SandboxHandle], bool]:
        """Reuse on a full match, or reserve a new handle within the caps.

        No ``await`` runs in here, so no cancel can land between the
        decision and the change of the registry.
        """
        async with self._registry_lock:
            if remote and self._other_org_on_box(binding.org):
                # SBX-15: one organization per remote box, across every
                # registry, decided under the lock. `_remote_org_admit` in
                # `_start` reads the daemon too.
                raise SandboxBusy(ONE_ORG_BUSY)
            victims: list[SandboxHandle] = []
            held = self._live.get(binding.name)
            if held is not None:
                if held.org != binding.org or held.agent != binding.agent:
                    raise SandboxRefused("A sandbox of another tenant holds this name.")
                if held.remote == remote and self._same_mounts(held, binding):
                    held.leases += 1
                    held.last_used = self._clock()
                    return held, victims, False
                if held.leases or held.starting or held.lock.locked():
                    raise SandboxBusy("This thread's sandbox is busy. Try again when it finishes.")
                self._drop(held)
                victims.append(held)
            victims += self._make_room(binding.org)
            # `remote` is set here, under the lock, so a second acquire of the
            # thread joins this start, and the one-org check sees it in flight.
            handle = SandboxHandle(
                binding=binding, lock=self._lock_for(binding.workspace),
                leases=1, last_used=self._clock(), remote=remote,
            )
            self._live[binding.name] = handle
            return handle, victims, True

    async def _join(self, handle: SandboxHandle) -> SandboxHandle:
        """Wait for a reused handle to be ready. A cancel gives the lease back."""
        try:
            await self._wait_ready(handle)
        except BaseException:
            handle.leases = max(0, handle.leases - 1)
            raise
        return handle

    def _abandon(self, handle: SandboxHandle) -> None:
        """Unregister a handle whose start failed or was cancelled. No await."""
        handle.failed = True
        handle.leases = 0
        handle.starting = False
        self._drop(handle)
        handle.ready.set()

    def _drop(self, handle: SandboxHandle) -> None:
        self._forget_grant(handle)
        handle.removed = True
        if self._live.get(handle.name) is handle:
            del self._live[handle.name]
        source = handle.workspace
        shared = any(h.workspace == source for h in self._live.values())
        lock = self._dir_locks.get(source)
        if lock is not None and not shared and not lock.locked():
            del self._dir_locks[source]

    def _make_room(self, org: str) -> list[SandboxHandle]:
        """Stop the oldest idle container that the caps allow (§7.1 rule 8).

        Called under the registry lock. An organization at its fair share
        stops its OWN oldest idle container. At the box cap, the oldest idle
        container of ANY organization stops. If no allowed container is idle,
        raises :class:`SandboxBusy`.
        """
        settings = self._settings()
        per_org = int(getattr(settings, "sandbox_max_per_org", 2))
        total = int(getattr(settings, "sandbox_max_total", 4))
        victims: list[SandboxHandle] = []
        mine = [h for h in self._live.values() if h.org == org]
        if len(mine) >= per_org:
            victim = _oldest_idle(mine)
            if victim is None:
                raise SandboxBusy(
                    f"This organization runs {len(mine)} sandboxes, its share, "
                    "and none is idle. Try again when one finishes."
                )
            self._drop(victim)
            victims.append(victim)
        if len(self._live) >= total:
            victim = _oldest_idle(self._live.values())
            if victim is None:
                raise SandboxBusy(
                    f"The box runs its maximum of {total} sandboxes, and none is "
                    "idle. Try again when one finishes."
                )
            self._drop(victim)
            victims.append(victim)
        return victims

    async def release(self, handle: SandboxHandle) -> None:
        """Mark the container idle. A container past its lifetime stops now."""
        handle.leases = max(0, handle.leases - 1)
        handle.last_used = self._clock()
        lifetime = float(getattr(self._settings(), "sandbox_max_lifetime_seconds", 7200))
        if handle.leases or handle.removed or handle.lock.locked():
            return
        if self._clock() - handle.started_at > lifetime:
            async with self._registry_lock:
                self._drop(handle)
            await self._remove(handle)

    # ── start / restart / remove ────────────────────────────────────────────

    async def _mounts_for(self, binding: RunBinding) -> list[Mount]:
        """The mounts of a start, after the nesting check (review P1-c)."""
        others = self._sandbox_dirs()
        if binding.target == PROJECTS_TARGET:
            # Recorded BEFORE the dir exists, so end_run deletes it even when
            # the start fails half way.
            self._note_run_data(binding)
        return await asyncio.to_thread(self._build_mounts, binding, others)

    def _build_mounts(self, binding: RunBinding, others: set[Path]) -> list[Mount]:
        check_not_nested(binding.workspace, others)
        cover = None
        readonly: list[tuple[Path, str]] = []
        if binding.target == PROJECTS_TARGET:
            prepare_projects_dirs(binding)
            cover = self.skill_cover(binding)
        if binding.target == APP_BUILDER_TARGET:
            # Before the mounts: the .git cover needs a .git (review P2).
            ensure_app_git(binding.workspace)
            readonly = self.app_builder_mounts()
        return mount_list(
            binding, self.git_cover(), readonly_mounts=readonly, skill_cover=cover,
        )

    # ── the app_builder target: the build scripts and the vendor cache ──────

    def app_builder_mounts(self) -> list[tuple[Path, str]]:
        """The read-only mounts of an ``app_builder`` start (§7.8, WS-43h).

        The list is code. No caller can add to it. It holds a copy of the
        build scripts at :data:`APP_BUILDER_BUILD_TARGET`, and the T2 vendor
        cache at :data:`T2_VENDOR_TARGET` when the deploy made it. A source
        that is missing is left out, so the build fails in the container and
        nothing runs on the host. :func:`mount_list` checks each source again:
        a real dir, with no link and no ``.git`` at any depth.
        """
        mounts: list[tuple[Path, str]] = []
        build = self.app_builder_build_copy()
        if build is not None:
            mounts.append((build, APP_BUILDER_BUILD_TARGET))
        vendor = _t2_vendor_source()
        if vendor is not None:
            mounts.append((vendor, T2_VENDOR_TARGET))
        return mounts

    def app_builder_build_copy(self) -> Path | None:
        """A copy of the agent's build scripts, made for a start, or ``None``.

        The copy holds only :data:`APP_BUILDER_BUILD_SOURCES`, so it holds no
        ``.git`` and no other file of the repo. Its name is a digest of
        the content, so a start never changes a copy that a running container
        mounts. Two starts that make the same copy at once each make a temp
        dir and rename it into place, as the skill cover does. A file-system
        error is :class:`SandboxUnavailable`.
        """
        source = app_builder_build_source()
        if source is None:
            _log.warning("sandbox_broker.app_builder_build_missing")
            return None
        try:
            return self._build_app_builder_copy(source)
        except OSError as exc:
            _log.warning("sandbox_broker.app_builder_build_copy_failed", error=str(exc)[:200])
            raise SandboxUnavailable(
                "The copy of the app build scripts could not be made. Try again."
            ) from exc

    def _build_app_builder_copy(self, source: Path) -> Path | None:
        files: dict[str, bytes] = {}
        for name, rel in APP_BUILDER_BUILD_SOURCES.items():
            src = source / rel
            if _is_link(src) or not src.is_file():
                continue
            files[name] = src.read_bytes()
        if not files:
            return None
        base = self.state_dir() / "app-builder-build"
        base.mkdir(parents=True, exist_ok=True)
        if _is_link(base):
            raise SandboxUnavailable("The build copy dir is not a real dir.")
        path = base / _digest(
            *(f"{n}:{hashlib.sha256(d).hexdigest()}" for n, d in sorted(files.items())),
            length=24,
        )

        def clean() -> bool:
            if _is_link(path) or (path.exists() and not path.is_dir()):
                raise SandboxUnavailable("A build copy is not a real dir.")
            if not path.is_dir():
                return False
            have = {e.name: e for e in os.scandir(path)}
            return set(have) == set(files) and all(
                e.is_file(follow_symlinks=False) and Path(e.path).read_bytes() == files[e.name]
                for e in have.values()
            )

        if clean():
            return path.resolve()
        tmp = base / f".tmp-{uuid.uuid4().hex}"
        tmp.mkdir()
        try:
            for name, data in files.items():
                (tmp / name).write_bytes(data)
            if path.is_dir() and not clean():
                shutil.rmtree(path, ignore_errors=True)
            try:
                os.rename(tmp, path)
            except OSError:
                # Another start renamed its copy into place first.
                if not clean():
                    raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return path.resolve()

    # ── the projects target: host files and run data (§16.3) ────────────────

    def _note_run_data(self, binding: RunBinding) -> None:
        self._run_data[(binding.workspace, binding.thread)] = binding.run_data_rel

    @contextlib.asynccontextmanager
    async def host_dir(self) -> AsyncIterator[RunBinding]:
        """Hold the bound run's dir lock while the host reads or writes, with no start.

        The twin of :meth:`host_files` for a host file call that needs no
        container: the file tools of projects-assistant, and the skill list.
        It takes the SAME lock as every container on the dir (the lock of the
        mount source), so no exec of any container on it runs inside the
        block. It reads the run binding itself, so no caller names a dir (R5).
        """
        binding = read_run_binding()
        async with self._lock_for(binding.workspace):
            yield binding

    @contextlib.asynccontextmanager
    async def host_dir_of(self, workspace: Path) -> AsyncIterator[Path]:
        """Hold the dir lock of *workspace*, for a host writer that is not its run.

        PR #656: a delegated run delivers a document into the working dir of
        the chat that called it, and a container of that chat can mount it.
        This takes the SAME lock as :meth:`host_dir` and every container on
        the dir. The caller takes *workspace* from a server-side binding
        (``write_artifact.delegation_target``), never from input (R5).
        """
        source = Path(workspace).resolve()
        async with self._lock_for(source):
            yield source

    async def ensure_thread_dirs(self, binding: RunBinding) -> None:
        """Make the thread's output and upload folders and run-data dir before any start.

        The file tools call it, so a write to ``outputs/``, ``inputs/`` or ``.run/`` lands
        where the container will see it. The caller holds :meth:`host_dir`.
        """
        _require_thread_slug(binding)
        self._note_run_data(binding)
        await asyncio.to_thread(prepare_projects_dirs, binding)

    def writes_refused(self, workspace: Path) -> bool:
        """True while a container on *workspace* is over its quota (§7.1 rule 10).

        The file tools refuse a write then, and still allow a delete.
        """
        return any(h.workspace == workspace and h.over_quota for h in self._live.values())

    async def end_run(self) -> bool:
        """Delete the bound run's run-data dir at the end of the run (§16.3).

        The executor calls it in the ``finally`` of every run, so a failed or
        a cancelled run deletes it too. It reads the run's own artifact
        context, never an argument (R5). With no run data recorded, it
        returns at once, so a run of any other agent pays nothing.

        The delete runs under the dir lock, with the safe opener. The
        thread's container then mounts a deleted dir at ``/workspace/.run``,
        so it is marked stale, and removed when idle. The next run of the
        thread starts a fresh container on a fresh run-data dir.

        SBX-15: a remote container holds a copy of the run's files in its
        tmpfs. So its run's end marks it stale too, and an idle one goes now.
        The sandbox box then keeps no tenant data past the run.

        WS-43g1 (§7.3): the run's end revokes each egress grant of its thread
        first, so no grant outlives its run, and the container is back on
        ``--network none``.
        """
        if (
            not self._run_data and not any(h.remote for h in self._live.values())
            and not self._grants
        ):
            return False
        from acb_skills.write_artifact import artifact_context

        ctx = artifact_context()
        thread = str(ctx.get("session_id") or "")
        raw = str(ctx.get("workspace_root") or "")
        if not thread or not raw:
            return False
        try:
            workspace = Path(raw).resolve()
        except (OSError, RuntimeError):
            return False
        revoked = await self._revoke_for_run(workspace, thread) if self._grants else False
        rel = self._run_data.get((workspace, thread))
        remote = [
            h for h in self._live.values()
            if h.remote and h.workspace == workspace and h.binding.thread == thread
        ]
        if rel is None and not remote:
            return revoked
        async with self._lock_for(workspace):
            removed = False
            if rel is not None:
                removed = await asyncio.to_thread(_remove_run_data, rel)
            self._run_data.pop((workspace, thread), None)
            stale = [
                h for h in self._live.values()
                if h.workspace == workspace and h.binding.thread == thread
            ]
            for handle in stale:
                handle.stale = True
        idle = [h for h in stale if h.leases == 0 and not h.starting]
        if idle:
            async with self._registry_lock:
                for handle in idle:
                    self._drop(handle)
            self._spawn_background(self._remove_all(idle))
        _log.info(
            "sandbox_broker.run_data_deleted", removed=removed, containers=len(idle),
            remote=len(remote),
        )
        return removed or bool(remote) or revoked

    async def _start(self, handle: SandboxHandle) -> None:
        """Run the container and record its id, and its init and keep-alive PIDs.

        On an error or a cancel, the container of THIS start is removed by its
        id, or by its start label when ``docker run`` printed no id yet.
        """
        settings = self._settings()
        image = pinned_image(settings)
        limits = Limits.from_settings(settings)
        backend = sandbox_backend(settings)
        self._check_target(handle.binding, backend)
        runtime = sandbox_runtime(settings, backend)
        remote = backend == BACKEND_REMOTE
        workspace_mb = self._remote_size_mb() if remote else None
        uid, gid = _process_ids()
        # On the remote backend the mounts are built for their checks only
        # (the real dir, no `.git` below the root). The container gets none.
        mounts = await self._mounts_for(handle.binding)
        handle.start_id, handle.container_id = uuid.uuid4().hex, ""
        if handle.egress is not None and remote:
            raise SandboxRefused("A docker-remote sandbox gets no egress grant.")
        env_file = None
        if handle.egress is not None:
            env_file = await asyncio.to_thread(
                write_egress_env_file, self._egress_dir() / "env", handle.start_id,
                handle.egress,
            )
        try:
            argv = build_run_argv(
                binding=handle.binding, mounts=() if remote else mounts, uid=uid, gid=gid,
                image=image, limits=limits, start_id=handle.start_id,
                runtime=runtime, workspace_tmpfs_mb=workspace_mb, egress=handle.egress,
                egress_env_file=env_file,
            )
            handle.remote, handle.pushed = remote, frozenset()
            await asyncio.to_thread(self._record_dir, handle.workspace)
            # SBX-15 review P2: the admit and `docker run` hold ONE lock, so two
            # starts of two orgs never both pass the admit.
            async with self._start_gate(remote):
                await self._remote_org_admit(handle.org)
                try:
                    await self._run_container(handle, argv)
                    handle.keepalive_pid = await self._probe_keepalive(handle.name)
                except BaseException as exc:
                    cleanup = self._spawn_background(
                        self._remove_container(handle.container_id, handle.start_id)
                    )
                    if not isinstance(exc, asyncio.CancelledError):
                        await asyncio.shield(cleanup)
                    raise
        finally:
            if env_file is not None:
                with contextlib.suppress(OSError):
                    env_file.unlink()
        handle.init_pid = 1
        handle.started_at = handle.last_used = self._clock()
        handle.network = handle.egress.network if handle.egress is not None else "none"
        _log.info(
            "sandbox_broker.started", name=handle.name, org=handle.org,
            agent=handle.agent, keepalive_pid=handle.keepalive_pid,
            backend=backend, runtime=runtime, network=handle.network,
        )

    def _start_gate(self, remote: bool) -> Any:
        """The ONE lock of every remote start, or no lock on the local backend.

        Hold it from :meth:`_remote_org_admit` until ``docker run`` returns,
        on every start path. The admit alone reads a state that a second start
        can change between its read and its ``docker run``.
        """
        return self._remote_start_lock if remote else contextlib.nullcontext()

    def _registered_handles(self) -> list[SandboxHandle]:
        """The handles of every registry of this broker: the mounted targets
        (``_live``) and the compute target of SBX-18 (``_compute_live``)."""
        return [*self._live.values(), *self._compute_live.values()]

    def _other_org_on_box(self, org: str) -> bool:
        """True when a registry holds a remote container of an org that is not *org*.

        Each claim asks it under the registry lock, and the admit asks it
        again. A handle is remote from its claim (``_claim``,
        ``_compute_claim``), so a start in flight counts.
        """
        return any(h.remote and h.org != org for h in self._registered_handles())

    async def _remote_org_admit(self, org: str) -> None:
        """One organization per remote box (SBX-15). Raises :class:`SandboxBusy`.

        Call it inside ``async with self._start_gate(remote)``, right before
        ``docker run`` of ANY container start, and hold the gate until that
        run returns. ``_start`` does it (a first start and a restart), and so
        must every other start path of this broker. On the local backend it
        does nothing.

        It refuses when a registry of this broker holds a remote container of
        another org. It also reads the org label of each sandbox container on
        the remote daemon, so a container that no registry knows (a race, a
        failed removal, a crash before the sweep) still counts. A container
        with no org label counts as another org. A read that fails refuses
        the start. This is the control against a gVisor escape that reads the
        data of another org in flight.
        """
        if sandbox_backend(self._settings()) != BACKEND_REMOTE:
            return
        if self._other_org_on_box(org):
            raise SandboxBusy(ONE_ORG_BUSY)
        found = await self._docker.run(
            ["ps", "-a", "--filter", f"label={LABEL_SANDBOX}=1",
             "--format", '{{.Label "' + LABEL_ORG + '"}}'],
            timeout=_SMALL_TIMEOUT_SECONDS,
        )
        if found.rc != 0:
            _log.warning("sandbox_broker.box_read_failed", error=found.stderr[-300:])
            raise SandboxUnavailable(
                "The broker could not read the containers of the sandbox box, so no "
                "sandbox starts."
            )
        orgs = {line.strip() or "(none)" for line in found.stdout.splitlines()}
        if orgs - {org}:
            _log.info("sandbox_broker.box_held", org=org, others=len(orgs - {org}))
            raise SandboxBusy(ONE_ORG_BUSY)

    async def _run_container(self, handle: SandboxHandle, argv: list[str]) -> None:
        try:
            result = await self._docker.run(argv, timeout=_RUN_TIMEOUT_SECONDS)
            if result.rc != 0 and "already in use" in result.stderr:
                await self._clear_stale_name(handle)
                result = await self._docker.run(argv, timeout=_RUN_TIMEOUT_SECONDS)
        except BaseException:
            # SBX-15c review P2: a cancel kills the CLI as a timeout does, so
            # the daemon can make the container late on this path too.
            self._note_late_start(handle.start_id)
            raise
        if result.timed_out:
            # SBX-15c: the daemon can make the container after the CLI died,
            # and after the start-label lookup of the cleanup. The reaper
            # looks it up again until the window ends.
            self._note_late_start(handle.start_id)
        if result.rc != 0:
            _log.warning(
                "sandbox_broker.start_failed", name=handle.name,
                org=handle.org, error=result.stderr[-400:],
            )
            raise SandboxUnavailable("The sandbox container did not start.")
        handle.container_id = (result.stdout.strip().splitlines() or [""])[-1].strip()

    async def _clear_stale_name(self, handle: SandboxHandle) -> None:
        """Remove a stale container of the SAME tenant that holds the name."""
        found = await self._docker.run(
            ["inspect", "--format", "{{.Id}}|{{json .Config.Labels}}", handle.name],
            timeout=_SMALL_TIMEOUT_SECONDS,
        )
        stale_id, _sep, raw = found.stdout.strip().partition("|")
        if found.rc != 0 or not stale_id.strip():
            raise SandboxUnavailable(
                "The container that held this sandbox name vanished before the "
                "broker could check it. Retry the command."
            )
        try:
            labels = json.loads(raw or "null") or {}
        except json.JSONDecodeError:
            labels = {}
        if labels.get(LABEL_ORG) != handle.org or labels.get(LABEL_AGENT) != handle.agent:
            raise SandboxRefused("A container of another tenant holds this sandbox name.")
        await self._remove_container(stale_id.strip(), "")

    async def _probe_keepalive(self, name: str) -> int:
        result = await self._docker.run(
            ["exec", name, "bash", "-c", PID_PROBE], timeout=_SMALL_TIMEOUT_SECONDS,
        )
        pids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.rc != 0 or len(pids) != 1 or not pids[0].isdigit():
            _log.warning(
                "sandbox_broker.probe_failed", name=name, rc=result.rc,
                pids=pids[:5], error=result.stderr[-300:],
            )
            raise SandboxUnavailable("The sandbox container did not settle after start.")
        return int(pids[0])

    async def _restart(self, handle: SandboxHandle, reason: str) -> None:
        """Remove the container and start it again with the SAME mounts.

        ``_start`` builds the mounts with :func:`mount_list`, as every start
        does, so the cover on ``/workspace/.git`` comes back. The network is
        the handle's: ``none``, or the network of its live grant (WS-43g1).
        A grant and a revoke recreate the container through here too.
        Raises :class:`SandboxUnavailable` when it fails.
        """
        _log.info("sandbox_broker.restarting", name=handle.name, reason=reason)
        await self._remove(handle)
        try:
            await self._start(handle)
        except SandboxError as exc:
            async with self._registry_lock:
                self._drop(handle)
            # The drop spent the network of a live grant (WS-43g2a).
            await asyncio.shield(self._flush_networks())
            raise SandboxUnavailable(
                f"The sandbox restart failed, so the sandbox is gone: {exc} "
                "Nothing runs on the host."
            ) from exc

    async def _remove_container(self, container_id: str, start_id: str) -> None:
        """Remove one container by its id, or by its start label. Never by name."""
        ids = [container_id] if container_id else []
        if not ids and start_id:
            found = await self._docker.run(
                ["ps", "-aq", "--filter", f"label={LABEL_START}={start_id}"],
                timeout=_SMALL_TIMEOUT_SECONDS,
            )
            ids = [line.strip() for line in found.stdout.splitlines() if line.strip()]
            if found.rc != 0:
                self._note_orphans([f"start:{start_id}"])
        if not ids:
            return
        result = await self._docker.run(["rm", "-f", *ids], timeout=_SMALL_TIMEOUT_SECONDS)
        if result.rc != 0 and "No such container" not in result.stderr:
            _log.warning("sandbox_broker.remove_failed", ids=ids, error=result.stderr[-300:])
            self._note_orphans(ids)

    def _is_remote(self) -> bool:
        try:
            return self._backend() == BACKEND_REMOTE
        except SandboxError:
            return False

    def _note_orphans(self, ids: Iterable[str]) -> None:
        """SBX-15 review P2: keep a failed remote removal for the reaper.

        A container left on the remote box holds the data of its org, and
        the one-org admit refuses every other org while it stays. So the
        reaper runs until :meth:`_reap_remote_orphans` removes it.
        """
        if not self._is_remote():
            return
        self._remote_orphans.update(ids)
        with contextlib.suppress(RuntimeError):  # no running loop
            self._ensure_reaper()

    def _note_late_start(self, start_id: str) -> None:
        """SBX-15c: keep the start label of a ``docker run`` that timed out.

        The reaper looks for its container on each tick, until it removes
        the container or :data:`_LATE_START_WINDOW_SECONDS` pass.
        """
        if not start_id or not self._is_remote():
            return
        entry = f"start:{start_id}"
        self._late_starts[entry] = self._clock() + _LATE_START_WINDOW_SECONDS
        self._note_orphans([entry])

    async def _reap_remote_orphans(self) -> int:
        """Remove ONLY the containers that a failed removal of this broker left.

        Each entry of ``_remote_orphans`` is a container id, or
        ``start:<start id>`` when even the lookup by start label failed. No
        other container is touched (review P2 of round 2): a daemon that
        another deployment shares holds live containers that no registry of
        this process knows, and only the startup sweep removes every labelled
        container. An entry that a removal took leaves the set, and a failed
        one stays for the next tick. Never raises.
        """
        removed = 0
        for entry in sorted(self._remote_orphans):
            try:
                if entry.startswith("start:"):
                    found = await self._docker.run(
                        ["ps", "-aq", "--filter", f"label={LABEL_START}={entry[6:]}"],
                        timeout=_SMALL_TIMEOUT_SECONDS,
                    )
                    if found.rc != 0:
                        continue
                    ids = [line.strip() for line in found.stdout.splitlines() if line.strip()]
                    late = self._late_starts.get(entry)
                    if not ids and late is not None and self._clock() < late:
                        continue  # SBX-15c: the daemon may still make it
                else:
                    ids = [entry]
                if ids:
                    result = await self._docker.run(
                        ["rm", "-f", *ids], timeout=_RUN_TIMEOUT_SECONDS,
                    )
                    if result.rc != 0 and "No such container" not in result.stderr:
                        _log.warning(
                            "sandbox_broker.orphan_remove_failed", error=result.stderr[-300:],
                        )
                        continue
                    removed += len(ids)
                self._remote_orphans.discard(entry)
                self._late_starts.pop(entry, None)
            except (SandboxError, OSError) as exc:
                _log.warning("sandbox_broker.orphan_reap_failed", error=str(exc)[:300])
        if removed:
            _log.info("sandbox_broker.orphans_removed", count=removed)
        return removed

    async def _remove(self, handle: SandboxHandle) -> None:
        """Remove the container of *handle*, then the network of each spent grant.

        Every exit of a container comes through here: a restart, an eviction,
        the reap, a release past its lifetime and a run-data delete. So a
        dropped grant never leaves its network behind (WS-43g2a).
        """
        try:
            await self._remove_container(handle.container_id, handle.start_id)
        finally:
            await asyncio.shield(self._flush_networks())

    async def _remove_all(self, handles: Iterable[SandboxHandle]) -> None:
        for handle in list(handles):
            await self._remove(handle)

    # ── egress (WS-43g1, §7.3) ──────────────────────────────────────────────

    def _egress_dir(self) -> Path:
        """``<state dir>/egress``, mode 0700. Only the broker writes in it."""
        return self.state_dir() / "egress"

    def _grants_path(self) -> Path:
        return self._egress_dir() / "grants" / EGRESS_GRANTS_FILE

    def _prepare_egress_dirs(self) -> tuple[Path, Path]:
        """The two read-only sources of the proxy: the script dir and the grants dir.

        The parent is 0700, so no other host user reads a grant. Each
        source is 0755, because the proxy reads it as uid 65534. The script
        is a copy, so the mount shows the proxy one file and no ``.git``.
        """
        base = self._egress_dir()
        base.mkdir(parents=True, exist_ok=True)
        os.chmod(base, 0o700)
        bin_dir, grants_dir = base / "bin", base / "grants"
        for path in (bin_dir, grants_dir):
            path.mkdir(exist_ok=True)
            os.chmod(path, 0o755)
        script = bin_dir / "egress_proxy.py"
        shutil.copyfile(egress_proxy_source(), script)
        os.chmod(script, 0o644)
        if not (grants_dir / EGRESS_GRANTS_FILE).exists():
            self._write_grants()
        return bin_dir.resolve(), grants_dir.resolve()

    def _write_grants(self) -> None:
        """Write every live grant, atomically. The proxy reads it per request.

        The file holds the SHA-256 of each token, never a token, and the door
        upstream. A rename replaces it, so the proxy never reads half a file.
        """
        path = self._grants_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "version": 1,
            "door_upstream": egress_door_upstream(self._settings()),
            "grants": {key: grant.record() for key, grant in self._grants.items()},
        }
        tmp = path.with_name(f".{EGRESS_GRANTS_FILE}.{uuid.uuid4().hex}")
        tmp.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)

    def _forget_grant(self, handle: SandboxHandle) -> None:
        """End the grant of *handle* in the file at once. No await, so no race.

        The token is dead at the proxy's next request. Never raises: a run's
        end and a drop must not fail on the file.
        """
        grant = handle.egress
        if grant is None:
            return
        handle.egress = None
        self._grants.pop(grant.key, None)
        # WS-43g2a: the network of the grant goes at the next flush. Each
        # removal of a container flushes, so no exit path can skip it.
        self._spend_network(grant.network, handle.container_id or handle.name)
        try:
            self._write_grants()
        except (OSError, SandboxError) as exc:
            _log.warning("sandbox_broker.egress_grants_write_failed", error=str(exc)[:300])

    def _spend_network(self, name: str, container: str = "") -> None:
        """Mark the network *name* for removal at the next flush. No await."""
        if name:
            self._spent_networks[name] = container or self._spent_networks.get(name, "")

    async def _flush_networks(self) -> None:
        """Remove the network of each spent grant. Best effort. Never raises."""
        while self._spent_networks:
            name, container = self._spent_networks.popitem()
            try:
                await self._release_network(name, container)
            except Exception as exc:  # a flush must never fail its caller
                _log.warning("sandbox_broker.egress_network_rm_failed", error=str(exc)[:300])

    async def _ensure_egress_network(
        self, grant: EgressGrant, container: str, attempt: list[str] | None = None,
    ) -> str:
        """The ``--internal`` network of *grant* alone (WS-43g2a).

        It refuses a found network that is not internal, or whose org label
        or run label is not the grant's. A failed create fails closed, so the
        container stays on ``--network none``: no free address pool, or a
        bridge name that another network holds.
        """
        name = grant.network
        found = await self._docker.run(
            ["network", "inspect", "--format",
             '{{.Internal}}|{{index .Labels "' + LABEL_ORG + '"}}|{{index .Labels "'
             + LABEL_EGRESS_RUN + '"}}', name],
            timeout=_SMALL_TIMEOUT_SECONDS,
        )
        if found.rc == 0:
            internal, owner, run = [*found.stdout.strip().split("|", 2), "", ""][:3]
            if internal != "true" or owner != grant.org or run != grant.run:
                raise SandboxRefused(
                    "The egress network of this grant is not its own internal "
                    "network, so no grant opens."
                )
            return name
        if attempt is not None:
            # Set BEFORE the call: a client timeout can still make the network
            # on the daemon, so the caller removes it on any later failure.
            attempt.append(name)
        made = await self._docker.run(
            ["network", "create", "--driver", "bridge", "--internal",
             "--label", f"{LABEL_EGRESS}=1", "--label", f"{LABEL_ORG}={grant.org}",
             "--label", f"{LABEL_EGRESS_RUN}={grant.run}",
             "--opt", "com.docker.network.bridge.name="
             + egress_bridge_name(grant.org, container, grant.key),
             name],
            timeout=_SMALL_TIMEOUT_SECONDS,
        )
        if made.rc != 0:
            no_pool = "address pool" in made.stderr
            _log.warning(
                "sandbox_broker.egress_network_failed", org=grant.org,
                reason="no_free_address_pool" if no_pool else "create_failed",
                error=made.stderr[-300:],
            )
            if no_pool:
                raise SandboxUnavailable(
                    "No free address pool is left for an egress network, so no grant opens."
                )
            raise SandboxUnavailable("The egress network did not start.")
        return name

    async def _ensure_proxy(self, image: str, network: str) -> None:
        """Start the proxy once, and join it to *network*.

        A running proxy whose ``--door-read-timeout`` is not the value of the
        settings goes, and a fresh one starts (WS-43g2a). The restart cuts
        each open connection of the old proxy.
        """
        door_s = door_read_timeout(self._settings())
        if door_s < DOOR_READ_TIMEOUT_MIN_S:
            raise SandboxUnavailable(
                "The door read timeout of the egress proxy is below "
                f"{DOOR_READ_TIMEOUT_MIN_S} s, so no grant opens."
            )
        state = await self._docker.run(
            ["container", "inspect", "--format",
             '{{.State.Running}}|{{index .Config.Labels "' + LABEL_EGRESS + '"}}|'
             "{{json .Config.Cmd}}",
             EGRESS_PROXY_NAME],
            timeout=_SMALL_TIMEOUT_SECONDS,
        )
        running, label, raw_cmd = [*state.stdout.strip().split("|", 2), "", ""][:3]
        try:
            cmd = json.loads(raw_cmd) if raw_cmd else []
        except ValueError:
            cmd = []
        current = (
            state.rc == 0 and running == "true" and label == "proxy"
            and isinstance(cmd, list) and proxy_door_read_timeout(cmd) == str(door_s)
        )
        if not current:
            if state.rc == 0:
                await self._docker.run(
                    ["rm", "-f", EGRESS_PROXY_NAME], timeout=_SMALL_TIMEOUT_SECONDS,
                )
            bin_dir, grants_dir = await asyncio.to_thread(self._prepare_egress_dirs)
            started = await self._docker.run(
                build_proxy_argv(
                    image=image, bin_dir=bin_dir, grants_dir=grants_dir,
                    door_read_timeout_s=door_s,
                ),
                timeout=_RUN_TIMEOUT_SECONDS,
            )
            if started.rc != 0:
                _log.warning("sandbox_broker.egress_proxy_failed", error=started.stderr[-300:])
                raise SandboxUnavailable("The egress proxy did not start.")
            # A fresh proxy is on no egress network. Join it again to the
            # network of every live grant, or those containers lose their
            # only way out.
            networks = sorted({g.network for g in self._grants.values()} | {network})
        else:
            networks = [network]
        # Each live network gets its join. A failed join of ANOTHER grant's
        # network is logged and the loop goes on, so one bad network never
        # strands the later grants. Only the new grant's own join refuses.
        failed_own = False
        for name in networks:
            try:
                joined = await self._docker.run(
                    ["network", "connect", name, EGRESS_PROXY_NAME],
                    timeout=_SMALL_TIMEOUT_SECONDS,
                )
                ok = joined.rc == 0 or "already exists" in joined.stderr
                error = joined.stderr[-300:]
            except (SandboxError, OSError) as exc:
                if name == network:
                    raise
                ok, error = False, str(exc)[:300]
            if not ok:
                _log.warning("sandbox_broker.egress_join_failed", network=name, error=error)
                failed_own = failed_own or name == network
        if failed_own:
            raise SandboxUnavailable("The egress proxy did not join the network.")

    async def _release_network(self, name: str, container: str = "") -> None:
        """Disconnect the proxy, and *container* if named, from the network of
        one grant, and remove the network.

        Each forgotten grant does this for its own network, whatever the other
        grants of the org (WS-43g2a). A network that a live grant still names
        stays. Never raises.
        """
        async with self._egress_lock:
            if any(g.network == name for g in self._grants.values()):
                return
            try:
                for member in (EGRESS_PROXY_NAME, container):
                    if member:
                        await self._docker.run(
                            ["network", "disconnect", "-f", name, member],
                            timeout=_SMALL_TIMEOUT_SECONDS,
                        )
                gone = await self._docker.run(
                    ["network", "rm", name], timeout=_SMALL_TIMEOUT_SECONDS,
                )
            except (SandboxError, OSError) as exc:
                _log.warning("sandbox_broker.egress_network_rm_failed", error=str(exc)[:300])
                return
            if gone.rc != 0 and "not found" not in gone.stderr:
                _log.warning("sandbox_broker.egress_network_rm_failed", error=gone.stderr[-300:])

    def _grant_inputs(self, handle: SandboxHandle) -> tuple[str, str]:
        """The run and the approver of a grant, from the run binding (R5).

        The handle must be the bound run's own container. The approver is
        the member bound to the run, because only that member's chat shows
        the card (§7.3). Raises :class:`SandboxRefused`.
        """
        from acb_skills.write_artifact import artifact_context

        binding = read_run_binding()
        if binding.org != handle.org or binding.name != handle.name:
            raise SandboxRefused("This sandbox is not the bound run's own container.")
        ctx = artifact_context()
        approver = str(ctx.get("member") or "").strip()
        if not approver:
            raise SandboxRefused("No member is bound to this run, so no one can approve egress.")
        return str(ctx.get("run_id") or binding.thread), approver

    async def grant_egress(
        self, handle: SandboxHandle, reason: str, hosts: Sequence[str],
    ) -> EgressGrant:
        """Open the network of *handle* to *hosts*, after a member approved it.

        §7.3, "The grant". Raises :class:`SandboxRefused` when egress is off,
        on ``docker-remote``, for a target that never gets egress, for a host
        off the allowlist, or when the handle is not the bound run's.

        It mints a token, names the network of this grant from the org, the
        container and the grant key, makes that network and the proxy, and
        RECREATES the container on it through :meth:`_restart`, so every
        mount of §7.1 rule 5 comes back. A failed recreate ends the grant. A
        new grant on the same container removes the network of the old one
        (WS-43g2a). WS-43g2b adds the card that calls this.
        """
        settings = self._settings()
        if not egress_enabled(settings):
            raise SandboxRefused(EGRESS_OFF)
        if sandbox_backend(settings) == BACKEND_REMOTE or handle.remote:
            raise SandboxRefused("The docker-remote sandbox has no egress yet.")
        if handle.binding.target not in EGRESS_TARGETS:
            raise SandboxRefused(
                f"The {handle.binding.target} target never gets network access."
            )
        run, approver = self._grant_inputs(handle)
        names = check_grant_hosts(hosts, settings)
        ttl = int(getattr(settings, "sandbox_egress_grant_ttl_seconds", 1800))
        if ttl <= 0:
            raise SandboxRefused("The sandbox_egress_grant_ttl_seconds setting is not valid.")
        image = pinned_image(settings)
        await self._wait_ready(handle)
        token = secrets.token_urlsafe(32)
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        grant = EgressGrant(
            token=token, org=handle.org, run=run, agent=handle.agent,
            approver=approver, hosts=names, expires_at=self._wall() + ttl,
            network=egress_network_name(handle.org, handle.name, key),
        )
        async with handle.lock:
            self._require_live(handle)
            old = handle.egress
            #: The network name once ``network create`` was called for it.
            #: A client timeout can still make it on the daemon, and the name
            #: holds a fresh random key, so it is never another run's.
            attempt: list[str] = []
            try:
                async with self._egress_lock:
                    # The old grant's network is spent here, and the removal
                    # of the old container in _restart removes it.
                    self._forget_grant(handle)
                    await self._ensure_egress_network(grant, handle.name, attempt)
                    await self._ensure_proxy(image, grant.network)
                    handle.egress = grant
                    self._grants[grant.key] = grant
                    await asyncio.to_thread(self._write_grants)
            except BaseException:
                # A found network that is not its own is never touched.
                if handle.egress is grant:
                    self._forget_grant(handle)
                if attempt:
                    self._spend_network(grant.network)
                if old is not None and not handle.removed and self._live.get(handle.name) is handle:
                    # A failed re-grant: the container is still on the old
                    # network with a dead token. Put it back on none (§7.3).
                    try:
                        await asyncio.shield(self._restart(handle, "egress_regrant_failed"))
                    except Exception as exc:  # the original failure is the answer
                        _log.warning(
                            "sandbox_broker.egress_regrant_restore_failed",
                            name=handle.name, error=str(exc)[:300],
                        )
                await asyncio.shield(self._flush_networks())
                raise
            try:
                await self._restart(handle, "egress_grant")
            except BaseException:
                self._forget_grant(handle)
                await asyncio.shield(self._flush_networks())
                raise
        _log.info(
            "sandbox_broker.egress_granted", name=handle.name, org=handle.org, run=run,
            agent=handle.agent, approver=approver, hosts=list(names),
            reason=str(reason or "")[:200], network=grant.network,
        )
        return grant

    async def revoke_egress(self, handle: SandboxHandle) -> bool:
        """End the grant of *handle*, and put it back on ``--network none``.

        The run's end calls it. The token dies in the file FIRST, then the
        container is recreated through :meth:`_restart` with every mount of
        §7.1 rule 5. Returns ``False`` when the handle had no grant.
        """
        if handle.egress is None:
            return False
        async with handle.lock:
            return await self._revoke_locked(handle, "revoked")

    async def _revoke_locked(self, handle: SandboxHandle, why: str) -> bool:
        """:meth:`revoke_egress` with the dir lock already held."""
        grant = handle.egress
        if grant is None:
            return False
        self._forget_grant(handle)
        try:
            if not handle.removed and self._live.get(handle.name) is handle:
                await self._restart(handle, f"egress_{why}")
        finally:
            # _restart removed the container and flushed. A handle that is
            # already gone flushes here.
            await asyncio.shield(self._flush_networks())
            _log.info(
                "sandbox_broker.egress_revoked", name=handle.name, org=grant.org,
                run=grant.run, why=why,
            )
        return True

    async def _revoke_for_run(self, workspace: Path, thread: str) -> bool:
        """Revoke each grant of the run's thread (§7.3). Never raises."""
        granted = [
            h for h in list(self._live.values())
            if h.egress is not None and h.workspace == workspace and h.binding.thread == thread
        ]
        revoked = False
        for handle in granted:
            try:
                revoked = await self.revoke_egress(handle) or revoked
            except Exception as exc:  # the run's end must never fail
                self._forget_grant(handle)
                revoked = True
                _log.warning(
                    "sandbox_broker.egress_revoke_failed", name=handle.name, error=str(exc)[:300],
                )
        return revoked

    async def _sweep_egress(self) -> int:
        """Remove every egress network and every grant. The proxy goes with the
        labelled containers. Never raises.

        On ``docker-remote`` it leaves the daemon alone: no grant runs there,
        and a shared remote daemon holds the networks of other deployments.
        """
        self._grants.clear()
        # The label filter below removes every egress network, spent or not.
        self._spent_networks.clear()
        for handle in self._registered_handles():
            handle.egress = None
        with contextlib.suppress(OSError, SandboxError):
            if self._grants_path().exists():
                self._write_grants()
        # An env file of a start that a crash cut short still holds a token.
        with contextlib.suppress(OSError, SandboxError):
            env_dir = self._egress_dir() / "env"
            if env_dir.is_dir():
                for path in env_dir.iterdir():
                    path.unlink()
        if self._is_remote():
            return 0
        try:
            found = await self._docker.run(
                ["network", "ls", "-q", "--filter", f"label={LABEL_EGRESS}=1"],
                timeout=_SMALL_TIMEOUT_SECONDS,
            )
            ids = [x.strip() for x in found.stdout.splitlines() if x.strip()]
            if found.rc != 0 or not ids:
                return 0
            gone = await self._docker.run(["network", "rm", *ids], timeout=_RUN_TIMEOUT_SECONDS)
        except (SandboxError, OSError) as exc:
            _log.info("sandbox_broker.egress_sweep_skipped", error=str(exc)[:200])
            return 0
        if gone.rc != 0:
            _log.warning("sandbox_broker.egress_sweep_failed", error=gone.stderr[-300:])
            return 0
        return len(ids)

    # ── exec (§7.1 rules 9, 10 and 11) ──────────────────────────────────────

    def _exec_argv(self, handle: SandboxHandle, command: str, timeout_s: int) -> list[str]:
        return [
            "exec", "--workdir", WORKSPACE_TARGET, handle.name,
            "bash", "-c", EXEC_WRAPPER, "mtr-exec", str(timeout_s), command,
        ]

    async def _kill_strays(self, handle: SandboxHandle) -> bool:
        """Kill every process except init and the keep-alive. True when clean."""
        result = await self._docker.run(
            ["exec", handle.name, "bash", "-c", KILL_SWEEP, "mtr-sweep",
             str(handle.keepalive_pid)],
            timeout=_SMALL_TIMEOUT_SECONDS,
        )
        clean = result.rc == 0 and "clean" in result.stdout
        if not clean:
            _log.warning(
                "sandbox_broker.strays_survived", name=handle.name, rc=result.rc,
                detail=(result.stdout + result.stderr)[-300:],
            )
        return clean

    async def exec(self, handle: SandboxHandle, command: str, timeout_s: float) -> ExecResult:
        """Run one command in the container, under the lock of its mount source.

        One exec at a time per mount source. The disk floor and the quota are
        checked first. After the command, every stray process dies, and the
        container restarts if one survives. A broken container restarts once,
        and the command does not run again.

        ⚠️ A cancel or an error once the command started (a member presses
        Stop, a tool times out) kills only the ``docker exec`` CLI. The
        command keeps running in the container. So the lock passes to a
        background task that runs the kill sweep, restarts on a survivor, and
        only THEN frees the dir. This frame waits for that task and re-raises.
        A second cancel ends the wait early, and the dir stays locked until the
        sweep ends (review round 2).
        """
        if not isinstance(command, str) or not command.strip():
            raise SandboxRefused("The command is empty.")
        timeout = self._clamp_timeout(timeout_s)
        await self._wait_ready(handle)
        await handle.lock.acquire()
        handed_off = False
        try:
            self._require_live(handle)
            self._check_free_disk()
            await self._check_quota(handle)
            if handle.egress is not None and handle.egress.expires_at <= self._wall():
                # §7.3: an expired grant ends at the next exec, as a revoke.
                await self._revoke_locked(handle, "expired")
            try:
                return await self._exec_locked(handle, command, timeout)
            except BaseException:
                handed_off = True
                cleanup = self._spawn_background(self._clean_after_abort(handle))
                with contextlib.suppress(BaseException):
                    await asyncio.shield(cleanup)
                raise
        finally:
            if not handed_off:
                handle.lock.release()

    async def _clean_after_abort(self, handle: SandboxHandle) -> None:
        """Sweep after an exec that a cancel or an error cut short, then free the dir.

        The exec's lock is held on entry, and only this task releases it.
        """
        try:
            live = not handle.removed and handle.keepalive_pid is not None
            if live and not await self._kill_strays(handle):
                await self._restart(handle, "aborted_exec")
        except Exception as exc:
            _log.warning(
                "sandbox_broker.abort_sweep_failed", name=handle.name, error=str(exc)[:300],
            )
        finally:
            handle.lock.release()

    async def _exec_locked(
        self, handle: SandboxHandle, command: str, timeout: int,
    ) -> ExecResult:
        cap = int(getattr(self._settings(), "sandbox_output_cap_bytes", 12288))
        if handle.remote:
            # SBX-15: the container sees the working dir only through a copy.
            await self._push(handle)
        handle.last_used = self._clock()
        started = time.monotonic()
        result = await self._docker.stream(
            self._exec_argv(handle, command, timeout),
            timeout=timeout + _HOST_GRACE_SECONDS, head=cap // 2, tail=cap - cap // 2,
        )
        elapsed = time.monotonic() - started
        if not result.host_timed_out and result.rc not in (0, None) and _DOCKER_FAILURE.search(
            result.stderr
        ):
            await self._restart(handle, "exec_failed")
            return ExecResult(
                exit_code=None, output="", output_bytes=0, truncated=False,
                duration_s=elapsed, restarted=True, message=RESTART_MESSAGE,
            )
        restarted = not await self._kill_strays(handle)
        # The copy out comes BEFORE a restart: the tmpfs dies with the container.
        copy_note = await self._pull(handle) if handle.remote else ""
        if restarted:
            await self._restart(handle, "survivor")
        await self.measure_workspace(handle)
        handle.last_used = self._clock()
        timed_out = result.host_timed_out or (result.rc == 137 and elapsed >= timeout - 0.5)
        unsent = unsent_note(handle.unsent) if handle.remote else ""
        notes = [
            note for note in (SURVIVOR_MESSAGE if restarted else "", copy_note, unsent) if note
        ]
        return ExecResult(
            exit_code=137 if result.host_timed_out else result.rc,
            output=result.output,
            output_bytes=result.total_bytes,
            truncated=result.truncated,
            duration_s=elapsed,
            timed_out=timed_out,
            restarted=restarted,
            message=" ".join(notes),
        )

    # ── the remote copy (SBX-15) ────────────────────────────────────────────

    def _spool(self) -> Any:
        """A temp file for one copy: in memory, and on disk in the state dir past
        :data:`_SPOOL_MEMORY`. It is gone when the copy ends."""
        base = self.state_dir()
        base.mkdir(parents=True, exist_ok=True)
        return tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY, dir=str(base))

    async def _push(self, handle: SandboxHandle) -> None:
        """Copy the working dir into the remote container, in place of its ``/workspace``.

        The exec holds the dir lock, so no host writer races it. A failure
        raises :class:`SandboxUnavailable`, and the command does not run.
        """
        size = self._remote_size_mb() * _MIB
        with self._spool() as spool:
            try:
                done = await asyncio.to_thread(
                    write_push_archive, handle.workspace, spool,
                    max_bytes=size, max_entries=self._remote_entries(),
                )
            except OSError as exc:
                raise SandboxUnavailable(
                    "The files of the working dir could not be read, so nothing ran."
                ) from exc
            await asyncio.to_thread(spool.seek, 0)
            result = await self._docker.transfer(
                ["exec", "-i", handle.name, "bash", "-c", PUSH_SCRIPT],
                timeout=_TRANSFER_TIMEOUT_SECONDS, source=spool,
            )
        if result.rc != 0:
            _log.warning(
                "sandbox_broker.push_failed", name=handle.name, rc=result.rc,
                error=result.stderr[-300:],
            )
            raise SandboxUnavailable(
                "The files of the working dir could not be copied into the sandbox, "
                "so nothing ran."
            )
        handle.pushed, handle.unsent = done.sent, done.unsent

    async def _pull(self, handle: SandboxHandle) -> str:
        """Copy ``/workspace`` back to the working dir. Returns a note, or ``""``.

        It runs after the kill sweep, under the dir lock. A failed copy, a
        copy over its cap or an archive that does not parse writes nothing,
        and the note tells the model that the changes are lost. It never
        raises a sandbox error. An aborted exec does not get here, so it
        keeps no change of the container (``_clean_after_abort``).
        """
        size = self._remote_size_mb() * _MIB
        try:
            with self._spool() as spool:
                result = await self._docker.transfer(
                    ["exec", handle.name, "bash", "-c", PULL_SCRIPT],
                    timeout=_TRANSFER_TIMEOUT_SECONDS, sink=spool,
                    limit=2 * size + 16 * _MIB,
                )
                if result.rc != 0:
                    _log.warning(
                        "sandbox_broker.pull_failed", name=handle.name, rc=result.rc,
                        error=result.stderr[-300:],
                    )
                    return REMOTE_COPY_FAILED
                await asyncio.to_thread(spool.seek, 0)
                done = await asyncio.to_thread(
                    apply_pull_archive, handle.workspace, spool, pushed=handle.pushed,
                    max_bytes=size, max_entries=2 * self._remote_entries(),
                )
        except (SandboxError, OSError, tarfile.TarError) as exc:
            _log.warning(
                "sandbox_broker.pull_failed", name=handle.name,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
            return REMOTE_COPY_FAILED
        _log.info(
            "sandbox_broker.pulled", name=handle.name, written=done.written,
            deleted=done.deleted, skipped=done.skipped,
        )
        return ""

    @contextlib.asynccontextmanager
    async def host_files(self, handle: SandboxHandle) -> AsyncIterator[SandboxHandle]:
        """Hold the mount source's lock while the host reads or writes it.

        No exec of ANY container on this dir runs inside the block, and the
        kill sweep of rule 9 left no process behind. So no sandbox process
        races a host file call (§7.5).
        """
        await self._wait_ready(handle)
        async with handle.lock:
            self._require_live(handle)
            yield handle

    # ── the compute target (SBX-18) ─────────────────────────────────────────

    async def _compute_claim(self, binding: RunBinding) -> SandboxHandle:
        """Reserve a compute slot, or raise :class:`SandboxBusy`. No await inside."""
        settings = self._settings()
        per_org = int(getattr(settings, "compute_max_per_org", 1))
        total = int(getattr(settings, "compute_max_total", 1))
        remote = self._backend() == BACKEND_REMOTE
        async with self._registry_lock:
            if remote and self._other_org_on_box(binding.org):
                # SBX-15: one org per remote box, across every target.
                raise SandboxBusy(ONE_ORG_BUSY)
            mine = [h for h in self._compute_live.values() if h.org == binding.org]
            if binding.name in self._compute_live or len(mine) >= per_org:
                raise SandboxBusy("This organization runs its share of compute. Try again soon.")
            if len(self._compute_live) >= total:
                raise SandboxBusy("The box runs its maximum of compute runs. Try again soon.")
            # `remote` is set under the lock, so every one-org check sees it.
            handle = SandboxHandle(
                binding=binding, lock=asyncio.Lock(), leases=1, last_used=self._clock(),
                remote=remote,
            )
            self._compute_live[binding.name] = handle
            return handle

    def _compute_drop(self, handle: SandboxHandle) -> None:
        handle.removed = True
        if self._compute_live.get(handle.name) is handle:
            del self._compute_live[handle.name]

    async def _compute_start(self, handle: SandboxHandle) -> None:
        settings = self._settings()
        image = pinned_image(settings)
        limits = Limits.from_settings(settings)
        backend = sandbox_backend(settings)
        runtime = sandbox_runtime(settings, backend)
        remote = backend == BACKEND_REMOTE
        uid, gid = _process_ids()
        handle.start_id, handle.container_id = uuid.uuid4().hex, ""
        handle.remote = remote
        argv = build_run_argv(
            binding=handle.binding, mounts=[], uid=uid, gid=gid,
            image=image, limits=limits, start_id=handle.start_id, runtime=runtime,
        )
        # SBX-15: the org admit and `docker run` hold ONE gate, as every
        # start of this broker does. The admit raises SandboxBusy or
        # SandboxUnavailable to refuse.
        async with self._start_gate(remote):
            await self._remote_org_admit(handle.org)
            admitted = True
            if _production_mode(settings) and (
                not remote or "--runtime=runsc" not in argv or not admitted
            ):
                raise SandboxRefused(
                    "Compute starts in production only under runsc, on the remote "
                    "backend, after the org admit."
                )
            await self._run_container(handle, argv)
        if not compute_enabled(handle.org):
            raise SandboxRefused("Compute was switched off for this organization.")
        handle.started_at = handle.last_used = self._clock()
        handle.starting = False
        handle.ready.set()
        _log.info("sandbox_broker.compute_started", name=handle.name, org=handle.org)

    @staticmethod
    def _compute_alive(handle: SandboxHandle) -> None:
        """Refuse when the reaper or the kill switch dropped this container."""
        if handle.removed:
            raise SandboxRefused("This compute run was stopped.")

    async def run_compute(
        self, files: dict[str, bytes], *, timeout_s: int,
    ) -> ComputeResult:
        """Run ``python main.py`` over *files* in a fresh, closed container.

        The org, the member and the thread come from the run binding only. The
        container never serves a second run (D187.1): it is removed at the
        end, on an error and on a timeout too. Raises :class:`SandboxRefused`,
        :class:`SandboxBusy` and :class:`SandboxUnavailable`. Nothing runs on
        the host.
        """
        binding = read_compute_binding()
        tar_in = build_compute_tar(files)
        if not compute_enabled(binding.org):
            raise SandboxRefused("Compute is not enabled for this organization.")
        _compute_production_guard(self._settings())
        try:
            seconds = int(timeout_s)
        except (TypeError, ValueError):
            seconds = COMPUTE_MAX_TIMEOUT_S
        seconds = max(COMPUTE_MIN_TIMEOUT_S, min(seconds, COMPUTE_MAX_TIMEOUT_S))
        await self._await_startup()
        handle = await self._compute_claim(binding)
        self._ensure_reaper()
        began = self._clock()
        try:
            await self._compute_start(handle)
            self._compute_alive(handle)
            put = await self._docker.run(
                ["exec", "-i", handle.name, "tar", "-x", "-C", WORKSPACE_TARGET],
                timeout=_SMALL_TIMEOUT_SECONDS, stdin=tar_in,
            )
            if put.rc != 0:
                _log.warning("sandbox_broker.compute_put_failed", name=handle.name,
                             error=put.stderr[-300:])
                raise SandboxUnavailable("The compute container did not take its inputs.")
            self._compute_alive(handle)
            exec_began = self._clock()
            ran = await self._docker.collect(
                ["exec", "--workdir", WORKSPACE_TARGET, handle.name,
                 "timeout", "-s", "KILL", str(seconds), "python", "main.py"],
                timeout=seconds + _HOST_GRACE_SECONDS, cap=COMPUTE_STREAM_CAP,
            )
            elapsed = self._clock() - exec_began
            killed = ran.rc == 137
            timed_out = ran.timed_out or (killed and elapsed >= seconds)
            out_files: dict[str, bytes] = {}
            if not ran.timed_out:
                self._compute_alive(handle)
                got = await self._docker.collect(
                    ["exec", handle.name, "tar", "-c", "-C", COMPUTE_OUT_TARGET, "."],
                    timeout=_SMALL_TIMEOUT_SECONDS,
                    cap=COMPUTE_MAX_TOTAL_BYTES + 1024 * 1024,
                )
                if got.rc == 0 and not got.truncated and not got.timed_out:
                    out_files = parse_compute_tar(got.stdout)
        finally:
            self._compute_drop(handle)
            await asyncio.shield(self._spawn_background(self._remove(handle)))
        return ComputeResult(
            exit_code=None if (timed_out or killed or ran.rc is None) else ran.rc,
            stdout=ran.stdout.decode("utf-8", errors="replace"),
            stderr=ran.stderr.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            files=out_files,
            duration_ms=int((self._clock() - began) * 1000),
        )

    # ── reaping and sweeping (§7.1 rules 12 and 13) ─────────────────────────

    async def reap_once(self) -> int:
        """Stop a container idle past the TTL, or older than the lifetime.

        "Idle" means released. A container past its lifetime stops even
        when a run still holds it, so a lease that a crashed run never
        released cannot hold a slot for ever. No container stops while an
        exec or a host file call holds its lock.

        On ``docker-remote`` it then removes each orphan of the remote box
        (:meth:`_reap_remote_orphans`), such as a container whose removal
        failed when the ssh link dropped (SBX-15 review P2).
        """
        settings = self._settings()
        ttl = float(getattr(settings, "sandbox_idle_ttl_seconds", 600))
        lifetime = float(getattr(settings, "sandbox_max_lifetime_seconds", 7200))
        now = self._clock()
        async with self._registry_lock:
            victims = [
                h for h in self._live.values()
                if not h.starting and not h.lock.locked()
                and (
                    (h.leases == 0 and now - h.last_used > ttl)
                    or now - h.started_at > lifetime
                )
            ]
            for victim in victims:
                self._drop(victim)
            # SBX-18: a compute container stops when its org left the flag, and
            # when it outlives the longest run plus the grace (a crashed run).
            stale = [
                h for h in self._compute_live.values()
                if not h.starting and (
                    not compute_enabled(h.org)
                    or now - h.started_at
                    > COMPUTE_MAX_TIMEOUT_S + 2 * _HOST_GRACE_SECONDS + _SMALL_TIMEOUT_SECONDS
                )
            ]
            for victim in stale:
                self._compute_drop(victim)
            victims += stale
        await self._remove_all(victims)
        if victims:
            _log.info("sandbox_broker.reaped", count=len(victims))
        if self._is_remote():
            await self._reap_remote_orphans()
        return len(victims)

    def _reaper_interval(self) -> float:
        """The reaper's sleep. The floor is 1 s, so a setting of 0 cannot spin."""
        raw = getattr(self._settings(), "sandbox_reaper_interval_seconds", 60)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 60.0
        return max(1.0, value)

    async def _reap_loop(self) -> None:
        interval = self._reaper_interval()
        while self._live or self._compute_live or self._remote_orphans:
            await asyncio.sleep(interval)
            try:
                await self.reap_once()
            except Exception as exc:
                _log.warning("sandbox_broker.reap_failed", error=str(exc)[:300])

    def _ensure_reaper(self) -> None:
        """Run the reaper while any container lives, or a remote orphan stays."""
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_loop())

    async def sweep(self) -> int:
        """Remove every container labelled ``metorite.sandbox=1``. Never raises.

        The egress proxy carries that label, so it goes too. Then every
        network labelled ``metorite.egress=1`` and every grant go (WS-43g1).

        SBX-15c: when the ``rm -f`` fails, each id goes to the orphan record
        (:meth:`_note_orphans`), so the reaper removes it later. On
        ``docker-remote`` such a container holds the one-org lock.
        """
        ids: list[str] = []
        try:
            found = await self._docker.run(
                ["ps", "-aq", "--filter", f"label={LABEL_SANDBOX}=1"],
                timeout=_SMALL_TIMEOUT_SECONDS,
            )
            self._note_docker(found.rc == 0)
            if found.rc != 0:
                _log.info("sandbox_broker.sweep_skipped", error=found.stderr[-200:])
                return 0
            ids = [line.strip() for line in found.stdout.splitlines() if line.strip()]
            if ids:
                gone = await self._docker.run(["rm", "-f", *ids], timeout=_RUN_TIMEOUT_SECONDS)
                if gone.rc != 0:
                    _log.warning(
                        "sandbox_broker.sweep_remove_failed", count=len(ids),
                        error=gone.stderr[-300:],
                    )
                    self._note_orphans(ids)
                    ids = []  # nothing was removed
        except (SandboxUnavailable, OSError) as exc:
            self._note_orphans(ids)
            self._note_docker(False)
            _log.info("sandbox_broker.sweep_skipped_no_docker", error=str(exc)[:200])
            return 0
        async with self._registry_lock:
            for handle in list(self._live.values()):
                self._drop(handle)
            for handle in list(self._compute_live.values()):
                self._compute_drop(handle)
        # WS-43g1: the proxy went with the labelled containers. Now the egress
        # networks and every grant go too.
        await self._sweep_egress()
        return len(ids)

    async def startup(self) -> None:
        """The startup sweep. It ALWAYS runs, whatever the scope holds.

        A gateway restart ends every run, so every labelled container goes,
        also one that an earlier scope left (§7.1 rule 13). Then the
        sandbox-dir list is trimmed to the dirs that still exist. Never raises.
        """
        removed = await self.sweep()
        try:
            kept = await asyncio.to_thread(self.trim_dir_list)
        except (OSError, SandboxError) as exc:
            _log.warning("sandbox_broker.trim_failed", error=str(exc)[:300])
            kept = -1
        # WS-43v: no container is live, so no skill cover is mounted.
        try:
            await asyncio.to_thread(shutil.rmtree, self._skill_covers_dir(), True)
        except (OSError, SandboxError) as exc:
            _log.warning("sandbox_broker.skill_cover_sweep_failed", error=str(exc)[:300])
        # §16.3: a run-data dir that a crash left goes too. No run is live.
        try:
            run_data = await asyncio.to_thread(remove_all_run_data)
        except (OSError, ValueError) as exc:
            _log.warning("sandbox_broker.run_data_sweep_failed", error=str(exc)[:300])
            run_data = False
        self._run_data.clear()
        _log.info(
            "sandbox_broker.startup", removed=removed, sandbox_dirs=kept,
            run_data_removed=run_data, **self._backend_fields(),
        )

    def _backend_fields(self) -> dict[str, str]:
        """The backend for a log line. The daemon address shows its scheme and host only."""
        settings = self._settings()
        try:
            backend = sandbox_backend(settings)
        except SandboxError:
            return {"backend": "invalid"}
        if backend != BACKEND_REMOTE:
            return {"backend": backend}
        raw = str(getattr(settings, "sandbox_docker_host", "") or "")
        return {"backend": backend, "docker_host": redact_docker_host(raw)}

    def start(self) -> None:
        """Schedule :meth:`startup` on the running loop. The lifespan calls it."""
        if self._startup is None or self._startup.done():
            self._startup = asyncio.get_running_loop().create_task(self.startup())

    async def stop(self) -> None:
        """Cancel the background tasks. The next startup sweeps the containers."""
        for task in (self._reaper, self._startup, self._probe):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._reaper = self._startup = self._probe = None


# ── The module seam ──────────────────────────────────────────────────────────

_BROKER: SandboxBroker | None = None


def get_broker() -> SandboxBroker:
    """The one broker of this process."""
    global _BROKER
    if _BROKER is None:
        _BROKER = SandboxBroker()
    return _BROKER


def is_sandbox_dir(path: str | os.PathLike[str]) -> bool:
    """True when *path* lies in a dir that a container mounts now, or did."""
    return get_broker().is_sandbox_dir(path)


def refuse_if_sandbox_dir(path: str | os.PathLike[str]) -> None:
    """Each host git site calls this first (§7.5 rule A, WS-43e)."""
    get_broker().refuse_if_sandbox_dir(path)


def host_git_allowed(path: str | os.PathLike[str] | None) -> bool:
    """True when host git may run in *path* (§7.5 rule A, WS-43e).

    Each host git site asks this, and it asks :func:`refuse_if_sandbox_dir`.
    An empty path answers ``False``, so a helper with no dir runs nothing. A
    dir that a container mounts now, or mounted, answers ``False``, and the
    site skips with one log line. A container could have written a
    ``.git/config`` or a hook in that dir, and host git would run it.
    Fence WS43-F13, ``tests/unit/test_no_host_git_on_sandbox_dir.py``.
    """
    if not path:
        return False
    try:
        refuse_if_sandbox_dir(path)
    except SandboxRefused:
        _log.info("sandbox_broker.host_git_skipped", path=str(path)[:200])
        return False
    return True


@contextlib.asynccontextmanager
async def delivery_guard(workspace: Path) -> AsyncIterator[bool]:
    """The dir lock of *workspace* for a delegated delivery (PR #656).

    Yields ``True`` when a container on the dir is over its quota, so the
    caller refuses the write (§7.1 rule 10). With no broker in this process,
    no container can mount the dir, so there is no lock to take.
    """
    broker = _BROKER
    if broker is None:
        yield False
        return
    async with broker.host_dir_of(workspace) as source:
        yield bool(broker.writes_refused(source))


async def run_compute(files: dict[str, bytes], *, timeout_s: int) -> ComputeResult:
    """Run ``python main.py`` over *files* in a closed compute container (SBX-18).

    Input names are ``main.py`` or ``inputs/tr_<8 hex>.txt``. The tenant comes
    from the run binding only. See :meth:`SandboxBroker.run_compute`.
    """
    return await get_broker().run_compute(files, timeout_s=timeout_s)


def covers(agent: str, org: str) -> bool:
    """True when the broker runs every code path of *agent* in *org* (§7.7, §16.3).

    ``False`` for every ``code_task`` and ``app_builder`` agent until WS-43f.
    WS-3a reads this. See :meth:`SandboxBroker.covers`.
    """
    return get_broker().covers(agent, org)


def lifts_shell_block(agent: str, org: str) -> bool:
    """True when a cover gives *agent* its host shell tools back (D85, §7.9).

    The D85 seam (``_tool_injection._sandbox_covers``) asks this, never
    :func:`covers` alone, since the merge of PR #598. A cover lifts the block only for a target whose
    shell tools route to the broker (WS-43f). The ``projects`` target never
    routes them, so a true ``covers()`` for projects-assistant keeps
    ``code_task``, ``run_script`` and ``install_dependency`` withheld
    (§16.3 condition 3). The target is checked first, so this never calls
    back into the seam.
    """
    if target_for_agent(agent) == PROJECTS_TARGET:
        return False
    return covers(agent, org)


def _host_shell_withheld(agent: str) -> bool:
    """§16.3 condition 3: the D85 seam keeps the host shell tools from *agent*.

    The seam is ``orchestrator._tool_injection._withheld_shell_tools`` (PR
    #598). Without it, nothing withholds ``code_task`` and ``run_script``
    from projects-assistant, so no organization is covered. The sandbox
    tools also refuse at run time when the run holds a host shell tool
    (``acb_skills.sandbox_tools``), so the two checks fail closed each on
    its own.
    """
    del agent
    try:
        from orchestrator import _tool_injection
    except ImportError:
        return False
    return callable(getattr(_tool_injection, "_withheld_shell_tools", None))


async def end_sandbox_run() -> bool:
    """The end of a run: delete its run-data dir (§16.3). Never raises.

    A no-op that touches no file when no sandbox ran in this process.
    """
    broker = _BROKER
    if broker is None:
        return False
    try:
        return await broker.end_run()
    except Exception as exc:  # the run's own finally must never fail
        _log.warning("sandbox_broker.end_run_failed", error=str(exc)[:300])
        return False


def start_sandbox_broker() -> None:
    """The gateway lifespan calls this at every startup (§7.1 rule 13)."""
    get_broker().start()


async def stop_sandbox_broker() -> None:
    """The gateway lifespan calls this at shutdown."""
    if _BROKER is not None:
        await _BROKER.stop()
