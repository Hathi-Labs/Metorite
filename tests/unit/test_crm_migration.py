"""The CRM migration, read as text — idempotency and the shape §3 specifies.

Spec: ``project-docs/specs/crm_app.md`` §3 · WS-26a done-when 1.

**Why static.** ``infra/postgres/README.md`` requires every ``02+`` migration to
be idempotent, because ``apply_migrations.sh`` replays the whole ladder on every
deploy. The obvious way to check that is to run it twice — and §10 deliberately
runs no database, so an idempotency claim that holds only "by inspection" is not
a claim at all. Reading the file is what makes it enforceable in CI: a
``CREATE TABLE`` that loses its ``IF NOT EXISTS`` fails here rather than on the
second deploy, which is the deploy nobody watches.

The file is found by CONTENT, not by number: R1 forbids writing an absolute
future migration number anywhere, and a test pinned to ``144_`` would be the
same mistake in a different file. If the migration is renumbered in review, this
still finds it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _crm_migration() -> Path:
    """The migration that creates ``crm_companies``, whatever it is numbered."""
    found = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS crm_companies" in path.read_text(
            encoding="utf-8",
        )
    ]
    assert len(found) == 1, (
        f"expected exactly one migration creating crm_companies, found "
        f"{[p.name for p in found]}"
    )
    return found[0]


@pytest.fixture(scope="module")
def sql() -> str:
    return _crm_migration().read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bare(sql: str) -> str:
    """The statement text with ``--`` comments stripped.

    Every assertion about what the migration DOES runs against this: the header
    and the inline notes talk about tables and constraints by name, and a check
    that its own explanation can satisfy is a check that fails open.
    """
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


# ── Placement ───────────────────────────────────────────────────────────────

def test_the_migration_takes_the_next_free_number() -> None:
    """R1 — resolved from the directory at build time, never from a spec.

    Asserted as the two properties that stay true forever: no other migration
    shares this number, and the ladder has no gap immediately below it. NOT
    asserted as "it is the highest in the repo" — that version was measured
    red the moment any later workstream's migration landed (verifier probe,
    2026-08-05), and a red unit test silently blocks deploy.
    """
    numbers = sorted(
        int(path.name.split("_", 1)[0])
        for path in MIGRATIONS.glob("*.sql")
        if path.name.split("_", 1)[0].isdigit()
    )
    mine = int(_crm_migration().name.split("_", 1)[0])
    assert numbers.count(mine) == 1, (
        f"two migrations share number {mine} — the ladder replays them in "
        "filename order, so one of them runs against the wrong schema"
    )
    assert mine - 1 in numbers, (
        "the ladder has a gap immediately below the CRM migration"
    )


def test_the_header_says_what_why_and_what_it_depends_on() -> None:
    """`infra/postgres/README.md` §'Adding or changing schema' step 1."""
    lines = _crm_migration().read_text(encoding="utf-8").splitlines()
    # The leading comment block, i.e. everything before the first statement —
    # NOT everything before the word CREATE, which the header itself mentions.
    header = "\n".join(
        line for line in lines[: next(
            i for i, line in enumerate(lines)
            if line.strip() and not line.lstrip().startswith("--")
        )]
    )
    for required in ("What:", "Why:", "Depends on:"):
        assert required in header, f"the migration header has no '{required}'"


# ── Idempotency (WS-26a done-when 1) ────────────────────────────────────────

def test_every_create_table_is_guarded(bare: str) -> None:
    unguarded = [
        m.group(0) for m in re.finditer(r"CREATE\s+TABLE\s+(\S+)", bare, re.I)
        if not m.group(1).upper().startswith("IF")
    ]
    assert not unguarded, (
        f"CREATE TABLE without IF NOT EXISTS: {unguarded}. apply_migrations.sh "
        "replays every migration on every deploy."
    )


def test_every_create_index_is_guarded(bare: str) -> None:
    unguarded = [
        m.group(0) for m in re.finditer(r"CREATE\s+INDEX\s+(\S+)", bare, re.I)
        if not m.group(1).upper().startswith("IF")
    ]
    assert not unguarded, f"CREATE INDEX without IF NOT EXISTS: {unguarded}"


def test_every_insert_carries_an_on_conflict_arm(bare: str) -> None:
    """A seed without one turns the second deploy into a unique-violation and
    aborts the rest of the ladder."""
    statements = [s for s in bare.split(";") if re.search(r"\bINSERT\s+INTO\b", s, re.I)]
    assert statements, "no seed INSERTs found — this check went vacuous"
    naked = [
        re.search(r"INSERT\s+INTO\s+(\w+)", s, re.I).group(1)
        for s in statements
        if not re.search(r"ON\s+CONFLICT", s, re.I)
    ]
    assert not naked, f"INSERT without ON CONFLICT into: {naked}"


def test_the_only_alter_is_a_guarded_constraint_add(bare: str) -> None:
    """``ALTER TABLE … ADD CONSTRAINT`` has no ``IF NOT EXISTS``, so the one
    circular FK is added inside a ``DO $$`` that checks ``pg_constraint``."""
    adds = re.findall(r"ADD\s+CONSTRAINT\s+(\w+)", bare, re.I)
    assert adds == ["crm_leads_converted_deal_id_fkey"], adds

    blocks = re.findall(r"DO \$\$(.*?)\$\$", bare, re.S)
    guarding = [b for b in blocks if "ADD CONSTRAINT" in b]
    assert len(guarding) == 1
    assert "pg_constraint" in guarding[0]
    assert "NOT EXISTS" in guarding[0]


def test_no_statement_drops_or_truncates_data(bare: str) -> None:
    """A replayed migration that drops something destroys a live deployment."""
    for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
        assert forbidden not in bare.upper(), f"the migration contains {forbidden}"


# ── §3's tables and their shape ─────────────────────────────────────────────

EXPECTED_TABLES = (
    "crm_companies", "crm_contacts", "crm_leads", "crm_deals",
    "crm_deal_contacts", "crm_lead_statuses", "crm_deal_statuses",
    "crm_activities", "crm_status_changes", "crm_lost_reasons",
)


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_section_three_creates_every_table(bare: str, table: str) -> None:
    assert f"CREATE TABLE IF NOT EXISTS {table}" in bare


def test_the_tables_are_created_before_they_are_referenced(bare: str) -> None:
    """Except for the one cycle, which the guarded ALTER closes."""
    order = [
        m.group(1) for m in
        re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)", bare)
    ]
    for table in EXPECTED_TABLES:
        body = bare.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1]
        body = body.split(");", 1)[0]
        for referenced in re.findall(r"REFERENCES\s+(\w+)", body):
            assert order.index(referenced) <= order.index(table), (
                f"{table} references {referenced}, which is created later"
            )


@pytest.mark.parametrize(
    ("table", "column", "action"),
    [
        # RESTRICT on the status FKs: a status still holding records must not
        # be deletable out from under them (§3.3/§3.4, surfaced as a 409).
        ("crm_leads", "status_id", "RESTRICT"),
        ("crm_deals", "status_id", "RESTRICT"),
        # SET NULL everywhere a parent is optional context, so deleting an
        # organization does not take its deals' history with it.
        ("crm_contacts", "organization_id", "SET NULL"),
        ("crm_deals", "organization_id", "SET NULL"),
        ("crm_deals", "lead_id", "SET NULL"),
        ("crm_deals", "lost_reason_id", "SET NULL"),
        ("crm_leads", "lost_reason_id", "SET NULL"),
    ],
)
def test_the_declared_delete_actions(
    bare: str, table: str, column: str, action: str,
) -> None:
    body = bare.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1].split(");", 1)[0]
    [line] = [
        ln for ln in body.splitlines() if ln.strip().startswith(column)
    ] or [
        ln for ln in body.splitlines()
        if re.match(rf"\s*{column}\s", ln)
    ]
    # The action may wrap onto the continuation line the column opens.
    window = body[body.index(line): body.index(line) + 400]
    assert f"ON DELETE {action}" in window, (
        f"{table}.{column} should be ON DELETE {action}"
    )


def test_activities_cascade_from_all_four_targets(bare: str) -> None:
    """An activity outlives nothing: its whole meaning is its target."""
    body = bare.split("CREATE TABLE IF NOT EXISTS crm_activities", 1)[1]
    body = body.split("CONSTRAINT crm_activities_target_required", 1)[0]
    for column in ("lead_id", "deal_id", "contact_id", "organization_id"):
        window = body[body.index(f"{column} "):][:200]
        assert "ON DELETE CASCADE" in window, f"{column} must CASCADE"


def test_deal_contacts_cascade_from_both_ends(bare: str) -> None:
    body = bare.split("CREATE TABLE IF NOT EXISTS crm_deal_contacts", 1)[1]
    body = body.split(");", 1)[0]
    assert body.count("ON DELETE CASCADE") == 2
    assert "PRIMARY KEY (deal_id, contact_id)" in body


def test_an_activity_must_name_at_least_one_target(bare: str) -> None:
    """§3.8's CHECK. An activity attached to nothing is invisible in every
    timeline and accumulates silently."""
    body = bare.split("CONSTRAINT crm_activities_target_required CHECK", 1)[1]
    body = body.split(")\n", 1)[0]
    for column in ("lead_id", "deal_id", "contact_id", "organization_id"):
        assert f"{column} IS NOT NULL" in body
    assert body.count("OR") == 3


@pytest.mark.parametrize(
    ("column", "values"),
    [
        ("source", "'manual', 'import', 'email', 'agent'"),
        ("type", "'open', 'ongoing', 'on_hold', 'won', 'lost'"),
        ("entity_type", "'lead', 'deal'"),
    ],
)
def test_the_vocabularies_are_check_constrained(
    bare: str, column: str, values: str,
) -> None:
    """`infra/postgres/README.md`: prefer a CHECK for a new status column, to
    stop casing and typo drift. These are also mirrored in `core.py`, which is
    what turns a bad value into a 422 instead of a driver 500."""
    assert f"CHECK ({column} IN ({values}))" in bare


def test_the_activity_type_vocabulary_is_constrained(bare: str) -> None:
    for kind in ("note", "call", "meeting", "task", "status_change", "system"):
        assert f"'{kind}'" in bare


def test_the_due_at_index_is_partial(bare: str) -> None:
    """§3.8 — the only question asked of due_at is 'what is still open', and
    the completed tail is the majority of the table within a quarter."""
    assert (
        "ON crm_activities (due_at) WHERE completed_at IS NULL" in bare
    )


@pytest.mark.parametrize(
    "index",
    [
        "idx_crm_organizations_name",
        "idx_crm_contacts_email",
        "idx_crm_contacts_organization_id",
        "idx_crm_leads_status_id",
        "idx_crm_leads_email",
        "idx_crm_deals_status_id",
        "idx_crm_deals_organization_id",
        "idx_crm_deals_expected_close_date",
        "idx_crm_activities_deal_id_created_at",
        "idx_crm_status_changes_entity",
    ],
)
def test_section_three_declares_its_indexes(bare: str, index: str) -> None:
    assert index in bare


def test_the_email_indexes_are_case_folding_and_not_unique(bare: str) -> None:
    """§3.2 — plain, NOT unique: the Zoho export has duplicate and blank
    addresses, so a unique index would make the import fail rather than the
    data honest. Dedup is enforced at conversion time, in code."""
    for table in ("crm_contacts", "crm_leads"):
        assert f"ON {table} (lower(email))" in bare
    assert "CREATE UNIQUE INDEX" not in bare.upper()


def test_every_table_carries_zoho_provenance_where_it_is_imported(
    bare: str,
) -> None:
    """§7.1 upserts ``ON CONFLICT (zoho_id)``, which needs the UNIQUE."""
    for table in ("crm_companies", "crm_contacts", "crm_leads", "crm_deals",
                  "crm_activities"):
        body = bare.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1]
        body = body.split(");", 1)[0]
        assert "zoho_id" in body and "UNIQUE" in body


def test_deals_carry_the_stage_age_clock_and_leads_do_not(bare: str) -> None:
    """§3.4 gives `status_changed_at` to deals only; the CRM's transition code
    reads that difference off the model, so the schema is the source."""
    deals = bare.split("CREATE TABLE IF NOT EXISTS crm_deals", 1)[1].split(");", 1)[0]
    leads = bare.split("CREATE TABLE IF NOT EXISTS crm_leads", 1)[1].split(");", 1)[0]
    assert "status_changed_at" in deals
    assert "status_changed_at" not in leads


def test_the_currency_column_exists_and_defaults_to_inr(bare: str) -> None:
    """§1's recorded non-goal: INR only, but additively so."""
    assert "currency" in bare and "DEFAULT 'INR'" in bare


