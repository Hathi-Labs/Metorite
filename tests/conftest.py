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
