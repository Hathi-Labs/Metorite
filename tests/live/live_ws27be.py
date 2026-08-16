"""WS-27be against a REAL Postgres — the query-plan claims, measured.

**R8 exists for exactly this ticket.** Every assertion below is about what the
*planner* does, and a hermetic fake has no planner: it agrees with whatever SQL
it is handed, so a unit test proving "the SQL says ILIKE" would have stayed green
through the entire four-month period in which `idx_pm_tasks_fts` served nothing.

What only a database can answer:

* does `CREATE EXTENSION pg_trgm` + `gin_trgm_ops` actually make the planner
  choose an index for `title ILIKE '%…%' OR description ILIKE '%…%'`, or does it
  keep the sequential scan it had? (Both are plausible; a small table keeps the
  scan, and *that* is the honest outcome the ticket asks us to report.)
* does it still choose the index when the pattern arrives as a BOUND PARAMETER
  through asyncpg rather than as the literal a hand-written `EXPLAIN` splices in?
  A generic plan built without the parameter's value cannot know the pattern is
  selective, and this is the difference between a measurement and a fix.
* does the third OR arm — `task_number = :number`, which `search.py` adds the
  moment somebody types `#42` — leave the whole disjunction un-servable?
* does the index defeat the tenant predicate? An index scan that finds a row and
  then relies on a filter to remove it is still correct, but it is worth an
  assertion that org A cannot see org B's matching task through the new path.
* is the migration idempotent — it is replayed on every ladder run.

⚠️ This script SEEDS VOLUME (60k tasks by default) and `TRUNCATE pm_projects
CASCADE`s first. Point it at a scratch database. It creates nothing it does not
also clean up, but the truncate is unconditional.

Running it::

    createdb -h /var/tmp -p 55432 -U postgres ccws27be
    pg_dump -h /var/tmp -p 55432 -U postgres -s cc \\
      | psql -h /var/tmp -p 55432 -U postgres -d ccws27be
    uv run python tests/live/live_ws27be.py

`WS27BE_DATABASE_URL` overrides the target; `WS27BE_ROWS` the volume.
"""
import asyncio
import os
import pathlib
import re
import sys

