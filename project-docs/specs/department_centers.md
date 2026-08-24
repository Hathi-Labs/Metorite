# Department Centers — one platform, many projections

> ## ⚠️ D49 (2026-08-24) — the Centers **surface** is withdrawn; this spec is now a design record
>
> The owner directed that the concept of Centers be removed from the application
> "for the time being". What that changed, exactly:
>
> - **Deleted:** the "Centers" section in the sidebar and the home grid. Nothing
>   navigates to a Center any more.
> - **Kept, untouched:** `workbench/control_plane/src/lib/centers.ts`, the
>   `/centers/<slug>` routes, the `center.*` feature rows (migration 140), and
>   the `group:<slug>` slice grants D12 is built on. The Center is no longer a
>   **destination**; it is still the **scoping primitive**, and the live Projects
>   grant model depends on it.
> - **Parked, not cancelled:** WS-13 · WS-14 · WS-15 · WS-16. Nothing dispatches
>   from this spec today.
> - **§5's Center roster (D22) is not repealed.**
> - **Center packages are retired as a pricing object** — see
>   `specs/launch_surface.md` §4 and §5, which is the authority for all of the
>   above. Where this spec and that one disagree, that one wins.


**Status:** Phase A shipped (UI scaffold + feature gating) — Centers reachable via the `center.*` feature vocabulary since 2026-08-03 (merged; nav gating decided by the catalog per #389). *(Header corrected 2026-08-09.)*. §2 records the defect and the registration checklist that prevents its recurrence. Phase B groups admin UI + seed shipped pending review (2026-08-01 — directory read view still open) · **Date:** 2026-08-03 · **Owner:** vjvarada

**Verified against code:** 2026-08-03 (WS-14 doc remediation, on `ws-14-doc-remediation`
off `bebbd924`; **repair round the same day off `264f881e`**). Scope of that pass: **§3
Phase C only** — all four bullets were audited against the tree, three were found to rest
on things that do not exist, and each now carries acceptance and an **AGENT-SAFE /
OWNER-GATE** label. §1, §2 and Phases B/D/E were not re-measured in that pass and keep
their earlier stamps. Findings, so no reader has to re-derive them: `email_account_member`
**exists nowhere in the repo** (0 hits across
`*.sql` and `*.py`); the seven agents the team-instancing bullet pointed at
(`sales`, `billing`, `delivery`, `startup-coach`, `triage`, `reconciler`, `strategy`)
**do not exist** — `apps/agents/` holds exactly six others; and `pending_actions`
(`infra/postgres/66_pending_actions.sql:13-38`) has **no column naming the requesting
member, their group, or a Center**, so per-Center approvals is a schema change behind an
open owner question, not a filter.

**Repair round, 2026-08-03 (review REQUEST-CHANGES on the pass above).** Four defects in
that pass, all fixed here: (1) **C1's acceptance could go green with no way to create a
grant** — done-when 1 now names a caller-reachable creation path (method, path,
permission) and done-when 2 requires the grant under test to be created *through it*, not
by a fixture `INSERT`; (2) **`role` was in the proposed schema with zero done-when
touching it** — it is **cut**, with the reasoning and the additive path back recorded;
(3) **C4 repeated an absolute this ticket exists to stop** — *"`actor` is the proposing
agent … not the human behind it"* is **false**, `routes/apps/tools.py:393` and
`routes/apps/actions.py:345` both write `app:<slug>:<email>`; the six proposers and five
actor shapes are now measured in place and **the OWNER-GATE verdict is unchanged and
better supported**; (4) **C2's "NOT DISPATCHABLE" was a third gate token** in a corpus
whose contract allows two — mapped onto AGENT-SAFE (the doc action) + blocked (the
build). C3 additionally states that its migration is pre-provisioning with columns
intentionally unread, and reproduces D3's column shape.

The commitment this document records: **Metorite stays one deployment, and
departments get Centers — scoped projections of the same platform, never
separate systems.** A "Sales Center" is the sales team's slice of the one
platform's apps, agents, memory, and workflows; it is not a second product that
"feeds data back."

This supersedes the earlier informal framing of per-department apps as separate
systems. It does not change the tenant rule — **but the tenant rule itself changed on
2026-08-08 (D15, `saas_multitenancy.md` §1)**, so state it in its current form:

> **A Center is never a tenant.** A tenant is an `organization_id` row, isolated by
> Postgres RLS; a Center is an `org_group` *inside* one tenant. A separate **deployment**
> is now a *placement* (a priced tier), not the tenant boundary — and it was never
> available for a department either way.

*(The superseded phrasing read "a separate deployment is reserved for a separate
organization, never for a department." The second half is unchanged and still binding;
the first half described D11, which D15 re-took.)*

**Why not separate systems.** Every load-bearing capability shipped in July is
cross-cutting and assumes one deployment: intersection authority
(`groups_sessions_authority.md` §3), agent delegation at intersected clearance
(`agent_architecture.md` §8), cross-system workflows (`workflows_app.md` §1.1),
`org:global` memory readable from every scope (`memory_architecture.md`,
`agent-kinds.md` §5), and the single Action Broker / approvals / audit path.
Splitting by department severs all five.

---

## 1. Nomenclature — the words we commit to

UI copy and specs use these terms consistently. Renames are cheap now and
expensive after the team learns the wrong word.

| Term | Meaning | Never call it |
|---|---|---|
| **Center** | A scoped projection of the platform for an audience: Personal, a department, or the Company. | portal, workspace, hub, module (in UI) |
| **Personal Center** | The signed-in user's own slice: apps mapped one-to-one with the person. | "My apps" |
| **Company Center** | The founder/exec projection: org-wide dashboard, live activity, approvals, digests. | Executive Center (audience is exec; *content* is the company) |
| **module** | The **backend** container primitive from research §5 (org_access Phase 2). UI says "Center"; schema and specs say `module`. | — |
| **group** | `org_group` — the scoping primitive. **Group slug = center slug, 1:1** (`sales`, `marketing`, `finance`, `operations`, `people`, `company`). UI copy may say "team" when addressing humans. | department (in schema) |
| **Workshop** | A builder surface: **App Workshop** (`/build/apps`), **Agent Workshop** (`/build/agents`). | Workbench, Creator, Studio (for items) |
| **Studio** | The nav *section* holding cross-cutting creation surfaces (Chat, Workflows, the Workshops). | — |
| **app / sub-app** | An app is a first-party vertical surface (Email, Tasks). A **sub-app is an (app, scope) pair** — the same app projected into a Center, never a fork. | — |
| **Agent Registry** | The admin page for registered agents (`/agents`). Renamed from "Agents" to stop colliding with Agent Workshop. | — |

Naming rules:

1. **Feature slugs:** `center.<slug>`; **routes:** `/centers/<slug>`; **group
   slugs:** `<slug>`. One slug, three namespaces, zero mapping tables.
2. **A Center item is (app + scope).** Sales→Tasks is the Tasks app filtered to
   `group:sales`; the shared mailbox is the Email app on a team-owned account.
   Forking an app per department is the bloat failure mode — refuse it in review.
3. **Live vs planned is stated, not implied.** Scaffold surfaces carry their
   build status (`lib/centers.ts` statuses; "Scaffold" badge on landing pages).
   A card that opens an unscoped surface says so in its caveat.

## 2. The information architecture (shipped)

Sidebar and home page (source of truth: `workbench/control_plane/src/lib/nav.ts`,
deriving Centers from `src/lib/centers.ts`):

1. **Personal Center** — Dashboard¹ · Email · WhatsApp · Tasks · Notes ·
   Memories · Artifacts. All personally instanced (`agent-kinds.md` §6).
2. **Centers** — Sales · Marketing · Finance · Operations · People · Company.
   Each is one nav item landing on `/centers/<slug>`; as sub-apps go live a
   Center's landing page remains its front door.
3. **Studio** — Chat · Workflows · App Workshop · Agent Workshop. Everyone can
   hold these tools (feature-gated); every *object* made with them (session,
   workflow, app, agent) is personally or team scoped and optionally shared.
4. **Admin** — Models · Agent Registry · Approvals · Integrations ·
   Live Activity · Members & Roles (admin-flag gated).

¹ `/dashboard` renders the company overview today; the personal rollup replaces
it here in Phase D, and the company view becomes the Company Center dashboard.

**Access:** migration `140_center_features.sql` seeds `center.*` rows
(category `centers`, none member-default). Owners/admins see every Center via
`feature:*`; everyone else sees a Center only when a role or override grants
it. Granting a department = `allow feature:center.sales` (+ group membership,
once groups have a UI). Route guards: `lib/access.ts` maps `/centers/<slug> →
center.<slug>`.

> **Seeding the catalog row is not enough — the slug must also be in
> `acb_auth.permissions.FEATURES`** (fix built on `ws-13-centers-feature-vocabulary`,
> 2026-08-03 — ~~unmerged; until it lands, no Center is reachable by *anyone*~~
> **MERGED; Centers reachable since 2026-08-03** *(corrected 2026-08-09 — and #389 has
> since made the catalog, not the code mirror, decide nav; retained as the defect
> record)*).
> `/auth/me` returns `list(access.allowed_features())`, and that method iterates
> the hardcoded Python tuple, never `feature_catalog`; the wildcard in
> `feature:*` is only ever evaluated against those literals, so an owner
> holding `*` still gets an empty Center set.

**Registering a Center — every place, or it is unreachable.** A Center is one
concept spread across five declarations, and omitting any one of them fails
*silently* in a different way. This is the checklist; nothing else in this spec
supersedes it.

| # | Where | What it decides | Omitted ⇒ |
|---|---|---|---|
| 1 | `workbench/control_plane/src/lib/centers.ts` | The registry the UI renders — `nav.ts` builds the Centers section from it, `access.ts` the `/centers/<slug>` → `center.<slug>` route map | No nav item, no landing page, no route guard entry |
| 2 | `infra/postgres/<next>_*.sql` — a `feature_catalog` row (category `centers`) **and** an `org_group` row, the way `140_center_features.sql` + `141_seed_center_groups.sql` did for the six (**find the next free number by listing the directory; never assume one**) | The admin UI's grantable-feature list and its category grouping; the group the Center projects | The feature cannot be granted or denied from `/settings/members` or `/settings/roles`, and the Center has no team behind it |
| 3 | `acb_auth.permissions.FEATURES` | The **only** vocabulary `allowed_features()` iterates; `/auth/me` returns exactly it | The pane is dropped from the nav and `/centers/<slug>` hits AccessGate — **for every principal, owner included** |
| 4 | `gateway/routes/admin/groups.py::CENTER_GROUP_SLUGS` | Which groups pair 1:1 with a Center — the grant-access toggle and the undeletable-group rule | The group is deletable and "grant the department in one admin action" does not appear |
| 5 | `tests/unit/test_org_access_control.py::EXPECTED_CENTER_SLUGS` | The one retyped literal — the anchor that stops the two invariant tests going vacuous when a derivation source empties | The suite goes red until you update it — deliberately: changing the set of Centers is a decision, not a refactor |

Rows 1, 3 and 4 are pinned to each other by
`tests/unit/test_org_access_control.py::test_centers_registry_matches_the_feature_vocabulary`
(it parses `centers.ts`, so a retyped copy cannot drift) and
`::test_every_center_has_a_feature_slug`. Row 2 is not machine-checked — the
catalog table is the admin UI's list, not the authorization vocabulary.

**Center rosters** (sub-apps + status) live in `lib/centers.ts` — that file is
the registry; this spec deliberately does not duplicate it. Highlights: Sales
(proposal generator, Zoho pipeline, shared mailbox, lead-intake workflow),
Operations (production tracker, inventory/BOM, dispatch, service & AMC — the
strongest early candidate for a hardware company), Company (dashboard, live
activity, approvals live today; digests + reviewed org knowledge planned).

## 3. Work plan — Phases B–E

Phase A (this scaffold) is shipped. Each later phase is independently
shippable and folds in the pending items from earlier plans it depends on.

### Phase B — Groups become real *(the unlock; do first)*

> **Update 2026-08-01: first two bullets shipped pending review.** Gateway
> `/admin/groups` CRUD + membership (`routes/admin/groups.py`, gated on the
> members-admin permissions; the center-feature grant additionally requires
> `admin:access:manage`), the `/settings/groups` admin surface ("Teams" in UI
> copy, per §1), and a seed migration for the six groups (idempotent,
> DO NOTHING so admin edits survive redeploys). Verified by
> `tests/unit/test_admin_groups.py`. The directory read view (bullet 3) is
> still open.

- ✅ **Groups admin UI** over `org_group` / `org_group_member` (mig 138) — CRUD +
  membership + lead role. Was flagged as the gap in `groups_sessions_authority.md` §6.
- ✅ Seed the six groups (slug = center slug). Backfill: adding a member grants
  their department's `center.*` feature alongside group membership (one admin
  action — an allow override with reason `group membership: <slug>`; removing
  membership deliberately does NOT auto-revoke it).