# ── Seeds ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name", ["New", "Contacted", "Nurture", "Qualified", "Lost"],
)
def test_the_lead_statuses_are_seeded(bare: str, name: str) -> None:
    assert f"'{name}'" in bare.split("INSERT INTO crm_lead_statuses", 1)[1]


@pytest.mark.parametrize(
    "name",
    ["Qualification", "Needs Analysis", "Proposal", "Negotiation",
     "Closed Won", "Closed Lost"],
)
def test_the_deal_statuses_are_seeded(bare: str, name: str) -> None:
    assert f"'{name}'" in bare.split("INSERT INTO crm_deal_statuses", 1)[1]


@pytest.mark.parametrize(
    "label",
    ["Price", "Competitor", "No budget", "No response",
     "Requirement dropped", "Other"],
)
def test_the_lost_reasons_are_seeded(bare: str, label: str) -> None:
    assert f"'{label}'" in bare.split("INSERT INTO crm_lost_reasons", 1)[1]


def test_each_pipeline_seeds_exactly_one_default(bare: str) -> None:
    """Two defaults is a coin toss for where a new record lands; none makes
    `load_default_status` fall through to whatever sorts first."""
    for table in ("crm_lead_statuses", "crm_deal_statuses"):
        seed = bare.split(f"INSERT INTO {table}", 1)[1].split(";", 1)[0]
        assert seed.count("true") == 1, f"{table} must seed exactly one default"


