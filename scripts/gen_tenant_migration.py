#!/usr/bin/env python3
"""Generate the MT-1b tenancy migration — org_id + FORCE RLS on every table.

Spec: ``project-docs/specs/saas_multitenancy.md`` §1.3 / MT-1b ·
shapes in ``saas_multitenancy_implementation.md`` §1 · board WS-29 · D15.

WHY A GENERATOR AND NOT A HAND-WRITTEN MIGRATION
------------------------------------------------
143 tables. Hand-writing that is 143 chances to omit ``FORCE``, or ``WITH
CHECK``, or the ``, true`` missing-ok flag — and each omission is silent. A
generator makes the *template* the reviewable artifact and the per-table
expansion mechanical.

WHY THE OUTPUT IS NOT A NUMBERED MIGRATION
------------------------------------------
Read ``scripts/apply_migrations.sh`` before changing this. That runner exists in
its current shape because of a **14h44m production outage**: a hung LLM call held
a session open, the runner asked for ACCESS EXCLUSIVE behind it, and because
Postgres's lock queue is FIFO every later reader queued behind the *waiting*
ALTER. Sending mail stopped.

MT-1b is precisely that shape of change, 143 times over:

* ``ADD COLUMN ... NOT NULL`` and ``SET NOT NULL`` take **ACCESS EXCLUSIVE** and
  scan the whole table. On ``email_messages`` with real mail in it, that is not
  instant.
* The backfill ``UPDATE`` rewrites every row.
* A single transaction wrapping all of it holds every lock until the end.

So this script writes to ``infra/postgres/generated/`` — **outside the numbered
sequence the deploy replays.** Promoting it into the sequence is a deliberate
human act, taken against a database, in a window. It is not something that
should happen because a file landed on main.

PHASING (why the output is four files, not one)
-----------------------------------------------
Each phase is separately applicable and separately abortable:

  1. ``add_columns``   — nullable ADD COLUMN, no scan, no lock of consequence
  2. ``backfill``      — batched UPDATE; re-runnable; the slow part
  3. ``constraints``   — SET NOT NULL + FK + index; the ACCESS EXCLUSIVE phase
  4. ``policies``      — ENABLE + FORCE RLS + the policy; instant, and the
                         moment isolation becomes real

⚠️ **Phase 4 is a cliff.** The instant it applies, every connection that has not
bound ``app.tenant_id`` reads **zero rows** — that is the fail-closed property
(§0.1) working as designed, and it means MT-1c must be deployed and verified
FIRST or the product goes dark. The ordering is not a preference.

Usage::

    uv run python scripts/gen_tenant_migration.py            # write the four files
    uv run python scripts/gen_tenant_migration.py --dry-run  # print a summary
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_MIGRATIONS = _REPO / "infra" / "postgres"
_OUT = _MIGRATIONS / "generated"

#: Tables that are cross-tenant BY DESIGN and must never carry a policy.
#: **This list is the security review.** Adding a name here exempts a table from
#: tenant isolation, so every entry carries its reason and a reviewer is expected
#: to challenge it.
EXEMPT: dict[str, str] = {
    # ── Control plane (§1.5): must be readable ACROSS tenants ──────────────
    "organization":            "the tenant list itself",
    "tenant_placement":        "control plane — which data plane serves whom",
    "user_identity":           "control plane — one row per human, global by design",
    "org_membership":          "control plane — the tenant-scoped half; org_id is its PK",
    # WS-31 CP-2d slice 2 (customer_console.md §CP-2d clause 7). Sits beside the
    # two identity rows above because it is the same kind of thing: it belongs to
    # an EMAIL, not to a tenant. The row is minted BEFORE any session exists, for
    # an address that may belong to no organization at all — the zero-org
    # (`console-empty`) case is the self-serve-signup funnel, not a corner — so
    # there is no organization_id to stamp on INSERT and none to match on SELECT.
    # Scoping it would not weaken isolation, it would BRICK email OTP outright
    # under the phase-4 policies (live in production since 2026-08-23). R5(a)'s
    # exempt-identity-table permission. It holds no tenant data: an address, a
    # SHA-256 of the code salted with AUTH_SECRET, an expiry and two counters.
    "auth_email_otp_token":    "belongs to an email, minted pre-session; no organization_id exists to stamp",
    # ── Catalogs: identical for every tenant, no customer data ─────────────
    "feature_catalog":         "a catalog of product surfaces, not tenant data",
    "schema_migrations":       "migration bookkeeping",
    # WS-39 S3b/S3c (migration 189). Sits beside `schema_migrations` because it
    # is the same kind of thing: bookkeeping ABOUT the schema, not data IN it.
    # One row means "a human authorised the gtd_* retirement on this database".
    # `DROP TABLE` is database-wide and has no per-tenant form, so scoping this
    # would create a column that can only ever be wrong — and worse, it would
    # invite a per-tenant arming that the drop it guards cannot honour. It is
    # read by migration 190's guard, which runs as the database owner outside
    # RLS, so a policy here would not even be consulted. Holds an email, a
    # timestamp and a note; no tenant data. See work_plan.md §6 (f), D53.5.
    "gtd_retirement_arm":      "arms a database-wide DDL drop; no per-tenant form exists",
    # ── Already tenant-keyed by an earlier migration ───────────────────────
    "provider_keys":           "keyed (organization_id, provider) by MT-0d / 158",
    "model_config":            "keyed (organization_id, key) by MT-0d / 158",
    "mcp_servers":             "keyed (organization_id, name) by MT-0d / 158",
    "org_role":                "carries organization_id since 130",
    "org_group":               "carries organization_id since 138",
    # ── Vendored schemas we do not own ─────────────────────────────────────
    "_prisma_migrations":      "LiteLLM's own schema",
}

#: Tables whose ``organization_id`` is reachable only through a parent. Listed so
#: a reviewer sees they were CONSIDERED, not missed — each still gets its own
#: column (denormalised on purpose: a policy that has to JOIN to find the tenant
#: is a policy that is slow on every read and wrong under a missing parent).
_DENORMALISE_NOTE = (
    "child rows carry their own organization_id rather than joining to a parent: "
    "an RLS policy runs on EVERY row of EVERY query, and a join in USING() is "
    "both a performance cliff and a correctness hole when the parent is gone"
)

#: ⚠️ Tables that ALREADY have a column called ``organization_id`` meaning
#: something else entirely. **These are not exempt and they are not scoped —
#: they are BLOCKED**, and the difference matters:
#:
#: ``crm_contacts.organization_id`` is the customer COMPANY a contact works at
#: (``REFERENCES crm_organizations``), not the tenant that owns the row. The
#: generator's phases are name-based, so left alone they would emit, for each:
#:
#:   phase 1  ADD COLUMN IF NOT EXISTS  -> silent no-op, the column exists
#:   phase 2  UPDATE ... WHERE organization_id IS NULL
#:                                      -> writes a TENANT id into a column whose
#:                                         FK points at ``crm_organizations``;
#:                                         aborts on that FK, mid-window
#:   phase 3  ADD CONSTRAINT ... REFERENCES organization(id)
#:                                      -> a second, contradictory FK on one
#:                                         column; fails on every existing value
#:
#: — i.e. the failure lands in the maintenance window, after phase 1 has run,
#: which is the worst moment to learn about it. Refusing at GENERATION time is
#: the whole point of this map.
#:
#: **These three tables therefore carry NO tenant isolation**, and they hold
#: customer CRM data. That is a real hole, not a resolved item. Closing it needs
#: an owner call this branch does not make: rename the CRM column
#: (``organization_id`` -> ``crm_organization_id``, touching every CRM route and
#: query), or give the tenant key a different name on these three tables alone
#: and accept that the column name means two things across the schema. Recorded
#: in ``specs/multi_tenancy_leak_audit.md``.
HOMONYM_BLOCKED: dict[str, str] = {
    "crm_contacts":   "organization_id = the customer company (144_crm.sql:74)",
    "crm_deals":      "organization_id = the customer company (144_crm.sql:197)",
    "crm_activities": "organization_id = the customer company (144_crm.sql:289)",
}

_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:public\.)?[\"']?([a-z_][a-z0-9_]*)[\"']?",
    re.IGNORECASE,
)

#: ``--`` line comments, stripped before :data:`_CREATE_RE` runs.
#:
#: ⚠️ **Not cosmetic.** These migrations document themselves in prose, and the
#: prose talks about SQL: ``163_crm_auto_lead_cursor.sql`` contains the sentence
#: "``CREATE TABLE IF NOT EXISTS`` above is a NO-OP against a database that…".
#: Regexing the raw text discovered a table called **``above``** and emitted
#: ``ALTER TABLE above ENABLE ROW LEVEL SECURITY`` into phase 4 — a statement
#: that cannot run, in a set that is promoted **by hand against production in a
#: maintenance window**, where an abort mid-phase is the 14h44m outage this
#: generator's docstring exists to prevent. A generator that reads comments as
#: schema is a generator that fails exactly where it is least recoverable.
#:
#: Line comments only: a ``/* … */`` block containing `CREATE TABLE` would slip
#: through, and none exists today. `discover_tables` asserts the shape it found
#: is real, so the next such case fails loudly here rather than in the window.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

#: A column literally named ``organization_id`` together with its FK target. The
#: name alone is not evidence of tenancy — matching on it is what let the homonym
#: through in the first place.
_ORG_COL_RE = re.compile(
    r"^\s*organization_id\s+[A-Za-z]+[^,]*?REFERENCES\s+([a-z_][a-z0-9_]*)",
    re.IGNORECASE | re.MULTILINE,
)


def discover_homonyms() -> dict[str, str]:
    """Tables whose ``organization_id`` references something OTHER than
    ``organization`` — derived from the migrations, never from a list.

    Derived on purpose: a hand-maintained list is exactly what
    :data:`HOMONYM_BLOCKED` is for, and a list checking itself proves nothing.
    This finds them; the map is the sign-off; :func:`main` refuses when the two
    disagree.
    """
    found: dict[str, str] = {}
    for path in sorted(_MIGRATIONS.glob("[0-9]*_*.sql")):
        parts = _CREATE_RE.split(path.read_text(encoding="utf-8"))
        for name, body in zip(parts[1::2], parts[2::2], strict=True):
            match = _ORG_COL_RE.search(body.split(";")[0])
            if match and match.group(1).lower() != "organization":
                found[name.lower()] = match.group(1).lower()
    return found


#: Words that are never a table name — the residue of prose that survives
#: comment stripping (e.g. a `CREATE TABLE` inside a string literal). Kept as a
#: last-resort assertion, not a filter: `discover_tables` RAISES on a hit so the
#: cause gets fixed, rather than quietly dropping a name that might be real.
_NEVER_A_TABLE = frozenset({"above", "below", "if", "not", "exists", "this", "the"})


def discover_tables() -> list[str]:
    """Every table the numbered migrations create, in name order.

    ``--`` comments are stripped first: these migrations explain themselves in
    prose *about SQL*, and reading that prose as schema put a table named
    ``above`` into a production maintenance-window script (see
    :data:`_LINE_COMMENT_RE`).
    """
    names: set[str] = set()
    for path in sorted(_MIGRATIONS.glob("[0-9]*_*.sql")):
        sql = _LINE_COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
        for match in _CREATE_RE.finditer(sql):
            name = match.group(1).lower()
            if name in _NEVER_A_TABLE:
                raise AssertionError(
                    f"{path.name}: discovered a table named {name!r}, which is "
                    "prose, not schema. Something is being read as a CREATE "
                    "TABLE that is not one (a block comment? a string "
                    "literal?). Fix the parser — do NOT filter the name away: "
                    "these files are applied by hand against production."
                )
            names.add(name)
    return sorted(names)


def _blocked_note() -> str:
    """The BLOCKED tables, in every generated file.

    In the header rather than a side document because the person reading these
    files is mid-window with a psql prompt open, and "which tables did this NOT
    cover" is the question they have no other way to answer.
    """
    if not HOMONYM_BLOCKED:
        return ""
    lines = [
        "--",
        "-- ⚠️ NOT COVERED BY THIS FILE — `organization_id` already means something",
        "-- else on these tables, so scoping them by that name would corrupt a",
        "-- business column. They carry NO tenant isolation until the column is",
        "-- renamed (owner call; see gen_tenant_migration.HOMONYM_BLOCKED):",
    ]
    lines += [f"--   {t:<18} {why}" for t, why in sorted(HOMONYM_BLOCKED.items())]
    return "\n".join(lines) + "\n"


def _header(phase: str, why: str, tables: int) -> str:
    return f"""-- ============================================================================
