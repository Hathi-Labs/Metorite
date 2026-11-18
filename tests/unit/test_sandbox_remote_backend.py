"""SBX-F21 — the runtime (SBX-8) and the ``docker-remote`` backend (SBX-15).

Spec ``project-docs/specs/agent_sandbox.md`` §4.3, §4.6, §11 (SBX-F21), §13,
and D227 gap B. The broker stays the one seam that runs ``docker``
(``maf_coding_engine.md`` §7.1, WS43-F1).

What breaks this fence:

- ``sandbox_runtime`` takes a value that is not ``runc`` or ``runsc``, or
  ``runsc`` does not reach ``docker run`` as ``--runtime=runsc``;
- the ``docker-remote`` backend binds a mount of this box, runs ``runc``
  outside dev, runs a target that needs a mount (``projects``,
  ``app_builder``), or falls back to the local daemon on a bad address;
- ``DOCKER_HOST`` of the remote daemon reaches a child that is not the
  broker's ``docker``, or a log line or an error holds more of the address
  than its scheme and host;
- the copy in sends a ``.git`` or a link. The copy out writes a link, a name
  with ``..`` or a ``.git``, writes past its caps, writes anything from an
  archive that did not end clean, deletes a host file that the copy in did
  not send, or runs after an aborted command;
- the sweep, the health probe and the end of a run do not reach the remote
  daemon, or a remote container outlives its run.

The fair share of D227 is pinned in ``test_sandbox_broker_tenant.py`` and the
runtime flag in ``test_sandbox_broker_argv.py``. The Docker half at the end
runs the remote code path against a real daemon, through an explicit
``DOCKER_HOST``, and ``sandbox-docker.yml`` fails on its skip.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import tarfile
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import (
    FakeDocker,
    bound_run,
    configure_env,
    flag_values,
    mounts_of,
    until,
)
from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: F401

ORG = "11111111-2222-3333-4444-555555555555"
HOST = "ssh://sandbox@10.20.30.40:2222"
SHOWN = "ssh://10.20.30.40"
_REAL_PROCESS_IDS = sb._process_ids


# ── a fake daemon that holds the /workspace of each container ───────────────


def _tar(entries: Sequence[tuple[str, str, bytes | str]]) -> bytes:
    """A tar of ``(kind, name, data)``: kind is file, dir, symlink, hardlink or fifo."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for kind, name, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, str(data)
                tar.addfile(info)
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, str(data)
                tar.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                tar.addfile(info)
            else:
                raw = data if isinstance(data, bytes) else data.encode()
                info.size = len(raw)
                tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


