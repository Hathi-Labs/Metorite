"""The `gtd_` tables are moving to honest names — and the rows come with them.

Owner directive, 2026-09-21: *"I really don't want GTD anymore... update the
naming convention for all of the table names accordingly."* The work goes
table family by table family, and this module is the fence for every slice
that has landed.

**Slice 1, the People family** (2026-09-21):

==========================  ======================
old                         new
==========================  ======================
``gtd_people``              ``people``
``gtd_person_skills``       ``people_skills``
``gtd_person_credentials``  ``people_credentials``
``gtd_person_absences``     ``people_absences``
``gtd_person_resumes``      ``people_resumes``
==========================  ======================

**Slice 2, the Calendar and the settings row** (2026-09-22):

==========================  =========================
old                         new
==========================  =========================
``gtd_day_state``           ``calendar_day_state``
``gtd_rollover_log``        ``calendar_rollover_log``
``gtd_settings``            ``user_settings``
==========================  =========================

**Slice 3, the three task-store survivors** (WS-39 S8 PR 2, 2026-09-23):

==========================  =========================
old                         new
==========================  =========================
``gtd_attachments``         ``attachments``
``gtd_horizons``            ``my_tasks_horizons``
``gtd_reviews``             ``my_tasks_reviews``
==========================  =========================

The rest of the task store is not renamed. Migration 217 drops it, and
``test_gtd_backfill.py`` fences that drop. ⚠️ ``attachments`` is a short,
generic name. No table, view or column in the ladder held it before, and
``pm_task_attachments`` contains it as a suffix, so a test fake must match it
through ``tests/unit/_sql_match.py``.

⚠️ **`gtd_settings` did NOT become `calendar_settings`, and that was a
decision.** The board groups it with the Calendar (D53.6) because it survives
the S3c drop. Its contents disagree: it carries ``chat_model``,
``capture_dedup``, ``auto_sync_on_open`` and ``workflow_stages`` for the Tasks
app beside ``day_start_hour``, ``energy_windows`` and ``auto_rollover`` for the
Calendar. It is one row of per-user preference, so it is the sibling of
``org_settings`` (migration 151) and it is named for that. Owner call,
2026-09-22.

**The rename lives in the migration that CREATES the table.** Two other shapes
were built and measured on 2026-09-21, and both were wrong:

1. *One rename migration at the end.* Every earlier file then addresses a name
   that is gone, so eight of them fail on replay.
2. *The same, with compatibility views.* A view does not satisfy
   ``CREATE INDEX``, so five index statements still fail.

The rename at the top of the creating file answers all three states in one
place — a fresh install, an existing database, and a replay.

⚠️ **The bug this suite exists for.** The prologue names the OLD table, so a
sweep that rewrites ``gtd_people`` to ``people`` everywhere rewrites the
prologue too, into ``ALTER TABLE people RENAME TO people``. That is a silent
no-op. An upgraded database keeps the old tables and gets empty new ones
beside them, and every test that only builds a fresh database still passes. It
was found by rebuilding a real pre-change database and upgrading it, which is
:func:`test_an_existing_table_is_renamed_WITH_its_rows` below. **Sweep first,
add the prologue second.**

⚠️ A sync connection, not an async engine. psycopg refuses Windows' default
``ProactorEventLoop`` and this repo's primary dev box is Windows (CLAUDE.md §6).
"""

from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATIONS = os.path.join(_ROOT, "infra", "postgres")

