"""The nightly staging export: what it writes, and what it never writes.

WS-38 T-9, ``project-docs/specs/staging_and_daily_release.md`` §5.11.
``scripts/staging_export.sh`` runs on the production box, inside the nightly
backup's verify step, against the throwaway container that holds the restore.

R8: the behavioural tests run the real script against a production-SHAPED
clone (the ladder plus the H3 phases). A stand-in ``docker`` forwards
``docker exec <ctr> psql|pg_dump …`` to the local client and that clone.

What it fences:

1. Off by default: with no STAGING_EXPORT_SLUGS it writes nothing.
2. The dump holds the kept organizations only.
3. ``flags.env`` holds feature switches only. No secret, no URL, no odd value.
4. The files are group-readable and not world-readable, with checksums.
5. A refusal by ``minimise.sql`` leaves the previous export in place.
6. The backup calls it after the verify, and its failure never fails the backup.
"""

from __future__ import annotations

import grp
import os
import pathlib
import subprocess

import pytest

from tests.unit.test_h3_rls_promotion_rehearsal import _DB_GATE, promoted  # noqa: F401
from tests.unit.test_staging_minimise_r8 import clone  # noqa: F401

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_EXPORT = _ROOT / "scripts/staging_export.sh"
_BACKUP = _ROOT / "scripts/backup_db.sh"

_STUB = """#!/bin/bash
# docker exec [-i] <ctr> <psql|pg_dump> [-U u] [-d db] args... -> local client on $STUB_DSN
[ "$1" = exec ] || exit 99
shift
[ "$1" = -i ] && shift
shift
cmd="$1"; shift
args=()
while [ $# -gt 0 ]; do
  case "$1" in -U|-d) shift 2 ;; *) args+=("$1"); shift ;; esac
done
exec "$cmd" "$STUB_DSN" "${args[@]}"
"""


def _group() -> str:
    return grp.getgrgid(os.getgid()).gr_name


def _run(tmp: pathlib.Path, dsn: str, slugs: str | None) -> subprocess.CompletedProcess:
    stub = tmp / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "docker").write_text(_STUB, encoding="utf-8")
    (stub / "docker").chmod(0o755)
    app = tmp / "app"
    (app / "workbench/control_plane").mkdir(parents=True, exist_ok=True)
    (app / ".env").write_text(
        "DEPLOY_ENV=production\nEMAIL_SYNC_ENABLED=true\nTASKS_LENS=on\n"
        "ACB_MASTER_KEY=root-secret-value\nGATEWAY_INTERNAL_TOKEN=tok\n"
        "REACH_ENABLED=true\nODD_ENABLED=a b c\nDATABASE_URL=postgres://x\n",
        encoding="utf-8")
    (app / "workbench/control_plane/.env.local").write_text(
        "NEXT_PUBLIC_SHELL_BAR=1\nAUTH_SECRET=wb-secret\nNEXT_PUBLIC_API_KEY_ENABLED=x\n"
        "MEMBER_INVITE_EMAIL_ENABLED=true\nRESEND_API_KEY=re_live\n", encoding="utf-8")
    env = {"PATH": f"{stub}:{os.environ['PATH']}", "STUB_DSN": dsn}
    if slugs is not None:
        env["STAGING_EXPORT_SLUGS"] = slugs
    return subprocess.run(
        ["bash", str(_EXPORT), "verify-ctr", "verify", str(tmp / "out"), str(app), _group()],
        capture_output=True, text=True, timeout=300, env=env)


# ── Structural: every platform ───────────────────────────────────────────────


def test_the_backup_calls_it_after_the_verify_and_never_fails_on_it() -> None:
    body = _BACKUP.read_text(encoding="utf-8")
    verified = body.index('echo "    restore verified"')
    call = body.index('staging_export.sh" "$verify_ctr" verify; then')
    removed = body.index("if remove_verify_ctr; then")
    assert verified < call < removed, "export from the verified container, before it goes"
    block = body[call - 200: call + 200]
    assert 'if [ -n "${STAGING_EXPORT_SLUGS:-}" ]; then' in block
    assert "The backup is unaffected." in body[call: call + 300]


def test_the_name_survives_the_clean_env_and_reaches_root_env() -> None:
    body = _BACKUP.read_text(encoding="utf-8")
    keep = body[body.index("offbox_clean_env_reexec"): body.index('-- "$@"')]
    assert "STAGING_EXPORT_SLUGS" in keep
    names = (_ROOT / "deploy/hostinger/root_env_names.txt").read_text(encoding="utf-8").split()
    assert "STAGING_EXPORT_SLUGS" in names


