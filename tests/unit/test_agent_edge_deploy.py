"""WS-55 EA-10a and EA-10b — the deploy of the MCP edge.

Spec: project-docs/specs/external_agent_access.md §15.3 (the EA-10 pins).

These tests read the repo, never the box. They hold four things:

1. The unit ``deploy/hostinger/acb-agent-edge.service``: each pinned line.
   The edge runs as a DynamicUser, never as ``acb``, and its process reads
   no ``.env`` file at all.
2. Its sandbox ``acb-agent-edge.service.d/50-hardening.conf``: the BH-2 shape,
   plus ``InaccessiblePaths=/opt/acb/app/.env`` with no ``-``.
3. The deploy step in ``scripts/vps_apply.sh``. The tests EXECUTE the step
   against a stub ``systemctl`` and ``sudo``. A unit that is not enabled stays
   off. The BO-23 unit loop installs the file and enables no service.
4. The Caddyfile: the site ``mcp.metorite.com`` with its body cap, and the six
   ``header_up -X-...`` strips on ``api.metorite.com``.

``tests/unit/test_caddy_auth_gate.py`` is the owner gate of the Caddy half
(gate (a)). This file proves the shape. That file proves the owner saw it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNITS = ROOT / "deploy" / "hostinger"
UNIT = UNITS / "acb-agent-edge.service"
DROP_IN = UNITS / "acb-agent-edge.service.d" / "50-hardening.conf"
GATEWAY_UNIT = UNITS / "acb-gateway.service"
APPLY = ROOT / "scripts" / "vps_apply.sh"
CADDYFILE = UNITS / "caddy" / "Caddyfile"

EDGE_DIR = "/opt/acb/app/apps/services/agent_edge"
ROOT_ENV = "/opt/acb/app/.env"
PORT = 8091


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _conf_lines(p: Path) -> list[str]:
    return [
        ln.strip() for ln in _read(p).splitlines()
        if ln.strip() and not ln.strip().startswith(("#", ";"))
    ]


def _values(p: Path, key: str) -> list[str]:
    return [ln.split("=", 1)[1] for ln in _conf_lines(p) if ln.startswith(f"{key}=")]


def _one(p: Path, key: str) -> str:
    (value,) = _values(p, key)
    return value


# ── 1. The unit ─────────────────────────────────────────────────────────────


RUN_DIR = "/run/acb-agent-edge"


def test_the_unit_holds_each_pinned_line() -> None:
    lines = _conf_lines(UNIT)
    for pin in (
        "DynamicUser=yes",
        "User=acb-agent-edge",
        "RuntimeDirectory=acb-agent-edge",
        f"WorkingDirectory={RUN_DIR}",
        f"EnvironmentFile={EDGE_DIR}/.env",
        f"ExecStart=/opt/acb/app/.venv/bin/uvicorn agent_edge.app:app --host 127.0.0.1 --port {PORT}",
    ):
        assert pin in lines, pin
    # Exactly one of each, so a second line cannot override a pin.
    for key in ("DynamicUser", "User", "RuntimeDirectory", "WorkingDirectory",
                "EnvironmentFile", "ExecStart"):
        assert len(_values(UNIT, key)) == 1, key


def test_the_edge_never_runs_as_acb_or_root() -> None:
    """Fix round 1, P1. As `acb`, the edge could read every 0600 file of acb:
    the root .env, its .env.bak-<stamp> and .env-edit.* copies, and more.
    InaccessiblePaths takes no glob, so only another identity closes them.
    A DynamicUser gets a fresh uid at each start and owns no file."""
    assert _one(UNIT, "DynamicUser") == "yes"
    user = _one(UNIT, "User")
    assert user not in ("acb", "root", "0", _one(GATEWAY_UNIT, "User")), user
    for key in ("Group", "SupplementaryGroups"):
        assert not _values(UNIT, key), key
    for ln in _conf_lines(DROP_IN):
        assert ln.split("=", 1)[0] not in ("DynamicUser", "User", "Group"), ln


def test_the_edge_process_reads_no_env_file_of_its_own() -> None:
    """systemd reads EnvironmentFile as root. The edge process cannot open
    that 0600 file, and pydantic raises on a .env it cannot open. So
    Settings must find NO .env in the working directory: the unit's own
    empty runtime directory. Never the checkout root, never the edge dir."""
    workdir = _one(UNIT, "WorkingDirectory")
    assert workdir == f"/run/{_one(UNIT, 'RuntimeDirectory')}"
    assert workdir not in ("/opt/acb/app", EDGE_DIR)
    assert not workdir.startswith("/opt/")
    env_file = _one(UNIT, "EnvironmentFile")
    assert env_file == f"{EDGE_DIR}/.env" != ROOT_ENV
    for f in (UNIT, DROP_IN):
        assert ROOT_ENV not in _values(f, "EnvironmentFile"), f.name


def test_the_unit_listens_on_loopback_only() -> None:
    exec_start = _one(UNIT, "ExecStart")
    assert "--host 127.0.0.1" in exec_start
    assert "0.0.0.0" not in exec_start


def test_the_port_belongs_to_the_edge_alone() -> None:
    """No other unit, drop-in or site uses the port of the edge."""
    pattern = re.compile(rf"\b{PORT}\b")
    for f in [*UNITS.glob("*.service"), *UNITS.glob("*.service.d/*.conf")]:
        if f in (UNIT, DROP_IN):
            continue
        assert not pattern.search(_read(f)), f.name
    composes = [*(ROOT / "infra").glob("**/docker-compose*.yml"),
                *(ROOT / "apps").glob("*/*/docker-compose*.yml")]
    assert composes
    for compose in composes:
        assert not pattern.search(_read(compose)), compose
    proxied = re.findall(rf"reverse_proxy\s+127\.0\.0\.1:{PORT}\b", _read(CADDYFILE))
    assert len(proxied) == 1, proxied


def test_a_gateway_restart_does_not_restart_the_edge() -> None:
    """The edge orders after the gateway. It does not require it, so a
    gateway restart on each deploy leaves the edge alone."""
    lines = _conf_lines(UNIT)
    assert not [ln for ln in lines if ln.startswith(("Requires=", "BindsTo=", "PartOf="))]
    assert "acb-gateway.service" in _one(UNIT, "After")


def test_the_unit_carries_no_secret_and_no_python_path() -> None:
    for f in (UNIT, DROP_IN):
        for ln in _conf_lines(f):
            assert "PYTHONPATH" not in ln, ln
            if ln.startswith("Environment="):
                name = ln.split("=", 2)[1]
                assert not re.search(r"KEY|TOKEN|SECRET|PASSWORD|DATABASE", name), ln


# ── 1b. The edge opens no sync engine (the pooler budget) ──────────────────
#
# Spec §15.3, the pooler budget. The edge pins ONE async connection
# (DB_POOL_SIZE=1, DB_MAX_OVERFLOW=0). The sync engine of acb_graph would take
# db_sync_pool_size + db_sync_max_overflow more. The budget counts the edge
# as 1, so the edge must never build that engine. Two proofs: no module on
# its code path imports acb_graph, and the running app builds no engine.

#: The modules that the edge runs: its own package, and the three acb_auth
#: modules that it imports by name.
EDGE_CODE = [
    *sorted((ROOT / "apps/services/agent_edge/agent_edge").glob("*.py")),
    ROOT / "packages/acb_auth/acb_auth/agent_tokens.py",
    ROOT / "packages/acb_auth/acb_auth/agent_oauth.py",
    ROOT / "packages/acb_auth/acb_auth/agent_signing.py",
]


def test_no_module_of_the_edge_imports_the_sync_engine() -> None:
    import ast

    assert len(EDGE_CODE) > 3
    for f in EDGE_CODE:
        tree = ast.parse(_read(f))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not name.startswith("acb_graph"), f"{f.name} imports {name}"
        assert "acb_graph" not in _read(f), f"{f.name} names acb_graph"


def test_the_running_edge_builds_no_sync_engine(tmp_path: Path) -> None:
    """A fresh process imports the edge app and serves a request with the
    flag on. Then the sync engine of acb_graph must not exist."""
    import sys

    prog = (
        "import sys\n"
        "from fastapi.testclient import TestClient\n"
        "from agent_edge.app import create_app\n"
        "r = TestClient(create_app()).get('/.well-known/oauth-authorization-server')\n"
        "assert r.status_code == 200, r.status_code\n"
        "m = sys.modules.get('acb_graph.db')\n"
        "print('built' if m and m.get_engine.cache_info().currsize else 'none')\n"
    )
    env = dict(os.environ, AGENT_ACCESS_ENABLED="true",
               DATABASE_URL="postgresql+psycopg://u:p@db.invalid:5432/x")
    r = subprocess.run([sys.executable, "-c", prog], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == "none", r.stdout


# ── 2. The sandbox ─────────────────────────────────────────────────────────

#: Each line of the drop-in, in order. A change here is a reviewed change.
HARDENING_LINES = [
    "[Service]",
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "ProtectSystem=strict",
    "ProtectHome=yes",
    "InaccessiblePaths=/opt/acb/app/.env",
    "InaccessiblePaths=-/opt/acb/app/workbench -/opt/acb/app/apps/services/customer_console/.env"
    " -/opt/acb/app/.git",
    "InaccessiblePaths=-/opt/acb/app/data -/var/lib/acb-gateway -/var/cache/acb-gateway",
    "InaccessiblePaths=-/run/user -/run/docker.sock -/var/run/docker.sock -/etc/acb -/etc/sudoers.d",
    "ProtectProc=invisible",
    "RestrictSUIDSGID=yes",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
    "LockPersonality=yes",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectControlGroups=yes",
    "ProtectClock=yes",
    "ProtectHostname=yes",
    "RestrictRealtime=yes",
]


def _hidden() -> list[str]:
    out: list[str] = []
    for value in _values(DROP_IN, "InaccessiblePaths"):
        out.extend(value.split())
    return out


def test_the_drop_in_holds_each_line_of_the_pin() -> None:
    assert _conf_lines(DROP_IN) == HARDENING_LINES


def test_the_root_env_is_hidden_with_no_dash() -> None:
    """The root .env holds GATEWAY_INTERNAL_TOKEN. The edge holds no bearer
    (EA3-1). With a "-", a typo in the path would pass in silence, so this
    entry has none. The root .env is always on the box."""
    hidden = _hidden()
    assert ROOT_ENV in hidden
    assert f"-{ROOT_ENV}" not in hidden


def test_the_other_secrets_of_the_box_are_hidden() -> None:
    """The second layer, for paths that are NOT 0600: the workbench tree
    (its .env.local and the .env.local.bak.* copies), the gateway data and
    the mem0 store. The identity alone does not close a 0644 file."""
    hidden = {p.lstrip("-") for p in _hidden()}
    assert {
        "/opt/acb/app/workbench",
        "/opt/acb/app/apps/services/customer_console/.env",
        "/opt/acb/app/data", "/var/lib/acb-gateway",
        "/run/docker.sock", "/var/run/docker.sock", "/run/user", "/etc/acb",
    } <= hidden
    assert "ProtectHome=yes" in _conf_lines(DROP_IN)
    # The code and the venv stay readable: no parent of them is hidden.
    for needed in (EDGE_DIR, "/opt/acb/app/.venv", "/opt/acb/app/packages"):
        assert not [p for p in hidden if needed == p or needed.startswith(p + "/")], needed
    assert "/opt/acb/app" not in hidden


def test_the_edge_writes_nothing_and_cannot_gain_a_privilege() -> None:
    lines = _conf_lines(DROP_IN)
    assert not [ln for ln in lines if ln.startswith("ReadWritePaths=")]
    for pin in ("NoNewPrivileges=yes", "ProtectSystem=strict", "CapabilityBoundingSet="):
        assert pin in lines, pin


def test_the_drop_in_changes_no_user_exec_or_env_file() -> None:
    for ln in _conf_lines(DROP_IN):
        key = ln.split("=", 1)[0]
        assert key not in ("User", "Group", "SupplementaryGroups", "EnvironmentFile",
                           "ExecStart", "WorkingDirectory"), ln


# ── 3. The deploy step ─────────────────────────────────────────────────────

BEGIN = "# >>> ea10 edge restart"
END = "# <<< ea10 edge restart"
HEALTH_URL = f"http://127.0.0.1:{PORT}/.well-known/oauth-authorization-server"


def _code_lines() -> list[str]:
    return [ln for ln in _read(APPLY).splitlines() if not ln.lstrip().startswith("#")]


def _block() -> str:
    lines = _read(APPLY).splitlines()
    return "\n".join(lines[lines.index(BEGIN): lines.index(END) + 1]) + "\n"


def _index(lines: list[str], needle: str) -> int:
    return next(i for i, ln in enumerate(lines) if needle in ln)


def test_the_step_sits_between_the_console_restart_and_the_strict_check() -> None:
    lines = _read(APPLY).splitlines()
    begin, end = lines.index(BEGIN), lines.index(END)
    assert lines.count(BEGIN) == 1 and lines.count(END) == 1
    console = _index(lines, "sudo systemctl restart acb-customer-console")
    dropins = _index(lines, 'install_dropins "$APP_DIR/deploy/hostinger"')
    check = _index(lines, "if ! bh2_strict_check; then")
    assert dropins < console < begin < end < check


def test_the_step_is_guarded_by_is_enabled() -> None:
    body = [ln.strip() for ln in _block().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert body[1] == 'if systemctl is-enabled --quiet "$edge_unit" 2>/dev/null; then'
    assert body[0] == 'edge_unit="acb-agent-edge"'
    # Every act sits inside the guard: no line between the guard and its
    # `else` starts at column 0.
    inside = _block().splitlines()
    start = next(i for i, ln in enumerate(inside) if ln.startswith("if systemctl is-enabled"))
    stop = next(i for i, ln in enumerate(inside) if ln == "else")
    for ln in inside[start + 1: stop]:
        assert ln.startswith(" ") or not ln.strip(), ln


def _bash() -> str | None:
    b = shutil.which("bash")
    if not b or "system32" in b.lower():
        return None
    return b


needs_bash = pytest.mark.skipif(_bash() is None, reason="needs a POSIX bash")


def _run_step(tmp_path: Path, *, enabled: bool, active: bool = True, ready: bool = True,
              same_unit: bool = False) -> tuple[subprocess.CompletedProcess, list[str]]:
    """EXECUTE the step, with stub `systemctl`, `sudo`, `sleep` and
    `wait_ready`. The stubs log each call. Nothing touches the real box."""
    tmp_path = tmp_path.resolve()
    app = tmp_path / "app"
    (app / "deploy" / "hostinger").mkdir(parents=True)
    shutil.copy(UNIT, app / "deploy" / "hostinger" / UNIT.name)
    etc = tmp_path / "etc"
    etc.mkdir()
    if same_unit:
        shutil.copy(UNIT, etc / UNIT.name)
    log = tmp_path / "log"
    log.write_text("", encoding="utf-8")
    prog = (
        "set -u\n"
        f'LOG="{log.as_posix()}"\n'
        f'APP_DIR="{app.as_posix()}"\n'
        'systemctl() {\n'
        '  echo "systemctl $*" >> "$LOG"\n'
        '  case "$1" in\n'
        f'    is-enabled) return {0 if enabled else 1} ;;\n'
        f'    is-active) return {0 if active else 3} ;;\n'
        '  esac\n'
        '  return 0\n'
        '}\n'
        'sudo() { "$@"; }\n'
        'journalctl() { echo "journalctl $*" >> "$LOG"; }\n'
        'install() { echo "install $*" >> "$LOG"; command install "$@"; }\n'
        'sleep() { :; }\n'
        'wait_ready() {\n'
        '  echo "wait_ready $*" >> "$LOG"\n'
        f'  return {0 if ready else 1}\n'
        '}\n'
        + _block().replace("/etc/systemd/system", etc.as_posix())
    )
    run = subprocess.run([_bash() or "bash"], input=prog.encode(), capture_output=True,
                         timeout=60, cwd=str(tmp_path))
    calls = log.read_text(encoding="utf-8").splitlines()
    return run, calls


@needs_bash
def test_a_unit_that_is_not_enabled_stays_off(tmp_path: Path) -> None:
    run, calls = _run_step(tmp_path, enabled=False)
    out = run.stdout.decode(errors="replace")
    assert run.returncode == 0, out + run.stderr.decode(errors="replace")
    assert calls == ["systemctl is-enabled --quiet acb-agent-edge"], calls
    assert "acb-agent-edge is not enabled here" in out
    assert "the edge stays off" in out
    assert not (tmp_path / "etc" / UNIT.name).exists(), "a disabled edge gets no unit here"


@needs_bash
def test_an_enabled_unit_is_installed_then_restarted_then_awaited(tmp_path: Path) -> None:
    run, calls = _run_step(tmp_path, enabled=True)
    out = run.stdout.decode(errors="replace")
    assert run.returncode == 0, out + run.stderr.decode(errors="replace")
    restart = calls.index("systemctl restart acb-agent-edge")
    reload_ = calls.index("systemctl daemon-reload")
    installed = next(i for i, c in enumerate(calls) if c.startswith("install -m 0644"))
    assert installed < reload_ < restart
    assert calls[-1] == f"wait_ready agent edge {HEALTH_URL} 60", calls
    assert _read(tmp_path / "etc" / UNIT.name) == _read(UNIT)
    assert "agent edge is active (127.0.0.1:8091)" in out


@needs_bash
def test_an_unchanged_unit_is_restarted_with_no_reload(tmp_path: Path) -> None:
    run, calls = _run_step(tmp_path, enabled=True, same_unit=True)
    assert run.returncode == 0, run.stdout.decode(errors="replace")
    assert "systemctl restart acb-agent-edge" in calls
    assert "systemctl daemon-reload" not in calls
    assert not [c for c in calls if c.startswith("install ")]


@needs_bash
@pytest.mark.parametrize(("active", "ready"), [(False, True), (True, False)],
                         ids=["not-active", "no-http-answer"])
def test_an_enabled_edge_that_does_not_come_up_fails_the_deploy(
    tmp_path: Path, active: bool, ready: bool
) -> None:
    run, calls = _run_step(tmp_path, enabled=True, active=active, ready=ready)
    out = run.stdout.decode(errors="replace")
    assert run.returncode == 1, out
    assert "AGENT EDGE FAILED TO START" in out
    assert any(c.startswith("journalctl -u acb-agent-edge") for c in calls), calls


def _unit_sync_block() -> str:
    text = _read(APPLY)
    start = text.index('echo "==> Syncing systemd units')
    end = text.index('echo "==> WS-49 BH-7: one restart for a unit')
    return text[start:end]


@needs_bash
def test_the_unit_loop_installs_the_edge_and_enables_no_service(tmp_path: Path) -> None:
    """BO-23 installs every unit file and enables TIMERS only. So the first
    deploy puts the edge on the box and starts nothing."""
    tmp_path = tmp_path.resolve()
    app, etc, binr = tmp_path / "app", tmp_path / "etc", tmp_path / "bin"
    (app / "deploy" / "hostinger").mkdir(parents=True)
    etc.mkdir()
    binr.mkdir()
    for unit in [*UNITS.glob("*.service"), *UNITS.glob("*.timer")]:
        shutil.copy(unit, app / "deploy" / "hostinger" / unit.name)
    for name, body in (("sudo", 'exec "$@"\n'),
                       ("systemctl", 'printf "%s\\n" "$*" >> "$FAKE_LOG"\n')):
        (binr / name).write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8", newline="\n")
        (binr / name).chmod(0o755)
    block = _unit_sync_block().replace("/etc/systemd/system", etc.as_posix())
    log = tmp_path / "systemctl.log"
    env = dict(os.environ, PATH=f"{binr}{os.pathsep}{os.environ.get('PATH', '')}",
               APP_DIR=app.as_posix(), FAKE_LOG=log.as_posix())
    r = subprocess.run([_bash() or "bash", "-c", block], env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (etc / UNIT.name).read_text(encoding="utf-8") == _read(UNIT)
    calls = log.read_text(encoding="utf-8").splitlines()
    assert not [c for c in calls if "acb-agent-edge" in c], calls
    assert not [c for c in calls if c.startswith(("enable", "start", "restart"))
                and ".service" in c], calls


# ── 4. The Caddyfile ───────────────────────────────────────────────────────

STRIPS = (
    "X-User-Email", "X-User-Role", "X-Actor-Via",
    "X-Agent-Connection", "X-Agent-Timestamp", "X-Agent-Signature",
)


def _site(host: str) -> list[str]:
    """The lines of one top-level site block, comments and blanks dropped."""
    out: list[str] = []
    depth = 0
    inside = False
    for raw in _read(CADDYFILE).splitlines():
        line = re.split(r"(?:^|\s)#", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        if depth == 0 and line == f"{host} {{":
            inside = True
            depth = 1
            continue
        if inside:
            depth += line.count("{") - line.count("}")
            if depth == 0:
                return out
            out.append(" ".join(line.split()))
        else:
            depth += line.count("{") - line.count("}")
    raise AssertionError(f"no site {host}")


def test_the_mcp_site_holds_the_pinned_lines() -> None:
    assert _site("mcp.metorite.com") == [
        "request_body {",
        "max_size 1MiB",
        "}",
        f"reverse_proxy 127.0.0.1:{PORT} {{",
        "lb_try_duration 30s",
        "lb_try_interval 250ms",
        "}",
    ]


def _caddy_bytes(value: str) -> int:
    """Caddy's size words (go-humanize): KB/MB are powers of 1000, and
    KiB/MiB are powers of 1024."""
    m = re.fullmatch(r"(\d+)(B|KB|MB|KiB|MiB)", value)
    assert m, value
    scale = {"B": 1, "KB": 1000, "MB": 1000**2, "KiB": 1024, "MiB": 1024**2}[m.group(2)]
    return int(m.group(1)) * scale


def test_the_body_cap_equals_the_cap_of_the_edge() -> None:
    """Caddy's cap must not refuse a body that the edge accepts, so the edge
    answers its own 413 with a JSON-RPC body (spec §9.6)."""
    from agent_edge import mcp

    (line,) = [ln for ln in _site("mcp.metorite.com") if ln.startswith("max_size ")]
    assert _caddy_bytes(line.split()[1]) == mcp.MAX_BODY_BYTES == 1024 * 1024


def test_the_caddy_upstream_is_the_port_of_the_unit() -> None:
    port = re.search(r"--port (\d+)", _one(UNIT, "ExecStart"))
    assert port and int(port.group(1)) == PORT
    assert f"reverse_proxy 127.0.0.1:{PORT} {{" in _site("mcp.metorite.com")


def _api_proxy_block() -> list[str]:
    lines = _site("api.metorite.com")
    start = lines.index("reverse_proxy 127.0.0.1:8080 {")
    end = lines.index("}", start)
    return lines[start + 1: end]


@pytest.mark.parametrize("header", STRIPS)
def test_the_api_site_strips_each_identity_header(header: str) -> None:
    assert f"header_up -{header}" in _api_proxy_block()


def test_the_strips_sit_in_the_proxy_to_the_gateway_only() -> None:
    """A strip outside the gateway's reverse_proxy block protects nothing."""
    text = _read(CADDYFILE)
    for header in STRIPS:
        assert len(re.findall(rf"^\s*header_up -{header}\s*$", text, re.MULTILINE)) == 1, header


def test_the_h303_check_counts_three_or_more() -> None:
    """HANDOFF H-303, check 1, word for word."""
    text = _read(CADDYFILE)
    count = sum(
        1 for ln in text.splitlines()
        if re.search(r"header_up -X-(User-Email|User-Role|Actor-Via)", ln)
    )
    assert count >= 3, count


def test_the_mcp_site_sets_no_identity_header() -> None:
    """The edge reads the member from the token row, never from a header."""
    assert not [ln for ln in _site("mcp.metorite.com") if ln.startswith(("header_up", "header "))]
