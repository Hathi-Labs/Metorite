"""HANDOFF H-358 (WS-61) — the root script that sets a flag on the staging box.

``deploy/hostinger/staging/acb-staging-flag`` is the one door through which the
``dev`` account changes ``/opt/acb/app/.env`` on the staging box, through one
sudoers line. Each rule in its header has a test here.

Every test drives the script on a temp env file through
``ACB_STAGING_FLAG_ENV_FILE``, and passes ``--no-restart``. Under that override
the script restarts nothing and writes no syslog line. The fixed path
``/opt/acb/app/.env`` is never read here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "hostinger" / "staging" / "acb-staging-flag"
BASH = shutil.which("bash")

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash is not installed"),
    pytest.mark.skipif(os.name != "posix", reason="the script runs on the Linux box only"),
    pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else False,
                       reason="as root the script ignores the override and uses /opt/acb/app/.env"),
]

SECRET = "sk-live-do-not-print-0123456789"


def _env_file(tmp_path: Path, body: str) -> Path:
    f = tmp_path / ".env"
    f.write_text(body, encoding="utf-8")
    f.chmod(0o600)
    return f


def _run(env_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(env_file.parent),
        "ACB_STAGING_FLAG_ENV_FILE": str(env_file),
    }
    return subprocess.run(
        [BASH or "bash", str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _lines(f: Path) -> list[str]:
    return f.read_text(encoding="utf-8").splitlines()


def _backups(f: Path) -> list[Path]:
    return sorted(f.parent.glob(f.name + ".bak-*"))


STAGING = "DEPLOY_ENV=staging\n"


# ── The script itself ────────────────────────────────────────────────────────


def test_the_script_is_executable_bash() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert body.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in body
    assert os.access(SCRIPT, os.X_OK), "git must track the script as mode 755"
    assert subprocess.run([BASH or "bash", "-n", str(SCRIPT)]).returncode == 0


# ── Rule 1: staging only ─────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [
    "",                                        # no DEPLOY_ENV line
    "DEPLOY_ENV=production\n",
    "DEPLOY_ENV=staging\nDEPLOY_ENV=production\n",   # the last line wins
    'DEPLOY_ENV="staging"\n',                  # quoted
    "DEPLOY_ENV=staging \n",                   # a trailing space
    "DEPLOY_ENV=Staging\n",
    "export DEPLOY_ENV=staging\n",             # odd forms, as in vps_apply.sh
    "DEPLOY_ENV = staging\n",
    " DEPLOY_ENV=staging\n",
    "DEPLOY_ENV=staging\nexport DEPLOY_ENV=staging\n",
])
def test_it_refuses_unless_the_file_says_exactly_staging(tmp_path: Path, body: str) -> None:
    f = _env_file(tmp_path, body + "KNOWLEDGE_ENABLED=false\n")
    before = f.read_bytes()
    for args in (("--list",), ("--no-restart", "set", "KNOWLEDGE_ENABLED", "true")):
        res = _run(f, *args)
        assert res.returncode == 2, (body, args, res.stderr)
        assert "refused" in res.stderr
        assert res.stdout == ""
    assert f.read_bytes() == before
    assert _backups(f) == []


def test_it_refuses_a_missing_file(tmp_path: Path) -> None:
    res = _run(tmp_path / "absent.env", "--list")
    assert res.returncode == 2 and "does not exist" in res.stderr


def test_it_refuses_an_unreadable_file(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    f.chmod(0o000)
    try:
        res = _run(f, "--list")
    finally:
        f.chmod(0o600)
    assert res.returncode == 2 and "cannot be read" in res.stderr


def test_it_refuses_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.env"
    real.write_text(STAGING, encoding="utf-8")
    link = tmp_path / ".env"
    link.symlink_to(real)
    res = _run(link, "--list")
    assert res.returncode == 2 and "symlink" in res.stderr


def test_a_crlf_staging_line_counts(tmp_path: Path) -> None:
    f = _env_file(tmp_path, "DEPLOY_ENV=staging\r\nKNOWLEDGE_ENABLED=true\r\n")
    res = _run(f, "--list")
    assert res.returncode == 0, res.stderr
    assert res.stdout == "KNOWLEDGE_ENABLED=true\n"


# ── Rule 2: the file path ────────────────────────────────────────────────────


def test_root_ignores_the_override() -> None:
    """A real euid 0 would read /opt/acb/app/.env, which the tests must never
    touch. So this reads the code path: the override is taken only when the
    euid is not 0, and the fixed path is the default."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "readonly FIXED_ENV_FILE=/opt/acb/app/.env" in body
    assert 'ENV_FILE="$FIXED_ENV_FILE"' in body
    guard = re.search(
        r'if \[ "\$EUID" -ne 0 \] && \[ -n "\$\{ACB_STAGING_FLAG_ENV_FILE:-\}" \]; then\n'
        r'  ENV_FILE="\$ACB_STAGING_FLAG_ENV_FILE"\n'
        r'  TEST_MODE=1\n'
        r'fi\n', body)
    assert guard, "the override must sit behind the EUID check"
    # The guard is the ONLY place that reads the override.
    assert body.count("ACB_STAGING_FLAG_ENV_FILE") == body.count(
        "ACB_STAGING_FLAG_ENV_FILE", 0, guard.end()), "the override is read outside the guard"
    # Nothing assigns ENV_FILE after the guard.
    assert not re.search(r"^\s*ENV_FILE=", body[guard.end():], re.M)


