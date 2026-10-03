"""WS43-F4 — exec hygiene, the restart, the output cap and the disk checks.

Spec ``project-docs/specs/maf_coding_engine.md`` §7.1 rules 9, 10 and 11, the
done-when 3 of WS-43c, and §10 WS43-F4.

What breaks this fence: a pipe loses the exit code, a child that calls
``setsid`` or forks twice outlives its exec, a broken container is not
restarted, a timeout does not kill, output passes the cap, or a disk check
fails to refuse.

Two halves. The first half drives the broker with a fake Docker, and runs in
the default unit job. The second half carries the ``sandbox_docker`` marker
and runs a real container. The default run deselects it, and pr-check runs it
in its own step, which fails on any skip, so a Docker test that skips proves
nothing (§10).

⚠️ Docker Desktop on a Windows dev box refuses host bind mounts (spec §5.2).
There, the Docker half swaps each bind mount for a named volume, and the one
test that needs the real bind mount skips. On Linux, in CI, every test runs
with the real bind mount of production.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import FakeDocker, bound_run, configure_env

ORG = "dddddddd-0000-0000-0000-00000000000d"
_REAL_PROCESS_IDS = sb._process_ids

#: The image of the Docker half until WS-43b ships the coding-sandbox image.
#: It is pinned by digest, as the broker demands. It has bash and coreutils,
#: and no procps, so the broker's scripts must work without ``ps`` or ``pkill``.
TEST_IMAGE = os.environ.get(
    "SANDBOX_TEST_IMAGE",
    "python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    return configure_env(monkeypatch, tmp_path)


@pytest.fixture
def docker() -> FakeDocker:
    return FakeDocker()


@pytest.fixture
def broker(docker: FakeDocker, env: dict[str, Any]) -> sb.SandboxBroker:
    return sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]


async def _acquire(broker: sb.SandboxBroker, thread: str = "t-1") -> sb.SandboxHandle:
    with bound_run(ORG, thread=thread):
        return await broker.acquire()


def _stream(rc: int | None, out: str = "", err: str = "", *, host_timeout: bool = False) -> Any:
    data = out.encode()
    return sb.StreamResult(rc, out, len(data), False, err, host_timeout)


# ═════════════════════════ the fake-Docker half ═════════════════════════════


async def test_each_exec_runs_under_timeout_kill_and_pipefail(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    await broker.exec(handle, "false | true", 30)
    argv = docker.command_execs()[-1]
    assert argv[:4] == ["exec", "--workdir", "/workspace", handle.name]
    assert argv[4:7] == ["bash", "-c", sb.EXEC_WRAPPER]
    assert argv[-2:] == ["30", "false | true"], "the command must be ONE argv entry"
    assert 'timeout -s KILL "$1"' in sb.EXEC_WRAPPER
    assert 'bash -o pipefail -c "$2"' in sb.EXEC_WRAPPER
    assert sb.EXEC_WRAPPER.rstrip().endswith("2>&1")


@pytest.mark.parametrize("asked,used", [(9999, 300), (0, 1), (-5, 1), (45, 45)])
async def test_the_timeout_is_clamped(
    broker: sb.SandboxBroker, docker: FakeDocker, asked: int, used: int,
) -> None:
    handle = await _acquire(broker)
    await broker.exec(handle, "true", asked)
    assert docker.command_execs()[-1][-2] == str(used)


async def test_every_exec_ends_with_the_kill_sweep(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    assert handle.init_pid == 1 and handle.keepalive_pid == 7
    await broker.exec(handle, "echo a", 5)
    await broker.exec(handle, "echo b", 5)
    sweeps = docker.sweeps()
    assert len(sweeps) == 2
    assert all(s[-1] == "7" for s in sweeps), "the sweep must spare the keep-alive PID"
    verbs = [c[0] for c in docker.calls if c[0] == "exec"]
    order = ["cmd" if sb.EXEC_WRAPPER in c else "sweep" if sb.KILL_SWEEP in c else "probe"
             for c in docker.calls if c[0] == "exec"]
    assert order == ["probe", "cmd", "sweep", "cmd", "sweep"], (verbs, order)


def test_the_kill_sweep_walks_proc_and_spares_only_init_keepalive_and_itself() -> None:
    script = sb.KILL_SWEEP
    assert 'case "$pid" in 1|"$keep"|"$self") continue' in script
    assert "/proc/[0-9]*" in script, "a process group or a session must not limit the sweep"
    assert "kill -9" in script
    assert "pkill" not in script and " ps " not in script, "the image may lack procps"


async def test_a_survivor_restarts_the_container(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    docker.stream_results.append(_stream(0, "done\n"))
    docker.sweep_results.append(sb.DockerResult(3, "survivors: 41\n", ""))
    result = await broker.exec(handle, "stubborn &", 5)
    assert result.restarted and result.message == sb.SURVIVOR_MESSAGE
    assert result.output == "done\n" and result.exit_code == 0
    assert docker.removals() == [handle.name]
    assert len(docker.runs()) == 2


async def test_a_failed_sweep_restarts_the_container(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    docker.sweep_results.append(
        sb.DockerResult(126, "", "OCI runtime exec failed: procReady not received")
    )
    result = await broker.exec(handle, "true", 5)
    assert result.restarted
    assert len(docker.runs()) == 2


@pytest.mark.parametrize("err", [
    "Error response from daemon: container abc is not running",
    "Error response from daemon: No such container: mtr-sbx-1",
    "OCI runtime exec failed: exec failed: unable to start container process",
])
async def test_a_broken_container_restarts_once_and_the_command_does_not_rerun(
    broker: sb.SandboxBroker, docker: FakeDocker, err: str,
) -> None:
    handle = await _acquire(broker)
    docker.stream_results.append(_stream(1, err=err))
    result = await broker.exec(handle, "make build", 5)
    assert result.restarted and result.exit_code is None
    assert result.message == sb.RESTART_MESSAGE
    assert "/tmp is empty" in result.message and "run the command again" in result.message
    assert len(docker.command_execs()) == 1, "the broker must not run the command again"
    assert len(docker.runs()) == 2
    assert docker.removals() == [handle.name]
    again = await broker.exec(handle, "make build", 5)
    assert not again.restarted and again.exit_code == 0


async def test_a_command_that_fails_by_itself_is_not_a_broken_container(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    docker.stream_results.append(_stream(127, "bash: nope: command not found\n"))
    result = await broker.exec(handle, "nope", 5)
    assert result.exit_code == 127 and not result.restarted
    assert len(docker.runs()) == 1


async def test_a_failed_restart_raises_and_never_runs_on_the_host(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    docker.stream_results.append(_stream(1, err="Error response from daemon: is not running"))
    docker.fail_run = True
    with pytest.raises(sb.SandboxUnavailable, match="Nothing runs on the host"):
        await broker.exec(handle, "true", 5)
    with pytest.raises(sb.SandboxRefused, match="stopped"):
        await broker.exec(handle, "true", 5)
    assert len(docker.command_execs()) == 1


async def test_a_host_side_timeout_counts_as_a_kill(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    docker.stream_results.append(_stream(None, "partial", host_timeout=True))
    result = await broker.exec(handle, "sleep 999", 2)
    assert result.timed_out and result.exit_code == 137
    assert len(docker.sweeps()) == 1, "the sweep must still kill what the command left"


async def test_one_exec_at_a_time_per_container(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    active = 0
    peak = 0

    async def slow(_argv: list[str]) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1

    docker.on_stream = slow
    await asyncio.gather(*(broker.exec(handle, f"echo {i}", 5) for i in range(4)))
    assert peak == 1


async def test_host_files_holds_the_exec_lock(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    handle = await _acquire(broker)
    events: list[str] = []

    async def mark(_argv: list[str]) -> None:
        events.append("exec")

    docker.on_stream = mark
    async with broker.host_files(handle):
        task = asyncio.create_task(broker.exec(handle, "true", 5))
        await asyncio.sleep(0.05)
        events.append("host-done")
    await task
    assert events == ["host-done", "exec"]


# ── the output cap ───────────────────────────────────────────────────────────


def test_capped_output_keeps_head_and_tail_and_the_total() -> None:
    capped = sb.CappedOutput(6, 6)
    for chunk in (b"ABCDEFGH", b"-" * 100, b"0123456789"):
        capped.feed(chunk)
    assert capped.total == 118 and capped.truncated
    text = capped.text()
    assert text.startswith("ABCDEF") and text.endswith("456789")
    assert "118 bytes in total" in text
    small = sb.CappedOutput(6, 6)
    small.feed(b"hello world")
    assert small.text() == "hello world" and not small.truncated


async def test_the_cli_stream_caps_five_million_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real streaming path, with python in place of the docker binary."""
    cli = sb.DockerCLI()
    monkeypatch.setattr(cli, "_binary", lambda: sys.executable)
    result = await cli.stream(
        ["-c", "import sys; sys.stdout.write('a' * 5_000_000 + 'END')"],
        timeout=60, head=6144, tail=6144,
    )
    assert result.rc == 0 and result.total_bytes == 5_000_003 and result.truncated
    assert result.output.startswith("a" * 6144) and result.output.endswith("END")
    assert len(result.output) < 12288 + 200
    assert "5000003 bytes in total" in result.output


