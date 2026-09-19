"""Shared fixtures for the tests/ tree (CI runs `pytest tests/unit/`)."""
from __future__ import annotations

import os

import pytest

# Snapshot DATABASE_URL before any test module imports. `import litellm`
# (reached through acb_llm by several test modules) calls load_dotenv() at
# import time, which copies a dev machine's .env DATABASE_URL into os.environ
# mid-collection — and the DB-gated tests in test_tenant_coverage.py would then
# run against whatever that value names instead of skipping. Those gates must
# answer to the environment pytest was LAUNCHED with, not to whichever module
# happened to import first. conftest.py imports before every test module, so
# this line runs ahead of any litellm import.
os.environ.setdefault("_ACB_DATABASE_URL_AT_LAUNCH", os.environ.get("DATABASE_URL", ""))

# The same idiom, one variable along (WS-31 CP-2b, customer_console.md §6(i)).
# `tests/unit/test_deployment_resolve_cache.py` is R8-gated on the TENANT
# database, which answers to TENANT_LADDER_DATABASE_URL and deliberately NEVER
# to DATABASE_URL: setting the latter for a `tests/unit/` run would arm
# test_tenant_coverage.py's two DB-gated tests, which fail by construction on a
# freshly-replayed ladder (one wants FORCE-RLS policies that live only in
# infra/postgres/generated/04_policies.sql, never replayed; the other wants a
# non-superuser app role). Both are WS-29 MT-1b/MT-1c's gates, not CP-2b's.
#
# One idiom, two variables; never a raw os.environ read at module scope. The
# snapshot exists for the identical reason the first one does — litellm's
# import-time load_dotenv() must not be able to point an R8 gate at whatever a
# dev machine's .env happens to name.
os.environ.setdefault(
    "_ACB_TENANT_LADDER_URL_AT_LAUNCH",
    os.environ.get("TENANT_LADDER_DATABASE_URL", ""),
)


@pytest.fixture(autouse=True)
def _isolate_write_artifact_context():
    """Snapshot and restore ``_WRITE_ARTIFACT_CONTEXT`` around every test.

    That dict is process-global state the executor populates per agent run
    (``session_id``, ``workspace_root``, ``integrations``) and that a dozen
    modules read — notably ``executor.resolve_run_queue``, which keys on
    ``session_id``. A test that populates it and does not restore it therefore
    leaks a live session into every test that runs afterwards, and the next test
    to touch that path blocks on a gateway call that never returns.

    The failure is order-dependent, so it stayed invisible: the tests that
    populate the global (test_write_artifact, test_share_artifact) happen to sort
    near the end of the run. Add one test file that sorts earlier and touches
    write_artifact and the whole suite hangs with no useful output. Rather than
    depend on filenames, isolate the global here so no test can leak it.
    """
    try:
        from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT
    except ImportError:  # acb_skills unavailable — nothing to isolate
        yield
        return
    snapshot = dict(_WRITE_ARTIFACT_CONTEXT)
    try:
        yield
    finally:
        _WRITE_ARTIFACT_CONTEXT.clear()
        _WRITE_ARTIFACT_CONTEXT.update(snapshot)


# ── R8: say out loud when the database-gated suites did not run ─────────────
#
# 🔴 **The hole this closes.** Measured 2026-08-30: a local `pytest` over the 26
# Console suites reported **123 passed, 843 skipped**, and every skip read "R8
# requires a REAL Postgres". Nothing about that run looked wrong. pytest prints
# the skip count in the same grey as everything else, and in a full-tree run it
# sits under 4,900 passes where nobody reads it. So the loop was: change SQL,
# run the suite, see green, push — and find out in CI, or in production.
#
# ⚠️ **ADVISORY, not a fence (R7).** This prevents nothing. It makes an
# invisible fact visible, which is a different and weaker thing, and it is
# labelled that way on purpose. The fence for R8 is CI, which sets both DSNs
# and fails when a test does.
#
# ⚠️ **Keyed on the ENVIRONMENT, never on the skip reasons.** Counting skips
# whose reason names the variable was the obvious design and it is a mirror:
# the 26 suites do NOT share one reason string (three of them phrase it
# differently), so the count would silently under-report the moment somebody
# wrote a twenty-seventh. Whether the variable is set is a fact with nothing to
# go stale.

