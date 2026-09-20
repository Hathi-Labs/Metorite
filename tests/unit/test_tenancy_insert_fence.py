"""No new migration may INSERT into a tenant-scoped table without naming it.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1j · HANDOFF H-104.

⚠️ **This fence exists because the same defect reached production THREE TIMES,
one deploy apart, and no test could see any of them.**

The tenancy phase (``infra/postgres/generated/``) adds ``organization_id`` to
140 tables and makes it NOT NULL. Those files are **not on the ladder** — the
runner globs ``[0-9][0-9]*_*.sql`` in ``infra/postgres`` only — so production
carries a shape that no developer database, and no CI run, has ever had. Every
``INSERT`` into one of those 140 tables that does not name ``organization_id``
is therefore a **latent production failure that passes every test**.

That is exactly how it went:

* Migration **200** fixed ``provision_org_roles`` (``org_role_permission``).
  Deployed. Provisioning then failed one statement later.
* Migration **201** fixed ``provision_org_owner`` (``user_role``). The fence for
  200 added the column to ONE table, so it could not see the second head.

Fixing heads one at a time is what made this slow. **This fence counts them all
at once**, and fails the build when a new one appears.

## What it does NOT do

It does not demand the tree be clean. Root ``CLAUDE.md`` §5: *existing
violations are findings for the board, not refactor targets*. So this is a
**ratchet** — the omissions that exist today are recorded below with a reason,
and the build fails on a **new** one. Fixing a grandfathered entry is a
one-line edit here, and the fence fails if an entry goes stale, so the list
cannot rot into a lie.

## Why a text scan and not a database check

A database check can only see the schema it is pointed at, and the whole defect
is that we point tests at the wrong one. The ladder's text is the same on every
machine, so this answers the same way in CI, on Windows, and on the box.
"""
from __future__ import annotations

import os
import re

#: Repository root — this file is ``<root>/tests/unit/test_tenancy_insert_fence.py``.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LADDER_DIR = os.path.join(_ROOT, "infra", "postgres")
_ADD_COLUMNS = os.path.join(_LADDER_DIR, "generated", "01_add_columns.sql")

_NUMBERED = re.compile(r"^(\d+)_.*\.sql$")
_ALTER = re.compile(r"^ALTER TABLE (\w+)", re.M)
#: ``INSERT INTO <table> ( <columns> )``. Deliberately blind to INSERT…SELECT
#: without a column list: that form cannot omit a column, it can only supply the
#: wrong number, which Postgres refuses on the ladder as loudly as in production.
_INSERT = re.compile(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)", re.I | re.S)


def tenancy_tables() -> frozenset[str]:
    """The 140 tables the tenancy phase scopes — DISCOVERED, never transcribed.

    Read from the generator's own output, so a table added to the tenancy phase
    is covered by this fence on the same commit that adds it. A hand-copied list
    is the failure mode ``_customer_console_ladder.py``'s docstring records.
    """
    with open(_ADD_COLUMNS, encoding="utf-8") as fh:
        found = frozenset(_ALTER.findall(fh.read()))
    if len(found) < 100:
        raise RuntimeError(
            f"only {len(found)} tenancy tables discovered in {_ADD_COLUMNS} — "
            "the generated phase names 140, so the parse is wrong and this "
            "fence would pass vacuously"
        )
    return found


_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")