def test_each_pipeline_seeds_a_lost_type_status(bare: str) -> None:
    """The lost gate is unreachable — and so is closing a deal honestly — if
    no lost-type lane exists to move into."""
    for table in ("crm_lead_statuses", "crm_deal_statuses"):
        seed = bare.split(f"INSERT INTO {table}", 1)[1].split(";", 1)[0]
        assert "'lost'" in seed and "'won'" in seed


# ── Feature registration (WS-26a done-when 2) ───────────────────────────────

def test_the_feature_catalog_row_uses_all_seven_columns(bare: str) -> None:
    """The insert shape 130_org_access_control.sql established. A short insert
    silently defaults `sort_order` to 100 (last) and `category` to 'apps'."""
    insert = bare.split("INSERT INTO feature_catalog", 1)[1]
    assert (
        "(slug, label, description, nav_href, category, sort_order, is_default)"
        in insert
    )
    assert (
        "('crm', 'CRM', 'Pipeline, leads and customers', '/crm', 'apps', 55, false)"
        in insert
    )


def test_the_catalog_row_does_not_stomp_is_default_on_redeploy(bare: str) -> None:
    """Same rule as 130: an admin may have retuned the default set."""
    conflict = bare.split("INSERT INTO feature_catalog", 1)[1]
    conflict = conflict.split("ON CONFLICT", 1)[1]
    assert "is_default" not in conflict


