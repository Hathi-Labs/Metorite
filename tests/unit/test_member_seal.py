"""A departed member's personal tree seals — D63, H-49.

**What this file can and cannot prove.** R8 is explicit that a hermetic fake
agrees with whatever SQL it is handed, and five live bugs shipped green that
way. The behaviour of this feature IS SQL: a recursive CTE, two filters, and an
``EXISTS`` that must survive both. So the behaviour is proved in
``tests/live/live_member_seal.sql``, which runs on real Postgres and carries 14
checks.

What belongs here is everything a fake cannot lie about:

* the SQL constants carry the filter, on **both** arms;
* **both** off-boarding doors reach the seal;
* the migration has the R6 shape;
* the live file and ``core.py`` have not drifted apart.

That last one is the interesting one. The live test inlines the visibility
query, because psql cannot import a Python constant. An inlined copy is a
second definition, and a second definition drifts. These tests read both and
compare, so the copy cannot quietly diverge from the original.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "apps/services/gateway/gateway/routes/projects/core.py"
SEAL = REPO / "apps/services/gateway/gateway/routes/projects/seal.py"
MEMBERS = REPO / "apps/services/gateway/gateway/routes/admin/members.py"
MIGRATION = REPO / "infra/postgres/215_projects_sealed.sql"
LIVE = REPO / "tests/live/live_member_seal.sql"


def read(path: Path) -> str:
    # Windows defaults to cp1252 and these files carry box-drawing characters.
    return path.read_text(encoding="utf-8")


def _constant(source: str, name: str) -> str:
    """The body of a triple-quoted module constant."""
    match = re.search(rf'^{re.escape(name)} = """(.*?)"""', source, re.S | re.M)
    assert match, f"{name} is not a triple-quoted constant any more"
    return match.group(1)


# ── The filter is in the SQL, on every arm ──────────────────────────────────


def test_the_unrestricted_clause_hides_sealed_projects_too():
    """⚠️ The admin is not exempt, and that is the point of D63.

    ``data:org:read`` is the widest grant in the product and the People Center
    holds it. D63 says an admin reading a departed colleague's private tasks
    "must never be an invisible act", and allows exactly one door for it:
    owner-only and logged. An unrestricted clause that skipped the filter
    WOULD BE that invisible act, available to every HR admin by default.
    """
    sql = _constant(read(CORE), "_TENANT_PROJECTS_SQL")
    assert "sealed_at IS NULL" in sql


def test_the_grant_closure_filters_both_arms():
    """Seed and recursive step, and the seed needs a JOIN to reach the column.

    Filtering only the recursive step leaves a sealed ROOT visible while hiding
    its children — the worst of the three outcomes, because the departed
    member's workspace still appears and it appears empty.
    """
    sql = _constant(read(CORE), "_VISIBLE_PROJECTS_SQL")
    assert sql.count("sealed_at IS NULL") == 2, (
        "both the seed and the recursive step must filter the seal"
    )
    assert "JOIN pm_projects gp ON gp.id = g.project_id" in sql, (
        "a grant row has no sealed_at; the seed has to join pm_projects"
    )


def test_the_assignee_arm_does_NOT_mention_the_seal():
    """D63's hand-over is the assignee arm surviving the seal.

    If somebody ever adds a seal filter to this arm, the hand-over silently
    stops working: a colleague loses the task they were already working on the
    moment its owner is deactivated. Nothing else in the tree would fail.
    """
    source = read(CORE)
    match = re.search(
        r"def task_visibility_clause.*?(?=\ndef |\Z)", source, re.S,
    )
    assert match, "task_visibility_clause moved; re-point this test"
    body = match.group(0)
    assert "pm_task_assignees" in body
    assert "sealed" not in body, (
        "the assignee arm must not consult the seal — that IS the hand-over"
    )


# ── Both doors, because one door is how they came to disagree ──────────────


@pytest.mark.parametrize("door", ["update_member", "remove_member"])
def test_both_offboarding_doors_seal(door: str):
    """``remove_member``'s own docstring records why this is parametrised.

    It says the route "used to hold its own copy of the comparison, which is
    precisely why the other door never grew one". The seal went into both in
    the same change, and this test is what keeps it there.
    """
    source = read(MEMBERS)
    match = re.search(rf"async def {door}\(.*?(?=\n@router|\Z)", source, re.S)
    assert match, f"{door} moved; re-point this test"
    assert "seal_personal_tree" in match.group(0)


def test_reactivating_unseals():
    """Otherwise a lifted suspension leaves the member locked out of their own
    workspace, with no route to fix it but a hand-written UPDATE."""
    source = read(MEMBERS)
    match = re.search(r"async def update_member\(.*?(?=\n@router|\Z)", source, re.S)
    assert "unseal_personal_tree" in match.group(0)


def test_the_seal_rides_the_status_write_transaction():
    """A status saying "gone" beside a visible workspace is the whole defect.

    Both statements live inside the one ``async with _tenant_session()`` block,
    so they commit together or not at all.
    """
    source = read(MEMBERS)
    match = re.search(r"async def update_member\(.*?(?=\n@router|\Z)", source, re.S)
    body = match.group(0)
    session = body.index("async with _tenant_session()")
    seal = body.index("seal_personal_tree")
    # The seal call is inside the session block, and indented under it.
    assert seal > session
    line = body[body.rindex("\n", 0, seal) + 1 : seal]
    assert line.startswith(" " * 16), "the seal must sit inside the session block"


def test_offboarding_is_recorded():
    """§3a rule 2 and D63 both want this: closing somebody's workspace is
    never an invisible act, and ``record_admin_change`` is the half a human
    reads back."""
    source = read(MEMBERS)
    assert source.count("tree_projects=tree_projects") >= 2
    assert 'tree="sealed"' in source


# ── The migration's shape (R6) ─────────────────────────────────────────────


def test_the_column_is_expand_shaped():
    sql = read(MIGRATION)
    assert "ADD COLUMN IF NOT EXISTS sealed_at TIMESTAMPTZ" in sql
    assert "NOT NULL" not in sql.split("COMMENT")[0], (
        "R6: nullable with no default, so existing rows need no backfill"
    )
    assert "DROP" not in sql.upper(), "a seal migration must not drop anything"


def test_nothing_in_the_feature_deletes():
    """D63: retained, invisible, NEVER deleted. R6's reasoning — you cannot
    undo a delete — is why the decision says so twice."""
    for path in (SEAL, MIGRATION):
        body = read(path).upper()
        assert "DELETE FROM" not in body, f"{path.name} deletes something"


def test_the_seal_is_idempotent_by_its_where_clause():
    """The timestamp is evidence of when the workspace closed. A retry must
    not quietly move it, and the guard is in the SQL rather than in a caller."""
    source = read(SEAL)
    match = re.search(r"async def seal_personal_tree\(.*?(?=\nasync def |\Z)",
                      source, re.S)
    assert "sealed_at IS NULL" in match.group(0)


def test_the_tree_is_found_case_folded():
    """R10 — migration 147's unique index is on ``lower(personal_owner)``, so a
    seed on the raw column would miss ``Priya@…`` for a caller who typed
    ``priya@…``."""
    assert "lower(p.personal_owner) = :who" in read(SEAL)


# ── The live file is a copy, so it must not drift ──────────────────────────


def test_the_live_test_uses_the_same_closure_as_core():
    """⚠️ The live SQL inlines the visibility query, because psql cannot import
    a Python constant. An inlined copy is a second definition.

    So compare the load-bearing lines. If somebody changes the closure in
    ``core.py`` and not in the live file, the live file starts proving
    something about a query the product no longer runs — which is worse than
    having no live test, because it reads as evidence.
    """
    core_sql = _constant(read(CORE), "_VISIBLE_PROJECTS_SQL")
    live_sql = read(LIVE)
    for fragment in (
        "JOIN pm_projects gp ON gp.id = g.project_id",
        "gp.sealed_at IS NULL",
        "JOIN granted a ON p.parent_project_id = a.id",
    ):
        assert fragment in core_sql, f"core.py lost: {fragment}"
        assert fragment in live_sql, f"the live test drifted, it lost: {fragment}"


def test_the_live_test_covers_the_two_claims_only_postgres_can_settle():
    """A live file that stopped exercising the subtree or the hand-over would
    still pass its own checks, and prove nothing worth the runtime."""
    body = read(LIVE)
    assert "the Area went with the root" in body
    assert "Bo still sees the task Ada handed him" in body


# ── The fakes agree with Postgres about the seal ───────────────────────────
#
# ⚠️ These prove the FAKES, not the product. They exist because H-130 is the
# record of what a disagreeing fake costs: `_ordered` honoured only the first
# ORDER BY key, so the hermetic suite could not have caught its own fix landing
# or failing. Teaching a fake a new column and never exercising the lesson
# leaves the same hole.
#
# The live file remains the authority on what the SQL does (R8). These check
# that a hermetic test written later will not pass for the wrong reason.


def _fake():
    from tests.unit._projects_fakes import FakeProjectsDB

    return FakeProjectsDB()


def _seal(db, project_id: str) -> None:
    """Stamp one seeded project, the way the UPDATE would."""
    for row in db.rows("pm_projects"):
        if str(row["id"]) == str(project_id):
            row["sealed_at"] = "2026-09-23T00:00:00Z"
            return
    raise AssertionError(f"no seeded project {project_id}")


def _row(db, project_id: str) -> dict:
    for row in db.rows("pm_projects"):
        if str(row["id"]) == str(project_id):
            return row
    raise AssertionError(f"no seeded project {project_id}")


def test_the_fake_closure_drops_a_sealed_project():
    db = _fake()
    live = db.seed_project(name="Live", subject="ada@seal.invalid")
    gone = db.seed_project(name="Sealed", subject="ada@seal.invalid")
    _seal(db, gone.id)

    visible = db.visible_project_ids("ada@seal.invalid", [])
    assert str(live.id) in visible
    assert str(gone.id) not in visible, "a sealed project stayed in the closure"


def test_sealing_a_root_takes_its_whole_subtree():
    """Sealing a ROOT must take its Area with it.

    ⚠️ This passes with the seed filter ALONE, and that is worth stating
    rather than leaving for somebody to rediscover. A sealed root never enters
    the seed set, so the descent has nothing to walk from and the Area is
    absent whether or not the recursive step filters. The test below is the
    one that isolates the recursive step.
    """
    db = _fake()
    root = db.seed_project(name="Ada", subject="ada@seal.invalid")
    area = db.seed_project(name="Errands", parent=str(root.id), subject=None)

    assert str(area.id) in db.visible_project_ids("ada@seal.invalid", [])

    _seal(db, root.id)
    visible = db.visible_project_ids("ada@seal.invalid", [])
    assert str(root.id) not in visible
    assert str(area.id) not in visible


def test_the_fake_closure_does_not_descend_THROUGH_a_seal():
    """The case that isolates the recursive filter, and only this shape does.

    ⚠️ **Found by mutation-testing my own fence.** The first version of this
    sealed the ROOT and asserted the Area vanished. I deleted the descent
    filter from the fake, and all 18 tests still passed — because a sealed
    root never reaches the seed set, so the Area is unreachable either way. A
    test that cannot fail is decoration.

    The seal has to sit in the MIDDLE. Grant the root and leave it live, seal
    the Area beneath it, and hang a sub-Area below that. Without the recursive
    filter the walk steps root → Area → sub-Area and returns both. With it,
    the Area is skipped and the sub-Area is never reached.
    """
    db = _fake()
    root = db.seed_project(name="Ada", subject="ada@seal.invalid")
    area = db.seed_project(name="Errands", parent=str(root.id), subject=None)
    deeper = db.seed_project(name="Bins", parent=str(area.id), subject=None)

    everything = db.visible_project_ids("ada@seal.invalid", [])
    assert {str(root.id), str(area.id), str(deeper.id)} <= everything

    _seal(db, area.id)
    visible = db.visible_project_ids("ada@seal.invalid", [])
    assert str(root.id) in visible, "the live root must survive its sealed child"
    assert str(area.id) not in visible
    assert str(deeper.id) not in visible, (
        "the walk descended THROUGH a sealed Area — the recursive filter is gone"
    )


def test_the_fake_unrestricted_answer_hides_sealed_projects_too():
    """D63 binds `data:org:read`. A fake that exempted it would let a future
    admin-tier test assert the wrong thing and pass."""
    db = _fake()
    live = db.seed_project(name="Live", subject="org")
    gone = db.seed_project(name="Sealed", subject="org")
    org = _row(db, live.id)["organization_id"]
    _seal(db, gone.id)

    everything = db.tenant_project_ids(org)
    assert str(live.id) in everything
    assert str(gone.id) not in everything


def test_a_seeded_project_is_live_by_default():
    """Every seeded row must carry `sealed_at = None`, the value Postgres
    returns for a project nobody has sealed."""
    db = _fake()
    project = db.seed_project(name="Anything")
    assert _row(db, project.id)["sealed_at"] is None
