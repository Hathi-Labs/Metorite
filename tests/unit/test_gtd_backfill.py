"""WS-39 S3b/S3c fences — the `gtd_*` retirement stays safe to deploy.

Board **WS-39** · decisions **D53.5** (three releases), **D53.6** (what
survives) · spec `project_management_app.md` §12.8 · gate `work_plan.md` §6 (f).

These are STRUCTURAL fences over the three migration files, read as text. The
behaviour is proven separately and against a real database — `tests/live/
live_ws39_s3b.sql` (47 checks, two-org) and `live_ws39_s3c.sql` (22 checks,
every refusal path). R8 is explicit that a hermetic fake agrees with whatever
SQL it is handed, so nothing here asserts what the SQL *does*.

What it does defend is the property that makes these files safe to MERGE, which
no live test can express: that applying them changes no data and drops no table.
Running the move is OWNER-GATE. A future edit that made the ladder run it would
execute that gate unattended, on a deploy, before services restart (R6) — and it
would look like a one-line change.

⚠️ **The function is defined TWICE on the ladder, and the LAST definition wins.**
189 defined `gtd_backfill_to_pm()` on 2026-08-26. 196/197 then added
`pm_projects.owns_statuses` and the CHECK that a root owns its statuses, and on
2026-09-23 the move failed on production because 189's root insert never set
the flag. 212 re-asserts the whole body with the flag (forward-only, H-104: an
edited 189 would re-run on checksum and still not be what production serves).
So every claim about *what the body says* is made against the last definition,
found by scanning the ladder rather than by naming a file — a 213 that
redefined the function again would move the claims, and this suite says so.

**WS-39 S8 PR 2 (2026-09-23) ends the story.** Migration 217 arms the guard
from reviewed code, lets 190 drop `gtd_items` and `gtd_waiting`, and drops
the rest of the store. The last section fences exactly what it drops and
what must survive, and proves the replayed ladder against a real Postgres.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
S3B = MIGRATIONS / "189_gtd_backfill_to_pm.sql"
S3B_FIX = MIGRATIONS / "212_gtd_backfill_owns_statuses.sql"
S3C = MIGRATIONS / "190_gtd_retirement_drop.sql"

#: Every file that (re)defines the backfill. Each one must define-and-never-call,
#: because each one runs on the ladder unattended.
DEFINERS = [S3B, S3B_FIX]
DEFINER_IDS = [p.name.split("_")[0] for p in DEFINERS]

_DEFINES = "CREATE OR REPLACE FUNCTION gtd_backfill_to_pm"


def sql(path: Path) -> str:
    """The file with ``--`` comments stripped.

    Essential rather than tidy: both headers quote the very commands the tests
    below forbid, because a runbook belongs next to the thing it runs. Without
    stripping, every one of these fences would fail on its own documentation.
    """
    text = io.open(path, encoding="utf-8").read()
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


def function_body(path: Path) -> str:
    """The text from the function's definition to the end of the file."""
    body = sql(path)
    return body[body.index(_DEFINES):]


def ladder_definitions() -> list[Path]:
    """Every numbered ladder file that defines the backfill, in ladder order.

    Discovered from the filesystem rather than transcribed, so a later
    redefinition cannot slip past this suite unnoticed (root CLAUDE.md §5:
    mirrors go stale and then lie). Ordered by the numeric prefix, which is the
    order `apply_migrations.sh` applies them in (`sort -V`).
    """
    found = [
        p for p in MIGRATIONS.glob("[0-9][0-9]*_*.sql") if _DEFINES in sql(p)
    ]
    return sorted(found, key=lambda p: int(p.name.split("_")[0]))


def last_definition() -> Path:
    return ladder_definitions()[-1]


def test_all_three_migrations_exist() -> None:
    # Guards against the whole suite passing vacuously if a file is renamed.
    assert S3B.is_file(), f"missing {S3B}"
    assert S3B_FIX.is_file(), f"missing {S3B_FIX}"
    assert S3C.is_file(), f"missing {S3C}"