def test_the_catalog_row_matches_the_features_tuple() -> None:
    """The other half of done-when 2 — a catalog row with no FEATURES entry is
    invisible to /auth/me even for an owner holding `*`. The full both-ways
    invariant lives in test_org_access_control.py."""
    from acb_auth import FEATURES

    assert "crm" in FEATURES


# ═══════════════════════════════════════════════════════════════════════════
# The Zoho-sync migration (WS-26b done-when 3)
# ═══════════════════════════════════════════════════════════════════════════
#
# 26a's fence, extended to the second CRM migration rather than duplicated
# into a second file: the rules are the same rules, and two copies of an
# idempotency fence drift until one of them stops failing.
#
# Found by CONTENT, exactly like the first one. R1 forbids writing an absolute
# future migration number anywhere, and a test pinned to `145_` would be that
# same mistake in a different file — the WS-26a verifier already measured the
# "it is the highest in the repo" variant red the moment a later workstream
# landed, so there are deliberately no highest-number assertions here either.


def _sync_migration() -> Path:
    """The migration that creates ``crm_zoho_tombstones``, whatever its number."""
    found = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS crm_zoho_tombstones" in path.read_text(
            encoding="utf-8",
        )
    ]
    assert len(found) == 1, (
        f"expected exactly one migration creating crm_zoho_tombstones, found "
        f"{[p.name for p in found]}"
    )
    return found[0]


@pytest.fixture(scope="module")
def sync_sql() -> str:
    return _sync_migration().read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sync_bare(sync_sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sync_sql.splitlines())


# ── Placement ───────────────────────────────────────────────────────────────

def test_the_sync_migration_takes_a_free_number_after_the_crm_spine() -> None:
    """Same two forever-true properties as the spine's: nobody shares its
    number, and the ladder has no gap below it. Plus one this migration needs
    on its own — it ALTERs tables 144 creates, and `apply_migrations.sh`
    replays in filename order, so running first would abort the ladder.
    """
    numbers = sorted(
        int(path.name.split("_", 1)[0])
        for path in MIGRATIONS.glob("*.sql")
        if path.name.split("_", 1)[0].isdigit()
    )
    mine = int(_sync_migration().name.split("_", 1)[0])
    spine = int(_crm_migration().name.split("_", 1)[0])

    assert numbers.count(mine) == 1, (
        f"two migrations share number {mine} — the ladder replays them in "
        "filename order, so one of them runs against the wrong schema"
    )
    assert mine - 1 in numbers, "the ladder has a gap below the CRM sync migration"
    assert mine > spine, (
        "the sync migration ALTERs the tables the CRM spine creates, so it must "
        "sort after it"
    )


def test_the_sync_header_says_what_why_and_what_it_depends_on() -> None:
    lines = _sync_migration().read_text(encoding="utf-8").splitlines()
    header = "\n".join(
        line for line in lines[: next(
            i for i, line in enumerate(lines)
            if line.strip() and not line.lstrip().startswith("--")
        )]
    )
    for required in ("What:", "Why:", "Depends on:"):
        assert required in header, f"the sync migration header has no '{required}'"
    assert "144_crm.sql" in header, (
        "a migration that ALTERs another one's tables must name it as a dependency"
    )


# ── Idempotency ─────────────────────────────────────────────────────────────

def test_the_sync_migration_guards_every_create(sync_bare: str) -> None:
    for keyword in ("TABLE", "INDEX"):
        unguarded = [
            m.group(0)
            for m in re.finditer(rf"CREATE\s+{keyword}\s+(\S+)", sync_bare, re.I)
            if not m.group(1).upper().startswith("IF")
        ]
        assert not unguarded, f"CREATE {keyword} without IF NOT EXISTS: {unguarded}"


