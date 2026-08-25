"""The tenant boundary, as a ratchet (WS-29).

⚠️ **114 of Metorite's 147 tables still carry no tenant key.** That is not
a bug list — it is the honest state of a system built for one organisation. The
bug would be adding the 147th.

**137 → 123 → 113 → 114.** (+1 2026-08-10: `crm_auto_lead_cursors`, mig 163 —
per-mailbox cursor, tenant derived through `email_accounts`; see its baseline
entry.) WS-29a keyed all 17 `pm_*` tables of the day (migration 161) — and the
two that arrived after it, `pm_intake` (164) and `pm_task_watchers` (165), were
keyed at CREATE time, so the Projects app is **19 of 19** scoped — while
they were still empty enough to make it a one-line default rather than a
backfill. Then `main`'s MT-0d keyed `provider_keys`, `model_config`,
`mcp_servers` and `plugins`, and MT-1a introduced a control plane that is
cross-tenant on purpose. The ratchet is what turns each of those into a
*required* edit: the "gained a key, leave the baseline" rule below goes red the
moment a migration lands, and stays red until this file and the schema agree.

## Why this file exists ALONGSIDE `test_tenant_coverage.py`

They are not duplicates, and the difference is the whole reason this one
survived the merge with `main`.

`test_tenant_coverage.py` (MT-1b) asks whether the **generator** covers every
table — it reads `gen_tenant_migration.discover_tables()` and the `EXEMPT` map.
It never looks at what an existing `organization_id` actually *references*.

This file matches the **foreign key's target**, and that distinction is not
hypothetical:

    crm_contacts.organization_id  REFERENCES crm_organizations   -- a CUSTOMER
    pm_tasks.organization_id      REFERENCES organization        -- the TENANT

Three CRM tables carry the first kind. A name-based check reads them as scoped;
they are not scoped at all. That mistake was in the first version of THIS file
too — it published "6 tables scoped" when the answer was 3 — and matching the FK
target is what corrected it. The same blind spot then turned out to be live in
MT-1b's generator, which would have emitted `UPDATE crm_contacts SET
organization_id = <tenant>` into a column whose FK points at `crm_organizations`
and aborted phase 2 in the maintenance window. `HOMONYM_BLOCKED` is the fix;
the assertions below are what stop it coming back.

## The rules

  * a table **not** in the baseline, not exempt and not blocked must carry a
    real tenant FK — this is the case that matters, because it is every table
    nobody has written yet;
  * a baselined table may stay as it is;
  * a baselined table that **gained** a tenant key fails until it is removed
    from the baseline, so the debt figure above is always the real one;
  * every discovered table lands in exactly one of the four buckets — the
    partition `test_tenant_coverage.py`'s docstring promises and its assertions
    do not actually check.

That third rule is what makes the others credible. A baseline only ever edited
downward when somebody happens to notice is a baseline that quietly becomes
fiction.

**Scope note.** `organization_id` on the table is the *shape*, not the
enforcement. D-MT-2 — open when this file was written — was **answered on `main`
as D15: pooled, enforced by RLS** against the `app.tenant_id` GUC that
`acb_common.db.tenant_session()` binds. `specs/saas_multitenancy.md` is
canonical for that. The column is what every one of those designs wanted, so
nothing here changes because of it.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

#: `LiteLLM_*` is a vendored product with its own tenancy model; it is not ours
#: to scope and its tables never reach our code.
FOREIGN_PREFIX = "LiteLLM"


def _generator():
    """Import ``scripts/gen_tenant_migration.py`` (not an installed package).

    Read rather than copied: `EXEMPT` and `HOMONYM_BLOCKED` are decisions, and a
    second hand-maintained copy of a decision is a copy that will disagree.
    """
    path = _REPO / "scripts" / "gen_tenant_migration.py"
    spec = importlib.util.spec_from_file_location("gen_tenant_migration", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_tenant_migration"] = mod
    spec.loader.exec_module(mod)
    return mod


#: Tables that carry a tenant key today. Not a baseline — the goal state.
EXPECTED_SCOPED = {
    "app_user",
    "org_group",
    "org_role",
    # WS-29a — the whole Projects app, keyed while it was empty (17 tables,
    # migration 161) — plus the two WS-27u/v tables that arrived after it and
    # were keyed at CREATE time (164 `pm_intake`, 165 `pm_task_watchers`).
    # ⚠️ Listing them here is bookkeeping, not enforcement: this set is only
    # asserted to be a SUBSET of what `_scan()` finds, so an omission is silent.
    # The enforcement is `test_a_new_table_must_carry_a_tenant_key`.
    "pm_activities", "pm_custom_fields", "pm_intake", "pm_notifications",
    "pm_project_grants", "pm_projects", "pm_recurrences", "pm_tags",
    "pm_task_assignees", "pm_task_attachments", "pm_task_counters",
    "pm_task_links", "pm_task_personal", "pm_task_statuses",
    "pm_task_types", "pm_task_watchers",
    "pm_tasks", "pm_view_task_positions", "pm_views",
}

#: ⚠️ FROZEN at 115 (113 + the mig-163 mailbox cursor + the mig-189 retirement arm, the
#: only entry here that is a DEPLOYMENT fact rather than pre-tenancy debt). Every table predating the multi-tenant decision that is
#: neither exempt (a deliberate cross-tenant table — see the generator's
#: `EXEMPT`) nor blocked (a name collision — see `HOMONYM_BLOCKED`).
#:
#: Adding a name here is allowed and must come with a reason in the PR; adding
#: one *silently* is how a 113 becomes a 140 without anybody choosing it.
#:
#: Nothing that appears in the generator's `EXEMPT` or `HOMONYM_BLOCKED` belongs
#: here — one table, one bucket, asserted below.
BASELINE_UNSCOPED = {
# access_*
    "access_request",
# action_*
    "action_item",
# agent_*
    "agent_avatars", "agent_blob", "agent_file_history", "agent_run",
    "agent_skill_setting",
# app_*
    "app_audit", "app_data", "app_files", "app_grants", "app_pins",
    "app_tool_grants", "app_versions",
# apps_*
    "apps",
# audit_*
    "audit_event",
# chat_*
    "chat_message", "chat_session", "chat_session_agent",
    "chat_session_participant",
# copilot_*
    "copilot_config", "copilot_event",
# crm_* — ⚠️ `crm_activities`, `crm_contacts` and `crm_deals` are NOT here.
    # They carry a column called `organization_id` that REFERENCES
    # crm_organizations, a CUSTOMER COMPANY, so they are neither scoped nor
    # ordinary debt: they are BLOCKED in `gen_tenant_migration.HOMONYM_BLOCKED`
    # until the column is renamed, because a generator that scopes by name
    # would corrupt a business column.
    # crm_auto_lead_cursors (migration 163): per-MAILBOX cursor state, one row
    # per email_accounts id with an ON DELETE CASCADE FK — its tenant derives
    # transitively through email_accounts (org-scoped by the generated RLS
    # set), and a direct organization_id would be a second copy of a derivable
    # fact that the purge cascade already treats as mailbox-owned.
    "crm_auto_lead_cursors",
    "crm_deal_contacts", "crm_deal_statuses", "crm_lead_statuses",
    "crm_leads", "crm_lost_reasons", "crm_organizations",
    "crm_status_changes", "crm_sync_cursors", "crm_zoho_tombstones",
# custom_*
    "custom_api_definitions",
# customer_*
    "customer",
# deal_*
    "deal",
# dynamic_*
    "dynamic_agents",
# email_*
    "email_accounts", "email_actions", "email_ai_drafts",
    "email_assistant_settings", "email_attachments", "email_cold_senders",
    "email_contacts", "email_embeddings", "email_executed_rules",
    "email_folders", "email_knowledge", "email_learned_patterns",
    "email_messages", "email_newsletters", "email_rule_guidance",
    "email_rule_patterns", "email_rules", "email_senders", "email_sync_log",
    "email_thread_status", "email_voice_profiles",
# gtd_*
    "gtd_attachments", "gtd_contexts", "gtd_day_state", "gtd_folders",
    "gtd_horizons", "gtd_items", "gtd_people", "gtd_person_resumes",
    "gtd_projects", "gtd_reviews", "gtd_rollover_log", "gtd_settings",
    "gtd_spaces", "gtd_waiting",
    # gtd_retirement_arm (migration 189) is UNSCOPED ON PURPOSE, and it is not
    # the usual pre-tenancy debt the rest of this block records. It holds one
    # row meaning "a human authorised the gtd_* schema retirement on THIS
    # database" — a DDL fact, not a tenant fact. `DROP TABLE` is database-wide;
    # there is no world in which gtd_items is dropped for one organization and
    # kept for another, so an organization_id on it would be a column that can
    # only ever be wrong: it would invite a per-tenant arming that the thing it
    # guards cannot honour. Read by migration 190's guard, which runs as the
    # database owner outside RLS. See work_plan.md §6 (f) and D53.5.
    "gtd_retirement_arm",
# live_*
    "live_session",
# meeting_*
    "meeting", "meeting_bot", "meeting_note", "meeting_recording",
# message_*
    "message",
# notes_*
    "notes_glossary",
# org_*
    "org_group_member", "org_role_permission", "org_settings",
# pending_*
    "pending_actions", "pending_commit",
# person_*
    "person",
# pm_*
#   — all 17 left this baseline in WS-29a (migration 161). They are asserted
#     as scoped by `EXPECTED_SCOPED` above, so their absence here is checked
#     rather than merely assumed.
# project_*
    "project",
# summary_*
    "summary_run",
# task_*
    "task", "task_accounts",
# transcript_*
    "transcript_segment",
# user_*
    "user_permission_override", "user_role",
# wa_*
    "wa_accounts", "wa_ai_drafts", "wa_categories", "wa_chat_avatars",
    "wa_chat_labels", "wa_chat_status", "wa_chats", "wa_commitments",
    "wa_contacts", "wa_group_summaries", "wa_labels", "wa_media",
    "wa_message_embeddings", "wa_messages", "wa_saved_replies",
    "wa_sync_log", "wa_templates",
# workflow_*
    "workflow_modules", "workflow_run_pauses", "workflow_runs",
    "workflow_triggers", "workflow_versions",
# workflows_*
    "workflows",}


#: ``organization_id … REFERENCES organization`` — the TENANT, on the same line
#: or the next one. Whitespace-tolerant because the migrations column-align.
_TENANT_FK = re.compile(
    r"\borganization_id\b[^,]*?REFERENCES\s+organization\s*\(", re.I | re.S
)


def _references_the_tenant(body: str) -> bool:
    """Does this table body carry a tenant key — as opposed to a HOMONYM?

    ⚠️ **This function exists because the first version of this file was wrong,
    and wrong in the direction that flatters.** It matched the column NAME, so
    it counted `crm_activities`, `crm_contacts` and `crm_deals` as tenant-scoped
    on the strength of an `organization_id` that `REFERENCES crm_organizations`
    — a CUSTOMER COMPANY, not the tenant root. The published figure was six
    scoped tables; it was three.

    A guard that can be satisfied by a coincidence of naming is not a guard: any
    future table with an `organization_id` pointing anywhere at all would have
    passed silently, which is precisely the failure this ratchet exists to
    prevent. The foreign key's TARGET is the claim, so the target is what is
    matched.
    """
    return bool(_TENANT_FK.search(body))


def _scan() -> tuple[set[str], set[str]]:
    """Every table the migrations define, and which of them are tenant-scoped.

    Read from the migrations rather than from `schema.generated.sql`, which is
    stale (it predates migration 146 and knows about none of the `pm_*`
    tables), and rather than from a live connection, which this suite does not
    have.

    **`ALTER TABLE … ADD COLUMN organization_id` counts.** That is how
    `app_user` got its tenant key in migration 130, so a `CREATE TABLE`-only
    scan reports the one table that matters most as unscoped — it did, in the
    first version of this file, and the answer was checked against a real
    Postgres before this was written.
    """
    tables: set[str] = set()
    scoped: set[str] = set()
    for path in sorted(glob.glob("infra/postgres/*.sql")):
        if os.path.basename(path) == "schema.generated.sql":
            continue
        with open(path, encoding="utf-8") as handle:
            src = handle.read()
        for match in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_][a-z0-9_]*)\s*\((.*?)\n\);",
            src,
            re.S,
        ):
            tables.add(match.group(1))
            if _references_the_tenant(match.group(2)):
                scoped.add(match.group(1))
        for match in re.finditer(r"ALTER TABLE\s+([a-z_][a-z0-9_]*)(.*?);", src, re.S):
            body = match.group(2)
            if re.search(r"ADD COLUMN[^;]*\borganization_id\b", body) and (
                _references_the_tenant(body)
            ):
                scoped.add(match.group(1))
    return tables, scoped


def test_the_scan_finds_the_migrations_at_all() -> None:
    """The failure that would make every other assertion here vacuous: a glob
    matching nothing gives an empty set, which satisfies every `not new` below."""
    tables, scoped = _scan()
    assert len(tables) > 100, f"only found {len(tables)} tables — the glob is wrong"
    assert scoped <= tables


def test_the_baseline_names_no_table_that_no_longer_exists() -> None:
    """A baseline naming a dropped table overstates the debt, and the count in
    the docstring stops meaning anything."""
    tables, _ = _scan()
    stale = sorted(BASELINE_UNSCOPED - tables)
    assert not stale, f"BASELINE_UNSCOPED names tables that do not exist: {stale}"


def test_a_new_table_must_carry_a_tenant_key() -> None:
    """⚠️ THE rule. Everything else here is bookkeeping.

    A table added from now on is a table added while the system is knowingly
    becoming multi-tenant, and backfilling a tenant key onto live rows costs
    orders of magnitude more than declaring one on an empty table.
    """
    gen = _generator()
    tables, scoped = _scan()
    unscoped = tables - scoped - {t for t in tables if t.startswith(FOREIGN_PREFIX)}
    new = sorted(unscoped - BASELINE_UNSCOPED - set(gen.EXEMPT)
                 - set(gen.HOMONYM_BLOCKED))
    assert not new, (
        f"{new} has no `organization_id` REFERENCING `organization`. "
        f"Metorite is multi-tenant (specs/saas_multitenancy.md): give it "
        f"one, or add it to BASELINE_UNSCOPED with the reason in your PR."
    )


def test_a_table_that_gained_a_tenant_key_leaves_the_baseline() -> None:
    """⚠️ The rule that keeps the debt figure honest.

    Without it the baseline only shrinks when somebody remembers, and the
    number in this file drifts from the truth in the direction that flatters.
    """
    _, scoped = _scan()
    fixed = sorted(scoped & BASELINE_UNSCOPED)
    assert not fixed, (
        f"{fixed} now carries `organization_id` — remove it from "
        f"BASELINE_UNSCOPED and lower the count in this file's docstring."
    )


def test_every_table_lands_in_exactly_one_bucket() -> None:
    """The partition, which is what makes the count above mean anything.

    ⚠️ This is the assertion `test_tenant_coverage.py`'s own docstring promises
    ("every table is either in EXEMPT or will be scoped") and does not make —
    its source-level test checks that exempt entries have *reasons* and that
    discovery found more than 100 tables, neither of which fails when a table
    silently belongs to no bucket at all.
    """
    gen = _generator()
    tables, scoped = _scan()
    buckets = {
        "scoped": scoped,
        "baseline": BASELINE_UNSCOPED,
        "exempt": set(gen.EXEMPT),
        "blocked": set(gen.HOMONYM_BLOCKED),
    }
    known = {t for t in tables if not t.startswith(FOREIGN_PREFIX)}
    homeless = sorted(known - set().union(*buckets.values()))
    assert not homeless, f"{homeless} belongs to no bucket"
    for a, b in (("baseline", "exempt"), ("baseline", "blocked"),
                 ("exempt", "blocked"), ("scoped", "blocked")):
        both = sorted(buckets[a] & buckets[b] & known)
        assert not both, f"{both} is in both {a} and {b} — one table, one bucket"


def test_the_blocked_list_is_derived_not_asserted() -> None:
    """⚠️ The homonym gate, checked in CI without running the generator.

    `discover_homonyms()` reads the migrations; `HOMONYM_BLOCKED` is the human
    sign-off. The generator refuses to emit when they disagree — this asserts
    the same thing at test time, so a fourth homonym table added next month is
    caught by a red build rather than by a failed apply in a window.
    """
    gen = _generator()
    found = gen.discover_homonyms()
    assert set(found) == set(gen.HOMONYM_BLOCKED), (
        f"detected {sorted(found)} but HOMONYM_BLOCKED declares "
        f"{sorted(gen.HOMONYM_BLOCKED)}. A table whose `organization_id` points "
        f"somewhere other than `organization` cannot be scoped by that name."
    )
    for name in found:
        assert gen.HOMONYM_BLOCKED[name].strip(), (
            f"{name} is blocked with no reason given. The reason is the whole "
            f"artefact: it is what tells the next reader this is an unclosed "
            f"hole rather than a decision somebody already made."
        )


def test_an_exemption_cannot_launder_a_homonym() -> None:
    """`EXEMPT` means "cross-tenant by design". `HOMONYM_BLOCKED` means "we
    cannot scope this yet and it is a hole". Filing the second as the first
    would make a real gap read as a resolved decision — and `EXEMPT` is the map
    a reviewer is told to challenge, so it is exactly where it would hide."""
    gen = _generator()
    overlap = sorted(set(gen.EXEMPT) & set(gen.discover_homonyms()))
    assert not overlap, (
        f"{overlap} is exempt AND has a conflicting `organization_id`. Exempt "
        f"says the table needs no isolation; the homonym says it cannot get "
        f"any. Those are different problems and must not share an entry."
    )


def test_the_expected_scoped_set_is_real_not_aspirational() -> None:
    """A name in `EXPECTED_SCOPED` that is not actually scoped would make this
    file claim coverage it does not have."""
    _, scoped = _scan()
    missing = sorted(EXPECTED_SCOPED - scoped)
    assert not missing, f"{missing} is listed as scoped but carries no key"


def test_the_frozen_count_matches_the_baseline() -> None:
    """The docstring quotes 115. A baseline whose stated size and real size
    disagree is a baseline nobody trusts.

    ⚠️ This number going UP is normally a regression — it means somebody added
    an unscoped table. It moved 114 → 115 for `gtd_retirement_arm` (mig 189),
    which is the one entry here that is not pre-tenancy debt: it records a
    human authorising a schema DROP on this database, and `DROP TABLE` has no
    per-tenant form. Raising it for an ordinary new table is not the same act
    and should be refused.
    """
    assert len(BASELINE_UNSCOPED) == 115
