"""Task 2516: rule 2 of an import discard names every table that cascades with a task.

A discard deletes the tasks that one import run made, and Postgres then
deletes every row that cascades with them. Rule 2 (``EDITED_SQL``) refuses
the discard when such a row holds a member's work. ``TASK_TABLES_CHECKED``
names the tables that rule 2 reads. ``TASK_TABLES_IGNORED`` names each other
table, with the reason that it holds no member work, or that a member's write
to it also writes a ``pm_activities`` row, which rule 2 reads.

Hermetic: no database. The migrations are read by ``_schema_cascade``, a
syntactic parser, so a foreign key in a shape it cannot read escapes this
file. ``tests/live/live_ws41_discard.py`` check 0.2 reads the real catalog,
and it stays the authority.
"""

from __future__ import annotations

import re

import pytest
from gateway.routes.projects import import_discard as discard

from tests.unit._schema_cascade import cascade_children

CHECKED = discard.TASK_TABLES_CHECKED
IGNORED = discard.TASK_TABLES_IGNORED

#: The five tables that task 2516 found in neither list, and where it put each.
CHECKED_BY_2516 = ("pm_agent_runs", "pm_cycle_tasks")
IGNORED_BY_2516 = ("pm_agent_run_activities", "pm_agent_run_strands", "pm_task_refs")


def _task_children() -> set[str]:
    """The tables that the migrations declare ``ON DELETE CASCADE`` to ``pm_tasks``."""
    return set(cascade_children("pm_tasks")) - {"pm_tasks"}


def test_no_table_is_in_both_lists_or_twice_in_one():
    assert len(CHECKED) == len(set(CHECKED)), "a table is in TASK_TABLES_CHECKED twice"
    assert not set(CHECKED) & set(IGNORED)


def test_each_ignored_table_gives_its_reason():
    for table, why in IGNORED.items():
        assert isinstance(why, str) and why.strip(), table


@pytest.mark.parametrize("table", CHECKED_BY_2516 + IGNORED_BY_2516)
def test_each_table_of_task_2516_is_in_exactly_one_list(table):
    assert (table in CHECKED) != (table in IGNORED), table


def test_the_placement_of_task_2516_holds():
    # A member's agent can claim or start a run with no activity row, and a
    # member can add a task to a cycle with no activity row: rule 2 reads both.
    assert set(CHECKED_BY_2516) <= set(CHECKED)
    # A run activity and a strand each hang off a run of the same task. Their
    # reasons lean on `pm_agent_runs`, so it must stay checked.
    assert set(IGNORED_BY_2516) <= set(IGNORED)
    assert "pm_agent_runs" in CHECKED


@pytest.mark.parametrize("table", CHECKED)
def test_rule_2_reads_each_checked_table(table):
    assert re.search(rf"\bFROM\s+{table}\b", discard.EDITED_SQL), (
        f"{table} is in TASK_TABLES_CHECKED, but EDITED_SQL does not read it"
    )


def test_every_table_that_cascades_with_a_task_is_in_a_list():
    missing = _task_children() - set(CHECKED) - set(IGNORED)
    assert not missing, (
        f"{sorted(missing)} cascade with a task, so a discard deletes their rows. "
        "Put each in TASK_TABLES_CHECKED with a clause in EDITED_SQL, or in "
        "TASK_TABLES_IGNORED with its reason (import_discard.py)."
    )


def test_each_listed_table_cascades_with_a_task():
    # Also proves that the parser reads the shape of every listed table.
    stale = (set(CHECKED) | set(IGNORED)) - _task_children()
    assert not stale, f"{sorted(stale)} no longer cascade with a task. Take them out of the list."