def strip_sql_comments(text: str) -> str:
    """Remove ``--`` and ``/* */`` comments, keeping strings and bodies intact.

    ⚠️ **Not optional, and the first draft of this fence got it wrong.** These
    migrations quote the very error they fix in their header comment — 200's
    docstring contains the literal text ``INSERT INTO org_role_permission
    (role_id, permission)``. A scan over raw text counted that prose as code,
    reported 2 omissions where there is 1, and would have let the ratchet drift
    by exactly the number of times someone explained the bug.

    Single-quoted strings and ``$tag$`` bodies are passed through, because a
    ``--`` inside either is data, not a comment. Newlines inside a comment are
    kept so nothing downstream needs to care about line numbers.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_string = in_line = in_block = False
    dollar: str | None = None
    while i < n:
        char, pair = text[i], text[i : i + 2]
        if in_line:
            if char == "\n":
                in_line = False
                out.append(char)
            i += 1
        elif in_block:
            if pair == "*/":
                in_block = False
                i += 2
                continue
            if char == "\n":
                out.append(char)
            i += 1
        elif dollar is not None:
            if text.startswith(dollar, i):
                out.append(dollar)
                i += len(dollar)
                dollar = None
                continue
            out.append(char)
            i += 1
        elif in_string:
            out.append(char)
            if char == "'":
                if text[i + 1 : i + 2] == "'":  # '' is an escaped quote
                    out.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
        elif tag := _DOLLAR_TAG.match(text, i):
            dollar = tag.group(0)
            out.append(dollar)
            i += len(dollar)
        elif char == "'":
            in_string = True
            out.append(char)
            i += 1
        elif pair == "--":
            in_line = True
            i += 2
        elif pair == "/*":
            in_block = True
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def omissions() -> dict[tuple[str, str], int]:
    """``(migration file, table) -> count`` of INSERTs that omit the column."""
    tables = tenancy_tables()
    found: dict[tuple[str, str], int] = {}
    for name in sorted(os.listdir(_LADDER_DIR)):
        if not _NUMBERED.match(name):
            continue
        with open(os.path.join(_LADDER_DIR, name), encoding="utf-8") as fh:
            text = strip_sql_comments(fh.read())
        for match in _INSERT.finditer(text):
            table, columns = match.group(1), match.group(2)
            if table not in tables:
                continue
            if re.search(r"\borganization_id\b", columns, re.I):
                continue
            found[(name, table)] = found.get((name, table), 0) + 1
    return found


#: ⚠️ **The ratchet.** ``(file, table) -> (count, reason)``, measured 2026-09-15.
#:
#: Every entry is one of three things, and the reason says which:
#:
#: * **GUARDED** — the file tests for the column and has a matching arm that
#:   names it. Correct as written, and the ELSE arm is what gets deleted the day
#:   the generated files join the ladder.
#: * **SUPERSEDED** — an older definition of a function that a later migration
#:   replaces. Harmless while the later one applies after it.
#: * **SEED** — a one-time data seed that already ran on production BEFORE the
#:   tenancy phase, so it is in the ledger and never replays there. ⚠️ It WOULD
#:   fail a fresh install against a tenancy-applied database. That is H-104's
#:   other half, and it stays open.
_GRANDFATHERED: dict[tuple[str, str], tuple[int, str]] = {
    ("130_org_access_control.sql", "org_role_permission"): (6, "SEED"),
    ("130_org_access_control.sql", "user_role"): (2, "SEED"),
    ("131_integration_memory_permissions.sql", "org_role_permission"): (4, "SEED"),
    ("133_workflows_publish_permission.sql", "org_role_permission"): (3, "SEED"),
    ("138_groups_and_session_participants.sql", "chat_session_participant"): (2, "SEED"),
    ("139_room_authorship_and_agents.sql", "chat_session_agent"): (1, "SEED"),
    ("144_crm.sql", "crm_deal_statuses"): (1, "SEED"),
    ("144_crm.sql", "crm_lead_statuses"): (1, "SEED"),
    ("144_crm.sql", "crm_lost_reasons"): (1, "SEED"),
    ("156_projects_tags.sql", "pm_tags"): (1, "SEED"),
    ("178_billing_purchase_permission.sql", "org_role_permission"): (2, "SEED"),
    ("179_org_provisioning.sql", "org_role_permission"): (1, "SUPERSEDED by 200"),
    ("179_org_provisioning.sql", "user_role"): (1, "SUPERSEDED by 201"),
    ("180_org_provisioning_create_only_guard.sql", "user_role"): (1, "SUPERSEDED by 201"),
    ("196_projects_status_sets.sql", "org_role_permission"): (1, "SEED"),
    ("200_provision_org_roles_tenancy.sql", "org_role_permission"): (1, "GUARDED"),
    ("201_provision_org_owner_tenancy.sql", "user_role"): (1, "GUARDED"),
    # Two arms, both guarded, and they guard DIFFERENTLY on purpose:
    # the trigger asks `pg_attribute` at RUN time (so promoting the
    # tenancy layer cannot leave it stale — see the migration header for
    # the 36-error regression that taught us), and the backfill asks
    # `information_schema.columns` once, the way 200 and 201 do. The arm
    # counted here is the no-column half of each.
    ("206_people_from_membership.sql", "gtd_people"): (2, "GUARDED"),
}


class TestNoNewTenancyOmission:
    """The ratchet. A new omission fails the build; the old ones are recorded."""

    def test_no_migration_omits_the_tenancy_column_unless_grandfathered(self):
        new = {
            key: count
            for key, count in omissions().items()
            if count > _GRANDFATHERED.get(key, (0, ""))[0]
        }
        assert not new, (
            "These INSERTs write a tenant-scoped table without naming "
            "`organization_id`. The tenancy phase makes that column NOT NULL on "
            "PRODUCTION, so each one raises there while passing here — which is "
            "H-104, three times over.\n\n"
            + "\n".join(
                f"  {name}: {count} INSERT(s) into {table}"
                for (name, table), count in sorted(new.items())
            )
            + "\n\nFix: name the column. If the migration must also run on a "
            "database WITHOUT it, guard the arms on `information_schema.columns` "
            "the way 200 and 201 do."
        )

    def test_the_ratchet_never_goes_stale(self):
        """An entry that no longer matches is removed, not left to rot.

        A grandfather list nobody prunes stops describing the tree and starts
        excusing it — and then the fence is decoration.
        """
        current = omissions()
        stale = {
            key: (recorded, current.get(key, 0))
            for key, (recorded, _) in _GRANDFATHERED.items()
            if current.get(key, 0) != recorded
        }
        assert not stale, (
            "The grandfather list disagrees with the tree. Update "
            "`_GRANDFATHERED` in this file — lower the count when you fix one, "
            "and delete the entry when it reaches zero.\n\n"
            + "\n".join(
                f"  {name} / {table}: recorded {was}, found {now}"
                for (name, table), (was, now) in sorted(stale.items())
            )
        )


class TestTheFenceCanActuallyFail:
    """Non-vacuity. A fence that cannot fail proves nothing.

    The 200-era fixture passed while production was broken, so "it was green"
    is not evidence about this fence either.
    """

    def test_it_discovers_the_tenancy_tables_rather_than_transcribing_them(self):
        tables = tenancy_tables()
        assert len(tables) >= 140
        # The two that H-104 actually broke, and the one the homonym rule keeps
        # OUT (its `organization_id` is the customer company, not the tenant).
        assert "org_role_permission" in tables
        assert "user_role" in tables
        assert "crm_contacts" not in tables

    def test_it_finds_the_omissions_that_are_really_there(self):
        found = omissions()
        # 130's seed block is the one that still fails a fresh install on a
        # tenancy-applied database. If the scan stopped finding it, the regex
        # broke and every other assertion here went vacuous.
        assert found.get(("130_org_access_control.sql", "org_role_permission")) == 6

    def test_it_does_not_flag_an_insert_that_names_the_column(self):
        # 200 writes org_role_permission TWICE — a guarded IF arm that names the
        # column and an ELSE arm that does not. Exactly 1 is the right answer; a
        # scan blind to the column list would say 2.
        assert omissions().get(
            ("200_provision_org_roles_tenancy.sql", "org_role_permission")
        ) == 1

    def test_it_does_not_read_a_COMMENT_as_code(self):
        """The bug the first draft of this fence shipped with.

        200's header quotes the failing statement verbatim to explain it. A raw
        text scan counted the explanation, so writing a clearer comment raised
        the omission count — which is the opposite of what this measures.
        """
        raw = open(
            os.path.join(_LADDER_DIR, "200_provision_org_roles_tenancy.sql"),
            encoding="utf-8",
        ).read()
        assert "INSERT INTO org_role_permission (role_id, permission)" in raw
        stripped = strip_sql_comments(raw)
        # Twice in the file, once outside a comment.
        assert raw.count("INSERT INTO org_role_permission") == 3
        assert stripped.count("INSERT INTO org_role_permission") == 2

    def test_the_stripper_keeps_string_literals_and_function_bodies(self):
        sql = "\n".join(
            (
                "INSERT INTO t (a) VALUES ('-- not a comment');",
                "-- a real one",
                "CREATE FUNCTION f() AS $b$ SELECT '/* nor this */'; $b$;",
            )
        )
        out = strip_sql_comments(sql)
        assert "-- not a comment" in out
        assert "/* nor this */" in out
        assert "a real one" not in out