- People Center's "Directory & org chart" is the same data rendered — build the
  read view here, not a parallel store.

### Phase C — Scoping deepens (org_access Phase 2, applied per Center)

Board row: `work_plan.md` §2 **WS-14**. The binding mechanism for every bullet below is
`tenancy_and_visibility.md` §3.2: **extend the shipped `email | group:<slug> | org`
subject vocabulary; do not invent a second one.** Each bullet carries its gate label per
the agent-ready spec contract item 7.

#### C1 — Tasks team slice · **AGENT-SAFE** · ~1 PR + 1 migration

`/tasks` scoped to the group's projects — the first (app + scope) sub-app, proving §1
rule 2. This is the bullet that has read "`/tasks` scoped to the group's projects" and
nothing else for weeks; that is not testable, so it is written out here.

**The grant table.** `tenancy_and_visibility.md` §4.1 makes the call and records the
alternatives: **`gtd_project_grant (project_id, subject, granted_by, created_at)`**, a
`gtd_*`-local table with a real FK onto `gtd_projects` — not a polymorphic
`object_grants`, and not a reuse of `app_grants` (whose key *is* `app_id UUID NOT NULL
REFERENCES apps(id)`, `114_custom_apps.sql:59`). It is a
`DECISION (agent-proposed, owner may overrule)` — registered on the board as
**D13** (`work_plan.md` §3); read §4.1's reasoning before overruling it, and if it is
overruled, the alternative is its own ticket that *also* migrates `app_grants`, never a
side effect of this slice.

