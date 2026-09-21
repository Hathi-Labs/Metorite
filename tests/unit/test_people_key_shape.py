"""WS-28a — gtd_people's key shape (People Center P-1/P-2).

Spec: `project-docs/specs/people_center_app.md` §2, §5, §7.

Migration 49 made `name` UNIQUE and left `email` unconstrained. That is
backwards for a directory that has to join on email: two real people cannot
share a name (they do), and nothing stopped two rows carrying the same address —
so an email→person join could silently attribute one person's capacity to
another. This fixes the shape *before* the People Center makes this table the
assignment source.

**Why the migration half is static.** `infra/postgres/README.md` requires every
`02+` migration to be idempotent because `apply_migrations.sh` replays the whole
ladder on every deploy — and it runs under `set -euo pipefail` + `ON_ERROR_STOP=1`,
so a statement that fails against real data stops the deploy. Reading the file is
what makes both properties enforceable in CI rather than "by inspection".

The file is found by CONTENT, not by number: R1 forbids writing an absolute
future migration number anywhere, so a test pinned to `148_` would be the same
class of mistake in a different file.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "infra" / "postgres"


@pytest.fixture(scope="module")
def sql() -> str:
    """The key-shape migration's STATEMENTS — comments stripped.

    Stripping is not tidiness. This file's header explains at length why the
    CHECK is `NOT VALID` and why a conflicting address is quarantined, so a
    naive `"NOT VALID" in sql` passes on the *prose* even after the keyword is
    deleted from the statement — which is exactly the mutant that survived the
    first time this file was written. Assertions must read what Postgres will
    execute, never what the file says about itself.
    """
    # ⚠️ Located by FILENAME, not by grepping for the index name.
    #
    # It used to search every migration for `uq_gtd_people_email_lower` and
    # assert exactly one hit. That broke the day migration 209 superseded
    # the index (H-125) and legitimately named it in its own `DROP INDEX` —
    # two hits, and twelve tests erroring on a change that was correct. A
    # lookup by content finds every file that MENTIONS the thing; this suite
    # is about one file, so it should say which.
    path = MIGRATIONS / "148_people_key_shape.sql"
    assert path.exists(), f"148_people_key_shape.sql is missing from {MIGRATIONS}"
    raw = path.read_text(encoding="utf-8")
    return "\n".join(re.sub(r"--.*$", "", line) for line in raw.splitlines())


@pytest.fixture(scope="module")
def importer():
    spec = importlib.util.spec_from_file_location(
        "import_hr_people", REPO / "scripts" / "import_hr_people.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── P-1: the constraints ────────────────────────────────────────────────────

def test_email_gains_a_partial_unique_index(sql: str):
    """Partial, because a directory row may legitimately have no email at all —
    a contractor in the org chart who has never had a login."""
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_gtd_people_email_lower\s+"
        r"ON gtd_people \(lower\(email\)\) WHERE email IS NOT NULL",
        sql,
    )


def test_the_unique_constraint_on_name_is_dropped(sql: str):
    assert "DROP CONSTRAINT" in sql
    # By SHAPE, not by Postgres's default `gtd_people_name_key` — a database
    # whose constraint was created under another name must still be cleaned up.
    assert "c.contype = 'u'" in sql
    assert "ARRAY['name']" in sql


def test_dropping_the_name_constraint_leaves_an_index_behind(sql: str):
    """The dropped UNIQUE took its implicit index with it, and the directory
    searches by name — so the lookup must not silently become a seq scan."""
    assert "idx_gtd_people_name_lower" in sql


# ── P-2: the status vocabulary ──────────────────────────────────────────────

def test_status_gains_the_four_value_check(sql: str):
    assert re.search(
        r"CHECK \(status IN \('active', 'contractor', 'alumni', 'invited'\)\)", sql
    )


def test_the_status_check_is_added_NOT_VALID(sql: str):
    """The deploy-safety half.

    A plain CHECK against a legacy value nobody anticipated would abort the
    migration and, under ON_ERROR_STOP=1, the entire deploy. NOT VALID enforces
    every future write while tolerating an existing offender.
    """
    assert re.search(
        r"ADD CONSTRAINT gtd_people_status_check\s+CHECK \([^;]*\) NOT VALID\s*;", sql
    )
    assert "VALIDATE CONSTRAINT gtd_people_status_check" in sql
    # …and the validation itself must not be able to fail the deploy either.
    assert "EXCEPTION WHEN check_violation" in sql


def test_legacy_status_values_are_mapped_not_guessed(sql: str):
    assert "'inactive'" in sql and "'alumni'" in sql


# ── Deploy safety ───────────────────────────────────────────────────────────

def test_every_new_object_is_created_idempotently(sql: str):
    """`apply_migrations.sh` replays this on every deploy."""
    for statement in re.findall(r"CREATE (?:UNIQUE )?INDEX[^;]*;", sql):
        assert "IF NOT EXISTS" in statement, statement
    for statement in re.findall(r"ALTER TABLE gtd_people ADD COLUMN[^;]*;", sql):
        assert "IF NOT EXISTS" in statement, statement


def test_the_status_constraint_is_guarded_against_re_adding(sql: str):
    """`ADD CONSTRAINT` has no `IF NOT EXISTS` in Postgres, so it needs a guard
    or the second deploy fails."""
    assert "IF NOT EXISTS (" in sql
    assert "gtd_people_status_check" in sql


def test_a_conflicting_address_is_moved_aside_not_deleted(sql: str):
    """Losing an address silently would be worse than the ambiguity this
    migration exists to remove."""
    assert "email_conflict" in sql
    assert re.search(r"SET email_conflict = COALESCE\(p\.email_conflict, p\.email\)", sql)


def test_the_duplicate_winner_is_chosen_deterministically(sql: str):
    """An arbitrary winner would make a re-run against a restored backup pick
    differently and move a *different* row's address."""
    assert "ORDER BY updated_at DESC NULLS LAST" in sql
    assert "created_at DESC NULLS LAST" in sql