def test_every_added_column_is_guarded(sync_bare: str) -> None:
    """``ADD COLUMN`` — unlike ``ADD CONSTRAINT`` — takes ``IF NOT EXISTS``
    directly, so no ``DO $$`` wrapper is needed and none should appear."""
    adds = re.findall(r"ADD\s+COLUMN(\s+IF\s+NOT\s+EXISTS)?", sync_bare, re.I)
    assert adds, "no ADD COLUMN found — this check went vacuous"
    assert all(guard for guard in adds), (
        "ADD COLUMN without IF NOT EXISTS: apply_migrations.sh replays every "
        "migration on every deploy"
    )


def test_the_sync_migration_adds_no_constraint_needing_a_do_block(
    sync_bare: str,
) -> None:
    assert "ADD CONSTRAINT" not in sync_bare.upper()


def test_any_sync_seed_carries_an_on_conflict_arm(sync_bare: str) -> None:
    """Stated as a rule rather than a count: this migration seeds nothing
    today, and the check must still bind the day somebody adds a row."""
    statements = [
        s for s in sync_bare.split(";")
        if re.search(r"\bINSERT\s+INTO\b", s, re.I)
    ]
    naked = [
        re.search(r"INSERT\s+INTO\s+(\w+)", s, re.I).group(1)
        for s in statements
        if not re.search(r"ON\s+CONFLICT", s, re.I)
    ]
    assert not naked, f"INSERT without ON CONFLICT into: {naked}"


def test_the_sync_migration_drops_or_truncates_nothing(sync_bare: str) -> None:
    for forbidden in ("DROP TABLE", "DROP COLUMN", "TRUNCATE", "DELETE FROM"):
        assert forbidden not in sync_bare.upper(), (
            f"the sync migration contains {forbidden} — a replayed migration "
            "that destroys data destroys a live deployment"
        )


# ── §7.1's shape ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "table",
    ["crm_companies", "crm_contacts", "crm_leads", "crm_deals"],
)
def test_the_four_record_tables_gain_the_dirty_pair(
    sync_bare: str, table: str,
) -> None:
    """§7.1's whole state machine is these two columns. `zoho_dirty` must
    default FALSE: applying this migration to a populated database must not
    mean "push every row that already exists to Zoho"."""
    body = sync_bare.split(f"ALTER TABLE {table}", 1)[1].split(";", 1)[0]
    assert "zoho_dirty" in body
    assert "BOOLEAN NOT NULL DEFAULT false" in body
    assert "zoho_synced_at" in body
    assert "TIMESTAMPTZ" in body


def test_the_statuses_and_the_activity_spine_are_not_dirty_tracked(
    sync_bare: str,
) -> None:
    """Vocabulary flows DOWN only, and an activity carries its own push signal
    (a NULL ``zoho_id``). A dirty column there would be a second answer to one
    question — and `crm_activities` has no `updated_at` for LWW to read.

    ``crm_activities`` IS altered, but only for the retry trio: its push is a
    third queue with the same starvation failure mode as the other two.
    """
    altered = set(re.findall(r"ALTER TABLE\s+(\w+)", sync_bare, re.I))
    assert altered == {
        "crm_companies", "crm_contacts", "crm_leads", "crm_deals",
        "crm_activities",
    }, altered

    activities = sync_bare.split("ALTER TABLE crm_activities", 1)[1].split(";", 1)[0]
    assert "zoho_dirty" not in activities
    assert "zoho_synced_at" not in activities
    for column in ("zoho_push_attempts", "zoho_push_error", "zoho_next_attempt_at"):
        assert column in activities, column
    # No status table is touched at all — the vocabulary is Zoho's while the
    # sync is on, so there is nothing here to push back.
    assert "crm_lead_statuses" not in altered
    assert "crm_deal_statuses" not in altered


@pytest.mark.parametrize(
    "table",
    ["crm_companies", "crm_contacts", "crm_leads", "crm_deals",
     "crm_activities"],
)
def test_every_push_queue_can_back_off_and_give_up(
    sync_bare: str, table: str,
) -> None:
    """Without the trio a row Zoho will NEVER accept — an amount past its
    field's range, a picklist value it rejects — is retried every cycle
    forever, and because every queue is read oldest-first under a LIMIT a
    handful of them starve everything behind."""
    body = sync_bare.split(f"ALTER TABLE {table}", 1)[1].split(";", 1)[0]
    assert "zoho_push_attempts   INT NOT NULL DEFAULT 0" in body, (
        "attempts must default to 0, not NULL: the `< :max_attempts` gate "
        "would exclude every row that has never failed"
    )
    assert "zoho_push_error" in body
    assert "zoho_next_attempt_at TIMESTAMPTZ" in body


def test_the_tombstone_queue_can_back_off_and_give_up(sync_bare: str) -> None:
    body = sync_bare.split(
        "CREATE TABLE IF NOT EXISTS crm_zoho_tombstones", 1,
    )[1].split(");", 1)[0]
    assert "attempts        INT NOT NULL DEFAULT 0" in body
    assert "last_error" in body
    assert "next_attempt_at TIMESTAMPTZ" in body


