"""The activity spine has ONE order, and the fake agrees with Postgres (H-159).

Spec: migration `213_pm_activities_seq.sql` · H-159 · H-130 · H-88.

## The defect, in one sentence

`created_at` defaults to `now()`, and in Postgres `now()` is the TRANSACTION
start time — so every activity written inside one transaction carries the
identical timestamp, and the spine's `ORDER BY created_at DESC, id DESC` then
settled the tie with a random UUID.

Two readers paid for it:

* ``core._coalescible_prior`` asks for "the latest row" and folds a new field
  change into it. An intervening event is meant to BREAK that run. When the
  intervening row tied with the prior field change, the UUID decided which
  came back, and half the time two edits that a third event separated were
  merged into one.
* ``activities.py``'s timeline pages with LIMIT/OFFSET over the same order.
  An unstable sort under paging shows a row twice or skips it, because two
  pages are two queries and nothing made them agree.

## And the fake could not see either

``_projects_fakes`` honoured only the FIRST ORDER BY key (H-130). So the
tie-break beside it was dropped, the fake disagreed with Postgres about
exactly the rows a tie-break exists for, and the hermetic suite could not
have caught the fix landing OR failing. That is the R8 hazard in CLAUDE.md,
from the other direction: a fake agrees with whatever SQL it is handed.

⚠️ Measured 2026-09-23: production held 108 activity rows and ZERO tied
groups, so neither defect had bitten real data. Fixed while the table was
160 kB and the rewrite was free.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit._projects_fakes import FakeProjectsDB, _order_keys

_REPO = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO / "infra" / "postgres" / "213_pm_activities_seq.sql"
_PROJECTS = _REPO / "apps" / "services" / "gateway" / "gateway" / "routes" / "projects"


def _read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


# -- The migration ----------------------------------------------------------


def test_the_migration_exists():
    # Guards the guard: a renamed file makes every assertion below vacuous.
    assert _MIGRATION.is_file(), f"missing {_MIGRATION}"


def test_the_column_is_a_sequence_and_is_added_idempotently():
    sql = _read(_MIGRATION)
    assert re.search(r"ADD COLUMN IF NOT EXISTS\s+seq\s+BIGSERIAL", sql, re.I)


def test_created_at_stays_the_PRIMARY_key_everywhere():
    """`seq` settles ties. It does not replace the timestamp.

    A row imported with an explicit `created_at` must keep sorting by the time
    it describes, not by the moment somebody imported it. So every reader
    orders `created_at` first and `seq` second, and none orders on `seq` alone.
    """
    for name in ("core.py", "activities.py"):
        sql = _read(_PROJECTS / name)
        assert "created_at DESC, seq DESC" in sql, name
        assert not re.search(r"ORDER BY\s+seq\b", sql, re.I), name


@pytest.mark.parametrize("name", ["core.py", "activities.py"])
def test_no_reader_still_breaks_the_tie_on_the_random_uuid(name):
    sql = _read(_PROJECTS / name)
    assert "created_at DESC, id DESC" not in sql


# -- The fake honours every key (H-130) -------------------------------------


def test_the_parser_reads_every_key_with_its_own_direction():
    assert _order_keys("SELECT 1 ORDER BY created_at DESC, seq DESC") == [
        ("created_at", True), ("seq", True),
    ]
    assert _order_keys("SELECT 1 ORDER BY a ASC, b DESC LIMIT :limit") == [
        ("a", False), ("b", True),
    ]
    # A table alias is stripped, and a bare key defaults to ASC.
    assert _order_keys("SELECT 1 ORDER BY t.name") == [("name", False)]


def test_the_parser_declines_what_it_cannot_read():
    # An expression is not a column. Returning [] makes the caller fall back
    # rather than invent an order — a fake that guesses is worse than one
    # that admits it does not know.
    assert _order_keys("SELECT 1 ORDER BY lower(name), id") == []
    assert _order_keys("SELECT 1") == []


def test_a_mixed_direction_pair_is_not_collapsed():
    """`ORDER BY a ASC, b DESC` is two directions, not one.

    A single tuple key with `reverse=True` would reverse BOTH, which is the
    shape that makes a fake quietly disagree with the database.
    """
    db = FakeProjectsDB()
    rows = [
        {"a": 1, "b": 1}, {"a": 1, "b": 2}, {"a": 2, "b": 1}, {"a": 2, "b": 2},
    ]
    out = db._ordered("SELECT * FROM t ORDER BY a ASC, b DESC", list(rows))
    assert [(r["a"], r["b"]) for r in out] == [(1, 2), (1, 1), (2, 2), (2, 1)]


# -- The fake mints the sequence, on BOTH write paths -----------------------


def test_seed_mints_a_sequence():
    db = FakeProjectsDB()
    first = db.seed("pm_activities", type="comment", created_by="a")
    second = db.seed("pm_activities", type="comment", created_by="a")
    assert second.seq > first.seq


def test_the_INSERT_path_mints_one_too():
    """The path `record_activity` actually takes.

    Stamping only `seed` is what made the first attempt at this fix look
    half-applied: every row the suite exercises is written through the INSERT
    path, so each had `seq = None` and fell straight back into the tie.
    """
    db = FakeProjectsDB()
    rows = []
    for body in ("one", "two", "three"):
        result = db._insert(
            "INSERT INTO pm_activities (type, body, created_by) "
            "VALUES (:type, :body, :who)",
            "pm_activities",
            {"type": "comment", "body": body, "who": "a"},
        )
        rows.append(result.fetchone())
    seqs = [r.seq for r in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3


def test_rows_sharing_a_timestamp_still_order():
    """The whole point, end to end in the fake.

    Three rows, one timestamp — which is what one transaction produces — and
    the newest must come back first.
    """
    db = FakeProjectsDB()
    stamp = SimpleNamespace()  # any single shared value
    for body in ("a", "b", "c"):
        db.seed("pm_activities", type="comment", body=body,
                created_by="x", created_at=stamp)
    out = db._ordered(
        "SELECT * FROM pm_activities ORDER BY created_at DESC, seq DESC",
        db.rows("pm_activities"),
    )
    assert [r["body"] for r in out] == ["c", "b", "a"]