> **No `role` column — decided 2026-08-03, not deferred.** Earlier drafts of this
> schema carried `role`, copied from `app_grants.role`
> (`114_custom_apps.sql:61-62`, `CHECK (role IN ('use','edit','own'))`). It is **cut**.
> `app_grants.role` earns its place because something branches on it — `can_edit`, at
> `routes/apps/_common.py:473`. Nothing would branch on a project grant's role in this
> slice: every done-when below is a **read**-path clause, and done-when 7 explicitly
> lets every write predicate stay owner-only. `role` would therefore ship with one
> legal value and no reader — a dead column, and an invitation for the next
> implementer to invent write semantics nobody chose. Whether a grantee may *edit*
> items in a granted project is a real question and it is **not answered here**; when
> it is, it arrives as its own ticket —
> `ALTER TABLE gtd_project_grant ADD COLUMN role …` at the next free migration number
> resolved at build time (R1). Additive, no backfill, no data at risk.

**The migration.** One new file in `infra/postgres/`, at **the next free number resolved
at build time by listing that directory** — never a number copied out of a document
(R1). Idempotent `CREATE TABLE IF NOT EXISTS`, per the conventions in
`infra/postgres/README.md`.

**The read path.** "Mine" ∪ "granted to a group I'm in". Measured blast radius:
**27 `user_id = :` predicates in `apps/services/gateway/gateway/routes/tasks/items.py`**
(re-count before starting: `rg -c "user_id = :" apps/services/gateway/gateway/routes/tasks/items.py`).
Resolve the caller's group set **once per request**, mirroring
`gateway/rooms.py:181-199`'s `my_groups` — not once per predicate, and not per row.

**Done when:**

1. **A grant is creatable by a caller — not only by hand-written SQL.** Two routes
   exist, on the shipped `/tasks` router (`routes/tasks/core.py:27-30`, which already
   carries `dependencies=[require_feature_router("tasks")]`):

   | Method + path | Body / param | Does |
   |---|---|---|
   | `POST /tasks/projects/{project_id}/grants` | `{"subject": "group:<slug>"}` | upserts one `gtd_project_grant` row, `granted_by` = the caller |
   | `DELETE /tasks/projects/{project_id}/grants/{subject}` | — | revokes it |

   **The permission required is `feature:tasks` plus project ownership, and nothing
   else.** No new permission slug is minted — do not add one to
   `acb_auth.permissions.FEATURES` or `CAPABILITIES` in this slice — and **no
   `admin:*` is required**: granting your own project to your own Center is not an
   admin act (contrast `routes/admin/groups.py`, where minting a `center.*` feature
   override for someone else legitimately needs `admin:access:manage`). Ownership is
   `gtd_projects.user_id = <caller>`, and the status codes mirror the shipped
   app-grant shape `get_app_or_404(db, slug, user, edit=True)`
   (`routes/apps/_common.py:459-475`): **404 when the caller cannot see the project,
   403 when they can see it but do not own it.**

   > ⚠️ The routes go in a new `routes/tasks/grants.py`, and that module **must be
   > added to the import list in `routes/tasks/__init__.py:7-18`**. The feature
   > modules register onto the one shared `router` as an *import side effect*
   > (`__init__.py:3-5`), so a module left out of that list mounts **nothing** —
   > while every test that calls the route function directly still passes. That is
   > the exact failure this criterion exists to prevent.

2. A member of group X, who does not own project P, can read P and its items when a
   `gtd_project_grant` row with `subject = 'group:X'` exists — **and the grant under
   test was created by invoking criterion 1's `POST` route function, not by an
   `INSERT` in a fixture.** A repository helper reachable only from a fixture does
   **not** satisfy this. Use the shipped hermetic convention rather than a live DB:
   call the async route function directly with a fake in-memory session,
   monkeypatching the DB seam on the SUT submodule —
   `tests/unit/test_admin_groups.py:7-21` states the convention verbatim and
   `tests/unit/test_app_grants.py` is where it started.
3. A member of **no** group containing X gets **`404`, not `403`**, on the same project
   and on every item under it. This matches the shipped convention — see the probe at
   `routes/memory.py:237-240` and its comment: *"404, not 403: whether a memory id
   exists elsewhere is itself something the caller should not be able to probe for."*
   A `403` here would leak the existence of another Center's project.
4. Revoking the grant **through criterion 1's `DELETE` route** restores the `404` on
   the next request, with no cache to invalidate.
5. A subject outside `email | group:<slug> | org` is rejected at write time by a
   validator that is **not a third route-local copy**. There is no shared home today,
   which is why "use the shared validator" was unbuildable as previously written: the
   only two validators are route-local, private, and disjoint —
   `routes/rooms.py::_valid_subject` (`:100-111`; `email | group:<slug> | org`) and
   `routes/apps/grants.py::is_valid_subject` (`:68-85`; `email | agent:<name> |
   agents:*`, and it **rejects** the literal `org` at `:77`). Importing either into
   the other's route package means importing a private symbol across packages.

   **This slice creates the home: `packages/acb_auth/acb_auth/permissions.py`,
   exported from `acb_auth/__init__.py`** — e.g. `valid_grant_subject(subject: str)
   -> bool`, which becomes the definition of record for the
   `email | group:<slug> | org` grammar. Why there, and not a new module: that file
   already owns the permission vocabulary and is **pure — no DB, no FastAPI, no I/O**
   (its own docstring, `permissions.py:1-8`, and `packages/AGENTS.md`); a subject
   grammar is vocabulary of exactly that kind; and all three consumers already import
   `acb_auth` (`routes/rooms.py:38`, `routes/apps/grants.py:26`,
   `routes/tasks/items.py:23`), so this adds **no new edge** to the import graph.
   The alternative homes were considered and rejected: a `gateway/`-local helper is
   not reachable from `packages/`, and a new `acb_auth` submodule would split one
   vocabulary across two files.

   > **Deliberately NOT in this slice:** repointing `_valid_subject` and
   > `is_valid_subject` at the new helper. `rooms.py:100-111` is published as an
   > anchor by this spec, `tenancy_and_visibility.md` §3.2, `work_plan.md` D12 and
   > `docs/multiplayer/memory-clearance.md`; editing it here would move four
   > documents' anchors for no gain to this slice. It is a named follow-on, not an
   > oversight — and per §3.2 the two grammars are not identical, so it is a
   > *composition*, not a deletion.
