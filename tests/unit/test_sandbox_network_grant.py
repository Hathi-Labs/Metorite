"""WS43-F9 — the egress grant of the sandbox broker (WS-43g1, task 4009).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.3 ("The grant", "The host
firewall", "The proxy"), §7.1 rule 5, done-when 3, 7 and 9 of WS-43g, and
§10 WS43-F9.

What breaks this fence: a grant opens the network while egress is off, a
grant moves the container to a network that is not its own organization's,
the run's end leaves a grant, the empty read-only cover on
``/workspace/.git`` is missing after a grant or after a revoke, the sweep
leaves the proxy, a network or a grant, ``docker-remote`` takes a grant, or
the host firewall script stops printing its rule.

Two halves. The first half drives the broker with a fake Docker, and runs in
the default unit job. The second half carries the ``sandbox_docker`` marker.
It runs a real proxy, a real door stub and a real sandbox container, and
shows that a granted run reaches the door and ``pypi.org``, and that the
proxy refuses any other host with a log line. Its containers carry the
``ws43g1-`` prefix, and its teardown removes only what it made.

Out of this slice (WS-43g2, WS-43g3): a run with no chat, the approval card,
and the firewall rule applied in CI (done-when 4 to 6 and 8).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import uuid
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
)
from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: F401

ORG = "99999999-0000-4000-8000-0000000000a1"
_REAL_PROCESS_IDS = sb._process_ids
ORG_B = "99999999-0000-4000-8000-0000000000b2"
MEMBER = "member@example.com"
_REPO = Path(__file__).resolve().parents[2]
_FIREWALL = _REPO / "apps" / "services" / "orchestrator" / "sandbox" / "host_firewall.sh"
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
#: What each ``--env-file`` held when ``docker run`` read it: path to
#: (mode, the variables). The broker removes the file right after.
_ENV_FILES: dict[str, tuple[int, dict[str, str]]] = {}


class EgressDocker(FakeDocker):
    """FakeDocker, plus the network calls and the proxy of the egress path."""

    def __init__(self) -> None:
        super().__init__()
        self.networks: dict[str, dict[str, Any]] = {}
        self.proxy_running = False
        self.proxy_runs: list[list[str]] = []

    async def run(self, args: Sequence[str], *, timeout: float) -> sb.DockerResult:
        argv = list(args)
        if argv[0] == "network":
            self.calls.append(argv)
            return self._network(argv)
        if argv[:2] == ["container", "inspect"]:
            self.calls.append(argv)
            if self.proxy_running:
                return sb.DockerResult(0, "true|proxy\n", "")
            return sb.DockerResult(1, "", "Error: No such container: mtr-egress-proxy")
        if argv[0] == "run" and "--env-file" in argv:
            path = Path(flag_values(argv, "--env-file")[0])
            lines = path.read_text(encoding="utf-8").splitlines()
            _ENV_FILES[str(path)] = (
                path.stat().st_mode & 0o777, dict(x.split("=", 1) for x in lines),
            )
        if argv[0] == "run" and sb.EGRESS_PROXY_NAME in argv:
            self.calls.append(argv)
            self.proxy_runs.append(argv)
            self.proxy_running = True
            return sb.DockerResult(0, "proxy-id\n", "")
        if argv[:2] == ["rm", "-f"] and sb.EGRESS_PROXY_NAME in argv:
            self.proxy_running = False
        return await super().run(args, timeout=timeout)

    def _network(self, argv: list[str]) -> sb.DockerResult:
        sub = argv[1]
        if sub == "inspect":
            net = self.networks.get(argv[-1])
            if net is None:
                return sb.DockerResult(1, "", f"Error: network {argv[-1]} not found")
            return sb.DockerResult(0, f"{str(net['internal']).lower()}|{net['org']}\n", "")
        if sub == "create":
            labels = dict(v.split("=", 1) for v in flag_values(argv, "--label"))
            self.networks[argv[-1]] = {
                "internal": "--internal" in argv, "org": labels.get(sb.LABEL_ORG, ""),
                "argv": argv,
            }
        elif sub == "rm":
            for name in argv[2:]:
                self.networks.pop(name, None)
        elif sub == "ls":
            return sb.DockerResult(0, "".join(f"{n}\n" for n in self.networks), "")
        return sb.DockerResult(0, "", "")

    def network_calls(self, sub: str) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["network", sub]]

    def sandbox_runs(self) -> list[list[str]]:
        return [c for c in self.runs() if sb.EGRESS_PROXY_NAME not in c]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    return configure_env(monkeypatch, tmp_path)


@pytest.fixture
def egress_on(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(env["settings"], "sandbox_egress_enabled", True)
    return env


@pytest.fixture
def docker() -> EgressDocker:
    return EgressDocker()


class Wall:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def wall() -> Wall:
    return Wall()


@pytest.fixture
def broker(docker: EgressDocker, env: dict[str, Any], wall: Wall) -> sb.SandboxBroker:
    return sb.SandboxBroker(docker=docker, wall_clock=wall)  # type: ignore[arg-type]


def _env_of(argv: list[str]) -> dict[str, str]:
    """The container env: the ``--env`` flags, and the ``--env-file`` as read."""
    env = dict(v.split("=", 1) for v in flag_values(argv, "--env"))
    for path in flag_values(argv, "--env-file"):
        env.update(_ENV_FILES[path][1])
    return env


def _grants_file(broker: sb.SandboxBroker) -> dict[str, Any]:
    return json.loads(broker._grants_path().read_text(encoding="utf-8"))


def _with_run_id(run_id: str = "run-7") -> None:
    from acb_skills.write_artifact import derive_artifact_context

    derive_artifact_context(run_id=run_id)


# ── done-when 3: dark by default ─────────────────────────────────────────────


def test_the_settings_defaults_keep_egress_off() -> None:
    from acb_common.settings import Settings

    fields = Settings.model_fields
    assert fields["sandbox_egress_enabled"].default is False
    assert fields["sandbox_egress_allow_hosts"].default == "pypi.org,files.pythonhosted.org"
    assert fields["sandbox_egress_door_upstream"].default == ""
    assert fields["sandbox_egress_grant_ttl_seconds"].default == 1800


@pytest.mark.parametrize("value", [False, None, "true", 1, "1"])
def test_only_a_real_true_turns_egress_on(value: Any) -> None:
    class S:
        sandbox_egress_enabled = value

    assert sb.egress_enabled(S()) is False


async def test_with_egress_off_no_proxy_and_no_egress_network_exists(
    broker: sb.SandboxBroker, docker: EgressDocker, env: dict[str, Any],
) -> None:
    """Done-when 3: a whole run, a refused grant, the run's end and a sweep."""
    with bound_run(ORG, member=MEMBER) as ws:
        (ws / ".git").mkdir()
        handle = await broker.acquire()
        await broker.exec(handle, "true", 5)
        with pytest.raises(sb.SandboxRefused, match="off on this platform"):
            await broker.grant_egress(handle, "pip install", ["pypi.org"])
        await broker.end_run()
    await broker.sweep()
    assert docker.proxy_runs == []
    assert not any(c[:2] in (["network", "create"], ["network", "connect"]) for c in docker.calls)
    assert not any(sb.EGRESS_PROXY_NAME in c for c in docker.calls)
    assert all(flag_values(r, "--network") == ["none"] for r in docker.runs())
    assert not (broker.state_dir() / "egress").exists(), "no grants file while egress is off"


