"""The sandbox broker — the ONE module that runs ``docker`` for a sandbox.

WS-43c. Spec ``project-docs/specs/maf_coding_engine.md`` §7.1 (D83).

A tool asks the broker for a container and for an exec, and never calls
``docker`` itself. The broker owns the Docker socket and every container of
the coding sandbox: one container per (organization, agent, thread), with no
network, a read-only root, a non-root uid, no capabilities, CPU, memory and
pids limits, and ONE read-write mount, the run's own working dir.

**Dark by construction.** ``acquire()`` refuses unless ``MAF_CODING_SCOPE``
names the bound agent's target for the bound organization, and the scope is
empty by default. Nothing on a live path calls the broker yet (WS-43d and
WS-43e wire the tools). The startup sweep is the one part that always runs.

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
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import time
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("orchestrator.sandbox_broker")

# ── Names ────────────────────────────────────────────────────────────────────

CONTAINER_PREFIX = "mtr-sbx-"
LABEL_SANDBOX = "metorite.sandbox"
LABEL_ORG = "metorite.org"
LABEL_AGENT = "metorite.agent"
LABEL_THREAD = "metorite.thread"

WORKSPACE_TARGET = "/workspace"
GIT_COVER_TARGET = "/workspace/.git"

#: The agent whose target is ``app_builder``. Every other agent is ``code_task``
#: (§7.7 condition 1).
APP_BUILDER_AGENT = "app-builder"
MAF_CODING_TARGETS = frozenset({"code_task", "app_builder"})

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

_MIB = 1024 * 1024
_HOST_GRACE_SECONDS = 15.0
_RUN_TIMEOUT_SECONDS = 60.0
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

    A target is ``code_task`` or ``app_builder``. An org is one organization
    id, or ``*`` for every organization. Raises :class:`ValueError` on an
    unknown target or an entry with no org, so a typo never turns on a target
    that nobody named.
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
    """``app_builder`` for app-builder, ``code_task`` for every other agent."""
    return "app_builder" if agent == APP_BUILDER_AGENT else "code_task"


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

    @property
    def thread_hash(self) -> str:
        return _digest(self.thread)

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
    dir under the Custom Apps root. Anything else is refused.
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
        return _check_strictly_under(raw, _custom_apps_root())
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
    return RunBinding(org=org, agent=agent, thread=thread, instance=instance, workspace=source)


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


def mount_list(
    binding: RunBinding,
    cover: GitCover,
    readonly_mounts: Sequence[tuple[Path, str]] = (),
) -> list[Mount]:
    """THE one function that builds the mounts of a container.

    Every start and every restart calls it, and so will a grant and a revoke
    (WS-43g). So no recreate can miss the cover on ``/workspace/.git``.

    - The run's working dir at ``/workspace``, read-write.
    - A ``.git`` at the root of the working dir is covered by an empty
      read-only bind mount, so the container can neither read nor write it.
    - A ``.git`` deeper in the working dir is refused, and so is a root
      ``.git`` that is a link.
    - *readonly_mounts* come from code, never from a caller. WS-43h adds the
      app-builder list. A source with ``.git`` at any depth is refused.
    """
    ws = binding.workspace
    _check_source_text(ws)
    if not ws.is_dir() or _is_link(ws) or ws.resolve() != ws:
        raise SandboxRefused("The working dir is not a real dir.")
    if _git_entries(ws, include_root=False):
        raise SandboxRefused(
            "The working dir holds a .git below its root. Only a root .git is allowed."
        )
    mounts = [Mount(ws, WORKSPACE_TARGET, readonly=False)]
    git_cover = _git_cover_mount(ws, cover)
    if git_cover is not None:
        mounts.append(git_cover)
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


def build_run_argv(
    *,
    binding: RunBinding,
    mounts: Sequence[Mount],
    uid: int,
    gid: int,
    image: str,
    limits: Limits,
) -> list[str]:
    """The ``docker run`` arguments, with no ``docker`` in front."""
    if uid == 0 or gid == 0:
        raise SandboxRefused("The sandbox never runs as uid 0 or gid 0.")
    argv = ["run", "-d", "--rm", "--name", binding.name]
    for key, value in binding.labels().items():
        argv += ["--label", f"{key}={value}"]
    argv += [
        "--network", "none",
        "--read-only",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,size={limits.tmpfs_mb}m",
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
    for mount in mounts:
        argv += mount.args()
    for key, value in sandbox_env(binding.thread_hash).items():
        argv += ["--env", f"{key}={value}"]
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

#: Kills every process except init, the keep-alive and itself, then checks
#: again. A child that called ``setsid`` or forked twice dies too, because the
#: sweep walks ``/proc`` and not a process group. A zombie is already dead.
#: Exit 0 prints ``clean``. Exit 3 prints the PIDs that survived.
KILL_SWEEP = """keep="$1"; self=$$; n=0
while :; do
  left=""
  for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    case "$pid" in 1|"$keep"|"$self") continue ;; esac
    st=""
    while read -r k v _; do
      if [ "$k" = "State:" ]; then st="$v"; break; fi
    done 2>/dev/null < "$p/status"
    [ -z "$st" ] && continue
    [ "$st" = "Z" ] && continue
    kill -9 "$pid" 2>/dev/null
    left="$left $pid"
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


