"""The staging box: its setup, its nightly rebuild and its own units.

WS-38 S-5 and T-9, ``project-docs/specs/staging_and_daily_release.md`` §5.2
to §5.5, §5.10 and §5.11.

Two scripts run as root on the dev box, and one of them drops a database. So
the tests below fence the guards before anything else:

* ``deploy/hostinger/bootstrap_staging.sh`` refuses on the production box.
* ``scripts/staging_refresh.sh`` refuses unless the box says DEPLOY_ENV=staging.

The rest fences what the box may hold and do:

* every outbound switch is off, and the nightly rebuild never turns one on;
* the staging-only units sit where production cannot install them;
* the database and the tunnel listen on loopback only;
* the production side's key line can forward one port and read one folder.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_BOOT = _ROOT / "deploy/hostinger/bootstrap_staging.sh"
_REFRESH = _ROOT / "scripts/staging_refresh.sh"
_ST = _ROOT / "deploy/hostinger/staging"


def _fn(path: pathlib.Path, name: str) -> str:
    body = path.read_text(encoding="utf-8")
    start = body.index(f"{name}() {{")
    return body[start: body.index("\n}\n", start) + 3]


def _code(path: pathlib.Path) -> str:
    return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines()
                     if not ln.lstrip().startswith("#"))


# ── The guards ───────────────────────────────────────────────────────────────


def test_the_bootstrap_refuses_on_the_production_box(tmp_path: pathlib.Path) -> None:
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "hostname").write_text("#!/bin/sh\necho 10.0.0.5 187.127.172.200\n", encoding="utf-8")
    (stub / "hostname").chmod(0o755)
    res = subprocess.run(["bash", str(_BOOT)], capture_output=True, text=True, timeout=30,
                         env={"PATH": f"{stub}:/usr/bin:/bin"})
    assert res.returncode == 1
    assert "production address" in res.stderr
    assert "Firewall" not in res.stdout, "a step ran before the guard"


def test_the_bootstrap_checks_production_before_it_changes_anything() -> None:
    body = _code(_BOOT)
    guard = body.index('fail "this box has the production address')
    for first_change in ("ufw ", "apt-get", "useradd", "git clone", "set_kv "):
        assert guard < body.index(first_change), first_change
    assert body.index("/etc/caddy/Caddyfile serves a production host") < body.index("ufw ")


@pytest.mark.parametrize("content", [None, "DEPLOY_ENV=production\n", "OTHER=1\n"],
                         ids=["no-env", "production", "no-line"])
def test_the_refresh_refuses_off_staging(tmp_path: pathlib.Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / ".env").write_text(content, encoding="utf-8")
    res = subprocess.run(["bash", str(_REFRESH)], capture_output=True, text=True, timeout=30,
                         env={"PATH": "/usr/bin:/bin", "STAGING_APP_DIR": str(tmp_path)})
    assert res.returncode == 1
    assert "does not say DEPLOY_ENV=staging" in res.stderr


def test_the_refresh_checks_staging_before_it_drops_anything() -> None:
    body = _code(_REFRESH)
    guard = body.index("does not say DEPLOY_ENV=staging")
    for act in ("DROP DATABASE", "systemctl stop", "rsync -a", "pg_restore"):
        assert guard < body.index(act), act


def test_the_refresh_never_touches_production() -> None:
    body = _code(_REFRESH)
    assert "rrsync" not in body, "the read-only limit belongs to the production side's key line"
    assert re.findall(r"rsync -a --delete", body) == ["rsync -a --delete"]
    # It reads from the production box, and it writes to its own import folder.
    assert '"acb@$prod_host:./" "$import_dir/"' in body


# ── Outbound stays off ───────────────────────────────────────────────────────


def _offs() -> list[str]:
    body = _BOOT.read_text(encoding="utf-8")
    return re.findall(r"\b([A-Z0-9_]+_ENABLED)=(?:0|false)\b", body)


def test_the_bootstrap_turns_every_outbound_switch_off() -> None:
    offs = set(_offs())
    for name in ("WHATSAPP_BRIDGE_ENABLED", "MEETING_BOT_ENABLED", "EMAIL_BACKGROUND_SYNC_ENABLED",
                 "EMAIL_SYNC_ENABLED", "MEMBER_INVITE_EMAIL_ENABLED", "REACH_ENABLED",
                 "WHATSAPP_ASSISTANT_ENABLED", "EMAIL_OTP_ENABLED",
                 "CUSTOMER_CONSOLE_RESOLVE_ENABLED", "SELF_SERVE_SIGNUP_ENABLED"):
        assert name in offs, name


def test_the_nightly_switches_never_turn_an_outbound_one_on() -> None:
    held = re.search(r"^held='([^']+)'$", _REFRESH.read_text(encoding="utf-8"), re.M).group(1)
    for name in set(_offs()):
        assert re.search(held, name), f"production's {name} would overwrite staging's OFF"


def test_the_switch_merge(tmp_path: pathlib.Path) -> None:
    imp = tmp_path / "import"
    imp.mkdir()
    (imp / "flags.env").write_text(
        "root:TASKS_LENS=on\nroot:EMAIL_SYNC_ENABLED=true\nroot:REACH_ENABLED=true\n"
        "root:BAD NAME=1\nroot:ODD_ENABLED=a b\nworkbench:NEXT_PUBLIC_SHELL_BAR=1\n"
        "workbench:MEMBER_INVITE_EMAIL_ENABLED=true\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("TASKS_LENS=off\nEMAIL_SYNC_ENABLED=false\nA=1\n", encoding="utf-8")
    env.chmod(0o600)
    wb = tmp_path / ".env.local"
    wb.write_text("MEMBER_INVITE_EMAIL_ENABLED=false\n", encoding="utf-8")
    body = _REFRESH.read_text(encoding="utf-8")
    held = re.search(r"^held='[^']+'$", body, re.M).group(0)
    prog = "\n".join(["set -euo pipefail", f"import_dir={imp}", held, _fn(_REFRESH, "apply_flags"),
                      f"apply_flags root {env}", f"apply_flags workbench {wb}"])
    res = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    assert sorted(env.read_text(encoding="utf-8").splitlines()) == [
        "A=1", "EMAIL_SYNC_ENABLED=false", "TASKS_LENS=on"]
    assert wb.read_text(encoding="utf-8").splitlines() == [
        "MEMBER_INVITE_EMAIL_ENABLED=false", "NEXT_PUBLIC_SHELL_BAR=1"]
    assert (env.stat().st_mode & 0o777) == 0o600


def test_set_kv_keeps_a_secret_and_the_mode(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "e"
    f.write_text("KEY=old\nX=1\n", encoding="utf-8")
    f.chmod(0o600)
    prog = "\n".join(["set -euo pipefail", _fn(_BOOT, "set_kv"),
                      f"set_kv {f} KEY new keep", f"set_kv {f} X 2", f"set_kv {f} Y 3 keep"])
    subprocess.run(["bash", "-c", prog], check=True, timeout=30)
    assert sorted(f.read_text(encoding="utf-8").splitlines()) == ["KEY=old", "X=2", "Y=3"]
    assert (f.stat().st_mode & 0o777) == 0o600
    assert not (tmp_path / "e.tmp").exists()


def test_the_bootstrap_writes_no_production_host_or_secret() -> None:
    body = _code(_BOOT)
    for line in body.splitlines():
        if "app.metorite.com" in line or "api.metorite.com" in line:
            assert "Caddyfile" in line, f"a production host outside the guard: {line.strip()}"
    assert "$(gen)" in body
    for name in ("ACB_MASTER_KEY", "GATEWAY_INTERNAL_TOKEN", "AUTH_SECRET"):
        assert re.search(rf'set_kv "\$\w+" {name} "\$\(gen\)" keep', body), name


# ── The staging-only units ───────────────────────────────────────────────────


def test_production_cannot_install_a_staging_unit() -> None:
    top = {p.name for p in (_ROOT / "deploy/hostinger").iterdir() if p.is_file()}
    for p in _ST.iterdir():
        if p.suffix in (".service", ".timer", ".slice"):
            assert p.name not in top, f"{p.name} also ships where production installs it"


def test_the_staging_database_and_tunnel_listen_on_loopback_only() -> None:
    db = (_ST / "acb-staging-db.service").read_text(encoding="utf-8")
    assert "-p 127.0.0.1:5435:5432" in db
    assert "pgvector/pgvector:pg17" in db, "staging must run production's major version"
    assert re.search(r"--memory 1g", db)
    tun = (_ST / "acb-console-tunnel.service").read_text(encoding="utf-8")
    assert "-L 127.0.0.1:18090:127.0.0.1:8090" in tun
    assert "StrictHostKeyChecking=yes" in tun and "User=acb" in tun


def test_the_production_key_line_is_one_port_and_one_read_only_folder() -> None:
    line = [ln for ln in (_ST / "authorized_keys.line").read_text(encoding="utf-8").splitlines()
            if ln and not ln.startswith("#")]
    assert len(line) == 1
    opts = line[0].rsplit(" KEY ", 1)[0]
    assert opts.startswith("restrict,")
    assert 'permitopen="127.0.0.1:8090"' in opts
    assert 'command="/usr/bin/rrsync -ro /var/lib/acb/staging-export/current"' in opts
    boot = _BOOT.read_text(encoding="utf-8").replace('\\"', '"')
    assert opts in boot, "the bootstrap prints a different line from the one recorded here"


def test_each_staging_drop_in_only_sets_the_slice() -> None:
    confs = list(_ST.glob("*.service.d/*.conf"))
    assert {c.parent.name for c in confs} == {
        "acb-gateway.service.d", "acb-workbench.service.d", "acb-agent-edge.service.d",
        "acb-pull.service.d"}
    for c in confs:
        code = [ln for ln in c.read_text(encoding="utf-8").splitlines()
                if ln and not ln.startswith("#")]
        assert code == ["[Service]", "Slice=acb-staging.slice"], c


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_the_scripts_are_executable_bash() -> None:
    for p in (_BOOT, _REFRESH, _ROOT / "scripts/staging_export.sh"):
        assert p.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n"), p
        assert subprocess.run(["bash", "-n", str(p)]).returncode == 0, p
