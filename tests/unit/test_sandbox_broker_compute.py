"""SBX-F26 — the ``compute`` target of the sandbox broker (SBX-18).

Spec ``project-docs/specs/agent_sandbox.md`` §0c rung 6a', §12 row SBX-18 and
§11 SBX-F26. The broker runs Python over data the chat already holds, in a
container with no network, no secret, no mount and no write to Metorite.

What breaks this fence: the target joins ``MAF_CODING_SCOPE``, an argv gains a
mount or loses a closing flag, a tenant comes from input, an output that is a
link or a nested name leaves the container, a container outlives its run, a
compute run starves a Projects run (or the reverse), the reaper keeps a
container whose org left the flag, or production allows a run with no remote
gVisor backend.

The first half drives the broker with a fake Docker. The last test carries the
``sandbox_docker`` marker and runs a pandas script in a real container.
"""
from __future__ import annotations

import io
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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

ORG = "cccccccc-0000-0000-0000-00000000000c"
OTHER = "cccccccc-0000-0000-0000-00000000000d"
MAIN = b"print('hi')\n"


class ComputeDocker(FakeDocker):
    """A fake that also answers ``run(stdin=)`` and ``collect``."""

    def __init__(self) -> None:
        super().__init__()
        self.stdins: list[bytes | None] = []
        self.python_result = sb.CollectResult(0, b"hi\n", b"", False, False)
        self.tar_out = tar_of({"result.csv": b"a,b\n1,2\n"})
        self.tar_result: sb.CollectResult | None = None
        self.put_rc = 0

    async def run(
        self, args: Sequence[str], *, timeout: float, stdin: bytes | None = None,
    ) -> sb.DockerResult:
        if list(args)[:2] == ["exec", "-i"]:
            self.stdins.append(stdin)
            self.calls.append(list(args))
            return sb.DockerResult(self.put_rc, "", "tar: boom" if self.put_rc else "")
        return await super().run(args, timeout=timeout)

    async def collect(
        self, args: Sequence[str], *, timeout: float, cap: int,
    ) -> sb.CollectResult:
        argv = list(args)
        self.calls.append(argv)
        if "python" in argv:
            return self.python_result
        return self.tar_result or sb.CollectResult(0, self.tar_out, b"", False, False)

    def execs_of(self, word: str) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "exec" and word in c]