os.environ["DATABASE_URL"] = os.environ.get(
    "WS27BE_DATABASE_URL",
    "postgresql+asyncpg://postgres@/ccws27be?host=/var/tmp&port=55432",
)
# Derived from THIS file, not hard-coded: run from a git worktree, an absolute
# `/home/user/Metorite/...` path silently imports the MAIN checkout's
# gateway and the script then reports on code that is not under test.
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps" / "services" / "gateway"))

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from acb_common.db import bind_tenant, release_tenant  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import search as pm_search  # noqa: E402
from gateway.routes.projects import tasks as pm_tasks  # noqa: E402
from gateway.routes.projects.core import (  # noqa: E402
    Page,
    resolve_visibility,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.filters import (  # noqa: E402
    MIN_QUERY,
    build_task_filters,
)
from sqlalchemy import text  # noqa: E402

ROWS = int(os.environ.get("WS27BE_ROWS", "60000"))

ANA = "ana@be-alpha.example"
BEN = "ben@be-beta.example"

A_PROJ = "be27be00-0000-4000-8000-0000000000a1"
B_PROJ = "be27be00-0000-4000-8000-0000000000b1"
A_STATUS = "be27be00-0000-4000-8000-0000000000a2"
B_STATUS = "be27be00-0000-4000-8000-0000000000b2"

#: A token planted in ~0.1% of org A's titles, and in exactly one of org B's.
#: Rare on purpose: an index is only the right plan for a selective predicate,
#: and a term matching a third of the table SHOULD keep the sequential scan.
RARE = "quicksilver"

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def note(label, value):
    """A measurement that is reported but not asserted on."""
    print(f"    · {label}: {value}")


def millis(plan: str) -> float:
    """`Execution Time: 609.121 ms` → 609.121."""
    found = re.search(r"Execution Time: ([\d.]+) ms", plan)
    return float(found.group(1)) if found else -1.0


def visited(plan: str) -> int:
    """The largest `Rows Removed by Filter` in a plan.

    The number that says "this predicate was applied to every row it could
    have been an index condition for" — which is the whole defect, and the
    thing an `Index Cond` line replaces.
    """
    counts = [int(n) for n in re.findall(r"Rows Removed by Filter: (\d+)", plan)]
    return max(counts) if counts else 0


def user(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects"]),
    )


def migration_sql() -> list[str]:
    """The trigram migration, found by CONTENT and split into statements.

    By content because R1 forbids writing a migration number down; split
    because asyncpg refuses more than one statement per prepared execution.
    Comments are stripped BEFORE the split for a subtler reason: they contain
    `:n` and `:number`, and SQLAlchemy's `text()` would read those as bind
    parameters and demand values for them.
    """
    found = [
        path for path in sorted((REPO / "infra" / "postgres").glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "gin_trgm_ops" in path.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, f"expected one trigram migration, found {found}"
    bare = "\n".join(
        re.sub(r"--.*$", "", line)
        for line in found[0].read_text(encoding="utf-8").splitlines()
    )
    return [s.strip() for s in bare.split(";") if s.strip()]


#: The indexes the migration adds. Dropped before the "before" measurement so
#: this script measures the real transition rather than trusting the tree.
NEW_INDEXES = (
    "idx_pm_tasks_title_trgm",
    "idx_pm_tasks_description_trgm",
    "idx_pm_tasks_task_number",
)


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        await db.execute(text(
            "DELETE FROM app_user WHERE email IN (:a, :b)"), {"a": ANA, "b": BEN})
        await db.execute(text(
            "DELETE FROM organization WHERE slug IN ('be_alpha', 'be_beta')"))
        orgs = {}
        for slug in ("be_alpha", "be_beta"):
            row = (await db.execute(text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"), {"s": slug})).fetchone()
            orgs[slug] = str(row.id)
        for email, slug in ((ANA, "be_alpha"), (BEN, "be_beta")):
            await db.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": orgs[slug]})
        for pid, slug, sid, owner in (
            (A_PROJ, "be_alpha", A_STATUS, ANA), (B_PROJ, "be_beta", B_STATUS, BEN),
        ):
            await db.execute(text(
                "INSERT INTO pm_projects (id, name, source, created_by, "
                "organization_id) VALUES (CAST(:p AS uuid), :p, 'manual', :o, "
                "CAST(:g AS uuid))"), {"p": pid, "o": owner, "g": orgs[slug]})
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:p AS uuid), 'org', :o)"), {"p": pid, "o": owner})
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:s AS uuid), "
                "CAST(:p AS uuid), 'To do', 10, 'todo', true)"),
                {"s": sid, "p": pid})

        # Volume in ONE statement — 60k round trips is a script nobody runs.
        # `g % 900 = 0` plants the rare token in ~0.1% of rows; the description
        # deliberately never carries it, so the description arm of the BitmapOr
        # is the one that returns nothing and the plan still has to include it.
        await db.execute(text(
            "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
            "  title, description, task_number, created_by, source) "
            "SELECT gen_random_uuid(), CAST(:p AS uuid), CAST(:p AS uuid), "
            "       CAST(:s AS uuid), "
            "       (ARRAY['Parser rewrite','Fix the crash','Rename module',"
            "              'Ship units','Cut latency','Invoice reconciliation',"
            "              'Migrate the ledger','Audit the roster',"
            "              'Refactor the scheduler','Backfill the cursor'"
            "        ])[1 + (g % 10)] || ' ' || g "
            f"       || CASE WHEN g % 900 = 0 THEN ' {RARE}' ELSE '' END, "
            "       'Routine follow-up work for item ' || g, "
            "       g, :o, 'manual' "
            "  FROM generate_series(1, :n) g"),
            {"p": A_PROJ, "s": A_STATUS, "o": ANA, "n": ROWS})
        # One beta task carrying the same rare token. Anything that tells the
        # two apart afterwards can only be the tenant predicate.
        await db.execute(text(
            "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
            "  title, task_number, created_by) VALUES (gen_random_uuid(), "
            "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), "
            f"'Beta {RARE} secret', 1, :o)"),
            {"p": B_PROJ, "s": B_STATUS, "o": BEN})
        await db.commit()
        await db.execute(text("ANALYZE pm_tasks"))
        await db.commit()
        return orgs
    finally:
        await db.close()


async def drop_new_indexes(db):
    for name in NEW_INDEXES:
        await db.execute(text(f"DROP INDEX IF EXISTS {name}"))
    await db.commit()


async def apply_migration(db):
    for statement in migration_sql():
        await db.execute(text(statement))
    await db.commit()
    await db.execute(text("ANALYZE pm_tasks"))
    await db.commit()


