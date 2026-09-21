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

from pathlib import Path

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