def test_the_tombstone_table_carries_everything_a_push_needs(
    sync_bare: str,
) -> None:
    """§7.1 — a tombstone cannot be a column on a row that no longer exists,
    so it is a table, and it must be self-sufficient: after the record is gone
    nothing else can supply the module or the id."""
    body = sync_bare.split(
        "CREATE TABLE IF NOT EXISTS crm_zoho_tombstones", 1,
    )[1].split(");", 1)[0]
    for column in ("module", "zoho_id", "entity_type", "deleted_by", "deleted_at"):
        assert column in body, column
    assert "module      TEXT NOT NULL" in body or "module TEXT NOT NULL" in body
    # `pushed_at` NULL until pushed is the retry contract: a failed push is
    # simply found again next cycle rather than lost. No NOT NULL and no
    # DEFAULT — either would mark every tombstone as already delivered.
    assert re.search(r"pushed_at\s+TIMESTAMPTZ\s*,?\s*$", body, re.M), body
    assert not re.search(r"pushed_at\s+TIMESTAMPTZ[^\n]*(NOT NULL|DEFAULT)", body)


def test_the_tombstone_table_has_no_foreign_keys(sync_bare: str) -> None:
    """Deliberate: the row it describes is gone by the time anybody reads it.
    An FK would make the tombstone undeletable-with or deleted-alongside the
    very deletion it exists to record."""
    body = sync_bare.split(
        "CREATE TABLE IF NOT EXISTS crm_zoho_tombstones", 1,
    )[1].split(");", 1)[0]
    assert "REFERENCES" not in body.upper()


def test_the_tombstone_entity_vocabulary_is_check_constrained(
    sync_bare: str,
) -> None:
    """`infra/postgres/README.md` prefers a CHECK for a status-like column, and
    `core.record_zoho_tombstone` writes `Entity.zoho_entity_type` — so the two
    vocabularies have to be the same one."""
    from gateway.routes.crm.core import ENTITIES

    body = sync_bare.split("CHECK (entity_type IN", 1)[1].split(")", 1)[0]
    for entity in ENTITIES.values():
        assert f"'{entity.zoho_entity_type}'" in body, entity.slug


def test_the_unpushed_backlog_index_is_partial(sync_bare: str) -> None:
    """The only query this table serves. A full index would be read by
    nothing: every row is pushed within a cycle and stays forever."""
    assert "ON crm_zoho_tombstones (deleted_at) WHERE pushed_at IS NULL" in sync_bare


def test_the_dirty_indexes_are_partial(sync_bare: str) -> None:
    """The push phase asks one question — "what is dirty" — against a table
    that is almost entirely clean."""
    for table in ("crm_companies", "crm_contacts", "crm_leads", "crm_deals"):
        assert f"ON {table} (zoho_dirty) WHERE zoho_dirty" in sync_bare


def test_the_cursor_table_is_keyed_on_the_module(sync_bare: str) -> None:
    """§7.1 — "``crm_sync_cursors`` (``module`` PK…)". A second cursor row for
    one module is not a second opinion; it lets the pull silently rewind."""
    body = sync_bare.split(
        "CREATE TABLE IF NOT EXISTS crm_sync_cursors", 1,
    )[1].split(");", 1)[0]
    assert "module         TEXT PRIMARY KEY" in body or "module TEXT PRIMARY KEY" in body
    for column in ("last_pulled_at", "last_run_at", "last_status"):
        assert column in body, column


def test_the_sync_migration_adds_no_new_source_value(sync_bare: str) -> None:
    """§7.1 — pulled rows carry the EXISTING `'import'` value, so the CHECK
    vocabulary in the spine needs no new member and no ALTER. An ALTER of a
    CHECK constraint here would be the thing this test exists to catch."""
    assert "CHECK (source" not in sync_bare
    assert "DROP CONSTRAINT" not in sync_bare.upper()


# ═══════════════════════════════════════════════════════════════════════════
# The defaulted-NOT-NULL map (WS-26c done-when 3)
# ═══════════════════════════════════════════════════════════════════════════
#
# Derived from the CRM spine migration, not the sync one: the guard reads
# CREATE TABLE bodies, and `zoho_dirty` arrives as an ALTER in the second
# migration on a column no request body may name.
#
# `core.NOT_NULL_DEFAULTED` decides which columns answer 422 for an explicit
# `null` instead of handing the caller a driver error. It is hand-kept, so it
# is derived from the migration here and pinned BOTH ways — a column that
# gains NOT NULL DEFAULT in SQL and not in the map keeps its 500, and one that
# leaves SQL and not the map refuses a request Postgres would have accepted.