async def explain(db, sql: str, params: dict) -> str:
    """The plan for the statement the endpoint actually runs.

    ⚠️ `EXPLAIN (ANALYZE)` here — not `EXPLAIN` alone — and with the caller's
    real bound parameters, because the question is whether the planner picks the
    index when the pattern arrives through asyncpg rather than spliced in as a
    literal. A hand-written EXPLAIN with the term typed into the SQL answers a
    different question than the one that matters.
    """
    rows = (await db.execute(
        text(f"EXPLAIN (ANALYZE, BUFFERS) {sql}"), params,
    )).fetchall()
    return "\n".join(r[0] for r in rows)


def search_statement(vis, *, include_triage=False, exclude="") -> str:
    return pm_search._SEARCH_SQL.format(
        visible=task_visibility_clause(vis),
        triage="TRUE" if include_triage else triage_exclusion_clause(),
        exclude=exclude,
    )


def search_params(vis, term: str, cap: int = 51) -> dict:
    from gateway.routes.projects.filters import like_escape
    escaped = like_escape(term)
    return {
        **vis.params, "term": f"%{escaped}%", "prefix": f"{escaped}%",
        "number": pm_search.task_number(term), "cap": cap,
    }


def list_statement(vis, **filters) -> tuple[str, dict]:
    """The list endpoint's shape, built through the SHARED filter builder so
    this measures the query `/projects/tasks` runs and not a paraphrase."""
    clauses, params = build_task_filters(**filters)
    where = " AND ".join([task_visibility_clause(vis), *clauses])
    return (
        f"SELECT t.id FROM pm_tasks t WHERE {where} "
        f"ORDER BY t.updated_at DESC, t.id LIMIT 51"
    ), {**vis.params, **params}


PLANS: dict[str, str] = {}


