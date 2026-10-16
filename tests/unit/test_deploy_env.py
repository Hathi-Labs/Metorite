"""One setting, DEPLOY_ENV, makes a box production or staging.

WS-38 S-4, ``project-docs/specs/staging_and_daily_release.md`` §5.4.

Before this, ``scripts/vps_apply.sh`` on ANY box installed production's
Caddyfile (``app.metorite.com`` and ``api.metorite.com``), enabled every
production timer (the backup and the chat smoke check against the production
organization), and seeded ``GATEWAY_PUBLIC_URL=https://api.metorite.com``. So a
staging box could not run the same script as production. It must, because two
scripts drift.

What this file fences:

1. ``DEPLOY_ENV`` is read from ``$APP_DIR/.env``. Absent means production, so
   the production box needs no edit. A value that is not ``production`` or
   ``staging`` stops the round. ``vps_pull.sh`` and ``vps_apply.sh`` read it
   with the SAME block, and the block survives ``set -euo pipefail``.
2. On staging the release ref is ``staging``, both for the pull and for the
   apply's default target.
3. ``Caddyfile.staging`` serves the two staging hosts and nothing else, and its
   api block strips the same identity headers as production's.
4. On staging the apply installs ``Caddyfile.staging`` and runs two timers.
5. The watchdog probes the ``api.`` host of the Caddyfile its box serves.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

from tests.unit.test_deploy_reexec import _git, linux_only
from tests.unit.test_deploy_target_ref import _apply, _ReleaseBox

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"
_PULL = _ROOT / "scripts/vps_pull.sh"
_CADDY = _ROOT / "deploy/hostinger/caddy/Caddyfile"
_CADDY_STAGING = _ROOT / "deploy/hostinger/caddy/Caddyfile.staging"
_WATCHDOG = _ROOT / "deploy/hostinger/health-watchdog.sh"
_PR_CHECK = _ROOT / ".github/workflows/pr-check.yml"


def _env_block(path: pathlib.Path) -> str:
    body = path.read_text(encoding="utf-8")
    start = body.index("# ── WHICH DEPLOYMENT THIS BOX IS (WS-38 S-4)")
    end = body.index("\nesac\n", start) + len("\nesac\n")
    return body[start:end]


def _strip_comment_lines(block: str) -> str:
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


# ── 1 and 2. The setting ─────────────────────────────────────────────────────


def test_both_scripts_read_the_setting_with_the_same_code() -> None:
    assert _strip_comment_lines(_env_block(_APPLY)) == _strip_comment_lines(_env_block(_PULL))


@pytest.mark.parametrize("script", [_APPLY, _PULL], ids=["apply", "pull"])
@pytest.mark.parametrize(
    "content, env, ref",
    [
        (None, "production", ""),
        ("DEPLOY_ENV=production\n", "production", ""),
        ("DEPLOY_ENV=staging\n", "staging", "staging"),
        ('DEPLOY_ENV="staging"\r\n', "staging", "staging"),
        ("OTHER=1\n", "production", ""),
    ],
    ids=["no-env-file", "production", "staging", "quoted-crlf", "no-line"],
)
def test_the_setting_under_the_strictest_shell(
    tmp_path: pathlib.Path, script: pathlib.Path, content: str | None, env: str, ref: str
) -> None:
    """``vps_pull.sh`` runs under ``set -euo pipefail``. A grep that finds no
    line exits 1, and without the guard the production box's pull would die
    at this block on every tick."""
    if content is not None:
        (tmp_path / ".env").write_text(content, encoding="utf-8", newline="")
    prog = "\n".join([
        "set -euo pipefail",
        f"APP_DIR={tmp_path}",
        _env_block(script),
        'echo "env=$DEPLOY_ENV ref=${RELEASE_REF:-}"',
    ])
    res = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == f"env={env} ref={ref}"


@pytest.mark.parametrize("script", [_APPLY, _PULL], ids=["apply", "pull"])
def test_an_unknown_value_stops_the_round(tmp_path: pathlib.Path, script: pathlib.Path) -> None:
    (tmp_path / ".env").write_text("DEPLOY_ENV=prod\n", encoding="utf-8")
    prog = "\n".join(["set -euo pipefail", f"APP_DIR={tmp_path}", _env_block(script), "echo ran"])
    res = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=30)
    assert res.returncode == 1
    assert "ran" not in res.stdout
    assert "This round did NOTHING" in res.stdout


@pytest.mark.parametrize("script", [_APPLY, _PULL], ids=["apply", "pull"])
@pytest.mark.parametrize(
    "line",
    ["export DEPLOY_ENV=staging\n", "DEPLOY_ENV = staging\n", " DEPLOY_ENV=staging\n",
     "DEPLOY_ENV=production\nexport DEPLOY_ENV=staging\n"],
    ids=["export", "spaced", "leading-space", "exact-then-export"],
)
def test_a_line_in_another_shape_stops_the_round(
    tmp_path: pathlib.Path, script: pathlib.Path, line: str
) -> None:
    """Each of these would read as production on a staging box, and the apply
    would then seed production's URLs into the staging .env."""
    (tmp_path / ".env").write_text(line, encoding="utf-8")
    prog = "\n".join(["set -euo pipefail", f"APP_DIR={tmp_path}", _env_block(script), "echo ran"])
    res = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=30)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "ran" not in res.stdout