def test_the_override_restarts_nothing() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'if [ "$RESTART" = 0 ] || [ "$TEST_MODE" = 1 ]; then' in body
    restart = body.index("systemctl restart")
    assert body.index('if [ "$RESTART" = 0 ] || [ "$TEST_MODE" = 1 ]; then') < restart


# ── set, --list, unset ───────────────────────────────────────────────────────


def test_set_appends_replaces_and_collapses_duplicates(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING
                  + "OPENAI_API_KEY=" + SECRET + "\n"
                  + "KNOWLEDGE_ENABLED=false\n"
                  + "FOO=1\n"
                  + "KNOWLEDGE_ENABLED=false\n"
                  + "KNOWLEDGE_ENABLED=other\n")
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true", "KNOWLEDGE_ORGS", "*")
    assert res.returncode == 0, res.stderr
    assert _lines(f) == [
        "DEPLOY_ENV=staging",
        "OPENAI_API_KEY=" + SECRET,
        "KNOWLEDGE_ENABLED=true",   # replaced in place, the two copies gone
        "FOO=1",
        "KNOWLEDGE_ORGS=*",         # appended
    ]
    res = _run(f, "--list")
    assert res.returncode == 0, res.stderr
    assert res.stdout == "KNOWLEDGE_ENABLED=true\nKNOWLEDGE_ORGS=*\n"


def test_no_restart_may_come_last(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "set", "COMPANY_SEARCH_ENABLED", "true", "--no-restart")
    assert res.returncode == 0, res.stderr
    assert "COMPANY_SEARCH_ENABLED=true" in _lines(f)


def test_set_keeps_the_inode_and_mode(tmp_path: Path) -> None:
    """deploy/AGENTS.md, WS-49 BH-2: a writer of .env never replaces its inode."""
    f = _env_file(tmp_path, STAGING)
    ino = f.stat().st_ino
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true")
    assert res.returncode == 0, res.stderr
    assert f.stat().st_ino == ino
    assert f.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".env-edit.*")), "a temp file stayed behind"


def test_unset_removes_every_line_of_the_key(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING
                  + "KNOWLEDGE_ORGS=a\nFOO=1\nKNOWLEDGE_ORGS=b\nCRM_ORGS=x\n")
    res = _run(f, "--no-restart", "unset", "KNOWLEDGE_ORGS", "CRM_ORGS")
    assert res.returncode == 0, res.stderr
    assert _lines(f) == ["DEPLOY_ENV=staging", "FOO=1"]
    assert _run(f, "--list").stdout == ""


def test_an_unchanged_value_writes_nothing(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING + "KNOWLEDGE_ENABLED=true\n")
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true")
    assert res.returncode == 0, res.stderr
    assert "no change" in res.stdout
    assert _backups(f) == []


def test_a_key_that_is_a_prefix_of_another_is_its_own(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING + "CRM_ORGS=a\nCRM_ORGS_EXTRA=b\n")
    res = _run(f, "--no-restart", "unset", "CRM_ORGS")
    assert res.returncode == 0, res.stderr
    assert _lines(f) == ["DEPLOY_ENV=staging", "CRM_ORGS_EXTRA=b"]