6. Every one of the 27 `user_id` predicates is either widened through the union path or
   explicitly justified in the PR as owner-only (e.g. a write path). "I widened the list
   endpoint" is not this criterion.
7. `uv run ruff check apps/services/gateway/gateway/routes/tasks/items.py \
   apps/services/gateway/gateway/routes/tasks/grants.py \
   packages/acb_auth/acb_auth/permissions.py` is clean.
   Do **not** claim `uv run ruff check .` clean — it reports ~1983 pre-existing errors
   on this tree and is not a signal.

**Verification** — *name the test files; never `uv run pytest tests/unit/` as a
directory, it hangs against this box's live DB*:

```
uv run pytest tests/unit/test_tasks_gtd.py tests/unit/test_tasks_archive_upstream.py \
              tests/unit/test_admin_groups.py tests/unit/test_org_access_control.py \
              tests/unit/test_tasks_project_grants.py -v -rs
uv run ruff check apps/services/gateway/gateway/routes/tasks/items.py \
                  apps/services/gateway/gateway/routes/tasks/grants.py \
                  packages/acb_auth/acb_auth/permissions.py
```

The first four files exist and are hermetic today — none carries a `skipif` guard
(verified 2026-08-03), so a new grant-path test added beside them **cannot skip green**,
unlike the room/authority files (`tenancy_and_visibility.md` §7's warning).
`test_tasks_project_grants.py` is **created by this slice** and is where done-when 1–5
live; it must likewise carry no `_db_ready()` guard. Re-list `tests/unit/` at dispatch
rather than trusting these names; there is no `test_tasks_items.py`.

#### C2 — Shared mailboxes · 🟢 **AGENT-SAFE (the doc action only)** · the build is 🔴 **blocked — no owner in fact**

*(Gate label, per contract item 7. **Corrected 2026-08-03:** this bullet used to read
"NOT DISPATCHABLE", which minted a **third** gate token into a corpus whose contract
allows exactly two — the same class of drift R2 and R3 exist to stop. It maps onto the
two without loss: the **doc action** below is ordinary AGENT-SAFE spec work — writing
the missing shared-mailbox section into `email_app_master_plan.md`, the spec D5 already
assigns — and the **build** is not gated on an owner *decision* but on that doc action,
so it is simply not dispatchable yet. Two clauses inside it stay owner's: **reassigning
ownership in `work_plan.md` §4** (an agent may not move work off the owner D5 named) and
any live mailbox reach. If the doc action lands and the build is still refused, that is
this bullet failing, not a missing third token.)*

**Struck from Phase C as an actionable bullet, 2026-08-03.** It read: *"`email_account_member`
by group (research §16.7); 'this mailbox belongs to the Sales team' ownership surfaced in
UI."* Two verified problems:

- **`email_account_member` is vapour.** Zero hits repo-wide across `*.sql` and `*.py`
  (measured 2026-08-03). It was cited as Phase-2 *content* by this spec and by
  `org_access_control.md:311` in a way that reads as though it shipped. It never
  existed. Nobody should cite it again as an existing table; if the work is built, the
  table is designed then, at the next free migration number resolved at build time.
- **The assigned owner does not mention it.** `work_plan.md` §4 assigns shared mailboxes
  to `email_app_master_plan.md`, "sequenced by WS-14" (D5). That spec contains **zero**
  occurrences of the phrase "shared mailbox" (measured 2026-08-03). Dispatching against
  it would send an implementer to a spec with nothing to implement.

**Where it really lives:** the storage shape is settled in
`tenancy_and_visibility.md` §5 — *a grant on the `email_accounts` **row**, not on
messages* — and `email_accounts.user_id` (`17_email_accounts.sql:16`) is the column it
widens. **The next action is not code: it is for `email_app_master_plan.md` to gain a
section for it, or for `work_plan.md` §4 to reassign the owner.** Until one of those
happens this bullet is not dispatchable and no agent should treat it as such.
WS-14 still *sequences* it (D5 is unchanged); WS-14 does not implement it.

#### C3 — Team-instanced agents · **AGENT-SAFE**, but read the traps first · ~1 PR

**Rewritten 2026-08-03 — the old bullet asked for agents that do not exist.** It sent
the implementer to the `agent-kinds.md` §6 roster for `sales` / `billing` / `delivery`
(`docs/multiplayer/agent-kinds.md:289-291`). **None of the seven aspirational agents named
in that roster exists.** `apps/agents/` holds exactly six, and they are different ones:
`agent-apis-config`, `agent-app-builder`, `agent-email-assistant`, `agent-orchestrator`,
`agent-task-manager`, `agent-whatsapp-assistant`. The roster is aspirational; it is not a
work list.

> ⚠️ **Trap — do not "align the agents to the roster".** The §6 roster assigns
> `task-manager`, `orchestrator` and `app-builder` **personal** instancing
> (`agent-kinds.md:288`, `:295`, `:296`). All three shipped `config.json` files say
> `"instancing": "shared"`. Flipping them to match the roster would **silently
> re-partition three live agents' memory and blob store**: `instance_key()` would start
> returning `u:<email>` instead of `''`, so `memory_scope()` moves from `agent:<slug>` to
> `agent:<slug>#u:<email>` and `blob_instance()` likewise — every existing memory and
> blob becomes unreachable from the running agent, with no error. That is a data
> migration wearing a config change's clothes, and `agent-kinds.md` §6 itself prescribes
> the quarantine-then-review procedure for it (shipped as migration 137). **It is not in
> this slice, and it is not AGENT-SAFE.**

> ⚠️ **Trap — the writer already exists.** `tenancy_and_visibility.md` §5 used to say
> `t:<team>` "exists but nothing writes it". That was false and is corrected there:
> `AgentManifest.instance_key()` returns `f"t:{self.sharing.team}"` for
> `instancing == "team"` (`acb_skills/manifest.py:242-246`), live on four non-test call
> sites. **What is missing is a config that asks for it, not code that produces it.**

**What this slice actually is, in order:**

1. **Decide which of the six existing agents (if any) should be team-instanced, and
   record it here.** The honest current answer is *possibly none*: two are already
   `personal` (email, whatsapp) and correctly so, and the four `shared` ones
   (apis-config, app-builder, orchestrator, task-manager) have no team boundary to draw
   yet because no team-owned agent has been built. A team-instanced agent becomes real
   when a Center gets its own agent — which is a new agent, not a re-flag of an existing
   one. **This is the first thing the ticket writes down**; it is a design note, and it
   is AGENT-SAFE to produce, but changing any existing agent's `instancing` is not.
2. **Reconcile the three contradictions** between `agent-kinds.md` §6 and the shipped
   `config.json` files, in the roster table itself. Either the roster is annotated as
   aspirational (preferred — it is an RFC), or a migration plan is written. Do not leave
   a table that a future reader will implement.
3. **The `dynamic_agents` sharing columns (D3).** Re-verified 2026-08-03:
   `15_dynamic_agents.sql:7-20` carries no owner, visibility or sharing column, and a
   repo-wide grep finds none — so this migration is genuinely WS-14's, at **the next
   free number resolved at build time** (R1). Per D3: columns now, derived from
   `agent_defs` manifests when agent-architecture Phase A lands.

   **The column shape is already written down — do not redesign it.** D3 points at
   `docs/multiplayer/agent-kinds.md` **§3**, whose `ALTER TABLE dynamic_agents` block
   (`agent-kinds.md:143-155`) is the shape of record: `instancing`
   (`personal|team|shared`, default `personal`), `visibility`
   (`private|team|organization`, default `organization`), `team_ref`, `memory_mode`
   (`instance|none`), `shareable`. Reproduced here so C3 is readable without opening
   an RFC under `docs/`. ⚠️ That block's own comment retains a stale `"119"`; take the
   **shape**, resolve the **number** at build time (R1).
4. **Answer, in the PR, whether that migration is additive to the live `config.json`
   path or replaces it.** `work_plan.md:149-153` (the D3 amendment) already says
   instancing ships via `config.json` today; the `agent_architecture.md` body does not
   say so, and this spec did not either. The default reading is **additive** —
   `dynamic_agents` rows describe GitHub-registered agents, `config.json` describes
   first-party ones, and `AgentManifest.from_config()` keeps reading the latter — but the
   ticket must state it rather than leave two stores with no precedence rule.

**Done when:** the roster contradictions are resolved in `agent-kinds.md`; the sharing
columns exist at a build-time-resolved migration number, in the `agent-kinds.md` §3
shape; the additive-vs-replacing question is answered in prose in this spec; and **no
existing agent's `instancing` value changed** (grep the six `config.json` files before
and after — four `shared`, two `personal`, unchanged).