#: ``source  TEXT NOT NULL DEFAULT 'manual'`` → ``source``. Deliberately does
#: NOT match ``NOT NULL`` without a DEFAULT: that is a missing *required*
#: field, which the route-level checks already answer.
_NOT_NULL_DEFAULT_RE = re.compile(
    r"^\s*(\w+)\s+[A-Za-z]+[^,\n]*?\bNOT\s+NULL\b[^,\n]*?\bDEFAULT\b",
    re.MULTILINE,
)


def _declared_not_null_defaults(bare: str, table: str) -> set[str]:
    body = bare.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1]
    body = body.split(");", 1)[0]
    return set(_NOT_NULL_DEFAULT_RE.findall(body))


#: The columns the platform stamps itself and no request body may name, so
#: they are outside the guard's remit even though the SQL declares them.
_PLATFORM_STAMPED: frozenset[str] = frozenset({"created_at", "updated_at"})

#: ``ADD COLUMN IF NOT EXISTS required_fields TEXT[] NOT NULL DEFAULT '{}'`` —
#: the same shape one migration later. Read from the WS-26h migration and NOT
#: from the sync one for the reason stated above: `zoho_dirty` also arrives as
#: a defaulted NOT NULL ALTER, but no request body may name it, so it is
#: outside this guard's remit exactly as `created_at` is. `required_fields` is
#: inside it — the settings grid PATCHes the column by name.
_ALTER_NOT_NULL_DEFAULT_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+(\w+)\s+"
    r"[^;]*?\bNOT\s+NULL\b[^;]*?\bDEFAULT\b",
    re.I | re.S,
)


def _altered_not_null_defaults(bare: str, table: str) -> set[str]:
    return {
        column for altered, column in _ALTER_NOT_NULL_DEFAULT_RE.findall(bare)
        if altered == table
    }


def test_the_defaulted_not_null_map_matches_the_migration(
    bare: str, discipline_bare: str,
) -> None:
    from gateway.routes.crm.core import NOT_NULL_DEFAULTED

    for table, declared_map in NOT_NULL_DEFAULTED.items():
        declared = (
            _declared_not_null_defaults(bare, table)
            | _altered_not_null_defaults(discipline_bare, table)
        ) - _PLATFORM_STAMPED
        assert declared == set(declared_map), (
            f"{table}: the migration declares NOT NULL DEFAULT on "
            f"{sorted(declared)} but core.NOT_NULL_DEFAULTED says "
            f"{sorted(declared_map)}. A column missing from the map answers a "
            "driver 500 for an explicit null; one that is only in the map "
            "refuses a request Postgres would have taken."
        )


def test_every_crm_table_with_such_a_column_is_in_the_map(bare: str) -> None:
    """The map is a dict keyed by table, so a table left out of it is silently
    unguarded rather than loudly wrong."""
    from gateway.routes.crm.core import NOT_NULL_DEFAULTED

    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (crm_\w+)", bare)
    missing = [
        table for table in tables
        if (_declared_not_null_defaults(bare, table) - _PLATFORM_STAMPED)
        and table not in NOT_NULL_DEFAULTED
    ]
    assert not missing, (
        f"{missing} declare a defaulted NOT NULL column but have no entry in "
        "core.NOT_NULL_DEFAULTED, so an explicit null there is still a 500."
    )


# ── WS-26h · the stage-discipline migration ─────────────────────────────────
#
# Two columns on `crm_deal_statuses` and nothing else: `required_fields` (the
# deal columns a stage demands before a deal may ENTER it) and `max_dwell_days`
# (the rot threshold, NULL = never). Found by CONTENT like every other
# migration here, so a renumber in review costs nothing (R1).


def _discipline_migration() -> Path:
    found = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "required_fields" in path.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, (
        f"expected exactly one migration adding required_fields, found "
        f"{[p.name for p in found]}"
    )
    return found[0]


@pytest.fixture(scope="module")
def discipline_bare() -> str:
    return "\n".join(
        re.sub(r"--.*$", "", line)
        for line in _discipline_migration().read_text(encoding="utf-8").splitlines()
    )


def test_the_discipline_migration_sorts_after_the_spine_it_alters() -> None:
    """Same two forever-true properties as the spine's, plus ordering.

    The ladder is replayed in filename order, so an ALTER that sorted before
    the CREATE it depends on would abort partway.
    """
    numbers = sorted(
        int(path.name.split("_", 1)[0])
        for path in MIGRATIONS.glob("*.sql")
        if path.name.split("_", 1)[0].isdigit()
    )
    mine = int(_discipline_migration().name.split("_", 1)[0])
    spine = int(_crm_migration().name.split("_", 1)[0])

    assert numbers.count(mine) == 1, (
        f"two migrations share number {mine} — the ladder replays them in "
        "filename order, so one of them runs against the wrong schema"
    )
    assert mine - 1 in numbers, "the ladder has a gap below the WS-26h migration"
    assert mine > spine, (
        "the stage-discipline migration ALTERs crm_deal_statuses, which the CRM "
        "spine creates, so it must sort after it"
    )