def test_the_ladder_defines_the_backfill_exactly_where_this_suite_says() -> None:
    """A third definition moves every body claim below; it must be registered."""
    assert ladder_definitions() == DEFINERS, (
        f"the ladder defines gtd_backfill_to_pm in {[p.name for p in ladder_definitions()]}, "
        f"this suite knows {[p.name for p in DEFINERS]}. The LAST definition is what "
        "production runs, so add the new file to DEFINERS and re-read every claim "
        "about the body (owns_statuses on the root, the root lookup, the tenant "
        "predicate) against it."
    )


def test_212_is_the_last_word_on_the_ladder() -> None:
    assert last_definition() == S3B_FIX, (
        "212 must be the LAST definition of gtd_backfill_to_pm on the ladder — it "
        "re-asserts 189's body with owns_statuses so that 197's CHECK admits the "
        "root. A later file redefining it must carry the same fix."
    )


# ── The property that keeps the owner-gate intact ───────────────────────────
#
# Parametrised over EVERY definer: 212 runs on the ladder exactly as 189 does,
# so a call in either would execute the gate unattended.

@pytest.mark.parametrize("path", DEFINERS, ids=DEFINER_IDS)
def test_each_definer_defines_the_backfill_but_never_calls_it(path: Path) -> None:
    body = sql(path)
    assert _DEFINES in body, f"{path.name} should define the backfill function"
    called = re.search(
        r"(SELECT|PERFORM)\s+\*?\s*(FROM\s+)?gtd_backfill_to_pm\s*\(", body
    )
    assert called is None, (
        f"{path.name} CALLS gtd_backfill_to_pm(). Running the move against a real "
        "database is OWNER-GATE (work_plan.md section 6 (f)); the deploy ladder "
        "applies migrations unattended, before services restart (R6). The "
        "migration must make the move POSSIBLE and leave a human to make it "
        "HAPPEN."
    )


@pytest.mark.parametrize("path", DEFINERS, ids=DEFINER_IDS)
def test_each_definer_writes_no_task_rows_at_apply_time(path: Path) -> None:
    """No top-level DML — every INSERT must live inside the function body."""
    body = sql(path)
    preamble = body[: body.index(_DEFINES)]
    for verb in ("INSERT INTO pm_", "UPDATE gtd_items", "INSERT INTO gtd_items"):
        assert verb not in preamble, (
            f"{path.name} runs `{verb}` at apply time — that is the data move "
            "happening on deploy rather than when the owner asks for it."
        )


# ── What 212 fixed — read from the LAST definition, which is what runs ───────

_INSERT_PROJECTS = re.compile(
    r"INSERT INTO pm_projects\s*\((?P<cols>[^)]*)\)\s*VALUES\s*"
    r"(?P<vals>.*?)(?:RETURNING|;)",
    re.S,
)


def project_inserts(path: Path) -> dict[str, tuple[list[str], str]]:
    """The root and child `INSERT INTO pm_projects`, keyed ``root``/``child``.

    Told apart by shape, not by position: the root names ``personal_owner``
    and no parent; the child names ``parent_project_id``.
    """
    out: dict[str, tuple[list[str], str]] = {}
    for m in _INSERT_PROJECTS.finditer(function_body(path)):
        cols = [c.strip() for c in m.group("cols").split(",")]
        key = "child" if "parent_project_id" in cols else "root"
        assert key not in out, f"{path.name} has two {key} inserts into pm_projects"
        out[key] = (cols, m.group("vals").strip())
    return out


def test_the_last_definition_inserts_the_root_with_owns_statuses_true() -> None:
    """The line production died on, 2026-09-23.

    197's CHECK (`parent_project_id IS NOT NULL OR owns_statuses`) refuses a
    root that owns nothing, and the column defaults to false. So the root
    insert must NAME the column and pass true — `ensure_personal_project` in
    routes/projects/personal.py is the shape of record.
    """
    path = last_definition()
    cols, vals = project_inserts(path)["root"]
    assert "personal_owner" in cols and "owns_statuses" in cols, (
        f"{path.name}: the personal ROOT insert must name owns_statuses — "
        "migration 197's CHECK pm_projects_root_owns_statuses refuses the row "
        "otherwise, which is exactly how the S3b move failed on production"
    )
    assert cols[-1] == "owns_statuses" and re.search(r"true\s*\)$", vals), (
        f"{path.name}: owns_statuses must be the LAST column of the root insert "
        f"and its value true (so the fence can read it). Got columns {cols}, "
        f"values ending {vals[-40:]!r}"
    )