> **For the reviewer, so this does not read as a no-op.** C3's only code artifact is a
> migration whose columns **nothing reads, on purpose**: it is **pre-provisioning,
> columns intentionally unread**, sanctioned by D3 ("columns now, manifest later"). No
> read path may be wired to them in this slice — instancing is served today by
> `AgentManifest.from_config()` / `instance_key()` off `config.json`, and adding a
> second live reader before WS-8 Phase A decides precedence is how two stores of truth
> start. A PR that ships these columns *and* a consumer is out of scope, not ahead of
> schedule. The rest of C3's value is documentary (the roster reconciliation and the
> additive-vs-replacing answer), and that is the honest description of the slice.

**Verification:**

```
uv run pytest tests/unit/test_agent_paths.py tests/unit/test_org_access_control.py -v -rs
rg -n '"instancing"' apps/agents/*/config.json     # expect 4 shared + 2 personal
```

#### C4 — Per-Center approvals routing · 🔴 **OWNER-GATE (an OWNER-DECISION)** — do not dispatch

**Re-stated honestly 2026-08-03.** It read: *"approvals inbox filterable by originating
group (org_access open Q2)."* That describes a UI filter. It is not one, for two
verified reasons.

**First, the question is open, verbatim.** `org_access_control.md:405` Q2:

> *"**Approval routing.** When a member lacking `admin:*` triggers an action needing
> approval, who is asked? Phase 1 routes to anyone with `feature:approvals`; per-module
> approvers is a Phase 2 question."*

Who is asked is a policy call about who can approve spending and outward writes on
another Center's behalf. No agent may take it. **OWNER-GATE.**

**Second, there is no column you can route on.** `infra/postgres/66_pending_actions.sql:13-38`
defines the whole row: `id`, `actor`, `action`, `target`, `payload`, `authority`,
`destructive`, `disposition`, `status`, `result`, `reviewed_by`, `reviewed_at`,
`created_at`. There is **no requesting-member column, no group column, and no Center
column.**

> **Correction, 2026-08-03 — this paragraph previously overstated its own evidence, and
> the overstatement was the exact sin WS-14 exists to fix.** It read: *"`actor` is the
> proposing agent … not the human behind it. A group cannot be derived from any existing
> column."* **Both halves of that absolute are false.** `pending_actions.actor` is
> written by exactly six `propose()` call sites, and **two of them put the requesting
> human's email in the string**: `routes/apps/tools.py:392-393` and
> `routes/apps/actions.py:344-345` both pass `actor=f"app:{slug}:{email}"`, where
> `email = _uid(user)` is the caller's identity (`tools.py:375`). For an app-tool
> proposal a group **is** derivable — parse the email out of the string and join
> `org_group_member`. The column comment's `"agent:sales"` is an *example*, and as it
> happens no shipped call site produces that shape at all.
>
> **The six shapes, measured (`rg -n 'proposal = propose\('` + the `actor=` argument at
> each site):**
>
> | Shape | Written at |
> |---|---|
> | `app:<slug>:<email>` | `routes/apps/tools.py:393` · `routes/apps/actions.py:345` |
> | `app:<slug>` | `routes/apps/publish.py:211` |
> | `workflow:<name-or-id>` | `routes/workflows/service.py:668`; threaded into `routes/workflows/tools.py:190` from `service.py:337` |
> | `tasks:<provider>` | `routes/tasks/providers.py:159` via `_broker_actor()` (`:127-130`) |
> | `tasks:clickup:ws:<workspace_id>` | the ClickUp override of the same method, `providers.py:314-317` |
>
> One shape on the list handed to this correction — `system:action_broker` — is **not**
> a `pending_actions.actor` at all: it is the *audit-event* actor the broker stamps on
> its own bookkeeping (`action_broker/broker.py:151`, `:161`, `:172`, `:225`, `:233`).
> Recorded so the next reader does not go looking for a seventh proposer.
>
> **The verdict survives; only the reasoning is repaired.** `actor` is a free-text
> identity string with **five** shapes and no grammar, and only **two of six** call
> sites carry a human at all. You cannot route approvals on a field that four of six
> proposers populate with no person in it — a Center-scoped inbox would silently show
> nothing for every workflow-, publish-, and provider-originated proposal. So C4 is
> still "answer Q2, then add a column", and the column must be **written by every
> proposer**, not parsed out of an ad-hoc string by the reader. That is a stronger
> argument than the false absolute it replaces, not a weaker one.

