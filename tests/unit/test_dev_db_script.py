"""`scripts/dev_db.sh` builds BOTH scratch databases, not only the Console's.

Spec: `specs/engineering_practice.md` §1.1 · root `CLAUDE.md` §6 (R8) · H-96.

🔴 **The script started the tenant database and never built it.** Its own
header promised the "mt-scratch pattern (:5433, full ladder applied)", and a
reader took the printed DSN as proof. Measured 2026-09-02: the Console database
got every file and the tenant database got ZERO tables. An R8 suite needing a
tenant table then failed — or skipped — against a database the script had just
called ready.

⚠️ These are STRUCTURAL. Whether the script works is answered by running it;
what cannot be answered that way is whether a later edit quietly removed a
step, because a script with no steps exits 0 just as happily as one with them.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev_db.sh"
BODY = SCRIPT.read_text(encoding="utf-8")


class TestItBuildsTheTenantDatabase:
    def test_it_touches_the_tenant_ladder_at_all(self):
        """H-96's own Check, kept as a test so it cannot silently reopen."""
        assert "infra/postgres" in BODY, (
            "dev_db.sh applies only the Console ladder again — the tenant "
            "database would start empty while the script says it is ready")

    def test_it_applies_the_base_schema(self):
        """⚠️ A replay of 02..209 onto a fresh container fails at 03, then at
        95. Both read as a broken migration. Neither is one."""
        assert "01_schema.sql" in BODY

    def test_the_base_schema_is_guarded_by_an_EMPTINESS_test(self):
        """🔴 `01_schema.sql` is what initdb lays down and is NOT re-runnable.

        `start_one` REUSES a running container, so an unguarded replay would
        fail on the first CREATE on every second invocation.
        """
        assert "information_schema.tables" in BODY, (
            "nothing distinguishes a fresh database from a built one")
        assert "tenant_tables" in BODY

    def test_it_creates_the_extensions(self):
        """`03_pending_commits.sql` wants `uuid_generate_v4()`."""
        assert 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"' in BODY

    def test_the_ladder_goes_through_the_SHARED_replayer(self):
        """🔴 Never a second loop written here.

        `apply_migrations.sh` already carries the numeric sort, the init-only
        skips and `ON_ERROR_STOP`. A plain glob puts `100_` before `10_` and
        the ladder dies at once, so a copy here would be a second opinion
        about the order of 209 files.
        """
        assert "apply_migrations.sh" in BODY
        # And it must not have grown its own loop over the ladder directory.
        assert "infra/postgres/[0-9]" not in BODY, (
            "dev_db.sh iterates the tenant ladder itself — use the replayer")

    def test_the_backup_is_skipped_for_the_SCRATCH_database_only(self):
        """⚠️ Correct here and nowhere else: a container that was empty a
        minute ago has nothing to restore."""
        assert "SKIP_PRE_MIGRATION_BACKUP=1" in BODY

    def test_it_resolves_the_repo_root_from_ITSELF(self):
        """`bash scripts/dev_db.sh` from a subdirectory must still find the
        replayer, so the path cannot come from the caller's cwd."""
        assert "BASH_SOURCE" in BODY

    def test_it_fails_LOUDLY_when_the_replay_fails(self):
        """A scratch database half built is the shape this whole entry is
        about. It must not print a DSN after that."""
        assert "the last 20 lines of the replay" in BODY


class TestItSaysWhatItDoesNotDo:
    def test_it_names_the_DATABASE_URL_gap(self):
        """📌 Two suites want `DATABASE_URL`, and this script does not set it.

        Setting it makes `test_tenant_coverage.py` run — and FAIL, because
        every table reports "missing tenant scoping". That is WS-29's
        unfinished RLS retrofit and not a broken setup, but it reads exactly
        like one. Twenty minutes of somebody's day, saved by four lines.
        """
        assert "DATABASE_URL" in BODY
        assert "tenant RLS" in BODY


class TestTheScratchDataLivesOnTmpfs:
    def test_both_containers_get_tmpfs_pgdata_unless_opted_out(self):
        """Task 2304: a commit on the WSL disk costs ~2.5 ms, on tmpfs ~0.05 ms.

        Fence: dropping the flag makes every box R8 run slow again.
        """
        assert "--tmpfs /var/lib/postgresql/data" in BODY
        assert "METORITE_SCRATCH_TMPFS" in BODY
        assert '"${tmpfs[@]}"' in BODY


# ── Task 4105: slots, so R8 runs go side by side ───────────────────────────
#
# These RUN the script. A copy sits in a temp tree with a fake `docker` first on
# PATH and a stub replayer, so no daemon and no database is touched. The fake
# records each call, and the test reads what the script would have done.

_FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_CALLS"
case "$1" in
    inspect) echo false ;;
    exec)
        case "$*" in
            *information_schema*) echo 0 ;;
            *ON_ERROR_STOP*) cat >/dev/null ;;
        esac
        ;;