def test_the_last_definition_inserts_the_child_inheriting_the_roots_lanes() -> None:
    """`mint_personal_child` is the shape of record: owns_statuses false, no
    status rows seeded for the child, and the task's status resolved against
    the ROOT — a category shares its root's four lanes."""
    path = last_definition()
    body = function_body(path)
    cols, vals = project_inserts(path)["child"]
    assert "personal_owner" in cols, (
        f"{path.name}: the child must carry personal_owner (191) or it appears "
        "on the company board"
    )
    assert cols[-1] == "owns_statuses" and re.search(r"false\s*\)$", vals), (
        f"{path.name}: the child insert must end with owns_statuses = false — a "
        f"category INHERITS its root's lanes. Got {cols}, values ending "
        f"{vals[-40:]!r}"
    )
    # Exactly ONE status seed, and it is the root's. A second `INSERT INTO
    # pm_task_statuses` would be a child seeding lanes of its own.
    seeds = re.findall(r"INSERT INTO pm_task_statuses", body)
    assert len(seeds) == 1, (
        f"{path.name} seeds pm_task_statuses {len(seeds)} times; the root's "
        "four lanes are the only set a personal tree has"
    )
    assert re.search(
        r"SELECT id INTO v_status FROM pm_task_statuses\s+WHERE project_id = v_proj",
        body,
    ), f"{path.name}: a task's status must resolve against the ROOT (v_proj)"


def test_the_last_definition_looks_the_root_up_as_a_root() -> None:
    """191's lesson, applied to the migration.

    Since 191 a member's categories carry `personal_owner` too, so a lookup on
    the address alone can answer with a child on a re-run — and the runbook
    RE-RUNS the move (step 8). `_load_personal_project` gained
    `parent_project_id IS NULL` for this; the function's own lookup must too.
    """
    path = last_definition()
    body = function_body(path)
    lookup = re.search(
        r"SELECT id INTO v_proj\s+FROM pm_projects\s+WHERE(?P<where>.*?);",
        body, re.S,
    )
    assert lookup is not None, f"{path.name}: the root lookup is missing"
    where = lookup.group("where")
    assert "parent_project_id IS NULL" in where, (
        f"{path.name}: the root lookup must say `parent_project_id IS NULL` — "
        "without it a re-run can pick a category as the root and mint a task "
        "under a node that owns no statuses. Got: " + " ".join(where.split())
    )
    assert "organization_id = v_owner.org" in where, (
        f"{path.name}: the root lookup lost its explicit tenant predicate"
    )