class RemoteFakeDocker(FakeDocker):
    """``FakeDocker`` with ``transfer``: it keeps each container's files."""

    def __init__(self) -> None:
        super().__init__()
        self.fs: dict[str, dict[str, bytes]] = {}
        self.pushed_names: list[list[str]] = []
        self.push_rc = 0
        self.pull_rc = 0
        self.pull_override: bytes | None = None
        #: container id -> its org label, as the daemon holds them.
        self.box: dict[str, str] = {}
        #: org labels of containers that the broker's registry does not know.
        self.unknown_orgs: list[str] = []

    async def run(self, args: Sequence[str], *, timeout: float) -> sb.DockerResult:
        argv = list(args)
        if argv[:1] == ["ps"] and "--format" in argv:
            self.calls.append(argv)
            orgs = [*self.box.values(), *self.unknown_orgs]
            return sb.DockerResult(0, "".join(f"{org}\n" for org in orgs), "")
        result = await super().run(args, timeout=timeout)
        if argv[0] == "run" and result.rc == 0:
            labels = dict(v.split("=", 1) for v in flag_values(argv, "--label"))
            self.box[result.stdout.strip()] = labels["metorite.org"]
        if argv[:2] == ["rm", "-f"]:
            for cid in argv[2:]:
                self.box.pop(cid, None)
        return result

    async def transfer(
        self, args: Sequence[str], *, timeout: float,
        source: bytes | BinaryIO | None = None, sink: BinaryIO | None = None,
        limit: int = 0,
    ) -> sb.DockerResult:
        argv = list(args)
        self.calls.append(argv)
        name = argv[2] if argv[:2] == ["exec", "-i"] else argv[1]
        if sb.PUSH_SCRIPT in argv:
            raw = source if isinstance(source, bytes) else source.read()  # type: ignore[union-attr]
            files: dict[str, bytes] = {}
            names: list[str] = []
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
                for member in tar:
                    names.append(member.name)
                    if member.isreg():
                        files[member.name] = tar.extractfile(member).read()  # type: ignore[union-attr]
            self.pushed_names.append(names)
            if self.push_rc:
                return sb.DockerResult(self.push_rc, "", "tar: boom")
            self.fs[name] = files
            return sb.DockerResult(0, "", "")
        if sb.PULL_SCRIPT in argv:
            data = self.pull_override
            if data is None:
                data = _tar(
                    [("dir", ".", b"")]
                    + [("file", f"./{rel}", body) for rel, body in sorted(self.fs[name].items())]
                )
            if limit and len(data) > limit:
                return sb.DockerResult(-1, "", "over its limit")
            assert sink is not None
            sink.write(data)
            return sb.DockerResult(self.pull_rc, "", "")
        raise AssertionError(f"an unknown transfer: {argv}")

    def transfers(self) -> list[str]:
        out = []
        for call in self.calls:
            if sb.PUSH_SCRIPT in call:
                out.append("push")
            elif sb.PULL_SCRIPT in call:
                out.append("pull")
        return out


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    values = configure_env(monkeypatch, tmp_path)
    s = values["settings"]
    monkeypatch.setattr(s, "acb_env", "prod")
    monkeypatch.setattr(s, "sandbox_backend", "docker-remote")
    monkeypatch.setattr(s, "sandbox_docker_host", HOST)
    monkeypatch.setattr(s, "sandbox_runtime", "runsc")
    return values


@pytest.fixture
def docker() -> RemoteFakeDocker:
    return RemoteFakeDocker()


@pytest.fixture
def broker(docker: RemoteFakeDocker, env: dict[str, Any]) -> sb.SandboxBroker:
    return sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]


# ── the settings ────────────────────────────────────────────────────────────


def test_the_new_settings_default_to_the_behaviour_of_before() -> None:
    from acb_common.settings import Settings

    f = Settings.model_fields
    assert f["sandbox_runtime"].default == "runc"
    assert f["sandbox_backend"].default == "docker"
    assert f["sandbox_docker_host"].default == ""
    assert f["sandbox_remote_workspace_mb"].default == 512
    for name in ("sandbox_runtime", "sandbox_backend", "sandbox_docker_host"):
        assert f[name].annotation is str, f"{name} must be a plain str, so a bad value never stops the boot"