#: new table -> (old table, the migration file that creates it)
RENAMED: dict[str, tuple[str, str]] = {
    # slice 1 — the People family
    "people": ("gtd_people", "49_gtd_people.sql"),
    "people_resumes": ("gtd_person_resumes", "74_gtd_people_editable_and_resumes.sql"),
    "people_absences": ("gtd_person_absences", "174_people_absences.sql"),
    "people_skills": ("gtd_person_skills", "176_people_skills.sql"),
    "people_credentials": ("gtd_person_credentials", "176_people_skills.sql"),
    # slice 2 — the Calendar, and the per-user settings row
    "user_settings": ("gtd_settings", "51_gtd_settings.sql"),
    "calendar_rollover_log": ("gtd_rollover_log", "78_gtd_calendar_rollover.sql"),
    "calendar_day_state": ("gtd_day_state", "92_gtd_day_state.sql"),
    # slice 3 — the task-store tables that survive migration 217's drop
    "attachments": ("gtd_attachments", "52_gtd_attachments.sql"),
    "my_tasks_horizons": ("gtd_horizons", "48_task_manager_gtd.sql"),
    "my_tasks_reviews": ("gtd_reviews", "48_task_manager_gtd.sql"),
}

#: A row this table accepts with no foreign key to satisfy, so the rename can
#: be shown to carry REAL data rather than an empty table. Not every table has
#: one — the children of `people` need a parent first, and for those the row
#: COUNT either side of the rename is the measurement.
SEED: dict[str, tuple[str, str]] = {
    "people": (
        "INSERT INTO {t} (name, email) VALUES ('Existing Person', 'keep@rename.example')",
        "SELECT name FROM {t} WHERE email = 'keep@rename.example'",
    ),
    "user_settings": (
        "INSERT INTO {t} (user_id) VALUES ('keep@rename.example')",
        "SELECT user_id FROM {t} WHERE user_id = 'keep@rename.example'",
    ),
    "calendar_day_state": (
        "INSERT INTO {t} (user_id, day) VALUES ('keep@rename.example', DATE '2026-01-02')",
        "SELECT user_id FROM {t} WHERE user_id = 'keep@rename.example'",
    ),
    "calendar_rollover_log": (
        "INSERT INTO {t} (user_id, item_id) "
        "VALUES ('keep@rename.example', gen_random_uuid())",
        "SELECT user_id FROM {t} WHERE user_id = 'keep@rename.example'",
    ),
    "attachments": (
        "INSERT INTO {t} (user_id, name, path) "
        "VALUES ('keep@rename.example', 'keep.pdf', 'data/keep.pdf')",
        "SELECT name FROM {t} WHERE user_id = 'keep@rename.example'",
    ),
    "my_tasks_horizons": (
        "INSERT INTO {t} (user_id, level, title) "
        "VALUES ('keep@rename.example', 3, 'Ship My Tasks')",
        "SELECT title FROM {t} WHERE user_id = 'keep@rename.example'",
    ),
    "my_tasks_reviews": (
        "INSERT INTO {t} (user_id) VALUES ('keep@rename.example')",
        "SELECT user_id FROM {t} WHERE user_id = 'keep@rename.example'",
    ),
}


def _sql(filename: str) -> str:
    with open(os.path.join(MIGRATIONS, filename), encoding="utf-8") as fh:
        return fh.read()


# -- The shape of the prologue -----------------------------------------------


@pytest.mark.parametrize("new,pair", sorted(RENAMED.items()))
def test_the_creating_migration_carries_a_guarded_rename(new, pair):
    old, filename = pair
    sql = _sql(filename)
    block = re.search(rf"DO \$rename_{new}\$(.*?)\$rename_{new}\$;", sql, re.DOTALL)
    assert block, f"{filename} has no rename prologue for {new}"
    body = block.group(1)

    # The old name, ONCE, as a quoted literal. See the module docstring.
    assert f"old_name CONSTANT text := '{old}';" in body

    # Renamed, never recreated.
    assert f"format('ALTER TABLE %I RENAME TO %I', old_name, '{new}')" in body

    # A view that wears the old name is left alone.
    assert "relkind = 'r'" in body
    # Never renames ONTO a name that is taken, so a replay does nothing.
    assert f"to_regclass('public.{new}') IS NULL" in body