def tar_of(entries: dict[str, bytes | tuple[str, str]]) -> bytes:
    """A tar stream. A tuple value is (kind, linkname): a symlink or a hard link."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for name, value in entries.items():
            info = tarfile.TarInfo(name)
            if isinstance(value, tuple):
                info.type = tarfile.SYMTYPE if value[0] == "sym" else tarfile.LNKTYPE
                info.linkname = value[1]
                tar.addfile(info)
            elif value is None:
                continue
            else:
                info.size = len(value)
                tar.addfile(info, io.BytesIO(value))
    return buf.getvalue()


class _Undone:
    """The settings, where every assignment is undone at the end of the test.

    A bare ``settings.x = v`` leaks into the next test file. This proxy sets
    through ``monkeypatch``, so it never does.
    """

    def __init__(self, target: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        object.__setattr__(self, "_real", target)
        object.__setattr__(self, "_mp", monkeypatch)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in type(self._real).model_fields:
            self._mp.setattr(self._real, name, value)
        else:  # a setting that main does not have yet (task 3430)
            self._mp.setitem(self._real.__dict__, name, value)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    e = configure_env(monkeypatch, tmp_path)
    e["settings"] = _Undone(e["settings"], monkeypatch)
    e["settings"].maf_coding_scope = "code_task:*,app_builder:*,compute:*"
    e["settings"].compute_run_orgs = ORG
    e["settings"].acb_env = "dev"
    return e


@pytest.fixture
def docker() -> ComputeDocker:
    return ComputeDocker()


@pytest.fixture
def broker(docker: ComputeDocker, env: dict[str, Any]) -> sb.SandboxBroker:
    return sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]


# ── the target and the scope ─────────────────────────────────────────────────


def test_compute_is_a_scope_target() -> None:
    assert sb.COMPUTE_TARGET == "compute"
    assert sb.COMPUTE_TARGET in sb.MAF_CODING_TARGETS
    assert sb.parse_maf_coding_scope("compute:*") == frozenset({("compute", "*")})
    assert sb.parse_maf_coding_scope(f"compute:{ORG}") == frozenset({("compute", ORG)})
    assert sb.parse_maf_coding_scope("projects:x") == frozenset({("projects", "x")})
    with pytest.raises(ValueError):
        sb.parse_maf_coding_scope("computer:*")
    with pytest.raises(ValueError):
        sb.parse_maf_coding_scope("nonsense:x")


def test_the_scope_alone_does_not_turn_compute_on(env: dict[str, Any]) -> None:
    env["settings"].maf_coding_scope = f"compute:{ORG}"
    env["settings"].compute_run_orgs = ""
    assert not sb.compute_enabled(ORG)


def test_the_flag_alone_does_not_turn_compute_on(env: dict[str, Any]) -> None:
    env["settings"].maf_coding_scope = ""
    env["settings"].compute_run_orgs = "*"
    assert not sb.compute_enabled(ORG)
    env["settings"].maf_coding_scope = "code_task:*,app_builder:*,projects:*"
    assert not sb.compute_enabled(ORG)
    env["settings"].maf_coding_scope = f"compute:{OTHER}"
    assert not sb.compute_enabled(ORG)


def test_the_defaults_are_dark_and_closed() -> None:
    from acb_common.settings import Settings

    fields = Settings.model_fields
    assert fields["compute_run_orgs"].default == ""
    assert fields["compute_max_per_org"].default == 1
    assert fields["compute_max_total"].default == 1
    # Production is the default environment, so the guard binds there.
    assert fields["acb_env"].default == "prod"


def test_compute_enabled_reads_the_flag(env: dict[str, Any]) -> None:
    s = env["settings"]
    s.compute_run_orgs = ""
    assert not sb.compute_enabled(ORG)
    s.compute_run_orgs = "*"
    assert sb.compute_enabled(ORG)
    assert not sb.compute_enabled(None)
    assert not sb.compute_enabled("")
    assert not sb.compute_enabled("   ")
    s.compute_run_orgs = f"{OTHER}, {ORG}"
    assert sb.compute_enabled(ORG) and sb.compute_enabled(OTHER)
    s.compute_run_orgs = f"{OTHER} {ORG}"
    assert sb.compute_enabled(ORG)
    s.compute_run_orgs = OTHER
    assert not sb.compute_enabled(ORG)


# ── the binding ──────────────────────────────────────────────────────────────


def test_the_compute_binding_comes_from_the_run_only(env: dict[str, Any]) -> None:
    with bound_run(ORG, agent="agent-x", thread="t-1", member="m@example.com"):
        plain = sb.read_run_binding()
        compute = sb.read_compute_binding()
    assert compute.agent == "compute" and compute.target == sb.COMPUTE_TARGET
    assert compute.org == ORG == plain.org
    assert compute.thread == plain.thread and compute.member == "m@example.com"
    assert compute.name != plain.name
    assert compute.name == sb.CONTAINER_PREFIX + sb._digest(ORG, "compute", "t-1")
    assert compute.labels()[sb.LABEL_AGENT] == "compute"
    assert compute.labels()[sb.LABEL_ORG] == ORG


def test_the_binding_refuses_a_run_with_no_org(env: dict[str, Any]) -> None:
    with bound_run(None), pytest.raises(sb.SandboxRefused):
        sb.read_compute_binding()


async def test_a_real_agent_named_compute_gets_no_project_container(
    broker: sb.SandboxBroker, env: dict[str, Any], docker: ComputeDocker,
) -> None:
    env["settings"].maf_coding_scope = "code_task:*,compute:*"
    with bound_run(ORG, agent="compute"), pytest.raises(sb.SandboxRefused):
        await broker.acquire()
    assert docker.runs() == []


def test_the_compute_target_mounts_nothing(env: dict[str, Any]) -> None:
    with bound_run(ORG):
        binding = sb.read_compute_binding()
    assert sb.mount_list(binding, sb.GitCover(Path("x"), Path("y"))) == []


# ── the argv ─────────────────────────────────────────────────────────────────


async def test_the_compute_argv_is_closed(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    with bound_run(ORG, thread="t-argv"):
        await broker.run_compute({"main.py": MAIN}, timeout_s=10)
    (argv,) = docker.runs()
    assert "-v" not in argv and "--volume" not in argv and "--mount" not in argv
    assert mounts_of(argv) == []
    assert flag_values(argv, "--network") == ["none"]
    assert "--read-only" in argv
    assert flag_values(argv, "--cap-drop") == ["ALL"]
    assert flag_values(argv, "--security-opt") == ["no-new-privileges"]
    assert flag_values(argv, "--cpus") == ["1"]
    assert flag_values(argv, "--memory") == ["1g"]
    assert flag_values(argv, "--memory-swap") == ["1g"]
    assert flag_values(argv, "--pids-limit") == ["64"]
    tmpfs = flag_values(argv, "--tmpfs")
    assert "/workspace:rw,nosuid,size=64m,uid=1000,gid=1000,mode=0700" in tmpfs
    assert any(t.startswith("/tmp:rw,nosuid,nodev,size=") for t in tmpfs)
    env = dict(e.split("=", 1) for e in flag_values(argv, "--env"))
    assert env["MPLBACKEND"] == "Agg"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONSAFEPATH"] == "1"
    assert not any(k.startswith("PIP_") or k == "PYTHONUSERBASE" for k in env)
    assert set(env) == {"HOME", "PATH", "MPLBACKEND", "PYTHONNOUSERSITE", "PYTHONSAFEPATH"}
    assert "--name" in argv and flag_values(argv, "--name") == [
        sb.CONTAINER_PREFIX + sb._digest(ORG, "compute", "t-argv")
    ]
    assert f"{sb.LABEL_AGENT}=compute" in flag_values(argv, "--label")


async def test_the_compute_argv_ignores_the_loose_sandbox_limits(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    env["settings"].sandbox_cpus = "4"
    env["settings"].sandbox_memory = "8g"
    env["settings"].sandbox_pids_limit = 4096
    with bound_run(ORG):
        await broker.run_compute({"main.py": MAIN}, timeout_s=10)
    (argv,) = docker.runs()
    assert flag_values(argv, "--cpus") == ["1"] and flag_values(argv, "--memory") == ["1g"]
    assert flag_values(argv, "--pids-limit") == ["64"]


# ── the input and the output ─────────────────────────────────────────────────


async def test_the_inputs_go_in_as_one_tar_and_the_script_runs(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    files = {"main.py": MAIN, "inputs/tr_0123abcd.txt": b"x,y\n1,2\n"}
    with bound_run(ORG):
        result = await broker.run_compute(files, timeout_s=10)
    (put,) = docker.execs_of("-x")
    assert put[:2] == ["exec", "-i"] and put[3:] == ["tar", "-x", "-C", "/workspace"]
    (stdin,) = docker.stdins
    with tarfile.open(fileobj=io.BytesIO(stdin or b"")) as tar:
        names = {m.name: m for m in tar}
        assert set(names) == {"inputs", "out", "main.py", "inputs/tr_0123abcd.txt"}
        assert names["out"].isdir()
        assert tar.extractfile("main.py").read() == MAIN  # type: ignore[union-attr]
    (ran,) = docker.execs_of("python")
    assert ran[-6:] == ["timeout", "-s", "KILL", "10", "python", "main.py"]
    assert ran[ran.index("--workdir") + 1] == "/workspace"
    (got,) = docker.execs_of("-c")
    assert got[-5:] == ["tar", "-c", "-C", "/workspace/out", "."]
    assert result.exit_code == 0 and result.stdout == "hi\n" and not result.timed_out
    assert result.files == {"result.csv": b"a,b\n1,2\n"}
    assert result.duration_ms >= 0


@pytest.mark.parametrize("name", [
    "other.py", "../main.py", "/main.py", "inputs/x.txt", "inputs/tr_0123ABCD.txt",
    "inputs/tr_0123abcd.txt.bak", "inputs/sub/tr_0123abcd.txt", "inputs/tr_0123abc.txt",
    "main.py\n", "out/main.py", "inputs/../main.py",
])
async def test_a_bad_input_name_is_refused_before_docker(
    name: str, broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    with bound_run(ORG), pytest.raises(sb.SandboxRefused):
        await broker.run_compute({"main.py": MAIN, name: b"x"}, timeout_s=5)
    assert docker.calls == []


def test_the_output_tar_keeps_only_flat_valid_regular_files() -> None:
    raw = tar_of({
        "ok.csv": b"1", "chart.png": b"\x89PNG", "d.json": b"{}", "n.txt": b"t",
        "evil.sh": b"x", "noext": b"x", "a.PY": b"x",
        "link.csv": ("sym", "/etc/passwd"), "hard.txt": ("hard", "ok.csv"),
        "sub/nested.csv": b"x", "../up.csv": b"x", "/abs.csv": b"x",
        "dots..csv": b"x", ".hidden.csv": b"x",
    })
    assert sb.parse_compute_tar(raw) == {
        "ok.csv": b"1", "chart.png": b"\x89PNG", "d.json": b"{}", "n.txt": b"t",
    }


def test_the_output_tar_caps_the_count_and_the_sizes() -> None:
    six = {f"f{i}.txt": b"x" for i in range(6)}
    assert len(sb.parse_compute_tar(tar_of(six))) == 5
    big = b"x" * (1024 * 1024 + 1)
    assert sb.parse_compute_tar(tar_of({"big.csv": big, "ok.csv": b"1"})) == {"ok.csv": b"1"}
    meg = b"x" * (1024 * 1024)
    four = {f"m{i}.csv": meg for i in range(4)}
    assert len(sb.parse_compute_tar(tar_of(four))) == 3  # 3 MB in all


def test_a_broken_output_tar_gives_no_file() -> None:
    assert sb.parse_compute_tar(b"not a tar" * 100) == {}
    assert sb.parse_compute_tar(b"") == {}


def test_the_parser_never_extracts(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("extract is forbidden")

    monkeypatch.setattr(tarfile.TarFile, "extractall", boom)
    monkeypatch.setattr(tarfile.TarFile, "extract", boom)
    assert sb.parse_compute_tar(tar_of({"a.txt": b"1"})) == {"a.txt": b"1"}
    import inspect

    assert "extractall" not in inspect.getsource(sb)


async def test_a_truncated_output_stream_gives_no_file(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    docker.tar_result = sb.CollectResult(0, docker.tar_out, b"", False, True)
    with bound_run(ORG):
        result = await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert result.files == {}


# ── the run: exit, timeout, cleanup ──────────────────────────────────────────


async def test_the_container_is_removed_after_a_run_and_never_reused(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    with bound_run(ORG, thread="t-same"):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    await broker.settle()
    assert len(docker.runs()) == 2
    assert docker.removals() == ["cid-1", "cid-2"]
    assert broker._compute_live == {}


async def test_a_nonzero_exit_is_a_result_and_stderr_is_raw(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    docker.python_result = sb.CollectResult(1, b"", b"Traceback\nboom\n", False, False)
    with bound_run(ORG):
        result = await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert result.exit_code == 1 and result.stderr == "Traceback\nboom\n"
    assert not result.timed_out


async def test_a_timeout_gives_no_exit_code_and_still_removes(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    docker.python_result = sb.CollectResult(None, b"part", b"", True, False)
    with bound_run(ORG):
        result = await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    await broker.settle()
    assert result.timed_out and result.exit_code is None and result.files == {}
    assert docker.removals() == ["cid-1"]


async def test_a_kill_by_timeout_is_a_timeout(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    docker.python_result = sb.CollectResult(137, b"", b"", False, False)
    now = [0.0]
    broker._clock = lambda: now[0]
    inner = docker.collect

    async def slow(args: Sequence[str], *, timeout: float, cap: int) -> sb.CollectResult:
        if "python" in args:
            now[0] = 9.0  # the script ran for 9 s, and the limit is 5 s
        return await inner(args, timeout=timeout, cap=cap)

    docker.collect = slow  # type: ignore[method-assign]
    with bound_run(ORG):
        result = await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert result.timed_out and result.exit_code is None


@pytest.mark.parametrize(("asked", "sent"), [(0, "1"), (-5, "1"), (30, "30"), (999, "60")])
async def test_the_timeout_is_clamped_to_1_to_60(
    asked: int, sent: str, broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    with bound_run(ORG):
        await broker.run_compute({"main.py": MAIN}, timeout_s=asked)
    (ran,) = docker.execs_of("python")
    assert ran[ran.index("-s") + 2] == sent


async def test_a_failed_start_leaves_no_container_and_no_slot(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    docker.fail_run = True
    with bound_run(ORG), pytest.raises(sb.SandboxUnavailable):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    await broker.settle()
    assert broker._compute_live == {}


async def test_a_failed_tar_in_removes_the_container(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    docker.put_rc = 2
    with bound_run(ORG), pytest.raises(sb.SandboxUnavailable):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    await broker.settle()
    assert docker.removals() == ["cid-1"]
    assert broker._compute_live == {}


async def test_a_daemon_that_is_gone_raises_unavailable(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    async def gone(*_a: Any, **_k: Any) -> Any:
        raise sb.SandboxUnavailable("Docker is not available on this box.")

    docker.run = gone  # type: ignore[method-assign]
    with bound_run(ORG), pytest.raises(sb.SandboxUnavailable):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert broker._compute_live == {}


async def test_a_cancel_removes_the_container(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    import asyncio

    gate = asyncio.Event()

    async def hang(args: Sequence[str], *, timeout: float, cap: int) -> sb.CollectResult:
        docker.calls.append(list(args))
        await gate.wait()
        return sb.CollectResult(0, b"", b"", False, False)

    docker.collect = hang  # type: ignore[method-assign]

    async def go() -> None:
        with bound_run(ORG):
            await broker.run_compute({"main.py": MAIN}, timeout_s=5)

    task = asyncio.get_running_loop().create_task(go())
    await until(lambda: bool(docker.execs_of("python")), "the script must start")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await broker.settle()
    assert docker.removals() == ["cid-1"] and broker._compute_live == {}


# ── the switch and the production guard ──────────────────────────────────────


async def test_a_run_is_refused_when_the_org_is_off(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    env["settings"].compute_run_orgs = OTHER
    with bound_run(ORG), pytest.raises(sb.SandboxRefused):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    env["settings"].compute_run_orgs = ""
    with bound_run(ORG), pytest.raises(sb.SandboxRefused):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert docker.calls == []


async def test_a_run_needs_the_scope_even_when_the_flag_is_on(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    env["settings"].compute_run_orgs = "*"
    env["settings"].maf_coding_scope = "code_task:*,app_builder:*,projects:*"
    with bound_run(ORG), pytest.raises(sb.SandboxRefused):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert docker.calls == []


async def test_production_refuses_without_a_remote_gvisor_backend(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    s = env["settings"]
    s.acb_env = "prod"
    # On main the two settings do not exist, so production fails closed.
    for attrs in ({}, {"sandbox_backend": "docker"}, {"sandbox_backend": "docker-remote"},
                  {"sandbox_runtime": "runsc"},
                  {"sandbox_backend": "docker", "sandbox_runtime": "runsc"},
                  {"sandbox_backend": "docker-remote", "sandbox_runtime": "runc"}):
        for key, value in attrs.items():
            setattr(s, key, value)
        with bound_run(ORG), pytest.raises(sb.SandboxRefused):
            await broker.run_compute({"main.py": MAIN}, timeout_s=5)
        for key in attrs:
            setattr(s, key, "")
    assert docker.calls == []


def _remote_runsc(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, *, runtime_arg: bool) -> None:
    """Production with the SBX-15 settings.

    The real ``build_run_argv`` adds ``--runtime=runsc`` since the merge with
    SBX-15. With *runtime_arg* False, a stub drops that flag, so the argv
    check of ``_compute_start`` still has a case to refuse.
    """
    s = env["settings"]
    s.acb_env = "prod"
    s.sandbox_backend = "docker-remote"
    s.sandbox_runtime = "runsc"
    if not runtime_arg:
        real = sb.build_run_argv

        def without_runtime(**kw: Any) -> list[str]:
            return [a for a in real(**kw) if a != "--runtime=runsc"]

        monkeypatch.setattr(sb, "build_run_argv", without_runtime)


async def test_production_refuses_an_argv_without_runsc_even_with_the_settings(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _remote_runsc(env, monkeypatch, runtime_arg=False)

    async def admit(org: str) -> None:
        return None

    monkeypatch.setattr(broker, "_remote_org_admit", admit, raising=False)
    with bound_run(ORG), pytest.raises(sb.SandboxRefused):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert docker.runs() == []


async def test_production_refuses_a_start_that_skips_the_org_lock(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SBX-F26: an admit that raises stops the start, and no container runs.

    Since the merge with SBX-15, ``_compute_start`` always awaits the admit
    in the start gate, so no start can skip it. The AST fence
    ``test_every_start_in_the_broker_passes_the_admit_under_the_gate``
    (``test_sandbox_remote_backend.py``) pins that.
    """
    _remote_runsc(env, monkeypatch, runtime_arg=True)

    async def refuse(org: str) -> None:
        raise sb.SandboxBusy("another org holds the box")

    monkeypatch.setattr(broker, "_remote_org_admit", refuse, raising=False)
    with bound_run(ORG), pytest.raises(sb.SandboxBusy):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert docker.runs() == []