async def test_the_cli_stream_kills_at_the_host_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = sb.DockerCLI()
    monkeypatch.setattr(cli, "_binary", lambda: sys.executable)
    started = time.monotonic()
    result = await cli.stream(
        ["-c", "import time; print('x', flush=True); time.sleep(30)"],
        timeout=1.5, head=100, tail=100,
    )
    assert result.host_timed_out and result.rc is None
    assert time.monotonic() - started < 10


async def test_the_exec_cap_comes_from_settings(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, int] = {}
    original = docker.stream

    async def spy(args: Any, *, timeout: float, head: int, tail: int) -> Any:
        seen.update(head=head, tail=tail)
        return await original(args, timeout=timeout, head=head, tail=tail)

    monkeypatch.setattr(docker, "stream", spy)
    handle = await _acquire(broker)
    await broker.exec(handle, "true", 5)
    assert seen == {"head": 6144, "tail": 6144}


# ── rule 10: the disk checks ─────────────────────────────────────────────────


async def test_the_free_space_floor_refuses_a_start_and_an_exec(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = await _acquire(broker)
    monkeypatch.setattr(env["settings"], "sandbox_min_free_disk_mb", 5120)
    monkeypatch.setattr(broker, "free_disk_mb", lambda: 100)
    with pytest.raises(sb.SandboxRefused, match="below the floor"):
        await broker.exec(handle, "true", 5)
    with pytest.raises(sb.SandboxRefused, match="below the floor"):
        await _acquire(broker, thread="t-new")
    assert docker.command_execs() == []
    assert len(docker.runs()) == 1


def test_the_free_space_is_measured_on_the_state_root_file_system(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Path] = []

    def usage(path: Any) -> Any:
        seen.append(Path(path))
        return shutil._ntuple_diskusage(10, 5, 42 * 1024 * 1024)  # type: ignore[attr-defined]

    monkeypatch.setattr(sb.shutil, "disk_usage", usage)
    assert sb.SandboxBroker().free_disk_mb() == 42
    from acb_skills.agent_paths import state_root

    assert state_root().is_relative_to(seen[0]) or seen[0] == state_root()


async def test_the_quota_refuses_the_next_exec_until_space_is_free(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_workspace_quota_mb", 1)
    with bound_run(ORG, thread="t-q") as ws:
        handle = await broker.acquire()
    big = ws / "outputs" / "big.bin"
    big.parent.mkdir(parents=True, exist_ok=True)

    async def write_big(_argv: list[str]) -> None:
        big.write_bytes(b"x" * (2 * 1024 * 1024))

    docker.on_stream = write_big
    first = await broker.exec(handle, "make big", 5)
    assert first.exit_code == 0
    assert broker.quota_exceeded(handle), "the measure after the exec must set the flag"
    docker.on_stream = None
    with pytest.raises(sb.SandboxRefused, match="over its quota"):
        await broker.exec(handle, "true", 5)
    assert len(docker.command_execs()) == 1
    big.unlink()  # file_access_delete still works, so the agent frees space
    again = await broker.exec(handle, "true", 5)
    assert again.exit_code == 0 and not broker.quota_exceeded(handle)


def test_the_quota_measure_follows_no_link(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "b.txt").write_bytes(b"y" * 50)
    assert sb._dir_size_bytes(tmp_path) == 150


# ═════════════════════════ the real-Docker half ═════════════════════════════


def _docker(*args: str, timeout: float = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


@pytest.fixture(scope="module")
def docker_image() -> str:
    if shutil.which("docker") is None:
        pytest.skip("Docker is not installed")
    if _docker("info", timeout=60).returncode != 0:
        pytest.skip("the Docker daemon does not answer")
    if _docker("image", "inspect", TEST_IMAGE).returncode != 0:
        pulled = _docker("pull", TEST_IMAGE, timeout=900)
        assert pulled.returncode == 0, pulled.stderr[-500:]
    return TEST_IMAGE


@pytest.fixture(scope="module")
def bind_mounts_work(docker_image: str, tmp_path_factory: pytest.TempPathFactory) -> bool:
    probe = tmp_path_factory.mktemp("bind-probe")
    result = _docker(
        "run", "--rm", "--mount", f"type=bind,source={probe},target=/w", docker_image, "true",
    )
    return result.returncode == 0


def _use_named_volumes(monkeypatch: pytest.MonkeyPatch, image: str, made: list[str]) -> None:
    """Docker Desktop only: mount a named volume where production binds a dir."""
    names: dict[str, str] = {}

    def args(self: sb.Mount) -> list[str]:
        key = str(self.source)
        if key not in names:
            name = f"mtr-test-{uuid.uuid4().hex[:10]}"
            assert _docker("volume", "create", name).returncode == 0
            fix = _docker(
                "run", "--rm", "--mount", f"type=volume,source={name},target=/v",
                image, "chown", "1000:1000", "/v",
            )
            assert fix.returncode == 0, fix.stderr
            names[key] = name
            made.append(name)
        spec = f"type=volume,source={names[key]},target={self.target}"
        return ["--mount", spec + (",readonly" if self.readonly else "")]

    monkeypatch.setattr(sb.Mount, "args", args)


@pytest.fixture
async def real(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker_image: str, bind_mounts_work: bool,
) -> Any:
    env = configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr(env["settings"], "sandbox_image", docker_image)
    monkeypatch.setattr(sb, "_process_ids", _REAL_PROCESS_IDS)
    volumes: list[str] = []
    if not bind_mounts_work:
        _use_named_volumes(monkeypatch, docker_image, volumes)
    broker = sb.SandboxBroker()
    env.update(broker=broker, bind=bind_mounts_work)
    try:
        yield env
    finally:
        for handle in list(broker._live.values()):
            _docker("rm", "-f", handle.name)
        await broker.stop()
        for name in volumes:
            _docker("volume", "rm", "-f", name)


def _strays(handle: sb.SandboxHandle) -> list[str]:
    """Every PID in the container except init, the keep-alive and the probe."""
    probe = _docker("exec", handle.name, "bash", "-c", sb.PID_PROBE)
    assert probe.returncode == 0, probe.stderr
    pids = [p for p in probe.stdout.split() if p.isdigit()]
    return [p for p in pids if p != str(handle.keepalive_pid)]


async def _real_handle(real: dict[str, Any], thread: str = "t-docker") -> sb.SandboxHandle:
    with bound_run(ORG, thread=thread):
        return await real["broker"].acquire()


@pytest.mark.sandbox_docker
async def test_docker_a_pipe_keeps_the_exit_code(real: dict[str, Any]) -> None:
    handle = await _real_handle(real)
    broker = real["broker"]
    assert (await broker.exec(handle, "false | true", 20)).exit_code == 1
    assert (await broker.exec(handle, "(exit 7) | cat", 20)).exit_code == 7
    assert (await broker.exec(handle, "echo ok | cat", 20)).exit_code == 0


@pytest.mark.sandbox_docker
async def test_docker_a_setsid_child_dies_with_its_exec(real: dict[str, Any]) -> None:
    handle = await _real_handle(real)
    result = await real["broker"].exec(
        handle, "setsid sleep 300 >/dev/null 2>&1 </dev/null & echo started", 20,
    )
    assert result.exit_code == 0 and "started" in result.output and not result.restarted
    assert _strays(handle) == [], "a setsid child outlived its exec"


@pytest.mark.sandbox_docker
async def test_docker_a_double_forked_child_dies_with_its_exec(real: dict[str, Any]) -> None:
    handle = await _real_handle(real)
    result = await real["broker"].exec(
        handle, "( (sleep 300 >/dev/null 2>&1 </dev/null &) & ); echo forked", 20,
    )
    assert result.exit_code == 0 and "forked" in result.output
    assert _strays(handle) == [], "a double-forked child outlived its exec"


@pytest.mark.sandbox_docker
async def test_docker_many_orphans_die_with_their_exec(real: dict[str, Any]) -> None:
    handle = await _real_handle(real)
    result = await real["broker"].exec(
        handle,
        "for i in $(seq 1 40); do (setsid sleep 300 >/dev/null 2>&1 </dev/null &); done; "
        "echo spawned",
        30,
    )
    assert "spawned" in result.output
    assert _strays(handle) == []


@pytest.mark.sandbox_docker
async def test_docker_a_background_child_that_holds_stdout_cannot_hang_the_exec(
    real: dict[str, Any],
) -> None:
    handle = await _real_handle(real)
    started = time.monotonic()
    result = await real["broker"].exec(handle, "sleep 300 & echo bg", 2)
    assert time.monotonic() - started < 2 + sb._HOST_GRACE_SECONDS + 10
    assert "bg" in result.output
    assert _strays(handle) == []


@pytest.mark.sandbox_docker
async def test_docker_a_timeout_kills(real: dict[str, Any]) -> None:
    handle = await _real_handle(real)
    started = time.monotonic()
    result = await real["broker"].exec(handle, "sleep 30", 2)
    elapsed = time.monotonic() - started
    assert result.exit_code == 137 and result.timed_out
    assert elapsed < 10, f"the timeout did not kill in time ({elapsed:.1f} s)"
    assert _strays(handle) == []


@pytest.mark.sandbox_docker
async def test_docker_output_is_capped(real: dict[str, Any]) -> None:
    handle = await _real_handle(real)
    result = await real["broker"].exec(handle, "head -c 5000000 /dev/zero | tr '\\0' a", 60)
    assert result.exit_code == 0
    assert result.output_bytes == 5_000_000 and result.truncated
    assert result.output.startswith("a" * 6144) and result.output.endswith("a" * 6144)
    assert len(result.output) < 12288 + 200
    assert "5000000 bytes in total" in result.output


@pytest.mark.sandbox_docker
async def test_docker_a_broken_container_is_restarted(real: dict[str, Any]) -> None:
    handle = await _real_handle(real)
    broker = real["broker"]
    await broker.exec(handle, "echo stale > /tmp/marker", 20)
    assert _docker("kill", handle.name).returncode == 0
    result = await broker.exec(handle, "echo hello", 20)
    assert result.restarted and result.exit_code is None
    assert result.message == sb.RESTART_MESSAGE
    after = await broker.exec(handle, "ls -A /tmp | wc -l", 20)
    assert after.exit_code == 0 and after.output.strip() == "0", "/tmp must be empty"
    assert (await broker.exec(handle, "echo hello", 20)).output.strip() == "hello"


@pytest.mark.sandbox_docker
async def test_docker_the_flags_of_rule_6_hold_in_a_real_container(real: dict[str, Any]) -> None:
    handle = await _real_handle(real)
    broker = real["broker"]
    uid, _gid = _REAL_PROCESS_IDS()
    assert (await broker.exec(handle, "id -u", 20)).output.strip() == str(uid)
    status = (await broker.exec(handle, "cat /proc/self/status", 20)).output
    assert "CapEff:\t0000000000000000" in status
    assert "NoNewPrivs:\t1" in status
    assert (await broker.exec(handle, "ls /sys/class/net", 20)).output.split() == ["lo"]
    assert (await broker.exec(handle, "touch /usr/x", 20)).exit_code != 0, "the root is writable"
    assert (await broker.exec(handle, "touch /tmp/x", 20)).exit_code == 0
    assert (await broker.exec(handle, "getent hosts example.com", 20)).exit_code != 0


@pytest.mark.sandbox_docker
async def test_docker_the_bind_mount_writes_show_on_the_host_with_the_gateway_uid(
    real: dict[str, Any],
) -> None:
    """WS-43c done-when 3. Production binds the tenant dir, so CI proves it."""
    if not real["bind"]:
        pytest.skip("Docker Desktop refused the host bind mount (spec §5.2). CI runs this.")
    with bound_run(ORG, thread="t-bind") as ws:
        (ws / "outputs").mkdir(exist_ok=True)
        (ws / ".git" / "hooks").mkdir(parents=True)
        (ws / ".git" / "config").write_text("[remote]\n url = https://x-token:TOKEN@h\n")
        handle = await real["broker"].acquire()
    broker = real["broker"]
    wrote = await broker.exec(handle, "echo from-sandbox > /workspace/outputs/f.txt", 20)
    assert wrote.exit_code == 0, wrote.output
    host_file = ws / "outputs" / "f.txt"
    assert host_file.read_text().strip() == "from-sandbox"
    if hasattr(os, "getuid"):
        assert host_file.stat().st_uid == os.getuid()
    listing = await broker.exec(handle, "ls -A /workspace/.git | wc -l", 20)
    assert listing.output.strip() == "0", "the container must see an empty .git"
    hook = await broker.exec(handle, "echo evil > /workspace/.git/hooks/post-commit", 20)
    assert hook.exit_code != 0, "the container wrote into the covered .git"
    assert not (ws / ".git" / "hooks" / "post-commit").exists()
    assert "TOKEN" in (ws / ".git" / "config").read_text()