def test_blank_emails_are_nulled_before_the_index_is_built(sql: str):
    """`''` is not NULL, so two blank addresses would collide under a partial
    index that only excludes NULL."""
    assert re.search(r"SET email = NULL\s*\n?\s*WHERE email IS NOT NULL AND btrim\(email\) = ''", sql)


def test_source_key_is_backfilled_before_the_name_constraint_is_dropped(sql: str):
    """Order is load-bearing: the backfill is collision-free ONLY while `name`
    is still guaranteed distinct."""
    backfill = sql.index("SET source_key =")
    drop = sql.index("DROP CONSTRAINT")
    assert backfill < drop


# ── The importer this would otherwise break ─────────────────────────────────

def test_the_importer_no_longer_upserts_on_name(importer):
    """`ON CONFLICT (name)` needs a unique constraint on `name` to infer. With
    that constraint gone it fails outright — the consequence P-1 did not name."""
    assert "ON CONFLICT (name)" not in importer.UPSERT
    assert "ON CONFLICT (source_key)" in importer.UPSERT


def test_the_importer_upsert_matches_the_partial_index_predicate(importer):
    """Postgres can only infer a PARTIAL unique index when the statement repeats
    its predicate. Without this the upsert raises at run time, not at import."""
    assert "WHERE source_key IS NOT NULL" in importer.UPSERT


def test_the_upsert_refreshes_the_name(importer):
    """Names are now mutable — a person can be renamed in the snapshot without
    becoming a second row, which is exactly what the old key made impossible."""
    assert re.search(r"DO UPDATE SET\s*\n\s*name = EXCLUDED\.name", importer.UPSERT)


def test_source_key_is_scoped_to_this_importer(importer):
    """So a person hand-added in the People Center is never overwritten by a
    snapshot re-import."""
    assert importer.source_key("Rahul") == "agent-project-manager:rahul"


def test_source_key_is_stable_across_spacing_and_case(importer):
    assert importer.source_key("  RAHUL  ") == importer.source_key("rahul")


def _snapshot(members: list[dict]) -> dict:
    return {"company": "X", "departments": [
        {"name": "Engineering", "head": "Vijay",
         "teams": [{"name": "Firmware", "members": members}]}
    ]}


def test_every_imported_row_carries_its_key_and_source(importer):
    rows = importer.build_rows(_snapshot([{"name": "Rahul", "email": "r@x.in"}]), {})
    assert rows[0]["source_key"] == "agent-project-manager:rahul"
    assert rows[0]["source"] == "agent-project-manager"


def test_two_people_may_share_a_name_and_still_import(importer):
    """The point of P-1. Both rows exist; they differ by source_key only in that
    the snapshot itself cannot express two people of one name — but nothing in
    the schema stops them any more."""
    rows = importer.build_rows(
        _snapshot([
            {"name": "Priya", "email": "priya.a@x.in"},
            {"name": "Priya", "email": "priya.b@x.in"},
        ]),
        {},
    )
    assert len(rows) == 2
    assert {r["email"] for r in rows} == {"priya.a@x.in", "priya.b@x.in"}


def test_a_duplicate_address_is_dropped_at_the_source_not_at_psql(importer):
    """The unique index would otherwise fail the whole import on the second row.
    First occurrence keeps the address; the other person still imports."""
    rows = importer.build_rows(
        _snapshot([
            {"name": "Rahul", "email": "shared@x.in"},
            {"name": "Sam", "email": "SHARED@x.in"},
        ]),
        {},
    )
    assert len(rows) == 2
    assert rows[0]["email"] == "shared@x.in"
    assert rows[1]["email"] is None


def test_a_blank_address_becomes_null_rather_than_empty_string(importer):
    rows = importer.build_rows(_snapshot([{"name": "Rahul", "email": "  "}]), {})
    assert rows[0]["email"] is None


def test_two_people_without_addresses_both_import(importer):
    """Regression guard on the dedup: NULL is not a duplicate of NULL."""
    rows = importer.build_rows(
        _snapshot([{"name": "Rahul"}, {"name": "Sam"}]), {}
    )
    assert len(rows) == 2
    assert all(r["email"] is None for r in rows)
