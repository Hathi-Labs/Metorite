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
from pathlib import Path, PurePosixPath
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
        #: container id -> its start label.
        self.starts: dict[str, str] = {}
        #: org labels of containers that the broker's registry does not know.
        self.unknown_orgs: list[str] = []
        #: The next this-many ``rm -f`` calls fail, as a dropped ssh link does.
        self.fail_rm = 0

    async def run(self, args: Sequence[str], *, timeout: float) -> sb.DockerResult:
        argv = list(args)
        if argv[:1] == ["ps"] and "--format" in argv:
            self.calls.append(argv)
            if sb.LABEL_START in argv[-1]:
                lines = [f"{cid[:12]}|{self.starts.get(cid, '')}" for cid in self.box]
                return sb.DockerResult(0, "".join(f"{line}\n" for line in lines), "")
            orgs = [*self.box.values(), *self.unknown_orgs]
            return sb.DockerResult(0, "".join(f"{org}\n" for org in orgs), "")
        if argv[:2] == ["rm", "-f"] and self.fail_rm:
            self.calls.append(argv)
            self.fail_rm -= 1
            return sb.DockerResult(1, "", "error during connect: ssh: connection reset")
        result = await super().run(args, timeout=timeout)
        if argv[0] == "run" and result.rc == 0:
            labels = dict(v.split("=", 1) for v in flag_values(argv, "--label"))
            cid = result.stdout.strip()
            self.box[cid] = labels["metorite.org"]
            self.starts[cid] = labels["metorite.start"]
        if argv[:2] == ["rm", "-f"]:
            for cid in argv[2:]:
                for full in [c for c in self.box if c.startswith(cid)]:
                    self.box.pop(full, None)
                    self.starts.pop(full, None)
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
    assert sb._daemon_names() == {
        "DOCKER_HOST": "unix:///var/run/docker.sock", "DOCKER_CONTEXT": None,
        "DOCKER_TLS_VERIFY": None, "DOCKER_CERT_PATH": None,
    }


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

    async def communicate(self, stdin: bytes | None = None) -> tuple[bytes, bytes]:
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


def test_a_type_swap_never_deletes_what_the_copy_in_did_not_send(tmp_path: Path) -> None:
    """The container turns a file into a dir, or a dir into a file. The host
    gives up only an entry that the copy in sent."""
    ws = tmp_path / "ws"
    (ws / "dir-on-host").mkdir(parents=True)
    (ws / "dir-on-host" / "kept.txt").write_bytes(b"kept")
    (ws / "file-on-host").write_bytes(b"not sent")
    (ws / "sent-file").write_bytes(b"sent")
    archive = io.BytesIO(_tar([
        ("file", "./dir-on-host", "now a file"),
        ("dir", "./file-on-host", b""),
        ("file", "./file-on-host/x", "x"),
        ("dir", "./sent-file", b""),
        ("file", "./sent-file/y", "y"),
    ]))
    done = sb.apply_pull_archive(
        ws, archive, pushed=frozenset({"sent-file"}), max_bytes=1 << 20, max_entries=100,
    )
    assert (ws / "dir-on-host" / "kept.txt").read_bytes() == b"kept"
    assert (ws / "file-on-host").read_bytes() == b"not sent"
    assert (ws / "sent-file" / "y").read_bytes() == b"y", "a sent file may become a dir"
    assert done.skipped >= 3


async def test_two_acquires_at_once_on_one_remote_thread_share_one_start(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """The handle is remote from its claim, so a second acquire joins it."""
    gate = asyncio.Event()
    docker.block_run = gate
    with bound_run(ORG, thread="t-twice"):
        first = asyncio.create_task(broker.acquire())
        await until(lambda: docker.runs(), "docker run must be in flight")
        second = asyncio.create_task(broker.acquire())
        await asyncio.sleep(0.05)
        gate.set()
        a, b = await asyncio.gather(first, second)
    assert a is b
    assert len(docker.runs()) == 1


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


# ── review P1: a name that cannot come back is never sent and never deleted ─


def test_a_name_that_cannot_come_back_is_not_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name with a byte that is not UTF-8 (a surrogate in Python) is not
    sent, and the model hears which names stayed behind. Any OS."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ok.txt").write_bytes(b"ok")
    bad = "lat\udce9.txt"

    def walk(top: Any, followlinks: bool = False) -> Any:
        yield str(ws), [], ["ok.txt", bad]

    monkeypatch.setattr(sb.os, "walk", walk)
    tree = sb._host_tree(ws, max_entries=100)
    assert tree.files == ["ok.txt"]
    assert tree.unsent == [bad]
    note = sb.unsent_note(tree.unsent)
    assert "lat\\xe9.txt" in note and note.startswith("1 name(s)")