async def main():
    orgs = await seed()
    print(f"seeded {ROWS} tasks in be_alpha, 1 in be_beta\n")

    token = bind_tenant(orgs["be_alpha"])
    db = await get_db()
    try:
        vis = await resolve_visibility(db, user(ANA))

        # ── before ──────────────────────────────────────────────────────────
        await drop_new_indexes(db)
        word_sql = search_statement(vis)
        PLANS["search · word · BEFORE"] = await explain(
            db, word_sql, search_params(vis, RARE))
        PLANS["search · #number · BEFORE"] = await explain(
            db, word_sql, search_params(vis, "1234"))
        lsql, lparams = list_statement(vis, q=RARE)
        PLANS["list ?q= · BEFORE"] = await explain(db, lsql, lparams)

        # ⚠️ The claim is NOT "there is a Seq Scan node". Postgres reached
        # `pm_tasks` through `idx_pm_tasks_project_id` here — a full index scan,
        # which walks every row just the same. The defect is that the ILIKE is a
        # FILTER rather than an `Index Cond`, and the count of rows it threw
        # away is the measurement of it.
        check("BEFORE: the ILIKE is a filter, never an index condition",
              "Index Cond: (title ~~*" in PLANS["search · word · BEFORE"], False)
        check("BEFORE: it is applied to (nearly) every task in the tenant",
              visited(PLANS["search · word · BEFORE"]) > ROWS * 0.9, True)
        check("BEFORE: the dead full-text index is not used",
              "idx_pm_tasks_fts" in PLANS["search · word · BEFORE"], False)

        # ── the migration ───────────────────────────────────────────────────
        await apply_migration(db)
        check("pg_trgm is installed",
              bool((await db.execute(text(
                  "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
              ))).fetchone()), True)
        # Replayed on every ladder run, so it must survive a second pass.
        await apply_migration(db)
        check("the migration is idempotent", True, True)

        for name in NEW_INDEXES:
            check(f"{name} exists",
                  bool((await db.execute(text(
                      "SELECT 1 FROM pg_class WHERE relname = :n"), {"n": name},
                  )).fetchone()), True)
        check("idx_pm_tasks_fts is still there (R6: additive only)",
              bool((await db.execute(text(
                  "SELECT 1 FROM pg_class WHERE relname = 'idx_pm_tasks_fts'"
              ))).fetchone()), True)

        # ── after ───────────────────────────────────────────────────────────
        PLANS["search · word · AFTER"] = await explain(
            db, word_sql, search_params(vis, RARE))
        PLANS["search · #number · AFTER"] = await explain(
            db, word_sql, search_params(vis, "1234"))
        lsql, lparams = list_statement(vis, q=RARE)
        PLANS["list ?q= · AFTER"] = await explain(db, lsql, lparams)

        check("AFTER: the ILIKE is now an INDEX CONDITION",
              "Index Cond: (title ~~*" in PLANS["search · word · AFTER"], True)
        check("AFTER: the search reaches pm_tasks through the trigram index",
              "idx_pm_tasks_title_trgm" in PLANS["search · word · AFTER"], True)
        check("AFTER: the description arm is indexed too",
              "idx_pm_tasks_description_trgm" in PLANS["search · word · AFTER"],
              True)
        check("AFTER: the list endpoint's ?q= uses it as well",
              "idx_pm_tasks_title_trgm" in PLANS["list ?q= · AFTER"], True)
        check("AFTER: a numeric query is served too — the third OR arm",
              "idx_pm_tasks_task_number" in PLANS["search · #number · AFTER"],
              True)
        check("AFTER: nothing is scanned row by row any more",
              max(visited(v) for k, v in PLANS.items()
                  if k.endswith("AFTER")) < ROWS * 0.1, True)

        # ── the boundary this fix does NOT reach, measured not assumed ──────
        #
        # `pg_trgm` extracts whole trigrams from the pattern, and `%ab%` has
        # none. MIN_QUERY is 2, so the shortest ACCEPTED query is the longest
        # one the index cannot serve. Reported rather than asserted green:
        # raising the minimum is a product decision, recorded as owed.
        # ⚠️ The planner may still CHOOSE the index for a 2-character pattern —
        # and it does. That is not the same as the index helping: with no whole
        # trigram to look up, the Bitmap Index Scan returns every entry and the
        # query costs what it always did. "Uses the index" is the wrong question;
        # the honest one is what it costs, so both are printed.
        two = "qu"
        assert len(two) == MIN_QUERY
        lsql, lparams = list_statement(vis, q=two)
        PLANS["list ?q=qu (MIN_QUERY) · AFTER"] = await explain(db, lsql, lparams)
        note("2-char query: index named in the plan",
             "idx_pm_tasks_title_trgm"
             in PLANS["list ?q=qu (MIN_QUERY) · AFTER"])
        note("2-char query: ms",
             millis(PLANS["list ?q=qu (MIN_QUERY) · AFTER"]))
        note("3+-char query: ms", millis(PLANS["list ?q= · AFTER"]))

        # ── the minimum, end to end ─────────────────────────────────────────
        lsql, lparams = list_statement(vis, q="a")
        PLANS["list ?q=a (below MIN_QUERY) · AFTER"] = await explain(
            db, lsql, lparams)
        check("a query below the minimum never touches pm_tasks",
              "One-Time Filter: false"
              in PLANS["list ?q=a (below MIN_QUERY) · AFTER"], True)

        listed = await pm_tasks.list_tasks(
            user=user(ANA), q="a", page=Page(page=1, page_size=50))
        check("the LIST endpoint answers empty, not everything",
              (listed.rows, listed.total), ([], 0))
        unfiltered = await pm_tasks.list_tasks(
            user=user(ANA), page=Page(page=1, page_size=1))
        check("…and the same call without q is NOT empty",
              unfiltered.total, ROWS)

        hits = await pm_search.search_tasks(q="a", user=user(ANA))
        check("search still answers empty for the same query", hits["rows"], [])

        # ── the tenant predicate survives the new index path ────────────────
        found = await pm_search.search_tasks(q=RARE, user=user(ANA))
        titles = [r["title"] for r in found["rows"]]
        check("alpha finds its own matches", len(titles) > 0, True)
        check("alpha cannot see beta's matching task",
              [t for t in titles if t.startswith("Beta ")], [])
    finally:
        await db.close()
        release_tenant(token)

    token = bind_tenant(orgs["be_beta"])
    db = await get_db()
    try:
        found = await pm_search.search_tasks(q=RARE, user=user(BEN))
        titles = [r["title"] for r in found["rows"]]
        check("beta finds exactly its one task through the same index",
              titles, [f"Beta {RARE} secret"])
    finally:
        await db.close()
        release_tenant(token)

    print("\n" + "=" * 78)
    print(f"{'plan':<44} {'ms':>10} {'rows filtered':>14}")
    for label, plan in PLANS.items():
        print(f"{label:<44} {millis(plan):>10.3f} {visited(plan):>14}")

    print("\n" + "=" * 78)
    for label, plan in PLANS.items():
        print(f"\n--- {label} " + "-" * max(0, 70 - len(label)))
        print(plan)
    print("\n" + "=" * 78)
    print("\n" + ("FAILURES: " + ", ".join(failures) if failures else "all green"))
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
