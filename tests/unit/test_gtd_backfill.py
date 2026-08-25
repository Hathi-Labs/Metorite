"""WS-39 S3b/S3c fences — the `gtd_*` retirement stays safe to deploy.

Board **WS-39** · decisions **D53.5** (three releases), **D53.6** (what
survives) · spec `project_management_app.md` §12.8 · gate `work_plan.md` §6 (f).

These are STRUCTURAL fences over the two migration files, read as text. The
behaviour is proven separately and against a real database — `tests/live/
live_ws39_s3b.sql` (34 checks, two-org) and `live_ws39_s3c.sql` (22 checks,
every refusal path). R8 is explicit that a hermetic fake agrees with whatever
SQL it is handed, so nothing here asserts what the SQL *does*.

What it does defend is the property that makes these files safe to MERGE, which
no live test can express: that applying them changes no data and drops no table.
Running the move is OWNER-GATE. A future edit that made the ladder run it would
execute that gate unattended, on a deploy, before services restart (R6) — and it
would look like a one-line change.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
S3B = MIGRATIONS / "189_gtd_backfill_to_pm.sql"
S3C = MIGRATIONS / "190_gtd_retirement_drop.sql"


def sql(path: Path) -> str:
    """The file with ``--`` comments stripped.

    Essential rather than tidy: both headers quote the very commands the tests
    below forbid, because a runbook belongs next to the thing it runs. Without
    stripping, every one of these fences would fail on its own documentation.
    """
    text = io.open(path, encoding="utf-8").read()
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


def test_both_migrations_exist() -> None:
    # Guards against the whole suite passing vacuously if a file is renamed.
    assert S3B.is_file(), f"missing {S3B}"
    assert S3C.is_file(), f"missing {S3C}"


# ── The property that keeps the owner-gate intact ───────────────────────────

def test_189_defines_the_backfill_but_never_calls_it() -> None:
    body = sql(S3B)
    assert "CREATE OR REPLACE FUNCTION gtd_backfill_to_pm" in body, (
        "189 should define the backfill function"
    )
    called = re.search(
        r"(SELECT|PERFORM)\s+\*?\s*(FROM\s+)?gtd_backfill_to_pm\s*\(", body
    )
    assert called is None, (
        "189 CALLS gtd_backfill_to_pm(). Running the move against a real "
        "database is OWNER-GATE (work_plan.md section 6 (f)); the deploy ladder "
        "applies migrations unattended, before services restart (R6). The "
        "migration must make the move POSSIBLE and leave a human to make it "
        "HAPPEN."
    )


def test_189_writes_no_task_rows_at_apply_time() -> None:
    """No top-level DML — every INSERT must live inside the function body."""
    body = sql(S3B)
    preamble = body[: body.index("CREATE OR REPLACE FUNCTION gtd_backfill_to_pm")]
    for verb in ("INSERT INTO pm_", "UPDATE gtd_items", "INSERT INTO gtd_items"):
        assert verb not in preamble, (
            f"189 runs `{verb}` at apply time — that is the data move happening "
            "on deploy rather than when the owner asks for it."
        )


def test_190_is_inert_until_two_independent_conditions_hold() -> None:
    body = sql(S3C)
    assert "gtd_retirement_arm" in body, "190 must check the arming table"
    assert "migrated_task_id IS NULL" in body, (
        "190 must check that every row was accounted for"
    )
    assert "RAISE EXCEPTION" in body, (
        "190 must REFUSE (not silently skip) when armed but unsafe — a quiet "
        "skip leaves somebody believing the retirement happened"
    )


def test_190_drops_without_cascade() -> None:
    """CASCADE would take unknown dependents with it, silently."""
    for match in re.finditer(r"DROP TABLE[^;]*;", sql(S3C), re.I):
        assert "CASCADE" not in match.group(0).upper(), (
            f"190 uses CASCADE: {match.group(0)!r}. A retirement should fail "
            "loudly on an unexpected dependent, not consume it."
        )


# ── D53.6 and its neighbours — what a sweep must not take ───────────────────

#: Every table sharing the `gtd_` prefix that is NOT part of this retirement,
#: with the authority that keeps it. The prefix is the only thing four unrelated
#: subsystems have in common, which is exactly why this list is written out.
KEEP = {
    "gtd_settings": "D53.6 — Calendar state, not a task row",
    "gtd_day_state": "D53.6 — Calendar state",
    "gtd_rollover_log": "D53.6 — Calendar state",
    "gtd_people": "the People directory (fetchPeople/createPerson)",
    "gtd_person_absences": "the People directory",
    "gtd_person_credentials": "the People directory",
    "gtd_person_resumes": "the People directory",
    "gtd_person_skills": "the People directory",
    "gtd_horizons": "WS-21 owns Horizons — DO-NOT-DISPATCH (work_plan.md section 4)",
    "gtd_reviews": "WS-18 owns Weekly Review; dead is not retired",
    "gtd_projects": "the LOCAL project tree — waits on S3a-client slice 5",
    "gtd_spaces": "the LOCAL project tree — waits on slice 5",
    "gtd_folders": "the LOCAL project tree — waits on slice 5",
    "gtd_contexts": "waits on slice 5",
    "gtd_attachments": "waits on slice 5",
}


@pytest.mark.parametrize("table,why", sorted(KEEP.items()))
def test_190_does_not_drop_the_tables_that_survive(table: str, why: str) -> None:
    dropped = re.findall(r"DROP TABLE\s+(?:IF EXISTS\s+)?(\w+)", sql(S3C), re.I)
    assert table not in dropped, (
        f"190 drops `{table}`, which must survive: {why}.\n"
        "D53.6 exists because a sweep of everything named `gtd_*` takes four "
        "unrelated subsystems with it."
    )


def test_190_drops_exactly_the_two_tables_s3b_replaced() -> None:
    dropped = {
        t.lower()
        for t in re.findall(r"DROP TABLE\s+(?:IF EXISTS\s+)?(\w+)", sql(S3C), re.I)
    }
    assert dropped == {"gtd_items", "gtd_waiting"}, (
        f"190 drops {sorted(dropped)}. S3b replaced exactly `gtd_items` "
        "(-> pm_tasks) and `gtd_waiting` (-> the pm_task_personal quartet, "
        "migration 188). Anything else is a different decision needing its own."
    )


def test_190_drops_s3b_scaffolding_before_the_table() -> None:
    """Measured, not predicted — this ordering failed once for real.

    ``gtd_backfill_plan`` selects from ``gtd_items``, so Postgres refuses to
    drop the table underneath it. The first S3c run died on exactly that, and it
    would otherwise have surfaced while ARMED and mid-cutover.
    """
    body = sql(S3C)
    assert "DROP VIEW" in body and "gtd_backfill_plan" in body, (
        "190 must drop the S3b preview view before dropping gtd_items"
    )
    assert body.index("gtd_backfill_plan") < body.index(
        "DROP TABLE IF EXISTS gtd_items"
    ), "the view must be dropped BEFORE the table it reads"


# ── Tenancy: a migration has no RLS to fall back on ─────────────────────────

def test_189_resolves_the_tenant_from_the_directory() -> None:
    """``organization_id`` must come from ``app_user``, never be inferred.

    §12.8's named failure is that a mis-mapped ``member_email`` publishes one
    member's private task into another's lens. ``pm_*`` RLS is bound at the
    ``get_db()`` seam; a migration runs as the database owner and bypasses it,
    so every tenant predicate has to be explicit in the SQL.
    """
    body = sql(S3B)
    assert re.search(r"JOIN\s+app_user\s+u\s+ON\s+lower\(u\.email\)", body), (
        "189 must resolve the owner by folded email against app_user — the "
        "globally-unique key (D-MT-1(a)) that makes the mapping a function "
        "rather than a guess"
    )
    assert "u.organization_id" in body, (
        "189 must take organization_id from the resolved app_user row"
    )
    assert "AND organization_id = v_owner.org" in body, (
        "189's personal-project lookup must carry an explicit tenant predicate. "
        "The gateway's `_load_personal_project` deliberately omits one and says "
        "why (RLS scopes the read) — neither reason protects a migration."
    )


def test_189_refuses_rather_than_guesses_an_owner() -> None:
    body = sql(S3B)
    assert "unmappable" in body, (
        "189 must mark rows it cannot resolve as `unmappable` rather than "
        "assigning them. `_uid` writes the literal 'anonymous' for an "
        "unauthenticated capture; handing that to somebody is the §12.8 failure."
    )