@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root reads any file")
@pytest.mark.parametrize("script", [_APPLY, _PULL], ids=["apply", "pull"])
def test_an_unreadable_env_stops_the_round(tmp_path: pathlib.Path, script: pathlib.Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DEPLOY_ENV=staging\n", encoding="utf-8")
    env.chmod(0)
    try:
        prog = "\n".join(["set -euo pipefail", f"APP_DIR={tmp_path}", _env_block(script), "echo ran"])
        res = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=30)
    finally:
        env.chmod(0o600)
    assert res.returncode == 1
    assert "cannot be read" in res.stdout


def test_the_block_runs_before_the_pull_reads_the_ref() -> None:
    body = _PULL.read_text(encoding="utf-8")
    assert body.index("# ── WHICH DEPLOYMENT THIS BOX IS") < body.index('RELEASE_REF="${RELEASE_REF:-release}"')
    apply = _APPLY.read_text(encoding="utf-8")
    assert apply.index("# ── WHICH DEPLOYMENT THIS BOX IS") < apply.index('RELEASE_REF="${RELEASE_REF:-release}"')


@linux_only
def test_a_staging_box_deploys_the_staging_ref(tmp_path: pathlib.Path) -> None:
    box = _ReleaseBox(tmp_path)          # release at A, main at B
    _git("push", "-q", "origin", f"{box.sha_b}:refs/heads/staging", cwd=tmp_path / "seed")
    (box.app / ".env").write_text("DEPLOY_ENV=staging\n", encoding="utf-8")
    res = _apply(box)
    assert res.returncode == 0, res.stdout + res.stderr
    assert box.head() == box.sha_b, "a staging box deployed release, not staging"
    assert f"target {box.sha_b[:12]}, from origin/staging" in res.stdout


@linux_only
def test_a_production_box_still_deploys_release(tmp_path: pathlib.Path) -> None:
    box = _ReleaseBox(tmp_path)
    _git("push", "-q", "origin", f"{box.sha_b}:refs/heads/staging", cwd=tmp_path / "seed")
    res = _apply(box)
    assert res.returncode == 0, res.stdout + res.stderr
    assert box.head() == box.sha_a


# ── 3. The staging Caddyfile ─────────────────────────────────────────────────


def _sites(path: pathlib.Path) -> dict[str, list[str]]:
    """Top-level site blocks: host -> its directive lines, comments dropped."""
    sites: dict[str, list[str]] = {}
    host = None
    depth = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        if depth == 0:
            m = re.match(r"^(\S+) \{$", line)
            host = m.group(1) if m else None
            if host:
                sites[host] = []
        elif host:
            sites[host].append(line.strip())
        depth += line.count("{") - line.count("}")
    return sites