def test_the_last_definition_never_names_is_default() -> None:
    """196 retired `pm_task_statuses.is_default` from every reader and will
    drop the column. A function that still named it would break on that day,
    from inside the one move that cannot be re-run after S3c."""
    path = last_definition()
    assert "is_default" not in function_body(path), (
        f"{path.name} still names pm_task_statuses.is_default; the gateway "
        "stopped writing it on 2026-09-06 and 196 marks it for a drop"
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


# ── WS-39 S8 PR 2: migration 217 drops the planned set and nothing else ─────
#
# 190 dropped only `gtd_items` and `gtd_waiting`, and only when armed. 217 arms
# it from reviewed code, lets 190's guard decide, and then drops the rest of
# the store. These fences read 217 as text. `tests/live/live_ws39_s8d.py`
# proves the same against a seeded Postgres, including the refusal, and the
# live test at the end of this module proves the replayed ladder.

S8 = MIGRATIONS / "217_gtd_task_store_drop.sql"

#: Exactly what S8 drops (my_tasks_cutover.md §4.3). `gtd_items` and
#: `gtd_waiting` go through 190's guard, so they are listed apart.
S8_GUARDED = frozenset({"gtd_items", "gtd_waiting"})
S8_DROPS = frozenset({
    "gtd_projects", "gtd_spaces", "gtd_folders", "gtd_contexts",
    "gtd_retirement_arm",
})

#: What must survive the drop, with the authority that keeps it. The first
#: eight carried the `gtd_` prefix and were renamed in slices 1 and 2. The
#: last three were renamed in this same PR, in migrations 52 and 48.
KEEP = {
    "user_settings": "D53.6 — member settings, not a task row",
    "calendar_day_state": "D53.6 — Calendar state",
    "calendar_rollover_log": "D53.6 — Calendar state",
    "people": "the People directory (fetchPeople/createPerson)",
    "people_absences": "the People directory",
    "people_credentials": "the People directory",
    "people_resumes": "the People directory",
    "people_skills": "the People directory",
    "attachments": "the file registry My Tasks and Projects both write (§4.3)",
    "my_tasks_horizons": "D65 keeps the Horizons store (§4.3)",
    "my_tasks_reviews": "WS-18 keeps the Weekly Review store (§4.3)",
}


def _drops(path: Path) -> set[str]:
    return {
        t.lower()
        for t in re.findall(r"DROP TABLE\s+(?:IF EXISTS\s+)?(\w+)", sql(path), re.I)
    }


def test_217_is_on_the_ladder_as_the_one_s8_drop() -> None:
    assert S8.is_file(), f"missing {S8}"
    droppers = sorted(
        p.name for p in MIGRATIONS.glob("[0-9][0-9]*_*.sql")
        if re.search(r"DROP TABLE", sql(p), re.I)
    )
    assert droppers == [S3C.name, S8.name], (
        f"these ladder files drop tables: {droppers}. A third one is a new "
        "one-way act and needs its own fence."
    )


def test_217_drops_exactly_the_planned_set() -> None:
    dropped = _drops(S8)
    assert dropped == set(S8_DROPS | S8_GUARDED), (
        f"217 drops {sorted(dropped)}. The §4.3 map drops exactly "
        f"{sorted(S8_DROPS | S8_GUARDED)}. Anything else is a new decision."
    )


@pytest.mark.parametrize("table,why", sorted(KEEP.items()))
def test_no_migration_drops_a_table_that_survives(table: str, why: str) -> None:
    for path in (S3C, S8):
        assert table not in _drops(path), (
            f"{path.name} drops `{table}`, which must survive: {why}.\n"
            "D53.6 exists because a sweep of everything named `gtd_*` takes "
            "unrelated subsystems with it."
        )


def test_217_drops_without_cascade() -> None:
    for match in re.finditer(r"DROP (?:TABLE|VIEW|FUNCTION)[^;]*;", sql(S8), re.I):
        assert "CASCADE" not in match.group(0).upper(), (
            f"217 uses CASCADE: {match.group(0)!r}. A retirement should fail "
            "loudly on an unexpected dependent, not consume it."
        )


def test_217_is_one_transaction() -> None:
    """The column drop in (c) must not survive a refusal in (a). psql -f
    commits each statement unless the file says BEGIN, and the runner feeds
    the file to psql."""
    body = sql(S8).strip()
    assert body.startswith("BEGIN;") and body.endswith("COMMIT;")
    assert body.count("BEGIN;") == 1 and body.count("COMMIT;") == 1


def test_217_arms_then_lets_190s_guard_decide() -> None:
    """The arm moved from a hand INSERT into reviewed code. The data check
    did not move: 190's guard still refuses on an unmigrated row."""
    body = sql(S8)
    arm = body.index("INSERT INTO gtd_retirement_arm")
    call = body.index("PERFORM gtd_retirement_drop()")
    assert arm < call, "217 must arm BEFORE it calls the guard"
    assert "'migration 217 (WS-39 S8, D73)'" in body, (
        "the arm row must name the migration, so the audit says who armed it"
    )
    assert "RAISE EXCEPTION" in body[call:], (
        "217 must refuse when the guard returns and the store is still there"
    )
    # The guard is the only path that drops a table with rows in it.
    assert "DELETE FROM gtd_items" not in body
    assert "TRUNCATE" not in body.upper()


#: gtd_items columns the backfill (212) never copies. A value in any of them
#: would be lost, so 217 refuses. `flexible` is exempt: NULL reads as flexible.
UNCOPIED = (
    "origin", "attachments", "sort_key", "important", "leveraged", "kept_mine", "deep_work",
    "scheduled_start", "scheduled_end", "actual_start", "actual_end",
    "parent_item_id", "archived_at", "workflow_stage", "assignees", "horizon_id",
)


def test_217_refuses_a_value_the_backfill_never_copied() -> None:
    body = sql(S8)
    block = body[body.index("$s8_uncopied$"):body.rindex("$s8_uncopied$")]
    for column in UNCOPIED:
        assert f"('{column}'," in block, f"217 does not check gtd_items.{column}"
    assert "'flexible'" not in block
    assert "RAISE EXCEPTION" in block
    # It runs before anything changes, so a refusal leaves the box as it was.
    assert body.index("$s8_uncopied$") < body.index("$s8_commitments$")


def test_217_refuses_a_tree_table_that_holds_rows() -> None:
    body = sql(S8)
    block = body[body.index("$s8_tree_empty$"):body.rindex("$s8_tree_empty$")]
    for table in ("gtd_projects", "gtd_spaces", "gtd_folders", "gtd_contexts"):
        assert f"'{table}'" in block, table
    assert "RAISE EXCEPTION" in block
    assert body.rindex("$s8_tree_empty$") < body.index(
        "DROP TABLE IF EXISTS gtd_projects;"), "the check must run before the drop"


def test_217_remaps_a_task_actions_dispatch_ref_before_the_drop() -> None:
    """Migration 129 put the gtd id in `action_item.dispatch_ref` for
    `kind = 'task'`. It must name the pm task before gtd_items goes."""
    body = sql(S8)
    remap = body.index("SET dispatch_ref = i.migrated_task_id::text")
    assert remap < body.index("PERFORM gtd_retirement_drop()")
    block = body[body.index("$s8_dispatch_ref$"):body.rindex("$s8_dispatch_ref$")]
    assert "a.kind = 'task'" in block
    assert "JOIN pm_tasks t ON t.id = i.migrated_task_id" in block


def test_217_moves_the_commitment_before_the_drop() -> None:
    """`task_id` is filled from `gtd_items.migrated_task_id`, so the copy must
    run while `gtd_items` exists, and only onto a pm_tasks row that exists."""
    body = sql(S8)
    copy = body.index("SET task_id = i.migrated_task_id")
    assert copy < body.index("PERFORM gtd_retirement_drop()")
    assert "JOIN pm_tasks t ON t.id = i.migrated_task_id" in body
    assert body.index("SET task_id = i.migrated_task_id") < body.index(
        "ALTER TABLE wa_commitments DROP COLUMN gtd_item_id")


def test_217_drops_in_foreign_key_order() -> None:
    """No CASCADE, so a child must go before the table it references."""
    body = sql(S8)
    order = [body.index(f"DROP TABLE IF EXISTS {t};")
             for t in ("gtd_projects", "gtd_folders", "gtd_spaces")]
    assert order == sorted(order), "gtd_projects -> gtd_folders -> gtd_spaces"
    assert body.index("DROP VIEW     IF EXISTS gtd_backfill_plan") < body.index(
        "DROP TABLE IF EXISTS gtd_projects;"), (
        "the S3b preview reads gtd_projects, so it goes first")
    assert body.index("DROP TABLE    IF EXISTS gtd_retirement_arm") < body.index(
        "DROP FUNCTION IF EXISTS gtd_retirement_drop()")


#: Migration 48's text, line endings folded, as this PR leaves it.
_48_SHA256 = "af96cf9a4d8ea14fb7d9ace2ef5c05e7baf03b0f3493a9a7fdceefd763adfe76"


def test_an_edit_to_48_is_a_decision_about_217() -> None:
    """48 still CREATEs the dropped store, because a fresh ladder needs it
    before 217 drops it. The runner re-runs a file whose checksum changed.
    So an edit to 48 alone rebuilds `gtd_items`, `gtd_waiting`,
    `gtd_projects` and `gtd_contexts` on production, EMPTY, and 217 does not
    run again to drop them. Nothing fails. The tables are simply back.

    The fix is to touch 217 in the same PR, so it re-runs too. It drops an
    empty rebuilt store and refuses one that holds rows. Then update the hash.
    """
    import hashlib

    text = (MIGRATIONS / "48_task_manager_gtd.sql").read_text(encoding="utf-8")
    digest = hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest()
    assert digest == _48_SHA256, (
        "migration 48 changed. Read this test's docstring: touch 217 in the "
        "same PR, or the dropped gtd_ tables come back empty on production."
    )


def test_the_survivors_are_created_under_their_new_names() -> None:
    """The three renames live in their creating migrations. The mechanism and
    its fence are `test_gtd_rename_upgrade.py`, and this only cross-checks."""
    from tests.unit.test_gtd_rename_upgrade import RENAMED

    for new in ("attachments", "my_tasks_horizons", "my_tasks_reviews"):
        assert new in RENAMED, f"{new} is not registered in RENAMED"


# ── R8: the replayed ladder has no gtd_ table ───────────────────────────────

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()


@pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)
def test_the_replayed_ladder_has_no_gtd_table_and_keeps_the_survivors() -> None:
    """`\\dt gtd_*` after a full ladder replay returns nothing (§5 S8, done
    when 2). The ladder here is replayed WITHOUT a ledger, so migration 48
    builds the old store again and 217 must drop it again."""
    from sqlalchemy import create_engine, text

    from tests.unit._tenant_ladder import apply_ladder

    engine = create_engine(_URL, future=True)
    try:
        with engine.begin() as conn:
            apply_ladder(conn)
        with engine.connect() as conn:
            left = conn.execute(text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'v') "
                "AND c.relname LIKE 'gtd%'")).scalars().all()
            assert left == [], f"the ladder still builds {left}"
            funcs = conn.execute(text(
                "SELECT proname FROM pg_proc WHERE proname IN "
                "('gtd_backfill_to_pm', 'gtd_retirement_drop')")).scalars().all()
            assert funcs == [], f"the ladder still defines {funcs}"
            column = conn.execute(text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'wa_commitments' "
                "AND column_name = 'gtd_item_id'")).scalar()
            assert column == 0, "wa_commitments.gtd_item_id survived the ladder"
            for table in sorted(KEEP):
                assert conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"),
                                    {"t": f"public.{table}"}).scalar(), table
    finally:
        engine.dispose()