esac
exit 0
"""

_STUB_REPLAYER = """#!/usr/bin/env bash
echo "replay $PG_CONTAINER" >> "$FAKE_CALLS"
echo "(1 applied, 0 already recorded)"
"""

_DSN = "export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:{}/cc_platform"
_TDSN = "export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:{}/acb_tenant"


def _run(tmp_path: Path, *args: str, **extra: str):
    """Run a copy of dev_db.sh. Returns (process, docker calls, log dir)."""
    root = tmp_path / "repo"
    for sub in ("scripts", "infra/customer_console", "infra/postgres", "bin", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "dev_db.sh")
    for name, body in (("scripts/apply_migrations.sh", _STUB_REPLAYER),
                       ("bin/docker", _FAKE_DOCKER),
                       ("infra/customer_console/001_a.sql", "SELECT 1;\n"),
                       ("infra/postgres/01_schema.sql", "SELECT 1;\n")):
        (root / name).write_text(body, encoding="utf-8", newline="\n")
    (root / "bin" / "docker").chmod(0o755)
    calls = root / "calls"
    calls.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("METORITE_SCRATCH_")}
    env.update(PATH=f"{root / 'bin'}{os.pathsep}{env['PATH']}", FAKE_CALLS=str(calls),
               METORITE_SCRATCH_LOG_DIR=str(root / "logs"), **extra)
    proc = subprocess.run(["bash", "scripts/dev_db.sh", *args], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    return proc, calls.read_text(encoding="utf-8").splitlines(), root / "logs"


def _started(calls: list[str]) -> list[tuple[str, str]]:
    """(container name, published port) of each `docker run`."""
    out = []
    for call in calls:
        words = call.split()
        if words[0] == "run":
            out.append((words[words.index("--name") + 1], words[words.index("-p") + 1]))
    return out


@pytest.mark.skipif(sys.platform == "win32", reason="the fake docker needs a POSIX PATH")
class TestSlots:
    def test_no_slot_is_exactly_todays_names_ports_and_dsns(self, tmp_path):
        proc, calls, logs = _run(tmp_path, "--export")
        assert proc.returncode == 0, proc.stderr
        assert _started(calls) == [
            ("metorite-scratch-console", "127.0.0.1:5433:5432"),
            ("metorite-scratch-tenant", "127.0.0.1:5434:5432"),
        ]
        assert proc.stdout.splitlines() == [_DSN.format(5433), _TDSN.format(5434)]
        assert "replay metorite-scratch-tenant" in calls
        assert (logs / "dev_db_err").is_file()
        assert (logs / "dev_db_tenant.log").is_file()

    def test_an_empty_slot_is_no_slot(self, tmp_path):
        proc, _, _ = _run(tmp_path, "--export", METORITE_SCRATCH_SLOT="")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines() == [_DSN.format(5433), _TDSN.format(5434)]

    @pytest.mark.parametrize("slot,cc,tenant", [(1, 5443, 5444), (2, 5453, 5454),
                                                (4, 5473, 5474)])
    def test_a_slot_has_its_own_names_ports_dsns_and_log(self, tmp_path, slot, cc, tenant):
        proc, calls, logs = _run(tmp_path, "--export", METORITE_SCRATCH_SLOT=str(slot))
        assert proc.returncode == 0, proc.stderr
        assert _started(calls) == [
            (f"metorite-scratch-console-s{slot}", f"127.0.0.1:{cc}:5432"),
            (f"metorite-scratch-tenant-s{slot}", f"127.0.0.1:{tenant}:5432"),
        ]
        assert proc.stdout.splitlines() == [_DSN.format(cc), _TDSN.format(tenant)]
        assert f"replay metorite-scratch-tenant-s{slot}" in calls
        for log in (f"dev_db_err-s{slot}-u{os.getuid()}",
                    f"dev_db_tenant-s{slot}-u{os.getuid()}.log"):
            assert (logs / log).is_file(), log
        # No call may reach the default pair, which another run may be using.
        for call in calls:
            words = call.split()
            assert "metorite-scratch-console" not in words, call
            assert "metorite-scratch-tenant" not in words, call

    def test_down_removes_only_the_slots_pair(self, tmp_path):
        proc, calls, _ = _run(tmp_path, "--down", METORITE_SCRATCH_SLOT="3")
        assert proc.returncode == 0, proc.stderr
        assert calls == ["rm -f metorite-scratch-console-s3 metorite-scratch-tenant-s3"]

    def test_down_with_no_slot_removes_the_default_pair(self, tmp_path):
        proc, calls, _ = _run(tmp_path, "--down")
        assert proc.returncode == 0, proc.stderr
        assert calls == ["rm -f metorite-scratch-console metorite-scratch-tenant"]

    def test_an_explicit_port_wins_over_the_slot(self, tmp_path):
        proc, calls, _ = _run(tmp_path, "--export", METORITE_SCRATCH_SLOT="2",
                              METORITE_SCRATCH_CONSOLE_PORT="6001",
                              METORITE_SCRATCH_TENANT_PORT="6002")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines() == [_DSN.format(6001), _TDSN.format(6002)]
        assert _started(calls) == [
            ("metorite-scratch-console-s2", "127.0.0.1:6001:5432"),
            ("metorite-scratch-tenant-s2", "127.0.0.1:6002:5432"),
        ]

    @pytest.mark.parametrize("bad", ["0", "10", "01", "a", "1 2", "-1"])
    @pytest.mark.parametrize("arg", ["--export", "--down"])
    def test_a_bad_slot_stops_before_any_docker_call(self, tmp_path, bad, arg):
        proc, calls, _ = _run(tmp_path, arg, METORITE_SCRATCH_SLOT=bad)
        assert proc.returncode == 2
        assert "METORITE_SCRATCH_SLOT" in proc.stderr
        assert calls == []
        assert proc.stdout == ""