# ── Rule 3: the key allowlist and the deny list ──────────────────────────────


@pytest.mark.parametrize("key", [
    "OPENAI_API_KEY", "GATEWAY_INTERNAL_TOKEN", "DATABASE_URL", "DEPLOY_ENV",
    "ACB_MASTER_KEY", "AUTH_SECRET", "POSTGRES_PASSWORD", "PGPASSWORD", "PGHOST",
    "GATEWAY_PORT", "SENTRY_DSN",
    # Deny wins over the allowlist.
    "KNOWLEDGE_API_KEY", "SECRET_ENABLED", "AUTH_ORGS", "WEBHOOK_URL_ENABLED",
    "DATABASE_ENABLED", "PRIVATE_ORGS", "CRED_MEMBERS",
    # The outbound switches stay off on staging (§5.10 item 3).
    "EMAIL_SYNC_ENABLED", "WHATSAPP_BRIDGE_ENABLED", "MEETING_BOT_ENABLED",
    "REACH_ENABLED", "MEMBER_INVITE_EMAIL_ENABLED", "WORKFLOW_SCHEDULER_ENABLED",
    "REACH_ORGS",
])
def test_the_deny_list_refuses(tmp_path: Path, key: str) -> None:
    f = _env_file(tmp_path, STAGING)
    for args in (("set", key, "true"), ("unset", key)):
        res = _run(f, "--no-restart", *args)
        assert res.returncode == 2, (key, args, res.stdout)
        assert "refused" in res.stderr
    assert _lines(f) == ["DEPLOY_ENV=staging"]
    assert _backups(f) == []


@pytest.mark.parametrize("key", [
    "COPILOT_CHAT_MODEL", "PG_MODE", "FOO", "NEXT_PUBLIC_MY_DAY_FEED",
    "SKIP_PRE_MIGRATION_BACKUP", "REDIS_DB", "X",          # X is too short
    "knowledge_enabled", "1_ENABLED", "KNOWLEDGE-ENABLED", "A" * 65 + "_ENABLED",
])
def test_a_key_outside_the_allowlist_is_refused(tmp_path: Path, key: str) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", key, "1")
    assert res.returncode == 2, (key, res.stdout)
    assert _lines(f) == ["DEPLOY_ENV=staging"]


@pytest.mark.parametrize("key", [
    "KNOWLEDGE_ENABLED", "KNOWLEDGE_ORGS", "KNOWLEDGE_EMBED", "COMPANY_SEARCH_ENABLED",
    "CRM_ORGS", "MY_DAY_DECIDE_ORGS", "AGENT_REGISTRY_ORGS", "MY_DAY_LOOP_ENABLED",
    "NEXT_PUBLIC_SHOW_PREVIEW_APPS", "NEXT_PUBLIC_TASKS_ENABLED", "MY_DAY_PROVIDERS",
    "NARROWING_AGENTS", "DECIDE_ENABLED", "DECIDE_FEATURE_MODES", "DECIDE_FEATURE_ORGS",
])
def test_an_allowlisted_key_is_written(tmp_path: Path, key: str) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", key, "on")
    assert res.returncode == 0, (key, res.stderr)
    assert f"{key}=on" in _lines(f)


def test_one_bad_key_changes_nothing(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true", "OPENAI_API_KEY", "x")
    assert res.returncode == 2
    assert _lines(f) == ["DEPLOY_ENV=staging"]


# ── Rule 4: the value ────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    "a b", '"true"', "'true'", "$(x)", "${HOME}", "`id`", "a\nDEPLOY_ENV=production",
    "a;b", "a|b", "a\\b", "a=b", "x" * 513, "\t",
])
def test_a_bad_value_is_refused(tmp_path: Path, value: str) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ORGS", value)
    assert res.returncode == 2, (value, res.stdout)
    assert _lines(f) == ["DEPLOY_ENV=staging"]
    assert value not in res.stderr


@pytest.mark.parametrize("value", [
    "", "*", "true", "0", "org-a,org-b", "a@b.co", "email.triage:shadow,my_day.x:on",
    "x" * 512, "+1/2",
])
def test_a_good_value_is_written(tmp_path: Path, value: str) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ORGS", value)
    assert res.returncode == 0, (value, res.stderr)
    assert _lines(f) == ["DEPLOY_ENV=staging", f"KNOWLEDGE_ORGS={value}"]


