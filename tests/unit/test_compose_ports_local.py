"""WS-49 BH-8 — no Docker service publishes a port on a public interface.

Fence BH-F5 of ``project-docs/specs/box_hardening.md``.

On 2026-10-08 Neo4j answered on the public internet. ``infra/docker-compose.yml``
published ``"7474:7474"`` and ``"7687:7687"``, which Docker binds to
``0.0.0.0``. Docker writes its own iptables rules, and those rules go around
``ufw``. The password was the compose default, ``neo4j_dev_change_me``, which
sits in this repo. Scanners connected. The memory profile that started Neo4j
came from ``acb.service``, a unit that only a heredoc in ``bootstrap.sh``
wrote, so no repo file held it and no test could see it.

This file pins four things:

1. Every published port in the production compose file binds to the LITERAL
   ``127.0.0.1``. A host part from an env var also fails. The gateway writes
   ``.env`` at run time, so ``${POSTGRES_BIND:-127.0.0.1}`` let a gateway write
   publish Postgres.
2. Neo4j has no default password, and its entrypoint guard refuses the bad
   ones. The compose file holds no ``${VAR:?}``: Compose interpolates the whole
   file before it reads profiles, so a required var breaks ``--profile core``
   too (measured on Compose v5.1.3).
3. ``acb.service`` is a repo file, it starts ``--profile core`` only, and the
   ``vps_apply.sh`` unit loop installs it.
4. ``bootstrap.sh`` installs that file and writes no unit from a heredoc.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "infra" / "docker-compose.yml"
_UNITS = _ROOT / "deploy" / "hostinger"
_UNIT = _UNITS / "acb.service"
_APPLY = _ROOT / "scripts" / "vps_apply.sh"
_BOOTSTRAP = _UNITS / "bootstrap.sh"
_DEPLOY = _UNITS / "deploy.sh"

_LOOPBACK = "127.0.0.1"

# Every host port the production file publishes today. A scan that finds
# nothing passes the loopback test, so this floor makes an empty scan fail.
_KNOWN_HOST_PORTS = {"5432", "6379", "7474", "7687", "3000", "8095", "6080"}

# The values the Neo4j guard must refuse: empty, the image default, and the
# old compose default that this repo published.
_BAD_NEO4J_PASSWORDS = ("", "neo4j", "neo4j_dev_change_me")

# Linux only, like test_deploy_smoke_wiring.py. On Windows a fake on PATH
# loses to Git Bash's own tools. CI runs these on ubuntu-latest.
_TOOLS = sys.platform.startswith("linux") and all(
    shutil.which(t) for t in ("bash", "sh", "cmp", "install")
)
needs_shell = pytest.mark.skipif(not _TOOLS, reason="needs Linux with bash and coreutils")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """The text without comment lines. A comment may name what the code must not do."""
    return [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _compose() -> dict[str, Any]:
    return yaml.safe_load(_read(_COMPOSE))


# ── 1. Published ports ──────────────────────────────────────────────────────


def _public_ports(compose: dict[str, Any]) -> list[str]:
    """Every published port that is not bound to the literal loopback address.

    Short syntax ``"HOST_IP:HOST:CONTAINER"`` must start with ``127.0.0.1:``.
    A bare ``"7474:7474"`` or an int binds to every interface. A ``$`` in the
    entry means the host part can come from an env file. Long syntax must carry
    ``host_ip: 127.0.0.1``. ``network_mode: host`` skips ``ports:`` and binds
    on the host, so it fails too.
    """
    bad: list[str] = []
    for name, svc in (compose.get("services") or {}).items():
        svc = svc or {}
        if str(svc.get("network_mode", "")).strip() == "host":
            bad.append(f"{name}: network_mode: host")
        for entry in svc.get("ports") or []:
            if isinstance(entry, dict):
                host_ip = str(entry.get("host_ip", ""))
                published = str(entry.get("published", ""))
                if host_ip != _LOOPBACK or "$" in host_ip + published:
                    bad.append(f"{name}: {entry!r}")
                continue
            text = str(entry)
            if "$" in text or not text.startswith(f"{_LOOPBACK}:"):
                bad.append(f"{name}: {text!r}")
    return bad


def _host_ports(compose: dict[str, Any]) -> set[str]:
    ports: set[str] = set()
    for svc in (compose.get("services") or {}).values():
        for entry in (svc or {}).get("ports") or []:
            if isinstance(entry, dict):
                ports.add(str(entry.get("published", "")))
            else:
                parts = str(entry).split(":")
                if len(parts) >= 3:
                    ports.add(parts[-2])
    return ports


def test_every_published_port_binds_to_loopback() -> None:
    bad = _public_ports(_compose())
    assert bad == [], (
        "these ports in infra/docker-compose.yml reach past the box. Docker "
        "port rules go around ufw. Write the host part as the literal "
        f"{_LOOPBACK}, with no env var: {bad}"
    )


def test_the_scan_sees_every_published_port() -> None:
    found = _host_ports(_compose())
    missing = _KNOWN_HOST_PORTS - found
    assert not missing, f"the scan no longer sees these host ports: {sorted(missing)}"


def test_the_port_fence_can_fail() -> None:
    synthetic = {
        "services": {
            "bare": {"ports": ["7474:7474"]},
            "int": {"ports": [5432]},
            "any": {"ports": ["0.0.0.0:6379:6379"]},
            "v6": {"ports": ["[::]:3000:3000"]},
            "env_host": {"ports": ["${POSTGRES_BIND:-127.0.0.1}:5432:5432"]},
            "env_port": {"ports": ["127.0.0.1:${PORT:-80}:80"]},
            "long_public": {"ports": [{"target": 7687, "published": "7687"}]},
            "long_env": {"ports": [{"target": 1, "published": "1", "host_ip": "${IP}"}]},
            "hostnet": {"network_mode": "host"},
            "ok_short": {"ports": ["127.0.0.1:8095:8080"]},
            "ok_long": {"ports": [{"target": 1, "published": "1", "host_ip": "127.0.0.1"}]},
        }
    }
    flagged = {line.split(":", 1)[0] for line in _public_ports(synthetic)}
    assert flagged == {
        "bare",
        "int",
        "any",
        "v6",
        "env_host",
        "env_port",
        "long_public",
        "long_env",
        "hostnet",
    }


def test_the_bind_overrides_are_gone() -> None:
    text = _read(_COMPOSE)
    for knob in ("POSTGRES_BIND", "REDIS_BIND"):
        assert f"${{{knob}" not in text, f"{knob} can publish a port from .env"


# ── 2. The Neo4j password ───────────────────────────────────────────────────


def _neo4j() -> dict[str, Any]:
    return _compose()["services"]["neo4j"]


def test_the_compose_file_has_no_required_var() -> None:
    """A ``${VAR:?}`` anywhere in the file fails every profile when VAR is
    unset, because Compose interpolates the whole file first. That would break
    ``acb.service`` and every deploy on a box that runs no Neo4j. ``$${`` is a
    container-side escape, so this skips it. Comment lines may name the form."""
    code = "\n".join(_code_lines(_read(_COMPOSE)))
    hits = re.findall(r"(?<!\$)\$\{[A-Za-z_][A-Za-z0-9_]*:?\?", code)
    assert hits == [], f"a required var breaks --profile core too: {hits}"


def test_neo4j_has_no_default_password() -> None:
    svc = _neo4j()
    assert svc["environment"]["NEO4J_AUTH"] == "neo4j/${NEO4J_PASSWORD:-}"
    health = " ".join(svc["healthcheck"]["test"])
    assert "neo4j_dev_change_me" not in health
    # The container env holds NEO4J_AUTH, not NEO4J_PASSWORD.
    assert "$${NEO4J_AUTH#neo4j/}" in health


def _guard_script() -> str:
    """The guard as the container shell sees it: Compose turns ``$$`` into ``$``."""
    entry = _neo4j()["entrypoint"]
    assert entry[:2] == ["/bin/sh", "-c"], entry
    return entry[2].replace("$$", "$")


def test_neo4j_guard_hands_over_to_the_image_entrypoint() -> None:
    svc = _neo4j()
    script = _guard_script()
    for bad in _BAD_NEO4J_PASSWORDS:
        assert (f'"{bad}"' if bad == "" else bad) in script
    assert 'exec tini -g -- /startup/docker-entrypoint.sh "$@"' in script
    assert svc["command"] == ["neo4j"]


@needs_shell
@pytest.mark.parametrize("password", _BAD_NEO4J_PASSWORDS)
def test_neo4j_guard_refuses_a_bad_password(tmp_path: Path, password: str) -> None:
    result = _run_guard(tmp_path, f"neo4j/{password}")
    assert result.returncode == 64, result.stdout + result.stderr
    assert "refusing to start" in result.stderr
    assert not (tmp_path / "started").exists()


@needs_shell
def test_neo4j_guard_starts_with_a_real_password(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, "neo4j/a-real-secret-7f3k")
    assert result.returncode == 0, result.stdout + result.stderr
    argv = (tmp_path / "started").read_text(encoding="utf-8").split()
    assert argv == ["-g", "--", "/startup/docker-entrypoint.sh", "neo4j"]


def _run_guard(tmp_path: Path, auth: str) -> subprocess.CompletedProcess[str]:
    fake = tmp_path / "bin"
    fake.mkdir()
    tini = fake / "tini"
    tini.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STARTED"\n', encoding="utf-8", newline="\n")
    tini.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{fake}{os.pathsep}{os.environ.get('PATH', '')}",
        NEO4J_AUTH=auth,
        STARTED=str(tmp_path / "started"),
    )
    return subprocess.run(
        ["sh", "-c", _guard_script(), "acb-neo4j-guard", "neo4j"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


# ── 3. acb.service is a repo file, core profile only ────────────────────────


def _unit_keys() -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for line in _code_lines(_read(_UNIT)):
        if "=" in line and not line.startswith("["):
            k, v = line.split("=", 1)
            keys.setdefault(k.strip(), []).append(v.strip())
    return keys


def test_the_compose_unit_is_a_repo_file() -> None:
    assert _UNIT.is_file(), "acb.service must live in deploy/hostinger/"
    keys = _unit_keys()
    assert keys["Type"] == ["oneshot"]
    assert keys["RemainAfterExit"] == ["yes"]
    assert keys["WorkingDirectory"] == ["/opt/acb/app"]
    assert keys["EnvironmentFile"] == ["/opt/acb/app/.env"]
    assert keys["Requires"] == ["docker.service"]
    assert keys["WantedBy"] == ["multi-user.target"]
    assert keys["ExecStop"] == ["/usr/bin/docker compose -f infra/docker-compose.yml down"]


def test_the_compose_unit_starts_the_core_profile_only() -> None:
    (start,) = _unit_keys()["ExecStart"]
    assert start == (
        "/usr/bin/docker compose -f infra/docker-compose.yml --profile core up -d --remove-orphans"
    )
    assert re.findall(r"--profile\s+(\S+)", start) == ["core"]
    assert "--profile memory" not in _read(_UNIT)


def test_no_deploy_script_starts_the_memory_profile() -> None:
    for path in (_APPLY, _BOOTSTRAP, _DEPLOY, _UNIT):
        for line in _code_lines(_read(path)):
            assert "--profile memory" not in line, f"{path.name}: {line.strip()!r}"


def test_vps_apply_installs_every_repo_unit() -> None:
    """``acb.service`` rides the BO-23 loop: it globs this directory, installs
    a unit that changed, and runs ``daemon-reload``."""
    code = _code_lines(_read(_APPLY))
    assert _UNIT.parent == _UNITS
    assert any('for unit in "$APP_DIR"/deploy/hostinger/*.service' in ln for ln in code), (
        "vps_apply.sh no longer globs deploy/hostinger/*.service"
    )
    assert any('sudo install -m 0644 "$unit" "/etc/systemd/system/$name"' in ln for ln in code)
    assert any("sudo systemctl daemon-reload" in ln for ln in code)


def _unit_sync_block() -> str:
    text = _read(_APPLY)
    start = text.index('echo "==> Syncing systemd units')
    end = text.index('echo "==> Running infra health probe"')
    return text[start:end]


@needs_shell
def test_the_unit_sync_installs_the_compose_unit(tmp_path: Path) -> None:
    """Run vps_apply.sh's own unit-sync block against a fake sudo and a fake
    systemctl, over a copy of the repo's unit files."""
    appd, etc, binr = tmp_path / "app", tmp_path / "etc", tmp_path / "bin"
    (appd / "deploy" / "hostinger").mkdir(parents=True)
    etc.mkdir()
    binr.mkdir()
    for unit in list(_UNITS.glob("*.service")) + list(_UNITS.glob("*.timer")):
        shutil.copy(unit, appd / "deploy" / "hostinger" / unit.name)
    for name, body in (
        ("sudo", 'exec "$@"\n'),
        ("systemctl", 'printf "%s\\n" "$*" >> "$FAKE_LOG"\n'),
    ):
        (binr / name).write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8", newline="\n")
        (binr / name).chmod(0o755)
    block = _unit_sync_block().replace("/etc/systemd/system", etc.as_posix())
    log = tmp_path / "systemctl.log"
    env = dict(
        os.environ,
        PATH=f"{binr}{os.pathsep}{os.environ.get('PATH', '')}",
        APP_DIR=appd.as_posix(),
        FAKE_LOG=log.as_posix(),
    )
    r = subprocess.run(
        ["bash", "-c", block],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (etc / "acb.service").read_text(encoding="utf-8") == _read(_UNIT)
    assert "daemon-reload" in log.read_text(encoding="utf-8").splitlines()


# ── 4. bootstrap.sh installs the repo file ──────────────────────────────────


def test_bootstrap_installs_the_repo_unit_and_writes_no_heredoc() -> None:
    text = _read(_BOOTSTRAP)
    code = _code_lines(text)
    assert not any("tee" in ln and "/etc/systemd/system/" in ln for ln in code), (
        "bootstrap.sh writes a unit with tee. Install the repo file instead"
    )
    assert not re.search(r"<<-?\s*'?UNIT'?", text), "bootstrap.sh still holds a unit heredoc"
    assert not any(ln.strip() == "[Service]" for ln in code), "a unit body sits in bootstrap.sh"
    assert any(
        'sudo install -m 0644 "$APP_DIR/deploy/hostinger/acb.service" '
        "/etc/systemd/system/acb.service" in ln
        for ln in code
    )
    install_at = next(i for i, ln in enumerate(code) if "deploy/hostinger/acb.service" in ln)
    after = code[install_at + 1 :]
    assert any("systemctl daemon-reload" in ln for ln in after)
    assert any("systemctl enable acb.service" in ln for ln in after)