# ── SBX-8: the runtime ──────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["kata", "runc --privileged", "RUNSC;x", "io.containerd.runc.v2"])
async def test_an_unknown_runtime_refuses_before_any_docker_call(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_runtime", value)
    with bound_run(ORG), pytest.raises(sb.SandboxRefused, match="runtime"):
        await broker.acquire()
    assert docker.calls == []


def test_build_run_argv_refuses_an_unknown_runtime(tmp_path: Path) -> None:
    binding = sb.RunBinding(org=ORG, agent="a", thread="t", instance="o:x", workspace=tmp_path)
    with pytest.raises(sb.SandboxRefused):
        sb.build_run_argv(
            binding=binding, mounts=[], uid=1000, gid=1000, image="sha256:" + "1" * 64,
            limits=sb.Limits("1", "1g", 256, 256), start_id="a" * 32, runtime="kata",
        )


# ── SBX-15: the remote start ────────────────────────────────────────────────


async def test_a_remote_start_mounts_nothing_and_runs_runsc(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    with bound_run(ORG) as ws:
        (ws / ".git").mkdir()  # the local backend would cover it with a bind mount
        handle = await broker.acquire()
    argv = docker.runs()[-1]
    assert mounts_of(argv) == [], "a remote daemon cannot see a path of this box"
    assert "--mount" not in argv and "-v" not in argv and "--volume" not in argv
    assert argv.count("--runtime=runsc") == 1
    tmpfs = flag_values(argv, "--tmpfs")
    assert tmpfs[0] == "/tmp:rw,nosuid,nodev,size=256m"
    assert tmpfs[1] == "/workspace:rw,nosuid,nodev,exec,size=512m,uid=1000,gid=1000,mode=0700"
    assert str(ws.resolve()) not in " ".join(argv)
    assert handle.remote
    for pair in (("--network", "none"), ("--cap-drop", "ALL"), ("--user", "1000:1000")):
        assert argv[argv.index(pair[0]) + 1] == pair[1]


async def test_production_refuses_runc_on_the_remote_backend(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_runtime", "runc")
    for acb_env in ("prod", "staging", ""):
        monkeypatch.setattr(env["settings"], "acb_env", acb_env)
        with bound_run(ORG), pytest.raises(sb.SandboxRefused, match="runsc"):
            await broker.acquire()
    assert docker.calls == []
    monkeypatch.setattr(env["settings"], "acb_env", "dev")
    with bound_run(ORG):
        await broker.acquire()
    assert "--runtime=runsc" not in docker.runs()[-1], "dev may test the backend under runc"


@pytest.mark.parametrize("agent,scope", [
    ("projects-assistant", "projects:*"),
    ("app-builder", "app_builder:*"),
])
async def test_a_target_that_needs_a_mount_refuses_on_the_remote_backend(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, agent: str, scope: str,
) -> None:
    monkeypatch.setattr(env["settings"], "maf_coding_scope", f"code_task:*,{scope}")
    if agent == "app-builder":
        app = env["apps"] / "my-app"
        app.mkdir()
        ctx = bound_run(ORG, agent=agent, workspace=str(app), instance="")
    else:
        ctx = bound_run(ORG, agent=agent)
    with ctx, pytest.raises(sb.SandboxRefused, match="code_task"):
        await broker.acquire()
    assert docker.calls == []


def test_covers_is_false_for_projects_on_the_remote_backend(
    broker: sb.SandboxBroker, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Else projects-assistant would lose its host tools for a run_command
    that always refuses."""
    monkeypatch.setattr(env["settings"], "maf_coding_scope", "projects:*")
    monkeypatch.setattr(broker, "healthy", lambda: True)
    assert broker.covers("projects-assistant", ORG) is False
    monkeypatch.setattr(env["settings"], "sandbox_backend", "docker")
    assert broker.covers("projects-assistant", ORG) is True


# ── SBX-15: the daemon address ──────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    "",
    "unix:///var/run/docker.sock",
    "npipe:////./pipe/docker_engine",
    "ssh://",
    "http://10.0.0.1",
    "ssh://a b@10.0.0.1",
    "10.0.0.1",
])
async def test_a_bad_address_never_falls_back_to_the_local_daemon(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_docker_host", value)
    with pytest.raises(sb.SandboxUnavailable):
        sb._daemon_names()
    with bound_run(ORG), pytest.raises(sb.SandboxUnavailable):
        await broker.acquire()
    assert docker.calls == []
    assert broker.healthy() is False


def test_a_local_socket_is_for_dev_only(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_docker_host", "unix:///var/run/docker.sock")
    monkeypatch.setattr(env["settings"], "acb_env", "dev")
    assert sb._daemon_names() == {"DOCKER_HOST": "unix:///var/run/docker.sock", "DOCKER_CONTEXT": None}


async def test_an_unknown_backend_fails_closed(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``e2b`` is SBX-10 and not built. A typo never reaches the local daemon."""
    for value in ("e2b", "docker remote", "remote"):
        monkeypatch.setattr(env["settings"], "sandbox_backend", value)
        with bound_run(ORG), pytest.raises(sb.SandboxUnavailable, match="backend"):
            await broker.acquire()
        assert broker.healthy() is False
    assert docker.calls == []


class _Proc:
    def __init__(self, stderr: bytes = b"") -> None:
        self.returncode = 0
        self._stderr = stderr
        self.stdin = None
        self.stdout = None
        self.stderr = None

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    async def wait(self) -> int:
        return 0


def _record_spawns(monkeypatch: pytest.MonkeyPatch, stderr: bytes = b"") -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def fake_exec(*args: Any, **kw: Any) -> _Proc:
        seen.append({"argv": list(args), "env": kw.get("env")})
        return _Proc(stderr)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return seen


async def test_only_the_docker_child_gets_the_remote_host(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acb_common.child_env import child_env, copilot_env, docker_env

    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    seen = _record_spawns(monkeypatch)
    cli = sb.DockerCLI()
    cli._binary = lambda: "/usr/bin/docker"  # type: ignore[method-assign]
    await cli.run(["ps"], timeout=5)
    child = seen[0]["env"]
    assert child["DOCKER_HOST"] == HOST
    assert "DOCKER_CONTEXT" not in child, "a context of this box must not win"
    for other in (child_env(), copilot_env(), docker_env()):
        assert HOST not in other.values()
        assert other.get("DOCKER_HOST") != HOST
    monkeypatch.setattr(env["settings"], "sandbox_backend", "docker")
    await cli.run(["ps"], timeout=5)
    assert "DOCKER_HOST" not in seen[1]["env"], "the local backend keeps the env of before"


def test_docker_env_takes_only_the_daemon_names() -> None:
    from acb_common.child_env import docker_env

    with pytest.raises(TypeError):
        docker_env(extra={"GH_TOKEN": "x"})
    with pytest.raises(TypeError):
        docker_env(extra=os.environ)  # type: ignore[arg-type]
    assert docker_env(extra={"DOCKER_HOST": "ssh://h"})["DOCKER_HOST"] == "ssh://h"


async def test_the_sweep_and_the_probe_reach_the_remote_daemon(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_spawns(monkeypatch)
    cli = sb.DockerCLI()
    cli._binary = lambda: "/usr/bin/docker"  # type: ignore[method-assign]
    broker = sb.SandboxBroker(docker=cli)
    await broker.sweep()
    await broker.probe_docker()
    assert seen[0]["argv"][1:] == ["ps", "-aq", "--filter", "label=metorite.sandbox=1"]
    assert seen[1]["argv"][1:3] == ["version", "--format"]
    assert all(call["env"]["DOCKER_HOST"] == HOST for call in seen)


async def test_a_bad_address_never_breaks_the_startup_sweep(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_spawns(monkeypatch)
    monkeypatch.setattr(env["settings"], "sandbox_docker_host", "")
    cli = sb.DockerCLI()
    cli._binary = lambda: "/usr/bin/docker"  # type: ignore[method-assign]
    broker = sb.SandboxBroker(docker=cli)
    await broker.startup()
    assert seen == [], "no docker process may start with no remote address"


# ── SBX-15: the address never leaves in full ────────────────────────────────


def test_the_address_shows_its_scheme_and_host_only() -> None:
    assert sb.redact_docker_host(HOST) == SHOWN
    assert sb.redact_docker_host("tcp://[fd00::5]:2376") == "tcp://fd00::5"
    assert sb.redact_docker_host("unix:///var/run/docker.sock") == "unix://local"


async def test_docker_stderr_loses_the_user_of_the_address(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record_spawns(monkeypatch, stderr=f"error during connect: {HOST}: sandbox@10.20.30.40: "
                   "Permission denied (publickey).".encode())
    cli = sb.DockerCLI()
    cli._binary = lambda: "/usr/bin/docker"  # type: ignore[method-assign]
    result = await cli.run(["ps"], timeout=5)
    assert "sandbox@" not in result.stderr
    assert HOST not in result.stderr and ":2222" not in result.stderr
    assert SHOWN in result.stderr


def test_the_startup_line_names_the_backend_and_the_short_address(
    broker: sb.SandboxBroker,
) -> None:
    assert broker._backend_fields() == {"backend": "docker-remote", "docker_host": SHOWN}


# ── SBX-15: the copy in and the copy out ────────────────────────────────────


async def test_a_command_sees_the_files_and_its_changes_come_back(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    with bound_run(ORG) as ws:
        (ws / "agent-data" / "scripts").mkdir(parents=True)
        (ws / "agent-data" / "scripts" / "r.py").write_bytes(b"print(1)\n")
        (ws / "agent-data" / "old.txt").write_bytes(b"old\n")
        (ws / "inputs").mkdir()
        (ws / "inputs" / "data.csv").write_bytes(b"a,b\n")
        (ws / ".git").mkdir()
        (ws / ".git" / "config").write_bytes(b"url = https://x-token:secret@h\n")
        handle = await broker.acquire()

        async def command(argv: list[str]) -> None:
            files = docker.fs[handle.name]
            assert files["agent-data/scripts/r.py"] == b"print(1)\n"
            files["agent-data/scripts/r.py"] = b"print(2)\n"
            files["outputs/chart.txt"] = b"chart\n"
            del files["agent-data/old.txt"]

        docker.on_stream = command
        result = await broker.exec(handle, "python3 agent-data/scripts/r.py", 10)
        assert result.message == ""
        assert docker.transfers() == ["push", "pull"]
        assert not any(".git" in n for n in docker.pushed_names[-1]), "the root .git has no cover here"
        assert (ws / "agent-data" / "scripts" / "r.py").read_bytes() == b"print(2)\n"
        assert (ws / "outputs" / "chart.txt").read_bytes() == b"chart\n"
        assert not (ws / "agent-data" / "old.txt").exists()
        assert (ws / "inputs" / "data.csv").read_bytes() == b"a,b\n"
        assert b"secret" in (ws / ".git" / "config").read_bytes(), "the copy out never touches .git"


async def test_the_copy_in_comes_before_the_command_and_the_copy_out_after(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    with bound_run(ORG):
        handle = await broker.acquire()
        await broker.exec(handle, "true", 5)
    order = [
        "push" if sb.PUSH_SCRIPT in c else "pull" if sb.PULL_SCRIPT in c
        else "command" if sb.EXEC_WRAPPER in c else "sweep" if sb.KILL_SWEEP in c else c[0]
        for c in docker.calls
    ]
    assert order[-4:] == ["push", "command", "sweep", "pull"]


async def test_an_unchanged_file_keeps_its_mtime(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """``sweep_changed_files`` reads mtimes, so a copy out rewrites only a change."""
    with bound_run(ORG) as ws:
        (ws / "outputs").mkdir()
        keep = ws / "outputs" / "keep.txt"
        keep.write_bytes(b"same\n")
        os.utime(keep, (1_000_000, 1_000_000))
        handle = await broker.acquire()
        await broker.exec(handle, "true", 5)
    assert keep.stat().st_mtime == 1_000_000


async def test_the_copy_out_takes_regular_files_with_clean_names_only(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
) -> None:
    with bound_run(ORG) as ws:
        handle = await broker.acquire()
        docker.pull_override = _tar([
            ("dir", ".", b""),
            ("file", "./outputs/good.txt", "good"),
            ("symlink", "./outputs/link", "/etc/passwd"),
            ("hardlink", "./outputs/hard", "./outputs/good.txt"),
            ("fifo", "./outputs/pipe", b""),
            ("file", "../escape.txt", "x"),
            ("file", "./a/../../escape2.txt", "x"),
            ("file", "/abs.txt", "x"),
            ("file", "./.git/config", "planted"),
            ("file", "./sub/.git/hooks/pre-commit", "planted"),
            ("file", "./back\\slash.txt", "x"),
        ])
        result = await broker.exec(handle, "true", 5)
    assert result.message == ""
    assert (ws / "outputs" / "good.txt").read_bytes() == b"good"
    for name in ("link", "hard", "pipe"):
        assert not (ws / "outputs" / name).exists(), name
    assert not (ws.parent / "escape.txt").exists()
    assert not (ws.parent / "escape2.txt").exists()
    assert not (ws / "abs.txt").exists()
    assert not (ws / ".git").exists() and not (ws / "sub").exists()
    assert not (ws / "back\\slash.txt").exists()


@pytest.mark.parametrize("fault", ["rc", "short", "over"])
async def test_a_copy_out_that_did_not_end_clean_writes_and_deletes_nothing(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    with bound_run(ORG) as ws:
        (ws / "agent-data").mkdir()
        (ws / "agent-data" / "keep.txt").write_bytes(b"keep\n")
        handle = await broker.acquire()
        whole = _tar([("file", "./outputs/new.txt", "new")])
        if fault == "rc":
            docker.pull_rc = 2
            docker.pull_override = whole
        elif fault == "short":
            docker.pull_override = whole[:700]
        else:
            monkeypatch.setattr(env["settings"], "sandbox_remote_workspace_mb", 1)
            docker.pull_override = _tar([("file", "./outputs/big.bin", b"x" * (2 * 1024 * 1024))])
        result = await broker.exec(handle, "true", 5)
    assert result.message == sb.REMOTE_COPY_FAILED
    assert (ws / "agent-data" / "keep.txt").read_bytes() == b"keep\n", "a pushed file stayed"
    assert not (ws / "outputs").exists()


async def test_the_copy_out_deletes_only_what_the_copy_in_sent(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    for name in ("sent.txt", "kept.txt", "new-on-host.txt"):
        (ws / "a" / name).write_bytes(name.encode())
    archive = io.BytesIO(_tar([("file", "./a/kept.txt", "kept.txt")]))
    done = sb.apply_pull_archive(
        ws, archive, pushed=frozenset({"a/sent.txt", "a/kept.txt"}),
        max_bytes=1 << 20, max_entries=100,
    )
    assert done.deleted == 1
    assert not (ws / "a" / "sent.txt").exists()
    assert (ws / "a" / "new-on-host.txt").exists(), "a file the copy in never sent stays"


async def test_an_aborted_command_keeps_no_change_of_the_container(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """A Stop cuts the command short. The copy out does not run, so a
    half-written output never lands on the host. The next command copies
    the host dir in again."""
    gate = asyncio.Event()

    async def hang(argv: list[str]) -> None:
        await gate.wait()

    with bound_run(ORG) as ws:
        handle = await broker.acquire()
        docker.on_stream = hang
        task = asyncio.create_task(broker.exec(handle, "sleep 100", 60))
        await until(lambda: docker.command_execs(), "the command must be in flight")
        docker.fs[handle.name]["outputs/half.txt"] = b"half"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await broker.settle()
    assert docker.transfers() == ["push"]
    assert not (ws / "outputs" / "half.txt").exists()


async def test_a_working_dir_over_the_remote_size_refuses_before_a_start(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_remote_workspace_mb", 1)
    with bound_run(ORG) as ws:
        (ws / "big.bin").write_bytes(b"x" * (1024 * 1024 + 1))
        with pytest.raises(sb.SandboxRefused, match="MB"):
            await broker.acquire()
    assert docker.runs() == []


async def test_a_failed_copy_in_runs_no_command(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    with bound_run(ORG):
        handle = await broker.acquire()
        docker.push_rc = 2
        with pytest.raises(sb.SandboxUnavailable, match="copied into"):
            await broker.exec(handle, "true", 5)
    assert docker.command_execs() == []


# ── SBX-15: one org per remote box ──────────────────────────────────────────


async def test_the_admit_reads_the_daemon_labels(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_remote_org_admit`` is the one check that every start path calls."""
    await broker._remote_org_admit(ORG)
    docker.unknown_orgs = [ORG]
    await broker._remote_org_admit(ORG)
    docker.unknown_orgs = ["22222222-0000-0000-0000-000000000002"]
    with pytest.raises(sb.SandboxBusy, match="one organization") as caught:
        await broker._remote_org_admit(ORG)
    assert "22222222" not in str(caught.value), "the answer names no other org"
    docker.unknown_orgs = [""]  # a sandbox container with no org label
    with pytest.raises(sb.SandboxBusy):
        await broker._remote_org_admit(ORG)
    calls = len(docker.calls)
    monkeypatch.setattr(env["settings"], "sandbox_backend", "docker")
    await broker._remote_org_admit(ORG)
    assert len(docker.calls) == calls, "the local backend reads nothing"


async def test_a_failed_read_of_the_box_refuses_the_start(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(args: Sequence[str], *, timeout: float) -> sb.DockerResult:
        docker.calls.append(list(args))
        if args[0] == "ps" and "--format" in args:
            return sb.DockerResult(1, "", "Cannot connect to the Docker daemon")
        return await RemoteFakeDocker.run(docker, args, timeout=timeout)

    monkeypatch.setattr(docker, "run", broken)
    with bound_run(ORG), pytest.raises(sb.SandboxUnavailable, match="could not read"):
        await broker.acquire()
    assert docker.runs() == []


def test_every_start_in_the_broker_passes_the_admit() -> None:
    """A new start path must call ``_remote_org_admit`` before ``docker run``."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sb))
    starters = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            calls = {
                c.func.attr for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            }
            if "_run_container" in calls:
                starters.append(node.name)
                assert "_remote_org_admit" in calls, f"{node.name} starts a container with no admit"
    assert "_start" in starters, "the detector cannot see the one start path it guards"


# ── SBX-15: no tenant data outlives its run on the box ──────────────────────


async def test_the_end_of_a_run_removes_its_remote_container(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    with bound_run(ORG, thread="t-end"):
        handle = await broker.acquire()
        await broker.exec(handle, "true", 5)
        await broker.release(handle)
        assert await broker.end_run() is True
        await broker.settle()
    assert docker.removals() == [handle.container_id]
    assert handle.removed


async def test_the_local_backend_keeps_end_run_a_no_op(
    docker: RemoteFakeDocker, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_backend", "docker")
    broker = sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]
    with bound_run(ORG, thread="t-local"):
        handle = await broker.acquire()
        await broker.exec(handle, "true", 5)
        await broker.release(handle)
        assert await broker.end_run() is False
    assert docker.removals() == []
    assert docker.transfers() == [], "the local backend copies nothing"
    assert mounts_of(docker.runs()[-1]), "the local backend still binds the working dir"


# ── DockerCLI.transfer with a real process ──────────────────────────────────


async def test_transfer_feeds_stdin_and_caps_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stdin path that the copy in uses, with bytes and with a file."""
    cli = sb.DockerCLI()
    monkeypatch.setattr(cli, "_binary", lambda: sys.executable)
    echo = ["-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])"]
    for source in (b"abc" * 1000, io.BytesIO(b"abc" * 1000)):
        sink = io.BytesIO()
        result = await cli.transfer(echo, timeout=60, source=source, sink=sink)
        assert result.rc == 0
        assert sink.getvalue() == (b"abc" * 1000)[::-1]
    sink = io.BytesIO()
    result = await cli.transfer(echo, timeout=60, source=b"x" * 5000, sink=sink, limit=100)
    assert result.rc == -1 and "limit" in result.stderr
    assert len(sink.getvalue()) <= 100


# ═════════════════════════ the real-Docker half ═════════════════════════════

DOCKER_ORG = f"eeeeeeee-0000-0000-0000-{uuid.uuid4().hex[:12]}"


def _docker(*args: str, host: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DOCKER_HOST": host}
    env.pop("DOCKER_CONTEXT", None)
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", env=env,
    )


def _test_host() -> str:
    """The daemon of the Docker half. ``SANDBOX_REMOTE_TEST_DOCKER_HOST`` names a
    second daemon. With none, the same daemon by its explicit address."""
    given = os.environ.get("SANDBOX_REMOTE_TEST_DOCKER_HOST", "").strip()
    if given:
        return given
    return "npipe:////./pipe/docker_engine" if os.name == "nt" else "unix:///var/run/docker.sock"


@pytest.fixture
async def remote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, coding_sandbox_image: str,  # noqa: F811
) -> Any:
    host = _test_host()
    values = configure_env(monkeypatch, tmp_path)
    s = values["settings"]
    monkeypatch.setattr(s, "sandbox_image", coding_sandbox_image)
    monkeypatch.setattr(s, "maf_coding_scope", "code_task:*")
    monkeypatch.setattr(s, "acb_env", "dev")
    monkeypatch.setattr(s, "sandbox_backend", "docker-remote")
    monkeypatch.setattr(s, "sandbox_docker_host", host)
    monkeypatch.setattr(
        s, "sandbox_runtime", os.environ.get("SANDBOX_REMOTE_TEST_RUNTIME", "runc"),
    )
    monkeypatch.setattr(sb, "_process_ids", _REAL_PROCESS_IDS)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    broker = sb.SandboxBroker()
    values.update(broker=broker, host=host)
    try:
        yield values
    finally:
        await broker.settle()
        await broker.stop()
        left = _docker(
            "ps", "-aq", "--filter", f"label=metorite.org={DOCKER_ORG}", host=host,
        ).stdout.split()
        if left:
            _docker("rm", "-f", *left, host=host)


@pytest.mark.sandbox_docker
async def test_docker_the_remote_path_moves_files_through_the_daemon(remote: dict[str, Any]) -> None:
    broker, host = remote["broker"], remote["host"]
    assert await broker.probe_docker(), f"the daemon at {sb.redact_docker_host(host)} did not answer"
    with bound_run(DOCKER_ORG, thread="t-remote") as ws:
        (ws / "agent-data").mkdir()
        (ws / "agent-data" / "in.txt").write_text("hello from the host\n")
        (ws / "agent-data" / "gone.txt").write_text("delete me\n")
        (ws / ".git").mkdir()
        (ws / ".git" / "config").write_text("url = https://x-token:secret@h\n")
        handle = await broker.acquire()
        inspect = _docker("inspect", "--format", "{{json .Mounts}}|{{json .HostConfig.Tmpfs}}",
                          handle.container_id, host=host)
        assert inspect.returncode == 0, inspect.stderr
        mounts, tmpfs = inspect.stdout.strip().split("|", 1)
        assert json.loads(mounts) in ([], None), f"a remote container mounts nothing: {mounts}"
        assert "/workspace" in json.loads(tmpfs)

        result = await broker.exec(
            handle,
            "cat agent-data/in.txt; test -e .git || echo no-git; "
            "mkdir -p outputs && printf out > outputs/o.txt; rm agent-data/gone.txt; "
            "ln -s /etc/passwd outputs/link; mkdir -p .local/b && printf '#!/bin/sh\\necho ran\\n' "
            "> .local/b/t && chmod +x .local/b/t && .local/b/t",
            60,
        )
        assert result.exit_code == 0, result.output
        assert "hello from the host" in result.output
        assert "no-git" in result.output
        assert "ran" in result.output, "the workspace tmpfs must allow exec"
        assert result.message == ""
        assert (ws / "outputs" / "o.txt").read_text() == "out"
        assert not (ws / "agent-data" / "gone.txt").exists()
        assert not (ws / "outputs" / "link").exists()
        assert "secret" in (ws / ".git" / "config").read_text()

        second = await broker.exec(handle, "cat outputs/o.txt", 30)
        assert second.output.strip() == "out", "a second command sees the first one's files"
        await broker.release(handle)
        assert await broker.end_run() is True
        await broker.settle()
    gone = _docker("ps", "-aq", "--filter", f"label=metorite.start={handle.start_id}", host=host)
    assert gone.stdout.strip() == "", "the remote container outlived its run"