-- MT-1b · phase {phase} — GENERATED, DO NOT EDIT BY HAND
-- ============================================================================
-- Regenerate with: uv run python scripts/gen_tenant_migration.py
-- Spec: project-docs/specs/saas_multitenancy.md §1.3 · MT-1b · WS-29 · D15
--
-- {why}
--
-- Tables in this phase: {tables}
{_blocked_note()}--
-- ⚠️ NOT a numbered migration. `apply_migrations.sh` does not replay this
-- directory. Promoting it is a deliberate act taken against a database in a
-- maintenance window — see the module docstring of the generator for the
-- outage that makes that non-negotiable.
-- ============================================================================

"""


def gen_add_columns(tables: list[str]) -> str:
    out = [_header("1/4 add_columns",
                   "Nullable ADD COLUMN. No table scan, no lock of consequence. "
                   "Safe to apply on a live system.", len(tables))]
    for t in tables:
        out.append(
            f"ALTER TABLE {t}\n"
            f"    ADD COLUMN IF NOT EXISTS organization_id UUID\n"
            f"    DEFAULT current_setting('app.tenant_id', true)::uuid;\n"
        )
    return "\n".join(out)


def gen_backfill(tables: list[str]) -> str:
    out = [_header("2/4 backfill",
                   "Batched UPDATE. Re-runnable and interruptible — each statement "
                   "is idempotent, so a run that aborts can simply be run again. "
                   "This is the slow phase; expect it to be the long pole on any "
                   "table with real volume.", len(tables))]
    out.append(
        "-- The operator's own organization owns every pre-existing row: this box\n"
        "-- served exactly one tenant before MT-1b.\n"
    )
    for t in tables:
        out.append(
            f"UPDATE {t} SET organization_id = "
            f"(SELECT id FROM organization WHERE slug = 'default')\n"
            f" WHERE organization_id IS NULL;\n"
        )
    return "\n".join(out)


def gen_constraints(tables: list[str]) -> str:
    out = [_header("3/4 constraints",
                   "SET NOT NULL + FK + index. ⚠️ THIS IS THE ACCESS EXCLUSIVE "
                   "PHASE — it scans each table. Apply in a window, table by "
                   "table if necessary, and never behind a long-running "
                   "transaction (see the generator docstring: that is the exact "
                   "shape of the 14h44m outage).", len(tables))]
    for t in tables:
        out.append(
            f"-- {t}\n"
            f"DO $$\nBEGIN\n"
            f"    IF EXISTS (SELECT 1 FROM {t} WHERE organization_id IS NULL) THEN\n"
            f"        RAISE EXCEPTION 'MT-1b: {t} still has unowned rows — "
            f"run phase 2 (backfill) to completion first';\n"
            f"    END IF;\n"
            f"END $$;\n"
            f"ALTER TABLE {t} ALTER COLUMN organization_id SET NOT NULL;\n"
            f"ALTER TABLE {t} ADD CONSTRAINT {t}_org_fk\n"
            f"    FOREIGN KEY (organization_id) REFERENCES organization(id) "
            f"ON DELETE CASCADE;\n"
            f"CREATE INDEX IF NOT EXISTS {t}_org_idx ON {t} (organization_id);\n"
        )
    return "\n".join(out)


def gen_policies(tables: list[str]) -> str:
    out = [_header("4/4 policies",
                   "ENABLE + FORCE ROW LEVEL SECURITY + the policy. Instant — no "
                   "scan. ⚠️ AND IT IS A CLIFF: the moment this applies, any "
                   "connection that has not bound app.tenant_id reads ZERO ROWS. "
                   "That is the fail-closed property working (§0.1). MT-1c must "
                   "be deployed AND VERIFIED first, or the product goes dark.",
                   len(tables))]
    out.append(
        "-- Four clauses, each load-bearing (saas_multitenancy_implementation.md §1.1):\n"
        "--   ENABLE       turns the policy on for ordinary roles\n"
        "--   FORCE        applies it to the table OWNER too — without this the\n"
        "--                owner silently reads every tenant\n"
        "--   USING        filters what a query can SEE\n"
        "--   WITH CHECK   constrains what it can WRITE. Without it a tenant can\n"
        "--                INSERT a row stamped with another tenant's id.\n"
        "--   , true       makes an unset GUC return NULL (-> no rows) instead of\n"
        "--                RAISING, so an unconverted path fails closed and quiet\n"
        "--                rather than 500-ing everywhere at once.\n"
    )
    for t in tables:
        out.append(
            f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE {t} FORCE  ROW LEVEL SECURITY;\n"
            f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t};\n"
            f"CREATE POLICY {t}_tenant_isolation ON {t}\n"
            f"    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)\n"
            f"    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);\n"
        )
    return "\n".join(out)


def main() -> int:
    # This script's summary is full of ⚠️/→, and Windows consoles default to
    # cp1252: printing the plan raised UnicodeEncodeError and killed the run
    # BEFORE any file was written (measured 2026-08-10). A generator that
    # cannot run on a maintainer's own machine gets run from memory instead,
    # which is how the set on disk goes stale.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:            # py3.7+; absent on odd wrappers
            reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan without writing files")
    args = ap.parse_args()

    all_tables = discover_tables()

    # ── The homonym gate, BEFORE anything is generated ──────────────────────
    #
    # Detection is derived from the migrations; HOMONYM_BLOCKED is the human
    # sign-off. Refusing when they disagree is the only part that protects a
    # table added next month, because that is the case where nobody remembers.
    homonyms = discover_homonyms()
    undeclared = sorted(set(homonyms) - set(HOMONYM_BLOCKED))
    if undeclared:
        print("\n⚠️ REFUSING TO GENERATE. These tables have an `organization_id` "
              "that references something other than `organization`:")
        for t in undeclared:
            print(f"      {t:<24} organization_id -> {homonyms[t]}")
        print("\nScoping them by column name would write a tenant id into a "
              "business column and abort phase 2 mid-window. Either rename the "
              "column, or declare the table in HOMONYM_BLOCKED with its reason.")
        return 1
    stale = sorted(set(HOMONYM_BLOCKED) - set(homonyms))
    if stale:
        print("\n⚠️ REFUSING TO GENERATE. These are declared in HOMONYM_BLOCKED "
              "but no longer have a conflicting `organization_id`:")
        for t in stale:
            print(f"      {t}")
        print("\nIf the column was renamed, the table can now be scoped — drop "
              "it from HOMONYM_BLOCKED so it rejoins the generated phases.")
        return 1

    blocked = [t for t in all_tables if t in HOMONYM_BLOCKED]
    scoped = [t for t in all_tables
              if t not in EXEMPT and t not in HOMONYM_BLOCKED]
    exempted = [t for t in all_tables if t in EXEMPT]

    print(f"discovered {len(all_tables)} tables in infra/postgres/[0-9]*.sql")
    print(f"  tenant-scoped : {len(scoped)}")
    print(f"  exempt        : {len(exempted)}")
    for t in exempted:
        print(f"      {t:<24} {EXEMPT[t]}")
    print(f"  ⚠️ BLOCKED    : {len(blocked)} — no isolation, name collision")
    for t in blocked:
        print(f"      {t:<24} {HOMONYM_BLOCKED[t]}")
    unknown = sorted(set(EXEMPT) - set(all_tables))
    if unknown:
        print("\n  ⚠️ exempt names that match no discovered table "
              "(stale entry, or the table moved):")
        for t in unknown:
            print(f"      {t}")

    if args.dry_run:
        print(f"\n(dry run — nothing written)\n{_DENORMALISE_NOTE}")
        return 0

    _OUT.mkdir(parents=True, exist_ok=True)
    phases = {
        "01_add_columns.sql": gen_add_columns(scoped),
        "02_backfill.sql": gen_backfill(scoped),
        "03_constraints.sql": gen_constraints(scoped),
        "04_policies.sql": gen_policies(scoped),
    }
    for name, body in phases.items():
        (_OUT / name).write_text(body, encoding="utf-8")
        print(f"wrote {(_OUT / name).relative_to(_REPO)}")

    print("\n⚠️ These are NOT numbered migrations and will not be replayed by "
          "apply_migrations.sh. Apply phases 1-4 IN ORDER, against a scratch "
          "database first. Phase 4 requires MT-1c deployed and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