_R8_VARS = ("CUSTOMER_CONSOLE_DATABASE_URL", "TENANT_LADDER_DATABASE_URL")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print what this run did NOT prove."""
    missing = [v for v in _R8_VARS if not os.environ.get(v, "").strip()]
    if not missing:
        return

    skipped = len(terminalreporter.stats.get("skipped", []))
    if skipped == 0:
        return

    w = terminalreporter.write_line
    terminalreporter.write_sep("=", "R8: what this run did not prove", yellow=True)
    w(f"{skipped} test(s) skipped. These are unset: {', '.join(missing)}")
    w("")
    w("Every suite that checks SQL against a real database was among them.")
    w("A hermetic fake agrees with whatever SQL it is handed, which is how")
    w("five live bugs shipped green (CLAUDE.md R8).")
    w("")
    w("  bash scripts/dev_db.sh                       # start the scratch DBs")
    w('  eval "$(bash scripts/dev_db.sh --export)"    # then re-run this suite')


# ── H-91: the scratch database must not grow without bound ──────────────────
#
# 🔴 **Every provisioning fixture leaked an organization per test.** About
# twenty of them post `/orgs/provision` with a fresh slug and remove nothing.
# Measured 2026-09-19 on one developer's scratch database: 7,766 organizations,
# 14,134 `control_audit` rows, 259 provider credentials.
#
# 🔴 **It is a MEASUREMENT defect, not a disk-space one.** The Operator Console
# reads the same database. Those rows drew 210 ghost tiers and 248 vendors onto
# the console's own pages, which stretched /providers to 25,000 pixels, and a
# page-size decision was nearly taken on a number the fixtures produced.
#
# ⚠️ **SESSION-scoped teardown, deliberately not per-test.** A per-test sweep
# would change what every existing test sees between cases, and several of the
# 26 suites build state across cases on purpose. This runs once, after the last
# test, so it changes no test's behaviour — it only stops the growth.
#
# ⚠️ **A DELETE on `organization` alone RAISES.** Most dependants cascade, but
# `payment_order`, `discount_code` and `discount_redemption` are NO ACTION and
# block, while `control_audit` and `provisioning_run` are SET NULL and would
# survive as orphans. The order below is that fact, written down.
#
# ⚠️ **The ids are CAST, and the sweep REPORTS its rowcount.** The first
# version passed a list of UUID objects to `= ANY(:ids)`, which matched
# nothing, and a DELETE that matches nothing raises nothing — so it ran
# happily and removed not one row. Silence was its only failure mode.
#
# ⚠️ **TWO CONCURRENT RUNS against one scratch database interfere**, and
# this sweep is one more way they do: it deletes every organization that
# appeared while it was running, which includes the other run's. Met on
# 2026-09-20, when a full `tests/unit/` run in another checkout was writing to
# the same database. Two runs already share `tier_catalog` and
# `provider_credential`, so they were never isolated -- give a second run its
# own database with `METORITE_SCRATCH_CONSOLE_PORT`.
#
# Set `METORITE_KEEP_SCRATCH=1` to inspect a run's rows afterwards.

#: Each is `(table, sql)`, in DEPENDENCY order -- children first.
#:
#: 🔴 **The order is the database's, not a guess.** Measured from
#: `pg_constraint` on 2026-09-20, after the first version deleted
#: `payment_order` before `discount_redemption` and the foreign key stopped it:
#:
#:   discount_redemption -> discount_code, organization, payment_order
#:   payment_order_line  -> payment_order
#:   payment_order       -> organization
#:   discount_code       -> organization
#:   control_audit       -> organization   (SET NULL, so it orphans)
#:   provisioning_run    -> organization   (SET NULL, so it orphans)
#:
#: ⚠️ **A redemption reaches my organizations by THREE paths.** Its own
#: column, the order it settled, and the code it spent. Deleting only by the
#: first leaves a row that then blocks the other two.
_IDS = "CAST(:ids AS uuid[])"
_SWEEP = (
    ("discount_redemption",
     "DELETE FROM discount_redemption WHERE "
     f"  organization_id = ANY({_IDS}) "
     f"  OR order_id IN (SELECT id FROM payment_order WHERE organization_id = ANY({_IDS})) "
     "  OR discount_code_id IN (SELECT id FROM discount_code "
     f"                         WHERE organization_id = ANY({_IDS}))"),
    ("payment_order_line",
     "DELETE FROM payment_order_line WHERE order_id IN ("
     f"  SELECT id FROM payment_order WHERE organization_id = ANY({_IDS}))"),
    ("payment_order",
     f"DELETE FROM payment_order WHERE organization_id = ANY({_IDS})"),
    ("discount_code",
     f"DELETE FROM discount_code WHERE organization_id = ANY({_IDS})"),
    ("control_audit",
     f"DELETE FROM control_audit WHERE organization_id = ANY({_IDS})"),
    ("provisioning_run",
     f"DELETE FROM provisioning_run WHERE organization_id = ANY({_IDS})"),
    ("organization",
     f"DELETE FROM organization WHERE id = ANY({_IDS})"),
)


@pytest.fixture(scope="session", autouse=True)
def _sweep_scratch_organizations():
    """Delete the organizations THIS run created, and nothing else.

    Keyed on the id set seen before the first test, so a row somebody staged by
    hand before running the suite survives. A truncate would be simpler and it
    would also destroy the thing a developer was looking at.
    """
    dsn = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()
    if not dsn or os.environ.get("METORITE_KEEP_SCRATCH", "").strip():
        yield
        return

    from sqlalchemy import create_engine, text

    eng = create_engine(dsn.replace("postgresql+asyncpg:", "postgresql+psycopg:"))
    try:
        with eng.begin() as c:
            before = {str(r[0]) for r in c.execute(text("SELECT id FROM organization"))}
    except Exception:
        # No database, no ladder, no sweep. A teardown must never be the
        # reason a suite fails to start.
        eng.dispose()
        yield
        return

    yield

    try:
        with eng.begin() as c:
            after = {str(r[0]) for r in c.execute(text("SELECT id FROM organization"))}
            mine = sorted(after - before)
            if not mine:
                return
            removed = {}
            for table, sql in _SWEEP:
                removed[table] = c.execute(text(sql), {"ids": mine}).rowcount
            left = c.execute(
                text("SELECT count(*) FROM organization WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": mine},
            ).scalar_one()
        # Reported, because a sweep that quietly does nothing is the bug this
        # replaces. The organization count is the one that has to reach zero.
        print(
            "\nH-91 sweep: this run created "
            f"{len(mine)} organization(s); removed "
            f"{removed.get('organization', 0)}, {left} left behind"
        )
        if left:
            print(f"  H-91 sweep INCOMPLETE — rows by table: {removed}")
    except Exception as exc:  # pragma: no cover - diagnostics only
        print(f"\nH-91 sweep could not run: {exc!r}")
    finally:
        eng.dispose()