# ── --list leaks nothing ─────────────────────────────────────────────────────


def test_list_prints_no_other_line(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING
                  + "OPENAI_API_KEY=" + SECRET + "\n"
                  + "DATABASE_URL=postgresql://u:" + SECRET + "@h/db\n"
                  + "# KNOWLEDGE_ENABLED=commented\n"
                  + "export KNOWLEDGE_ORGS=" + SECRET + "\n"
                  + "KNOWLEDGE_API_KEY=" + SECRET + "\n"
                  + "EMAIL_SYNC_ENABLED=false\n"
                  + "KNOWLEDGE_ENABLED=true\n"
                  + "garbage line\n")
    res = _run(f, "--list")
    assert res.returncode == 0, res.stderr
    assert res.stdout == "KNOWLEDGE_ENABLED=true\n"
    assert SECRET not in res.stdout + res.stderr


def test_set_never_prints_the_file(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING + "OPENAI_API_KEY=" + SECRET + "\n")
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true")
    assert res.returncode == 0
    assert SECRET not in res.stdout + res.stderr


# ── Rule 5: the backup ───────────────────────────────────────────────────────


def test_each_write_makes_a_backup(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING + "OPENAI_API_KEY=" + SECRET + "\n")
    first = f.read_text(encoding="utf-8")
    assert _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true").returncode == 0
    assert _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "false").returncode == 0
    assert _run(f, "--no-restart", "unset", "KNOWLEDGE_ENABLED").returncode == 0
    baks = _backups(f)
    assert len(baks) == 3, baks
    for b in baks:
        assert re.fullmatch(r"\.env\.bak-\d{8}T\d{6}Z(-\d+)?", b.name), b.name
        assert b.stat().st_mode & 0o777 == 0o600, "cp -p keeps the mode"
    contents = {b.read_text(encoding="utf-8") for b in baks}
    assert first in contents
    assert first + "KNOWLEDGE_ENABLED=true\n" in contents
    assert first + "KNOWLEDGE_ENABLED=false\n" in contents


# ── Rule 6: the restart ──────────────────────────────────────────────────────


def test_the_restart_picks_the_unit_and_waits_for_health() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_*) wb=1 ;;" in body
    assert '[ "$gw" = 1 ] && units+=(acb-gateway)' in body
    assert '[ "$wb" = 1 ] && units+=(acb-workbench)' in body
    assert "readonly HEALTH_URL=http://127.0.0.1:8080/health" in body
    assert "readonly HEALTH_WAIT_S=90" in body
    # A curl exit code must not end the wait loop under set -e.
    assert re.search(r"code=\"\$\(curl [^\n]*\|\| true\)\"", body)
    assert body.rstrip().splitlines()[-1].startswith("fail "), "no 200 must exit non-zero"


def test_a_next_public_key_warns_that_a_restart_is_not_enough(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "NEXT_PUBLIC_SHOW_PREVIEW_APPS", "1")
    assert res.returncode == 0, res.stderr
    assert ".env.local" in res.stderr and "WARNING" in res.stderr


# ── Rule 7: the log ──────────────────────────────────────────────────────────


def test_the_log_names_keys_and_never_values(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ORGS", "org-zz-unique", "CRM_ORGS", "*")
    assert res.returncode == 0, res.stderr
    log = [ln for ln in res.stderr.splitlines() if "log:" in ln]
    assert len(log) == 1
    assert "set KNOWLEDGE_ORGS CRM_ORGS" in log[0] and "user=" in log[0]
    assert "org-zz-unique" not in res.stdout + res.stderr
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'who="${SUDO_USER:-$(id -un)}"' in body
    assert 'logger -t acb-staging-flag -- "$msg"' in body


# ── Usage ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [
    (), ("bogus",), ("set",), ("set", "KNOWLEDGE_ENABLED"),
    ("set", "KNOWLEDGE_ENABLED", "true", "CRM_ORGS"), ("unset",), ("--list", "extra"),
])
def test_bad_usage_exits_2(tmp_path: Path, args: tuple[str, ...]) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, *args)
    assert res.returncode == 2, (args, res.stdout)
    assert _lines(f) == ["DEPLOY_ENV=staging"]
