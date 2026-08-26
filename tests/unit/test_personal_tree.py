"""The personal project TREE — migration 191, and the two queries it leans on.

Owner directive 2026-08-26: personal projects "do not show up in the project
management app but show up in the tasks app", and a member can categorise them.

Migration 191 delivers all three by re-keying one index, and it is worth being
precise about why that is enough — because it is also why this file exists.
191 adds no filter of its own. It makes `personal_owner` mean *private to this
person at any depth*, and then relies on two queries that ALREADY existed and
were deliberately not edited:

    tree.py:152      AND personal_owner IS NULL        → excludes the tree
    personal.py:664  OR lower(proj.personal_owner)=... → includes the tree

So the privacy guarantee lives in code that looks incidental. Somebody
tidying `tree.py` into `WHERE created_by = ...`, or narrowing the lens's arm to
"the root only", breaks a promise made to users without touching anything named
after it. These tests are structural for exactly that reason: the behaviour is
proven on real Postgres in `tests/live/live_ws39_personal_tree.sql` (13 checks),
and what cannot be proven there is that the code still *reaches* that behaviour.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO / "infra" / "postgres" / "191_personal_project_tree.sql"
_PERSONAL = (_REPO / "apps" / "services" / "gateway" / "gateway" / "routes"
             / "projects" / "personal.py")
_TREE = (_REPO / "apps" / "services" / "gateway" / "gateway" / "routes"
         / "projects" / "tree.py")


def _read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


def test_the_migration_exists() -> None:
    # Guards the guard — a renamed file would make every test below vacuous.
    assert _MIGRATION.is_file(), f"missing {_MIGRATION}"


def test_uniqueness_moved_onto_the_root() -> None:
    body = _read(_MIGRATION)
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_projects_personal_root",
        body,
    ), "191 must create the root-scoped index"
    assert "parent_project_id IS NULL" in body, (
        "the new index must be partial on the ROOT — that is the whole change"
    )
    assert "DROP INDEX IF EXISTS uq_pm_projects_personal_owner" in body, (
        "191 must drop the old all-rows index; leaving it would keep children "
        "from carrying personal_owner, which is the thing being fixed"
    )


def test_the_new_index_is_created_before_the_old_one_is_dropped() -> None:
    """Order matters: no window in which two roots could be minted."""
    body = _read(_MIGRATION)
    assert body.index("CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_projects_personal_root") < \
        body.index("DROP INDEX IF EXISTS uq_pm_projects_personal_owner"), (
        "create the replacement before dropping the constraint it replaces"
    )


def test_the_root_lookup_says_which_project_it_means() -> None:
    """``_load_personal_project`` returns a WRITE TARGET.

    Before 191 "the row with my address on it" and "my root" were the same
    question. They are not any more. Without the predicate this returns an
    arbitrary node of the member's tree, and a quick capture lands in whichever
    category the planner happened to pick.
    """
    body = _read(_PERSONAL)
    match = re.search(
        r"async def _load_personal_project.*?\)\)\.fetchone\(\)", body, re.S
    )
    assert match, "could not find _load_personal_project"
    assert "parent_project_id IS NULL" in match.group(0), (
        "_load_personal_project must ask for the ROOT explicitly since "
        "migration 191 — see this module's docstring"
    )


def test_the_projects_app_still_excludes_every_private_row() -> None:
    """191's privacy half. This filter was NOT edited, and must not be."""
    body = _read(_TREE)
    assert "personal_owner IS NULL" in body, (
        "the Projects app's project list no longer filters on "
        "`personal_owner IS NULL`. Since migration 191 that single predicate is "
        "what keeps a member's ENTIRE private tree — inbox and every category — "
        "off the company board. Owner directive 2026-08-26: personal projects "
        "'do not show up in the project management app'."
    )


def test_the_tasks_lens_still_includes_every_private_row() -> None:
    """191's visibility half, and it must stay depth-agnostic."""
    body = _read(_PERSONAL)
    assert re.search(r"lower\(proj\.personal_owner\)\s*=\s*:who", body), (
        "MY_TASKS_FROM no longer matches on `proj.personal_owner`. Since "
        "migration 191 that arm is what puts a member's categories — and the "
        "tasks inside them — into their Tasks app. Owner directive 2026-08-26: "
        "personal projects 'show up in the tasks app'."
    )
    # ⚠️ Narrowing this arm to the root would compile, pass every other test,
    # and silently hide every categorised task from the member who filed it.
    arm = re.search(r"OR lower\(proj\.personal_owner\) = :who[^\n]*\n", body)
    assert arm and "parent_project_id" not in arm.group(0), (
        "MY_TASKS_FROM's ownership arm must NOT be narrowed to the root — the "
        "lens is meant to see the whole private tree, at every depth"
    )