def test_the_discipline_header_says_what_why_and_what_it_depends_on() -> None:
    lines = _discipline_migration().read_text(encoding="utf-8").splitlines()
    header = "\n".join(
        line for line in lines[: next(
            i for i, line in enumerate(lines)
            if line.strip() and not line.lstrip().startswith("--")
        )]
    )
    for required in ("What:", "Why:", "Depends on:"):
        assert required in header, f"the WS-26h migration header has no '{required}'"
    assert "144_crm.sql" in header, (
        "the header must name the migration whose table it alters"
    )


def test_every_discipline_column_add_is_guarded(discipline_bare: str) -> None:
    """`infra/postgres/README.md` — the ladder is replayed every time, so a
    bare ADD COLUMN fails on the second pass and takes the release with it."""
    adds = re.findall(r"ADD\s+COLUMN(\s+IF\s+NOT\s+EXISTS)?", discipline_bare, re.I)
    assert adds, "the WS-26h migration adds no column"
    assert all(guard for guard in adds), (
        "every ADD COLUMN in the WS-26h migration needs IF NOT EXISTS"
    )


def test_the_discipline_migration_drops_or_tightens_nothing(
    discipline_bare: str,
) -> None:
    """R6 — expand only. We cannot roll back, and the currently-running code
    meets this schema for a window, so the contract half belongs later."""
    for forbidden in (
        "DROP", "TRUNCATE", "DELETE FROM", "SET NOT NULL", "ALTER COLUMN",
        "RENAME",
    ):
        assert forbidden not in discipline_bare.upper(), (
            f"the WS-26h migration contains '{forbidden}' — it is additive and "
            "expand-only by contract"
        )


def test_it_adds_exactly_the_two_columns_the_ticket_names(
    discipline_bare: str,
) -> None:
    added = set(re.findall(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+(\w+)",
        discipline_bare,
        re.I,
    ))
    assert added == {
        ("crm_deal_statuses", "required_fields"),
        ("crm_deal_statuses", "max_dwell_days"),
    }, f"the WS-26h migration adds {sorted(added)}"


def test_rot_never_defaults_to_a_threshold(discipline_bare: str) -> None:
    """`max_dwell_days` is nullable and undefaulted: NULL means "never rots".

    A default would silently give every existing lane a rot policy nobody
    chose, and every card on the live board would grow a badge it never earned.
    """
    statement = re.search(
        r"ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+max_dwell_days[^;]*",
        discipline_bare,
        re.I,
    )
    assert statement is not None
    assert "DEFAULT" not in statement.group(0).upper()
    assert "NOT NULL" not in statement.group(0).upper()


def test_lead_statuses_gain_nothing(discipline_bare: str) -> None:
    """Both columns are deal-only, the same asymmetry `probability` has: the
    allowlist is deal columns, and rot is measured off `status_changed_at`,
    which migration 144 gave deals and withheld from leads."""
    assert "crm_lead_statuses" not in discipline_bare


#: ``    probability SMALLINT NOT NULL DEFAULT 0,`` → ``probability``. Anchored
#: on a type keyword so a CHECK continuation line is not read as a column.
_COLUMN_RE = re.compile(
    r"^\s+(\w+)\s+(?:UUID|TEXT|INT|SMALLINT|BOOLEAN|TIMESTAMPTZ|DATE|NUMERIC|JSONB)\b",
    re.MULTILINE,
)


def _declared_columns(bare: str, table: str) -> set[str]:
    body = bare.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1].split(");", 1)[0]
    return set(_COLUMN_RE.findall(body))


def test_the_deal_only_field_set_matches_the_schema(
    bare: str, discipline_bare: str,
) -> None:
    """R7 — `admin.DEAL_ONLY_FIELDS` is hand-kept, so it is derived here.

    It decides which payload fields answer 422 on a LEAD status instead of
    reaching an INSERT that names a column `crm_lead_statuses` has not got. A
    fourth deal-only column added to the model and forgotten there would be
    silently accepted and then surface as a driver 500 — the exact defect
    WS-26c dw 3 recorded for `probability`.
    """
    from gateway.routes.crm.admin import DEAL_ONLY_FIELDS, StatusIn

    deal = _declared_columns(bare, "crm_deal_statuses") | {
        column for altered, column in re.findall(
            r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+(\w+)",
            discipline_bare,
            re.I,
        )
        if altered == "crm_deal_statuses"
    }
    lead = _declared_columns(bare, "crm_lead_statuses")
    # Intersected with the payload model: a deal-only column no request body
    # can name needs no refusal, because no request can offer it.
    expected = (deal - lead) & set(StatusIn.model_fields)

    assert expected == set(DEAL_ONLY_FIELDS), (
        f"the schema says the deal-only status fields a payload may name are "
        f"{sorted(expected)}, but admin.DEAL_ONLY_FIELDS says "
        f"{sorted(DEAL_ONLY_FIELDS)}"
    )
