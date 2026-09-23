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
"""
from __future__ import annotations

import io
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


# ── D53.6 and its neighbours — what a sweep must not take ───────────────────

#: Every table sharing the `gtd_` prefix that is NOT part of this retirement,
#: with the authority that keeps it. The prefix is the only thing four unrelated
#: subsystems have in common, which is exactly why this list is written out.
KEEP = {
    "user_settings": "D53.6 — Calendar state, not a task row",
    "calendar_day_state": "D53.6 — Calendar state",
    "calendar_rollover_log": "D53.6 — Calendar state",
    "people": "the People directory (fetchPeople/createPerson)",
    "people_absences": "the People directory",
    "people_credentials": "the People directory",
    "people_resumes": "the People directory",
    "people_skills": "the People directory",
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
