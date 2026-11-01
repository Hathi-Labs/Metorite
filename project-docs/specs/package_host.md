# Package host — every app is a package, arranged around the person (WS-PKG)

> **Product:** Metorite · **Feature:** the package host (the shell registry, installs, slots and the arrangement layer) · **Updated:** 2026-11-01 · **Owner:** vjvarada
> **Status:** 🚧 **IN BUILD.** PKG-1 (the jobs) and PKG-2 (the 23 pane links, the 8 preset ids and the 5 card keys) are built, with no change a member can see. The rest of this spec is not built.
> **Verified against code on 2026-11-01 at `f59a636b5`.** Each path in §12 was read on that commit.
> **Board:** WS-PKG in `work_plan.md` §2.1. **Decision:** D147. D12, D49, D87, D89, D111, D127, D136, D141 and D146 bind it.
> **Metorite:** project "Package Host", epic 1234, this spec task 1236. The slices PKG-1 to PKG-20 are tasks 1239 to 1258, in order.
> **Research first:** the comment "Research notes, 2026-11-01" on epic 491, copied into §11.
> **Gate summary:** every slice is AGENT-SAFE. The slices that change what a member sees follow the D145 loop: preview, owner approval, then the gates.

---

## 0. Thesis

The owner wants Metorite to change shape around each organization and each person, and to keep changing as the organization grows. An organization builds throwaway apps, sub-apps, and changes to the apps it has.

**Every app is a package.** Projects, the CRM and Email are packages. A company's "Field visits" app is a package. A board that Mira makes in chat for one week is a package. The shell, the assistant, ⌘K, the bell, Home, search and the MCP door read one run-time registry of installed packages. They stop reading lists that are written in code.

**An install has a scope and a lifetime.** Removal or expiry deletes every contribution of the install at once. So a throwaway app leaves nothing behind.

**A layer arranges the result around the person.** Packages decide what exists. The arrangement layer decides where it goes, for the org, for a team and for one member. The AI suggests a change and gives the reason. It never moves a stable region without a click.

This spec does not add a second app model. It grows the AI Studio package (`ai_studio.md` §3) into the one shape for every app.

---

## 1. Scope and non-goals

### 1.1 In scope

1. **One manifest for built-in apps** (§3). Each built-in app declares its contributions in the Studio manifest shape. Its views stay code.
2. **The run-time registry** (§5): installs, contributions, one resolve call for each viewer.
3. **Scopes and lifetimes** (§6): private, group, organization and system installs, an optional end date, and promotion.
4. **Sub-apps and overlays** (§8): a package that extends another package through slots that the host page declares.
5. **The arrangement layer** (§9): the org default, the team lens and the member overlay, stable and adaptive regions, and suggestions with reasons.

### 1.2 Non-goals

1. **No second plugin runtime.** The registry stores data. It runs no package code.
2. **No customer code in our process.** Member code runs only off the box (D127.2, D130).
3. **No third visibility axis.** Scopes reuse the D12 subject grammar.
4. **No Center.** A team view is a lens (D49, D87).
5. **No app-local look.** A package names no colour, font or pixel size (`ai_studio.md` §1.2).
6. **No move without consent.** The AI never moves a stable region (§9.2).
7. **No schema change for each tenant.** A field extension is a row, never a column (§8.3).
8. **No cross-company marketplace.** That needs its own spec.

---

## 2. Terms

| Term | Meaning |
|---|---|
| **Package** | One versioned manifest: what an app is and what it adds. `ai_studio.md` §3.6 holds the Studio layers |
| **Install** | One package version, installed at one scope, with an optional end date |
| **Contribution** | One thing an install adds at one point, such as a ⌘K job or a Home card |
| **Point** | A named kind of contribution (§4) |
| **Slot** | A named place on a host page that a package may fill (§8) |
| **Scope** | Who sees an install: `private`, `group:<slug>`, `org` or `system` |
| **Lifetime** | The optional `ends_at` of an install |
| **Overlay** | An install that changes another package within the change points it allows (§8.4) |
| **Arrangement** | The order and placement of contributions for one viewer (§9) |

