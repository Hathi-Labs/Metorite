"""The sandbox broker — the ONE module that runs ``docker`` for a sandbox.

WS-43c. Spec ``project-docs/specs/maf_coding_engine.md`` §7.1 (D83).

A tool asks the broker for a container and for an exec, and never calls
``docker`` itself. The broker owns the Docker socket and every container of
the coding sandbox: one container per (organization, agent, thread), with no
network, a read-only root, a non-root uid, no capabilities, CPU, memory and
pids limits, and ONE read-write mount, the run's own working dir.

**Dark by construction.** ``acquire()`` refuses unless ``MAF_CODING_SCOPE``
names the bound agent's target for the bound organization, and the scope is
empty by default. The one live caller is ``acb_skills.sandbox_tools`` (WS-43d),
for projects-assistant under ``projects:<org>`` (§16.3), and only when
:func:`covers` is true. The startup sweep is the one part that always runs.

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
import uuid
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
#: A fresh id per container start. The broker finds and removes a start
#: by this label or by its container id, NEVER by the name, so a late
#: removal cannot kill a newer container of the same name.
LABEL_START = "metorite.start"

WORKSPACE_TARGET = "/workspace"
GIT_COVER_TARGET = "/workspace/.git"
#: The two nested mounts of the ``projects`` target (§16.3, D86). They lie
#: inside ``/workspace``, and no other target gets them.
OUTPUTS_TARGET = "/workspace/outputs"
RUN_DATA_TARGET = "/workspace/.run"
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
PROJECTS_AGENT = "projects-assistant"
PROJECTS_TARGET = "projects"
MAF_CODING_TARGETS = frozenset({"code_task", "app_builder", PROJECTS_TARGET})

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
_HEALTH_TTL_SECONDS = 60.0
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

    A target is ``code_task``, ``app_builder`` or ``projects`` (D86, §16.3).
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
    and ``code_task`` for every other agent."""
    if agent == APP_BUILDER_AGENT:
        return "app_builder"
    if agent == PROJECTS_AGENT:
        return PROJECTS_TARGET
    return "code_task"


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


def prepare_projects_dirs(binding: RunBinding) -> None:
    """Make the thread's output folder, the ``.run`` placeholder and the run-data dir.

    The ``projects`` target only (§16.3). Each dir is made with the safe
    opener, so no part of its path can be a link, just before the start. A
    plain file or a link in the way raises :class:`SandboxRefused`.
    """
    from acb_skills import safe_open

    try:
        safe_open.ensure_dir(binding.workspace, binding.outputs_rel)
        safe_open.ensure_dir(binding.workspace, RUN_DATA_MOUNTPOINT)
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


def projects_mounts(binding: RunBinding) -> list[Mount]:
    """The two nested read-write mounts of the ``projects`` target (§16.3).

    - ``outputs/<thread slug>/`` at ``/workspace/outputs``, over the shared
      ``outputs/``. So a container sees only its own thread's outputs, and
      never the parent folder or another thread's folder. It is the one
      nested mount that the broker allows within its own workspace.
    - The run-data dir at ``/workspace/.run``, in this thread's container
      only. It lies under ``state_root()/.run-data``, outside every kept
      folder, so the blob store never holds it.
    - The partition marker ``.cc-instance`` at itself, READ-ONLY. The
      gateway's write-through and fault-in read it, so a container must not
      rewrite it.

    :func:`prepare_projects_dirs` makes the dirs. This checks them again.
    """
    ws = binding.workspace
    try:
        outputs = _real_dir(ws / binding.outputs_rel)
        _real_dir(ws / RUN_DATA_MOUNTPOINT)
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
        Mount(run_data, RUN_DATA_TARGET, readonly=False),
        Mount(marker, INSTANCE_MARKER_TARGET, readonly=True),
    ]


