# Plane PM-platform research — what to adopt, adapt, and refuse (2026-08)

> ⚠️ **RESEARCH — REFERENCE-ONLY (2026-08-10 consolidation, D26).** No work dispatches from
> this document; its P-queue was minted as WS-27u–z in `project_management_app.md` §9.1. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Product:** Metorite · **Concern:** second research appendix for the native
> project-management app (WS-27), beside `paca_pm_research_2026-08.md` · **Created:**
> 2026-08-09 · **Status:** 🟢 research complete — **reference-only, owns no work and no
> status**; adaptation verdicts are annealed into `specs/project_management_app.md` §11.19 and
> **minted as tickets WS-27u–z in its §9.1**, which is the owning spec · **Owner:** vjvarada
>
> **Research provenance (2026-08-09):**
> - `makeplane/plane` @ `31853ab` (v1.4.1), shallow clone read at `/workspace/makeplane/plane`
>   (ephemeral — re-clone with `GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1
>   https://github.com/makeplane/plane`). Facts verified against the tree, not the README;
>   every claim below that reached a verdict was spot-checked at its cited `file:line` by a
>   second reader.
> - ⚠️ **LICENSE WALL — Plane is AGPL-3.0-only** (`LICENSE.txt`, SPDX headers per file).
>   This is categorically different from Paca's Apache-2.0. **Nothing may be copied,
>   translated, or paraphrased-at-the-code-level from this repository — patterns, shapes,
>   and interaction designs only**, re-derived in our own idiom. A single lifted function
>   would put the gateway under AGPL's network-copyleft. Everything in this document is
>   deliberately written as behavioral description for that reason. (One nuance: Plane's
>   *editor* builds on TipTap, which is itself MIT — the underlying library is usable;
>   Plane's extensions of it are not.)
> - Four parallel research passes (data model · API behaviors · web UI/UX · whole-product
>   surfaces), each verified against our tree before synthesis. Where a finding repeats
>   across passes it appears once here, at its strongest.

---

## 1. What Plane is, and why it maps onto us

Plane is a production open-source Jira/Linear alternative: Django/DRF + Celery over
Postgres/Redis, a member app (⚠️ **corrected 2026-08-10**: at the commit we read it is **React Router 7**, not Next.js, behind a hand-written `next/*` compatibility shim — the migration technique is worth banking, nothing more), and — this is its most interesting architectural
property — **four user-facing surfaces over one API**: `apps/web` (members), `apps/space`
(anonymous public boards, with its own separate view tree), `apps/admin` (instance
god-mode), `apps/live` (a Node Hocuspocus/Yjs server for collaborative page editing).

Why it maps: it is the strongest available reference for **project management at product
maturity** — the features that appear only after years of real users (intake queues,
auto-archive policy, notification digests, webhook delivery hardening, per-user view
preferences, five layout types). Paca told us how agents join the table; Plane tells us
what the table looks like when a thousand teams have eaten at it.

Why it does *not* map wholesale: Plane is workspace-flat (no container tree), orders
issues by a single float, has no relation-cycle guard, re-states its guest filter in every
endpoint, and pays a pervasive soft-delete tax. On each of those our existing design is
ahead, and §2 records the evidence so nobody trades down.

## 2. Where Plane validates what we already built — keep, don't churn

These are counterexamples and convergences, recorded so a future reader doesn't re-open
settled questions:

| Ours | Plane's version | Verdict |
|---|---|---|
| **Per-view fractional ordering** (`pm_view_task_positions`, D-PM-5) | One `sort_order` float per issue, scoped per state (`issue.py:158,206-210`) — a task cannot sit in different orders on two boards | **KEEP OURS.** Plane is the documented counterexample; it cannot express our Center-slice vs People-board requirement. |
| **Tenant key filled + cross-checked by DB trigger** (migration 161) | Same denormalized `workspace_id` on every row, but stamped in ORM `save()` only (`project.py:180-189`) — raw SQL bypasses it, nothing refuses parent/child disagreement | **KEEP OURS.** Plane independently validates D-MT-3's carry-the-key shape; our fill-or-refuse trigger is the stronger mechanism. |
| **Atomic counter** — `INSERT … ON CONFLICT DO UPDATE … RETURNING` on `pm_task_counters` | `pg_advisory_xact_lock` + `MAX(sequence)` + a permanent `IssueSequence` ledger table (`issue.py:184-214`) | **KEEP OURS.** Same never-reuse guarantee, one statement, no lock choreography, no ledger. Theirs is an ORM workaround. |
| **Relation cycle guards** (`assert_no_block_cycle`, `assert_no_task_cycle`) | **None.** Their relation endpoint accepts any graph (`app/views/issue/relation.py:209-246`) | **KEEP OURS.** We are ahead of prior art here, not behind it. |
| **One visibility predicate** (`task_visibility_clause`, the single most dangerous line rule) | Guest filtering re-implemented per endpoint (`base.py:909-920`, `search/issue.py:141-144`, …) — every new endpoint must remember | **KEEP OURS.** Their repetition is the strongest available evidence for the single-predicate rule. |
| **404-never-403 (R5)** | Generic 403s; workspace-admin bypasses project checks (`permissions/base.py:64-84`) | **KEEP OURS.** Both conflict with our doctrine. |
| **Bulk: validate-all-then-apply, per-task outcomes** | Per-issue loop that queues activity events *before* a mid-loop abort — the log can claim work that never committed (`archive.py:305-341`) | **KEEP OURS.** Borrow only their machine-readable error codes in per-task outcomes. |
| **Page-batched aggregate attachers** (`filters.py` two-query pattern) | Correlated subqueries per row, gzip to compensate | **KEEP OURS**, and adopt the *requirement* framing: every new list badge must be page-batched, never per-row. |
| **422 on unknown filter/sort values** | Silent fallback to default sort; invalid uuids silently dropped from filters | **KEEP OURS.** Their allowlists were added *after* two order-by-injection CVEs (`order_queryset.py:15-16` cites GHSA-2r95-c453-vxmr) — ours were allowlists from day one. |
| **Statuses-as-data with semantic `category`; priority as a fixed enum** | Identical split: states are rows with a `group`, priority is a hard-coded 5-value enum (`issue.py:141-146`) | **CONVERGED.** Industry position confirmed from a second independent source. |
| **`completed_at` stamped in exactly one writer at the category boundary** | Same, in model `save()` (`issue.py:240-255`) — but note Plane does **not** stamp on `cancelled`; we do | **CONVERGED**, with the delta recorded: our analytics must distinguish done/cancelled by category, never by the timestamp alone. |
| **Agent-as-member identity** | Integrations act through a bot *user* with real membership + API token (`integration/base.py`) | **CONVERGED** with Paca §5 and our D-PM-4. Third independent source. |

## 3. The backend gaps worth taking, ranked

### 3.1 Intake / triage — the missing front door *(top pick)*

The strongest transferable design in the repository, and it lands exactly on our §6.5
email-to-task plan and the agent-created-task question.

Shape (`intake.py:50-84`, `state.py:14-21`, `issue.py:92-101`): a submitted item **is a
real task from birth**, wrapped by a thin intake row carrying
`status ∈ {pending, rejected, snoozed, accepted, duplicate}`, `snoozed_till`,
`duplicate_to` (FK to the canonical task), `source`/`source_email`. The load-bearing
trick is a synthetic **triage** status category whose members are excluded from every
default query — un-triaged capture never pollutes a board, and *accepting is a status
flip, never a copy*, so provenance survives. Snoozed items drop out of the queue until
`snoozed_till`.

For us: a `pm_intake` join table (not a column — a task can only be in intake once, and
the wrapper carries intake-only fields), a `triage` value in the status-category
vocabulary, one added predicate in the default list exclusion, and a triage rail in the
UI with four actions (accept / decline / mark-duplicate-of / snooze). Routing decisions
(auto-accept from trusted senders, agent screening) belong to `/workflows` per ADR-028/D6
— the *states* live in PM, the *automation* lives in the engine. `duplicate_to` is the
disposition our personal-inbox vocabulary lacks today.

### 3.2 Watchers + mention discipline — the collaboration primitive we skipped

Three composable behaviors (`notification_task.py`, `issue.py:574-594`):

1. **A subscribers table** (task ↔ member): the notification audience becomes
   *subscribers*, not just assignees/mentioned. Anyone can watch a task they can see.
2. **Auto-subscribe on touch**: acting on a task (comment, edit, assign) subscribes the
   actor — the people who touched a task keep hearing about it without opting in.
3. **Mention diffing**: on comment/description *edit*, mentions are set-differenced
   against the previous content — **only new mentions notify**. A freshly-mentioned user
   is excluded from the same event's subscriber fan-out so they get exactly one
   "mentioned" notification, not mention + activity. Description edits never notify
   subscribers at all.

Our current audience is assignees + parsed `@address` targets, and an edited comment
re-notifies everyone. This is the cheapest genuinely-missing multiplayer piece:
`pm_task_watchers(task_id, watcher)` + the diff rule in the comment PATCH path. Our
visibility gate stays the stronger one (we check the recipient's grant closure via
`resolve_visibility_for`; Plane checks project membership only).

### 3.3 Auto-archive / auto-close policy — two columns and a sweeper

`Project.archive_in` / `close_in` (months, 0=off — `project.py:110-111`) + a nightly job
(`issue_automation_task.py`) that archives long-untouched closed tasks and closes stale
open ones to the project's default closing status. Two details worth keeping exactly:
automation-driven activity rows are flagged (`automation: true`) so timelines don't read
as human edits, and tasks inside an active cycle are exempt.

For us: two nullable INTs on root `pm_projects`, the sweeper as a **`/workflows`
scheduled workflow** (ADR-028/D6 — a PM-app cron would be the second engine), and one
guard adopted immediately regardless: **manual archive refuses unless the task's status
category is done/cancelled** (`archive.py:257-263`) — an archived open task silently
exits every default list, which is a trap, not a feature. Directly serves post-ClickUp-
import hygiene: years of dead imported tasks age out without anyone gardening.

### 3.4 Activity rows carry id *and* label for FK-valued fields

`IssueActivity.old_value/new_value` hold display strings while `old_identifier/
new_identifier` hold the UUIDs (`issue.py:415-438`). History survives status renames;
revert is exact. For us this is a **meta-shape rule, not a migration**: `field_change`
entries for status/parent/project must carry `{field, old_id, new_id, old_label,
new_label}`. Costs nothing now; makes §4's revert endpoint and the timeline immune to
lane renames. Companion behavior: **consecutive same-actor description edits coalesce**
(bump the previous activity's timestamp instead of appending —
`issue_activities_task.py:88-111`); autosaving editors otherwise write dozens of rows.

### 3.5 List-read mechanics: semantic sort ranks, stable ties, picker exclusions

- **Status sorts order by category rank** (backlog→todo→in_progress→done→cancelled),
  never alphabetically; priority likewise (`order_queryset.py:150-169`).
- **Every ordering appends a deterministic tiebreaker** (`created_at, id`) so pagination
  never straddles ties (`:186-192`). We should assert this structurally on `TASK_SORTS`.
- **Picker-context search exclusions** (`search/issue.py:37-83`): choosing a parent
  excludes self + ancestors + descendants; choosing a relation excludes already-related
  tasks in either direction. Our write-time cycle guards stay; the search API grows an
  `exclude_relatives_of=<task_id>` param so pickers can't offer what the write will 422.
- **Sub-task rollup gains a category distribution** beside `{done,total}`
  (`sub_issue.py:171-201`) — the datum for a segmented progress ring; one grouped
  aggregate, no denormalization. Hidden (archived) children stay excluded from rollups.

### 3.6 Generic import provenance: `(external_source, external_id)`

Plane carries the pair on every importable entity (`issue.py:162-163`, states, labels,
cycles, attachments), giving *every* importer idempotent upsert semantics
(`api/views/issue.py:616-646`) — where our `clickup_id` is single-provider. Adopt **at
the moment 161's named ticket widens the ClickUp constraint per-org anyway**: rename the
concept to `(external_source, external_id)`, `UNIQUE (organization_id, external_source,
external_id)`. The `clickup_snapshot`/`clickup_synced_at` columns stay ClickUp-specific
(they serve the merge, not identity). Their importer *framework*, by contrast, is a
vestige (moved to closed-source; no live routes) — our dry-run/mapping-plan/verify
approach is strictly better and stays.

### 3.7 Patterns to bank for features we'll build later

- **Cycles/sprints reference design** (for the reserved `pm_sprints`): membership is a
  join table, not a column; **no burndown time-series table** — live burndown computes
  from `completed_at`, and cycle close freezes totals + distributions into one
  `progress_snapshot JSONB` (`cycle.py:74`, `cycle_transfer_issues.py:410-458`); closing
  rolls incomplete tasks forward as an explicit, logged transfer. Our `completed_at`
  column is already the entire data requirement.
- **Webhook delivery checklist** (when `/workflows` grows a webhook-out node): HMAC
  signature header, per-delivery UUID, request+response log table, bounded retries with
  jitter, auto-disable + owner email after final failure, retryable-vs-permanent
  distinction, and **SSRF-pinned fetch** (resolve→validate→pin, never follow redirects —
  `webhook_task.py:312-317`, closing the DNS-rebinding TOCTOU their GHSA cites). Tenant
  URLs are hostile input; this list is complete and each item was learned from an
  incident.
- **Email digest outbox**: in-app notifications write immediately; email writes an
  outbox row, and a 5-minute sweep groups per receiver→task→actor into ONE digest
  (`email_notification_task.py:46-85`). Preference flags gate the email channel only.
  Never send-per-event.
- **Export jobs**: async job row (status, filters JSONB, unique token) → file → presigned
  URL with 7-day expiry → daily cleanup sweeper (`exporter.py`, `export_task.py`).
  Re-downloadable history. A filtered-list CSV/XLSX export is small and high-leverage.
- **Delta-sync feed** for agents/mobile: a list variant ordered by `updated_at` with
  `updated_at__gt`, plus the prerequisite trick — satellite writes (comments, links,
  assignees) bump the task's `updated_at` (`issue_activities_task.py:1532-1538`), or the
  feed misses them. Their "cursor" is offset-in-costume — do not copy it as keyset.
- **`is_epic` flag on task types** (`issue_type.py:20`) instead of seeding-convention
  identity — one line, makes the Epic-root rule enforceable without knowing seed names.
- **Project `timezone` column** (`project.py:116`) — gives the Gantt and any auto-close
  sweeper a correct midnight; today we have nowhere to hang that.
- **Per-user view state**: shared `pm_views` stay canonical; a
  `pm_view_user_state(view_id, member, config)` sibling holds each member's grouping/
  collapse state (Plane's `ProjectUserProperty` family, `project.py:342-369`).
- **Session rows carry a denormalized, indexed `user_id`** (`session.py`) — the whole
  "list/revoke my sessions" feature is that one denormalization. For the control plane.

## 4. The frontend gaps worth taking, ranked

Verdicts here anneal into the UI work queue; each is an interaction spec, not a port.

1. **Spreadsheet layout** — the missing fifth view. One row per task, one column per
   card-field, every cell an inline editor, per-column sort in the header, sub-tasks
   expand indented in-table, quick-add pinned to the bottom. Power users triage here.
   Our custom fields map naturally to columns; the column set = the same visibility
   contract as card chips (below).
2. **Kanban sub-grouping (swimlanes)** — `group_by` × `sub_group_by` (status columns ×
   assignee rows = the standup matrix), per-lane collapse, empty lanes hidden unless
   asked for. Our grouping lib already computes both axes; the cross-product render is
   the missing piece.
3. **Display-properties contract** — a per-view "shown fields" toggle set; every chip on
   every card gates on it; the same key set drives spreadsheet columns and calendar
   blocks. Plane unifies at the field-visibility level, we unify at the derived-data
   level (`taskCard.ts`) — **combining both is better than either**: keep `taskCard.ts`
   as the single fact layer, add the user-facing visibility contract on top, persist it
   with the saved view.
4. **Quick-add in every group** — inline title-only row in each list group / kanban
   column / calendar day, **pre-filled with the group's value** (adding under "In
   Progress / Alice" creates it in-progress, assigned to Alice), Enter submits and
   resets so you can keep typing. Highest-frequency action in the product.
5. **Peek escalation + focus return** — TaskPanel gains side-peek ↔ centered-modal ↔
   full-page sizes, and **Escape returns focus to the originating card**
   (`view.tsx:104-113`) so keyboard flow survives open→close in long lists.
6. **Save/Update view affordances** — the applied-filters row compares live state to the
   applied saved view (we already guarantee the `toConfig`/`fromConfig` round trip) and
   conditionally offers Save view / **Update view** / Clear all. Makes view divergence
   legible.
7. **Palette as action system** — keep our ranked search palette, add: an action
   registry (create task, switch layout, go-to project, and mutate-open-task pickers:
   status/assignee/priority *inside* the palette), two-key go-sequences (`g`+`h` home
   style, 1s timeout), all shortcuts suppressed while typing in inputs, and a
   shortcuts-help modal. Skip their URL-context machinery — we have one surface.
8. **Keyboard selection cursor** — ArrowUp/Down moves an active-row cursor, Shift+Arrow
   extends selection from it, Enter opens the panel; feeds the existing BulkBar.
9. **Drop feedback** — when a drag can't drop (grouped by assignee, say), a translucent
   overlay states *why* ("drop here would reassign — drag disabled"); after any drop or
   quick-add, the moved card scrolls into view and flash-highlights. Pure feedback,
   no write-model change; replaces our silent drag restriction.
10. **Calendar** — week layout beside month, weekend toggle, per-day quick-add
    (due-date prefilled), per-day overflow ("+N more") instead of our whole-month
    banner. We already have drag-between-days.
11. **Notifications inbox** — bell opens a two-pane inbox (list + embedded TaskPanel),
    mark-read-on-open, tabs all/mentions with **separate unread counts** (the mention
    badge is the high-signal one), snooze later.
12. **Human task IDs + copy-link** — we already allocate per-root numbers; surface them
    (`KEY-42` style) on cards/panel with a copy-deep-link button. Makes tasks
    referenceable in chat and commits — which our agent spine wants anyway.
13. **Timeline polish** — zoom presets (week/month/quarter as px-per-day steps),
    drag-bar-edges to set dates, hover-a-dateless-row to place it with a 1-day default.
    **Keep** our dependency arrows + warn-don't-reschedule (D-PM-12); Plane's OSS core
    doesn't even render dependency arrows. **Refuse** their infinite-extend canvas —
    our fixed filtered range is simpler and bounded.
14. **Small wins**: one-slot localStorage draft for the create form (restore on reopen);
    click a progress-bar segment to apply that status-group filter; pin projects/views
    to the tree top (flat, no folders); a capped recently-viewed list in MyWork; a tiered
    `EmptyState` primitive in `ui/` (text-only vs text+CTA, tokens only).

## 5. Refusals, with the reason on record

- **Modules** (second M:N grouping axis): exactly what D-PM-8 rejected; costs four
  tables per axis (join + user-props + links + favorites). Our subtree + multi-grant
  already expresses deliverable grouping.
- **Estimate systems** (Estimate + EstimatePoint indirection): two tables and a join to
  say "3 points" whose meaning mutates if the system is edited. `estimate_mins INT`
  aggregates without interpretation. Recorded for the day someone asks for t-shirt sizes.
- **Four-format descriptions + full-row version snapshots + the live collab server**:
  collaborative-editor infrastructure (Yjs binary canonical, HTML/JSON/stripped derived,
  a Node sidecar delegating auth per-connection). A whole second realtime stack beside
  AG-UI/SSE for a need neither the PM spec nor Notes has established. **Two lessons kept
  even on refusal**: store derived forms beside the canonical one and regenerate on
  every save (makes search/email/export free); and if we ever add rich text, start from
  TipTap-the-MIT-library, markdown-stored, mention-autocomplete first — our
  mention→notification wiring already exists, which is the hard part.

  > ⚠️ **AMENDED 2026-08-10 — this refusal bundled two separable things, and the bundling
  > was the error.** A second-pass read established that **`apps/live` is not required for
  > an editor at all**: Plane ships three editor tiers off one core, and the collaborative
  > provider is `undefined` for two of them. Verified — the *only* consumer of the realtime
  > server anywhere in their web app is the Pages body; task descriptions and comments never
  > open a socket. So "rich text" and "a second realtime stack" are independent decisions
  > and only the second is refused here. **The refusal of the collab stack stands unchanged.
  > The refusal of rich text does not survive its own reasoning** — it is a client-side
  > dependency with zero new services, on a library (**TipTap v3**) we ALREADY ship and use
  > in `src/app/email/components/SignatureEditor.tsx`. Minted as a ticket in
  > `project_management_app.md` §9.4 rather than left inside a refusal that no longer
  > applies to it.
- **Pervasive soft-delete**: every query in their tree re-asserts `deleted_at IS NULL`;
  a missed guard resurrects ghosts. Our archived-only posture stands. If any pm table
  ever gains `deleted_at`, the non-obvious part to copy is **paired partial-unique
  constraints** (`WHERE deleted_at IS NULL`) so re-creating a deleted name works.
- **Draft shadow table + `is_draft` flag** (two mechanisms for one concept — a scar,
  not a pattern): our personal projects + one-slot form draft cover capture.
- **Comment threading + INTERNAL/EXTERNAL comment access**: serves their public-board
  surface; not ours (yet — see §6).
- **Server-side grouped pagination** (RowNumber windows per group): correct pattern at
  10k-task boards; at our sizes client grouping over the filtered page is simpler.
  Revisit only when a single board exceeds a few thousand tasks.
- **Their importer framework and integration registry**: vestigial in OSS (moved to
  closed-source); our dry-run/mapping-plan importer is strictly better.
- **Stickies**: personal scratch notes are out of Projects scope; a project-less task
  already covers it.

## 6. Two questions this research raised — ⚠️ BOTH ANSWERED 2026-08-09 (same day)

> Answers recorded as **D-PM-13** (docs → knowledge base; PM links, never owns; two-key
> access) and **D-PM-14** (public boards deferred) in `project_management_app.md` §8.
> The analyses below are kept as the record each answer was given against.

**Q1 — Public read-only boards.** Plane publishes any container under a capability URL
(`anchor = uuid4().hex`, physically separate view tree +
serializers so the public surface is reviewable in one directory — `apps/space`,
`deploy_board.py`). A client-facing roadmap view is real product value. But for us it
would be **the first anonymous tenant-data READ route**: `/workflows/hooks/{token}`
established the capability-URL category for *writes into a rate-limited engine*; an
anchor route *streams org data out*. Under pooled RLS the handler must resolve
anchor→org **before** `SET LOCAL app.tenant_id` — one deliberate, auditable bypass. If
ever built: dedicated `routes/pm_public/` module with its own read-only models (never a
flag on member endpoints), no member-roster endpoint (Plane exposes member names/avatars
to anyone with the anchor — refuse that), per-board disable, rate limits, and a
leak-audit entry. The honest alternative is invite-as-restricted-guest.

> ⚠️ **Three corrections from the second-pass read (2026-08-10).** All three make the
> deferred feature look *less* safe than this paragraph did, so they matter to the revisit.
> (1) **There is no per-board kill switch.** `is_disabled` and `is_activity_enabled` exist
> on the model and are read by no view or component anywhere in the tree. Revocation is
> unpublish — which soft-deletes the row and loses every existing link — and republishing
> mints a new anchor. "Per-board disable" above is therefore a thing we would have to
> BUILD, not adopt. (2) **Refusing the roster endpoint would not close the leak.**
> `created_by` rides in the list projection and vote/reaction payloads carry each actor's
> name and avatar, so member identity escapes twice more by other routes. (3) **The
> capability URL is recoverable from the identity URL**: an unauthenticated endpoint answers
> "give me the anchor for this workspace slug + project id", so anyone who ever knew a
> project's UUID can recover its current anchor forever — which defeats unpublish/republish
> as a rotation. Generalises to our own `/workflows/hooks/{token}` category: a capability
> scheme is only as strong as the weakest endpoint that will hand the capability out.
**ANSWERED — D-PM-14: deferred.** *"For now, let's leave out public read-only boards. We
will revisit it when needed."* This paragraph is the starting point for that revisit.

**Q2 — Who owns project docs?** Plane's Pages (wiki with hierarchy, project attachment,
versions, an embed/backlink log) is their second-biggest surface. Our PM spec assigns
docs to Notes; `note_taker_app.md` §1.2 declares itself *not* a general document editor.
So nobody owned free-form project documentation — until this question was put to the
owner. **ANSWERED — D-PM-13:** there is a separate **knowledge base**; PM *fits in with*
it rather than owning docs. KB documents are creator-owned, shared to people or a team,
and visibility follows the share — grant-vocabulary shaped, so the KB should reuse
`email | group:<slug> | org` rather than mint a second vocabulary. PM's integration is a
reference row (task/project → doc), two-key access (the link never widens the doc's
audience, nor the doc the task's), R5 on both sides. Plane's Pages model remains useful
purely as the checklist of what the *KB* itself will eventually want: hierarchy,
project attachment, versions, an embed/backlink log.

A third, smaller: Plane's `guest_view_all_features=false` mode (guests see only tasks
they created) suggests a **restricted grant level** for contractors/clients — worth
holding until a real external collaborator shows up, then it's a grant attribute, not a
role.

## 7. Where the two references disagree — and which side we take

| Question | Paca | Plane | We take |
|---|---|---|---|
| Ordering | Per-view side table | One float per issue | **Paca** (built, D-PM-5) — Plane is the counterexample |
| Containers | One self-FK tree | Flat workspace→project | **Paca** (built) — departments/subprojects are real for us |
| Statuses | Rows + semantic category | Rows + semantic group | Both — converged |
| Agents/integrations as members | First-class thesis | Bot-user pattern | Both — converged (third source) |
| Task capture from outside | — (absent) | Intake/triage state machine | **Plane** (§3.1 — its biggest single contribution) |
| Sprint mechanics | — (absent) | Join table + snapshot-on-close | **Plane**, when sprints come (§3.7) |
| Layout breadth | List/board | +Spreadsheet, +sub-grouped kanban, +week calendar | **Plane** (§4) |
| Outbound webhooks / digests / exports | — (absent) | Hardened, incident-informed | **Plane**, as requirement checklists (§3.7) |

## 8. Consolidated verdict table (annealed into `project_management_app.md` §11.19)

| # | Item | Verdict | Where it lands |
|---|---|---|---|
| P-1 | Intake/triage (wrapper row, triage category, accept-in-place, duplicate_to, snooze) | **ADOPT** | new ticket candidate, pairs with §6.5 email capture |
| P-2 | Watchers table + auto-subscribe + mention diffing (edit notifies additions only) | **ADOPT** | notifications seam |
| P-3 | Archive guard (closed categories only) | **ADOPT now** | one predicate in the archive path |
| P-4 | `archive_in`/`close_in` columns + `/workflows` sweeper, automation-flagged activities | **ADOPT** | pm_projects + workflows |
| P-5 | Activity meta carries `{old_id,new_id,old_label,new_label}`; description-edit coalescing | **ADOPT** | `record_activity` meta rule |
| P-6 | Category-ranked status sort + deterministic `(created_at,id)` tiebreaker on every sort | **ADOPT** | `TASK_SORTS` |
| P-7 | Picker-context exclusions in search (`exclude_relatives_of`) | **ADOPT** | search.py |
| P-8 | Child category-distribution beside `{done,total}` | **ADOPT (when panel draws segments)** | relation counts attacher |
| P-9 | `(external_source, external_id)` generic provenance, per-org unique | **ADAPT at the 161-ticket moment** | importer identity |
| P-10 | Spreadsheet layout | **ADOPT** | biggest UI gap |
| P-11 | Kanban sub-grouping | **ADOPT** | board |
| P-12 | Display-properties visibility contract over `taskCard.ts` | **ADOPT** | shared card layer + saved views |
| P-13 | Group-context quick-add everywhere | **ADOPT** | all layouts |
| P-14 | Peek size escalation + Esc-returns-focus | **ADOPT** | TaskPanel |
| P-15 | Save/Update-view divergence affordances | **ADOPT** | FilterBar |
| P-16 | Palette action registry + go-sequences + shortcuts help | **ADAPT** | SearchPalette |
| P-17 | Keyboard selection cursor for bulk ops | **ADOPT** | selection lib |
| P-18 | Drop-refusal overlay with reason + post-drop flash | **ADOPT** | board/list |
| P-19 | Calendar week layout, per-day quick-add + overflow | **ADAPT** | CalendarView |
| P-20 | Two-pane notifications inbox, split mention badge | **ADAPT** | NotificationBell |
| P-21 | Surface human task IDs + copy-link | **ADOPT** | cards + TaskPanel |
| P-22 | Timeline zoom presets + edge-drag dates + hover-to-date | **ADAPT** | TimelineView (keep D-PM-12) |
| P-23 | Sprints reference design (join + snapshot-on-close + carry-forward) | **BANK** | future pm_sprints |
| P-24 | Webhook-out checklist (sign, log, retry, auto-disable, SSRF pin) | **BANK** | future workflows node |
| P-25 | Email digest outbox + sweep | **BANK** | when PM emails |
| P-26 | Export job pattern (token, presigned, expiry sweep) | **ADOPT (small)** | filtered-list CSV |
| P-27 | Delta-sync feed + satellite `updated_at` bump | **ADAPT (agents/mobile)** | list variant |
| P-28 | `is_epic` flag; project `timezone`; per-user view state; session `user_id` denorm | **ADOPT piecemeal** | small columns |
| P-29 | Public boards | **DEFERRED (D-PM-14, owner 2026-08-09)** | §6 Q1 |
| P-30 | Pages/wiki | **REFUSE — docs live in the knowledge base (D-PM-13)** | §6 Q2 |
| P-31 | Modules; estimate systems; collab stack; pervasive soft-delete; stickies; their importer | **REFUSE** | §5 |
