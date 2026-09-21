"""The People tables are no longer called ``gtd_*`` — and the rows came with them.

Owner directive, 2026-09-21: *"I really don't want GTD anymore... update the
naming convention for all of the table names accordingly."* Slice 1 is the
People family, five tables:

==========================  ======================
old                         new
==========================  ======================
``gtd_people``              ``people``
``gtd_person_skills``       ``people_skills``
``gtd_person_credentials``  ``people_credentials``
``gtd_person_absences``     ``people_absences``
``gtd_person_resumes``      ``people_resumes``
==========================  ======================

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
no-op. An upgraded database keeps the old tables and gets five EMPTY new ones
beside them, and every test that only builds a fresh database still passes. It
was found by rebuilding a real pre-change database and upgrading it, which is
:func:`test_an_existing_table_is_renamed_WITH_its_rows` below.

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
    "people": ("gtd_people", "49_gtd_people.sql"),
    "people_resumes": ("gtd_person_resumes", "74_gtd_people_editable_and_resumes.sql"),
    "people_absences": ("gtd_person_absences", "174_people_absences.sql"),
    "people_skills": ("gtd_person_skills", "176_people_skills.sql"),
    "people_credentials": ("gtd_person_credentials", "176_people_skills.sql"),
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
    """``ALTER TABLE people RENAME TO people`` is what a sweep leaves behind."""
    sql = _sql(pair[1])
    assert f"ALTER TABLE {new} RENAME TO {new}" not in sql
    assert f"RENAME TO %I', '{new}', '{new}'" not in sql


@pytest.mark.parametrize("new,pair", sorted(RENAMED.items()))
def test_the_prologue_runs_before_the_create(new, pair):
    """A rename after the CREATE finds the new name taken and does nothing."""
    sql = _sql(pair[1])
    assert sql.index(f"$rename_{new}$") < sql.index(f"CREATE TABLE IF NOT EXISTS {new}")


def test_no_migration_creates_a_table_under_an_old_people_name():
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

    The row count is the measurement. An INSERT would need each table's own
    NOT NULL columns, and the property under test is that the rename carries
    whatever rows are there.
    """
    old, filename = pair
    sql = _sql(filename)
    conn = eng.connect()
    trans = conn.begin()
    try:
        conn.execute(text(f"ALTER TABLE {new} RENAME TO {old}"))
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
    finally:
        trans.rollback()
        conn.close()


@live
def test_the_rename_carries_the_rows_it_is_given(eng):
    """One table, with a row we put there ourselves, end to end."""
    conn = eng.connect()
    trans = conn.begin()
    try:
        conn.execute(text("ALTER TABLE people RENAME TO gtd_people"))
        conn.execute(
            text(
                "INSERT INTO gtd_people (name, email) "
                "VALUES ('Existing Person', 'keep@rename.example')"
            )
        )
        with conn.connection.dbapi_connection.cursor() as cur:
            cur.execute(_sql("49_gtd_people.sql"))
        survived = conn.execute(
            text("SELECT name FROM people WHERE email = 'keep@rename.example'")
        ).scalar()
        assert survived == "Existing Person"
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