def mount_list(
    binding: RunBinding,
    cover: GitCover,
    readonly_mounts: Sequence[tuple[Path, str]] = (),
) -> list[Mount]:
    """THE one function that builds the mounts of a container.

    Every start and every restart calls it, and so will a grant and a revoke
    (WS-43g). So no recreate can miss the cover on ``/workspace/.git``, or
    the cover of a thread's output folder.

    - The run's working dir at ``/workspace``, read-write. For the
      ``projects`` target it is READ-ONLY (review P1, fix round 1): every
      thread of one organization mounts the same dir, so a thread that could
      write it could plant code that another member's thread runs beside that
      member's run data. Its only writable paths are its own output folder
      and its run data, the two nested mounts below.
    - A ``.git`` at the root of the working dir is covered by an empty
      read-only bind mount, so the container can neither read nor write it.
    - A ``.git`` deeper in the working dir is refused, and so is a root
      ``.git`` that is a link.
    - The ``projects`` target adds its two nested mounts
      (:func:`projects_mounts`). No other target gets them.
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
    shared = binding.target == PROJECTS_TARGET
    mounts = [Mount(ws, WORKSPACE_TARGET, readonly=shared)]
    git_cover = _git_cover_mount(ws, cover)
    if git_cover is not None:
        mounts.append(git_cover)
    if binding.target == PROJECTS_TARGET:
        mounts += projects_mounts(binding)
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
    start_id: str,
) -> list[str]:
    """The ``docker run`` arguments, with no ``docker`` in front.

    ``--read-only`` always comes with the ``/tmp`` tmpfs. The image sets
    ``HOME=/tmp``, and tools such as matplotlib fail with no writable home.
    """
    if uid == 0 or gid == 0:
        raise SandboxRefused("The sandbox never runs as uid 0 or gid 0.")
    if not re.fullmatch(r"[0-9a-f]{32}", start_id):
        raise SandboxRefused("The start id is not valid.")
    argv = ["run", "-d", "--rm", "--name", binding.name]
    for key, value in {**binding.labels(), LABEL_START: start_id}.items():
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
    env = sandbox_env(binding.thread_hash)
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
    for key, value in env.items():
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
    """Runs the ``docker`` CLI. The only process starts in this module.

    A timeout OR a cancel kills the CLI process and reaps it, so a cancelled
    run never leaves a ``docker`` process behind (review P1-b).
    """

    def _binary(self) -> str:
        path = shutil.which("docker")
        if not path:
            raise SandboxUnavailable("Docker is not available on this box.")
        return path

    async def _spawn(self, args: Sequence[str]) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            self._binary(), *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(BaseException):
            await asyncio.shield(proc.wait())

    async def run(self, args: Sequence[str], *, timeout: float) -> DockerResult:
        """Run one short docker command to its end."""
        proc = await self._spawn(args)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            await self._kill(proc)
            return DockerResult(-1, "", f"docker {args[0] if args else ''} timed out")
        except BaseException:
            await self._kill(proc)
            raise
        return DockerResult(
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
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
            stderr=err.decode("utf-8", errors="replace"),
            host_timed_out=host_timed_out,
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
        """True when *path* lies in a dir that a container mounts now, or did."""
        try:
            real = Path(path).resolve()
        except (OSError, RuntimeError):
            return True
        return any(real == d or real.is_relative_to(d) for d in self._sandbox_dirs())

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
        if not self.healthy():
            return False
        return _host_shell_withheld(agent)

    def healthy(self) -> bool:
        """§7.7 condition 4: Docker answers, and the free-space floor holds.

        The image must be pinned too, or no container can start. The Docker
        answer is cached for ``_HEALTH_TTL_SECONDS``. A stale answer starts a
        probe in the background and is used until the probe ends. Before the
        first answer, the broker is not healthy, so a flip fails closed.
        """
        settings = self._settings()
        try:
            pinned_image(settings)
        except SandboxError:
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
        if not maf_coding_scope_allows(target_for_agent(binding.agent), binding.org):
            raise SandboxRefused(
                "MAF_CODING_SCOPE does not name this target for this organization, "
                "so the sandbox starts nothing."
            )
        if binding.target == PROJECTS_TARGET:
            _require_thread_slug(binding)
        await self._await_startup()
        self._check_free_disk()
        if not self._reusable(binding):
            await self._preflight(binding)
        handle, victims, fresh = await self._claim(binding)
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

    def _reusable(self, binding: RunBinding) -> bool:
        held = self._live.get(binding.name)
        return (
            held is not None and held.workspace == binding.workspace
            and not held.failed and not held.stale
        )

    async def _preflight(self, binding: RunBinding) -> None:
        """Check every input of a start BEFORE a container is evicted for it.

        The image, the limits, the uid, the state dir and the mounts. A start
        that would fail must not cost another run its idle container.
        """
        settings = self._settings()
        pinned_image(settings)
        Limits.from_settings(settings)
        uid, gid = _process_ids()
        if uid == 0 or gid == 0:
            raise SandboxRefused("The sandbox never runs as uid 0 or gid 0.")
        await self._mounts_for(binding)

    async def _claim(
        self, binding: RunBinding,
    ) -> tuple[SandboxHandle, list[SandboxHandle], bool]:
        """Reuse on a full match, or reserve a new handle within the caps.

        No ``await`` runs in here, so no cancel can land between the
        decision and the change of the registry.
        """
        async with self._registry_lock:
            victims: list[SandboxHandle] = []
            held = self._live.get(binding.name)
            if held is not None:
                if held.org != binding.org or held.agent != binding.agent:
                    raise SandboxRefused("A sandbox of another tenant holds this name.")
                if held.workspace == binding.workspace and not held.failed and not held.stale:
                    held.leases += 1
                    held.last_used = self._clock()
                    return held, victims, False
                if held.leases or held.starting or held.lock.locked():
                    raise SandboxBusy("This thread's sandbox is busy. Try again when it finishes.")
                self._drop(held)
                victims.append(held)
            victims += self._make_room(binding.org)
            handle = SandboxHandle(
                binding=binding, lock=self._lock_for(binding.workspace),
                leases=1, last_used=self._clock(),
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
        if binding.target == PROJECTS_TARGET:
            prepare_projects_dirs(binding)
        return mount_list(binding, self.git_cover())

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

    async def ensure_thread_dirs(self, binding: RunBinding) -> None:
        """Make the thread's output folder and run-data dir before any start.

        The file tools call it, so a write to ``outputs/`` or ``.run/`` lands
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
        """
        if not self._run_data:
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
        rel = self._run_data.get((workspace, thread))
        if rel is None:
            return False
        async with self._lock_for(workspace):
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
        _log.info("sandbox_broker.run_data_deleted", removed=removed, containers=len(idle))
        return removed

    async def _start(self, handle: SandboxHandle) -> None:
        """Run the container and record its id, and its init and keep-alive PIDs.

        On an error or a cancel, the container of THIS start is removed by its
        id, or by its start label when ``docker run`` printed no id yet.
        """
        settings = self._settings()
        image = pinned_image(settings)
        limits = Limits.from_settings(settings)
        uid, gid = _process_ids()
        mounts = await self._mounts_for(handle.binding)
        handle.start_id, handle.container_id = uuid.uuid4().hex, ""
        argv = build_run_argv(
            binding=handle.binding, mounts=mounts, uid=uid, gid=gid,
            image=image, limits=limits, start_id=handle.start_id,
        )
        await asyncio.to_thread(self._record_dir, handle.workspace)
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
        handle.init_pid = 1
        handle.started_at = handle.last_used = self._clock()
        handle.network = "none"
        _log.info(
            "sandbox_broker.started", name=handle.name, org=handle.org,
            agent=handle.agent, keepalive_pid=handle.keepalive_pid,
        )

    async def _run_container(self, handle: SandboxHandle, argv: list[str]) -> None:
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
        does, so the cover on ``/workspace/.git`` comes back. The network
        stays ``none``. Raises :class:`SandboxUnavailable` when it fails.
        """
        _log.info("sandbox_broker.restarting", name=handle.name, reason=reason)
        await self._remove(handle)
        try:
            await self._start(handle)
        except SandboxError as exc:
            async with self._registry_lock:
                self._drop(handle)
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
        if not ids:
            return
        result = await self._docker.run(["rm", "-f", *ids], timeout=_SMALL_TIMEOUT_SECONDS)
        if result.rc != 0 and "No such container" not in result.stderr:
            _log.warning("sandbox_broker.remove_failed", ids=ids, error=result.stderr[-300:])

    async def _remove(self, handle: SandboxHandle) -> None:
        await self._remove_container(handle.container_id, handle.start_id)

    async def _remove_all(self, handles: Iterable[SandboxHandle]) -> None:
        for handle in list(handles):
            await self._remove(handle)

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
        """Hold the mount source's lock while the host reads or writes it.

        No exec of ANY container on this dir runs inside the block, and the
        kill sweep of rule 9 left no process behind. So no sandbox process
        races a host file call (§7.5).
        """
        await self._wait_ready(handle)
        async with handle.lock:
            self._require_live(handle)
            yield handle

    # ── reaping and sweeping (§7.1 rules 12 and 13) ─────────────────────────

    async def reap_once(self) -> int:
        """Stop a container idle past the TTL, or older than the lifetime.

        "Idle" means released. A container past its lifetime stops even
        when a run still holds it, so a lease that a crashed run never
        released cannot hold a slot for ever. No container stops while an
        exec or a host file call holds its lock.
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
        await self._remove_all(victims)
        if victims:
            _log.info("sandbox_broker.reaped", count=len(victims))
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
        while self._live:
            await asyncio.sleep(interval)
            try:
                await self.reap_once()
            except Exception as exc:
                _log.warning("sandbox_broker.reap_failed", error=str(exc)[:300])

    def _ensure_reaper(self) -> None:
        """Run the reaper while any container lives."""
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_loop())

    async def sweep(self) -> int:
        """Remove every container labelled ``metorite.sandbox=1``. Never raises."""
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
                await self._docker.run(["rm", "-f", *ids], timeout=_RUN_TIMEOUT_SECONDS)
        except (SandboxUnavailable, OSError) as exc:
            self._note_docker(False)
            _log.info("sandbox_broker.sweep_skipped_no_docker", error=str(exc)[:200])
            return 0
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
        # §16.3: a run-data dir that a crash left goes too. No run is live.
        try:
            run_data = await asyncio.to_thread(remove_all_run_data)
        except (OSError, ValueError) as exc:
            _log.warning("sandbox_broker.run_data_sweep_failed", error=str(exc)[:300])
            run_data = False
        self._run_data.clear()
        _log.info(
            "sandbox_broker.startup", removed=removed, sandbox_dirs=kept,
            run_data_removed=run_data,
        )

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


def covers(agent: str, org: str) -> bool:
    """True when the broker runs every code path of *agent* in *org* (§7.7, §16.3).

    ``False`` for every ``code_task`` and ``app_builder`` agent until WS-43f.
    WS-3a reads this. See :meth:`SandboxBroker.covers`.
    """
    return get_broker().covers(agent, org)


def lifts_shell_block(agent: str, org: str) -> bool:
    """True when a cover gives *agent* its host shell tools back (D85, §7.9).

    The D85 seam (``_tool_injection._sandbox_covers``) asks this, never
    :func:`covers` alone. A cover lifts the block only for a target whose
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
    #598). Until it is on this branch, nothing withholds ``code_task`` and
    ``run_script`` from projects-assistant, so no organization is covered.
    The sandbox tools also refuse at run time when the run holds a host
    shell tool (``acb_skills.sandbox_tools``), so the two checks fail closed
    each on its own.
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