# ── Tenancy: a migration has no RLS to fall back on ─────────────────────────

@pytest.mark.parametrize("path", DEFINERS, ids=DEFINER_IDS)
def test_each_definer_resolves_the_tenant_from_the_directory(path: Path) -> None:
    """``organization_id`` must come from ``app_user``, never be inferred.

    §12.8's named failure is that a mis-mapped ``member_email`` publishes one
    member's private task into another's lens. ``pm_*`` RLS is bound at the
    ``get_db()`` seam; a migration runs as the database owner and bypasses it,
    so every tenant predicate has to be explicit in the SQL.
    """
    body = sql(path)
    assert re.search(r"JOIN\s+app_user\s+u\s+ON\s+lower\(u\.email\)", body), (
        f"{path.name} must resolve the owner by folded email against app_user — "
        "the globally-unique key (D-MT-1(a)) that makes the mapping a function "
        "rather than a guess"
    )
    assert "u.organization_id" in body, (
        f"{path.name} must take organization_id from the resolved app_user row"
    )
    assert "AND organization_id = v_owner.org" in body, (
        f"{path.name}'s personal-project lookup must carry an explicit tenant "
        "predicate. The gateway's `_load_personal_project` deliberately omits "
        "one and says why (RLS scopes the read) — neither reason protects a "
        "migration."
    )


def test_189_refuses_rather_than_guesses_an_owner() -> None:
    """The VIEW is 189's alone — 212 re-asserts the function, not the plan."""
    body = sql(S3B)
    assert "unmappable" in body, (
        "189 must mark rows it cannot resolve as `unmappable` rather than "
        "assigning them. `_uid` writes the literal 'anonymous' for an "
        "unauthenticated capture; handing that to somebody is the §12.8 failure."
    )


@pytest.mark.parametrize("path", DEFINERS, ids=DEFINER_IDS)
def test_each_definer_leads_with_what_the_plan_refuses(path: Path) -> None:
    """Every definition of the function must consult the plan's verdict and
    report the refused rows FIRST — the owner loop below it only ever joins
    `app_user`, so an unresolvable row is never assigned, but a run that buried
    the refusal under the successes would let it go unread."""
    body = function_body(path)
    assert "verdict <> 'mappable'" in body, (
        f"{path.name}'s function must count gtd_backfill_plan rows whose verdict "
        "is not `mappable` — that is how an unresolvable owner is refused "
        "rather than guessed"
    )
    assert body.index("'REFUSED'") < body.index("FOR v_owner IN"), (
        f"{path.name} must report the refused rows BEFORE the owner loop runs"
    )