def test_the_root_copy_carries_the_script_and_its_sql() -> None:
    lines = (_ROOT / "deploy/hostinger/root_lib_files.txt").read_text(encoding="utf-8").splitlines()
    assert "scripts/staging_export.sh staging_export.sh" in lines
    assert "infra/staging/minimise.sql staging_minimise.sql" in lines


def test_the_minimise_step_blanks_the_assistant_profile() -> None:
    """WS-63 PZ-9: the export carries no member's assistant profile. The
    behaviour is proven in ``test_staging_minimise_r8.py`` (R8)."""
    sql = (_ROOT / "infra/staging/minimise.sql").read_text(encoding="utf-8")
    body = sql.split("-- 6. Nothing credential-shaped is left.", 1)[0]
    assert "UPDATE public.user_settings           SET assistant_profile = NULL;" in body


def test_a_tenant_cannot_set_the_slugs() -> None:
    """The name decides whose data leaves the production box."""
    from acb_common.env_guard import is_platform_env

    assert is_platform_env("STAGING_EXPORT_SLUGS")


# ── Behavioural: R8 ──────────────────────────────────────────────────────────


@_DB_GATE
def test_off_by_default_writes_nothing(tmp_path: pathlib.Path, clone) -> None:  # noqa: F811
    res = _run(tmp_path, clone.dsn, None)
    assert res.returncode == 0, res.stderr
    assert "nothing is exported" in res.stdout
    assert not (tmp_path / "out").exists()


@_DB_GATE
def test_the_export_holds_the_kept_organization_and_safe_switches(
    tmp_path: pathlib.Path, clone  # noqa: F811
) -> None:
    res = _run(tmp_path, clone.dsn, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    cur = tmp_path / "out/current"
    files = sorted(p.name for p in cur.iterdir())
    assert files == ["SHA256SUMS", "extensions.txt", "flags.env", "roles.txt", "staging.dump"]
    for p in cur.iterdir():
        assert (p.stat().st_mode & 0o777) == 0o640, p.name
    check = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=cur, capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr

    orgs = subprocess.run(
        ["pg_restore", "-a", "-t", "organization", "-f", "-", str(cur / "staging.dump")],
        capture_output=True, text=True, check=True).stdout
    assert "h3rls-a" in orgs and "h3rls-b" not in orgs

    flags = (cur / "flags.env").read_text(encoding="utf-8").splitlines()
    assert sorted(flags) == sorted([
        "root:EMAIL_SYNC_ENABLED=true", "root:TASKS_LENS=on", "root:REACH_ENABLED=true",
        "workbench:NEXT_PUBLIC_SHELL_BAR=1", "workbench:MEMBER_INVITE_EMAIL_ENABLED=true",
    ]), flags
    joined = (cur / "flags.env").read_text(encoding="utf-8")
    for secret in ("root-secret-value", "wb-secret", "re_live", "tok", "postgres://"):
        assert secret not in joined, secret


@_DB_GATE
def test_a_refusal_keeps_the_previous_export(tmp_path: pathlib.Path, clone) -> None:  # noqa: F811
    good = _run(tmp_path, clone.dsn, "h3rls-a")
    assert good.returncode == 0, good.stdout + good.stderr
    before = (tmp_path / "out/current/SHA256SUMS").read_text(encoding="utf-8")
    bad = _run(tmp_path, clone.dsn, "no-such-org")
    assert bad.returncode == 1
    assert "REFUSED" in bad.stderr
    assert (tmp_path / "out/current/SHA256SUMS").read_text(encoding="utf-8") == before
    assert len(list((tmp_path / "out").glob("r.*"))) == 1, "a refused export left its scratch"
    assert (tmp_path / "out/current").is_symlink()


@pytest.mark.parametrize("slugs", ["Default", "a,,b", "a;rm -rf /", "a b"])
def test_a_malformed_slug_list_stops_before_any_work(tmp_path: pathlib.Path, slugs: str) -> None:
    res = _run(tmp_path, "postgresql://unused", slugs)
    assert res.returncode == 2
    assert not (tmp_path / "out").exists()


def test_a_symlinked_export_folder_refuses(tmp_path: pathlib.Path) -> None:
    """Root writes there. A link would point root's chmod and mv somewhere else."""
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "out").symlink_to(tmp_path / "elsewhere")
    res = _run(tmp_path, "postgresql://unused", "h3rls-a")
    assert res.returncode == 2
    assert "symlink" in res.stderr
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_the_export_is_capped_in_time() -> None:
    body = _BACKUP.read_text(encoding="utf-8")
    assert 'timeout -k 30 600 bash "$(dirname "${BASH_SOURCE[0]}")/staging_export.sh"' in body
