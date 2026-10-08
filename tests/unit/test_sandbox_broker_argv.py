"""WS43-F2 — the ``docker run`` arguments of a sandbox container.

Spec ``project-docs/specs/maf_coding_engine.md`` §7.1 rules 3, 5, 6 and 7, and
§10 WS43-F2.

What breaks this fence: the arguments lose a flag of rule 6, gain a ``-p``,
``--privileged``, ``--cap-add`` or a socket mount, mount a read-only source
with ``.git``, leave a workspace ``.git`` uncovered, put ``.local`` first on
``PATH``, or use uid 0. Also a start and a restart that build different
mounts, because one function builds the mounts for every start.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import (
    PINNED_IMAGE,
    FakeDocker,
    bound_run,
    configure_env,
    flag_values,
    mounts_of,
)

ORG = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    return configure_env(monkeypatch, tmp_path)


@pytest.fixture
def docker() -> FakeDocker:
    return FakeDocker()


@pytest.fixture
def broker(docker: FakeDocker, env: dict[str, Any]) -> sb.SandboxBroker:
    return sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]


async def _start(broker: sb.SandboxBroker, docker: FakeDocker, **bind: Any) -> list[str]:
    with bound_run(ORG, **bind):
        await broker.acquire()
    return docker.runs()[-1]


def _pairs(argv: list[str]) -> set[tuple[str, str]]:
    return {(argv[i], argv[i + 1]) for i in range(len(argv) - 1)}


# ── rule 6: every flag ───────────────────────────────────────────────────────


async def test_the_run_carries_every_flag_of_rule_6(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    argv = await _start(broker, docker)
    pairs = _pairs(argv)
    for pair in [
        ("--network", "none"),
        ("--tmpfs", "/tmp:rw,nosuid,nodev,size=256m"),
        ("--user", "1000:1000"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--cpus", "1"),
        ("--memory", "1g"),
        ("--memory-swap", "1g"),
        ("--pids-limit", "256"),
        ("--workdir", "/workspace"),
    ]:
        assert pair in pairs, f"missing {pair}"
    assert "--read-only" in argv
    assert "--init" in argv
    assert argv[:2] == ["run", "-d"]
    assert argv[-3:] == [PINNED_IMAGE, "sleep", "infinity"]


async def test_the_run_gains_no_dangerous_flag(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    argv = await _start(broker, docker)
    for flag in (
        "-p", "--publish", "-P", "--publish-all", "--privileged", "--cap-add",
        "-v", "--volume", "--volumes-from", "--device", "--pid", "--ipc",
        "--userns", "--uts", "--cgroupns", "--add-host", "--env-file", "-e",
    ):
        assert flag not in argv, f"{flag} must never reach docker run"
    joined = " ".join(argv)
    assert "docker.sock" not in joined
    assert "seccomp=unconfined" not in joined
    assert "apparmor=unconfined" not in joined
    assert len(flag_values(argv, "--network")) == 1


async def test_the_limits_come_from_settings(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = env["settings"]
    monkeypatch.setattr(s, "sandbox_cpus", "2")
    monkeypatch.setattr(s, "sandbox_memory", "512m")
    monkeypatch.setattr(s, "sandbox_pids_limit", 64)
    monkeypatch.setattr(s, "sandbox_tmpfs_mb", 32)
    pairs = _pairs(await _start(broker, docker))
    assert {("--cpus", "2"), ("--memory", "512m"), ("--memory-swap", "512m"),
            ("--pids-limit", "64"), ("--tmpfs", "/tmp:rw,nosuid,nodev,size=32m")} <= pairs


def test_the_settings_defaults_match_rule_6() -> None:
    from acb_common.settings import Settings

    f = Settings.model_fields
    assert f["maf_coding_scope"].default == ""
    assert f["sandbox_image"].default == ""
    assert f["sandbox_cpus"].default == "1"
    assert f["sandbox_memory"].default == "1g"
    assert f["sandbox_pids_limit"].default == 256
    assert f["sandbox_tmpfs_mb"].default == 256
    assert f["sandbox_output_cap_bytes"].default == 12288
    assert f["sandbox_max_per_org"].default == 2
    assert f["sandbox_max_total"].default == 4
    assert f["sandbox_min_free_disk_mb"].default == 5120
    assert f["sandbox_workspace_quota_mb"].default == 2048
    assert f["sandbox_idle_ttl_seconds"].default == 600
    assert f["sandbox_max_lifetime_seconds"].default == 7200


@pytest.mark.parametrize("cpus,memory", [("1;rm", "1g"), ("1", "1g --privileged")])
async def test_a_bad_limit_setting_is_refused(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, cpus: str, memory: str,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_cpus", cpus)
    monkeypatch.setattr(env["settings"], "sandbox_memory", memory)
    with bound_run(ORG), pytest.raises(sb.SandboxRefused):
        await broker.acquire()
    assert not docker.runs()


async def test_read_only_always_comes_with_the_tmp_tmpfs(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The image sets HOME=/tmp, so a read-only root needs the /tmp tmpfs.

    WS-43b's verifier: matplotlib fails with no writable home. The pair must
    hold on a first start and on a restart, and a size of 0 is refused.
    """
    with bound_run(ORG, thread="t-tmpfs"):
        handle = await broker.acquire()
        docker.stream_results.append(
            sb.StreamResult(1, "", 0, False, "Error response from daemon: is not running", False)
        )
        assert (await broker.exec(handle, "true", 5)).restarted
    runs = docker.runs()
    assert len(runs) == 2
    for argv in runs:
        assert "--read-only" in argv
        tmpfs = flag_values(argv, "--tmpfs")
        assert len(tmpfs) == 1, tmpfs
        assert re.fullmatch(r"/tmp:rw,nosuid,nodev,size=[1-9][0-9]*m", tmpfs[0]), tmpfs
    for size in (0, -1):
        monkeypatch.setattr(env["settings"], "sandbox_tmpfs_mb", size)
        with bound_run(ORG, thread=f"t-tmpfs-{size}"), pytest.raises(sb.SandboxRefused):
            await broker.acquire()
    assert len(docker.runs()) == 2