**So the ticket is "answer Q2, then add a column", not "add a filter".** In that order:
the column's shape (a `requested_by` member email? a `center` slug? both?) follows from
the answer, and adding one first would bake in a routing model nobody chose. When Q2 is
answered, the migration goes at the next free number resolved at build time (R1), and
the filter is the small part.

### Phase D — Dashboards and the Company Center
- **Center dashboards**: per-department rollup pages replacing the "planned"
  cards, fed by app queries + digest workflows (`workflows_app.md` G1 names
  report digests as a launch goal).
- **Personal dashboard**: the Personal Center rollup (footnote ¹).
- **Fix the two flagged defects under the founder view**:
  orchestrator runs without org-scope memory (`agent_architecture.md` §11.1.2),
  and per-agent observability totals are misleading once instancing lands
  (`agent-kinds.md` §9.4 — per-instance cost attribution).
- **Weekly executive digest**: a scheduled workflow per Center → one Company
  Center brief.
- **Owner scope colour (2026-08-09/10, D21 + D22 — carry into this phase's
  acceptance when it dispatches):** dashboards are **configurable per
  department** (widget/data selection, not a fixed layout); leadership can build
  **multiple** company-wide dashboards, not one; and the standalone Dashboards
  app follows the §5 dual-access rule (grouped by Center, union of the caller's
  slices).

### Phase E — AI budgets and governance
- **Per-member AI budgets**: monthly token/cost caps enforced at the gateway's
  LLM choke points (all traffic already flows through them with cost recorded
  per run). Soft-warn at 80%, downgrade-to-fast-tier or block at 100%
  (owner-configurable), exec exempt. Surfaced in Models settings + per-member
  admin view + observability. Depends on Phase D's attribution fix.
- Attribution and subject ordering per `work_plan.md` D1/D2: one attribution
  record — (run, member, agent, instance) stamped at the choke points — with
  per-member caps first; the multiplayer plan's per-room `token_budget` +
  degrade-to-read-only builds later on the same records.
- Later, per-group budgets roll up the same data by Center.

### Deliberately not in this plan
- Floor control / steer / observer lane — multiplayer workstream
  (`docs/multiplayer/README.md` §8), tracked there.
- Entity-graph RLS and consent records — org_access Phases 4–5.
- Multi-tenant / SaaS — `saas_multitenancy.md` (WS-29; D15) — research §17 is
  background only; untouched by Centers.

## 4. Open questions

1. ~~**"Pomad Centre."**~~ **Resolved 2026-08-01.** Owner confirmed the name
   was a stray (should have read Metorite), not a planned venture. All
   twelve sites across eight files were rewritten as "a second tenant
   deployment" — preserving each sentence's meaning, including the T2
   security gate in `agent_platform_hardening_2026-07.md` §64. Decision
   record: `work_plan.md` D9. *(2026-08-09: the "second tenant deployment"
   phrasing those rewrites installed embodied D11 and has itself been
   re-swept to organization/placement language after D15; this inventory
   stays as history.)*
2. ~~**R&D / Engineering Center?**~~ **ANSWERED 2026-08-10 (owner, D22): yes —
   R&D joins the roster**, launching **slices-only** (the cross-cutting apps
   scoped to its team; no unique apps until a real workflow demands one). The
   registration cost stands unchanged: adding it is §2's *Registering a Center*
   checklist end to end — `lib/centers.ts`, a `feature_catalog` migration row,
   `acb_auth.permissions.FEATURES`, `CENTER_GROUP_SLUGS`, and the test anchor
   `EXPECTED_CENTER_SLUGS`. Doing only the first two is how Centers came to be
   unreachable by everyone once already;
   `tests/unit/test_org_access_control.py::test_centers_registry_matches_the_feature_vocabulary`
   is what now stops that recipe from passing CI.
3. ~~**Support: Operations sub-app or own Center?**~~ **ANSWERED 2026-08-10
   (owner, D22): its own Center**, paired with the future Customer Support &
   Success module (`future_modules_roadmap.md` §3). Service & AMC's Operations
   sketch (§2) stays where it is until the Support module is specced; the
   Center registers by the same §2 checklist. Operations itself also launches
   slices-only (D22) — its unique-app sketch (production tracker, inventory/BOM,
   dispatch) remains future scope.
4. **Guest access to Centers** — org_access open Q4; a guest with
   `center.sales` only is a plausible contractor shape and needs a decision
   before external sharing.

## 5. The Center roster of record (owner architecture statement, 2026-08-10 — D22)

The owner stated the full system shape in session; the four ambiguities it
surfaced were answered the same day (work_plan.md §3 D22). This section is the
roster of record; §2's shipped six-center list is the *current build state*, and
the delta between them is registration work by §2's checklist.

**Three kinds of surface, one platform** (nothing here changes "Centers are
projections, never separate deployments"):

1. **Personal Center — per-user, NOT a department.** Each member's private
   workspace (D12's `private` tier). *(Reconciled with D23 pricing,
   2026-08-10:)* the **surface** exists for every member automatically — no
   `org_group`, no membership grant — and always shows its **Core apps** (my
   Tasks, my Calendar, basic AI chat, personal dashboard); its **comms apps —
   Email, WhatsApp, Meetings — light up only with the optional Personal Center
   package (₹600/user, D23.1)**, rendering as locked/upsell otherwise per the
   §2.4 degradation contract. ⚠️ Engineering consequence: §2's five-place
   registration checklist assumes a group-backed Center; Personal needs a
   registry entry whose scope is the caller, not a group — design that variant
   when Personal registers, do not force a fake group.
2. **Department Centers — seven:** Sales · Marketing · Finance · **R&D (new,
   D22)** · People · Operations · **Support (new, D22)**. Each an `org_group` +
   `center.*` slug per §1, **sold as a Center package (D23, 2026-08-10: ₹600
   app-bearing / ₹300 slices-only — `saas_multitenancy.md` §2.4b)**. Every
   department Center carries the cross-cutting apps sliced to its team, in two
   commercial classes *(D23 correction)*: the **base slices — Projects,
   Knowledge Base, Dashboards — ride inside every Center package**;
   **Workflows (₹300) and Builder (App + Agent, ₹500) are separately-purchased
   org-wide add-ons** that light up in all the Centers of a user who holds
   them — a package alone does NOT include them; basic Agent Chat is Core. (The
   D21 slicing doctrine — D12 tiers + visibility-declared-at-creation — governs
   all of them.) Unique apps per Center: Sales = the CRM
   surfaces **including products, price books, brochures/product info
   and the proposal generator — all inside the Sales Center package (₹600; CRM
   is the internal atom, D23), never separate
   SKUs (D22)**; Marketing = the future Marketing module (social/ads/website —
   `future_modules_roadmap.md` §2); Finance = the Finance module; People = the
   People directory surfaces (Core) + HR expansions; Support = the future
   Support module; **R&D and Operations launch slices-only** (unique apps
   deferred until a real workflow demands them — Operations' §2 sketch stays
   future scope).
**Dual access paths for cross-cutting apps (owner, 2026-08-10 — D22 amendment).**
Projects, Workflows, App Builder, Agent Builder, Agent Chat, Dashboards and
Knowledge Base are reachable **two ways, same data, same grants** *(holding the
app is the prerequisite either way — base slices come with any Center package,
Workflows/Builder with the org-wide add-on, basic Agent Chat with Core; D23)*:

- **Via a Center** — the app pre-scoped to that Center's slice (what §1 already
  defines: a Center item is app + scope).