@pytest.mark.parametrize("new,pair", sorted(RENAMED.items()))
def test_the_rename_is_not_the_swept_no_op(new, pair):
    """``ALTER TABLE people RENAME TO people`` is what a sweep leaves behind.

    Measured against the EXECUTABLE block, not the file. The prologue's own
    warning quotes the broken statement to explain it, and an assertion over
    the whole file reads that comment as the defect. Slice 1 passed this only
    because the quoted text happened to wrap across two comment lines.
    """
    sql = _sql(pair[1])
    block = re.search(rf"DO \$rename_{new}\$(.*?)\$rename_{new}\$;", sql, re.DOTALL)
    assert block, f"{pair[1]} has no rename prologue for {new}"
    body = re.sub(r"--.*", "", block.group(1))
    assert f"ALTER TABLE {new} RENAME TO {new}" not in body
    assert f"RENAME TO %I', '{new}', '{new}'" not in body
    assert f"old_name CONSTANT text := '{new}'" not in body


@pytest.mark.parametrize("new,pair", sorted(RENAMED.items()))
def test_the_prologue_runs_before_the_create(new, pair):
    """A rename after the CREATE finds the new name taken and does nothing."""
    sql = _sql(pair[1])
    assert sql.index(f"$rename_{new}$") < sql.index(f"CREATE TABLE IF NOT EXISTS {new}")


def test_no_migration_creates_a_table_under_an_old_name():
    for name in sorted(os.listdir(MIGRATIONS)):
        if not re.match(r"^\d+_.*\.sql$", name):
            continue
        sql = _sql(name)
        for new, (old, _) in RENAMED.items():
            assert f"CREATE TABLE IF NOT EXISTS {old}" not in sql, (
                f"{name} still creates {old}. It must create {new}."
            )


# -- The same, against a real Postgres (R8) ----------------------------------

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

live = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}
        ).scalar()
    )


@live
def test_a_fresh_ladder_builds_the_new_names_and_not_the_old(eng):
    with eng.begin() as conn:
        for new, (old, _) in RENAMED.items():
            assert _exists(conn, new), f"the ladder did not build {new}"
            assert not _exists(conn, old), f"the ladder still builds {old}"


@live
@pytest.mark.parametrize("new,pair", sorted(RENAMED.items()))
def test_an_existing_table_is_renamed_WITH_its_rows(eng, new, pair):
    """The upgrade path. Put the old name back, then apply the migration.

    This is the arm that the swept no-op failed. Everything happens inside one
    transaction and is rolled back, so the ladder database does not change.

    Where :data:`SEED` has a row for the table, the row itself is the proof.
    Otherwise the row COUNT is, because a child of `people` needs a parent
    before it can hold anything.
    """
    old, filename = pair
    sql = _sql(filename)
    seed = SEED.get(new)
    conn = eng.connect()
    trans = conn.begin()
    try:
        conn.execute(text(f"ALTER TABLE {new} RENAME TO {old}"))
        if seed:
            conn.execute(text(seed[0].format(t=old)))
        before = conn.execute(text(f"SELECT count(*) FROM {old}")).scalar()

        with conn.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)

        assert _exists(conn, new), f"{filename} did not rename {old} to {new}"
        assert not _exists(conn, old), (
            f"{filename} left {old} in place and made a second table. This is "
            "the swept-no-op failure the module docstring records."
        )
        after = conn.execute(text(f"SELECT count(*) FROM {new}")).scalar()
        assert after == before
        if seed:
            assert conn.execute(text(seed[1].format(t=new))).scalar() is not None, (
                f"{new} exists but the seeded row did not survive the rename"
            )
    finally:
        trans.rollback()
        conn.close()


@live
@pytest.mark.parametrize("new,pair", sorted(RENAMED.items()))
def test_applying_the_creating_migration_again_changes_nothing(eng, new, pair):
    sql = _sql(pair[1])
    conn = eng.connect()
    trans = conn.begin()
    try:
        with conn.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)
            cur.execute(sql)
        assert _exists(conn, new)
        assert not _exists(conn, pair[0])
    finally:
        trans.rollback()
        conn.close()