---

## 3. One manifest for every app

### 3.1 The rule

**One manifest, one schema, one validator.** The A2 schema of `ai_studio.md` holds the Studio fields: objects, actions, views, workflows and skill. This spec owns the fields below. The A2 validator checks both halves.

| Field | Owner | Meaning |
|---|---|---|
| `kind` | this spec | `system` for a built-in app, `studio` for a Studio package |
| `contributes` | this spec | The contributions, keyed by point (§4). It grows the `shell` block of `ai_studio.md` §3.6 |
| `extends` | this spec | The id of the host package, for a sub-app or an overlay |
| `slots` | this spec | The slots that this package's pages declare (§8.1) |
| `needs` | this spec | The connections, features and packages that this package needs (§5.4) |
| `overrides` | this spec | The changes an overlay makes (§8.4) |
| `objects`, `actions`, `views`, `workflows`, `skill` | `ai_studio.md` | Unchanged |

**A2 is built** (#1562). The model and `validate_manifest` are in `packages/acb_common/acb_common/studio_manifest.py`. The JSON Schema is `studio_manifest.schema.json` beside it, and the TypeScript twin is `lib/studio/manifest.ts`. The model refuses an unknown key, so PKG-5 adds the fields of this table to that one model. Do not write a second model.

**The P0 files hold fragments.** Each file of §3.3 holds the `contributes` block of one built-in app. `shell_manifest.py` (PKG-1) reads the files and checks only what the loader needs. At PKG-5, `validate_manifest` checks the fragments too, and the loader keeps only the file read.

### 3.2 A built-in app

A built-in app is a **system package**. It ships in the repo and in the release. Its views are code, so its manifest has no `views` block. Every other contribution is data.

```json
{
  "studio": 1,
  "kind": "system",
  "id": "projects",
  "name": "Projects",
  "contributes": {
    "nav": [{"href": "/projects", "label": "Projects", "icon": "FolderKanban", "team": "across", "feature": "projects", "launch": "live"}],
    "jobs": [{"id": "capture", "label": "New task", "words": ["task", "todo"], "href": "/projects?do=capture", "icon": "CirclePlus"}],
    "search": [{"source": "projects.tasks"}],
    "needs": [{"provider": "projects.assigned_overdue"}],
    "cards": [{"key": "today"}],
    "agent": "projects-assistant"
  },
  "slots": [
    {"id": "task.panel", "kind": "record.panel", "record": "task"},
    {"id": "task.actions", "kind": "record.action", "record": "task"}
  ]
}
```

The example shows the shape, not the final schema. PKG-1 to PKG-3 move each real list into these files.

### 3.3 Where the files live

**One source file per app, in one folder.** The source is JSON in `packages/acb_common/acb_common/app_manifests/shell/<app>.json`. Python reads it at import. A script writes a generated TypeScript copy for the workbench, and a test fails when that copy is stale. This is the lockstep pattern of `test_genui_catalog_lockstep.py`, with one source in place of two hand-kept lists.

---

## 4. Contribution points

Each point keeps the seam that owns it today. Only the place where the seam reads its list changes.

| Point | What it adds | Consumer seam (origin/main) | Conflict rule |
|---|---|---|---|
| `nav` | A sidebar entry | `visibleSections` in `lib/nav.ts` | One owner for each `href` |
| `page` | A routed page of blocks | The Studio renderer (`ai_studio.md` D1) | One owner for each route |
| `jobs` | A verb in ⌘K "Do" | `lib/shell/registry.ts`, `routes/shell/intent.py` | One owner for each job id |
| `search` | Records in Find and in `search_company` | `retrieval/sources.py`, `routes/shell/search.py` | One owner for each source id |
| `needs` | A bell item | `routes/shell/needs.py` `register(Provider)` | `register` refuses a duplicate today |
| `cards` | A Home card | `lib/shell/presets.ts`, `routes/admin/me.py` | One owner for each card key |
| `skill` | A tool pack | `acb_skills/tool_packs.py`, then `ai_studio.md` F3 | One owner for each pack id |
| `actions` | Catalog rows | D136 manifests, `GET /studio/actions` (B1) | One owner for each action id |
| `triggers` | A workflow start on an event | `ingestion/event_hooks.py` | Many listeners are allowed |
| `agent` | The dock agent on a page | `pane.agent`, `lib/shell/assistant.ts` | One for each page |
| `slot` | Content in a host slot (§8) | The host page | The host declares the limit |
| `fields` | Fields on a host record (§8.3) | The field layer (PKG-13) | One owner for each field key |
| `settings` | A section in Settings | `personalization.md` §12.6 | One owner for each section id |

**A duplicate claim fails at install,** not at render. The install refuses with the point, the key and the package that holds it. This is the Cordis rule: "declaring a slot is claiming it".

---

## 5. The registry

### 5.1 Two tables

**The tables hold installs and contributions, never a manifest.** A Studio package is a row of `apps` (`114_custom_apps.sql`), and its versions are rows of `app_versions`. A system package is a file of §3.3. System packages have no rows here. The migration is `infra/postgres/NNN_package_registry.sql`, with the number taken at build time (R1).

**`package_install`**: one row for each install of one Studio app at one scope.

| Column | Type and rule |
|---|---|
| `organization_id` | `uuid NOT NULL`, the tenant default and FK of `315_brief_runs.sql` |
| `id` | `uuid NOT NULL DEFAULT gen_random_uuid()`, the primary key |
| `app_id` | `uuid NOT NULL`. FK `(organization_id, app_id)` to `apps (organization_id, id)`, `ON DELETE CASCADE` |
| `version` | `int NULL`, an `app_versions.version` of the app. NULL means the owner's working draft |
| `scope_kind` | `text NOT NULL`, CHECK in `private`, `group`, `org` |
| `scope_subject` | `text NOT NULL`. The owner's email for `private`, `group:<slug>` for `group`, and `org` for `org` |
| `installed_by` | `text NOT NULL`, CHECK `installed_by = lower(installed_by)` |
| `ends_at` | `timestamptz NULL`. NULL means the install does not expire |
| `state` | `text NOT NULL DEFAULT 'active'`, CHECK in `active`, `paused`, `archived` |
| `archived_at` | `timestamptz NULL`. CHECK: it is set when, and only when, `state` is `archived` |
| `created_at`, `updated_at` | `timestamptz NOT NULL DEFAULT now()` |

Constraints and indexes of `package_install`:
- `UNIQUE (organization_id, id)`, the target of the child FK.
- `UNIQUE (organization_id, app_id, scope_kind, scope_subject)`. One app has one install at each scope.
- CHECK `version IS NOT NULL OR scope_kind = 'private'`. Only the owner runs a draft.
- CHECK on `scope_subject`: `org` for `org`, a `group:` prefix for `group`, and a lower-case address with no prefix for `private`.
- A partial index on `(organization_id, ends_at) WHERE ends_at IS NOT NULL`, for the expiry job.

**`package_contribution`**: one row for each claim of one install at one point (§4).

| Column | Type and rule |
|---|---|
| `organization_id` | `uuid NOT NULL`, the tenant default and FK |
| `install_id` | `uuid NOT NULL`. FK `(organization_id, install_id)` to `package_install (organization_id, id)`, `ON DELETE CASCADE` |
| `point` | `text NOT NULL`, CHECK in the 13 points of §4 |
| `key` | `text NOT NULL`, the key that the point's conflict rule names (an `href`, a job id, a card key) |
| `payload` | `jsonb NOT NULL DEFAULT '{}'`, CHECK `octet_length(payload::text) <= 65536` |
| `position` | `int NOT NULL DEFAULT 0` |

The primary key is `(organization_id, install_id, point, key)`.

**Both tables** take the ENABLE, FORCE and policy block of `312_jobs.sql`, in DO blocks, through the `get_db()` seam (R5).

**`apps` gains one key.** The migration adds `UNIQUE (organization_id, id)` to `apps`, so that the composite FK can point at it. `id` is the primary key already, so no row can break it. This is an expand step (R6).

Five decisions that the columns carry:
1. **`needs_setup` is not stored.** The resolve computes it from the needs (§5.2 step 4), so it can never be stale.
2. **The scope grammar is D12's.** The code that writes a row (PKG-5 and later) passes each `scope_subject` through `normalize_grant_subject`. That function is in `acb_auth/permissions.py`. Do not copy it.
3. **A share with named people stays `private`.** The people get rows in `app_grants`, the sharing table of `114_custom_apps.sql`. So `apps.visibility = people` maps to a private install plus grants. The resolve (PKG-5) shows a private install to its owner and to each grantee.
4. **A duplicate claim is refused in code, not by an index.** Some points allow many claims (`triggers`, `slot`), and a private claim conflicts only with what its owner sees. PKG-5 checks the claims of §4 in the install transaction, under `pg_advisory_xact_lock` on the organization.
5. **A purge deletes the member's private installs.** Their contributions go by the cascade. An org or group install that the member made stays, and the purge counts it. This follows the rule of `routes/admin/members.py`: "purge the person, keep their work".

### 5.2 One resolve call

`GET /shell/manifest` returns the resolved contributions for the viewer:

1. Start with the system packages from code.
2. Add the org installs, the group installs of the viewer's groups, and the viewer's private installs.
3. Drop each contribution whose feature the viewer does not hold. The one entitlement seam decides (`acb_auth/permissions.py`).
4. Mark each install whose `needs` fail as `needs_setup` (§5.4).
5. Apply the arrangement (§9).

The workbench caches the answer through `useCachedResource`. A change to an install bumps a version, and the cache reads again.

### 5.3 Disposal

**Removal deletes every contribution of the install, in one transaction, by `install_id`.** Expiry and the kill switch use the same path. No point keeps a list of its own, so no point can keep a stale row. A paused install shows nothing within 10 seconds (`ai_studio.md` J3).

### 5.4 Needs

A package names what it needs: a connection, a feature, or another package. A missing need puts the install in `needs_setup`. The install shows one setup card, which links to the place that fixes it. It never shows an error. This is the Cordis `PENDING` state, and `ai_studio.md` I1 uses it for connections.

---

## 6. Scopes and lifetimes

### 6.1 Scopes

| Scope | Who sees it | Who installs it |
|---|---|---|
| `system` | Every member with the feature | The release |
| `org` | Every member with the feature | A holder of `studio:publish`, one approved version (`ai_studio.md` J2) |
| `group:<slug>` | The members of the group | A member of the group with `feature:studio` |
| `private` | One member | Any member with `feature:studio` |

### 6.2 The lifecycle of an app made in chat (D147, PKG-Q2)

1. **Made.** Mira builds the app in chat. It is private, and `ends_at` is 30 days later.
2. **Kept.** The member taps Keep. `ends_at` becomes null.
3. **Shared.** The member shares it with named people or a group. Each viewer runs it with their own rights.
4. **Company.** A holder of `studio:publish` reads the manifest diff and approves one version.

**Promotion changes the scope of the same install.** It never copies the package.

**Expiry.** A daily job archives each install whose `ends_at` has passed, through the disposal path (§5.3). The records of the archived package stay for 30 more days, and the owner can restore it in that time. Then the job deletes them. The owner gets a note in the bell 3 days before each step.

---

## 7. Trust tiers

| Tier | What it may hold | Who may build it | Safety line |
|---|---|---|---|
| **T0 · system** | Code views and any point | The repo, through the review loop | The review loop and the fences |
| **T1 · declarative** | Blocks, catalog actions, workflows and a skill | Any member with `feature:studio` | The A2 validator. Every action runs as the viewer |
| **T2 · sandboxed UI** | One code block (`ai_studio.md` L1) | As T1 | An iframe with `connect-src 'none'`. Each bridge call goes through the B2 door |
| **T3 · server logic** | Logic modules | First-party first. Members after SBX-9 | The off-box sandbox only (D127.2, D130) |
| **Never** | Package code in the gateway or the orchestrator | — | — |

A slot (§8) accepts T1 content only. A T2 block may fill a page of its own package, never a host slot.

---

## 8. Slots, sub-apps and overlays

### 8.1 Slots

A host page declares its slots in its manifest. A slot has an id, a kind and the record it shows.

| Slot kind | What it holds | First host pages |
|---|---|---|
| `record.panel` | A panel of blocks about this record | The CRM record sheet, the task panel |
| `record.action` | A button that calls one catalog action with this record | The CRM record sheet, the task panel |
| `record.field` | Extension fields on this record (§8.3) | The CRM record sheet, the task panel |
| `page.tab` | A tab of blocks on the host page | A project page, the CRM module list |

The host renders the slot through the one renderer. The package never draws outside it.

### 8.2 Sub-apps

A sub-app is a package with `extends`. Example: an "Installations" package with `"extends": "crm"`. Its nav entry shows under CRM in the sidebar, and its panel shows on each CRM account. It has its own objects and its own skill. When the host package is not installed, the sub-app is `needs_setup` (§5.4).

### 8.3 Field extensions

A package may add fields to a host record, such as "warranty end" on a CRM asset. The values live in one shared layer, keyed by the organization, the host record kind, the host record id and the package. The fields use the types of `ai_studio.md` §3.1. A read and a write go through the host app's door, as the viewer.

The three stores of today (`pm_custom_fields`, `crm_field_defs`, `app_data`) stay. Merging them is a non-goal of this spec.

### 8.4 Overlays

An overlay changes another package within the change points that the target allows. It is how an organization changes an app it already has.

An overlay may:

1. Fill a slot of the target.
2. Add fields to the target's records.
3. Hide, reorder or rename the target's nav entries, jobs and cards for its scope.
4. Add rules to the target's skill.

An overlay may not change an action's class, remove a permission check, or reach data that the target's actions do not serve. An admin sees each layer of a package and who installed it, in the J4 admin view. A system package update keeps every overlay, or names the conflict (`ai_studio.md` K2).

---

## 9. Arranged around the person

### 9.1 Three levels

1. **Org default.** An admin sets the sidebar order and the Home preset for each role.
2. **Team lens.** A team view filters the same apps (D87).
3. **Me.** The member's pins, order and preset. This exists today in `user_settings.shell_prefs`.

A nearer level wins: me, then the team lens, then the org default, then the system order.

### 9.2 Stable and adaptive regions

| Region | Where | How it changes |
|---|---|---|
| **Stable** | The sidebar, the app pages, the host slots | Only on a click by the member, or by an admin for the org default |
| **Adaptive** | A Today band on Home, the first ⌘K suggestions, the assistant's opening line | Rebuilt each morning from the member's signals. Each item says why it is there |

**Why.** Adaptive menus were not faster than fixed or customizable menus, and users preferred the ones they could customize (Findlater and McGrenere, CHI 2004). Predictability raises satisfaction on its own (Gajos et al., CHI 2008).

### 9.3 Signals and suggestions

The signals are the member's open tasks and their due dates, the calendar, the bell, the apps the member opens, and the assistant profile. The arranger makes two kinds of output:

1. **The Today band.** Up to five items, each with a reason, for example "3 visits planned today".
2. **Suggestions for a stable region.** Example: "You opened Field visits 9 times this week. Pin it?" A tap on Yes writes the change. A tap on No hides that suggestion for 30 days.

**No learning in silence** (D111). A learned change to a stable region is always a suggestion first.

### 9.4 Generated personal views

Mira can compose a view for one member from blocks over existing actions. Example: "My visits this week, with check-in". It is a private package with no objects and an end date (§6.2). It shows in the Today band. It costs nearly nothing, because it owns no data.

### 9.5 Appearance

Mode, density and accent live in the browser only today (`lib/theme/storage.ts`). PKG-19 stores them in `user_settings`, so they follow the member to a second device. The browser copy stays as the first paint.

---

## 10. Slices

**Tier** is the review tier of `next-ticket.md` step 1b. Every slice ships dark behind `PACKAGE_HOST_ORGS` (one `*_ORGS` reader), unless it changes no behaviour. Migration numbers are taken at build time (R1).

### Phase P0 — One manifest for each built-in app (no behaviour change)

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **PKG-1** | The manifest folder, its loader and the TypeScript generator (§3.3). Move the ⌘K jobs into it: one source for `registry.ts` `JOBS` and `intent.py` `JOBS` | Both lists come from the files. The `TestOneJobList` assertions pass, with their TypeScript parser pointed at the generated copy or the loader. A new test fails when the generated copy is stale. No job changes its id, label, link, gate or order | STD | AGENT-SAFE |
| **PKG-2** | Move the panes, the presets and the cards into the files: `SHELL_PANES`, `SHELL_PRESETS` and `SHELL_CARDS` derive from them | `test_auth_me_shell.py::TestOneVocabulary` passes. `nav.test.ts` still counts 14 live panes | STD | AGENT-SAFE |
| **PKG-3** | Move `SCOPE_APPS`, `ALSO_SERVES`, `HREF_FEATURES` and `ALWAYS_ALLOWED` into the files | A test fails when a shell map is written by hand outside the files. The search, dock and access tests pass unchanged | STD | AGENT-SAFE |

### Phase P1 — The run-time registry

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **PKG-4** | The two tables of §5.1, the key on `apps`, the generated tenant files, and the purge rows of §5.1 decision 5 | Expand-only migration (R6). R8 in `tests/unit/test_package_registry_r8.py`: org B reads none of org A's rows, an unbound session reads none, a contribution cannot point at another org's install, and a delete of an install deletes its contributions. `test_tenant_coverage.py` passes with a new case `test_the_package_registry_tables_are_tenant_scoped`. `test_admin_member_purge.py` passes with the new rows | HIGH | AGENT-SAFE |
| **PKG-5** | `GET /shell/manifest`, the resolve of §5.2. The fields of §3.1 join the A2 model, and `validate_manifest` checks the P0 files | R8: a private install shows for its owner only, a group install for its members only. A contribution with a feature the viewer lacks never shows. A duplicate claim fails at install | HIGH | AGENT-SAFE |
| **PKG-6** | The workbench reads the resolved manifest behind the flag | With the flag off, the shell is byte-for-byte as before. With it on, an org install shows in the sidebar, ⌘K and the bell. Visual review in four contexts | STD | AGENT-SAFE |
| **PKG-7** | Features from installs pass the one entitlement seam | A feature that only an install declares is visible to a holder of `*`. The `FEATURES` tuple stays the system list | HIGH | AGENT-SAFE |
| **PKG-8** | Studio H1 and H2 use this path. `ai_studio.md` §14 points here | An installed Studio app reaches every point of §4 through the registry. No Studio-only list exists | STD | AGENT-SAFE |

### Phase P2 — Lifetimes and promotion

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **PKG-9** | Disposal by install id (§5.3), with the kill switch | A test removes an install and finds no row of it at any point. A paused install shows nothing within 10 seconds | HIGH | AGENT-SAFE |
| **PKG-10** | `ends_at`, Keep, and the expiry job with the archive and the restore (§6.2) | An install expires on its date. A restore within 30 days brings it back whole. The bell note comes 3 days before each step | HIGH | AGENT-SAFE |
| **PKG-11** | Promotion: private → people or a group → org, on the same install. It joins `ai_studio.md` J2 | A promotion keeps the install id. A share to the org without approval stays pending | HIGH | AGENT-SAFE |
| **PKG-12** | "Make it an app": Mira builds a private package from chat (`ai_studio.md` M1, grown) | A chat request makes a private install with `ends_at`. It opens by link after the chat ends | STD | AGENT-SAFE |

### Phase P3 — Slots, sub-apps and overlays

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **PKG-13** | The shared field-extension layer (§8.3) | Expand-only migration. R8: FORCE RLS holds. A value of the wrong type is refused with the field name | HIGH | AGENT-SAFE |
| **PKG-14** | Slots on the CRM record sheet and the task panel (§8.1) | A T1 package fills a panel and a button. A T2 block in a host slot is refused at install. The host tests pass unchanged. Visual review | STD | AGENT-SAFE |
| **PKG-15** | Sub-apps: `extends`, the nav sub-entry, `needs_setup` when the host is missing | A sub-app of CRM shows under CRM. Without CRM it shows its setup card | STD | AGENT-SAFE |
| **PKG-16** | Overlays (§8.4), and the layer list in the J4 admin view | An overlay hides and renames a job for its scope only. An overlay that changes an action's class is refused | HIGH | AGENT-SAFE |

### Phase P4 — Arranged around the person

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **PKG-17** | The org default and the team lens of §9.1 | An admin sets a sidebar order for a role. A member's own pins still win | STD | AGENT-SAFE |
| **PKG-18** | The Today band, with a reason for each item (§9.3) | The band shows at most five items, each with a reason. A test shows that no stable region moves without a member action. D145 preview first | STD | AGENT-SAFE |
| **PKG-19** | Pin suggestions with a reason, and generated personal views (§9.3, §9.4) | Yes writes the pin. No hides that suggestion for 30 days. A generated view is a private install with an end date | STD | AGENT-SAFE |
| **PKG-20** | Appearance in `user_settings` (§9.5) | A second browser opens with the member's mode, density and accent | STD | AGENT-SAFE |

### Phase P5 — Code tiers

No new slices. `ai_studio.md` L1 (the T2 code block) and B5 (member logic after SBX-9) carry this phase.

**The first dispatchable slice is PKG-1.** It changes no behaviour and waits on no question.

---

## 11. What other apps do

Research of 2026-11-01. The full notes, with a "Not verified" line for each source, are the comment "Research notes, 2026-11-01" on epic 491.

| Product | What it does | We take | We do not take |
|---|---|---|---|
| **DeepSeek Harness** (vendor repo, Cordis kernel) | About 150 plugin services. Each registration returns a disposer. A plugin with an unmet need waits in `PENDING`. A UI slot has one owner | Disposal by install (§5.3), `needs_setup` (§5.4), one owner for each key (§4) | Plugins in the main process with full trust. Its own safety notice says it "must not be treated as secure or production-ready" |
| **Backstage** (vendor docs) | Every feature is an extension with an `attachTo` parent, and the host can override any extension | Built-in apps are packages too (§3) | Compiled-in third-party code |
| **VS Code** (vendor docs) | Manifest contributions, lazy activation, a remote kill | One manifest of contributions, the kill switch (§5.3) | Extension code with editor rights |
| **MCP Apps** (open standard, 2026-01-26) | A sandboxed `ui://` view, CSP from declared domains, a separate origin | The T2 rules (§7) | Optional consent for writes |
| **A2UI** (open protocol) | JSON from an approved component catalog | T1 is the default tier (§7) | A second protocol in our own client |
| **Salesforce, ServiceNow, Power Platform, Atlassian Forge** (vendor docs, excerpts) | Metadata as schema, cross-app access denied by default, layered solutions, re-consent on a permission change | Overlays within declared change points, and a visible layer list (§8.4) | Silent "top layer wins" overrides |
| **Retool, Airtable Omni, Microsoft Agent Builder** (vendor claims) | AI-built apps run with the viewer's rights. An admin limits sharing with the whole org | Promotion changes the scope of the same install (§6.2) | — |
| **Findlater and McGrenere 2004, Gajos et al. 2008** (papers) | Users prefer menus they can customize. Predictability raises satisfaction | Stable and adaptive regions (§9.2) | Menus that reorder by themselves |

**Not verified.** The Salesforce, ServiceNow, Power Platform and Forge rows rest on search excerpts. The paper rows rest on summaries, not full texts. **No source** shows an expiry for AI-built apps, so §6.2 is our own design.

---

## 12. Current file paths

Each path was read on `f59a636b5`.

**Workbench** (`workbench/control_plane/src/`)
- `lib/nav.ts` (`NavPane`, `NAV_SECTIONS`, `visibleSections`), `lib/nav.test.ts` (14 live panes)
- `lib/shell/registry.ts` (`ShellJob`, `JOBS`), `lib/shell/presets.ts` (`CARD_KEYS`), `lib/shell/assistant.ts` (`ALSO_SERVES`), `lib/shell/seams.test.ts`
- `lib/access.ts` (`HREF_FEATURES`, `ALWAYS_ALLOWED`)
- `lib/theme/storage.ts` (appearance in the browser)
- `components/GenerativeUINode.tsx`, `lib/useCachedResource.ts`

**Gateway** (`apps/services/gateway/gateway/`)
- `routes/shell/intent.py` (`JOBS`), `routes/shell/needs.py` (`REGISTRY`, `register`), `routes/shell/search.py` (`PROVIDERS`, `SCOPE_APPS`)
- `routes/admin/me.py` (`SHELL_PRESETS`, `SHELL_CARDS`, `SHELL_PANES`)
- `retrieval/sources.py` (`REGISTRY`)

**Packages**
- `packages/acb_auth/acb_auth/permissions.py` (`FEATURES`, `EffectiveAccess`)
- `packages/acb_common/acb_common/app_manifests/` (D136 `App`, `Route`)
- `packages/acb_skills/acb_skills/tool_packs.py` (`PACKS`)

**Schema**
- New migrations are `infra/postgres/NNN_*.sql`. The patterns to copy are `312_jobs.sql` (the RLS block) and `315_brief_runs.sql` (two tables and a composite FK). `scripts/gen_tenant_migration.py` writes `infra/postgres/generated/`
- `infra/postgres/114_custom_apps.sql` (`apps`, `app_versions`, `app_grants`, `app_data`), `155_projects_custom_fields.sql`, `244_crm_mirror_columns.sql` (`crm_field_defs`), `239_user_settings_per_org.sql`, `240` (`shell_prefs`)

**Tests**
- `tests/unit/test_shell_intent.py` (`TestOneJobList`), `tests/unit/test_auth_me_shell.py` (`TestOneVocabulary`), `tests/unit/test_tenant_coverage.py`, `tests/unit/test_studio_catalog_r8.py` (the R8 pattern), `tests/unit/test_admin_member_purge.py`
- `apps/services/gateway/gateway/routes/admin/members.py` (`_PURGE_DELETES`, `_PURGE_KEEPS`)

---

## 13. Verification commands

Each slice names its own tests. These bind every WS-PKG PR:

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"   # R8 needs a database
uv run python scripts/affected_tests.py --run
uv run pytest tests/unit/test_shell_intent.py tests/unit/test_auth_me_shell.py tests/unit/test_tenant_coverage.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/nav.test.ts src/lib/shell/seams.test.ts
bash scripts/ci_local.sh
```

A slice that changes UI also runs the `visual-review` skill in light mode, at compact density, under a changed accent and at four widths.

---

## 14. Fences (R7)

| Rule | Fence |
|---|---|
| One source for each shell list | New with PKG-1: `tests/unit/test_shell_manifests.py` (the generated copy is current, and no hand-kept list remains) |
| A new table is tenant-scoped | `tests/unit/test_tenant_coverage.py` |
| No app mounts its own palette, bell, rail or ⌘K | `src/lib/shell/seams.test.ts` |
| The live pane count changes only on purpose | `src/lib/nav.test.ts` |
| Removal leaves no contribution | New with PKG-9: `tests/unit/test_package_disposal_r8.py` |
| A stable region moves only on a member action | New with PKG-18: `src/lib/shell/arrangement.test.ts` |

---

## 15. Owner questions

The owner accepted PKG-Q1 to PKG-Q5 as recommended on 2026-11-01 (D147).

| Id | Question | Answer |
|---|---|---|
| PKG-Q1 | Do built-in apps become packages on the same manifest? | **Yes.** Their views stay code (T0) |
| PKG-Q2 | How long does an app made in chat live? | **30 days unless kept.** Then 30 days in the archive, then deletion (§6.2) |
| PKG-Q3 | May a company app add a panel or a button to a built-in page? | **Yes**, T1 only, into declared slots (§8) |
| PKG-Q4 | How far may the product rearrange itself for each person? | **It suggests and never moves** a stable region (§9.2) |
| PKG-Q5 | Which harness lessons go first? | **Replay tests and logged model inputs**, then the goal record. `web_research.md` §8 carries them |