- **As a standalone app** — the app's own top-level surface, whose primary
  information architecture is **grouped by Center**: the user sees every Center
  they hold access to (membership or explicit `group:` grant, per D12), and
  under each, that Center's data — e.g. opening Projects shows the Centers as
  the first-level grouping, each containing its projects and tasks. Leadership
  with org-tier access sees all Centers in the same layout.

The rule that keeps this honest: **both paths resolve visibility through the
same D12 grants** — the standalone app view is a union of the caller's Center
slices, never a separate permission model, and a Center the caller cannot access
never renders as a group header. First consumers when their tickets dispatch:
WS-27's portfolio/grouping views (Projects) and WS-15's dashboards.

3. **Company Center — kept (D22), the leadership surface.** WS-15's mandate:
   company-wide dashboards (multiple, configurable — the D21 colour), org-level
   rollups. Cross-cutting apps additionally offer leaders an all-slices filter
   in-app, built as explicit org-tier grants per D12/D14 — never a bypass.

**The admin/IT plane is not a Center:** Appearance, Membership & roles, Live
activity (observability), Integrations, Approvals, Agent Registry, AI
credits/access (WS-30 console + operator views). These are Core-module admin
surfaces gated by capability, present regardless of Center membership.

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-13 — **Centers B — groups become real** (groups admin UI, seed six groups, People directory read view)

**State cell (as of the move):** 🟡

**Narrative (verbatim):** Groups admin UI + six-group seed **built 2026-08-01, pending owner review** (`routes/admin/groups.py`, `/settings/groups`, seed migration; see `department_centers.md` Phase B update). People directory read view still open. The unlock for everything below. Single owner: Centers B (groups spec §6 step 5 and org_access Phase 2 are mirrors). ✅ **FIXED 2026-08-03 (`ws-13-centers-feature-vocabulary`): the feature-vocabulary half of this row is closed.** `acb_auth.permissions.FEATURES` now carries the six `center.*` slugs in migration-140 sort order, two invariant tests in `tests/unit/test_org_access_control.py` now fail loudly if one goes missing — `::test_every_center_has_a_feature_slug` (anchored on a literal `EXPECTED_CENTER_SLUGS`, because the first version *derived* the expectation from `CENTER_GROUP_SLUGS` and therefore went vacuous when that tuple was emptied) and `::test_centers_registry_matches_the_feature_vocabulary` (**parses** `lib/centers.ts` and pins it both ways to `FEATURES`, so the documented "add a Center" recipe can no longer reproduce this bug with a green suite). `department_centers.md` §2 now carries the five-place registration checklist. And the admin role editor groups its chips by `feature_catalog.category` with a real "Centers" heading (`settings/roles/page.tsx`, `Feature.category` union widened in `members/types.ts`). No migration was needed — 140 already widened the CHECK. **Separate, still open:** `workbench/control_plane/src/app/page.tsx:11-12` renders `NAV_SECTIONS` with **no** access filter, so the home grid still advertises every pane (Centers included) to every viewer while the sidebar correctly hides them — recorded in `workbench/AGENTS.md`. The finding as originally written, for the record: **Centers were unreachable by ANYONE, including the owner.** `/auth/me` returns `"features": list(access.allowed_features())` (`routes/admin/me.py:84`), and `allowed_features()` iterates the **hardcoded Python tuple** `acb_auth.permissions.FEATURES` (`:64-81` as the tuple then stood; `:73-101` after the fix) — sixteen slugs, **no `center.*` entry**. The frontend gates on exactly those slugs: `lib/access.ts:66` maps `/centers/<slug>` → `c.feature` (= `center.sales`…), `canUseFeature` is `access.features.includes(slug)` (`:118`), and `visibleSections` drops any pane whose feature is absent — **and drops the whole section when it empties** (`lib/nav.ts:229-233`). Net effect: the Centers section renders in neither nav, and typing `/centers/sales` hits `AccessGate`'s "You don't have access to this". Migration `140_center_features.sql` **does** seed six `feature_catalog` rows, but `allowed_features()` never reads that table — so migration 140's own comment ("owners and admins see all Centers via their `feature:*` baseline") is **false as written**: an owner holding `*` still gets an empty set, because the wildcard is only ever evaluated against the sixteen literals. The fix taken was the vocabulary one (`FEATURES` gains the Center slugs) plus the invariant test; making `allowed_features()` read `feature_catalog` was rejected — `permissions.py` is pure and does no I/O by design.

**Corrections applied 2026-08-09:**
- People-directory item closed by WS-28b (2026-08-06); the nav-filter / "catalog-read rejected" claims were inverted by merged #389 (`747b65af`) — the catalog decides now.

### WS-14 — **Centers C — scoping deepens** (tasks team slice, shared mailboxes, team-instanced agents, per-Center approvals)

**State cell (as of the move):** 🟢 **unblocked 2026-08-03 (D12)**