def test_the_copy_out_never_deletes_a_name_it_cannot_read_back(tmp_path: Path) -> None:
    """The second guard. ``a\\b`` reads as ``a/b`` through the safe opener,
    so a delete of ``a\\b`` would remove the real ``a/b``."""
    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    (ws / "a" / "b").write_bytes(b"real")
    archive = io.BytesIO(_tar([("dir", ".", b"")]))
    done = sb.apply_pull_archive(
        ws, archive, pushed=frozenset({"a\\b", "lat\udce9.txt"}),
        max_bytes=1 << 20, max_entries=100,
    )
    assert (ws / "a" / "b").read_bytes() == b"real"
    assert done.deleted == 0 and done.skipped == 2


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot name a file with a backslash or a non-UTF-8 byte")
async def test_a_round_trip_keeps_each_file_that_the_container_never_touched(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """The reviewer's case on a real file system: ``lat\\xe9.txt``, and
    ``a\\b`` beside a real ``a/b``. Nothing of them is sent or deleted."""
    with bound_run(ORG, thread="t-names") as ws:
        (ws / "a").mkdir()
        (ws / "a" / "b").write_bytes(b"real")
        (ws / "a\\b").write_bytes(b"backslash")
        latin = ws / os.fsdecode(b"lat\xe9.txt")
        latin.write_bytes(b"latin")
        handle = await broker.acquire()
        result = await broker.exec(handle, "true", 5)
    assert (ws / "a" / "b").read_bytes() == b"real"
    assert (ws / "a\\b").read_bytes() == b"backslash"
    assert latin.read_bytes() == b"latin"
    assert not any("\\" in n or "lat" in n for n in docker.pushed_names[-1])
    assert "2 name(s)" in result.message and "a\\b" in result.message


# ── review P2: the copy out streams each file ───────────────────────────────


async def test_the_copy_out_streams_each_file_and_never_holds_it_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file larger than one chunk comes back whole, through
    ``replace_stream``, and never through the in-memory ``replace_bytes``."""
    from acb_skills import safe_open

    def whole(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the copy out held a file in memory whole")

    monkeypatch.setattr(safe_open, "replace_bytes", whole)
    reads: list[int] = []
    original = tarfile.ExFileObject.read

    def counted(self: Any, size: int = -1) -> bytes:
        reads.append(size)
        return original(self, size)

    monkeypatch.setattr(tarfile.ExFileObject, "read", counted)
    ws = tmp_path / "ws"
    ws.mkdir()
    body = os.urandom(3 * sb._READ_CHUNK + 17)
    archive = io.BytesIO(_tar([("file", "./outputs/big.bin", body)]))
    done = sb.apply_pull_archive(
        ws, archive, pushed=frozenset(), max_bytes=1 << 22, max_entries=10,
    )
    assert (ws / "outputs" / "big.bin").read_bytes() == body
    assert done.written == 1
    assert reads and all(0 < size <= sb._READ_CHUNK for size in reads), reads


# ── review P2: an address outside dev ───────────────────────────────────────


@pytest.mark.parametrize("value", [
    "ssh://sandbox@127.0.0.1",
    "ssh://sandbox@localhost",
    "ssh://sandbox@[::1]",
    "ssh://sandbox@0.0.0.0",
    "ssh://sandbox@[::ffff:127.0.0.1]",
    "tcp://127.9.9.9:2376",
])
def test_a_loopback_daemon_refuses_outside_dev(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_docker_cert_path", str(tmp_path))
    monkeypatch.setattr(env["settings"], "sandbox_docker_host", value)
    with pytest.raises(sb.SandboxUnavailable, match="this box"):
        sb._daemon_names()
    monkeypatch.setattr(env["settings"], "acb_env", "dev")
    assert sb._daemon_names()["DOCKER_HOST"] == value, "dev may test against itself"


def test_tcp_outside_dev_needs_tls_with_a_client_certificate(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    s = env["settings"]
    monkeypatch.setattr(s, "sandbox_docker_host", "tcp://10.20.30.40:2376")
    for missing in ("", str(tmp_path / "no-such-dir")):
        monkeypatch.setattr(s, "sandbox_docker_cert_path", missing)
        with pytest.raises(sb.SandboxUnavailable, match="certificate"):
            sb._daemon_names()
    monkeypatch.setattr(s, "sandbox_docker_cert_path", str(tmp_path))
    names = sb._daemon_names()
    assert names["DOCKER_TLS_VERIFY"] == "1"
    assert names["DOCKER_CERT_PATH"] == str(tmp_path)
    monkeypatch.setattr(s, "sandbox_docker_host", HOST)
    names = sb._daemon_names()
    assert names["DOCKER_TLS_VERIFY"] is None and names["DOCKER_CERT_PATH"] is None


async def test_the_docker_child_of_a_tcp_daemon_gets_the_tls_names(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    s = env["settings"]
    monkeypatch.setattr(s, "sandbox_docker_host", "tcp://10.20.30.40:2376")
    monkeypatch.setattr(s, "sandbox_docker_cert_path", str(tmp_path))
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "")
    seen = _record_spawns(monkeypatch)
    cli = sb.DockerCLI()
    cli._binary = lambda: "/usr/bin/docker"  # type: ignore[method-assign]
    await cli.run(["ps"], timeout=5)
    child = seen[0]["env"]
    assert child["DOCKER_TLS_VERIFY"] == "1" and child["DOCKER_CERT_PATH"] == str(tmp_path)
    monkeypatch.setattr(s, "sandbox_docker_host", HOST)
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", "/etc/docker/certs")
    await cli.run(["ps"], timeout=5)
    child = seen[1]["env"]
    assert "DOCKER_TLS_VERIFY" not in child and "DOCKER_CERT_PATH" not in child


async def test_the_ssh_command_line_loses_its_user_and_port(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real shape of a docker CLI ssh error (review P3)."""
    _record_spawns(monkeypatch, stderr=(
        b"error during connect: Get \"http://docker.example.com/v1.47/containers/json\": "
        b"command [ssh -o ConnectTimeout=30 -T -l sandbox -p 2222 -- 10.20.30.40 docker "
        b"system dial-stdio] has exited with exit status 255"
    ))
    cli = sb.DockerCLI()
    cli._binary = lambda: "/usr/bin/docker"  # type: ignore[method-assign]
    result = await cli.run(["ps"], timeout=5)
    assert "sandbox" not in result.stderr
    assert "2222" not in result.stderr
    assert "-l (user)" in result.stderr and "-p (port)" in result.stderr


def test_a_bare_port_number_elsewhere_stays(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2, P3: with port 22, a time such as 12:22:01 keeps its
    digits. Only the port of the host token and of ``-p`` goes."""
    monkeypatch.setattr(env["settings"], "sandbox_docker_host", "ssh://sandbox@10.20.30.40:22")
    text = "12:22:01 ssh -l sandbox -p 22 -- 10.20.30.40:22 failed at line 7:22"
    out = sb._scrub(text)
    assert out == "12:22:01 ssh -l (user) -p (port) -- 10.20.30.40 failed at line 7:22"


# ── review P2: a failed remote removal never locks the box ──────────────────


async def test_a_failed_removal_is_reaped_and_frees_the_box(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """The ssh link drops during the ``rm -f`` of org A's container. The
    reaper removes the orphan, and org B then starts."""
    org_b = "22222222-0000-0000-0000-000000000002"
    with bound_run(ORG, thread="t-orphan"):
        handle = await broker.acquire()
        await broker.release(handle)
        docker.fail_rm = 1
        assert await broker.end_run() is True
        await broker.settle()
    assert set(docker.box.values()) == {ORG}, "the failed removal left the container"
    assert broker._remote_orphans
    with bound_run(org_b, thread="t-b"), pytest.raises(sb.SandboxBusy):
        await broker.acquire()
    await broker.reap_once()
    assert docker.box == {} and not broker._remote_orphans
    with bound_run(org_b, thread="t-b"):
        b = await broker.acquire()
    assert b.org == org_b
    await broker.stop()


async def test_a_reaper_tick_removes_only_the_recorded_orphans(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """Review round 2, P2. A daemon that another deployment shares holds a
    labelled container that no registry here knows. A tick never removes it:
    only the ids that a failed removal recorded go. The startup sweep stays
    the one full scan."""
    with bound_run(ORG, thread="t-live"):
        handle = await broker.acquire()
    docker.box["cid-other-deployment"] = ORG
    docker.starts["cid-other-deployment"] = "f" * 32
    calls = len(docker.calls)
    await broker.reap_once()
    assert "cid-other-deployment" in docker.box
    assert handle.container_id in docker.box
    assert docker.calls[calls:] == [], "a tick with no orphan sends no docker call"
    broker._remote_orphans.add("cid-gone")
    await broker.reap_once()
    assert ["rm", "-f", "cid-gone"] in docker.calls[calls:]
    assert "cid-other-deployment" in docker.box and not broker._remote_orphans
    await broker.stop()


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


def _call_name(func: Any) -> str:
    import ast

    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_run(call: Any) -> bool:
    """A call that runs a container: ``_run_container``, or a docker call
    whose argv is ``["run", ...]`` or ``build_run_argv(...)``."""
    import ast

    name = _call_name(call.func)
    if name == "_run_container":
        return True
    if name in {"run", "stream", "transfer", "collect"} and call.args:
        first = call.args[0]
        if isinstance(first, ast.List | ast.Tuple) and first.elts:
            head = first.elts[0]
            return isinstance(head, ast.Constant) and head.value == "run"
        if isinstance(first, ast.Call):
            return _call_name(first.func) == "build_run_argv"
    return False


def starts_without_admit(source: str) -> tuple[list[str], list[str]]:
    """``(starters, offenders)`` of *source*.

    A starter is a function that runs a container or builds its argv. It is
    an offender unless EACH of its runs lies in the body of one
    ``async with self._start_gate(...)`` whose same body awaits
    ``_remote_org_admit`` (review round 2, P3). SBX-15c: that await is a
    statement of its own in the gate body, and the run lies in a LATER
    statement of the same body. An admit after the run, or an admit inside an
    ``if``, does not count. A starter that builds an argv and runs nothing is
    an offender too.
    """
    import ast

    starters: list[str] = []
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
        runs = [c for c in calls if _is_run(c)]
        builds = [c for c in calls if _call_name(c.func) == "build_run_argv"]
        if not runs and not builds:
            continue
        starters.append(node.name)
        gated: set[int] = set()
        for item in ast.walk(node):
            if not isinstance(item, ast.AsyncWith) or not any(
                isinstance(w.context_expr, ast.Call)
                and _call_name(w.context_expr.func) == "_start_gate"
                for w in item.items
            ):
                continue
            first_admit = next(
                (
                    i for i, stmt in enumerate(item.body)
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await)
                    and isinstance(stmt.value.value, ast.Call)
                    and _call_name(stmt.value.value.func) == "_remote_org_admit"
                ),
                None,
            )
            if first_admit is None:
                continue
            after = [
                c for b in item.body[first_admit + 1:] for c in ast.walk(b)
                if isinstance(c, ast.Call)
            ]
            gated |= {id(c) for c in after if _is_run(c)}
        if not runs or any(id(r) not in gated for r in runs):
            offenders.append(node.name)
    return starters, offenders


def test_every_start_in_the_broker_passes_the_admit_under_the_gate() -> None:
    """Each start path awaits ``_remote_org_admit`` and runs the container
    in the body of ONE start gate, ``_start`` and SBX-18's ``_compute_start``
    too.

    The gate holds the admit and ``docker run`` together (review P2), so two
    starts of two orgs never both pass the admit.
    """
    import inspect

    starters, offenders = starts_without_admit(inspect.getsource(sb))
    assert {"_start", "_compute_start"} <= set(starters), (
        f"the detector cannot see the start paths it guards: {starters}"
    )
    assert offenders == [], f"these start a container with no gated admit: {offenders}"


def test_the_admit_detector_sees_each_shape_of_a_start() -> None:
    bad = [
        "async def a(self):\n    await self._docker.run(build_run_argv(binding=b))\n",
        "async def b(self):\n    await self._docker.run(['run', '-d', 'img'], timeout=1)\n",
        "async def c(self, h, argv):\n    await self._remote_org_admit(h.org)\n"
        "    await self._run_container(h, argv)\n",
        "async def d(self, h, argv):\n    async with self._start_gate(True):\n"
        "        pass\n    await self._remote_org_admit(h.org)\n"
        "    await self._run_container(h, argv)\n",
        # The admit under the gate, the run after it.
        "async def g(self, h, argv):\n    async with self._start_gate(True):\n"
        "        await self._remote_org_admit(h.org)\n"
        "    await self._run_container(h, argv)\n",
        # The admit and the run in two different gates.
        "async def h(self, h, argv):\n    async with self._start_gate(True):\n"
        "        await self._remote_org_admit(h.org)\n"
        "    async with self._start_gate(True):\n"
        "        await self._run_container(h, argv)\n",
        # The getattr form of SBX-18 before the merge: no call name to match.
        "async def i(self, h, argv):\n    async with self._start_gate(True):\n"
        "        await getattr(self, '_remote_org_admit')(h.org)\n"
        "        await self._run_container(h, argv)\n",
        # An argv that is built and never run under a gate.
        "def j(b):\n    return build_run_argv(binding=b)\n",
        # SBX-15c: the run before the admit, in one gate.
        "async def k(self, h, argv):\n    async with self._start_gate(True):\n"
        "        await self._run_container(h, argv)\n"
        "        await self._remote_org_admit(h.org)\n",
        # SBX-15c: the admit and the run in one statement.
        "async def m(self, h, argv):\n    async with self._start_gate(True):\n"
        "        await asyncio.gather(self._remote_org_admit(h.org), self._run_container(h, argv))\n",
        # SBX-15c: an admit that only an `if` runs.
        "async def n(self, h, argv, x):\n    async with self._start_gate(True):\n"
        "        if x:\n            await self._remote_org_admit(h.org)\n"
        "        await self._run_container(h, argv)\n",
        # SBX-15c: an admit that is not awaited.
        "async def p(self, h, argv):\n    async with self._start_gate(True):\n"
        "        self._remote_org_admit(h.org)\n"
        "        await self._run_container(h, argv)\n",
    ]
    for source in bad:
        assert starts_without_admit(source)[1], source
    good = (
        "async def e(self, h, argv):\n    async with self._start_gate(True):\n"
        "        await self._remote_org_admit(h.org)\n"
        "        await self._run_container(h, argv)\n"
    )
    assert starts_without_admit(good) == (["e"], [])
    ps_only = "async def f(self):\n    await self._docker.run(['ps'])\n"
    assert starts_without_admit(ps_only) == ([], [])


async def test_two_starts_of_two_orgs_never_both_pass_the_admit(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """Review P2. Two start paths that no registry joins (as a second target
    would be) still meet the gate: the second admit reads the daemon after
    the first ``docker run`` returned, and is busy."""
    org_b = "22222222-0000-0000-0000-000000000002"
    with bound_run(ORG, thread="t-a"):
        binding_a = sb.read_run_binding()
    with bound_run(org_b, thread="t-b"):
        binding_b = sb.read_run_binding()
    handle_a = sb.SandboxHandle(binding=binding_a, lock=asyncio.Lock())
    handle_b = sb.SandboxHandle(binding=binding_b, lock=asyncio.Lock())
    gate = asyncio.Event()
    docker.block_run = gate
    first = asyncio.create_task(broker._start(handle_a))
    await until(lambda: docker.runs(), "the first docker run must be in flight")
    second = asyncio.create_task(broker._start(handle_b))
    await asyncio.sleep(0.05)
    assert len(docker.runs()) == 1, "the second start must wait on the gate"
    gate.set()
    await first
    with pytest.raises(sb.SandboxBusy, match="one organization"):
        await second
    assert len(docker.runs()) == 1
    assert set(docker.box.values()) == {ORG}


async def test_the_admit_reads_the_compute_registry(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """SBX-18 keeps its own registry. The admit reads it, and a remote
    compute handle of another org holds the box."""
    from types import SimpleNamespace

    other = "22222222-0000-0000-0000-000000000002"
    broker._compute_live = {"c1": SimpleNamespace(org=other, remote=True)}  # type: ignore[assignment]
    with pytest.raises(sb.SandboxBusy):
        await broker._remote_org_admit(ORG)
    broker._compute_live = {"c1": SimpleNamespace(org=ORG, remote=True)}  # type: ignore[assignment]
    await broker._remote_org_admit(ORG)


# ── the merge with SBX-18: the org lock binds the compute target too ────────

ORG_B = "22222222-0000-0000-0000-000000000002"


@pytest.fixture
def compute_on(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    s = env["settings"]
    monkeypatch.setattr(s, "maf_coding_scope", "code_task:*,compute:*")
    monkeypatch.setattr(s, "compute_run_orgs", f"{ORG},{ORG_B}")
    monkeypatch.setattr(s, "self_serve_signup_enabled", "")


async def _compute_started(broker: sb.SandboxBroker, org: str, thread: str) -> sb.SandboxHandle:
    with bound_run(org, thread=thread):
        handle = await broker._compute_claim(sb.read_compute_binding())
        await broker._compute_start(handle)
    return handle


async def test_a_compute_start_is_busy_while_a_code_task_of_another_org_lives(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, compute_on: None,
) -> None:
    with bound_run(ORG, thread="t-code"):
        await broker.acquire()
    with bound_run(ORG_B, thread="t-compute"), pytest.raises(sb.SandboxBusy, match="one organization"):
        await broker._compute_claim(sb.read_compute_binding())
    assert len(docker.runs()) == 1


async def test_a_code_task_start_is_busy_while_a_compute_of_another_org_lives(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, compute_on: None,
) -> None:
    handle = await _compute_started(broker, ORG, "t-compute")
    assert handle.remote, "a compute handle on docker-remote is remote"
    assert "--runtime=runsc" in docker.runs()[-1]
    with bound_run(ORG_B, thread="t-code"), pytest.raises(sb.SandboxBusy, match="one organization"):
        await broker.acquire()
    assert len(docker.runs()) == 1


async def test_a_compute_start_reads_the_daemon_under_the_gate(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, compute_on: None,
) -> None:
    """A container that no registry knows still holds the box for compute."""
    docker.unknown_orgs = [ORG]
    with bound_run(ORG_B, thread="t-compute"):
        handle = await broker._compute_claim(sb.read_compute_binding())
        with pytest.raises(sb.SandboxBusy, match="one organization"):
            await broker._compute_start(handle)
    assert docker.runs() == []


async def test_two_starts_at_once_of_compute_and_code_task_give_one_run(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, compute_on: None,
) -> None:
    gate = asyncio.Event()
    docker.block_run = gate

    async def code_task() -> Any:
        with bound_run(ORG, thread="t-code"):
            return await broker.acquire()

    async def compute() -> Any:
        return await _compute_started(broker, ORG_B, "t-compute")

    tasks = [asyncio.create_task(code_task()), asyncio.create_task(compute())]
    await asyncio.sleep(0.1)
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    busy = [r for r in results if isinstance(r, sb.SandboxBusy)]
    assert len(busy) == 1, results
    assert len(docker.runs()) == 1
    assert len(set(docker.box.values())) == 1


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


# ── SBX-15c: three follow-ups of the docker-remote backend ──────────────────


class _StreamProc:
    """A ``docker`` child for :meth:`DockerCLI.collect`, with two real readers."""

    def __init__(self, stdout: bytes, stderr: bytes) -> None:
        self.returncode = 0
        self.stdin = None
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()

    async def wait(self) -> int:
        return 0


#: The stderr of a compute run when the ssh link drops: the docker CLI names
#: the ssh command line, the key file and the cert dir of the gateway box.
_SSH_DROP = (
    b"Traceback: ValueError: bad row\n"
    b"error during connect: command [ssh -o ConnectTimeout=30 -T -l sandbox -p 2222 -- "
    b"10.20.30.40 docker system dial-stdio] has exited with exit status 255: "
    b"sandbox@10.20.30.40: Connection reset.\n"
    b"Identity file /home/acb/.ssh/sandbox_key not accessible.\n"
    b"ssh: connect to host 10.20.30.40 port 2222: Connection refused\n"
    b"dial tcp 10.0.0.5:2376: connect: connection refused\n"
    b"open /etc/metorite/sandbox-tls/key.pem: permission denied\n"
    b"Authorization: Bearer abcdef0123456789 sk-ant-api03-abcdefghijklmnopqrstuvwx\n"
)


def _set_home(monkeypatch: pytest.MonkeyPatch, home: str) -> None:
    """Give ``Path.home()`` the POSIX path *home* on every platform. The broker
    runs on Linux. On Windows ``Path.home()`` does not read ``HOME``."""
    monkeypatch.setattr(sb.Path, "home", classmethod(lambda cls: PurePosixPath(home)))


async def test_the_stderr_of_a_compute_run_holds_no_address_path_or_key(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SBX-15c fix 1. ``run_compute`` gives the stderr of ``collect`` to the
    model, so ``collect`` scrubs it through ``_scrub``, the one scrub of the
    broker."""
    _set_home(monkeypatch, "/home/acb")
    monkeypatch.setattr(env["settings"], "sandbox_docker_cert_path", "/etc/metorite/sandbox-tls")

    async def fake_exec(*args: Any, **kw: Any) -> _StreamProc:
        return _StreamProc(b"result 42\n", _SSH_DROP)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    cli = sb.DockerCLI()
    cli._binary = lambda: "/usr/bin/docker"  # type: ignore[method-assign]
    got = await cli.collect(["exec", "c", "python", "main.py"], timeout=5, cap=1 << 20)
    err = got.stderr.decode("utf-8")
    for leak in (
        "sandbox@", "-l sandbox", "2222", "/home/acb", "/etc/metorite/sandbox-tls",
        "sk-ant-api03", "abcdef0123456789", "10.20.30.40", "10.0.0.5", "2376",
    ):
        assert leak not in err, f"{leak!r} reached the stderr of a compute run: {err}"
    assert "-l (user) -p (port)" in err
    assert "connect to host (sandbox box) port (port)" in err  # review P3
    assert "dial tcp (sandbox box)" in err
    assert "(host path)/.ssh/sandbox_key" in err and "(secret)" in err
    assert "ValueError: bad row" in err, "the member's own error stays readable"
    assert got.stdout == b"result 42\n", "the stdout of the member's code stays as it is"


def test_the_scrub_takes_a_whole_host_path_on_any_backend(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_backend", "docker")
    monkeypatch.setattr(env["settings"], "sandbox_docker_host", "")
    _set_home(monkeypatch, "/root")
    out = sb._scrub("open /root/.ssh/id: no; /rootfs/bin and /root.bak stay; /root. Bearer abc.def")
    assert out == (
        "open (host path)/.ssh/id: no; /rootfs/bin and /root.bak stay; (host path). (secret)"
    )
    _set_home(monkeypatch, "/")
    assert sb._scrub("/usr/bin/python") == "/usr/bin/python", "a home of / takes nothing"


async def test_a_failed_startup_removal_is_recorded_and_reaped(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """SBX-15c fix 2. The ssh link drops during the ``rm -f`` of the startup
    sweep. The old container of org A goes to the orphan record, the reaper
    removes it, and org B then starts."""
    docker.box = {"cid-old-a": ORG}
    docker.ps_ids = ["cid-old-a"]
    docker.fail_rm = 1
    await broker.sweep()
    assert docker.box == {"cid-old-a": ORG}, "the failed removal left the container"
    assert broker._remote_orphans == {"cid-old-a"}, "a failed sweep removal must be recorded"
    assert broker._reaper is not None, "the reaper runs while an orphan stays"
    with bound_run(ORG_B, thread="t-b"), pytest.raises(sb.SandboxBusy):
        await broker.acquire()
    docker.ps_ids = []
    await broker.reap_once()
    assert docker.box == {} and not broker._remote_orphans
    with bound_run(ORG_B, thread="t-b"):
        b = await broker.acquire()
    assert b.org == ORG_B
    await broker.stop()


async def test_a_startup_removal_that_raises_is_recorded_too(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker.box = {"cid-x": ORG}
    docker.ps_ids = ["cid-x"]
    real = docker.run

    async def broken(args: Sequence[str], *, timeout: float) -> sb.DockerResult:
        if list(args[:2]) == ["rm", "-f"]:
            raise OSError("ssh: broken pipe")
        return await real(args, timeout=timeout)

    monkeypatch.setattr(docker, "run", broken)
    assert await broker.sweep() == 0
    assert broker._remote_orphans == {"cid-x"}
    await broker.stop()


async def test_the_local_sweep_keeps_no_orphan_record(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record is for the remote box. The local sweep runs at each start."""
    monkeypatch.setattr(env["settings"], "sandbox_backend", "docker")
    docker.ps_ids = ["cid-x"]
    docker.fail_rm = 1
    await broker.sweep()
    assert not broker._remote_orphans


def _late_daemon(docker: RemoteFakeDocker, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """``docker run`` times out. The test then makes the container, as a daemon
    that finishes the create after the CLI died does. The start-label lookup
    reads the daemon as it is."""
    seen: dict[str, str] = {}
    real = docker.run

    async def run(args: Sequence[str], *, timeout: float) -> sb.DockerResult:
        argv = list(args)
        if argv[0] == "run":
            docker.calls.append(argv)
            labels = dict(v.split("=", 1) for v in flag_values(argv, "--label"))
            seen.update(start=labels[sb.LABEL_START], org=labels[sb.LABEL_ORG])
            return sb.DockerResult(-1, "", "docker run timed out", timed_out=True)
        if argv[:3] == ["ps", "-aq", "--filter"] and argv[3].startswith(f"label={sb.LABEL_START}="):
            docker.calls.append(argv)
            sid = argv[3].split("=", 2)[2]
            ids = [cid for cid, start in docker.starts.items() if start == sid]
            return sb.DockerResult(0, "".join(f"{i}\n" for i in ids), "")
        return await real(args, timeout=timeout)

    monkeypatch.setattr(docker, "run", run)
    return seen


def _daemon_makes(docker: RemoteFakeDocker, seen: dict[str, str], cid: str = "cid-late") -> None:
    docker.box[cid] = seen["org"]
    docker.starts[cid] = seen["start"]


async def test_a_container_made_after_a_run_timeout_is_reaped(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SBX-15c fix 3. The cleanup looks up the start label and finds nothing,
    and the daemon makes the container after that. The start label stays in
    the orphan record, so the next tick finds the container and removes it."""
    seen = _late_daemon(docker, monkeypatch)
    with bound_run(ORG, thread="t-late"), pytest.raises(sb.SandboxUnavailable):
        await broker.acquire()
    entry = f"start:{seen['start']}"
    assert entry in broker._remote_orphans, "a run timeout must leave its start label"
    lookups = [c for c in docker.calls if c[:3] == ["ps", "-aq", "--filter"]]
    assert lookups, "the cleanup looked up the start label once"
    await broker.reap_once()
    assert entry in broker._remote_orphans, "an empty lookup inside the window keeps it"
    _daemon_makes(docker, seen)
    await broker.reap_once()
    assert ["rm", "-f", "cid-late"] in docker.calls
    assert docker.box == {} and not broker._remote_orphans and not broker._late_starts
    await broker.stop()


async def test_a_cancel_during_docker_run_leaves_its_start_label(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P2. A cancel kills the CLI as a timeout does, so the daemon can
    make the container late on this path too."""
    docker.block_run = asyncio.Event()
    with bound_run(ORG, thread="t-cancel"):
        task = asyncio.create_task(broker.acquire())
        await until(lambda: docker.runs(), "docker run must be in flight")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    labels = dict(v.split("=", 1) for v in flag_values(docker.runs()[-1], "--label"))
    entry = f"start:{labels[sb.LABEL_START]}"
    assert entry in broker._remote_orphans and entry in broker._late_starts
    await broker.stop()


async def test_a_late_start_entry_ends_with_its_window(
    docker: RemoteFakeDocker, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    broker = sb.SandboxBroker(docker=docker, clock=lambda: now[0])  # type: ignore[arg-type]
    seen = _late_daemon(docker, monkeypatch)
    with bound_run(ORG, thread="t-late"), pytest.raises(sb.SandboxUnavailable):
        await broker.acquire()
    now[0] += sb._LATE_START_WINDOW_SECONDS - 1
    await broker.reap_once()
    assert f"start:{seen['start']}" in broker._remote_orphans
    now[0] += 2
    await broker.reap_once()
    assert not broker._remote_orphans and not broker._late_starts
    await broker.stop()


async def test_a_compute_run_timeout_leaves_its_start_label_too(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker, compute_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _late_daemon(docker, monkeypatch)
    with bound_run(ORG, thread="t-compute"):
        handle = await broker._compute_claim(sb.read_compute_binding())
        with pytest.raises(sb.SandboxUnavailable):
            await broker._compute_start(handle)
        broker._compute_drop(handle)
        await broker._remove(handle)  # the `finally` of run_compute
    assert f"start:{seen['start']}" in broker._remote_orphans
    _daemon_makes(docker, seen)
    await broker.reap_once()
    assert docker.box == {} and not broker._remote_orphans
    await broker.stop()


async def test_a_run_that_fails_in_time_records_no_late_start(
    broker: sb.SandboxBroker, docker: RemoteFakeDocker,
) -> None:
    """Only a timeout leaves a start label. A refused run made nothing."""
    docker.fail_run = True
    with bound_run(ORG, thread="t-fail"), pytest.raises(sb.SandboxUnavailable):
        await broker.acquire()
    assert not broker._remote_orphans and not broker._late_starts
    await broker.stop()


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


@pytest.mark.sandbox_docker
async def test_docker_a_name_that_cannot_come_back_never_touches_the_host(
    remote: dict[str, Any],
) -> None:
    r"""Review P1 with real GNU tar. The container writes ``a\b`` beside the
    host's real ``a/b``, and a name with the byte 0xE9. Neither reaches the
    host, and the real ``a/b`` keeps its bytes. A UTF-8 name goes in and
    comes back as itself."""
    broker, host = remote["broker"], remote["host"]
    with bound_run(DOCKER_ORG, thread="t-names") as ws:
        (ws / "a").mkdir()
        (ws / "a" / "b").write_bytes(b"real")
        (ws / "café.txt").write_bytes(b"cafe")
        handle = await broker.acquire()
        # Raw strings: `\b` and `\351` must reach bash as text, so that
        # bash makes the backslash name and the byte 0xE9.
        result = await broker.exec(
            handle,
            r"printf x > 'a\b'; printf y > " + r'"$(printf ' + r"'lat\351.txt')" + '"; '
            "printf z > a/new.txt; printf n > naïve.txt; cat café.txt; "
            r"ls -b",
            30,
        )
        assert result.exit_code == 0, result.output
        assert "lat\\351.txt" in result.output and "a\\\\b" in result.output, result.output
        assert result.message == "", result.message
        assert "cafe" in result.output, "a UTF-8 name goes in as itself"
        assert (ws / "a" / "b").read_bytes() == b"real"
        assert (ws / "a" / "new.txt").read_bytes() == b"z"
        assert (ws / "naïve.txt").read_bytes() == b"n", "a UTF-8 name comes back as itself"
        assert not [p for p in ws.rglob("*") if "lat" in p.name or "\\" in p.name]
        second = await broker.exec(handle, "cat a/b", 30)
        assert second.output.strip() == "real", "the real a/b still goes in whole"
        await broker.release(handle)
        assert await broker.end_run() is True
        await broker.settle()
    gone = _docker("ps", "-aq", "--filter", f"label=metorite.start={handle.start_id}", host=host)
    assert gone.stdout.strip() == ""


# ── SBX-15c on a real daemon ────────────────────────────────────────────────


class _OwnOrgCLI(sb.DockerCLI):
    """The real CLI, with two changes for a test on a shared daemon.

    The sweep sees only the containers of ``DOCKER_ORG``, so it never removes
    a sandbox of another session on this daemon. ``fail_rm`` refuses that many
    ``rm -f`` calls, as a dropped ssh link does. ``late_run`` makes each
    ``docker run`` time out before it reaches the daemon, and keeps its labels.
    """

    def __init__(self) -> None:
        self.fail_rm = 0
        self.late_run = False
        self.labels: dict[str, str] = {}

    async def run(
        self, args: Sequence[str], *, timeout: float, stdin: bytes | None = None,
    ) -> sb.DockerResult:
        argv = list(args)
        if argv == ["ps", "-aq", "--filter", f"label={sb.LABEL_SANDBOX}=1"]:
            argv += ["--filter", f"label={sb.LABEL_ORG}={DOCKER_ORG}"]
        if argv[:2] == ["rm", "-f"] and self.fail_rm:
            self.fail_rm -= 1
            return sb.DockerResult(1, "", "error during connect: ssh: connection reset")
        if argv[:1] == ["run"] and self.late_run:
            self.labels = dict(v.split("=", 1) for v in flag_values(argv, "--label"))
            return sb.DockerResult(-1, "", "docker run timed out", timed_out=True)
        return await super().run(argv, timeout=timeout, stdin=stdin)


def _labelled(host: str, image: str, labels: dict[str, str]) -> str:
    made = _docker(
        "create", *[f"--label={k}={v}" for k, v in labels.items()], image, "true", host=host,
    )
    assert made.returncode == 0, made.stderr
    return made.stdout.strip()


def _exists(host: str, cid: str) -> bool:
    return _docker("ps", "-aq", "--no-trunc", "--filter", f"id={cid}", host=host).stdout.strip() != ""


@pytest.mark.sandbox_docker
async def test_docker_the_stderr_of_collect_is_scrubbed_on_a_real_daemon(
    remote: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SBX-15c fix 1 through real pipes: a container writes the daemon address,
    a path of this box's home and a key to its stderr."""
    host = remote["host"]
    monkeypatch.setenv("HOME", "/home/acb")
    script = f"echo {host} /home/acb/.ssh/k sk-ant-api03-abcdefghijklmnopqrstuvwx >&2; echo out"
    got = await sb.DockerCLI().collect(
        ["run", "--rm", f"--label={sb.LABEL_ORG}={DOCKER_ORG}", "--network=none",
         remote["settings"].sandbox_image, "sh", "-c", script],
        timeout=120, cap=1 << 20,
    )
    assert got.rc == 0, got.stderr
    err = got.stderr.decode("utf-8")
    assert host not in err and "/home/acb" not in err and "sk-ant-api03" not in err, err
    assert sb.redact_docker_host(host) in err
    assert "(host path)/.ssh/k" in err and "(secret)" in err
    assert got.stdout == b"out\n"


@pytest.mark.sandbox_docker
async def test_docker_a_failed_sweep_removal_is_reaped_on_a_real_daemon(
    remote: dict[str, Any],
) -> None:
    """SBX-15c fix 2: the sweep's ``rm -f`` fails, and the reaper removes the
    container from the real daemon."""
    host, image = remote["host"], remote["settings"].sandbox_image
    cid = _labelled(host, image, {sb.LABEL_SANDBOX: "1", sb.LABEL_ORG: DOCKER_ORG})
    cli = _OwnOrgCLI()
    broker = sb.SandboxBroker(docker=cli)
    cli.fail_rm = 1
    assert await broker.sweep() == 0, "a failed removal removed nothing"
    assert _exists(host, cid), "the failed removal left the container"
    assert any(cid.startswith(o) for o in broker._remote_orphans)
    await broker.reap_once()
    assert not _exists(host, cid) and not broker._remote_orphans
    await broker.stop()


@pytest.mark.sandbox_docker
async def test_docker_a_container_made_after_a_run_timeout_is_reaped_on_a_real_daemon(
    remote: dict[str, Any],
) -> None:
    """SBX-15c fix 3: the daemon makes the container after the cleanup lookup.
    The next reaper tick finds it by its start label on the real daemon."""
    host, image = remote["host"], remote["settings"].sandbox_image
    cli = _OwnOrgCLI()
    cli.late_run = True
    broker = sb.SandboxBroker(docker=cli)
    with bound_run(DOCKER_ORG, thread="t-late"), pytest.raises(sb.SandboxUnavailable):
        await broker.acquire()
    start = cli.labels[sb.LABEL_START]
    assert f"start:{start}" in broker._remote_orphans
    cid = _labelled(host, image, cli.labels)
    await broker.reap_once()
    assert not _exists(host, cid), "the late container outlived the reaper tick"
    assert not broker._remote_orphans and not broker._late_starts
    await broker.stop()