def test_staging_serves_two_hosts_and_no_production_name() -> None:
    hosts = set(_sites(_CADDY_STAGING))
    assert hosts == {"api.dev.metorite.com", "dev.metorite.com"}
    raw = _CADDY_STAGING.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))
    for name in ("app.metorite.com", "api.metorite.com", "operator.metorite.com",
                 "mcp.metorite.com", "\nmetorite.com"):
        assert name not in code, name


def test_the_staging_api_block_is_production_s_api_block() -> None:
    """Identity headers stripped, `/internal/*` closed. The staging data is
    shaped like production's, so a forged X-User-Email on staging is a leak."""
    assert _sites(_CADDY_STAGING)["api.dev.metorite.com"] == _sites(_CADDY)["api.metorite.com"]


def test_the_staging_app_block_is_production_s_app_block() -> None:
    assert _sites(_CADDY_STAGING)["dev.metorite.com"] == _sites(_CADDY)["app.metorite.com"]


def test_ci_validates_both_caddyfiles() -> None:
    body = _PR_CHECK.read_text(encoding="utf-8")
    assert "for f in Caddyfile Caddyfile.staging; do" in body


# ── 4. What the apply installs on staging ────────────────────────────────────


def test_staging_installs_its_own_caddyfile() -> None:
    body = _APPLY.read_text(encoding="utf-8")
    assert (
        '[ "$DEPLOY_ENV" = "staging" ] && '
        'CADDY_REPO="$APP_DIR/deploy/hostinger/caddy/Caddyfile.staging"'
    ) in body
    # The switch comes after the default and before the validate.
    assert body.index('CADDY_REPO="$APP_DIR/deploy/hostinger/caddy/Caddyfile"\n') < body.index(
        "Caddyfile.staging\"\n"
    ) < body.index('sudo caddy validate --config "$CADDY_REPO"')


def test_staging_runs_two_timers_and_disables_the_rest() -> None:
    body = _APPLY.read_text(encoding="utf-8")
    m = re.search(r'^staging_timers="([^"]+)"$', body, re.M)
    assert m, "the staging timer list is gone"
    timers = m.group(1).split()
    assert timers == ["acb-pull.timer", "acb-health-watchdog.timer"]
    for t in timers:
        assert (_ROOT / "deploy/hostinger" / t).exists(), t
    assert 'sudo systemctl disable --now "$st_name"' in body


def test_production_runs_h132_s_loop_and_only_staging_skips_it(tmp_path: pathlib.Path) -> None:
    """Run the real timer block with a recording `sudo`. Production must
    enable EVERY shipped timer and disable none (H-132)."""
    body = _APPLY.read_text(encoding="utf-8")
    start = body.index('staging_timers="')
    end = body.index("\nfi\n", body.index('for timer in "$APP_DIR"/deploy/hostinger/*.timer; do')) + 4
    block = body[start:end]
    shipped = sorted(p.name for p in (_ROOT / "deploy/hostinger").glob("*.timer"))
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "sudo").write_text('#!/bin/sh\necho "$*" >> "$SUDO_LOG"\n', encoding="utf-8")
    (stub / "sudo").chmod(0o755)
    for env in ("production", "staging"):
        log = tmp_path / f"{env}.log"
        res = subprocess.run(
            ["bash", "-c", f"APP_DIR={_ROOT}\nDEPLOY_ENV={env}\n" + block],
            capture_output=True, text=True, timeout=30,
            env={"PATH": f"{stub}:/usr/bin:/bin", "SUDO_LOG": str(log)},
        )
        assert res.returncode == 0, res.stderr
        calls = log.read_text().splitlines()
        enabled = sorted(c.split()[-1] for c in calls if c.startswith("systemctl enable --now"))
        disabled = sorted(c.split()[-1] for c in calls if c.startswith("systemctl disable --now"))
        if env == "production":
            assert enabled == shipped and disabled == []
        else:
            assert enabled == ["acb-health-watchdog.timer", "acb-pull.timer"]
            assert disabled == sorted(set(shipped) - set(enabled))