# ── the grant (§7.3, "The grant") ────────────────────────────────────────────


async def test_a_grant_recreates_the_container_on_its_own_org_network(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any], wall: Wall,
) -> None:
    with bound_run(ORG, member=MEMBER) as ws:
        _with_run_id()
        (ws / ".git").mkdir()
        handle = await broker.acquire()
        grant = await broker.grant_egress(handle, "pip install", ["pypi.org"])
    first, second = docker.sandbox_runs()
    network = sb.egress_network_name(ORG)
    assert flag_values(first, "--network") == ["none"]
    assert flag_values(second, "--network") == [network]
    assert handle.network == network and handle.egress is grant
    # Every mount of §7.1 rule 5 comes back, the .git cover included.
    assert mounts_of(first) == mounts_of(second)
    assert any(m.endswith("target=/workspace/.git,readonly") for m in mounts_of(second))
    url = f"http://grant:{grant.token}@{sb.EGRESS_PROXY_NAME}:{sb.EGRESS_PROXY_PORT}"
    assert {k: _env_of(second)[k] for k in _PROXY_VARS} == dict.fromkeys(_PROXY_VARS, url)
    assert not set(_PROXY_VARS) & set(_env_of(first))
    # Review round 1: the token never reaches the argv. The env file is 0600
    # in a 0700 folder, and it is gone once the container started.
    assert grant.token not in " ".join(second)
    (env_file,) = flag_values(second, "--env-file")
    assert _ENV_FILES[env_file][0] == 0o600
    assert oct(Path(env_file).parent.stat().st_mode & 0o777) == "0o700"
    assert not Path(env_file).exists()
    assert Path(env_file).parent.is_relative_to(broker.state_dir())
    # The grant: the run binding, never input (R5).
    assert (grant.org, grant.run, grant.agent, grant.approver) == (
        ORG, "run-7", "agent-x", MEMBER,
    )
    assert grant.hosts == ("pypi.org",)
    assert grant.expires_at == wall.now + 1800
    assert grant.token not in repr(grant)
    # The file holds the hash of the token, never the token.
    doc = _grants_file(broker)
    assert list(doc["grants"]) == [grant.key]
    assert doc["grants"][grant.key]["approver"] == MEMBER
    assert grant.token not in json.dumps(doc)
    # The network: internal, labelled with the org, on an mtr-eg- bridge.
    made = docker.networks[network]["argv"]
    assert "--internal" in made
    assert f"{sb.LABEL_ORG}={ORG}" in flag_values(made, "--label")
    assert flag_values(made, "--opt") == [
        f"com.docker.network.bridge.name={sb.egress_bridge_name(ORG)}"
    ]
    assert ["network", "connect", network, sb.EGRESS_PROXY_NAME] in docker.calls


