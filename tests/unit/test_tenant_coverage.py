"""MT-1b — every application table is tenant-scoped, or exempt with a reason.

Spec: ``project-docs/specs/saas_multitenancy.md`` §1.3 / MT-1b · WS-29 · D15.

The point of a generated migration is that nobody hand-writes 143 policies and
omits ``FORCE`` on one of them. The point of *this* file is the other half: that
a table added next month is covered **without anyone remembering**, which is the
same by-construction discipline root ``AGENTS.md`` constraint 10 already applies
to authentication and ``test_db_engine_seam.py`` applies to connection pools.

Two layers, because they fail at different times:

* **Source-level (always runs).** Every table the numbered migrations create is
  either in the generator's ``EXEMPT`` map or will be scoped by the generated
  migration. Catches a new table the moment its migration lands, months before
  anyone deploys.
* **Database-level (skips without Postgres).** The live catalog actually carries
  the column, ``FORCE`` and a policy. Catches a migration that was written but
  never applied.

⚠️ **Both layers here match on the column NAME.** Neither asks what an existing
``organization_id`` references. Three CRM tables had one that pointed at the
customer company, not the tenant, until WS-53 CRM-T1 renamed it to
``company_id`` (2026-10-11). That is why
``gen_tenant_migration.HOMONYM_BLOCKED`` exists, and why the partition
assertions ("every table is in exactly one bucket") live in
``test_tenancy_boundary.py``, which matches the foreign key's target. This file
does not make that check, despite what the paragraph above sounds like.

⚠️ **The exemption map IS the security review.** Adding a name to
``gen_tenant_migration.EXEMPT`` takes a table out of tenant isolation. It is the
only legitimate way out — and therefore the only way an illegitimate one gets in.
Every entry carries a reason and a reviewer is expected to challenge it.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _generator():
    """Import ``scripts/gen_tenant_migration.py`` (not an installed package)."""
    path = _REPO / "scripts" / "gen_tenant_migration.py"
    spec = importlib.util.spec_from_file_location("gen_tenant_migration", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_tenant_migration"] = mod
    spec.loader.exec_module(mod)
    return mod


# ==========================================================================
# Source-level — always runs, no database.
# ==========================================================================
def test_every_table_is_scoped_or_exempt_with_a_reason() -> None:
    gen = _generator()
    tables = gen.discover_tables()
    assert len(tables) > 100, (
        "table discovery collapsed — the regex or the migration glob broke, and "
        "a coverage test that discovers nothing passes vacuously"
    )
    for name, reason in gen.EXEMPT.items():
        assert reason.strip(), f"{name} is exempt with no reason given"


def test_exemptions_are_deliberate_and_few() -> None:
    """A growing exemption list is how tenant isolation quietly stops meaning
    anything. This is a tripwire, not a hard limit — raise it *in a PR that
    explains why*, never to make a build green."""
    gen = _generator()
    assert len(gen.EXEMPT) <= 15, (
        f"{len(gen.EXEMPT)} exempt tables. Each one is a table with no tenant "
        "isolation. Justify the growth explicitly."
    )


def test_the_pending_ask_table_is_tenant_scoped() -> None:
    """WS-51 S2 (``chat_run_continuity.md`` §4 S2). ``chat_pending_ask`` holds
    the questions a run asks a member. It must never be exempt, and the
    committed policy phase must carry it."""
    gen = _generator()
    assert "chat_pending_ask" in gen.discover_tables()
    assert "chat_pending_ask" not in gen.EXEMPT
    assert "chat_pending_ask" not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert "ALTER TABLE chat_pending_ask FORCE  ROW LEVEL SECURITY" in policies


def test_the_crm_settings_table_is_tenant_scoped() -> None:
    """WS-53 CRM-T5 (``crm_platform.md`` §3.1, §16 Q3). ``crm_settings`` holds
    the CRM mode of an organization and who in it may open the CRM. It must
    never be exempt, and the committed policy phase must carry it."""
    gen = _generator()
    assert "crm_settings" in gen.discover_tables()
    assert "crm_settings" not in gen.EXEMPT
    assert "crm_settings" not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert "ALTER TABLE crm_settings FORCE  ROW LEVEL SECURITY" in policies


def test_the_integration_connections_table_is_tenant_scoped() -> None:
    """WS-53 CRM-Z2 (``crm_platform.md`` §5.3). ``integration_connections``
    holds the tokens of each connection of an organization. It must never be
    exempt, and the committed policy phase must carry it."""
    gen = _generator()
    assert "integration_connections" in gen.discover_tables()
    assert "integration_connections" not in gen.EXEMPT
    assert "integration_connections" not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert "ALTER TABLE integration_connections FORCE  ROW LEVEL SECURITY" in policies


def test_the_crm_sync_runs_table_is_tenant_scoped() -> None:
    """WS-53 CRM-Z4/Z5 (``crm_platform.md`` §5.3). ``crm_sync_runs`` is the
    run lock and the health record of the mirror sync. It must never be
    exempt, and the committed policy phase must carry it."""
    gen = _generator()
    assert "crm_sync_runs" in gen.discover_tables()
    assert "crm_sync_runs" not in gen.EXEMPT
    assert "crm_sync_runs" not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert "ALTER TABLE crm_sync_runs FORCE  ROW LEVEL SECURITY" in policies


@pytest.mark.parametrize(
    "table",
    ["crm_source_profiles", "crm_pipelines", "crm_pipeline_stages", "crm_views",
     "crm_module_records", "crm_field_rules", "crm_field_sections",
     "crm_convert_mappings"],
)
def test_the_crm_q1_q2_tables_are_tenant_scoped(table: str) -> None:
    """WS-53 CRM-Q1/Q2 (``crm_platform.md`` §18). The source profile and the
    deal pipelines hold one organization's data. None may be exempt, and the
    committed policy phase must carry each. CRM-Q6a adds the generic record
    store (§18.7)."""
    gen = _generator()
    assert table in gen.discover_tables()
    assert table not in gen.EXEMPT
    assert table not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies


def test_the_task_refs_table_is_tenant_scoped() -> None:
    """WS-57 TR-1 (``task_references.md`` §5). ``pm_task_refs`` holds the URLs
    of one organization's tasks. It must never be exempt, and the committed
    policy phase must carry it (R5)."""
    gen = _generator()
    assert "pm_task_refs" in gen.discover_tables()
    assert "pm_task_refs" not in gen.EXEMPT
    assert "pm_task_refs" not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert "ALTER TABLE pm_task_refs FORCE  ROW LEVEL SECURITY" in policies


@pytest.mark.parametrize("table", ["pm_agent_runs", "pm_agent_run_activities"])
def test_the_agent_run_tables_are_tenant_scoped(table: str) -> None:
    """WS-55 EA-12a (``external_agent_access.md`` §12.1, EA12-1). The runs of
    an outside agent and their activities hold one organization's work. Neither
    may be exempt, and the committed policy phase must carry each (R5)."""
    gen = _generator()
    assert table in gen.discover_tables()
    assert table not in gen.EXEMPT
    assert table not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies


def test_the_my_day_card_state_table_is_tenant_scoped() -> None:
    """WS-58 MD-2a (``my_day_feed.md`` §13.1). ``my_day_card_state`` holds what
    one member of one organization did with each card. It must never be
    exempt, and the committed policy phase must carry it (R5)."""
    gen = _generator()
    assert "my_day_card_state" in gen.discover_tables()
    assert "my_day_card_state" not in gen.EXEMPT
    assert "my_day_card_state" not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert "ALTER TABLE my_day_card_state FORCE  ROW LEVEL SECURITY" in policies


def test_the_my_day_judgments_table_is_tenant_scoped() -> None:
    """WS-58 MD-6a-1 (``my_day_feed.md`` §13.2). ``my_day_judgments`` holds
    what ``decide`` judged about one member's cards. It must never be exempt,
    and the committed policy phase must carry it (R5)."""
    gen = _generator()
    assert "my_day_judgments" in gen.discover_tables()
    assert "my_day_judgments" not in gen.EXEMPT
    assert "my_day_judgments" not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert "ALTER TABLE my_day_judgments FORCE  ROW LEVEL SECURITY" in policies


@pytest.mark.parametrize("table", ["reach_outbox", "reach_signal_log", "reach_requests"])
def test_the_reach_tables_are_tenant_scoped(table: str) -> None:
    """WS-59 RE-1a (``proactive_reach.md`` §7). The outbox holds the text
    Reach sent a member, and the log holds which cards it looked at. RE-5
    (§11.2) adds the requests of "Tell me on WhatsApp", which hold tenant
    text too. None may be exempt, and the committed policy phase must carry
    each (R5)."""
    gen = _generator()
    assert table in gen.discover_tables()
    assert table not in gen.EXEMPT
    assert table not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies


def test_the_fill_rank_table_is_tenant_scoped() -> None:
    """WS-53 CRM-Q17a-1 (``crm_platform.md`` §18.9.3 item 3). ``crm_field_fill``
    holds how full each field of one organization's CRM is. It must never be
    exempt, and the committed policy phase must carry it (R5)."""
    gen = _generator()
    assert "crm_field_fill" in gen.discover_tables()
    assert "crm_field_fill" not in gen.EXEMPT
    assert "crm_field_fill" not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert "ALTER TABLE crm_field_fill FORCE  ROW LEVEL SECURITY" in policies


@pytest.mark.parametrize(
    "table",
    ["kb_spaces", "kb_space_grants", "kb_pages", "kb_page_versions", "kb_links",
     "kb_chunks"],
)
def test_the_knowledge_tables_are_tenant_scoped(table: str) -> None:
    """WS-61 KB-1 and KB-3 (``knowledge_base.md`` §6.1, §12 and §14). The
    spaces, grants, pages, versions, links and the chunks of the index hold
    what one organization wrote down. None may be exempt, and the committed
    policy phase must carry each (R5)."""
    gen = _generator()
    assert table in gen.discover_tables()
    assert table not in gen.EXEMPT
    assert table not in gen.HOMONYM_BLOCKED
    policies = (_REPO / "infra" / "postgres" / "generated" / "04_policies.sql").read_text(
        encoding="utf-8",
    )
    assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies


def test_generated_policies_carry_all_four_load_bearing_clauses() -> None:
    """FORCE, WITH CHECK and the missing-ok flag each have an incident behind
    them (saas_multitenancy_implementation.md §1.1). Drop any one and the
    migration still applies cleanly while isolation is silently wrong."""
    gen = _generator()
    sql = gen.gen_policies(["some_table"])
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE  ROW LEVEL SECURITY" in sql, (
        "ENABLE alone leaves the table OWNER reading every tenant"
    )
    assert "USING      (organization_id" in sql
    assert "WITH CHECK (organization_id" in sql, (
        "without WITH CHECK a tenant can WRITE a row stamped with another "
        "tenant's id — USING only filters reads"
    )
    assert "current_setting('app.tenant_id', true)" in sql, (
        "the missing-ok flag must be true, or an unset GUC RAISES instead of "
        "returning NULL and every unconverted path 500s at once"
    )


def test_the_generated_set_on_disk_matches_the_tables_that_exist() -> None:
    """The committed phase files must name exactly the tables the ladder
    creates — no missing table, and **no name that is not a table at all**.

    Both halves have a measured incident behind them (2026-08-10):

    - `pm_intake` and `pm_task_watchers` (migrations 164/165) were absent from
      all four committed phase files. Promoting the set would have given them a
      tenant column and **no policy** — two Projects tables silently readable
      and writable across tenants while everything around them failed closed.
    - `04_policies.sql` carried ``ALTER TABLE if ENABLE ROW LEVEL SECURITY``.
      There is no table named ``if``: the generator regexed `CREATE TABLE`
      out of *comment prose* (a backticked ``CREATE TABLE IF NOT EXISTS`` in
      `143_access_request.sql`). That statement cannot run, and phase 4 is
      applied **by hand against production in a maintenance window**, so it
      would have aborted the promotion part-way through.

    This is the only test that reads the committed artifact rather than calling
    the generator, which is exactly why it catches a stale checkout.
    """
    generated = _REPO / "infra" / "postgres" / "generated" / "04_policies.sql"
    if not generated.is_file():
        pytest.skip("generated set not present in this checkout")

    gen = _generator()
    expected = set(gen.discover_tables()) - set(gen.EXEMPT) - set(gen.HOMONYM_BLOCKED)
    on_disk = set(
        re.findall(r"ALTER TABLE ([a-z_][a-z0-9_]*) ENABLE ROW LEVEL SECURITY",
                   generated.read_text(encoding="utf-8"))
    )

    phantom = sorted(on_disk - expected)
    assert not phantom, (
        f"{phantom} appear in the promotion script but are not tables the "
        "migrations create. Do not delete the lines — fix the generator and "
        "regenerate: `uv run python scripts/gen_tenant_migration.py`."
    )
    missing = sorted(expected - on_disk)
    assert not missing, (
        f"{missing} carry no RLS policy in the committed set — they would be "
        "cross-tenant readable after promotion. Regenerate: "
        "`uv run python scripts/gen_tenant_migration.py`."
    )


def test_add_column_phase_is_nullable_and_defaulted() -> None:
    """Phase 1 must not scan or lock — that is what makes it safe on a live box.
    And the DEFAULT is what means no INSERT statement in 209 gateway files
    changes (§1.3)."""
    gen = _generator()
    sql = gen.gen_add_columns(["some_table"])
    assert "ADD COLUMN IF NOT EXISTS organization_id UUID" in sql
    assert "DEFAULT current_setting('app.tenant_id', true)::uuid" in sql
    assert "NOT NULL" not in sql, (
        "phase 1 must stay nullable — NOT NULL here takes ACCESS EXCLUSIVE and "
        "scans the table, which is the phase-3 problem, not the phase-1 one"
    )


def test_constraint_phase_refuses_to_run_before_the_backfill() -> None:
    gen = _generator()
    sql = gen.gen_constraints(["some_table"])
    assert "RAISE EXCEPTION" in sql and "unowned rows" in sql


def test_generated_files_are_outside_the_replayed_sequence() -> None:
    """`apply_migrations.sh` replays `infra/postgres/[0-9]*.sql` on every deploy.

    MT-1b must NOT land there: phase 3 is ACCESS EXCLUSIVE across 135 tables and
    phase 4 is a cliff that dark-fails every unbound connection. Promoting it is
    a human act in a window — not a consequence of a file reaching main.
    """
    generated = _REPO / "infra" / "postgres" / "generated"
    if not generated.is_dir():
        pytest.skip("generator has not been run in this tree")
    for path in generated.glob("*.sql"):
        assert not path.name[0].isdigit() or path.parent.name == "generated"
    replayed = {p.name for p in (_REPO / "infra" / "postgres").glob("[0-9]*_*.sql")}
    assert not (replayed & {p.name for p in generated.glob("*.sql")})


# ==========================================================================
# Database-level — skips without Postgres. NEVER report a skip as a pass.
# ==========================================================================
def _db_ready() -> bool:
    # The launch-time snapshot (tests/conftest.py), not the live variable:
    # litellm's import-time load_dotenv() can plant a dev .env's DATABASE_URL
    # into os.environ mid-collection, which would point these tests at an
    # unmigrated local database instead of skipping. Fall back to the live
    # variable only when the snapshot was never taken (running outside pytest).
    snap = os.environ.get("_ACB_DATABASE_URL_AT_LAUNCH")
    if snap is not None:
        return bool(snap)
    return bool(os.environ.get("DATABASE_URL"))


_needs_db = pytest.mark.skipif(
    not _db_ready(),
    reason="needs a migrated Postgres (DATABASE_URL unset). ⚠️ This test is the "
           "only one that proves the migration was actually APPLIED — a green "
           "run without it proves the SQL was written, not that it works.",
)


@_needs_db
@pytest.mark.asyncio
async def test_live_catalog_has_column_force_and_policy() -> None:
    from acb_common.db import get_db
    from sqlalchemy import text

    gen = _generator()
    session = await get_db()
    try:
        rows = (await session.execute(text("""
            SELECT c.relname                AS table_name,
                   c.relrowsecurity         AS rls_enabled,
                   c.relforcerowsecurity    AS rls_forced,
                   EXISTS (SELECT 1 FROM information_schema.columns col
                            WHERE col.table_name = c.relname
                              AND col.column_name = 'organization_id') AS has_col,
                   EXISTS (SELECT 1 FROM pg_policies p
                            WHERE p.tablename = c.relname)             AS has_policy
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r'
        """))).mappings().all()
    finally:
        await session.close()

    bad = [
        r["table_name"] for r in rows
        if r["table_name"] not in gen.EXEMPT
        and not (r["has_col"] and r["rls_enabled"] and r["rls_forced"] and r["has_policy"])
    ]
    assert not bad, f"tables missing tenant scoping in the live catalog: {sorted(bad)}"


@_needs_db
@pytest.mark.asyncio
async def test_app_role_cannot_bypass_rls() -> None:
    """A superuser, a BYPASSRLS role, or the table owner all read every tenant.
    FORCE handles the owner; this catches the other two."""
    from acb_common.db import get_db
    from sqlalchemy import text

    session = await get_db()
    try:
        row = (await session.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ))).first()
    finally:
        await session.close()
    assert row is not None
    assert not row[0], "the app connects as a SUPERUSER — RLS does not apply to it"
    assert not row[1], "the app role has BYPASSRLS — RLS does not apply to it"