async def test_production_runs_with_runsc_and_an_admit(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _remote_runsc(env, monkeypatch, runtime_arg=True)
    seen: list[str] = []

    async def admit(org: str) -> None:
        seen.append(org)

    monkeypatch.setattr(broker, "_remote_org_admit", admit, raising=False)
    with bound_run(ORG):
        result = await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert result.exit_code == 0 and seen == [ORG]
    assert "--runtime=runsc" in docker.runs()[0]


async def test_staging_counts_as_production(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    env["settings"].acb_env = "staging"
    with bound_run(ORG), pytest.raises(sb.SandboxRefused):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert docker.calls == []


# ── a switch-off during a run ────────────────────────────────────────────────


async def test_the_reaper_skips_a_starting_container_and_the_start_rechecks(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    import asyncio

    docker.block_run = asyncio.Event()

    async def go() -> None:
        with bound_run(ORG):
            await broker.run_compute({"main.py": MAIN}, timeout_s=5)

    task = asyncio.get_running_loop().create_task(go())
    await until(lambda: bool(docker.runs()), "the start must be in flight")
    env["settings"].compute_run_orgs = OTHER
    assert await broker.reap_once() == 0  # a starting container is not the reaper's
    assert len(broker._compute_live) == 1
    docker.block_run.set()
    with pytest.raises(sb.SandboxRefused):
        await task
    await broker.settle()
    assert docker.execs_of("python") == [] and docker.removals() == ["cid-1"]
    assert broker._compute_live == {}


async def test_a_dropped_container_runs_no_further_exec(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    original = docker.run

    async def flip(args: Sequence[str], *, timeout: float, stdin: bytes | None = None) -> Any:
        result = await original(args, timeout=timeout, stdin=stdin)
        if list(args)[:2] == ["exec", "-i"]:  # the tar-in just ended
            env["settings"].compute_run_orgs = OTHER
            assert await broker.reap_once() == 1
        return result

    docker.run = flip  # type: ignore[method-assign]
    with bound_run(ORG), pytest.raises(sb.SandboxRefused):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    await broker.settle()
    assert docker.execs_of("python") == [] and docker.execs_of("-c") == []
    assert "cid-1" in docker.removals() and broker._compute_live == {}


# ── no wildcard while anyone can sign up ─────────────────────────────────────


def test_a_wildcard_is_refused_while_self_serve_signup_is_on(env: dict[str, Any]) -> None:
    s = env["settings"]
    s.self_serve_signup_enabled = "true"
    s.maf_coding_scope = f"compute:{ORG}"
    s.compute_run_orgs = "*"
    assert not sb.compute_enabled(ORG)  # the flag wildcard
    s.maf_coding_scope = "compute:*"
    s.compute_run_orgs = ORG
    assert not sb.compute_enabled(ORG)  # the scope wildcard
    s.maf_coding_scope = f"compute:{ORG}"
    assert sb.compute_enabled(ORG)  # named org, named scope: allowed
    s.self_serve_signup_enabled = ""
    s.maf_coding_scope = "compute:*"
    s.compute_run_orgs = "*"
    assert sb.compute_enabled(ORG)


# ── the caps ─────────────────────────────────────────────────────────────────


async def _hold(broker: sb.SandboxBroker, docker: ComputeDocker, org: str, thread: str):
    import asyncio

    gate = asyncio.Event()
    started = asyncio.Event()

    async def hang(args: Sequence[str], *, timeout: float, cap: int) -> sb.CollectResult:
        docker.calls.append(list(args))
        if "python" in args:
            started.set()
            await gate.wait()
        return sb.CollectResult(0, b"", b"", False, False)

    docker.collect = hang  # type: ignore[method-assign]

    async def go() -> None:
        with bound_run(org, thread=thread):
            await broker.run_compute({"main.py": MAIN}, timeout_s=5)

    task = asyncio.get_running_loop().create_task(go())
    await started.wait()
    return task, gate


async def test_a_second_run_of_one_org_is_busy(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    task, gate = await _hold(broker, docker, ORG, "t-1")
    with bound_run(ORG, thread="t-2"), pytest.raises(sb.SandboxBusy):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    gate.set()
    await task
    with bound_run(ORG, thread="t-2"):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)


async def test_the_total_cap_is_busy(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    env["settings"].compute_run_orgs = "*"
    env["settings"].compute_max_per_org = 2  # so only the box cap can refuse
    task, gate = await _hold(broker, docker, ORG, "t-1")
    with bound_run(OTHER, thread="t-2"), pytest.raises(sb.SandboxBusy):
        await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    gate.set()
    await task


async def test_compute_and_projects_never_starve_each_other(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    env["settings"].maf_coding_scope = "code_task:*,compute:*"
    env["settings"].sandbox_max_per_org = 1
    env["settings"].sandbox_max_total = 1
    with bound_run(ORG, thread="t-code"):
        code = await broker.acquire()
    assert broker._live  # the one sandbox slot is taken
    with bound_run(ORG, thread="t-code"):
        result = await broker.run_compute({"main.py": MAIN}, timeout_s=5)
    assert result.exit_code == 0
    assert broker._live.get(code.name) is code and not code.removed
    # and a held compute run does not take the sandbox slot
    task, gate = await _hold(broker, docker, ORG, "t-c2")
    with bound_run(ORG, thread="t-code"):
        again = await broker.acquire()
    assert again is code
    gate.set()
    await task


# ── the reaper and the sweep ─────────────────────────────────────────────────


async def test_the_reaper_stops_a_compute_container_whose_org_left_the_flag(
    broker: sb.SandboxBroker, docker: ComputeDocker, env: dict[str, Any],
) -> None:
    task, gate = await _hold(broker, docker, ORG, "t-1")
    assert await broker.reap_once() == 0  # still on the flag
    assert len(broker._compute_live) == 1
    env["settings"].compute_run_orgs = OTHER
    assert await broker.reap_once() == 1
    assert broker._compute_live == {}
    assert "cid-1" in docker.removals()
    gate.set()
    with pytest.raises(sb.SandboxRefused):  # the next step sees the drop
        await task


async def test_the_reaper_stops_a_compute_container_past_the_longest_run(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    now = [0.0]
    broker._clock = lambda: now[0]
    task, gate = await _hold(broker, docker, ORG, "t-1")
    now[0] = 10_000.0
    assert await broker.reap_once() == 1
    gate.set()
    with pytest.raises(sb.SandboxRefused):  # the next step sees the drop
        await task


async def test_the_startup_sweep_clears_the_compute_registry(
    broker: sb.SandboxBroker, docker: ComputeDocker,
) -> None:
    task, gate = await _hold(broker, docker, ORG, "t-1")
    docker.ps_ids = ["cid-1"]
    await broker.sweep()
    assert broker._compute_live == {}
    gate.set()
    with pytest.raises(sb.SandboxRefused):  # the next step sees the drop
        await task


# ── the real container ───────────────────────────────────────────────────────

from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: E402,F401
from tests.unit.test_sandbox_exec_hygiene import (  # noqa: E402,F401 — fixtures by name
    DOCKER_ORG,
    bind_mounts_work,
    docker_image,
    real,
)

PANDAS_SCRIPT = b"""\
import json, os, socket
import pandas as pd
df = pd.read_csv('inputs/tr_0123abcd.txt')
df['c'] = df['a'] + df['b']
df.to_csv('out/sum.csv', index=False)
try:
    socket.create_connection(('1.1.1.1', 53), timeout=2)
    net = 'open'
except OSError:
    net = 'closed'
with open('out/probe.json', 'w') as fh:
    json.dump({'net': net, 'rows': len(df), 'secrets': [k for k in os.environ
               if k != 'GPG_KEY' and ('KEY' in k or 'TOKEN' in k or 'SECRET' in k)]}, fh)
print(df['c'].sum())
"""


@pytest.mark.sandbox_docker
async def test_docker_a_pandas_script_runs_offline_through_run_compute(
    real: dict[str, Any], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    import json

    s = real["settings"]
    monkeypatch.setattr(s, "compute_run_orgs", DOCKER_ORG)
    monkeypatch.setattr(s, "maf_coding_scope", f"compute:{DOCKER_ORG}")
    monkeypatch.setattr(s, "acb_env", "dev")
    monkeypatch.setattr(sb, "_BROKER", real["broker"])
    with bound_run(DOCKER_ORG, thread="t-compute-docker"):
        result = await sb.run_compute(
            {"main.py": PANDAS_SCRIPT, "inputs/tr_0123abcd.txt": b"a,b\n1,2\n3,4\n"},
            timeout_s=50,
        )
    await real["broker"].settle()
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "10"
    assert result.files["sum.csv"].decode().splitlines() == ["a,b,c", "1,2,3", "3,4,7"]
    probe = json.loads(result.files["probe.json"])
    assert probe == {"net": "closed", "rows": 2, "secrets": []}
    assert real["broker"]._compute_live == {}