def test_staging_seeds_staging_hosts() -> None:
    body = _APPLY.read_text(encoding="utf-8")
    assert '_gw_default="https://api.dev.metorite.com"; _wb_default="https://dev.metorite.com"' in body
    assert 'echo "GATEWAY_PUBLIC_URL=$_gw_default"' in body


# ── 5. The watchdog probes its own box's host ────────────────────────────────


@pytest.mark.parametrize(
    "caddyfile, host",
    [(_CADDY, "api.metorite.com"), (_CADDY_STAGING, "api.dev.metorite.com")],
    ids=["production", "staging"],
)
def test_the_watchdog_reads_its_host_from_the_caddyfile(caddyfile: pathlib.Path, host: str) -> None:
    body = _WATCHDOG.read_text(encoding="utf-8")
    fn = body[body.index("watchdog_vhost() {") : body.index("\n}\n", body.index("watchdog_vhost() {")) + 3]
    res = subprocess.run(
        ["bash", "-c", fn + f'\nwatchdog_vhost "{caddyfile}"'],
        capture_output=True, text=True, timeout=30,
    )
    assert res.stdout.strip() == host


def test_the_watchdog_falls_back_to_production() -> None:
    body = _WATCHDOG.read_text(encoding="utf-8")
    assert 'CADDY_VHOST="${CADDY_VHOST:-api.metorite.com}"' in body


# ── 6. The workbench's copy of the setting (S-7) ─────────────────────────────


def _wb_block() -> str:
    body = _APPLY.read_text(encoding="utf-8")
    start = body.index('echo "==> Reconciling the workbench\'s DEPLOY_ENV')
    end = body.index("\nfi\n", start) + len("\nfi\n")
    return body[start:end]


@pytest.mark.parametrize(
    "env, before, after",
    [
        ("staging", "GATEWAY_INTERNAL_TOKEN=t\n", "GATEWAY_INTERNAL_TOKEN=t\nDEPLOY_ENV=staging\n"),
        ("staging", "A=1\nDEPLOY_ENV=production\nB=2\n", "A=1\nB=2\nDEPLOY_ENV=staging\n"),
        ("staging", "A=1\nDEPLOY_ENV=staging\n", "A=1\nDEPLOY_ENV=staging\n"),
        ("production", "A=1\nDEPLOY_ENV=staging\n", "A=1\n"),
        ("production", "A=1\n", "A=1\n"),
    ],
    ids=["staging-adds", "staging-replaces", "staging-unchanged",
         "production-removes-a-stray-copy", "production-untouched"],
)
def test_the_workbench_gets_the_setting_from_the_root_env(
    tmp_path: pathlib.Path, env: str, before: str, after: str
) -> None:
    """The band reads `.env.local`. A staging box with the line only in the
    root `.env` drew no band, and a stray copy on production would draw one."""
    wb = tmp_path / ".env.local"
    wb.write_text(before, encoding="utf-8")
    wb.chmod(0o600)
    prog = "\n".join(["set -e", f"WB_ENV={wb}", f"DEPLOY_ENV={env}", _wb_block()])
    res = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    assert wb.read_text(encoding="utf-8") == after
    assert (wb.stat().st_mode & 0o777) == 0o600, "the secrets file lost its mode"
    assert not (tmp_path / ".env.local.tmp").exists()


def test_the_copy_runs_before_the_workbench_build() -> None:
    body = _APPLY.read_text(encoding="utf-8")
    assert body.index("Reconciling the workbench's DEPLOY_ENV") < body.index(
        'echo "==> Rebuilding + restarting workbench (Next.js)"'
    )
