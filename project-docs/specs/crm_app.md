# CRM App — Master Plan (native CRM; Zoho CRM retirement path)

> **Product:** Metorite · **Feature:** CRM (Sales Center's primary module) · **Created:** 2026-08-05
> **Status:** 🟢 **WS-26a + WS-26b + WS-26c BUILT AND DEPLOYED** (2026-08-05/06) ·
> 🟢 **WS-26d read half BUILT** (2026-08-06) · 🟢 **WS-26d-email BUILT, MERGED (PR #392)
> AND DEPLOYED 2026-08-07** — the address index landed as migration 154, applied on prod.
> 26a–c merged together on branch `ws-26-crm-app`.
> **26a** — migration `144_crm.sql` (§3.1–§3.10), `feature:crm`
> registered on both sides, `gateway/db.py` engine seam with `routes/tasks/core.py` converted
> as its proof, and the `routes/crm/` API (§4 minus `import_zoho.py`) live behind the feature
> gate.
> **26b** (merged from `ws-26b-zoho-sync`, PR #363) — the two-way Zoho sync: `list_leads` +
> `list_deleted` on the read client, the single write client
> `ingestion/sources/zoho/writer.py` (one caller, grep-asserted), migration
> `145_crm_zoho_sync.sql` (dirty columns + `crm_zoho_tombstones` + `crm_sync_cursors`),
> `routes/crm/{import_zoho,sync_zoho,broker_handlers}.py`, the `crm.zoho_*` Action-Broker
> handlers registered from `main.py`, and `CRM_ZOHO_SYNC` (ships **OFF**) gating only the
> lifespan loop.
> **26c** (merged from `ws-26c-crm-ui`) — the `/crm` app (`src/app/crm/` + BFF proxy), the three
> frontend registration points with a `live ⇒ href` fence on `CenterApp`, the deal-contacts
> endpoints (`routes/crm/deal_contacts.py`, one primary per deal enforced on the shared
> `core.link_deal_contact` seam), `organization_name` on the deal list + board payloads, the
> `NOT_NULL_DEFAULTED` null guard, and the three review residuals.
> **26d (read half)** (branch `ws-26d-agent-crm`) — `apps/agents/agent-crm/` (`crm-assistant`,
> `runtime:"maf"`, `OpenAIChatCompletionClient`, `X-CC-Agent`) with four READ tools
> (`search_crm`, `get_pipeline`, `get_record`, `get_timeline`) over the existing `/crm` routes,
> registered in `_KNOWN_AGENTS` + `_AGENT_REGISTRY` + `agent_registry.json`; and `"crm"` added
> to the WhatsApp `_KNOWN_SYSTEMS` allowlist **parse-only** (§6). The email-thread timeline
> join, `CRM_AUTO_LEAD`, and every write tool are explicitly NOT in it — see §9.
>
> **Deployment state, measured 2026-08-06:** migrations **144 and 145 are applied on prod**
> and `/crm` is **live**. The Zoho backfill has been **run and is complete** — 737
> organizations, 1,189 contacts, 1,516 leads, 551 deals, 1,909 notes; **zero dirty rows and
> zero unmatched owners**. §7.1's pre-flip curl check was verified against the tenant: the
> RFC-1123 `If-Modified-Since` header is honored (304).
> ⚠️ **CORRECTED 2026-08-11 (WS-26i audit).** This paragraph used to end "**Nothing has ever
> written the Zoho tenant** — that is still true, and stays true until the owner flips
> `CRM_ZOHO_SYNC`". **That is false and has been since 2026-08-06**, when the owner ENABLED
> the sync loop (§6 WS-26 (a); struck in this file's own Board record, which the header had
> not been swept to match). **The loop is RUNNING**: it cycles every 600s, and
> `core.mark_dirty_on_insert` / `mark_dirty_on_update` stamp every native write to the four
> `ZOHO_TRACKED_TABLES` — so **any CRM write lands in the live Zoho tenant within one
> cycle**, and a delete propagates as a real upstream DELETE via
> `core.record_zoho_tombstone`. This is the sentence an implementer reads before deciding
> whether a write is safe, which is why it is corrected here rather than only downstream.
> What remains OWNER-GATE is changing the running loop and any hand-run push cycle
> (`work_plan.md` §6).
> · **WS-26d-email: 🟢 MERGED + DEPLOYED 2026-08-07 (PR #392)** — the
> caller-scoped email→CRM timeline join, the address index (migration 154,
> applied), `TimelineEntry.kind = "email_thread"` on both sides, and
> `tests/unit/test_crm_email_timeline.py` (30 cases; two of them are the
> mutation fence for the scoping rule).
> · **WS-26d-write: 🟢 MERGED + DEPLOYED 2026-08-08 (PR #400, no migration;
> deploy 31217978773 log-verified — gateway restart loads the module)** —
> the four confirmation-gated write tools in `apps/agents/agent-crm/agents.py`
> (`create_lead`, `update_deal_status`, `log_activity`, `convert_lead`),
> `_ALLOWED_METHODS` widened to `{GET, POST, PATCH}` (never DELETE/PUT), the
> path fence extended past f-strings to `.format`/`%`/`+`, and
> `tests/unit/test_crm_agent_write.py` (76 cases) + `test_crm_agent.py` grown from
> 87 to 143. **LIVE: a confirmed agent write is born `zoho_dirty` and reaches the
> live tenant within one 600s sync cycle (D-CRM-9).**
> · **WS-26d-autolead: 🟢 BUILT 2026-08-08 (branch `ws-26d-autolead`) — NOT
> FLIPPED, NOT DEPLOYED.** `CRM_AUTO_LEAD` exists in
> `acb_common/settings.py` and ships **False**; the CRM step is
> `routes/crm/auto_lead.py`, called from `process_new_mail` from **inside
> `if auto_lead_enabled():`** so the OFF state enters nothing; the new
> `crm_auto_lead_cursors` table (migration **158** — 157 is held by open PR
> #399) carries the `activated_at` / `processed_watermark` / `last_run_at`
> trio the deep-resync discriminator, the incremental cursor and the dormancy
> re-anchor need. **Nothing has been flipped and nothing has been deployed** —
> the flip stays OWNER-GATE (`work_plan.md` §6 (b)), and while the flag is off
> this changes no runtime behaviour at all. Tests:
> `tests/unit/test_crm_auto_lead.py` (75 cases); fifteen mutants run red and
> reverted. ⚠️ **Two review rounds landed on this branch.** The first closed two P1s
> that only a running cursor would have shown: an OFF→ON round trip minted the
> whole OFF window (27 leads measured), and a single failure advanced the
> cursor over every candidate behind it (3 leads lost, measured). The second
> closed a P1 the FIRST FIX introduced — the re-anchor reset the anchor to
> `now` and returned early, which discarded the very message that woke the
> step, every night and every weekend; it now clamps to `now - 1h` and runs the
> batch. See the ticket's done-when 8 and 9.
> · **WS-26e: 🟡 SPEC, nothing built.**
> **26f** — 🟢 **MERGED + DEPLOYED 2026-08-07 (PR #391), NOT RUN against the tenant.** f1
> `POST /crm/import/zoho/stages` (`routes/crm/stage_metadata.py`, floor
> `admin:access:manage`, **dry-run by default**, `?apply=true` to write, >1 pipeline STOPS
> before the DB is opened per D-CRM-11) + the two settings readers on the Zoho read client
> (`list_deal_layouts` / `list_deal_pipelines`, with `ZohoScopeError` / `ZohoApiVersionError`
> so no-scope, no-data and no-such-endpoint are three different reported outcomes); f2
> `?tab=settings` — three grids over the EXISTING admin API plus D-CRM-10's clamp in
> `admin.py::_validate_status` (won=100 / lost=0, 422 on the contradiction, read off the ROW
> so a single-field PATCH cannot slip past it); f3 weighted ₹ per lane (a whole-lane SQL
> aggregate, never derived from the returned page) + the board-header rollup; f4 the
> `closed_at` proxy backfill, a **direct UPDATE that bypasses `mark_dirty_on_update`** so the
> repair queues zero pushes. **No migration** — 144 already carried every column this needed.
> ⚠️ **Running it against the production tenant — dry run or apply — is OWNER-GATE
> (`work_plan.md` §6) and has NOT been done**; the expected first outcome is `no_scope`,
> because the tenant's refresh token was never minted with `ZohoCRM.settings.*` and
> re-minting it is the owner's act. f2's grids are the fallback that needs no token at all.
> **26g** — 🟢 **BUILT 2026-08-07** (branch `ws-26g-reports`, **no migration** — 144's
> `crm_status_changes` already carried every column this reads). `routes/crm/reports.py`
> = four read-only endpoints on the shared gated router (`/crm/reports/{pipeline,funnel,
> win-loss,owners}`); `?tab=reports` on the existing URL grammar with
> `components/Reports.tsx` + pure `lib/reports.ts`. **`WEIGHTED_SQL` was lifted into
> `core.py` beside `WEIGHTED_TYPES`** (`pipeline.py` still re-exports it) so the formula
> has exactly one definition, and `core.status_wire` replaced the two hand-kept status
> projections rather than becoming a third. **The cross-language parity mechanism is
> minted, not inherited**: `tests/fixtures/crm_weighted_parity.json` (new directory) is
> read by BOTH `tests/unit/test_crm_reports.py` — through the emitted SQL, whose
> expression `_crm_fakes._WEIGHTED_SUM_RE` parses out of the statement text — and
> `board.test.ts`, through `weightedDeal`/`weightedRows`. ⚠️ **The funnel is written for
> what the log RECORDS**: "entered" is a visited-set union (`from_status`, `to_status`,
> and the deal's current stage), because `crm_status_changes` logs transitions only and
> all 551 imported deals have zero rows; dwell is grouped by **`from_status`**, the stage
> being LEFT; renamed lanes orphan their history and are reported in `unmatched`, never
> dropped; and NULL `closed_at` (every imported closed deal until f4's owner-gated
> backfill runs) falls outside the trailing window and is counted separately so a 0% win
> rate is explicable. Lost reasons carry a NAMED unattributed bucket — the earlier claim
> that reason data is "complete by construction" is FALSE, because the importer bypasses
> both gates. **No `GROUP BY` is emitted** (the ticket's deliberate choice): per-key
> aggregates in `get_pipeline`'s shape, so the weighted expression stays the one the fake
> reads. 47 hermetic cases + 20 vitest + the 14-row shared fixture on both sides; 5
> mutants measured red. One fake-fidelity bug found and fixed on the way: `_crm_fakes`'
> `lower(col) = :param` reader matched a NULL column, which SQL never does.
> · **WS-26h (stage discipline): 🟢 BUILT 2026-08-11** (branch
> `claude/crm-command-center-tasks-i8l7n4`, migration **169** — number taken from the
> directory at build time per R1 and re-checked at merge; every test finds the file by
> CONTENT so a renumber in review is free). `crm_deal_statuses` gains
> `required_fields TEXT[] NOT NULL DEFAULT '{}'` and a nullable `max_dwell_days SMALLINT`,
> both **deal-only** — the same asymmetry `probability` already has, because the allowlist
> is deal columns and rot is measured off `status_changed_at`, which 144 gave deals and
> withheld from leads. The gate is `pipeline.py::_require_entry_fields`, called from
> `apply_status_transition` immediately after the lost-reason refusal, so it inherits all
> three of that rule's properties: **entry-only** (`records.patch_record` enters the
> transition only when `status_id` actually MOVES), **refused before any of the three
> effects**, and **satisfiable by the SAME PATCH** — which is what lets the modal send
> fields + status in one request. `core.STAGE_REQUIREABLE_FIELDS` is the allowlist and is
> validated when the column is WRITTEN, never when it is read: a typo'd name can never be
> satisfied by any deal, so validating on the way out would leave a lane that refuses every
> move with a message about a field that does not exist. ⚠️ **Two decisions worth
> re-reading before extending this:** `0` and `False` are VALUES and only `None`/blank text
> count as absent (`_is_blank`, mirrored in `board.ts`) — a falsiness test would refuse a
> genuine ₹0 deal, and it was measured against a real `NUMERIC` column returning
> `Decimal('0.00')`; and **CREATE was deliberately not gated** — recorded in the
> function's own docstring rather than fixed by ambush, and **now resolved by D-CRM-13
> and ticketed as WS-26h2**: the explicit-`status_id` create is gated, the DEFAULTED
> create and the convert path stay open, so a settings-grid edit still cannot
> retroactively make quick-create or lead conversion refusable. ⚠️ **This paragraph
> previously undercounted the ungated create paths as two** (`POST /crm/deals`,
> `_create_deal`). **There are three.** The third is `import_zoho.apply_record` →
> `core.upsert_by_zoho_id`, reached by the backfill route *and* by `sync_zoho.pull_phase`
> — the **enabled** 600s loop — and it is the one that must remain ungated forever.
> Corrected 2026-08-11; the same undercount in `pipeline.py`'s docstring was corrected in
> the same PR. Rot is **presentation only** (`board.ts::rotLevel` → `ROT_TONES`, amber past the
> threshold, destructive past 2×, strictly-past so a card is fine ON the allowance day):
> nothing moves, closes or hides. `LostReasonModal` was **absorbed** into `MoveModal`
> rather than sat beside — a lost stage can also be a gated one, and two dialogs each
> raised by its own 422 is the shape where a user answers a question and is refused again.
> **Fences (R7):** `test_crm_pipeline.py` +24 cases (the gate, the entry-only property, the
> zero/blank pair, both settings validators); `test_crm_migration.py` grew a WS-26h section
> and its `NOT_NULL_DEFAULTED` derivation now reads ALTER-added columns too — the sync
> migration stays excluded with its existing reason (`zoho_dirty` is platform-written, this
> column is PATCHed by name); **`test_crm_stage_discipline_parity.py` is new** and reads
> `REQUIREABLE_FIELDS` out of `board.ts`, holding the two-language allowlist together the
> way `CATEGORY_HUES` is held — measured red in both directions; `board.test.ts` +14,
> `settings.test.ts` +13. **R8:** the migration was applied to a real PostgreSQL 16, replayed
> three times for idempotency, and the package's own `insert_row`/`update_row`/`status_wire`
> were run against it — the `TEXT[]` round-trip is the one thing the hermetic fakes cannot
> answer. ⚠️ **Corrected 2026-08-11 (WS-26h2 repair round 1): this said "Not deployed,
> not merged" and both halves were wrong about the merge.** WS-26h **MERGED in PR #425**
> — `d471ae8`, and `git branch -a --contains d471ae8` lists `remotes/origin/main`.
> **Deploy NOT independently verified**: a merge is not a deploy, and there is no
> migration-ledger line for 169 and no log line in evidence here (non-negotiable 8).
> · **WS-26h2 (entry requirements on the CHOSEN create stage): 🟢 BUILT 2026-08-11**
> (branch `claude/crm-command-center-tasks-i8l7n4`; **no migration, no UI change, no change
> to `core.STAGE_REQUIREABLE_FIELDS` or `board.ts::REQUIREABLE_FIELDS`**). The gate is **one
> call in `records._resolve_status`**, beside the create-side lost-reason refusal and in the
> same order, under `if chosen:` — where `chosen = values.get("status_id")` is the caller's
> own claim and `load_default_status` is the server's default (**D-CRM-13**). It sees
> `values`, the caller's body *after* `create_record` has defaulted an absent `owner_email`
> to the acting user, so an unstated owner is never "missing" while an explicit `null` is.
> `_require_entry_fields` gained **`NO_EXISTING_RECORD`**, a first-class "there is no stored
> row" shape, and now **refuses `None` with a `TypeError`** rather than reading it as one:
> `getattr(None, field, None)` answers `None` for every field, so a miswired caller would
> silently refuse a move it should have allowed and be indistinguishable from the create
> case. ⚠️ **The gate label is a property of the SITE**: AGENT-SAFE in `records.py`,
> OWNER-GATE anywhere `sync_zoho.pull_phase` traverses (§6 WS-26 (a)). One of the three
> create paths is gated; the other two — the DEFAULTED create together with `_create_deal`,
> and `import_zoho.apply_record` → `core.upsert_by_zoho_id` — stay open. **Fences (R7):**
> `test_crm_pipeline.py` **67 → 84**, `test_crm_routes.py` **121 → 131**,
> `test_crm_convert.py` **31 → 32** — **+28**, collected, counted against a worktree at
> `591231d` rather than estimated. **Eighteen mutants measured red**, and one is worth
> recording: moving the gate to `core.insert_row` keyed on `crm_deals` — the tempting "one
> seam" — leaves `test_crm_zoho_import.py` + `test_crm_zoho_sync.py` **136 green** (the
> importer duplicates the statement rather than delegating, so the pull walks straight past
> it) and turns **only** the siting fence and the two D-CRM-13 cases red. Done-when 9 held:
> neither Zoho test file was touched. `pipeline.py`'s docstring — which still said CREATE
> was ungated and still counted the ungated paths as two — is corrected in the same change
> (R4). **R8 does not bind this slice: no SQL, no migration, no predicate.**
> ⚠️ **Repair round 1 (2026-08-11): a verifier FAILED this and a reviewer approved it with
> findings; all nine done-whens were independently re-derived and met, and the defects were
> in the CONTRACT and the docs rather than in the gate.** **(1)** Done-when 8's prescribed
> fence — the set of files CONTAINING the literal `_require_entry_fields(` — is a text match
> that could not back its own reachability docstring: `import_zoho.apply_record` calling
> `records._resolve_status` stayed green (no new call site, yet the gate lands on the
> **enabled** pull, and `apply_record` already sets `values["status_id"]` server-side so
> `chosen` would be truthy for every pulled deal), an aliased import stayed green, and a
> *comment* recording why the path must stay ungated turned it RED — making deletion of that
> comment the cheapest way back to green. Replaced by **two AST fences**: call sites (real
> `ast.Call` nodes, aliases and module-attribute forms resolved) and **transitive
> reachability from `sync_zoho.pull_phase` / `import_zoho.apply_module`**, with the static,
> `routes/crm/*.py`-scoped limit stated rather than implied. Eight shapes pinned against
> synthetic packages so the fence going blind is a red test; all six re-measured against the
> real package, including the comment case staying green. **(2)** Done-when 6's
> `Decimal("0.00")` clause is **unsatisfiable through `POST /crm/deals`** — `DealIn.amount`
> is `float | None` (measured: `Decimal('0.00')` → `0.0 <class 'float'>`) — so the criterion
> was relabelled to what the test earns, not the test to what the criterion said. **(3)**
> Two false deploy-state sentences in this header (WS-26h and WS-26i-export, both saying
> "not merged" while both are on `origin/main`) are corrected above; the previous cycle
> failed on the same class and it did not get to survive on "pre-existing". **(4)** The
> fence count in this paragraph said `+9` where the measurement was `+8`, and the first
> round's mutant tally was reported as twelve when thirteen were run (two carried the same
> number). Every count here is now collected rather than recalled: **19 mutants red across
> the three rounds, plus one deliberate GREEN control** — a comment naming the gate, which
> the replaced fence would have failed.
> ⚠️ **Repair round 2 (re-verify PASSED; one hole closed).** The new fence's own self-test
> did not exercise the capability its helper's docstring leans on: `_crm_imports` walks the
> WHOLE tree so a **function-body import** counts, but all eight fixtures imported at top
> level, so mutating it to `tree.body` left the synthetic suite 8 green and the whole file
> 84 green. Load-bearing, not academic — **`core.py` cannot import `pipeline` at top level**
> (measured: `ImportError: cannot import name 'CLOSING_TYPES' from partially initialized
> module 'gateway.routes.crm.core'`), so the "one seam" mis-siting the fence names as the
> one to watch can ONLY be written as a function-body import, and `core.insert_row` is not
> reachable from the pull entry points either. The `core.py` fixture modelled a siting that
> cannot exist; its import moved inside the function, which makes the fixture realistic AND
> pins the walk — the `tree.body` mutant now goes red. ~~**Also recorded, not built:** WS-26h's
> own fence carries the same text-match defect…~~ — **BUILT 2026-08-11 as WS-26h-fence, below.**
> **Not deployed, not merged.**
> · **WS-26h-fence (WS-26h's siting fence converted to the AST mechanism): 🟢 BUILT
> 2026-08-11** (branch `claude/crm-command-center-tasks-i8l7n4`, **test + docs only — no
> `routes/crm/` change, no migration, no UI**). The banked wart above is paid off. `_GATE`
> was a module-level constant; it is now an **argument** (`_gate_call_files(package, gate)`,
> `_gate_reached_from(package, entries, gate)`) so **one** set of helpers answers for
> **two** gates — `_MOVE_GATE = ("pipeline", "apply_status_transition")` (WS-26h) and
> `_ENTRY_GATE = ("pipeline", "_require_entry_fields")` (WS-26h2). No third mechanism was
> minted; the helpers were not forked. WS-26h now has the same **two** fences the entry gate
> has: `test_the_move_gate_is_called_from_exactly_two_files` (direct call sites) and
> `test_the_zoho_pull_never_enters_the_stage_gate` — the WS-26h name is **kept** and now
> carries the reachability claim its docstring always made. ⚠️ **Measured against the real
> package, applying and reverting each mutant, old fence vs new** (the last two are the
> point — a conversion that keeps the old answer there achieves nothing): a direct call in
> `import_zoho.py` → red / red; an **aliased** import → **GREEN / red**; the **indirect**
> route `import_zoho.apply_record` → `records.patch_record` → **GREEN / red** (chain
> reported: `import_zoho.apply_module -> import_zoho.apply_record -> records.patch_record ->
> pipeline.apply_status_transition`); a **comment** naming the call → **RED / green**;
> baseline → green / green. The synthetic self-test grew **8 → 15 cases** and now asserts
> **both** gates on **every** case, so a shape aimed at one gate cannot quietly move the
> other's answer; the move gate's fixture case for `core.py` uses a **function-body import**,
> because repair round 2's F1 lesson is that a top-level fixture import pins nothing about
> `_crm_imports`' whole-tree walk. Suite **84 → 92**. Four helper mutants measured red
> against the new cases: `ast.walk(tree)` → `tree.body` (2 red, one per gate),
> `_resolved_call` dropping the import branch (15 red), `_gate_call_files` ignoring its
> `gate` argument (10 red), `_gate_reached_from` ignoring it (1 red). **R8 does not bind:
> no SQL, no migration, no predicate.** Neither Zoho suite was edited.
> ⚠️ **Repair round 1 (2026-08-11): verifier PASSED, reviewer REQUEST-CHANGES on a P1 —
> and the P1 was a REGRESSION against the fence the conversion deleted.** `_crm_imports`
> gated on `node.module.startswith("gateway.routes.crm.")`, so a **relative** import
> (`node.module == "pipeline"`, `level=1`) matched nothing and `from . import pipeline`
> (`node.module is None`) was dropped before the branch was reached. The substring scan
> caught **any** spelling; the first AST cut caught one. Measured old-vs-new against a copy
> of the real package, **six** shapes were green-where-old-was-red — relative import,
> `from . import pipeline`, star import, `__init__` re-export, module-level call (outside any
> `def`), and a name bound to the package (`from .. import crm` → `crm.pipeline.f()`) — **and
> the decisive indirect route respelled `from .records import patch_record` was green on both
> fences**, i.e. one line would have put the transition on the enabled 600s pull with all four
> fences green. Not hypothetical: `orchestrator` and `meeting_bot` already carry 19 relative
> imports, and `pyproject.toml` selects no `TID` rules, so nothing else refuses one. All six
> now read, each with its own synthetic case, each measured red first — the suite is
> **15 → 25 cases** and the file **92 → 103**. Two more findings fixed in the same round:
> **reachability entered at `pull_phase` only**, so a gate reached from the PUSH half
> (`push_records` / `apply_push_result` / `_settle` / `_fail`) reported `[]` while running
> every cycle — the entry set is now the cycle itself (`_sync_loop` / `run_cycle` /
> `_run_cycle_locked`), measured green-then-red; and the call-file docstring **claimed
> function-level siting an assertion over `f"{module}.py"` does not have** — corrected to
> claim only the file, deliberately, because WS-26i-bulk done-when 1 moves the call to
> `apply_record_patch` inside `records.py` and a function-level assertion would turn that
> sanctioned change red. Eleven helper mutants now measured red (six of them new).
> ⚠️ **What stays blind, split by the ONE question that decides whether a hole is a REGRESSION against the substring scan this replaced — is the gate's own name written immediately before a `(`?** **(A) Name never written before a `(`, so the old scan was blind too — NOT regressions:** dispatch through a value (`_MOVE = apply_status_transition` then `_MOVE(…)`, `partial`, a cross-package callback); a registry or object holding the gate under ANOTHER name (`_REGISTRY["move"](…)`, `Registry.move(…)`); and `getattr(pipeline, "apply_status_transition")(…)` — **measured green on the old scan too**, because `")("` intervenes and the literal never appears. **(B) Name IS written before a `(`, so the old scan went RED — residual REGRESSIONS, exactly two, both left open deliberately:** a call qualified by something the graph cannot tie to a package module — `importlib.import_module("…pipeline").apply_status_transition(…)` and `_GATES.apply_status_transition(…)` where `_GATES` is a local object or class. ⚠️ **The reason recorded in repair round 1 for leaving them was FALSE and is corrected here:** it claimed closing them meant resolving unbound attributes against every top-level name, letting an innocent `db.close()` fabricate a chain — a reviewer disproved it by building the fix, and a narrow resolver reading only STRING CONSTANTS adds **no** edges to the real package, reddens both forms, and never looks at `db.close()`. **The real reason is reach, not risk:** `importlib.import_module` appears **once** in all of `apps/` + `packages/` (`orchestrator/declarative.py:100`, a plugin loader) and **zero** times in the gateway. Neither shape is the plausible refactor this fence targets; both are deliberate evasion, and a fence cannot be built against someone willing to edit the fence file. **(C)** An indirect route beginning at module level (module-level code is a call SITE but not an entry POINT). **(D)** Anything outside `routes/crm/*.py`. **Not deployed, not merged.**
> WS-26i (data management): 🟡 SPEC-THIN, audit-narrow before dispatch — **except
> WS-26i-export, below.**
> · **WS-26i-export (the filtered-list CSV export): 🟢 BUILT + REPAIRED 2026-08-11** (branch
> `claude/crm-command-center-tasks-i8l7n4`, **no migration**, **read-only — the live
> Zoho tenant is untouched**). `routes/crm/export.py` =
> `GET /crm/export/{leads,deals,contacts,organizations}.csv`, four literal paths with the
> `export/` segment FIRST so `/crm/leads/{record_id}` cannot shadow it. The filters are the
> caller's, through the SAME `core.list_contract` the list uses — which brings its refusals
> with it (`?status_id` on contacts/organizations, an unknown sort key, an unknown
> direction are all 422). ⚠️ **`ListQuery.limit` is deliberately never bound**: it is the
> page clamp (`MAX_PAGE_SIZE = 100`) and binding it would have exported the first 100 rows
> of the 1,516-row lead list with a 200 and no warning. The row cap is
> `export.MAX_EXPORT_ROWS = 10_000` — "the whole CRM twice over" against a measured 3,993 —
> and exceeding it is a **422 naming the real count**, never a partial file, checked with a
> `count(*)` over the same WHERE before a row is rendered. **`csv_cell`, the BOM and the
> RFC-4180 writer were PROMOTED out of `routes/projects/export.py` into
> `gateway/csv_export.py`** and both apps now consume it (Projects re-exports the names, so
> its test file was untouched): a formula guard with two copies is one that does not get
> the next fix. Client half: `app/crm/lib/columns.ts` (the column vocabulary lifted out of
> `RecordList.tsx`, which keeps only the JSX renderers), `filters.exportQuery` built by
> deleting the page off `listQuery`, `api.exportRecords`, an Export `Button` on the four
> list tabs, and `@/lib/export` — `filenameFromDisposition`/`saveCsv` promoted out of
> Projects with the fallback filename made a required argument. ⚠️ **The CRM BFF proxy did
> `res.json()` then `NextResponse.json(…)` unconditionally**, so a `text/csv` body arrived
> as `{}` with a 200; it now passes the upstream `Content-Type` and `Content-Disposition`
> through, the arm Projects already carried. **Fences (R7):** `tests/unit/test_crm_export.py`
> (47 cases) — the page-clamp trap end to end at 150 rows, the cap refusal, its boundary and
> the READ-COMMITTED race past it, the parameter-parity sweep per entity, the column
> vocabulary READ out of `columns.ts` the way `test_crm_stage_discipline_parity.py` reads
> `board.ts`, and done-when 7 asserted **twice**: no writer is imported (AST) and every
> statement an export issues is a `SELECT`; `src/lib/export.test.ts` RUNS both BFF proxies
> end to end over a BOM'd body and also sweeps them for the JSON-stamping shape;
> `filters.test.ts` +4. Every fence was measured red before its code existed.
> ⚠️ **Repair round 1 (2026-08-11), recorded because three of the four defects were fences
> that passed while broken** — full write-up in §9. **(1)** Both BFF proxies did
> `await res.text()`, a UTF-8 decode, which **strips the BOM the gateway emits**; measured
> `EF BB BF 4E 61 6D` in, `4E 61 6D 65` out, so done-when 5 was not met end to end. Both now
> read `res.arrayBuffer()`. **This also fixes the same bug in Projects** —
> `api/projects/[...path]` has carried the identical arm since WS-27ae, so
> `/projects/export/tasks.csv` reaches Excel without its BOM; it is fixed here deliberately,
> not by accident. ⚠️ Evidenced precisely, after a verifier challenged an earlier "in
> production" phrasing: WS-27ae is **on `main`** (`1de846a` is an ancestor of `origin/main`
> via `ebf68f4`, PR #422) and `deploy` reported success on that SHA — **almost certainly
> live, not proven live**, because a green job is not delivery evidence (non-negotiable 8).
> **(2)** The done-when-1 parity fence compared the SHALLOW `dependant.query_params`, empty
> on both sides because both use a class `Depends()` — it now recurses. **(3)** `LIMIT :cap`
> at exactly the cap could return exactly the cap after a concurrent insert (READ COMMITTED)
> and ship a partial file; it binds `cap + 1` and refuses on the rendered count.
> **(4)** `X-Export-Rows` was unreachable through the proxy; both proxies forward it.
> ⚠️ **Corrected 2026-08-11 (same repair round, same defect class): WS-26i-export MERGED
> in PR #426** — `7255344`, `git branch -a --contains 7255344` lists
> `remotes/origin/main`. **Deploy NOT independently verified** — and note the Projects
> BOM fix rides this merge, so `/projects/export/tasks.csv` is repaired in the tree, not
> demonstrably in production. The other four WS-26i items stay 🔴 NO-GO.
> **DEMO CRITICAL PATH (owner-directed 2026-08-07, §9.0): ~~dispatch D1 f~~ (∥ ~~D2 d-email~~) →
> ~~D3 g~~ → ~~D4 d-write~~ → D5 d-autolead; h/i/e deferred past the demo. Full chain and all
> gates intact — the order re-sequences, it does not thin.**
> · **Owner:** vjvarada · **Board row:** WS-26
>
> ⚠️ **`.env.example` cannot carry `CRM_ZOHO_SYNC`** — plan-guard blocks agent writes to it, so
> the variable is documented here and in `acb_common/settings.py` only. Same for the
> `.claude/hooks/plan-guard.mjs` OWNER_GATES entry WS-26b's ticket asks for: `.claude/` is
> untracked, so that edit lands on the box-side copy, not in this change.
>
> **Not in WS-26a, on purpose:** `schema.generated.sql` was NOT regenerated (struck from
> done-when 1 by the 2026-08-05 audit — it needs a migrated live DB and is ~43 migrations stale
> repo-wide, so refreshing it here would bundle an unrelated resync into this change). It stays
> an owner-run chore.
>
> **Research provenance (2026-08-05):**
> - `frappe/crm` @ develop — **AGPL-3.0: no code may be copied.** Data-model facts, enum
>   vocabularies, and workflow concepts below are unprotectable facts, reimplemented fresh.
> - `trycompai/crm` @ main — **MIT: code may be copied**, but the stack (NestJS/tRPC/Prisma/
>   Eve/Better Auth) doesn't survive translation; we take design patterns, not code.
> - Metorite full-tree sweep — every Zoho touchpoint and app-convention anchor cited
>   below was verified in-tree on the date above.

---

## 1. Product vision and scope

**Who this is for:** Fracktal Works sales — today effectively the owner plus the sales
colleagues WS-24 will eventually admit. The company sells hardware (3D printers), filament,
service contracts (AMCs), and projects. Deals are INR, phone/email/WhatsApp-driven, modest
volume (thousands of records, not millions).

**What it replaces:** Zoho CRM. Today Zoho is the system of record and Metorite holds a
read-only nightly mirror of it (§2). The native CRM inverts that: **Metorite becomes the
system of record, Zoho becomes an import source, then Zoho is retired.**

**What "done" means (end state, Phase E):**
1. Leads, deals, contacts, organizations live in `crm_*` tables with a working pipeline UI
   (list + kanban + record page + timeline).
2. All Zoho data is imported with provenance (`zoho_id`), counts verified.
3. The email app, the tasks app, WhatsApp and the agent platform *bind to* CRM records
   instead of duplicating them (§6) — the platform already owns email sync, tasks,
   notifications, and agents; **the CRM delegates those concerns, never rebuilds them.**
   (This is Frappe CRM's structural lesson: it stays small by delegating email, contacts,
   files, and audit to its framework. Same move here.)
4. The Zoho mirror, its cron, webhook, credentials and config are retired (§7.4), closing
   part of WS-2's standing credential exposure.

**Non-goals (v1 — record departures here per `user_management_contract.md` §7):**
- Multi-currency and exchange rates. INR only; a `currency` column exists with default
  `'INR'` so this is additive later.
- Territories, sales hierarchies, per-team record visibility. Single org (D11); §8 D-CRM-3.
  *[D11 was re-taken by D15 (2026-08-08): org-wide-read v1 stays the within-org design, and
  cross-tenant isolation arrives via RLS at MT-1b — no hand-written org predicates; see
  work_plan.md D15 and R5.]*
- SLA/response-time engine, assignment rules, sequences/campaigns, marketing automation.
- No-code custom-field or layout editors. Fields live in migrations; layouts in code.
- Quoting/invoicing/taxes. Deal line items only (Phase C); billing stays out of scope.
- Telephony SDKs (Twilio/Exotel). Manual call logging only.
- A saved-views table. v1 view state lives in the URL (trycompai pattern); canned views are
  code.
- Zoho write support **outside the sync engine**. *(Amended 2026-08-05, owner-directed —
  D-CRM-7.)* The original non-goal ("no Zoho write path at all; we are leaving, not
  deepening") is overruled: while Zoho is still in use, WS-26b runs a **faithful two-way
  sync**, which requires a write client. The boundary that survives: the sync engine is the
  **single writer** — no route handler, agent tool, or skill calls Zoho directly, the write
  client has exactly one caller (grep-asserted), and the whole write path retires with
  WS-26e. ✅ **Built 2026-08-05**; WS-1's "no Zoho write path exists" clause and the §4
  registry row were corrected in the same change (board Authority rule: fix the mirror).

---

### 1.x Future phases — the rest of the Sales suite (D22, 2026-08-10; NOT dispatchable)

Owner call, recorded in `work_plan.md` §3 D22: **products, price books,
brochures/product-information library, and the proposal generator are CRM-module
scope** — and under D23 (2026-08-10) the CRM module is an internal billing atom
sold **only inside the Sales Center package (₹600/user — `saas_multitenancy.md`
§2.4b)**, so all of these ship as Sales Center capabilities, never separate
SKUs. None is specced; each needs its own section here (data model, API,
UI, done-whens per the §1 contract in `work_plan.md`) before any ticket exists.
Sequenced after the current letters (h · i · e cutover); nothing in this note
changes them.

## 2. Current state — the Zoho mirror, measured 2026-08-05

**Zoho is read-only batch ingestion into three Phase-0 graph tables. There is no CRM UI, no
Zoho agent tool that calls the API, no write path, and no `lead` table anywhere.**

| What | Where |
|---|---|
| Client (OAuth refresh + paginated `GET /crm/v2/*`) | `apps/services/ingestion/ingestion/sources/zoho/client.py` — `list_accounts/deals/contacts/notes/tasks/users`, plus `list_leads` + `list_deleted` **added by WS-26b**. Still read-only. |
| Write client (**added by WS-26b**, D-CRM-7) | `apps/services/ingestion/ingestion/sources/zoho/writer.py` — create/update/upsert/delete per module. Exactly ONE caller (`routes/crm/sync_zoho.py::execute_push`), grep-asserted; every call arrives broker-gated; retires with WS-26e. |
| Normaliser (Accounts→Customer, Contacts/Users→Person, Deals→Deal) | `apps/services/ingestion/ingestion/sources/zoho/normaliser.py` |
| Webhook receiver (shared-secret, fail-closed, enqueue-only) | `apps/services/ingestion/ingestion/sources/zoho/webhook.py`; registered `gateway/main.py` (`/webhooks/zoho` in `PUBLIC_ROUTES`) |
| Nightly sync (02:50) + manual script | `ingestion/scheduler.py::_run_zoho` · `scripts/zoho_sync.py` |
| Mirror tables (`zoho_id TEXT UNIQUE` on each) | `person`, `customer`, `deal` in `infra/postgres/01_schema.sql`; ORM `packages/acb_graph/acb_graph/models.py`; upserts `acb_graph/repo.py` |
| Mirror consumers | `apps/services/orchestrator/orchestrator/sales_views.py` (customer-360/pipeline read models) · `scripts/reconciler.py` (quiet-deal escalation) · `acb_graph/resolver.py` (entity resolution) · six `skills/sales|reconciler/*` skills (all `rollout_stage: shadow`, read the graph, never Zoho) |
| Credentials/config | `acb_common/settings.py` (`zoho_*`) · `.env.example` · `acb_llm/key_store.py` (`zoho-crm`) · `acb_skills/integrations.py` (`_zoho_crm`) · `gateway/routes/integrations.py` (catalog card, health check) · `gateway/routes/oauth.py` (`zoho-crm` provider) |
| Feature gating helper | `require_feature_router` — `packages/acb_auth/acb_auth/deps.py` (re-exported from `acb_auth`); FEATURES tuple in `acb_auth/permissions.py` |
| Frontend | **No Zoho data rendered anywhere.** `lib/centers.ts` Sales Center lists "Pipeline (Zoho CRM)" `status:"planned"`, no href |

Consequences that shape this plan:
- **Migration is import-and-retire, not a live cutover.** Nothing user-facing breaks when
  Zoho goes away; only the mirror consumers above need repointing (Phase E).
- **Leads were never mirrored** — WS-26b added the read-only `list_leads` to the existing
  client (one `GET`, same shape as its siblings).
- The graph mirror keeps running untouched through Phases A–D; retiring it is Phase E.

---

## 3. Data model

All tables in one migration at the **next free number at build time** (R1 — resolve from
`infra/postgres/`, never from a spec; 144 was free on 2026-08-05). Idempotent per
`infra/postgres/README.md`: `CREATE TABLE IF NOT EXISTS`, `INSERT … ON CONFLICT DO NOTHING`,
guarded `DO $$`. PKs `UUID DEFAULT gen_random_uuid()`, timestamps `TIMESTAMPTZ DEFAULT now()`,
indexes `idx_<table>_<cols>`. ~~Refresh `schema.generated.sql` in the same PR~~ — **struck by
the 2026-08-05 audit** (needs a migrated live DB; stays an owner-run chore, see §9 dw 1).

The spine is Frappe's four-entity shape (battle-tested; maps 1:1 onto Zoho's modules) with
trycompai's activity spine and provenance columns.

### 3.1 `crm_organizations` (Zoho: Accounts)
`id` · `name TEXT NOT NULL` · `website` · `industry` · `no_of_employees` · `annual_revenue
NUMERIC(14,2)` · `phone` · `email` · `address JSONB` · `description` · `linkedin_url` ·
`owner_email TEXT` · `source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN
('manual','import','email','agent'))` · `zoho_id TEXT UNIQUE` · `last_activity_at TIMESTAMPTZ`
· `created_at` · `updated_at`. Index: `name`, `owner_email`, `last_activity_at`.

### 3.2 `crm_contacts` (Zoho: Contacts)
`id` · `first_name TEXT NOT NULL` · `last_name` · `email` · `phone` · `mobile` · `title` ·
`organization_id UUID REFERENCES crm_organizations ON DELETE SET NULL` · `description` ·
`linkedin_url` · `owner_email` · `source` (as above) · `zoho_id TEXT UNIQUE` ·
`last_activity_at` · `created_at` · `updated_at`.
Index: `lower(email)` (plain, **not** unique — Zoho data has duplicates and blanks; dedup is
enforced at conversion time by code, §3.7), `organization_id`, `last_activity_at`.

### 3.3 `crm_leads` (Zoho: Leads) — person+company denormalized inline, **no FKs until conversion**
`id` · `first_name` · `last_name` · `lead_name TEXT NOT NULL` (computed fallback chain:
names → organization_name → email local-part → 'Unnamed lead') · `email` · `phone` · `mobile`
· `organization_name TEXT` (free text — becomes a `crm_organizations` row only on conversion)
· `website` · `industry` · `no_of_employees` · `annual_revenue NUMERIC(14,2)` ·
`status_id UUID NOT NULL REFERENCES crm_lead_statuses ON DELETE RESTRICT` · `lead_source TEXT`
· `owner_email` · `description` · `lost_reason_id UUID REFERENCES crm_lost_reasons ON DELETE
SET NULL` · `lost_note TEXT` · conversion provenance: `converted_at TIMESTAMPTZ` ·
`converted_contact_id / converted_organization_id / converted_deal_id` (each `UUID … ON DELETE
SET NULL`) · `source` · `zoho_id TEXT UNIQUE` · `last_activity_at` · `created_at` · `updated_at`.
Index: `status_id`, `lower(owner_email)`, `lower(email)`, `last_activity_at`. Default lists
filter `converted_deal_id IS NULL` (**B6** — the FK link, not the timestamp: deleting the
deal SET-NULLs the link and the lead returns to the working list; `converted_at` survives
as history).

### 3.4 `crm_deals` (Zoho: Deals)
`id` · `name TEXT NOT NULL` · `organization_id UUID REFERENCES crm_organizations ON DELETE SET
NULL` · `status_id UUID NOT NULL REFERENCES crm_deal_statuses ON DELETE RESTRICT` ·
`status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now()` (stage-age clock) · `amount
NUMERIC(14,2)` · `currency TEXT NOT NULL DEFAULT 'INR'` · `probability SMALLINT` (auto-filled
from status default when NULL) · `expected_close_date DATE` · `closed_at TIMESTAMPTZ` (stamped
when status.type becomes won/lost) · `lost_reason_id … SET NULL` · `lost_note` · `next_step
TEXT` · `lead_id UUID REFERENCES crm_leads ON DELETE SET NULL` (provenance; powers timeline
inheritance §5.3) · `owner_email` · `description` · `source` · `zoho_id TEXT UNIQUE` ·
`last_activity_at` · `created_at` · `updated_at`.
Index: `status_id`, `organization_id`, `owner_email`, `expected_close_date`, `last_activity_at`.

### 3.5 `crm_deal_contacts` — M:N with role
`deal_id UUID REFERENCES crm_deals ON DELETE CASCADE` · `contact_id UUID REFERENCES
crm_contacts ON DELETE CASCADE` · `role TEXT` · `is_primary BOOLEAN NOT NULL DEFAULT false` ·
`PRIMARY KEY (deal_id, contact_id)`. Code enforces at most one primary per deal.

### 3.6 Statuses as data (Frappe's model; **not** an enum — trycompai's frozen enum is the anti-lesson)
`crm_lead_statuses` and `crm_deal_statuses`, same shape:
`id` · `name TEXT NOT NULL UNIQUE` · `color TEXT NOT NULL DEFAULT 'gray'` · `position INT NOT
NULL` (kanban lane order) · `type TEXT NOT NULL CHECK (type IN
('open','ongoing','on_hold','won','lost'))` · `is_default BOOLEAN NOT NULL DEFAULT false` ·
deal statuses additionally `probability SMALLINT NOT NULL DEFAULT 0`.
Semantics: kanban lanes = rows ordered by `position`; `type` is the machine-readable class —
entering a `lost` status **requires** a lost reason (422 otherwise); entering `won`/`lost`
stamps `closed_at`. Seeds (`ON CONFLICT DO NOTHING`; the importer appends Zoho's real stage
names, §7.1): leads `New/Contacted/Nurture/Qualified/Lost`; deals
`Qualification/Needs Analysis/Proposal/Negotiation/Closed Won/Closed Lost`.

### 3.7 Lead → deal conversion (one endpoint, Frappe's flow)
`POST /crm/leads/{id}/convert` with optional `{contact_id?, organization_id?, deal?{...}}`:
1. **Contact:** caller-chosen, else matched by `lower(email)` (the one dedup rule: email
   identifies a person), else created from the lead's person fields.
2. **Organization:** caller-chosen, else matched by exact `name` = `organization_name`, else
   created from the lead's org fields. Skipped entirely when `organization_name` is empty.
3. **Deal:** created carrying name (= organization_name or lead_name), owner, amount if
   given, `lead_id` provenance, contact as primary; status = default deal status.
4. Lead: stamped `converted_*`, status → the first `won`-type lead status if one exists.
   Converting an already-converted lead → 409.

### 3.8 `crm_activities` — the single timeline spine (trycompai's shape)
`id` · `type TEXT NOT NULL CHECK (type IN
('note','call','meeting','task','status_change','system'))` · `subject TEXT` · `body TEXT` ·
`occurred_at TIMESTAMPTZ` · `due_at TIMESTAMPTZ` · `completed_at TIMESTAMPTZ` (tasks) ·
target FKs, all nullable, at least one required (`CHECK`): `lead_id / deal_id / contact_id /
organization_id`, each `ON DELETE CASCADE` · `created_by TEXT NOT NULL` (email or
`agent:<name>`) · `meta JSONB` · `zoho_id TEXT UNIQUE` (imported Notes/Tasks) · `created_at`.
Indexes: `(deal_id, created_at)`, `(lead_id, created_at)`, `(contact_id, created_at)`,
`(organization_id, created_at)`, `due_at` partial `WHERE completed_at IS NULL`.
Every write to an activity target also bumps that row's `last_activity_at` (denormalized,
trycompai discipline). Status transitions write a `status_change` activity **and** a
`crm_status_changes` row.

### 3.9 `crm_status_changes` — funnel/dwell analytics for free (Frappe's status-change log)
`id` · `entity_type TEXT CHECK (entity_type IN ('lead','deal'))` · `entity_id UUID` ·
`from_status TEXT` · `to_status TEXT NOT NULL` · `changed_by TEXT NOT NULL` · `changed_at
TIMESTAMPTZ DEFAULT now()` · `dwell_seconds BIGINT` (time in `from_status`).
Index: `(entity_type, entity_id, changed_at)`.

### 3.10 `crm_lost_reasons`
`id` · `label TEXT NOT NULL UNIQUE` · `position INT NOT NULL DEFAULT 0`. Seeded:
`Price / Competitor / No budget / No response / Requirement dropped / Other`.

**Phase-C tables** (own migration, next free number at build time): `crm_products`
(`code UNIQUE`, `name`, `rate NUMERIC(14,2)`, `active`) and `crm_deal_line_items`
(`deal_id CASCADE`, `product_id SET NULL`, `name`, `qty NUMERIC(10,2)`, `rate`,
`discount_pct`, `amount`, `position`) — Fracktal sells printers + filament + AMCs; line
items with totals, no taxes.

---

## 4. API surface — `routes/crm/` package in the gateway

**Layout convention** (mirror `routes/tasks/`): `core.py` is the leaf owning the shared
`router`, Pydantic models, and row→model mappers; feature modules register routes on
`core.router` as an import side effect; `__init__.py` imports them in order and re-exports
`router`. Registered in `gateway/main.py` in its own fail-soft `try/except` block like every
other app router.

```python
router = APIRouter(prefix="/crm", tags=["crm"],
                   dependencies=[require_feature_router("crm")])
```

**Engine seam (BO-10 — this is load-bearing):** the gateway has 12 `create_async_engine`
call sites and the board's standing instruction is *"the next app should extend a shared
seam, not add engine 13."* Phase A therefore adds `gateway/db.py::get_engine()` (module-level
cached, the `routes/tasks/core.py` pattern lifted verbatim), `routes/crm` consumes **only**
that, and `routes/tasks/core.py` is converted to it as the proof the seam works. Converting
the other ten call sites is explicitly out of scope (D-CRM-4).

Modules and endpoints (all under `feature:crm` unless noted):

| Module | Endpoints |
|---|---|
| `core.py` | router, engine import, models, `list_contract()` helper. Also the two things every forecast surface shares, kept here so they have ONE definition: **`WEIGHTED_SQL`** (lifted out of `pipeline.py` at WS-26g when it gained a second consumer — `pipeline` re-exports it, so nothing that imported it from there had to move; a second copy would defeat `_crm_fakes._WEIGHTED_SUM_RE`, which reads the expression out of the statement text so a drifted formula changes the tests' answer) and **`status_wire`** (the status row→model projection, which `admin` and `pipeline` each had a private copy of; `reports` would have been the third). |
| `records.py` | CRUD ×4: `GET/POST /crm/{leads,deals,contacts,organizations}`, `GET/PATCH/DELETE /crm/<entity>/{id}`. List contract: `q, sort, dir, page, page_size≤100`, per-entity filters (`status_id, owner, source`) → `{rows, total}`. Sort via **allowlist**, never interpolated (trycompai's `resolveOrderBy` rule). |
| `pipeline.py` | `GET /crm/pipeline` (deals grouped by status: rows ordered per-lane, count + `SUM(amount)` + — WS-26f f3 — `weighted` per lane, all three aggregated over the WHOLE lane rather than the returned page) · `POST /crm/leads/{id}/convert` (§3.7) · status transition inside `PATCH` writes dwell log + activity + `status_changed_at` + probability default |
| `activities.py` | `GET /crm/<entity>/{id}/timeline` (merged: activities ∪ status changes ∪ — Phase D — linked email threads; a deal's timeline unions its `lead_id`'s history, labeled) · `POST /crm/<entity>/{id}/activities` · `PATCH/DELETE /crm/activities/{aid}` (complete task, edit note) |
| `deal_contacts.py` *(WS-26c)* | `GET/POST /crm/deals/{id}/contacts` · `DELETE /crm/deals/{id}/contacts/{contact_id}`. §3.5's "at most one primary per deal, enforced in code" lives on `core.link_deal_contact`, which the convert path also goes through — promoting demotes the incumbent first, in the same transaction. A **new module** rather than more of `records.py`: the package's stated layout is one feature module per concern, and deal-contacts are a sub-resource with their own invariant (build-time decision C1). |
| `admin.py` | `GET/POST/PATCH/DELETE /crm/statuses/{lead,deal}` + `/crm/lost-reasons` (reorder = PATCH `position`). Gated `feature:crm` (v1 decision D-CRM-3: the sales team manages its own pipeline; revisit when WS-24 admits colleague #1). `DELETE` on an in-use status → 409 (FK RESTRICT surfaces it). **WS-26f adds D-CRM-10's clamp to `_validate_status`**: probability outside 0-100 → 422, and a won-type lane that would not forecast 100 (or a lost-type one that would not forecast 0) → 422 naming the rule. It reads the state the write LEAVES — the row plus the payload — because `{"type": "won"}` alone and `{"probability": 40}` alone each contradict the rule only in combination with what is stored, so `patch_status` loads the row before validating. |
| `import_zoho.py` | `POST /crm/import/zoho` — **gated `require_permission("admin:access:manage")`** (existing admin capability; minting nothing per `user_management_contract.md` §3). ⚠️ `integrations:use:zoho-crm` was the first choice and is **wrong**: `131_integration_memory_permissions.sql` grants `member` `integrations:use:*`, so under `permission_matches` every member would hold it — the code floor must be an admin capability; the §6 owner gate governs the *run* on top of it. §7.1. Also owns the Zoho→native **field mapping**, which `sync_zoho.py` imports rather than re-deriving. |
| `stage_metadata.py` *(WS-26f)* | `POST /crm/import/zoho/stages` — the pipeline repair (§5.1 system 1), same `admin:access:manage` floor and the same reasoning: it rewrites the pipeline rather than a record. **Dry-run by default; `?apply=true` is the write and the registered owner gate (§6 (d)).** More than one pipeline STOPS the run before the database is opened (D-CRM-11). Also owns f4's `closed_at` proxy backfill, the one deal-row write in the package that deliberately **bypasses `core.update_row`** so it cannot dirty 500+ imported rows into the push queue. Reaches Zoho through `import_zoho._client()` — one seam, not two. |
| `reports.py` *(WS-26g)* | `GET /crm/reports/{pipeline,funnel,win-loss,owners}` — the four forecast & funnel blocks (§5.1 system 2). **Read-only**: no write, no Zoho call, no flag, no migration. Reuses `core.WEIGHTED_SQL` rather than retyping it. Emits **no `GROUP BY`** — per-key aggregates in `pipeline.get_pipeline`'s shape, because the weighted expression binds the lane's own default as `:stage_probability`, and a grouped statement would have to reach that through a join and would stop being the expression the parity fixture and the test fake both read; the set-shaped questions (visited sets, medians, orphan names) read their rows and are answered in Python. Three data truths it is written FOR, each of which makes the naive query wrong: the log records **transitions only** (so "entered" is a visited-set union including the deal's current stage — a `to_status` count reports zero for all 551 imported deals), it stores **names not ids** and `entity_type` is the only thing saying which vocabulary they came from (renamed lanes are reported in `unmatched`, never dropped), and **`closed_at` is NULL on every imported closed deal** until f4's owner-gated backfill runs. |
| `sync_zoho.py` | The two-way sync engine (§7.1's seven bullets) + `POST /crm/sync/zoho` (same `admin:access:manage` floor; runs one cycle **with or without** `CRM_ZOHO_SYNC`) + the gateway-lifespan loop, flag-gated. `execute_push` is the writer's only caller. |
| `broker_handlers.py` | The Action-Broker gate every push crosses and the three `crm.zoho_*` handlers, registered from `main.py` exactly like `register_task_broker_handlers` (D-CRM-8). **Registers no routes** — deliberately not imported from `__init__.py`. |

Rules that bind (from `user_management_contract.md`): identity from
`X-User-Email` only (R3); no `PUBLIC_ROUTES` additions — the BFF proxies everything (R2);
server-side checks first, UI hiding second (R9); email comparisons case-insensitive (R10);
destructive deletes report what cascaded (R7/R8 — deleting a deal reports its activities and
line-item counts).

---

## 5. UI — `/crm` app in the control plane

**Files:** `workbench/control_plane/src/app/crm/{page.tsx, components/, lib/}` + BFF proxy
`src/app/api/crm/[...path]/route.ts` (the `tasks` proxy pattern: `gatewayHeaders()`,
`force-dynamic`, 30s timeout). Registration (the five-place checklist,
`department_centers.md` §2): `FEATURES` += `"crm"` (`acb_auth/permissions.py`) ·
`feature_catalog` row — **all seven columns** per `130_org_access_control.sql`'s insert
shape: `('crm','CRM','Pipeline, leads and customers','/crm','apps', 55, false)` —
`sort_order` 55 (beside Tasks at 50, not defaulted to last), `is_default` **false**
deliberately: `feature:crm` reaches only `*`-holders (owner) and `admin` (`feature:*`)
until an admin grants it, because `manager`/`member` feature grants are enumerated in 130.
That is consistent with D-CRM-3 and stated again in WS-26c · `nav.ts` pane (Personal→no; **Centers**: it is the Sales Center's
module; also a flat `PANES` entry `/crm`) · `access.ts` `HREF_FEATURES` `["/crm","crm"]` ·
`centers.ts`: Sales Center's "Pipeline (Zoho CRM)" `planned` entry becomes
`{label:"CRM", status:"live", href:"/crm"}` · `test_org_access_control.py` invariants extend.

**Surfaces (Phase C):**
1. **Deals kanban** (the landing tab): lanes = `crm_deal_statuses` by `position`, colored;
   cards show name/org/amount/owner/stage-age; drag → `PATCH status_id`; per-lane count +
   ₹ total. List view toggle with the shared list contract (sortable columns, filter chips —
   the email app's QuickFilters pattern).
2. **Leads / Contacts / Organizations lists** — same list engine, `converted` filter chip on
   leads.
3. **Record sheet** — URL-as-state (`?deal=<id>`), opened over the list, no `/[id]` routes
   (trycompai pattern; back button closes). Left: timeline (newest-first, status changes and
   notes/tasks/calls inline, quick composer for note/task/call). Right: fields panel with
   inline edit, org/contact cards, owner, status pill dropdown, Convert button on leads.
4. **Convert modal** — resolves dedup interactively: pick matched contact/org or create new
   (§3.7's caller-chosen ids).
5. **Quick-create modals** (~6 fields) per entity.
6. **Pipeline settings** (`?tab=settings`, WS-26f f2) — three grids over the existing admin
   API, plus the Zoho stage pull whose `?apply=true` is an owner gate.
7. **Reports** (`?tab=reports`, WS-26g) — the four blocks of §5.1 system 2 rendered from
   `/crm/reports/*`: forecast by stage, funnel, win/loss, owner leaderboard. Another tab on
   the SAME URL grammar rather than a route, so the record sheet stays open across it
   (`?tab=reports&deal=<id>` is a link somebody sends). ⚠️ **It computes nothing.** Every
   figure is server-side and rendered verbatim; `lib/reports.ts` holds bar widths and the
   wording only. The one formula that legitimately exists on both sides is `board.ts`'s
   weighted ₹, and the shared fixture `tests/fixtures/crm_weighted_parity.json` is what
   holds it to the SQL — read by pytest and vitest, because two independently typed tables
   are not parity.

Theming: Tailwind v4 semantic tokens (`bg-background`, `text-muted-foreground`, …), Lucide
icon names as strings, `useViewMode()` for mobile. State: zustand store + pure helpers in
`lib/` with colocated vitest tests (the `tasks` layout).

**As built (WS-26c).** `lib/` holds everything server-shaped and pure, and each file is
unit-tested: `urlState.ts` (the URL grammar — `?deal=` opens the sheet over the list,
`?sort=`/`?dir=` make a sorted list shareable, and `selectTab` drops the filters that do not
travel, `sort` included because the keys are a **per-entity server allowlist** and a stale
one is a 422 that empties the list), `board.ts` (lane order, tone, the move plan, the
optimistic re-tally, and `needsLostReason`), `filters.ts` (the list contract,
including *never* sending `?status_id` to an entity without a pipeline), `convert.ts`
(§3.7's match rules, mirrored so the modal pre-selects what the server would do),
`format.ts` (₹ in `en-IN` lakh/crore grouping, stage age, dwell) and `api.ts` (the BFF
client; refusals keep their status so a 409 can be explained rather than reported as a
failure). `store.ts` is a thin zustand store whose one rule is that a **write re-reads what is on
screen, whatever the response was** — a stale row after a 409 reads as success, and a
created record that is invisible until somebody hits refresh is the same lie told the other
way round. It keeps the last loaded view so `refreshCollection()` can re-read the board or
the list without every caller threading the view back in.
`components/` is composition only. ⚠️ `CenterApp` in `lib/centers.ts` is a union
discriminated on `status`, so a `live` entry without an `href` is a **compile** error: the
existing `test_centers_registry_matches_the_feature_vocabulary` reads each Center's
`feature:` field and nothing else, so it cannot see a mistake in `apps[]` at all.
Runtime twin: `src/lib/centers.test.ts`.

### 5.1 Pipeline blueprint — order, probability, forecast, discipline *(added 2026-08-07)*

**Why this section exists.** The owner's first working session on the live board
(2026-08-07) found lanes out of order and imported stages at 0% probability. That is not
a modeling gap: `crm_deal_statuses` has carried `position` and `probability` since 144,
`pipeline.py` already inherits the stage default into a deal on entry, `crm_deals` carries
a per-deal `probability` that the backfill filled from Zoho's own field, and `admin.py`
exposes full CRUD over all of it. It is two delivery gaps. (1) The importer, refusing to
invent semantics for a stage it has never seen, **appends** it past the last position with
`probability 0` (`import_zoho.py::_ensure_status`) — and because 144's six seed lanes hold
*renamed* variants of Zoho's defaults ("Proposal" vs the tenant's likely
"Proposal/Price Quote"), name-match missed them, so the tenant's real stages sit at the end
of the board behind seed lanes that may hold zero deals. (2) The admin API is **headless**
— there is no settings surface, so nobody can fix (1) without curl. This section is the
plan of record for the pipeline as a *system*; §9's WS-26f–i are its tickets.

**The pipeline science (what the numbers mean, so nobody re-derives it wrong):**

- A **stage** carries a default win probability — the historical chance that a deal
  sitting in it eventually closes won. B2B benchmarks (and 144's seeds agree):
  qualification 10–20, needs analysis 20–30, proposal 40–60, negotiation 70–90, won 100,
  lost 0. These are priors, not gospel — once a quarter of native history exists,
  recalibrate them against `crm_status_changes` conversion data (WS-26g's funnel makes
  that a read, not a project).
- A **deal** may override its stage's default (`crm_deals.probability`, nullable; on
  entering a stage a NULL inherits the stage default — shipped behaviour). A rep who
  knows the champion just left marks a Proposal-stage deal at 20 without moving it.
  Deal-level probability is what forecast math reads — never the stage's.
- **Weighted pipeline** = Σ(`amount` × `probability`/100) over deals in open/ongoing-type
  stages. It appears wherever money already appears: per-lane under the existing ₹ total,
  a board-header rollup, and WS-26g's reports.
- **`type` stays king.** The machine class (`open/ongoing/won/lost`) is what stamps
  `closed_at`, drives funnel math and the sync's semantics. Probability *informs*, `type`
  *decides*: a mis-set 100% stage can lie to a forecast but cannot close a deal. Zoho's
  `forecast_type` maps onto `type`; do not grow a parallel enum (D-CRM-10).

**The four systems** *(the operative dispatch order is §9.0's demo critical path,
owner-directed 2026-08-07 — it interleaves these with the WS-26d slices and defers
3 and 4 past the demo)*:

1. **Pipeline truth + settings (WS-26f).** Repair the live stage set from Zoho's own
   pipeline metadata (`settings/pipeline` carries the tenant's real `sequence_number` and
   `forecast_type` per stage), then give the pipeline a management surface — drag-reorder,
   inline probability/color/type, lost-reason manager — so the next fix never needs an
   engineer. The sales team manages its own pipeline (D-CRM-3).
2. **Forecast & funnel (WS-26g).** Weighted totals on the board; a reports tab with
   funnel conversion, per-stage dwell, win rate, cycle time, lost-reason breakdown and an
   owner leaderboard. `crm_status_changes` has recorded every transition with actor and
   timestamp since day one — the report is a query, not an instrument.
3. **Stage discipline (WS-26h).** Entry requirements per stage (the lost-reason-on-lost
   mechanism, generalized: e.g. no entering Proposal without an `amount`) and **rot** —
   a deal older in-stage than the stage's threshold wears an age badge
   (`status_changed_at` is already the stage-age clock). Pipedrive's lesson, adopted
   deliberately: discipline enforced by *visibility*, not locks.
4. **Data management (WS-26i).** Duplicate merge (the convert modal's match rules already
   *find* duplicates; merge is the missing verb), bulk actions on the lists, CSV
   import/export, saved views on the URL grammar `urlState.ts` already defines.

**Deliberate deferrals — written down so nobody "helpfully" builds them early:**
**multiple pipelines** (one sales motion, one pipeline; the additive path is a
`pipeline_id` FK on statuses+deals and a board switcher — D-CRM-11 holds until a second
motion exists); **outreach sequences/cadences** (that is scheduled-send territory, and
schedule-send is owner-PARKED in the email app); **AI deal scoring** (needs a quarter of
native funnel history; the log is accruing it now).

---

## 6. Integrations — bind, don't rebuild

- **Email (Phase D, highest leverage):** timeline resolution joins existing email tables by
  address — a CRM contact/lead with `email` shows its threads read-time from the email app's
  store (single Outlook account today; account-scoped). **No link table in v1** (D-CRM-5).
  Optional `CRM_AUTO_LEAD` (default **OFF**, flip = OWNER-GATE §6): unknown inbound sender →
  draft lead with `source='email'`, honoring the email app's suppression doctrines. Respect
  the auto-drafting directive: this creates CRM rows, never email drafts.
- **Tasks:** v1 keeps CRM follow-ups as `crm_activities type='task'` (due date, completion,
  timeline-visible). Deep `gtd_items` linking is deferred — recorded future work, not v1.
- **WhatsApp (Phase D):** `routes/whatsapp/transport/context.py::_KNOWN_SYSTEMS` gains
  `"crm"` — **BUILT 2026-08-06, and PARSE-ONLY.** That constant is an allowlist read by
  `parse_entity_ref`, so adding `"crm"` changes exactly one behaviour: a
  `crm:<kind>:<uuid>` ref that somebody sets by hand parses into an `EntityRef` instead of
  being discarded as an unknown system. **Nothing writes `wa_contacts.entity_ref` — for any
  system, anywhere in the repo** (pinned structurally by `tests/unit/test_crm_agent.py`), so
  there is no CRM linker yet; and the `crm` block on `ChatContextModel` is still `None`,
  which is the other half of parse-only. Writing the ref and filling that block is a later
  slice, and it owes both halves together — a link the drawer cannot render is not a link.
- **Agent (Phase D):** `apps/agents/agent-crm/` (name `crm-assistant`; `runtime:"maf"`,
  `OpenAIChatCompletionClient`, `X-CC-Agent` headers — the `agent-email-assistant` template,
  **including its `_headers()` fail-closed identity rule**: a run that cannot resolve its
  acting user refuses rather than calling the gateway with the bearer alone, which
  `acb_auth/deps.py` §1b would read as SERVICE_ACCESS).
  **READ half BUILT 2026-08-06** — `search_crm`, `get_pipeline`, `get_record`,
  `get_timeline`, each a thin wrapper over the existing `/crm` routes (the agent never
  queries the DB).
  **WRITE half BUILT 2026-08-08** — `create_lead`, `update_deal_status`, `log_activity`,
  `convert_lead`, each `@_annotate_risk(destructive=True)` and each awaiting
  `request_confirmation` **before it constructs a mutating request**, with no
  `non_interactive_default` passed anywhere, so an unattended run writes nothing (B5 closed;
  see §9 `### WS-26d-write` for the as-built record and its four decisions).
  The verb allowlist was **widened, never deleted**: `_ALLOWED_METHODS = {"GET", "POST",
  "PATCH"}`, still checked inside the single round-trip helper every tool goes through, and
  `DELETE`/`PUT` — and any `_delete`/`_put` helper — are still absent, so the check that
  used to enforce "read-only" now enforces **"never destroys"**. There is no delete tool and
  no field-edit tool: the only mutations are the four above.
  Registered in `_KNOWN_AGENTS` + `_AGENT_REGISTRY` (`routes/agent.py`) +
  `agent_registry.json`. **Orchestrator routability comes from `_AGENT_REGISTRY`** —
  `orchestrator/agents.py:303-307` imports it directly (plus `_load_dynamic_agents()`); no
  runtime code reads `agent_registry.json`, which is kept for the catalog only.
  The existing `agent_registry.json` `sales` entry (codeless) and `agents.json`
  `agent-sales-assistant` (external repo) are untouched — different names, no collision.
- **Graph mirror consumers (Phase E):** `orchestrator/sales_views.py` and
  `scripts/reconciler.py` re-read from `crm_*`; `acb_graph/resolver.py`'s Zoho ingest path
  and the six shadow sales skills follow the repoint or retire with the mirror.

---

## 7. The Zoho migration path

### 7.1 Backfill + two-way sync (Phase B — building AGENT-SAFE; enabling against prod OWNER-GATE)

*(Re-scoped 2026-08-05, owner-directed — D-CRM-7: "while we are using Zoho, ensure we do a
faithful two-way sync, until we do away with Zoho entirely.")*

**Bootstrap:** `POST /crm/import/zoho {dry_run: bool}` performs the initial backfill —
pulls via the existing client (adding `list_leads`), maps:

| Zoho | Native | Notes |
|---|---|---|
| Accounts | `crm_organizations` | Account_Name, Website, Industry, Annual_Revenue, Phone, Billing_* → `address` |
| Contacts | `crm_contacts` | names, Email, Phone, Mobile, Title, Account link |
| Leads | `crm_leads` | names, Company→`organization_name`, Lead_Status→status (auto-created), Lead_Source |
| Deals | `crm_deals` | Deal_Name, Amount, **Stage→status auto-created** (position appended; type guessed: name ~ won/lost, else open), Closing_Date, Account + Contact links, Probability |
| Notes | `crm_activities type='note'` | parented via `$se_module` |
| Tasks | `crm_activities type='task'` | Subject, Due_Date, What_Id/Who_Id |
| Users | owner mapping | Zoho owner id → email via `list_users`; unmatched → import actor |

Idempotent: upsert `ON CONFLICT (zoho_id)`. Report:
`{module: {fetched, created, updated, skipped, errors[]}}`; `dry_run` fetches and reports
without writing.

**Continuous sync (the coexistence mode):** after backfill, a sync engine keeps both sides
faithful until cutover:

- **Single-writer seam, THROUGH the broker gate (D-CRM-8):** all Zoho write calls live in
  one writer module beside the client (`ingestion/sources/zoho/writer.py`), called from
  exactly one place — the sync engine in `routes/crm/sync_zoho.py` — grep-asserted. Every
  push routes through an Action-Broker gate exactly the way the tasks app's ClickUp writes
  do (`routes/tasks/providers.py::_broker_gate` — *"the single audited chokepoint for
  source-of-truth writes"*, default disposition auto-applies while `ACTION_BROKER_ENFORCE`
  is off): registered `crm.zoho_*` broker handlers, an audit row per push. This satisfies
  root `AGENTS.md` constraints #4/#8 instead of departing from them. Consequence, accepted
  deliberately: if the owner ever flips broker enforcement ON, sync pushes queue for
  approval and the sync becomes supervised rather than continuous. No agent tool, route
  handler or skill reaches Zoho directly; agents write the native CRM and the sync
  propagates.
- **Zoho → native:** incremental pull (`If-Modified-Since` — `client._list_module` already
  takes `modified_since`) for the four record modules + Notes/Tasks, plus Zoho's
  **deleted-records API** (a new read function beside `list_*`); Zoho deletes become
  native deletes (cascading activities per the FK graph, loudly counted in the sync
  report). Pulled rows carry `source='import'` — the existing CHECK vocabulary needs **no**
  new value and no ALTER. **Echo suppression is a stated rule:** a pull-applied write goes
  through `update_row(..., touch=False)` and never sets `zoho_dirty` — a two-cycle
  fake-client test must converge to zero pushes.
- **Native → Zoho:** dirty-tracking on the four record tables (`zoho_dirty` set by native
  writes to zoho-linked and native-new rows; `zoho_synced_at`); the engine pushes dirty
  rows (create ⇒ acquires `zoho_id`, update ⇒ upsert by id). Native deletes of
  zoho-linked rows write a **`crm_zoho_tombstones` row inside the delete transaction**
  (`module`, `zoho_id`, `entity_type`, `deleted_by`, `deleted_at`, `pushed_at NULL until
  pushed`) — a tombstone cannot be a column on a row that no longer exists. Native
  `note`/`task` activities push as Zoho Notes/Tasks; `status_change`/`system` activities
  never push (no Zoho analog — Zoho keeps its own stage history).
- **Pull cursors are schema too:** `crm_sync_cursors` (`module` PK, `last_pulled_at`,
  `last_run_at`, `last_status`) — incremental pull without a persisted per-module cursor
  re-reads the world after every restart. Both new tables + the dirty columns land in one
  migration at the next free number (landed as `145_crm_zoho_sync.sql`), with 26a's static
  idempotency fence extended to it — found by CONTENT, never by number (R1).
  **Two rules the cursor has to obey, both found by the 26b verifier:**
  1. **One snapshot per cycle, read before anything moves.** The pull phase writes cursors
     as it goes, so a deleted-records read that fetched the cursor *afterwards* would get
     the watermark that very cycle just wrote — and Zoho would answer "nothing deleted"
     for every deletion older than this cycle's newest `Modified_Time`. Those deletions are
     missed **permanently**: the cursor only moves forward, so no later cycle asks about
     that window again. `read_cursors()` snapshots once and both phases take it as an
     argument.
  2. **The watermark is the newest record that APPLIED, capped below the OLDEST that
     failed.** `If-Modified-Since` is a single instant, so a failed record stays retryable
     only while the cursor is strictly below it — and the failure may well be *older* than
     a success in the same batch, which is why the ceiling is the oldest failure and not
     simply "don't use the newest fetched". Full rule: nothing fetched ⇒ **keep** the
     existing watermark, adopting the cycle start only when there isn't one (so an unchanged
     module stops re-reading its table, while a momentarily empty window does not drag the
     cursor forward to now); nothing applied ⇒ stand
     still; a failure we cannot place in time (no readable `Modified_Time`) ⇒ stand still,
     because "we do not know" must not read as "nothing failed"; otherwise the newest
     applied, never backwards. **Accepted cost:** a record that fails every cycle pins that
     module's cursor and its window is re-read every ten minutes until it applies. That is
     the deliberate direction — the pull is idempotent, so a repeated window is wasted work
     while an advanced cursor is lost data — and it is never silent: `pull_record_errors`
     is non-zero and `last_status` stays `'partial'` on every such cycle. Per-record apply
     failures are also folded into the cycle summary count; a cycle that dropped nine
     records must not log `errors=0`.
  3. **The pull asks for `sort_by=Modified_Time&sort_order=asc`.** Zoho's default order is
     that key DESCENDING, so a record edited between page 1 and page 2 — by anyone, our own
     push included — jumps to the front and shifts every later record back one slot, and the
     record that sat on the page boundary is never returned. Ascending makes the sequence
     append-only for the duration of the pull.
- **Transaction shape — one bad record must not lose the batch, and Postgres does not
  agree by default.** A statement error aborts the whole transaction, not the statement, so
  a per-record `try/except` "survives" a bad row while in fact losing every row after it,
  the cursor write and the commit — and the next cycle repeats it identically forever.
  Every applied record and every push therefore runs inside a **SAVEPOINT**
  (`core.savepoint`), each phase commits its own work, and each pull module commits WITH its
  cursor. `_number()` additionally clamps values outside `NUMERIC(14,2)` to NULL, because
  Zoho's currency fields have no such ceiling and one fat-fingered amount is otherwise a
  poisoned transaction rather than a bad row.
- **The external write is committed before anything else runs.** Zoho's API has no
  idempotency token, so the window between "the create returned 200" and "the `zoho_id` is
  durable locally" is a duplicate factory: a crash or an aborted transaction in it loses the
  id while the record exists upstream, and every later cycle creates ANOTHER. Push → stamp →
  **commit**, per record. For the same reason `stop_crm_zoho_sync` signals and waits
  (`STOP_GRACE_SECS`) rather than cancelling: a cancel lands wherever the cycle happens to
  be, including inside that window.
- **One cycle at a time.** `run_cycle` takes a process-wide lock and a second caller gets
  **409** (`POST /crm/sync/zoho`) or skips (the loop). Two overlapping cycles see the same
  dirty rows and both create them upstream. ⚠️ The lock is in-process, which is correct only
  while the gateway runs as a single worker — a second worker needs
  `pg_advisory_xact_lock` on the same key.
- **Failure has a ceiling.** Both push queues (records + activities, and tombstones) carry
  `attempts` / `last_error` / `next_attempt_at`: exponential backoff, and after
  `MAX_PUSH_ATTEMPTS` the row is parked, counted in `pushed.given_up` and logged at ERROR.
  Without it a row Zoho will never accept sits at the front of an oldest-first `LIMIT` queue
  forever and starves everything behind it. A tombstone whose record Zoho no longer has
  (404, or a 200 carrying `RECORD_NOT_IN_MODULE`) is a **success** — the goal state holds.
- **Approval writes state back.** Under broker enforcement the queued push, when approved,
  runs through the same `apply_push_result` the inline path uses — otherwise every approval
  performs the write and records nothing, so the row stays dirty and each approval mints
  another copy upstream. The gate also refuses to enqueue a second proposal for an
  `(action, target)` already pending: the row is re-offered every cycle by design, which at
  ten-minute ticks is 144 identical inbox rows per day per stuck record.
- **Conflicts:** record-level last-writer-wins comparing Zoho `Modified_Time` against
  native `updated_at`; both-changed conflicts are counted and logged per cycle, never
  silent. No field-level merge in v1 (D-CRM-6 amended).
- **The sync must not mutate native data by echoing itself.** Three rules, all learned
  from the 26b verifier's round-trip reading:
  - **`source` is provenance and is written on INSERT only.** It is deliberately excluded
    from the `ON CONFLICT` arm (`core.INSERT_ONLY_COLUMNS`), so a row typed into this app
    stays `'manual'` after the sync pushes it up and pulls it back. Rewriting it would flip
    every native-origin row to `'import'` on its first echo — a silent one-way rewrite of
    the column the `?source=` filter reads.
  - **Zoho's required fields are PADDED on push and un-padded on pull.** Zoho makes fields
    NOT NULL that §3.2/§3.3 allow to be blank (a Contact needs `Last_Name`; a Lead needs
    `Last_Name` and `Company`), so the push fills them from a field that is always
    populated. `import_zoho.strip_padding_echo` drops a pulled value when — and only when —
    the native column is NULL *and* the value is exactly what we would have padded it from.
    Accepted cost, stated: a human in Zoho who genuinely types `Last_Name` = the first name
    onto a contact whose native surname is blank is indistinguishable from our padding, and
    the native column stays NULL. `PADDED_FROM` is held to `to_zoho_*` by a test that reads
    the builders' **source** (AST-walks each for the `or`-fallback that IS a pad) rather
    than restating the map — a pad added without a guard entry fails there.
  - **Anything DERIVED from a padded field is derived after the strip, never inside the
    mapper.** `crm_leads.lead_name` is §3.3's fallback chain over first/last/organization —
    two of which the push pads — so deriving it in `map_lead` folded our own padding into
    the display name: a lead called "Asha" came back "Asha Asha", and `lead_name` is on the
    conflict arm, so every cycle rewrote it. `map_lead` therefore does **not** emit
    `lead_name`; `apply_record` computes it after `strip_padding_echo`.
  - **Activity DELETES sync in NEITHER direction, while activity creates do.** A note
    deleted here survives in Zoho; one deleted in Zoho survives here. **Accepted v1 cost**
    (P2, 2026-08-05 review): an activity is an append-mostly log entry whose stale copy
    misleads nobody about the pipeline, Zoho is being retired, and closing it means a second
    tombstone path plus a delete predicate over a table with four nullable parents. Records —
    where a stale copy IS misleading — propagate deletes both ways. `apply_zoho_deletes`
    iterates `RECORD_MODULES` only and says so; `activities.delete_activity` says so too.
    ⚠️ Do not close one direction alone: a native tombstone without the matching Zoho→native
    delete makes the two sides disagree in a NEW way.
  - **A native field CLEAR does not reach Zoho, and the next pull restores the old value.**
    The push prunes `None` (sending it would CLEAR the field at Zoho, so a column we simply
    do not carry would blank the tenant's copy every cycle) — which means "user emptied
    this field" and "we have nothing for this field" are the same wire state, and Zoho's
    surviving value comes back on the next pull. **Accepted, not fixed:** distinguishing
    them needs per-field dirty tracking, i.e. exactly the field-level merge D-CRM-6 rules
    out for v1. Clearing a field on both sides, or clearing it in Zoho, both work.
- **Pipeline vocabulary flows DOWN only** while sync is on: stage/status picklists are
  managed in Zoho and auto-created natively (as backfill already does); native status
  creation is not pushed (Zoho picklist mutation needs settings-API writes — out of scope,
  and the vocabulary dies with Zoho anyway).
- **Cadence + switches:** the scheduled loop runs only when **`CRM_ZOHO_SYNC=1`** (ships
  OFF; flip is OWNER-GATE, §6; the flag reads from `acb_common` settings like its
  siblings). The loop follows the **gateway's own in-process scheduler pattern**
  (`routes/workflows/scheduler.py` / `routes/tasks/scheduler.py`, started in `main.py`'s
  lifespan) — NOT `ingestion/scheduler.py`: ingestion's pyproject cannot depend on the
  gateway, so a `routes/crm/` engine cannot be driven from there. Interval ~10 min.
  `POST /crm/sync/zoho` (same `admin:access:manage` floor) runs one cycle on demand and
  works regardless of the flag, because a hand-run cycle is an explicit admin act. The
  nightly graph-mirror sync in `ingestion/scheduler.py::_run_zoho` is untouched either way
  (Phase E retires it) — ⚠️ audit note 2026-08-05: no `deploy/` unit references the
  ingestion scheduler, so whether that 02:50 job actually runs on the box is unverified
  from the repo.

### 7.2 Coexistence (Phases B–D)
Both sides stay writable and faithful (§7.1). Native records without a `zoho_id` exist in
Zoho within one sync cycle of creation. The team can move to the native UI (Phase C) at
its own pace instead of on a cutover day.

### 7.3 Cutover (Phase E, OWNER-GATE)
Final sync cycle → parity check (per-module Zoho counts vs `crm_*` counts, plus owner spot
checks) → `CRM_ZOHO_SYNC` off → team stops writing to Zoho → sync engine, writer and
backfill endpoint retired with the rest of §7.4.

### 7.4 Retirement inventory (Phase E — exact paths, verified 2026-08-05)
`ingestion/sources/zoho/` (client kept if any consumer remains, else all four files) ·
`scripts/zoho_sync.py` · `scheduler._run_zoho` + its 02:50 cron · `queue.STREAM_ZOHO` +
consumer mapping · `main.py` zoho router include + `PUBLIC_ROUTES` `/webhooks/zoho` ·
`routes/integrations.py` Zoho card/health/test · `routes/oauth.py` `zoho-crm` ·
`settings.py` `zoho_*` · `key_store.py` `zoho-crm` · `acb_skills/integrations.py` `_zoho_crm`
+ `FIELD_TO_ENV` · `.env.example` Zoho block (**OWNER-GATE — plan-guard blocks agent
writes**) · `TriggerPanel.tsx` zoho option · tests `test_zoho_normaliser.py`,
`test_phase0_zoho_reconciler.py` · `agents.json`/`agent_registry.json` `zoho-crm`
integration declarations. Then **revoke the Zoho refresh token** — this executes part of
WS-2 (the standing "rotate Zoho token" P0 becomes "revoke", strictly better).

---

## 8. Decisions — `DECISION (agent-proposed, owner may overrule)`

- **D-CRM-1 — New `crm_*` tables; the Phase-0 graph tables are not extended.**
  `person`/`customer`/`deal` are a cross-system entity-resolution mirror (ClickUp/Odoo ids
  on the same rows) with different semantics from an operational CRM (no statuses-as-data,
  no activities, no leads). Rejected: growing the mirror in place — it would couple the CRM
  to `acb_graph`'s resolver semantics and every mirror consumer at once. Cost: Phase E owes
  the §6 repoint.
- **D-CRM-2 — Statuses are rows, not enums** (color/position/type/probability). The importer
  must represent Zoho's actual stage names, and the owner reshapes the pipeline without a
  deploy. Rejected: trycompai's hardcoded enum — their own docs show it froze their process
  into code.
- **D-CRM-3 — CRM data is org-visible to `feature:crm` holders in v1; `owner_email` is
  assignment, not ACL.** A CRM is a shared team surface (both reference products agree);
  D11 records one org, and the workflows app's org-wide-read v1 is the shipped precedent
  (`routes/workflows/crud.py` records it). *[D11 was re-taken by D15 (2026-08-08):
  org-wide-read v1 stays the within-org design, and cross-tenant isolation arrives via RLS at
  MT-1b — no hand-written org predicates; see work_plan.md D15 and R5.]* 404-not-403 owner
  scoping (R5) deliberately does
  **not** apply — recorded departure per contract §7. Revisit with WS-14's `group:` grants
  when colleague #1 lands.
- **D-CRM-4 — Engine seam:** `gateway/db.py::get_engine()`; `crm` consumes it, `tasks` is
  converted as proof, the other ten call sites are out of scope. Rejected: importing another
  app's `core` (cross-app coupling) and a 13th module-level engine (the exact BO-10
  anti-pattern).
- **D-CRM-5 — Email binding is a read-time address join, no link table in v1.** One account,
  modest volume; a link table adds a sync obligation with no v1 payoff. Cost: no manual
  "attach this thread to that deal" — deferred with the link table.
- **D-CRM-6 — ~~Import is last-import-wins until cutover~~ superseded by D-CRM-7's sync;
  the surviving half:** conflicts resolve at record level (last-writer-wins on modified
  timestamps), never field-level merge — complexity without a customer until colleagues
  are in the app.
- **D-CRM-7 — `DECISION (owner-answered 2026-08-05)`: coexistence is a faithful TWO-WAY
  sync, not a one-way import.** Owner's words: *"while we are using Zoho, ensure we do a
  faithful two way sync, until we do away with Zoho entirely."* This overrules the
  original §1 non-goal (no Zoho write path) and re-scopes WS-26b per §7.1. The agent-held
  boundary that survives: **the sync engine is the single Zoho writer** and the whole
  write path retires with WS-26e. Retirement stays the end state.
- **D-CRM-8 — sync pushes route through the Action-Broker gate** (agent-proposed,
  audit-forced 2026-08-05). The first draft claimed the ClickUp sync bypasses the broker
  as precedent — **the audit measured the opposite**: `_broker_gate` is the tasks app's
  single audited chokepoint and auto-applies while enforcement is off. The Zoho writer
  follows it: `crm.zoho_*` broker handlers, one audit row per push, auto-apply default.
  This also re-opens WS-1's struck clause on our terms — the "Zoho write client" its row
  said didn't exist is now specced here, and its broker handlers are WS-26b's, not BO-1's.
  Accepted consequence: broker enforcement ON turns the sync supervised.
- **D-CRM-9 — `DECISION (owner, 2026-08-06)`: agent-originated CRM writes enter the Zoho
  push queue exactly like human ones.** Agent-originated and (future)
  `CRM_AUTO_LEAD`-originated CRM writes are treated identically to a person's: every native
  write is born `zoho_dirty = true` (`routes/crm/core.py::mark_dirty_on_insert` /
  `mark_dirty_on_update`) and pushes on the next sync cycle, which `POST /crm/sync/zoho` runs
  with or without `CRM_ZOHO_SYNC`. Faithful two-way sync (D-CRM-7) applies to every native
  write regardless of author — **no special case, no held-back tier.** The safety boundary
  for agent writes is the confirmation gate on the tools themselves (the future write-tools
  slice), not a fork in the sync semantics. Read together with the create-half rule the
  mechanism already has: a row arriving WITH a `zoho_id` came from Zoho and is not born
  dirty, so "native write" here means exactly what the code already keys on. This resolves
  the WS-26d audit's push-queue blocker; the confirmation mechanism (B5) is still open.
- **D-CRM-10 — `DECISION (agent-proposed, owner may overrule)`: probability = stage
  default + per-deal override; forecast math reads the deal, never the stage; `type`
  alone decides.** `crm_deal_statuses.probability` is the prior a deal inherits on
  entering the stage (shipped, `pipeline.py`); `crm_deals.probability` is what every
  forecast computation reads. Won-type stage rows are pinned to 100 and lost-type to 0 —
  enforced at the admin PATCH (422 on contradiction, explicit beats silent rewrite), not
  trusted downstream. Weighted pipeline = Σ(`amount` × `probability`/100) over deals in
  open/ongoing-type stages only; `won` contributes to *closed* revenue, never to
  pipeline. Probability never triggers mechanics: `type` stamps `closed_at`, gates the
  lost-reason requirement, and drives funnel math. Zoho's `forecast_type`
  ("Open"/"Closed Won"/"Closed Lost") maps onto `type` at the WS-26f metadata pull; no
  parallel forecast-category enum is added.
- **D-CRM-11 — `DECISION (agent-proposed, owner may overrule)`: one pipeline until a
  second sales motion exists.** No `crm_pipelines` table now. The additive path when a
  second motion is real: `pipeline_id` FK on `crm_deal_statuses` + `crm_deals`, a board
  switcher, nothing else changes shape. WS-26f's metadata pull verifies the premise — if
  `settings/pipeline` returns more than one pipeline for the layout, this decision goes
  back to the owner *before* the repair is applied, because a name-match repair against
  the wrong pipeline scrambles the board it was meant to fix.

- **D-CRM-12 — `DECISION (agent-proposed, owner may overrule)`: the CRM agent renders an
  email thread as sender, subject, thread-status and date — and NEVER its snippet or body
  text.** The timeline join is caller-scoped, so `get_timeline` returns the asking
  person's own mail. An agent answer does not stay with the asker: it is written into a
  chat transcript, and a **room** has other participants who can read it
  (`groups_sessions_authority.md`). Rendering a snippet there publishes the CONTENT of one
  member's inbox to everybody present; rendering sender+subject publishes only that a
  conversation exists, which is what any email client's list view already shows a person
  looking over your shoulder. The line is drawn at content on purpose — it is the one
  place where "what the screen shows" and "what the agent may say" must differ, because
  the screen has an audience of one. The `/crm` UI keeps the snippet (`EmailEntry`): the
  payload is unchanged and the browser is the caller's own. If the owner would rather have
  richer chat answers, the thing to change is this line, not the payload — and the room
  clearance filter is the mechanism that would have to carry it instead.

- **D-CRM-13 — `DECISION (agent-proposed, owner DELEGATED the call 2026-08-11)`: entry
  requirements gate the stage a caller CHOSE, never the stage the server DEFAULTED to.**
  WS-26h shipped with CREATE ungated and recorded the gap; the 2026-08-11 audit found
  that closing it naively inverts the feature. Measured, not argued: `QuickCreateModal`
  sends no `status_id` at all and `ConvertDeal` has no `status_id` field
  (`_create_deal` always calls `load_default_status`), so **every deal the product
  creates lands in the default stage.** The recorded hole is therefore reachable by
  direct API call only — not by any UI, and not by the CRM agent, which POSTs only
  `/crm/leads` and `/convert`. Meanwhile a gate on the defaulted path would 422
  quick-create and *every* lead conversion the moment an owner puts `required_fields`
  on the default lane, which `admin.py` permits with no restriction. Worse:
  `organization_id` is requirable and **neither surface can supply it** —
  `ConvertModal.tsx:126` renders "This lead names no company, so no organization is
  created" and offers no picker — so a default lane requiring it would make those
  leads permanently unconvertible.

  **The principle, so this is a rule rather than a workaround: entry requirements
  exist to prove a deal EARNED its way into a stage, and a deal in the default lane
  has claimed nothing yet.** The default lane is where deals start, not somewhere they
  advance to; demanding proof of progress to enter it is a category error. Creating
  straight into a late stage by API *is* a claim worth checking, and that is precisely
  the case this gates.

  This does **not** overturn `records._resolve_status`'s recorded doctrine — *"the gate
  belongs to the status, not to the verb that reached it."* That comment governs the
  **lost-reason** rule, which is a property of a TERMINAL status: a deal is lost or it
  is not, however it got there. Entry requirements are a property of an ENTRY lane. Same
  word, two different questions; the doctrine is scoped to the terminal case and stays
  there. Anyone reading the two side by side should read this paragraph, not re-derive
  the tension.

  **Direction of travel, deliberately chosen:** this is the permissive reading, and
  tightening later is cheap — delete the "caller supplied `status_id`" condition and
  the strict rule falls out. The strict reading is not cheaply reversible: it ships an
  organization picker, tells users conversion can be refused, and makes the ~1,516
  imported leads a judgement call nobody can automate. Same expand-then-contract logic
  R6 applies to schema, applied to product behaviour.

  ⚠️ **Rider — NOT built under this decision, owed back to the owner.** `admin.py`
  currently allows `required_fields` on the default status, which under D-CRM-13 is
  configuration that silently does nothing. Refusing it there would make the rule
  self-evident at the point of use instead of documented three files away. It is banked
  rather than assumed because it changes an existing settings surface and could reject
  configuration already saved.

**Build-time decisions, recorded post-hoc (WS-26a implementer, 2026-08-05 — owner may
overrule any of them):**
- **B1** — `POST` defaults `owner_email` to the acting user when the field is absent
  (explicit `null` stays unassigned); without this the `owner` filter matches nothing and
  WS-26a ships no owner picker.
- **B2** — lead `dwell_seconds` derives from `max(crm_status_changes.changed_at)` falling
  back to `created_at` (leads deliberately carry no `status_changed_at` column).
- **B3** — the lost-reason requirement applies on **create into** a lost-type status, not
  only on transition — the rule belongs to the status type. *(Verifier addendum
  2026-08-05: the sibling §3.6 rule follows the same reach — creating a deal directly in a
  won/lost status stamps `closed_at`, matching what the transition path does.)*
- **B4** — `insert_row`/`update_row` coerce JSONB + temporal params explicitly (bare
  `text()` declares no column types to asyncpg); read half mirrors
  `routes/tasks/core.py::_parse_jsonb`.
- **B5** — `crm_status_changes.entity_type`/`changed_at` and the `crm_activities` target
  CHECK gained NOT NULL where §3.8/§3.9 were silent (strengthening only).
- **B6** *(adversarial review P1, 2026-08-05)* — "converted" is keyed on
  `converted_deal_id IS NOT NULL`, never on `converted_at`: both the lead-list filter and
  the re-convert 409. The timestamp version stranded a lead invisibly forever when its deal
  was deleted (FK SET-NULLs the link, the timestamp survives), with SQL as the only
  recovery. Deleting a converted deal now returns its lead to the working list,
  re-convertible.
- **Review repairs, same pass** — `record_activity` keeps its own docstring's promise and
  bumps the target's `last_activity_at` itself (the next caller — WS-26b's importer,
  WS-26d's agent tools — cannot ship records that sort as never-touched);
  `PATCH /crm/activities/{id}` refuses `status_change`/`system` rows exactly as DELETE does
  (one rule, both verbs); the three `owner_email` indexes are `lower(owner_email)` to match
  the only predicate that reads them (R10), and contacts gained the missing one.
- **Open question for the owner (deliberately unimplemented):** reopening a won/lost deal
  leaves `closed_at` stale — §3.4 stamps and never clears. Clearing on a move back to a
  non-terminal type is one line in `apply_status_transition` once decided.

**Build-time decisions, WS-26c implementer (2026-08-05 — owner may overrule any of them):**
- **C1 — the deal-contacts endpoints are a NEW module (`routes/crm/deal_contacts.py`), not
  more of `records.py`.** The package's layout convention is one feature module per concern
  registering on `core.router`, and a deal's people are a sub-resource with an invariant of
  their own. It also keeps WS-26c's gateway diff off `records.py`, which WS-26b is editing
  in parallel. Rejected: `records.py` (a fifth concern in a file whose whole shape is "four
  entities × five verbs").
- **C2 — "at most one primary per deal" is enforced on the shared seam
  `core.link_deal_contact`, not per route.** 26a recorded the rule as convention with one
  writer; adding endpoints would have made it two opinions. The convert path was moved onto
  the same function (its bare `insert_row` is gone), and the demote-before-promote order is
  deliberate — the intermediate state is "no primary", never "two". Pinned structurally: no
  module outside `core.py` may INSERT into `crm_deal_contacts`.
- **C3 — `organization_name` is projected by wrapping the base SELECT in a derived table**
  (`core.project_joined`), not by inlining the join into `FROM`. `crm_organizations` also
  carries `owner_email`, `source`, `name` and the timestamp trio, so an inlined join makes
  every unqualified predicate `list_contract` renders ambiguous — the fix would be to
  qualify all of them, in all four entities, to serve one. Wrapping also means the join runs
  over one page rather than the table. The outer `ORDER BY` is not decoration: a join over
  an ordered subquery does not preserve its order.
- **C4 — "was the lead name hand-edited?" is answered by recomputing, not by a column.**
  `core.lead_name_is_derived` compares the stored name to what the fallback chain would
  produce; a `lead_name_is_custom` flag would have to be maintained by every writer (the
  importer, the sync engine, the agent tools) and the one that forgets it silently reverts a
  typed name. Accepted cost: typing exactly what the chain would have produced leaves the
  name derived — which produces the same string, so nobody can tell.
- **C5 — the explicit-`null` guard is a per-table map of NOT NULL **defaulted** columns**
  (`core.NOT_NULL_DEFAULTED`), checked inside `insert_row`/`update_row` so a route added
  later inherits it. Keyed by table because `probability` is NOT NULL DEFAULT 0 on
  `crm_deal_statuses` and nullable on `crm_deals` — a flat column set would refuse a
  legitimate "clear the probability". The map is derived from the migration and pinned both
  ways by `test_crm_migration.py`, which caught `crm_status_changes` missing from the first
  version.
- **C6 — the frontend keeps the wire's snake_case.** The tasks app maps snake→camel at its
  client boundary; the CRM's field names ARE its column names (`row_to_model` maps
  generically by field name), so a rename layer would be a second vocabulary to keep in step
  with migration 144 — and the copy is what drifts.
- **C7 — the board asks for a lost reason BEFORE sending the move.** The gateway answers 422
  before writing any of the transition's three effects, and the reason travels *with* the
  `PATCH` rather than in a second request, so the move either lands whole or not at all.

**Adversarial-review repairs, same branch (2026-08-05 → 06). Three changed a decision:**
- **C8 — `sort`/`dir` are VIEW state, in the URL, not component state.** The first pass held
  them in `useState`, `listQuery` never sent them and the load effect never watched them, so
  a column header flipped its own arrow and re-issued an identical request — a control that
  changes its appearance and nothing else, which reads as working. Putting them in `CrmView`
  fixes all three at once (the effect already keys on view fields), makes a sorted list
  shareable, and lets `selectTab`'s existing "filters that do not travel" rule clear a stale
  key — which matters more here than for the others, because sort keys are a per-entity
  server allowlist and a carried-over key is a 422, not an odd order. `canSortBy` is
  belt-and-braces behind it for the hand-edited-URL case.
- **C9 — `similarOrganizations` excludes the exact match by IDENTITY, not by comparing
  strings.** The review found the exact match rendered twice (both entries carrying the same
  id, so both drew selected) because the filter compared a lowercased candidate against a
  non-lowercased lead name. The suggested fix — fold both sides — also removes the duplicate,
  but it hides every case-variant: `matchOrganization` is case-SENSITIVE, so "bosch india" is
  *not* the exact match and is precisely the near-miss the list exists to surface. Excluding
  `o.id === exact?.id` fixes the duplicate and keeps the near-miss.
- **C10 — `link_deal_contact`'s `is_primary` is tri-state (`None` = leave it alone), like
  `role`.** `DealContactIn.is_primary` defaulted to `False`, so "set this contact's role"
  demoted the deal's primary as a side effect: a field the caller never mentioned deciding
  something. Explicit `false` still demotes — a deal may legitimately have no primary.

The other four were straight defects, fixed without a decision: `createRecord` and a
*successful* `patchRecord` now re-read the collection (`refreshCollection`); `moveDeal`'s
post-move re-read carries the board's owner filter instead of widening it to the whole
pipeline; the kanban's `dragstart` sets a `dataTransfer` payload, without which Firefox
never starts the drag at all; and `api.moveDeal` now consumes `board.moveRequest` (extended
to carry the lost fields) rather than building a second, diverging shape beside a function
whose comments claimed to be the only one.

---

## 9. Tickets — WS-26a…i (every item AGENT-SAFE unless labeled)

### 9.0 Demo critical path — the dispatch order *(owner-directed 2026-08-07)*

The owner's directive: **demo-ready as soon as possible, without compromising the
plumbing.** Operationally that means the queue below is a strict priority order for
dispatch — it re-*sequences* the open tickets, it does not thin them. Every slice still
runs the full chain (spec-auditor → implementer → verifier → diff-reviewer → PR), every
done-when stands, and every §6 owner gate stays a gate.

**The demo narrative the order serves** (what a viewer sees, in the order they see it):
a board that looks *right* (real stages, right order, weighted ₹) → a record that looks
*alive* (open a deal: the actual email thread history) → a number the boss cares about
(weighted pipeline, leaderboard) → the AI story (ask the assistant — already live; have
it create a lead behind a confirmation card) → optionally, the closer: an email from an
unknown sender becomes a lead on its own.

**Build order — strict, one reason each:**
| # | Slice | Why here | Parallel? |
|---|-------|----------|-----------|
| D1 | **WS-26f** (incl. the new f4 backfill) — ✅ **BUILT 2026-08-07, not run** | The board is the demo's first screen; today it is the broken part. | — |
| D2 | **WS-26d-email** | The "this is not a toy" moment. Disjoint files from D1 (`activities.py`/`Timeline.tsx` vs. importer/admin/settings). | ∥ with D1 |
| D3 | **WS-26g** — ✅ **BUILT 2026-08-07** (branch `ws-26g-reports`, no migration) | The forecast number. **After D1** — f2 and the reports tab both extend the `page.tsx`/`urlState.ts` tab grammar, and two parallel PRs there is a needless conflict. | after D1 |
| D4 | **WS-26d-write** ✅ **BUILT 2026-08-08** | The AI-creates-a-lead demo beat. Lives in `apps/agents/agent-crm/` — collides with nothing above. No migration. | ∥ with any |
| D5 | **WS-26d-autolead** — ✅ **BUILT 2026-08-08, flag OFF, NOT flipped, NOT deployed** (migration **163** — renumbered from 158 after #404/#399 took 157-162) | Built whenever; the **flip is OWNER-GATE** and pushes real leads into Zoho (D-CRM-9) — demo it only if the owner wants that story told live. | ∥ with any |

**Deferred until after the demo, deliberately — not demoted:** WS-26h (discipline),
WS-26i (data management), WS-26e (cutover). No demo viewer sees them; they lose nothing
by waiting, and h explicitly depends on f2's grids anyway.

**Honesty notes for the demo itself** (narrate these, don't paper over them):
- `crm_status_changes` only records **native** transitions, so funnel/dwell start
  sparse and fill as the team actually works the pipeline. That is the feature working,
  not a gap.
- Imported won/lost deals carry **no real close timestamp** — f4 backfills `closed_at`
  from Zoho's `Closing_Date` as a labeled proxy so win/loss and won-₹ render non-empty;
  native closes stamp true times from then on.

**The owner's pre-demo runbook** (every act here is §6-gated or an owner act by nature):
1. Merge D1's PR when it lands → approve the f1 `?apply=true` repair (dry-run report
   first), or hand-set the stages in the f2 settings tab if the metadata probe reports
   no-scope.
2. Merge D2–D4 as they land (deploy applies their migrations automatically).
3. Grant `feature:crm` to everyone who will sit in the demo — or demo from the owner's
   login and say so.
4. Optional: flip `CRM_AUTO_LEAD` (understanding each auto-lead queues for the live
   Zoho tenant per D-CRM-9).

### WS-26a — Schema + feature registration + core API · ✅ **BUILT 2026-08-05**
*(Audited GO-NARROWED 2026-08-05; blockers A/B folded in below. Landed as
`infra/postgres/144_crm.sql` + `apps/services/gateway/gateway/db.py` +
`apps/services/gateway/gateway/routes/crm/{core,records,pipeline,activities,admin}.py`,
fenced by `tests/unit/test_crm_{routes,pipeline,convert,migration}.py` — 185 cases, zero DB
and zero network. **Built, not deployed:** applying the migration is the owner's move.)*
Done when:
1. The Phase-A migration (next free number) creates §3.1–§3.10; idempotency is **statically
   asserted** in `tests/unit/test_crm_migration.py` (every `CREATE TABLE`/`CREATE INDEX`
   carries `IF NOT EXISTS`, every seed `INSERT` carries `ON CONFLICT`) — §10 runs no DB, so
   inspection-only idempotency doesn't count. **`schema.generated.sql` is out of scope**:
   its resync needs a migrated live DB (`scripts/dump_schema.sh`), it is ~43 migrations
   stale repo-wide, and regenerating it here would bundle an unrelated schema resync into
   this PR — it is a separate owner-run chore.
2. `"crm"` is in `FEATURES` and the `feature_catalog` row exists — fenced by a **new**
   invariant in `tests/unit/test_org_access_control.py` that derives every
   `feature_catalog` INSERT slug from `infra/postgres/*.sql` (the `_schema_cascade.py`
   technique) and pins it against `FEATURES` both ways; any pre-existing drift goes in an
   explicit commented exceptions literal, never a silent filter. The existing invariants
   are `center.*`-only by construction and fence nothing for an `apps`-category slug.
3. `routes/crm/` exists per §4, registered fail-soft in `main.py`; `gateway/db.py` exists;
   `routes/crm` contains **zero** `create_async_engine` calls and `routes/tasks/core.py`
   consumes `gateway.db` (grep-assertable both ways).
4. CRUD + list contract works per §4 (sort allowlist rejects unknown keys with 422);
   status transition writes `crm_status_changes` + `status_change` activity +
   `status_changed_at`, fills probability default, requires lost reason on `lost`-type
   (422), stamps `closed_at`.
5. Convert implements §3.7 including email-match dedup, 409 on re-convert.
6. `tests/unit/test_crm_routes.py`, `tests/unit/test_crm_pipeline.py`,
   `tests/unit/test_crm_convert.py` pass **named** (never bare `tests/unit/`), no
   DB/network; the wiring fence is a `"gateway.routes.crm"` entry in `GATED_ROUTERS`
   (`tests/unit/test_org_access_enforcement.py`), added deliberately — that registry is
   the test's opinion, not the router's.

### WS-26b — Zoho two-way sync · ✅ **BUILT 2026-08-05** / 🔴 **OWNER-GATE to enable against prod**
*(Re-scoped 2026-08-05 per D-CRM-7; audited GO-NARROWED the same day and repaired —
blockers 1–9 folded in below. Landed on branch `ws-26b-zoho-sync` as
`ingestion/sources/zoho/{client.py→list_leads+list_deleted, writer.py}` +
`infra/postgres/145_crm_zoho_sync.sql` +
`apps/services/gateway/gateway/routes/crm/{import_zoho,sync_zoho,broker_handlers}.py` +
the `core.py` dirty-marking choke point and `records.py` tombstone-in-delete, fenced by
`tests/unit/test_crm_zoho_{import,sync}.py` — 129 new cases, zero DB and zero network, plus
26a's migration fence extended to the second migration. **Built, not run:** no backfill and
no cycle has ever executed against the tenant.)*

**Build-time decisions, recorded post-hoc (WS-26b implementer — owner may overrule):**
- **C1** — the dirty-marking choke point is `core.insert_row`/`core.update_row`, keyed on
  the EXISTING `touch` flag. A pull applies with `touch=False`, which already meant "do not
  bump `updated_at`"; reusing it means "is this a real edit" and "should this be pushed back"
  are one switch that cannot disagree, and a route added later inherits both. The create half
  keys on the payload instead — a row arriving with a `zoho_id` came FROM Zoho and is not born
  dirty — so no caller has to remember a flag.
- **C2** — **activities carry no dirty column.** Their push signal is a NULL `zoho_id`: an
  activity is a log entry, so "has it been pushed" and "has it changed" are the same question,
  and stamping the id on success is what makes the push idempotent. This is why migration 145
  alters only the four record tables.
- **C3** — the Zoho→native field mapping lives in `import_zoho.py` and the sync engine
  imports it. A backfill and a pull that map `Deal_Name` differently is a divergence nobody
  notices until the counts stop matching.
- **C4** — the broker gate + `crm.zoho_*` handlers live in `routes/crm/broker_handlers.py`,
  which deliberately does **not** import the writer: an approved queued push re-enters through
  `sync_zoho.execute_push`, so the writer keeps exactly one import site and the grep assertion
  stays meaningful.
- **C5** — a **queued** push (broker enforcement ON) leaves the row dirty and stamps nothing.
  BO-1b is the same bug on the ClickUp side: treating the `pending` marker as success shows a
  row as synced that exists in no tenant.
- **C6** — a Zoho→native delete bypasses `records.delete_record` and therefore writes **no**
  tombstone. A tombstone there would push the deletion straight back at the tenant that just
  reported it — an echo in the most destructive direction available.
- **C7** — `DELETE /crm/<entity>/{id}` now takes the acting user (for `deleted_by`) and its
  response gained `zoho_delete_queued`, so a caller is told the deletion leaves this app.

**Verifier repairs (2026-08-05, same branch — five findings, all taken):**
- **V1 (was a FAIL)** — the deleted-records read took its cursor *after* `pull_phase` had
  already advanced it, so any Zoho deletion older than that cycle's newest `Modified_Time`
  was silently and **permanently** missed. Cursors are now snapshotted once
  (`read_cursors()`) before either phase and passed to both. Pinned by a test asserting the
  `<module>/deleted` read's `since` equals the PRE-cycle cursor and is strictly older than
  the cursor the same cycle wrote.
- **V2** — the pull cursor advanced to the newest *fetched* record even when some records
  failed to apply, dropping them permanently; and the cycle summary counted only the
  cycle-level and push-level error lists, logging `errors=0` over a batch that lost rows.
  Now `advance_cursor()` watermarks on the newest successfully **applied** record and
  `SyncCycleReport.pull_record_errors` folds the per-record failures into the summary.
  *(Re-verification: the first repair was still wrong when the failure was **older** than a
  success — `apply_module` now returns a `ModulePass` carrying both `newest_applied` and
  `oldest_failed`, and the cursor may only move strictly below the oldest failure. The
  pinned-cursor cost of that is recorded in §7.1.)*
- **V3** — three echo mutations. `source` is now insert-only (see §7.1); the push's padding
  of Zoho's required fields is stripped on the way back in (`strip_padding_echo`), with the
  one indistinguishable case recorded in §7.1 rather than hidden; and `lead_name` — which is
  DERIVED from two padded fields — is computed after the strip instead of inside `map_lead`,
  which had it round-tripping "Asha" into "Asha Asha" on the conflict arm.
  *(Re-verification found that third one, and that the map-vs-padder drift test restated
  `PADDED_FROM` instead of reading `to_zoho_*`; it now AST-walks the builders.)*
- **V4** — a native field clear never reaching Zoho is **documented as an accepted cost** of
  D-CRM-6's no-field-level-merge (§7.1), not changed.
- **V5** — `writer.upsert_record` was exported and never called (`execute_push` branches the
  verb itself). Deleted: the single write surface should stay countable, and "upsert by id"
  is a decision in `push_records`, not a verb.

**Adversarial-review repairs (2026-08-05, same branch — six findings, all real
Postgres/Zoho semantics the unit fakes cannot see):**
- **A1 (P1)** — the cycle was ONE transaction with no savepoints, so a single statement
  error (a Zoho amount overflowing `NUMERIC(14,2)`) aborted it, took every later statement,
  rolled back the cursor, and made the next cycle die identically forever. Now: a SAVEPOINT
  per applied record and per push (`core.savepoint`), a commit per phase and per pull
  module, and `_number()` clamps out-of-range values to NULL.
- **A2 (P1)** — Zoho writes happened inside the transaction that recorded them, so an abort
  after a successful create discarded the stamped `zoho_id` and the next cycle made a
  DUPLICATE (Zoho has no idempotency token). Now: write → stamp → **commit** per record, and
  `stop_crm_zoho_sync` signals + waits instead of cancelling mid-cycle.
- **A3 (P1)** — the broker approval path never wrote state back, so under enforcement every
  approval minted another duplicate and nothing converged; and nothing deduped the queue.
  Now: the handler shares `apply_push_result` with the inline path, and the gate skips
  enqueueing when an identical `(action, target)` is already pending.
- **A4 (P1)** — no failure ceiling: poison rows starved both oldest-first queues forever.
  Now: `attempts`/`last_error`/`next_attempt_at` on all three push queues (migration 145,
  edited in place — still unapplied), exponential backoff, a give-up threshold counted in
  `pushed.given_up` and logged at ERROR, and a tombstone whose Zoho record is already gone
  counts as success.
- **A5 (P1)** — `POST /crm/sync/zoho` had no reentrancy guard, so overlapping cycles
  double-created. Now a process-wide lock: 409 for a second caller, skip for the loop.
- **A6 (P2)** — activity deletes sync in neither direction while creates do. **Documented as
  an accepted v1 cost** (§7.1) rather than built, with both `apply_zoho_deletes` and
  `activities.delete_activity` stating it; closing one direction alone would be worse.
- Mirror nits taken with them: the "adopt cycle start" prose now matches the code (nothing
  fetched KEEPS the previous watermark), the RFC-1123-vs-ISO `If-Modified-Since` question is
  recorded as an owner pre-flip `curl`, and the pull asks for
  `sort_by=Modified_Time&sort_order=asc` to close the page-shift window.
- **Also, from 26c's verifier** — `import_zoho`'s deal-contact link inserted a literal
  `is_primary = true`, so a deal with a hand-set primary A whose Zoho record names B ended
  up with **two** primaries (the one-primary rule is code, not a constraint). `is_primary` is
  now computed inside the INSERT from `NOT EXISTS (… WHERE deal_id = :deal_id AND
  is_primary)`, which also closes the read-then-write race and stays correct under the
  one-primary seam WS-26c adds.

Done when:
1. `list_leads` **and a deleted-records read function** added to the client; Zoho **write**
   functions exist only in `ingestion/sources/zoho/writer.py` (create/update/delete per
   module), the writer has exactly **one** caller — the sync engine — and every push
   routes through the registered `crm.zoho_*` broker handlers (D-CRM-8). All
   grep-asserted the way §9 WS-26a's seam checks are.
2. Backfill `POST /crm/import/zoho` per §7.1 behind `admin:access:manage` (see §4's
   warning — `integrations:use:*` is member-wide); `dry_run` writes nothing (asserted);
   statuses auto-created idempotently — **pinned statically against the statement text**
   (`ON CONFLICT` present on the status upserts, the `test_crm_migration.py` technique):
   `_crm_fakes.py` models no `ON CONFLICT`, so a fake-only "second run creates 0" is a
   mirror agreeing with itself.
3. The sync engine implements §7.1's **seven** sync bullets: broker-gated dirty-push
   (native create acquires `zoho_id`), incremental pull with **persisted `crm_sync_cursors`**,
   **`crm_zoho_tombstones`** written inside the native delete transaction and pushed as
   Zoho deletes, Zoho deletes applied natively with loud counts, record-level LWW with a
   per-cycle conflict count, **echo suppression** (pull-applied writes never set
   `zoho_dirty`; a two-cycle fake-client test converges to zero pushes), vocabulary
   down-only. The migration (next free number, 145 free at audit time) carries the two new
   tables + the dirty columns under 26a's static idempotency fence.
4. `CRM_ZOHO_SYNC` ships OFF, and the OFF-state assertion **fails against today's tree**:
   with the flag off, the gateway lifespan registers no CRM sync loop AND a native write
   sets `zoho_dirty` while leaving `zoho_synced_at` NULL (both asserted). The manual cycle
   endpoint works without the flag.
5. `tests/unit/test_crm_zoho_import.py` + `tests/unit/test_crm_zoho_sync.py` cover
   mapping, idempotency, owner-mapping, dirty-push, LWW both directions, tombstones, echo
   suppression, and the single-writer + broker seams — against a fake client, no network.
6. **Both** stale mirrors are updated in the same PR: the WS-1 row's *"There is no Zoho
   write path anywhere in the repo to route through the broker…"* sentence, and the §4
   registry row's *"the CRM never adds one"* clause (already softened 2026-08-05; finish
   it when the writer lands).
**Enabling the flag, the first backfill, and any hand-run cycle against prod Zoho+DB are
registered in §6 — the sync WRITES the live Zoho tenant.**

⚠️ **Owner pre-flip check, one `curl` (unverifiable from the repo).** The client sends
`If-Modified-Since` in **RFC 1123** (`Tue, 01 Jan 2026 00:00:00 +0000`) — the form the
existing `_list_module` has always used. Zoho's v2 docs also describe an **ISO-8601**
`If-Modified-Since`, and the two are not interchangeable: if the tenant ignores an
unparseable header it returns EVERYTHING, and the pull is silently full rather than
incremental (correct, idempotent, and far more expensive) — while if it errors, the pull
fails loudly and is caught. Before the first enable, confirm which form the tenant honours:

    curl -sD- -o/dev/null "$ZOHO_API_DOMAIN/crm/v2/Accounts?per_page=1" \
      -H "Authorization: Zoho-oauthtoken $TOKEN" \
      -H "If-Modified-Since: $(date -R -d '1 hour ago')"

A `304` (or a short body) means RFC 1123 is honoured. A full first page means it is being
ignored — switch the format in `client._with_modified_since`, which is the single place
both readers build it. **Measured 2026-08-06 against the live tenant: RFC 1123 is
honoured** (`304`, empty body, against a `200`/1537-byte control).

⚠️ **Tenant limitation found on the first live cycle, same day: Zoho returns no
`Modified_Time` for any module here, and no `Created_Time` for `Deals`.** Leads,
Accounts, Contacts and Notes carry `Created_Time`, which `modified_time()`'s fallback
chain picks up; `Deals` carries neither (0 of 551 parse, and naming the fields in
`fields=` returns them empty — a per-module field permission on this tenant, not our
query). Two consequences, both now handled rather than assumed away:
1. **The watermark.** `advance_cursor` gained an untimestamped-but-clean branch: a batch
   where everything applied with zero failures adopts the cycle's start. Without it the
   `Deals` cursor stayed NULL and the module re-pulled all 551 records every ten minutes
   forever, while every other module converged to one or two.
2. **LWW for deals is one-sided, deliberately.** With no Zoho timestamp,
   `_zoho_changed_since_agreement` reads "changed" and `_later` reads "not later", so a
   dirty deal always resolves **native-wins**. That is the recoverable direction the
   helper's own docstring argues for (the other silently discards a colleague's typing)
   and it is left as-is: `Last_Activity_Time` and `Stage_Modified_Time` ARE returned for
   deals, but neither means "last modified", and using a semantically-wrong field would
   flip conflicts toward the unrecoverable side. Revisit only if the tenant's field
   permissions change — and if they do, the cursor branch above becomes dead code for
   `Deals` rather than wrong. ⚠️ `.env.example` cannot
document the new flag (plan-guard protects it); the PR body must carry the var for the
owner. Also owed by this ticket: add `CRM_ZOHO_SYNC` (and `CRM_AUTO_LEAD`) to
`.claude/hooks/plan-guard.mjs` OWNER_GATES per that file's own "§6 changes update
OWNER_GATES in the same PR" rule — noting `.claude/` is untracked, so this lands on the
box-side copy, not in the PR.

### WS-26c — UI (+ the API addendum the surfaces need) · ✅ **BUILT 2026-08-05**
*(Audited GO-NARROWED 2026-08-05; blockers folded in below. Landed on branch
`ws-26c-crm-ui` as `workbench/control_plane/src/app/crm/{page.tsx,components/,lib/}` +
`src/app/api/crm/[...path]/route.ts`, the three registration edits in
`src/lib/{nav,access,centers}.ts` with `CenterApp` re-typed so `live ⇒ href` is a compile
error, and the gateway addendum
`apps/services/gateway/gateway/routes/crm/deal_contacts.py` + `core.link_deal_contact` /
`core.project_joined` / `core.reject_null_on_defaulted` / `core.lead_name_is_derived`.
Fenced by 103 new vitest cases and 41 new pytest cases (the four `test_crm_*.py` files go
191 → 232); **zero DB, zero network.** Adversarial review returned REQUEST-CHANGES on the
first pass; all seven findings repaired in the same branch — see C8–C10 in §8.
**Built, not deployed:** migrations 144 and 145 have still not been applied anywhere.
Merged into `ws-26-crm-app` alongside 26b on 2026-08-06 — every resolution there was the
union of both slices, so 26b's Zoho fields and 26c's join/null-guard fields coexist on the
same `Entity` and both guards run on every write.)*
Notes: until an admin grants `feature:crm`, the UI is visible to owner/admin only (§5).
**Migration 144 has not been applied anywhere**, so live rendering, drag persistence and
deep links are **owner-verified after the migration applies** — the agent builds and
tests against fixtures and must not reach for a DB. Registration is **three** frontend
files, not five — `FEATURES` and the `feature_catalog` row already shipped with 26a.
The `test_centers_registry_matches_the_feature_vocabulary` invariant reads only each
Center's `feature:` field — it CANNOT detect a mistake in the apps[] flip, so don't cite
it as a fence; the real fence to add is `live ⇒ href` on `CenterApp`.
Done when:
1. `nav.ts` pane, `access.ts` `HREF_FEATURES` `["/crm","crm"]`, and the `centers.ts`
   Sales "Pipeline (Zoho CRM)" entry flipped to `{label:"CRM", status:"live",
   href:"/crm"}` — plus a `live ⇒ href` invariant (type-level or a small test) so a live
   entry without an href cannot ship.
2. **API addendum (gateway, small):** deal-contacts endpoints exist
   (`GET /crm/deals/{id}/contacts`, `POST`/`DELETE .../contacts/{contact_id}` with
   `is_primary` handling enforcing one primary per deal — closing the "by convention
   only" gap 26a recorded), and deal list/pipeline payloads carry `organization_name`
   via LEFT JOIN (kanban cards must not client-side-join a ≤100-page org list).
3. **Review residuals closed, each with a test:** `?status_id` on contacts/organizations
   → 422 (not silently ignored); an explicit body `null` on a defaulted NOT NULL column
   (`source`, status `color`) → 422 (not a driver 500); PATCH preserves a hand-edited
   `lead_name` (re-derive only when the name fields change AND `lead_name` wasn't
   custom-set).
4. The four surfaces + convert modal render through the BFF proxy against fixture data;
   kanban drag issues the `PATCH`; `?deal=` deep link opens the sheet (fixture-level
   assertions; live behavior owner-verified post-migration).
5. Pure helpers tested in colocated vitest (`src/app/crm/lib/*.test.ts`); `tsc` and
   `npm test` green; the gateway addendum's tests join the named `test_crm_*.py` files.

### WS-26d — Integrations · 🟢 AGENT-SAFE except the flag
**Audited 2026-08-06 → GO-NARROWED.** The ticket as written bundled four independent
things behind one done-when, and three of them were not dispatchable: the email-thread
join had no testable, caller-scoped done-when; `CRM_AUTO_LEAD` named no hook to attach to;
and the write tools named no confirm/risk mechanism. So the slice was narrowed to the two
halves that were fully specified.

**BUILT 2026-08-06 (branch `ws-26d-agent-crm`):**
1. **`agent-crm` — the READ half.** `apps/agents/agent-crm/` (`crm-assistant`,
   `runtime:"maf"`, `OpenAIChatCompletionClient`, `X-CC-Agent`) on the
   `agent-email-assistant` template **including its `_headers()` fail-closed identity
   rule**; four read tools — `search_crm`, `get_pipeline`, `get_record`, `get_timeline` —
   each a thin wrapper over the existing `/crm` routes carrying the caller's
   `X-User-Email`, so the agent inherits the route's authorization instead of holding a
   second opinion about it. Registered in `_KNOWN_AGENTS`, `_AGENT_REGISTRY` and
   `agent_registry.json`; `build_agents()` constructs one native MAF `Agent`. Read-only was
   a property of the transport (`_ALLOWED_METHODS = {"GET"}` checked in the one round-trip
   helper), asserted three ways in the test file: behaviourally (a POST raises before the
   request is built), structurally (no verb helper exists), and by observation (every call
   the four tools make is a GET).
   ⚠️ **SUPERSEDED 2026-08-08 by WS-26d-write, which is what this design was for.** The
   allowlist was widened to `{GET, POST, PATCH}` and `_post`/`_patch` added — but the CHECK
   stayed in `_request` and `DELETE`/`PUT` stayed out, so the mechanism now enforces "never
   destroys" rather than "never writes", and the three assertions above were generalised
   rather than deleted. Read the §9 `### WS-26d-write` as-built block for the current state;
   what remains true here verbatim is the identity rule, the path guards and the
   one-authorization-rule design.
   ⚠️ **Diff review found the method allowlist was only half the boundary, and the other
   half was missing.** `record_id` went into the path unvalidated, and httpx removes `..`
   segments before sending, so `get_record("deals", "../../admin/members")` issued
   `GET /admin/members` carrying the internal bearer and the caller's `X-User-Email`;
   `/admin/members/{email}/access`, `/email/messages`, `/whatsapp/chats` and
   `/memory/agent:<name>` were all reachable. Identity was preserved, so it was scope
   escape rather than privilege escalation — but "cannot see outside the CRM" is a claim
   this spec, the `_AGENT_REGISTRY` description and `apps/AGENTS.md` all make. Fixed by
   `_record_uuid`, which validates AND canonicalises at the same layer `_entity_slug`
   validates the entity (every CRM table keys on `CAST(:id AS uuid)`, so a non-UUID id is
   never legitimate — which also stops a hallucinated `"ACME-123"` producing a driver 500).
   The trigger is what this slice creates: `record_id` is LLM-filled and the model's
   context is counterparty-authored CRM text, so a system-prompt rule was the wrong control
   class. **A future tool must route path segments through `_entity_slug`/`_record_uuid`** —
   an AST fence over every `/crm` f-string enforces it, and it caught a deliberately
   half-applied mutation during review.
2. **WhatsApp `_KNOWN_SYSTEMS` gains `"crm"` — PARSE-ONLY**, per §6. Nothing writes
   `wa_contacts.entity_ref` yet and the `crm` context block stays `None`; both halves are
   pinned by test so "we added the constant" is never mistaken for "the link works".

**Build-time decision, this slice:** `config.json` declares `sharing.shareable: false`,
matching both sibling assistants. Inert today (`is_shareable()` has no runtime consumer),
but it is the safe default to record now rather than to discover later: when sharing is
wired, `assert_can_run_agent_in_session` folds on `can_run_agent` — i.e. `agents:run:*`,
which every member holds — and **never on `feature:*`**, while `feature:crm` is
`is_default false` (migration 144). A shareable CRM agent would therefore put CRM records
into a transcript a non-`feature:crm` member can read. Revisit together with D-CRM-3's
`group:` grants at WS-14, not before.

**B3, B4, B5 and B7 — CLOSED 2026-08-06** by §9.1, §9.2 and §9.3 below; every anchor in
them was read off `origin/main` at `af3d3fc8` rather than recalled. **B6/D-CRM-9 —
RESOLVED 2026-08-06**: the push-queue question ("do agent writes reach Zoho like human
writes?") is owner-answered — yes, identically (§8 D-CRM-9). The three remaining WS-26d
slices are therefore dispatchable; each is a separate ticket below and none may be
narrowed into another.

### WS-26d-email — the email→CRM timeline join · 🟢 BUILT 2026-08-07
*(Branch `ws-26d-email-timeline`; not merged, not deployed. Everything below is
the ticket as dispatched and is kept as the record of why it is shaped this
way. What landed: `activities._email_account_scope` / `_record_addresses` /
`_email_entries`, `_timeline(entity, record_id, limit, user)` with all four
routes passing the caller, `email_thread` on both `TimelineEntry` types, the
third branch in `Timeline.tsx` behind the pure `crm/lib/timeline.ts`, the
matching third branch in `agent-crm`'s `get_timeline` (the agent is the **third**
consumer of this shape and had the same binary dispatch — it rendered every
email entry as `email_thread: (no subject)`, and because `_timeline` merges then
truncates, a mail-heavy deal answered "what's the story with this deal?" with
twenty blank rows) — rendering sender/subject/status but never the snippet,
**D-CRM-12** — the email source capped at half the merged page so a chatty
mailbox cannot evict the record's own history, the
`(account_id, LOWER(from_address->>'email'))` index on `email_messages`, and
`tests/unit/test_crm_email_timeline.py` + the `_crm_fakes.py` readers it needs.
Two of those tests are a MUTATION FENCE: deleting the `_email_account_scope(…)`
call from the query must turn both red — verified, and re-verified green after
a byte-identical revert.)*
*(Closes B3. Highest-leverage item in §6 and the one with the sharpest failure mode:
the CRM is org-visible to every `feature:crm` holder (D-CRM-3) while a mailbox is
owner-scoped, so an unscoped join publishes one member's inbox to the whole company.)*

**The scoping rule, non-negotiable.** The join is **caller-scoped, never record-scoped**:
the timeline shows email *the caller can already read*, not email *the record has*. Two
holders of `feature:crm` opening the same lead may legitimately see different email
entries, and a holder with no mailbox sees none. The predicate is the email app's own
`_account_scope` (`routes/email/core.py`, `def _account_scope`) — a fragment generator that appends
`em.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid)` and mutates the
caller's `params`; the caller must pre-seed `params["uid"]`. Note it hardcodes the alias
`em` (`analytics.py:66-68` already had to `.replace()` it) — alias the CRM query's
`email_messages` as `em` and the fragment drops in unchanged.

**Where it comes from — decide, do not drift.** Cross-routes-package imports are
precedented (`routes/notes/dispatch.py:280-282` imports private names from
`routes/email/core`), but `routes/crm` has explicitly declined that once already, on
D-CRM-4 grounds (`routes/crm/broker_handlers.py:61-64`, which re-implemented four lines
rather than import them). **Decision: copy the twelve-line predicate into `routes/crm/`
as `_email_account_scope`, with a comment naming `routes/email/core.py::_account_scope`
as its origin and this line as the reason.** (Cited by symbol, not by line: the 2026-08-07
audit found the original `:424-435` citation had already drifted to `:407-418`.) Rejected alternatives: importing it (contradicts the
CRM package's own stated doctrine, and couples the CRM's read path to the email app's
private surface) and promoting it to a shared module (correct eventually, but it drags
`routes/email`'s callers into a WS-26 PR). If it is copied a *third* time anywhere,
promote it instead — two copies is a coincidence, three is a missing module.

**The unit is the THREAD, not the message.** There is no `email_threads` table;
`email_messages.thread_id` (`17_email_accounts.sql:43`, indexed `:70-71`) is the grouping
column, and the conversation is already the unit of classification and snooze across the
app. Group by `(account_id, thread_id)`, take the newest message per thread for the
display row, and left-join `email_thread_status (account_id, thread_id)`
(`27_email_reply_tracking.sql:14-23`) for the status badge when present. A row-per-message
timeline double-counts every conversation.

**The address join.** `from_address` is `JSONB {name, email}` and `to_addresses` /
`cc_addresses` are `JSONB [{name, email}]` (`17_email_accounts.sql:46-49`); the writer
always supplies both keys, `""` when absent (`email_ingestion/persist.py:178-190`). Case is
**not** normalised on write, so every comparison lowercases at query time — the existing
contact card is the exact precedent: `LOWER(em.from_address->>'email') = :addr`
(`routes/email/transport/contacts.py:404-412`), which is already this join, already
caller-scoped. v1 matches **inbound only** (`from_address`); outbound matching
(`to_addresses @> :tojson`, precedent at `senders.py:1259-1263`) is deferred — it needs a
GIN index that does not exist and doubles the query cost for a marginal gain on a mailbox
being retired.

**Which records join.** `crm_organizations`, `crm_contacts` and `crm_leads` have an `email`
column (`144_crm.sql:40,70,150`, contacts/leads already indexed `lower(email)`).
**`crm_deals` does not** — a deal reaches email only through its originating
`lead_id → crm_leads.email` or through `crm_deal_contacts → crm_contacts.email`, and the
done-when must state which (v1: both, unioned, matching how `_timeline` already inherits
lead history). **Organizations do NOT join by email domain** in v1: an `@fracktal.in`
domain match would attach the entire mailbox to our own org record.

**The index.** No existing index serves `LOWER(from_address->>'email') = :addr` — the two
FTS GINs (`17_email_accounts.sql:80-89`, `72_email_search_fts.sql:28-37`) bury the address
inside a `to_tsvector` expression, usable only via `@@`. Add
`(account_id, LOWER(from_address->>'email'))` at **the next free migration number at build
time**. (The contact card runs this unindexed today over ~14k messages; the CRM timeline
would run it on every record open.)

**Threading the caller through.** `_timeline()` (`routes/crm/activities.py:94`) takes no
user today; the four routes have `user` and drop it (`:220-254`). Change the signature and
pass it — that is the whole plumbing change, and it is why this must not be done "quickly"
in a later slice.

**Done-when:**
1. A `feature:crm` holder who owns **no** email account sees **zero** email entries on
   every CRM timeline — named test, not an argument.
2. Two accounts, two holders: each sees only their own account's threads on the same
   record. A test that only ever runs one account cannot see this bug.
3. `TimelineEntry.kind` gains `"email_thread"` in **both** `activities.py:75-86` and
   `workbench/control_plane/src/app/crm/lib/types.ts:148-155` (a closed TS union today —
   tsc fails if only one side is updated), and `Timeline.tsx` renders it with the origin
   label the merge already carries.
4. A record whose `email` is NULL or matches nothing returns the same shape with no email
   entries — never an error, never a full-mailbox fallback.
5. The join is bounded by the existing `limit` (`Query(100, ge=1, le=500)`), applied per
   source before the merge, so one chatty thread cannot crowd out status history.
6. Migration adds the address index; number taken at build time (R1).

**Tests:** `tests/unit/test_crm_email_timeline.py` (B7), reusing `tests/unit/_crm_fakes.py`.
Frontend: extend the existing CRM vitest for the third `kind`.

### WS-26d-autolead — `CRM_AUTO_LEAD` · ✅ **BUILT 2026-08-08** · 🔴 **OWNER-GATE to flip — NOT FLIPPED, NOT DEPLOYED**
*(Closes B4.)*

> **As built** (branch `ws-26d-autolead`, migration **158**
> `163_crm_auto_lead_cursor.sql` *(renumbered from 158 at merge — the migration-renumber trap, again)* — the number taken from the directory at
> build time per R1, and `test_crm_auto_lead.py` finds the file by CONTENT,
> so a renumber in review breaks nothing). **The flag is `False` everywhere and nothing has been
> deployed**: with `CRM_AUTO_LEAD` off this branch changes no runtime
> behaviour, which is the whole point of done-when 2.
>
> * `crm_auto_lead: bool = False` in `packages/acb_common/acb_common/settings.py`,
>   beside `crm_zoho_sync` (its precedent shape). **`.env.example` deliberately
>   untouched** — plan-guard territory; the flag is documented here and in
>   `settings.py` only, and a test pins its absence from that file.
> * `apps/services/gateway/gateway/routes/crm/auto_lead.py` — the whole step.
>   **It lives in `routes/crm` and not in the email package** because what it
>   does is write a CRM record; it registers no routes and, like
>   `broker_handlers.py`, is deliberately absent from
>   `routes/crm/__init__.py`. It **imports** the automation package's PUBLIC
>   identity primitives (`sender_scope` / `resolve_org_domains` /
>   `normalize_domain`) rather than copying them — D-CRM-4 declined to import
>   another package's *private* helper, and a third copy of "is this person a
>   colleague?" is the drift that rule exists to prevent.
> * The call site in `routes/email/scheduler_hooks.py::process_new_mail` is
>   `if auto_lead_enabled():` with the step's own import INSIDE the branch, so
>   the OFF state does not load `routes/crm` on the mail path at all. The
>   predicate stays above the gate on purpose: `auto_lead_enabled` is the
>   flag's ONE definition, and reading `settings.crm_auto_lead` in the hook
>   instead would make two places responsible for agreeing what the flag means
>   (the `sync_enabled` precedent). **It runs LAST, after auto-archive**, so
>   the step considers what is still in the INBOX once the account's own
>   automation has finished — mail the user's own rules archived never becomes
>   a lead. ⚠️ **One divergence from the five sibling steps, recorded:** the
>   import sits INSIDE the `try`, so a `routes/crm` module that fails to import
>   is logged on `sync.auto_lead_failed` like any other CRM failure rather than
>   raised out of the mail path.
> * `tests/unit/test_crm_auto_lead.py` — **75 cases**, each done-when named in
>   a test. `tests/unit/_crm_fakes.py` gained two readers and one capability:
>   `@>` jsonb containment and the case-folding
>   `EXISTS (… jsonb_array_elements …)` form (each needed because a probe the
>   fake cannot see is a probe it answers "yes" to for every Sent message —
>   and the second must be STRIPPED before the existing `_JSONB_LOWER_CMP`
>   reader misreads its inner comparison), plus `fail_on(..., after=N)`,
>   because *where* in a batch a failure lands is the whole property under
>   test in done-when 9.
> * **Fifteen mutants run red and were reverted** (seven pre-review, six for
>   the repair round, two more for the delta re-review): the flag check · the `received_at > activated_at`
>   predicate · the watermark advance · the internal-domain second gate ·
>   `type='system'` · the service write path · in-batch dedup · the dormancy
>   re-anchor · the prefix-only advance · the stall WARNING (demoted to INFO) ·
>   the case-folding Sent probe · suffix-aware internal domains · the
>   `last_run_at` stamp on a quiet cycle · the anchor CLAMP (reset to `now`) ·
>   the early return that discarded the triggering batch. The Sent-probe mutant
>   is additionally
>   checked for PRECISION: reverting it must NOT redden the lower-case
>   already-emailed case, or the mutant broke the probe rather than narrowing
>   it.
>
> **Five decisions the ticket did not record, each with its reason:**
> 1. **`activated_at` and `processed_watermark` are stamped to the SAME
>    instant on activation**, so the activating cycle mints nothing. That is
>    the deep-resync case stated positively: on the day the flag flips, every
>    mailbox's entire history predates activation.
> 2. **The two colleague gates answer different questions, and gate 1 is asked
>    WITHOUT the configured extra domains.** `sender_scope`'s own extra-domain
>    arm only `lstrip('@')`s its input while `resolve_org_domains` runs
>    `normalize_domain` — the exact divergence `runner.py:1635-1641` documents.
>    Routing the configured list through gate 2 alone means there is ONE
>    normalisation of it here rather than two that can disagree; it is also
>    what makes gate 2 load-bearing rather than a restatement of gate 1, and a
>    named test (a colleague on an org domain somebody typed as an address)
>    goes red when it is deleted.
> 3. **The watermark advances over the successful PREFIX** — over messages
>    that minted nothing, never past one whose lead write raised (done-when 9,
>    rewritten 2026-08-08 after the diff review measured the first version
>    losing three leads permanently on a single pool exhaustion). A held cursor
>    is reported at WARNING every cycle rather than left to look like a quiet
>    mailbox. Each candidate is wrapped in `core.savepoint` so one statement
>    error cannot abort the batch's transaction (the WS-26b lesson).
> 4. **The lead and its first activity are two transactions.**
>    `create_record` opens and commits its own session — it is the same
>    function `POST /crm/leads` calls — so a failure between the two leaves a
>    lead with an empty timeline, logged and counted. The alternative was a
>    second, divergent write path for the record itself, which is what
>    done-when 3 forbids.
> 5. **An account whose `user_id` is blank is skipped before the cursor is
>    even activated.** `actor()` would attribute the lead to `"anonymous"`,
>    and a lead that is nobody's follow-up and that the `owner` filter cannot
>    match is worse than no lead.
> 6. ⚠️ **The re-anchor CLAMPS `activated_at` to `now - REANCHOR_GAP_SECONDS`
>    and runs the batch anyway** — it does not stamp `now`, and it does not
>    return early. Corrected in the delta re-review after the first fix shipped
>    a P1 regression of its own: this step is invoked only when a sync
>    PERSISTED mail (`email_ingestion/scheduler.py:463-472`), never once per
>    period, so the cycle that trips dormancy is always the cycle carrying the
>    message that woke it — and resetting the anchor excluded that message
>    permanently, every night and every weekend. **The premise the earlier
>    version of this note gave for the third column was false** (it claimed the
>    step runs every 600s on a quiet mailbox; it does not run at all).
>    `last_run_at` is still the right clock, for the narrower reason recorded in
>    the cursor paragraph, and it is stamped on every cycle including empty and
>    stalled ones. The ruling's constant (3600s), log key
>    (`sync.auto_lead_reanchored`) and fail-closed behaviour on a genuine OFF
>    window are unchanged; the accepted residual — the last gap-width of the
>    window is minted — is recorded there and in §6 (b).
> 7. **Both attacker-controlled strings are clipped** (`MAX_NAME_CHARS` 120,
>    `MAX_SUBJECT_CHARS` 500, with a `…` marker). Nothing upstream bounds
>    either: a display name is whatever the sending server put in the header,
>    and it becomes `lead_name` — a column every CRM list, board card and Zoho
>    push then carries.
>
> **What an owner still has to do, in order:** merge → deploy (migration 163
> applies automatically) → flip `CRM_AUTO_LEAD` (§6 (b)). The first ON-state
> run on each mailbox activates the cursor and mints nothing; leads start
> appearing from mail that arrives after that moment. The same is true after
> any period with the flag off or the service down for more than an hour: the
> next cycle re-anchors, mints nothing from the gap, and says so at WARNING.
>
> ⚠️ **The migration is numbered 158, not 157.** Open PR #399
> (`157_projects_recurrence.sql`) holds 157, and two migrations sharing a
> number replay in filename order against the wrong schema. The ladder
> therefore carries a deliberate reservation gap at 157 until that PR lands;
> the migration header names it, and the test finds the file by CONTENT so a
> further renumber in review costs nothing.

**The hook is `process_new_mail(account_id)` — `routes/email/scheduler_hooks.py:57`.**
It is the shared new-mail pipeline (rules → sweep → categorize senders → classify threads
→ auto-archive) and it is the single entry point *however mail arrived*: the background
scheduler, the manual-sync route and the webhook all funnel through it. Each step is
independently try/except-isolated (`:81-112`) — the CRM step copies that shape, so a CRM
failure can never break mail sync. It receives `account_id` only, so the step re-queries
the newly-classified messages itself.

**Rejected alternative, and why it is recorded:** `_run_rules_job`'s per-message loop
(`routes/email/automation/runner.py:1589-1717`) already holds the parsed sender and the
classification, which is tempting. It is wrong here for two reasons: a classifier outage
`continue`s **without stamping** the watermark (`:1678-1685`), so a hook placed there
double-fires on retry; and it only ever sees INBOX mail with `rules_processed_at IS NULL`,
so historical backfills (`rules_held_back_at`, `84_email_rules_held_back.sql:22-27`) never
reach it. Seam A is the durable one — **but read the next paragraph before trusting it,
because for THIS feature "reaches history" flips from virtue to catastrophe.**

**The candidate set and the backfill discriminator (2026-08-08 audit blocker G1 —
the load-bearing paragraph).** `process_new_mail` is ALSO reached by deep resyncs:
`resync_account` runs a ≈1-year all-folder backfill and then fires the hook, a
first-ever sync of a newly-connected mailbox is deep by the same heuristic, and neither
path stamps `rules_held_back_at` (its only writer is `_backfill_and_clean_job`, which
does not go through this hook). A candidate query of "everything classified" would
therefore mint a lead per unknown external sender in a year of mail the moment a second
mailbox connects — each born `zoho_dirty`, each pushed to the live tenant within one
600s cycle, with no confirmation card anywhere on a scheduler hook and no delete tool.
The step therefore keeps a **per-account three-timestamp cursor** in a new table
(migration at the next free number at build time, R1):
- `activated_at` — **the start of the current ON epoch**, not "the first time anyone
  ever enabled this". **The backfill discriminator is `received_at > activated_at`**:
  mail that ARRIVED before this epoch began is history and mints nothing, no matter
  when a resync classifies it.
- `processed_watermark` — the incremental cursor: candidates are classified inbox mail
  (`rules_processed_at IS NOT NULL`, `rules_held_back_at IS NULL`) with
  `rules_processed_at > processed_watermark`.
- `last_run_at` — when the step last RAN, stamped on **every** cycle including the ones
  that considered nothing. This is the dormancy clock. ⚠️ It is a third column rather
  than a reading of the second for a narrower reason than first claimed: BOTH freeze on
  a mailbox that receives nothing, because the hook does not fire at all without new
  mail. What separates them is a cycle that ran and found no *candidates* — mail outside
  the inbox, held back, or predating the anchor — which advances `last_run_at` and not
  the watermark; and that is also the state a deliberate stall holds the watermark in,
  so keying dormancy on the watermark would re-anchor past a stall and quietly undo it.

**Re-anchoring, and why "set ONCE" was wrong (2026-08-08 diff review, P1-1).**
`activated_at` alone guards a deep resync and does nothing about a flag that was on,
turned OFF for four weeks, and turned back on: the cursor still carries the old anchor,
so the first ON cycle mints the whole OFF window in one batch — **measured at 27 leads
for a 27-day window**, each pushing unattended into the live tenant. So when an
ON-state cycle finds `now - last_run_at > REANCHOR_GAP_SECONDS` (a named constant,
3600), it **clamps `activated_at` forward to `now - REANCHOR_GAP_SECONDS`**, logs
`sync.auto_lead_reanchored` with `gap_seconds`, and **runs the batch normally**.

⚠️ **CLAMP, never reset — and never discard the triggering batch** (2026-08-08 delta
re-review; the first fix stamped `now` and returned early, which was a P1 regression in
its own right). **This step is not invoked once per scheduler period.**
`email_ingestion/scheduler.py:463-472` reads `synced` off the sync result and fires the
hook **only when mail was actually persisted**, so a mailbox with no new mail does not
run this step at all — which means the cycle that trips the dormancy test is *always*
the cycle carrying the message that woke it. Measured: no mail 22:00→07:30, a cold
prospect writes at 07:30:50, the sync persists it, the step runs at 07:31:05, and a
reset-to-`now` anchor excluded that message **permanently — every night and every
weekend**. Clamping keeps the fail-closed property that matters (anything received
longer ago than the gap width stays excluded, so a real OFF window or multi-day outage
still mints nothing from its backlog) while admitting everything received inside the
last hour, which is where the triggering message always is. The watermark is NOT
touched on re-anchor: OFF-window mail is excluded by the anchor predicate whatever its
`rules_processed_at` says, and moving it would skip the triggering batch a second way.

**Documented residual, accepted:** mail received in the final `REANCHOR_GAP_SECONDS` of
an OFF window IS minted when the flag comes back on. The bound is on TIME, not on volume
— one gap-width of mail (an hour), drained across however many cycles the per-cycle cap
takes, since the cap defers rather than drops. It is the deliberate price of never
dropping the message that woke the step. A named test asserts it as the bound rather than
leaving it to be discovered. Fail-closed otherwise: a missed lead is hand-creatable and visible in
the mailbox; 27 unattended pushes into the live tenant are neither.

**A failure never advances the cursor past lost work (2026-08-08 diff review, P1-2).**
The watermark advances to the max `rules_processed_at` of the **contiguous prefix of
the batch that successfully wrote its leads**; on the first lead-write failure it stops
moving, and later successes in the same batch are simply re-considered next cycle (free
— the third unknown-sender step finds the lead they already created). This matters
because the step opens a SECOND session per lead through `create_record` while holding
the batch's own, so pool exhaustion fails many candidates at once and an unconditional
advance steps over every one of them permanently — **measured at 3 candidates, 3
errors, watermark advanced, three leads lost for good.** When the watermark does not
move and errors > 0 the cycle logs `sync.auto_lead_stalled` at **WARNING, every cycle**:
a held cursor and a quiet mailbox both create nothing, so the counters cannot tell them
apart and only the level makes it visible. The accepted cost is a visible stall on a
genuinely poison head message, deliberately — fail closed toward the CRM. ⚠️ A failure
to write the first **activity** does NOT hold the cursor: the lead is already committed,
so the message would be skipped on re-consideration and the activity never retried.

Per-cycle candidate cap (a named constant, ~200) with the overflow COUNTED in the log
line — silent truncation reads as "covered everything". The fetch takes cap+1 so
"there is more" is a fact rather than an inference, and if the cap falls INSIDE a group
of rows sharing one `rules_processed_at` the whole group is deferred to the next cycle
(the watermark is a timestamp, so advancing it into a cut group loses the rest of it);
`ORDER BY rules_processed_at, id` gives that group a stable order. Latent only —
production stamps one transaction per message, so ties do not occur today.

Residual race accepted and recorded: two concurrent `process_new_mail` invocations for
one account can read the same watermark and double-mint; the cost is one visible,
hand-deletable duplicate lead, and the alternative (a unique index minted on a column
where 1,516 imported rows may already carry duplicates) is a deploy-blocking constraint
of exactly the shape migration 148 had to defuse. Do not "fix" the race with that index.

**"Unknown sender" is not a new idea — mirror `_maybe_block_cold`**
(`routes/email/automation/senders.py:1242-1273`), which already answers it in two steps:
a memo table, then "have we ever emailed them" (`to_addresses @> :tojson` over Sent). The
CRM version adds a third step — no `crm_contacts`/`crm_leads` row with that
`lower(email)`. **Dedup mechanism (audit blocker G3): the previously prescribed
`INSERT … ON CONFLICT DO NOTHING` cannot fire — `crm_leads` has no unique constraint on
email (`idx_crm_leads_email` is a plain index; only `zoho_id` is UNIQUE).** The real
shape is the SELECT guard above plus **in-batch de-duplication** (one sender emailing
twice in a single batch mints one lead), and the cross-invocation race is accepted per
the cursor paragraph.

⚠️ **The Sent probe folds case HERE, unlike `_maybe_block_cold` (2026-08-08 diff
review, P2-4).** Postgres's `@>` is case-EXACT, so an owner who wrote to
`Asha@AcmeRobotics.com` has, as far as containment is concerned, never emailed
`asha@acmerobotics.com` — and she replies in lower case, so her reply minted a lead for
somebody already mid-conversation. This step asks the question with
`EXISTS (SELECT 1 FROM jsonb_array_elements(to_addresses) … WHERE lower(…) = :addr)`
instead. `_maybe_block_cold` is deliberately **left alone**: it is the email package's
predicate and its blast radius is the cold-email blocker, not this.

**The first activity is metadata, never content (audit blocker G2).** The originating
message is logged with **`type='system'`** — deliberately outside the Zoho push
predicate (`sync_zoho.py` pushes `type IN ('note','task')` only), so the activity never
leaves the native CRM; a test pins the exclusion rather than leaving it to the
predicate's current spelling. Its `subject` is the mail's subject line; its `meta`
carries sender display name, sender address, received timestamp, message id and thread
id; **`body` stays empty — no mail body, no snippet, ever.** The lead row itself is
org-visible to `feature:crm` holders (D-CRM-3) and pushes to Zoho (D-CRM-9); sender +
subject is the proportionate disclosure for a cold inbound inquiry, the body is not —
the same line D-CRM-12 draws for the agent's rendering, applied to what a machine
writes.

**Never create a lead for a colleague.** `is_own_mail` / `sender_scope`
(`routes/email/automation/identity.py:64-101`) is the single answer to "is this person a
colleague?" across the automation package, and it fails SAFE to `"external"` — which is
the wrong direction here, so the CRM step must treat `"external"` as *necessary but not
sufficient* and still apply the internal-domain list (`cleanup.py:298-303`). A lead row
for your own CFO, pushed into the live Zoho tenant (D-CRM-9), is the failure this
paragraph exists to prevent. ⚠️ **That list matches SUBDOMAINS too** (2026-08-08 diff
review, P2-6): `cfo@mail.fracktal.in` is the CFO, and a company's own
`mail.`/`corp.`/regional subdomains are routine — exact matching alone let him through
while `cfo@fracktal.in` was caught. The suffix test is anchored on a leading dot, so
`notfracktal.in` is not a subdomain of `fracktal.in`; a bare `endswith` would refuse a
real prospect's leads, which is the same damage in the other direction.

**Done-when:**
1. `crm_auto_lead: bool = False` in `acb_common/settings.py`, shipping OFF.
2. **OFF-state is byte-identical**: with the flag off, `process_new_mail` makes no CRM
   call and issues no CRM query — pinned by a test that fails if the call is merely
   short-circuited *inside* the CRM step rather than skipped before it.
3. ON-state, per candidate message (the cursor paragraph's two predicates) from an
   unknown external sender: exactly one `crm_leads` row, `source='email'`, owner = the
   account's user, `lead_name` via `core.compute_lead_name` from the STRIPPED display
   name (the "Asha Asha" trap, §8 B-series), created through `records.py`'s write path
   (never raw SQL — `_resolve_status`, the owner default, `validate_source` and
   `mark_dirty_on_insert` all live there), and the originating message logged as the
   lead's first activity per the metadata-never-content paragraph.
4. Re-running the same sync creates **no** second lead (idempotency test), and one
   sender emailing twice in a single batch mints one lead (in-batch dedup test).
5. A colleague sender, a self-sender and an already-known contact each create nothing —
   three separate named cases. `is_own_mail`'s `"external"` fallback is necessary-not-
   sufficient: the internal-domain list (`cleanup.py:298`) is the second gate.
6. Per D-CRM-9 each created lead is born `zoho_dirty` and queues for Zoho; the test
   asserts that rather than leaving it implied. The first activity's `type='system'`
   exclusion from the push predicate is asserted in the same file.
7. **A deep resync / first sync of a newly-connected mailbox mints NOTHING**: the hook
   run against an account whose messages all predate `activated_at` creates zero leads —
   the test seeds a year-old classified backlog and runs the ON-state hook against it.
8. **An OFF→ON round trip mints nothing from the OFF window** (added 2026-08-08, P1-1):
   the hook run against an account whose cursor was anchored weeks ago and whose
   `last_run_at` is stale creates zero leads and re-anchors the cursor. ⚠️ Its
   counterpart is equally named and equally load-bearing: **the message that WOKE the
   step is minted, not discarded** — an overnight lull expressed the way the scheduler
   produces it (the step simply not called), then a message arrives and IS turned into a
   lead in the same cycle that re-anchors. Neither test may seed `last_run_at` by hand:
   a stale value the scheduler cannot produce is a test green on fiction, which is
   exactly how the first fix shipped its regression. The residual is asserted too — mail
   from the last gap-width of the OFF window is minted, deliberately and boundedly.
9. **A failure never advances the cursor past lost work** (added 2026-08-08, P1-2):
   a batch of `[ok, raise, ok]` leaves the watermark at the FIRST message's stamp, the
   next cycle re-considers messages 2 and 3, and a cycle whose watermark did not move
   with errors > 0 logs `sync.auto_lead_stalled` at WARNING **on every cycle it
   persists**. A failed first *activity* is the counter-case: it is counted separately
   and does not hold the cursor.

**Tests:** `tests/unit/test_crm_auto_lead.py` (B7).

### WS-26d-write — the CRM write tools · ✅ **BUILT 2026-08-08**
*(Closes B5. `create_lead`, `update_deal_status`, `log_activity`, `convert_lead`.)*

> **As built** (branch `ws-26d-write`, **no migration** — every route these
> tools call already existed): the four tools in
> `apps/agents/agent-crm/agents.py`, each awaiting `request_confirmation` before
> it constructs a mutating request and none passing
> `non_interactive_default`; `_ALLOWED_METHODS` widened from `{"GET"}` to
> `{"GET", "POST", "PATCH"}` with the check kept inside `_request`; `_post` and
> `_patch` added and `_delete`/`_put` deliberately still absent. Tests:
> `tests/unit/test_crm_agent_write.py` (new, 76 cases),
> `tests/unit/test_crm_agent.py` (87 → 143, generalised from the read half's
> hand-typed tool lists to an `_INVOCATIONS` table fenced against `_TOOLS`), and
> `tests/unit/_crm_agent_fakes.py` (new — the loader, the recording fake client
> and the confirmation stubs both files share). Six mutants run red and were
> reverted (six pre-review, four more for the repairs). R4 sweep — **eleven** read-only claims corrected, the last four found
> by diff review: `agents.py`'s module docstring · `_request`'s refusal text ·
> `config.json` · `pyproject.toml` · `_AGENT_REGISTRY` (`routes/agent.py`) ·
> `apps/AGENTS.md` · `instructions.md` · `agent_registry.json` ·
> `apps/services/gateway/AGENTS.md` · this spec's §6 Agent bullet · this spec's
> §9 WS-26d read-half build record (marked superseded rather than rewritten —
> it is a build record, and the design it describes is what the write half was
> for).
>
> **Four decisions the ticket below did not record, each with its reason:**
> 1. **Reads may precede the confirmation; writes may not.** Done-when 1 says
>    "zero HTTP calls when confirmation is denied", but three of the four tools
>    cannot honestly describe what they are about to do without reading first:
>    `update_deal_status` resolves the stage NAME to an id, `convert_lead` has to
>    know whether this lead is already converted, and `log_activity` has to name
>    the record it is writing to. A card reading "move deal 8f3c-… to <a stage
>    nobody checked exists>" is a signature bought under a misdescription. So the
>    asserted invariant is **no mutation before consent** —
>    `test_everything_read_before_the_confirmation_is_a_read` pins that every
>    pre-card call is a GET — while `create_lead`, which is creating the record
>    and so already holds every fact its card needs, is still held to literally
>    zero calls. (The cited proof shape at
>    `test_email_tool_consolidation.py:272-280` forbids the specific write rather
>    than all traffic.)
>    ⚠️ **`log_activity` moved into this bucket under diff review**, and the
>    reason is the sharper case for the whole allowance: its card carried the
>    type, the subject and the body — all from the same turn of conversation — so
>    a wrong `record_id` produced a card **byte-identical** to the correct call.
>    `search_crm` routinely returns two deals whose names differ by a word; there
>    is no delete tool to take the note back; and by D-CRM-9 it is queued for the
>    live tenant by the time anybody notices. One GET buys the one fact that
>    makes the card checkable, and
>    `test_the_card_names_the_record_being_written_to` now holds all four tools
>    to it.
> 2. **The stage is resolved by name inside the tool** (supervisor ruling), via
>    `GET /crm/statuses/deal` — no new read tool, no UUID on the LLM surface, and
>    an unknown name comes back as the list of real lane names rather than a
>    relayed 422.
> 3. **A lost-type target requires a `lost_reason`**, resolved the same way
>    against `GET /crm/lost-reasons` (supervisor ruling). This pre-empts the 422
>    `_resolve_status`/`apply_status_transition` raise on the "close this as
>    lost" demo beat. The tool **never creates** a stage or a reason — pinned by
>    `test_the_vocabulary_is_only_ever_read`, which watches the refusing paths
>    too, since those are where minting the missing row would be tempting.
>    ⚠️ **Both vocabularies resolve to ALL case-insensitive matches, never the
>    first** (diff review). Postgres UNIQUE is case-SENSITIVE and the importer's
>    `ensure_status` mints unseen lanes by name, so "Closed Won" and "Closed won"
>    can coexist without anybody having decided to create both; a first-match
>    lookup would move the deal into whichever the query happened to order first.
>    Two rows with the same spoken name is a question for a human — the tool
>    refuses and lists the candidates **quoted**, since they may differ by
>    nothing but casing.
> 4. **`create_lead` takes no `owner_email` argument at all.** `create_record`
>    already defaults it to the acting user server-side, so surfacing it would
>    add an LLM-filled identity field whose only power is to attribute a lead to
>    somebody who never asked for it. Normalize-on-write in
>    `routes/crm/records.py` was ruled OUT of scope.
>
> **`_annotate_risk(open_world=...)` is False, deliberately.** The vocabulary
> means "the tool reaches outside Metorite", and these tools speak only to
> the gateway; the Zoho hop is `sync_zoho`'s, on its own broker gate. D-CRM-9 is
> still true — the row is born `zoho_dirty` — and it is said on the confirmation
> card instead, where the person deciding can weigh it. ⚠️ **That note is the
> FIRST line of the card body, not the last** (diff review):
> `request_confirmation` clips `context` at 4000 characters, so appending it
> meant a ~4KB note body silently dropped exactly the warning it was there to
> give, while the card also stopped matching the wire. The payload block is now
> budgeted under the fixed line and carries an explicit truncation marker; the
> WIRE always carries the full text.
>
> **Two findings from diff review are recorded as open and deliberately NOT
> fixed here**, because both are route-layer behaviour rather than this tool's:
> clearing `lost_reason_id` when a deal moves back OUT of a lost stage, and
> validating `expected_close_date` before it reaches `/convert`.

**The mechanism is `request_confirmation` (`acb_skills/ask_tools.py:345-348`), awaited at
the top of the tool, before the HTTP call.** It is a plain async function — not a
decorator, not a return shape: it emits a `confirmation_requested` event the UI renders as
a card and blocks on a Future; only a literal `APPROVE` proceeds (`:398`). With no delivery
channel it **fails CLOSED** (`:446-448`), and `non_interactive_default="approve"` is the
per-call opt-out reserved for reversible actions — **no CRM write tool may pass it.** Call
shape to copy: `agent-email-assistant/agents.py:1377-1386` (confirm, then `_post`).

**`@_annotate_risk` is the risk half, and it is a shared convention, not decoration** —
`acb_skills.tool_annotations.annotate`, aliased identically across the email assistant,
the GTD skill and the ClickUp skill, read by `permission_policy.py:184` and the
orchestrator's tool builders. Its vocabulary is four booleans (`read_only`, `destructive`,
`idempotent`, `open_world`). **Annotation is not enforcement** — the policy layer
explicitly defers to the tool's own `request_confirmation` (`permission_policy.py:15,194`).
So: annotate *and* confirm; neither substitutes for the other.

**Confirmation and the Action Broker are not interchangeable — they fail in opposite
directions.** `request_confirmation` denies on error; `_broker_gate` proceeds on error
(`routes/tasks/providers.py:139-147,165-167` — "a broker-layer error never blocks a
user-approved write"). That difference is the whole division of labour:

| Write | Mechanism | Why |
|---|---|---|
| Agent tool → native CRM row | `request_confirmation`, fail-closed | Arguments are LLM-filled; there is no prior human approval to inherit |
| That row → Zoho | the existing `crm.zoho_*` broker gate (`crm/broker_handlers.py:45-50`) | By then already approved; the broker's job is audit + kill switch, and it must not lose the write |
| `CRM_AUTO_LEAD` rows | no tool confirmation possible → **the flag itself is the gate** | No human is in the loop at all; hence OWNER-GATE (§6) |

**Done-when:**
1. Each of the four tools awaits `request_confirmation` **before** any mutating request is
   constructed — proven by a test asserting zero HTTP calls when confirmation is denied
   (the shape at `test_email_tool_consolidation.py:272-280`).
2. Each is `@_annotate_risk(destructive=True, ...)`; the existing
   `test_crm_agent.py::test_the_tools_are_risk_annotated_read_only` is generalised rather
   than duplicated.
3. **Non-interactive runs deny**: no delivery channel ⇒ no write, for all four, and no tool
   passes `non_interactive_default="approve"` — assert the absence structurally, the way
   the read half asserts its path guard.
4. The read half's `_record_uuid`/`_entity_slug` guards cover every new path segment; the
   AST fence is extended to the write tools' path idiom (today it only scans `/crm`
   f-strings — a `.format()`/`%`/`+` build passes it, per the re-review's P2).
5. `convert_lead` keys on `converted_deal_id`, never `converted_at` (§8 B6).

**Tests:** `tests/unit/test_crm_agent_write.py` (B7).

**Flipping `CRM_AUTO_LEAD` = OWNER-GATE (§6)** — and per D-CRM-9 the ON-state queues each
auto-created lead for push into the live Zoho tenant.

### WS-26f — Pipeline truth + settings UI · ✅ **BUILT 2026-08-07** · 🔴 **OWNER-GATE to run against prod**
*(§5.1 system 1. Trigger: the owner's first live look, 2026-08-07 — lanes out of order,
imported stages at 0%. Three sub-slices, one branch; f2 and f3 are useful even if f1's
metadata probe comes back empty-handed.)*

> **As built** (branch `ws-26f-pipeline-truth`, no migration — 144 already had every
> column): `routes/crm/stage_metadata.py` (f1 + f4, registered on `core.router`,
> reaching Zoho through `import_zoho._client()` so one seam is bound in tests),
> `ingestion/sources/zoho/client.py` += `list_deal_layouts` / `list_deal_pipelines`
> + `ZohoScopeError` / `ZohoApiVersionError`, `admin.py`'s D-CRM-10 clamp,
> `pipeline.py`'s per-lane `weighted` aggregate, and on the frontend
> `?tab=settings` (`components/PipelineSettings.tsx`, `lib/settings.ts`) plus the
> weighted ₹ surfaces. Tests: `tests/unit/test_crm_stage_metadata.py` (new, 40),
> clamp cases in `test_crm_routes.py`, lane cases in `test_crm_pipeline.py`,
> `lib/settings.test.ts` (new), `board.test.ts` + `urlState.test.ts` extended.
>
> **Three deliberate departures from the text below, each with its reason:**
> 1. **A created won/lost lane lands at 100/0, not "probability 0"** (step 3). The
>    same PR makes the admin API 422 a won-type lane that does not forecast 100
>    (D-CRM-10), so creating one at 0 would mint a row f2's own grid refuses to
>    save. "Probability 0" still holds for every stage the probe cannot type.
> 2. **Both settings reads go to `/crm/v8`; every record reader stays on
>    `/crm/v2`.** `settings/pipeline` does not exist on v2, and the layouts read
>    is scoped to the same pipeline call, so both go through one helper
>    (`client._settings_json`) against one constant,
>    `client.SETTINGS_API_VERSION = "v8"` — the only v8 in the tree, named once
>    so there is exactly one thing to change. The other **eight** reads are
>    untouched on v2 — the six module readers, `list_deleted` and `list_users`.
>    A tenant that refuses
>    the version raises `ZohoApiVersionError` and the run **reports it and
>    writes nothing**; it is never walked down the version list, because the
>    endpoint that answers on an older version is a different endpoint with
>    different fields. ⚠️ If the first real dry run comes back with a version
>    refusal, the thing to change is that one constant — there is no v2 layouts
>    call to go hunting for.
> 3. **The response key is read tolerantly** (`pipeline` then `pipelines`; a
>    pipeline's stages from `maps` then `stages`). Misreading it is
>    indistinguishable from "this tenant configured nothing", and both write
>    nothing — so the cost of getting it wrong is a silent no-op, which is the one
>    outcome this endpoint exists to prevent.
>
> **Review repairs, 2026-08-07 (diff review, same branch):**
> - **The D-CRM-10 clamp judges a TRANSITION, not a resting state.** It fires
>   only when the payload names `type` or `probability`. Reading the stored row
>   unconditionally made the importer's own output unmanageable: `ensure_status`
>   mints an unseen Zoho stage at probability 0 and guesses its type from the
>   name, so a pull creates won-type lanes at 0% — and a position-only PATCH on
>   one answered 422 about a probability the caller never mentioned, aborting
>   f2's reorder loop partway and leaving duplicate positions. The rule is "you
>   may not MOVE a stage into a contradiction", not "you may not touch one".
> - **`closed_at` is absent, not zeroed, when the backfill never ran.** The stop
>   and the unavailable outcomes return before the database is opened, and a
>   zeroed section there reported a measurement nobody took ("0 close dates
>   missing", next to a banner saying nothing was read). `null` is the sentinel
>   on both sides; the UI reads it through `settings.ts::backfillRan`.
> - **The probability probe has a fourth outcome, `unavailable`.** A refused
>   layout read (or a tenant with no Deals layout) used to leave the probe at its
>   `no_data` default — a claim ABOUT a layout nobody had, pointing the owner at
>   the settings grid when the thing to change was the API version constant.
> - **A reorder issues every PATCH and reports the failures together**
>   (`settings.ts::applyPatches`). Stopping at the first refusal left the written
>   rows on their new numbers and the rest on their old ones.
> - **Deleting a lost reason is confirmed** — `crm_deals.lost_reason_id` is
>   `ON DELETE SET NULL`, so a misclick silently strips the attribution WS-26g's
>   lost-reason breakdown reads, and nothing on screen changes to show it. The
>   dialog names that consequence. ⚠️ **Count-before-delete is DEFERRED** (the
>   admin API returns no usage count for a reason, unlike the 409 that guards a
>   status still holding deals); the confirm gate is what shipped.

**Root cause, verified in code.** `import_zoho.py::_ensure_status` appends an unseen
Zoho stage past the last `position` with `probability = 0` — the right refusal at import
time, the wrong place to leave the data forever. 144's seeds renamed Zoho defaults
("Proposal", "Negotiation" vs the tenant's likely "Proposal/Price Quote",
"Negotiation/Review"), so name-match missed them and the board is seed lanes (some
plausibly empty) followed by the tenant's real stages in first-encounter order.

**f1 — the stage-metadata pull.** `POST /crm/import/zoho/stages`, floor
`admin:access:manage` (this rewrites the pipeline, not a record — same floor as the
record import, same reasoning):
1. Resolve the Deals layout id (`settings/layouts?module=Deals`), then
   `GET /crm/v8/settings/pipeline?layout_id=…` (scope `ZohoCRM.settings.pipeline.READ`).
   Per stage: `display_value`, `sequence_number`, `forecast_type`, `forecast_category`.
   That is the tenant's REAL lane order. **If more than one pipeline returns, STOP and
   surface it — D-CRM-11 goes back to the owner before anything is written.**
2. Probability: probe the same layout metadata's Stage `pick_list_values` for a per-value
   `probability` (Zoho documents this inconsistently, and this tenant already withholds
   record-level fields — §7.1). Absent → probabilities stay untouched and f2 is the
   editor; the report must distinguish **no-scope** (an OAuth scope error, named in the
   response) from **no-data** (metadata present, field absent) — the existing token was
   not minted with `settings.*` scopes, so no-scope is the *expected* first outcome, and
   re-minting the token is the owner's act (WS-2 territory, named in the report).
3. Apply by name-match to `crm_deal_statuses`: matched → `position = sequence_number×10`,
   `type` from `forecast_type` ("Closed Won"→`won`, "Closed Lost"→`lost`, else keep the
   existing `type`); unmatched tenant stage → created in sequence (probability 0, owner
   sets it in f2); native-only lane (no tenant counterpart) → **reported, never deleted**
   — deletion is the owner's call in f2, and `ON DELETE RESTRICT` 409s a lane holding
   deals anyway.
4. **Dry-run by default, `?apply=true` to write** — the record importer's own pattern
   (fetch and report, write nothing). The report lists per-stage: matched/created,
   position before→after, type before→after, probability before→after, orphans.
5. **No deal row is touched.** Statuses tables are native config and do not sync to Zoho;
   the repair queues zero pushes. Pinned by test, not asserted in prose.

**f2 — Pipeline Settings surface.** `?tab=settings` in the existing URL grammar (no new
route file): three grids — deal stages (drag-reorder writes renumbered `position`s ×10
via the existing per-row PATCH; inline name/color/type/probability/is_default), lead
statuses (same, minus probability), lost reasons (label + position). Gate: `feature:crm`
(D-CRM-3 — the sales team manages its own pipeline); the f1 pull button additionally
behind `admin:access:manage`. Server-side clamp lands in `admin.py` PATCH/POST:
probability outside 0–100 → 422; won-type with probability ≠ 100 or lost-type ≠ 0 → 422
naming the rule (D-CRM-10). The grids use the EXISTING admin API — if a needed endpoint
is missing, that is a spec bug to raise, not an addendum to sneak in.

**f3 — probability on the money surfaces.** Weighted ₹ per lane under the existing lane
total and a board-header total+weighted rollup — one pure function in `board.ts`
(deal-level `probability`, NULL treated as stage default — the inheritance materializes
on entry, so NULL survives only on rows that predate a move; handle it anyway).
Record sheet: probability becomes an editable field with its stage's default shown when
inherited; the status-pill dropdown shows each stage's default beside its name.

**f4 — the `closed_at` proxy backfill *(added 2026-08-07 for the demo path, §9.0)*.**
The importer never stamps `closed_at` (only native transitions do), so every imported
won/lost deal is invisible to win/loss, cycle-time and won-₹ math. Same apply, separate
report section: deals sitting in a won/lost-type stage with `closed_at IS NULL` adopt
their `expected_close_date` (Zoho's `Closing_Date`) as a **labeled proxy** — it is the
only date the tenant gave us; rows where it is also NULL stay NULL and are counted in
the report rather than invented. ⚠️ **The backfill must be a direct UPDATE that bypasses
the dirty-marking write path**: `closed_at` is native-only bookkeeping, and routing 500+
rows through `mark_dirty_on_update` would queue that many no-op pushes into the live
Zoho tenant on the next cycle.

**Done-when:**
1. Dry-run returns the full report and writes nothing — test pins zero writes, including
   the >1-pipeline stop.
2. Apply is idempotent — second run reports zero changes.
3. No-scope vs no-data are distinguishable in the response, each with a named test; the
   no-scope response names the missing scope string.
4. Post-apply, the ONLY deal-row change is f4's `closed_at` on won/lost-stage rows with
   a non-NULL `expected_close_date`, and **nothing is `zoho_dirty` that wasn't before**
   — pinned by test (the §5.1 safety property, and the proof f4 bypassed the
   dirty-marking path). NULL-date rows counted in the report, not invented.
5. Settings grids render from the live GETs; reorder issues renumbered PATCHes; the
   won=100/lost=0 clamp is tested on BOTH sides (client validation message, server 422).
6. Weighted math, **split across the two places it is computed** *(amended 2026-08-07
   by the WS-26f audit's correction C4 — the original clause said "lane + header render
   it through the BFF against fixtures", which a rows-derived lane total would have
   satisfied while being wrong)*: the **lane** figure is a whole-lane SQL aggregate
   surfaced as `weighted` on `PipelineLane` (`pipeline.py` and `lib/types.ts`), because
   `get_pipeline` caps `rows` at `per_lane` and the frontend sends no cap — a
   rows-derived lane total is explicitly not acceptable, and the lane number is bound by
   tests against the SQL (including the `COALESCE` onto the stage default). The **pure
   function** stays in `board.ts` for the header rollup over lanes and the record-sheet
   math, bound by fixture tests for NULL/0/100/mixed probability and the
   open/ongoing-only filter — `on_hold` excluded, with its own named assertion.
7. Running `?apply=true` against prod is **OWNER-GATE** (work_plan §6) — everything
   before that line is agent-safe.

**Tests:** `tests/unit/test_crm_stage_metadata.py` (new), `test_crm_routes.py` (clamps),
vitest: `board.test.ts` (weighted math), settings-grid helpers colocated.

### WS-26g — Forecast & funnel reports · ✅ **BUILT 2026-08-07** (branch `ws-26g-reports`, no migration)
*(§5.1 system 2. The instrument has run since 26a — but read what it actually records:
`crm_status_changes` logs TRANSITIONS only. Record creation writes no row
(`records.py::create_record`) and the Zoho importer writes none either, so every
imported deal has zero log rows and a deal's first stage never appears as a
`to_status`. The funnel definition below is written for that shape, not around it.
`closed_at` and `status_changed_at` are stamped by the move path; `dwell_seconds` is
stamped on the row for the stage being LEFT and is deliberately NULL on a first
transition — "we have no earlier timestamp" is not a measurement of zero.)*

`?tab=reports`, read-only SQL in a new `routes/crm/reports.py` (same router+gate;
core.py-is-the-leaf). Four blocks, each a named endpoint:
1. **Pipeline by stage** — per open/ongoing stage: deal count, ₹ total, ₹ weighted;
   grand totals. Same formula as f3's board math — **do not retype it**: reuse the one
   weighted SQL expression (lifting `WEIGHTED_SQL` into `core.py` beside
   `WEIGHTED_TYPES` is the sanctioned move; `_crm_fakes.py::_WEIGHTED_SUM_RE` reads the
   formula out of the statement text precisely so a drifted copy changes the test's
   answer). The cross-language parity test pins `board.ts::weightedDeal`/`weightedRows`
   against the SQL on ONE shared fixture file — `tests/fixtures/crm_weighted_parity.json`
   (new directory), rows of `(amount, deal_probability, stage_probability, stage_type,
   expected_weighted)` — read by BOTH runners, pytest and vitest. Two independently
   typed tables are not parity. (The `priority.ts ⟷ priority.py` "precedent" is two
   hand-maintained mirrors joined by a comment, with no shared fixture anywhere in the
   repo — WS-26g mints the mechanism, it does not follow one.)
2. **Funnel** — per stage, three numbers, each defined here because both conventions
   exist in the wild and the log's shape makes naive readings wrong:
   - **Entered** = deals whose VISITED SET includes the stage. A deal's visited set is
     every `from_status` ∪ every `to_status` across its `crm_status_changes` rows
     (`entity_type = 'deal'`) ∪ its CURRENT stage name. The union is what makes
     creation- and import-stage membership count despite the transitions-only log: a
     deal imported into Qualification then moved once carries Qualification only in
     `from_status`; a deal with zero rows carries its stage only in `status_id`.
     Visited stages only — a deal jumping Qualification→Negotiation counts in those
     two, no backfill of skipped lanes.
   - **Conversion-forward %** = of the deals that visited stage N, the share whose
     visited set includes ANY deal-stage with `position` strictly greater than N's —
     "ever got past it", not "went to exactly N+1". Zero when the denominator is zero,
     never an error.
   - **Median dwell days** = median of `dwell_seconds` grouped by **`from_status`** —
     that column names the stage being LEFT, the opposite key from the `to_status`
     reading. NULL `dwell_seconds` rows are excluded (first transitions, by design),
     and a deal still sitting in a stage has no dwell row at all — label the number as
     median-over-departures, because that is what it is.
   Status names are the join key (the log stores NAMES, not ids) and f2's settings grid
   can RENAME a lane, which orphans that name's history: rows whose
   `from_status`/`to_status` match no current deal-stage name are tallied into an
   explicit `unmatched` field on the payload — reported, never silently dropped.
3. **Win/loss** — trailing 90d: win rate (won/(won+lost) by `closed_at`), average cycle
   (`created_at`→`closed_at`), lost-reason breakdown. Two data truths this block is
   built FOR: `closed_at` is NULL on every imported closed deal until the owner runs
   f4's backfill (§6 gate (d) — NOT yet run), so NULL-`closed_at` rows fall outside the
   window — zeros, never errors, never "closed today"; and the lost-reason gate binds
   native transitions only — the importer bypasses both gates and `lost_reason_id` is
   `ON DELETE SET NULL` — so the breakdown carries a NAMED unattributed bucket rather
   than dropping those rows. (An earlier version of this block claimed the reason data
   is "complete by construction". It is not: imports.)
4. **Owner leaderboard** — per `owner_email`: open count, weighted ₹, won ₹ trailing 90d.

**Done-when:** each block has fixture tests proving the math (the visited-set union
rule and the `from_status` dwell key each named in a test); an empty CRM returns zeros,
never errors; NULL `closed_at` / NULL `dwell_seconds` never error; unmatched status
names are reported, not dropped; the tab renders through the BFF; all math server-side
except f3's board duplication, which the shared-fixture parity test bridges.

**Tests:** `tests/unit/test_crm_reports.py`, shared fixtures via `_crm_fakes.py`. If
the reports SQL aggregates with `GROUP BY`, teach the fake a GROUP BY reader in its
existing expression-reading style (or emit per-key queries in `get_pipeline`'s shape and
accept the N+1 over 3.4k rows — pick deliberately and say which in the PR). Never
hard-code an aggregation answer into the fake: that turns "fixture tests proving the
math" into a mirror agreeing with itself.

**As built (2026-08-07).** *The choice above was made: **per-key queries**, no `GROUP BY`
reader added.* The weighted expression binds the LANE's own default as
`:stage_probability`, so a grouped statement would have to reach that value through a
join and would stop being the expression `_WEIGHTED_SUM_RE` reads — which is the whole
mechanism that makes a drifted formula change the test's answer. The set-shaped questions
(visited sets, medians, orphan names) read their rows and are answered in Python, where
"a deal's visited set" is expressible at all. Cost: O(stages) + O(owners × stages) small
aggregates over 551 rows, with the owner list capped at 25 and the remainder REPORTED.

Endpoints: `GET /crm/reports/{pipeline,funnel,win-loss,owners}`, registered on the shared
gated router. `WEIGHTED_SQL` moved to `core.py` beside `WEIGHTED_TYPES` (`pipeline.py`
re-exports it); `core.status_wire` absorbed `admin`'s and `pipeline`'s duplicate status
projections rather than gaining a third. `?tab=reports` extends `NON_ENTITY_TABS` and the
`page.tsx` chain; `components/Reports.tsx` renders and `lib/reports.ts` holds the pure
helpers.

Two findings worth carrying forward:

1. **The `entity_type = 'deal'` filter was not, at first, load-bearing** — the funnel keys
   every set through deal ids, so a lead's log row (carrying a lead's `entity_id`) is
   excluded twice over and the obvious mutant SURVIVED. `crm_status_changes.entity_id` has
   **no foreign key** (144, so the log outlives the row), so the id space is not a
   guarantee and a mis-stamped write is a live possibility. The behavioural test now seeds
   a row stamped `lead` against a DEAL's id — the exact state the filter rejects — and the
   mutant goes red with lead-vocabulary stage names appearing in the deal funnel's
   `unmatched`. A statement-text assertion backs it up.
2. **`_crm_fakes`' `lower(col) = :param` reader matched a NULL column**, which SQL never
   does (`lower(NULL) = ''` is UNKNOWN, not true). It made the unassigned-owner bucket
   count rows its own aggregate would never have summed. Fixed to SQL's semantics, which
   is also why NULL and blank `owner_email` are two rows on the wire and one word
   ("Unassigned") in the UI: the per-owner aggregate can bind `IS NULL` or
   `lower(owner_email) = :owner` and not an OR, so folding them server-side would give a
   row whose count came from one rule and whose ₹ came from another.

**Three defects found in diff review (PR #397) and fixed on the same branch:**

3. **`refreshCollection()`'s `reports` branch was unreachable from the one write its own
   comment named.** `moveDeal` hard-coded `api.getPipeline` and never called
   `refreshCollection()`, so from `?tab=reports&deal=<id>` the sheet's status pill moved a
   deal and all four blocks kept their old numbers indefinitely — while the fetched
   pipeline landed in `lanes`, state that tab does not render, so board and reports
   disagreed on one screen. A *lead* status change went through `patchRecord` and DID
   refresh, so the two entities behaved differently for no visible reason. `moveDeal` now
   goes through the same seam every other write uses (`loadBoard` issues the identical
   owner-scoped request, so the board keeps its scope). **There was no `store.test.ts` at
   all** — the branch was asserted only by its comment. There is one now, and it pins WHICH
   request each write issues rather than the state it settles into: a store's job is
   deciding what to re-read, and final-state assertions cannot see a decision never made.
4. **The leaderboard's bucket key and its SQL predicate normalised differently.** `_owners`
   grouped on `.strip().lower()` while the aggregate matched `lower(owner_email)` with no
   `trim()`, so `'vj@fracktal.in'` and `'vj@fracktal.in '` shared a bucket the SQL then
   under-filled: the padded deal landed in NO bucket, `omitted` still said 0, and "By owner"
   silently disagreed with "Forecast by stage". Now `lower(trim(owner_email))`, and the
   fake reads the wrapper as an expression (`_LOWER_EQ` captures it, `_normalized` applies
   it) so dropping `trim()` changes the test's answer.
5. **The trailing window had a lower bound only.** f4 backfills `closed_at` from Zoho's
   `Closing_Date`, which is a *forecast* date — imported deals routinely carry one in the
   future — so a `Closed Lost` deal dated next quarter counted as closed in the last 90
   days, moved `win_rate`, and added its whole forward span to the cycle average. The
   report would have got *worse* the moment the owner ran the repair meant to make it work.
   Both predicates (win/loss and the leaderboard's won ₹) now carry `closed_at <= :until`,
   inclusive at both ends, stated in the test name.

**Mutants measured red:** the `entity_type` filter (a), `from_status` → `to_status` dwell
grouping (b), the current-stage union term (c — the 551-imported-deals-report-zero case),
the weighted formula's inner `COALESCE` in SQL *and* in `board.ts` (d, both parity halves),
silently dropping unmatched names (e), `trim()` off the owner predicate (f), the win/loss
upper bound (g), the leaderboard's upper bound alone (h), and `moveDeal` reverted to its
hard-coded board fetch (i, three vitest cases).

### WS-26h — Stage discipline: entry requirements + rot · ✅ BUILT 2026-08-11 · 🟢 AGENT-SAFE · after f2 · fence converted 2026-08-11 (WS-26h-fence)
*(Built on branch `claude/crm-command-center-tasks-i8l7n4`, migration 169, no owner gate
touched. The ticket below is left as written — it is what was built against; the status
header's WS-26h paragraph records what shipped, what was measured, and the one gap left
open on purpose.)*
*(**WS-26h-fence, 2026-08-11** — this ticket's siting fence was a literal substring scan
and is now an AST call-graph fence sharing WS-26h2's helpers, keyed on
`_MOVE_GATE = ("pipeline", "apply_status_transition")`. Test + docs only; no `routes/crm/`
change. See the status header's WS-26h-fence paragraph for the mutant table.)*
*(§5.1 system 3. The mechanism exists in miniature: lost-type moves already demand a
reason — `needsLostReason` in `board.ts`, enforced server-side. Generalize exactly that,
change nothing about it.)*

- Migration (next free number at build time, R1): `crm_deal_statuses` gains
  `required_fields TEXT[] NOT NULL DEFAULT '{}'` and `max_dwell_days SMALLINT NULL`
  (NULL = never rots).
- `required_fields` values come from a server allowlist of real deal columns (`amount`,
  `expected_close_date`, `organization_id`, `owner_email`) validated at PATCH time —
  free-text column names are how a typo becomes a lane nobody can enter.
- Server: the status-move path in `pipeline.py` (the one that stamps `closed_at`) returns
  422 naming the missing fields when the TARGET stage requires them and the deal (plus
  the same PATCH's own updates) lacks them. Entry-only: requirements never block a
  non-move edit.
- UI: `planMove` grows the check alongside `needsLostReason` — the move modal prompts for
  the missing fields inline and sends ONE PATCH carrying fields + status together.
- Rot: presentation only. The card's existing stage-age turns amber past
  `max_dwell_days`, red past 2× — computed from `status_changed_at`, no mechanics, no
  locks.
- f2's grids grow both columns (hence *after f2*).

**Done-when:** move-with-missing-fields → 422 naming them; same PATCH carrying the fields
→ succeeds; non-move PATCH never blocked; unknown field name in `required_fields` → 422;
rot thresholds pure-function tested (NULL, boundary, 2×); settings grid edits both
columns.

**Tests:** extend `tests/unit/test_crm_pipeline.py`; vitest `board.test.ts` (rot,
move-plan).

### WS-26h2 — Entry requirements on the CHOSEN create stage · ✅ BUILT 2026-08-11 · 🟢 AGENT-SAFE · no migration
*(Built on branch `claude/crm-command-center-tasks-i8l7n4`, no migration, no owner gate
touched — the gate landed in `records._resolve_status` and nowhere else, which is the
condition the label below is stated against. The ticket is left as written; the status
header's WS-26h2 paragraph records what shipped and what was measured.)*
*(Minted 2026-08-11 from the WS-26h CREATE-gap audit. Contract authored by the
spec-auditor, not by the implementer. Closes the create half of the gap **for the case a
caller actually chose the stage**, and leaves the defaulted case deliberately open per
**D-CRM-13** — which is where WS-26h's rationale actually bites.
`records._resolve_status` already distinguishes the two: `values.get("status_id")` is
the caller's choice, `load_default_status` is the server's.)*

> ⚠️ **The audit corrected a miscount that both this spec and `pipeline.py`'s docstring
> carried: there are THREE ungated create paths, not two.** The third is
> `import_zoho.apply_record` → `core.upsert_by_zoho_id`, reached by the backfill route
> **and by `sync_zoho.pull_phase` — the enabled 600s production loop.** It builds its own
> `INSERT … ON CONFLICT (zoho_id) DO UPDATE` and calls neither `core.insert_row` nor
> `records.create_record`. **It must stay ungated.** An implementer told there are two
> paths will find the third and gate it, and the next sync cycle will start refusing rows
> from the live upstream tenant. Both stale sentences are corrected in this ticket's PR
> under R4.

- Server: in `records._resolve_status` (`records.py:139`), beside the existing
  lost-reason create refusal, apply `pipeline._require_entry_fields` **only when the
  caller supplied `status_id`.** Never when the status came from `load_default_status`.
- `_require_entry_fields` must accept "no existing row" as a first-class shape rather
  than being handed `None` and relying on `getattr(None, field, None)` (`pipeline.py:168`).
- The gate must be **importable into `records.py` and reachable from nowhere else.** It
  must not be placed in `core.insert_row`, `core.upsert_by_zoho_id`,
  `import_zoho.apply_record` or `apply_module`. `core.insert_row` keyed on
  `table == "crm_deals"` is the tempting "one seam" and misses the pull today **only
  because `upsert_by_zoho_id` duplicates the statement rather than delegating** — do not
  build on that accident.
- No migration, no UI change, no change to `core.STAGE_REQUIREABLE_FIELDS` or
  `board.ts::REQUIREABLE_FIELDS`.

**Done-when** (each measured red first, per R7):
1. `POST /crm/deals` with an explicit `status_id` naming a stage whose `required_fields`
   the body does not satisfy → **422 naming exactly the missing fields**, and no
   `crm_deals` row is written.
2. The same POST carrying the missing fields in the same body → **201**, one row,
   `status_id` as sent.
3. `POST /crm/deals` with **no** `status_id`, against a default stage carrying
   `required_fields = {amount}` and a body with no amount → **201**. The defaulted path
   is not gated (D-CRM-13).
4. `POST /crm/leads/{id}/convert`, default stage carrying
   `required_fields = {organization_id}`, on a lead naming no company → **200 and a deal
   created.** Conversion is never refusable by a settings-grid edit.
5. `required_fields = {owner_email}` on an explicitly-chosen stage with no `owner_email`
   in the body → **201**: `create_record` defaults an absent owner from the acting user
   before the status is resolved, so it is never absent. An explicit `"owner_email": null`
   → 422.
6. The create path shares `_is_blank` with the move path, asserted **on** the create
   path rather than assumed to carry: `0` for a required `amount` is a **value** (201),
   and `""` / `"   "` on a required text field are **absent** (422).
   ⚠️ **Corrected 2026-08-11 (repair round 1): this criterion originally also demanded
   `Decimal("0.00")` → 201 through `POST /crm/deals`, which is unsatisfiable.** `DealIn.amount`
   is `float | None` and `patch` on the create path is always `clean_payload(DealIn)`, so a
   `Decimal` provably cannot reach the gate that way — measured:
   `DealIn(name='x', amount=Decimal('0.00')).amount` → `0.0 <class 'float'>`. The `Decimal`
   half belongs to the **move** path, where WS-26h measured it against a real `NUMERIC`
   column; on the create path it is asserted at the gate itself
   (`_require_entry_fields(status, NO_EXISTING_RECORD, {"amount": Decimal("0.00")})`), which
   is what the shared `_is_blank` actually earns. The test is kept and relabelled, not
   deleted — mutating `_is_blank` to plain falsiness turns it red.
7. Creating a **lead** into a lead status is never gated — asserted explicitly, not left
   to the absence of the column (`169_crm_stage_discipline.sql` is deal-only, and
   `auto_lead.py:862` reaches `create_record` for leads).
8. **Structural fence (R7), the load-bearing one.** A sibling of
   `test_crm_pipeline.py::test_the_zoho_pull_never_enters_the_stage_gate` (which ~~greps the
   literal `apply_status_transition(`~~ — **corrected 2026-08-11 by WS-26h-fence: it is now
   an AST fence on the same helpers, keyed on `_MOVE_GATE`** — and still would **not** fire
   on this ticket's change, because it protects the move gate only), asserting that no
   `routes/crm/` code outside
   `pipeline.py` + `records.py` can reach `_require_entry_fields`. `import_zoho.py` /
   `sync_zoho.py` / `core.py` reaching it means the **enabled production sync loop**
   (600s, §6 WS-26 (a)) starts refusing rows from the live upstream tenant on the next
   cycle after any settings-grid save. Assert structurally, not by example — the plausible
   refactor ("make the importer use the shared create seam") is exactly the one no example
   test would be watching.
   ⚠️ **Corrected 2026-08-11 (repair round 1): the shape this criterion originally
   prescribed — "the set of files CONTAINING the literal `_require_entry_fields(`" — is a
   text match, and it could not back the reachability claim its own docstring made.** Three
   holes, all measured: `import_zoho.apply_record` calling `records._resolve_status` stays
   GREEN (no new call site, yet the gate lands on the pull — and `apply_record` already sets
   `values["status_id"]` server-side, so `chosen` would be truthy for every pulled deal);
   an aliased import stays GREEN; and a **comment** in `import_zoho.py` recording why the
   path must stay ungated turns it RED, making deletion of that comment the cheapest way
   back to green. What ships instead is **two AST fences over `routes/crm/*.py`**:
   `test_the_entry_gate_is_called_from_exactly_two_files` (real call nodes, aliases and
   module-attribute forms resolved, comments and docstrings ignored) and
   `test_no_zoho_pull_path_can_reach_the_entry_gate` (transitive static call graph from
   ~~`sync_zoho.pull_phase`~~ **`sync_zoho._sync_loop` / `run_cycle` / `_run_cycle_locked` —
   widened 2026-08-11, repair round 1** — and `import_zoho.apply_module`, reporting the chain
   it found). Entering at `pull_phase` fenced only the pull half: a cycle also PUSHES
   (`push_records` / `push_activities` / `push_tombstones`, and below them
   `apply_push_result` / `_settle` / `_fail`), and a gate reached from the push half ran
   against the live tenant every cycle while both reachability fences reported `[]`. Measured
   green at `pull_phase`, red at `_run_cycle_locked`.
   **Stated limit, so the claim does not outrun the mechanism:** the graph is STATIC and
   scoped to `routes/crm/*.py` — it does not see dispatch through a variable, a registry
   dict or a callback handed across the package boundary, and nothing in the package
   reaches the gate that way today.
   ⚠️ **This limit statement was itself an overclaim until 2026-08-11 (WS-26h-fence repair
   round 1) — the same defect one layer up.** It listed three blind spots and had **six
   more**, every one of them a shape the deleted substring scan CAUGHT: relative imports at
   any level (`from .pipeline import …`, `from . import pipeline`, `from ..crm.pipeline import
   …`), star imports, a symbol re-exported through the package `__init__`, a name bound to the
   package itself (`from .. import crm` → `crm.pipeline.f()`), and calls written at module
   level outside any `def`. All six are now READ and each has a synthetic case.    ⚠️ **What stays blind, split by the ONE question that decides whether a hole is a REGRESSION against the substring scan this replaced — is the gate's own name written immediately before a `(`?** **(A) Name never written before a `(`, so the old scan was blind too — NOT regressions:** dispatch through a value (`_MOVE = apply_status_transition` then `_MOVE(…)`, `partial`, a cross-package callback); a registry or object holding the gate under ANOTHER name (`_REGISTRY["move"](…)`, `Registry.move(…)`); and `getattr(pipeline, "apply_status_transition")(…)` — **measured green on the old scan too**, because `")("` intervenes and the literal never appears. **(B) Name IS written before a `(`, so the old scan went RED — residual REGRESSIONS, exactly two, both left open deliberately:** a call qualified by something the graph cannot tie to a package module — `importlib.import_module("…pipeline").apply_status_transition(…)` and `_GATES.apply_status_transition(…)` where `_GATES` is a local object or class. ⚠️ **The reason recorded in repair round 1 for leaving them was FALSE and is corrected here:** it claimed closing them meant resolving unbound attributes against every top-level name, letting an innocent `db.close()` fabricate a chain — a reviewer disproved it by building the fix, and a narrow resolver reading only STRING CONSTANTS adds **no** edges to the real package, reddens both forms, and never looks at `db.close()`. **The real reason is reach, not risk:** `importlib.import_module` appears **once** in all of `apps/` + `packages/` (`orchestrator/declarative.py:100`, a plugin loader) and **zero** times in the gateway. Neither shape is the plausible refactor this fence targets; both are deliberate evasion, and a fence cannot be built against someone willing to edit the fence file. **(C)** An indirect route beginning at module level (module-level code is a call SITE but not an entry POINT). **(D)** Anything outside `routes/crm/*.py`. Eight shapes were pinned against synthetic packages
   (`test_the_siting_fences_see_the_shapes_they_claim_to_see`) so "the fence went blind" is
   a red test, and all six were re-measured against the real package — eight being what h2
   shipped. *(**2026-08-11, WS-26h-fence and its two repair rounds:** it is now a
   **25-case synthetic self-test** and asserts **both** gates on every one of them — seven
   cases added for the move gate, nine for the import spellings the first AST cut could not
   read, one for `_merge_names`' union. ⚠️ **That number is itself pinned now**:
   `test_the_spec_quotes_the_real_number_of_fence_cases` reads every
   "N-case synthetic self-test" out of THIS file and asserts it equals `len(_FENCE_CASES)`,
   because two consecutive repair rounds found a stale count here and nothing failed.)*
   ⚠️ **Repair round 2:** `_crm_imports` reads the WHOLE tree so a **function-body import**
   counts, and nothing made that capability fail if it regressed — mutating it to
   `tree.body` left the synthetic suite 8 green and the whole file 84 green. It is not a
   nicety: **`core.py` cannot import `pipeline` at top level at all** — measured,
   `ImportError: cannot import name 'CLOSING_TYPES' from partially initialized module
   'gateway.routes.crm.core'` — so the "one seam" mis-siting this fence names as the one to
   watch can ONLY ever be written as a function-body import, and `core.insert_row` is not
   reachable from the pull entry points, so the reachability half does not cover it either.
   The `core.py` fixture used a top-level import, i.e. it modelled a siting that cannot
   exist. Moving that import inside the function closed both halves at once: the fixture is
   now realistic and the `tree.body` mutant goes red.
9. `test_crm_zoho_import.py` and `test_crm_zoho_sync.py` pass **unchanged** — no edit to
   either file is permitted in this PR. An edit there is the signal the gate was mis-sited.

**Tests:** extend `tests/unit/test_crm_pipeline.py` and `tests/unit/test_crm_routes.py`;
`tests/unit/test_crm_convert.py` for done-when 4.

**Gate label: 🟢 AGENT-SAFE — conditional, and the condition is done-whens 8 and 9.**
Stated so a reviewer can check it rather than trust it: the slice touches no Zoho code,
needs no migration, flips no flag, and writes nothing to the live tenant beyond what any
native create already does (a created deal is born `zoho_dirty` and pushes within one
cycle — unchanged here). **It becomes OWNER-GATE the moment the check is placed anywhere
`sync_zoho.pull_phase` traverses**, because §6 WS-26 (a) governs changes to a *running*
loop. The gate label is a property of the implementation SITE, not of the ticket.

**R8 does not bind this slice** — it introduces no SQL, no migration and no predicate.

✅ **CLOSED 2026-08-11 by WS-26h-fence** — the item banked below was built on the same
branch, test + docs only. `_GATE` became an **argument**, so `_gate_call_files` and
`_gate_reached_from` now answer for `_MOVE_GATE` and `_ENTRY_GATE` through one mechanism;
WS-26h gained the reachability half it never had. Both riders below are discharged: the
transitive coverage no longer has to be relied on (the move gate has its own reachability
fence, so removing or relocating the `apply_status_transition` → `_require_entry_fields`
call can no longer silently open the pull to the move gate), and the ~10-line follow-up was
taken as soon as the helpers reached `main` at `a06fa6a`. Measured old-vs-new on the real
package: aliased import **green → red**, indirect route **green → red**, comment naming the
call **red → green**. The record below is left as written.

⚠️ **Banked as a board item, deliberately NOT built here (2026-08-11, repair round 2):
WS-26h's own fence `test_the_zoho_pull_never_enters_the_stage_gate` has the same defect
this ticket fixed in its own** — it greps the literal `apply_status_transition(` in file
text, so it is blind to an aliased call and red on a comment, while its docstring claims a
structural property. It is a **correctness wart, not an exposure**, and the reason is worth
stating rather than re-deriving: its live-system property is **already covered
transitively** by `test_no_zoho_pull_path_can_reach_the_entry_gate`, because
`apply_status_transition` calls `_require_entry_fields` unconditionally — so any static
path from the pull to the MOVE gate is also a path to the ENTRY gate and fails there.
Measured: the direct and aliased forms go red on the new fence while WS-26h's stays green.
The residual is an aliased call from a file the loop cannot reach (`broker_handlers.py`,
`auto_lead.py` — and `CRM_AUTO_LEAD` is still OFF).
**Two riders, because both change what the follow-up is worth:**
- The transitive coverage **depends on `apply_status_transition` continuing to call
  `_require_entry_fields`**, and evaporates *silently* the day that call is removed or
  relocated. Nothing asserts the dependency today.
- `_crm_imports` / `_crm_call_graph` / `_gate_reached_from` back **both** fences, so
  whichever ticket converts WS-26h's fence fixes both at once — a ~10-line change once
  these helpers are on `main`, which is an argument for merging this branch rather than
  growing it.
The `TEXT[]` round-trip it depends on was verified against a real Postgres 16 by WS-26h
and is unchanged. Say so in the PR rather than leaving a reviewer to ask.

### WS-26i — Data management: merge, bulk, import/export, saved views · 🔴 AUDITED NO-GO 2026-08-11 · doc remediation DONE 2026-08-11 for two of the five

*(Remediation status, 2026-08-11: **i-export** specced and BUILT; **i-bulk** specced below
(`WS-26i-bulk`) with its four recorded drifts corrected in the audit bullet. **Merge, CSV
import and saved views stay 🔴 NO-GO** and still fail §1 contract point 3 — no implementer
may be dispatched from their bullets.)*
*(§5.1 system 4. Deliberately thin — the WS-26d lesson: four things behind one done-when
is how a ticket goes undispatchable. Each item below gets its own spec-auditor narrowing
before any build.)*

> **Audit record — 2026-08-11, all five items judged separately. Do not re-run this
> audit from the same starting point; it cost ~440s and every anchor below was opened on
> the code, not recalled.** The row fails §1 contract point **3** (no "Done-when" and no
> "Tests" line — the only ticket in §9 with neither), point **5** (§10 carries no WS-26i
> verification block) and point **7** (§9's header makes an unlabeled item read AGENT-SAFE,
> and four of the six sub-behaviours write the LIVE Zoho tenant, so the absent label is
> doing work it cannot do). **No implementer may be dispatched from this section until a
> per-item done-when exists.**
>
> **Per item:**
> - **Duplicate merge — NO-GO, doc-blocked on the spec's own admission, and it is the
>   destructive one.** §7.1 has no merge semantics and the writer has no merge verb. Three
>   uncosted consequences: the loser's tombstone issues a **real DELETE against the live
>   tenant** and we cannot roll back (R6); re-pointed activities do not follow, because
>   `crm_activities` is outside `ZOHO_TRACKED_TABLES` and §7.1 says activity deletes sync in
>   neither direction, so the loser's notes stay attached upstream to a record we then
>   delete; and `ORGANIZATIONS.cascades` under-describes an org merge — `crm_contacts.organization_id`
>   and `crm_deals.organization_id` are both `ON DELETE SET NULL` and the existing
>   `DeleteResponse.cascaded` never reports them. Needs merge written into §7.1 and a
>   D-CRM number. **Probably needs §6 registration too.**
> - **Bulk actions — NO-GO on point 3; sequence it SECOND even once specced.** Two findings
>   the precedent hides: (a) **the reuse seam Projects had does not exist here** —
>   Projects' bulk reuses `automation.apply_task_patch(db, …)`, but CRM's
>   `records.patch_record` (`records.py:235`) OWNS its own session — `async with
>   _tenant_session() as db:` at **`records.py:247`** — so it cannot be called once per
>   record inside a bulk request; the ticket must first extract a `db`-taking patch seam or
>   it grows a second CRM writer (the defect CLAUDE.md §4 names).
>   ⚠️ **Two corrections to this finding, 2026-08-11 (WS-26h-fence doc pass). The
>   precondition survives both; only its anchor and its reason were wrong.**
>   **(i) Anchors, re-verified at `a06fa6a`:** `patch_record` is at `records.py:235` and its
>   session at `:247`. This bullet previously cited neither, and any citation predating
>   WS-26h2 is **~29 lines low** — that landing moved the seam. Re-open the file rather than
>   trusting a number.
>   **(ii) The reason is NOT "bulk needs one transaction".** Projects' bulk is deliberately
>   **not** atomic: `routes/projects/bulk.py::bulk_edit` returns per-task
>   `applied`/`skipped`/`failed` (`bulk.py:337-339`, response at `:393-394`) and a single
>   record's refusal never rolls the batch back. What `patch_record`'s shape actually forbids
>   is **N records without N sessions**: one `_tenant_session()` per record in a loop, while
>   the request already holds one, is the pool hazard `auto_lead.py:78-79` documents in prose
>   and `tests/unit/test_crm_auto_lead.py:1311` pins as a test — "the step opens a SECOND
>   session per lead … so a pool that runs out fails several candidates at once". Projects
>   opens **one** session for the whole batch (`bulk.py:344-384`) and hands `db` down; CRM
>   cannot, until the seam is extracted. Bulk should copy Projects' per-record outcome
>   reporting, not invent atomicity Projects does not have.
>   ~~(b) **WS-26h collision** — `pipeline._require_entry_fields` raises a raw
>   `HTTPException(422)`, while Projects' per-record catch is on a typed `TaskPatchError`, so
>   that catch does not transfer.~~ **STRUCK as a blocker 2026-08-11: it is a design note,
>   not a second precondition, and reading it as one over-states the ticket's cost.** A
>   per-record `except HTTPException as exc:` narrowed to `exc.status_code in (404, 422)` —
>   re-raising everything else so a 401/403/500 still fails the request — turns both the
>   `require_row` 404 and the `_require_entry_fields` 422 into a per-record `failed` entry
>   **with no change to WS-26h/h2 code or tests** (`core.require_row`'s 404 is
>   `core.py:859`). Projects already catches a bare
>   `HTTPException` for its not-found arm (`bulk.py:349-350`) beside its typed one, so the
>   shape has precedent in the very file being copied. What remains true and load-bearing is
>   the **volume**: a bulk `{"status_id": X}` refuses every deal missing X's
>   `required_fields`, and each ACCEPTED move writes both a `crm_status_changes` and a
>   `crm_activities` row — ~1,500 inserts for a 500-row lane move.
>   ⚠️ **(c) A third precondition, undocumented anywhere until now and worth a repair round
>   if missed:** `tests/unit/test_crm_pipeline.py` pins the move gate's **seam location** to
>   `records.py`. Extract `patch_record`'s body into a new module and
>   `test_the_move_gate_is_called_from_exactly_two_files` goes red on the changed call-file
>   set. **As of 2026-08-11 (WS-26h-fence) that constraint is expressed STRUCTURALLY — an
>   AST call-graph fence keyed on `_MOVE_GATE = ("pipeline", "apply_status_transition")` —
>   and no longer by text match**, so it now also fires on an aliased call and no longer
>   fires on a comment. **`WS-26i-bulk` below decides the question: the seam STAYS in
>   `records.py`, so that fence passes with zero edits** — measured, a seam relocated to a
>   new module reports `['bulk_seam.py', 'pipeline.py']` and goes red, with `records.py`
>   dropping out of the set. `test_the_zoho_pull_never_enters_the_stage_gate` beside it is
>   the reachability half and stays green either way, provided the new home is not reachable
>   from the sync cycle (`sync_zoho._sync_loop` / `run_cycle` / `_run_cycle_locked`, which
>   covers the push half too) or from `import_zoho.apply_module`.
> - **CSV import — NO-GO, no precedent.** Neither `import_tasks.py` (local mirror) nor
>   `import_clickup.py` (API) is CSV. Inherits two hazards: every created row is born
>   `zoho_dirty` and mass-creates upstream, and §6 WS-26 (b) records that a `UNIQUE` index on
>   `crm_leads.email` was REFUSED — so "match-by-email first" has no uniqueness to lean on.
>   Also enters WS-26h's recorded gap from the far side: **CREATE is not gated**, so an
>   import can seed deals into a stage whose entry requirements they do not satisfy.
> - **Saved views — NO-GO on point 3, and its persistence call is doc-blocked in the DB
>   direction.** `serializeView`/`parseView` do round-trip the whole view, so "a saved view
>   is a named URL" holds. But a table means migration 170 **and** pre-empts an owner call the
>   tenancy plan explicitly parks: `specs/multi_tenancy.md` §58-65 records that on `crm_*`,
>   `organization_id` already means the CUSTOMER COMPANY, and *"that must be decided before
>   WS-29d touches `crm_*`"* — while `test_tenancy_boundary.py::test_a_new_table_must_carry_a_tenant_key`
>   refuses the table without one. A genuine pincer, and a doc blocker rather than a build one.
> - **CSV EXPORT — the recommended slice, and the ONLY one clearable by a single doc edit.**
>   It touches the live Zoho tenant not at all, needs no migration, collides with WS-26h not
>   at all, and has a line-for-line sibling reviewed 24 hours earlier
>   (`routes/projects/export.py`, WS-27ae). Real need: ~4,000 records whose only export route
>   today is the Zoho UI we are retiring. **The auditor wrote the done-when block that clears
>   it; adding that block to this section is the next ticket.**
>
> ⚠️ **Two traps recorded for whoever takes the export slice — both measured, neither
> guessable from this spec.** (1) `core.list_contract` clamps `page_size` to
> `MAX_PAGE_SIZE = 100`: reuse it for the WHERE clause ONLY and override the LIMIT, or the
> export silently returns the first 100 rows of a 1,516-row lead list — the exact silent
> truncation an export must never do, arriving through the door marked "reuse the shared
> builder". (2) The CRM BFF proxy (`src/app/api/crm/[...path]/route.ts`) does `res.json()`
> then `NextResponse.json(...)` unconditionally, so a `text/csv` body becomes `{}` with a
> 200; Projects hit this and fixed it.
>
> **Two further drifts found and corrected in this same pass:** the bullet below said
> "3.4k records total today" — the measured backfill is **3,993** (737 + 1,189 + 1,516 +
> 551) and that count is five days old; and the saved-views bullet cited a "QuickFilters
> precedent" for persistence — `QuickFilters.tsx` is a hardcoded 13-entry `CHIPS` array with
> **zero `localStorage`**, and there is no `localStorage` anywhere under `src/app/crm/`, so
> that precedent does not exist. Both are struck in the bullets below.
- **Duplicate merge** (contacts/orgs): the convert modal's match rules already FIND
  duplicates (`convert.ts`, §3.7 mirror); merge = pick survivor, re-point FKs (deals,
  activities, deal_contacts), and — the hard part — express the loser to Zoho under
  D-CRM-7 semantics (a merge is an update+delete pair with tombstone implications;
  spec against §7.1 before building).
- **Bulk actions** on the lists: multi-select → owner/status change, delete. Per-record
  endpoints vs a bulk endpoint is an audit-time call (~~3.4k~~ **3,993** records as
  measured at the backfill, 2026-08-06 — corrected 2026-08-11; it is the number this
  endpoint-shape decision rests on). **Now specced as `WS-26i-bulk` below (2026-08-11) —
  the endpoint shape is decided there; dispatch from that section, not from this bullet.**
- **CSV import/export**: export = current list filter, server-streamed; import = the Zoho
  importer's dedup discipline generalized (match-by-email first).
- **Saved views**: the URL grammar IS the view (`urlState.ts`); a saved view is a named
  URL. Per-user table vs localStorage is an audit-time call ~~(QuickFilters precedent)~~ —
  **struck 2026-08-11: there is no such precedent.** `QuickFilters.tsx` is a hardcoded
  13-entry `CHIPS` array that persists nothing, and `src/app/crm/` contains no
  `localStorage` at all. The DB direction is doc-blocked on the `crm_*` `organization_id`
  naming call — see the audit record above.

### WS-26i-bulk — Bulk edit on the CRM lists · 🟢 AGENT-SAFE (backend) · no migration
*(Supersedes the "bulk needs a `db`-taking patch seam first" clause in the 2026-08-11 audit record above. That clause was right that the seam is needed and wrong about why: Projects' bulk is deliberately NOT atomic, so what `records.patch_record`'s shape forbids is N records without N SESSIONS — the pool hazard `auto_lead.py:79` already records — not a single transaction. The `HTTPException(422)` "collision" recorded there is **not** a second precondition; see done-when 6.)*

> **Provenance:** contract **authored by the spec-auditor, 2026-08-11**, and pasted here
> verbatim apart from the two dated correction notes below (done-when 1 and done-when 4),
> which exist because the fence those criteria are keyed on was converted in the **same PR**
> that recorded this contract (WS-26h-fence). **It still wants a spec-auditor pass before
> dispatch** — authored-by-auditor is not audited-as-written, and the conversion changed what
> done-when 1 has to say. Anchor precision re-measured at `a06fa6a` while pasting: the
> `auto_lead` pool-hazard sentence spans **`auto_lead.py:78-79`** (the auditor cites `:79`,
> the second of its two lines), and the seam being split is `records.patch_record` at
> **`records.py:235`** with its session at **`:247`**.

**Scope.** One endpoint, `POST /crm/{leads,deals,contacts,organizations}/bulk`, plus the `db`-taking patch seam it is built on. **Non-goals, stated so they are not drifted into:** the CREATE path (`records.create_record`, `_resolve_status`, WS-26h2's gate) is untouched; `_require_entry_fields`' exception type is untouched; bulk DELETE is not in this ticket (it is the destructive verb and rides with duplicate-merge); the multi-select **UI is a separate ticket** — `src/app/crm/` has no selection affordance at all today, and Projects' `selection.ts` + `BulkBar.tsx` live under `src/app/projects/` and would need promotion, not copying.

**Gate label: AGENT-SAFE, and the reasoning is a property of the SITE.** The seam extraction reaches no Zoho path: `import_zoho`, `sync_zoho` and `broker_handlers` import `records` not at all, write through `core.upsert_by_zoho_id` / `core.update_row(touch=False)`, and sit on the unbound `_get_db` seam rather than `_tenant_session` (`core.py:53-57`). `mark_dirty_on_update` is inside `core.update_row` (`core.py:1041`), i.e. BELOW the seam, so this refactor cannot change when a row is marked dirty. §6 WS-26 (a) therefore does not bind.
⚠️ **Volume warning, not a gate:** each bulk request leaves up to `MAX_BULK` rows `zoho_dirty`, and `sync_zoho.PUSH_BATCH_LIMIT = 500` means a full 500-deal lane move fills one whole Deals push batch — 500 live upstream updates in one 600s cycle. D-CRM-9 already ruled that native writes push like human ones, so this is intended. Say it out loud in the endpoint's docstring.

**Done when:**
1. `records.patch_record`'s body is split: a `db`-taking seam `apply_record_patch(db, entity, record_id, values, *, actor_email) -> dict` holding everything from `require_row` to `update_row`, and `patch_record` reduced to `clean_payload` / `validate_source` / `async with _tenant_session()` / one call. ⚠️ **The seam stays IN `records.py`.** `tests/unit/test_crm_pipeline.py:1098` (`test_the_zoho_pull_never_enters_the_stage_gate`) asserts the files containing `apply_status_transition(` are exactly `["pipeline.py", "records.py"]`; a new module goes red. That test passes with **zero edits**.
   > ⚠️ **Correction, 2026-08-11 — the criterion survives, the anchor and the test name do
   > not.** WS-26h-fence converted that fence in the same PR that recorded this contract, so
   > as of `ce314e8`: **the call-file assertion now lives in
   > `test_the_move_gate_is_called_from_exactly_two_files` (`test_crm_pipeline.py:1273`)**,
   > keyed on `_MOVE_GATE = ("pipeline", "apply_status_transition")`, and
   > `test_the_zoho_pull_never_enters_the_stage_gate` (`:1301`) is now the **reachability**
   > half. `:1098` no longer names either.
   > **The constraint is unchanged in force and stronger in kind — measured, not assumed.**
   > Extracting `patch_record`'s call to `apply_status_transition` into a new
   > `routes/crm/bulk_seam.py` was simulated against a copy of the real package: the call-file
   > fence goes **RED** reporting `['bulk_seam.py', 'pipeline.py']` — note `records.py` drops
   > out of the set entirely, so the failure names the relocation rather than merely adding to
   > the list — while `test_the_zoho_pull_never_enters_the_stage_gate` stays **GREEN**, because
   > the new module is not reachable from the sync cycle (`sync_zoho._sync_loop` / `run_cycle` /
   > `_run_cycle_locked`, which covers the push half too) or from
   > `import_zoho.apply_module`.
   > So the "zero edits" clause splits: the **reachability** test passes unedited either way,
   > and it is the **call-file** test that must be left green by keeping the seam in
   > `records.py`. It is now an **AST call-graph** check rather than a text match, which means
   > it also catches an aliased call and no longer trips on a comment naming the function —
   > the seam may not be hidden behind `from …pipeline import apply_status_transition as _move`.
2. `patch_record`'s signature is unchanged — `(entity, record_id, payload, user)` — asserted by `inspect.signature`. **No existing test file is edited**; the ~42 existing `crm_records.patch_record(` call sites across `test_crm_pipeline.py`, `test_crm_routes.py` and `test_crm_zoho_sync.py:1799` pass as they stand.
3. Two calls to the seam inside ONE bound session commit once: bind `_crm_fakes.bind_db`, open `records._tenant_session()`, call the seam twice, assert `fake.committed == 1` and both rows updated. (`bind_db` at `_crm_fakes.py:1038-1069` yields the fake and commits on clean exit, so `committed` is the observable — this is the property the seam exists for and it is not observable without a second caller.)
4. **Structural fence, and what it proves stated precisely:** `_tenant_session_functions()` (the existing AST helper at `tests/unit/test_db_engine_seam.py:515`) over `records.py` reports `patch_record` in the set and `apply_record_patch` **not** in it. ⚠️ **This proves the seam's own function body opens no session. It does not prove no callee opens one** — that is a reachability claim and this is not the mechanism for it. If reachability is wanted, parameterise `_GATE` (currently a module-level constant at `test_crm_pipeline.py:1153`) into an argument of `_gate_reached_from` and reuse `_crm_call_graph`. **Do not mint a third mechanism.**
   > ⚠️ **Correction, 2026-08-11: the parameterisation this criterion asks for is ALREADY
   > DONE** — WS-26h-fence, same PR. `_GATE` no longer exists; there are two named targets
   > (`_MOVE_GATE` at `test_crm_pipeline.py:1139`, `_ENTRY_GATE` at `:1142`) and the gate is an
   > **argument**: `_gate_call_files(package, gate)` (`:1236`) and
   > `_gate_reached_from(package, entries, gate)` (`:1249`), both over the shared
   > `_crm_call_graph` (`:1204`). So if the reachability claim is wanted here, it costs a third
   > **constant** and one call — not a change to the helpers. **Do not mint a third mechanism
   > and do not fork the helpers to add a target**; that instruction is now load-bearing in the
   > other direction, because the seam is easy to copy. Everything else in this criterion
   > stands unchanged, including the limit it states: `_tenant_session_functions()` answers for
   > the seam's own body only.
5. `POST /crm/<entity>/bulk` takes `{ids: [...], patch: {...}}`, caps at `MAX_BULK = 500` (422 above it, naming the count — Projects' shape, `projects/bulk.py:62,318`), refuses a no-op patch with 422, and returns `{requested, applied, results, skipped, failed}`. The patch dict is validated by constructing the entity's existing pydantic model (`LeadIn`/`DealIn`/`ContactIn`/`OrganizationIn`) and passing it through `clean_payload` — `exclude_unset` (`core.py:1306-1313`) makes a caller-supplied dict behave exactly like a PATCH body, so bulk inherits the single-record validation rather than growing a second opinion.
6. **Per-record refusals are collected, everything else aborts the batch.** The loop catches `HTTPException` **narrowed to `status_code in (404, 422)`** and re-raises anything else. Every per-record refusal on this path is one of those two — `require_row` 404, `validate_source` 422, the lost-reason 422 (`pipeline.py:121-128`), the entry-fields 422 (`pipeline.py:288-294`). Tested both directions: a mixed batch reports `failed` per id and still applies the rest; an injected 403 propagates and applies nothing.
7. ⚠️ **Per-record continuation is safe only for PRE-WRITE refusals, and the ticket says so.** `_require_entry_fields` runs at `pipeline.py:129`, before the two INSERTs at `:138`/`:147`, so a refused record has issued no SQL. A genuine driver error (IntegrityError) poisons the transaction and MUST abort the batch — that is what done-when 6's re-raise arm buys.
8. A bulk of N records leaves **exactly N** rows `zoho_dirty = true` — no more, no fewer — asserted against the fake. This is the fence on the only production-visible consequence.
9. `tests/unit/test_crm_zoho_import.py` and `tests/unit/test_crm_zoho_sync.py` pass with **zero edits** — the evidence that nothing landed on the enabled 600s loop.

**Tests:** `tests/unit/test_crm_bulk.py` (new), run with the shared-fake block in §10.

⚠️ **Anchors re-measured at `a06fa6a` while pasting, since a stale one costs a repair round.**
Confirmed exactly as written: `core.py:53-57` (the `_get_db` leaves — `sync_zoho`, `auto_lead`,
`broker_handlers`), `core.py:1041` (`mark_dirty_on_update` inside `update_row`),
`sync_zoho.PUSH_BATCH_LIMIT = 500`, `projects/bulk.py:62` (`MAX_BULK = 500`) and `:318` (the
422), `pipeline.py:121-128` / `:129` / `:138` / `:147` / `:288-294`, `_crm_fakes.py:1038-1069`,
`test_db_engine_seam.py:515`, `test_crm_zoho_sync.py:1799`, and that none of `import_zoho.py` /
`sync_zoho.py` / `broker_handlers.py` imports `records`. **One count made exact — and corrected once, 2026-08-11
(repair round 1), because the first attempt mixed the two conventions in the very sentence
announcing it had removed the ambiguity.** ⚠️ **Count qualified call sites, not bare text.**
Measured: **41** `crm_records.patch_record(` call sites across `tests/unit/` — **32** in
`test_crm_pipeline.py` (not 34), 8 in `test_crm_routes.py`, 1 in `test_crm_zoho_sync.py:1799`.
The **34** first recorded here was `grep -c "patch_record("` over `test_crm_pipeline.py`, which
counts prose and fixture strings as well as calls; that number has since moved to **37** purely
because this ticket's own fence fixtures added more `patch_record(` text, which is the argument
for the qualified convention rather than a matter of taste. Done-when 2's "~42" is the total
(41) and the §10 block's "~33" is `test_crm_pipeline.py`'s share (32); both are the right
quantity approximated, and **32 / 41 are the numbers to check against.**

### WS-26i-export — The filtered-list CSV export · 🟢 BUILT + REPAIRED 2026-08-11 · no migration
*(Built on branch `claude/crm-command-center-tasks-i8l7n4`; as-built record in this file's
status header. Both traps hit and fenced. **The first cut did NOT meet done-when 5 end to
end and claimed it did** — see the repair note below. All seven met after repair round 1.
MERGED in PR #426 (`7255344`, on `origin/main`); deploy not independently verified.)*

⚠️ **Repair round 1, 2026-08-11 — four defects, one of them decisive.** An independent
verifier and an adversarial reviewer converged on the same byte-level finding, and it is
recorded here rather than quietly fixed because three of the four were fences that
*passed while the thing they fence was broken*:

1. **The UTF-8 BOM was destroyed in transit.** The gateway emits it
   (`gateway/csv_export.py`) and `test_crm_export.py` asserts it, but the BFF proxy did
   `await res.text()` — a UTF-8 *decode*, which strips a leading byte order mark — and
   rebuilt the response from the decoded string. Measured on node v22: upstream
   `EF BB BF 4E 61 6D`, relayed `4E 61 6D 65`. Excel on Windows then reads the 3,993-row
   Zoho backfill as the system code page and "Café" arrives as "CafÃ©", which is the exact
   failure the BOM exists to prevent. Both proxies now read `res.arrayBuffer()` and pass
   the bytes. **`api/projects/[...path]` carried the identical arm, so Projects' export
   has served BOM-less CSV since WS-27ae** — fixed here rather than left broken while the
   shared fence documents the broken shape as correct. That is a deliberate one-line reach
   outside this ticket, not scope creep. ⚠️ **Deploy state, stated exactly** (an earlier
   draft claimed "production" and a verifier rightly refused it): WS-27ae is on `main`
   (`1de846a` ancestor of `origin/main` via `ebf68f4`, PR #422) and the `deploy` workflow
   reported success on that SHA at 2026-08-10T23:53:53Z. Nobody has read back a ledger
   line or a deployed SHA, so it is **almost certainly live, not proven live** — the
   distinction CLAUDE.md non-negotiable 8 exists to keep. The defect is in merged code
   either way.
2. **The done-when-1 parity fence was vacuous.** It compared
   `route.dependant.query_params`, which is the SHALLOW set; both the list and the export
   take their filters through a class `Depends()`, so it was `set() - set()` on all four
   entities. Adding a `city` filter to `records.ListParams` — the exact drift it claims to
   catch — left all 46 cases green. It now recurses `dependant.dependencies`, asserts its
   own precondition (that the flattening actually found `ListParams`), and checks the
   reverse direction too. Measured red under that mutation on all four entities.
3. **The LIMIT could ship a partial file instead of refusing.** `_tenant_session()` opens
   one transaction but Postgres defaults to READ COMMITTED, so the `count(*)` and the row
   query take different snapshots: count 9,998 → no 422 → a concurrent auto-lead insert
   lands → `LIMIT :cap` at exactly `MAX_EXPORT_ROWS` returns exactly `MAX_EXPORT_ROWS` →
   a partial file with a 200. The row query now binds `cap + 1` and refuses on the
   rendered count, so the invariant no longer depends on the count and the render
   agreeing. The extra row is evidence for a refusal, never rendered.
4. **`X-Export-Rows` was a shipped no-op** — the BFF proxy forwarded two headers and this
   was not one of them, so the header's stated purpose ("for anything reading this
   programmatically") had no caller that could serve it. Forwarded rather than deleted:
   Projects' gateway sets it too and both its unit and live suites assert it, so deleting
   would have been the asymmetric choice.

`src/lib/export.test.ts` no longer greps source for the defect it was pinning
(`expect(source).toContain("await res.text()")`, commented "`res.text()` keeps the
bytes" — it does not). It now **runs** both proxies end to end over a BOM'd `text/csv`
body and compares bytes, checks a 422 from the same endpoint still arrives as readable
JSON, and checks the filename and row count survive.

*(Minted 2026-08-11 by the WS-26i audit, which is recorded above. This is the one of the
five WS-26i items clearable by a doc edit alone: it touches the live Zoho tenant **not at
all**, needs no migration, collides with WS-26h not at all, and has a line-for-line sibling
in `routes/projects/export.py` (WS-27ae). Real need: ~4,000 records whose only export route
today is the Zoho UI we are retiring. **The other four items stay NO-GO** — do not widen
this ticket into them.)*

`GET /crm/export/{entity}.csv` for the four entities, mirroring `routes/projects/export.py`.

**Done-when:**
1. The filters are the caller's, built by the **same** `core.list_contract` the list
   endpoint uses — `q`, `status_id`, `owner`, `source`, `include_converted`, `sort`, `dir`.
   A second filter parser is a defect.
2. `?status_id` on contacts/organizations → **422** (the existing refusal, not a silent
   ignore); unknown `sort` key → 422 naming the entity's allowlist; unknown `dir` → 422.
3. **Complete or refused.** A `count(*)` over the same WHERE runs before any row is
   rendered; over `MAX_EXPORT_ROWS` → 422 naming the real count and the cap. Never
   truncated.
4. Columns are the entity's declared vocabulary in declaration order, promoted out of
   `RecordList.tsx` into `src/app/crm/lib/columns.ts` so a Python fence can read the
   TypeScript — the mechanism `test_crm_stage_discipline_parity.py` already uses for
   `board.ts`.
5. RFC 4180 via `csv.writer(lineterminator="\r\n", quoting=QUOTE_MINIMAL)` + a UTF-8 BOM;
   server-owned `Content-Disposition` filename built from the entity slug only, never from
   free text.
6. The BFF proxy passes the upstream `Content-Type` and `Content-Disposition` through
   instead of stamping `application/json`; a 422 from the same endpoint still arrives as
   JSON.
7. Read-only: no `INSERT`, no `UPDATE`, no `zoho_dirty`, no tombstone. **Asserted, not
   assumed** — the sync loop is running and any dirtied row is pushed to the live tenant
   within one 600s cycle.

**Tests:** new `tests/unit/test_crm_export.py`; vitest for the client half.

⚠️ **Two traps, both measured 2026-08-11, neither guessable from this spec.**
**(1)** `core.list_contract` clamps `page_size` to `MAX_PAGE_SIZE = 100`. Reuse it for the
WHERE clause ONLY and override the LIMIT — reusing it verbatim silently exports the first
100 rows of a 1,516-row lead list, which is the exact silent truncation done-when 3 forbids,
arriving through the door marked "reuse the shared builder".
**(2)** The CRM BFF proxy (`src/app/api/crm/[...path]/route.ts`) does `res.json()` then
`NextResponse.json(...)` unconditionally, so a `text/csv` body becomes `{}` with a 200.
Projects hit this and fixed it — copy their arm, comment and all.

### WS-26e — Cutover + retirement · 🔴 OWNER-GATE end-to-end
Final import + parity report; §6 consumers repointed; §7.4 inventory retired; Zoho refresh
token revoked (executes part of WS-2); spec status header + board row updated in the same
PR (R4).

---

## 10. Verification

    # WS-26a + WS-26b + WS-26c — all six CRM files plus the access pair:
    uv run pytest tests/unit/test_crm_zoho_import.py tests/unit/test_crm_zoho_sync.py \
                  tests/unit/test_crm_routes.py tests/unit/test_crm_pipeline.py \
                  tests/unit/test_crm_convert.py tests/unit/test_crm_migration.py \
                  tests/unit/test_org_access_control.py \
                  tests/unit/test_org_access_enforcement.py -q

    # WS-26h + WS-26h2 + WS-26h-fence — the two stage gates and where each may be
    # called from. Four fences over ONE AST mechanism (call sites + reachability,
    # per gate), the 25-case synthetic self-test that keeps them honest, and the
    # fence on THIS number (it reads the count back out of this file):
    uv run pytest tests/unit/test_crm_pipeline.py -q \
                  -k "move_gate or entry_gate or zoho_pull_never or siting_fences \
                      or spec_quotes"

    # WS-26i-bulk — the db-taking patch seam and the bulk endpoint.
    # ⚠️ Every CRM route test shares tests/unit/_crm_fakes.py, so the new file is
    # NEVER run alone: a drifted fake shows up as a SIBLING failing.
    # ⚠️ test_crm_pipeline.py is in this list twice over: it carries the ~33
    # patch_record call sites that must pass UNEDITED (done-when 2) and the
    # apply_status_transition siting fence that pins the seam to records.py
    # (done-when 1). The two Zoho files are done-when 9's evidence.
    # ⚠️ Correction 2026-08-11 (WS-26h-fence): that siting fence is now
    # test_the_move_gate_is_called_from_exactly_two_files, an AST call-graph
    # check — test_the_zoho_pull_never_enters_the_stage_gate beside it is the
    # reachability half and stays green even if the seam moves. Measured: the
    # patch_record body relocated to a new module reports
    # ['bulk_seam.py', 'pipeline.py']. Measured call sites: 34 in this file,
    # 41 across tests/unit/.
    uv run pytest tests/unit/test_crm_bulk.py tests/unit/test_crm_pipeline.py \
                  tests/unit/test_crm_routes.py tests/unit/test_crm_convert.py \
                  tests/unit/test_crm_migration.py tests/unit/test_crm_export.py \
                  tests/unit/test_crm_stage_metadata.py \
                  tests/unit/test_crm_stage_discipline_parity.py \
                  tests/unit/test_crm_reports.py tests/unit/test_crm_email_timeline.py \
                  tests/unit/test_crm_zoho_import.py tests/unit/test_crm_zoho_sync.py \
                  tests/unit/test_db_engine_seam.py -q
    # No UI in this ticket — no tsc/vitest line. The multi-select surface is
    # WS-26i-bulk-ui and carries its own.

    # WS-26i-export — the CSV export. ⚠️ Every CRM route test shares
    # tests/unit/_crm_fakes.py, so the new file is NEVER run alone: a fake that
    # drifts shows up as another suite failing, not as this one passing.
    uv run pytest tests/unit/test_crm_export.py tests/unit/test_crm_routes.py \
                  tests/unit/test_crm_pipeline.py tests/unit/test_crm_convert.py \
                  tests/unit/test_crm_migration.py tests/unit/test_crm_reports.py \
                  tests/unit/test_crm_email_timeline.py \
                  tests/unit/test_crm_stage_metadata.py -q
    cd workbench/control_plane && npx tsc --noEmit && npx vitest run

    # WS-26h + WS-26h2 — stage discipline: the MOVE gate and the CREATE gate.
    # WS-26h shipped with a Tests: line but was never given a §10 block; this
    # covers both halves.
    # ⚠️ Every CRM route test shares tests/unit/_crm_fakes.py, so no file here
    # is ever run alone: a drifted fake shows up as a SIBLING failing, not as
    # this one passing.
    # ⚠️ The two Zoho files are in this list for a reason — they are the
    # evidence that the create gate did NOT land on the enabled production sync
    # loop, and they must pass with ZERO edits (WS-26h2 done-when 9).
    uv run pytest tests/unit/test_crm_pipeline.py tests/unit/test_crm_routes.py \
                  tests/unit/test_crm_convert.py tests/unit/test_crm_migration.py \
                  tests/unit/test_crm_stage_metadata.py \
                  tests/unit/test_crm_stage_discipline_parity.py \
                  tests/unit/test_crm_zoho_import.py tests/unit/test_crm_zoho_sync.py \
                  tests/unit/test_crm_reports.py tests/unit/test_crm_email_timeline.py \
                  tests/unit/test_crm_export.py -q
    # WS-26h's UI half only (rot badges, move plan) — WS-26h2 renders nothing:
    cd workbench/control_plane && npx tsc --noEmit && npx vitest run

    # WS-26d read half — the agent, the parse-only WhatsApp constant, AND the
    # four registration fences the slice leans on. Run all seven together: a
    # new agent's failure mode is not a broken tool, it is silently never
    # loading, and only the fences can see that. test_crm_agent.py alone goes
    # green on an agent that no run path can reach.
    uv run pytest tests/unit/test_crm_agent.py \
                  tests/unit/test_agent_gateway_identity.py \
                  tests/unit/test_whatsapp_context.py \
                  tests/unit/test_agent_manifest.py \
                  tests/unit/test_orchestrator_registration.py \
                  tests/unit/test_resolve_agent_for_run.py \
                  tests/unit/test_default_deny_auth.py -q

    # WS-26d remaining slices — each names its own file (B7, closed 2026-08-06).
    # Written BEFORE the code, so a slice cannot land with its verify line
    # invented after the fact:
    #   WS-26d-email    tests/unit/test_crm_email_timeline.py
    #   WS-26d-autolead tests/unit/test_crm_auto_lead.py
    #   WS-26d-write    tests/unit/test_crm_agent_write.py
    # Each runs WITH the six-file WS-26a-c block above, never alone: the email
    # join changes `_timeline`'s signature and its body, and every CRM route
    # test shares `_crm_fakes.py` with it.
    #
    # ⚠️ An earlier version of this line claimed test_crm_routes.py "already
    # pins `_timeline`". It did not — it exercises _log_activity,
    # patch_activity and delete_activity only, and `_timeline` had ZERO direct
    # coverage until test_crm_email_timeline.py. That file therefore also
    # carries the regression floor for the behaviour the join did not add:
    # activity + status-change entries, a deal inheriting its lead's history
    # labelled `lead`, newest-first order, limit truncation, and the 404.

    # WS-26f + WS-26g — the settings/stage-metadata slice and the reports slice.
    # Written BEFORE WS-26g's code, per the discipline above. Both bind
    # _crm_fakes.py with every other CRM route test, so they run together:
    uv run pytest tests/unit/test_crm_reports.py tests/unit/test_crm_stage_metadata.py \
                  tests/unit/test_crm_email_timeline.py tests/unit/test_crm_routes.py \
                  tests/unit/test_crm_pipeline.py tests/unit/test_crm_convert.py \
                  tests/unit/test_crm_migration.py -q

    cd workbench/control_plane && npx tsc --noEmit && npm test

⚠️ The WS-26d block takes **~5 minutes**, nearly all of it in `test_agent_manifest.py`
(it parametrises over every first-party `config.json` and imports the real
orchestrator resolver). It is not hung — do not interrupt it and do not drop the
slow file to make the command feel faster. What each fence catches, since a
green run tells you nothing about why they are listed: `test_agent_manifest.py`
— the new `config.json` must resolve the same tool surface the executor
injects, and its `sharing.instancing` must stay `shared` or the
`PENDING_INSTANCE_MIGRATION` canary fires (declaring `personal` would
re-partition memory with no migration behind it); `test_orchestrator_registration.py`
— the `_AGENT_REGISTRY` shape that makes an agent delegatable at all;
`test_resolve_agent_for_run.py` — that a stored session name still resolves;
`test_default_deny_auth.py` — that no route escaped the app-wide auth guard.

**Owner-verified — migrations 144 and 145 are applied on prod as of 2026-08-06** (none of
this can be checked from the repo, and WS-26c deliberately did not reach for a database):
the board renders real lanes in `position` order with their ₹ totals; dragging a card
persists and the card stays put on reload; `?deal=<id>` opens the sheet on a real record and
Back closes it; the convert modal's matches are the ones the server picks; a lost move is
refused without a reason and accepted with one.

⚠️ Never `uv run pytest tests/unit/` bare — whole-directory collection hangs on the Windows
box against the live DB. Name the files. The pr-check gates that bind: ruff
`--select F821,F601,F602,F502,F7,B006` (blocking), xenon max-absolute F (blocking),
frontend tsc + vitest (blocking).

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-26 — **CRM app — native CRM + Zoho retirement** *(minted 2026-08-05)*
**State cell (as of the move):** ✅ **a + b + c BUILT + DEPLOYED** · ✅ **d read half BUILT + DEPLOYED** · ✅ **D2 = d-email BUILT 2026-08-07** (branch `ws-26d-email-timeline`, merged) · ✅ **D4 = d-write MERGED + DEPLOYED 2026-08-08 (PR #400, no migration; deploy 31217978773 log-verified)** · 🟢 **d-autolead dispatchable** · ✅ **D1 = f BUILT 2026-08-07 (branch `ws-26f-pipeline-truth`, NOT run against prod)** · ✅ **D3 = g BUILT 2026-08-07 (branch `ws-26g-reports`, no migration)** · 🟢 **DEMO CRITICAL PATH (owner-directed 2026-08-07, spec §9.0): ~~D1 f~~ (∥ D2 d-email) → ~~D3 g~~ → ~~D4 d-write~~ → D5 d-autolead** · 🟡 **h/i/e deferred past the demo; i spec-thin**
**Narrative (verbatim):** Research pass 2026-08-05: `frappe/crm` (AGPL — **concepts only, no code**), `trycompai/crm` (MIT), full-tree Zoho sweep. **Zoho today is a read-only nightly mirror** into the Phase-0 graph tables (`person`/`customer`/`deal`) with no UI, no write path, and **no Leads pull** — so leaving Zoho is import-and-retire, not a live cutover. Spine: Frappe's lead→convert→deal+contact+organization with **statuses-as-data** (color/position/type/probability); trycompai's single activity-spine table + `source` provenance + `last_activity_at` discipline. **BO-10 contribution: WS-26a adds the shared engine seam (`gateway/db.py::get_engine()`, tasks converted as proof) instead of engine 13.** Tickets: **a** schema + feature registration + core API — **BUILT 2026-08-05** (mig `144_crm.sql`, `feature:crm`, `gateway/db.py` seam + tasks converted, `routes/crm/`; **migration 144 applied on prod and `/crm` live as of 2026-08-06**) · **b** **Zoho two-way sync — BUILT 2026-08-05** (branch `ws-26b-zoho-sync`: `list_leads` + `list_deleted` on the read client, the single write client `ingestion/sources/zoho/writer.py` with one grep-asserted caller, mig `145_crm_zoho_sync.sql` (dirty columns + `crm_zoho_tombstones` + `crm_sync_cursors`), `routes/crm/{import_zoho,sync_zoho,broker_handlers}.py`, `crm.zoho_*` broker handlers registered from `main.py`, 80 new hermetic tests). *(Re-scoped 2026-08-05, owner-directed D-CRM-7: "faithful two way sync until we do away with Zoho entirely" — coexistence is bidirectional, not import-once.)* **Measured 2026-08-06: mig 145 is applied on prod and the BACKFILL HAS RUN — 737 orgs / 1,189 contacts / 1,516 leads / 551 deals / 1,909 notes, zero dirty rows, zero unmatched owners; the §7.1 pre-flip curl confirmed the tenant honors RFC-1123 `If-Modified-Since` (304). The PUSH direction has still never run: `CRM_ZOHO_SYNC` ships OFF, nothing has ever written the live Zoho tenant, and enabling the flag or hand-running a push cycle against prod stays OWNER-GATE §6.** WS-1's "no Zoho write path anywhere" clause was corrected in the same change (done-when 6) · **c** UI + the API addendum — **BUILT 2026-08-05** on branch `ws-26c-crm-ui` atop 26a and **merged with b into `ws-26-crm-app` 2026-08-06** (`/crm` app + BFF proxy; the three frontend registration points with `CenterApp` re-typed so `live ⇒ href` is a compile error; `routes/crm/deal_contacts.py` with one-primary-per-deal enforced on the shared `core.link_deal_contact` seam the convert path now also uses — 26b's importer is the one excepted writer and computes `is_primary` in-statement so a backfill can never demote a hand-set primary; `organization_name` on the deal list + board via a derived-table LEFT JOIN; the three review residuals — `?status_id` on a pipeline-less entity → 422, explicit `null` on a defaulted NOT NULL column → 422 not a driver 500, and a hand-edited `lead_name` surviving a name-field PATCH. **Deployed:** migrations 144 and 145 are applied on prod as of 2026-08-06 and `/crm` is live, so live rendering, drag persistence and deep links are owner-verifiable now) · **d** integrations — **audited 2026-08-06 GO-NARROWED and the narrowed slice is BUILT** (branch `ws-26d-agent-crm`): `apps/agents/agent-crm/` (`crm-assistant`, MAF, four READ tools over the existing `/crm` routes carrying the caller's `X-User-Email`, read-only enforced at the transport by a GET-only method allowlist) registered in `_KNOWN_AGENTS` + `_AGENT_REGISTRY` + `agent_registry.json`, plus `"crm"` added to the WhatsApp `_KNOWN_SYSTEMS` allowlist **parse-only** (nothing writes `wa_contacts.entity_ref`, the `crm` context block stays `None`, both pinned by test). **The three held-back items are now DISPATCHABLE — their doc blockers (B3/B4/B5/B7) were closed 2026-08-06 in `crm_app.md` §9.1-§9.3, every anchor read off `origin/main` rather than recalled:** **WS-26d-email** (the timeline join is CALLER-scoped, never record-scoped — it reuses the email app's `_account_scope` predicate, copied into `routes/crm/` rather than imported per D-CRM-4, joins by thread not message, inbound `from_address` only, and needs a new address index at the next free migration number) · **WS-26d-autolead** (hook = `routes/email/scheduler_hooks.py::process_new_mail`, the one seam scheduler+manual+webhook all funnel through; the per-message rules loop was considered and REJECTED because a classifier outage there double-fires and history backfills never reach it; unknown-sender test mirrors `_maybe_block_cold`, colleague suppression via `is_own_mail`) · **WS-26d-write — BUILT 2026-08-08** (branch `ws-26d-write`, **no migration**: every route the four tools call already existed). `request_confirmation` awaited at the top of each tool before any mutating request is built, fail-closed, and the `non_interactive_default` keyword is asserted ABSENT from the whole module rather than asserted != "approve" — pinning the argument rather than the value means a mutant does not get to pick a spelling the fence has not heard of. `_ALLOWED_METHODS` **widened, never deleted**: `{GET, POST, PATCH}`, still checked inside `_request`, with `DELETE`/`PUT` and any `_delete`/`_put` helper still absent, so the check that used to enforce "read-only" now enforces "never destroys". Path fence extended past `ast.JoinedStr` to `.format`/`%`/`+` (the re-review's P2) and — the part that makes it maintainable — **tested against synthetic sources one per idiom**, so "the fence went blind" is a red test rather than a silent gap. Two supervisor rulings landed as built: `update_deal_status` resolves the stage BY NAME inside the tool against `GET /crm/statuses/deal` (no UUID on the LLM surface; an unknown name returns the real lane names), and a lost-type target requires a `lost_reason` resolved the same way against `GET /crm/lost-reasons` — pre-empting the 422 the "close this as lost" demo beat would otherwise hit — with the vocabulary **only ever read, never created**. `create_lead` takes **no `owner_email` argument at all** (the route derives it from the acting user), deleting an LLM-filled identity field from the surface entirely. ⚠️ **One recorded departure from done-when 1**: the invariant asserted is *no mutation before consent*, not *no HTTP before consent* — two tools must read to describe honestly what they are about to do, and every pre-card call being a GET is itself pinned; the two tools that owe nothing to a pre-read are still held to literally zero calls. `@_annotate_risk` is the shared annotation convention and is NOT enforcement; the Action Broker covers the Zoho push, not the native write — the two fail in OPPOSITE directions and are not interchangeable. 76 new hermetic cases + `test_crm_agent.py` 87 → 143; ten mutants run red and reverted. **Built, not deployed.** The push-queue question is CLOSED — **D-CRM-9 (owner, 2026-08-06): agent-originated writes queue for Zoho exactly like human ones** (🟡 remainder; the flip stays OWNER-GATE) · **e** cutover + retirement inventory + **Zoho refresh-token revoke, which executes part of WS-2's standing P0** (🔴 OWNER-GATE end-to-end). Data-visibility departure recorded: org-visible to `feature:crm` holders in v1, `owner_email` is assignment not ACL (D-CRM-3; workflows v1 is the precedent) — revisit at WS-14 `group:` grants / colleague #1. **Pipeline blueprint added 2026-08-07 (spec §5.1) after the owner's first live board session found lanes out of order and imported stages at 0% probability — root cause: the importer appends unseen Zoho stages past the seeds at probability 0, and 144's seeds renamed Zoho's defaults so name-match missed them; the admin API that could fix it is headless.** New tickets: **f** pipeline truth + settings UI — **BUILT 2026-08-07** (branch `ws-26f-pipeline-truth`, **no migration**: 144 already carried `position`, `probability`, `type`, `closed_at` and `expected_close_date`). `routes/crm/stage_metadata.py` = `POST /crm/import/zoho/stages`, floor `admin:access:manage`, **dry-run by default** and `?apply=true` to write; **>1 pipeline STOPS before the DB is opened** (D-CRM-11); f4's `closed_at` proxy is a direct UPDATE that bypasses `mark_dirty_on_update`, asserted statically against the statement text AND the module's call graph because the shared fake writes only what a SET clause names. Two settings readers on the Zoho read client with `ZohoScopeError`/`ZohoApiVersionError` so **no-scope, no-data and no-such-endpoint are three different reported outcomes** — and no-scope is the *expected* first answer, since the tenant's refresh token was never minted with `ZohoCRM.settings.*`. D-CRM-10's clamp landed in `admin.py::_validate_status` reading the ROW rather than the payload (a PATCH naming only `type`, or only `probability`, contradicts the rule only in combination with what is stored). `?tab=settings` is the headless-API fix and needs no Zoho token at all. **Nothing has been run against the tenant — dry run included.** · **g** forecast & funnel reports off `crm_status_changes` — **BUILT 2026-08-07** (branch `ws-26g-reports`, **no migration**: 144's `crm_status_changes` already carried every column). `routes/crm/reports.py` = four read-only endpoints (`/crm/reports/{pipeline,funnel,win-loss,owners}`) on the shared gated router, plus `?tab=reports`. **`WEIGHTED_SQL` lifted into `core.py`** (pipeline re-exports it) so the forecast formula has ONE definition, and `core.status_wire` absorbed two duplicate status projections instead of gaining a third. **The cross-language parity mechanism is minted here, not inherited** — the `priority.ts ⟷ priority.py` "precedent" is two hand-kept mirrors joined by a comment with no shared fixture anywhere: `tests/fixtures/crm_weighted_parity.json` (new directory) is read by BOTH pytest (through the emitted SQL, whose expression `_crm_fakes._WEIGHTED_SUM_RE` parses out of the statement text) and vitest (through `board.ts::weightedDeal`/`weightedRows`). ⚠️ **The funnel is defined against what the log RECORDS, not what its name suggests**: `crm_status_changes` logs transitions only, so all 551 imported deals have zero rows and "entered" is a VISITED-SET union (`from_status`, `to_status`, and the deal's current stage) — a `to_status` count reports an empty funnel for the whole live board; dwell is grouped by **`from_status`** (the stage being LEFT, the opposite key from the naive reading); the log stores NAMES, so a renamed lane orphans its history and orphans are REPORTED in `unmatched`, never dropped; and NULL `closed_at` — every imported closed deal until f4's owner-gated backfill runs — falls outside the trailing window, with the count reported so a 0% win rate is explicable rather than mysterious. The lost-reason breakdown carries a NAMED unattributed bucket: the earlier "complete by construction" claim is FALSE, since the importer bypasses both gates and `lost_reason_id` is `ON DELETE SET NULL`. **No `GROUP BY` is emitted** — the ticket asked for the choice to be stated: per-key aggregates in `get_pipeline`'s shape, because the weighted expression binds the lane's own default as `:stage_probability` and a grouped statement would stop BEING the expression the fixture and the fake read. 47 hermetic cases + 20 vitest + the 14-row shared fixture on both sides, 5 mutants red. **Two findings:** the `entity_type='deal'` mutant initially SURVIVED (the funnel keys through deal ids, so a lead row is excluded twice over) — closed with a row stamped `lead` against a DEAL's id, which is realistic precisely because `entity_id` has **no FK**; and `_crm_fakes`' `lower(col) = :param` reader matched a NULL column, which SQL never does, making the unassigned-owner bucket count rows its own aggregate never summed · **h** stage entry-requirements + rot badges (after f2) · **i** merge/bulk/CSV/saved-views (🟡 spec-thin, audit-narrow first). **Demo path (2026-08-07, spec §9.0): full chain + all gates intact, tickets re-sequenced not thinned; f gained f4 — imported won/lost deals have no `closed_at` (importer never stamps it), backfilled from Zoho `Closing_Date` as a labeled proxy via a direct UPDATE that MUST bypass dirty-marking or ~500 no-op pushes queue for the live tenant.**

**Corrections applied 2026-08-09:**
- f and g are MERGED TO MAIN (#391, #397) — the 'on branch' wording was stale
- the body's 'Built, not deployed' sentence about d-write contradicted the row's own state cell — d-write is MERGED + DEPLOYED, log-verified via deploy 31217978773 (2026-08-08)
- every 'CRM_ZOHO_SYNC ships OFF / never run' sentence is struck — the sync loop was ENABLED BY THE OWNER 2026-08-06 (work_plan.md §6 WS-26 (a))
- d-autolead is BUILT with PR #403 OPEN (merge + CRM_AUTO_LEAD flip are the owner's)
- d-email took two post-merge fixes not reflected here (0aa30dec timeline share, acc80d2d migration renumber)
- the autolead seam finding of b09093a8 (backfills also reach process_new_mail's seam) applies.