**Narrative (verbatim):** **The blocker is answered.** This row read "blocked on what makes a project a team's project" for weeks; **D12** answers it: **a project belongs to a team when an explicit grant row carries a `group:<slug>` subject** — *not* derived from assignees, *not* an owning column. Both alternatives and why they were rejected are recorded in `specs/tenancy_and_visibility.md` §4 (`DECISION (owner-answered 2026-08-03)`); §5's gap table is the app-by-app map, and §3.2 is binding on the mechanism — **extend the existing `email | group:<slug> | org` subject vocabulary, do not invent a second one.** ⚠️ **The primitive is narrower than previously claimed:** only **rooms** honour `group:` today (`routes/rooms.py::_valid_subject` `:100-111`, expanded at `gateway/rooms.py:181-199` — **corrected 2026-08-03 from the stale `:163-179`**, which is the `chat_session` SELECT, not the group join; the `SELECT g.slug` is at `:192`). `app_grants` does **not** — `routes/apps/grants.py::is_valid_subject` (`:68-85`) is `email | agent:<name> | agents:*` and explicitly **rejects `org`** (`:77`); the "identical to grants.is_valid_subject" docstring at `rooms.py:103` is false and should be corrected by whichever ticket touches it first. **What it can now build, in order:** (1) the tasks team slice — a project grant table + a read path unioning "mine" with "granted to a group I'm in" (blast radius: 27 `user_id` predicates in `routes/tasks/items.py`); (2) the `dynamic_agents` sharing columns per D3 — re-verified 2026-08-03, `15_dynamic_agents.sql:7-20` has **no** owner/visibility/sharing column and a repo-wide grep finds none, so this migration is genuinely WS-14's, at the **next free number resolved at build time** (R1); (3) `group:` on the Custom-Apps grant subject, the cheapest conversion since `apps.visibility` already carries the three tiers. Shared mailboxes stay `email_app_master_plan.md`'s implementation, sequenced here (D5). **Not blocked on WS-8 Phase A** (D3 amendment) and **not** waiting on WS-13's UI — but note WS-13's new finding: the Center *surfaces* are currently unreachable, so scoping work will need that one-line feature-vocabulary fix to be demonstrable. ⚠️ **Re-audited 2026-08-03 → the row was NOT dispatchable as written; `department_centers.md` §3 Phase C was rewritten and this row now points at four lettered bullets, only two of which are work.** **C1 tasks team slice — 🟢 AGENT-SAFE**, and it is the whole of the near-term value: grant table decided (`tenancy_and_visibility.md` §4.1 = **D13**, `gtd_project_grant`, agent-proposed and overrulable, **no `role` column**), union read path, migration at the next free number resolved at build time, and a **404-not-403** assertion for the non-member (the shipped convention — `routes/memory.py:237-240`). ✅ **Repaired 2026-08-03** after review found C1's acceptance could go green with **no way to create a grant**: done-when 1 now names a caller-reachable creation path (`POST`/`DELETE /tasks/projects/{project_id}/grants` on the shipped `/tasks` router, `feature:tasks` + project ownership, 404-not-403 per `routes/apps/_common.py:459-475`, module wired into `routes/tasks/__init__.py`), done-when 2 requires the grant under test to be created **through that route** rather than by a fixture `INSERT`, and done-when 5 names the shared validator's home (`packages/acb_auth/acb_auth/permissions.py`) — it previously named no module and no shared home existed. **C2 shared mailboxes — 🟢 AGENT-SAFE for the doc action, build blocked, no owner in fact** (see §4; the bullet's old "NOT DISPATCHABLE" was a third gate token and was mapped onto the contract's two). **C3 team-instanced agents — 🟢 AGENT-SAFE but narrow:** the seven agents the old bullet named do not exist, and `t:<team>`'s *writer already ships* (`acb_skills/manifest.py:242-246`), so the slice is the `dynamic_agents` columns (shape per `agent-kinds.md` §3, `:143-155`; **pre-provisioning — the columns are intentionally unread, per D3, and wiring a consumer is out of scope**) plus reconciling `agent-kinds.md` §6 against three shipped `config.json` files — **changing any existing agent's `instancing` is a silent memory/blob re-partition and is out of scope.** **C4 per-Center approvals — 🔴 OWNER-DECISION** (org_access Q2 open; `pending_actions` has no member/group/Center column).

**Corrections applied 2026-08-09:**
- C1 must be re-audited against WS-27e's D-PM-6 one-store revision before dispatch; "only rooms honour `group:`" is stale — `pm_project_grants` shipped on the same vocabulary 2026-08-06.

### WS-14a — **Tenancy TV-1 — the three `org_group` slug-only joins** *(minted 2026-08-03)*

**State cell (as of the move):** 🟢 **AGENT-SAFE · 1 small PR**

**Narrative (verbatim):** Owning spec: **`specs/tenancy_and_visibility.md` §2**, which passes all seven contract points and had **no board row** until now — §4 assigned it to a spec, and the dispatch loop selects from §2, so the corpus's most dispatch-ready ticket was undispatchable. `org_group` is joined on **slug alone** at three sites; slug is unique only *within* an org (`UNIQUE (organization_id, slug)`, `138_…sql:49`), and **two of the three sit inside the session-authority intersection**, where a too-wide group *widens* access. Nothing leaks today (D11: one org), but these are wrong within one org too, which is why they survive D11. **Anchors, re-verified 2026-08-03 — the previously-published ones were wrong at `520476ab` and are corrected in the spec:** (a) `apps/services/gateway/gateway/rooms.py:181-199`, the `SELECT g.slug` at `:192` *(was `:170-179` = `if row is None` + the participant fetch)*; (b) `:368-403`, `SESSION_VISIBLE_SQL` opening at `:368` with the slug join at `:377` *(was `:332-340` = the tail of `resolve_room_access`'s return)*; (c) `packages/acb_auth/acb_auth/access.py:330-336`, `_GROUP_MEMBER_SQL` — **correct, unchanged**. ⚠️ **The spec's own "verified red" requirement was unsatisfiable and was repaired in the same pass:** §7 named `tests/unit/test_session_authority.py` and `tests/unit/test_rooms.py` as the extension point, and both open with `pytest.mark.skipif(not _db_ready(), …)` (`:33-51` and `:33-52`), so a fixture added there **skips green** with no Postgres. §2 done-when 2 now attaches red-first to a genuinely hermetic string assertion over the three queries (which requires lifting anchor a's inline SQL to a module constant — that extraction is part of the ticket), and done-when 3 requires quoting a `-v`/`-rs` run showing the DB-backed fixture `passed`, never `skipped`. Numbered **14a** rather than a fresh WS-n because it is the `org_group`-join half of the same subject-vocabulary surface WS-14 generalises; the two are independent PRs and either may land first.

**Corrections applied 2026-08-09:**
- Absorbed by WS-29 as MT-1i (2026-08-08); under D15 the three joins leak across tenants — the D11-era "wrong within one org, leaking in none" severity framing is superseded; the open two-org DB fixture criterion travels with MT-1i.

### WS-15 — **Centers D — dashboards + Company Center** (Center dashboards, personal dashboard, weekly digest workflows, orchestrator org-memory fix per D4)

**State cell (as of the move):** 🟡 WS-13

**Narrative (verbatim):** Digest workflows double as `workflows_app.md` G1 launch metric — one artifact, both scorecards.

**Corrections applied 2026-08-09:**
- Unchanged.

### WS-16 — **Centers E — AI budgets** (per-member caps at the LLM choke points; per-room degrade later)

**State cell (as of the move):** 🟡 WS-6

**Narrative (verbatim):** Subjects per D2.

**Corrections applied 2026-08-09:**
- Unchanged.