# ── rule 6: the uid, the environment and PATH ───────────────────────────────


@pytest.mark.parametrize("ids", [(0, 1000), (1000, 0), (0, 0)])
async def test_uid_0_is_refused(
    broker: sb.SandboxBroker, docker: FakeDocker, monkeypatch: pytest.MonkeyPatch,
    ids: tuple[int, int],
) -> None:
    monkeypatch.setattr(sb, "_process_ids", lambda: ids)
    with bound_run(ORG), pytest.raises(sb.SandboxRefused, match="uid 0"):
        await broker.acquire()
    assert not docker.runs()


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX only: Windows has no uid")
def test_the_uid_is_the_gateway_process_uid() -> None:
    assert sb._process_ids() == (os.getuid(), os.getgid())


async def test_the_environment_holds_no_secret_and_local_goes_last(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    argv = await _start(broker, docker, thread="thread-env")
    env = dict(v.split("=", 1) for v in flag_values(argv, "--env"))
    th = sb._digest("thread-env")
    assert env == {
        "HOME": "/tmp",
        "PIP_USER": "1",
        "PIP_CACHE_DIR": "/tmp/pip-cache",
        "PYTHONUSERBASE": f"/workspace/.local/{th}",
        "PATH": f"{sb.SYSTEM_PATH}:/workspace/.local/{th}/bin",
    }
    parts = env["PATH"].split(":")
    assert parts[-1] == f"/workspace/.local/{th}/bin"
    assert all(".local" not in p for p in parts[:-1]), ".local must be LAST on PATH"


# ── rule 3: name and labels ──────────────────────────────────────────────────


async def test_the_name_and_the_labels(broker: sb.SandboxBroker, docker: FakeDocker) -> None:
    argv = await _start(broker, docker, agent="agent-x", thread="t-9")
    name = flag_values(argv, "--name")[0]
    import hashlib

    want = hashlib.sha256(f"{ORG}\x00agent-x\x00t-9".encode()).hexdigest()[:16]
    assert name == f"mtr-sbx-{want}"
    labels = dict(v.split("=", 1) for v in flag_values(argv, "--label"))
    start = labels.pop("metorite.start")
    assert labels == {
        "metorite.sandbox": "1",
        "metorite.org": ORG,
        "metorite.agent": "agent-x",
        "metorite.thread": sb._digest("t-9"),
    }
    assert len(start) == 32 and int(start, 16) >= 0, "each start gets a fresh id"


# ── rule 5: the mounts ───────────────────────────────────────────────────────


async def test_one_read_write_mount_the_tenant_dir(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    with bound_run(ORG) as ws:
        await broker.acquire()
    mounts = mounts_of(docker.runs()[-1])
    assert mounts == [f"type=bind,source={ws.resolve()},target=/workspace"]


async def test_a_root_git_dir_is_covered_by_an_empty_read_only_mount(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    with bound_run(ORG) as ws:
        (ws / ".git" / "hooks").mkdir(parents=True)
        (ws / ".git" / "config").write_text("[remote]\n url = https://x-token:secret@h\n")
        await broker.acquire()
    mounts = mounts_of(docker.runs()[-1])
    cover = broker.git_cover()
    assert mounts[1] == f"type=bind,source={cover.empty_dir},target=/workspace/.git,readonly"
    assert not any(cover.empty_dir.iterdir()), "the cover must be empty"
    assert not cover.empty_dir.is_relative_to(ws.resolve())


async def test_a_root_gitfile_is_covered_by_an_empty_read_only_file(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    with bound_run(ORG) as ws:
        (ws / ".git").write_text("gitdir: /somewhere/else\n")
        await broker.acquire()
    cover = broker.git_cover()
    assert mounts_of(docker.runs()[-1])[1] == (
        f"type=bind,source={cover.empty_file},target=/workspace/.git,readonly"
    )
    assert cover.empty_file.stat().st_size == 0


@pytest.mark.parametrize("kind", ["dir", "gitfile"])
async def test_a_git_below_the_root_is_refused(
    broker: sb.SandboxBroker, docker: FakeDocker, kind: str,
) -> None:
    with bound_run(ORG) as ws:
        sub = ws / "agent-data" / "repo"
        sub.mkdir(parents=True)
        if kind == "dir":
            (sub / ".git").mkdir()
        else:
            (sub / ".git").write_text("gitdir: /etc\n")
        with pytest.raises(sb.SandboxRefused, match="below its root"):
            await broker.acquire()
    assert not docker.runs()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
async def test_a_root_git_link_is_refused(broker: sb.SandboxBroker, docker: FakeDocker) -> None:
    with bound_run(ORG) as ws:
        (ws / ".git").symlink_to(ws.parent)
        with pytest.raises(sb.SandboxRefused, match="link"):
            await broker.acquire()
    assert not docker.runs()


def _binding(ws: Path) -> sb.RunBinding:
    return sb.RunBinding(org=ORG, agent="agent-x", thread="t", instance="o:x", workspace=ws)


@pytest.mark.parametrize("depth", [0, 1, 3])
def test_a_read_only_source_with_git_at_any_depth_is_refused(
    tmp_path: Path, depth: int,
) -> None:
    ws = (tmp_path / "ws").resolve()
    ws.mkdir()
    ro = (tmp_path / "ro").resolve()
    where = ro.joinpath(*["d"] * depth)
    where.mkdir(parents=True)
    (where / ".git").mkdir()
    cover = sb.GitCover(empty_dir=tmp_path / "e", empty_file=tmp_path / "f")
    with pytest.raises(sb.SandboxRefused, match=r"\.git"):
        sb.mount_list(_binding(ws), cover, readonly_mounts=[(ro, "/opt/build")])


def test_a_clean_read_only_source_mounts_read_only_outside_workspace(tmp_path: Path) -> None:
    ws = (tmp_path / "ws").resolve()
    ws.mkdir()
    ro = (tmp_path / "ro").resolve()
    ro.mkdir()
    cover = sb.GitCover(empty_dir=tmp_path / "e", empty_file=tmp_path / "f")
    mounts = sb.mount_list(_binding(ws), cover, readonly_mounts=[(ro, "/opt/build")])
    assert mounts[-1] == sb.Mount(ro, "/opt/build", readonly=True)
    with pytest.raises(sb.SandboxRefused):
        sb.mount_list(_binding(ws), cover, readonly_mounts=[(ro, "/workspace/x")])


def test_a_socket_source_is_refused(tmp_path: Path) -> None:
    sock = (tmp_path / "docker.sock").resolve()
    sock.mkdir()
    cover = sb.GitCover(empty_dir=tmp_path / "e", empty_file=tmp_path / "f")
    with pytest.raises(sb.SandboxRefused, match="Docker socket"):
        sb.mount_list(_binding((tmp_path).resolve()), cover, readonly_mounts=[(sock, "/s")])


async def test_a_start_and_a_restart_build_the_same_mounts(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    """One function builds the mounts, so a restart keeps the .git cover."""
    with bound_run(ORG) as ws:
        (ws / ".git").mkdir()
        handle = await broker.acquire()
        docker.stream_results.append(
            sb.StreamResult(1, "", 0, False, "Error response from daemon: is not running", False)
        )
        result = await broker.exec(handle, "true", 5)
    assert result.restarted
    first, second = docker.runs()
    assert mounts_of(first) == mounts_of(second)
    assert any(m.endswith("target=/workspace/.git,readonly") for m in mounts_of(second))
    def without_start(argv: list[str]) -> list[str]:
        return [a for a in argv if not a.startswith("metorite.start=")]

    assert without_start(first) == without_start(second), (
        "a restart must re-apply every flag and mount"
    )
    assert flag_values(first, "--label") != flag_values(second, "--label"), (
        "a restart must get a fresh start id"
    )


# ── §7.2: the image is pinned ────────────────────────────────────────────────


@pytest.mark.parametrize("image", [
    "metorite/coding-sandbox:latest",
    "metorite/coding-sandbox",
    "python:3.12-slim",
    "x@sha256:short",
])
async def test_a_mutable_image_reference_is_refused(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, image: str,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_image", image)
    with bound_run(ORG), pytest.raises(sb.SandboxRefused, match="pinned"):
        await broker.acquire()
    assert not docker.runs()


@pytest.mark.parametrize("image", [
    "python:3.12-slim@sha256:" + "f" * 64,
    "registry.example/coding-sandbox@sha256:" + "0" * 64,
    "sha256:" + "1" * 64,
])
def test_an_immutable_image_reference_is_accepted(image: str) -> None:
    class S:
        sandbox_image = image

    assert sb.pinned_image(S()) == image


async def test_no_image_starts_nothing(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_image", "")
    with bound_run(ORG), pytest.raises(sb.SandboxUnavailable):
        await broker.acquire()
    assert not docker.runs()