async def test_the_proxy_runs_as_nobody_with_no_capability_and_no_port(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        await broker.grant_egress(handle, "pip", ["pypi.org"])
    (argv,) = docker.proxy_runs
    pairs = {(argv[i], argv[i + 1]) for i in range(len(argv) - 1)}
    for pair in [
        ("--user", "65534:65534"), ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"), ("--memory", "128m"),
        ("--memory-swap", "128m"), ("--pids-limit", "64"), ("--network", "bridge"),
        ("--name", sb.EGRESS_PROXY_NAME),
    ]:
        assert pair in pairs, pair
    assert "--read-only" in argv
    for flag in (
        "-p", "--publish", "-P", "--publish-all", "--privileged", "--cap-add", "-v",
        "--volume", "--env-file", "-e", "--pid", "--ipc", "--userns", "--device",
    ):
        assert flag not in argv, flag
    assert "docker.sock" not in " ".join(argv)
    mounts = mounts_of(argv)
    assert len(mounts) == 2 and all(m.endswith(",readonly") for m in mounts)
    assert {m.split("target=")[1].split(",")[0] for m in mounts} == {
        sb.EGRESS_BIN_TARGET, sb.EGRESS_GRANTS_TARGET,
    }
    assert _env_of(argv) == {"PYTHONDONTWRITEBYTECODE": "1"}, "the proxy holds no credential"
    bin_dir = Path(mounts[0].split("source=")[1].split(",")[0])
    assert sorted(p.name for p in bin_dir.iterdir()) == ["egress_proxy.py"]
    assert (bin_dir / "egress_proxy.py").read_bytes() == sb.egress_proxy_source().read_bytes()
    assert oct((broker.state_dir() / "egress").stat().st_mode & 0o777) == "0o700"


async def test_a_second_grant_reuses_the_proxy_and_the_network(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        first = await broker.grant_egress(handle, "pip", ["pypi.org"])
        second = await broker.grant_egress(handle, "pip", ["files.pythonhosted.org"])
    assert len(docker.proxy_runs) == 1
    assert len(docker.network_calls("create")) == 1
    assert list(_grants_file(broker)["grants"]) == [second.key], "the old token is dead"
    assert first.token != second.token


async def test_two_orgs_never_share_an_egress_network(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER, thread="ta"):
        a = await broker.acquire()
        await broker.grant_egress(a, "pip", ["pypi.org"])
    with bound_run(ORG_B, member=MEMBER, thread="tb"):
        b = await broker.acquire()
        await broker.grant_egress(b, "pip", ["pypi.org"])
    assert a.network != b.network
    assert a.network == sb.egress_network_name(ORG) and b.network == sb.egress_network_name(ORG_B)
    assert {n["org"] for n in docker.networks.values()} == {ORG, ORG_B}
    assert len(sb.egress_bridge_name(ORG)) <= 15


async def test_a_network_of_another_org_or_not_internal_is_refused(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    name = sb.egress_network_name(ORG)
    for planted in ({"internal": True, "org": ORG_B}, {"internal": False, "org": ORG}):
        docker.networks[name] = planted
        with bound_run(ORG, member=MEMBER):
            handle = await broker.acquire()
            with pytest.raises(sb.SandboxRefused, match="not its own internal"):
                await broker.grant_egress(handle, "pip", ["pypi.org"])
        assert handle.egress is None and not broker._grants


# ── the refusals of a grant ──────────────────────────────────────────────────


@pytest.mark.parametrize("hosts", [
    ["upload.pypi.org"], ["evil.com"], ["pypi.org", "example.com"], [], "pypi.org",
    [sb.DOOR_HOST],
])
async def test_a_host_off_the_allowlist_opens_nothing(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any], hosts: Any,
) -> None:
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        with pytest.raises(sb.SandboxRefused):
            await broker.grant_egress(handle, "pip", hosts)
    assert docker.network_calls("create") == [] and docker.proxy_runs == []
    assert len(docker.runs()) == 1 and handle.egress is None


@pytest.mark.parametrize("upstream", ["gateway.internal:8000", "10.0.0.5", "10.0.0.5:0", ""])
async def test_the_door_needs_an_upstream_address(
    broker: sb.SandboxBroker, egress_on: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    upstream: str,
) -> None:
    monkeypatch.setattr(egress_on["settings"], "sandbox_egress_door_upstream", upstream)
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        with pytest.raises(sb.SandboxRefused, match="no upstream address"):
            await broker.grant_egress(handle, "door", [sb.DOOR_HOST])


async def test_the_door_with_an_upstream_address_is_granted(
    broker: sb.SandboxBroker, egress_on: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress_on["settings"], "sandbox_egress_door_upstream", "10.0.0.5:8000")
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        grant = await broker.grant_egress(handle, "door", [sb.DOOR_HOST, "pypi.org"])
    doc = _grants_file(broker)
    assert doc["door_upstream"] == "10.0.0.5:8000"
    assert doc["grants"][grant.key]["hosts"] == [sb.DOOR_HOST, "pypi.org"]


async def test_docker_remote_takes_no_grant(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        monkeypatch.setattr(egress_on["settings"], "sandbox_backend", "docker-remote")
        with pytest.raises(sb.SandboxRefused, match="docker-remote"):
            await broker.grant_egress(handle, "pip", ["pypi.org"])
    assert docker.network_calls("create") == [] and docker.proxy_runs == []


@pytest.mark.parametrize("agent", [sb.PROJECTS_AGENT, sb.COMPUTE_AGENT])
async def test_projects_and_compute_never_get_egress(
    broker: sb.SandboxBroker, egress_on: dict[str, Any], tmp_path: Path, agent: str,
) -> None:
    binding = sb.RunBinding(org=ORG, agent=agent, thread="t", instance="", workspace=tmp_path)
    handle = sb.SandboxHandle(binding=binding, lock=asyncio.Lock())
    with pytest.raises(sb.SandboxRefused, match="never gets network access"):
        await broker.grant_egress(handle, "pip", ["pypi.org"])


async def test_a_run_with_no_member_cannot_approve(
    broker: sb.SandboxBroker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=""):
        handle = await broker.acquire()
        with pytest.raises(sb.SandboxRefused, match="No member"):
            await broker.grant_egress(handle, "pip", ["pypi.org"])


async def test_a_grant_for_the_container_of_another_run_is_refused(
    broker: sb.SandboxBroker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER, thread="thread-1"):
        handle = await broker.acquire()
    with bound_run(ORG, member=MEMBER, thread="thread-2"), pytest.raises(
        sb.SandboxRefused, match="not the bound run"
    ):
        await broker.grant_egress(handle, "pip", ["pypi.org"])
    with bound_run(ORG_B, member=MEMBER, thread="thread-1"), pytest.raises(sb.SandboxRefused):
        await broker.grant_egress(handle, "pip", ["pypi.org"])
    assert handle.egress is None


async def test_a_failed_recreate_ends_the_grant(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        docker.fail_run = True
        with pytest.raises(sb.SandboxUnavailable):
            await broker.grant_egress(handle, "pip", ["pypi.org"])
    assert handle.egress is None and not broker._grants
    assert _grants_file(broker)["grants"] == {}


# ── done-when 7: the revoke and the run's end ────────────────────────────────


def _assert_back_on_none(docker: EgressDocker, granted: list[str]) -> None:
    last = docker.sandbox_runs()[-1]
    assert flag_values(last, "--network") == ["none"]
    assert not set(_PROXY_VARS) & set(_env_of(last))
    assert mounts_of(last) == mounts_of(granted)
    assert any(m.endswith("target=/workspace/.git,readonly") for m in mounts_of(last))


async def test_a_revoke_puts_the_container_back_on_none_with_every_mount(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER) as ws:
        (ws / ".git").mkdir()
        handle = await broker.acquire()
        await broker.grant_egress(handle, "pip", ["pypi.org"])
        granted = docker.sandbox_runs()[-1]
        assert await broker.revoke_egress(handle) is True
        assert await broker.revoke_egress(handle) is False
    _assert_back_on_none(docker, granted)
    assert handle.egress is None and handle.network == "none"
    assert _grants_file(broker)["grants"] == {}
    assert sb.egress_network_name(ORG) not in docker.networks, "the org's network goes too"


async def test_the_runs_end_leaves_no_grant(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER) as ws:
        (ws / ".git").mkdir()
        handle = await broker.acquire()
        await broker.grant_egress(handle, "pip", ["pypi.org"])
        granted = docker.sandbox_runs()[-1]
        await broker.release(handle)
        assert await broker.end_run() is True
    _assert_back_on_none(docker, granted)
    assert not broker._grants and _grants_file(broker)["grants"] == {}
    assert docker.networks == {}


async def test_the_runs_end_of_another_thread_keeps_the_grant(
    broker: sb.SandboxBroker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER, thread="thread-1"):
        handle = await broker.acquire()
        await broker.grant_egress(handle, "pip", ["pypi.org"])
    with bound_run(ORG, member=MEMBER, thread="thread-2"):
        await broker.end_run()
    assert handle.egress is not None


async def test_an_expired_grant_ends_at_the_next_exec(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any], wall: Wall,
) -> None:
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        await broker.grant_egress(handle, "pip", ["pypi.org"])
        wall.now += 1801
        result = await broker.exec(handle, "true", 5)
    assert result.exit_code == 0
    assert handle.egress is None
    assert flag_values(docker.sandbox_runs()[-1], "--network") == ["none"]
    assert _grants_file(broker)["grants"] == {}


async def test_a_restart_keeps_the_grant_and_the_cover(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    """§7.1 rule 11: a restart after an OCI error keeps the network state."""
    with bound_run(ORG, member=MEMBER) as ws:
        (ws / ".git").mkdir()
        handle = await broker.acquire()
        await broker.grant_egress(handle, "pip", ["pypi.org"])
        docker.stream_results.append(
            sb.StreamResult(1, "", 0, False, "Error response from daemon: is not running", False)
        )
        assert (await broker.exec(handle, "true", 5)).restarted
    granted, restarted = docker.sandbox_runs()[-2:]
    assert flag_values(restarted, "--network") == [sb.egress_network_name(ORG)]
    assert mounts_of(restarted) == mounts_of(granted)


# ── the sweep ────────────────────────────────────────────────────────────────


async def test_the_sweep_removes_the_proxy_the_networks_and_every_grant(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        await broker.grant_egress(handle, "pip", ["pypi.org"])
    docker.ps_ids = [handle.container_id, "proxy-id"]
    await broker.sweep()
    assert "proxy-id" in docker.removals(), "the proxy carries the sandbox label"
    assert ["network", "rm", sb.egress_network_name(ORG)] in docker.calls
    assert docker.networks == {}
    assert not broker._grants and handle.egress is None
    assert _grants_file(broker)["grants"] == {}


async def test_the_sweep_leaves_a_remote_daemon_s_networks_alone(
    broker: sb.SandboxBroker, docker: EgressDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No grant runs on docker-remote, and a shared daemon holds other networks."""
    monkeypatch.setattr(env["settings"], "sandbox_backend", "docker-remote")
    monkeypatch.setattr(env["settings"], "sandbox_docker_host", "ssh://sbx@10.0.0.9")
    await broker.sweep()
    assert docker.network_calls("ls") == [] and docker.network_calls("rm") == []


def test_the_proxy_carries_the_label_that_the_sweep_removes(tmp_path: Path) -> None:
    argv = sb.build_proxy_argv(
        image="sha256:" + "a" * 64, bin_dir=tmp_path / "bin", grants_dir=tmp_path / "grants",
    )
    assert f"{sb.LABEL_SANDBOX}=1" in flag_values(argv, "--label")


# ── the host firewall script (written and tested, not applied) ───────────────

_BASH = shutil.which("bash")


@pytest.mark.skipif(_BASH is None, reason="needs bash")
def test_the_firewall_script_prints_one_drop_rule_for_each_family() -> None:
    assert subprocess.run([_BASH, "-n", str(_FIREWALL)], check=False).returncode == 0
    out = subprocess.run(
        [_BASH, str(_FIREWALL)], capture_output=True, text=True, timeout=30, check=True,
        encoding="utf-8",
    ).stdout.splitlines()
    rule = "-I INPUT -i mtr-eg-+ -m comment --comment metorite-sandbox-egress -j DROP"
    assert out == [f"iptables {rule}", f"ip6tables {rule}"]
    assert sb.egress_bridge_name(ORG).startswith(sb.EGRESS_BRIDGE_PREFIX)
    assert sb.EGRESS_BRIDGE_PREFIX + "+" in rule


@pytest.mark.skipif(_BASH is None, reason="needs bash")
def test_the_firewall_script_refuses_an_unknown_mode_and_a_non_root_apply() -> None:
    bad = subprocess.run(
        [_BASH, str(_FIREWALL), "--bogus"], capture_output=True, timeout=30, check=False,
    )
    assert bad.returncode == 64
    if os.geteuid() != 0:
        apply = subprocess.run(
            [_BASH, str(_FIREWALL), "--apply"], capture_output=True, timeout=30, check=False,
        )
        assert apply.returncode == 2


@pytest.mark.skipif(_BASH is None, reason="needs bash")
@pytest.mark.parametrize(("version", "ok"), [
    ("29.9.2", True), ("26.0.0", True), ("26.1.4", True), ("25.0.5", True),
    ("25.1.0", True), ("23.0.11", True), ("23.0.15", True),
    ("25.0.4", False), ("25.0.0", False), ("24.0.9", False), ("23.0.10", False),
    ("20.10.24", False), ("", False), ("garbage", False), ("26.0.0-rc1", True),
])
def test_the_firewall_check_refuses_a_docker_hit_by_cve_2024_29018(
    version: str, ok: bool,
) -> None:
    """Before 26.0.0, 25.0.5 and 23.0.11, an --internal network forwards DNS out."""
    done = subprocess.run(
        [_BASH, str(_FIREWALL), "--check-docker-version", version],
        capture_output=True, timeout=30, check=False,
    )
    assert (done.returncode == 0) is ok


def test_nothing_in_the_repo_applies_the_firewall_script() -> None:
    """To apply it on a box is owner gate WS43-G1, and in CI it is WS-43g3."""
    hits: list[str] = []
    for root in ("deploy", "scripts", ".github", "infra"):
        base = _REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".sh", ".yml", ".yaml", ".py", ".service"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                if "host_firewall.sh" in text:
                    hits.append(path.relative_to(_REPO).as_posix())
    assert hits == []


# ── the Docker half ──────────────────────────────────────────────────────────
# Deselected by default (pyproject.toml). .github/workflows/sandbox-docker.yml
# runs it and fails on any skip.

DOCKER_ORG = f"99999999-0000-4000-8000-{uuid.uuid4().hex[:12]}"
_DOOR_TOKEN = "msbx_" + "d" * 43

_STUB = r"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        seen = sorted(k.lower() for k in self.headers.keys())
        if self.headers.get("X-Sandbox-Capability") != "__TOKEN__":
            return self.send(401, {"detail": "Unauthorized", "seen": seen})
        return self.send(200, {"rows": [{"id": "lead-1"}], "seen": seen, "path": self.path})
    def send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass
HTTPServer(("0.0.0.0", 8080), H).serve_forever()
""".replace("__TOKEN__", _DOOR_TOKEN)

_PROBE = r"""
import json, os, socket
import requests
import metorite_door as md
out = {}
os.environ["METORITE_API_TOKEN"] = "__TOKEN__"
try:
    out["door"] = md.Door(timeout_s=20).get("/crm/leads")
except Exception as e:
    out["door"] = "error: %s %s" % (type(e).__name__, e)
try:
    r = requests.get("https://pypi.org/simple/pip/", timeout=30)
    out["pypi"] = r.status_code
except Exception as e:
    out["pypi"] = "error: %s" % type(e).__name__
try:
    requests.get("https://example.com/", timeout=20)
    out["other"] = "answered"
except requests.exceptions.ProxyError as e:
    out["other"] = "proxy_refused" if "403" in str(e) else "proxy_error: %s" % e
except Exception as e:
    out["other"] = "error: %s" % type(e).__name__
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    out["direct"] = "connected"
except OSError as e:
    out["direct"] = "refused: %s" % type(e).__name__
try:
    socket.getaddrinfo("pypi.org", 443)
    out["dns"] = "resolved"
except OSError as e:
    out["dns"] = "failed"
print("RESULT " + json.dumps(out))
""".replace("__TOKEN__", _DOOR_TOKEN)


def _docker(*args: str, timeout: float = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", check=False,
    )


@pytest.fixture
async def real(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    coding_sandbox_image: str,  # noqa: F811 — the fixture, imported above
) -> Any:
    """A real broker on the local daemon, a door stub, and unique names."""
    probe = tmp_path / "bind-probe"
    probe.mkdir()
    if _docker(
        "run", "--rm", "--mount", f"type=bind,source={probe},target=/w",
        coding_sandbox_image, "true",
    ).returncode != 0:
        pytest.fail("the proxy needs a bind mount of the grants dir, and it failed here")
    tag = uuid.uuid4().hex[:10]
    stub = f"ws43g1-door-{tag}"
    monkeypatch.setattr(sb, "CONTAINER_PREFIX", f"ws43g1-sbx-{tag}-")
    monkeypatch.setattr(sb, "EGRESS_PROXY_NAME", f"ws43g1-proxy-{tag}")
    env = configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr(env["settings"], "sandbox_image", coding_sandbox_image)
    monkeypatch.setattr(sb, "_process_ids", _REAL_PROCESS_IDS)
    monkeypatch.setattr(env["settings"], "sandbox_egress_enabled", True)
    broker = sb.SandboxBroker()
    try:
        started = _docker(
            "run", "-d", "--name", stub, "--network", "bridge", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "1000:1000", coding_sandbox_image, "python3", "-I", "-c", _STUB,
        )  # fmt: skip
        assert started.returncode == 0, started.stderr
        ip = _docker(
            "inspect", "-f", "{{.NetworkSettings.Networks.bridge.IPAddress}}", stub,
        ).stdout.strip()
        assert ip, "the door stub has no bridge address"
        monkeypatch.setattr(env["settings"], "sandbox_egress_door_upstream", f"{ip}:8080")
        env.update(broker=broker, stub=stub, proxy=sb.EGRESS_PROXY_NAME)
        yield env
    finally:
        await broker.settle()
        await broker.stop()
        left = _docker("ps", "-aq", "--filter", f"label=metorite.org={DOCKER_ORG}").stdout.split()
        _docker("rm", "-f", stub, sb.EGRESS_PROXY_NAME, *left)
        _docker("network", "rm", sb.egress_network_name(DOCKER_ORG))


def _networks_of(name: str) -> set[str]:
    raw = _docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", name).stdout
    return set(json.loads(raw or "{}"))


def _proxy_lines(proxy: str) -> list[dict[str, Any]]:
    logs = _docker("logs", proxy).stdout.splitlines()
    return [json.loads(x) for x in logs if x.startswith("{") and '"decision"' in x]


@pytest.mark.sandbox_docker
async def test_docker_a_granted_run_reaches_the_door_and_pypi_and_nothing_else(
    real: dict[str, Any],
) -> None:
    """The task's done-when: the door, an allowlisted host, and a refused one.

    The run reaches the door stub through the proxy, with no DNS lookup and
    no proxy token at the stub. It reaches ``pypi.org`` by ``CONNECT`` 443.
    The proxy refuses ``example.com`` with a log line that names the
    approver. The container reaches nothing directly. The run's end puts it
    back on ``--network none`` with the ``.git`` cover in place.
    """
    broker: sb.SandboxBroker = real["broker"]
    with bound_run(DOCKER_ORG, member=MEMBER, thread="t-egress") as ws:
        _with_run_id("run-docker")
        (ws / ".git").mkdir()
        (ws / "probe.py").write_text(_PROBE, encoding="utf-8")
        handle = await broker.acquire()
        assert _networks_of(handle.name) == {"none"}
        await broker.grant_egress(handle, "the door and pip", [sb.DOOR_HOST, "pypi.org"])
        assert _networks_of(handle.name) == {sb.egress_network_name(DOCKER_ORG)}
        result = await broker.exec(handle, "python3 probe.py", 120)
        line = next(
            (x for x in result.output.splitlines() if x.startswith("RESULT ")), "",
        )
        assert line, result.output
        out = json.loads(line[7:])
        lines = _proxy_lines(real["proxy"])
        await broker.release(handle)
        assert await broker.end_run() is True
        after = _networks_of(handle.name)
        mounts = _docker("inspect", "-f", "{{json .Mounts}}", handle.name).stdout
    assert isinstance(out["door"], dict), out
    assert out["door"]["rows"] == [{"id": "lead-1"}]
    assert "proxy-authorization" not in out["door"]["seen"]
    assert not {"via", "x-forwarded-for", "forwarded"} & set(out["door"]["seen"])
    assert out["pypi"] == 200, f"pypi.org through CONNECT 443: {out['pypi']} (box network?)"
    assert out["other"] == "proxy_refused", out
    assert out["direct"].startswith("refused"), out
    by_host = {(x["host"], x["decision"]): x for x in lines}
    door = by_host[(sb.DOOR_HOST, "allow")]
    assert door["reason"] == "door" and door["approver"] == MEMBER
    assert door["organization"] == DOCKER_ORG and door["run"] == "run-docker"
    pypi = by_host[("pypi.org", "allow")]
    assert pypi["method"] == "CONNECT" and pypi["port"] == 443 and pypi["bytes_down"] > 0
    other = by_host[("example.com", "refuse")]
    assert other["reason"] == "host_not_on_grant" and other["approver"] == MEMBER
    # Done-when 7: the run's end leaves no grant, on --network none, cover on.
    assert after == {"none"}
    assert any(m.get("Destination") == "/workspace/.git" for m in json.loads(mounts or "[]"))
    assert _docker("network", "inspect", sb.egress_network_name(DOCKER_ORG)).returncode != 0
    grants = json.loads(broker._grants_path().read_text(encoding="utf-8"))
    assert grants["grants"] == {}
    # Recorded for the report: does an --internal network resolve outside names?
    print(f"WS43G1 dns-in-sandbox={out['dns']} direct={out['direct']}")


@pytest.mark.sandbox_docker
async def test_docker_a_revoked_token_is_refused_at_the_next_request(
    real: dict[str, Any],
) -> None:
    """A revoke takes effect at the proxy's next request, with a log line."""
    broker: sb.SandboxBroker = real["broker"]
    with bound_run(DOCKER_ORG, member=MEMBER, thread="t-revoke"):
        handle = await broker.acquire()
        grant = await broker.grant_egress(handle, "pip", ["pypi.org"])
        await broker.revoke_egress(handle)
    proxy = real["proxy"]
    host_probe = (
        "import requests;"
        f"p='http://grant:{grant.token}@127.0.0.1:{sb.EGRESS_PROXY_PORT}';"
        "r=requests.get('https://pypi.org/simple/pip/',proxies={'https':p},timeout=20)"
    )
    ran = _docker("exec", proxy, "python3", "-I", "-c", host_probe, timeout=60)
    assert ran.returncode != 0 and "407" in ran.stderr, ran.stderr[-600:]
    refused = [x for x in _proxy_lines(proxy) if x["reason"] == "no_valid_grant"]
    assert refused and refused[-1]["decision"] == "refuse"


async def test_a_fresh_proxy_joins_the_network_of_every_live_grant(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    """Review round 1: a proxy that died and starts again joins each org's network."""
    with bound_run(ORG, member=MEMBER, thread="ta"):
        a = await broker.acquire()
        await broker.grant_egress(a, "pip", ["pypi.org"])
    docker.proxy_running = False  # the proxy died
    with bound_run(ORG_B, member=MEMBER, thread="tb"):
        b = await broker.acquire()
        await broker.grant_egress(b, "pip", ["pypi.org"])
    assert len(docker.proxy_runs) == 2
    after = docker.calls[docker.calls.index(docker.proxy_runs[1]):]
    joined = {c[2] for c in after if c[:2] == ["network", "connect"]}
    assert joined == {sb.egress_network_name(ORG), sb.egress_network_name(ORG_B)}


async def test_the_env_file_goes_even_when_the_start_fails(
    broker: sb.SandboxBroker, docker: EgressDocker, egress_on: dict[str, Any],
) -> None:
    with bound_run(ORG, member=MEMBER):
        handle = await broker.acquire()
        docker.fail_run = True
        with pytest.raises(sb.SandboxUnavailable):
            await broker.grant_egress(handle, "pip", ["pypi.org"])
    folder = broker.state_dir() / "egress" / "env"
    assert not folder.exists() or list(folder.iterdir()) == []


def test_the_proxy_never_forwards_between_its_networks(tmp_path: Path) -> None:
    argv = sb.build_proxy_argv(
        image="sha256:" + "a" * 64, bin_dir=tmp_path / "bin", grants_dir=tmp_path / "grants",
    )
    assert set(flag_values(argv, "--sysctl")) == {
        "net.ipv4.ip_forward=0", "net.ipv6.conf.all.forwarding=0",
    }
