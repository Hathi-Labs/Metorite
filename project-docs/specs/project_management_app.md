# Projects App — Master Plan (native project management; ~~ClickUp retirement path~~ **the PM system of record**)

> 🔴 **READ §12 FIRST — 2026-08-24, D52/D53 re-cut this spec's premise.**
> ClickUp is **retired outright, not cut over**: there is no connector, no importer,
> no sync and no coexistence window. **§6's ClickUp binding and the whole of §7's
> migration path are SUPERSEDED** — they describe a staged inversion against an
> external system that no longer exists in this product. §11's parity backlog stays
> valuable as the *record of what parity required*, and its verdicts still stand;
> read it as history, not as a plan. Board row **WS-39**.
> The second half of §12 is the one every reader gets wrong: **Projects is now also
> the store behind the Tasks app** (D53) — `pm_tasks` + `pm_task_personal` are the
> only task store, and `routes/projects/personal.py` is the lens Tasks renders.

> **Product:** Metorite · **Feature:** Projects (the primary work-management
> module, sliced into every other Center) · **Created:** 2026-08-05 · **Updated: 2026-08-25**
> 🟢 **WS-27bg SLICE 2 REMAINDER — RENAME — BUILT 2026-08-21 on `ws-27bg-project-rename`
> (PR #47), NOT merged and NOT deployed** (§11.36's narrowed list, corrected) — a project can
> be renamed for the first time: inline on the tree row, frontend only, **no migration and no
> API change** (`PATCH /projects/nodes/{id}` already accepted `name`). This closes the
> "no project-editing UI at all" finding in §9.8.1's measurement table. ⚠️ Fenced by 5 unit
> cases and 8 Playwright cases, and **nothing in CI runs `e2e/`** — H-27. ·
> **Previously updated 2026-08-11**
> (**WS-27be built — §11.33, migration 170**; **WS-27ak's narrowed slice built — §11.31**;
> **WS-27bc's one dispatchable slice built — §11.34**;
> **WS-27am's — §11.29**; **WS-27bd's — §11.30**;
> each narrowed, and what each STRUCK is the more useful half of the record) ·
> 🟢 **WS-27be BUILT 2026-08-11 on `ws-27be-trgm-search-index`, NOT merged and NOT deployed**
> (§11.33) — `pg_trgm` + `gin_trgm_ops` on `pm_tasks.title`/`.description` and a plain btree on
> `task_number`, in `infra/postgres/170_projects_search_trgm.sql` (number taken at build time,
> R1; **re-check at merge**). `idx_pm_tasks_fts` — dead since migration 146, because `ILIKE`
> cannot use a `to_tsvector` index — is **kept**, additive-only per R6; its drop and the
> `pg_stat_user_indexes.idx_scan` trigger for it are recorded in §11.33. `MIN_QUERY` moved from
> `search.py` to `filters.py`, so the list endpoint's `?q=` now enforces the same minimum the
> palette always did and answers `{"rows": []}` rather than 422 or the whole board. Verified
> against a **real Postgres** at 60k rows (R8): `Filter:` → `Index Cond:`, 652.9 ms → 0.71 ms on
> search, 441.7 ms → 0.31 ms on the list — `tests/live/live_ws27be.py`, 23 checks green.
> ⚠️ **Two things are owed:** the `idx_pm_tasks_fts` drop, and a product decision on raising
> `MIN_QUERY` to 3 — trigram indexes cannot serve a 2-character pattern, so the shortest
> ACCEPTED query is the longest UNSERVABLE one (127 ms, measured). ·
> 🟢 **WS-27bc's `pagedPicker` BUILT 2026-08-11 on `ws-27bc-paged-picker`, NOT merged and NOT
> deployed** (§11.34) — `app/projects/lib/pagedPicker.ts`: a 48px scroll-threshold predicate
> whose boundary is inclusive, page accumulation that dedupes by id (the list is ordered by
> `updated_at`, so a row legitimately crosses a page boundary), a terminal state **derived** from
> a short page rather than a `hasMore` flag that can disagree with the rows, and a min-length
> gate that **imports** `MIN_QUERY` and `isCurrent` from `./search` — fenced by a source scan,
> because no behavioural test can tell a shared constant from a second copy. Pure module, 31
> cases, 9/9 mutants red; **no component, no endpoint change, no call site wired, no debounce.**
> 🔴 **WS-27bc as a whole remains NO-GO** (§9.5.2): its central "leading-wildcard scan"
> justification is **struck as false** (the predicate is `ILIKE '%…%'` either way and `pg_trgm`
> appears zero times in `infra/`; the minimum bounds the RESULT SET), its 300ms contradicts
> seven ad-hoc debounce copies with no shared helper, and `/projects/search` (capped-not-paged,
> by decision) versus `/projects/tasks` (paged, unranked) is a decision nobody has taken. Its
> surface half is re-sequenced **behind** WS-27ak, not beside it. ·
> 🟢 **WS-27ak item (1) `Modal` BUILT 2026-08-11, REPAIRED the same day on
> `ws-27ak-modal-repair` (repair round 1 of 2 — two behavioural fixes, three corrected
> records, one new structural fence), NOT merged and NOT deployed** (§11.31) —
> `src/components/ui/Modal.tsx` over **`@base-ui/react@1.7.0`**
> (D-PM-15), the **only** file in the tree allowed to import the substrate (conformance rule
> **8**, which is why this slice necessarily edited the root `CLAUDE.md`), and all **six** of
> `/projects`' hand-rolled dialogs render it. `app/projects/page.tsx` was **not touched** and
> `overlayOpen` still reports the truth — the wrapper is strictly controlled and holds no open
> state, pinned by a Playwright case that presses `g t` under an open dialog and asserts the
> page does not navigate. Frontend only — no migration, no API change; one new dependency.
> 🔴 **Two of the ticket's done-whens were factually impossible against the substrate the
> decision chose, and are restated here rather than reported as met** — see §11.31: there is no
> `outsidePressEvent` prop on `Dialog.Root` in 1.7.0 (the behaviour is right, the mechanism is a
> rendered `Dialog.Backdrop`), and **`@base-ui/react` never sets a real `inert` attribute** — it
> marks the background `aria-hidden` and contains focus with guard nodes, so a screen reader
> cannot walk in but **find-in-page still can**. Items (2) Tooltip, (3) Toast, (4) Combobox and
> (5) Skeleton are **not built**. ·
> **Previously updated 2026-08-10**
> (status truth pass + tenancy alignment — R4; **WS-27ag shell/mobile slice built the same
> day**; **S4 convergence slice built the same day — §11.21**; **S6 card-pills slice built the
> same day — §11.23**; **WS-27ac calendar week/overflow slice built the same day — §11.24**;
> **WS-27ab view-ergonomics slice — §11.25**; **WS-27ae's export third — §11.26**;
> **WS-27ae's delta-sync + small-columns thirds — §11.27, migration 168**;
> **WS-27al's narrowed thirds — §11.28**; **WS-27bd's two of five — §11.30**) ·
> 🟢 **WS-27al (1)(2)(5-narrowed) BUILT 2026-08-11, merged onto `claude/paca-research-task-management-a1f6zd`, NOT
> merged and NOT deployed** (§11.28) — `ControlLink` on the list and table task titles (the
> app's first `<a href>` on a task at all, so cmd/ctrl/shift/middle-click finally open a new
> tab), the shared `data-prevent-outside-click` walker consumed by `NotificationBell`, and
> **My Work's overdue predicate folded onto the shared one — it had NO completion check.**
> ⚠️ It also had **no in-app caller**, so this was a latent divergence, not a visible bug;
> an earlier draft of this bullet claimed a finished task read as overdue in My Work and
> that was wrong (§11.28). Frontend only — no migration, no API change.
> ⚠️ **Three of the ticket's six items are struck, not deferred silently** — numbered as
> §9.4.2 numbers them, not as the build order: **(3)** lazy tooltip mounting is unbuildable
> here (zero `Tooltip` components exist; the native `title=` attribute has no machinery to
> mount lazily → WS-27ak(2), Wave 2); **(4)** selected-first ordering names no target
> multi-select; **(6)** selection self-heal is **already shipped** (`app/projects/page.tsx`
> prunes off `onScreen` via `src/lib/selection.ts`). Built: **(1)**, **(2)** and the
> narrowed **(5)**.
> 🔴 **The ticket body's "seven predicates" and "today counts as due" are both wrong** and were
> deliberately NOT acted on: there are three (plus one deliberately-different waiting
> predicate under its own contract), and `<` vs `<=` is pinned by two tests with explanatory
> comments — the day-boundary question is a doc blocker for the owner, not an agent's call. ·
> **Status:** ✅ **WS-27 a–t MERGED AND DEPLOYED** (a b d e f i j k l m n via #390/#393/#394/#398;
> o–t via **#399**; **u–z via #408**, 2026-08-10) — migrations **146, 147, 150, 152, 155, 156,
> 160, 161, 164, 165, 166 are applied on prod** (164/165/166 log-verified on the 2026-08-10
> deploy). The earlier "not deployed and never run" status is **struck**; what remains never-run
> is the **ClickUp import against the live workspace**, which is OWNER-GATE (§6 WS-27 (a)).
> 🟡 **c** two-way sync (waits on WS-1 BO-1a+BO-1b) · 🔴 **g** cutover/retirement · 🟡 **h**
> `gtd_items` retirement (data move 🔴) · 🟢 **u–z shipped**, their owner activation steps in
> HANDOVER §1 ·
> 🟢 **ae (delta-sync + small columns) BUILT 2026-08-10, on branch, NOT merged and NOT
> deployed** (§11.27) — `GET /projects/delta/tasks` with a keyset cursor, an explicit
> `removed[]` over migration **168**'s `pm_task_tombstones`, satellite `updated_at` bumps,
> `pm_task_types.is_epic` and `pm_view_user_state`. **Migration 168 is NOT applied on prod**
> (verified only against a scratch Postgres 16). The CSV-export third of the basket is a
> separate slice. ·
> 🟢 **ag BUILT 2026-08-10 — MERGED to `main` and DEPLOYED** (#421) (§11.20) — the app joins
> the house shell and gets a mobile layout at all: `AppShell` learns `isProjectsPage`
> (Projects · Views · Search), the tree and the mode picker become drawer sheets, an opened
> task is full-screen on a phone, the desktop rail collapses at Tasks' `w-60`, and the
> six-purpose header splits into a title row and an action row. Frontend only — no migration,
> no API change. ⚠️ **The phone-viewport and four-theme visual pass is still owed**: no
> browser was runnable in the build environment (§11.20's closing note). ·
> 🟢 **S1 BUILT 2026-08-10 — MERGED to `main` and DEPLOYED** (#421) (§9.2, under WS-27ad) —
> the /tasks board card and column adopt /projects' chrome under the owner's ruling that
> *"the Tasks app is only a slice of the Projects app"*: one column shape and surface, one
> gutter, the shell's `completed` and `atCursor` props finally passed by their /tasks caller,
> the shared `AvatarStack` instead of a private copy, the selection checkbox moved OUT of the
> card as a sibling target, and the title clamped to two lines in the shared file — which
> **amends ad's recorded "modal select-mode is KEPT" decision on the card side**. Frontend
> only — no migration, no API change. ⚠️ Visual pass still owed for the same reason as af/ag. ·
> 🟢 **S3 (selection/bulk parity) BUILT 2026-08-10 for the /tasks LIST surfaces, on branch
> `ws-s3-selection-bulk-parity`, **MERGED to `main` and DEPLOYED** (#421) (§9 ticket "S3") — WS-27ad's
> kept modal select-mode is **reversed by owner ruling** ("Projects is canonical, Tasks
> conforms"): `selectMode` is now a derived mirror of "something is selected" and gates only
> the bulk bar, the checkbox is permanent and sits outside the row content, shift-sweep is
> ungated, the bar moved to the top onto Projects' chrome and primitives, and select-all
> exists. ⚠️ The board card (`TaskCard`/`TaskBoard`, sibling slice S1) and `WaitingForView`
> are still modal, and no browser was run here either. ·
> 🟢 **S4 BUILT 2026-08-10 — MERGED to `main` and DEPLOYED** (#421) (§11.21) — the three
> findings where **Projects**, not Tasks, carried the defect: `MyWork`'s active pill moves
> off `bg-accent` onto the house `bg-primary/10 text-primary` (now fenced by a **sixth
> conformance rule**, per-file and ratcheted), `MyWork`'s bespoke fourth task row is rebuilt
> on `TaskCardShell`/`TaskMeta`/`StatusChip`, and the board's one conflated empty state
> becomes two — filtered-to-nothing (with **Clear filters**) vs genuinely empty — off the
> existing `isFiltered` predicate, through a promoted `src/components/EmptyState.tsx`.
> Frontend only — no migration, no API change. ⚠️ **The four-theme sweep is owed** for the
> same reason: no browser runs in this environment. ·
> 🟢 **S5 BUILT 2026-08-10, on branch `ws-s5-projects-task-panel`, NOT merged and NOT
> deployed** (§11.22) — the second reversed-direction finding: `/projects`' `TaskPanel`
> adopts `/tasks`' `ItemDetail` **composition** (header chip row → grouped details cells →
> discrete labelled sections → pinned composer, one scroll region) and loses its two raw
> controls. **`Select` is added to `src/components/ui/Input.tsx`** — the themed
> single-choice field the tree never had, which 38 files had each hand-rolled — and the file
> input is hidden behind a `<Button>` that lists what is uploading. **Conformance grows a
> seventh rule** for both. Frontend only — no migration, no API change. ⚠️ Same owed check:
> no browser runs here, so the phone viewport and the four-theme sweep are for review. ·
> 🟢 **S6 BUILT 2026-08-10, on branch `ws-s6-projects-card-pills`, NOT merged and NOT
> deployed** (§11.23) — the board/list card finally draws the facts its own row already
> carries: a **priority chip** off `importance` and **named, registry-coloured tag pills**
> instead of a bare count — both of which `DEFAULT_SHOWN` has promised since WS-27x while only
> the spreadsheet honoured them, so **no shown-fields default moved** — plus `estimate_mins`,
> which `taskFacts` had silently dropped behind a comment claiming the column did not exist.
> The shared vocabulary is **extended, not forked**: `MetaTone` gains `warning`, `MetaChip`
> gains an optional `hue` (a NAME, resolved by `TaskMeta` alone) and `TaskFacts.tagCount`
> becomes `tags`. Frontend only — no migration, no API change. ✅ **The four-theme × two-mode
> visual sweep was actually run this time** (Playwright + the pre-installed Chromium, fixtures
> at the network boundary), which closes the check af/ag/S1/S3/S4/S5 all left owed *for this
> surface*; one honest finding recorded in §11.23 about Material dark's pale `--warning`. ·
> 🟢 **WS-27ab BUILT 2026-08-10 — merged onto `claude/paca-research-task-management-a1f6zd`, in PR #422 — **MERGED to `main` 2026-08-11** (`ebf68f4`);
> the ~~NOT on `main` and NOT deployed~~ clause is struck, it was true only while #422 was open** (§11.25) — view ergonomics, from Plane research P-14/15/16: the task panel gains
> **peek → side → full**, persisted per user, and Escape hands focus back to the card that
> opened it; the saved-view association **survives an edit** and `FilterBar` grows a
> dirty-view row (**Update view · Save as new · Reset**) driven by ONE pure
> `grouping.viewDivergence` over the `toConfig`/`fromConfig` round trip; the palette's
> commands become a **declared registry** (`lib/commands.ts`) that `g`/`v` key sequences run
> and the `?` sheet is *printed from*. Plus the WS-27x gap S6 recorded: the list's **Status
> and Assignees columns now obey `shown_fields`** like every other field — **no default
> moved**, both keys have been in `DEFAULT_SHOWN` since WS-27x. Frontend only — no migration,
> no API change (the view update uses the existing `PATCH /projects/views/{id}`). ✅ Browser
> driven, and the four-theme × two-mode sweep run. ·
> 🟢 **WS-27ac BUILT 2026-08-10 — merged onto `claude/paca-research-task-management-a1f6zd`, in PR #422 — **MERGED to `main` 2026-08-11** (`ebf68f4`);
> the ~~NOT on `main` and NOT deployed~~ clause is struck, it was true only while #422 was open** (§11.24) — the calendar gains a **week** layout, an **exact** `+N more` that
> expands, and a drop that says why it refused. The week is the month grid's own row, not a
> second calculation: `mondayOffset` and `runOfDays` are shared, `MonthGrid` became
> `CalendarGrid` with a `layout` discriminator, and `calendarWindow`/`taskDays`/`placeTasks`/
> `rescheduleTo` never learned there was a second layout — so a week asks the SAME endpoint
> for ten days and gets the same filters and the same `triage` default. Done-when 5 landed as
> a window-shape parametrisation of the §11.16 coverage rule. Frontend plus one gateway test
> file — **no migration, no API change, no new query parameter.** ✅ Four-theme × two-mode
> sweep and every gesture (overflow, quick-add, drag-reschedule, refusal) driven in a real
> browser, pinned to UTC+5:30. ⚠️ One honest limit recorded in §11.24: of the two drop
> refusals only the foreign-payload one is reachable today. ·
> 🟢 **WS-27ae (EXPORT THIRD) BUILT 2026-08-10 — merged onto `claude/paca-research-task-management-a1f6zd`, in PR #422 — **MERGED to `main` 2026-08-11** (`ebf68f4`);
> the ~~NOT on `main` and NOT deployed~~ clause is struck** (§11.26) — `GET /projects/export/tasks.csv`: the caller's
> current filters through the one shared `build_task_filters`, the view's `shown_fields` as
> the columns (in the vocabulary's order, which is what the table draws), `#` and `Title`
> unconditional, and a toolbar `Export` button. **No migration** (R1: no number taken; 168 is
> the sibling agent's for the delta-sync and small-columns thirds, which are NOT built).
> ⚠️ **The ticket's "export-job pattern" does not exist in this repo** — verified absent;
> what shipped is a synchronous bounded response instead. ⚠️ **The cap is a refusal, not a
> truncation**: past 5000 matching rows the endpoint answers 422 naming the real count,
> because a partial CSV is byte-indistinguishable from a complete one. CSV-injection cells
> (`=`/`+`/`-`/`@`/TAB/CR) are apostrophe-prefixed, bare numbers exempt. ⚠️ **A browser WAS
> run for this slice** — the export triggered from the real toolbar with a filter applied,
> the downloaded bytes are the gateway's own, and the four-theme × two-mode sweep plus a
> 390 px viewport were checked; and it caught a real defect (`Response.text()` strips the
> UTF-8 BOM, so the saved file differed from the bytes the endpoint produced). It also found
> a **hermetic-fake defect**: `_projects_fakes` read `?status_category=` as "hide closed
> work", so `status_category=done` returned the OPEN tasks — fixed here.
> 🔧 **Correction 2026-08-11: the BOM fix above was only half applied, and this export has
> served BOM-less CSV ever since it began serving at all.** ⚠️ **What is evidenced, stated
> exactly** (an earlier draft of this line said "in production" and was asked to justify it,
> correctly): WS-27ae is on `main` — `1de846a` is an ancestor of `origin/main`, merged by
> `ebf68f4` (PR #422) — and the `deploy` workflow reported **success** on `ebf68f4` at
> 2026-08-10T23:53:53Z. That is strong but it is *a green job*, and CLAUDE.md
> non-negotiable 8 says delivery is verified by evidence and never by one: nobody has read a
> ledger line or a deployed SHA back for this change. So: **almost certainly live, not
> proven live.** The defect is real either way — it is in the merged code — and the fix does
> not depend on which it is. `saveCsv` kept the last hop as bytes, but the BFF proxy
> (`src/app/api/projects/[...path]/route.ts`) did `await res.text()` and rebuilt the
> response — the same UTF-8 decode one hop earlier, so the BOM was already gone. Measured on
> node v22 through the real handler: `EF BB BF 4E 61 6D` in, `4E 61 6D 65` out. Found and
> fixed while building the CRM's copy of the arm (WS-26i-export, `crm_app.md` §9); the proxy
> now reads `res.arrayBuffer()` and also forwards `X-Export-Rows`. Fence:
> `src/lib/export.test.ts` runs both proxies end to end over a BOM'd body. **The fix rides
> the WS-26i-export branch and is not deployed.** ·
> 🟢 **WS-27am BUILT 2026-08-11 (NARROWED), merged onto `claude/paca-research-task-management-a1f6zd`, NOT merged to `main`
> and NOT deployed** (§11.29) — §9.4.2's item 3 in full plus item 1 as a primitive.
> **The tree had no error boundary anywhere** (zero `componentDidCatch`, no `error.tsx`), so
> one malformed group shape blanked the whole app: `src/components/LayoutBoundary.tsx` now
> wraps `/projects`' canvas region, keyed by layout and project, and its **Retry re-mounts by
> bumping a key** — arithmetic kept pure in `src/lib/layoutBoundary.ts` because vitest here is
> node-env and cannot render. `EmptyState` gains the triad's **third arm**: a CTA drawn
> **disabled rather than hidden** (`disabled`/`disabledReason` + `emptyStateCopy`'s
> `canCreate`), all optional, defaults unchanged, **wired at no call site** — the three list
> surfaces were held open by sibling slices and an additive prop needs no edit there.
> 🔴 **§9.4.2's item 2 (the loader/empty/error HOC) is STRUCK, not built**: "one HOC per
> layout" names no layouts and reads tree-wide, so it cannot be closed as written — a doc
> blocker, not a build. Frontend only — no migration, no API change. ⚠️ Two claims are
> **review-only and not fenced** ("a malformed shape must not blank the app", "Retry
> re-mounts rather than re-crashing"): both need a render with a throwing child, and adding a
> DOM substrate to this runner is a decision above this ticket's pay grade. Four-theme sweep
> owed. ·
> 🟢 **WS-27bd (NARROWED to items 5 and 2) BUILT 2026-08-11, merged onto
> `claude/paca-research-task-management-a1f6zd`, NOT merged to `main` and NOT deployed**
> (§11.30) — the right-click
> menu arrives on `/projects`' cards as a **promotion, not a build**: the working generic menu
> at `app/tasks/components/ContextMenu.tsx` moves to `src/components/ContextMenu.tsx` behind a
> re-export shim (/tasks' five call sites unedited), and its items come from a new declared
> registry `lib/taskMenu.ts` read by **both** `TaskBoard` and `MyWork`. `TaskCardShell` is
> **unchanged** — it has accepted `onContextMenu` since S1 and /projects simply never passed
> it. Item 2 lands as `lib/rowState.ts`, a pure `pending: Set` / `errors: Map` reducer wired
> into `RelationsBlock`'s unlink rows. Frontend only — no migration, no API change, no new
> prop from `page.tsx`. **Three of the five items were STRUCK before any code**: (1) the
> shortcut registry is a third keyboard seam, not a papercut, and nothing binds `Mod+F`
> anyway; (3) clipboard-failure-never-claims-success is **already true** at all eight
> `writeText` sites; (4) no dismissible banner with a persist-forever key exists. ⚠️ **Cards
> only** — the row half is deferred, and §9.5.2's "reading the same action registry the
> palette already uses" is **corrected in §11.30**: `lib/commands.ts` is the page registry and
> holds no task-scoped action to resolve to, so the two registries are held **disjoint** by
> test instead. ⚠️ Fences mutation-measured (22/22); the pointer/Escape behaviour is
> **review-only** (node test env, no DOM) and the four-theme sweep is owed. ·
> **Owner:** vjvarada · **Board row: WS-27**
>
> **Tenancy (audited 2026-08-10 — this spec previously cited no tenancy decision at all).**
> The canonical architecture is **D15** — tenant = `organization_id`, enforced by Postgres
> FORCE ROW LEVEL SECURITY bound at the `get_db()` seam (`project-docs/specs/saas_multitenancy.md`;
> its `_implementation.md` holds the shapes). **Cite D15, never `multi_tenancy.md`'s D-MT-*,**
> which is the earlier narrower record and carries a superseded-for-architecture banner.
> All **19** `pm_*` tables are tenant-keyed (17 by migration 161, `pm_intake`/`pm_task_watchers`
> at CREATE time) and `routes/projects/` is clean against R5: one shared seam, no Redis, no
> route reads a tenant from request input. **Two known non-mechanical conversion sites are
> recorded where the tenancy agents will see them** — `core.resolve_organization_id` (handover
> H2) and `automation.run_lifecycle_sweep` (MT-1d). Do not "fix" either from this spec.
>
> **Commercially (D23/D24):** Projects is **not** an a-la-carte module. It rides inside
> **every Center package** as one of the base cross-cutting slices (with Knowledge Base and
> Dashboards); the paid Projects surfaces are what a Center package buys, and the
> `project_management_app` feature slug is gated by the entitlement seam, never sold alone
> (`saas_multitenancy.md` §2.4b).
>
> **Verified 2026-08-06:** 140 hermetic cases across
> `test_projects_{routes,grants,migration,import_mapping}.py` (no DB, no ClickUp, no LLM),
> plus the unchanged org-access and CRM fences — 298 passed on the combined set.
> Frontend: **315 vitest cases** and `tsc --noEmit` clean.
> **Fifteen mutants measured red and reverted byte-identical:** WS-27a's five (unscoped
> visibility clause, dropped assignee escape, transition skipping its activity,
> `completed_at` never cleared on reopen, removed Epic-root rule), WS-27b's four
> (applying the suggestion instead of the confirmed mapping, refusing to import an unmapped
> Space, a plan that writes, and a re-import that duplicates), and WS-27d's six (an unknown
> Center yielding an empty forest, `planDrop` never materialising, a board drop hard-coding
> status, unpositioned tasks sorting to the top, a missing nav pane, a Center linking at a
> forked route). **WS-27e adds six more:** an overlay keyed by task rather than per member,
> a personal-only completion that leaves the board behind, `is_triaged` always true, an
> inbox that drops its personal-project arm, a disposition filter matching only the stored
> value, and a tickler that ignores `defer_until`. ⚠️ Two of those first **survived** and
> the fake was at fault, not the tests — it applied the inbox's arms unconditionally instead
> of keying them off the statement, the exact mirror failure `_projects_fakes.py`'s own
> docstring warns about. Found by mutation, not by review.
>
> **Not built, on purpose:** no sync (WS-27c — blocked on BO-1a/BO-1b), no automation or
> agent dispatch (WS-27f), no `gtd_items` retirement (WS-27h), and
> `schema.generated.sql` was NOT regenerated — it needs a migrated live DB and is stale
> repo-wide, so it stays an owner-run chore (the WS-26a precedent).
>
> **Not in WS-27a, on purpose:** sprints, custom fields, time tracking, a docs/wiki surface,
> and the ACP-style "hand a task to the owner's local coding CLI" — all recorded as non-goals
> or later phases in §1, so their absence is a decision, not an omission.
>
> **Research provenance (2026-08-05):**
> - `Paca-AI/paca` @ master (v0.11.0) — **Apache-2.0: patterns adopted, no code translated**
>   (stack mismatch). Full findings + adopt/adapt/refuse verdicts:
>   `specs/paca_pm_research_2026-08.md` (reference-only; this spec owns all work).
> - Metorite full-tree sweep — every ClickUp touchpoint, `gtd_*` anchor, and Centers
>   convention cited below was verified in-tree on the date above.

---

## 1. Product vision and scope

**Who this is for:** all of Fracktal Works. Today the company's work lives in ClickUp
(departments as Spaces, projects as Folders/Lists, tasks/subtasks) and Metorite's
`/tasks` app is a *personal* GTD lens over it. This spec adds the missing middle: a native,
org-level project-management system — **departments → projects → subprojects → tasks →
subtasks**, ClickUp/Paca-grade — that lives in the People Center and projects scoped slices
into every other Center.

**What it replaces:** ClickUp. Today ClickUp is the system of record (root `AGENTS.md`
constraint 8) and Metorite holds two mirrors of it (§2). The native Projects app
inverts that in stages: **first two-way coexistence sync, then Metorite becomes the
system of record, then ClickUp is retired.** The inversion is deliberate and staged in §7 —
a reviewer should read it as the same import-and-retire move WS-26 made for Zoho, with the
extra middle phase two-way sync demands.

**What "done" means (end state, WS-27g):**
1. Departments, projects, subprojects, tasks, and subtasks live in `pm_*` tables with a
   working UI (project tree + list + board + task panel + activity timeline) at `/projects`.
2. The People Center shows the whole portfolio; every other Center sees exactly the
   projects granted to its `org_group` — **(app + scope) projections per
   `department_centers.md` §1 rule 2, never forks.**
3. Every member's personal `/tasks` app surfaces the `pm_tasks` assigned to them, with
   their GTD overlay intact (§6.1) — the org board and the personal system are two lenses
   on one fact.
4. Task events drive the existing `/workflows` automation app, and assigning a task to
   `agent:<name>` dispatches a real agent run whose progress is visible on the task (§6.3,
   §6.4).
5. All ClickUp data is imported with provenance (`clickup_id`), counts verified; the sync,
   both ClickUp code paths, their webhook, cron, and credentials are retired (§7.4).

**Non-goals (v1 — record departures here per `user_management_contract.md` §7):**
- **Sprints.** Paca has them; we don't run Scrum. The schema leaves room (nothing blocks a
  later `pm_sprints` + a `sprint` view context); no table now.
- ~~**Custom fields.**~~ Paca's `field_key`→JSONB pattern was the recorded additive path,
  and **WS-27l took it 2026-08-07** (§11.9). The departure from the recorded plan is one
  line: deleting a definition also strips its values, which Paca does not do.
- ~~**Tags as a first-class registry.**~~ v1 was `tags TEXT[]` on tasks (searchable, no
  colors) and the registry was named as additive later; **WS-27m added it 2026-08-07**
  (§11.10). The array stayed — the registry sits beside it, not instead of it.
- **Time tracking, docs/wiki, dashboards-in-app.** Notes and the Center dashboards
  (WS-15) own those concerns; the Projects app binds, never rebuilds (§6).
- **A second automation engine.** ADR-028/D6: `/workflows` is the only engine; WS-27
  contributes events and node types to it (§6.3).
- **A second realtime stack.** The Control Plane's existing polling/SSE conventions apply;
  no Socket.IO sibling.
- **ClickUp *feature* parity.** Whiteboards, chat, goals, forms — out. The retirement bar
  is "our work-management needs", not "ClickUp's feature list".

---

## 2. Current state — the two ClickUp systems and the personal store, measured 2026-08-05

**ClickUp integration exists twice, independently, and neither is an org-level PM store.**

| What | Where |
|---|---|
| **System A — Phase-0 graph mirror** (read-only, shallow: comments/subtasks/custom fields ignored) | `apps/services/ingestion/ingestion/sources/clickup/{client,normaliser,webhook}.py` → `task`/`project`/`person` rows in `acb_graph` (`infra/postgres/01_schema.sql`). Webhook HMAC-verified, fail-closed; `taskDeleted` logged and skipped. Consumers: `orchestrator/sales_views.py`, `scripts/reconciler.py`, `acb_graph/resolver.py` |
| **System B — per-user Tasks-app connector** | `gateway/routes/tasks/providers.py` — `BaseTaskProvider` + `ClickUpProvider`; `task_accounts` (per-user, encrypted creds, `schema_cache`, `last_delta_token`); pull via `POST /tasks/sync`; push via `_broker_gate` |
| Personal store the connector fills | `gtd_projects` + `gtd_items` (`source 'LOCAL'\|'SYNCED'`, `provider_task_id`, `sync_state`, GTD overlay never clobbered on re-sync) — `infra/postgres/48_task_manager_gtd.sql` + ~20 extensions (59 subtasks, 60 spaces/folders, 91 assignees…) |
| The `/tasks` app over it | `gateway/routes/tasks/` — 21 modules, ~11.8k lines, ~68 endpoints behind `require_feature_router("tasks")`; **27 `user_id = :` predicates in `items.py`** (owner-scoped by design) |
| People substrate | `gtd_people` (+ resumes, `capability_embedding vector(1536)`); WS-24 N4: directory open, HR fields restricted |
| Centers scaffold | `lib/centers.ts` (People Center's five sub-apps all `status:"planned"`), `140_center_features.sql` + `141_seed_center_groups.sql`, `center.people` in `FEATURES` — **Centers gate navigation, not data** (migration 140's own header) |
| Broker chokepoint for ClickUp writes | `providers.py::_broker_gate` → `broker_handlers._WRITERS`. **BO-1a + BO-1b landed 2026-08-11**: all 6 gated actions have handlers (AST-derived fence), and `_push_pending_item` honours the pending marker (`sync_state='awaiting_approval'`). ⚠️ Still open — **BO-1d**: four *other* callers index the marker as a result (`accounts.py:335`/`:403`, `planning.py:377` → HTTP 500 under enforcement; `items.py:790` swallows a queued update). **That is what blocks the `ACTION_BROKER_ENFORCE` flip**, not BO-1a/BO-1b |

Consequences that shape this plan:
- **There is no org-level native store to extend in place.** `gtd_*` is per-user by
  construction (the 27 predicates are the measurement); an org PM store has opposite
  visibility semantics. That is the D-PM-1 question (§8).
- **A ClickUp write path already exists and already goes through the broker** — unlike
  WS-26, which faced a read-only mirror. Two-way sync therefore inherits **BO-1a and BO-1b
  as named prerequisites** (§9 WS-27c), not discoveries.
- **The two ClickUp systems retire on different schedules**: System B's ClickUp arm goes
  when personal mirroring repoints to `pm_*`; System A goes at WS-27g when the graph-mirror
  consumers repoint (the §6 repoint D-CRM-1 already owes has the same shape here).

---

## 3. Data model

All tables in one migration at the **next free number at build time** (R1 — resolve from
`infra/postgres/`, never from a spec). Idempotent per `infra/postgres/README.md`:
`CREATE TABLE IF NOT EXISTS`, `INSERT … ON CONFLICT DO NOTHING`, guarded `DO $$`. PKs
`UUID DEFAULT gen_random_uuid()`, timestamps `TIMESTAMPTZ DEFAULT now()`, indexes
`idx_<table>_<cols>`, new-status columns as CHECKs. `schema.generated.sql` is **not**
regenerated (owner-run chore, per the WS-26a precedent).

The spine is Paca's shape — two self-FKs for the whole hierarchy, statuses/types as data,
per-view fractional ordering, one activity spine — with Metorite's provenance columns
(`source`, `clickup_id`) and actor strings (`email` or `agent:<name>`).

### 3.1 `pm_projects` — departments, projects, and subprojects are one table
`id` · `name TEXT NOT NULL` · `description TEXT` · `parent_project_id UUID REFERENCES
pm_projects(id) ON DELETE CASCADE` (NULL = root; arbitrary depth, cycle-checked in code) ·
`task_prefix TEXT` (root projects only — human ids like `RND-42`) · `status TEXT NOT NULL
DEFAULT 'active' CHECK (status IN ('active','on_hold','done','archived'))` · `lead TEXT`
(email or `agent:<name>` — assignment, not ACL) · `position DOUBLE PRECISION` (sibling
order in the tree) · `source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN
('manual','import','agent'))` · `clickup_id TEXT UNIQUE` · `clickup_kind TEXT CHECK
(clickup_kind IN ('space','folder','list'))` (a Space, Folder, or List may each become a
project — the importer flattens ClickUp's container zoo into this one self-FK, §7.1) ·
`created_by TEXT NOT NULL` · `created_at` · `updated_at` · `archived_at TIMESTAMPTZ`.
Index: `parent_project_id`, `status`, `clickup_id`.

**A department is a root `pm_project` whose grant row names a Center's group** (§3.2). No
department table: the Center *is* the department (`department_centers.md` §1), and the
grant is what makes a subtree "belong" to it.

### 3.2 `pm_project_grants` — the scoping primitive (D12/D13's vocabulary, this store's table)
`id` · `project_id UUID NOT NULL REFERENCES pm_projects ON DELETE CASCADE` · `subject TEXT
NOT NULL` (**exactly the shipped vocabulary: `email` \| `group:<slug>` \| `org`** —
`tenancy_and_visibility.md` §3.2 is binding; do not invent a second one) · `created_by TEXT
NOT NULL` · `created_at` · `UNIQUE (project_id, subject)`.

Grants apply to the project **subtree** (a grant on a root project covers its
subprojects/tasks). Read model in §4. This is a *sibling* of WS-14 C1's `gtd_project_grant`
(D13) — same vocabulary, different store; C1 remains the personal Tasks app's ticket and is
unchanged by this spec.

### 3.3 `pm_task_statuses` — statuses as data (D-CRM-2 / Paca convergence)
Scoped to a **root** project (subtree inherits): `id` · `project_id NOT NULL REFERENCES
pm_projects ON DELETE CASCADE` · `name TEXT NOT NULL` · `color TEXT NOT NULL DEFAULT
'gray'` · `position INT NOT NULL` · `category TEXT NOT NULL CHECK (category IN
('backlog','todo','in_progress','done','cancelled'))` · `is_default BOOLEAN NOT NULL
DEFAULT false` · `UNIQUE (project_id, name)`. The `category` is the machine-readable
semantic: `done`/`cancelled` drive completion, mirror-to-personal disposition (§6.1), and
the automation `predecessor_done`-style gates; name/color/position are free, so the
importer can represent ClickUp's actual per-list status names (the D-CRM-2 argument,
verbatim). Root-project creation seeds Backlog/To do/In progress/Done.

### 3.4 `pm_task_types` — types are semantics, hierarchy is structure
`id` · `project_id NOT NULL REFERENCES pm_projects ON DELETE CASCADE` (root-scoped) ·
`name TEXT NOT NULL` · `icon TEXT` · `color TEXT` · `is_system BOOLEAN NOT NULL DEFAULT
false` · `UNIQUE (project_id, name)`. Seeded per root project: Task (default), Bug,
**Epic (`is_system`)**. Paca's two hard rules adopted: an Epic-typed task cannot have a
parent, and Paca demoted "Subtask" from system type to convention — a subtask is just a
task with a parent, so we never mint a Subtask type at all.

### 3.5 `pm_task_counters` + `pm_tasks`
`pm_task_counters(project_id UUID PRIMARY KEY REFERENCES pm_projects ON DELETE CASCADE,
last_value BIGINT NOT NULL DEFAULT 0)` — root projects only; atomic
`UPDATE … SET last_value = last_value + 1 RETURNING`.

`pm_tasks`: `id` · `project_id NOT NULL REFERENCES pm_projects ON DELETE CASCADE` (may be
any node in the tree) · `root_project_id UUID NOT NULL REFERENCES pm_projects ON DELETE
CASCADE` (denormalized for the counter, status/type scope, and subtree reads; maintained by
code, re-stamped on move) · `task_number BIGINT NOT NULL` · `UNIQUE (root_project_id,
task_number)` · `parent_task_id UUID REFERENCES pm_tasks(id) ON DELETE SET NULL`
(subtasks, arbitrary depth; Paca's depth-50 ancestor cycle walk at write time) ·
`type_id UUID REFERENCES pm_task_types ON DELETE SET NULL` · `status_id UUID NOT NULL
REFERENCES pm_task_statuses ON DELETE RESTRICT` · `title TEXT NOT NULL` · `description
TEXT` (Markdown — the platform's format; no block JSON) · `importance SMALLINT`
(higher = more urgent; bucketed in UI) · `estimate_mins INT` · `start_date DATE` ·
`due_at TIMESTAMPTZ` · `completed_at TIMESTAMPTZ` (stamped when status crosses into
`done`) · `tags TEXT[] NOT NULL DEFAULT '{}'` (GIN) · `created_by TEXT NOT NULL` ·
`source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN
('manual','import','email','agent','automation'))` · `clickup_id TEXT UNIQUE` ·
`clickup_snapshot JSONB` (last-synced provider field state — the three-way-merge base,
§7.2) · `clickup_synced_at TIMESTAMPTZ` · `created_at` · `updated_at` · `archived_at`.
Index: `(project_id)`, `(root_project_id, status_id)`, `parent_task_id`, `due_at`,
`clickup_id`, GIN on `tags`, FTS GIN on `title || description`.

### 3.6 `pm_task_assignees` — humans and agents, one vocabulary
`task_id UUID NOT NULL REFERENCES pm_tasks ON DELETE CASCADE` · `assignee TEXT NOT NULL`
(email, case-folded on write, or `agent:<name>`) · `assigned_by TEXT NOT NULL` ·
`assigned_at` · `PRIMARY KEY (task_id, assignee)`. Index: `lower(assignee)` — the personal
lens (§6.1) and "assigned to me" predicates read `lower(assignee) = :email` (R10). Paca's
member-row indirection is refused (D-PM-4): the platform's actor convention is already the
string vocabulary, and a join table to `app_user` would exclude agents.

### 3.7 `pm_task_links`
`id` · `source_task_id` · `target_task_id` (both `NOT NULL REFERENCES pm_tasks ON DELETE
CASCADE`) · `link_type TEXT NOT NULL CHECK (link_type IN
('blocks','relates_to','duplicates'))` · `created_by TEXT NOT NULL` ·
`UNIQUE (source_task_id, target_task_id, link_type)` · `CHECK (source_task_id <>
target_task_id)`.

### 3.8 `pm_activities` — the single timeline spine (comments = activities)
`id` · `task_id UUID REFERENCES pm_tasks ON DELETE CASCADE` · `project_id UUID REFERENCES
pm_projects ON DELETE CASCADE` · **CHECK: at least one target non-NULL** (the
`crm_activities` move) · `type TEXT NOT NULL CHECK (type IN
('comment','status_change','field_change','link','assignment','agent_run','sync',
'system'))` · `body TEXT` · `meta JSONB` (`field_change` carries
`{"changes":[{field,old,new}]}` — the Paca diff-and-revert shape; `agent_run` carries
`{run_id, agent}`; `sync` carries the conflict record, §7.2) · `created_by TEXT NOT NULL`
(email, `agent:<name>`, or `system:sync` / `system:workflow:<id>`) · `created_at` ·
`updated_at` · `deleted_at` (comments only). Index: `(task_id, created_at)`,
`(project_id, created_at)`.

One transition, three effects (the CRM's `apply_status_transition` lesson): a status PATCH
writes the new `status_id`, a `status_change` activity, and `completed_at` when crossing
into/out of `done` — one helper, called by every mutator including sync and automation.

### 3.9 `pm_views` + `pm_view_task_positions` — saved views and manual order
`pm_views`: `id` · `project_id NOT NULL REFERENCES pm_projects ON DELETE CASCADE` ·
`name TEXT NOT NULL` · `view_type TEXT NOT NULL CHECK (view_type IN ('list','board'))` ·
`config JSONB NOT NULL DEFAULT '{}'` (**presentation top-level** — `fields`, `column_by`,
`sort_by`, `swimlanes`; **query constraints under `config.filters`** — status/assignee/
type/tag arrays + `include_subtree BOOLEAN`) · `position DOUBLE PRECISION` · `created_by` ·
timestamps. Root-project creation seeds one List and one Board (`column_by: "status"`).

`pm_view_task_positions`: `id` · `view_id NOT NULL REFERENCES pm_views ON DELETE CASCADE` ·
`task_id NOT NULL REFERENCES pm_tasks ON DELETE CASCADE` · `position DOUBLE PRECISION NOT
NULL` · `group_key TEXT` · `UNIQUE (view_id, task_id)`. Fractional indexing exactly per
`paca_pm_research_2026-08.md` §2.4: no rank column on `pm_tasks`; unpositioned tasks sort
by `created_at`; the first drag materialises the group. **This is what lets the People
Center master board and a Sales-slice board order the same task differently without
fighting.**

### 3.10 What is deliberately absent
No `pm_sprints`, no `pm_custom_field_definitions`, no attachments table (bind `/tasks`'s
`gtd_attachments` pattern later or reuse the email/notes storage seam — additive), no
notifications table (the platform inbox owns notification concerns), no closure table, no
`position` on `pm_tasks`.

---

## 4. API surface

Layout mirrors `routes/crm/`: a `routes/projects/` package where `core.py` is the leaf
(router + entity registry + models + SQL helpers) and feature modules register routes on
the shared router as an import side effect; every path is a literal, so import order is not
load-bearing. Registered in `main.py` with the standard fail-soft `try/except`, listed in
`tests/unit/test_org_access_enforcement.py::GATED_ROUTERS`.

```python
router = APIRouter(
    prefix="/projects", tags=["projects"],
    dependencies=[require_feature_router("projects")],
)
from gateway.db import get_db as _get_db  # the shared engine seam (BO-10 / D-CRM-4)
```

**The engine seam is non-negotiable:** `routes/projects` contains **zero**
`create_async_engine` calls; it consumes `gateway/db.py` (the seam WS-26a built and proved
on tasks). No engine 13.

| Module | Endpoints |
|---|---|
| `tree.py` | `GET /projects/tree` (the granted forest, nested) · `GET/POST /projects/nodes` · `GET/PATCH/DELETE /projects/nodes/{id}` · `POST /projects/nodes/{id}/move` · grants: `GET/POST/DELETE /projects/nodes/{id}/grants` |
| `tasks.py` | `GET/POST /projects/tasks` (list contract: allowlisted sorts, `?q=` FTS, filters incl. `project_id` + `include_subtree`, keyset pagination) · `GET/PATCH/DELETE /projects/tasks/{id}` · `POST /projects/tasks/{id}/move` · assignees `PUT/DELETE` · links `POST/DELETE` |
| `activities.py` | `GET /projects/tasks/{id}/timeline` · `POST /projects/tasks/{id}/comments` · `PATCH/DELETE /projects/comments/{id}` · `POST /projects/activities/{id}/revert` (field_change only) |
| `admin.py` | statuses + types CRUD per root project (`RESTRICT` delete answers 409 naming the count in use) |
| `views.py` | views CRUD · `PUT /projects/views/{id}/positions` (bulk upsert) |
| `me.py` | `GET /projects/assigned-to-me` — the flat "what is mine" read |
| `personal.py` (WS-27e) | `GET /projects/my/inbox` · `GET/POST /projects/my/project` · `POST /projects/my/tasks` · `PATCH /projects/tasks/{id}/personal` · `POST /projects/tasks/{id}/{complete,defer}` · `GET /projects/my/contexts` |
| `mapping.py` (WS-27b) | no routes — the three suggestion signals and their combination, kept apart from the importer because a proposal and an application are different acts (D-PM-10) |
| `import_clickup.py` (WS-27b) | `POST /projects/import/clickup/plan` (proposes a Center per Space, writes nothing) · `POST /projects/import/clickup` (applies the confirmed mapping) |
| `sync.py` (WS-27c) | `POST /projects/sync` · `GET /projects/sync/status` · `GET /projects/sync/conflicts` |

Patterns carried from `routes/crm/core.py`: the frozen `Entity` registry dict (segment
matched against it, never interpolated), sort keys as an allowlist (unknown = 422), typed
JSONB/timestamptz binds, two models per entity (output = column names 1:1; one all-optional
input for POST+PATCH with create-time requirements on the registry).

**Read model (D-PM-3):** a project (and its subtree) is visible to a caller when a grant
row on it or an ancestor matches `org`, `group:<slug>` for a group the caller belongs to,
or the caller's email (case-insensitive) — **or** the caller is assigned to the specific
task ("assigned-to-me always sees its own tasks"). Non-visible ⇒ **404, never 403** (R5).
Full-portfolio view (the People Center's "all departments") additionally requires
**`data:org:read`** — the slug D14 measured at zero consumers; this is deliberately its
first consumer, making `manager`'s org-wide visibility a mechanism instead of a name.
Writes: task-level writes for any caller who can see the project; project/status/type/grant
admin for the project's `lead`, `created_by`, or `admin:members:manage` holders.

**Rules that bind** (`user_management_contract.md`): identity from `X-User-Email` only
(R3); no `PUBLIC_ROUTES` additions — the BFF proxies everything (R2); server-side checks
first (R9); email comparisons case-insensitive both sides (R10); destructive deletes report
what cascaded (R7/R8) — deleting a project names the subtree/task/activity counts.

**Events:** every mutation emits `pm.task.created|updated|status_changed|assigned|
comment_added` and `pm.project.*` through `ingestion/event_hooks.emit_event` — the same
path ClickUp webhooks use today, which is what makes §6.3's automation binding one seam
instead of a new bus.

---

## 5. UI

```
workbench/control_plane/src/app/projects/
  page.tsx                      # tree sidebar + active view (list | board)
  components/*.tsx              # ProjectTree, TaskListView, TaskBoardView, TaskPanel,
                                #   TimelinePane, ViewSettings, StatusAdmin
  lib/*.ts + *.test.ts          # pure helpers: fractional positions, grouping, filters
src/app/api/projects/[...path]/route.ts   # BFF proxy (gatewayHeaders(), force-dynamic,
                                          #   AbortSignal.timeout, byte-exact passthrough)
```

**Registration — the five-place checklist does NOT apply** (this is an app, not a Center),
but the app-slug half does, and it is both-ways invariant-tested:
1. `acb_auth.permissions.FEATURES` gains `"projects"` (beside `"tasks"`).
2. `feature_catalog` row in the WS-27a migration: `('projects','Projects','Departments,
   projects and team tasks','/projects','apps', 56, false)` — **`is_default false`**, the
   D-CRM-3 posture: reaches `*`-holders and `admin` until an admin grants it.
3. `nav.ts` `PANES` entry + `access.ts` `HREF_FEATURES` `/projects → projects`.
4. `tests/unit/test_org_access_control.py` — the existing catalog↔FEATURES both-ways
   invariants pick the slug up automatically; add the named
   `test_projects_is_registered_on_both_sides` per the WS-26a precedent.

**Center projections (the (app + scope) rule, `department_centers.md` §1 rule 2):**
- `lib/centers.ts` People Center: flip a sub-app to
  `{label: "Projects & work", status: "live", href: "/projects"}`.
- Every other Center gains/updates a sub-app `{status: "live", href:
  "/projects?center=<slug>"}`. The query param is **presentation only** — it pre-filters
  the tree to projects granted `group:<slug>`; the server's grant model is what actually
  scopes data, so a hand-edited URL shows nothing the caller couldn't already see (R9).
- **Refuse any per-Center fork of the app in review.**

Conventions: Tailwind v4 semantic tokens only; Lucide icon names as strings; zustand +
pure `lib/` helpers with colocated vitest; drag-drop writes one `pm_view_task_positions`
upsert per drop (the board's cross-column drag patches whatever field `column_by` names).

---

### 5.1 The tree grammar — space, folder, project, subproject

**Owner directive, 2026-08-31.** Migrations 193 and 194. The tree had one
node type and no depth limit. Real trees grew five levels of identical rows,
and no row showed what it was.

The grammar is now:

```
space (root) -> [folder] -> project -> [folder] -> subproject
```

**Projects count toward the depth. Folders do not.** A space is generation 1,
a project is 2, and a subproject is 3. Three is the limit. A folder groups
the nodes below it. A folder does not nest, it holds no tasks, and it is
never a root.

`pm_projects.kind` holds `project` or `folder`. NULL reads as `project`
(R6). The grammar itself is one pure function,
`core.assert_node_grammar`. It is not a CHECK constraint, because the rules
must read the parent chain, and a CHECK cannot walk one. Create and move
both call it. A PATCH cannot change a kind: a folder that became a project
would move past every rule.

**A LEVEL is derived, and never stored.** `core.node_level` reads the kind
and the generation. A stored level and the tree can disagree, and the tree
is the fact.

**What each level does:**

| Level | Shows | Run state | Icon | Holds tasks |
|---|---|---|---|---|
| space | a dashboard of the whole subtree | no | its own | no |
| folder | a dashboard of the projects below | no | folder glyph | no |
| project | its views, with the subtree folded in | yes | run-state dot | yes |
| subproject | its own views | yes | run-state dot | yes |

**A space is not a project.** It shows a roll-up, and it has none of a
project's views. A folder does the same for the projects below it. A
project that holds subprojects keeps its views and adds the subtree to
them. To see one subproject alone, click that subproject.

**Only a project or a subproject has a run state.** A space summarises and a
folder groups. Neither does work, so neither starts, pauses or stops.
`core.assert_run_state_allowed` refuses the write. The menu also hides the
control, but the refusal is the fence.

**One endpoint feeds every roll-up.** `GET /projects/nodes/{id}/summary`
counts the subtree in two grouped queries. It gives the totals, the counts
for each category, the overdue count, and one line for each direct child.
Every count passes through the caller's own task-visibility clause. A
roll-up that added rows the reader cannot open is a disclosure channel in a
summary's clothes.

**Space Settings** changes the name, the icon and the icon colour. Open it
with a right-click on the space. `pm_projects.icon` holds a themed icon
NAME, and `pm_projects.icon_slot` holds a slot from 1 to 8 on the
categorical ramp. Neither column holds a colour. The theme decides which
pack draws the glyph, and what the hue is in light mode and dark mode. A
hex value in these columns is the one thing `DESIGN_SYSTEM.md` rule 1
refuses, and a later re-theme cannot reach it.

🔭 **FUTURE — a space belongs to a team.** When departments, teams and
groups exist, a space gets an owning team. The space then appears in that
team's Center. The seam is already here: a `pm_project_grants` row with a
`group:<slug>` subject is how a Center gets its slice today (D12). So the
future column names an owner. It is not a second grant mechanism, and it is
not a second dialog — it is a third field in Space Settings.

**Fences:** `tests/unit/test_projects_node_kind.py` and
`tests/unit/test_projects_space_identity.py` read each CHECK out of its
migration, then exercise every refusal.
`workbench/control_plane/src/app/projects/lib/tree.test.ts` pins the UI
half: `childCreationOptions`, `levelOf`, `hasRunState`, `showsDashboard` and
`spaceMarker`. It also asserts that every icon the picker offers is present
in the themed registry.

#### 5.1a Amendments of 2026-08-31 — owner directives, patterned on Plane

The owner asked for six changes on 2026-08-31. Each shape below follows a
named surface in Plane (github.com/makeplane/plane at commit `effd0c5`).
The colours and controls stay ours: every hue resolves through
`statusAccent` or the categorical ramp, never a hex value.

**The ramp holds twelve slots, and the hash uses eight.** Migration 195
widens the `icon_slot` CHECK to 1..12. Slots 9 to 12 are for an explicit
choice only. `hashSlot` keeps its modulus at 8, so no hash-assigned
context, tag or label repaints. The fence is "never lands outside the HASH
range" in `categorical.test.ts`. The icon picker offers 64 names, and
`tree.test.ts` checks every name against the themed registry.

**A project can show the dashboard beside its views.** `overview` joined
the view modes. It draws the same `NodeDashboard` a space shows, from the
same summary endpoint. It is never the default canvas. The filter bar, the
composer, the bulk bar and the triage rail hide on it.

**Analytics is a KPI strip over a per-space table.** One row for each
space, one column for each lane, an overdue column, and a totals footer.
The table reads the same `/summary` endpoint as the dashboards, so the two
surfaces cannot disagree. Plane's created-vs-resolved chart needs a time
series the endpoint does not carry, so `AnalyticsView.tsx` records it as
future work instead of drawing invented data.

**"My work" left the Projects sidebar.** `/tasks` is the personal lens
over the one store (D52 to D54). A second door inside Projects taught the
split that the D-PM-6 revision removed. The `my/*` gateway routes stay,
and `/tasks` serves them.

**The header holds view controls and one overflow menu.** Custom fields,
Tags and Lifecycle open from the overflow menu, not from three buttons
beside the view switcher. The composer bar shows only on the timeline
canvas, which has no in-place capture, and when the assign-work pre-fill
needs a visible place to land. Every other canvas captures in place
through `QuickAdd`, which inherits the group it sits in.

---

### 5.x The standalone app groups by Center (D22 amendment, 2026-08-10)

`department_centers.md` §5's dual-access rule names this app its first consumer:
the top-level `/projects` surface's primary grouping is **by Center** — the
caller sees each Center they hold access to (D12 membership or `group:` grant),
containing that Center's projects and tasks; org-tier holders see all Centers in
the same layout; a Center the caller cannot access never renders as a group
header. This is an IA requirement on the portfolio/list views, not a new
permission model — visibility resolves through the same grants as everything
else. Carry into the acceptance of whichever remaining letter first touches the
portfolio/grouping views.

## 6. Integrations — bind, don't rebuild

### 6.1 Personal tasks (`/tasks`) — the org↔personal seam this spec exists for
The requirement: a `pm_task` assigned to a member appears in their personal GTD system, and
completing it in either place is one fact. **Since 2026-08-06 that is true by construction
rather than by synchronisation** — there is one row, and the personal view is a lens over
it (D-PM-6 revised). What follows describes the superseded mirror; it is kept for the
reader who needs to know what was rejected. ~~Mechanism is the Tasks app's
existing provider machinery mirrors `pm_tasks` where `lower(assignee) = user` into
`gtd_items` as `source='SYNCED'` rows (internal provider `metorite`, no credentials,
no broker gate — it is not an outward write). The GTD overlay (disposition, context,
energy, refile) is **never clobbered on re-sync** — the discipline `sync.py` already
enforces for ClickUp rows; completion state flows both ways (provider-of-record =
`pm_tasks`). The GTD lens maps `category` → disposition exactly as the ClickUp lens maps
stages today (done→DONE, backlog→SOMEDAY, assigned-to-me→NEXT, assigned-elsewhere→WAITING
with a `gtd_waiting` row). Result: clarify, calendar/timeboxing, Waiting-For, and delegation
all work on org tasks with **zero** changes to their code.

### 6.2 People (`gtd_people`) — assignment intelligence
Assignee pickers and the delegate flow read the existing directory + capability layer
(`fetch_people_for_clarify`, `capability_embedding`) — suggestion, never auto-assignment.
WS-24 N4's HR-field projection applies unchanged; the Projects app reads only directory
fields. The People Center's "Directory & org chart" sub-app remains WS-13's read view —
this spec does not build a parallel people store.

### 6.3 Workflows (`/workflows`) — automation, one engine
WS-27f emits `pm.*` events into `event_hooks.emit_event` → `workflows/triggers.
dispatch_event` (the path that already exists) and contributes **two node types**: a
`pm.update_task` action (multi-field patch through the ordinary service, so it gets
validation + a `field_change` activity with `created_by='system:workflow:<id>'` — Paca's
"mutate through the ordinary service" rule) and a `pm.task_event` trigger config (project/
status/assignee filters). Paca-grade engine uplifts — multi-branch switch conditions,
per-step input/output snapshots, due-date-offset triggers, the derived dependency map —
are **`workflows_app.md` backlog items** (single owner, D6); this spec records the demand
and stops.

**Written down 2026-08-06 — `workflows_app.md` §13.** The demand is no longer only recorded
here as a sentence: the engine spec now carries a full Paca-referenced uplift backlog,
**U1–U8**, each with the Paca design, this engine's measured current state, and a done-when.
The mapping from this section is exact: **U1 is the `pm.update_task` node** (WS-27f's first
half) and **U7 is agent dispatch** (§6.4, WS-27f's second half); U2/U3/U6 are the switch,
step snapshots and due-date trigger named above; **U4** (task retargeting over
`parent|children|blocks|…`) is the item this section had not named and is what makes "when
every child is Done, move the parent to Done" expressible at all. Nothing in §13 is built —
it is the reference an implementer picks up, so WS-27f no longer has to re-derive the engine
work from Paca's source.

### 6.4 Agents — assignment is dispatch
Assigning `agent:<name>` (WS-27f): the `pm.task.assigned` event carries the agent target; a
consumer creates the run through the existing orchestrator dispatch (the same seam chat
delegation uses), records an `agent_run` activity on the task immediately (Paca's
`agent.session.started` move — the handoff is visible in the timeline before the agent
says anything), and the agent works the task through a `skill-projects` tool family over
this API under its own `agent:<name>` identity — permission-intersected with the acting
member via `EffectiveAccess.intersect()`. Run completion/failure posts a closing activity.
**Agent edits are ordinary edits (D-PM-9):** during coexistence an agent may work linked
tasks as well as native ones, and its changes reach ClickUp through the WS-27c sync
chokepoint on exactly the same terms as the owner's own — auto-applying while
`ACTION_BROKER_ENFORCE` is off, attributable and timeline-reversible either way. Read
D-PM-9's Cost paragraph before building this: it names what that does and does not
guarantee.

### 6.5 Email / WhatsApp / Notes
Bind at the activity spine: email-to-task capture (`capture_email.py`) gains a `pm_tasks`
target beside `gtd_items`; Notes' action-item HITL (`actions.py`) gains "create as project
task". Both are thin: one insert path each, reusing §3.8's helper. Deeper linking
(`entity_ref`-style) follows the WS-26d pattern later.

### 6.6 The graph mirror (`acb_graph`)
`project`/`task` mirror rows keep flowing from ClickUp untouched until WS-27g, which
repoints the consumers (`reconciler.py`'s quiet-deal escalation reads deals, not tasks —
the task-side consumers are `resolver.py` and any org-brain queries) to `pm_*`, then
retires System A's ClickUp arm.

---

## 7. The migration path — coexistence, inversion, retirement

**Constraint-8 amendment, stated plainly:** root `AGENTS.md` #8 ("source systems are
authoritative") holds through WS-27a–c with ClickUp as the PM source of truth. WS-27g
inverts it **for project management only** — Metorite becomes the system of record and
ClickUp is retired — the same recorded inversion `crm_app.md` §1 made for Zoho. The
amendment lands in root `AGENTS.md` in the WS-27g PR, not before.

### 7.1 Import (WS-27b) — plan, then apply

**Step 1 — the mapping plan (D-PM-10).** `POST /projects/import/clickup/plan` reads the
workspace and returns one row per Space: the Space, its task/subtask counts, a **suggested
Center**, a confidence, and the evidence behind the suggestion. Three signals, cheapest and
most reliable first:

1. **Assignee overlap** — the share of the Space's task assignees who are members of each
   `org_group`. Deterministic, no LLM, and the strongest signal the platform already holds.
2. **Name match** — the Space name against Center names, slugs, and their aliases.
3. **Content classification** — a sampled set of task/subtask titles classified through
   `acb_llm`'s tiered routing. **EVAL-LOCKED**, like `routes/tasks/ai.py::propose`.

The plan is **pre-filled from existing grants** on re-run, so a mapping the owner has
already confirmed is stable and a re-import can never silently re-map a Space. Suggestions
are proposals: nothing is applied from this endpoint.

**Step 2 — the import.** The owner's confirmed mapping is passed to
`POST /projects/import/clickup`, which pulls with an owner-connected `task_accounts`
credential: Spaces → root projects, Folders → subprojects, Lists → subprojects (leaf
containers), tasks + subtasks → `pm_tasks` with `parent_task_id`, per-list statuses →
root-project `pm_task_statuses` (union by name, `category` mapped from ClickUp status type),
assignees → emails via the member map `schema_cache` already holds, everything stamped
`source='import'` + `clickup_id`. Each **mapped** Space's root project also gets a
`group:<slug>` grant — that grant is the entire mechanism by which the Space becomes a
Center's slice.

**Unmapped Spaces still import, in full.** They simply receive **no group grant**, which
leaves them reachable in `/projects` for `data:org:read` holders (the People Center's
full-portfolio view) and for anyone assigned to their tasks — so nothing is stranded or
invisible to the owner. Mapping one later is a grant write, never a re-import.

Re-runnable: upsert on `clickup_id`; **during coexistence a re-import is last-import-wins
on ClickUp-sourced fields only** (never on rows/fields the sync marks locally newer, §7.2).
Import summary reports per-entity counts; parity check = ClickUp count vs `pm_*` count per
Space.

### 7.2 Coexistence — two-way sync (WS-27c), the genuinely novel surface
Nothing in the repo does bidirectional reconciliation today; this is designed here, not
inherited:
- **Pull**: scheduled delta pull (`last_delta_token` discipline from `task_accounts`) +
  the existing ClickUp webhook fan-in, both landing on one upsert path.
- **Push**: local `pm_*` edits to ClickUp-linked rows queue as outbound mutations through
  **`_broker_gate`** — the single audited chokepoint (AGENTS.md #4). **Prerequisites,
  named:** **BO-1a** (register the missing `clickup.delete_task`/`archive_task` handlers)
  and **BO-1b** (honour the `pending` marker instead of writing `sync_state='synced'` with
  an empty id). Neither is optional; both are WS-1 tickets this spec depends on.
- **Merge**: three-way, field-level, using `clickup_snapshot` as the base: a field changed
  on only one side takes that side; changed on both sides ⇒ **newest-wins by timestamp,
  and the losing value is written to the timeline as a `sync` activity** (`meta` carries
  `{field, ours, theirs, taken}`) — a conflict is never silent and always recoverable via
  the timeline. Snapshot re-stamped after every successful reconcile.
- **Idempotency discipline** (Paca §9 of the research doc): every sync mutation checks
  "already in target state" first; re-delivery of a webhook or a re-run of a pull is a
  no-op.

### 7.3 Cutover (WS-27g, first half)
Final import + parity counts per Space · flip the sync to **pull-only mirror** (ClickUp
edits still land; pushes stop) · a soak window where the org works in `/projects` · then
stop the pull.

### 7.5 The `gtd_items` retirement (WS-27h) — the cost D-PM-6's revision accepted

One store means the old one goes. Sequenced **after** WS-27e (which is the destination) and
independent of the ClickUp work, because it is a move between two tables we own:

1. `/tasks` reads a **union** of `gtd_items` and `pm_tasks` during coexistence, so the app
   keeps working while rows move.
2. Every `gtd_items` row migrates: `LOCAL` rows into the owner's personal project; `SYNCED`
   rows onto their `pm_tasks` counterpart by `clickup_id`, with the GTD overlay landing in
   `pm_task_personal`. The disposition vocabulary is **unchanged on purpose** (§3.12), so
   this is a copy rather than a translation.
3. `items.py`'s 27 `user_id` predicates retire with the table they scope. They are untouched
   by WS-27e, deliberately — the blast radius WS-14 C1 measured belongs to this ticket.
4. `gtd_projects`, `gtd_spaces`, `gtd_folders` retire with it; `gtd_people` does **not** —
   that is the People Center's store (`specs/people_center_app.md`).

⚠️ **Not started, and it is the largest single piece of WS-27 remaining.** Until it lands
there are two personal task stores, which is the state this decision exists to end.

#### 7.5.1 The destination table — every column, named *(added 2026-08-10)*

> **Why this exists.** Step 2 above says "a copy rather than a translation." That sentence is
> true of the **seven** overlay columns `pm_task_personal` already mirrors, and false of the
> rest. `gtd_items` carries **thirty-one** columns plus a `gtd_waiting` side table; fifteen of
> them have no `pm_tasks` home at all. Executed as written, this migration would be a
> **feature deletion wearing the word "copy"** — the founder priority matrix, all of
> timeboxing, and the entire Waiting-For view would simply stop existing. Nobody would notice
> until the data was already gone, because the migration would report success.
>
> So the destination of every column is named here **before** the migration is written. A
> column that reaches the day of the move without a row in this table is a bug in this spec,
> not a judgement call for whoever happens to be executing.

**The governing question for each column is *whose fact is it?*** — because that decides the
table, and getting it wrong is what a mirror-shaped design does to you later:

* a **task** fact is true for everyone looking at the row → `pm_tasks`
* a **member** fact can differ between two people on the same task → `pm_task_personal`

The test that settles the hard cases: *if Ana and Ben are both on this task, can their answers
legitimately differ?* If yes, it is per-member. This is the same argument `147_projects_personal.sql`
already makes for `disposition` (one person is doing it, the other is waiting on it), applied
consistently rather than only where it was first noticed.

| `gtd_items` column | Destination | Why |
|---|---|---|
| `disposition`, `next_action`, `context`, `energy`, `time_estimate_mins`, `is_two_minute`, `defer_until`, `clarified_at` | **`pm_task_personal`** ✅ exists | The seven-plus-one already mirrored 1:1 by migration 147. This is the part that genuinely is a copy. |
| `title`, `description`, `due_at`, `completed_at`, `created_at`, `updated_at` | **`pm_tasks`** ✅ exists | Same name, same meaning. |
| `parent_item_id` | `pm_tasks.parent_task_id` ✅ | Same shape. |
| `archived_at` | `pm_tasks.archived_at` ✅ | Same shape. |
| `assignees` (JSONB, mig 91) | `pm_task_assignees` ✅ | Rows, not JSONB. `assignee` (the older singular, mig 48) folds into the same set. |
| `attachments` (JSONB, mig 52) | `pm_task_attachments` ✅ | Rows, not JSONB. |
| `sort_key` (mig 58) | `pm_view_task_positions` ✅ | D-PM-5: order is per view, not a column on the task. |
| `project_id` → `gtd_projects` | `pm_tasks.project_id` | `LOCAL` rows land in the owner's personal project (§3.11). |
| `workflow_stage` (mig 57) | `pm_tasks.status_id` | Free text → a real status row. The status→stage map already exists; the **colour** already agrees via `src/lib/statusAccent.ts`. |
| ~~**`important`**~~ | 🔴 **OVERRULED 2026-08-13 → NO new column at all (D-PM-28)** | The owner ruled importance is a property of the work, not a per-member judgement — so it is shared. And it **already exists**: `pm_tasks.importance` is a *four-level* scale, strictly richer than this boolean. Adding a boolean beside it would be the CLAUDE.md §5 defect. `/tasks` adopts the four-level control (D-PM-29 rule 4); `importance = 3` is relabelled "Urgent" → "Critical" so it stops making a time claim once urgency is derived. The argument this row used to carry — *"Ana may rate a shared task important and Ben may not"* — is kept struck rather than deleted, because it is the reasoning that was overruled, not a mistake nobody made. |
| **`leveraged`, `kept_mine`** (mig 68) | **`pm_task_personal`** — 🔴 **NEW columns** | The part of the founder matrix that stays personal (D-PM-28): a rare asymmetric upside is my read of a shared task, and `kept_mine` dismisses a suggestion shown only to me. |
| **`urgent_window_hours`** (mig 68) | **Org-level setting** — 🔴 not per-user, not per-task | Urgency is DERIVED and shared (D-PM-28), so a per-user window would have Ana and Ben seeing different urgency on the same task — the exact divergence the decision removes. |
| **`scheduled_start`, `scheduled_end`** (76), **`flexible`** (79), **`actual_start`, `actual_end`** (80) | **`pm_task_personal`** — 🔴 **NEW columns** | Timeboxing. *When I plan to do it* and *when I actually did* are mine; two people on one task book their own calendars. Note `pm_tasks.start_date` is a **different** fact (when the work starts, shared) and must not be conflated. |
| **`deep_work`** (mig 96) | **`pm_task_personal`** — 🔴 **NEW column** | A sibling of `energy`, which is already per-member. My concentration classification, not the task's. |
| `is_hard_date` | **`pm_tasks`** — 🔴 **NEW column** | The one calendar-shaped flag that is *not* personal: a deadline is either immovable or it is not, and that is true for everyone. |
| **`origin`** (JSONB, mig 65) | **`pm_tasks`** — 🔴 **NEW**, or the P-9 `(external_source, external_id)` pair | Email-capture provenance: where this task came from. A task fact. Prefer the generic provenance pair (`plane_pm_research_2026-08.md` P-9) over a second JSONB blob — one provenance vocabulary, not two. |
| `source`, `account_id`, `provider_task_id`, `provider_url`, `provider_status`, `sync_state` | **Retire with WS-27g**, not here | These are the ClickUp arm. They become meaningless when the provider retires, so they are a *legitimate* drop — **but only if WS-27g lands first or concurrently**. ⚠️ If WS-27h runs first, the `SourceBadge` loses its data while the integration is still live. Sequencing constraint, recorded. |
| `is_mine` | **Derived, dropped** | `personal.derive_disposition` already computes it from `pm_task_assignees`. A stored copy of a derivable fact is the mirror problem in miniature. |
| `deleted_at` (mig 67) | **Dropped → `archived_at`** | `pm_*` has no soft-delete and gains none: pervasive soft-delete was **refused** (P-31). Rows with `deleted_at` migrate as archived, and the reason travels with them. |
| `user_id` | **Dissolved** | Becomes `pm_projects.personal_owner` (which project) plus `pm_task_personal.member_email` (whose overlay). One column becomes two because it was doing two jobs. |
| `synced_at` | **Dropped** | Meaningless without the provider arm. |
| 🆕 **`migrated_task_id`** (mig 189) | **No destination — it dies with the table** | Added by **S3b** as scaffolding, not as data: it points at the `pm_tasks` row this became, which makes the backfill idempotent (a re-run skips what it already moved), auditable (old joins to new while both exist), and gives **S3c** its precondition — nothing is dropped while any row is still NULL. It is the one column here whose correct destination is *nowhere*, and it is named anyway because a column that reaches the day of the move without a row in this table is a bug in this spec. ⚠️ Do not "migrate" it into `pm_tasks`: a pointer to a table that no longer exists is worse than no pointer. |
| `horizon_id` → `gtd_horizons` | 🔴 **BLOCKED — WS-21 owns Horizons and is DO-NOT-DISPATCH** (`work_plan.md` §4) | This migration **cannot** decide the fate of a feature another workstream owns. Either WS-21 rules first, or `gtd_horizons` and this FK outlive the retirement as an explicitly-parked island. Do not quietly drop it. |
| **`gtd_waiting`** (table: `item_id`, `waiting_on`, `delegated_at`, `expected_by`, `last_nudged_at`, `resolved`, `created_at`) | 🔴 **NEW table `pm_task_waiting`**, keyed `(task_id, member_email)` | The whole Waiting-For view (WS-18, built 2026-08-02) rests on this. Per-member for the same reason as `disposition`: Ana waits on Ben while Ben waits on a vendor, about one task. A single delegation row per task cannot express that. `item_id` becomes `task_id`; the member half of the key is **new** — the legacy table had no such column because the legacy store was single-user by construction (`gtd_items.user_id`), which is precisely the assumption one store removes. |

**What this changes about the ticket.** WS-27h is no longer "move rows between two tables we
own." It is:

1. a **schema** step — 🔴 twelve new columns on `pm_task_personal`, one on `pm_tasks`, one new
   `pm_task_waiting` table, plus the provenance decision. Expand/contract per **R6**: nullable
   with defaults, tighten later, never rename in place.
2. the **union read** (step 1 above), which is independently valuable — it is what lets the
   Tasks app show a Projects view of the same person's work without the two disagreeing about
   what "my tasks" means. **This is the step to build first**; it unblocks UI parity work
   without touching a single row.
3. the **data move**, which is 🔴 owner-gated.
4. the **predicate retirement** (step 3 above).

**Sequencing constraints, both load-bearing:** WS-27g before or with the provider-column drop;
WS-21's Horizons ruling before `horizon_id` can be resolved either way.

**Done-when, added:** a test asserts every `gtd_items` column and every `gtd_waiting` column
appears in this table with a destination, so a column added to the legacy store after this was
written cannot reach the migration unnoticed — the failure mode this section exists to prevent.

### 7.4 Retirement inventory (WS-27g, second half)
System A ClickUp arm: `ingestion/sources/clickup/` (client, normaliser, webhook),
`scheduler.py`'s ClickUp job, `scripts/clickup_sync.py`, `/webhooks/clickup` from
`PUBLIC_ROUTES` · System B ClickUp arm: `ClickUpProvider` + the four broker handlers +
ClickUp rows in `task_accounts` (the provider *interface* stays — it is the personal app's
abstraction and §6.1's internal provider uses it) · `apps/skills/skill-clickup-sync/` ·
integrations catalog card + OAuth provider entry · graph-mirror consumer repoint (§6.6) ·
**revoke the ClickUp tokens** (owner act) · root `AGENTS.md` #8 amendment + `README.md`
mentions. Each path re-verified at execution time, not trusted from this list.

---

## 8. Decisions

**D-PM-1 — New `pm_*` tables; `gtd_*` is not extended into an org store.**
`DECISION (agent-proposed, owner may overrule).` `gtd_items`/`gtd_projects` are per-user by
construction (owner-scoped 404 model, 27 `user_id` predicates, per-user `task_accounts`
sync) and carry a personal GTD overlay; an org PM store has opposite visibility semantics
and shared mutable state. Extending in place would put one table under two ownership
models — `gateway/AGENTS.md` 12c's exact warning. **Rejected:** growing `gtd_*` org-wide
(every existing predicate becomes a bug surface; the overlay's "never clobber" contract
breaks when rows are shared). **Cost:** assigned tasks exist in two tables during
coexistence (mirrored by §6.1's internal provider — the same duality `SYNCED` rows already
live with), and WS-27g owes the graph-mirror repoint.

**D-PM-2 — Hierarchy is two self-FKs + types-as-data (the Paca shape).**
`DECISION (agent-proposed, owner may overrule).` Departments/projects/subprojects are one
`pm_projects` self-FK; tasks/subtasks one `pm_tasks` self-FK; Epic/Story are `pm_task_types`
rows with the Epic-root rule; depth-bounded cycle walks in code. **Rejected:** per-level
tables (ClickUp's Space/Folder/List zoo — the importer *flattens* it instead) and a closure
table (write amplification nothing here needs). **Cost:** subtree reads are recursive CTEs;
`root_project_id` is denormalized to keep the hot paths flat.

**D-PM-3 — Visibility is grant-based from day one; full portfolio rides `data:org:read`.**
`DECISION (agent-proposed, owner may overrule).` Center slices are this feature's point, so
scoping cannot be deferred the way D-CRM-3 deferred it: `pm_project_grants` ships in
WS-27a using the shipped subject vocabulary, subtree-inherited, 404-not-403. The
all-departments view requires `data:org:read` — deliberately giving D14's zero-consumer
slug its first consumer. **Rejected:** org-visible v1 (contradicts the slice requirement)
and a new `projects:read_all` slug (WS-24 N4 precedent: a new slug is nobody's grant until
an admin acts, which would blank the People Center for the owner too). **Cost:** the read
model is a union query on day one; creation defaults to an `org` grant so a solo org
notices nothing until it starts scoping.

**D-PM-4 — Actors and assignees are the `email | agent:<name>` string vocabulary.**
`DECISION (agent-proposed, owner may overrule).` Paca's member-row indirection is refused;
the platform's convention (`crm_activities.created_by`, broker `actor` strings) already
admits both species, and `EffectiveAccess.intersect()` is our stronger answer to agent
authority. **Rejected:** a `pm_members` table (a third membership store beside `app_user`
and `org_group`). **Cost:** no per-project roles in v1; write floors are lead/creator/
`admin:members:manage` (§4).

**D-PM-5 — Ordering is per-view fractional indexing; no rank column on tasks.**
`DECISION (agent-proposed, owner may overrule).` Per `paca_pm_research_2026-08.md` §2.4;
it is what makes the same task orderable differently in the People Center and a Center
slice. **Rejected:** `gtd_item_sort_key`-style single order (one global order cannot serve
N views) . **Cost:** one side table and materialise-on-first-drag semantics the UI must
implement faithfully.

~~**D-PM-6 — The personal connection is the Tasks app's provider seam, run internally.**
`DECISION (agent-proposed, owner may overrule).` §6.1's mechanism: `pm_tasks` mirrored into
`gtd_items` as `source='SYNCED'` under an internal `metorite` provider…~~
— **SUPERSEDED 2026-08-06.** Kept struck rather than deleted because the replacement is
only legible against what it replaces: the mirror was the thing rejected, and a reader who
finds `pm_task_personal` without this will wonder why the obvious answer was not taken.

**D-PM-6 (revised) — ONE task store. The personal manager is a lens, not a copy.**
`DECISION (owner-directed 2026-08-06: "the personal task manager should be a proper
extension of the project system … a cohesive whole that should fit within each other".)`

`pm_tasks` is **the** task table. Three consequences, and they are the whole design:

1. **Assignment is not a sync.** A task assigned to a member is the row in their inbox.
   Completing it there completes it for the project at the same instant, because there is
   one row and one status. The mirror would have had two rows for one fact, and every
   feature built afterwards — search, calendar, agents, reporting, the weekly review —
   would have had to know about both.
2. **Private work is a personal project.** An ordinary `pm_projects` row carrying
   `personal_owner`, granted to that one address (§3.11). Nothing about tasks, boards,
   timelines, automation or agent dispatch needs a special case; it is a project whose
   grant happens to name a person. Personal projects are excluded from every *team* read —
   "My tasks" is not a department — which is presentation, not access.
3. **The GTD overlay is per-member** (§3.12, `pm_task_personal`). Two people assigned the
   same task hold different dispositions: the person doing it says NEXT, the person who
   delegated it says WAITING. A single column on `pm_tasks` could not express that, and it
   is what delegation looks like rather than an edge case.

**Rejected:** (a) the mirror, above — cohesion was the owner's stated requirement and a
mirror is by construction two things; (b) keeping `gtd_items` for private todos and merging
in the UI (owner-answered: two task tables forever, and every future feature has to handle
both — the seam that quietly drifts); (c) rewriting `/tasks` onto `pm_tasks` in one pass
(same end state, but ~11.8k lines and 68 endpoints at once, and a regression there breaks
the owner's daily driver).

**Cost, and it is real:** `gtd_items` becomes legacy and needs its own retirement
(**WS-27h**, §7.5) — a second retirement project running beside ClickUp's. `/tasks` reads a
union of both stores until it lands. The 27 owner-scoped predicates in `items.py` are
untouched by this ticket and are WS-27h's problem, deliberately.

**A property worth stating because it falls out rather than being built:** `disposition` is
NULL until a member triages, and the read *derives* one from the task's status. So "never
looked at" and "deliberately filed to INBOX" stay distinguishable — which is the only
question the Weekly Review exists to ask, and a column defaulting to `'INBOX'` would have
destroyed it silently.

**D-PM-7 — Sync conflicts: three-way merge, newest-wins per field, conflicts logged to the
timeline.** `DECISION (agent-proposed, owner may overrule).` §7.2. **Rejected:** whole-row
last-writer-wins (silently destroys the other side's edits — the exact class of lie BO-1b
documents) and manual conflict queues (an approval inbox for field merges would drown the
owner). **Cost:** `clickup_snapshot` storage per linked task and a merge function that must
be property-tested (§10).

~~**Open questions for the owner (deliberately unimplemented):** portfolio layer? · agent
writes during coexistence? · first-import scope?~~ — **ALL THREE ANSWERED 2026-08-06.** Kept
struck rather than deleted so the answers below read as decisions taken, not defaults
inherited. They are D-PM-8, D-PM-9 and D-PM-10.

**D-PM-8 — No portfolio/program layer; grants are the only grouping axis.**
`DECISION (owner-answered 2026-08-06).` A project may carry several grants at once, so a
genuinely cross-department initiative appears in both Centers without a second grouping
axis, and the People Center sees the whole forest through `data:org:read` (§4).
**Rejected:** a `pm_programs` table above departments — it is a second axis every view,
filter and picker would have to carry, for an expressiveness the grant model already has.
**Cost:** a cross-cutting initiative is expressed as multiple grants on one project (or a
shared parent project), not as a named program. If named programs are wanted later they are
purely additive — a table plus a nullable column — and nothing in §3 forecloses them.

**D-PM-9 — Agent edits to ClickUp-linked tasks are treated exactly like human edits.**
`DECISION (owner-answered 2026-08-06 — the agent proposed queueing agent-originated pushes
for approval and was overruled).` An agent may work any task it can see, native or linked,
and its edits sync outward through the same `_broker_gate` path as the owner's own.
**Rejected:** (a) asymmetric approval for agent-originated pushes — it would have made
agents second-class actors in a model whose whole point (D-PM-4) is one actor vocabulary;
(b) restricting agents to native-only projects until cutover — that would leave agents
useless on the existing portfolio for the entire coexistence period, which is most of
WS-27's life. **Cost, stated once and plainly:** `_broker_gate` auto-applies by default
(`ACTION_BROKER_ENFORCE` unset), so during coexistence a mistaken agent edit reaches the
live ClickUp workspace with no human in between. Three properties bound that cost and
**none of them is a gate — do not describe them as one**: every agent edit is attributable
(`created_by='agent:<name>'` on the activity, plus the broker audit row), reversible from
the timeline (§3.8's `field_change` carries old and new), and the whole class becomes
queue-on-approval the moment `ACTION_BROKER_ENFORCE` is flipped — itself an owner gate, and
one whose flip-blockers WS-27c already depends on (BO-1a and BO-1b landed 2026-08-11;
**BO-1d, the four callers that index the pending marker as a result, is open and is what
now blocks the flip**). **Consequence that
motivated the call:** WS-27f's agent dispatch is demoable against real portfolio data
during coexistence rather than only against tasks created after cutover.

**D-PM-10 — ClickUp Spaces map to Centers explicitly, from agent-proposed suggestions;
unmapped Spaces still import and stay reachable.**
`DECISION (owner-answered 2026-08-06).` Mechanism in §7.1: a `plan` endpoint proposes a
Center per Space from assignee-overlap, name match, and an EVAL-LOCKED content
classification, with the evidence attached; the owner confirms; the import applies the
confirmed mapping as `group:<slug>` grants. **Rejected:** (a) making the mapping a required
precondition of import — it would block the import on a decision the owner may reasonably
want to take *after* seeing the data; (b) auto-applying suggestions — a wrong auto-map
grants one Center visibility of another department's work, which is the single error class
this app must never make silently; (c) giving unmapped Spaces an `org` grant — harmless
today with one member, wrong the moment colleagues land, and invisible when it turns wrong.
**Cost:** the importer grows a plan endpoint and an LLM-backed suggester carrying its own
eval lock, and the owner performs one confirmation step per import run. **Scope note:** this
supersedes the earlier "pilot Space vs all Spaces" framing — scope is now a per-Space
decision the plan step surfaces, so both a pilot and a full import are the same code path.

**D-PM-11 — The timeline shows a chosen SCOPE, not every task.**
`DECISION (agent-proposed 2026-08-08, owner delegated the choice back — "go ahead with the
decision that you think would be best to make the product as useful as possible").`
**ANSWERED: (b), with (c) underneath.** A Gantt of 400 rows is a
wall nobody reads, so something has to decide which tasks earn a bar. Three candidates, and
they are not equivalent:

* **(a) A task TYPE, the Paca answer.** Paca's Timeline pre-filters to the `Epic` system type
  and its view settings let you add others back. Clean, and it costs us a convention we do
  not have: `pm_task_types` is per-root data with no reserved names, so "Epic" would either
  become a seeded row every project inherits or a name-match, and a name-match is a rule that
  silently stops working the day somebody renames a type.
* **(b) Hierarchy DEPTH.** Top-level tasks get bars; subtasks roll up into the parent's bar
  and expand on click. Needs no new vocabulary — `parent_task_id` already says it — and it
  matches how the tree is already drawn everywhere else in this app.
* **(c) Whatever the current filters select.** No new concept at all: the timeline is the
  board's filters in a third shape, which is the rule §11.16 already holds for the calendar.
  Honest, and it puts the wall back the moment somebody clears the filters.

**Chosen: (b), with (c) underneath it** — depth decides the default and the filter bar
still narrows, so the two compose instead of competing. **Rejected:** (a) as the primary,
because inventing a reserved type name to make a chart legible is a data-model change in
service of a rendering problem, and D-PM-2 put types in the hands of each project on purpose.
**Cost:** a subtask's dates have to roll up into the parent's bar, which means the parent's
bar is sometimes derived rather than stored, and "why does this bar not match the dates I
typed" becomes a question the UI has to answer on the bar itself.

**D-PM-12 — Does a dependency CONSTRAIN the schedule, or only describe it?**
`DECISION (owner-delegated 2026-08-08).` The owner was given the three options and their
costs, and answered *"go ahead with the decision that you think would be best to make the
product as useful as possible"* — so the agent's recommendation was taken as the decision
rather than the question being left open. **ANSWERED: (c) — constrain, but only warn.**
Recorded this way, and not as an agent proposal, because the delegation was explicit and
the reasoning below is what it was delegated on.

This is the one that changes what the data means, which is why it is not agent-proposed.
Jira and ClickUp both offer to **push** a dependent task's dates when you move its blocker.
Adopting that turns `pm_task_links` from a description into a constraint:

* **(a) Describe only.** Drawing an arrow records the dependency and moves nothing. This is
  the straight extension of WS-27p, which states the position explicitly — *"blocked-ness is
  DERIVED and SHOWN, never enforced"* — on the argument that dependencies in a real workspace
  are frequently approximate, and a tool that will not let somebody finish work they have
  finished is a tool they route around. **Cost:** the chart will show arrows pointing
  backwards in time, because nothing stops a blocker being due after the task it blocks.
  Users read that as the feature being broken.
* **(b) Constrain, and auto-push.** Moving a blocker drags its dependents forward. What
  people expect from Jira. **Cost, and it is not small:** one drag becomes an unbounded
  cascade of writes across a project — every one of them a real `PATCH` with a
  `field_change` activity and a revert (§3.8), so a single gesture can produce fifty timeline
  rows and fifty notifications. It also directly contradicts WS-27p's stated position, so
  taking it means striking that paragraph rather than quietly living beside it.
* **(c) Constrain, but only warn.** The arrow goes red and the panel says "this starts before
  its blocker finishes"; nothing is written. Keeps WS-27p's position intact, kills the
  backwards-arrow complaint, and adds no cascade. **Cost:** somebody still has to do the
  rescheduling by hand, which is exactly the work (b) automates.

**Chosen: (c).** It is the only one of the three that neither contradicts a decision already
made nor lets one gesture write fifty rows, and it can become (b) later behind an explicit
per-project setting — whereas (b) cannot become (c) without taking a behaviour away from
people who have started relying on it. **WS-27p's position therefore stands unamended:**
blocked-ness is derived and shown, never enforced, and a schedule conflict is now shown the
same way — a red arrow and a sentence, with nothing written.

**What "useful as possible" actually argued for, since that was the brief.** The reflex
answer is (b), because auto-push is the feature Jira advertises. But the useful half of a
dependency is *knowing* — being told, at the moment you move something, that two tasks now
disagree. (b) delivers that and then also silently rewrites other people's dates, which is
where it stops being useful: the cascade lands in the timeline (§3.8) as fifty
`field_change` rows and fifty notifications with no single act to point at, and the first
time somebody's carefully-negotiated date moves without them touching it, they stop trusting
the dates. (c) keeps the information and drops the part that costs trust. **What it does not
do** is reschedule for you, and if that turns out to be the thing actually wanted, (b) is
still reachable — as an opt-in per project, with the cascade bounded and previewed before it
writes, which is a better version of (b) than the one that would have shipped today.

**D-PM-13 — Project docs live in the KNOWLEDGE BASE; PM links to them, never owns them.**
`DECISION (owner-answered 2026-08-09).` The Plane research (§11.19,
`plane_pm_research_2026-08.md` §6 Q2) surfaced that free-form project documentation was
owned by nobody: this spec assigned it to Notes, and `note_taker_app.md` §1.2 declines it.
The owner's answer, verbatim: *"we have separately a knowledge base, so somehow the PM tool
has to fit in with the knowledge base and be able to do that. Now everybody who creates a
knowledge base will own it, and if it's shared with multiple people or shared across the
team, then depending on the user access, they have access to the knowledge base document."*

What that binds, stated as the integration contract:

1. **PM never grows a docs surface.** Plane's Pages stays refused (P-30); the §5 non-goal
   is now permanent, not provisional. A "project doc" is a knowledge-base document that a
   project or task **links to**.
2. **The KB's access model is: creator owns; shared to people or a team; visibility follows
   the share.** That is grant-vocabulary shaped — the same `email | group:<slug> | org`
   subjects `pm_project_grants` already uses (D12) are the natural encoding of "shared with
   multiple people or across the team", and the KB should reuse that vocabulary rather than
   mint a second one.
3. **Two keys, never one.** Linking a KB document to a task does NOT widen the document's
   audience: a viewer sees the link's title/existence only if they satisfy the *document's*
   grants, independently of satisfying the task's. The converse also holds — a doc reader
   doesn't gain the task. R5 applies on both sides (a non-granted viewer gets 404, never a
   locked-item stub). This is the same two-door lesson S2-8 taught about assignees.
4. **The PM-side shape, when the KB exists as a store:** a `pm_task_links`-style reference
   row (task/project → KB doc id) rendered beside attachments in the panel, with the KB's
   own grant check resolving at read time — never a copied snapshot of the doc, which would
   silently fork access. Until the KB store lands, this decision blocks nothing in the
   beyond-parity queue; it exists so no ticket accidentally builds doc storage inside PM.

**D-PM-14 — Public read-only boards: DEFERRED.**
`DECISION (owner-answered 2026-08-09).` *"For now, let's leave out public read-only boards.
We will revisit it when needed."* Not built, not scheduled. The risk analysis to start from
when revisited is `plane_pm_research_2026-08.md` §6 Q1 — the anchor-capability-URL shape, a
physically separate route module with read-only models, no member-roster endpoint, per-board
kill switch, and the RLS-bypass point that must be resolved before `SET LOCAL app.tenant_id`.
Until then the gateway's posture is unchanged: no anonymous tenant-data read routes exist.

**D-PM-24 — The `inert` gap in `Modal` is ACCEPTED. We do not re-implement `markOthers`.**
`DECISION (2026-08-11, owner-delegated).` WS-27ak slice 1's done-when said *"the background is
`inert`, not merely covered (so find-in-page and a screen reader cannot walk into it)"*.
**`@base-ui/react@1.7.0` cannot deliver the first half** — `FloatingFocusManager:339` passes
`ariaHidden`, `markOthers` defaults `inert: false`, nothing in the package ever passes
`inert: true`, and the only real `inert` sits on the portal's own `InternalBackdrop` while
**closed**. Measured in Chromium: `[inert] = 0`; the background carries `aria-hidden="true"`
plus a `data-base-ui-inert` marker.

**What we actually have, stated precisely so nobody has to re-derive it:** screen readers
**cannot** reach the background · Tab **cannot** leave the dialog (guard nodes) · **Ctrl+F
still finds the page behind the scrim.**

**Accepted, for three reasons.** (1) The two properties that carry real accessibility weight
are both covered; find-in-page reaching background text is an annoyance, not a failure.
(2) **Every dialog we ship is dismissible** — nothing in this product depends on hard
isolation, which is what `inert` is really for. (3) Closing it means the wrapper walking
`document.body`'s children itself, i.e. **a second implementation of `markOthers`** — the
parallel seam CLAUDE.md §5 forbids, and a permanent maintenance liability against an upstream
that will very likely add the flag.

⚠️ **Revisit on either trigger**, and they are cheap to notice: Base UI exposes an `inert`
option (watch `markOthers`' signature, which already accepts one), **or** we design a
non-dismissible confirm gate — a destructive-action modal that must not be escaped is the
first surface where "merely covered" stops being good enough.
**R7: advisory.** No test can assert the *absence* of a substrate feature; what IS fenced is
that the docs no longer claim otherwise (`Modal.tsx`'s header, `DESIGN_SYSTEM.md` §4a, and
§11.31 were all corrected in repair round 1 after two of them shipped the false claim).

**D-PM-21 — UI behaviour is verified in a REAL BROWSER (Playwright), narrowly. No jsdom.**
`DECISION (2026-08-11, owner-delegated: "make the decisions as per what you recommend").`
Wave 2 (WS-27ak: Modal → Tooltip → Toast → Skeleton) is almost entirely behaviour that no
current test in this tree can observe. `vitest.config.ts` is `environment: "node"` with
`include: ["src/**/*.test.ts"]`, so `.tsx` tests are not collected and there is no jsdom,
happy-dom or `@testing-library`: **React rendering is verified by nothing but a human
looking at it.**

**Rejected: jsdom + `@testing-library`.** It is the cheaper-looking option and it buys the
wrong half. jsdom has **no layout engine**, so scroll-lock with scrollbar compensation,
collision-aware positioning, viewport flip and real Tab order are all unverifiable under it
— and those are precisely the behaviours most likely to be silently wrong. Adding it would
mint a second test environment that still cannot answer the questions Wave 2 asks.

**Chosen: Playwright**, because the infrastructure already exists and is idle —
`playwright.config.ts`, six `e2e/*.spec.ts`, Chromium pre-installed at `/opt/pw-browsers`.
This is switching something on, not building it.

✅ **VERIFIED RUNNING 2026-08-11, twice, independently** — `npx playwright test
e2e/theming.spec.ts` → **20 passed**, exit 0. This decision is not a plan; it is a capability
that works today. The exact invocation, from `workbench/control_plane`:

```
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
PLAYWRIGHT_EXECUTABLE_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome \
  npx playwright test e2e/<spec>.spec.ts --reporter=line
```

⚠️ **Corrected 2026-08-11 — the first draft of this paragraph said "a spec *may need* an
explicit `executablePath`", which understated it in both directions.** The escape hatch is
**already wired** at `playwright.config.ts:22-27`, which reads `PLAYWRIGHT_EXECUTABLE_PATH`
into `launchOptions` — it landed with #424. So no config edit is needed. But the two env vars
are **required, not optional**: without them the run fails hard looking for
`chromium_headless_shell-1223` (`playwright-core/browsers.json` pins 1223; `/opt/pw-browsers`
has 1194). ⚠️ **`npm run test:e2e` as written in `package.json` sets neither and therefore
fails** — fixing that script belongs to the first slice that lands a spec.
**Never run `npx playwright install`** — the environment forbids it, and it is not needed.

🔴 **A claim this correction retires.** Every Wave 1 as-built, and several of my own reports,
said the four-theme sweep was "owed at review — no browser runs in this environment."
**A browser runs in this environment**, and `e2e/theming.spec.ts` already asserts
Fluent/Material/Graphite control personality across 20 cases. What is genuinely unfenced is
*cross-app layout continuity*, not "anything in a browser". The weaker claim was repeated
often enough to become received truth, which is exactly how a tree acquires a false
constraint.

**Scope, and it is deliberately narrow: one spec per primitive, asserting only what no other
method can see.** Focus moves in on open · Tab wraps at both ends · the background is
genuinely `inert` (find-in-page and a screen reader cannot walk into it) · Escape is caught
at the dialog, not on `document` · focus returns to the opener, never `<body>` · scroll locks
without the page shifting. **Not** a broad e2e suite — those rot, and a rotting suite is
worse than none because it teaches people to ignore red.

**The evidence this is worth it, from the day it was decided.** Wave 1's adversarial reviewer
raised a P1: making the task title a real `<a>` means Enter escapes the canvas keydown
handler (true — `stepCursor` returns null at `cursor < 0` and `TaskList.tsx:171` returns
*before* `preventDefault`) and therefore performs a full-document GET that reboots the SPA and
discards filters, selection and view mode. Plausible, well-argued, **and wrong**: the default
action of Enter on an anchor is *to dispatch a click*, which `ControlLink` intercepts.
Settled in minutes by driving Chromium against a repro faithful to React's root delegation —
zero navigations, URL unchanged. Under the status quo that is a repair round spent on a
non-bug, every time.
**R7:** the fence is the spec files themselves; there is no test that can force their
existence, so this decision is **advisory** until a WS-27ak slice lands one.

**D-PM-22 — "Overdue" stays timestamp-granular: `due_at < now()`. Today does NOT count as
due.** `DECISION (2026-08-11, owner-delegated).` WS-27al(5) inherited *"today counts as due"*
from the upstream reference. **Refused, and recorded so it is not re-proposed.** Our store is
timestamp-granular and the reference is date-granular; these are different models, not a bug
and its fix. Adopting theirs would invert two assertions that deliberately pin `<` over `<=`
and carry their reasoning in comments (`src/lib/taskCard.test.ts:174`,
`src/app/projects/lib/mywork.test.ts:35-42` — *"a task is late once the moment has passed,
not at the moment itself"*), **and** change `gateway/routes/projects/filters.py:182`,
dragging R8 and R6 into a ticket labelled "logic-only, no dependency". What *is* required, and
is now true, is that all four predicates agree on **completion**: a finished task is never
overdue. **Fences already in place:** `taskCard.test.ts`, `mywork.test.ts` (both pinning `<`),
and the SQL's `CLOSED_CATEGORIES` exclusion.

**D-PM-23 — The task-menu registry is a SECOND registry at a different scope, and that is
correct.** `DECISION (2026-08-11, owner-delegated).` WS-27bd's acceptance said its items
derive from `app/projects/lib/commands.ts`. **That criterion was unsatisfiable and the
implementer's deviation is ACCEPTED.** `COMMANDS` holds `go.*`, `view.*`, `panel.*`,
`project.*` and `help.shortcuts` — **nothing task-scoped** — so a card menu whose every item
resolved to it would read *"Widen the task panel · Custom fields · Import from ClickUp"*.
`app/projects/lib/taskMenu.ts` is therefore the task-scoped registry and `commands.ts` stays
the page-scoped one.
⚠️ **This is not a licence to grow registries.** The condition, already fenced in
`taskMenu.test.ts`, is that **the two stay disjoint in both directions** — no shared id, no
shared label, no `task.*` in `COMMANDS`, no non-`task.*` in `TASK_MENU_ACTIONS`. Extending
`commands.ts` to task scope remains possible but is **a ticket, not a side effect**: it also
moves the `g`/`v` key sequences and the printed `?` shortcuts sheet, which are generated from
that registry.
📌 **The process point, which outlives this ticket.** An implementer rewrote its own
acceptance criterion. The reasoning was right and it was disclosed in three places rather than
buried — that is the behaviour we want. But the *decision* to change acceptance is a
reviewer/owner call, and it is being recorded here rather than left inside an as-built,
because acceptance that a builder can silently edit is not acceptance.

**D-PM-25 — a project's RUN STATE and whether it is ARCHIVED are two axes, not one column.**
`DECISION (2026-08-13, owner-directed.)` Migration 146 shipped
`pm_projects.status CHECK (status IN ('active','on_hold','done','archived'))` **and**
`archived_at TIMESTAMPTZ` in the same table, so `archived` is stored twice in two shapes —
and the enum conflates two independent questions: *is work flowing?* and *do you want to see
this?* A project can be done-and-visible (just finished, still on the board) or
paused-and-filed (shelved indefinitely). One column cannot say both. Therefore:

* **`status` is the RUN STATE only** — `queued` · `active` · `on_hold` · `stopped` · `done`.
  Two values are added; **`archived` leaves the axis.**
* **Archive is `archived_at IS NOT NULL`, and nothing else.** That is the shipped task idiom
  (`pm_tasks.archived_at`, excluded at `filters.py:260`), so this extends the existing seam
  rather than minting a second one (CLAUDE.md §5).

⚠️ **`active` and `on_hold` are NOT renamed.** The UI labels them **Ongoing** and **Paused**;
the stored values stand. R6 forbids renaming in place, `active` is the DEFAULT on every
existing row, and display-label-over-stored-value is already how `pm_task_statuses` works
(`name` free, `category` machine-readable). A rename touches every call site and buys a word.

**The expand/contract path (R6 — we cannot roll back).** Widen the CHECK to the union of old
and new values → backfill each `status='archived'` row by stamping `archived_at` and setting a
run state → drop `'archived'` from the CHECK in a **later** release. ⚠️ The backfill population
must be **measured on the live box, not assumed empty**: no UI writes this column, but the API
has accepted it on create and PATCH since 146.

**D-PM-26 — project state DERIVES onto its tasks; it never writes them.**
`DECISION (2026-08-13, owner-ruled.)` Pausing, stopping or archiving a project changes **no
`pm_tasks` row**. Effective state is resolved at read time from the task's project and its
ancestors — a task is paused *because its project is*, exactly as a `pm_project_grants` row on
a root covers the subtree without being denormalised onto children (§3.2).

This is **D-PM-12's ruling applied to a second surface**: an arrow warns, it never reschedules;
a pause derives, it never writes. The five costs a cascade would carry, each of them
measurable rather than aesthetic:

1. **Reversibility.** Resume must restore each task's *prior* status — a stash column or a
   timeline reconstruction, i.e. new state whose only purpose is undoing a write we did not
   need to make.
2. **The timeline.** `pm_activities` is the single spine (§3.8). Four hundred tasks through one
   pause/resume cycle is eight hundred rows nobody will read.
3. **Notifications.** WS-27j fires off task change; a cascade tells every assignee that nothing
   happened.
4. **Delta-sync.** Migration 168's feed keys on `updated_at`, so a cascade bumps every row in
   the subtree and every delta client re-pulls the whole project.
5. **Concurrency.** D-PM-20 is still owed, so writes are last-write-wins; a mass write is the
   worst possible interaction with agents writing beside humans.

**The one write that IS correct is the user's act, not the state change's:** stopping a project
**offers** to close its open tasks — *"12 tasks are still open. Close them as cancelled?"* —
executed through the shipped bulk endpoint (WS-27n) as ordinary audited transitions. Declining
leaves them open. An offer is not a cascade.

**Pause governs attention, not permission** (owner-ruled the same day): a paused project still
accepts comments, re-planning and grooming, because re-planning is usually *why* it was paused.
Archived is read-only.

⚠️ **R8 binds the read side.** Deriving means task reads consult the project, so the plan must
be `EXPLAIN`ed against a real Postgres at realistic row counts before any performance claim is
made. WS-27be is the precedent: an index that *looked* like it covered the case was unusable
for twenty-four migrations and no unit test could have said so.

**D-PM-27 — the project-state hue map, and why it must not route through `keywordHue`.**
`DECISION (2026-08-13, owner-ruled — the owner was shown the collision and its cost and chose
this mapping.)`

| Run state | Label | Hue |
|---|---|---|
| `active` | Ongoing | **green** |
| `on_hold` | Paused | **amber** |
| `stopped` | Stopped | **red** |
| `queued` | Queued | **gray** |
| `done` | Done | **blue** |

⚠️ **This deliberately diverges from the task-status vocabulary on two hues, and the divergence
is recorded so it is not "fixed" later.** `CATEGORY_HUES` maps `in_progress → blue` and
`done → green`; a project tree and a task board sit on the same screen, so green will mean
"running" in one and "finished" in the other. The owner was given that cost and ruled for the
mapping above. **An agent finding this inconsistent should cite this decision and stop, not
repaint it.**

🔴 **The implementation constraint that makes it work.** `PROJECT_STATE_HUES` is a **closed
lookup consulted directly**. It must NOT fall through `resolveHue`'s name-keyword step, because
`keywordHue` maps the literal word **`active` → blue** (`/(progress|doing|active|working|review)/`)
and `done` → green — so routing a project state through the generic resolver produces the
**opposite** of this decision on two of the five states. The map lives in
`src/lib/statusAccent.ts` (rule 4 — extend the shared module, never mint a project-local
palette) and is fenced by a test asserting each state resolves to its ruled hue *and* that the
map is unreachable from `resolveHue`.

⚠️ **Hue is never the only channel.** Each state also carries a glyph through `<Icon name>`: a
dense tree, read at a glance or read by a colour-blind user, must not depend on colour alone.

**D-PM-28 — the three priority axes: importance is SHARED and already exists, urgency is
DERIVED, only `leveraged` is personal.** `DECISION (2026-08-13, owner-ruled: "important and
urgent can be systems that are shared between personal tasks as well as project management
tasks. Only leveraged is an additional thing that appears in the personal task manager.")`

| Axis | Question it answers | Where it lives |
|---|---|---|
| **importance** | how much does this matter? | **`pm_tasks.importance`** (0–3) — shared, **already shipped** |
| **urgent** | how soon? | **derived from `due_at`**, never stored |
| **leveraged** | rare, asymmetric upside? | **`pm_task_personal.leveraged`** — personal, 🔴 new |

🔴 **This overrides §7.5.1's row for `important`**, which sent it to `pm_task_personal` on the
argument that *"Ana may rate a shared task important and Ben may not."* The owner ruled the
other way: importance is a property of the work. The override is recorded here rather than
edited quietly into that table, because a spec row that changes owner without a trace is how a
later reader concludes the table was always wrong.

**No boolean `important` column is added, and that is the point.** `pm_tasks.importance` is
already a *four-level* scale — strictly richer than `gtd_items.important`'s boolean — so adding
the boolean beside it would be the CLAUDE.md §5 defect (a second way to ask an existing
question) in the very decision meant to prevent it. The existing scale **is** this axis. So the
owner's ruling costs **one** new column, not three.

🔴 **`importance = 3` is relabelled "Urgent" → "Critical", and it is not cosmetic.**
`IMPORTANCE_OPTIONS` (`app/projects/lib/table.ts:164`) currently reads
`3 Urgent · 2 High · 1 Normal · 0 Low`. The moment urgency is derived from `due_at`, a
hand-set "Urgent" pill can sit on a card whose due date is months away — **two things called
urgent on one card, one manual and one derived, disagreeing.** One word, one meaning:
importance says how much, urgency says how soon.

**`/tasks` adopts the four-level control; its boolean toggle is retired** (D-PM-29, and the
owner's standing "Projects is canonical, Tasks conforms"). Mapping a boolean onto a 4-level
scale is lossy in the write direction — toggling "Important" off has no single correct level to
return to — so the conforming surface takes the richer control rather than inventing a mapping.

⚠️ **`urgent_window_hours` must be shared, not per-user.** `gtd_settings` holds it per person
today. If urgency is a shared axis and the window is personal, Ana and Ben see different
urgency on the same task, which is precisely the divergence this decision removes. It becomes
an org-level setting; where exactly is an implementation call, that it is not per-user is not.

**D-PM-29 — Projects is the MASTER schema; `/tasks` reproduces it and may only ADD.**
`DECISION (2026-08-13, owner-ruled: "We want to have the project management settings as the
master and this is just reproduced in the personal apps… Apart from those few additional data
points and fields, it should be exactly the same as the product management field so as to
prevent confusion. Any changes made on the personal tasks should also reflect on the product
management task.")`

This is D-PM-6 ("one row, and the personal view is a lens over it") stated as a **schema**
rule rather than a storage one, and it settles a class of question that keeps recurring:

1. **A field's default home is `pm_tasks`.** Personal placement is the exception and must earn
   itself against §7.5.1's own test — *can Ana's and Ben's answers on the same task
   legitimately differ?*
2. **`/tasks` may add fields Projects does not have** (`leveraged`, and the GTD overlay
   `pm_task_personal` already carries: disposition, next action, context, energy, two-minute,
   defer). It may **not** hold a different version of a field Projects has.
3. **A write from the personal app is a write to the same row.** No mirror, no reconciliation,
   no "which one wins" — the property is already true by construction since 2026-08-06 and this
   decision is what stops a future ticket re-introducing a sync.
4. Where the two disagree on presentation, **Tasks conforms** — including retiring its own
   control when Projects has a richer one for the same fact.

⚠️ **What this does NOT overturn.** Timeboxing (`scheduled_start`/`scheduled_end`/`flexible`)
stays personal on §7.5.1's reasoning, which rule 1 does not defeat: two people on one task book
their own calendars, so their answers legitimately differ. `deep_work` likewise, as a sibling of
`energy`. **`actual_start`/`actual_end` are the genuinely arguable pair** — "how long did this
take" is a question a PM tool must answer at the task level, while "when did *I* work on it" is
mine — and they are flagged here as an owner question rather than resolved by an agent reading
a rule. ✅ **ANSWERED — D-PM-30, shared.**

**D-PM-30 — `actual_start` / `actual_end` are SHARED, on `pm_tasks`.**
`DECISION (2026-08-13, owner-ruled.)` 🔴 **This overrides §7.5.1's row**, which sent them to
`pm_task_personal` alongside timeboxing on the argument that two people on one task book their
own calendars.

The override follows D-PM-29's own rule rather than contradicting it: a field's default home is
`pm_tasks`, personal placement is the exception and must earn itself, and *"how long did this
task take"* is a property of the **work**, not of a person. Projects has `estimate_mins` and
**no actuals at all** today, which means it cannot answer *"did this take as long as we said?"*
— the question a PM tool exists to answer, and one that a per-member column can never answer at
the task level.

⚠️ **What this does NOT decide.** Per-person time tracking ("Ana logged 3h, Ben logged 1h") is
a **timesheet**, a separate concern with its own rows, and nothing here forecloses it. The two
are not alternatives: a shared actual is when the work started and stopped; a timesheet is who
spent what on it. Timeboxing (`scheduled_start`/`scheduled_end`/`flexible`) and `deep_work`
stay personal, unmoved — those genuinely differ between two people on one task.

**D-PM-31 — the task search minimum is 3 characters, not 2.**
`DECISION (2026-08-13, owner-ruled.)` WS-27be left this open and the open state was the worst
of the three available: `MIN_QUERY = 2` **accepts** a two-character query that the `pg_trgm`
index physically **cannot serve** (a trigram is three characters), so the shortest query the
product allows is the longest one it has to answer with a sequential scan — 127 ms, measured
at 60k rows.

Raising it to 3 makes every accepted search index-served. The cost is stated rather than
buried: typing two characters now returns `{"rows": []}` rather than a slow result, on both
`/projects/search` and the list endpoint's `?q=` (WS-27be moved `MIN_QUERY` to `filters.py` so
one constant governs both — that is what makes this a one-line change rather than two).

---

## 9. Tickets

> ### 9.0 Reference links — the two upstream repositories, pinned
>
> *(Added 2026-08-10 on the owner's instruction: "link relevant files from their respective
> repositories in those features… so that when we are developing it, we always have a
> reference to the original file to study.")*
>
> Tickets below carry **REF:** lines pointing at the file that inspired them. Both repos are
> pinned to a **commit SHA, never a branch** — `main` moves, and a line number against a
> moving branch is a link to the wrong code within a week.
>
> | Repo | Licence | Pinned commit | Link prefix |
> |---|---|---|---|
> | `makeplane/plane` | **AGPL-3.0** | `31853ab2b8b7810c59dc30d22e52c8f4b5a71a47` (= `v1.4.1-rc2`) | `https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/` |
> | `Paca-AI/paca` | **Apache-2.0** | `09dab28e3caee9e43891697998dcfa7fcf76991c` | `https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/` |
>
> **⚠️ The two licences are not the same rule, and the difference is load-bearing.**
>
> * **Plane is AGPL-3.0.** Reading it and reimplementing a behaviour is fine — functionality
>   and interaction design are not copyrightable. **Copying its source, markup, CSS or assets
>   into this tree is not available to us**, at any size. A REF: link to Plane means *go read
>   this to understand the behaviour*, and nothing more.
> * **Paca is Apache-2.0** (verified: plain, no Commons Clause, no added terms, no NOTICE
>   file). Permissive — reuse in a proprietary product **is** legally available, subject to
>   retaining the copyright and licence notices, carrying the licence text, and stating that
>   the file was modified. **We still default to reimplementation**, for an engineering
>   reason rather than a legal one: anything on our surfaces has to satisfy
>   `DESIGN_SYSTEM.md`, so a copied component gets rewritten anyway. If anyone ever does copy
>   a Paca file substantially, that is a decision to record here with the attribution
>   discharged in the same PR — not a thing to do quietly.
>
> A REF: is **evidence and a reading list, never an instruction to port.** Where the upstream
> is a thin wrapper over a permissively-licensed library, the REF: names the **library**
> instead — that is the more useful reference, and in several cases converts a blocked idea
> into an `npm install`.


**WS-27a — schema + feature registration + core API.** 🟢 AGENT-SAFE.
Done when: (1) the migration exists at the next free number, idempotent, with the
`feature_catalog` row — and the WS-26a-style **static idempotency test** over the migration
text passes; (2) `"projects"` is in `FEATURES` and the both-ways catalog invariants stay
green, including the named `test_projects_is_registered_on_both_sides`; (3) `routes/
projects/` serves §4's tree/tasks/activities/admin/views/me modules behind
`require_feature_router("projects")`, consumes `gateway/db.py` (grep-assertable: zero
`create_async_engine` under `routes/projects`), and is listed in `GATED_ROUTERS`; (4) the
grant read model answers 404-not-403 for a non-granted caller and honours `email`/`group:`/
`org` subjects + assigned-to-me, proven hermetically; (5) status transition writes all
three effects (§3.8); (6) `pm.*` events are emitted through `event_hooks.emit_event`.

**WS-27b — ClickUp org importer + the Space→Center mapping plan.** ✅ **BUILT 2026-08-06**
(`routes/projects/mapping.py` + `import_clickup.py`, 25 hermetic cases, 4 mutants red) ·
🔴 **running either endpoint against the production workspace is still OWNER-GATE** (§6 of
`work_plan.md`) — **neither has been executed**.
Two things the build recorded that this ticket did not ask for, both in `import_clickup.py`:
statuses are derived from the **tasks'** own status types rather than the space workflow
(that is where ClickUp puts the type, and it costs no extra API call), and **subtasks import
as top-level tasks of the right list** — ClickUp carries a parent id only on a detail fetch,
so linking them would cost one call per task; WS-27c's sync already fetches detail and
re-parents them. Recorded rather than silently skipped, because "subtasks became top-level
tasks" is exactly the surprise an importer must not spring.
Done when: (1) `POST /projects/import/clickup/plan` returns one row per Space with counts,
a suggested Center, a confidence, and the evidence, from all three §7.1 signals — and
**writes nothing**, proven by a test that asserts no INSERT/UPDATE reaches the session;
(2) the plan pre-fills from existing `group:` grants, so a re-run of an already-confirmed
mapping proposes the same mapping (fenced by a test — this is what stops a re-import
silently re-mapping a Space); (3) the LLM classifier is EVAL-LOCKED and the plan degrades
to the two deterministic signals when it is unavailable, never failing the whole plan;
(4) `POST /projects/import/clickup` maps Space/Folder/List/task/subtask/status/assignee per
§7.1 with provenance, re-runnably, and applies the confirmed mapping as `group:<slug>`
grants; (5) **a Space absent from the mapping still imports in full and receives no group
grant**, and a test proves it is then visible to a `data:org:read` holder and to an
assignee, and invisible to an unrelated Center's member (the D-PM-10 (c) rejection made
executable); (6) the response reports per-entity imported/updated/skipped counts, the
grants applied, and a parity summary; (7) permission floor is `admin:access:manage` (the
WS-26b finding: `integrations:use:*` gates nothing); (8) a dry-run mode reports counts
writing nothing.

**WS-27c — two-way coexistence sync.** 🟢 **UNBLOCKED for build 2026-08-11 — BO-1a +
BO-1b landed (WS-1)**; AGENT-SAFE · 🔴 enabling push against the real workspace is
OWNER-GATE, and that gate is **also** behind **BO-1d** (the four callers that still index
the broker's pending marker as a result — `accounts.py:335`/`:403`, `planning.py:377`,
`items.py:790`). Read BO-1d before writing criterion (2): it is the same class of bug at a
different layer, and this workstream must not reproduce it.
Done when: (1) delta pull + webhook fan-in share one idempotent upsert path; (2) every
outbound mutation flows through `_broker_gate` and a pending disposition is honoured (the
BO-1b class is fenced by a test that fails on an empty provider id marked synced); (3) the
three-way merge takes the newest side per field and writes a `sync` activity for every
conflict, property-tested over generated edit interleavings; (4) `GET /projects/sync/
status` + `/conflicts` report truthfully (a failed push is never shown synced).

**WS-27d — UI + Center projections.** ✅ **BUILT 2026-08-06**
(`src/app/projects/` + the BFF proxy; 34 vitest cases, 6 mutants red).
One finding recorded: `featureForPath` is fed `usePathname()`, which carries **no query
string**, so `/projects?center=<slug>` never reaches the route guard — the slice URL is
gated on the bare `/projects` path. A first version of the registration test asserted the
query-string form and was wrong about the contract, not about the code; no speculative
query-stripping was added to the shared `access.ts` for a case that cannot occur.
Done when: (1) `/projects` renders tree + list + board + task panel + timeline against the
real API via the BFF proxy; (2) drag-drop writes fractional positions (one upsert per drop)
and cross-column drags patch the `column_by` field; (3) nav/access registration per §5 with
`tsc --noEmit` + vitest green; (4) the People Center sub-app flips live and each Center's
slice pre-filters by its group — with a vitest asserting the `?center=` param filters
presentation only.

**Authoring landed 2026-08-06** (`lib/assignees.ts` + edits to `page.tsx`, `ProjectTree`,
`TaskPanel`; 17 vitest cases, 7 mutants red). WS-27d shipped a UI that could **read and
drag but never create**: `createProject`, `createTask` and `setAssignees` existed in the
client and were wired to nothing, so a member could only work with rows a ClickUp import
had put there. Four surfaces close it, each placed where the answer already is:

- **New department** from the sidebar header, **new subproject** from a `+` on the node
  itself — the parent is on screen, and a dialog that asks "which parent?" is how a
  fifty-node tree acquires mis-parented rows.
- **New task** from a one-field row above the board. Status is deliberately **not sent**:
  the API picks the project's default (`create_task`), so the browser never has to know
  which lane a new task starts in.
- **Subtask** from the task panel. A subtask is a task with a parent (§3.5) — one endpoint,
  one table, so it inherits statuses, timeline and assignment whole.
- **Assignees** as removable chips plus one input. This is where **D-PM-4 stops being a
  schema note**: an agent and a person go in the same field, and the only difference on
  screen is an icon. Handing work to an agent is now literally the same gesture as handing
  it to a colleague — which is the precondition for WS-27f's dispatch being reachable at
  all, since `pm.task.assigned` is what it keys off.

Two details worth keeping: `withAssignee` returns the **same array** when the assignee is
already present, and the caller skips the PUT on that identity — a re-assert must not emit
`pm.task.assigned` and re-dispatch an agent run. And `parseAssignees` splits on commas,
semicolons and newlines but **never on spaces**, because a pasted `Priya <priya@x.com>`
would otherwise shred into tokens that assign work to nobody.

**WS-27e — the personal lens (one store).** ✅ **BUILT 2026-08-06**
(migration `147_projects_personal.sql`, `routes/projects/personal.py`; 31 hermetic cases,
6 mutants red). **Its shape changed with D-PM-6's revision** — this was specced as a mirror
into `gtd_items` and is built as a lens over `pm_tasks`, so the done-whens below are
restated against what was actually built rather than what the mirror would have owed.
Done when: (1) an assigned `pm_task` appears in the assignee's inbox **with no sync** —
same row, same id — with the correct derived disposition; (2) a member's triage cannot move
the team's board and a status change cannot overwrite a stated disposition, both proven
structurally; (3) completing from the inbox moves the shared status and writes the
transition's three effects; (4) two assignees hold independent dispositions; (5) a personal
project is created once, granted to its owner alone, and excluded from every team read;
(6) no route here accepts a `?member=` in any form.

**The surface landed 2026-08-06** (`src/app/projects/components/MyWork.tsx` +
`lib/mywork.ts`; 17 vitest cases, 7 mutants red). WS-27e had shipped API-only, which meant
the cohesion the revision bought was true in the schema and invisible to a member. **"My
work" sits above the project tree in the same app** — not a second surface and not a second
nav entry, because a personal lens reached from somewhere else re-teaches exactly the split
D-PM-6 was revised to remove. Four decisions worth recording, each of which could
reasonably have gone the other way:

- **Four lanes, not eight.** `INBOX | NEXT | WAITING | SOMEDAY` are work states and get
  lanes; `PROJECT | REFERENCE` are filing states and collapse into one "Filed" lane shown
  only when occupied; `DONE | TRASH` the endpoint already excludes. Eight lanes would make
  the daily view a filing cabinet.
- **Empty work lanes still render.** "You have triaged nothing into today" is a real and
  useful state, and a lane that vanishes when empty cannot say it. Only "Filed" hides.
- **Undated tasks sort BELOW dated ones.** A task nobody dated is not more urgent than one
  due tomorrow, and the opposite order is how a personal list stops being read.
- **Untriaged is stated in the row, not implied by a missing badge**, and counted in the
  header. That count is the Weekly Review's whole question and is only answerable because
  the server derives dispositions instead of storing them on first read (§3.12).

Completing from a row calls `POST /tasks/{id}/complete`, which moves the **shared** status
— the checkbox carries a title saying so. Triage buttons call
`PATCH /tasks/{id}/personal` and cannot touch a shared field. One repair the surface forced:
`TaskPanel` previously read the *selected project's* statuses, which is wrong for a task
opened from My work — it may belong to any project the member is assigned into — so the
panel's statuses are now resolved from the task's own root project.

**WS-27f — automation + agent dispatch.** ✅ **BUILT 2026-08-06**
(`routes/projects/automation.py` + `agent_dispatch.py`, the `pm_task` node type in
`workflows/engine/`, `PM_EVENT_TOPICS` in the catalog; 34 hermetic cases, 10 mutants red).
Both halves of `workflows_app.md` §13 — **U1** the task-mutation node, **U7** dispatch.
The node types land in `workflows_app.md`'s tree per D6 and are recorded there.

**Six decisions worth reading before changing any of it:**

- **The engine imports a service, not a route.** `apply_task_patch` is transport-free and
  reuses `apply_status_transition`, `update_row`, `record_activity` — so an automation's
  edit is *indistinguishable in validation* from a human's PATCH and lands the same
  timeline row. That is Paca's "mutate through the ordinary service" rule as code.
- **Status is named, never keyed** (`"Done"`, or the category as a fallback). Statuses are
  per-project rows, so a graph pinned to one project's status UUID could only ever automate
  that project — the opposite of what an automation is for. An unknown lane fails with the
  project's actual lane names in the message.
- **A `pm_task` node is NOT write-class.** The `write_without_approval` publish gate fires
  for `tool` nodes reaching *external* systems; an internal task move must not need an
  approval step. That exemption is now pinned by a test rather than true by accident.
- **"Already in target state" writes nothing** — and the test asserts **no `UPDATE` is
  issued**, not merely that no activity was written. `update_row` stamps `updated_at`, so a
  redundant write is invisible in a diff while leaving the task looking freshly touched;
  an automation firing on `pm.task.updated` would bump every task it inspected, forever.
- **Assignment is dispatch, from a sink.** `PUT /tasks/{id}/assignees` emits and returns;
  `agent_dispatch.on_event` is registered beside the workflows dispatcher. A slow or broken
  agent therefore cannot fail the act of assigning somebody a task. Only **newly added**
  assignees dispatch — `set_assignees` emits the added set, so a re-assert cannot start a
  second run, and both sides say so.
- **The handoff activity is committed BEFORE the run starts** (Paca's
  `agent.session.started`), and the failure path writes too. A dispatch that fails silently
  leaves a session that appears to be running forever and nobody knows to pick the work up.

**One engine defect found and fixed:** `templating.resolve_value` keeps an unresolvable
`{{ref}}` **as-is at run time by design**, and `{{trigger.missing}}` passes the publish gate
because its *root* is legal. The literal would have reached Postgres as a would-be uuid and
come back "Task not found", sending the maker to look for a task rather than at their
reference. The node now fails with `task id did not resolve: '…'`.

Done when: (1) an event-triggered workflow mutates a task and the target carries a
`pm_activities` row actored `system:workflow:<id>`; (2) unknown field and missing target
both fail at **publish** with named issues; (3) re-running against a task already in the
target state records a skip and writes nothing; (4) the node is served by
`GET /workflows/catalog` (D7); (5) assigning `agent:<name>` starts a run whose session is on
the task timeline within the same request.
Done when: (1) `pm.*` events reach `dispatch_event` (proven at the `emit_event` seam);
(2) the `pm.update_task` action mutates through the ordinary service and stamps
`system:workflow:<id>`; (3) assigning `agent:<name>` produces an orchestrator run, an
immediate `agent_run` activity, and a closing activity on completion/failure; (4) the
`skill-projects` tool family lets an agent read/update its assigned task under its own
identity, permission-intersected.

**WS-27h — `gtd_items` retirement.** 🟡 sequenced after WS-27e; the data move itself is
🔴 **OWNER-GATE** (it rewrites the owner's live task store).
Done when: (1) `/tasks` serves a union with no visible regression; (2) every `gtd_items`
row has a `pm_tasks` counterpart and the counts match per disposition; (3) the overlay
landed in `pm_task_personal` with dispositions preserved exactly; (4) `items.py`'s
owner-scoped predicates and the `gtd_*` task tables are gone. See §7.5.

**WS-27g — cutover + ClickUp retirement.** 🔴 **OWNER-GATE end-to-end** (final import,
parity sign-off, sync flips, consumer repoint, token revocation, constraint-8 amendment —
each registered in `work_plan.md` §6).

**WS-27t — the timeline, and dependencies you can draw.** ✅ **BUILT 2026-08-08.**
Was 🟡 blocked on D-PM-12; the owner delegated the choice back on 2026-08-08 and it is
answered as **(c) constrain-and-warn**, with D-PM-11 as **(b) hierarchy depth**. Both are
recorded in §8 with the alternatives they beat.

*Asked for directly, 2026-08-08:* **"a timeline view that can also make tasks and subtasks
dependent on each other, with wiring them to each other, similar to how it works on Jira and
ClickUp."** Two things, and the second is the one that matters — a Gantt chart with no
dependency gesture is decoration, which is precisely why Gantt was a non-goal until now.

**The data is already built.** This is a rendering-and-gesture ticket, not a schema one:

| Needed | Status |
|---|---|
| `start_date` (DATE) + `due_at` (timestamptz) on every task | ✅ migration 146, surfaced at WS-27q |
| `pm_task_links` with `blocks`, `CHECK(source <> target)` | ✅ WS-27a |
| Cycle refusal on `blocks` (`assert_no_block_cycle`, `MAX_DEPTH`-bounded) | ✅ WS-27p |
| Both-direction read with `direction` on each link | ✅ WS-27p `GET /tasks/{id}/relations` |
| Blocked-count and subtask progress per row, in one aggregate | ✅ WS-27s `attach_relation_counts` |
| Interval-overlap window query, timezone-safe | ✅ WS-27q `OVERLAPS` |
| Move a task's dates through the ordinary write path | ✅ WS-27q `rescheduleTo` → `PATCH /tasks/{id}` |

**What is genuinely new is three things.** (1) Bar geometry on a continuous date axis instead
of a day grid. (2) `GET /projects/timeline`, or an argument for why the calendar endpoint
serves both — it very nearly does, and the honest difference is that a timeline wants the
LINKS for every row in the window, which is one more aggregate of exactly the shape
`attach_relation_counts` already is. (3) The arrow gesture, which is the only part with no
precedent anywhere in this tree.

**Paca has the chart and not the wiring.** `apps/web/src/components/projects/interactions/
roadmap-view.tsx` (438 lines, Apache-2.0) is a real Gantt and worth taking the geometry from:
a sticky 280px task column beside a scrolling canvas, `PX_PER_DAY = 28`, month header cells
computed by walking `Date(y, m+1, 1)`, a today line, a range auto-fitted to the data with
seven days of padding either side, single-date tasks drawn as a one-day bar, and — the rule
this app would have arrived at anyway — **an undated task listed on the left with no bar**,
which is the same honesty §11.16 enforces with its `undated` count. It draws **no dependency
arrows at all** (zero matches for arrow/svg/path/depend) and is **entirely read-only** (zero
for drag/resize). So Paca answers "how do I lay out bars"; for the wiring, Jira and ClickUp
are the reference and the interaction is ours to design.

**Two decisions gate it.** **D-PM-11** — what earns a bar (agent-proposes hierarchy depth,
owner may overrule). **D-PM-12** — whether an arrow constrains the schedule or only describes
it (**owner-answer required**; the agent recommends *warn, do not push*, because auto-push
contradicts WS-27p's stated position and turns one drag into an unbounded cascade of real
`PATCH`es, each carrying a `field_change` activity and a notification).

**Done when:** (1) a timeline view renders every task in a window as a bar from `start_date`
to `due_at`, with undated tasks listed and unbarred; (2) `blocks` links are drawn as arrows
between bars, in the direction WS-27p already stores; (3) dragging from one bar to another
creates a `blocks` link through the existing endpoint, and a drag that would close a cycle is
refused with `assert_no_block_cycle`'s existing message rather than a new one; (4) whatever
D-PM-12 decides is implemented and its rejected alternatives are recorded; (5) the board's
filters apply, and the parameter-coverage test §11.16 added is extended to the new endpoint
so a filter cannot be dropped silently; (6) the geometry is pure and tested — including
across at least three timezones, the WS-27q lesson.

**Not in scope:** resizing a bar by dragging its edge (a second gesture with its own
half-day/rounding questions), critical-path computation, and baselines. Each is a separate
decision, and none of them is what was asked for.

---

### 9.1 The beyond-parity queue (minted 2026-08-09 from the Plane research, §11.19)

Six tickets, in recommended build order. Each verdict traces to
`plane_pm_research_2026-08.md` (P-numbers); ⚠️ **the AGPL wall in that doc's header binds
every one of these** — shapes re-derived in our idiom, never translated. All of them inherit
the standing protocol: hermetic tests against the fake, mutation-tested guards, a live
Postgres run, and R1 (migration numbers resolved at build time — every number below is a
description, not an assignment).

> **✅ ALL SIX BUILT 2026-08-10** on the restarted branch (after #399 merged), six parallel
> agents + two integration merges; full suites green (5789 backend / 1278 frontend).
> Build-time facts that differ from or sharpen the ticket text:
> - Migration numbers resolved as **164** (intake), **165** (watchers), **166** (lifecycle);
>   `main` had taken 163 in the interim. WS-27w needed **no** migration.
> - **WS-27w item 1**: no archive endpoint existed at all — `archived_at` had no writer. The
>   ticket's guard therefore shipped as new `POST …/archive` + `/unarchive` endpoints with
>   the 422 guard built in (guard phrased as `category not in CLOSING_CATEGORIES`, so
>   `triage` is refused without depending on WS-27u).
> - **WS-27v**: migration 165 seeds each task's author as a watcher (the pre-watchers
>   audience included authors; the switch must not silently unsubscribe them), and only
>   *delivered* mentions auto-subscribe.
> - **WS-27z**: `/workflows` workflows are DB rows, not files — so the deliverable is the
>   sweep (`automation.run_lifecycle_sweep`), a config-free **`pm_lifecycle`** engine node
>   mirroring `pm_task`'s wiring, and `automation: true` on `record_activity`. Authoring +
>   publishing the schedule-triggered workflow is an **owner step on the live box**
>   (HANDOVER §1.1). "Default closing status" does not exist in the schema; the sweep's
>   recorded model is first `cancelled` lane by position, else first `done`. Root's policy
>   governs the subtree; the API 422s a policy write on a child.
> - **Continuity backport (owner directive, same day)**: the WS-27y machinery was promoted
>   to shared code (`src/lib/cursor.ts`, `src/components/QuickAdd.tsx` + `useFlash`) with
>   re-export shims, the Tasks app's cards now draw the WS-27s chip vocabulary, and its
>   board/list gained the cursor, group-context quick-add, and drop-refusal/flash grammar.
>   Remaining divergences are recorded in HANDOVER (Tasks' modal selection vs Projects'
>   shift-range is the biggest).

**WS-27u — intake/triage: the front door.** 🟢 AGENT-SAFE *(P-1)*.
A captured task is real from birth, parked out of sight until a human rules on it.
Done when: (1) a migration adds a `pm_intake` join table (`task_id` unique, `status ∈
pending|accepted|declined|duplicate|snoozed`, `snoozed_until`, `duplicate_of_task_id`,
`source`, `source_ref`, `organization_id` — the tenant key every `pm_*` table
carries per **D15**, kept on the row rather than derived through a parent; the
rule's original statement was `multi_tenancy.md`'s D-MT-3, superseded for
architecture but unchanged on this point) and a `triage` value in the
status-category vocabulary; (2) the **default list exclusion is one predicate in
`core.py`** beside the visibility clause — tasks whose status category is `triage` appear
in no board/list/calendar/timeline/search surface unless `include_triage` is passed, and
the §11.16 parameter-coverage test is extended so no surface can drop it silently;
(3) `POST /projects/intake` creates task+wrapper in one transaction; accept flips status
in place (never copies), decline archives with the wrapper as provenance, duplicate sets
`duplicate_of_task_id` and archives, snooze hides from the queue until `snoozed_until`;
(4) all four actions write `pm_activities` rows and the wrapper survives them — provenance
is permanent; (5) a triage rail in the UI lists pending items with the four actions;
(6) visibility: the intake queue is scoped by the same project grants as the tasks it
wraps — R5 applies. **Not in scope:** routing rules (auto-accept, agent screening) —
those are `/workflows` nodes per D6, added when email capture (§6.5) lands.

**WS-27v — watchers, and mentions that behave.** 🟢 AGENT-SAFE *(P-2, P-20 part)*.
Done when: (1) migration adds `pm_task_watchers(task_id, watcher, organization_id)`,
unique per pair; (2) commenting, editing, assigning, or being mentioned auto-subscribes
(idempotent), and explicit watch/unwatch endpoints exist; (3) the notification audience
becomes watchers ∪ assignees, still filtered by the recipient's actual visibility
(`resolve_visibility_for` stays the gate — Plane's membership-only check is the
counterexample, not the model); (4) **mention diffing**: editing a comment or description
notifies only *newly added* mentions — proven by a hermetic test that edits a comment
twice; (5) the actor of a change is never notified of it (existing rule, re-asserted over
the new audience); (6) the unread endpoint returns `{total, mentions}` separately and the
bell shows the mention count distinctly. **Not in scope:** notification snooze/archive.

**WS-27w — read-path and history hardening.** 🟢 AGENT-SAFE *(P-3, P-5, P-6, P-7, P-21)*.
A basket of small corrections, each independently shippable:
(1) **archive guard** — archiving a task whose status category is not done/cancelled is
422, with the category named in the message; (2) **activity meta rule** — `field_change`
entries for FK-valued fields carry `{field, old_id, new_id, old_label, new_label}`, and a
structural test over `record_activity` call sites enforces it; (3) **description-edit
coalescing** — a same-actor consecutive description/comment-body edit updates the prior
activity row's timestamp instead of appending; (4) **semantic sorts** — sorting by status
orders by category rank then position, never alphabetically; every entry in `TASK_SORTS`
ends with a deterministic `(created_at, id)` tiebreaker, asserted structurally; (5)
**picker exclusions** — search accepts `exclude_relatives_of=<task_id>` (self, ancestors,
descendants, already-related both directions) so pickers cannot offer what the write will
422; write-time guards stay; (6) **human task IDs** — the per-root number every task
already has renders on cards and panel with a copy-deep-link affordance.

**WS-27x — the spreadsheet layout, and the shown-fields contract.** 🟢 AGENT-SAFE
*(P-10, P-12)*. Two pieces, one ticket, because the column set IS the contract.
Done when: (1) a per-view `shown_fields` list joins the saved-view config (`toConfig`/
`fromConfig` round trip extended, tested); (2) every chip `TaskMeta` renders gates on it —
`taskCard.ts` stays the single fact-derivation layer, this is the visibility layer on top;
(3) a Table layout renders one row per task with columns = shown fields, inline editors
per cell driving the existing `PATCH` path (status, assignee, dates, importance, custom
fields), per-column header sort mapping to existing `TASK_SORTS`, sub-tasks expanding
indented in-table; (4) a quick-add row sits at the bottom (shares WS-27y's machinery);
(5) keyboard: arrows move the cell cursor, Enter edits, Esc cancels; (6) DESIGN_SYSTEM
throughout — no raw colours, `Icon`/`Button`/`Input` primitives, theme suite green.

**WS-27y — board and list interaction upgrades.** 🟢 AGENT-SAFE *(P-11, P-13, P-17, P-18)*.
Done when: (1) **sub-grouping** — board accepts a second grouping axis rendered as
swimlanes (group columns × sub-group rows), per-lane collapse persisted with the view,
empty lanes hidden unless asked; (2) **group-context quick-add** — every list group,
board column/lane, and calendar day offers an inline title-only add **pre-filled with
that group's value** (status, assignee, date…), Enter submits and resets for the next;
(3) **drop feedback** — dragging where a drop is disallowed overlays the target with the
*reason*; after any drop or quick-add the moved card scrolls into view and flashes;
(4) **keyboard cursor** — ArrowUp/Down moves an active-row cursor, Shift+Arrow extends
the existing selection from it, Enter opens the panel; feeds `BulkBar` unchanged.

**WS-27z — lifecycle policy: auto-archive and auto-close.** 🟡 *(P-4; the sweeper touches
real data on a schedule — enable per project, default off)*.
Done when: (1) migration adds `archive_after_months` and `close_after_months` (nullable
INT, NULL=off) to root `pm_projects`, plus a `timezone` column (P-28) so "a month
untouched" has a defensible midnight; (2) the sweeper is a **`/workflows` scheduled
workflow** (D6 — never a PM-app cron) that archives closed-category tasks untouched
beyond the window and closes stale open ones to the project's default closing status;
(3) every automated change writes an activity row flagged `automation: true` and renders
distinctly in the timeline; (4) tasks in `triage` (WS-27u) are exempt; (5) the manual
archive guard (WS-27w item 1) ships first — this ticket depends on it.

**Deferred small basket** *(pull individually when adjacent code is touched)*: peek size
escalation + Esc-returns-focus (P-14), Save/**Update view** dirty affordances (P-15),
palette action registry + go-sequences (P-16), calendar week layout + per-day
quick-add/overflow (P-19), filtered-list CSV export (P-26), delta-sync feed + satellite
`updated_at` bump (P-27), `is_epic` flag + per-user view state + session `user_id` denorm
(P-28 rest). **§9.2 promotes P-14/15/16 (WS-27ab), P-19 (WS-27ac) and banks the rest as
WS-27ae.** Banked for their trigger events: sprints (P-23, when sprints are wanted),
webhook-out checklist (P-24, when `/workflows` grows the node), email digest outbox (P-25,
when PM emails). Owner-decided: docs = knowledge base (D-PM-13); public boards deferred
(D-PM-14).

---

### 9.2 The post-tenancy queue (minted 2026-08-10, after H2 landed on `main`)

Four tickets. The first exists because the **Projects app owns two of the residues that
gate WS-29's phase-4 promotion** (D27 findings 2 and 3) — closing them here means Projects
is not the reason RLS cannot be switched on. The other three drain the deferred basket and
the continuity audit. None needs a migration, so R1 costs this wave nothing; all four
inherit the standing protocol (hermetic tests against the fake, a live Postgres run, and
for anything tenant-shaped, **R8** — verified against a real database, never a fake alone).

**WS-27aa — the two tenancy residues Projects owns.** ✅ **BUILT 2026-08-10** *(D27 (2);
MT-1d's named site; the H2 ratchet's one Projects exemption — both now struck)*.
Two scheduled/background paths in this app still touch the database with no tenant.
Done when: (1) **`run_lifecycle_sweep` takes an explicit tenant and refuses without one** —
the signature gains a required `organization_id`, the roots query gains
`AND organization_id = :org`, and a sweep constructed without a tenant raises rather than
sweeping every customer's projects (H4's rule: *a job that forgets doesn't leak one row, it
leaks unbounded*); (2) the tenant comes from a **stored fact, never request input** (R11) —
the workflow's owner resolved through `app_user`, the shape
`routes/crm/auto_lead._owner_organization` already uses, and a resolution that finds
nothing is an error, not a fallback; `_pm_lifecycle_sweeper` binds it with
`bind_tenant`/`release_tenant` around the sweep so the writes carry the right GUC the
moment phase 4 lands; (3) **`agent_dispatch` carries its tenant on the event payload** —
`pm.task.assigned` emits the task's own `organization_id`, read inside the request's bound
session at emit time, and `on_event`/`_run_and_record` bind that explicitly instead of
inheriting an ambient one; a payload without an org refuses rather than running unbound;
(4) **two-org proof against a real database**: a
sweep bound to org A leaves org B's stale tasks untouched, and a dispatch bound to A writes
A's activity row — plus a refusal test for each path; (5) the `projects/agent_dispatch`
entry leaves `H2_EXEMPT_FILES` (the file no longer needs it), every seam ratchet stays
green, and the handover's MT-1d site + D27 finding 2 are struck with the measurement that
replaced them. **Not in scope:** the other H4 consumers (ingestion, reconciler, broker) —
they belong to WS-29's own H4 slice; this ticket closes only what Projects owns.

**As built (2026-08-10), and the three places the ticket above was wrong.**

*The sweep.* `run_lifecycle_sweep(db, *, organization_id, actor, now)` — required,
never defaulted; a blank one raises `TenantUnbound` (the seam's own exception, not a
second vocabulary) **before any statement is issued**, and the roots query is now
`WHERE parent_project_id IS NULL AND organization_id = CAST(:org AS uuid)`. The fence is
on the roots query alone because everything below reaches its rows through `project.id`.
`workflows/service._pm_lifecycle_sweeper` resolves the tenant on an unbound session
(`SELECT au.organization_id FROM workflows w JOIN app_user au ON lower(au.email) =
lower(w.owner_email) WHERE w.id = CAST(:wid AS uuid)`) and then opens
`tenant_session(org)` for the sweep — auto_lead's shape, re-derived, not a second one.

⚠️ **The tenant source is the workflow OWNER, not the workflow row.** Verified against
`infra/postgres/132_workflows.sql` *and* a live catalog: `workflows` has **no
`organization_id` column** — it exists only in the unapplied `generated/01_add_columns.sql`
(H3 phase 1). `live_ws27aa.py` asserts that absence, so the day phase 1 lands this script
goes red and `_workflow_organization` becomes a one-column read.

⚠️ **MT-1d's "it needs a per-tenant loop" is wrong and is struck.** A loop inside the
sweep would be one tenant's scheduled workflow acting for every other tenant — the
unbounded-job shape H4 exists to forbid. The loop is over **workflows**: each tenant
schedules its own, and each one sweeps exactly its own.

*The dispatch.* `set_assignees` reads the task's `organization_id` inside its already-bound
session (NOT NULL since migration 161) and puts it on `pm.task.assigned`; `on_event`,
`_run_and_record` and `_record_outcome` all open `tenant_session(that_org)` — the argument
form, never the ambient one.

⚠️ **Done-when 3's "records the refusal on the task timeline" cannot be built and is
struck.** The timeline is `pm_activities`, which is tenant data: writing the refusal there
needs precisely the unbound session being refused, and under phase-4 policies it would
write nothing anyway. The refusal is a WARNING log line
(`projects.agent_dispatch_refused`, carrying task and agents) and **no** write. Since the
emitter always stamps the field, it fires only for a foreign or replayed emitter.

*Evidence.* `tests/live/live_ws27aa.py` — 23 checks against Postgres 16, two organizations
with identical policies and identically stale tasks: alpha's sweep archives alpha's task
and leaves beta's, beta's sweep then archives its own, both activity rows are stamped with
the sweeping tenant, both refusal paths refuse, and no ambient tenant leaks out.
Mutation-measured: deleting the roots predicate turns 4 live checks red (including
`BETA's stale task is untouched: got True, want False`) and 3 hermetic ones.
`H2_BASELINE_ELSEWHERE` is unchanged at **111** — the sweeper trades its unbound session
for the resolver, which must stay unbound — while `routes/projects` goes from **2** unbound
sites to **0** and `H2_EXEMPT_FILES` loses its Projects entry.

**WS-27ab — view ergonomics: peek, dirty views, one palette registry.** ✅ **BUILT
2026-08-10** *(P-14, P-15, P-16; as built, and the one place the ticket was wrong, in
§11.25)*.
Done when: (1) **peek escalation** — `TaskPanel` offers peek → side → full, the choice
persists per user, and Esc returns focus to whatever opened the panel so the card/row keeps
the cursor (WS-27y's cursor is the thing being returned to); (2) **dirty-view affordances**
— `FilterBar` shows when live filter/sort/group/shown-field state diverges from the saved
view, offering *Update view*, *Save as new* and *Reset*; divergence is **one exported pure
function** over the config with its own tests, never scattered comparisons — the config
round-trip (`toConfig`/`fromConfig`) is the single fact it reads; (3) **palette action
registry** — `SearchPalette`'s commands become a declared registry (`id`, `label`,
`section`, `keywords`, `run`, `when`) instead of inline branches, `g`-sequences navigate
(`g p`, `g m`, `g t`…), and `?` renders a shortcuts sheet **generated from that same
registry** so the help cannot drift from the behaviour; (4) tests over the registry: every
action carries a label and section, every go-sequence resolves to a route that exists, and
no two actions share a key sequence; (5) DESIGN_SYSTEM throughout — no raw colours, the
`Icon`/`Button`/`Input` primitives, theme suite green.

**WS-27ac — calendar: week layout, per-day quick-add, honest overflow.** ✅ **BUILT
2026-08-10** *(P-19; as built — **§11.24**; merged onto the working branch, PR #422, not on `main`, not
deployed)*.
Done when: (1) `CalendarView` gains a **week** layout beside month, both driven by the
existing `lib/calendar.ts` date math — one implementation, extended, never a second;
(2) each day cell carries the shared group-context quick-add (`components/QuickAdd.tsx`)
pre-filled with that day's date; (3) **overflow is exact** — a day with more tasks than fit
shows `+N more` with the true count and expands rather than clipping silently; (4) dragging
between days reschedules through the existing `PATCH` path wearing WS-27y's drop-refusal
reason and post-drop flash; (5) the §11.16 parameter-coverage test extends to the week
range, so the `triage` exclusion (WS-27u) cannot be dropped by the new surface.

**As built (§11.24).** `MonthGrid` → `CalendarGrid` with a `layout` discriminator;
`monthGrid` and `weekGrid` share `mondayOffset` and `runOfDays`, fenced by a test that walks
**every day of a month** asserting `weekGrid(d).days` equals the month row containing `d`,
plus a structural read of `CalendarView.tsx` for `new Date(`/`.getDay()`/`.setDate(`.
`monthLabel`/`isOutsideMonth`/`shiftMonth` became `gridLabel`/`isPadding`/`shiftGrid` — a
week has no padding, and "next" means one of whatever is on screen. Overflow is
`dayFill(count, limit, expanded)` with `hidden = count - shown`, `DAY_LIMITS` month 3 /
week 8, no fold at one-over. `dayDropRefusal` names the two drops that previously did
nothing silently — ⚠️ only the "not on this calendar" case is reachable today (§11.24).
Quick-add and the drag `PATCH` were already correct and were re-verified in a browser.
Done-when 5 landed as a **window-shape parametrisation** (`CLIENT_WINDOWS`) over the same
endpoint. Browser-verified in four themes × two modes.

**WS-27ad — Tasks ↔ Projects continuity, round 2.** ✅ **BUILT 2026-08-10** *(the backport
agent's recorded gap list, HANDOVER §1; scope extended mid-ticket by the owner to put the
VISUAL layer — board/list/card/colour — first)*.
The first backport promoted chips, cursor, quick-add and flash to shared code. These are
the divergences it recorded and deliberately left.
Done when: (1) **one selection grammar** — the shift-range anchor moves into shared code
beside the cursor, both apps consume it, and Tasks' modal select-mode either becomes the
shared range behaviour or is kept with the reason written next to it (a divergence with a
recorded reason is a decision; an undocumented one is drift); (2) **board chrome
converges** — Tasks' accent caps + drop-gap reorder and Projects' swimlanes +
append-on-drop are reconciled, the winning behaviour implemented once and consumed twice;
(3) Tasks' flat lists (Done/Waiting/Someday/Archive), `WaitingForView` and the Inbox gain
the shared cursor and group-context quick-add, retiring the Inbox's local `j`/`k` idiom;
(4) a test asserts both apps import the shared modules rather than re-declaring them — the
re-export shims stay, a third copy is a failure; (5) calendar asymmetry stays **out of
scope** and stays recorded (Tasks has a ten-file module, Projects one view).

**As built.** The seam is `src/lib/{statusAccent,selection,cursor,boardDrop,taskCard}.ts`
and `src/components/{StatusChip,TaskCardShell,DropGap,QuickAdd,useFlash,TaskMeta}.tsx`,
fenced by `src/lib/sharedTaskUi.test.ts` (each thing declared once, both apps importing it,
shims staying shims, no second name→class palette).

- **Colour (owner-directed).** Three vocabularies existed and a fourth fact was stored and
  never drawn: `pm_task_statuses.color` (migration 146, on the API since) rendered
  *nowhere*, so every Projects column was one `bg-muted` while Tasks' board was
  colour-coded. `lib/statusAccent.ts` is now the one palette, resolved **stored colour →
  status category (Projects' six) → name keyword (Tasks' user-named stages) → positional**,
  with `lastIsDone` as Tasks' own rule. Projects' board caps/headers, swimlane headers,
  list group headers and list/table status pills consume it; `projects/lib/tags.chipClass`
  and `tasks/lib/stageColors` delegate. Tasks renders byte-identically (pinned).
- **Card.** `components/TaskCardShell.tsx` — Tasks' `rounded-lg / bg-card / p-3 / shadow`
  box wins over Projects' `bg-background / p-2` (which was the page colour, i.e. a
  card-shaped hole in the column). `shown_fields` gating unchanged.
- **Selection.** `lib/selection.ts` holds `clickSelect` / `range` / `toggle` / `prune` /
  `allSelected`; `stepCursor`'s duplicated sweep now reads it. Projects' page and the Tasks
  store both drive it, so shift-click and Shift+Arrow behave identically. ~~**Tasks' modal
  select-mode is KEPT**, with the reason in `tasks/components/ItemList.tsx`: `selectMode`
  changes what a *click means*, and a permanent checkbox on a `TaskCard` would take the
  drag-grip gutter or make one gesture mean two things.~~ **Struck on the card side by S1
  below** — the checkbox went *outside* the card instead of into it, so it takes no gutter
  from the grip and no gesture means two things; the premise that those were the only two
  options was the error.
  select-mode is KEPT**, with the reason in `tasks/components/ItemList.tsx`.~~
  **REVERSED by owner ruling 2026-08-10 — "Projects is canonical, Tasks conforms" — and
  built for the list surfaces the same day (S3, below).** The kept reason (a permanent
  checkbox would take the drag-grip gutter or make one gesture mean two things) was never
  structural: Projects had already solved it by putting the box OUTSIDE the card as a
  sibling in the row, while Tasks put it INSIDE, absolutely positioned over the grip — so
  the collision was one Tasks had built for itself.
- **Board chrome.** Drop-gap reorder beats append-on-drop and is now
  `components/DropGap.tsx` + `lib/boardDrop.ts` (`gapKey`, `dropIndexFor` — the downward
  intra-group off-by-one, previously buried in `taskStore.reorderItem`), consumed by both
  boards; Projects' unconditional append is gone (a body drop still appends). Accent caps
  went the other way. **Swimlanes stay Projects-only**, reason in `tasks/TaskBoard.tsx`:
  Tasks' second axes are *computed* (priority/mode from flags × due date), so a lane grid
  would be a grid whose cells refuse every drop — the same reason `lib/quickAdd` refuses
  those axes.
- **Flat surfaces.** `tasks/components/FlatList.tsx` (Done/Someday/Archive/Engage/Priority)
  and `WaitingForView` now run the shared cursor, flash and selection. Per-view quick-add
  is `lib/quickAdd.viewQuickAdd`: Someday incubates, Done logs, **Waiting and Archive
  refuse with the reason in code** (a create can set the WAITING bucket but not the
  delegation, so the box would file under "Unassigned" — a sibling group).
- **Inbox.** The local `j`/`k` walk is retired; arrows and Enter are `lib/cursor`, the
  triage keys (`e x t s r 2`) stay local, and the shortcuts sheet says `↑ / ↓`.

**Recorded, not done:** the calendar asymmetry (out of scope, per done-when 5);
`app/crm/lib/board.ts` holds a *third* name→class palette for pipeline stages, exempted in
the seam test with its reason; `tasks/lib/contextColors.ts` uses raw Tailwind palette
classes (`sky-500`…) rather than semantic tokens — legal under the conformance suite, off
the token system, and a Tasks-only axis with no Projects counterpart.

**S1 — board card and column convergence (round 3).** ✅ **BUILT 2026-08-10**, on branch,
NOT merged. *(Owner ruling, not re-litigated: "the Tasks app is only a slice of the Projects
app" — **Projects is canonical**; where the two disagree and neither is clearly better,
Tasks conforms. A GTD-specific need Projects has no equivalent for is a legitimate reason to
diverge, provided the reason is written next to the code.)* Frontend only, three files:
`app/tasks/components/{TaskCard,TaskBoard}.tsx` and the shared
`components/TaskCardShell.tsx`. Nothing in `app/projects/` was edited — the Projects-side
effect (item 6) travels through the shell.

1. **Column chrome.** `rounded-xl` + `bg-secondary/30` → `rounded-lg border border-border
   bg-card`, refusal overlay `rounded-lg`, cards spaced by the column's `space-y-1` instead
   of a per-card `mb-2`. Cards and drop gaps are now siblings, because `space-y-*` only
   reaches direct children. The drag-over highlight and the accent cap stay.
2. **Completed treatment.** `TaskCardShell` had accepted `completed` since ad and **nothing
   under `app/tasks/` had ever passed it**, so a done task was dimmed and struck through on
   one board and drawn as live work on the other, from one component, with every test green.
   `completed={Boolean(item.completedAt)}` now reaches both the shell and `TaskCardTitle`.
3. **Cursor ring.** The board's wrapper `<div className="ring-2 ring-ring">` is gone; the
   card takes `atCursor` and the shell draws the ring on the card's own radius. `useFlash`'s
   `attach` moved onto the card element too, so the thing that flashes is the card.
4. **Avatars.** `TaskCard`'s private `Avatar`/`AvatarStack` are deleted for
   `components/TaskMeta`'s shared pair, `max={1}` on this app's narrow rows.
5. **The checkbox.** Out of the card and into a left gutter as a sibling in a `flex
   items-start gap-1.5` row with `stopPropagation` — /projects' pattern — and **always
   present**, not mode-gated. `selectMode` no longer changes what a click on a card means;
   `TaskCard` has no `selectMode` prop at all. The **drag grip is dropped, not relocated**:
   the whole card is `draggable`, so it was never a handle, only a hint pointing at a spot
   that is not special; /projects draws none; and the shell's hover lift is the affordance
   both boards already use. The `pr-5` the grip reserved is gone with it.
6. **Title truncation** — *the one judgement call, taken in the shared file.* /projects
   clamped to one line, /tasks wrapped without limit. `TaskCardTitle` now clamps to
   `line-clamp-2` **for both apps** and strips a caller-supplied `truncate`/`line-clamp-*`
   rather than merging it, because `white-space: nowrap` and `display: -webkit-box` on one
   element resolve by CSS source order — invisible in review. Cost: a /tasks next action
   longer than two lines ends in an ellipsis where it used to wrap in full (the full text is
   in the focus modal); a /projects card is up to one line taller. `truncate` is still
   passed at `projects/components/TaskBoard.tsx` and is now a no-op — deleting it is a
   one-line follow-up for whoever next owns that file.

**Kept, deliberately** (structural, backed by fields `pm_tasks` does not have): the priority
badge and suggestion badge, the project chip, the `SourceBadge`, the inline `ScheduleButton`,
`item.nextAction` under the title, and `StatusPill`'s interactivity — though its
`!selectMode` gate is gone, since that gate only existed because the card *was* the checkbox.

**Fences** (R7). All three are source scans in `src/lib/sharedTaskUi.test.ts`, and the file
now says why they cannot be render tests: `vitest.config.ts` is `environment: "node"` with
`include: ["src/**/*.test.ts"]`, so there is no DOM and `.tsx` test files are not collected —
adding jsdom to fence one prop is a larger change than the thing fenced. (1) *every caller of
`TaskCardShell` passes `completed` and `atCursor`* — the tag scanner is brace-aware, because
a lazy regex stops at the `>` of the first arrow function and would pass for the wrong
reason; (2) *no board re-implements the card cursor ring* (`ring-ring` absent from both
`TaskBoard.tsx` files, present in the shell); (3) *both boards' columns use the same radius*.
`AvatarStack` and `TaskCardTitle` joined the `SEAM` table, and `SEAM` rows gained an
`except` map with a per-file argument plus a staleness check — `components/room/Identity.tsx`
exports a different `AvatarStack` (room participants, photographs, presence rings, and the
per-person identity hues conformance deliberately excepts from theming). All scans strip
comments first: the first run failed on the code comment explaining which class had just
been *removed*, and a gate a comment can trip teaches people not to comment.

⚠️ **The audit's premise about `rounded-xl` is wrong in this tree, and the fence was written
to the true rule instead.** `AGENTS.md` rule 6 and `DESIGN_SYSTEM.md` §4 say `rounded-xl` is
"a fixed 12px that ignores Graphite's 0.125rem" — but `src/app/globals.css` (the `@theme`
block, ~ll. 217-227) derives the **whole** `--radius-*` scale from `--radius`, and
`--radius-xl` is literally `var(--radius)`, i.e. the same value `rounded-lg` resolves to.
`rounded-xl` is therefore fully themed here, and a tree-wide ratchet on it would have
baselined **274 correctly-themed occurrences across ~70 files**. What was actually wrong is
narrower and is what shipped: two boards drew one object at two radii on two surfaces. The
doc claim should be corrected by whoever owns `AGENTS.md`; it is left alone here rather than
edited from a ticket that owns three component files.

⚠️ **Not verified: how any of this looks.** No browser is runnable here (Playwright cannot
install), so the Fluent → Material → Graphite sweep and the phone-viewport pass are owed at
review, exactly as for af and ag. What was checked is `npx tsc --noEmit`, the full
`npx vitest run`, `npx vitest run src/lib/theme/`, `eslint` on the changed files, and each
new fence mutation-measured red before being reverted byte-identical.

**WS-27ae — export, delta-sync, small columns.** 🟢 AGENT-SAFE *(P-26, P-27, P-28 rest)*.
A three-part basket, dispatched in parts after aa–ad.

- **Export (P-26) — ✅ BUILT 2026-08-10**, merged onto the working branch (PR #422), **not on
  `main` and not deployed**. As-built: **§11.26**. ⚠️ **The "export-job pattern" this ticket named
  does not exist** — searched and confirmed absent from `apps/`, `packages/`, `tests/` and
  `workbench/`; the only hits are vendored `.venv` code. What shipped is a **synchronous**
  bounded CSV over the same filtered query the list endpoint runs
  (`GET /projects/export/tasks.csv`), rather than a job queue invented to satisfy a phrase.
  The cap is a **refusal**, not a truncation: past 5000 matching rows the endpoint answers
  422 naming the count, because a partial CSV is byte-indistinguishable from a complete one.
  Cells opening `=`/`+`/`-`/`@` are neutralised, with plain numbers exempt so exported sums
  still work. No migration.
- **Delta-sync + satellite `updated_at` bumps (P-27) and the small columns (P-28 rest) —
  ✅ BUILT 2026-08-10**, merged onto the working branch (PR #422), **not on `main` and not deployed**.
  As-built: **§11.27**. `GET /projects/delta/tasks` (path chosen so `/projects/tasks/{task_id}`
  cannot shadow it), a `(updated_at, id)` keyset cursor with a horizon, and — the part a naive
  feed cannot do — an explicit `removed[]` fed by migration **168**'s `pm_task_tombstones`
  (written by an AFTER DELETE trigger, so a project CASCADE is recorded too) plus rows that
  changed and fell out of scope. **Losing VISIBILITY stays inexpressible**, documented and
  pinned as silence. Satellites bump the task through `record_activity` plus `core.touch_task`;
  watchers, the personal overlay, notifications and per-view order deliberately do not.
  Small columns: `pm_task_types.is_epic` (read by `core.is_epic_type`) and `pm_view_user_state`
  (read by `views.list_views`); the session `user_id` denorm was **not built, because
  `chat_session.user_id` already exists and is already indexed** (migration 02) — measured,
  not assumed. Migration **168**, both tables tenant-scoped, generated phase files regenerated.
  ⚠️ No UI consumes any of it yet, and tombstone retention is owed to a `/workflows` sweep (D6).

> **Integration note (2026-08-10).** The two halves were built concurrently from the same
> base and collided in three places that only integration could see: both registered a module
> in `routes/projects/__init__.py`, both wrote an as-built section, and **both created
> `tests/live/live_ws27ae.py`** — different files, same name, because they share a ticket
> letter. Split into `live_ws27ae_export.py` and `live_ws27ae_delta.py`, with every reference
> in code, tests, the migration and this spec repointed. A live script named in a comment but
> absent from disk is the stale-mirror failure this repo keeps paying for.

**WS-27af — the themed categorical ramp.** 🟢 AGENT-SAFE. ✅ **BUILT 2026-08-10.**
*(Owner-ruled the same day, choosing the ramp over tokenising to the semantic set or
widening the DESIGN_SYSTEM exception.)* `--cat-1 … --cat-8` in all four theme manifests in
both modes (64 values), bridged to Tailwind, with `src/lib/categorical.ts` as the shared
vocabulary — slot chosen by hashing the item's **name**, never an array index, so nothing
silently repaints when a list is reordered. `tasks/lib/contextColors.ts` became the worked
adapter, the same shape `stageColors.ts` has over `statusAccent.ts`. Also retired: the two
raw-palette sites no exception covered, `SourceBadge`'s hand-rolled chrome (now `<Badge>`,
so it finally picks up Graphite's uppercase and Material's tracking), and the off-grid type
scale (`text-[12px]`/`text-[13px]` → `text-xs`/`text-sm`, 167 sites — which also restores
the user's density preference, since `--ui-scale` reaches rem and not px).
**Its lasting deliverable is the fence, not the ramp:** conformance gained a fifth rule for
raw Tailwind palette classes, per-file baselines that only go down. `bg-sky-500/10` passed
every previous regex — it is a named class, not a bracket class — which is how ~950 of them
accumulated tree-wide. ⚠️ Measured, and the ticket was wrong: `/tasks` held **142** across
13 files, not 52; the tree holds **952** across 77.

**WS-27ag — the house shell, and a mobile UI at all.** 🟢 AGENT-SAFE. ✅ **BUILT
2026-08-10** — see §11.20 for the as-built record.

> ### ⚠️ Owed at review — the check no test in this tree performs
>
> Neither af nor ag could run a browser (Playwright's download fails in the build
> environment), so **the phone-viewport pass and the Fluent → Material → Graphite sweep did
> not happen** for either slice. Both compensated honestly — a production `next build`,
> icon names verified against the theme registry, a hand-traced z-order, and for the ramp,
> the shipped values rendered to PNGs as real composites and inspected, including under a
> simulated deuteranopia transform. That last produced a finding worth keeping: **eight
> qualitative hues collapse to about four under dichromacy** (1/4, 2/8, 6/7 merge), which no
> eight-hue palette survives — so every shipped use pairs the hue with the label it colours,
> and the limit is written into `themes.ts`, `categorical.ts` and `DESIGN_SYSTEM.md` rather
> than left implicit. None of that substitutes for looking at the running app on a phone in
> four themes. That gate is still open.

**S3 — selection and bulk-action grammar parity (`/tasks` conforms to `/projects`).**
🟢 AGENT-SAFE, frontend only, no migration. ✅ **BUILT 2026-08-10 for the LIST surfaces**
*(owner observation: "Tasks shows the selection checkbox only when the appropriate setting
is there in the select options on top; in the Projects app the checkbox for selecting a
task is present." Owner ruling: Projects is canonical.)* Branch `ws-s3-selection-bulk-parity` — **MERGED to `main` and DEPLOYED.**

- **`selectMode` is no longer a mode.** It is a derived mirror of `selectedIds.size > 0`
  maintained in one helper (`taskStore.applySelection`), and it decides only whether the
  bulk bar is up. The "Select" button and `setSelectMode` are gone; `ItemList`,
  `TaskListGrouped` and `FlatList` do not read `selectMode` at all.
- **The checkbox is unconditional and OUTSIDE the row content** — its own gutter beside the
  drag grip, so a click on the row still opens the task and the two gestures stop competing
  for one gutter. Drag-reorder is therefore no longer switched off while something is
  selected.
- **Shift-sweep is ungated** on both list surfaces (it required select mode; Projects never
  did), through the same `@/lib/selection` + `@/lib/cursor` both apps already shared.
- **The bulk bar converged on Projects'**: top-mounted on the same `border-b border-border
  bg-muted px-3 py-2` chrome, built from `Button`/`Badge` instead of the hand-rolled
  outline `<button>`s (AGENTS.md rule 3 — the conformance regexes only see solid fills, so
  CI had been silent on them).
- **Select-all added** (`selectAllVisible`), and it means the FILTERED set, not the store.
  With it, `pruneSelection` — a selection that outlived its filter is how a bulk archive
  hits rows nobody can see, the same defect Projects' page already prunes for.
- **Bulk power parity is not faked.** `/items/bulk` takes a disposition and
  `/items/bulk-archive` an archive flag; that is the entire /tasks bulk surface, and
  `gtd_items` has no tags column at all. Archive/restore/delete is the honest set and the
  bar says so. ⚠️ The ticket's premise that status/assignee/importance "do not exist on
  `gtd_items`" is wrong — the *fields* exist (`workflowStage`, `assignee(s)`,
  `important`/`leveraged`); what is missing is any bulk endpoint for them. Widening the bar
  is a gateway ticket.
- **Fence:** `app/tasks/lib/selectionParity.test.ts` (13 cases) — the store invariant after
  every transition, the absence of `setSelectMode`, the three surfaces not reading
  `selectMode` or drawing a conditional checkbox, and both apps' bar carrying the same
  chrome string. Verified by mutation: forcing `selectMode: true` fails 3 cases, re-gating
  FlatList's checkbox fails 2.
- ⚠️ **Not this slice, and still modal:** `TaskCard`/`TaskBoard` (the board's card checkbox
  — owned by S1) and `WaitingForView` (owned by nobody in this wave) still draw their box
  only while `selectMode` is true. Since `selectMode` is now derived, the FIRST pick on
  those two surfaces has to come from Select-all or from a list surface until the card-side
  move lands. **No browser was run**, so the phone-viewport and four-theme pass is owed
  here as it is for af/ag.

**Open, and needing an owner ruling** *(surfaced by af, 2026-08-10)*: WS-27ad standardised
the **shared** card title on `text-[13px]` — deliberately choosing `/tasks`' size as the
common one — while af established the house scale as `text-sm`/`text-xs`/`text-[11px]`/
`text-[10px]` and removed every other off-grid size. So the one remaining off-grid size now
lives in `src/components/TaskCardShell.tsx:120`, which **both** apps render. The two
decisions contradict; changing it repaints Projects as well as Tasks, so it was left alone
rather than settled by whichever slice touched it last.

---

### 9.3 The Plane traceability queue (minted 2026-08-10 from the P-1…P-31 audit)

The 2026-08-09 Plane research recorded 31 findings and a 14-item frontend list. Minting
WS-27u–z and WS-27aa–ae carried most of them, but a re-derivation from the CODE (not from the
write-ups) on 2026-08-10 found four that **nothing owns**, plus two adoption triggers that name
tickets which do not exist. They are collected here so the loss is recorded once and closed
deliberately, rather than rediscovered by a third reading of the same research.

> **Why this happened, since the mechanism matters more than the four items.** The research
> numbered its *findings* P-1…P-31 and its *frontend list* 1–14 **independently**, and §8's
> consolidated verdict table — the table the minting actually read — maps only P-numbers. So
> frontend item 14 was never eligible to be minted: it has no P-number, and a `P-\d+` grep over
> the two documents can never reveal its absence. The fence against a repeat is not a test but
> a rule: **anything that earns a verdict earns an identifier in the table that minting reads.**
> Advisory — nothing in this tree can test a document's completeness.

**WS-27ah — the four the mint dropped.** 🟢 AGENT-SAFE. Frontend plus one gateway aggregate;
no migration.
Done when: (1) **P-8, child category distribution** — `subtask_progress` returns the children's
distribution across status *categories* beside `{done, total}`, and the panel's progress bar
draws segments instead of one `bg-primary` fill. Cheap (one grouped aggregate in
`relations.py`) and it unblocks (2). ⚠️ Its stated trigger — "when the panel draws segments" —
has half-fired: `RelationsBlock.tsx` now draws a *bar*, which is the shape that wants segments,
but the datum cannot express them. (2) **Click a progress segment to filter by that status
group** (research §4 item 14). (3) **P-22, timeline polish** — zoom presets (week/month/quarter
as px-per-day steps) replacing the fixed `PX_PER_DAY = 24`, drag-a-bar-edge to set dates, and
hover-a-dateless-row to place it with a one-day default. ⚠️ **Read `TimelineView.tsx` before
starting**: the dateless row currently renders static text whose `title` asserts *"there is
nothing to place"* — the code argues the opposite of this ticket, so a future reader would not
recognise the gap. Whichever way it lands, that comment must stop contradicting the product.
**Keep** the dependency arrows and D-PM-12's warn-never-reschedule. (4) the rest of research §4
item 14: **create-form draft restore** (one localStorage slot, restored on reopen),
**pinned projects/views** at the tree top (flat, no folders), and a **capped recently-viewed
list** in MyWork. (5) The fifth sub-item, the tiered `EmptyState` primitive, **already exists**
— built incidentally by S4 at `src/components/EmptyState.tsx` rather than by this research —
but it landed in `src/components/`, not `ui/` as §4 asked, and `app/tasks/components/ItemList.tsx`
still declares a **second, local** `EmptyState`. Retire the duplicate and add the SEAM row to
`src/lib/sharedTaskUi.test.ts`, or record why not.

**WS-27ai — the notifications inbox (the half of P-20 that was descoped in flight).**
🟢 AGENT-SAFE.
WS-27v shipped the split unread counts (`NotificationBell.tsx`, `lib/notifications.ts`) and
wrote "(P-20 **part**)". The remaining part was never minted, never banked, and appears in no
deferred basket — the parenthetical is its only trace, which is how a descope becomes a
disappearance.
Done when: a two-pane inbox (list + the embedded task panel), all/mentions tabs each with their
own unread count, **mark-read-on-open**, and snooze-later. ⚠️ The bell deliberately does the
*opposite* of mark-read-on-open today and says so in its own header — that is a real decision,
not an oversight, so this ticket must either reverse it explicitly or keep it and drop that
done-when. Do not "fix" it silently.

**WS-27aj — the two adoption triggers that name no ticket.** 🟡 One half needs an owner ruling.
(1) **P-9 / generic import provenance.** `161_projects_tenancy.sql` defers the per-org ClickUp
constraint to "the ticket that onboards the second tenant" — **no such ticket exists on WS-27 or
WS-29.** Consequence, stated plainly: `clickup_id` is globally unique, so **two organisations
cannot import the same ClickUp workspace**, and nothing on the board says so. The research's
preferred shape is a generic `(external_source, external_id)` pair rather than a ClickUp-specific
column. Cheap now, expensive after the second tenant exists — which is the definition of a
retrofit trap. (2) **Research §6's "restricted" grant level** for contractors and clients: prose
with no P-number, no D-number and no home. It self-defers ("hold until a real external
collaborator shows up") but nothing will surface it when one does. ⚠️ A third visibility level
would touch D12's two-axis model, so this half is an **owner decision, not an agent ticket**.

**Two prose adoption instructions buried in research §2's "KEEP OURS" table** — the one table
nobody re-reads, because it is framed as validation rather than as work. Recorded here so they
are not lost a second time: (a) *"borrow their machine-readable error codes in per-task
outcomes"* — `bulk.py` is half-converted (`"not_found"` and `"unchanged"` are codes,
`"reason": str(exc)` is a human string a client cannot branch on); (b) *"every new list badge
must be page-batched, never per-row"* — honoured today by `filters.attach_relation_counts`, but
stated as a **requirement with no fence**, which is an R7 gap: nothing fails if the next badge
is written per-row.

---

### 9.4 The second-pass Plane queue (minted 2026-08-10 from a full-monorepo read)

The first pass (§9.1, research §3/§4) ranked *features* and stopped there. On the owner's
instruction — *"read the entire code and lift the features and the specifications from
them"* — six agents read all ~3,000 TypeScript files plus the API: `apps/web`, the API and
data model, `packages/editor` + `apps/live`, `packages/ui` + `propel`, `apps/space` +
`admin` + i18n, and a traceability audit (§9.3). This section carries what the first pass
could not see, because it was looking at the wrong layer.

> ### The finding that reframes the whole exercise
> **Almost none of Plane's interaction quality is Plane's.** `packages/propel` is a thin
> wrapper over **Base UI** (MIT); the editor is **TipTap + ProseMirror** (MIT); the
> collaboration layer is **Yjs + Hocuspocus** (MIT); the palette is **cmdk**, the date
> picker **react-day-picker**, the charts **Recharts**, the drag-and-drop
> **pragmatic-drag-and-drop** (Apache-2.0). The AGPL wall blocks their *glue and their
> menus* — which we would have to rewrite for `DESIGN_SYSTEM.md` conformance anyway.
> **The substrate is a shopping list, not a wall.** Verify each licence at install time;
> `node_modules` was absent from the read clone, so versions come from their manifest.
>
> Two consequences worth stating plainly. **We already ship TipTap v3** and use it in
> `src/app/email/components/SignatureEditor.tsx`, so a rich comment box is *extensions*,
> not an engine. And **we ship no headless-primitive library at all**, which is why we have
> seven hand-rolled modals and no focus trap anywhere in the tree.

**Where we are AHEAD, recorded so nobody "improves" us backwards.** Plane has **zero**
`prefers-reduced-motion` handling repo-wide; strips the focus ring from every button
variant without replacing it; has no automated contrast test; runs one theme axis
(light/dark/contrast) against our style × mode; imports `lucide-react` directly
everywhere; and carries two parallel UI packages with duplicate primitives — the exact
"second implementation of an existing seam" our CLAUDE.md §5 forbids, visible in someone
else's tree as evidence for the rule. **Our theming engine is the better engine.** Every
motion behaviour taken from below ships behind our existing reduced-motion rule; theirs is
a counterexample, not a model.

#### 9.4.1 Owner decisions owed — these gate the tickets under them

> ### ✅ **D-PM-15 — ANSWERED 2026-08-11: Base UI.**
> `DECISION (owner-answered 2026-08-11).` Chosen over Radix and over building on
> `floating-ui`, on the evidence below: **Plane's `propel` and Paca (17 of its 24 primitives)
> each chose Base UI independently, for exactly the primitive set WS-27ak enumerates.**
>
> **What this unblocks:** WS-27ak, in the debt order §9.7.2 fixed — **Modal → Tooltip → Toast
> → Skeleton**. Measured at `ebf68f4e`: **zero focus traps** across **69** hand-rolled
> `fixed inset-0` overlays · **no toast system at all** (no `useToast`, no `<Toaster>`, no
> `ToastProvider`) · native `title=` tooltips in **157** files · `animate-pulse` improvised in
> **26**.
>
> **Three conditions this decision carries, each of which has already failed somewhere we can
> point at:**
> 1. **Every primitive gets a Metorite wrapper in `src/components/ui/`** carrying
>    `.cc-control`, resolving icons through `<Icon name>`, using only semantic tokens. Call
>    sites import ours, never the library's, or the library's defaults become a second design
>    system. **R7: the conformance suite gains a rule naming that import restriction, or the
>    rule is advisory.**
> 2. **One substrate, and the rule binds vendored registries too.** Paca's `package.json`
>    carries Base UI **and** `radix-ui`, the second reaching exactly one file, inherited from a
>    vendored component registry. That is the second-substrate failure walking in the back
>    door — observed, not hypothesised. A `cva`/shadcn-style registry drop is the usual vector.
> 3. ~~**Base UI has no Combobox.**~~ 🔴 **FALSE — corrected 2026-08-11, hours after this
>    decision was recorded, and the correction is worth more than the rider.** Verified against
>    the registry rather than against our notes: `@base-ui/react@1.7.0` **ships `combobox`**,
>    and also `tooltip`, `toast`, `dialog`, `drawer`, `context-menu`, `alert-dialog`,
>    `autocomplete`, `select`, `popover` and `menu`. Only **`skeleton` is genuinely absent**,
>    so WS-27ak item 5 stays a build and items 1–4 are all served.
>    **How the error got in:** the "no Combobox" claim was read off the `@base-ui-components`
>    line inside the *pinned* Plane and Paca clones — stale by a package rename plus seven
>    minor versions. It is the same failure mode this file keeps recording: a measurement
>    taken from someone else's manifest and never re-derived from the thing itself.
>    **Consequence:** WS-27bc's entire rationale for running *ahead* of this decision is gone.
>    It is re-sequenced behind WS-27ak in §9.7.2, and separately NO-GO on its own contract.
>
> ⚠️ **Verify each licence at install time** — and it has now been done, which is the only
> reason the error above was caught. **The package is `@base-ui/react@^1.7.0`.** Licence read
> from the package itself (`npm pack` + extract): `package/LICENSE` is the 17-line MIT text
> ("Copyright (c) 2019 Material-UI SAS"), `package.json` `"license": "MIT"`. Five runtime
> dependencies, **all MIT** (`@babel/runtime`, `@base-ui/utils`, `@floating-ui/react-dom`,
> `@floating-ui/utils`, `use-sync-external-store`); `sideEffects: false`, tree-shakes per
> subpath. React peer `^17 || ^18 || ^19` — we are on **19.2.4** ✓. `date-fns` appears in
> `peerDependencies` but is **optional** (date pickers only), so no second date library rides
> in.
> 🔴 **Do NOT install `@base-ui-components/react`** — the name our research implies. It is
> **deprecated** ("Package was renamed to @base-ui/react") and its `latest` is stuck at
> `1.0.0-rc.0`, so installing the name the evidence points at ships a release candidate of a
> renamed package.
> ⚠️ Next 16.2.6 / Turbopack interop is **unverified** — it could not be checked without
> installing. Stated as unknown rather than assumed.

**~~D-PM-15 (owed, but now evidenced)~~ — the headless-primitive substrate.** Base UI vs Radix
vs Headless UI. Everything in WS-27ak depends on it and **picking two would create the
parallel seam our own rules forbid**, so this is one choice made once.

> ✅ **The Paca read (2026-08-10) turns this from taste into evidence.** Paca ships
> **Base UI** as the substrate for **17 of its 24 primitives**; Plane's `propel` is *also* a
> thin Base UI wrapper. **Two independent products, independently, chose Base UI for exactly
> the primitive set WS-27ak enumerates.** That is the strongest external signal this decision
> is going to get.
> Two riders that change the ticket rather than the choice. **(a) Base UI has no Combobox** —
> every "pick from a long list" surface in Paca is a hand-rolled search input inside a
> Popover. So the substrate closes WS-27ak items 1, 2, 3 and 5, and **item 4 stays a build
> whichever library wins**; its behaviour is specified in §9.5 and can be written now.
> **(b) The rule must bind vendored registries too.** Paca's `package.json` carries Base UI
> **and** `radix-ui` — the second reaching exactly one file, inherited from a vendored
> component registry. That is the second-substrate failure walking in the back door, which is
> the mechanism this decision exists to prevent, observed happening in someone else's tree. What it buys: a real focus trap,
focus return, scroll-lock with scrollbar compensation, collision-aware positioning,
roving tabindex, typeahead — the behaviours nobody hand-rolls correctly. Every primitive
still gets a Metorite wrapper in `src/components/ui/` carrying `.cc-control`,
resolving icons through `<Icon name>`, using only semantic tokens; call sites import ours,
never the library's, or the library's defaults become a second design system. **R7:** the
conformance suite gains a rule naming the import restriction, or it is advisory.

**D-PM-16 — org-wide vocabularies. ✅ OWNER-RULED 2026-08-14: adopt the nullable project
scope.** Plane's tags, custom fields and task types carry a **nullable** project scope, so a
vocabulary row is either org-wide or project-local, with paired partial-unique constraints.
Ours were all `project_id NOT NULL`. ⚠️ This was the most expensive item in its section if
got wrong: dropping NOT NULL later is trivial, but merging the duplicate rows twelve root
projects will each have accumulated — their own "Bug", "urgent", "Client" — is a
judgement-call migration nobody can automate. The ruling is what stops that merge ever
becoming necessary.

**`project_id IS NULL` means org-wide.** Three findings from the audit that set the shape:

✅ **There is no R5 gap, and this is what makes the ruling cheap.** All three tables already
carry `organization_id NOT NULL` from **migration 161** (`161_projects_tenancy.sql:109/120/121`,
backfilled at 341/352/353, tightened at 368/379/380). So a row with `project_id IS NULL` is
still tenant-anchored — it is org-wide *within one organization*, never global. Had the tenant
anchor been reached only through `project_id → pm_projects`, nulling it would have produced
untenanted rows visible to every tenant, and this ruling would have needed a migration to fix
that first.

⚠️ **These are ROOT-project scoped, not per-project.** `pm_custom_fields` and `pm_tags` both say
so in their own headers ("configuration is root-scoped and the subtree inherits… so a task moved
between subprojects keeps tags that still mean something"). So the duplication being prevented
is per **root** project, and the new axis is root-local vs org-wide — there is no third level.

🔴 **Shadowing must be ruled, because for tags one name is one string.** `pm_tasks.tags` stores
the tag's **display text**, not a foreign key ("the exact text stored in every `pm_tasks.tags`
entry for this tag"). An org-wide `bug` and a root-local `bug` are therefore *the same tag* on
every task, while being two registry rows carrying two colours. The union must resolve to
exactly one row per identity or the colour is ambiguous — the same class of failure the table's
own comment warns about for `' bug'` versus `'bug'`. **Rule: most specific wins — a root-local
row shadows an org-wide row of the same identity**, and it needs a test, not a convention.

Constraint shape, paired per table, on each table's existing identity (`name` for task types,
**`lower(name)`** for tags because tag identity is already case-insensitive, `field_key` for
custom fields):

```
UNIQUE (project_id,      <identity>) WHERE project_id IS NOT NULL   -- root-local
UNIQUE (organization_id, <identity>) WHERE project_id IS NULL       -- org-wide
```

**R6 — this is an expand, and it contracts nothing.** Dropping NOT NULL only widens what is
accepted, so old code (which always writes a `project_id`) keeps working unchanged, and old
readers filtering `WHERE project_id = :x` simply do not see org-wide rows — invisible, not
broken. **Ship dark**: the read-path union is harmless, so the flag belongs on the affordance
that *creates* an org-wide row, which is the irreversible half.

**D-PM-17 (owed) — the i18n discipline (not i18n itself).** Plane's numbers are one
forecast: 28 namespaces × 19 locales ≈ **5,181 keys per locale**, for a surface *smaller*
than ours.

> ⚠️ **Two corrections from the Paca read (2026-08-10), both of which make adoption look
> cheaper than the Plane-only figure implied.** (1) **`i18next-icu` is not needed for
> plurals.** Paca's Russian bundle carries `_one/_few/_many/_other`, i.e. CLDR plural
> categories come from **i18next core via `Intl.PluralRules`**; ICU is only required for
> select/ordinal/nested constructs. My earlier text named ICU as the plural mechanism and
> that was wrong. (2) **The scale is not fixed by the library.** Paca does the same job in
> **10 namespaces × 1,644 keys per locale** against Plane's 28 × 5,181 — so the honest
> forecast is a range, and the shape of the surface drives it more than the tool does.
> The recommendation below is unchanged, and the second correction strengthens it: the
> cheap discipline is worth adopting precisely because the expensive half is not fixed. The cost splits in two and only one half is avoidable: translation is
unavoidable and unchanged by anything we do today; **extraction** — walking every
component, finding every literal, inventing a key — is the expensive, unreviewable,
long-branch half, and it collides head-on with our "keep branches short" rule. The
recommendation is deliberately modest: **do not adopt i18n now; stop making it more
expensive every week.** New surfaces put user-facing strings in a per-surface keyed module
rather than inline in JSX; never build a sentence by concatenation; never branch on count
in TypeScript (ICU cannot absorb either). Then adoption is "move a file", not "read every
component". Binds new and changed work only — the existing tree is a finding, not a
refactor. The libraries are MIT (`i18next` + `i18next-icu`) if we ever do adopt.

**D-PM-18 (owed) — RTL as a design-system rule.** Plane punted entirely, so adopting their
i18n buys us **nothing** here. RTL is logical CSS properties (`margin-inline-start` over
`margin-left`), mirrored iconography and directional layout — it lands on
`DESIGN_SYSTEM.md`, not on strings. Writing logical properties in a centrally-themed system
costs **nothing today** and is a full-surface sweep later. This is a one-line addition to
an existing seam, which is exactly the kind of change that is free now and impossible to
schedule later.

#### 9.4.2 Tickets

**WS-27ak — the primitive layer.** ~~🟡 Blocked on D-PM-15.~~ 🟢 **AGENT-SAFE** for items 1, 3 and 5 (item 2 see the `HoverPopover` note; item 4 now served by the substrate). **D-PM-15 is answered — Base UI — 100 lines above this ticket in §9.4.1, and this header contradicted it for hours.**
Sequenced by debt, not by ease: (1) **`Modal`** first — seven hand-rolled copies, **zero
focus traps**, and it proves the theming wrapper on the hardest case. The behaviour a
dialog owes: focus moves in on open; Tab wraps at both ends; the background is `inert`, not
merely covered (so find-in-page and a screen reader cannot walk into it); scroll locked
with scrollbar-width compensation so the page does not shift; Escape captured *at the
dialog*, not on `document` where it races every other listener — our current shape; focus
returns to the opener, or a sensible fallback if it unmounted, never `<body>`; `role` +
`aria-modal` + `aria-labelledby`/`describedby`; and outside-click dismissal only when the
press both started *and* ended outside, so a text selection dragged out of the dialog does
not close it. (2) **`Tooltip`** — we use the native `title` attribute in ~157 files.
(3) **`Toast`**, with the promise-bound form (`loading → success | error` mutating one
toast in place, actions derived from the resolved value) — it is also the delivery vehicle
for the copy-link affordance. (4) **`Combobox`** — our `Select` is a styled native
`<select>`, so every "pick from a long list" surface is unserved. (5) **`Skeleton`** —
~20 files improvise `animate-pulse`.

> ### ⚠️ Audit outcome 2026-08-11 — **GO-NARROWED to item (1) Modal**, after doc repairs
>
> 🟢 **Item (1) BUILT 2026-08-11, REPAIRED the same day — `ws-27ak-modal-repair`, NOT merged,
> NOT deployed. Full record, the two done-whens that could not be met as written, and repair
> round 1: §11.31.** Items (2)–(5) unchanged by that slice.
>
> **Done when:** `src/components/ui/Modal.tsx` wraps **`@base-ui/react@^1.7.0`**'s `dialog`
> (see D-PM-15 for the verified package, licence and the deprecated name NOT to install), is
> the only import of that library outside `src/components/ui/`, and **all six of `/projects`'
> hand-rolled dialogs render it**. The six are not a guess — a previous author already
> enumerated them in code at `app/projects/page.tsx:968-974`'s `overlayOpen`:
> `ShortcutsSheet.tsx:47` · `SearchPalette.tsx:162` · `ImportClickUp.tsx:166` ·
> `FieldManager.tsx:122` · `TagManager.tsx:86` · `LifecyclePolicy.tsx:62`. Each owns its own
> `fixed inset-0`, so **`page.tsx` is not touched** — which matters, because it is the hottest
> file in the tree and was edited by both of today's merges.
>
> 🔴 **One done-when was factually impossible and is restated, not dropped.** The ticket said
> *"Escape captured **at the dialog**, not on `document` where it races every other
> listener"*. **Base UI does exactly what that forbids** —
> `@base-ui/react/floating-ui-react/hooks/useDismiss.js:419` binds `keydown` on
> `ownerDocument(...)`. So the sentence cannot be satisfied by the substrate D-PM-15 chose.
> The observable it was reaching for, which IS testable: **with a dialog open, one Escape
> closes exactly one surface and `page.tsx:1039`'s window handler does not also fire.**
> That handler already returns early on `overlayOpen` (`:1003`), so the real risk in this
> slice is that a wrapper taking ownership of open state silently breaks that suppression —
> after which `g`-sequences navigate out from under a half-filled ClickUp import form.
> **That is the highest-value adversarial test here**, and it is Playwright-observable.
>
> ⚠️ **A second obligation the ticket implies but does not state:** Base UI's default
> `outsidePressEvent` is `'sloppy'` (fires on `pointerdown`); the ticket's "press must start
> *and* end outside" is `'intentional'`.
> 🔴 **Corrected 2026-08-11 at build time — "the wrapper sets it" is not possible. There is no
> `outsidePressEvent` prop on `Dialog.Root` in `@base-ui/react@1.7.0`**;
> `dialog/root/useDialogRoot.mjs:23-33` computes it internally and returns `'intentional'`
> whenever a backdrop element exists. So the source scan pins the **rendered
> `Dialog.Backdrop`**, not a prop name that does not exist (§11.31). The
> pattern is the one this file keeps recording: an API read off notes rather than
> re-derived from the package.
>
> 🔴 **There is already a Modal in this tree, and building the ticket as written authors a
> second one** — the WS-27bd(5) ContextMenu situation repeating.
> `app/email/components/automation/ui.tsx:15` exports `Modal({title, description, onClose,
> children, footer, maxWidth})` with **five** consumers, whose own docstring says it is
> *"kept dependency-free… to match the rest of the email app, which hand-rolls its overlays."*
> It has window-Escape and backdrop dismiss, and **no** focus trap, focus return, scroll lock,
> `role` or `aria-modal`. This slice leaves its five call sites alone and records the
> retirement, exactly as ContextMenu did. (The same file's `HoverPopover:132` is a partial
> answer to item (2), which is another reason Tooltip is not first.)
>
> **Re-measured — five of the ticket's numbers are wrong.** "69 files `fixed inset-0`" is
> **70 files / 95 occurrences**, of which only **60 across 48 files are dialogs**: 21 are
> empty dismiss-scrims for dropdowns, 12 are drawers/bottom-sheets/full-screen modes, 2 are
> prose in comments. "Seven hand-rolled copies" derives from nothing; `/projects` has **six**.
> ✅ Zero focus traps and zero `inert=` attributes: **confirmed**. ✅ `src/components/ui/`
> **already exists** (`Badge`, `Button`, `Input`) — this extends a home, it does not mint one.
>
> **The free harness is the argument for Modal first.** Driven in Chromium at `00c47c6b` with
> **zero API mocking** (`/projects` renders without auth; `?` opens `ShortcutsSheet`):
> `dialogs: 1` · `activeElement after open: BODY` (focus never moves in) · `focus inside after
> 6 Tabs: false ×6` (no trap — focus walks the background) · `[inert] elements: 0` ·
> `activeElement after close: A`, an arbitrary anchor rather than the opener. The hardest
> primitive is the one with the cheapest fence.
>
> ⚠️ **Two costs to know before dispatch.** (1) Conformance **rule 8** (no `@base-ui/react`
> import outside `src/components/ui/`) trips `conformance.test.ts`'s own rule-count fence,
> which asserts that **`AGENTS.md` and the root `CLAUDE.md`** both quote the same number —
> all three say "seven" today, so this slice necessarily edits the root `CLAUDE.md`. That is
> mechanical and intended; it should not surprise a reviewer. (2) `DESIGN_SYSTEM.md` has **no
> overlay/z-index section at all** — the tree uses ad-hoc `z-40…z-95` and backdrops split
> across `bg-black/40|50|60|70`, `bg-background/70|80`, `bg-foreground/20`, and `bg-black/60`
> passes all seven conformance rules today because `PALETTE_CLASS` lists only the numbered
> ramps, not `black`/`white`. The wrapper must pick a token deliberately **and**
> `DESIGN_SYSTEM.md` must gain a short overlay section, or this ships the eighth backdrop.
>
> ⚠️ **A seam tension to state rather than resolve silently:** Wave 1 shipped
> `src/lib/outsideClick.ts` whose docstring names *"Wave 2's Modal / Tooltip / Combobox"* as
> why it exists ahead of need. Base UI brings its own outside-press handling, so a Base-UI
> Modal will **not** consume it. That is acceptable — but it must be written down, or the tree
> has two answers and no record of why.
>
> **🔴 Items (2) Tooltip and (5) Skeleton are NO-GO as written**: their entire done-when is a
> count of the problem ("~157 files use `title=`", "~20 files improvise `animate-pulse`"), not
> a definition of done. Item (3) Toast has a real criterion and survives. Do not dispatch
> (2) or (5) until each has acceptance.

> ### ✅ Acceptance criteria for items (2) Tooltip and (5) Skeleton — written 2026-08-11
> Both were **NO-GO** because their entire done-when was a count of the problem
> (*"we use the native `title` attribute in ~157 files"*, *"~20 files improvise
> `animate-pulse`"*) rather than a definition of done. A count is a reason to act, not a
> description of what "acted" looks like. Written here by the workstream owner so they stop
> being undispatchable; scope only — the sequencing in §9.7.2 is unchanged.
>
> **Item (2) — `Tooltip`. Done when:**
> 1. `src/components/ui/Tooltip.tsx` wraps `@base-ui/react`'s `tooltip` and is covered by
>    conformance **rule 8** (no new rule; the count is pinned by a fence across three files).
> 2. **Hover-intent, not raw hover** — a delay before showing (~400ms) and a *shorter* delay
>    on the way out, so crossing a toolbar does not strobe every icon. This is the whole
>    reason the native `title` feels broken, and it is the criterion to hold.
> 3. **Reachable without a pointer**: it shows on keyboard focus, not only on hover, and
>    dismisses on Escape. The native attribute never appears on focus at all.
> 4. **Touch has an answer, even if the answer is "nothing"** — `(hover: hover)` /
>    `(pointer: fine)`, never UA-sniffing (§9.4.4 refuses that by name). A tooltip that is
>    unreachable on a phone must not be the only carrier of information the user needs.
> 5. **It is not the accessible name.** `aria-describedby`, and the trigger keeps its own
>    label — a tooltip used *as* the label disappears for anyone who cannot hover.
> 6. ⚠️ **`app/email/components/automation/ui.tsx:132` already exports `HoverPopover`**, a
>    partial answer with its own consumers. Same treatment as `Modal`/`ContextMenu`: leave it,
>    record it for retirement, do **not** author a third.
> 7. **Convert a named starting set, not "157 files"** — a slice picks one surface
>    (`/projects`' toolbar is the obvious first), converts it completely, and the rest follows
>    per surface. A sweep of 157 files is a long branch, which is the root cause behind three
>    migration collisions and a duplicated tenancy design.
> **Fences:** hover-intent timing is pure logic in a `.ts` module (delay in, state out) and
> unit-testable; show-on-focus and dismiss-on-Escape are Playwright (D-PM-21); "not the
> accessible name" is a structural scan. Do not fence 7 with a count — a ratchet on
> `title="` occurrences would make every unrelated file's edit a chore.
>
> **Item (5) — `Skeleton`. Done when:**
> 1. `src/components/ui/Skeleton.tsx`, themed, replacing improvised `animate-pulse` at a
>    **named** starting set of surfaces (same per-surface rule as above).
> 2. **It respects `prefers-reduced-motion`** — we already hold this rule tree-wide and the
>    upstream reference does not (§9.4's "where we are AHEAD"). A pulsing skeleton is exactly
>    the animation that triggers people.
> 3. **Irregularity is derived, never random.** §9.4.4 refuses `Math.random()` in a skeleton
>    render by name: it re-randomises every render and makes the placeholder shimmer *shape*
>    change under the reader. Hash the row index.
> 4. **It matches the shape it replaces** — a skeleton whose height differs from the loaded
>    row causes a layout jump on arrival, which is worse than a spinner. Same line count, same
>    approximate widths.
> 5. **A minimum on-screen time** (~300ms) once shown, so a fast response does not produce a
>    flash of skeleton — the defect that makes teams rip skeletons out again.
> **Fences:** 3, 4 and 5 are pure logic (hash→width, shape derivation, the min-display state
> machine) and belong in a `.ts` module; 2 is a structural scan for the reduced-motion guard.

**WS-27al — the logic-only wins.** 🟢 AGENT-SAFE, no library, no design decision, no
dependency on D-PM-15. The cheapest real quality in this whole section:
(1) **`ControlLink`** — a row that is a real `<a href>` but intercepts plain left-click to
open the panel, so cmd/ctrl/middle-click still open a new tab. Our clickable rows are
`<div onClick>`; today we are the one place on the user's machine where that is broken.
(2) **`data-prevent-outside-click`** — outside-click dismissal walks up from the target and
bails on the attribute, so a picker portalled out of a dropdown stops closing the dropdown
underneath it. ~15 lines, no ref plumbing between components that do not know each other.
(3) **Lazy tooltip mounting** — mount positioning machinery on first hover, not for 1,200
rows that will never be hovered. (4) **Selected-first ordering** in multi-selects, sorted
**on open and frozen while open** (theirs re-sorts live, so the option you just ticked
jumps under your cursor). (5) **One overdue predicate** — never true for a done or
cancelled item, and **today counts as due**; ours must be one function, not seven.
(6) **Selection self-heals**: when the filtered list changes, drop selected ids that are no
longer present, so a bulk action cannot fire at something off-screen.

> ### ⚠️ Audit outcome 2026-08-11 — **GO-NARROWED to (1), (2) and a subset of (5)**
> Re-derived from the tree, not from this ticket's own prose. **Two of six items describe
> work that already exists**, which is what an unaudited "measured" header buys you.
>
> **Done when:** (1) `TaskList.tsx` and `TableView.tsx` render a `ControlLink` in the task
> **title cell** — ⚠️ *not* around the row: both are `<tr onClick=…>` (`TaskList.tsx:318-321`,
> `TableView.tsx:533-541`) and an `<a>` cannot wrap a `<tr>`; a pure `src/lib/controlLink.ts`
> passes a test asserting plain left-click intercepts while `metaKey`/`ctrlKey`/`shiftKey`/
> `altKey`/`button===1` do not (**assert middle-click explicitly — the upstream reference
> missed exactly that case**); and a structural assertion in the `sharedTaskUi.test.ts:332`
> idiom proves both files *render* it, not merely import it. (2) an outside-click walker bails
> on `data-prevent-outside-click`, consumed at `NotificationBell.tsx:75-80`, with the
> parent/attribute accessor **injected** so it is testable in this tree's node environment.
> (5) `app/projects/lib/mywork.ts:64` stops disagreeing with the other predicates about
> completion.
> ⚠️ `TaskCardShell.tsx` is **out of scope**: it is a `role="button"` div by the documented
> decision in its own comment at lines 70-73 (nested interactive elements inside an anchor are
> invalid HTML).
>
> **(5) re-scoped — the ticket's own two claims are both wrong.** There are not "seven"
> predicates; there are **three in TypeScript and one in SQL**: the shared
> `src/lib/taskCard.ts:190` (correct — excludes completed) · `src/app/projects/lib/mywork.ts:64`
> (**no completion check at all** — a finished task with a past due date renders overdue in
> MyWork, and *that* is the real defect) · `src/app/tasks/lib/waiting.ts:68` (deliberately
> different under a documented contract — **out of scope, do not merge it**) ·
> `gateway/routes/projects/filters.py:182` (the best of the four: excludes done *and*
> cancelled). Not overdue predicates and not to be merged: `tasks/lib/priority.ts:36,50`,
> `CalendarView.tsx:195`, `StartupRitual.tsx:106` are "due soon" horizon scans.
> 🔴 **"today counts as due" is REFUSED as under-specified and is now an owner question.**
> Adopting it inverts two deliberately-pinned assertions that carry their reasoning in
> comments — `src/lib/taskCard.test.ts:174` and `src/app/projects/lib/mywork.test.ts:35-42`
> ("Pins `<` rather than `<=`: a task is late once the moment has passed, not at the moment
> itself") — **and** changes `filters.py:182`, dragging R8 (verify SQL against a real database)
> and R6 into a ticket labelled "logic-only, no dependency". Our store is timestamp-granular;
> the reference is date-granular. That is a semantic choice, not a bug fix.
>
> **~~(3) lazy tooltip mounting~~ — MOVED to WS-27ak(2), Wave 2.** There is no `Tooltip`
> component in `src/` at all, so there is no positioning machinery to mount lazily; the native
> `title=` attribute has none. The item had no target here and its dependency was invisible.
>
> **~~(4) selected-first ordering~~ — STRUCK pending a named target.** The ticket names no
> multi-select. The nearest candidate, the FilterBar tag row (`FilterBar.tsx:369-385`, already
> ordered `byUsage`), is a permanently-visible chip strip with **no "open" moment**, so
> "sorted on open and frozen while open" is meaningless there. Re-mint when a real
> open/close multi-select exists.
>
> **~~(6) selection self-heals~~ — ALREADY SHIPPED. Struck.** `app/projects/page.tsx:722-733`
> prunes the selection off `onScreen` through `src/lib/selection.ts:105 prune`, and
> `app/tasks/lib/taskStore.ts:923` does the same. Verified verbatim — the shipped comment even
> uses this ticket's own example ("select forty, narrow to three").

**WS-27am — the three-state list surface.** 🟢 AGENT-SAFE.
(1) The **empty-state triad**: filters-active-but-no-match (action: *Clear filters*),
never-populated (action: *Create*), and no-permission — the last renders the CTA
**disabled rather than hidden**, so the user learns the action exists and that they cannot
do it. Our §4.14 asked for a tiered primitive; that is the shape, this is the decision
rule. (2) One **loader/empty/error HOC** per layout so each surface stops hand-rolling its
three states — with their judgement call kept: **an empty calendar still renders**, because
empty chrome is meaningful there and not in a table. (3) A **per-layout error boundary**
whose Retry re-mounts by bumping a key rather than clearing a flag (which re-crashes
instantly). A malformed group shape must not blank the app.

> ### ⚠️ Audit outcome 2026-08-11 — **GO-NARROWED to (3) in full and (1) as a capability**
>
> **Done when:** (3) `src/components/LayoutBoundary.tsx` wraps every canvas rendered by
> `app/projects/page.tsx`, its Retry re-mounts by **bumping a key**, and a structural
> assertion in the `sharedTaskUi.test.ts` idiom proves every canvas is inside it — measured,
> **the tree contains zero error boundaries**: no `ErrorBoundary`, no `componentDidCatch`, no
> Next `error.tsx`, so a malformed group shape blanks the whole app today. (1) the
> **no-permission arm only**: `EmptyState.tsx` gains an *additive optional* disabled-with-reason
> action and `emptyStateCopy` gains the third arm, unit-tested, **wired at no call site this
> wave** (every candidate call site belongs to a parallel agent).
>
> **(1) is two-thirds already shipped.** `src/components/EmptyState.tsx` (promoted by S4) plus
> `app/projects/lib/emptyState.ts:46 emptyStateCopy` already deliver filters-active-but-no-match
> and never-populated, consumed at `TaskBoard.tsx:471` and `TaskList.tsx:207`. Only the
> no-permission arm — CTA **disabled rather than hidden** — is new.
>
> **~~(2) the loader/empty/error HOC~~ — STRUCK for this wave; the doc must name its surfaces
> first.** "One HOC **per layout**" never says which layouts: `/projects` has five canvases plus
> MyWork, and the sentence reads tree-wide. An item that cannot be enumerated cannot be closed,
> so it cannot be dispatched. Re-mint with the surface set written down.
>
> **The duplicate `EmptyState` is NOT this ticket's.** `app/tasks/components/ItemList.tsx:490`
> declares a second one, but **WS-27ah owns retiring it** by name (§9.3, "Retire the duplicate
> and add the SEAM row… or record why not"), and `EmptyState.tsx`'s own docstring already says
> that edit is held by another slice. WS-27ah is Wave 4.
>
> ⚠️ **Two done-whens here are inherently review-only and must not be faked.** "A malformed
> group shape must not blank the app" and "Retry re-mounts rather than re-crashing" require
> rendering a throwing child. This tree **cannot** do that: `vitest.config.ts` is
> `environment: "node"` with `include: ["src/**/*.test.ts"]`, so `.tsx` tests are not even
> collected, and there is no jsdom, happy-dom or `@testing-library` installed. Adding one is a
> substrate decision, not a papercut ticket. A pure test that Retry increments a key asserts
> the arithmetic, not the remount — label it as such rather than letting it read as a fence.

**WS-27an — the inline-autosave contract.** 🟢 AGENT-SAFE. Six behaviours, and the third is
the one everybody omits: 1.5s debounce; save on blur with trim; **save on unmount if
dirty**, so closing the panel mid-keystroke does not lose the edit; empty **reverts to the
last good value** rather than persisting empty, with an inline required message; a
character counter that appears only while focused; and a separate "Saving… → Saved"
indicator. When not editable it renders as plain text, not a disabled input.

**WS-27ao — a rich comment/description editor (Lite tier).** 🟢 AGENT-SAFE, **no new
service**, no schema change. Supersedes the rich-text half of research §5's refusal, which
bundled it with the collab server; the collab-server refusal stands. Smallest slice that
makes the product feel modern: markdown-on-paste and markdown-on-copy (`tiptap-markdown`,
MIT); **mention as a node**, not a text token — but *serialising back to the same
`@address` form*, so `notifications.mentionsIn()` and the whole notification wiring stay
untouched while the editor draws a proper chip; image paste/drop with an optimistic local
preview before upload completes. ⚠️ Two traps read from their implementation: clearing the
composer after submit must be flagged so the asset-GC pass does not delete the images you
just posted; and the two heavy extensions (syntax highlighting, the emoji dataset) are
statically imported there and roughly double the bundle — lazy-load both. **No slash
commands, no collaboration, in this slice.** ⚠️ Their editor has **9 aria attributes across
232 files** and a **Tab keyboard trap by default**; we implement the dropdowns properly and
Tab always leaves the field unless inside a list.

**WS-27ap — the boolean filter tree.** 🟢 AGENT-SAFE, needs a migration. **Expensive to
retrofit.** Our filters are a flat dict, implicitly AND-ed, so "assigned to me **or**
watching, **and not** done" is unexpressible. Theirs is a nested `and`/`or`/`not` grammar
over leaf conditions, with a declared-field allowlist (an undeclared field is a 400, not a
silent drop) and a max nesting depth. Our `build_task_filters` is **already pure, returning
clauses + params** — it is exactly the leaf evaluator; what is missing is the tree walker,
the depth cap and an allowlist we already have in `VIEW_FILTER_KEYS`. Do it their way:
**a new column beside the old, both read, the old one dropped in a later release** (R6) —
their own late converter drops a record's filters silently on failure, which is the lossy
migration to avoid. Every saved view, dashboard widget, agent query and the delta feed's
scope predicate reads that config, so the stored corpus only grows.

**WS-27aq — notification preferences.** 🟢 AGENT-SAFE, needs a migration. One preference
row with **nullable** workspace and project keys, so global default, per-Center override
and per-project mute are one table; plus `snoozed_till`/`archived_at` beside `read_at`.
**The argument for now rather than later: we already shipped watchers with
auto-subscribe-on-touch**, which manufactures volume by design. Without a mute the next
user action is turning the bell off, and the whole watcher feature becomes dead weight.
Widening `pm_notifications.kind`'s three-value CHECK is also cheaper before three clients
hard-code three values. ⚠️ **Their mistake, do not copy:** their preference flags gate the
**email channel only** — the in-app bell is unmutable.

**WS-27ar — favourites/pins and recent-visits.** 🟢 AGENT-SAFE, needs a migration (two
small tables). §4 item 14 already asked for pinned projects/views and recently-viewed
(§9.3's WS-27ah owns the UI). The design point: **one generic
`(user, entity_type, entity_id)` table**, not an `is_pinned` column on `pm_projects`, then
another on `pm_views`, then another on dashboards — four columns, four queries, no
ordering, no folders, and a migration to unify. One table now costs the same as the first
column. ⚠️ Cap recent-visits per user on write; theirs grows unbounded with no sweeper.

**WS-27as — the join-table authorisation audit.** 🔴 SECURITY. Partly done.
Reading Plane's **GHSA-4w5x-wc9w-f47x** (they scoped a cycle-issue join write by issue id
alone, so a caller could re-point another tenant's rows) prompted a check of ours.
✅ **`views.set_positions` FIXED 2026-08-10** — it validated the view and then wrote every
`task_id` unchecked. Remaining: audit every other endpoint taking `task_ids[]` and writing
a join row — `relations.py`, `tags.py`, `watchers.py`, `bulk.py` (bulk already resolves
per task and is clean) — each with a test using **`member_user`, never `projects_user()`**:
the latter holds `*` including `data:org:read`, and a scoping test written with it passes
whether or not the code is correct. That is not hypothetical; the first draft of the
positions fence had exactly that defect.

**WS-27at — the living design-system gallery.** 🟢 AGENT-SAFE. The one item here that
improves our *process* rather than the product, and it targets the gap CLAUDE.md names by
name: the conformance suite checks eight regexes (WS-27ak added rule 8) and **nothing tests layout or cross-app
continuity, so the theme-switch sweep is the real gate** — a manual gate that every slice
this session owed and several skipped. One internal route rendering every token, every
control and every state across all four themes × both modes turns that sweep into one page.
Plane documents its own elevation vocabulary as executable stories showing ✅ correct and
❌ wrong nesting; that is the shape.

#### 9.4.3 Banked, with the trigger that should wake them

- **`?fields=`/`?expand=`** sparse fieldsets — one generic read affordance; the allowlist
  must be server-side or `fields=` becomes a column-name oracle. *Trigger: the first mobile
  or agent client that complains about payload size.*
- **Composite type tokens** (size + leading + tracking + weight in one class, on a rem
  ramp). Fixes a defect `AGENTS.md` already admits: our two arbitrary-px sizes opt out of
  the user's density preference because `--ui-scale` reaches rem and not px. *Trigger: the
  `text-[13px]` vs `text-sm` ruling, which is already owed.*
- **Paired surface state variants** (`-hover`/`-active`/`-selected` per surface token) so
  every surface stops inventing its own hover. Take the state-variant half, skip their
  depth renumbering.
- **Container queries** for panel-local layout — we use **zero** and 517 viewport-breakpoint
  prefixes, while our documented layout is exactly the flex-content-plus-380px-panel case
  container queries exist for.
- **A page-gutter spacing token.** `DESIGN_SYSTEM.md` §6 currently documents `px-4 sm:px-6`
  as a string to retype on every page — the shape of drift the rest of the document exists
  to prevent.
- **Sprint membership exclusivity.** When sprints are built (P-23), the join needs
  `UNIQUE (task_id)`: theirs enforces exclusivity in one handler and not in the schema, so
  one forgetful code path puts a task in two sprints and every burndown is silently wrong.
  Trivial then, a migration later.
- **Per-tenant settings store.** We have a settings *surface* and no settings *store*.
  ~31 of their 36 instance-config keys must be per-tenant for us; the sharp edge is
  **per-tenant OAuth**, which is not a storage problem but a **callback-routing** one (the
  tenant must ride in `state` and be validated, and the login page must resolve tenant
  before offering buttons). Cheap now, an auth-entry-path rewrite later. ⚠️ Credential
  handling is owner-gated (work_plan §6) — this is written up and handed over, never built
  against live credentials.
- **A separate, short-lived admin session cookie.** Their instance admin is a different
  table *and* a different cookie with a 1-hour age. This sharpens our owner-gate registry
  from a behavioural rule into a mechanical one — an owner acting as owner would be on a
  different session from an owner reading their inbox.
- **A grouped-aggregate primitive** for dashboards — one allowlisted endpoint, so every
  widget does not grow its own copy of the visibility predicate. ⚠️ Encode their trap: with
  an M:N axis (tags, assignees) per-bucket counts are distinct but the row total is a sum
  of buckets, so a two-tag task is counted twice and the total exceeds reality.
- **Saved-view lock + archive** (two nullable columns). ⚠️ **Refuse** its sibling — their
  view `access` private/public enum is a second visibility axis and collides with D12.
- **Reactions** on tasks and comments. Adjacent to Chat; a product call, not a UI one.

#### 9.4.4 Added to the refusal list (research §5), with reasons

Their **pervasive soft-delete with an async recursive cascade** — a background task walks
reverse relations and soft-deletes children, printing and skipping per-relation failures.
Three reasons beyond the ones already recorded: it is not atomic with the delete, so
children outlive their parent for an unbounded window; a partial failure leaves a
permanently inconsistent graph with no record; and — **the one that matters for us** — it
interacts badly with a change feed, because children get tombstoned at arbitrary later
times and a delta client observes a parent disappear before its children with no way to
order the two. Our archived-only posture plus the `AFTER DELETE` trigger is the better
answer. · **Their role model**: three ordered integer roles, and a **workspace admin
bypasses project role checks entirely** — a privilege-escalation shape, plus generic 403s
that collide with our 404-never-403 doctrine. Nothing to take; ours is strictly richer. ·
**`ProjectPublicMember`**, a shadow membership table created silently when a non-member
comments on a public board — a second membership vocabulary; for us a public participant
is a **grant**, not a table. · **Secrets decrypted into API responses** and a **Fernet key
derived with the literal salt `"salt"`** — return set/unset, never the value. ·
**`CORS_ALLOW_ALL_ORIGINS` failing open** when an env var is unset, with credentials
enabled and secure cookies disabled — fail closed; refuse to boot. · **`fields = "__all__"`
on public-facing serializers**, which auto-publishes every future column. · **Same-origin
path mounting** of a public surface (it already caused an XSS mitigation in their tree) —
if we ever expose one it goes on a **different origin**, decided before the first link
exists. · **`class-variance-authority`** — a `cva` recipe is exactly the "documented class
string" our `DESIGN_SYSTEM.md` §3 rejected, because a theme's control personality is not
expressible in a class string. · **UA-sniffing for touch** — use `(hover: hover)` and
`(pointer: fine)`. · **`Math.random()` in a skeleton render** — take the irregularity,
derive it from a hash of the row index. · **Unmount-on-scroll virtualization for any
subtree owning unsaved input** — browser find-in-page cannot see it and an in-progress edit
is lost.

---

### 9.5 The Paca queue (minted 2026-08-10 from a full read of the second reference)

Paca (**Apache-2.0**, pinned `09dab28e`) was our *first* reference and its research doc had
**no UI section at all** — the same blind spot Plane's first pass had. Four agents re-read it
in full: `apps/web`, `services/api`, the agent/MCP/realtime layer, and its e2e suite. That pass
found **28 defects in our own Paca record** (recorded at `paca_pm_research_2026-08.md` §10–§11)
and the queue below.

> ### The cross-reference — the one thing neither single-repo pass could produce
> **1. Paca ships Base UI too.** 17 of its 24 primitives. Plane's `propel` is also a Base UI
> wrapper. **Two independent products, independently, chose the same substrate for exactly the
> primitive set WS-27ak enumerates** — that is D-PM-15 answered with evidence instead of taste.
> **2. Paca is ahead on exactly one axis, and it is ours.** Its ~4,500-line agent surface has
> **no Plane counterpart at all** — Plane is not an AI product. Everything in §9.5.1 below is
> therefore single-sourced, and it is the closest external analogue to our own thesis.
> **3. On everything Plane's queue already covers, Paca is the WEAKER reference.** No
> multi-select, no bulk edit, no keyboard cursor anywhere in 61k lines; native HTML5 drag only,
> so its board cannot be reordered on a phone at all; a hand-rolled task modal with no focus
> trap; one theme axis; raw Tailwind palette classes for every categorical hue. **Importing
> from Paca on those axes would be a regression.** Where the two references disagree, we now
> know which to follow.

#### 9.5.1 The agent surface — where Paca is genuinely ahead of everything

**WS-27au — the agent-run transcript.** 🟢 AGENT-SAFE.
A run is not a log. Heterogeneous backend events (assistant message, tool call, observation,
error, rejection) fold into one assistant *turn* per burst, and within a turn group into a
collapsible **Reasoning** block, a collapsible **N tool calls** block, and the reply as prose.
The details that make it correct: a synthetic terminal `finish` call is unwrapped so its
message reads as the answer rather than an opaque card; a tool result arriving with no matching
open call (a history gap after resume) still renders as a standalone complete card instead of
vanishing; unknown event types fall through to plain text rather than disappearing; and a
streaming tool part is created once and **mutated in place**, so a diff captured early is not
cleared by a later update. This is the contract between an agent event bus (our AG-UI + Action
Broker) and any chat renderer, and nothing in Plane specifies it.
**REF:** [`apps/web/src/components/projects/agents/conversation-to-thread-messages.ts`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/projects/agents/conversation-to-thread-messages.ts)
· their test file is the better spec: [`apps/web/src/components/projects/agents/conversation-to-thread-messages.test.ts`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/projects/agents/conversation-to-thread-messages.test.ts)

**WS-27av — the inline tool-approval bar.** 🟢 AGENT-SAFE. **This is the Action Broker's
human-in-the-loop surface, specified.**
Allow/deny by default; a host-declared option list when present (allow-once / allow-always /
reject-once / reject-always) with **allow options ordered first and only the first styled
primary**; unknown custom kinds filtered out; and **a refusal path always preserved** — if the
declared list contains no reject option, a Deny button is synthesised. An option may demand a
second confirm step naming the **grants being conferred** as code chips. The card auto-expands
exactly once on `requires-action` and never re-opens after the user collapses it.
⚠️ **Read this together with the agent-layer finding that Paca's BACKEND has no approval
primitive at all** (`paca_pm_research` §10): the protocol here comes from `@assistant-ui/react`
(MIT), not from Paca. So the UI contract is adoptable and **the enforcement is ours to design**
— which is exactly the split D-PM-19 below records.
**REF:** [`apps/web/src/components/assistant-ui/tool-fallback.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/assistant-ui/tool-fallback.tsx) (approval bar, and the
`isError`-separate-from-`status` rule: a tool that *returned* an error reports `complete`, so
checking status alone renders a failure as a success)

**WS-27aw — the agent activity ledger.** 🟢 AGENT-SAFE, needs a migration (two partial indexes).
Per-agent, cross-entity: every task and doc the agent touched, typed, described in the **same
sentence vocabulary as the task timeline**, deep-linked, with the entity title shown greyed and
unlinked when the source was deleted — so the ledger survives its subjects. Filterable by
source type, date range and text; keyset-paginated.
**Not "here is a chat log" but "here is the ledger of changes this agent made to the
business."** Neither we nor Plane have it. For us it is *cheaper than for them*: they must
`UNION ALL` two activity tables, we have one `pm_activities` with the actor already recorded as
`agent:<name>` — the read model is a filtered query over a table that exists.
**REF:** [`services/api/internal/domain/agent/activity_feed.go`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/services/api/internal/domain/agent/activity_feed.go) · [`apps/web/src/components/projects/agents/agent-activity-tab.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/projects/agents/agent-activity-tab.tsx)

**WS-27ax — an agent assignee should LOOK like one.** 🟢 AGENT-SAFE. **Paca's own biggest
miss, and the design space it leaves open for us.**
Measured: `member_type === "agent"` is read in exactly two files in 61k lines, neither a card,
a row, nor the properties panel. There is no bot glyph, no agent chip, no "an agent is working
on this right now" state — the only visible trace is a timeline line. We already have the
vocabulary (D-PM-4: agents and people are one assignee list, `agent:<name>`) and `TaskCardShell`
already draws an avatar stack.
Done when a card shows *an agent owns this*, *it is mid-run*, *here is how to watch it*, and
*here is how to stop it*. The last two matter most: a running agent the user cannot see or stop
is the failure mode this ticket exists to prevent.
**REF:** no upstream — this is the gap, not the pattern. Nearest shape:
[`apps/web/src/components/projects/agents/conversation-view.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/projects/agents/conversation-view.tsx) (status badge, always-reachable Stop, ~30s heartbeat so the sandbox reaper does not kill a run the user is watching)

**WS-27ay — agent presets.** 🟢 AGENT-SAFE, trivial (data) + a slice (picker).
Creating an agent starts from role-shaped templates — Software Engineer, Code Reviewer, QA,
**Planner**, **Business Analyst**, Custom — each carrying provider, model and a system prompt
that names the *tools* it should use and the output convention it should follow. Every field
stays editable; the preset is a starting point, not a mould. For a Centers product this maps
onto department-shaped agents almost one-for-one, and it is the cheap answer to an empty Agent
Builder. ⚠️ Must live in our existing Agent Builder registry, never a second one.
**REF:** [`apps/web/src/lib/agent-api.ts`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/lib/agent-api.ts) (the preset table with full prompts)

#### 9.5.2 Tickets from the interaction surface

**WS-27az — per-activity Revert and View diff, from the timeline.** 🟢 AGENT-SAFE.
Hover a system entry → *View diff* (hunk-collapsed) and *Revert*, which reads the
`{field, old, new}` record, resolves names back to ids, and applies an ordinary update — so
**the revert is itself an auditable activity**. We adopted the schema from this reference and
never built the affordance; `pm_activities` already carries the change record.
⚠️ Two corrections to inherit deliberately rather than repeat: their `isRevertable` only checks
that *some* change has an `old` key, so a field it cannot restore still offers an enabled menu
item that silently does nothing — **ours computes revertability from the fields it can actually
restore**; and their custom-field diffs record no old/new at all, so ours must.
Disproportionately valuable in an agent product: undoing what an agent just did, one field at a
time, is a trust mechanism. **Plane has nothing comparable.**
**REF:** [`apps/web/src/components/projects/interactions/task-detail/activity-pane.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/projects/interactions/task-detail/activity-pane.tsx) (the field-by-field revert map, and the weak predicate to improve on)

**WS-27ba — the saved-view filter that does not rot.** 🟢 AGENT-SAFE. **Cheap now, expensive
later.**
Their filter config is a **recursive, dimension-agnostic selector** — `all` plus per-item
exceptions, nesting, named virtual groups, and per-custom-field range/contains blocks — not an
ID array. "Every status except Archived" keeps working when someone adds a status; an ID
snapshot silently starts hiding new work. With Center slices this matters more for us than for
them: a Center's default view is created once and lives for years.
⚠️ **Take the shape and resolve it SERVER-side.** Theirs is stored, handed back, and never read
by the server — all filtering arrives as client-built query parameters, so their saved filters
are advisory. Our `146_projects.sql` explicitly claims the opposite property; **we hold it and
they do not, so Paca is not prior art for the split** — only for the shape.
Composes with WS-27ap (the boolean filter tree from Plane): same JSONB config, one resolver.
**REF:** [`services/api/internal/domain/sprint/entity.go`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/services/api/internal/domain/sprint/entity.go) (the recursive `FilterConfig`)

**WS-27bb — per-column board pagination.** 🟢 AGENT-SAFE. The largest scale gap between their
board and ours: each column is its own paginated query with a **server-side total** in the
header (or a summed numeric field — story points — instead of a count), so "Load more" never
lies. The subtle part worth copying: when a realtime event forces a refetch, the column's
**expanded depth is remembered and re-requested**, so a column the user expanded to 200 does not
silently snap back to 20. Ours fetches one flat list.
**REF:** [`apps/web/src/components/projects/interactions/interaction-layout.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/projects/interactions/interaction-layout.tsx)

**WS-27bc — the long-list picker.** 🟢 AGENT-SAFE. **The behavioural half of WS-27ak(4), and it
can be written before D-PM-15 resolves** — Base UI has no Combobox, so this is a build whichever
substrate wins. Scroll-pagination at a 48px threshold; server-side search debounced at 300ms
with a **minimum query length of 2**, below which it falls back to the unfiltered first page
rather than firing a leading-wildcard scan with no index behind it. That minimum is a
performance fence, not a nicety.
**REF:** [`apps/web/src/lib/scroll-pagination.ts`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/lib/scroll-pagination.ts) · [`apps/web/src/components/projects/interactions/use-epic-search.ts`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/projects/interactions/use-epic-search.ts)

> ### 🔴 Audit outcome 2026-08-11 — **NO-GO. The blocker is documentation, so the doc fix IS the ticket.**
> Contract point 3 fails outright: **there is no "Done when".** Three constants are not
> acceptance. Everything below is verified against the code, not against the ticket.
>
> 🟢 **Amended 2026-08-11 — the one dispatchable slice below is now BUILT (§11.34), on branch
> `ws-27bc-paged-picker`, NOT merged and NOT deployed.** `app/projects/lib/pagedPicker.ts` +
> its test: the threshold predicate, page accumulation with dedupe by id, a terminal state
> derived from a short page, and a min-length gate that **imports** `MIN_QUERY` and `isCurrent`
> from `./search` rather than re-deriving either — held there by a source scan, not only by
> behaviour. 9/9 mutants red. Point 2's false justification is struck **in the module's own
> docstring**, so the correction travels with the code.
> **The ticket as a whole stays NO-GO, and nothing else below moved:** point 3 (the debounce
> number, and the seven ad-hoc copies with no shared helper) is untouched — a shared debounce is
> its own ticket and this slice deliberately minted neither an eighth copy nor a half seam;
> point 4 (two endpoints, incompatible contracts) is untouched and is why `isSearchable` reports
> the minimum and stops instead of choosing a fallback; point 6 (the surface half, behind
> WS-27ak) is untouched — there is no component, popover or listbox in this slice, and it is
> wired to no call site. What is left of WS-27bc after §11.34 is exactly the two decisions and
> the surface.
>
> **1. A third of it is already shipped — the confident third.** "Server-side search debounced
> with a minimum query length of 2" exists in `gateway/routes/projects/search.py:72`
> (`MIN_QUERY = 2`, enforced at `:228`, answering empty rather than 422) and
> `app/projects/lib/search.ts:27` (*"Mirrors the gateway's `MIN_QUERY`"*), consumed by
> `TriageRail.tsx:85`. `email/components/RecipientInput.tsx` is a **working hand-rolled
> long-list picker** with `role="combobox"/"listbox"/"option"` already wired. A fourth
> implementation is the CLAUDE.md §5 defect authored by the ticket meant to prevent it.
>
> 🔴 **2. Its central justification is FALSE and must be struck.** The ticket says the minimum
> stops *"a leading-wildcard scan with no index behind it"*. The query is
> `t.title ILIKE :term OR t.description ILIKE :term` with `term = '%…%'` — **`'%ab%'` is
> exactly as unindexable as `'%a%'`.** `pg_trgm` appears **zero** times in `infra/`. The
> minimum bounds the **result set**, not the scan, which is what the gateway's own docstring
> (`search.py:68-71`) actually says: *"returning half the workspace."* Replace the wording
> with the true claim.
>
> **3. The 300ms contradicts the tree.** `projects/lib/search.ts:30` is `DEBOUNCE_MS = 180`,
> `FilterBar.tsx:162` is an inline `300`, `RecipientInput` is `200` — and there is **no shared
> debounce helper**, just seven ad-hoc copies. Either reconcile to one number, or say
> explicitly that this ticket mints `src/lib/`'s debounce and retires all seven. Minting an
> eighth is the defect; minting a shared one and leaving seven is half a seam.
>
> 🔴 **4. A contradiction that blocks the build outright, and it is a decision not an edit.**
> The ticket wants scroll-pagination **and** server search **and** a fallback to "the
> unfiltered first page" in one surface. But `/projects/search` is **capped, not paged**, by a
> recorded decision in its own module docstring (*"Nobody pages through search results; they
> retype. A `LIMIT` with no `OFFSET` is the honest shape"*), and has **no unfiltered mode** —
> `q=""` returns `{"rows": []}`. `/projects/tasks` **is** paged but its `q` has no minimum and
> no ranking. Two endpoints, incompatible contracts, spec silent on which. An implementer must
> either straddle both and let the list visibly reorder at the 2-character boundary, or reopen
> `search.py`'s paging decision. **Neither is an agent's call.**
>
> **5. Only scroll-pagination is genuinely unbuilt.** Zero `IntersectionObserver`, `onScroll`,
> `scrollHeight`, `loadMore` or `hasMore` anywhere under `app/projects/`.
>
> **6. Re-sequenced: the surface half now depends on WS-27ak, it does not race it.**
> §9.7.2's *"runs in parallel and before D-PM-15 resolves"* was written while that decision was
> open. It is answered, and **its "Base UI has no Combobox" rider was itself false** (D-PM-15,
> corrected: `@base-ui/react@1.7.0` ships `combobox`). A picker owes a popover, `aria-expanded`
> /`aria-controls`/`aria-activedescendant`, listbox roles and focus return — all of which
> D-PM-15 condition 1 says arrive as a wrapper in `src/components/ui/`. Hand-rolling a popover
> shell here is the second-substrate failure condition 2 exists to prevent, arriving through a
> ticket instead of a vendored registry.
>
> ➡️ **The one slice dispatchable today — 🟢 BUILT 2026-08-11, §11.34** — is
> substrate-independent and has no component in it:
> a pure `app/projects/lib/pagedPicker.ts` + test — threshold predicate, page accumulation and
> dedupe, min-length gate — reusing `search.ts:80`'s existing `isCurrent()` stale-response
> guard rather than re-deriving it. ⚠️ And its real target is
> `RelationsBlock.tsx`'s `<Input placeholder="task id">`, which is the only true long-list
> surface in `/projects` — note `search.py`'s `exclude_relatives_of` was built for exactly
> that and has **zero client consumers** today.

**WS-27bd — the small rules, each removing a class of defect.** 🟢 AGENT-SAFE, all trivial.
(1) **Shortcuts release unclaimed keys** — `Mod+F` opens page search only where a page
registered one and otherwise falls through to the browser's find-in-page; the fence is a test
asserting `preventDefault` is not called when no handler is registered. (2) **Per-row pending
and per-row error** in lists, so three concurrent installs show three spinners and one failure
is attributed to one row — against the usual single `isPending` that disables everything.
(3) **Clipboard failure never claims success** — a denied or insecure-context write must not
flip the button to "Copied", because the user may need to select the text by hand.
(4) **Signature-keyed dismissal** for banners — dismiss *this* announcement, not the banner
forever. (5) **Context menu on cards and rows**, reading the same action registry the palette
already uses — one registry, two surfaces; measured, `/projects` has zero `onContextMenu`.
**REF:** [`apps/web/src/lib/shortcuts/provider.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/lib/shortcuts/provider.tsx) · [`apps/web/src/components/plugins/PluginMarketplacePanel.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/plugins/PluginMarketplacePanel.tsx) · [`apps/web/src/components/home/UpdateBanner.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/home/UpdateBanner.tsx) · [`apps/web/src/components/projects/interactions/task-context-menu.tsx`](https://github.com/paca-ai/paca/blob/09dab28e3caee9e43891697998dcfa7fcf76991c/apps/web/src/components/projects/interactions/task-context-menu.tsx)

> ### ⚠️ Audit outcome 2026-08-11 — **GO-NARROWED to (5) cards-only and (2)**
> Three of five items describe work that is already done or has no target in this tree.
>
> **Done when:** (5) `src/components/ContextMenu.tsx` exists **as a promotion of
> `app/tasks/components/ContextMenu.tsx`**, both `/tasks` and `/projects` consume that one
> implementation, /projects cards (`TaskBoard.tsx`, `MyWork.tsx`) open it, its items are
> derived from `app/projects/lib/commands.ts`, and a structural fence asserts **exactly one
> shared ContextMenu** with the pre-existing `email/components/EmailList.tsx` copy recorded as
> a **named exemption** — a fence that is silently red on arrival, or silently passes over a
> known second copy, is worse than none. (2) `RelationsBlock.tsx` keeps `pending: Set<id>` and
> `errors: Map<id,string>` in a pure reducer, so three concurrent operations show three
> spinners and one failure is attributed to one row.
>
> 🔴 **(5) is a PROMOTION, and the ticket's own wording is the trap.** "Context menu on cards
> and rows" reads like a build. A working generic one already exists at
> `app/tasks/components/ContextMenu.tsx` — `CtxItem[]` union, viewport flip, Escape, click-away
> — wired at **five** call sites (`tasks/components/TaskCard.tsx:216,298`, `InboxCard.tsx:173`,
> `calendar/TimeGrid.tsx:521`, `calendar/UnscheduledRail.tsx:136`), and a **second** lives at
> `email/components/EmailList.tsx:429,697`. A third would be a CLAUDE.md §5 defect authored by
> the ticket that was meant to prevent it. ✅ `TaskCardShell.tsx` already **accepts and wires**
> `onContextMenu` (lines 41/63/82) — /projects simply never passes it, so this is a
> pass-through. ⚠️ The registry is `app/projects/lib/commands.ts`, **not** `src/lib/commands.ts`
> — §11.25's shorthand misleads on the path.
> ⚠️ **Cards only this wave.** The row half waits for WS-27al, which is making table rows
> link-navigable in the same click path.
>
> **~~(1) shortcuts release unclaimed keys~~ — NO-GO.** Its own stated fence ("`preventDefault`
> is not called when no handler is registered") is a good test **of a registry that does not
> exist**. Keyboard handling lives in three unrelated places (`projects/page.tsx:1034`,
> `projects/lib/search.ts:118-140`, `projects/lib/commands.ts:388-396`); consolidating them
> mints a **third** keyboard seam, which is a seam decision, not a papercut. Separately,
> **nothing binds `Mod+F` anywhere** — zero matches — so "falls through to find-in-page" is
> already true by absence.
>
> **~~(3) clipboard failure never claims success~~ — ALREADY TRUE at all eight sites. Struck.**
> Six of eight `clipboard.writeText` calls have an explicit `catch` that deliberately does not
> flip "Copied", including /projects' own at `TaskPanel.tsx:493-506`, which carries a comment
> explaining why. The two `.then()` sites (`MessageActionBar.tsx:33`, `MarkdownMessage.tsx:132`)
> never run their success branch on rejection either — they leak an unhandled rejection, which
> is a different and smaller defect. Re-scoping this as "one clipboard helper, eight call
> sites" would be a legitimate ticket, but it is **a different one** and not this wave's.
>
> **~~(4) signature-keyed banner dismissal~~ — STRUCK, no target.** No dismissible banner with a
> persist-forever key exists anywhere. The nearest seam, `src/lib/dismissedTools.ts`, is
> already id-keyed — i.e. already signature-keyed in spirit.

**WS-27be — the task search index nobody can use, and the missing minimum.**
✅ **BUILT 2026-08-11 — as-built in §11.33.** Branch `ws-27be-trgm-search-index`, migration
`170_projects_search_trgm.sql`. NOT merged, NOT deployed. **Landed on the `pg_trgm` route**
(the owner's directive; the plans support it — `Filter:` became `Index Cond:`, 652.9 ms →
0.71 ms at 60k rows), plus **a third index the ticket did not ask for and the fix does not work
without**: `task_number` needed its own btree or the numeric OR arm keeps the whole disjunction
un-servable (§11.33). `idx_pm_tasks_fts` is **left in place** per R6, with its drop and the
trigger for it recorded in §11.33. `MIN_QUERY` moved to `filters.py` and `search.py` re-exports
it. **Two things are owed, not done:** the `idx_pm_tasks_fts` drop, and the product decision on
raising `MIN_QUERY` to 3 — a 2-character query is one character below what a trigram index can
serve and still costs 127 ms. *Original ticket, verbatim, below.*

🟢 AGENT-SAFE,
needs a migration. **Found 2026-08-11 while auditing WS-27bc; it is bigger than that ticket
and unrelated to its scope, so it is minted here rather than smuggled in.**

Two facts, both verified against the tree:
1. **`idx_pm_tasks_fts` has been dead weight since migration 146.** `146_projects.sql:248`
   creates `GIN (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,'')))`
   — a full-text index. The only query that searches those columns is
   `search.py`'s `t.title ILIKE '%…%' OR t.description ILIKE '%…%'`, and **`ILIKE` cannot use a
   `to_tsvector` GIN index.** So every `/projects/search` call is a sequential scan of
   `pm_tasks` plus two joins plus a full sort before `LIMIT 51`, while an index that looks like
   it covers the case sits unused. Nobody has noticed because the table is small — which is
   exactly the condition under which this stays invisible until it is expensive.
2. **The list endpoint has no minimum query length.** `filters.py:202` accepts any non-empty
   `q`, and `FilterBar.tsx:162` sends it after 300ms with no length check, so
   `GET /projects/tasks?q=a` fires today with no cap — the very query WS-27bc claims to be
   preventing, at the one endpoint where nothing prevents it.

Done when: the search path and the index agree — **either** `pg_trgm` + a `gin_trgm_ops` index
so `ILIKE` is servable (zero `pg_trgm` in `infra/` today, so this is a new extension and an
owner-visible choice), **or** the query moves to `to_tsvector`/`websearch_to_tsquery` and uses
the index that already exists, **or** `idx_pm_tasks_fts` is dropped as honest dead weight.
Whichever way it lands, the decision is recorded and the leftover is not left looking useful.
Plus: `filters.py:202` gets the same minimum `search.py` already enforces, or a written reason
why the list endpoint does not need one.
⚠️ **R8** — this is a SQL/plan question and hermetic fakes agree with whatever SQL they are
handed. It must be verified against a real Postgres, with `EXPLAIN` before and after; a green
unit test proves nothing here.
⚠️ **R6** — expand/contract. A new index is additive; dropping the old one is a later release.

#### 9.5.3 Decisions owed

**D-PM-19 (owed) — the agent autonomy gate.** **The single most important gap in either
reference.** Paca has **no approval or human-in-the-loop primitive anywhere in its agent
layer** — a grep of the whole territory returns prose only. An agent's autonomy is exactly its
project-role permission set, exercised unilaterally; the only human levers are pause and stop,
after the fact. Its "you MUST invoke a skill before acting" rule is a paragraph in a prompt with
nothing enforcing it. Plane has no agent surface at all.
So: **neither reference is prior art, and we design it.** The natural seam is a **per-tool gate
at the tool layer**, not a sentence in a system prompt — and the UI contract already exists as
a library (WS-27av). Shapes the Action Broker and agent dispatch together, which is why it is a
decision and not a ticket.

~~**D-PM-20 (owed)**~~ ✅ **D-PM-20 — ANSWERED 2026-08-13: an `updated_at` precondition on
PATCH, and NO version column.** Neither reference has any concurrency control: no version
column, no etag, read-modify-write throughout. **With agents writing concurrently with humans —
which is the entire point of our product — last-write-wins is a data-loss design**, and a
revert affordance (WS-27az) makes it worse by replaying stale values. The precondition is
nearly free *now* and breaks every client at once if added later.

`DECISION (2026-08-13, owner-selected as the next item; the shape below is the agent's, taken
after the audit that follows.)`

**The audit that made this decidable** (measured against `e294ce7`):

| Question | Answer |
|---|---|
| Any concurrency control today? | **None.** The eight 409s in the package are constraint conflicts (a status in use, a duplicate field key), not preconditions. |
| Does `updated_at` move on every write? | **Yes by default** — `core.update_row` appends `updated_at = now()` unless `touch=False`. |
| How many opt out? | **Exactly two**, both stamping `recurrence_spawned_at`. |
| Task write endpoints needing it | **8** across `tasks.py`, `bulk.py`, `relations.py`. |

🔴 **The "one semantic must cover both" worry resolves cleanly, and that is the finding.** The
two `touch=False` sites are correct for **both** consumers at once: a recurrence bookkeeping
stamp should not make a delta client re-pull, *and* it should not invalidate a human's pending
edit either. So one `updated_at` genuinely serves the feed's keyset cursor and the write
precondition — there is no conflict to arbitrate, which is what makes this buildable rather
than a redesign.

**The shape:**

* **`If-Match: <updated_at>`** on the task PATCH — the value the client last read, echoed back.
  A mismatch is **412 Precondition Failed** carrying the current row, so the client can show
  *"someone changed this while you were editing"* with the actual values rather than a bare
  refusal.
* **No new column.** `updated_at` already exists, already moves on every write that matters, and
  already serves the delta cursor. A `version INT` would be a second monotonic fact about one
  row — the CLAUDE.md §5 defect, and one that would then need its own migration and its own
  backfill.
* **Absent header = no precondition**, deliberately. Making it mandatory on day one breaks every
  existing caller at once, which is the exact failure this decision exists to avoid. It is
  advisory first and tightened in a later release (R6's discipline applied to an API rather than
  a schema).
* ⚠️ **Timestamp granularity is the trap to measure, not assume.** `updated_at` is
  `timestamptz`; two writes inside the same microsecond would be indistinguishable. R8: prove
  the round-trip preserves enough precision through asyncpg **and** through the JSON encoder
  before claiming the precondition is exact.

**Not in scope:** bulk edit's semantics (what a partial precondition failure means across
thirty tasks is its own question), and WS-27az's revert.

#### 9.5.4 Added to the refusal list, with reasons

**`is_public` as a boolean on the container.** One flag opens **26 anonymous read routes** —
including the member list, comments, and presigned attachment download URLs. A visibility axis
that is a column on the container has no granularity and no audit: you cannot make a board
public without making its files public. Confirms **D-PM-14**, and is the second sighting of the
same shape (Plane's anchor model was the first). · **`X-Agent-ID` as an identity over a shared
install-wide key whose fallback principal is SUPER_ADMIN** — violates **R11** three ways, and
its own narrowing step is skipped on any global-scope route. · **A migration runner with no
ledger that re-executes every file on every boot**, plus an in-place `DROP COLUMN` in the same
file that backfills — the direct negation of R1/R6 and of "verify delivery by evidence". ·
**Producer-minted primary keys inserted without `ON CONFLICT` off an at-least-once stream** —
one redelivery poisons the pending list permanently. · **Permission reads that ignore
soft-delete**: their human path omits the predicate their agent path includes, so a removed
member keeps every permission indefinitely. The fence for us is a test that revokes a grant and
asserts 403 on the next read. · **A second activity spine per entity type**, and **a second
project-role vocabulary** — both are our own CLAUDE.md §5 rule demonstrated in someone else's
tree. · **Client-supplied fractional positions with no check that the task belongs to the
view** — the third sighting of the join-table authorisation class we fixed in
`views.set_positions`; it is a category, not an incident. · **Per-project agent rows**: the same
assistant in ten projects is ten rows and ten secrets to rotate. · **`proOptions:
hideAttribution`** on React Flow — MIT library, but removing the attribution mark requires a
paid subscription; do not copy the flag. · **`class-variance-authority`**, again — it arrives
*with* vendored component registries, which is exactly how the second substrate walks in.

---

### 9.6 Upstream reference index — Plane

*Every link pinned to `31853ab2` per §9.0. All **128** references in the source report were
mechanically checked: the file exists at that path and the cited end line is within the file.
Two candidates failed that check and were corrected before inclusion — which is the argument
for `tests/unit/test_reference_links.py` rather than trust.*

**A REF is a reading list, not a port.** Plane is AGPL-3.0: read it, then write ours.

#### P-1 … P-31 (the original research queue)

| Item | Upstream reference | The part worth reading |
|---|---|---|
| P-1 intake/triage | [`issue.py#L92-L100`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/db/models/issue.py) | ⭐ the load-bearing trick: the **default manager excludes `state__group=triage`**, which is the whole "capture does not pollute a board" mechanism |
| P-2 watchers + mention diffing | [`notification_task.py#L53-L111`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/bgtasks/notification_task.py) | set-difference of old vs new mentions; the fan-out excludes new mentions *and* the actor |
| P-3 archive guard | [`archive.py#L255-L278`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/app/views/issue/archive.py) | one `state.group not in (completed, cancelled)` check; its bulk sibling is the counterexample (aborts mid-loop) |
| P-4 lifecycle sweeper | [`issue_automation_task.py#L28-L149`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/bgtasks/issue_automation_task.py) | exempts issues in an unfinished cycle; stamps `automation: True` into the activity |
| P-5 activity id+label | [`issue.py#L414-L437`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/db/models/issue.py) | `old_value`/`new_value` beside `old_identifier`/`new_identifier` |
| P-6 category-ranked sort | [`order_queryset.py#L145-L193`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/utils/order_queryset.py) | ⚠️ their tiebreaker is only `-created_at`; **ours is `(created_at, id)` — we are a step ahead of the source we cited** |
| P-7 picker exclusions | [`search/issue.py#L37-L83`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/app/views/search/issue.py) | four separate methods, not one param — ours is a consolidation, not a port |
| P-8 child distribution | [`sub_issue.py#L165-L201`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/app/views/issue/sub_issue.py) | one `defaultdict(list)` keyed by state group, no denormalisation |
| P-9 import provenance | [`api/views/issue.py#L616-L646`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/api/views/issue.py) | idempotent PUT-upsert keyed on the pair. ⚠️ uniqueness is enforced **at the query, not by a constraint** — we should add the constraint they lack |
| P-10 spreadsheet | [`use-table-keyboard-navigation.tsx#L7-L62`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/hooks/use-table-keyboard-navigation.tsx) | the arrow-cursor logic; row nesting caps at depth 3 |
| P-11 swimlanes | [`swimlanes.tsx#L50-L59`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/issue-layouts/kanban/swimlanes.tsx) | the empty-lane visibility predicate |
| P-12 shown-fields contract | [`with-display-properties-HOC.tsx#L18-L35`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/issue-layouts/properties/with-display-properties-HOC.tsx) | ⭐ **35 lines is the entire contract** |
| P-13 group-context quick-add | [`quick-add/root.tsx#L100-L136`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/issue-layouts/quick-add/root.tsx) | `reset()` fires *before* the request, so typing continues immediately |
| P-14 peek escalation | [`peek-overview/view.tsx#L102-L113`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/peek-overview/view.tsx) | focus return gated on "no modal / not in an input / no editor bar open" |
| P-15 dirty-view affordances | [`rich-filters/filter.ts#L294-L312`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/shared-state/src/store/rich-filters/filter.ts) | the store half is the instructive one |
| P-16 palette registry | [`shortcut-handler.ts#L30-L140`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/power-k/core/shortcut-handler.ts) | **library: `cmdk` (MIT)** for the list; this file is the registry + key-sequence machine |
| P-17 keyboard cursor | [`use-multiple-select.ts#L290-L360`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/hooks/use-multiple-select.ts) | Shift+Arrow extends from the cursor; both share one neighbour helper |
| P-18 drop refusal + flash | [`group-drag-overlay.tsx#L27-L86`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/issue-layouts/group-drag-overlay.tsx) | **library: `pragmatic-drag-and-drop` (Apache-2.0)** owns the drag; only the reason overlay is theirs |
| P-19 calendar | [`calendar/issue-blocks.tsx#L60-L113`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/issue-layouts/calendar/issue-blocks.tsx) | ⚠️ **correction: their per-day overflow is a paginated "Load more", not "+N more"** — WS-27ac shipped the better one |
| P-20 notifications inbox | [`workspace-notifications/root.tsx#L85-L118`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/workspace-notifications/root.tsx) | ⚠️ their split count is a `sender__icontains="mentioned"` **string match** — use a real column |
| P-21 human task IDs | [`work-item/base.ts#L315-L340`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/utils/src/work-item/base.ts) | `/browse/KEY-42/` is a real resolvable route, not a display string |
| P-22 timeline polish | [`gantt-chart/add-block.tsx#L26-L80`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/gantt-chart/helpers/add-block.tsx) | ⭐ the direct answer to our `TimelineView`'s "there is nothing to place" comment |
| P-23 sprints | [`cycle_transfer_issues.py#L400-L470`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/utils/cycle_transfer_issues.py) | carry-forward as an explicit logged transfer |
| P-24 webhooks | [`url_security.py#L160-L230`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/utils/url_security.py) | ⭐ resolve→validate→pin, closing the DNS-rebinding TOCTOU; its docstring explains why `requests` alone is unsafe |
| P-25 email digest | [`email_notification_task.py#L46-L84`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/bgtasks/email_notification_task.py) | group per receiver → entity → actor, then one bulk `processed_at` |
| P-26 export job | [`exporter_expired_task.py#L22-L53`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/bgtasks/exporter_expired_task.py) | the 8-day sweep deletes the object and nulls the URL, keeping the history row |
| P-27 delta feed | [`issue_activities_task.py#L1526-L1538`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/bgtasks/issue_activities_task.py) | the unconditional satellite bump is the prerequisite. ⚠️ **their `cursor` is offset in costume — do not copy as keyset** (ours is a real keyset) |
| P-28 small columns | [`session.py#L44-L56`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/db/models/session.py) | the `create_model_instance` override that denormalises the user id *is* the feature |
| P-29 public boards | [`space/views/project.py#L54-L63`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/space/views/project.py) | ⚠️ the `AllowAny` **anchor-recovery** endpoint that defeats unpublish-as-rotation. `is_disabled`: **NO UPSTREAM — read by zero views and zero components** |
| P-30 pages/wiki | [`page.py#L23-L175`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/db/models/page.py) | refused as PM; useful as the KB's eventual checklist (hierarchy, versions, backlinks) |
| P-31 refusals | [`db/mixins.py#L48-L84`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/db/mixins.py) | the soft-delete tax every query then re-asserts |

#### The active queue (§9.3 / §9.4)

| Ticket | Upstream reference | Note |
|---|---|---|
| WS-27ah(1) segments | [`linear-progress-indicator.tsx#L21-L55`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/ui/src/progress/linear-progress-indicator.tsx) | the renderer that consumes P-8's datum |
| WS-27ah(2) click-to-filter | [`active-cycle/progress.tsx#L60-L90`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/cycles/active-cycle/progress.tsx) | the click emits a `state_group in [...]` filter — exactly the behaviour |
| WS-27ah(4a) draft restore | [`quick-actions.tsx#L40-L67`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/workspace/sidebar/quick-actions.tsx) | one slot **per workspace**, cleared on submit |
| WS-27ah(4b) pins | [`favorite.py#L14-L50`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/db/models/favorite.py) | ⚠️ theirs has **folders** via a `parent` self-FK; our ticket says flat — take the generic table + `sequence`, leave `parent` |
| WS-27ah(4c) recently-viewed | [`recent_visited_task.py#L17-L52`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/bgtasks/recent_visited_task.py) | ⚠️ **correction to §9.4**: a cap *does* exist, but it is written `if count == 20` — an equality, so once the count ever exceeds 20 it never trims again. Copy the intent, not the comparison |
| WS-27ai inbox | [`notification-card/item.tsx#L46-L67`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/workspace-notifications/sidebar/notification-card/item.tsx) | mark-read-on-open; the right pane embeds peek with `embedIssue` (no portal) — that flag is the two-pane trick |
| WS-27ak(1) Modal | [`propel/dialog/root.tsx#L1-L141`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/propel/src/dialog/root.tsx) | **library: Base UI (MIT)** owns focus trap/inert/scroll-lock. Plane adds only sizing — go to the library |
| WS-27ak(3) Toast | [`propel/toast/toast.tsx#L290-L324`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/propel/src/toast/toast.tsx) | the promise-bound `loading → success \| error` one-toast mutation |
| WS-27al(1) ControlLink | [`control-link.tsx#L19-L58`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/ui/src/control-link/control-link.tsx) | ⚠️ theirs exempts meta/ctrl only — **middle-click is not exempted**; ours must be |
| WS-27al(2) prevent-outside-click | [`use-peek-overview-outside-click.tsx#L16-L45`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/hooks/use-peek-overview-outside-click.tsx) | `closest("[data-prevent-outside-click]")` plus a containment check |
| WS-27al(3) lazy tooltip | [`ui/tooltip.tsx#L56-L82`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/ui/src/tooltip/tooltip.tsx) | ⚠️ read the in-file FIXME: `renderByDefault` **defaults true**, so the optimisation is opt-in and mostly unused. Invert the default |
| WS-27al(5) overdue predicate | [`work-item/base.ts#L171-L187`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/utils/src/work-item/base.ts) | false for completed/cancelled; `<= 0` days makes **today count as due** |
| WS-27al(6) selection self-heal | [`use-multiple-select.ts#L372-L384`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/hooks/use-multiple-select.ts) | ten lines |
| WS-27am(1) empty triad | [`empty-states/project-issues.tsx#L30-L71`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/issue-layouts/empty-states/project-issues.tsx) | the no-permission branch renders the CTA **disabled, not hidden** |
| WS-27am(2) layout HOC | [`issue-layout-HOC.tsx#L45-L63`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/issue-layouts/issue-layout-HOC.tsx) | 19 lines, including the `layout !== CALENDAR` judgement call |
| WS-27am(3) error boundary | [`layout-error-boundary.tsx#L36-L59`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/common/layout-error-boundary.tsx) | Retry bumps a key so children genuinely remount |
| WS-27an autosave | [`title-input.tsx#L46-L140`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/web/core/components/issues/title-input.tsx) | ⭐ **best single-file reference in the queue** — all six behaviours, including save-on-unmount |
| WS-27ao editor | [`editor/extensions.ts#L100-L120`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/editor/src/core/extensions/extensions.ts) | **libraries: TipTap v3 + ProseMirror + tiptap-markdown (MIT)** — we already ship TipTap. Asset-GC sweeper: [`file_asset_task.py#L20-L26`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/bgtasks/file_asset_task.py) |
| WS-27ap filter tree | [`filter_backend.py#L164-L217`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/utils/filters/filter_backend.py) | the `and`/`or`/`not` walker + allowlist + depth cap. ⚠️ the **lossy converter to avoid** is [`filter_migrations.py#L47-L57`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/utils/filters/filter_migrations.py) — per-record `except: log; continue` silently drops a view's filters |
| WS-27aq notif prefs | [`notification.py#L81-L110`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/db/models/notification.py) | one row, nullable workspace **and** project. ⚠️ their flags only ever set `send_email` — **the in-app bell is unmutable**, verified |
| WS-27as join-table audit | [`cycle/issue.py#L238-L262`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/apps/api/plane/app/views/cycle/issue.py) | ⭐ the **post-fix** code with GHSA-4w5x-wc9w-f47x named in-file — the pattern our remaining audit checks for |
| WS-27at gallery | [`design-system-philosophy.stories.tsx#L70-L230`](https://github.com/makeplane/plane/blob/31853ab2b8b7810c59dc30d22e52c8f4b5a71a47/packages/propel/src/design-system/design-system-philosophy.stories.tsx) | ✅/❌ nesting pairs rendered live — read for *content*, not tooling |

#### Where there is nothing to link — and that is the answer

`is_disabled` per-board kill switch · `collaboration-cursor` (a string in a type union, no
implementation) · **RTL** (zero occurrences repo-wide, so D-PM-18 gains nothing from Plane) ·
**timeline dependency arrows** (not rendered in their OSS core, so **D-PM-12 stands
unchallenged**) · **page-batched list badges** (their implementation is the anti-pattern; the
rule is ours and their file is only the evidence for it) · **per-tenant OAuth callback routing**
(instance-global upstream — the hard part is unsolved there).

---

### 9.7 The usability sequencing (owner directive 2026-08-11)

§9.3–§9.5 minted 21 tickets ranked by *finding*, not by *impact*. The owner's instruction
on 2026-08-11 re-ranks them by one question:

> *"the features … which will make the most impact in the UI/UX and feature set to ensure
> that the UI/UX is as clean, neat, and organized as possible and can bring us to a state
> of usability as soon as possible."*

This section records the resulting order. It changes **no ticket's scope** — every
done-when above stands unedited. It owns sequencing only, and where it disagrees with a
ticket's own ranking prose, `work_plan.md` §2's board row wins over both.

#### 9.7.1 The measurement that set the order

Re-measured against the tree at `ebf68f4e`, 2026-08-11 — not quoted from §9.4, which was
written a day earlier against a description of the tree rather than a count of it.

| Anchor | Count | Why it ranks where it does |
|---|---|---|
| Focus traps, whole tree | **0** | All 7 grep hits for `focus-trap\|FocusTrap\|inert` are the *word* "inert" in prose comments. Not one real trap, not one `inert` attribute. |
| Files with a hand-rolled `fixed inset-0` overlay | **69** | The population the missing trap applies to. |
| Toast system (`useToast` / `<Toaster>` / `ToastProvider`) | **0** | ⚠️ **Worse than §9.4 stated.** There is no confirmation channel at all — a mutation either reports inline or reports nothing. This moved Toast up WS-27ak's order. |
| Files using the native `title=` tooltip | **157** | ⚠️ **Corrected 2026-08-11.** This row first read "**125**, §9.4 said ~157; the real figure is 125" — that was my error, not §9.4's. `title="` matches 125 files, `title={` matches 108, and `title=` matches **157**. §9.4's figure was right; my narrower grep was not, and a count quoted without its method is not a measurement. Unstyled, ~500ms delayed, invisible on touch. |
| Files improvising `animate-pulse` | **26** | §9.4 said ~20. |
| Headless-primitive library in `package.json` | **none** | No Radix, no Base UI, no Headless UI, no cmdk, no sonner, no floating-ui. Confirms §9.4's framing: WS-27ab's palette is hand-rolled too. |
| `<Link>` / `<a href>` anywhere under `src/app/projects` | **0** | ⚠️ **Corrected 2026-08-11.** First written as "124 `onClick`, 0 `role=\"button\"`"; both halves were wrong. The real count is **122** `onClick` lines across 23 files, and `role="button"` is **not** absent — `TaskCardShell.tsx:74` emits it for every /projects card. **The gap is the missing `<a href>`, not the missing button role**: cmd/ctrl/shift/middle-click cannot open a task in a new tab anywhere in `/projects`. ⚠️ The list rows are `<tr>` (`TaskList.tsx:318`, `TableView.tsx:533`), so a link lands in the **title cell** — an `<a>` cannot wrap a `<tr>`. |
| `onContextMenu` in `/projects` | **0** | ✅ **Cheaper than §9.5 implied**: `src/components/TaskCardShell.tsx` already *accepts* the prop and `/tasks` already ships `app/tasks/components/ContextMenu.tsx`. WS-27bd(5) is wiring, not building. |
| `EmptyState` implementations | **2** | `src/components/EmptyState.tsx` + a local one at `app/tasks/components/ItemList.tsx:490`. |

#### 9.7.2 The four waves

**Wave 1 — the papercut wave. DISPATCHED 2026-08-11.** 🟢 all AGENT-SAFE, no migration, no
library, no owner decision, nothing blocking. **WS-27al** (logic-only wins) · **WS-27am**
(three-state list surface) · **WS-27bd** (the small rules). Highest usability-per-hour in
the queue: it is the wave that makes the app stop feeling improvised.

**Wave 2 — the primitive wave.** 🟡 **D-PM-15 is the only gate in the entire sequence.**
**WS-27ak** in debt order — **Modal → Tooltip → Toast → Skeleton** (Toast promoted above
Skeleton by the measurement above; §9.4.2's original order had it third by count, and count
was the wrong axis). ~~**WS-27bc** (long-list picker) runs *in parallel and before the decision resolves*, because
Base UI has no Combobox and it is a build whichever substrate wins.~~
🔴 **Struck 2026-08-11 — both halves of that rationale were wrong.** D-PM-15 is answered, so
there is no race to run ahead of; and **`@base-ui/react@1.7.0` does ship `combobox`** (the
"no Combobox" claim was read off a renamed, deprecated package inside the pinned clones).
WS-27bc is separately **NO-GO** on its own contract (§9.5.2) and its surface half is
re-sequenced **behind** WS-27ak's wrapper layer. Its one dispatchable piece today is a pure
`pagedPicker.ts` with no component in it.

**Wave 3 — the "does it lose my work" wave.** 🟢 no decision needed. **WS-27an** (inline
autosave — save-on-unmount-if-dirty is the behaviour that currently loses an edit when the
panel closes mid-keystroke) · **WS-27at** (the living gallery, which is what stops waves 1–2
drifting apart and converts the manual theme-switch sweep into one page).

**Wave 4 — the modern-feel wave.** **WS-27ao** (rich comment editor, Lite — extensions on
the TipTap v3 we already ship, not an engine) · **WS-27ah** (segmented clickable progress,
timeline zoom/edge-drag, pins, recently-viewed, draft restore).

#### 9.7.3 Deliberately not on this path

Recorded so they do not read as forgotten: **WS-27ap** (boolean filter tree) ·
**WS-27aq** (notification preferences) · **WS-27ai** (notifications inbox) · **WS-27az**
(revert from timeline) · **WS-27ba** (non-rotting saved view) · **WS-27bb** (per-column
board pagination) · the whole agent queue **WS-27au–ay**. All real; none on the shortest
line to a clean, usable Projects app.

⚠️ **The one tension worth stating rather than burying.** Four deferred items are *cheap
now and expensive later*, and deferring them is a real cost, not a free one:
**D-PM-20**'s `updated_at` precondition on PATCH (adding it later breaks every client at
once, and migration 168's `updated_at` already serves the delta feed, so one semantic must
cover both) · **WS-27ar**'s single generic `(user, entity_type, entity_id)` pins table
(otherwise four `is_pinned` columns across four tables and a unifying migration) ·
**WS-27ap**/**WS-27ba**'s stored filter grammar (the saved-view corpus only grows). The
recommendation carried to the owner was to take D-PM-20 and WS-27ar's table shape alongside
Wave 1; ap/ba wait until after Wave 3.

---

### 9.8 WS-27bg — project run state, the indicator, and archive (minted 2026-08-13)

**WS-27bg — the project lifecycle axis made real.** 🟢 AGENT-SAFE. Owner-directed
2026-08-13: *"each project should show one colored indicator in front of it — green for
ongoing, red for stopped, orange for paused"*, plus **queued**, plus *"the ability to archive
an entire project and its tasks."* Decisions **D-PM-25** (two axes), **D-PM-26** (derive, never
cascade) and **D-PM-27** (the hue map) were taken before minting and are not re-opened here.

#### 9.8.1 The measurement — this feature is half-built and entirely invisible

Measured against `efd843a`, 2026-08-13:

| Anchor | Finding |
|---|---|
| `pm_projects.status` | Shipped **since migration 146** — `CHECK (status IN ('active','on_hold','done','archived'))`, `DEFAULT 'active'`, indexed (`146_projects.sql:60-61`). |
| API write path | **Already validated** on create (`tree.py:246`) and PATCH (`tree.py:304`) against `core.py:68`'s `PROJECT_STATUSES`. |
| API read path | **Already returned** — `status` is in `tree.py:64`'s column list. |
| Frontend type | **Already declared** — `app/projects/lib/tree.ts:15`, `status?: string \| null`. |
| `ProjectTree.tsx` | Contains the string `status` **zero times**. Nothing renders it, nothing filters on it, no control writes it. |
| `pm_projects.archived_at` | Exists since 146. **Never written by anything.** |
| Project archive endpoint | **Does not exist.** The only removal path is `DELETE /nodes/{project_id}` (`tree.py:389`) — an unrecoverable cascade over subtree + tasks + grants. |
| Project-editing UI | **Does not exist.** `patchProject` has one call site (`LifecyclePolicy.tsx:47`), `createProject` one (`page.tsx:1052`). A project cannot be renamed in this app. **✅ CLOSED 2026-08-25** — the measurement stands as taken against `efd843a`; slice 2 added the menu and the slice-2 remainder (`ws-27bg-project-rename`, PR #47) added rename. |

⚠️ **This is the second instance of a failure this repo has already recorded once.**
`src/lib/statusAccent.ts`'s own header documents `pm_task_statuses.color` as *"stored since
migration 146, exposed on the API as `StatusRow.color`, and rendered nowhere"* — so every
Projects board column drew the same grey for months. Same table, sibling column, same shape.
The lesson worth carrying: **a validated column with no consumer is not "partly done", it is a
claim the API makes and the product does not honour.**

#### 9.8.2 🔴 Three automation paths corrupt data the day `on_hold` starts meaning something

None of these is visible from a UI mock. Each writes real rows.

1. **The lifecycle sweeper closes a paused project's backlog.** `run_lifecycle_sweep`
   (`automation.py:298`) walks every root project with a policy enabled and moves open tasks
   untouched beyond `close_after_months` into the closing lane — **with no project-status
   predicate anywhere in the query.** Pause a project for a quarter with a three-month policy
   and the sweeper cancels its backlog for the crime of being paused, writing as
   `system:workflow:<id>` through `apply_status_transition`, so `completed_at`, the
   `status_change` activity and recurrence all behave *exactly as if a person had done it*.
2. **Recurrence keeps spawning.** A series advances when a task closes (`recurrence.py`, §11.13);
   a paused project that closes anything keeps minting new work into itself.
3. **Agent dispatch keeps dispatching.** `pm.task.assigned` → `agent_dispatch` (WS-27aa) puts an
   agent to work inside a project nobody is working.

**All three take ONE predicate, defined once and consulted by each — not three guards.** A
second copy of "is this project runnable" is the CLAUDE.md §5 defect that this ticket would
otherwise author three times over.

#### 9.8.3 Slice 1 — the axis, the endpoints, the automation guard 🟢

> **Done when:**
> * A migration widens `pm_projects.status`'s CHECK to `('active','on_hold','done','archived','queued','stopped')`
>   — the **union**, per R6 expand-then-contract — and `PROJECT_STATUSES` (`core.py:68`) carries
>   the same six. The contraction that drops `'archived'` is a **later** release and is named in
>   the migration's own header, not left implicit.
> * A backfill stamps `archived_at = now()` on every surviving `status='archived'` row and moves
>   it to a run state. ⚠️ **It must be correct for ANY population, because the population cannot
>   be measured from here** — reading the live database is owner-gated reach (§6), so an agent
>   that "confirms it is empty" is reporting a guess. The backfill is therefore set-based and
>   idempotent, correct at zero rows and at ten thousand, and the **owner is asked for the count
>   at review** so the deploy is verified by evidence rather than by a green job.
> * `POST /projects/nodes/{id}/archive` and `.../unarchive` stamp and clear
>   `pm_projects.archived_at`, emit `pm.project.archived` / `pm.project.unarchived`, and return
>   the same honest `cascaded` counts `delete_node` returns — **read before the write**, for the
>   reason `tree.py:396` already gives. Archiving warns on open work; it never blocks.
> * **No `pm_tasks` row is written by any of it** (D-PM-26). A test asserts `pm_tasks.updated_at`
>   is byte-identical across a pause, a stop, an archive and an unarchive of a project holding
>   tasks — this is the fence that makes the decision real rather than documented.
> * One predicate — `is_runnable(project)` — defined once and consulted by the lifecycle sweep,
>   the recurrence spawn and the agent dispatch. Each of the three has a test that goes **red**
>   when the guard is removed, i.e. mutation-measured, not merely present.
> * Default task reads exclude tasks whose project is archived, honouring the existing
>   `include_archived` flag rather than minting a second one.
> * **R8:** the derived read path is `EXPLAIN`ed against a real Postgres at realistic row counts,
>   and the plan is recorded in the as-built. A hermetic fake agrees with whatever SQL it is
>   handed; five live bugs have shipped green that way.

#### 9.8.4 Slice 2 — the indicator and the control surface 🟢

> **Done when:**
> * `PROJECT_STATE_HUES` lands in `src/lib/statusAccent.ts` per **D-PM-27**, as a closed lookup
>   with a test proving it is **unreachable from `resolveHue`** (the `keywordHue` trap: `active`
>   → blue, `done` → green, i.e. the opposite of the ruling on two of five states).
> * Every project row in `ProjectTree.tsx` draws its state as **hue + glyph** — `<Icon name>`,
>   never colour alone — with the effective (inherited) state drawn recessive against a node's
>   own state. Status is writable at **any** depth; the effective state is the most restrictive
>   ancestor. ⚠️ This deliberately differs from the lifecycle policy, which is root-only and
>   422s on a child (migration 166's header): a policy is *configuration*, a run state is a fact
>   about a unit of work, and a subproject is a unit of work.
> * A project control surface exists at all — the status picker and Archive live on the promoted
>   `src/components/ContextMenu.tsx` (WS-27bd) and `src/components/ui/Modal.tsx` (WS-27ak); no
>   new dialog is hand-rolled and no `fixed inset-0` is added.
> * **Archive is the default affordance and Delete is deliberately harder to reach.** Today
>   `DELETE` is the only path and it is unrecoverable; shipping archive without re-ranking them
>   leaves the destructive action as the obvious one.
> * Stopping a project offers the bulk close (D-PM-26) and takes "leave them as-is" for an
>   answer.
> * **D-PM-21:** the theme sweep is a Playwright case, not a promise — the indicator renders
>   under Fluent, Material and Graphite, in both modes.

#### 9.8.5 Slice 3 — the honest surfaces 🟢

> **Done when:**
> * **Nothing in a queued, paused or stopped project is ever overdue.** ⚠️ There are **four**
>   predicates and they were unified only one wave ago — `src/lib/taskCard.ts:190`,
>   `app/projects/lib/mywork.ts:64`, the deliberately-different `app/tasks/lib/waiting.ts:68`
>   under its own contract, and the SQL at `filters.py:182`. Landing this in three of four
>   re-forks the vocabulary WS-27al spent a slice merging. **D-PM-22 is untouched**: this changes
>   *which tasks are eligible to be overdue*, never `<` versus `<=`.
> * Tasks in a non-running project leave **My Work** and the assignee counts. My Work answers
>   "what should I do now"; for a paused project the answer is nothing.
> * **Calendar and timeline de-emphasise; they do not silently drop.** The day cell collapses
>   non-running tasks out of the visible stack and carries an expandable `+3 paused` — the
>   **honest-overflow** pattern WS-27ac already shipped (§11.24), not a new one. A timeline bar
>   keeps its position, renders muted, and never draws an overdue edge.
> * Due-date notifications are suppressed for non-running projects.

#### 9.8.6 Deliberately out of scope, recorded so it does not read as forgotten

Per-project state **history** (who paused it, when, why) — `pm_activities` is project-less today
(§3.8 is a task timeline), so a project-level audit trail is its own ticket, not a side effect of
this one. Also out: any change to `DELETE /nodes/{id}`'s semantics beyond its ranking in the UI.

---

### 9.10 WS-27bi — the PATCH precondition (minted 2026-08-13, D-PM-20)

**WS-27bi — `If-Match` on the task PATCH.** 🟢 AGENT-SAFE. **No migration, no new column.**
D-PM-20 is answered; the audit behind it is in that decision and is not repeated here.

> **Done when:**
> * `PATCH /projects/tasks/{id}` honours **`If-Match: <updated_at>`** — matching proceeds,
>   mismatching answers **412** with the current row in the body so a client can show what
>   changed rather than only that something did.
> * **An absent header still succeeds.** Advisory first; mandatory is a later release. A
>   precondition made compulsory on day one breaks every existing caller at once, which is the
>   failure this whole decision exists to prevent.
> * **No `version` column is added.** `updated_at` already moves on every write that matters
>   (`core.update_row`, `touch=True` by default) and already serves migration 168's keyset
>   cursor. A second monotonic fact about one row is the CLAUDE.md §5 defect.
> * The two `touch=False` sites stay untouched and a test says why: a `recurrence_spawned_at`
>   stamp must neither wake a delta client nor invalidate a human's pending edit.
> * ⚠️ **R8 — measure the granularity, do not assume it.** `updated_at` is `timestamptz`; prove
>   a round-trip through asyncpg **and** the JSON encoder preserves enough precision that two
>   writes cannot collide into one token. A precondition that silently compares truncated values
>   is worse than none, because it reports safety it does not have.
> * The delta feed's cursor behaviour is unchanged, asserted rather than assumed.
>
> **Not in scope:** bulk edit (what a partial precondition failure means across thirty tasks is
> its own question) and WS-27az's revert.

#### 9.10.1 The R8 measurement — done 2026-08-14, and it moved two fences

Measured on a real Postgres 16 through asyncpg and `fastapi.encoders.jsonable_encoder`, not
reasoned about. **Verdict: the precondition is implementable** — parse the token in Python, bind
it as a `datetime`, let Postgres compare it as `timestamptz`. That round trip is exact for all
three token shapes:

| Case | Encoder token | Compares equal in pg |
|---|---|---|
| ordinary microseconds | `2026-08-14T09:21:49.448124+00:00` | ✅ |
| trailing-zero microseconds | `2026-03-04T05:06:07.100000+00:00` | ✅ |
| `microsecond == 0` | `2026-01-01T00:00:00+00:00` | ✅ |

Three things the measurement found that reading the code would not have:

🔴 **A naive (offset-stripped) token silently compares `True`.** asyncpg reinterprets a
tz-less `datetime` in the session zone, which on a UTC box happens to match. A client that
drops the offset therefore gets a precondition that *appears* to work and would start
mis-comparing under any session-TZ change. This is precisely the "reports safety it does not
have" failure the decision was written against. **Fence: reject a naive token with 400 — never
accept it.** Test: a token with the offset stripped must not pass.

🔴 **`::text` and the JSON encoder disagree.** Postgres renders the trailing-zero case as
`2026-03-04 05:06:07.1+00` (5 fractional digits, zeros trimmed); the encoder renders
`.100000`. As strings they differ; as `timestamptz` they are equal. **Fence: the token is
never produced by `::text` and never string-compared** — compare in the database, or between
parsed `datetime`s. A string comparison passes every test written against ordinary
microseconds and fails ~1 row in 10 on a trailing zero.

⚠️ **asyncpg refuses a `str` bound where a `timestamptz` is inferred** (`invalid input for
query argument`). This is a helpful failure, not an obstacle: it makes the parse step
mandatory rather than optional, so the string-comparison mistake above is hard to make by
accident on this stack.

#### 9.10.2 A finding for the board — `now()` can move backwards (NOT this ticket)

While measuring, an overlapping-transaction case reproduced on the real database:

```
T1 txn now() = 09:22:18.560649      (starts first)
T2 txn now() = 09:22:18.762134      (starts 201ms later, writes, commits FIRST)
after T2 commit : 09:22:18.762134
after T1 commit : 09:22:18.560649   <-- WENT BACKWARDS by 201ms
```

`now()` is the **transaction-start** timestamp, so a transaction that opens early and commits
late stamps `updated_at` with a time earlier than a row already written by a newer,
faster-committing transaction.

**This does not harm the precondition** — and that is the reason it does not expand this
ticket's scope. `If-Match` compares one exact value; a backwards stamp still differs from the
client's token, so the client still gets its 412 and refetches. The precondition never reports
"unchanged" when the row changed.

**It is a real gap in migration 168's keyset cursor**, which is already-merged code: a delta
client whose cursor has advanced past `.762` will never be handed the row later stamped
`.560`, and that change is silently dropped from the stream. Reachable only when two write
transactions to the same row overlap and the older commits last — short request transactions
make it rare, not impossible.

Per CLAUDE.md §5 this is recorded as a **finding for the board, not a refactor**: it predates
this work, and fixing it (`clock_timestamp()`, or a sequence) is a change to the delta feed's
contract that deserves its own row and its own decision.

---

### 9.11 WS-27bj — org-wide vocabularies (minted 2026-08-14, D-PM-16)

**WS-27bj — nullable project scope on the three vocabularies.** 🟡 **Migration + read-path
change.** D-PM-16 is ruled; its audit is recorded there and is not repeated here. Take the
migration number at build time and re-check it at merge (**R1** — three collisions in two weeks).

> **Done when:**
> * `project_id` is **nullable** on `pm_task_types`, `pm_custom_fields` and `pm_tags`, with the
>   paired partial-unique indexes from D-PM-16 replacing each table's current whole-table
>   `UNIQUE`. `organization_id` stays `NOT NULL` on all three — **an org-wide row is org-wide
>   within one tenant, never global**, and a test says so by trying to read another tenant's
>   org-wide row and getting nothing.
> * A project's effective vocabulary is **org-wide ∪ root-local**, and **root-local shadows
>   org-wide on the same identity** (D-PM-16). ⚠️ For tags this is a correctness rule, not a
>   preference: `pm_tasks.tags` stores display text, so both rows describe the *same* tag on
>   every task and the union must yield exactly one colour. A test asserts the shadowed row is
>   the one that renders.
> * Identity matches each table's existing rule — `lower(name)` for tags (already
>   case-insensitive), `name` for task types, `field_key` for custom fields. **Not** a new
>   normalisation invented here.
> * ⚠️ **R8 — verify the partial uniques against a real database.** Two partial indexes that
>   between them permit a duplicate is exactly the bug a hermetic fake cannot see, because a
>   fake agrees with whatever SQL it is handed. Prove all four cases: two org-wide duplicates
>   rejected, two root-local duplicates rejected, org-wide + root-local of the same name
>   **accepted** (that is the shadowing case, not a violation), and the same name under two
>   different tenants accepted.
> * **R6 — expand only.** Old writers always send a `project_id` and keep working; old readers
>   filtering `project_id = :x` do not see org-wide rows. No contraction in this release, and
>   the header names the later one if any.
> * **Ship dark**: the flag gates the affordance that *creates* an org-wide row, not the read
>   union. Creating one is the half that is hard to walk back.

> **Not in scope:** migrating any existing per-project row up to org-wide (that is the
> judgement call D-PM-16 exists to avoid ever needing, and it is the owner's, not an agent's),
> and the admin UI for managing org-wide vocabularies — the seam lands first.

#### 9.11.1 As built — slice 2, the read path (2026-08-14)

Slice 1 was migration `175_projects_org_vocabularies.sql`. Slice 2 is the Python half: one
seam in `core.py` (`vocabulary_scope`, `shadowed`, `org_wide_exists`, `refuse_org_wide_write`,
`org_vocabularies_enabled`) and three readers that use it — `admin.list_types`,
`custom_fields.load_definitions`, `tags.load_registry_rows`/`list_tags`.

**The union lands on the SEAM, not on the list endpoint.** `load_definitions` has four callers
— the list, the create's duplicate check, the export's column set, and `apply_values`'
validation of what a task may store. An org-wide field that listed but whose values were then
refused as an unknown key would be the worst of both, so the union sits where all four meet.

**Three decisions inside the slice, each with its reason:**

* **The org arm's tenant is read from `:root`'s own project row**, not passed as a second
  parameter. That makes `vocabulary_scope()` a pure function of `:root`, so no caller can
  compose it correctly-but-without the tenant — and the anchor is then a row the caller has
  already been through `load_visible_project` for, which is a stronger fact than an org id
  passed alongside.
* **Only a SAME-SCOPE clash is refused on create.** A project registering its own "bug" while
  the organization also has one is the shadowing D-PM-16 permits (172's R8 case 3), not a
  duplicate. ⚠️ The org-wide arm cannot ask the effective list at all — `shadowed` hides a
  shadowed org-wide row by design — so it asks the table through `org_wide_exists`.
* **An org-wide row is created deliberately and edited nowhere.** `refuse_org_wide_write` is a
  guard rather than a policy: every rename/merge/delete path hands `str(row.project_id)` to a
  uuid cast, and for an org-wide row that is the literal string `"None"` → an unhandled cast
  error, i.e. a 500 where a refusal belongs. The admin surface stays out of scope.

**Gates on the create:** the flag (`PROJECTS_ORG_VOCABULARIES`, default OFF, read at call
time) **and** `admin:settings:manage`. The permission is not decoration — an org-wide row lands
in every project in the organization, including ones the writer cannot see, which crosses the
visibility boundary the rest of the package respects. A manager holds `data:org:read` and does
not get this.

##### R8 — verified against a real Postgres, from the live clause builders

The probe was **generated by calling `vocabulary_scope()` and `VOCABULARY_IDENTITY`**, never by
retyping the SQL: a verification that retypes the statement proves the retyping. Migration 172
was applied to the throwaway cluster from its own file, then:

| Case | Result |
|---|---|
| the union, from A's root | 4 rows — `bug` (local) **and** `Bug` (org) both present; B's `theirs` absent |
| the tenant fence, from B's root | only `theirs` — A's org-wide rows invisible |
| `list_tags` in full (aliased scope + the usage count) | `org-only` = **2**, A's tasks only, not 3 and not 0 |
| `org_wide_exists` cross-tenant | A `t`, B `f`, and case-insensitive (`'BUG'` finds `'Bug'`) |
| an unknown `:root` | 0 rows — fails **closed** |

⚠️ Case 1 returning **both** spellings is correct and worth stating: the union is SQL, the
shadowing is Python. Pushing the tie-break into a `DISTINCT ON` would work and was rejected so
the rule can be asserted directly rather than inferred from an `ORDER BY` somebody later tidies.

##### Mutation — fourteen mutants, and the two that got through first

Every fence was proved by breaking it. **Two survived the first pass**, and both were the
hermetic-fake failure R8 exists for:

1. **The usage-count correlation.** Reverting `t.root_project_id = CAST(:root AS uuid)` to
   `= g.project_id` — the spelling that reports every org-wide tag as used by **0 tasks** —
   left all 57 tests green, because the mirror computed the count itself and agreed with either
   statement. Fixed by having the mirror read the correlation *off the statement*.
2. **The explicit tenant on an org-wide INSERT.** Dropping it left the tests green because the
   fake fell back to its own organization; the real 161 trigger "does NOT invent a tenant" —
   with a NULL parent it returns `NEW` unchanged and `NOT NULL` refuses. Fixed by narrowing the
   fake's fallback to the two rows that genuinely must tenant themselves (a root project, and
   an org-wide vocabulary row).

The live probe had already proved the correct behaviour in both cases. What was missing was a
test that would go **red** if someone undid it — which is the difference between verified and
fenced, and is why the mutation pass is not optional here.

##### Two findings for the board, neither fixed in this slice

* 🔴 **`TagRow` is declared TWICE on the frontend** — `lib/tags.ts` and `lib/api.ts` — and
  `page.tsx` passes rows between them. The two are assignable only while they agree, which is
  how widening one surfaced the other. Both were widened; collapsing two public wire types is
  its own change (CLAUDE.md §5).
* The `MAX_TAGS_PER_PROJECT` / `MAX_FIELDS` caps now count the **effective** set. A picker
  showing 500 tags is unusable whichever scope contributed them, and counting only the local
  half would let the real number reach 1000. Stated because it is a behaviour change for a
  tenant that adopts many org-wide entries.

> **Still owed on WS-27bj:** the admin surface for managing org-wide vocabularies (explicitly
> out of scope above), which is also what would let an org-wide row be edited or retired.

---

### 9.9 WS-27bh — the four facts the card already knows and never says (minted 2026-08-13)

**WS-27bh — draw what is already stored.** 🟢 AGENT-SAFE. **No migration, no new column, no
API shape change.** Owner-selected 2026-08-13 from the card audit, all four items.

#### 9.9.1 The audit that minted it

Every `pm_tasks` fact, against where it actually renders. Measured at `5fb38a5`:

| Fact | Table | Card chip | Panel | |
|---|---|---|---|---|
| status · title · due_at · tags · assignees · importance · estimate · subtasks · blocked · custom fields | ✓ | ✓ | ✓ | fine |
| **`type_id`** → `pm_task_types` (name · **icon** · **colour** · `is_epic`) | ✗ | ✗ | ✗ | **never drawn** |
| **`recurrence_id`** | ✗ | ✗ | editor only | **never drawn** |
| **`source`** (`manual`/`import`/`email`/`agent`/`automation`) | ✗ | ✗ | ✗ | **never drawn** |
| **urgency** (derivable from `due_at`) | ✗ | ✗ | ✗ | **no such concept** |
| `start_date` | ✓ | ✗ | ✓ | table only |
| watchers | ✗ | ✗ | ✓ | panel only |
| `completed_at` · `updated_at` | ✗ | ✗ | — | no column |

🔴 **`pm_task_types` is the fourth instance of one failure.** It is seeded per root project with
a name, an **icon** and a **colour** (`Task`/circle-check, `Bug`/bug, `Epic`/layers), it gained
`is_epic` at WS-27ae — and `type_id` reaches the frontend only as a **board grouping key**
(`lib/board.ts:236`). **A bug and a feature render identically on every surface in the app.**
The three prior instances: `pm_task_statuses.color` (stored since 146, drawn nowhere, every lane
grey), `estimate_mins` (dropped by `taskFacts` behind a comment claiming the column did not
exist), `pm_projects.status` (validated on two endpoints, drawn zero times — WS-27bg). ⚠️ The
pattern is worth naming because it is not carelessness: each was *stored correctly and reachable
by the API*, so every test passed and the only symptom was a UI that looked plainer than the
data underneath it.

#### 9.9.2 Done when

> * **Task type** draws on the card and the list, using the registry's own `icon` and `color`
>   through `statusAccent` — **not** a fifth palette (rule 4). An Epic is distinguishable from a
>   Task at a glance. `type` joins `FIELD_KEYS`/`FIELD_LABELS` and `CHIP_FIELD`, and the
>   gateway's `SHOWN_FIELDS` gains it in the same change — `shownFields.ts`'s own header warns
>   that a key added on one side only is *"a preference the server silently strips on the next
>   save."*
> * **Urgency** is derived from `due_at` — never stored — with a step between silent and
>   overdue, so a task due in three hours stops rendering identically to one due in three
>   months. ⚠️ **`importance = 3` is relabelled "Urgent" → "Critical" in the same slice**
>   (D-PM-28): shipping derived urgency while a hand-set pill still says "Urgent" puts two
>   disagreeing urgencies on one card. The window is a shared constant in this slice; its
>   org-level setting is D-PM-28's follow-up, not this ticket.
> * **A recurring task is visible as one** on the card. It matters more since WS-27bg: recurrence
>   now stops in a paused project, and a series that has silently stopped is exactly the thing a
>   card should be able to say.
> * **Source** draws for the origins that are not `manual`. `/tasks` already ships `SourceBadge`
>   in six components — ⚠️ **read it before building, and promote rather than author a second
>   one** if it fits; this is the WS-27bd(5) ContextMenu situation, and the audit that minted
>   this ticket exists to stop the app growing a fourth of something.
> * Every new chip obeys the existing `shown_fields` gate — a fact a view has hidden earns no
>   chip, per `card.visibleChips`.
> * **D-PM-21:** a Playwright case drives the new chips under Fluent → Material → Graphite.

#### 9.9.3 Not in this ticket

`start_date` as a chip, a watcher count, and `completed_at`/`updated_at` columns are real gaps
from the same audit and were **not** selected by the owner. Recorded here so the next reader
knows they were seen and skipped, not missed.

---

## 10. Verification

⚠️ Never `uv run pytest tests/unit/` bare — whole-directory collection hangs on the
Windows box against the live DB. **Name the files.**

```bash
uv run pytest tests/unit/test_projects_routes.py tests/unit/test_projects_grants.py \
              tests/unit/test_projects_migration.py \
              tests/unit/test_projects_import_mapping.py \
              tests/unit/test_projects_personal.py \
              tests/unit/test_gtd_retirement_plan.py \
              tests/unit/test_org_access_control.py tests/unit/test_org_access_enforcement.py
cd workbench/control_plane && npx tsc --noEmit && npm test
```

*(Corrected 2026-08-10, found by S5 while running this block: it named
`test_projects_sync.py` and `test_projects_personal_mirror.py`, **neither of
which exists** — the second is `test_projects_personal.py`, and the first never
landed under that name. A verification command that names a missing file makes
pytest exit non-zero before running anything, so anyone who pasted this block
saw a red run that had nothing to do with their change — and anyone who "fixed"
it by deleting the offending path silently dropped real coverage.)*

House style applies: hermetic route tests (fake session, monkeypatch the DB seam on the
SUT submodule), the migration asserted idempotent **statically** over its text, cascade
claims checked against `tests/unit/_schema_cascade.py`'s derived FK graph (the N8 lesson:
never report a destroyed row as kept), and mutants for each new guard measured red and
reverted byte-identical.

---

## 11. ClickUp parity — the measured gap, and how it gets closed

> **Added 2026-08-06** against the owner's standing requirement: *"I want to be able to do
> everything that I was doing from ClickUp and more."* WS-27g retires ClickUp, and it cannot
> honestly be called until this list is short. **Measured against the built tree** (twelve
> `pm_*` tables, 34 endpoints) rather than recalled — every "have" below is a table or a
> route that exists today.

### 11.1 What is already there

Hierarchy (departments → projects → subprojects → tasks → subtasks, two self-FKs) ·
statuses-as-data with a semantic category · task types · assignees in one vocabulary for
people **and** agents · comments and a single activity timeline **with field-level revert** ·
`blocks | relates_to | duplicates` links · per-view fractional ordering · board and list
surfaces · the personal lens · grant scoping with Center projections · the ClickUp importer ·
a `pm_task` automation node and assignment→agent dispatch.

Several of those ClickUp does **not** have — revert, agents as assignees, per-Center
projections of one board. That is the "and more" half, and it is already true.

### 11.2 What is missing, in the order it hurts

Ordered by *what stops somebody using this instead of ClickUp on a Monday*, not by how
interesting it is to build.

| # | Gap | Why it blocks | Ticket |
|---|---|---|---|
| 1 | ~~**Attachments**~~ | — | **WS-27i ✅ BUILT 2026-08-06** |
| 2 | ~~**Notifications + @mentions**~~ | — | **WS-27j ✅ BUILT 2026-08-07** |
| 3 | ~~**Filters, grouping and saved views**~~ | — | **WS-27k ✅ BUILT 2026-08-07** |
| 4 | ~~**Custom fields**~~ | — | **WS-27l ✅ BUILT 2026-08-07** |
| 5 | ~~**Tags**~~ | — | **WS-27m ✅ BUILT 2026-08-07** |
| 6 | ~~**Bulk edit / multi-select**~~ | — | **WS-27n ✅ BUILT 2026-08-07 · unblocks g** |
| 7 | ~~**Recurring tasks**~~ | — | **WS-27o ✅ BUILT 2026-08-07** |
| 8 | ~~**Dependency and subtask UI**~~ | — | **WS-27p ✅ BUILT 2026-08-07** |
| 9a | ~~**Calendar view**~~ | — | **WS-27q ✅ BUILT 2026-08-08** |
| 9b | **Timeline view** (Gantt bars on a date axis) | The calendar answers *what is due when*; it cannot answer *what runs alongside what*, which is the question a multi-month project asks | **WS-27t** |
| 10 | ~~**Global task search**~~ | — | **WS-27r ✅ BUILT 2026-08-08** |
| 11 | ~~**The card looks nothing like /tasks'**~~ | — | **WS-27s ✅ BUILT 2026-08-07** |
| 12 | **Dependencies cannot be drawn** | `blocks` exists and is cycle-guarded, but wiring one means a dropdown and a task number in a panel — Jira and ClickUp make it a drag between two bars | **WS-27t** |

**Deliberately NOT on this list:** sprints (a stated non-goal, §1), and time tracking and
checklists (Paca moved both out of core into plugins — the growth path is subtraction). If
any is wanted, it is a decision to record, not an omission to fix.

~~and Gantt~~ — **REVERSED 2026-08-08, owner-asked.** Kept struck rather than deleted
because the reversal is the interesting part: the original note treated Gantt as decoration,
which is true of the *chart* and false of the thing the owner actually asked for — a surface
where a dependency is DRAWN rather than typed. `pm_task_links` has been cycle-guarded since
WS-27p and reachable only through a dropdown and a task number; the chart is the gesture's
excuse to exist. Recorded as it should have been: a decision, in **D-PM-11** and
**D-PM-12**, with a ticket (**WS-27t**) that does not start until D-PM-12 is answered.

### 11.3 Sequencing, and the one dependency that matters

**WS-27n (bulk edit) gates WS-27g — and as of 2026-08-07 it is built (§11.11), so this
dependency is satisfied.** The cutover imports a real workspace, and an import that cannot be
re-triaged in bulk is an import somebody abandons halfway — leaving two live systems, which is
the exact state the retirement exists to end. It was built before the cutover, as required.
WS-27g itself remains 🔴 OWNER-GATE for its own reasons (§6), which this does not change.

**1 → 2 → 3 are the daily-use tier** and should go first as a block: a member who can
attach a file, hear about an assignment, and filter their board can run a day here. 4-5
(custom fields, tags) are the *modelling* tier — they change what a task can say. 6-10 are
reach.

Every one is 🟢 **AGENT-SAFE** to build. The gates stay where they already are: running the
importer against production, confirming a Space→Center mapping, and the WS-27g cutover are
owner acts (`work_plan.md` §6), and nothing in §11 changes that.

### 11.4 WS-27i — attachments (built 2026-08-06)

Migration `150_projects_attachments.sql`, `routes/projects/attachments.py`, the Files
section of the task panel. 25 hermetic cases, 10 mutants red.

**One file store, not two.** `gtd_attachments` already IS Paca's "central files registry"
(research §2.7) — owner, name, mime, size, path — so the bytes and the upload rules are
**imported** from the capture flow rather than copied. A second table with a second storage
directory would have meant two places to back up, two size limits to keep in step and two
answers to "is this extension allowed".

**What differs is who may READ, and that is the entire reason for the join.**
`gtd_attachments` is owner-scoped end to end; `pm_task_attachments` makes a file readable by
anyone who can see a task it hangs off. Two consequences, both security properties rather
than conveniences:

- **There is no attach-by-id endpoint.** Upload and attach are one call. A caller who could
  name an arbitrary `attachment_id` could attach somebody else's private capture to a task
  they own and read it back — privilege escalation dressed as a feature.
- **A personal capture stays unreachable here**, because it has no join row.

Detaching **keeps the bytes**: the same file may hang off another task, and deleting the row
from under it would turn one person's tidy-up into somebody else's broken link. Detaching
something already gone is a no-op, not a 404 (Paca's lenient-removes lesson, research §6).

**A bug caught before it shipped:** the projects BFF proxy re-serialised every POST as JSON.
A multipart upload would have failed `req.json()`, fallen into the `catch(() => ({}))`, and
reached the gateway **with no file at all — while still answering 201**. The proxy now passes
a non-JSON body through byte-for-byte. `workflows_app.md` §3.3b documents the identical trap
for HMAC-signed webhook bodies; this was that trap on the upload path.

**A test that was asserting the wrong thing:** the first traversal test checked the file's
path on disk, which is safe *by construction* (`<uuid><suffix>` — the supplied name never
reaches it), so a mutant removing `_safe_name` entirely survived. What that function actually
protects is the **stored name**, which is echoed into the descriptor, rendered in the UI, and
handed to `FileResponse(filename=…)` — i.e. into a `Content-Disposition` header, where
separators, quotes and newlines matter. Both properties are now asserted separately.

### 11.5 WS-27j — notifications and @mentions (built 2026-08-07)

Migration `152_projects_notifications.sql`, `routes/projects/notifications.py`, the bell in
the Projects header, the mention picker in the comment box. 39 hermetic + 27 vitest cases,
10 mutants red.

**Three rules decide who hears, and each one is the whole reason for a rule.**

1. **Never the actor.** A bell that pings you about your own click is a bell people mute,
   and a muted bell notifies nobody about anything.
2. **Never an agent.** Agents are handed work by the WS-27f dispatch sink, which starts a
   run. A row addressed to `agent:<name>` would sit unread forever and inflate a badge
   nobody could clear. Enforced in Python **and** by a CHECK, because a row that reached the
   table another way would still be wrong.
3. **Never somebody who cannot open it.** This is the security property. A notification
   carries the task's title, so delivering one outside the project's grant closure leaks
   that title and lands the recipient on a 404. The comment still posts; the response names
   who was skipped, and the UI says so — silently dropping a mention would leave the author
   believing a colleague was pulled in.

**Rule 3 needed new machinery, and the machinery is the point.** `resolve_visibility` reads
a `UserContext`; the recipient of a mention has no request in flight. `resolve_visibility_for`
answers for a third party by reading the same tables `/auth/me` reads and handing them to the
**real** `build_access`, so a wildcard grant (`*`, `data:*`) and an allow/deny override
resolve identically on both paths. Re-deriving that precedence in SQL is how two answers to
"may they see this" start disagreeing.

**Written inside the transaction, not emitted on the bus.** `core.emit` swallows failures by
construction so a broken workflow can never fail a task edit — right for agent dispatch,
where a missed run is recoverable, and wrong here. An assignment that committed while its
notification did not is exactly the silent assignment this ticket closes.

**A mention is an ADDRESS, not a name.** Migration 148 dropped `UNIQUE(name)` on the argument
that two real people share one, so `@Priya` has no answer and guessing would ping the wrong
person about work that is not theirs. The picker inserts `@priya@fracktal.in` so nobody types
it; the browser's pattern is deliberately the same one the gateway parses, because a composer
that highlighted names the server ignores would promise notifications nobody receives.

**Audience is derived, not subscribed.** A comment reaches the task's assignees and its
author. A `pm_task_watchers` table is the fuller answer and is not this ticket — and this is
the set one would be seeded from, so nothing here has to be undone when it arrives.

**Two bugs found on the way in, both shipping at the time:**

- **Every project-task file upload was answering 422.** `core.ACTIVITY_TYPES` mirrors
  `pm_activities`'s CHECK by hand, and `record_activity` refuses an unknown type *before* the
  insert. Migration 150 added `attachment` to the database; the tuple was never updated. All
  25 attachment tests passed throughout, because they monkeypatch `record_activity` — the
  seam under test was mocked out. Fixed, with two tests that read the migrations rather than
  restating them: one asserting set equality with the CHECK, one grepping every
  `activity_type=` call site in the package.
- **`/projects?task=<id>` did nothing.** The People Center's "Open work" list has linked
  there since WS-28b and landed on an unchanged board, because the page never read the
  parameter. The bell needed the same entry point, so it is now wired.

### 11.6 WS-27b's missing UI — the import was unreachable (built 2026-08-07)

`routes/projects/import_clickup.py` shipped with WS-27b and **no way to call it
from the product**. The empty state read *"No projects yet. Create one, or
import a ClickUp workspace"* — naming an action that had no control anywhere in
the app. So a new install stayed empty, and the fastest route to real data was
a curl command.

`components/ImportClickUp.tsx` + `lib/importPlan.ts` close that. 18 vitest
cases, 3 mutants red.

**Three steps, and only the last one writes.** Preview (`/import/clickup/plan`)
reads the live tenant and lists every Space with its folders, lists, tasks and
people, plus the proposed Center and the evidence for it. Dry run
(`/import/clickup {dry_run:true}`) exercises the whole path — including the
Space→Folder→List flattening — and reports what it *would* create. Import is
the same call with the flag off, on a button that says "writes".

**The mapping is still the owner's act (D-PM-10), and the UI is built so it
stays one.** The suggestion is pre-filled and shown beside its confidence *in
words* — "a guess — check it" rather than `0.45`, because a bare number invites
acceptance without looking. A **confirmed** mapping always beats a fresh
suggestion, so re-running never silently re-maps a Space somebody already ruled
on; that is the mutant most worth having red.

**Unmapped Spaces are a notice, not a blocker**, matching the importer's own
behaviour: they import in full and stay reachable, and refusing them would make
the mapping a precondition of seeing the data you need to decide the mapping.

**`already_present` is reported, never hidden.** The upsert is idempotent and
re-running is the normal case; "0 created" with no mention of the 400 rows it
matched reads as a failure of the import rather than a success of the last one.

⚠️ The owner gate is unchanged and is now exactly one click: **building** this
was agent-safe, **pressing Import** is the owner's act, and no agent has run
either endpoint against production.

### 11.7 The Tasks-app mirror path (built 2026-08-07)

Owner-directed: *"just show up all the data that is there in the Tasks app
inside the Projects app"* — one department now, real departments later.

`POST /projects/import/from-tasks` + `routes/projects/import_tasks.py`. 43
hermetic cases, 7 mutants red.

**Why a second importer rather than a flag on the first.** `import_clickup.py`
talks to the live tenant: it needs a working token, spends LLM budget proposing
a Center per Space, and asks the owner to confirm a mapping *before* anything is
written. That is the right shape for the migration and the wrong shape for "show
me my work today" — being made to decide who may see what before you have seen
the data is backwards. This one reads `gtd_projects` and `gtd_items`, the
ClickUp mirror the Tasks app already holds, so there is no API call, no token,
no rate limit and no model spend, and it works when the connector is stale.

**One department, and the real ClickUp shape beneath it.** Everything lands
under a single root the caller names; below that, **Space → Folder → List** are
rebuilt as projects, each carrying its own `clickup_id` and `clickup_kind` —
the same flattening `import_clickup` performs, so both paths produce one shape.
Promoting a Space node to a root is how the department split happens later: one
`/move`, not a re-import.

⚠️ **The placement comes from `task_accounts.schema_cache->'hierarchy'`, not
from `gtd_projects.space_id`.** Migration 60 defines that column as LOCAL-only
and it is *always NULL* on the synced rows this importer reads.

**The root IS org-granted, and that is narrower than it sounds.** §11.6's
importer deliberately does not org-grant unmapped Spaces, because bulk-granting
a whole tenant is a large implicit decision. Here the caller named one
department and asked for their work inside it — the same act as
`tree.create_node`, which org-grants for exactly the reason a solo org must not
be locked out of the thing it just made.

**Four properties the tests pin, each a way this could quietly do harm:**

1. **Nothing outside `pm_*` is written.** The Tasks app's rows are the mirror
   and must survive untouched, or an import would damage the personal task
   manager it read from.
2. **Only `source <> 'LOCAL'` rows are read.** A personal capture is the
   member's own; publishing it to a shared board is a disclosure nobody asked
   for.
3. **The provider's own status names are kept**, mapped to our categories.
   Renaming somebody's "Backlog" to "To do" makes the board stop matching the
   tool it came from on day one.
4. **A task whose list did not come across is counted, not dropped.** "412
   imported" while 30 were silently skipped is a number that gets trusted and
   should not be.

**Three defects a real Postgres found that the hermetic suite could not.**
Run against a scratch database with the full migration set and a seeded mirror:

1. **`gtd_projects.space_id` is LOCAL-only.** The first version read it for the
   Space, so every import would have recorded `null` — a promise in the
   docstring, the commit message and the PR body that was never true.
2. **`pm_projects` has no `clickup_snapshot` column** (only `pm_tasks` does).
   The first real click on "Bring it all in" would have answered 500. A fake DB
   accepts any column; Postgres does not. This shipped in #393 and was fixed
   before anybody pressed the button.
3. **The preview under-counted.** It reported 4 projects where the run then
   created 7, because Space and Folder nodes were only tallied on the write
   path — a number somebody would have read out loud during a demo.

Verified end to end afterwards: the tree comes out
`Fracktal Works / Engineering [space] / Hardware [folder] / Enclosure [list]`,
statuses keep their ClickUp names, assignee emails lowercase, a LOCAL capture
("Buy milk") stays out, `gtd_*` is untouched, and a re-run reports
`created: 0, already_present: 4`.

**A gap mutation testing found, worth recording.** Deleting `dry_run` from the
write guards left every test green: on a *first* dry run `_root_department`
returns `None`, so `root_id is None` blocks the write and the `dry_run` check
beside it never has to do anything. On a **second** dry run the department
already exists and its id comes back regardless — and the projects resolve too —
leaving `dry_run` as the only thing between a preview and a write. That is the
realistic case (preview → import → preview again), and it now has its own test.

### 11.8 WS-27k — filters, grouping and saved views (built 2026-08-07)

*"My open bugs in Ops, grouped by assignee"* is the sentence §11.2 used to name the gap. It
is now typeable: `routes/projects/filters.py`, `lib/grouping.ts`, `components/FilterBar.tsx`,
with the board and the list both drawing whatever `groupTasks` returned. 34 hermetic + 24
vitest cases, 13 mutants red, and 23 checks run against a real Postgres.

**One filter builder, shared by the endpoint and by saved views.** A saved view is nothing
but a stored set of these filters. Two implementations would drift, and a *saved* view that
shows a different set of tasks than the same filters typed by hand is the one thing it may
not do — so `build_task_filters` is a pure function that both paths call, and the test that
says so compares the two outputs directly.

**Every filter is a WHERE clause.** Pagination happens in SQL. A filter applied in Python
after `LIMIT` returns short pages, and *"page 2 is empty but there are 40 more"* is the kind
of bug people work around for months instead of reporting.

**`overdue` means past due AND still open.** A finished task with a past due date is not
overdue, it is done. Colouring it red forever is how a board teaches people to ignore red.

**An unknown status category is a 422, not an empty board.** A client filtering on
`in-progress` (hyphen) would otherwise see nothing and conclude the project is empty. The
error names the five real categories, which is the whole difference between a typo that takes
five seconds and one that takes an afternoon.

**An unknown config *key* is dropped; an unknown *value* falls back.** Those are different
failures. A view is a saved preference written by an older client, so refusing to open one
because it carries a key this version has never heard of would turn every deploy into a
migration of everybody's saved views. A bad `group_by`, on the other hand, still has to
render something, and `status` is the board's own axis rather than a guess.

**A task with two assignees appears in BOTH columns.** It is both people's work; picking one
arbitrarily hides it from the other. The consequence is that group sizes sum to more than the
task count, which is why the header counts tasks.

**Empty status lanes are kept; every other grouping drops empties.** A board missing its "In
progress" column reads as *"this project has no in-progress state"*, not *"nothing is in
progress"*. There is no equivalent meaning to an "assignees with nothing assigned" column.

**Dragging is offered only when the columns are statuses.** A drop writes the field the
columns represent, and status is the one that is a plain `PATCH status_id` — assignees are a
separate PUT, priority is an integer, and moving a task between projects crosses a grant
boundary. A card that can be dragged into a column which cannot accept it, and snaps back, is
worse than a column that is honestly static.

**`toConfig` is deliberately not `toQuery`.** A query string carries only text, so `toQuery`
writes `"true"`; a config is JSON and keeps a boolean a boolean. `fromConfig` refuses a string
where a toggle belongs — a hand-edited `"false"` must not read as on — so a view built from
query shape would come back with every toggle silently cleared. That round trip is a test.

**The project's order-bearing board is withheld from the chips.** `tree.py` seeds two views
per project, and the `board` one owns every `pm_view_task_positions` row. Offering its ✕
would offer to delete every hand-arranged position on the project. Saved views sit at
position 300, above the seeded pair, so `orderBearingView` — one function, used by both the
drag handler and the delete guard — keeps answering the seeded board.

**A fifth live bug, found the same way as the previous four.** `due_before` was
`t.due_at < CAST(:due_before AS timestamptz)` with the string straight off the query string.
asyncpg infers the parameter's type *from that cast* and then refuses to encode a `str`, so
the query never reached the database and **`?due_before=…` answered 500** — while the
hermetic fake, which agrees with whatever SQL it is handed, stayed green. `parse_when` now
parses on this side and binds a real `datetime`, an unparseable value is a 422 that says what
was expected, and a naive value is read as UTC rather than inheriting the connection's
TimeZone. Two tests: one on the bound value's *type*, one refusing any `CAST(:param AS
timestamp…)` anywhere in the builder, so the next `after=` filter written the obvious way
fails in CI instead of in production.

### 11.9 WS-27l — custom fields (built 2026-08-07)

ClickUp's signature feature, and the fourth row of the parity backlog. Migration
`155_projects_custom_fields.sql`, `routes/projects/custom_fields.py`, `lib/customFields.ts`,
a field block in the task panel and a manager dialog behind **Fields** in the header. 47
hermetic + 36 vitest cases, 23 mutants red, 35 checks against a real Postgres.

The shape is the one §5's non-goals recorded as the additive path: **definitions in a table,
values denormalised onto the task as JSONB keyed by `field_key`.**

**Why not a row per (task, field).** That is the textbook EAV answer and it costs a join per
field on every board paint — five custom fields across two hundred imported tasks is a
thousand rows to gather and re-pivot, per render. The JSONB column arrives with the task for
free, which is what makes the denormalisation worth its cost.

**What the denormalisation costs, stated rather than discovered.** A value is not
referentially tied to its definition, so the *database* cannot stop a key no definition owns
from being written. That guarantee moves into Python, and it is why the validation is the
feature rather than a formality:

* **An unknown key is a 422**, not a silent drop. A typo that no-ops looks exactly like a
  save, and the sender finds out weeks later.
* **A patch MERGES.** A client that knows about three of five fields must not wipe the other
  two by sending what it knows — and an older client, or an automation written before a field
  existed, is precisely that client.
* **An explicit `null` CLEARS the key**, removing it rather than storing a null. It is the
  only way to express "unset this", and a stored null would make "never filled in" and
  "deliberately emptied" the same value in every filter downstream.
* **`true` is not the number 1.** `isinstance(True, int)` is True in Python, so a number
  branch reached before the boolean one accepts both, in both directions. The coercers are
  one-per-type in a dispatch dict specifically so that ordering cannot be undone by accident.

**The deliberate departure from Paca: deleting a definition strips its values.** Paca's
research notes record "deleting a definition does not clean task data" as an accepted cost
(§2.3). It is not accepted here. A key left behind in the JSONB is invisible — no definition
means no column, no form row, no filter — right up until somebody creates a field with the
same name, and then every old value resurfaces carrying the new meaning. The count of tasks
cleared is reported (R7/R8), for the same reason `delete_view` reports its cascade.

**Two things a definition may not change once values exist**, both because the stored values
would stop meaning what they say: **`field_key` is never editable** — it is the identity
every value is filed under, and changing it orphans the lot in one statement — and
**`field_type` is refused with a 409 naming the count**, because "Customer" going from text to
select cannot re-interpret what is already written. Dropping a *select option* some task still
holds is refused the same way; adding one is free. The UI shows the derived key while the name
is still being typed, since that is the last moment anybody can change it.

**Custom fields are revertible, which makes them first-class.** `patch_task` folds a custom
edit into the SAME `field_change` activity under namespaced keys (`custom.<key>`) rather than
inventing an activity type — `record_activity` refuses a type the migration's CHECK does not
list, the trap that made every attachment upload answer 422, and a custom field changing *is*
a field change. Revert then restores it by **merging onto what the task holds now**, never by
writing back the whole object: another field may have been edited since, and replacing the
blob would silently undo that too — a revert that reverts more than it names.

**A bug this ticket's own tests caught before it shipped.** `changedValues` compared a form's
boolean against a `null` baseline, so a task with an unanswered checkbox sent `open: false` on
*every* save and posted a timeline entry for an edit nobody made. A checkbox has no "unset"
state to render — an unticked box and a never-answered field are the same pixels — so `false`
is the baseline for a boolean and `null` for everything else.

**A fence that was quietly a subset check.** `test_projects_routes` asserted that a list of
paths was mounted, which catches the module somebody remembered to add a path for — i.e. the
one least likely to have been forgotten. It now also reads the package directory and asserts
that **every module declaring a `@router` route is imported by `__init__.py`**, which is the
trap `department_centers.md` C1 documents: a module left out mounts nothing while every test
that calls its functions directly still passes. Verified by deleting the import and watching
it fail.

### 11.10 WS-27m — the tag registry (built 2026-08-07)

The fifth row of the parity backlog, and the one the research notes left open **on purpose**.
`paca_pm_research_2026-08.md` row 13 REFUSED Paca's model — *"a bare jsonb string array on
tasks. No registry, no colors, no rename/merge — the weakest part of Paca's model; don't copy
it as-is"* — and §5's non-goals shipped `pm_tasks.tags TEXT[]` in its place with a registry
named as additive later. This is that registry.

Migration `156_projects_tags.sql`, `routes/projects/tags.py`, `lib/tags.ts`, a picker in the
task panel, a tag row in the filter bar, a `tag` axis on the board, and a manager behind
**Tags** in the header. 31 hermetic + 37 vitest cases, 16 mutants red, 30 checks against a
real Postgres.

**The array stays.** The obvious "proper" alternative is a join table, and it is the wrong
trade here for the same reason §11.9 gave for custom fields: the array arrives with the task,
and its GIN index (146) already answers *"tagged X"* without touching another relation. A
join table would add a row per tag per task and a join to every board paint, to buy
referential integrity this app can enforce in one place instead.

**What the registry buys, given that:**

* **One spelling per tag.** "Bug", "bug" and "BUG" are one tag, so filtering by it finds all
  of it rather than a third of it. The task's array stores the **registry's** display form,
  which is what makes that true rather than aspirational — and what lets a rename be one
  statement. Identity is case-insensitive (a unique index over `lower(name)`, so two racing
  requests cannot create both); display is case-preserving.
* **Rename**, which rewrites every task wearing the tag and reports the count.
* **Merge**, which is the answer to the real failure mode of free tagging.
* **A colour**, so a board can show a tag rather than spell it.

**Applying an unregistered tag REGISTERS it.** Refusing would make tagging a two-step errand —
leave the task, create the tag, come back — which is how tagging gets abandoned, and an
abandoned tag set is worse than a messy one. **The cost, stated: every typo becomes a tag.**
Which is exactly why merge is here and is not optional, and why the picker *shows* the moment
of creation ("Create …") rather than minting silently.

**A rename onto an existing name is a 409, not a silent merge.** They are different operations
with different outcomes — a merge destroys one tag — and quietly doing the destructive one
because the names collided is the kind of surprise that stops people using a rename button.
The error names the tag that is in the way and points at merge.

**A task carrying BOTH tags ends a merge with the target once.** That is the case that is easy
to get wrong, and getting it wrong leaves a duplicate that renders twice and survives the next
merge too. `merged_tags` is a pure function precisely so that case can be asserted directly —
and it is why the rewrite runs over the affected rows in Python rather than as an
`array_replace`, which would leave the duplicate.

**Two tag filters, because both questions get asked and one cannot answer the other.** `tags`
is ANY (`&&` — *"bugs or regressions"*), `tags_all` is ALL (`@>` — *"the ones that are both"*).
Collapsing them into one parameter would silently pick a meaning, and with three tags the two
answers differ by almost everything. Both use the operators the existing GIN index serves.

**On the board, a task with three tags appears in three columns** — the same honesty as a task
with two assignees appearing in both theirs (§11.8).

**The migration backfills, and rewrites data — deliberately, and narrowly.** `tags` has been on
`pm_tasks` since 146 and the import path writes them, so an empty registry beside a tagged
corpus would mean the first rename found nothing and the manager showed nothing. The winning
display form is **the spelling people actually use** (the most frequent), ties broken
deterministically — `min()` alone would canonicalise a corpus of 400 "Bug" to a single stray
"BUG". Task arrays are then made to agree with the registry: it only ever replaces a tag with
a different *casing* of the same tag, the meaning is identical, and the count is reported in a
NOTICE. Leaving it undone would make the registry's central claim false on day one.

**A bug the live run caught in that block.** The canonicalisation used
`FROM pm_tasks t, unnest(t.tags) AS tag LEFT JOIN pm_tags g ON … t.root_project_id …` — with
the implicit comma form the `LEFT JOIN` binds only to `unnest(...)` and `t` is not in scope
for its `ON` clause, so the migration aborted with *"invalid reference to FROM-clause entry
for table t"*. Rewritten as `CROSS JOIN LATERAL … WITH ORDINALITY`, which also fixed a second
problem the first version would have shipped: `array_agg(DISTINCT ...)` sorts by its own
expression, so every task's tag list would have come back alphabetised.

### 11.11 WS-27n — bulk edit (built 2026-08-07)

The sixth row of the backlog, and **the one §11.3 names as gating WS-27g**. That dependency is
now satisfied: the cutover imports a real workspace, and an import that cannot be re-triaged
in bulk is one somebody abandons halfway, leaving two live systems — the exact state the
retirement exists to end.

`routes/projects/bulk.py` (`POST /projects/tasks/bulk`), `lib/selection.ts`,
`components/BulkBar.tsx`, plus checkboxes on the board and the list. 35 hermetic + 32 vitest
cases, 16 mutants red, 34 checks against a real Postgres. **No migration.**

**It reuses `automation.apply_task_patch` rather than growing a second writer.** That service
exists because WS-27f needed a task edit *indistinguishable in validation from a human PATCH*;
a bulk endpoint with its own field handling would be a third opinion about what a task edit is,
and three opinions drift. Assignees and tags are separate write paths on the task, so those
are handled here — once, and through the same registry the panel uses, because the tag
registry cannot be true if bulk is a second door into the array.

**Status is named, never keyed — and here that is load-bearing rather than stylistic.** A
selection can span projects, and a status id belongs to exactly one root. Sending `status_id`
for fifty tasks across three projects would put two thirds of them in a lane that is not
theirs, or fail on the foreign key. `status_id` in a bulk patch is therefore a 422 with its
own message, because it is the mistake somebody makes by copying a single-task PATCH body and
"unknown field" would not explain why the thing that works on one task is refused on fifty.

**Assignees and tags are ADD/REMOVE, never SET.** "Assign these to Priya" means *also* Priya;
a replace across a selection wipes every individual assignment the fifty tasks already
carried. The destructive spelling is absent rather than merely discouraged.

**Shape is validated once, up front; outcomes are per task.** Those are genuinely different
failures. A field nobody can set is the same mistake for every task in the selection and earns
a 422 before anything is written. A status name that exists in one project and not another is
a fact about *that task*, and failing the whole batch for it would make a mixed selection
unusable — which is precisely the selection somebody makes after an import.

**A task the caller cannot see is skipped, not an error** (R5). Reporting it per id says
exactly what a per-id 404 would say and nothing more; aborting instead would let a caller
probe for existence by watching whether the batch failed. **One transaction**, because partial
application is the worst outcome available: a re-triage that half-happened is harder to
recover from than one that did not, since nobody can tell which half.

**Re-asserting a value is not a change.** Fifty tasks that were already Priya's would each
gain a timeline entry saying nothing, and a bulk edit reporting "50 changed" when it changed
nothing is one whose count nobody can trust. `moved_people` is pure so that claim is asserted
directly.

**One notification per person per batch, not one per task.** Being handed fifty tasks should
ring once and say fifty. Fifty bells is a bell people turn off, and a muted bell notifies
nobody about anything — WS-27j's own argument, applied to the case that would have broken it.

In the browser, the selection is **pruned whenever the filter changes**: selecting forty,
narrowing to three and pressing Done must not act on thirty-seven tasks nobody can see. A
shift-click ranges over the board's *on-screen* order (after filtering and grouping), and a
task drawn in two columns — a two-assignee card, §11.8 — counts once. The outcome line names
every category including the boring ones, because "47 changed" against "I selected 50" is a
support conversation, whereas "2 already like that, 1 not available" is a sentence somebody
already read.

### 11.12 A shipped notification bug this ticket uncovered

Building bulk assignment surfaced a defect in WS-27j that had been live since it shipped.

**There are two ways to see a task** — a grant on its project, or being an assignee of it —
and `core.task_visibility_clause` says so in one place, with a docstring warning that a second
implementation "would drift the moment one is edited alone". `notifications.deliverable` had
drifted exactly that way: it probed only `vis.project_clause('t.root_project_id')`.

Two consequences, both silent:

1. **Anybody assigned work in a project they hold no grant on was judged undeliverable.** They
   could open the task — `get_task` uses the shared clause — but the assignment that put them
   there notified nobody, and the response told the assigner they could not see it. That is
   the silent assignment WS-27j exists to end, still open for the most common case in a
   grant-scoped app: delegating outward.
2. **Scoping to `root_project_id` also missed a grant made on a SUBPROJECT.** The old test
   asserted that scoping, on the argument that "probing `project_id` would miss a grant made
   on an ancestor" — which is backwards, and running it against a real Postgres is what showed
   it: the grant closure is recursive and expands *downward*, so `project_id` catches an
   ancestor grant, while `root_project_id` is the one that misses a subproject grant.

`deliverable` now uses `task_visibility_clause`. The test that encoded the wrong reasoning has
been replaced with one that records why it was wrong, and a second asserts the shared clause
still carries both branches.

**A fake collision the fix exposed, worth recording.** The hermetic suite's `FakeDB` matched
the rule-3 probe by substring. The new clause embeds both `pm_task_assignees` and the
closure's `UNION`, which is exactly the fingerprint the *audience* branch used — so
`deliverable` received a list of assignees where it expected a visibility answer, and four
tests failed for a reason with nothing to do with the code under test. The probe is now
matched first and the audience branch keys off `assignee AS who`, which only its own query
has. A fake that dispatches on substrings needs its fingerprints to be *specific*, not merely
present.

### 11.13 WS-27o — recurring tasks (built 2026-08-07)

*"Every operations cadence is recurring. Without it those live in someone's head or in
ClickUp."*

Migration `160_projects_recurrence.sql`, `routes/projects/recurrence.py`, `lib/recurrence.ts`
and a repeat row in the task panel. 45 hermetic + 27 vitest cases, 31 mutants red, 39 checks
against a real Postgres.

**No scheduler — and that is forced rather than chosen.** §5's non-goals: *"A second
automation engine. ADR-028/D6: `/workflows` is the only engine; WS-27 contributes events and
node types to it."* A recurrence worker here would be exactly that second engine. So the
successor is created **when a task closes**: `apply_status_transition` already owns that
moment, which means a task finished from the board, from My work, from an automation or from a
bulk edit all recur identically. A second call site would be a fifth way to finish a task that
forgets to.

**What that costs, stated rather than discovered.** A series only advances when somebody
finishes the current one. A monthly report nobody closes does not pile up twelve copies —
which is right — but a daily stand-up nobody ticks does not appear tomorrow, which is the
honest limitation. Materialising ahead of time is already reachable through the engine that
owns scheduling (a cron trigger plus the `pm_task` node WS-27f added), so nothing here has to
be undone to get it.

**The anchor is per rule, because the two answers mean different things.** `due` keeps the
schedule — "stock count on the 1st" stays on the 1st however late the last one was closed, so
the series does not drift. `completed` measures the interval from when the work was actually
done — "water the plants every 3 days" restarts when you water them. Neither is a sensible
global default. A `due` anchor also **catches up**: a monthly task closed six weeks late would
otherwise produce a successor already overdue the moment it appeared, which teaches people the
date is meaningless. The missed occurrences are *skipped rather than backfilled* — nobody
wants four copies of a stand-up they did not attend.

**The date arithmetic is where this is either right or quietly wrong for a year**, so it is
pure and each case is one assertion:

* **January 31st, monthly.** The day is clamped at *computation* time and stored as asked.
  Storing the clamped value instead would permanently demote the rule to the 28th after its
  first February.
* **February 29th, yearly.** The same shape, once every four years.
* **"Every other Monday and Thursday."** Within a week the rule takes the next allowed day;
  only when the week runs out does it jump `interval` weeks. A naive `+14 days` alternates
  between the two days instead of giving both days of every second week.
* **A stand-up at 09:00** stays at 09:00.

**Closing a task twice must not spawn twice.** A task can cross into `done` more than once —
close it, reopen it to add a note, close it again — and every crossing reaches the same seam.
`recurrence_spawned_at` is the guard, and it is never cleared: reopening undoes `completed_at`,
but it does not un-emit a successor that already exists and may already have been worked on.

**Stopping a series keeps the work.** Deleting the rule detaches the tasks it produced rather
than deleting them: they are real work, some of it finished, and a "stop repeating this"
button that swept away three months of completed reports would be the last time anybody
pressed it.

**Two bugs the live run caught, and reading could not.**

1. **The weekly CHECK passed the very row it existed to reject.**
   `CHECK (freq <> 'weekly' OR array_length(weekdays, 1) >= 1)` looks correct and is not:
   `array_length('{}', 1)` returns **NULL**, `NULL >= 1` is NULL, and a CHECK constraint only
   *fails* on FALSE. A weekly rule with no weekdays inserted happily. `coalesce(…, 0)` fixes
   it, and a test now asserts the coalesce is present because the hermetic suite has no
   database to try the expression on.
2. **`_next_number` and `_default_status` were reimplementations**, and one of them invented a
   column (`last_number`; the real one is `last_value`). Both were replaced by `core`'s own
   `next_task_number` and `load_default_status` — the same mistake WS-27n had just been careful
   to avoid, made two tickets later in the same package.

**A third, caught by its own test:** `int(rule.get("interval") or 1)` turns an explicit `0`
into "every 1" — a typo that looks exactly like a save, and one the database's CHECK would
then have refused as a 500 rather than a 422. Absent now means "every 1"; zero means the
sender made a mistake.

**In the browser, the sentence is the feature.** A form of five controls is a shape; *"Every 2
weeks on Mon, Thu, keeping to the schedule"* is something somebody can check before committing
to it — shown live rather than on save, because picking the wrong anchor is invisible until a
cadence has drifted for three months. The occurrence limit reads as what is **left**, not the
cap, and switching frequency clears the fields the new one does not use so a stale
`day_of_month` cannot reappear.

### 11.14 WS-27p — dependencies and subtasks, made reachable (built 2026-08-07)

*"`pm_task_links` and `parent_task_id` both exist, unreachable from the board. Data with no
surface is a promise the product does not keep."*

`routes/projects/relations.py` (`GET /projects/tasks/{id}/relations`), `lib/relations.ts` and
a relations block in the task panel. 21 hermetic + 16 vitest cases, 11 mutants red, 19 checks
against a real Postgres. **No migration** — the tables have been right since 146.

**Both halves were unreachable, and for different reasons.** Links could be *created* and
*deleted* since WS-27a but never **listed**: `get_task` returns a `links` **count** and nothing
else, so no client could draw one. Subtasks could be created from the panel but never listed
either — `?parent_task_id=` has existed on the list endpoint since WS-27a and nothing called
it. What was missing was a way to read them, and one rule nobody had written down.

**That rule: `blocks` may not form a cycle.** `assert_no_task_cycle` has guarded
`parent_task_id` since WS-27a, and the identical hazard sat unguarded on links the whole time.
A blocks B blocks C blocks A is a deadlock no human can resolve by finishing something, and
every walk over it runs forever. `assert_no_block_cycle` closes it, bounded by the same
`MAX_DEPTH` its sibling uses, and it **tracks what it has seen** — data can already contain a
loop, since every link created before the guard existed went in unchecked, and the walk has to
terminate over one rather than spin.

**Only `blocks` is guarded.** A cycle in `relates_to` or `duplicates` is redundant, not
harmful, and refusing one would be a rule with no failure to prevent.

**Blocked-ness is DERIVED and SHOWN, never enforced.** A task is blocked when something that
blocks it is still open, so a blocker reaching `done` makes the section go quiet — that is how
you learn you can start. Refusing to *close* a blocked task is the obvious next step and is
deliberately not taken: dependencies in a real workspace are frequently approximate, and a
tool that will not let somebody finish work they have finished is a tool they route around —
after which the links stop being maintained and the feature is worse than absent.

**Visibility is applied to the CHILDREN, not inherited from the parent.** A subtask can be
moved into a project the reader cannot see, and listing it because its parent is readable
would disclose a title from behind a grant. The live run asserts a subtask in an ungranted
project is absent *and* that its title does not appear.

**One endpoint, both directions.** `blocks` outgoing means "this holds those up"; incoming
means "this is waiting". A client given one side would have to ask twice and would still not
know which was which — so each link carries a `direction`, and the browser's `populated()`
turns that into headings, with **Blocked by first** because it is the only section that
changes what somebody should do next. Empty sections are dropped: six empty headings on every
task is how a panel becomes something people scroll past.

**Progress counts the status CATEGORY**, not `completed_at`, for the same reason everything
else in this app does: a project can name its finished lane "Shipped" or "Signed off", and
`cancelled` counts as resolved even though nothing was completed. It reads as "1 of 3" rather
than a percentage — 33% is a worse answer than "1 of 3" to the question people are asking.

### 11.15 WS-27s — the shared task card (built 2026-08-07)

Not on the parity backlog, and asked for directly: *"the UI, kanban, task cards etc can be
taken from the tasks app right? so that the experience seems familiar?"*

**Familiar, yes. Taken, no — and the difference is the whole ticket.** `/tasks`'s `TaskCard`
is 395 lines bound to `useTaskStore` and to `GtdItem`'s own fields — `energy`, `deepWork`,
`disposition`, `nextAction` — none of which `pm_tasks` has or should grow. Worse, D-PM-6 has
`gtd_items` retiring at WS-27h, so a straight port would take the Projects board down with
it. What moved instead is the **vocabulary**: `@/lib/taskCard` holds how a duration reads,
what an avatar's letters are, what counts as overdue, and which chips a task earns;
`@/components/TaskMeta` is the one file that turns a tone name into a colour. Both apps draw
from those, and neither knows about the other's store.

**A card can only show what the LIST endpoint returns, and it was returning almost nothing.**
`pm_task_links` and `parent_task_id` have been readable since WS-27p — *one task at a time*.
A board draws them on every card at once, so this ticket is mostly a backend one: two
aggregates over the page's ids (`attach_relation_counts`), filling `subtasks {done,total}`
and `blocked_by_count` on every row. Per card it would be N+1 across an imported workspace of
hundreds, and at the three-task scale of any test the two look identical.

**A finished blocker does not block, and the count says so in SQL.** The same rule WS-27p's
`blocked_by_open` makes, moved into the aggregate rather than applied after: a card still
marked blocked after its dependency shipped is a card people learn to ignore, and one round
trip per card to find out is the N+1 again. Archived subtasks leave the denominator for the
matching reason — counted, "2/3" could never reach 3/3.

**A zero earns no chip.** Most tasks have no subtasks, no tags and no blockers; drawing "0"
for each turns the meta row into noise and pushes the chips that mean something off the edge
of a 288px column. Chip order is fixed — blocked, due, progress, then the quiet counts — so
the row can be scanned rather than read.

**Overdue is past due AND still open**, and it changes the *icon* as well as the tone, so the
signal survives a reader who cannot tell muted from destructive. `/tasks` was checking only
the date, which painted every completed task with a past due date red forever; sharing the
function fixed that side too, and it is the one behaviour change this ticket makes outside
Projects.

**What the card honestly does not claim.** No attachment count and no estimate: attachments
are counted on the single-task read (WS-27i) and there is no estimate column at all. A
plausible zero would be the card asserting something the endpoint never told it.

The hermetic fake needed teaching, as it did for WS-27n — and the lesson recorded there
applied again: every clause in the two roll-ups is mirrored **only when the statement carries
it**, and which end of a `blocks` link is the blocked one is read off the SQL rather than
assumed. A mirror that filters unconditionally agrees with itself no matter what the route
stops emitting, which is how a deleted WHERE clause survives a green suite.

### 11.16 WS-27q — the calendar (built 2026-08-08)

**Backlog row 9 was named "Calendar / timeline view" and this built the calendar half only.**
Recorded here because closing the whole row was wrong: a month grid of day cells answers *what
is due when*, and a timeline of bars on a continuous axis answers *what runs alongside what*.
They are different questions, the second is the one a multi-month project asks, and the row is
now split — 9a closed, **9b open as WS-27t**.

The first view that **cannot be a page**.

**`/projects/tasks` is paginated, which is right for a list and catastrophic for a
calendar.** A month with ninety tasks read at `page_size=50` draws forty of them and leaves
the other days looking EMPTY. A short page announces itself — "page 2 of 3"; a short month
does not, and nobody investigates a quiet week. So `GET /projects/calendar?from=&to=` takes a
WINDOW, returns everything in it, and when the cap is reached says `truncated` rather than
handing back a plausible-looking month.

**`start_date` has existed since migration 146 and no surface had ever shown it.** The same
complaint §11.14 makes about links, and the reason a calendar is the view that needed
building: a task is a BAR from its start to its due date, not a dot on one day.

**Overlap, not equality.** A task that starts Monday and is due Friday belongs on Wednesday's
cell. `due_at BETWEEN :from AND :to` — the implementation everyone writes first — puts it on
Friday alone, which is exactly the week somebody looks at Wednesday and concludes they are
free. The clause is `coalesce(start_date, due_at) < :to AND coalesce(due_at, start_date) >=
:from`, so a task with one date is a point and a task with both is a bar.

**A task with NEITHER date falls out through NULL**, which is correct and invisible — so
`undated` counts them with the SAME filters and the view says "12 unscheduled". Dropping them
silently is how a calendar comes to look like the whole workspace while showing a third of it.

**The window is read in UTC and the client asks for a day of slack.** A `start_date` is a
floating calendar date and a `due_at` is an instant; no single frame makes both exact, since a
`due_at` of 23:00Z sits on the next day in IST and the previous one in PST. Rather than
pretend, the server OVER-selects and the browser — the only party that knows the viewer's
timezone — does the placement. `start_date` is anchored with `AT TIME ZONE 'UTC'` rather than
`CAST(… AS timestamptz)`, which would silently read the connection's `TimeZone`: a session
setting no caller controls and no test would notice changing. A live run with the session set
to `America/Los_Angeles` pins that.

**Filters carry across the switch, and one is deliberately excluded.** Board and calendar are
the same question in different shapes, so a filtered board that shows everything on the
calendar reads as the FILTER breaking. `due_before` stays out because it bounds the same
column as the window and the loser of a contradiction leaves no trace; `overdue` looks like
its twin and is not — "already late" is a fact about the status as much as the date. Since
FastAPI **ignores an unknown query parameter**, a dropped filter is not an error but a silent
behaviour change, so a test asserts the calendar's parameter set covers the list's minus a
named, reasoned exclusion list.

**No second write path.** Dragging a card is `PATCH /tasks/{id}` — the same validation, the
same `field_change` activity, the same revert. A `POST /calendar/move` is how two paths start
disagreeing about what is allowed.

**Dragging a bar moves the WHOLE bar, and keeps the time of day.** The span is an estimate
somebody made; a drag that silently shortens it to one day destroys information the user did
not offer to change. Writing only the dropped date — the version every calendar implements
first — leaves the other end behind and inverts the interval the moment you drag left. "Due
Friday at 5" dragged to Monday is due Monday at 5.

**`new Date("2026-08-07")` is midnight UTC**, which is the 6th anywhere west of Greenwich, and
routing a `start_date` through it is the single most common way a calendar loses a day. The
grid works in `YYYY-MM-DD` keys throughout. That claim is only *behaviourally* testable west
of Greenwich — in UTC and everywhere east, the buggy version happens to give the same answer —
so the suite runs in four timezones AND pins the rule structurally, because CI runs in one.

**Building it found a hole in the test fake.** `overdue`'s date half (`due_at < now()`) had
never been mirrored, so every `overdue` test since WS-27k was really asserting only the
status half and would have passed with the date comparison deleted. Teaching the fake `<
now()` killed that mutant on the list endpoint as well as the calendar.

### 11.17 WS-27t — the timeline, and dependencies you can draw (built 2026-08-08)

Asked for directly: *"a timeline view that can also make tasks and subtasks dependent on each
other, with wiring them to each other, similar to how it works on Jira and ClickUp."* Two
things, and the second is the one that matters — **a Gantt chart with no dependency gesture is
decoration**, which is exactly why Gantt was a non-goal until this was asked for.

**Almost none of this was schema work.** Dates, links, the cycle guard, the both-direction
read, blocked counts and the interval-overlap window all shipped in WS-27a/p/q/s. The whole
ticket is one aggregate, some geometry, and a gesture.

**The window is the resource, so there is no `/projects/timeline`.** `GET /projects/calendar`
grew `include_links`, and calendar and timeline are two renderings of one question — the same
rule §11.8 states for list and board, extended a third time. A second endpoint would be a
second filter surface to keep in step.

**An arrow needs two bars, so an edge is returned only when BOTH ends are in the window.**
The edge to an off-window blocker is not lost, it is undrawable: `blocked_by_count` already
badges the visible bar, which is the honest rendering of *"something you cannot see is holding
this up"*. Only `blocks` is drawn — `relates_to` and `duplicates` have no direction that means
anything to a schedule (WS-27p's `DIRECTED_TYPES`), and an arrow would claim a sequence nobody
asserted.

#### D-PM-11 — hierarchy depth decides what earns a bar

Top-level tasks get rows; subtasks fold in and expand on a chevron. **A parent with no dates
of its own borrows its children's span**, marked `derived` and drawn dashed, because otherwise
the default view is blank for exactly the projects that use subtasks properly. **A subtask
whose parent is off-window is promoted to its own row** rather than hidden — hiding it is how
a filtered timeline silently drops work.

Paca's Timeline pre-filters to a reserved `Epic` type instead. Rejected: `pm_task_types` is
per-project data with no reserved names (D-PM-2), so "Epic" would have to become either a
seeded row every project inherits or a name-match that stops working the day somebody renames
a type. `parent_task_id` already means depth and cannot be renamed.

#### D-PM-12 — an arrow WARNS; it never reschedules

The owner was given the three options and delegated the choice back. Chosen: **constrain, but
only warn**. A `blocks` edge whose blocker's END falls after the blocked task's START is drawn
in the danger tone with a sentence that says *nothing has been rescheduled*.

**Why not Jira's auto-push,** which was the reflex answer: the useful half of a dependency is
*knowing* — being told, the moment you move something, that two tasks now disagree. Auto-push
delivers that and then also silently rewrites other people's dates, which is where it stops
being useful. The cascade lands in the activity spine (§3.8) as dozens of `field_change` rows
and dozens of notifications with no single act to point at, and the first time somebody's
negotiated date moves without them touching it, they stop trusting the dates. It also
contradicts WS-27p's written position — blocked-ness is **derived and shown, never enforced**
— which now stands unamended. (b) remains reachable later as an opt-in per project, with the
cascade bounded and previewed before it writes; that is a better version of it than the one
that would have shipped today.

Three sub-rules, each one a way the warning could have become noise:

* **equal dates are not a conflict.** A blocker due the 10th and a task starting the 10th is
  the normal way people schedule a handover. Flagging it fires on half a healthy plan, after
  which nobody reads the warning at all.
* **a missing date on either end is not a conflict.** It is unknowable, and a warning that
  fires on absent data teaches people it means nothing.
* **a finished blocker never conflicts.** WS-27p's rule applied to the warning exactly as it
  applies to the badge.

**One rule, two surfaces.** `conflicts()` is pure and lives in `lib/timeline.ts`; the timeline
colours its arrows with it and `RelationsBlock` writes its sentence with it — which is why
`GET /tasks/{id}/relations` grew `start_date` and `due_at`. Two implementations of *"does this
start before its blocker finishes"* would eventually disagree, and the one that got it wrong
would be the surface nobody was looking at.

**The cycle check is NOT duplicated in the browser.** `canLink` refuses only self-links and
exact duplicates; `a→b` when `b→a` exists is allowed through so `assert_no_block_cycle`
refuses it with its own message. A second bounded graph walk in the client is the one that
drifts, and a drag creates a link through the same `POST /tasks/{id}/links` the panel's
dropdown uses — same guard, same activity, same permission.

**Paca gave the layout and nothing else.** `roadmap-view.tsx` (438 lines, Apache-2.0):
sticky task column, fixed pixels-per-day, month cells walked with `Date(y, m+1, 1)`, a today
line, a data-fitted range with padding, an undated task listed and unbarred. It draws **no
dependency arrows** and is **entirely read-only**, so everything from the handle onwards is
ours.

**Two rounding traps, and only one is catchable in CI's timezone.** `dayPx` rounds its
millisecond division because a range that straddles a DST transition is 23 or 25 hours across
it, and an unrounded quotient lands a fraction of a day off for every day after — permanently.
The behavioural test only fails in a zone that *has* daylight saving, so the rule is pinned
structurally too, the same treatment `new Date("2026-08-07")` gets. A bar also covers its
**last** day rather than stopping at that day's left edge; the alternative makes every span
one day short and a one-day task a zero-width line.

**Found while building:** the fake's mirror of the new edge query hard-coded which column was
the blocker, so a mutant that swapped the SQL's two aliases — every arrow drawn backwards —
passed the whole suite. It now reads the roles and the membership tests off the statement, and
both mutants die behaviourally.

### 11.18 WS-27r — the search surface, and the LIKE defect under it (built 2026-08-08)

The last row of the parity backlog. *"`?q=` exists on the list endpoint; there is no search
surface."* Both halves turned out to be true, and the second one was worse than advertised.

**`_` and `%` were live wildcards on every search anybody had done.** `build_task_filters`
bound `%{q}%` raw, so `_` — LIKE's single-character wildcard — meant `task_id` also matched
`taskXid` and `task-id`, and `50%` quietly meant `50`. In a workspace where people search for
identifiers all day that is a steady drip of hits nobody asked for, and it reads as fuzzy
matching rather than as a bug. `like_escape` fixes it **on the shared builder**, so the board
and every saved view get the fix, not only the new endpoint — fixing only the new code would
have left the bug exactly where people meet it.

**Why a second endpoint, having twice argued against one.** The list answers *"which tasks
match these filters, in this order, on this page"*; search answers *"what did you mean"*.
It **ranks** — and the list's ordering is a column allowlist (`TASK_SORTS`) that deliberately
cannot express relevance, so a `sort=relevance` would be a sort key that only works when `q`
is present, a worse contract than a separate route. It is **capped, not paged**: nobody pages
through search results, they retype, and page 2 of a relevance ordering is where relevance has
run out. And it **names the project**, which the list does not because its caller already has
the tree. What decides *what a caller may see* is still shared — same
`task_visibility_clause`, same archived rule — so search can never surface what the list would
hide. That is the part that must not be duplicated; the rest is a different question.

**Ranking happens in SQL, before the `LIMIT`.** Ranked afterwards over a capped set, the best
answer is only present if it was already inside the arbitrary fifty rows the database happened
to return — a defect that presents as "search is bad at long queries". Four tiers: the exact
task number, a title PREFIX, a title match, then description-only; ties break on recency, then
id, so a repeated search does not reshuffle.

**`#42` is a task number.** People quote them, and a search box that returns every task whose
description mentions 42 has ignored what was typed. Bounded to eighteen digits — `task_number`
is a BIGINT, and an unbounded `int()` on user input is a parse nobody asked for.

**A short query is empty, not a 422.** A search box types one character on the way to three,
and an error flashing on every keystroke is noise the user cannot act on. Below the minimum it
costs no database round trip at all.

**Comments are deliberately not searched.** The largest text in the system and the least
likely to be what somebody is hunting by name; a comment hit would also have to render as its
task, which makes ranking across the two incomparable. Recorded so the absence reads as a
decision rather than an oversight.

#### The palette

`⌘K` from anywhere in Projects, not a search page: the question is *"where is that task"*,
asked while doing something else, usually about a project the person is not looking at. A page
makes finding something a place you navigate **to**, which is one navigation more than the
problem has.

Four rules that only break under real typing speed on a real connection, so all four are
pure functions in `lib/search.ts` rather than something to click at:

* **"No results" may be claimed only once, and never while a request is in flight.** Shown
  during the gap it flashes between every keystroke and its answer — the commonest bug in
  hand-rolled search UIs, and it reads as the search being broken rather than slow. The
  previous results stay on screen while the next load runs, so the list does not blank and
  re-fill under the cursor.
* **A stale response must not win.** "par" and "parser" are two requests with no ordering
  guarantee; a slow "par" landing last replaces the right answers with old ones and the list
  changes without a keystroke. The endpoint echoes `query` back, so the guard needs no request
  ids.
* **The arrows belong to the palette, unless a modifier is held.** Left to the browser they
  move the text caret to the start or end of the query — two effects from one key. But
  `Cmd+Left` is "go to line start", and stealing it breaks editing inside the palette's own
  box.
* **The highlight needle is escaped before it becomes a regex.** Searching `(draft)` would
  otherwise throw a syntax error and blank the palette — the browser-side twin of the very
  LIKE defect this ticket fixed on the server.

**Found by the live run, invisible to all 43 hermetic tests:** `:number IS NOT NULL` names no
column, so Postgres has nothing to infer the parameter's type from and asyncpg answers
`AmbiguousParameterError: could not determine data type of parameter $1` — the query never
runs. A Python fake has no type system to be ambiguous about. Fixed with an explicit
`CAST(:number AS bigint)` and pinned structurally, because that is the only level at which a
hermetic suite can hold it.

**The fake learned to read LIKE properly.** `like_to_regex` translates `%`, `_` and the
backslash escape rather than doing a substring match — a mirror that treated the pattern as a
literal would have agreed with both the escaped and the unescaped implementation, and the
whole defect would have been invisible to the suite that exists to catch it.

### 11.19 Plane research — the beyond-parity queue (research 2026-08-09)

*"I want you to learn and study this project as well and add it as another reference in
addition to Paca … come back with findings about what we can actually lift from it to make
our system fully featured and better, both in terms of backend as well as UI/UX."*

Second reference studied: `makeplane/plane` v1.4.1. Full findings, evidence, and the
consolidated verdict table live in **`specs/plane_pm_research_2026-08.md`** (reference-only,
owns no work — same posture as the Paca doc). ⚠️ **Plane is AGPL-3.0**: patterns and
interaction designs only, never code — categorically stricter than Paca's Apache-2.0, and
the research doc's license wall is binding on every ticket below.

**What the research changed here:**

1. **Twelve of our shipped decisions are now validated against a second production
   codebase** (research doc §2): per-view ordering, the trigger-enforced tenant key, the
   atomic counter, cycle guards (Plane has none), the single visibility predicate,
   404-never-403, validate-then-apply bulk, page-batched aggregates, 422-over-fallback
   (theirs arrived after two CVEs), statuses-as-data + priority-as-enum, single-writer
   `completed_at`, agent-as-member. None of these should be re-litigated against a future
   reference without reading that table first.

2. **The beyond-parity ticket queue.** §11.2's ClickUp-parity backlog is CLOSED; the next
   backlog is Plane-informed, tabled as P-1…P-31 in the research doc §8. The high-value
   head of the queue, in recommended build order:
   - **Intake/triage** (P-1) — wrapper row + `triage` status category excluded from default
     lists + accept-in-place; the front door §6.5's email capture and agent-created tasks
     have been missing. Pairs with `/workflows` for routing (D6: states in PM, automation
     in the engine).
   - **Watchers + mention diffing** (P-2) — `pm_task_watchers`, auto-subscribe on touch,
     edits notify only *new* mentions.
   - **Archive guard** (P-3, one predicate, do immediately) — refuse manual archive unless
     the status category is done/cancelled; an archived open task silently exits every
     default list.
   - **Spreadsheet layout + kanban sub-grouping + display-properties contract + group-context
     quick-add** (P-10…P-13) — the four UI gaps with the highest daily-use value.
   - **Auto-archive policy** (P-4) — `archive_in`/`close_in` on root projects; sweeper is a
     `/workflows` scheduled workflow, never a PM cron.
   - Activity meta id+label rule and description-edit coalescing (P-5); semantic sort ranks
     + deterministic tiebreaker (P-6); picker exclusions in search (P-7); human task IDs
     surfaced with copy-link (P-21).

3. **Two owner questions minted — and answered the same day** (research doc §6): **Q1**
   public read-only boards → **deferred, D-PM-14** ("revisit when needed"); **Q2** who owns
   free-form project docs → **the knowledge base, D-PM-13** — PM links to creator-owned,
   grant-shared KB documents and never grows a docs surface of its own.

4. **A non-goal reversed in part**: §5 refuses "a docs surface" and "sprints" — both stand,
   but the sprints refusal now carries Plane's reference design (join-table membership,
   snapshot-on-close, carry-forward — research doc §3.7) so the eventual build starts from
   a settled shape rather than a blank page.

### 11.20 WS-27ag — the house shell, and a mobile UI at all (built 2026-08-10)

**The measured problem.** `/projects` shipped twenty-plus letters of function with **no
mobile layout of any kind**. `page.tsx` imported neither `useViewMode` nor
`useMobileDrawer` — the only cross-cutting app in the tree that did not — so a phone got
the desktop tree: a fixed 256px `<nav>` beside a five-mode canvas inside `AppShell`'s
`pb-nav` scroller, and a **third** column the moment a task was opened.
`AppShell.tsx` enumerated `isChatPage`, `isEmailPage`, `isTasksPage`, `isNotesPage`,
`isWhatsAppPage` and `isAppWorkshopEditPage`; there was no `isProjectsPage`, so the bottom
bar offered nothing but Menu. Separately the app owned no page shell (Tasks and Email share
a slim `h-10` bar; Projects went from a non-collapsible rail straight into one `<header>`
carrying six unrelated things), and it was the tree's only systematic user of `bg-accent`
where the house active token is `bg-primary/10 text-primary`.

**Shipped.** Frontend only; no migration, no API change, no new dependency.

1. **The mobile branch.** One pane. `ProjectTree` + My work move into the shell drawer as a
   sheet; the view-mode picker becomes a second sheet; an opened `TaskPanel` becomes a
   full-screen `fixed inset-0 z-[60]` surface (the panel's own `max-w-md` is a *docked
   column* width, lifted by the shell rather than by the panel, which knows nothing about
   which layout it is in).
2. **`AppShell` gains `isProjectsPage`** and three tabs — **Projects · Views · Search** —
   added beside the existing branches, never inside one. The set is exactly what the
   desktop layout owns and a phone cannot otherwise reach: a 240px rail, a toolbar row of
   five modes, and a ⌘K palette with no keyboard. **Notifications deliberately did not get
   a tab**: `NotificationBell` is self-anchored with no external open control, so the bell
   stays in the page's own title row where its 320px dropdown fits a phone.
3. **The house shell on desktop** — a slim `h-10` bar (rail toggle · divider · app title ·
   right-aligned app actions) over a **collapsible** rail at Tasks' `w-60`, replacing the
   third sidebar width in three apps and the one rail that could not collapse.
4. **The six-purpose header splits three ways**: app scope (search, notifications) to the
   top bar; a **title row** (what you are looking at); an **action row** (the five modes
   left, project actions right).
5. **Three `bg-accent` sites → `bg-primary/10 text-primary`** (My work, the mode switch,
   the selected tree node). Four more remain in `FilterBar`, `TaskList`, `MyWork`,
   `SearchPalette` and `lib/tags.ts` — a later slice owns those files.
6. **`"Loading projects…"` de-duplicated** into `LOADING_COPY`, reached through one
   `renderState()` seam that also carries the empty and error surfaces; the failure strip
   stopped wearing `bg-muted` (the token for *quiet*) and now wears `bg-destructive/10`.

**Two seams left for the slice that follows**, both marked `── SEAM (WS-27ag) ──` in
`page.tsx`: the shared `<Toast>` mount point inside `overlays` (one place, above both
layouts, below every dialog), and `renderState()` as the single call site the shared
`EmptyState` replaces.

⚠️ **Two rules were learned here and are written in the code, not just recorded.**
(1) **The shell drawer holds a snapshot** — `AppShell` keeps injected content in its own
state, so a sheet handed over once keeps rendering the props it was built with; the page
re-injects on every change to what the sheet draws, and every callback inside it is a
`useState` setter so the re-injection cannot loop through the drawer's context.
(2) **Dismissing the drawer from the outside must clear the page's `sheet` state**, or the
next tree or mode change reopens the sheet the user just closed.

**Not fenced, and said plainly:** this tree has **no structural or layout test at all** —
`conformance.test.ts` checks colour, icon imports and solid-button chrome, and nothing
checks that an app has a mobile branch, that a bottom-bar tab has a listener, or that a
`cc-mobile-nav` detail string agrees at both ends. Every rule above is **advisory**;
`tsc --noEmit`, the 1278 vitest cases and `npx vitest run src/lib/theme/` were green, and a
production `next build` prerendered `/projects`, but **no browser check was possible in the
build environment** (the Playwright chromium download is blocked), so the phone-viewport
and four-theme pass is owed at review.

### 11.21 S4 — the Projects side conforms, where Tasks is the better one (built 2026-08-10)

The standing ruling on the two task surfaces is **"Projects is canonical, Tasks conforms"**
(that is what WS-27ad and WS-27af did). **These three findings are the exceptions**, where
the defect was on the Projects side. Convergence runs both ways when the evidence says so.
Frontend only — no migration, no API change, no new dependency.

1. **The active token.** `MyWork.tsx` painted its selected context pill
   `bg-accent text-accent-foreground`; the measured house token for active/selected is
   `bg-primary/10 text-primary` (AGENTS.md rule 6 — /tasks, /email, `src/components`).
   Two call sites swapped, plus `aria-pressed` on both, which the toggles never carried.
   **The lasting deliverable is the fence, not the two-line swap**: `conformance.test.ts`
   gains a **sixth rule** — the PAIR `bg-accent text-accent-foreground`, ratcheted per file
   exactly like rules 1/3/5, with `lib/statusAccent.ts` excepted **with the argument** (its
   violet lane's `chip` is a hue, not a state). `hover:bg-accent` and `bg-accent/10` are
   deliberately NOT matched: a gate that cries wolf is one somebody switches off. The
   remaining Projects sites are baselined and can now only go down —
   `FilterBar.tsx` 2 · `SearchPalette.tsx` 1 · `app/people/page.tsx` 1.
2. **The fourth task card is gone.** `MyWork`'s `Row` bypassed `TaskCardShell`, `TaskMeta`
   and `StatusChip` — and `MyWork` is the *personal task list inside Projects*, so it sits
   directly opposite `/tasks` in the owner's comparison and was the one surface that looked
   like neither app. It is now the shared shell, the shared title, the shared chip row
   (`lib/card.cardChips` — so the hand-written "overdue · due · no date" line is replaced by
   the same due/blocked/checklist chips the team board draws) and a `StatusChip` for the GTD
   disposition. What stays local is the *interaction*: complete (which moves shared status)
   and re-triage, the way `/tasks`' `TaskCard` keeps its GTD badges inside the same box.
   The capture field and the triage buttons became `Input`/`Button` primitives.
   **The disposition's hue goes through the shared vocabulary**, never a local class map:
   `accentForDisposition` in `projects/lib/accent.ts` feeds hue NAMES to `statusAccent`, and
   `accent.test.ts` asserts it **agrees with the name-keyword route wherever that route has
   an opinion** (rule 5) — `Inbox` gray, `Waiting on` amber, i.e. whatever `/tasks` would
   draw for a stage of the same name.
3. **Two empty states where there was one.** The board said *"Nothing to show. Clear a
   filter, or this project has no statuses yet"* — one sentence naming both causes and
   asking the reader which was theirs — and the list said a flat "No tasks here yet" even
   when a filter was hiding everything. `/tasks` solved this properly (`NoMatchState` with a
   **Clear filters** action + `EmptyState`), so that shape is ported: **"No tasks match your
   filters." / "Clear them to see everything here again." + Clear filters** when
   `isFiltered(filters)` (the SAME predicate the toolbar's Clear button and `filtered` badge
   read — not a second one), and otherwise **"This project has no statuses yet."** on the
   status axis or **"No tasks here yet."** everywhere else. The box is promoted to
   `src/components/EmptyState.tsx`; the *decision and the copy* are pure in
   `projects/lib/emptyState.ts` and unit-tested, because vitest here is node-env and a
   component test could not run.

**Fences added** (R7): conformance rule 6 above · `sharedTaskUi.test.ts` gains
`components/EmptyState.tsx` as a seam entry + `/projects` as a consumer ·
`projects/lib/emptyState.test.ts` (9 cases, including *the filtered copy never mentions
statuses and the empty copy never mentions filters* — the actual defect — and that **every
icon it names is mapped in every pack**, which caught a `FilterX` that is mapped in none and
would have rendered one Lucide outline in a screen of Material Symbols) ·
`accent.test.ts` gains the disposition agreement + four-distinct-hues cases. `filters` and
`onClearFilters` are **required** props on `TaskBoard`/`TaskList`, so `tsc` is the fence
against an unwired call site. All four fences were mutation-checked (each was made to fail
by reintroducing the defect, then restored).

**Owed, and not claimed:** ⚠️ **no browser check was possible** — Playwright cannot install
here — so the Fluent → Material → Graphite sweep on `/projects` *and* on `/tasks` beside it
is owed at review; nothing in this tree tests layout. Also owed: retiring `/tasks`'
`ItemList.tsx` `NoMatchState`/`EmptyState` onto the shared box (a `/tasks` file another
slice held open — the `sharedTaskUi` consumer row for `tasks` is deliberately absent until
then), `page.tsx`'s `renderState()` seam (WS-27ag left it marked for exactly this
component), and `projects/lib/mywork.isOverdue`, which this change left caller-less and is a
second answer to a question `lib/taskCard.isOverdue` already answers better (it also checks
`completed_at`).

### 11.22 S5 — the task panel adopts the Tasks detail's composition (built 2026-08-10)

**Owner-reported from screenshots of the deployed app**, comparing `/projects`' `TaskPanel`
with `/tasks`' `ItemDetail` side by side: *"Task cards seem to be very different."* Like
§11.21 this is a **reversed-direction** finding — the standing ruling is "Projects is
canonical, Tasks conforms", and on this one surface Tasks was the designed one. Frontend
only: no migration, no API change, no new dependency. Branch `ws-s5-projects-task-panel` — **MERGED to `main` and DEPLOYED** (#420, deploy verified
by log evidence: SHA on the box + `Migrations complete`).**

**The controls, before → after.** Measured on `main` `54e4b880`:

| Control | Before | After |
|---|---|---|
| Status | bare `<select>` with a copied class string | `<Select>` (new primitive) inside the Status cell |
| Attachments | `<input type="file">`, i.e. *"Choose Files / No file chosen"* | hidden input raised by `<Button icon="Upload">`, with the in-flight filenames listed |
| Close | raw `<button>` + `<Icon name="X">` | `<Button variant="ghost" size="icon-sm" icon="X">` |
| Comment | raw `<textarea>` + raw `<button className="bg-primary …">` | `<Textarea>` + `<Button>` (this is the −1 on `SOLID_BUTTON_DEBT`) |
| Assignee / subtask entry | two raw `<input>`s | `<Input>`, the subtask one with a leading `Plus` |
| Assignee chips, "auto" marker | hand-rolled `<span>`s | `<Badge>` (`warning` tone for an address that is neither an email nor `agent:<name>`) |
| Mention chips | raw rounded `<button>`s | `<Button variant="secondary" size="sm">` |

Imports from `@/components/ui/` went from **one** to **four**, plus `StatusChip`.

**`Select` is a seam, not a one-off.** `src/components/ui/Input.tsx` had `Input` and
`Textarea` and no single-choice field at all, so **38 files** hand-rolled one — each with
its own `const SELECT = "cc-control rounded-lg border border-border …"` — of which **37
remain** after this change (nine in `app/projects/`, five in `app/tasks/`), all baselined.
The primitive uses `appearance-none` plus
`<Icon name="ChevronDown">` so the disclosure glyph follows the active **pack**; the native
triangle is drawn by the OS and follows neither the theme nor the pack. One honest limit,
shared with every `<select>` on the web: the popup list is the browser's, so the option rows
do not take our tokens. `Textarea` also gains a declared `ref` (React 19 passes it as an
ordinary prop; the comment box needs it to restore the caret after an @mention).

**The composition**, re-derived by reading `ItemDetail.tsx` rather than described from
memory: header (`bg-card`) carrying ref + copy-link + watch + close, the title, then the
status **chip row** → one scroll region holding `DETAILS` (bordered `FieldCell`s: Status,
Assignees) · `DESCRIPTION` · `PROPERTIES` (tags, repeats) · the custom-fields block ·
`LINKS & SUBTASKS` · `FILES` · `ACTIVITY` → a pinned comment composer. The panel root moved
to `bg-background` so the `bg-card` cells read as cards, which is why `ItemDetail` is built
that way. It used to be **two** scroll regions — a fixed field block that could eat the
whole panel on a short window, with the timeline scrolling under it.

**What did NOT come across, deliberately:** `MetaEdit`'s click-to-edit flip (Projects'
controls are live; hiding them behind a click is an interaction change, not a composition
one) and every Tasks-only concept — context, energy, the founder priority matrix — which
live on `pm_task_personal` and are not this surface's data. Everything Projects has and
Tasks does not stays: task ref and deep link, tags, relations/links, watchers, recurrence,
custom fields, attachments, the activity timeline and comments.

**Width.** The details grid is `grid-cols-1` with **no** responsive variant. The cap is
lifted by the PAGE (`[&>aside]:max-w-none` on the phone branch), so a `sm:` variant would
key off the viewport and split the 448px docked column on a 4K monitor — the exact collision
`/tasks`' detail hit when it was docked at 380px. The title wraps rather than truncating.

**Fence (R7): `conformance.test.ts` rule 7**, two halves.
*Selects* — `<select>` per file, ratcheted like rules 1/3/5/6, with 37 files baselined
(TaskPanel deliberately absent as the worked example) and `components/ui/Input.tsx`
allowlisted with a staleness check. *File pickers* — **absolute, no budget**: every other
picker in the tree (chat upload, résumé parser, signature image, meeting audio, email
composer) was already hidden behind a real control, so this one is a rule with no
exceptions. `SOLID_BUTTON_DEBT` 30 → 29 in the same change, per the file's own protocol.

Both halves were **mutation-checked** (raw `<select>` restored → red; `className="hidden"`
deleted → red; file restored byte-identically each time), and the second mutation is why the
fence is right: the first draft tested `/\bhidden\b/` over the whole tag, which
**`aria-hidden` satisfied**, so deleting the hiding class left the gate green. Two scanner
traps are written into the test: the shared `strip()` treats `accept="image/*"` as an opening
`/*` and swallowed the rest of `SignatureEditor.tsx` (so this rule strips comments only at a
token boundary), and reading raw source made the rule fail on the *comments explaining it*.

**Owed, and not claimed:** ⚠️ **no browser check was possible** — Playwright cannot install
here — so the phone viewport and the Fluent → Material → Graphite sweep on `/projects` *and*
on `/tasks` beside it are owed at review. What was done instead: `tsc --noEmit` clean, 1634
vitest cases green, `npx vitest run src/lib/theme/` green (361), a production `next build`
that prerendered `/projects`, and every icon name checked against
`lib/theme/icon-data/registry.json` for all packs. Also owed, and deliberately not done
here: promoting `SectionLabel` (and `ItemDetail`'s copy) into `src/components/` with a
`sharedTaskUi` SEAM row — that edit touches `app/tasks/**`, which another slice holds open —
and the same for `TagPicker`/`RepeatEditor`/`CustomFieldValues`' own small labels, which
still use the old lowercase style as sub-labels inside `PROPERTIES`.

⚠️ **This section's own verification block (§10) is stale**: it names
`tests/unit/test_projects_sync.py` and `tests/unit/test_projects_personal_mirror.py`, and
neither exists — the second is `test_projects_personal.py`. Run with those two corrected,
379 pass.

### 11.23 S6 — the board/list card draws the facts its own row already carries (built 2026-08-10)

**Owner-reported**, with a screenshot of a `/tasks` card beside a `/projects` one: *"we should
have the relevant pills to show up in the cards of the projects app."* Frontend only: no
migration, no API change, no new dependency, and **no change to the shown-fields vocabulary on
either side of the wire**. Branch `ws-s6-projects-card-pills` — **MERGED to `main` and DEPLOYED** (#421 / `1aec373d`,
deploy verified by log evidence).

**The job was not to copy the Tasks pills.** Half of what that card carries is GTD-only —
`@context`, energy, deep-work, the founder priority matrix, the ClickUp source badge — and
none of it exists on a `pm_tasks` row. What was measured instead is the gap between what the
Projects card DRAWS and what its own `TaskRow` already HOLDS.

**Before → after**, measured on `main` `0afa05db`:

| Fact | On `TaskRow`? | Before | After |
|---|---|---|---|
| `importance` | yes | **drawn nowhere on a card** (Table column only) | a priority chip, gated on `importance` |
| `tags` | yes | one chip reading `🏷 3` | up to **3 named pills** in the registry's colour, then `🏷 +N` |
| `estimate_mins` | yes | **dropped** — `taskFacts` never mapped it, and the file's own comment claimed the column did not exist | mapped; chip gated on `estimate` (off by default, unchanged) |
| `due_at` / overdue | yes | already a chip | unchanged |
| `subtasks`, `blocked_by_count` | yes | already chips | unchanged |
| `assignees` | yes | already the shared `AvatarStack` | unchanged |
| status | yes | already `StatusChip`, off-axis only | unchanged (the rule was already right) |

**`shown_fields` gates all of it, and no default moved.** `importance` and `tags` have been in
`DEFAULT_SHOWN` since WS-27x — the field picker has been promising Priority and Tags on every
view while only the spreadsheet honoured them. So the two headline chips required **zero**
change to `DEFAULT_SHOWN`, `FIELD_KEYS` or the gateway's `filters.SHOWN_FIELDS` mirror: this
slice makes the card obey a contract that already existed. `estimate` and `start_date` stay
**off** by default, as WS-27x left them.

**One vocabulary, extended rather than forked** (`AGENTS.md` rules 4 and 7):

* `MetaTone` gains `warning`. A four-level priority scale needs a step between "fine" and "on
  fire"; only `Urgent` takes `danger`. The tone stays a NAME — `TaskMeta` remains the only
  file that turns one into a class.
* `MetaChip` gains an optional `hue?: AccentHue`, meaning *draw me as a filled pill in this
  hue*. A chip with a hue is an **identity** (which tag); a chip with only a tone is a
  **measurement** (how late, how blocked). `accentForHue(hue).chip` is byte-identical to
  `app/projects/lib/tags.chipClass(color)`, so a tag is one colour on the card, in the picker,
  in the manager and in the filter bar. No second palette, no bespoke pill component.
* `TaskFacts.tagCount: number` **became** `tags: TagFact[]`. The count was the shape of a fact
  with the fact removed. `/tasks` never passed `tagCount`, so nothing there changed.
* Chip keys may now be namespaced `<kind>:<discriminator>` (`tags:ops`). `chipKind()` is the
  one reader, and the shown-fields gate goes through it — a whole-key lookup would have
  silenced every tag chip while looking like it worked.

**What was deliberately left off, because a card that shows everything shows nothing.**
`start_date` (a floating `DATE`; a useful "not started yet" chip needs a day comparison that
belongs to the app adapter, not to a shared module whose `relativeTime` would move it a day
west of Greenwich — and it is off by default anyway, so almost nobody would see it). Blocking
(this task blocks others) — `attach_relation_counts` returns `blocked_by_count` only, so it is
a gateway ticket, not a card one. Watchers and recurrence — not on `TaskRow` at all. Triage —
already carried by the status chip. Custom fields — unbounded density on a 288px card; the
table is where a view's selected fields belong. `source` / `clickup_id` — on `TaskModel` but
not on the browser's `TaskRow`, and a provenance badge is a different ticket from a work-fact.

**Fences added (R7).** `taskCard.test.ts`: `chipKind` splits on the FIRST colon, tags are named
not counted, the cap is `MAX_TAG_CHIPS` and the overflow names what it swallowed, the hue comes
from the stored colour and falls back to gray, and every chip carries an icon **or** a hue.
`card.test.ts`: unset priority draws nothing while `0` (Low) does, the labels are
`table.importanceLabel`'s word for word, only the top of the scale escalates, the four glyphs
are distinct and collide with neither `AlertTriangle` nor `Ban`, the registry colour survives
case-folding, and **every chip kind `cardChips` can emit — including the spliced priority
chip, which `taskMeta` never emits — maps onto a key `FIELD_KEYS` actually offers.**
`sharedTaskUi.test.ts`: a new SEAM row pins the tone→class table to `TaskMeta.tsx`, and four
new "both apps reach it" rows cover `lib/taskCard` and `components/TaskMeta`.

**Visual check — done, not owed.** This is the first slice in the wave with a browser actually
driven. `next build` → `next start -p 3457` → Playwright against `/opt/pw-browsers/chromium`,
with the fixture at the **network boundary** (`page.route` over `/api/projects/*` and
`/api/auth/me`) so the real `TaskBoard`/`TaskList`/`TaskMeta` render real `TaskRow`s and no
product code was touched to make it happen. Board and list captured, then the DESIGN_SYSTEM §8
sweep: RapidTool · Fluent · Material · Graphite × light and dark, plus `/tasks` beside it.
Every pill repaints with the theme; the tag pills match the filter bar's chips for the same
tag in every theme. **One honest note:** Material dark's `--warning` is a pale peach
(`hsl(35 90% 78%)`), so the `High` chip reads faint against `Normal` there — the chevron-up vs
dash glyph is what carries the distinction, which is why the four levels have four glyphs.
That is the theme's token doing what it says, not a hardcoded colour.

### 11.24 WS-27ac — the calendar's week layout, per-day quick-add, honest overflow (built 2026-08-10)

**Plane research item P-19** (§11.19), promoted by §9.2. Frontend plus one test file on the
gateway side: **no migration, no API change, no new endpoint, no new query parameter.** Branch
`ws-27ac-calendar-week`, merged onto the working branch (PR #422) — **not on `main`, not deployed.**

**The whole ticket is "extend the arithmetic", and the defect it exists to prevent is a second
copy of it.** `lib/calendar.ts` already held every day decision as a pure function; a week
layout is a natural place to write `const monday = d.getDate() - d.getDay() + 1` in the
component and move on. That expression agrees with `monthGrid`'s padding on six days out of
seven and moves **Sunday** a week forward, which is a bug that survives every demo that does
not happen on a Sunday. So the two shapes now share both halves of the calculation:

* `mondayOffset(date)` — the `(getDay() + 6) % 7` rotation that makes Monday the week start,
  written once and read by the month grid's padding and the week grid's walk-back alike.
* `runOfDays(startKey, count)` — the one place a grid's days are enumerated, in `YYYY-MM-DD`
  keys, never through `new Date(iso)`.

`MonthGrid` became **`CalendarGrid`** with a `layout` discriminator. Everything downstream —
`calendarWindow`, `taskDays`, `placeTasks`, `rescheduleTo` — reads `grid.days` and **never
learned there was a second layout**, which is why a week asks the same endpoint for ten days
instead of forty-four and gets the same filters, the same triage default and the same
placement. `monthLabel`/`isOutsideMonth`/`shiftMonth` became `gridLabel`/`isPadding`/
`shiftGrid`: each was a month-only name for a question both layouts ask, and keeping the old
name beside a new one is how the two answers drift.

**A week grid has no padding, and that is a decision, not an omission.** `isPadding` returns
false for every day of a week: all seven are the subject. Reusing the month's rule (day's
month ≠ `grid.month`) would grey out part of five weeks a year for a reason no viewer could
name. `grid.month` on a straddling week is its MONDAY's month, and `isPadding` is its only
reader.

**Overflow is exact, and one-over is not folded.** `dayFill(count, limit, expanded)` returns
`{shown, hidden}` with `hidden = count - shown` and nothing else. The failure it replaces is
the cell that renders its first three and stops: **a day with eleven tasks then looks exactly
like a day with three**, which is the same dishonesty `truncated` and `undated` were added to
prevent one level up. Two refinements are in the arithmetic rather than the component so they
cannot drift from the limit they apply: folding fires at `limit + 2`, because a `+1 more` row
occupies the row it would have saved; and an expanded cell reports `hidden: 0`, because a
`+8 more` left under an expanded day is a count of tasks the viewer is already looking at.
Limits are `DAY_LIMITS` — month 3, week 8 — beside the function, not in the JSX.

**A refused drop says why.** `dayDropRefusal(task)` is the calendar's counterpart to
`board.dropRefusal`: that one asks about the AXIS ("this column is computed"), this one about
the TASK. Two questions, two functions, neither surface knowing the other's. Both cases it
names already ended in `rescheduleTo` returning `null`, i.e. in *nothing happening*, which
reads as a broken drag rather than as a refusal. A no-op drop (dropped back on its own day) is
still silent — nothing was denied, so nothing is announced. ⚠️ **Honest limit:** of the two
refusals, only "that task is not on this calendar" is reachable today (a stale or foreign
drag payload), and it surfaces as the page's error banner. The undated one draws the hover
overlay and cannot fire from the calendar's own cards, because an undated task is not drawn —
it is the `undated` count. It is fenced by unit test and is the guard for the moment the
unscheduled tally becomes a draggable tray; the alternative was to leave a gesture that
sometimes silently does nothing.

**The per-day quick-add and the drag were already right and are unchanged** —
`components/QuickAdd.tsx` (the shared control) with `quickAddPrefill("day", key)`, and
`PATCH /projects/tasks/{id}` with WS-27y's post-drop flash. Both were re-verified in a browser
rather than assumed, because "already there" is exactly the claim a week layout can break
without anyone noticing.

**Fences added (R7).** `lib/calendar.test.ts` — 60 tests, of which the load-bearing ones are:
*every day of August* asserts `weekGrid(d).days` equals the `monthGrid` row containing `d` (the
one-implementation claim, behavioural), Sunday pinned by name (the one day the naive form
differs), `shown + hidden === count` swept over both limits, `dayFill` agreeing with
`rescheduleTo` about what cannot move, and a **structural** read of `CalendarView.tsx`
asserting it contains no `new Date(`, `.getDay()`, `.setDate(` or `86400` — because the
behavioural version of "there is no second week math" cannot see a duplicate that happens to
be correct today. Mutation-measured: rewriting `weekGrid` in the naive form turns **2** tests
red; `hidden: limit` instead of `count - limit` turns **3** red.
`tests/unit/test_projects_calendar.py` — the §11.16 parameter-coverage rule extended to the
**window shapes the browser actually sends** (`CLIENT_WINDOWS`: a month grid's forty-four days
and a week grid's ten, each with its day of slack). Both are legal windows; both keep the
board's filters and the same-filters `undated` count; and both hold the **`triage` exclusion**
(WS-27u) with `include_triage` still the only way to ask. Plus a structural test that the week
layout did not grow its own endpoint. Mutation-measured: deleting the triage predicate from
`get_calendar` turns **both** parametrisations red.

**Why a window parametrisation rather than another route assertion.** `include_triage` is
declared once and `test_no_surface_can_silently_drop_include_triage` already pins the
declaration. What that test cannot see is a new surface that keeps the parameter and stops
sending it — or, worse, one that reaches for its own read and inherits a filter set nobody
compared. Parametrising over the two windows is the assertion that survives either.

**Visual check — done, not owed.** `next build` → `next start -p 3602` → Playwright against
`/opt/pw-browsers/chromium`, fixtures at the **network boundary** only, the browser pinned to
`Asia/Kolkata` (UTC+5:30 — the timezone the window's slack exists for) and to a fixed clock.
Measured, not eyeballed: month draws **42 cells in 6 rows** at 96px; the 12th holds 8 tasks,
shows **3** and says **`+5 more`**, expands to 8 with **Show less**, and folds back. Week draws
**7 cells in 1 row** at 554px, labelled **`10 – 16 August 2026`**, and shows all 8 on the 12th
(limit 8). The next/previous arrows read `Next week`/`Previous week` in week layout and
`Next month`/`Previous month` in month, and stepping moves **`17 – 23 August 2026`**, i.e.
seven days. A four-day bar (start 10th, due 13th) occupies four cells in **both** layouts; a
`start_date`-only task sits on the day written. Quick-add on the 14th posted
`due_at: 2026-08-14T06:30:00.000Z` — **noon local in IST**, the day clicked. Dragging
"Calibrate the 0.8mm…" from the 10th to the 13th posted a single
`PATCH {due_at: "2026-08-13T12:00:00.000Z"}` — the day moved, **17:30 IST preserved** — and the
landing flash fired. A drop carrying a payload the window does not contain raised
**"That task is not on this calendar."** DESIGN_SYSTEM §8 sweep run: RapidTool · Fluent ·
Material · Graphite × dark and light, all four `--primary`/`--radius` pairs distinct in the
DOM. The layout toggle and the `+N more` are `Button` primitives, so Graphite uppercases them
(`MONTH` · `WEEK` · `+5 MORE`) and Material makes them pills without either being asked to.

**Recorded, not done.** The calendar asymmetry stays out of scope and stays recorded (WS-27ad
done-when 5): `/tasks` keeps its ten-file calendar module, `/projects` has one view. Under the
owner's 2026-08-10 ruling that Projects is canonical and `/tasks` will derive from it (D-PM-6),
`src/app/tasks/**` was not touched. Also left: the `undated` tally is still text, not a
draggable tray — the affordance `dayDropRefusal`'s second case is waiting for; and the week
layout has no time axis (an all-day grid, not `/tasks`' `TimeGrid`), which is a different
ticket and a different data shape.

### 11.25 WS-27ab — view ergonomics: peek escalation, dirty views, one palette registry (built 2026-08-10)

Plane research items **P-14/15/16** (§11.19), plus a sixth done-when added by the owner from
the S6 review: the `shown_fields` gap on the list. Frontend only: **no migration, no API
change, no new dependency**, and no change to the shown-fields vocabulary on either side of
the wire. The view update rides the existing `PATCH /projects/views/{id}`, which has accepted
`config` (through `normalise_view_config`) since WS-27k. Branch `ws-27ab-view-ergonomics`, merged onto the working
branch — **in PR #422; not on `main`, not deployed.**

**1 · Peek → side → full.** `lib/panelMode.ts` is the vocabulary: three stops, narrowest
first, a `max-w-*` per stop (`xs` · `md` · `3xl`), `widerPanel`/`narrowerPanel` that **stop at
the ends rather than wrapping** (a cycling control makes "wider" mean "suddenly tiny" on the
third press), and a `localStorage` read that degrades a corrupt value to the default rather
than to a panel with no width class. Persistence is `localStorage`, the house idiom for a
reading preference (`ViewModeProvider`, `Sidebar`'s folds) — a per-user server preference
would be a table and an endpoint for a value with no meaning on another device. The panel is
**one component at all three stops**; only its width class and, at `full`, where `page.tsx`
mounts it (over the board, scrimmed, at `/tasks`' `max-w-3xl` reading width) change. Read in
an **effect**, not a lazy initialiser: `localStorage` does not exist during SSR and the two
renders would disagree.

⚠️ **The ticket said "Esc returns focus to whatever opened the panel". Built as written, it
did nothing** — and the browser is what said so. Opening a task leaves focus on the card (the
board canvas is `tabIndex={0}` and the card is its focusable descendant), so the panel's own
`onKeyDown` **never sees Escape at all**: measured, Esc did nothing unless you had first
clicked *into* the panel. The fix is two handlers with one rule between them — the panel
keeps its own (first Escape leaves a comment box holding text, second closes), and the page's
window listener closes the panel when focus is outside it. The panel's handler calls
`stopPropagation`, so exactly one of the two ever fires. Focus return is an unmount cleanup
over the element captured at mount, guarded by `document.contains` (the board reloads under an
open panel, and focusing a detached node silently moves focus to `<body>`).

**2 · The dirty-view row.** The measured defect was not a missing affordance but a **dropped
association**: `changeFilters`, `onGroupBy`, `onSubGroupBy` and `changeShownFields` each ran
`setActiveViewId(null)`, so touching one control severed the board from the view on the first
keystroke and the only way back was to re-apply and lose the edit. Those four lines are gone.
The chip stays lit with an edited dot, and `FilterBar` grows a row offering **Update view ·
Save as new · Reset** — three answers and no fourth. Divergence is **one exported pure
function**, `grouping.viewDivergence`, beside the round trip it reads: both sides go through
`toConfig`, and the saved side through `fromConfig` first, so a config stored by an older
client or hand-edited into a shape `fromConfig` normalises compares **by meaning, not by
bytes**. A byte comparison lights the marker on a board nobody touched, which is how such a
marker comes to be ignored. Sets are compared as sets (tag CSV, collapsed lanes,
`shown_fields`), an assignee's case is noise because the server treats it so, and the four
parts are named back (`filters`, `grouping`, `lanes`, `shown fields`) so the row says *what*
moved. `Reset` re-applies through the same `onApplyView` the chip uses — one path back, so
"reset" and "click the chip again" cannot come to mean different things. `Update view`
replaces the stored row with the **server's** response: `normalise_view_config` may drop a key,
and keeping the local copy would leave the bar comparing against a config that was never
stored, i.e. a dirty marker that never clears.

**3 · The palette action registry.** The ticket says the palette's commands "become a declared
registry instead of inline branches"; the palette had **no commands at all** — it was a task
finder. So this adds them, in the shape the ticket demanded and never as branches:
`lib/commands.ts` declares `id`, `label`, `section`, `keywords`, `icon`, optional `sequence`,
optional `href`, `when`, `run`. Three consumers, one source — the palette lists them, the page
runs their key sequences, and `ShortcutsSheet` is *printed from* `shortcutSections()`, so `?`
cannot describe behaviour the keyboard does not have.

* **The Go section is derived from `@/lib/nav`**, not written out. `GO_KEYS` assigns eight
  letters and nothing else; the label, the glyph and the route are the `NavPane` the sidebar
  draws, so `g t` says *Tasks* because that is what Tasks is called. A pane removed from the
  nav produces no command, and the test makes that loud rather than quietly shorter help.
* `ViewMode`/`VIEW_MODES` **moved out of `page.tsx`** into the registry's file. Two lists of
  the five canvases is how the toolbar and the palette come to offer different sets.
* `stepSequence` restarts on a dead prefix: `g` · `z` · `g` · `p` reaches Projects. Without
  it the third key is swallowed clearing the prefix and the shortcut fails once per typo.
  A prefix is forgotten after `SEQUENCE_TIMEOUT_MS`, sequences are suppressed while anything
  modal is open, and `isTypingTarget` keeps a bare letter out of the quick-add box.
* Commands lead the palette's list and task hits follow, because rows appearing **below** the
  cursor cannot move what is already under it — the hits arrive after a debounce.

**4 · `shown_fields` gates the list's last two columns** *(the sixth done-when)*. Every other
field on `TaskList` gated; **Status and Assignees rendered unconditionally**, so un-ticking
*Status* silenced its chip on the board and left the column standing on the list — the field
picker was lying on that surface. **Before → after:**

| Case | Before | After |
|---|---|---|
| a view nobody edited (`DEFAULT_SHOWN`) | `#` · Title · Status · Assignees · Details | **identical** — both keys are in `DEFAULT_SHOWN` |
| *Status* un-ticked | column still drawn | column gone; `colSpan` 6 → 5 |
| both un-ticked | both still drawn | both gone; `colSpan` 6 → 4 |
| a saved view whose stored `shown_fields` omits them | columns drawn anyway | columns hidden — the stored choice is finally honoured |

**No default moved**, so turning this on hides nothing from anyone who never opened the field
picker. The one behaviour change that reaches existing data is the last row, and it is the
point of the ticket. The column set is `table.listColumns` rather than a hand-counted number,
because the header, every group heading's `colSpan` and the quick-add row's `colSpan` have to
be the same figure — two hand-counts is how a heading comes to span four of five columns.

**Fences added (R7).** `panelMode.test.ts` (17): escalation stops at the ends, a corrupt or
absent stored value reads as the default, a `Storage` that throws does not take the panel with
it, Escape blurs a field holding text and closes an empty one, every stop has a label, a glyph
and a `max-w` class. `commands.test.ts` (38): **every action carries a label, a section and a
glyph**; **every go-sequence resolves to a route in `nav.PANES`**; **no two actions share a key
sequence** *and* none is a prefix of another (which would make the shorter one unreachable);
every glyph is mapped in `icon-data/registry.json` for every pack; `when` hides what would
no-op; the cursor never lands on a heading; and the sheet prints every command exactly once
with the command's own keys. `grouping.test.ts` (+11): the round trip is clean, each part is
named alone, four normalising configs are *not* dirty, set order is noise, `[]` shown-fields is
a real choice in both directions. `table.test.ts` (+6): the default list is byte-identical to
before the gate, each column drops independently, and the three fixed columns survive
everything.

**Conformance.** Two baselines came **down**, as the ratchet requires:
`SearchPalette.tsx` left `ACTIVE_DEBT` entirely (its selected row is now
`bg-primary/10 text-primary`) and `FilterBar.tsx` went **2 → 1** (the applied-view chip; only
its pressed tag chip remains). Nothing was added: the new `ShortcutsSheet.tsx` and
`commands.ts`/`panelMode.ts` carry no budget and are clean. One honest note — *Save as new*
wears `Plus` rather than the `Bookmark` on the Save-view button beside it, because `Bookmark`
has **no entry in `icon-data/registry.json`** and draws the Lucide glyph under Fluent and
Material. Fixing the existing one is a registry ticket; adding a second was not on.

**Verified in a browser, not asserted.** `next build` → `next start -p 3601` → Playwright
against `/opt/pw-browsers/chromium`, fixtures at the **network boundary** only. Measured:
the panel at 448 → 320 (peek) → 448 (side) → 766 in a `position: fixed` overlay (full), the
choice surviving a reload, Escape from the *board* closing it with focus landing back on the
card that opened it (identity-checked, not "not `<body>`") and ArrowDown then moving the board
cursor; the dirty row absent on apply, present after an edit, worded *"Overdue firmware has
unsaved changes to its filters."*, cleared by Reset with the filter restored, and cleared by
Update view with the `PATCH` body captured; `?` drawing 24 rows in five sections with real
keys; `g t` navigating to `/tasks`; the list's columns and `colSpan`s tracking the field
picker exactly; and the phone branch carrying **no** width switch with the panel at the full
390px even with `"full"` persisted. Then the DESIGN_SYSTEM §8 sweep — RapidTool · Fluent ·
Material · Graphite × light and dark, on the panel, the dirty row, the palette and the sheet,
with `/tasks` beside it: `data-theme` applied in all eight, *Update view* rendering as a
Material pill (`9999px`), a Graphite 2px uppercase button and a Fluent 4px one, and **zero page
errors** in every combination.
### 11.26 WS-27ae (export third) — the filtered-list CSV export (built 2026-08-10)

> **Numbering note (resolved at integration).** This was written as §11.26 on a branch cut
> from `main` at `1aec373d`, where §11.24 and §11.25 did not yet exist — three slices were in
> flight at once, each numbering against the same base. They were merged in ticket order
> (§11.24 WS-27ac, §11.25 WS-27ab, §11.26 here), so the run is contiguous and **the gap this
> note originally warned about does not exist.** Kept as a record of why parallel slices are
> assigned numbers up front: the two before these both reached for §11.24 and had to be
> renumbered by hand.

**Scope: the export third of WS-27ae only.** Delta-sync (P-27), the satellite `updated_at`
bumps, `is_epic`, per-user view state and the session `user_id` denorm (P-28 rest) are a
sibling agent's, along with migration 168. **Nothing here needs a migration** (R1: no number
taken).

#### ⚠️ The ticket named a pattern that does not exist

§9.2 says *"filtered-list CSV export on the export-job pattern"*. **There is no export-job
pattern in this repo.** Measured, not assumed: `export_job` / `ExportJob` / `export-job`
returns nothing under `apps/`, `packages/`, `tests/` or `workbench/` (the only hits are
vendored `litellm` and `apscheduler` code in `.venv`), and `routes/projects/` had no export
endpoint of any kind. So the phrase named an aspiration, not a seam to extend.

**What was built instead: a synchronous, bounded CSV response over the SAME filtered query
the list endpoint already runs.** One request, one `text/csv` body, no job row, no polling,
no worker, no artefact to expire. Standing up a job queue to satisfy a phrase would have
been infrastructure nobody asked for, carrying a genuinely new failure surface — orphaned
jobs, expiring downloads, and a second place the tenant boundary has to hold — in exchange
for nothing this app can measure. If exports ever outgrow one request, the thing to build is
the queue, on evidence, as its own ticket.

#### The endpoint

`GET /projects/export/tasks.csv` — `apps/services/gateway/gateway/routes/projects/export.py`,
mounted from `__init__.py` like every other feature module.

⚠️ **The path is not `/projects/tasks/export.csv`.** That spelling would be shadowed by
`/projects/tasks/{task_id}` and which handler answered would depend on router registration
order — the one trap `__init__.py` promises this package does not have. A literal segment of
its own keeps that promise true; `test_projects_export` asserts the shadowing spelling is
absent as well as the real one present.

**Three rules, each because the obvious implementation is wrong:**

1. **The filters are the caller's, through the ONE pure builder.** The handler's query
   parameters are `list_tasks`' verbatim minus pagination, and every one of them goes into
   `filters.build_task_filters` — the same function the list, the board and the calendar use.
   A second filter parser would drift and then the file and the screen would disagree about
   what *"my open bugs in Ops"* means. Fenced two ways: a structural assertion that
   `build_task_filters` and `task_visibility_clause` are both called, and — borrowed from the
   calendar's version of the same problem — a test that reads BOTH route signatures off the
   router and fails on a list parameter the export does not declare, because **FastAPI drops
   an undeclared query parameter silently** and the file would look fine.
2. **The columns are the view's `shown_fields`**, plus `#` and `Title` unconditionally (a row
   you cannot identify is not a row). ⚠️ **Column ORDER is the vocabulary's, not the stored
   list's** — the ticket said "in the view's own order", but `shownFields.ts` is explicit that
   the stored list is a **SET** and `table.tableColumns` draws it in declaration order, so
   honouring the stored order would have made the file's columns differ from the screen's.
   Core keys in `filters.SHOWN_FIELDS` order, then custom fields in registry order; a
   `custom.<key>` whose definition was deleted after the save produces no column, exactly as
   the table renders nothing for it. The set is normalised by `normalise_view_config`, so the
   endpoint cannot accept a key a saved view could not store. **An absent or empty
   `shown_fields` yields only the unconditional pair** — not the client's `DEFAULT_SHOWN`,
   which would be a second copy of a preference that drifts.
3. **It is never truncated: it is complete, or it is refused.** `MAX_EXPORT_ROWS = 5000`,
   checked with a `count(*)` over the same WHERE **before a single row is rendered**; past it
   the answer is a **422 naming the real count and the cap** and no file at all. This is the
   opposite branch from the calendar (WS-27q), which truncates and says so — and the
   difference is the medium, not the taste: a short month is *drawn* with a "truncated"
   banner beside it, while a downloaded spreadsheet has no banner and nobody scrolls to the
   bottom of one to check whether it ended early. A partial CSV is byte-indistinguishable
   from a complete one. `test_there_is_no_partial_file_path_at_all` pins that there is no
   `OFFSET` and no `MAX_EXPORT_ROWS + 1` probe — the second assertion exists because copying
   the calendar's truncate-and-say-so shape is the likeliest future regression.

**Tenancy (R5/R11).** `_tenant_session()`, the ambient form every request handler in this
package uses; **no `get_db()`, no engine, no second idiom**, so `routes/projects` stays at
**zero** unbound sites with **no** H2 exemption and `test_converted_packages_stay_converted`
needed no edit. The tenant is the one the request already bound from the authenticated
session's `app_user` row; the identity is the `UserContext`. Neither is ever read from a
header, query parameter or body — the endpoint has no parameter through which either could
be supplied. The grant closure is the same `task_visibility_clause` every other read binds,
so an export with no `project_id` (a real request — "everything I can see") carries only the
caller's own grants, and an unreadable `project_id` is **404, not an empty file**.

#### CSV correctness, and the injection decision

Quoting is `csv.writer` with `QUOTE_MINIMAL` and RFC-4180 `\r\n` — comma, embedded quote and
embedded newline are the module's problem, deliberately, because every hand-rolled CSV
writer gets the newline case wrong.

**Formula injection is NEUTRALISED, not documented away.** A cell whose first character is
`=`, `+`, `-`, `@`, TAB or CR is prefixed with a single apostrophe: a task titled `=SUM(A1)`
exports as `'=SUM(A1)`.

- *Why guard rather than warn:* these strings are counterparty-authored — task titles, tags
  and custom-field text, and an imported ClickUp workspace is thousands of them nobody here
  typed. The payload is `=cmd|'/c calc'!A0`, which executes with the credentials of whoever
  double-clicks the file.
- *Why a prefix rather than quoting:* ⚠️ **quoting is not a mitigation.** A spreadsheet
  evaluates the cell after the CSV quoting is stripped, so `QUOTE_ALL` changes nothing. That
  is pinned by its own test, because "we quote everything" is the plausible wrong fix.
- *Why the apostrophe is acceptable:* it is visible, reversible and one character, so
  somebody who genuinely wanted a formula can see what happened. A formula that runs has no
  such tell.
- *The one exemption:* a cell that is **exactly** a number keeps its leading `-`. Without it
  every negative number would arrive as text and the sums people export a CSV to compute
  would silently stop working. `-5` is a number to Excel; `-5+cmd|…` is not a number and is
  still guarded.

A **UTF-8 BOM** leads the body, because Excel otherwise reads the file as the system code
page and every non-ASCII title arrives mojibake.

⚠️ **Two columns deliberately carry the STORED value rather than the label the table draws**:
`importance` and `estimate`. Their formatting vocabularies (`table.IMPORTANCE_OPTIONS`,
`durationLabel`) live in the browser, and copying either server-side would be a second
vocabulary that drifts. A spreadsheet wants the number anyway — `2` sorts and sums, `High`
does not.

⚠️ **One column knows MORE than the screen**: `attachments`. The list endpoint does not count
attachments (`lib/card.ts` says so honestly, and the table draws `—`), so the export
aggregates `pm_task_attachments` over the exported ids in one query rather than exporting a
column of nothing.

#### The UI

`Export` in the Projects toolbar (`FilterBar.tsx` — where the filter and the field set are
chosen; anywhere else it would read as exporting the *project* rather than the *view*),
built from the house `Button` with `icon="Download"`, never an `<a download>` dressed as one.

⚠️ **It is FETCHED, not navigated to.** `window.location = …` would download the file and
would turn the 422 refusal into a tab full of JSON — which would make refusing strictly worse
than truncating. Fetching is what lets the gateway's own sentence (the matched count, the
cap, and what to do) land on the board. Proven in a real browser: the refusal renders, no
file is written, and the button recovers.

⚠️ **`saveCsv` takes a `Blob`, and the first version did not — this was a real defect caught
only by running it.** `Response.text()` decodes UTF-8 with `TextDecoder`, which **strips a
leading byte order mark**, so the file the browser saved was measurably different from the
bytes the endpoint produced and the server's Excel fix was silently undone.

The BFF proxy (`src/app/api/projects/[...path]/route.ts`) previously stamped
`Content-Type: application/json` on **every** response. It now forwards the gateway's own
content type and its `Content-Disposition`; the filename is therefore the server's single
choice, read back by `filenameFromDisposition` rather than composed a second time in the
browser. A refusal from the same endpoint is still JSON and still arrives as JSON, because
the proxy reads what upstream sent rather than what the route usually sends.

🔧 **Correction, 2026-08-11 (WS-26i-export repair round 1) — this export has shipped
WITHOUT its BOM since WS-27ae, and the paragraph above was wrong to say the bytes survived
"end to end".** `saveCsv` taking a `Blob` fixed the last hop only. The BFF proxy did
`const text = await res.text()` and rebuilt the response from the decoded string, which is
the *same* decode, one hop earlier: the BOM was gone before `saveCsv` ever saw it. Measured
on node v22 through the real handler — upstream `EF BB BF 4E 61 6D`, relayed
`4E 61 6D 65`. A task titled "Café" therefore reaches Excel on Windows as "CafÃ©", which is
exactly what the gateway's BOM exists to prevent.

Found while building the CRM's copy of this proxy arm (`crm_app.md` §9, WS-26i-export) and
fixed here in the same change, because leaving one of two identical proxies broken while the
new shared fence documents the shape as correct is worse than either. The proxy now reads
`res.arrayBuffer()` and passes the bytes; it also forwards `X-Export-Rows`, which the
gateway has set since WS-27ae and no caller could reach. **Fence:**
`src/lib/export.test.ts` RUNS both proxies over a BOM'd `text/csv` body and compares bytes
(a decoded comparison cannot see a BOM at all) — replacing the previous version, which
asserted `toContain("await res.text()")` and so pinned the defect in place. Measured red
under the revert. **Not deployed** — the fix rides the WS-26i-export branch.

#### A hermetic-fake defect this ticket found (worth more than the feature)

⚠️ `tests/unit/_projects_fakes.py` read the **positive** `?status_category=` clause —
`EXISTS (… s.category = ANY(:categories))` — as if it were the **negative** "hide closed
work" clause, because its branch matched any subquery block naming `pm_task_statuses` and
`category`. Every behavioural test in the tree filtered on `todo`, where the two answers
coincide, so **`status_category=done` returned the OPEN tasks and the mirror agreed with
itself**. It is now keyed on the bound parameter each clause actually carries, and the
negative branch on the closed vocabulary (`'done', 'cancelled'` / `:closed`). Found by the
live Postgres run, which is the entire argument for R8.

#### Verification (R8 included)

`tests/unit/test_projects_export.py` — 41 hermetic tests. `tests/live/live_ws27ae_export.py` — the
same endpoint against a **real Postgres**, all checks green, covering what a text-matching
mirror cannot answer: that the `count(*)` and the row query compose the **identical**
predicate across seven filters (the never-truncated contract IS that equality), that
`ANY(CAST(:ids AS uuid[]))` binds Python `str`s on both roll-ups, that the grant closure
scopes an unscoped export, and that the hostile title survives a real writer and a real
parse. Browser: the export was triggered from the real toolbar with a filter applied on
screen (5 rows → 2), the downloaded bytes are the ones the gateway produced, and the
Fluent → Material → Graphite sweep shows the button painting **identically to its neighbour**
in all four themes × two modes (Material's pill radius, Graphite's uppercase) and visible at
a 390 px viewport.

**Not done, deliberately:** the delta-sync feed, the satellite `updated_at` bumps, and the
P-28 remainder — the other two thirds of WS-27ae, owned by a sibling agent with migration
168. Also not done: a scheduled/emailed export, an `.xlsx` writer, and exporting anything
other than tasks. None is asked for.

### 11.27 WS-27ae — delta-sync and the small columns (built 2026-08-10)

*The delta-sync and small-columns thirds of the WS-27ae basket. The CSV-export third is
a sibling slice and is recorded separately.* Migration **168** — number taken by listing
`infra/postgres/` at file creation (highest on disk and on `origin/main` was
`167_projects_seed_status_colours.sql`) and re-checked immediately before commit. R1.

**The endpoint is `GET /projects/delta/tasks`, not `/projects/tasks/delta`.** The natural
spelling would be matched by `/projects/tasks/{task_id}` and the answer would depend on
import order — the route-shadowing trap `routes/workflows/__init__.py` documents and this
package's `__init__` claims immunity from *because* every path is a literal. The claim is
now also a test (`test_the_path_cannot_be_shadowed_by_the_task_id_route`).

**The deletion answer, because a naive feed cannot express one.** `WHERE updated_at >
:since` can never tell a client a task was DELETED — the row stops appearing, and an
upsert client keeps the ghost for as long as it lives. Plane's feed has that hole. So the
response is `{rows, removed, cursor, has_more, snapshot, reconcile_after}` and `removed[]`
carries two shapes:

1. **hard deletes**, from `pm_task_tombstones` (migration 168), written by an **AFTER
   DELETE trigger on `pm_tasks`** rather than by `delete_task`. ⚠️ That choice is the
   whole value of the table: `pm_projects` CASCADEs to `pm_tasks`, so an application-level
   write would have recorded the one deletion path that has an endpoint and silently
   missed the one that takes hundreds of tasks at once — which is precisely the deletion a
   client is holding the most rows for.
2. **fell out of scope** — a task that changed and no longer satisfies the feed's own
   predicate (archived, moved into `triage`, moved out of the requested subtree). Stream A
   therefore applies **no** project scoping, archive filter or triage exclusion; those are
   applied in a second pass over the ids it returned, and whatever does not survive is a
   removal. Filtering at the first step is exactly the naive feed's blind spot.

⚠️ **One removal shape is NOT expressible, and is documented rather than pretended away:
losing VISIBILITY.** A revoked grant simply makes the row stop matching the visibility
clause; nothing records that it used to match, and manufacturing that would mean storing a
per-member history of everything anybody could ever see. Clients are told to reconcile
with a full pull (`reconcile_after`, on every response). `test_losing_VISIBILITY_is_silence_
and_that_is_documented` pins the SILENCE, so the day somebody closes the gap the docs and
the behaviour move together instead of the docs going quietly stale.

**The boundary: a keyset, plus a horizon.** The cursor is the `(updated_at, id)` of the
last row actually **delivered**, compared with SQL's row comparison — so a page of rows
sharing one instant is delivered exactly once each, which `updated_at > :since` cannot do
(it skips) and `>=` cannot do either (it loops). A bare ISO timestamp is still accepted for
a first call and is treated as inclusive. On top of that the feed refuses to look at
anything newer than `now() - HORIZON_LAG_SECONDS` (5): `now()` is TRANSACTION-start time,
so a writer that began before the read can commit after it with an earlier stamp and land
behind a cursor forever. **The residual is stated, not eliminated:** a write transaction
that stays open longer than the lag while another commits after it can still be missed
until the client's next full reconcile. Every write path in this package is one short
request.

**Satellites that bump the task, and the ones that deliberately do not.** The bump lives at
ONE choke point — `core.record_activity` bumps whenever the entry names a task, because an
activity naming a task *is* the statement that the task changed — plus `core.touch_task`
at the writes that legitimately record no activity: link **target** on create, **both ends**
on unlink, comment edit, comment soft-delete, the old and new **parent** on a re-parent, and
the **promoted subtasks** plus the parent on a delete (their `parent_task_id` SET NULLs, so
nothing in this package writes them). Covered: assignees, links, attachments, comments and
timeline entries, subtask membership, plus tags/custom fields/status which already live on
the task row. **Deliberately not bumped:** `pm_task_watchers` (one person's subscription —
bumping would wake every synced device in the company each time somebody pressed Watch),
`pm_task_personal` (the §6.1 per-user overlay), `pm_notifications` (one person's bell),
`pm_view_task_positions` (per-VIEW order, D-PM-5), `pm_view_user_state`. The fence (R7) is
`test_every_satellite_writer_bumps_its_task_or_is_named_here`, which scans the package for
satellite writes and requires each writing module to reach the bump, with a named allow-list
for modules that only write satellites of a task they created in the same transaction.

**The small columns, and what reads each.**
* `pm_task_types.is_epic` — read by the new `core.is_epic_type`, which `assert_epic_has_no_
  parent` calls at its three sites. §3.4's rule stops keying off a SEED NAME, so a project
  can call its top level "Initiative" and still get it. Written through `admin.create_type` /
  `patch_type` and stamped by `tree._seed_root`; the migration backfills every type the old
  predicate matched, and un-flagging the *system* Epic is a 409 (the seed-name arm would
  still apply, so a 200 would be a write that reported itself applied while nothing changed).
* `pm_view_user_state` — read by `views.list_views`, which now returns each view with the
  **caller's own** overlay attached, plus `GET/PUT /projects/views/{id}/state`. It fixes
  WS-27y storing `collapsed_lanes` in the SHARED config, i.e. one person's collapse
  collapsing the lane for the whole company. Presentation only: `filters.
  VIEW_USER_STATE_KEYS` excludes filters, because two people must never be looking at a
  saved view that means two different sets of tasks. Its normaliser is **absent-preserving**
  and therefore deliberately not `normalise_view_config` with a different key set — that one
  fills a `group_by` default, which on an overlay would re-group the board for whoever
  collapsed a lane.
* ⚠️ **The session `user_id` denorm was NOT built, because it already exists.** Measured
  before writing: `chat_session.user_id` is `TEXT NOT NULL` and indexed by
  `chat_session_user_idx (user_id, updated_at DESC)` since migration 02, and
  `public."Session"` carries a `user_id` with an FK. P-28 asks for a denormalisation that is
  present in both session stores. `pm_task_statuses.color` was stored from migration 146 and
  rendered nowhere for twenty-one migrations; a column with no reader is not a feature, and
  the check that avoided repeating that is itself a test
  (`test_the_session_user_id_denorm_is_already_there`).

**R5 / tenancy.** Both new tables carry `organization_id NOT NULL REFERENCES organization`
(D-MT-3), `pm_view_user_state` through migration 161's `pm_organization_from_parent` trigger
(its 20th attachment), and both are in the regenerated `infra/postgres/generated/` phase
files — MT-1b's own lesson, since `pm_intake` and `pm_task_watchers` were once absent from
all four. `pm_task_types.is_epic` is a column on a table that already carries the key.
`routes/projects` stays at **zero** unbound sites and holds no H2 exemption; the feed takes
its tenant from `resolve_visibility` like every other read and declares no
identity-shaped query parameter (asserted).

⚠️ **The organization-delete trap, measured rather than reasoned about.** With a plain FK
and no guard, deleting an `organization` cascades to `pm_projects` → `pm_tasks`, fires the
tombstone trigger, and the insert references the organization the same statement has already
removed:

```
ERROR:  insert or update on table "pm_task_tombstones" violates foreign key
        constraint … Key (organization_id)=(860f107d…) is not present in table "organization".
```

i.e. the first version made an organization **undeletable**. Fixed in the trigger, not by
dropping the constraint: it returns early when the tenant is already gone — which is also
the right answer, since a deleted tenant has no clients left to tell. The FK therefore stays
in migration 161's shape and `test_tenancy_boundary.py`'s new-table rule is satisfied on its
own terms rather than by an exemption.

⚠️ **A second hole the LIVE harness found and the hermetic suite could not.** A tombstone
left by a project CASCADE names a project row that no longer exists, so the
project-visibility closure — which resolves grants through `pm_projects` — can never match
it. With the closure alone, the single deletion that strands the most rows on a client was
the one deletion the feed could not report. The query gained a second arm for tombstones
whose project is gone, still bounded by the tenant predicate; what a member can learn from
it is the uuid and instant of a task in a project of their own company that has since been
deleted, with no title and no content. **Measured:** deleting that arm leaves the whole
hermetic suite green and turns `live_ws27ae_delta.py` red, so the comment above the line says the
live script is its only fence.

**Verification.** `tests/unit/test_projects_delta.py` — 41 hermetic cases. `tests/live/
live_ws27ae_delta.py` — **32 checks against Postgres 16**, two organizations whose tasks are
stamped at identical instants: alpha's feed never returns beta's row or removal and vice
versa, paging one row at a time across a shared instant delivers each exactly once, a row
written this second is withheld and the same row delivered once it ages past the lag, a
project CASCADE leaves a tombstone the endpoint never touched, the human task id survives
the row, the per-user overlay round-trips through JSONB with the tenant filled by 161's
trigger, another tenant 404s on the view, and an organization is still deletable.
Mutation-measured: degrading the keyset to a bare timestamp, deleting the horizon, dropping
the tombstone stream, weakening `has_more`, removing `record_activity`'s bump and bumping
only one end on an unlink each turn the hermetic suite red; removing the orphan-tombstone
arm turns the LIVE script red and nothing else.

**Owed, deliberately not built here.** (1) **Tombstone retention** — they are one small row
per deleted task and never swept; a sweep is a scheduled job touching real data, which D6
says belongs to `/workflows`, the shape WS-27z already had to hand over. (2) **No UI
consumes any of this yet**: the per-user overlay is served by `list_views` and the board
still reads `collapsed_lanes` from the shared config, so the behaviour changes only when a
frontend slice adopts it. (3) The feed has **no client** in-tree — it is built for the
agents/mobile consumers P-27 names.

### 11.28 WS-27al — the papercuts that were actually buildable (built 2026-08-11)

Wave 1. Frontend only — no migration, no API change, no new dependency. The ticket named
six items; **three were struck against the tree before a line was written**, and the
striking is the more useful half of this record.

**(1) `ControlLink` — `/projects` gets its first `<a href>` on a task.**
Measured at `ebf68f4e`: **zero** `<a href>` and zero `next/link` anywhere in the app's task
surfaces, so cmd/ctrl-click, shift-click and middle-click did nothing at all — the one
interaction every other application on the machine agrees on.

- `src/lib/controlLink.ts` — `shouldIntercept(event)`, pure. False for `button !== 0`,
  meta/ctrl/shift/alt, and an already-`defaultPrevented` click; true otherwise.
- `src/components/ControlLink.tsx` — a real anchor that calls `preventDefault()` and runs
  `onActivate` **only** when `shouldIntercept` says so.
- Wired into `TaskList.tsx` and `TableView.tsx`, at the **title cell**. ⚠️ The rows are
  `<tr onClick>`, and an `<a>` cannot wrap a `<tr>` — so the row keeps its handler and the
  link lives in the cell. `TaskCardShell` is deliberately untouched: it is a
  `role="button"` div by a decision recorded in its own comment (nested interactive
  elements inside an anchor are invalid HTML), so **cards were out of scope**.
- Two rules written into the component because both are silent when wrong: it is a **plain
  `<a>`, never `next/link`** (the target is the route the reader is already on; a `<Link>`
  would prefetch it once per row for a navigation that only happens in a *new* tab), and it
  **stops propagation unconditionally** — otherwise a plain click opens the panel twice and
  a cmd-click opens a tab *and* the panel, honouring the modifier by half.
- The href is `lib/card.taskDeepLink`, the same `/projects?task=<id>` the bell emits and
  `page.tsx` reads. No second spelling.

**(2) `data-prevent-outside-click`.** `src/lib/outsideClick.ts` — `shouldDismiss(target,
walk)` climbs from the clicked node and returns false on the surface itself, on any
ancestor carrying the attribute, and on a null target; `domClickWalk(surface)` is the one
DOM adapter. `NotificationBell` consumes it. ⚠️ **Stated honestly: the motivating case does
not exist in `/projects` yet** — nothing here portals a picker out of a dropdown, so today
the walker and the old `contains()` behave identically. It is built ahead of the need
because Wave 2 (Modal · Tooltip · Combobox) creates that case immediately and the
alternative is three hand-rolled answers arriving at once. **The walker takes an injected
parent/attribute accessor rather than an `Element` on purpose**: the runner is
`environment: "node"`, so a DOM-bound version would have no fence at all.

**(3) The overdue predicate — narrowed, and the ticket's own claim corrected.**
The ticket says "seven" predicates and that "today counts as due". **Both are wrong.**
Measured: three (`src/lib/taskCard.ts` · `app/projects/lib/mywork.ts` ·
`gateway/routes/projects/filters.py`), plus `app/tasks/lib/waiting.ts`'s
`isWaitingOverdue`, which is deliberately different under a documented contract and was
left alone. The real defect was narrower than "seven copies": **`mywork.ts`'s had
no completion check at all**, disagreeing with both `taskCard.ts` and the SQL filter. Its
body is now gone — it delegates to `@/lib/taskCard.isOverdue`, the `app/tasks/lib/utils.ts`
adapter shape — keeping only its local calling convention.
⚠️ **Corrected 2026-08-11, and the correction matters for how this reads.** An earlier
draft of this paragraph — and of the header bullet, and of the report that went to the
owner — said *"a finished task with a past due date rendered overdue in My Work."* **It did
not.** `mywork.isOverdue` had **no in-app caller at all**: `MyWork.tsx:353` draws the
overdue chip through `cardChips` → the shared `taskCard.ts` predicate, which was always
correct. This was a divergent exported helper waiting for its first caller — a latent trap,
not a visible bug. Worth closing, and closed; but a "measured" claim that nobody checked
against the call graph is exactly the kind of thing this file exists to stop repeating.
The export was kept rather than deleted because deleting it would delete the fence.
🔴 **`<` was NOT changed to `<=` and the SQL was not touched.** `taskCard.test.ts` and
`mywork.test.ts` both pin `<` with explanatory comments ("a task is late once the moment
has passed, not at the moment itself"). Whether today counts as due is a **doc blocker for
the owner**, not an agent's call.

**Struck, each for a measured reason.** (4) **Lazy tooltip mounting** — unbuildable:
**zero `Tooltip` components exist**, the app uses the native `title=` attribute, and there
is no positioning machinery to mount lazily. It is WS-27ak(2), Wave 2. (5) **Selected-first
ordering** — the ticket names no target multi-select; `FilterBar`'s tag row is a
permanently-visible chip strip with no "open" moment, so "sorted on open and frozen while
open" has no referent there. (6) **Selection self-heal** — **already shipped**:
`app/projects/page.tsx` prunes the selection against `onScreen` via `src/lib/selection.ts`'s
`prune`. Verified verbatim; nothing to do.

**Fences (R7), each mutation-measured.**

| Fence | Mutation applied | Result |
|---|---|---|
| `src/lib/controlLink.test.ts` — modifier cases, **middle-click asserted by name** (the upstream reference exempts meta/ctrl only and misses it) | dropped the `button !== 0` arm | 2 red |
| `src/lib/controlLink.test.ts` — "wired, not merely imported" source scan over `TaskList.tsx` + `TableView.tsx` (import · renders · has `href` **and** `onActivate`) | reverted `TaskList`'s title cell to a `<span>` | 2 red, import-half still green (the two halves are distinct) |
| `src/lib/outsideClick.test.ts` — bails on the attribute, **and dismisses the same chain without it** | dropped the `isGuarded` arm | 2 red |
| `src/app/projects/lib/mywork.test.ts` — a completed task with a past due date is not overdue, **and the same row still open is** | restored the pre-fix body | 1 red; the two `<`-pinning assertions stayed green |

⚠️ **"cmd-click opens a second tab" is review-only in this tree, and that is not faked.**
`vitest.config.ts` is `environment: "node"` with `include: ["src/**/*.test.ts"]` — no jsdom,
no `@testing-library`, `.tsx` tests are not collected. Adding a DOM environment to fence one
component is a substrate decision, not a papercut. What the fences prove is that the
decision is right and the wiring exists; that a modified click really opens a tab, and that
the four themes still draw the title identically now it is an anchor (Tailwind preflight
gives `a { color: inherit; text-decoration: inherit }`, so no colour was written), is
`DESIGN_SYSTEM.md` §8 and a human.

**Owed / noticed, deliberately not done.** The anchor makes every task title a tab stop —
correct for a real link and an accessibility gain over a `<div onClick>` row, but it changes
tab order on a long table and deserves a look. `/tasks`' list surfaces have the same missing
`<a href>` and are not in this slice.
### 11.29 WS-27am (narrowed) — the error boundary, and the third empty state (built 2026-08-11)

The ticket in §9.4.2 carries three items. **Two were built, one is struck**, and the strike
is the part worth reading.

**Struck: item 2, the loader/empty/error HOC.** The sentence is *"one HOC **per layout**"*
and it never says which layouts. `/projects` alone has five canvases plus `MyWork`, and the
clause reads tree-wide — so "done" is unknowable and the surface set would have been the
implementer's guess, not the spec's decision. Recorded as a doc blocker rather than closed
by guessing. `page.tsx`'s `renderState()` seam (WS-27ag left it marked) is untouched and
still owed.

**Item 3 — the per-layout error boundary — is the substance of the slice.** Measured before
building: **the tree had no error boundary at all** — zero `componentDidCatch`, zero
`ErrorBoundary`, no Next.js `error.tsx`. One malformed group shape thrown out of one card
took React's whole root down, and the user got a white document: no chrome, no nav, and
nothing saying which of "empty" and "broken" had happened.

- `src/components/LayoutBoundary.tsx` — the class boundary, mounted in
  `app/projects/page.tsx` around the canvas scroll region. Scoped to the **canvases**, which
  are the code that walks server-shaped data, so the tree, toolbar, filter bar and task panel
  stay alive while one canvas is broken: switching view, clearing a filter and picking
  another project are all still available, and all three are plausible ways out.
- **Retry bumps a key; it never clears a flag.** `state.attempt` is the guarded subtree's
  `key` and only ever increments. The arithmetic lives in `src/lib/layoutBoundary.ts` so it
  can be tested at all — vitest here is `environment: "node"` and its `include` covers
  `.test.ts` only, so a `.tsx` test is not even collected.
- `caught()` deliberately returns **only** `error`. It is `getDerivedStateFromError`, whose
  return value React *merges* — an `attempt` in it would reset the key on every crash and
  turn the key bump silently back into a flag clear. That is a fenced assertion, not a
  comment.
- The boundary is **keyed by layout and project** in `page.tsx`, so a crashed canvas does not
  follow the user to data that is fine.

**Item 1 — the no-permission arm — landed as a primitive capability, wired at no call site.**
Two-thirds of the triad shipped with S4 (§11.21): `src/components/EmptyState.tsx` plus
`app/projects/lib/emptyState.ts` already answer *filtered-to-nothing* and *never populated*.
The third arm renders its CTA **disabled rather than hidden**, so the reader learns the
action exists *and* that it is not theirs — hidden, they learn neither and go looking.
`EmptyStateAction` gains optional `disabled` / `disabledReason` (and `onClick` becomes
optional, because a disabled action has nothing to run); `emptyStateCopy` gains optional
`canCreate`, defaulting **true** so every existing caller renders exactly what it did. The
reason is rendered, not only tooltipped: a disabled button is not focusable, so `title`
alone is unreachable from a keyboard and never appears on a touch screen. Precedence is
**filtered → no-permission → status-axis → empty**, and the argued step is the middle one —
a viewer on a column-less board must not be told *"add a status"*, because unfollowable
advice reads as a broken app rather than as limited access. `TaskBoard`/`TaskList`/
`TableView` were **not** edited: an additive optional prop needs no call-site change, and
those files were held open by sibling slices.

**Fences added** (R7), all mutation-measured red and restored byte-identical:
`src/lib/layoutBoundary.test.ts` — the arithmetic (retry advances and never re-uses a key;
`caught` cannot reset it) **and** a source scan in `sharedTaskUi.test.ts`'s idiom asserting
the boundary is declared once, consumes the tested helpers rather than re-deriving them,
hands `attempt` to React as a `key`, and **is the scroll region's only child**, which is what
makes a seventh canvas guarded without anybody remembering to add a row. `emptyState.test.ts`
gains the no-permission arm — present-but-disabled, never absent, never enabled, and
outranked by filters.
Mutants: hiding the CTA (4 red) · enabling it (2) · rendering `MyWork` outside the boundary
(2) · dropping the boundary's `key` (1) · Retry clearing the flag (1) · dropping the child
`key` (1) · `caught` returning `attempt: 0` (2) · `retry` re-using its key (3).

⚠️ **Honestly outside any fence, and labelled review-only:** *"a malformed group shape must
not blank the app"* and *"Retry re-mounts rather than re-crashing"* need a render with a
throwing child. This runner has no jsdom and no testing-library, and adding a DOM substrate
to fence one component is a substrate decision, not a papercut ticket — so it was **not**
done. Those two claims are checked by throwing from a canvas and looking. The four-theme
sweep on the fallback is owed for the same reason every UI slice in this wave owes one: no
browser runs here. Also owed: `renderState()`'s retirement onto `EmptyState`, a caller that
actually passes `canCreate` (the per-project write grant is not on the client's row shapes
yet), and a `/tasks`-side boundary — `LayoutBoundary` is shared by placement, with one
consumer.

⚠️ **Corrected 2026-08-11 by adversarial review — the remaining wiring is TWO edits, not
one, and the second is the whole feature.** This entry said the arm needed "a caller that
actually passes `canCreate`". Passing it is necessary and **not sufficient**: both
consumers — `TaskBoard.tsx:558` and `TaskList.tsx:210` — render
`action={copy.filtered ? { label: "Clear filters", … } : undefined}`, i.e. they build their
own action and **discard `copy.action` unconditionally**. So a slice that only passes
`canCreate: false` would produce exactly the outcome the arm exists to prevent: a read-only
reader with **no CTA at all**, never seeing `disabledReason`, while
`emptyState.test.ts`'s *"offers the action DISABLED — never absent, never enabled"* stays
green. That is a fence that passes while the shipped screen does the forbidden thing — the
gap being a call site the fence cannot see. The follow-up must change both call sites to
prefer `copy.action`, and the fence for it has to be structural (the call site), not another
assertion about the pure module.
### 11.30 WS-27bd (narrowed) — the right-click menu, promoted; per-row pending (built 2026-08-11)

**Two of the five items in the §9.5.2 basket. Three were struck before any code was
written, each for a measured reason** — the strikes are the more valuable half of this
entry, because each one is a slice somebody would otherwise build.

**Struck (1) — "shortcuts release unclaimed keys".** Its stated fence (`preventDefault` is
not called when no handler is registered) is a good test **of a registry that does not
exist**. Keyboard handling today is three unrelated places — `projects/page.tsx`'s window
listener, `lib/search.ts`, `lib/commands.ts`'s `stepSequence` — so building it means minting
a **third** keyboard seam, which is a seam decision, not a papercut. And nothing binds
`Mod+F` anywhere in the tree (zero matches), so "falls through to find-in-page" is already
true by absence.

**Struck (3) — "clipboard failure never claims success": ALREADY TRUE.** All eight
`clipboard.writeText` sites were checked. Six carry an explicit `catch` that deliberately
does not flip "Copied", `/projects`' own site (`TaskPanel.tsx`) among them, with a comment
saying why. The two `.then()` sites in the chat components never run their success branch on
rejection either; they leak an **unhandled rejection**, which is a different and much
smaller defect and is recorded rather than swept in here.

**Struck (4) — "signature-keyed banner dismissal": no target.** No dismissible banner with a
persist-forever key exists. The nearest seam, `src/lib/dismissedTools.ts`, is already
id-keyed.

**Built (5) — the context menu, as a PROMOTION.** `/projects` had zero `onContextMenu` and a
working generic menu already existed at `app/tasks/components/ContextMenu.tsx`, wired at five
call sites. It was **moved** to `src/components/ContextMenu.tsx` with a re-export shim left
at the old path — /tasks' five call sites are unedited — and wired onto the board's cards
and My work's cards. `components/TaskCardShell.tsx` has accepted `onContextMenu` since S1 and
/projects simply never passed it, so the wiring is a prop pass-through and the shell is
**unchanged**. The ITEMS come from a new declared registry, `lib/taskMenu.ts` (Open · Copy
link · Select/Deselect · Change status, each with the context it is offered in), read by both
surfaces — one registry, two surfaces. Nothing new arrives from `page.tsx`: Open is
`onSelect`, Select is `onToggle`, and a status change is `onDrop` carrying
`buildColumnDropUpdate`'s axis patch with an **empty** position plan, so the card keeps the
manual order it had.

⚠️ **Scope: CARDS ONLY.** The row half of the basket's wording is deferred — table rows were
being made link-navigable in a parallel slice and two agents in one click path is how a
regression gets attributed to neither.

⚠️ **`lib/taskMenu.ts` is a SECOND registry at a different scope, deliberately, and this
corrects §9.5.2's wording.** The basket says the menu should read "the same action registry
the palette already uses". It cannot: `lib/commands.ts` is the **page** registry (go, view,
panel, project) and contains no task-scoped action for Open / Copy link / Select / Change
status to resolve to; a card menu assembled from what it does contain would read
"Widen the task panel · Custom fields · Import from ClickUp". Extending `commands.ts` with
task actions is a legitimate option and it changes the palette, the `g`/`v` sequences and the
printed shortcuts sheet at once — a ticket, not a side effect. What is enforced instead is
that the two registries stay **disjoint in both directions**, which is the "no second
vocabulary" property the sentence was reaching for.

**Built (2) — per-row pending and per-row error**, in `RelationsBlock.tsx`'s unlink rows.
State is a pure reducer, `lib/rowState.ts` (`pending: Set<id>`, `errors: Map<id, string>`),
so three concurrent unlinks are three independent spinners and one refusal is written under
the row it was about. Two rules that are easy to get backwards are written into it: a retry
does **not** blank the previous error on entry (it clears on success — the rule `MyWork`
already follows), and `prune` drops state keyed to a row that has left the list, so a message
cannot outlive the row it was attributed to. The add-link FORM keeps its own error: it is one
control, not a row.

**Fences (R7), all mutation-measured — 22 mutants applied, 22 killed.**
`src/lib/sharedTaskUi.test.ts` gains the structural row: **exactly one `ContextMenu` is
declared under `src/`**, and both apps consume it. The pre-existing THIRD copy inside
`app/email/components/EmailList.tsx` is a **recorded exemption naming it** — it is genuinely
the same interaction, but it carries flyout submenus and bulk-vs-single fan-out the flat
shared menu does not express, so retiring it is an email ticket (CLAUDE.md §5: existing
violations are findings for the board). The exemption is itself checked: the suite fails if
the file stops declaring one. `taskMenu.test.ts` pins which entries each surface is offered,
that the task's own status is the ticked one, that no separator opens or closes the menu once
a group is filtered away, that every emitted id traces back to a declared action through the
`<kind>:<discriminator>` grammar, and the disjointness cross-check against `COMMANDS`.
`rowState.test.ts` pins three-at-once with one attributed failure.

**Honest about what is NOT fenced.** "Right-click opens the menu at the pointer and Escape
closes it" is **review-only** in this tree: `vitest.config.ts` is `environment: "node"` with
`include: ["src/**/*.test.ts"]`, so there is no DOM and `.tsx` tests are not collected. The
viewport-flip, the click-away catcher and the Escape handler are the promoted file's own,
unchanged and already in production on /tasks' five call sites — but nothing here re-proves
them. **The four-theme visual pass is also owed**, as for every UI slice in this environment.

### 11.31 WS-27ak item (1) — the `Modal` primitive, and six dialogs onto it (built 2026-08-11)

**Status: BUILT + REPAIRED 2026-08-11. Branch `ws-27ak-modal-repair`, cut from `684e3f2f`
(itself cut from `be26999b`). NOT merged, NOT deployed.** Frontend only: no migration, no
gateway change, one new dependency. Verification at repair: `npx tsc --noEmit` exit 0 ·
`npx vitest run` 85 files / 1916 tests, exit 0 · `npx next build` exit 0 ·
`npx playwright test e2e/modal.spec.ts` **10 passed**, exit 0.

**Built.** `workbench/control_plane/src/components/ui/Modal.tsx` — a Metorite wrapper over
`@base-ui/react@1.7.0`'s `dialog` (D-PM-15's substrate; installed under the **new** name, not the
deprecated `@base-ui-components/react` whose `latest` is a release candidate). Semantic tokens
only, `<Icon name>` for glyphs, every control a `<Button>`. Six `/projects` dialogs render it:
`ShortcutsSheet` · `SearchPalette` · `ImportClickUp` · `FieldManager` · `TagManager` ·
`LifecyclePolicy`. **`app/projects/page.tsx` is unchanged**, as the audit required.

**`overlayOpen` survives, and that is the load-bearing part.** The wrapper is **strictly
controlled** — `open` in, `onClose` out, no internal open state — so `page.tsx:968-974`'s
`overlayOpen` is still derived from the page's own state and `:1039`'s window listener still
returns early on it. Fenced in a real browser: with a dialog up, `g` then `t` does **not**
navigate to `/tasks`, and the same sequence works again the moment it closes.

**Measured before → after, same harness, same page (`/projects`, no auth, no API mocking):**

| | before (`00c47c6b`) | after |
|---|---|---|
| `activeElement` after open | `BODY` | inside the popup |
| focus inside after 6 Tabs | `false ×6` | `true` for 10 Tabs and 6 Shift+Tabs |
| background marked | nothing | `aria-hidden="true"`, cleared on close |
| `activeElement` after close | `A` (an arbitrary anchor) | the opener, by identity |
| page scroll under an open dialog | scrolls | locked, no width change, freed on close |

**Repair round 1 (2026-08-11) — what the verifier and reviewer found, and what changed.**
The primitive's code was confirmed right; five of the seven findings were **documents lying
about the code**, which is the failure mode this spec keeps recording.

*Two behavioural defects, both fixed:*

1. 🔴 **The documented `finalFocus` fallback did not exist — focus landed on `<body>`.**
   `Modal.tsx` promised focus "falling back to Base UI's own resolution when it has unmounted
   — never `<body>`". There is no such fallback in the substrate:
   `FloatingFocusManager.js:476` drops `elementFocusedBeforeOpen` once `!isConnected`,
   `getPreviouslyFocusedElement()` filters disconnected elements and returns `undefined`,
   `getReturnElement` returns `null`, and **no `.focus()` runs at all**. Reachable in a
   dialog this slice converted: `ImportClickUp`'s only trigger is the empty-state button
   (`ProjectTree.tsx:140`), and a real import calls `onImported()` → `page.tsx` bumps
   `treeKey` → the tree refetches and **replaces the trigger while the dialog is still
   open**. Closing it then left `activeElement: BODY` — verbatim the pre-slice measurement
   this work exists to remove. **The wrapper now resolves it itself**, in a documented order:
   an explicit `finalFocus` ref if still connected → the opener captured *during the render
   in which `open` flips true* (an effect is too late — child effects run first, so the
   substrate has already moved focus in) → the page's `<main>` / `[role="main"]` landmark,
   focused with a `tabIndex={-1}` the wrapper adds and removes again on blur → and if the
   document has neither, **the docstring says focus is left on `<body>`** rather than
   claiming a guarantee. The landmark is focused by the wrapper in a microtask rather than
   handed back to the substrate, because Base UI's `getFirstTabbableElement()` would redirect
   a `tabindex="-1"` container to its first tabbable *child*, i.e. the first control on the
   page — a jump, not a return.
2. 🔴 **`max-h-full` was baked into the popup's base classes, so every call site's height was
   a dead knob.** Measured: `getComputedStyle(popup).maxHeight === "100%"` with
   `max-h-[80vh]` passed in — two `max-height` utilities of equal specificity are decided by
   the order Tailwind *emits* them, not the order in `class`, so `ShortcutsSheet`'s
   `max-h-[80vh]` and `ImportClickUp`'s `max-h-[88vh]` both lost and the importer rendered
   ~10% taller than designed. The default is now applied only when the caller has not
   expressed a height (`CALLER_SETS_MAX_HEIGHT`, any responsive prefix included).

*Three records corrected — the code was right, the documents were not:*

3. `Modal.tsx`'s own header asserted Base UI's `markOthers` "sets a real `inert` attribute …
   so find-in-page and a screen reader cannot walk into the page underneath". It does not
   (measured `[inert] = 0`); it sets `aria-hidden="true"` plus a `data-base-ui-inert`
   **marker**. The header now states the truth precisely — **screen readers cannot reach the
   background, Tab cannot leave the dialog (guard nodes), find-in-page CAN still reach it** —
   which is what §11.31's point 2 below already said and what the primitive's own file, the
   one every future call site reads, was contradicting.
4. `DESIGN_SYSTEM.md` §4a repeated the same false `inert` claim **and mis-named a fence**,
   which is an R7 violation: it credited conformance rule 8 with stopping "a hand-rolled
   dialog … becoming the seventh scrim colour", but rule 8 only forbids *importing*
   `@base-ui/react` outside `src/components/ui/` — a `fixed inset-0 bg-black/60` div imports
   nothing and is exactly the 70-file status quo. §4a now separates each fence from what it
   does **not** do, and a **real, narrow structural fence** was added:
   `conformance.test.ts`'s *"the converted `/projects` dialogs do not grow an overlay back by
   hand"* — none of the six converted files may contain `fixed inset-0` (they grep clean
   today, so it passes on arrival and catches the regression), plus a companion case that the
   six still import `Modal` so the scan cannot go vacuous. The claim that a *new* surface
   uses `Modal` is now labelled **advisory**, because nothing in this tree can tell a new
   dialog from a new dropdown scrim. §4a also said "six different values" and listed seven.
5. `e2e/modal.spec.ts` recorded a mutation measurement that does not reproduce — "0 dialogs
   with `Dialog.Backdrop` removed". Removing the backdrop alone is **10 passed / exit 0**,
   because `DialogPortal.mjs:38` renders an `InternalBackdrop` whenever `modal === true` and
   that alone keeps `outsidePressEvent` at `'intentional'`. The assertion is still a genuine
   discriminator; the mutation that reaches `'sloppy'` is **both** halves (backdrop removed
   **and** `modal="trap-focus"`), measured **8 passed / 2 failed**. The comment now records
   that run and names `conformance.test.ts` as the static guard on the backdrop's presence.

*Two minor:* `ShortcutsSheet` passed both `label` and `title` and the `label` was dead (the
wrapper uses it only when there is no `title`) — dropped; and every converted dialog's close
button had become the generic `aria-label="Close"`, where this sheet previously said "Close
the shortcuts sheet". A `closeLabel` prop (default `"Close"`) restores the specific name.
`ImportClickUp`'s subtitle was being `truncate`d by `<Dialog.Description>` and clipped on a
phone — *"Every Preview writes nothing. Only the buttons that say so write."* — so the
description wraps now; the title still does not.

**Fence:** `workbench/control_plane/e2e/modal.spec.ts` — **10** cases, Playwright (D-PM-21),
all mutation-measured. The repair's own measurements: passing `finalFocus` straight through
(the pre-repair wrapper) → **9 passed / 1 failed**, `activeElement: BODY`; re-baking
`max-h-full` unconditionally → **9 passed / 1 failed**; adding `fixed inset-0` to
`TagManager` → conformance **31 passed / 1 failed**; breaking one of the six `Modal` imports
→ conformance **31 passed / 1 failed** on the companion guard. `npm run test:e2e` **could not run in this environment at all** (it set
neither `PLAYWRIGHT_*` variable and Playwright looked for a browser revision that is not
installed); it now goes through `scripts/run-e2e.mjs`, which fills them in when the pre-installed
Chromium is really there and changes nothing otherwise. Static fence: **conformance rule 8** —
nothing outside `src/components/ui/` may import the substrate, plus a `package.json` check that a
*second* substrate cannot arrive through a vendored registry (D-PM-15 condition 2). Rule 8 tripped
the suite's own rule-count assertion, so `workbench/control_plane/AGENTS.md` and the root
`CLAUDE.md` now say **eight**; that edit is mechanical and expected.

🔴 **Two of the ticket's done-whens are factually impossible against the substrate D-PM-15 chose.
Restated, not dropped — and not reported as met.**

1. **"outside-click dismissal only when the press both started *and* ended outside."** The
   behaviour is delivered; the mechanism named in the dispatch brief is not. **There is no
   `outsidePressEvent` prop on `Dialog.Root` in 1.7.0** — `dialog/root/useDialogRoot.mjs:23-33`
   computes it internally and returns `'intentional'` **whenever a backdrop element exists**
   (`<Dialog.Backdrop>`, or the internal one `<Dialog.Portal>` renders for `modal === true`). The
   wrapper renders both, exposes no way to turn either off, and conformance rule 8 asserts the
   backdrop is still there. ⚠️ The *discriminating* browser case is the direction nobody writes
   down: a press that starts on the backdrop and is **released inside** the dialog. Measured —
   `intentional` keeps it open, `sloppy` has already closed it on `pointerdown`. The
   drag-out-of-the-dialog direction the ticket names does **not** discriminate the two (Base UI's
   `insideReactTree` guard covers it either way), which is exactly the sort of fence that passes
   for the wrong reason.
2. 🔴 **"the background is `inert`, not merely covered" is NOT met, and cannot be by this
   substrate.** `@base-ui/react@1.7.0` **never sets a real `inert` attribute** on the background:
   `FloatingFocusManager.mjs:339` calls `markOthers(…, { ariaHidden: modal })` and nothing in the
   package passes `inert: true` (`markOthers.mjs:147-155` defaults it to `false`). What it does is
   `aria-hidden="true"` plus a `data-base-ui-inert` marker, and focus containment via guard nodes.
   **Consequence:** a screen reader cannot walk into the background and Tab cannot leave the
   dialog — but **find-in-page still can**. The dispatch brief asserted the opposite ("its
   `markOthers` sets a real `inert` attribute on siblings"); that is wrong for 1.7.0 and the code
   is cited above. Closing the gap means either a Base UI change or the wrapper marking body
   children itself, which is a second implementation of `markOthers` and therefore **a board
   decision, not an agent's**.

**Deliberate behaviour changes, recorded because they are changes.** `FieldManager`, `TagManager`
and `LifecyclePolicy` had **no** Escape and **no** outside-press dismissal at all; they have both
now, uniformly, because a primitive whose behaviour is per-call-site is not a primitive.
`TagManager`'s inline rename gained `stopPropagation` on Escape so the first press still leaves the
field rather than closing the dialog — the substrate binds Escape on `document`, which sits above
React's root container. `ShortcutsSheet` lost its own window-Escape listener (the reason for it —
"nothing in the sheet is focused" — no longer holds) and the search palette moved from `pt-24` to
the shared `pt-16`.

**`src/lib/outsideClick.ts` is NOT consumed by this Modal, deliberately.** Its docstring names
"Wave 2's Modal / Tooltip / Combobox" as why it exists ahead of need; Base UI brings its own
outside-press handling with a start-and-end rule the hand-rolled walker does not express. It
remains the answer for a popover we do not build on the substrate. Written down in
`AGENTS.md` rule 8 so the tree does not have two answers and no record of why.

**Retirement owed, not done:** `app/email/components/automation/ui.tsx:15` exports a second
`Modal` with **five** consumers and no focus trap, focus return, scroll lock, `role` or
`aria-modal`. Its call sites were left alone. Naming it here is the same move WS-27bd made for the
email `ContextMenu`; adding a sixth consumer is the thing not to do.

**Also verified, since D-PM-15 flagged it as unknown:** Next 16.2.6 / Turbopack interop with Base
UI **works** — `npx next build` exit 0 with the substrate imported.

**Not built, and not attempted:** items (2) Tooltip and (5) Skeleton (NO-GO — their done-when is a
count of the problem), (3) Toast, (4) Combobox. **Owed:** the four-theme visual pass, as for every
UI slice here — no test in this tree looks at a layout. *(Toast landed afterwards as slice 2 —
§11.32.)*

### 11.32 WS-27ak item (3) — the `Toast` primitive, and the two things its own tests got wrong (built 2026-08-11, repaired and verified 2026-08-12)

**Status: BUILT on `ws-27ak-toast`, VERIFIED 2026-08-12, merged via PR #430.** Frontend only: no
migration, no gateway change. `src/lib/toast.ts` (the message rules, the dedupe key and the promise
state machine, as a pure module) + `src/components/ui/Toast.tsx` on `@base-ui/react@1.7.0`'s
`toast`, wired at four call sites (`layout.tsx`, `NotificationBell.tsx`, `TableView.tsx`,
`TaskPanel.tsx`), fenced by `src/lib/toast.test.ts`, `e2e/toast.spec.ts` and an extension of
conformance rule 8.

**Verified independently 2026-08-12** (the implementing agent died on an API limit before any of
this ran, and the PR was opened as a draft saying so): `npx tsc --noEmit` exit 0 · `npx vitest run`
**88 files / 1983 tests** exit 0 · `npx next build` exit 0 · **`npx playwright test e2e/toast.spec.ts`
6/6**, with `e2e/modal.spec.ts` 10/10 as the control.

🔴 **Two of the six browser tests were red, and BOTH were the test's fault, not the component's.**
Worth recording because each is a trap the next e2e author will hit:

1. **`[role="alert"]` counted Next's route announcer.** Next renders a permanent assertive live
   region, `#__next-route-announcer__`, inside a **shadow root** on `<next-route-announcer>`.
   Playwright's selector engine pierces open shadow roots and `document.querySelectorAll` does not
   — so the page reads as one alert from the console and two from the test, and every count in that
   test was off by one. Now excluded by id.
2. **`getByRole` cannot see a high-priority toast's controls.** `priority: "high"` — which is what
   makes an error assertive — also makes Base UI stamp `aria-hidden="true"` on the *visible* toast
   until the viewport is focused (`ToastRoot.mjs:418`), deliberately, so a reader is not told the
   same thing twice. Its `Retry` and dismiss buttons go out of the a11y tree with it. The two clicks
   are now structural, with the reason written at each; **F6 is the reader's way in** and the last
   test in the file already walked that path.

⚠️ **And one fence in the snapshot was dead — found by mutation, not by reading.** The test named
*"a keyed re-fire updates the toast rather than stacking a second one"* fired the second toast from
the **Retry** button, whose handler closes the toast *before* re-running the closure (deliberate:
closing afterwards would dismiss the toast the retry had just raised). With nothing on screen there
is nothing to stack against, so with `toastIdFor` stubbed to `undefined` the test **still passed**.
The second fire now comes from pressing "Mark all read" again — the button survives a failed mark,
and the error toast is still up. Measured under the same mutation: **2 toasts mid-flight and 2
settled**, test red. Dedupe was never unfenced (`toast.test.ts` pins `toastIdFor`, 2 tests red under
the mutation) — but nothing in a browser proved the *behaviour*, which is what the name claimed.

**Owed, unchanged:** the four-theme visual pass (Fluent → Material → Graphite), as for every UI
slice here. And the constraint the docstring carries stands: **do not raise a toast from inside a
`Modal`** — `markOthers` hides the toast portal as a sibling of the dialog portal, so the three
wired call sites are all outside dialogs. Lifting it is a board decision, not a call site's.

### 11.33 WS-27be — the search index nobody could use, and the missing minimum (built 2026-08-11)

**Status: BUILT 2026-08-11. Branch `ws-27be-trgm-search-index`, cut from `583a0d38` on
`claude/paca-research-task-management-a1f6zd`. NOT merged, NOT deployed.** One migration
(`170_projects_search_trgm.sql` — number taken from the directory at build time, R1; re-check at
merge), one gateway module changed, no API shape change, no frontend change. Verification:
`uv run ruff check <the six touched files>` exit 0 · `uv run pytest tests/unit/test_projects_filters.py
tests/unit/test_projects_migration.py tests/unit/test_projects_search.py tests/unit/test_projects_routes.py
tests/unit/test_migration_prefixes.py` **294 passed**, exit 0 · the whole `/projects` unit surface
(20 files) **669 passed**, exit 0 · `uv run python tests/live/live_ws27be.py` **23 checks, all
green**, exit 0, against a real Postgres 16 with 60k seeded tasks.

**The defect, restated from the plans rather than from the ticket.** `146_projects.sql` created
`idx_pm_tasks_fts` over `to_tsvector('english', title || ' ' || description)`. Nothing has ever
used it: both readers of those columns — `search.py::_SEARCH_SQL` and
`filters.build_task_filters` — spell the predicate `ILIKE '%…%'`, and no `to_tsvector` index
serves `ILIKE`. Measured at 60k rows, the `BEFORE` plan reaches `pm_tasks` through
`idx_pm_tasks_project_id`, applies the ILIKE as a **`Filter`**, and throws away **59,935 rows**
to return 51.

**The decision, and the sanity check the owner asked for.** `pg_trgm` + `gin_trgm_ops`, not
`websearch_to_tsquery`. The owner's reasoning holds and is worth restating as a *product* claim,
not a performance one: full-text search stems (`parsing` stops matching `parser`), drops
stop-words, and cannot match inside a word at all — `task_i` would return nothing for `task_id`,
and `50%` and `#42` stop meaning what they mean. Our search box is documented as
substring-oriented (§11.18, and `like_escape` exists precisely because people search
identifiers). Trigrams keep every answer byte-identical. `EXPLAIN` confirms the planner takes
them, so the "recommend dropping `idx_pm_tasks_fts` instead" branch did not have to be taken.

**Measured, same harness, same statements, before → after** (`tests/live/live_ws27be.py`, 60k
tasks in one tenant, plans taken with the caller's REAL bound parameters through asyncpg — not a
hand-written `EXPLAIN` with the term spliced in as a literal, which answers a different question):

| statement | before | after | rows filtered before → after |
|---|---:|---:|---|
| `/projects/search?q=quicksilver` | 652.9 ms | **0.71 ms** | 59,935 → 2 |
| `/projects/search?q=1234` (numeric) | 558.5 ms | **0.30 ms** | 59,985 → 2 |
| `/projects/tasks?q=quicksilver` | 441.7 ms | **0.31 ms** | 59,935 → 1 |

The claim that carries this is not the millisecond count, it is the plan line: `Filter: (title
~~* '%quicksilver%')` became `Index Cond: (title ~~* '%quicksilver%')` under a `BitmapOr`.

**Three indexes, and the one that was nearly missed.** A `BitmapOr` forms only when every arm is
indexed well. `search.py::task_number` turns `#42` — or a bare `42` — into a third arm,
`OR task_number = 42`, and **`(root_project_id, task_number)` looks like it covers that and does
not**: `task_number` is its second column, so the arm is answered by reading the *whole* composite
index. Postgres does exactly that rather than give up, and the plan prints `Bitmap Index Scan on
pm_tasks_root_project_id_task_number_key`, a line that reads like the index working. It costs
1693 planner units and 298 buffers against 4.4 and 1 for a dedicated btree; on a 240k-row
measurement database the planner abandoned the disjunction altogether and the whole search fell
back to a scan (195 ms vs 1.6 ms). So `idx_pm_tasks_task_number` ships in the same migration.
⚠️ **This is one line beyond the ticket's literal "index on the searched columns", and it is
named here rather than absorbed**: without it the fix is invisible to anyone who types a task
number, which in a task tracker is constantly.

**R6 — `idx_pm_tasks_fts` is deliberately NOT dropped.** Removing the only other text index in
the same change that introduces an untried one, on a ladder we cannot roll back, is the trade the
rule exists to refuse. **The follow-up and its trigger, recorded so it is not lost:** once
`pg_stat_user_indexes.idx_scan` for `idx_pm_tasks_title_trgm` is **non-zero** on the live database
and for `idx_pm_tasks_fts` is still **zero** (`SELECT indexrelname, idx_scan FROM
pg_stat_user_indexes WHERE relname = 'pm_tasks'`), `DROP INDEX idx_pm_tasks_fts` is a migration of
its own. It reclaims ~18 MB per 240k tasks and, more usefully, stops the next reader believing
search is covered. Fenced meanwhile: `test_the_old_full_text_index_is_not_dropped_in_the_same_change`.

**The missing minimum, and why it is `FALSE` rather than a dropped clause.** `filters.py` accepted
any non-empty `q`; `search.py` has always enforced `MIN_QUERY = 2`. The constant **moved into
`filters.py`** and `search.py` re-exports it — one rule, one definition, and the dependency arrow
already pointed that way. A sub-minimum `q` now appends a literal `FALSE`, so the answer is
`{"rows": [], "total": 0}`, matching `search.py:228`'s empty-result-not-422 choice; the plan is
`Result → One-Time Filter: false` and `pm_tasks` is never touched (0.016 ms). **Dropping the
clause would have been the worse fix**: a one-character `q` would return the whole board, so a
client that sent it renders an unfiltered list believing it is filtered.

**The client needed no change, and that is the point.** `FilterBar.tsx:162` sends what was typed
and renders what the server answers, so with empty-result semantics the two agree by
construction. Teaching the client the threshold would put a second copy of `MIN_QUERY` in
TypeScript — the disagreement this ticket was opened about, reintroduced on the other side.

**Honest limit, measured not assumed: `MIN_QUERY = 2` is one character below what a trigram index
can serve.** `pg_trgm` extracts whole trigrams from the pattern and `%qu%` contains none. The
planner still *names* the index in the plan — and then reads every entry: **127.5 ms** for `?q=qu`
against 0.31 ms for a 3-character term, i.e. no better than before. Raising the minimum to 3
would make every accepted query servable, but "qa", "ui" and "hr" are real searches, so it is a
**product decision and is owed, not taken**. The harness prints both numbers on every run so the
cost stays visible.

**Tenancy (R5).** No new table, nothing to key. The index does **not** defeat the tenant
predicate: the trigram bitmap finds candidate rows and `organization_id = :vis_org` plus the grant
closure are applied as filters above it, so the row set is unchanged. Fenced live in two
directions — org A's search for the planted token returns its own hits and not org B's
identically-titled task; org B's returns exactly its one.

**Fences (R7), each mutation-measured.** Ten source mutations, each turning a named test red:
dropping the `FALSE` clause (3 fail) · removing the minimum (6) · lowering it to 1 (7) ·
`search.py` declaring its own `MIN_QUERY` (7 — the value comparison alone would **not** catch this,
because CPython caches small integers, so the fence is structural) · dropping the LIKE escape (3) ·
deleting the description index (1) · deleting the `task_number` index (1) · removing
`CREATE EXTENSION` (1) · un-guarding a `CREATE INDEX` (1) · adding the `DROP INDEX` this migration
must not carry (1). The live harness was mutation-measured too: deleting the `task_number` index
from the migration turns two of its checks red and its exit code to 1.

⚠️ **One existing fence was rewritten, not deleted.**
`test_the_list_endpoint_escapes_too_not_only_search` asserted on the **source text** of the single
line this ticket had to reshape — so it failed on a refactor whose subject it did not touch, and
would equally have passed on a rewrite that kept the spelling and dropped the behaviour. It now
asserts on the **bound pattern** `build_task_filters` hands the database, across four
metacharacter cases. Same claim, strictly stronger.

**Not done, deliberately.** The `idx_pm_tasks_fts` drop (above, with its trigger) · raising
`MIN_QUERY` to 3 (product decision, owed) · anything on the `to_tsvector` route (the decision went
the other way and the plans support it) · a per-tenant partial index (`pm_tasks` is not big enough
for that to be anything but a guess).

### 11.34 WS-27bc (the one dispatchable slice) — `pagedPicker`, a pure module with no picker in it (built 2026-08-11)

**Status: BUILT 2026-08-11. Branch `ws-27bc-paged-picker`, cut from `583a0d38` on
`claude/paca-research-task-management-a1f6zd`. NOT merged, NOT deployed.** Frontend only:
**no migration, no gateway change, no new dependency, no component, and no call site wired.**
Verification: `npx tsc --noEmit` exit 0 · `npx vitest run` **86 files / 1947 tests**, exit 0
(base was 85 / 1916 — this slice is the one new file and its 31 cases).

**The ticket is still NO-GO; this is the substrate-independent remainder** §9.5.2's audit
identified as genuinely unbuilt. Everything the audit blocked stayed blocked and is listed below
rather than quietly skipped.

**Built.** `workbench/control_plane/src/app/projects/lib/pagedPicker.ts` +
`pagedPicker.test.ts` — four pieces of arithmetic and nothing else:

1. **`shouldLoadMore(scrollTop, clientHeight, scrollHeight, threshold = 48)`** — pure numbers,
   no element and no `IntersectionObserver`, because `vitest.config.ts` here is
   `environment: "node"` and a decision taken inside a component would have no fence at all.
   Two boundary rules are deliberate and both are asserted: **at** the threshold counts as
   reached (`<=`, not `<` — a scroller resting exactly on the line would otherwise wait for a
   pixel of scroll a trackpad's momentum may never deliver), and a scroller whose content does
   not fill its viewport is **already at its end**, which is the classic "first page did not
   fill the box so no scroll event ever fires and paging stalls at page 1" bug answered in the
   predicate rather than at each call site. A non-finite measurement asks for nothing.
2. **`appendPage` — dedupe by id, first position wins, last payload wins.** The hazard is real
   and not defensive: `/projects/tasks` is ordered by `updated_at`, so page N+1 is a second
   query against a table that moved in between and a row can legitimately come back. Keeping the
   **first position** stops the list re-sorting under the reader's cursor; keeping the **last
   payload** stops it rendering a title we have just been told is stale.
3. **A terminal state that is derived, never flagged.** `done` is a property of the page in
   hand (`page.length < pageSize`), not a `hasMore` boolean kept beside the rows that can be
   updated on one path and not the other. ⚠️ It is computed from the **raw** page length, never
   from how many rows the dedupe actually added — a full page whose rows were all already on
   screen adds nothing, and reading that as the end of the list truncates the picker at whatever
   the reader happened to have seen. `pageSize <= 0` is treated as done, or the caller asks
   forever for a page that cannot contain anything.
4. **The min-length gate and the stale-response guard are both IMPORTED.** `MIN_QUERY` and
   `isCurrent` come from `app/projects/lib/search.ts` (`:27` and `:80`), which already mirror
   `gateway/routes/projects/search.py:72` and already solve the out-of-order-response bug. A
   third copy of the number, or a second query comparison, is the CLAUDE.md §5 defect — and this
   module is precisely where someone would add one.

**The structural assertion is the reason this file was worth writing carefully.** A behavioural
test cannot tell a shared constant from a second copy that happens to hold the same number today,
so `pagedPicker.test.ts` closes with a source scan (the `controlLink.test.ts` /
`sharedTaskUi.test.ts` idiom, rooted at `src/` via `import.meta.url` so a checkout with agent
worktrees does not scan itself): the module must import both names from `./search`, must declare
neither itself, and must actually **call** `isCurrent(` and reference `MIN_QUERY` outside the
import line — an import a module never uses is what a half-finished revert leaves behind.

**Fences mutation-measured 9/9 red, file restored byte-identical:** `<=` → `<` at the threshold
(2 red) · dedupe removed, pages concatenated (5 red) · a repeated row keeping its stale payload
(2 red) · `done` derived from accumulated growth instead of the raw page (1 red) · the
stale-response guard removed (2 red) · `>=` → `>` on the minimum (3 red) · the `pageSize <= 0`
guard removed (1 red) · the minimum no longer trimming (1 red) · **`MIN_QUERY` re-declared
locally instead of imported (2 red — the §5 rule made executable).**

🔴 **Struck, corrected in the module's own docstring: the ticket's central justification.**
WS-27bc claims the minimum stops "a leading-wildcard scan with no index behind it." It does not.
The gateway's predicate is `ILIKE '%…%'` either way and `'%ab%'` is exactly as unindexable as
`'%a%'`; `pg_trgm` appears zero times in `infra/`. What the minimum bounds is the **result set**,
in the gateway's own words (`search.py:68-71`): "returning half the workspace."

**Not built, each for a recorded reason — these are findings, not omissions:**
- **No debounce helper, shared or local.** There are **seven** ad-hoc copies in the tree
  (`projects/lib/search.ts`'s `DEBOUNCE_MS = 180`, `FilterBar.tsx:162`'s inline `300`,
  `RecipientInput`'s `200`, …) and no shared one. Minting an eighth is the defect; minting a
  shared one and leaving seven is half a seam. That is its own ticket, and the ticket's "300ms"
  is the number it has to reconcile.
- **No component, no popover, no listbox.** A picker's surface owes `aria-expanded`,
  `aria-controls`, `aria-activedescendant`, focus return and outside-press — all of which
  D-PM-15 condition 1 says arrive as a wrapper under `src/components/ui/`. Hand-rolling that
  shell here is the second-substrate failure condition 2 exists to prevent. The surface half is
  re-sequenced **behind** WS-27ak, not beside it.
- **No endpoint change, and no fallback below the minimum.** `/projects/search` is
  capped-not-paged by a recorded decision in its own module docstring and answers `{"rows": []}`
  to an empty `q`; `/projects/tasks` is paged with no minimum and no ranking. `isSearchable`
  therefore reports the fact and stops — choosing between straddling the two (the list visibly
  reorders at the two-character boundary) and reopening `search.py`'s paging decision is a
  decision, not an edit.
- **Nothing is wired to a call site.** The eventual target is `RelationsBlock.tsx`'s
  `<Input placeholder="task id">` (`:336-347`), the only true long-list surface in `/projects`.
  ⚠️ Recorded for whoever wires it: `search.py`'s `exclude_relatives_of` was built for exactly
  that picker (WS-27w item 5) and has **zero client consumers** today.

**Owed:** nothing visual — this slice renders nothing, so the four-theme pass does not apply to
it. What it cannot prove is that a real scroller fires at the right moment; there is no DOM and
no e2e here, and that stays review-only until the surface half lands.

### 11.35 WS-27bg slice 1 — the run-state axis, archive, and three automations that would have corrupted data (built 2026-08-13)

**Migration 171** (`171_projects_run_state.sql`, number taken at build time per R1 —
**re-check at merge**). Frontend untouched; slices 2 and 3 carry the indicator and the
dated surfaces.

#### What shipped

* **`pm_projects.status` widens to the union** `queued · active · on_hold · stopped · done ·
  archived` — the expand half of R6. `archived` is *retained in the CHECK* so the pre-restart
  gateway keeps validating through the deploy window, and its removal is a named later release
  rather than folklore. **`core.RUN_STATES`** is the axis proper (five values); **the write
  path validates against RUN_STATES, not PROJECT_STATUSES**, so a caller cannot PATCH a project
  to `status='archived'` without stamping `archived_at` and so recreate the very defect D-PM-25
  removes.
* **`archived_root_id`**, and it is the reason archive is reversible. `POST /nodes/{id}/archive`
  stamps the project's whole subtree with the id of the project the user actually archived;
  `POST /nodes/{id}/unarchive` clears exactly the rows carrying that id. A subproject somebody
  had **already** archived on its own keeps its own origin and therefore *survives* the parent's
  restore. Unarchiving a project that an ancestor swept in is **refused with the ancestor named**
  — the `_refuse_lifecycle_on_child` move, keeping the incoherent state (a visible subtree inside
  a filed one) unreachable rather than documented.
* **The read path derives, and costs no task write.** `filters.build_task_filters` gains
  `EXISTS (… pm_projects p WHERE p.id = t.project_id AND p.archived_at IS NULL)` under the
  **existing** `include_archived` flag rather than a second one.
* **One predicate, three call sites** — `core.is_runnable` / `runnable_project_clause`,
  consulted by the lifecycle sweep, the recurrence spawn and the agent dispatch.

#### 🔴 The finding: the lifecycle sweeper would have cancelled paused work

`run_lifecycle_sweep` walked every root with a policy and moved stale OPEN tasks into the
closing lane with **no project-status predicate at all**. Harmless only while `status` meant
nothing — which is exactly how long it had been. The moment `on_hold` becomes real, a project
paused for a quarter under a three-month close policy has its whole backlog moved to
`cancelled` by `system:workflow:<id>`, **through `apply_status_transition`**, so `completed_at`
is stamped, the `status_change` activity is written and recurrence fires. It is
indistinguishable from a person having done it, which is why nobody would find it for months.

Two smaller ones of the same family: recurrence would keep minting occurrences into a paused
project, and `pm.task.assigned` would keep putting agents to work in one.

⚠️ **One subtlety the recurrence guard gets right and an obvious implementation gets wrong:**
the skip does **not** stamp `recurrence_spawned_at`. The ended-series path *does* stamp it, and
copying that would silently kill the series at the moment somebody paused the project. A pause
is not an end. Fenced live: *"the series is NOT stamped dead by the pause"*.

#### Verification

**R8 — a real Postgres 16, ladder replayed 01→171 into a throwaway database.**

* `tests/live/live_ws27bg.py` — **27 checks, all green**, driving the real endpoint functions
  (`archive_node`, `unarchive_node`, `list_tasks`, `run_lifecycle_sweep`, `spawn_successor`,
  `agent_dispatch.on_event`).
* **Every automation check is run TWICE** — once `on_hold` (must not fire) and once `active`
  (must fire). A guard that refuses everything passes a one-sided test, and three of these are
  one-line `continue`s.
* **Mutation-measured, five mutants, each caught:** remove the sweep guard → the paused check
  goes red while the active one stays green · remove the recurrence guard → three red · remove
  the dispatch guard → two red · drop the `EXISTS` from the read → the archived task reappears ·
  **replace the subtree CTE with `id = :pid` → four red**, which is the check that a
  quietly-non-recursive walk cannot pass.
* **The backfill was verified on rows that actually exist**, not asserted empty: a database
  built to 170, seeded with two `status='archived'` projects — one with a prior `archived_at`,
  one without — then migrated. Both moved to `on_hold` + self-stamped origin; the one with a
  real 2024 filing date **kept it** (the `coalesce`, doing its job) rather than having it
  overwritten with `now()`. A second replay changed nothing.
* **Plan (R8):** the new `EXISTS` plans as a Hash Join / Nested Loop over
  `idx_pm_tasks_project_id`, **never a per-row `SubPlan`**, which is what the live check
  asserts. ⚠️ Deliberately a claim about SHAPE, not duration — a timing taken on a handful of
  rows is not a measurement (WS-27be's lesson).
* **Under FORCE ROW LEVEL SECURITY too.** The generated phase-4 policy set was applied and all
  27 checks re-run green; all 21 `pm_*` tables verified carrying `organization_id` + RLS +
  FORCE + a policy. Not required by the current board state (phase 4 is not enabled), and worth
  knowing before it is.
* Hermetic: `tests/unit/test_projects_run_state.py`, **17 tests**, including the mirror test
  that **reads the CHECK out of the migration** rather than restating it — the lesson
  `ACTIVITY_TYPES` records in its own docstring, after a hand-mirrored vocabulary 422'd every
  file upload while 25 tests stayed green. Mutation-measured: a value added to the tuple but
  not the CHECK goes red; `archived` smuggled back into `RUN_STATES` goes red.
* Full suite: **5999 passed, 51 skipped** (`-k "not memory_integration and not calendar"`).

#### ⚠️ Owed, and not fakeable from here

* **The backfill population.** Reading the live database is owner-gated reach (§6), so the
  count of `status='archived'` rows on prod is **asked of the owner at review**. The migration
  is correct at zero rows and at ten thousand; what cannot be produced from here is the number.
* **The contraction** that drops `'archived'` from the CHECK and from `PROJECT_STATUSES` — a
  later release, trigger named in the migration header.
* Slice 2 (indicator, picker, the project control surface that does not exist yet) and slice 3
  (overdue across four predicates, My Work, calendar/timeline honest overflow).

#### 📌 A process note worth keeping

`git checkout` was used to revert a *mutation* in `tree.py` and reverted **the whole file**,
because that file — unlike the other four — had no backup taken first. Caught immediately by
`git diff --stat` (the file had vanished from the change list) and re-applied. The lesson is
the cheap one: when mutation-testing, back up every file you are about to mutate, and diff the
tree afterwards rather than trusting that the revert did what it looked like.

---

### 11.36 WS-27bg slice 2 — the indicator, and the control surface that did not exist (built 2026-08-13)

Frontend only — **no migration, no API change**; slice 1's endpoints are the ones consumed.

#### What shipped

* **`PROJECT_STATES` in `src/lib/statusAccent.ts`** (D-PM-27) — label, hue and glyph per run
  state, a **closed lookup** that never falls through `resolveHue`. `active` → green,
  `on_hold` → amber, `stopped` → red, `queued` → gray, `done` → blue, labelled *Ongoing* and
  *Paused* over their stored values (D-PM-25).
* **`effectiveState` in `app/projects/lib/tree.ts`** — a node's state is its own, or the more
  restrictive thing an ancestor says, computed **on the way down the render** and written
  nowhere (D-PM-26). It returns `{state, inherited}` rather than one value, which is what lets
  the tree draw a pause somebody set *here* differently from one it merely inherits.
* **The indicator, in front of every project row**, replacing the generic folder glyph — in a
  tree where every row is a project, "this is a project" was carrying no information and the
  state is worth the slot. Inherited states draw at half emphasis and say so in their label.
* **`app/projects/lib/projectMenu.ts` + a right-click menu on the tree** — the run-state picker
  and Archive/Unarchive, on the promoted `ContextMenu` (WS-27bd). Pure builder, `themedIcon()`
  conversion at the surface, matching `taskMenu.ts`'s split exactly.
* **The actions run on the promise-bound toast** (WS-27ak slice 3's form) and re-read the tree
  rather than patching rows optimistically — archiving stamps a whole subtree server-side and a
  state change alters what every descendant *effectively* is, so the set of rows a write touches
  is not knowable from the row that was clicked. The archive toast reports `open_tasks`, which
  is the warning D-PM-26 asks for.

#### 🔴 Narrowed, with the reason stated rather than the item dropped

* **Delete is deliberately NOT added to the menu.** §9.8.4 asked that "archive is the default
  affordance and delete is deliberately harder to reach". `DELETE /nodes/{id}` is an
  unrecoverable cascade over the subtree, every task and every grant, and it has **never had a
  control in the UI** — so that criterion is satisfied most strongly by adding the reversible
  action and leaving the irreversible one exactly as unreachable as it was. Putting both into a
  menu people are still learning is how somebody loses a department.
* **The bulk close on Stop is deferred.** D-PM-26 says stopping should *offer* to close open
  tasks. That is a modal, a bulk call and a count shown before agreement — a slice, not a menu
  item, and smuggling it in as a side effect of a state change is the exact shape D-PM-26
  forbids.
* **Rename was still owed here — ✅ SHIPPED 2026-08-25 in the slice-2 remainder.** It was not in
  §9.8.4's done-when and is **not built in this slice**; `ws-27bg-project-rename` (**PR #47**,
  built 2026-08-21) adds it: inline on the tree row rather than in a dialog, matching
  TagManager's rename idiom (§9.11). Frontend only — no migration, no API change, because
  `PATCH /projects/nodes/{id}` already accepted `name` and already listed it in
  `_TRACKED_PROJECT_FIELDS`, so the rename lands on the project timeline unaided. Fenced by
  5 cases in `projectMenu.test.ts` and 8 browser cases in `e2e/project-rename.spec.ts`.
  ⚠️ **The browser suite is not CI-wired** — nothing runs `e2e/` at all (H-27), which is the
  same gap this section's four-theme sweep depends on.

#### Verification

* `tsc` **0** · `next build` **0** · vitest **89 files / 2007 tests**, up from the 1983 baseline
  (+24: 9 `projectMenu`, 7 `statusAccent`, 8 `tree`) · conformance suite green, **no new
  baseline entries**.
* 🔴 **The four-theme sweep is FENCED for this surface, not promised.**
  `e2e/project-state.spec.ts` — **10 cases, all green** — drives a routed tree fixture in real
  Chromium under **Fluent, Material and Graphite** and asserts *five states resolve to five
  distinct computed colours*, that none is transparent, that an inherited state renders at
  reduced opacity, and that the five **glyph paths** differ so the state survives a reader who
  cannot separate the hues. Every slice since WS-27am owed this pass and several skipped it;
  this is the first Projects surface where it is a test rather than an intention.
* **Mutation-measured, four mutants, each caught:** route the project map through `resolveHue`
  → the D-PM-27 fence goes red (2 tests) · give two states the same glyph → red · stop deriving
  the inherited state → red (2 tests) · **make `on_hold` green in the vocabulary → the browser
  spec goes red in all three themes**, which is the one that proves the sweep discriminates
  rather than merely passing.

⚠️ **The unit tests assert a hue NAME; only the browser spec proves two names paint
differently.** That distinction is the whole reason the Playwright case exists: `statusAccent`'s
own header records a colour column that was stored correctly for months while every lane drew
the same grey, and no unit test could have seen it.

---

### 11.37 WS-27bg repair — the three guards disagreed about WHICH project (found and fixed 2026-08-13, after #437 merged)

**A defect in merged code, found by re-reading slice 1 before building slice 3.** No migration.

#### What was wrong

Slice 1 gave the three automation paths one predicate — and then handed each of them a
*different row*:

| Path | Read | Effect |
|---|---|---|
| `run_lifecycle_sweep` | the **ROOT** project, then acted on the whole subtree by `root_project_id` | read too **high** |
| `spawn_successor` | the task's **immediate** project | read too **low** |
| `agent_dispatch.on_event` | the task's **immediate** project | read too **low** |

So the subtree in between was governed by nobody, and it failed in **both** directions:

* a task in an **active subproject** beneath a **paused root** was correctly skipped by the
  sweep and **still spawned successors and still dispatched agents** — while `ProjectTree`
  drew that very subproject as *"Paused — inherited from a parent project"*. **The product
  said paused and the automation ran.**
* a stale task in a **paused subproject** beneath an **active root** was **swept and closed
  anyway**, because the sweep's candidate query selects by `root_project_id`.

Reproduced on a real database before any fix was written, rather than argued from the code.

#### The fix

`core.is_runnable_with_ancestors(db, project_id)` — one `WITH RECURSIVE` walk up the parent
chain reduced by `bool_and`, i.e. *"the most restrictive ancestor wins"* as SQL. It is the
server's copy of `app/projects/lib/tree.ts::effectiveState`, which slice 2's UI already used.
Consumed by both guards; the sweep's candidate query gains the same predicate as a `NOT EXISTS`
so **every task earns its own verdict from its own project's chain** rather than inheriting the
root's. Still derives, still writes nothing (D-PM-26). `is_runnable` survives for the
single-row case and now says in its docstring that it knows nothing about ancestors.

#### 🔴 The fence was DEAD on its first version, and only a mutation found it

The four new live checks passed **with the recursion deleted**. The fixtures (`T_REPEAT`,
`T_AGENT`) sat directly in `ROOT`, so pausing `ROOT` paused their own *immediate* project and
the ancestor walk was never exercised at all. Moving them into the grandchild — and re-arming
the series, which an earlier check had stamped — makes the mutation turn both checks red.

This is the second time in this workstream that a fence looked like coverage and was not (the
Toast slice's keyed-re-fire assertion was the first). Both were found the same way: **by
breaking the code on purpose and checking the test noticed.** A test written after the fix,
never run against the bug, asserts that today's behaviour is today's behaviour.

⚠️ A second, smaller test defect the same run: direction 2 initially read `T_STALE`'s closure
from **an earlier check's sweep** rather than its own. A fixture reused across checks must be
returned to a known state, or the later check measures the earlier one's leftovers.

#### Verification

* `tests/live/live_ws27bg.py` — now **31 checks green** (27 + 4), against real Postgres.
* Both directions asserted, each with its paired positive control (*"and the SAME task IS
  swept once its own project is running"*), because a guard that refuses everything passes a
  one-sided test.
* **Mutation-measured twice**: deleting the recursion left the first version green (fence dead),
  and turns the repaired version **red on both checks**.
* Hermetic: the fake was taught the chain query explicitly rather than left to fall through.
  ⚠️ Falling through would have answered `None` → `False` → *"nothing is ever runnable"*,
  silently disabling recurrence and dispatch across the whole suite while every assertion still
  read as a real refusal. `tests/unit` 5999 passed / 51 skipped.

---

## 12. The 2026-08-24 re-cut — ClickUp out, Tasks in (D52 · D53)

**Status:** owner directive 2026-08-24, recorded as **D52** and **D53** in
`work_plan.md` §3. Board row **WS-39**. This section is the owning statement for
both; where it disagrees with §6, §7 or §11, **this section wins**.

### 12.1 What the owner asked for

> *"Remove all the ClickUp integrations. The projects app becomes the ClickUp clone
> itself, and we don't have to have an external integration. We have a full-blown
> project management app within Metorite itself."*
>
> *"The projects app and the tasks app are tightly coupled to each other. The tasks
> app is just the personal view of the company-wide projects. And in addition, the
> tasks app has personal tasks that are not configured in the projects app because
> they are specific only to the person."*

### 12.2 What this SUPERSEDES in this spec

| Section | Fate |
|---|---|
| **§6 Integrations — "bind, don't rebuild"** | **Superseded for ClickUp only.** The principle is untouched and still governs Zoho, Gmail and every future connector. ClickUp is no longer an integration of any kind. |
| **§7 The migration path — coexistence, inversion, retirement** | **Superseded whole.** Every phase of it (§7.1 import, coexistence, last-import-wins on ClickUp-sourced fields, the inversion, the retirement) presumes an external system of record. There is none. |
| **§8 D-PM-10** (owner confirms the Space→Center mapping) | **Moot.** There is no import, so there is no mapping to confirm. Kept in §8 as a record of *why* a mapping was owner-gated — the reasoning generalises to any future bulk grant. |
| **§11 ClickUp parity — the measured gap** | **Kept, re-classified as history.** Its verdicts stand and its backlog closed on 2026-08-09; it is the record of what parity cost, not a plan. Do not dispatch from it. |
| **§9.7 sequencing letters `c`, `g`, `h`** | **`c` cancelled** (two-way sync, unbuilt by decision). **`g` reduced** to the two owner acts in `work_plan.md` §6 (c-1)/(c-2). **`h` absorbed into WS-39 S3a–S3c.** |

### 12.3 What Projects becomes

**One store, three lenses.** `pm_tasks` + `pm_task_personal` hold every task in the
product. Three surfaces render them, and none of them is a copy:

| Lens | Route | Question it answers |
|---|---|---|
| **Projects** | `/projects` | What is the company doing, and where does this fit? |
| **Tasks** | `/tasks` | What am *I* doing next? |
| **Calendar** | `/calendar` | When am I doing it? (D54) |

**The property that matters:** completing a task in Tasks completes it in Projects,
at the same instant, because it is one row. There is no sync, no id mapping and no
reconciliation — and there is nothing to *keep* consistent, which is a stronger
guarantee than keeping two things consistent well.

### 12.4 Personal tasks — the part that is genuinely new to read

A personal task is **a private task in the member's own personal project**, not a row
in a second table. The owner's "tasks specific only to the person… that particular
person might not want to deploy to the overall project management app" is answered by
two existing mechanisms and no new one:

1. `POST /projects/my/project` creates (or adopts) the caller's personal project.
2. The task is written with `visibility = private` — D12's model, unchanged.

**Promotion is a move, not a publish.** Deciding a personal task *should* be on the
company board is `PATCH` of `project_id` + `visibility`. The row keeps its id, its
history, its comments and its attachments, because it never moved stores.

⚠️ **Do not build endpoints for this.** `routes/projects/personal.py` shipped it under
WS-27e / D-PM-6 on 2026-08-06: `/projects/my/inbox`, `/projects/my/project`,
`/projects/my/tasks`, `/projects/my/contexts`, `PATCH /projects/tasks/{id}/personal`.
The overlay table `pm_task_personal` already carries `disposition`, `next_action`,
`context`, `energy`, `time_estimate_mins`, `is_two_minute`, `defer_until` and
`clarified_at` — the GTD overlay, column for column (measured 2026-08-24). What is
missing is a **caller**, not a contract.

### 12.5 The overlay is per-member, and that is not an edge case

Two people assigned one task legitimately hold different dispositions — the person
doing it says `NEXT`, the person who delegated it says `WAITING`. A single column on
`pm_tasks` cannot express that, which is why the overlay is keyed
`(task_id, member_email)`. This is what delegation *is*, and it is the reason the
personal lens is an overlay rather than a filter.

### 12.5a The scheduled block — per member, per D53.7

`pm_task_personal` gained six columns in **migration 187** (WS-39 S3a):
`scheduled_start`, `scheduled_end`, `flexible`, `is_hard_date`, `actual_start`,
`actual_end`.

**They are on the overlay for the same reason `disposition` is**, and it is
worth stating plainly because it is the question every reader asks: two people
assigned one task each block *their own* time for it. A `scheduled_start` on
`pm_tasks` would make one person's calendar a write to everybody's — and the
second assignee's afternoon would simply vanish the moment the first scheduled
theirs, with neither told.

`pm_tasks.due_at` is the opposite case and stays where it is: **one deadline,
shared by everyone assigned**, a fact about the work rather than about anyone's
week. Two axes, two homes, no third.

⚠️ `flexible` is **nullable**. NULL is "never stated" — resolved as flexible by
the reader, deliberately distinct from a member explicitly pinning it, because
"never stated" is what the Ideal Week packer is allowed to fill in.

The read is `GET /projects/my/calendar?start=&end=`, a **half-open** `[start,
end)` window so consecutive weeks tile rather than double-counting a block that
begins exactly at midnight. It reuses `_MY_TASKS_SQL`, so it inherits the tenant
and identity clauses rather than restating them.

Verified against a real Postgres: `tests/live/live_ws39_s3a.sql` (8 checks,
including that the partial index is *chosen*) and `tests/live/live_ws39_s3a_bind.py`
(which caught two would-be 500s the hermetic suite could not see).

### 12.6 Acceptance — WS-39 S1 (ClickUp excision) · AGENT-SAFE

**Status: ✅ BUILT 2026-08-24 · 🔧 REPAIRED 2026-08-25 (repair round 1, PR #91),
NOT merged and NOT deployed.** The adversarial review returned MERGE-AFTER-FIXES on
one finding that this acceptance list did not ask for and should have —
**criterion 8, added below.**

⚠️ **What r1 corrected, because the lesson generalises to every excision:**
*deleting the code does not delete the rows, and it does not delete the UI pointing
at them.* Criteria 1–7 were all met on 2026-08-24 and the integration was genuinely
gone — yet a live `/tasks` would still have shown a permanent "Sync failed" badge
(the scheduler launched a loop per `sync_enabled` `task_accounts` row with no
provider filter, and every cycle 400'd on `build_provider`) and still offered a
"Connect workspace…" button whose only possible outcome was that same 400. Both are
fixed on the branch; the fixes and their fences are recorded in `work_plan.md` §2
WS-39's repair-round paragraph.

**Done when all of these hold:**

1. No ClickUp **integration surface** survives: no connector, no receiver, no
   importer, no OAuth entry, no catalog tile, no settings field, no scheduled job,
   no App-Workshop tool, no workflow tool. ⚠️ **This is deliberately narrower than
   "no `rg -i clickup` hit anywhere", and the four carve-outs are the honest part:**
   - **(a) `src/app/tasks/` — 210 references across 27 files, NOT touched.** That is
     the GTD surface **S3a** re-points onto `/projects/my/*`. De-ClickUping it in S1
     would rewrite code the next slice rewrites again.
   - **(b) `routes/tasks/` connector plumbing (~100 call sites), NOT touched.**
     Unreachable now that the registry is empty; deleted in S3a with the store it
     serves.
   - **(c) the preserved columns and their writers** — `clickup_id` /
     `clickup_kind` / `clickup_snapshot` / `clickup_synced_at` / `clickup_user_id`,
     plus `scripts/seed_demo.py`, `scripts/import_hr_people.py`,
     `scripts/reconciler.py` and `infra/seed/hr/hr_structure.json`, which read or
     write them. They go in the D52.3 column-drop release, not before (R6).
   - **(d) migration comments, `learning-resources/`, `docs/*/mockup-*.html`** —
     historical record and teaching material. Rewriting history is not retirement.
2. `apps/services/ingestion/ingestion/sources/clickup/`, `apps/skills/skill-clickup-sync/`,
   `scripts/clickup_sync.py`, `routes/projects/import_clickup.py`,
   `routes/projects/import_tasks.py` and
   `workbench/control_plane/src/app/projects/components/ImportClickUp.tsx` **do not exist**.
3. The `ClickUpProvider` arm is gone from `routes/tasks/providers.py` and no provider
   registry resolves `"clickup"`.
4. `CLICKUP_API_TOKEN` / `CLICKUP_WORKSPACE_ID` / `CLICKUP_WEBHOOK_SECRET` appear in no
   settings object, no integration catalog entry and no OAuth entry.
5. **No migration is added.** D52.3's columns stay; dropping them is `work_plan.md`
   §6 (c-2).
6. Root `AGENTS.md` constraint 8 is amended (D52.4).
7. `uv run pytest` over the affected suites is green with the ClickUp tests **deleted,
   not skipped**, and `npx tsc --noEmit && npx vitest run` is green in
   `workbench/control_plane`.
8. 🆕 **Added 2026-08-25 by repair round 1 — the SURVIVING ROWS are inert and no
   surface offers an action against them.** D52.3 keeps columns; nothing ever said
   the `task_accounts` rows go, and they do not. So:
   - **no background job selects a row whose provider has no connector.** The filter
     lives at the SELECTION seam (`routes/tasks/scheduler.py::_known_providers`,
     consumed by `_enabled_accounts_by_org` and `_read_interval`) and derives its set
     from `providers._CONNECTORS` — the same registry `build_provider` consults, so
     there is one vocabulary and no second list to drift. One structured warning per
     skipped account per **boot**, never per cycle.
   - **nothing writes `sync_status`/`sync_error` for such a row**, which is the
     user-visible half: that pair IS the "Sync failed" badge.
   - **no surface offers connect, sync or schema-refresh for a retired provider.**
     Rows may still be LISTED read-only (they explain where imported items came from)
     with one muted line saying sync is retired.
   **Fence (R7):** `tests/unit/test_h3_rls_promotion_rehearsal.py::
   TestSchedulerBindUnderForceRls::test_a_retired_provider_account_launches_no_loop`
   — seeds a real `provider='clickup'` row on a phase-4-promoted catalog, asserts
   zero loops AND that the badge columns are untouched, with a stub-connector sibling
   row as the control so "zero" cannot be a broken sweep; plus
   `test_the_known_provider_set_comes_from_the_registry`, which proves the vocabulary
   by CHANGING the registry rather than comparing two literals.

**Verification commands:** `uv run pytest tests/unit/test_projects_import_mapping.py
tests/unit/test_projects_import_tasks.py tests/unit/test_clickup_ingestor.py
tests/unit/test_clickup_normalise_dlq.py` must all report **collected 0 items / file
not found** (the files are deleted), and
`uv run pytest tests/unit/test_integration_env_scoping.py tests/unit/test_app_tools.py
tests/unit/test_action_broker.py` must be green with their ClickUp cases removed.

### 12.7 Acceptance — WS-39 S3a (Tasks becomes the lens) · AGENT-SAFE

**Done when:** the `/tasks` frontend issues **no** request to `/api/tasks/items*`;
every task read and write goes to `/projects/my/*` or `/projects/tasks/*`; no code path
writes `gtd_items`; and a task created in `/tasks` is visible in `/projects` under the
creator's personal project **in the same page load**, with no sync step.

**Fence (R7):** a test that greps the `/tasks` app tree for `/api/tasks/items` and
fails on any hit — a structural fence over the whole surface, not an example test,
because the failure mode is *one component left behind*, not *the design was wrong*.

### 12.8 Acceptance — WS-39 S3b/S3c (backfill and drop) · 🔴 OWNER-GATE

Building and R8-testing against scratch databases is agent-safe. **Running either
against a real database is the owner's act** (`work_plan.md` §6 (f)). S3b must be
proven **two-org on real Postgres** before it is offered, and the specific thing to
prove is that a mis-mapped `member_email` cannot publish one member's private task
into another member's lens.

🆕 **MET 2026-08-26 — migrations 189 (backfill) and 190 (drop).**

| Criterion | Where it is proven |
|---|---|
| two-org, on real Postgres | `tests/live/live_ws39_s3b.sql` — 37 checks against the `tenant-scratch` container |
| a mis-mapped `member_email` cannot cross tenants | checks **4a–4g**, including the lens query itself (`MY_TASKS_FROM`'s ownership arm) run as each member |
| ...and the check can actually fail | verified RED by two mutations: cross-tenant org resolution trips **4b**, a wrong `member_email` on the overlay trips **4c** |
| nothing is lost in the move | checks **5a–5i** — disposition, context/energy/estimate, `due_at` on the task (D53.7), the Waiting-For quartet on the overlay (D53.8), `completed_at` |
| the move is re-runnable | checks **8a–9b** — a second pass duplicates nothing, and sweeps a row written between the move and the flag flip |
| every S3c refusal path refuses | `tests/live/live_ws39_s3c.sql` — 22 checks, all four states in one rolled-back transaction |
| building it did not execute the gate | `tests/unit/test_gtd_backfill.py` — 24 structural fences, verified red |

**The acceptance the spec did not ask for, and should have.** Three defects
surfaced that no criterion above names:

* **`gtd_items.deleted_at` is a soft delete**, undoable and hidden from every
  view. Carried over naively it would have **resurrected every task each member
  had deleted**, irreversibly (R6). It maps to `pm_tasks.archived_at`: hidden,
  not destroyed, and still satisfying S3c's "every row accounted for".
* **S3b's own preview view blocked S3c's `DROP TABLE`.** Found by running it,
  not by reading it — and it would otherwise have surfaced while armed and
  mid-cutover.
* **The S3b suite's "self-cleaning" claim was false** until a mutation run
  exposed it: the teardown pattern could not reach the literal `'anonymous'`.

Each is the same shape — a fact about the OLD store that the mapping had to
honour, invisible from the `pm_*` side. A future store retirement should start
by enumerating the source table's soft-delete, its dependents and its magic
values, before designing the target rows.

⚠️ **Still owner-gated, unchanged.** Running either against a real database is
the owner's act. `docs/TASKS_LENS.md` carries the runbook; `H-29` is the queue
entry.

---

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-27 — **Projects app — native project management + ClickUp retirement** *(minted 2026-08-05)*
**State cell (as of the move):** ✅ **a + b + d + e + i BUILT 2026-08-06 · j + k + l + m + n BUILT 2026-08-07** · 🟢 f dispatchable · 🟡 c gated · 🟡 h sequenced
**Narrative (verbatim):** Research pass 2026-08-05: `Paca-AI/paca` v0.11.0 (Apache-2.0 — **patterns adopted, no code translated**; findings + the adopt/adapt/refuse table live in `specs/paca_pm_research_2026-08.md`, reference-only), plus a full-tree ClickUp sweep. **ClickUp today is TWO independent systems** — the Phase-0 graph mirror (read-only, shallow) *and* the per-user Tasks-app connector with a **live broker-gated write path** — so leaving ClickUp is coexistence-sync-then-invert, **not** WS-26's import-and-retire; the constraint-8 inversion is staged and recorded in spec §7. Spine: Paca's two-self-FK hierarchy (departments→projects→subprojects→tasks→subtasks as `pm_projects` + `pm_tasks`, types-as-data with the Epic-root rule), statuses-as-data with a semantic `category` (D-CRM-2 convergence), per-view fractional ordering (`pm_view_task_positions` — what lets People-Center and Center-slice boards order the same task differently), and a single activity spine. **First data-scoped app:** `pm_project_grants` on the shipped `email|group:<slug>|org` vocabulary (D12; sibling of C1's D13, which is unchanged), 404-not-403, and the full-portfolio view gives D14's zero-consumer `data:org:read` its **first consumer**. **Three owner answers recorded 2026-08-06 as D-PM-8/9/10, and two of them changed the build:** **D-PM-8** no portfolio/program layer — grants are the only grouping axis, a cross-department project simply carries several (a `pm_programs` table stays purely additive if wanted later); **D-PM-9** agent edits to ClickUp-linked tasks are treated **exactly like human edits** (*the agent proposed queueing agent-originated pushes for approval and was overruled*) — so during coexistence a mistaken agent edit reaches the live workspace with no human in between while `ACTION_BROKER_ENFORCE` is off; bounded by attribution (`agent:<name>`), timeline-reversibility, and the fact that the enforce flip converts the whole class to queue-on-approval. Read D-PM-9's Cost paragraph before building WS-27f; **D-PM-10** ClickUp Spaces map to Centers **explicitly**, from agent-proposed suggestions (assignee-overlap → name match → EVAL-LOCKED content classification), owner-confirmed, applied as `group:<slug>` grants — and an **unmapped Space still imports in full with no group grant**, staying reachable in `/projects` for `data:org:read` holders and its assignees. This supersedes the "pilot vs all Spaces" framing: scope is now a per-Space decision the plan step surfaces, so a pilot and a full import are one code path. Tickets: **a** schema + `feature:projects` both sides + core API on the `gateway/db.py` seam — **BUILT 2026-08-06** (mig `146_projects.sql`, `routes/projects/` with zero `create_async_engine` calls, 115 hermetic cases + 5 mutants measured red; **not deployed — the migration has not been applied anywhere**) · **b** ClickUp org importer **+ the Space→Center mapping plan** — **BUILT 2026-08-06** (`routes/projects/mapping.py` + `import_clickup.py`; `POST /projects/import/clickup/plan` proposes and writes nothing, `POST /projects/import/clickup` applies the confirmed mapping; 25 hermetic cases, 4 mutants red incl. *applying the suggestion instead of the confirmed mapping*; **neither endpoint has been run — prod execution stays OWNER-GATE, §6**) · **c** two-way coexistence sync — three-way field merge, conflicts logged to the timeline (🟡 **blocked on WS-1's BO-1a + BO-1b, named prerequisites**; enabling push is OWNER-GATE) · **d** UI + Center (app + scope) projections, no forks — **BUILT 2026-08-06** (`src/app/projects/` tree + board + list + task panel + timeline, BFF proxy, nav/access registration, all six Centers linking at the SAME `/projects` path and differing only by `?center=`; 34 vitest cases incl. a registration fence, 6 mutants red) · **authoring landed 2026-08-06** — d shipped a UI that could read and drag but never **create**, so a member could only work with rows a ClickUp import had put there: new department / subproject (from the node, where the parent already is), new task (status not sent — the API picks the project's default), subtask from the panel, and **assignees as chips where an agent and a person share one field** (`lib/assignees.ts`, 17 vitest cases, 7 mutants red). That last one is where D-PM-4 stops being a schema note and is the precondition for WS-27f's dispatch being reachable at all · **e** the personal lens — **BUILT 2026-08-06** and **its shape changed**: `D-PM-6` was revised (owner-directed — *"the personal task manager should be a proper extension … a cohesive whole"*) from a mirror into **one store**. `pm_tasks` is THE task table; private work is a personal project (`pm_projects.personal_owner`); the GTD overlay is **per-member** (`pm_task_personal`, mig `147_projects_personal.sql`) so two assignees can hold different dispositions. Assignment is no longer a sync — the inbox row IS the project row, and completing it there moves the shared status. 31 hermetic cases, 6 mutants red. **Its surface landed the same day** — "My work" above the project tree in the SAME app (`components/MyWork.tsx` + `lib/mywork.ts`, 17 vitest cases, 7 mutants red): capture-first, four work lanes that render even when empty, untriaged counted in the header (the Weekly Review's question, answerable only because dispositions are derived not stored), and a completion checkbox that moves the **shared** status. e had shipped API-only, so the cohesion the revision bought was true in the schema and invisible to a member. One repair it forced: `TaskPanel` read the *selected* project's statuses, wrong for a task opened from My work — now resolved from the task's own root project. **Cost accepted: `gtd_items` becomes legacy and WS-27h retires it** · **f** automation + agent dispatch — **BUILT 2026-08-06** (`routes/projects/automation.py` + `agent_dispatch.py`, the `pm_task` node type in `workflows/engine/`, `PM_EVENT_TOPICS` served by the catalog; 34 hermetic cases, 10 mutants red). Both halves of `workflows_app.md` §13 — **U1** the task-mutation node and **U7** dispatch. The engine imports a transport-free SERVICE, not a route, so an automation's edit is indistinguishable in validation from a human PATCH and lands the same timeline row; **status is named, never keyed** (a graph pinned to one project's status UUID could only automate that project); a `pm_task` node is deliberately **not** write-class (the approval gate is for outward writes — now pinned by a test rather than true by accident); and "already in target state" is asserted to issue **no UPDATE at all**, because `update_row` stamps `updated_at` and a redundant write is invisible in a diff while making a task look freshly touched. Assignment dispatches from an event SINK beside the workflows dispatcher, so a broken agent cannot fail the act of assigning somebody a task, and the handoff activity is committed BEFORE the run starts. **Engine defect found and fixed:** `resolve_value` keeps an unresolvable `{{ref}}` as-is at run time by design and `{{trigger.missing}}` passes the publish gate because its *root* is legal — the literal would have reached Postgres as a would-be uuid and returned "Task not found", pointing the maker at the wrong thing · **i** attachments — **BUILT 2026-08-06** (mig `150_projects_attachments.sql`; 25 cases, 10 mutants red): one file store (`gtd_attachments` reused, upload rules imported) with a thin `pm_task_attachments` join that carries the ACCESS decision, so a file is readable by whoever can see the task rather than only its uploader; no attach-by-id endpoint exists, because naming an arbitrary attachment id would let a caller attach somebody else's private capture to their own task and read it back. **Caught before shipping:** the projects BFF proxy re-serialised every POST as JSON, so a multipart upload would have reached the gateway with NO FILE while still answering 201 · **j–r** the rest of the ClickUp-parity gap, measured against the built tree and sequenced in spec §11 (attachments, notifications/@mentions, filters+saved views, custom fields, tags, bulk edit, recurring, dependency UI, calendar, search) — all 🟢, and **n (bulk edit) gates g — and n is now BUILT (2026-08-07), so that dependency is SATISFIED**: an import that cannot be re-triaged in bulk is one somebody abandons halfway, leaving two live systems, which is the state the retirement exists to end. g itself stays 🔴 OWNER-GATE for its own reasons (§6); this changes the prerequisite, not the gate · **g** cutover + retirement inventory (both ClickUp systems) + token revoke + the root-`AGENTS.md` constraint-8 amendment (🔴 OWNER-GATE end-to-end) · **h** `gtd_items` retirement — the cost D-PM-6's revision accepted: union read, row migration into `pm_tasks` + `pm_task_personal`, then `items.py`'s 27 owner-scoped predicates retire with the table they scope (🟡 after e; the data move is 🔴 OWNER-GATE — it rewrites the owner's live task store).. **j BUILT 2026-08-07** (mig `152_projects_notifications.sql`, `routes/projects/notifications.py`, the header bell + mention picker; 39 hermetic + 27 vitest cases, 10 mutants red): closes §11.2's second gap — *"assignment is silent"*. **Three rules decide who hears**, each the whole reason for a rule: never the actor (a bell that pings you about your own click gets muted, and a muted bell notifies nobody about anything); never an agent (they are handed work by the WS-27f dispatch sink, so a row addressed to one sits unread forever inflating a badge nobody can clear — enforced in Python AND by a CHECK); and **never somebody who cannot open it**, which is the security property: the notification carries the task's TITLE, so delivering one outside the grant closure leaks it and lands them on a 404. That third rule needed `resolve_visibility_for`, which answers for a THIRD PARTY — `resolve_visibility` reads a `UserContext` and the recipient of a mention has no request in flight — by reading the tables `/auth/me` reads and handing them to the **real** `build_access`, so wildcards and allow/deny overrides resolve identically on both paths. Notifications are written **inside the transaction**, not emitted on the bus: `emit` swallows failures by construction so a broken workflow can never fail a task edit, which is right for agent dispatch and wrong here. A mention is an **address, not a name**, because 148 dropped `UNIQUE(name)` — `@Priya` has no answer. **Two bugs found on the way in, both shipping at the time:** every project-task file upload was answering **422** (`ACTIVITY_TYPES` never learned 150's `attachment`; all 25 attachment tests passed because they monkeypatch `record_activity` — the seam under test was mocked out), fixed with two tests that READ the migrations; and `/projects?task=<id>` did nothing, though the People Center has linked there since WS-28b. **WS-27b's UI BUILT 2026-08-07** (`components/ImportClickUp.tsx`, `lib/importPlan.ts`; 18 vitest cases, 3 mutants red): the importer shipped with WS-27b and was **unreachable from the product** — the empty state named "import a ClickUp workspace" and no control anywhere did it, so a new install stayed empty and the only route to real data was curl. Three steps, only the last of which writes: Preview (`/plan`, reads the tenant) → Dry run (`dry_run:true`, exercises the flattening) → Import. **The mapping stays the owner's act (D-PM-10)** and the UI is built so it stays one: the suggestion is pre-filled and shown beside its confidence IN WORDS ("a guess — check it", not `0.45`, because a bare number invites acceptance without looking), and a CONFIRMED mapping always beats a fresh suggestion so a re-run never silently re-maps a Space somebody already ruled on. Unmapped Spaces are a notice rather than a blocker, matching the importer. ⚠️ The gate is unchanged and is now exactly one click: building this was agent-safe, pressing Import is the owner's act, and no agent has run either endpoint against production. **The Tasks-app mirror path BUILT 2026-08-07** (`routes/projects/import_tasks.py`, `POST /import/from-tasks`; 43 hermetic cases, 7 mutants red) — owner-directed: *"just show up all the data that is there in the Tasks app inside the Projects app"*. A SECOND importer rather than a flag: the ClickUp one needs a live token, spends LLM budget and demands a Center mapping BEFORE anything is written, which is backwards for "show me my work today". This reads `gtd_projects`/`gtd_items` — the mirror already on the box — so no API call, no token, no model spend, and it works when the connector is stale. One named department, with the real ClickUp shape beneath it — **Space → Folder → List** rebuilt as projects, each carrying its own `clickup_id`/`clickup_kind`, so promoting a Space node is how the department split happens later. **Verified against a real Postgres** (full migration set + seeded mirror), which found THREE defects the hermetic suite could not: `gtd_projects.space_id` is LOCAL-only so the Space was always NULL; `pm_projects` has no `clickup_snapshot` column, so the first real click would have 500'd (this shipped in #393 and was fixed before anybody pressed it); and the preview under-counted 4 vs 7 because container nodes were only tallied on the write path. The root IS org-granted (same act as `create_node`, narrower than bulk-granting a tenant). Pinned: nothing outside `pm_*` is written; only `source <> 'LOCAL'` rows are read (a personal capture must not be published to a shared board); the provider's own status names are kept; an orphaned task is COUNTED, not dropped. **Mutation found a real gap**: deleting `dry_run` from the write guards left every test green, because on a FIRST dry run `root_id is None` blocks the write anyway — on a SECOND one the department exists and `dry_run` is the only protection. That is the realistic case (preview → import → preview again) and it now has its own test. **k BUILT 2026-08-07** (`routes/projects/filters.py`, `lib/grouping.ts`, `components/FilterBar.tsx`; 34 hermetic + 24 vitest cases, 13 mutants red, and 23 checks against a REAL Postgres) — closes §11.2's third gap, the one whose name was the sentence *"my open bugs in Ops, grouped by assignee"*. **ONE filter builder serves both the list endpoint and saved views**, because a saved view is nothing but a stored set of these filters and two implementations would drift — a *saved* view showing a different set than the same filters typed by hand is the one thing it may not do, so a test compares the two outputs directly. **Every filter is a WHERE clause**: paging happens in SQL, so a filter applied in Python after `LIMIT` returns short pages, and *"page 2 is empty but there are 40 more"* is a bug people work around for months instead of reporting. **`overdue` means past due AND still open** — a finished task with a past due date is done, and permanent red is how a board teaches people to ignore red. **An unknown category is a 422 naming the five real ones**, not an empty board a client reads as "this project is empty". Unknown config **keys** are DROPPED while a bad **value** falls back, because those are different failures: a view is a preference written by an older client, so refusing one over an unrecognised key would make every deploy a migration of everybody's saved views, whereas rendering still has to produce something. On the board, **a task with two assignees appears in BOTH columns** (it is both people's work; picking one hides it from the other, so the header counts tasks not group sizes), **empty status lanes are kept** while every other grouping drops empties (a missing "In progress" column reads as "no such state", not "nothing in progress"), and **dragging is offered only when the columns are statuses** — a drop writes the field the columns represent, and status is the one that is a plain PATCH, so a card that can be dragged into a column which cannot accept it and snaps back is worse than an honestly static column. `toConfig` is deliberately NOT `toQuery`: a query string carries only text so `toQuery` writes `"true"`, and `fromConfig` refuses a string where a toggle belongs, so a view built from query shape would come back with every toggle silently cleared. The project's **order-bearing board is withheld from the chips** — `tree.py` seeds one `board` view per project and it owns every `pm_view_task_positions` row, so offering its ✕ would offer to delete every hand-arranged position; saved views sit at position 300 above the seeded pair and `orderBearingView` is one function used by both the drag handler and the delete guard. **A FIFTH live bug, found the same way as the previous four:** `due_before` was `CAST(:due_before AS timestamptz)` with the raw query-string value — asyncpg infers the parameter's type FROM that cast and then refuses to encode a `str`, so the query never reached Postgres and **`?due_before=…` answered 500** while the hermetic fake, which agrees with whatever SQL it is handed, stayed green. `parse_when` parses on this side and binds a real `datetime`; garbage is a 422 that says what was expected; a naive value is read as UTC rather than inheriting the connection's TimeZone. Two tests — one on the bound value's TYPE, one refusing any `CAST(:param AS timestamp…)` anywhere in the builder — so the next `after=` filter written the obvious way fails in CI instead of in front of a member. **l BUILT 2026-08-07** (mig `155_projects_custom_fields.sql`, `routes/projects/custom_fields.py`, `lib/customFields.ts` + the panel block and the Fields dialog; 47 hermetic + 36 vitest cases, 23 mutants red, 35 checks against a REAL Postgres) — ClickUp's signature feature, and the shape §5's non-goals already recorded as the additive path: **definitions in a table, values denormalised onto the task as JSONB keyed by `field_key`**. NOT a row per (task, field): that is the textbook EAV answer and costs a join per field on every board paint — five fields across two hundred imported tasks is a thousand rows to gather and re-pivot, per render — whereas the JSONB column arrives with the task for free. **The cost is stated rather than discovered**: a value is not referentially tied to its definition, so the DATABASE cannot stop a key no definition owns from being written, and that guarantee moves into Python. Hence the validation IS the feature: an unknown key is a **422 not a silent drop** (a typo that no-ops looks exactly like a save); a patch **MERGES** (a client that knows three of five fields must not wipe the other two — and an older client, or an automation written before a field existed, is precisely that client); an explicit **null CLEARS the key** rather than storing a null, because it is the only way to express "unset this" and a stored null makes "never filled in" and "deliberately emptied" one value in every filter; and **`true` is not the number 1** — `isinstance(True, int)` is True in Python, so the coercers are one-per-type in a dispatch dict specifically so the boolean check can never drift below the number one. **The deliberate departure from Paca:** its research notes record "deleting a definition does not clean task data" as an accepted cost; it is NOT accepted here, because a key left in the JSONB is invisible — no definition means no column, no form row, no filter — until somebody recreates the name and every old value resurfaces carrying the new meaning. The cleared count is reported (R7/R8). Two things a definition may not change once values exist, both because the stored values would stop meaning what they say: **`field_key` is never editable** (it is the identity every value is filed under) and **`field_type` is a 409 naming the count** (text→select cannot re-interpret what is already written); dropping a select option some task holds is refused the same way, adding one is free, and the UI shows the derived key while the name is still being typed since that is the last moment anybody can change it. **Custom fields are REVERTIBLE, which is what makes them first-class:** `patch_task` folds a custom edit into the SAME `field_change` activity under `custom.<key>` rather than inventing an activity type — `record_activity` refuses a type the CHECK does not list, the trap that made every attachment upload answer 422 — and revert restores by **merging onto what the task holds NOW**, never writing back the whole object, since another field may have been edited since and replacing the blob would silently undo that too. **A bug the ticket's own tests caught before it shipped:** `changedValues` compared a form's boolean against a `null` baseline, so a task with an unanswered checkbox sent `open:false` on EVERY save and posted a timeline entry for an edit nobody made — a checkbox has no "unset" state to render, so `false` is its baseline. **And a fence that was quietly a subset check:** `test_projects_routes` asserted a LIST of mounted paths, which catches the module somebody remembered to add a path for; it now also reads the package directory and asserts every module declaring a `@router` route is imported by `__init__.py` — the C1 trap where a missing import mounts nothing while every direct-call test still passes. Verified by deleting the import and watching it fail. **m BUILT 2026-08-07** (mig `156_projects_tags.sql`, `routes/projects/tags.py`, `lib/tags.ts` + the panel picker, the filter row, a `tag` board axis and the Tags dialog; 31 hermetic + 37 vitest cases, 16 mutants red, 30 checks against a REAL Postgres) — the row the research notes left open ON PURPOSE: `paca_pm_research_2026-08.md` row 13 REFUSED Paca's model (*"a bare jsonb string array on tasks. No registry, no colors, no rename/merge — the weakest part of Paca's model"*) and §5 shipped `pm_tasks.tags TEXT[]` in its place with a registry named as additive later. **The array STAYS** — a join table would add a row per tag per task and a join to every board paint, to buy referential integrity this app enforces in one place, whereas the array arrives with the task and its GIN index (146) already answers "tagged X". What the registry buys: **one spelling per tag** (identity is case-INSENSITIVE via a unique index over `lower(name)` so two racing requests cannot create both, display is case-PRESERVING, and the task's array stores the REGISTRY's form — which is what makes "filter by bug finds all of it" true rather than aspirational, and what lets a rename be one statement); **rename**; **merge**; and a **colour**. **Applying an unregistered tag REGISTERS it** — refusing would make tagging a two-step errand (leave the task, create the tag, come back), which is how tagging gets abandoned, and an abandoned tag set is worse than a messy one. **The cost is stated: every typo becomes a tag** — which is exactly why merge is here and is not optional, and why the picker SHOWS the moment of creation rather than minting silently. **A rename onto an existing name is a 409, not a silent merge**: different operations, different outcomes, and one of them destroys a tag — quietly doing the destructive one because the names collided is what stops people using a rename button. **A task carrying BOTH tags ends a merge with the target ONCE** — the case that is easy to get wrong, and getting it wrong leaves a duplicate that renders twice and survives the next merge too; `merged_tags` is pure so that case is asserted directly, and the rewrite runs over affected rows in Python rather than as an `array_replace`, which would leave the duplicate. **TWO tag filters** because both questions get asked and neither answers the other: `tags` is ANY (`&&`) and `tags_all` is ALL (`@>`); collapsing them would silently pick a meaning, and with three tags the answers differ by almost everything. On the board a task with three tags appears in three columns, the same honesty as two assignees appearing in both theirs. **The migration backfills AND rewrites data, deliberately and narrowly:** `tags` has been on `pm_tasks` since 146 and the import path writes them, so an empty registry beside a tagged corpus means the first rename finds nothing; the winning display form is **the spelling people actually use** (most frequent, ties broken deterministically — `min()` alone would canonicalise 400 "Bug" to a single stray "BUG"), and task arrays are then made to agree, only ever swapping one CASING of a tag for another, with the count reported in a NOTICE. **A bug the live run caught in that block:** the canonicalisation used the implicit-comma `FROM pm_tasks t, unnest(t.tags) ... LEFT JOIN` form, where the LEFT JOIN binds only to `unnest(...)` and `t` is not in scope for its ON clause — the migration aborted with "invalid reference to FROM-clause entry for table t". Rewritten as `CROSS JOIN LATERAL … WITH ORDINALITY`, which also fixed a second problem the first version would have shipped: `array_agg(DISTINCT ...)` sorts by its own expression, so every task's tag list would have come back alphabetised. **n BUILT 2026-08-07** (`routes/projects/bulk.py` → `POST /projects/tasks/bulk`, `lib/selection.ts`, `components/BulkBar.tsx` + checkboxes on board and list; 35 hermetic + 32 vitest cases, 16 mutants red, 34 checks against a REAL Postgres; **no migration**) — **the ticket §11.3 names as gating g, so that prerequisite is now satisfied.** It **reuses `automation.apply_task_patch` rather than growing a second writer**: that service exists because WS-27f needed an edit indistinguishable in validation from a human PATCH, and a bulk endpoint with its own field handling would be a third opinion about what a task edit is. Tags go through the same registry the panel uses, because the registry cannot be true if bulk is a second door into the array. **Status is named, never keyed — load-bearing here rather than stylistic**: a selection spans projects and a status id belongs to one root, so `status_id` for fifty tasks across three projects puts two thirds of them in a lane that is not theirs; it is a 422 with its OWN message, because it is the mistake somebody makes by copying a single-task PATCH body and "unknown field" would not explain why the thing that works on one task is refused on fifty. **Assignees and tags are ADD/REMOVE, never SET** — "assign these to Priya" means ALSO Priya, and a replace wipes every individual assignment the fifty tasks already carried; the destructive spelling is absent rather than discouraged. **Shape validated once, outcomes per task**, because those are different failures: an unsettable field is the same mistake for all fifty (422 before any write), while a status name present in one project and absent from another is a fact about THAT task — failing the batch for it makes a mixed selection unusable, which is precisely the selection somebody makes after an import. **An invisible task is SKIPPED, not an error** (R5: per-id reporting says exactly what a per-id 404 says, and aborting would let a caller probe for existence). **One transaction** — a re-triage that half happened is harder to recover from than one that did not, because nobody can tell which half. **Re-asserting a value is not a change** (`moved_people` is pure so the claim is asserted directly): fifty tasks already Priya's would each gain a timeline entry saying nothing, and a count nobody can trust is worse than no count. **ONE notification per person per batch, not one per task** — being handed fifty tasks should ring once and say fifty; fifty bells is a bell people turn off, which is WS-27j's own argument applied to the case that would have broken it. In the browser the selection is **pruned whenever the filter changes** (select forty, narrow to three, press Done must not act on thirty-seven nobody can see), a shift-click ranges over the board's ON-SCREEN order, a two-assignee card drawn in two columns counts once, and the outcome line names every category including the boring ones. **AND IT UNCOVERED A SHIPPED WS-27j BUG** (spec §11.12): `notifications.deliverable` probed only `project_clause('t.root_project_id')` while `core.task_visibility_clause` — whose docstring warns a second implementation "would drift the moment one is edited alone" — carries TWO ways in. So (1) anybody assigned work in a project they hold no grant on was judged undeliverable: they could OPEN the task, the assignment notified nobody, and the response told the assigner they could not see it — the silent assignment WS-27j exists to end, still open for the most common case in a grant-scoped app; and (2) scoping to `root_project_id` ALSO missed a grant made on a SUBPROJECT — the old test asserted that scoping on the argument that "probing `project_id` would miss a grant made on an ancestor", which is BACKWARDS, as a real-Postgres run showed: the closure is recursive and expands DOWNWARD. Fixed to use the shared clause; the test that encoded the wrong reasoning now records why it was wrong. **A fake collision the fix exposed:** `FakeDB` matched the rule-3 probe by substring, and the new clause embeds both `pm_task_assignees` and the closure's `UNION` — exactly the AUDIENCE branch's fingerprint — so `deliverable` got a list of assignees where it expected a visibility answer and four tests failed for a reason unrelated to the code under test. A fake that dispatches on substrings needs fingerprints that are SPECIFIC, not merely present

**Corrections applied 2026-08-09:**
- a/b/d/e/f/i/j/k/l/m/n are MERGED TO MAIN (#390, #393, #394, #398 + fixes) — the 'BUILT (branch)' wording was stale
- the state cell's 'f dispatchable' contradicted the body's 'f BUILT 2026-08-06' — f is built and merged
- the WS-27j notifications.deliverable clause bug (probes project_clause instead of core.task_visibility_clause) is an OPEN defect recorded at spec §11.12, found by n's tests.