@dataclass(frozen=True)
class StreamResult:
    rc: int | None
    output: str
    total_bytes: int
    truncated: bool
    stderr: str
    host_timed_out: bool


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


class DockerCLI:
    """Runs the ``docker`` CLI. The only process starts in this module."""

    def _binary(self) -> str:
        path = shutil.which("docker")
        if not path:
            raise SandboxUnavailable("Docker is not available on this box.")
        return path

    async def run(self, args: Sequence[str], *, timeout: float) -> DockerResult:
        """Run one short docker command to its end."""
        proc = await asyncio.create_subprocess_exec(
            self._binary(), *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            return DockerResult(-1, "", f"docker {args[0] if args else ''} timed out")
        return DockerResult(
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
        )

    async def stream(
        self, args: Sequence[str], *, timeout: float, head: int, tail: int,
    ) -> StreamResult:
        """Run ``docker exec`` and keep a capped copy of its output."""
        proc = await asyncio.create_subprocess_exec(
            self._binary(), *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
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
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            rc = None
        return StreamResult(
            rc=rc,
            output=capped.text(),
            total_bytes=capped.total,
            truncated=capped.truncated,
            stderr=err.decode("utf-8", errors="replace"),
            host_timed_out=host_timed_out,
        )


# ── Handles and results ──────────────────────────────────────────────────────


@dataclass(eq=False)
class SandboxHandle:
    """One container of the bound run. Get it from :meth:`SandboxBroker.acquire`."""

    binding: RunBinding
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    leases: int = 0
    started_at: float = 0.0
    last_used: float = 0.0
    init_pid: int = 1
    keepalive_pid: int | None = None
    starting: bool = True
    failed: bool = False
    removed: bool = False
    over_quota: bool = False
    workspace_bytes: int = 0
    network: str = "none"

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


# ── Host-side measures ───────────────────────────────────────────────────────


def _dir_size_bytes(root: Path) -> int:
    """The apparent size of every file below *root*, with no link followed."""
    total = 0
    stack = [root]
    while stack:
        here = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


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
    ) -> None:
        self._docker = docker or DockerCLI()
        self._clock = clock
        self._live: dict[str, SandboxHandle] = {}
        self._registry_lock = asyncio.Lock()
        self._dirs: set[Path] | None = None
        self._reaper: asyncio.Task[None] | None = None
        self._startup: asyncio.Task[None] | None = None

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

    # ── the sandbox-dir list (§7.1 rule 5, §7.5 rule A) ─────────────────────

    def _load_dirs(self) -> set[Path]:
        if self._dirs is None:
            try:
                text = self._dir_list_file().read_text(encoding="utf-8")
            except FileNotFoundError:
                text = ""
            self._dirs = {Path(line) for line in text.splitlines() if line.strip()}
        return self._dirs

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
        """True when *path* lies in a dir that a container mounts now, or did."""
        try:
            real = Path(path).resolve()
        except (OSError, RuntimeError):
            return True
        dirs = set(self._load_dirs()) | {h.workspace for h in self._live.values()}
        return any(real == d or real.is_relative_to(d) for d in dirs)

    def refuse_if_sandbox_dir(self, path: str | os.PathLike[str]) -> None:
        """Raise when host git would run on a dir that a container could write."""
        if self.is_sandbox_dir(path):
            raise SandboxRefused(
                f"Host git refuses {str(path)[:200]!r}: a sandbox container "
                "mounts this dir, or mounted it."
            )

    # ── coverage (§7.7) ─────────────────────────────────────────────────────

    @staticmethod
    def covers(agent: str, org: str) -> bool:
        """``False`` for every agent until WS-43f is built (§7.7).

        A report, a log line or a WS-3a check must not count an agent as
        covered only because its ``code_task`` runs in the broker.
        """
        del agent, org
        return False

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

    async def measure_workspace(self, handle: SandboxHandle) -> int:
        """Measure the whole working dir, and set the quota flag."""
        used = await asyncio.to_thread(_dir_size_bytes, handle.workspace)
        quota_mb = int(getattr(self._settings(), "sandbox_workspace_quota_mb", 2048))
        handle.workspace_bytes = used
        handle.over_quota = used > quota_mb * _MIB
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
            quota_mb = int(getattr(self._settings(), "sandbox_workspace_quota_mb", 2048))
            raise SandboxRefused(
                f"The working dir is over its quota of {quota_mb} MB. "
                "Delete files to free space, then run the command again."
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
        """
        binding = read_run_binding()
        if not maf_coding_scope_allows(target_for_agent(binding.agent), binding.org):
            raise SandboxRefused(
                "MAF_CODING_SCOPE does not name this target for this organization, "
                "so the sandbox starts nothing."
            )
        await self._await_startup()
        self._check_free_disk()
        handle, victims, fresh = await self._claim(binding)
        await self._remove_all(victims)
        if fresh:
            await self._start_fresh(handle)
            return handle
        if handle.starting:
            async with handle.lock:  # the starter holds it until the start ends
                pass
        try:
            self._require_live(handle)
        except SandboxError:
            handle.leases = max(0, handle.leases - 1)
            raise
        return handle

    async def _claim(
        self, binding: RunBinding,
    ) -> tuple[SandboxHandle, list[SandboxHandle], bool]:
        """Reuse on a full match, or reserve a new handle within the caps."""
        async with self._registry_lock:
            victims: list[SandboxHandle] = []
            held = self._live.get(binding.name)
            if held is not None:
                if held.org != binding.org or held.agent != binding.agent:
                    raise SandboxRefused("A sandbox of another tenant holds this name.")
                if held.workspace == binding.workspace and not held.failed:
                    held.leases += 1
                    held.last_used = self._clock()
                    return held, victims, False
                if held.leases or held.starting or held.lock.locked():
                    raise SandboxBusy("This thread's sandbox is busy. Try again when it finishes.")
                self._drop(held)
                victims.append(held)
            victims += self._make_room(binding.org)
            handle = SandboxHandle(binding=binding, leases=1, last_used=self._clock())
            await handle.lock.acquire()
            self._live[binding.name] = handle
            return handle, victims, True

    def _drop(self, handle: SandboxHandle) -> None:
        handle.removed = True
        if self._live.get(handle.name) is handle:
            del self._live[handle.name]

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

    async def _start_fresh(self, handle: SandboxHandle) -> None:
        """Start a reserved handle. The caller holds its lock."""
        try:
            await self._start(handle)
        except BaseException:
            handle.failed = True
            async with self._registry_lock:
                self._drop(handle)
            raise
        finally:
            handle.starting = False
            handle.lock.release()
        self._ensure_reaper()

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
            await self._remove(handle.name)

    # ── start / restart / remove ────────────────────────────────────────────

    def _mounts_for(self, binding: RunBinding) -> list[Mount]:
        return mount_list(binding, self.git_cover())

    async def _start(self, handle: SandboxHandle) -> None:
        """Run the container and record its init and keep-alive PIDs."""
        settings = self._settings()
        image = pinned_image(settings)
        limits = Limits.from_settings(settings)
        uid, gid = _process_ids()
        mounts = await asyncio.to_thread(self._mounts_for, handle.binding)
        argv = build_run_argv(
            binding=handle.binding, mounts=mounts, uid=uid, gid=gid,
            image=image, limits=limits,
        )
        await asyncio.to_thread(self._record_dir, handle.workspace)
        result = await self._docker.run(argv, timeout=_RUN_TIMEOUT_SECONDS)
        if result.rc != 0 and "already in use" in result.stderr:
            await self._clear_stale_name(handle)
            result = await self._docker.run(argv, timeout=_RUN_TIMEOUT_SECONDS)
        if result.rc != 0:
            _log.warning(
                "sandbox_broker.start_failed", name=handle.name,
                org=handle.org, error=result.stderr[-400:],
            )
            raise SandboxUnavailable("The sandbox container did not start.")
        try:
            handle.keepalive_pid = await self._probe_keepalive(handle.name)
        except SandboxUnavailable:
            await self._remove(handle.name)
            raise
        handle.init_pid = 1
        handle.started_at = handle.last_used = self._clock()
        handle.network = "none"
        _log.info(
            "sandbox_broker.started", name=handle.name, org=handle.org,
            agent=handle.agent, keepalive_pid=handle.keepalive_pid,
        )

    async def _clear_stale_name(self, handle: SandboxHandle) -> None:
        """Remove a stale container of the SAME tenant that holds the name."""
        found = await self._docker.run(
            ["inspect", "--format", "{{json .Config.Labels}}", handle.name],
            timeout=_SMALL_TIMEOUT_SECONDS,
        )
        try:
            labels = json.loads(found.stdout or "null") or {}
        except json.JSONDecodeError:
            labels = {}
        if labels.get(LABEL_ORG) != handle.org or labels.get(LABEL_AGENT) != handle.agent:
            raise SandboxRefused("A container of another tenant holds this sandbox name.")
        await self._remove(handle.name)

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
        does, so the cover on ``/workspace/.git`` comes back. The network
        stays ``none``. Raises :class:`SandboxUnavailable` when it fails.
        """
        _log.info("sandbox_broker.restarting", name=handle.name, reason=reason)
        await self._remove(handle.name)
        try:
            await self._start(handle)
        except SandboxError as exc:
            async with self._registry_lock:
                self._drop(handle)
            raise SandboxUnavailable(
                f"The sandbox restart failed, so the sandbox is gone: {exc} "
                "Nothing runs on the host."
            ) from exc

    async def _remove(self, name: str) -> None:
        result = await self._docker.run(["rm", "-f", name], timeout=_SMALL_TIMEOUT_SECONDS)
        if result.rc != 0 and "No such container" not in result.stderr:
            _log.warning("sandbox_broker.remove_failed", name=name, error=result.stderr[-300:])

    async def _remove_all(self, handles: Iterable[SandboxHandle]) -> None:
        for handle in handles:
            await self._remove(handle.name)

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
        """Run one command in the container, under the container's lock.

        One exec at a time per container. The disk floor and the quota are
        checked first. After the command, every stray process dies, and the
        container restarts if one survives. A broken container restarts once,
        and the command does not run again.
        """
        if not isinstance(command, str) or not command.strip():
            raise SandboxRefused("The command is empty.")
        timeout = self._clamp_timeout(timeout_s)
        self._require_live(handle)
        async with handle.lock:
            self._require_live(handle)
            self._check_free_disk()
            await self._check_quota(handle)
            return await self._exec_locked(handle, command, timeout)

    async def _exec_locked(
        self, handle: SandboxHandle, command: str, timeout: int,
    ) -> ExecResult:
        cap = int(getattr(self._settings(), "sandbox_output_cap_bytes", 12288))
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
        if restarted:
            await self._restart(handle, "survivor")
        await self.measure_workspace(handle)
        handle.last_used = self._clock()
        timed_out = result.host_timed_out or (result.rc == 137 and elapsed >= timeout - 0.5)
        return ExecResult(
            exit_code=137 if result.host_timed_out else result.rc,
            output=result.output,
            output_bytes=result.total_bytes,
            truncated=result.truncated,
            duration_s=elapsed,
            timed_out=timed_out,
            restarted=restarted,
            message=SURVIVOR_MESSAGE if restarted else "",
        )

    @contextlib.asynccontextmanager
    async def host_files(self, handle: SandboxHandle) -> AsyncIterator[SandboxHandle]:
        """Hold the container's lock while the host reads or writes its dir.

        No exec runs inside the block, and the kill sweep of rule 9 left no
        process behind. So no sandbox process races a host file call (§7.5).
        """
        self._require_live(handle)
        async with handle.lock:
            self._require_live(handle)
            yield handle

    # ── reaping and sweeping (§7.1 rules 12 and 13) ─────────────────────────

    async def reap_once(self) -> int:
        """Stop idle containers past the idle TTL or the lifetime."""
        settings = self._settings()
        ttl = float(getattr(settings, "sandbox_idle_ttl_seconds", 600))
        lifetime = float(getattr(settings, "sandbox_max_lifetime_seconds", 7200))
        now = self._clock()
        async with self._registry_lock:
            victims = [
                h for h in self._live.values()
                if h.leases == 0 and not h.starting and not h.lock.locked()
                and (now - h.last_used > ttl or now - h.started_at > lifetime)
            ]
            for victim in victims:
                self._drop(victim)
        await self._remove_all(victims)
        if victims:
            _log.info("sandbox_broker.reaped", count=len(victims))
        return len(victims)

    async def _reap_loop(self) -> None:
        interval = float(getattr(self._settings(), "sandbox_reaper_interval_seconds", 60))
        while self._live:
            await asyncio.sleep(max(1.0, interval))
            try:
                await self.reap_once()
            except Exception as exc:
                _log.warning("sandbox_broker.reap_failed", error=str(exc)[:300])

    def _ensure_reaper(self) -> None:
        """Run the reaper while any container lives."""
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_loop())

    async def sweep(self) -> int:
        """Remove every container labelled ``metorite.sandbox=1``."""
        try:
            found = await self._docker.run(
                ["ps", "-aq", "--filter", f"label={LABEL_SANDBOX}=1"],
                timeout=_SMALL_TIMEOUT_SECONDS,
            )
        except (SandboxUnavailable, OSError) as exc:
            _log.info("sandbox_broker.sweep_skipped_no_docker", error=str(exc)[:200])
            return 0
        if found.rc != 0:
            _log.info("sandbox_broker.sweep_skipped", error=found.stderr[-200:])
            return 0
        ids = [line.strip() for line in found.stdout.splitlines() if line.strip()]
        if ids:
            await self._docker.run(["rm", "-f", *ids], timeout=_RUN_TIMEOUT_SECONDS)
        async with self._registry_lock:
            for handle in list(self._live.values()):
                self._drop(handle)
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
        _log.info("sandbox_broker.startup", removed=removed, sandbox_dirs=kept)

    def start(self) -> None:
        """Schedule :meth:`startup` on the running loop. The lifespan calls it."""
        if self._startup is None or self._startup.done():
            self._startup = asyncio.get_running_loop().create_task(self.startup())

    async def stop(self) -> None:
        """Cancel the background tasks. The next startup sweeps the containers."""
        for task in (self._reaper, self._startup):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._reaper = self._startup = None


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


def covers(agent: str, org: str) -> bool:
    """``False`` for every agent until WS-43f (§7.7). WS-3a reads this."""
    return SandboxBroker.covers(agent, org)


def start_sandbox_broker() -> None:
    """The gateway lifespan calls this at every startup (§7.1 rule 13)."""
    get_broker().start()


async def stop_sandbox_broker() -> None:
    """The gateway lifespan calls this at shutdown."""
    if _BROKER is not None:
        await _BROKER.stop()
