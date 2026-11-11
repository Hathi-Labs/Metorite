# Metorite plan: contradictions audit

**Result: the plan has 30 contradictions. Nine are HIGH.** The three worst:

1. Pricing has two live owner directives.
2. The first outside customer has four dates.
3. The written critical path is not the work that the team builds.

Audit date: 2026-11-07. Read-only. No repository file changed.

**Sources.** `origin/main` at `f2671c706` (2026-11-06 23:49 +0530). Line numbers for
`work_plan.md`, `INDEX.md`, `HANDOFF.md` and the specs come from that commit. "LP" is the
owner's local launch plan, `Metorite-launch-plan/` (a folder on the owner's PC, outside this repo)
(third version, 2026-10-30). "Board" is the Metorite MCP, read on 2026-11-07. "Memory" is the
auto-memory folder of this project.

**Format of each item.** Severity · A (file:line) · B (file:line) · why they conflict ·
suggested resolution · who decides.

---

## HIGH

### 1. Pricing: flat ₹500 against the D152 seat tiers
- **A.** D49, `work_plan.md:1181-1188`: "₹500 per user per month ... plus AI credits ... supersedes D23 and D24". Repeated in `CLAUDE.md` §2 ("Pricing is FLAT"), `specs/launch_surface.md` §4 and `specs/ai_studio.md:521` (STU-Q7 "Inside the ₹500 seat"). Owner in chat on 2026-11-06 (memory `orchestration-playbook-direction.md`): "it's all included in the flat, same price".
- **B.** D152, owner directive of 2026-11-01. It is only on the unmerged branch `origin/mt-e2003d32-pln-0-seat-plans`, commit `383809b23`, `work_plan.md:5016` there: "Standard, Pro and Max, at $20, $100 and $200 a month ... the plans sell worldwide from day 1". Board: epic 1273 (WS-PLN), task 1409 "PLN-50 · ... D152 replaces flat ₹500 (OWNER-GATE)". Task 1274 (PLN-0) has been In progress since 2026-11-02.
- **Why.** Two owner directives set the price, one in rupees for India and one in dollars for the world. The repo says one thing and the board says the other. LP `03-cost-model.md:120` models revenue at ₹500. D123 (Razorpay, GST) and the DPDP work assume India.
- **Resolution.** The owner names the live model. Then merge PLN-0 with banners on D49 and `launch_surface.md` §4, or close the branch and epic 1273.
- **Who decides.** Owner.

### 2. The first outside customer has four dates
- **A.** §2.0, `work_plan.md:124`: "M2 · 31 March 2027 · The first paid customer". D119.1, `work_plan.md:4615`: "M2 is the first paid customer, 31 March 2027".
- **B.** LP `README.md:3,17`: "Beta lands around 25 November ... First customers: 5 paying customers in December". LP `04-calendar-and-risks.md:71-73`: "Beta done on 25 November ... First 5 paying customers go live" from 1 to 23 December. `work_plan.md:128`: "Ten six-week pilots must complete in December, so they start by mid-November". D179, `work_plan.md:5423`: "Studio opens to every beta org".
- **Why.** Customer zero (M0, about 17 Nov), pilots (start mid-Nov), beta (25 Nov), paying customers (December) and the first paid customer (31 Mar 2027) are five names with four dates. WS-CM, the money path, is "🟡 gated · M2" (`work_plan.md:158`). December paying customers need H-14, SC-5 and the rate card (D123), and LP `04:28` confirms that "No one can pay".
- **Resolution.** Record one decision that names the outside-user stages and their dates, and that amends D119 or retires the LP target. Re-tag WS-CM.
- **Who decides.** Owner.

### 3. Pilots in mid-November against isolation first
- **A.** `work_plan.md:128`: the ten pilots "start by mid-November".
- **B.** D119.5, `work_plan.md:4619`: "Tenant isolation (M1) lands before the first pilot outside customer zero". The isolation spine is §2.0 item 5, `work_plan.md:136` (H2, then H3.2, then H3).
- **Why.** H2, H3.2 and H3 have 0 commits since 2026-10-28. Epic 520 (Tenancy and Security) is Backlog on the board. MT-1j-exec (task 572, "a real test org 2") is Backlog, due 2026-12-15. So a mid-November pilot breaks D119.5, and a D119.5-safe pilot cannot finish in December.
- **Resolution.** Move the pilot start, or name a smaller isolation gate that the owner accepts for the pilots.
- **Who decides.** Owner.

### 4. The written critical path is not the work that the team builds
- **A.** §2.0, `work_plan.md:130-146`: items 1 to 15 are "the critical path". Item 1 is customer zero (M0, P0), item 3 is issue reporting, item 5 is the isolation spine, items 7 and 8 are branch protection and secrets, and item 13 is pay.
- **B.** The owner's lane order of 2026-11-06 (board task 1642, memory `lane-plan-2026-11.md`): "L7 (AI Studio and the package host) comes first, because per-company customization is the goal". The repo does not record this order.
- **Why.** Of 356 commits on main since 2026-10-28, 37 (10%) touch a critical-path ID. Only 13 (4%) touch the M0, isolation, secrets, branch-protection or money items, and most of those are WS-29 tenant-binding fixes. Studio, package host and web research made 63 of the 203 commits since 2026-11-01 (31%). On the board, the M0 tasks due 2026-11-17 are all Backlog: IR-1 (556), IR-2 (557), CZ-DEWIN-1 (547), M0.4 (553) and E-05 (597). Only H-259 (550) is In progress. Epic 491 (Studio) is the only epic that is In progress.
- **Resolution.** Rewrite §2.0 so that it states the owner's real order and the new milestone dates, or move the lanes back to the critical path.
- **Who decides.** Owner.

### 5. Studio: "after Phase 2", "dark in M1" and "live for all" at the same time
- **A.** D119.3, `work_plan.md:4617`: "Moved after Phase 2. Studio, Writing Help, the UX rules, MCP data parity, Notes, Workflows and the Dashboard". D127.1, `work_plan.md:4683`: "It is scheduled after Phase 2". §2.2 WS-11, `work_plan.md:205`: Workflows "⏸ parked · after Phase 2 (D119.3) · now in WS-STU".
- **B.** D141.1, `work_plan.md:4887`: "Studio starts now ... leaves M4". D141 also says "It extends D127 and changes none of its rules", but it does change D127.1. D146.5, `work_plan.md:4964`: "Stage 1 dark in M1, Stage 2 to the pilots in M2". D179, `work_plan.md:5423`: Studio is live in nav for every org. Memory `ai-studio-ws-stu-state.md`: `STUDIO_ORGS=*` has been live in production since 2026-11-06 17:23 UTC. `specs/ai_studio.md:4`: "Every built slice is dark behind STUDIO_ORGS, which is empty on every box". §2.1 WS-STU, `work_plan.md:170`: "Stage 1 in M1, Stage 2 in M2".
- **Why.** Five records give five schedules. D119.3 and D127.1 carry no amendment banner, and D146.5 contradicts D179 without naming it.
- **Resolution.** Add banners on D119.3, D127.1 and D146.5 that cite D141 and D179. Fix the `ai_studio.md` header and the WS-STU and WS-11 rows. The owner answers STU-Q6, the milestone of each stage.
- **Who decides.** An agent fixes the text. The owner confirms the milestones.

### 6. "Customer zero" already includes a second real company
- **A.** D119.1, `work_plan.md:4615`, and WS-CZ, `work_plan.md:156`: customer zero is "Fracktal and Shaurrya Tele (DEWiN)". DEWiN is its own organization (`f01d029f`, slug `dewinai`), on production with Zoho data since 2026-10-15 (memory `crm-custom-module-overlay.md`).
- **B.** WS-TS, `work_plan.md:157`: "a second organization is safe" is an M1 goal. §2.0 item 12, `work_plan.md:143`: "A real second organization: MT-1j-exec provisions it". D36: "Fracktal Works is customer zero". §6.0 D1, `work_plan.md:5621`: H3 is "the gate for customer #2".
- **Why.** The plan treats a second organization as a future event behind H3. But a second customer org already holds data on production, and its definition as "customer zero" moves it outside the D119.5 gate. DEWiN's mirror has also been paused since 2026-10-15, so the M0 claim "uses Metorite every day" is not true for it.
- **Resolution.** State whether DEWiN is inside or outside the D119.5 isolation gate. Record the residual risk on WS-TS.
- **Who decides.** Owner.

### 7. Specs that the critical path needs do not exist, and one has a date this week
- **A.** D119.4, `work_plan.md:4618`: "WS-37 compliance spec is written in Phase 1 ... DPDP has a November 2026 date". §2.0 item 3, `work_plan.md:134`: IR-0 to IR-3 come "before the pilots".
- **B.** `INDEX.md:124`: WS-37 has "No spec". LP `04-calendar-and-risks.md:19`: "DPDP Rules: Consent managers on 2026-11-13". WS-IR, `work_plan.md:185`: owning spec "none yet", but tagged M0. `INDEX.md:119-126` lists only WS-36 and WS-37 as rows with no spec. §2.1 also has WS-CZ (`:156`), WS-WH (`:180`), WS-DOC (`:183`) and WS-IR (`:185`) at "none yet".
- **Why.** §1's contract (`work_plan.md:29-45`) forbids a dispatch with no spec. So IR, which is M0 and due 2026-11-17 on the board, cannot be built by the rules. The DPDP date falls six days from today.
- **Resolution.** Write the WS-IR spec and the WS-37 spec now. Add the four rows to the INDEX gap table.
- **Who decides.** An agent writes them. The owner sets their priority over the L7 lane.

### 8. D118 says "state lives in Metorite", but R4 orders state into the repo
- **A.** D118, `work_plan.md:8` and `:4601-4609`: "Metorite holds the status, the priority, the owner and the order". D118.4 (`:4608`) and D135.1 (`:4783`) retire `HANDOFF.md` into tasks.
- **B.** R4, `work_plan.md:56-57`: "A PR that ships spec'd work updates the owning spec's status header in the same PR". §1 item 1 (`:35`) requires a dated status header.
- **Evidence of state in the repo.** 16 of 31 §2.1 rows carry dated build state, and 30 of 31 carry a "next" item, which is order. WS-UX (`:179`) holds 871 characters of GL build history. WS-STU (`:170`) says "except A1 and E1 (this PR)". WS-CHAT (`:181`) says "a parallel session owns it". 56 of the 99 ACTIVE rows in INDEX carry BUILT or MERGED text. 71 of the 109 specs carry build state in their first 40 lines. `HANDOFF.md` still holds 239 entries (73 OWNER, 166 AGENT), and H-440 to H-442 came after D135. `hardening_backlog.md` holds about 60 work rows, and INDEX does not list it. The board fails the other way. The epics of ten 🟢 active rows (519, 520, 521, 479, 475, 783, 1234, 1368, 669, 522) say Backlog. So neither authority shows which work is active.
- **Resolution.** Amend R4 and §1 item 1 to read "update the Metorite task, and the header names the task". Strip the §2.1 "Gates · next" cells to the gate and the epic. Move the active state to the epic status. Close the HANDOFF entries into tasks.
- **Who decides.** The owner approves the R4 change. An agent does the rest.

### 9. Staging is retired, but the binding docs still send agents there
- **A.** D144, `work_plan.md:4920`: "Staging is retired". D145 (`:4936`): local staging replaces it.
- **B.** `specs/engineering_practice.md:50-66` (binding by D28): "We run four environments ... 3. Staging — `https://dev.metorite.com` ... Test on staging before production". The same file at `:161` says "The owner retired staging". `specs/development_and_delivery_framework.md`, the WS-PL owning spec, names staging 46 times and never cites D144. `INDEX.md:36` lists `STAGING_SETUP_RUNBOOK.md` as "ACTIVE". `INDEX.md:134` lists `staging_and_daily_release.md` as PROPOSED. §6.0 A3 (`:5594`) is "D-D, where staging runs". WS-MCP (`:169`) has "SW-10b (the sweep on staging first)". HANDOFF H-335, H-38, H-359, H-360, H-379 and H-403 are staging entries. LP `04:72` says "Test on staging".
- **Also.** `CLAUDE.md` §4 step 2 tells an agent to run `scripts/stage_local.sh`, and says that "premerge.sh needs" its `local-staging` pass. The script is not on main, and `premerge.sh` does not check `local-staging`.
- **Resolution.** Banner or retire the four staging docs, fix `engineering_practice.md` §1, delete the six HANDOFF entries and §6.0 A3, and change CLAUDE.md step 2 to "after LD-9 lands".
- **Who decides.** Agent. D144 already decided this.

---

## MEDIUM

### 10. INDEX files active specs as deferred, and parked specs as active
- **A.** `INDEX.md:157` heading: "DEFERRED / PARKED / HISTORICAL — nothing dispatches from these". Under it, `INDEX.md:164` lists `calendar_events_reminders.md` ("ACTIVE for reminders ... ACTIVE for events"), which owns WS-CAL (`work_plan.md:162`). `INDEX.md:165` lists `agent_sandbox.md` ("✅ ACTIVE"), and §2.0 item 9 (`:140`) says "SBX-1 ... is next".
- **B.** The ACTIVE table lists parked or undecided specs. These are `note_taker_app.md` and `meeting_bot_platform_plan.md` (`:106`, WS-19 ⏸), `workflows_app.md` (`:110`, WS-11 ⏸), `whatsapp_assistant_channel.md` (`:81`, WS-47 ⏸ D115), `whatsapp_message_manager.md` (`:107`) and `department_centers.md` (`:96`, "nothing dispatches"). They also include `external_agent_dispatch.md` (`:46`, "PROPOSED, no decision taken") and `chat_first_surface.md` (`:57`, "PROPOSED", though D109 decided it). §2.1 (`work_plan.md:184`) names `core_brief.md` "(PROPOSED)", but `INDEX.md:74` and the spec header say ACTIVE.
- **Why.** INDEX is the classification of record (`work_plan.md:21`). An agent that obeys it refuses live work and builds parked work.
- **Resolution.** Move each spec to its true section in one PR.
- **Who decides.** Agent.

### 11. One D-number, two decisions (D71 to D73)
- **A.** `specs/credit_pricing.md:561-563` records D71 (bill from the peak rate), D72 (credits per million tokens) and D73 (margin per tier) as "Owner directed it on 2026-09-04". `INDEX.md:64` repeats these meanings.
- **B.** §3 gives the same numbers to other decisions: D71 is admission modes (`work_plan.md:2758`), D72 is operator elevation (`:2796`) and D73 is the My Tasks rename (`:2842`). D74 is skipped for credit expiry (`:2913`).
- **Why.** A citation of "D72" resolves to two different owner decisions. The three credit decisions have no valid global id.
- **Resolution.** Give the credit decisions free numbers, record them in §3 and fix both files.
- **Who decides.** An agent does it. The owner confirms that the 2026-09-04 directions stand.

### 12. The TOOL_PACKS gate says "owner-only", but D179 gave the flip to the supervisor
- **A.** §6.0 D4, `work_plan.md:5624`: "`TOOL_PACKS` and `TOOL_PACKS_ORGS` ... any value other than empty. Each is owner-only, and the §3a window does not open it". The row label is "build agent-safe, run owner-only".
- **B.** D179.2, `work_plan.md:5433`: "The supervisor turns it on ... `STUDIO_ORGS=*` and the `studio` pack in `TOOL_PACKS`". Memory: an agent added `studio` to production `TOOL_PACKS` on 2026-11-06.
- **Why.** The gate register, which agents must obey, does not record the exception. The same D4 cell also lists many flags as "gate enforcement-flip, which the §3a window covers", which contradicts its own "run owner-only" label.
- **Resolution.** Record the D179 exception in §6.0 D4 by name. Split D4 into owner-only flags and window flags.
- **Who decides.** Owner.

### 13. The launch set: a fixed table, or the package registry?
- **A.** `specs/launch_surface.md:94`: "This table is the authority. The registry in code (`src/lib/nav.ts`) must match it exactly", with a count fence of 15. §6.0 C3 (`work_plan.md:5613`) makes a promotion an owner act.
- **B.** D147.2, `work_plan.md:4983`: "The shell ... read installed packages, not lists in code". PKG-6 merged on 2026-11-06: "the workbench reads the resolved manifest (sidebar and ⌘K, dark)". `specs/package_host.md:402,478` still says "14 live panes" and names no rule for the live set. `launch_surface.md:159-161` lists Workflows, App Workshop and Agent Workshop as preview panes, but `nav.ts` has none of them, and D127.1 and D141.2 retire them.
- **Why.** When `PACKAGE_HOST_ORGS` turns on, an install can change the sidebar without the owner gate or the count fence.
- **Resolution.** Decide whether the owner gate H-21 applies to installs. Write the answer into both specs. Remove the three dead preview rows.
- **Who decides.** Owner, for the gate. Agent, for the text.

### 14. Superseded decisions carry no banner
- **A.** The §3 intro, `work_plan.md:272-274`, names only D11 and D10 part 1 as superseded.
- **B.** These decisions are superseded or amended but carry no banner:
  - D23 (`:2055`, pricing shaped by Centers) and D24, which D49 supersedes (`:1188`). D18 (`:2207-2211`) still says "cite D23 for anything a customer buys". D20 (`:2141`) keeps "Complete ... at ₹3,600".
  - D55 (`:896`, "development → staging → production"), which D144 changes.
  - D39 (`:1542`, the queue in the repo), which D118 and D135 change.
  - D17 (`:2230`, Mem0 binds the tenant) and D21 (`:2120`, Knowledge "builds on Mem0, Graphiti"), which D132 changes.
  - D119.3 and D127.1, which D141 changes.
  - D124.4 (`:4664`, "After Phase 2"), which the D136 amendment (`:4796`) changes.
  - D73 ("The Tasks app is My Tasks"), which OP-4 changes (`launch_surface.md:125`).
- **Resolution.** Add a one-line banner to each record that names its successor.
- **Who decides.** Agent.

### 15. The milestone tags disagree with §2.0 M4 and M1
- **A.** §2.0 M4, `work_plan.md:126`: "The UX System, Writing Help, MCP data parity and Notes". D119.3 moves "the UX rules, MCP data parity" to after Phase 2. §2.0 M1 (`:123`) means "Brief tasks 1 to 7 are done".
- **B.** WS-UX (`:179`) is "🟢 active · M1", and the glass look is live in production (D143, D149). For MCP parity, WS-MCP (`:169`) says M2, epic 495 says "M3", and D136 (`:4796`) says "now" for MCP-P0 to P2. WS-PKG (`:171`), WS-WEB (`:172`) and WS-CO (`:178`) are tagged M1, but none is a brief task. WS-CIV (`:173`) has no milestone, which breaks the legend at `:152`.
- **Resolution.** Rewrite M4 and re-tag the rows against one milestone definition.
- **Who decides.** The owner confirms. An agent edits.

### 16. Specs that still sell Centers and packages
- **A.** D49 (`work_plan.md:1181`): one flat seat, with no Center packages and no add-ons. `launch_surface.md` §5 withdraws Centers.
- **B.** `specs/customer_console.md:866` (WS-CM owning spec): "Center packages and add-ons stack on top". `specs/future_modules_roadmap.md:12-13`: "Marketing Center package (₹600)". `specs/crm_app.md:439`: "sold only inside the Sales Center package (₹600/user". `specs/people_center_app.md:3` (WS-PP owning spec): "People Center (`/centers/people` and the `/people` app behind it)". `launch_surface.md:20` tells an agent to "switch the org to Fluent, then Material, then Graphite", which are themes that D69 and D149 retired.
- **Resolution.** Banner or rewrite each passage.
- **Who decides.** Agent.

### 17. The Tasks app and `gtd_*`: three different stories
- **A.** `CLAUDE.md` §2 (main, lines 71-73): "ONE task store, three lenses ... Tasks (`/tasks`) is my lens". D73 (`:2842`): "The Tasks app is My Tasks". `INDEX.md:104-105` and §4 (`work_plan.md:5520`): "task_manager_app.md (the personal GTD lens — untouched as an app".
- **B.** `launch_surface.md:125`: "My Tasks left the sidebar on 2026-10-25 ... `/tasks` redirects". `one_projects_app.md` (INDEX:59): "My Tasks stops being an app".
- **Also, gtd.** §6.0 D2 (`work_plan.md:5622`): "the `gtd_*` backfill and drop ... Built on this branch, unrun". `INDEX.md:108`: "The calendar persists to `gtd_items` directly". §4 (`:5522`): "`calendar_focus_os.md` §5 canonical for `gtd_time_blocks`". But `infra/postgres/217_gtd_task_store_drop.sql` is on main, and CLAUDE.md says "`gtd_*` is gone".
- **Resolution.** Update CLAUDE.md §2 to "Projects (My work), Calendar". Delete §6.0 D2. Correct INDEX:108 and the §4 row.
- **Who decides.** Agent.

### 18. WhatsApp is live for customers but frozen
- **A.** `launch_surface.md:104` and `nav.ts:283-294`: "My WhatsApp" has been `live` since 2026-10-08.
- **B.** D115 (`work_plan.md:4490`): "deprioritized". §2.2 WS-20 (`:215`): "⏸ parked". WS-WA (`:166`): "🟡 gated · M2".
- **Why.** A customer-facing app has no active workstream, and only fixes are allowed on it. D115's reason ("email, Projects and Knowledge ... first") also conflicts with the 2026-11-06 lane order.
- **Resolution.** Move it to `preview`, or lift D115 for upkeep.
- **Who decides.** Owner, because a promotion or a withdrawal is §6.0 C3.

### 19. Shared mailbox: "do not dispatch" against ACTIVE
- **A.** §4, `work_plan.md:5509`: "Shared mailboxes | ⚠️ OWNERLESS IN FACT — do not dispatch".
- **B.** WS-EM (`:164`) names `specs/shared_mailbox.md` as its owning spec. `INDEX.md:102`: "✅ ACTIVE (2026-10-23). The owner answered Q-SM-1 to Q-SM-9". HANDOFF H-415 plans SM-3 to SM-9.
- **Resolution.** Point the §4 row at `shared_mailbox.md`.
- **Who decides.** Agent.

### 20. Merge queue: the launch plan against D176
- **A.** D176.3, `work_plan.md:5365`: "The 2026-10-16 decision stays. No merge queue". WS-CIV (`:173`).
- **B.** LP `04-calendar-and-risks.md:69`: week 1 "Build the dispatcher, merge queue and hot-file locks". LP `01-resources-for-1-month.md` (§2): "A merge queue that tests many candidate batches at once". LP `05-budget-and-timeline.md:100`.
- **Why.** The LP (2026-10-30) is older than D176 (2026-11-04), but the LP is still the budget sheet that the owner shares.
- **Resolution.** Strike the merge queue from LP E02 and E25, or reopen D176.3.
- **Who decides.** Owner.

### 21. Team size: a soft cap of 10 against 40 seats per company
- **A.** D123.3, `work_plan.md:4654`: "Ten users per organization is a soft cap that matches `MAX_TEAM_SIZE`".
- **B.** LP `03-cost-model.md:3`: "Running 50 companies at 2,000 seats", which is about 40 seats a company. LP `04:33` flags the cap.
- **Resolution.** Raise or remove the cap before the first paying customer, or change the LP model.
- **Who decides.** Owner.

### 22. The roadmap inputs live in neither authority
- **A.** D118.1 (`work_plan.md:4605`): Metorite holds "the order of every work item". CLAUDE.md §4: "work that is not on it is invisible".
- **B.** LP `README.md:9`: "Nothing here is in Metorite". LP `tasks/INDEX.md:3`: 380 tasks, "None is in Metorite", including the HRMS, ticketing, payments and field-tracking apps. `specs/core_brief.md:50-53`: the brief "Metorite Core Function Tasks", which sets M1 and M2 (D119), "is not in the repository". §2.0 cites "brief tasks 1 to 7" and "8 to 15", but no file lists them.
- **Resolution.** File the 15 brief tasks as one Metorite epic. Decide when LP E01 to E25 go on the board.
- **Who decides.** Owner. Memory `launch-plan-local.md` records the owner's "keep it local".

### 23. Scope is larger than the people who do it
- **A.** 27 rows of §2.1 are 🟢 active (`work_plan.md:156-186`), and 53 rows of §2.2 are still "🟢 open" (`:194-262`). That makes 80 open workstream ids. The 2026-11-06 plans add more, each with no row: WS-PLN, the orchestration playbook (task 1906, owner asked for a D record), WS-ONB (task 1872), WS-CL and WS-BUD.
- **B.** The people are the owner plus agents. The LP plans "you plus 4" developers from 9 November (LP `05:101`), but the memory of 2026-11-06 says "The contributors are not chosen yet". §6.0 C2 (`:5612`): CODEOWNERS "cannot be written before a second person exists". LP `04:47-53`: 71 owner-gated entries wait, and "You become the bottleneck".
- **Gaps among the active streams.** No spec: WS-CZ, WS-DOC and WS-IR. No Metorite epic: WS-CO (`:178`). A "next" item that is defined nowhere in the repo: E-58 (WS-PP, `:163`), and on the board OB-1 is "Blocked by E-58". Only one active epic (491, Studio) is In progress.
- **Resolution.** Park every stream outside the agreed order, or close the §2.2 rows to "now in X" with no 🟢.
- **Who decides.** Owner.

---

## LOW

### 24. AUTO has two homes and a project that does not exist
- **A.** WS-BRIEF, `work_plan.md:184`: "automations in sub-epic AUTO of epic 479 · gate AUTO-3 (the trust ladder)".
- **B.** WS-AUTO, `:175`: "project 'Automations', epic 807 · ... AUTO-3 (D131) ... merged". The board has no project "Automations". Epic 807 is in Proactive Intelligence (Backlog, due 2027-03-31). Commits use both "WS-BRIEF AUTO-2" and "WS-AUTO AUTO-7b".
- **Resolution.** Give AUTO one row and the real project name. Remove the stale gate.
- **Who decides.** Agent.

### 25. The §4 owners are parked rows or stale facts
- `work_plan.md:5507`: Groups admin goes to WS-13, and team-instanced agents go to WS-14. Both are parked by D49. `:5512`: Budgets go to WS-16, which is parked. `:5513`: Digest workflows go to WS-15, which is parked.
- `:5506`: "`vjvarada@fracktal.in` is the only signed-in member". This conflicts with §6.0 C4 (`:5614`, "TWO `active` `admin` rows") and with two customer-zero orgs.
- §4 header (`:5502`) and R6 (`:80`) cite §5 and §8, but these sections no longer exist.
- **Resolution.** Reassign the owners or mark each row unowned. Delete the stale facts.
- **Who decides.** Agent.

### 26. Mem0 and Graphiti retired, but still cited as live
- **A.** D132, `work_plan.md:4738`: "Mem0 retires with Graphiti".
- **B.** `specs/knowledge_base.md:152,271`: "`memory_architecture.md` (WS-9) owns Mem0" and "Nothing here replaces Mem0". The spec cites D132 nowhere. `INDEX.md:58`: "`memory_architecture.md` keeps Mem0 and the compartments". `specs/note_taker_app.md:273` writes meetings to "Mem0/Graphiti". `INDEX.md:147` says that `system_architecture.md` "carries a stale-warning banner", but it has none. Its line 9 says "Source of truth lives in ClickUp, Zoho, Odoo", and ADR-011 is "Mem0 + Graphiti".
- **Resolution.** Add the D132 pointers and a real stale banner.
- **Who decides.** Agent.

### 27. §6.0 rows that are already moot
- C1 (`work_plan.md:5611`) says "Restore branch protection on `main`", and §2.0 item 7 (`:138`) asks for the same. But `gh api .../branches/main/protection` shows seven required checks on main today.
- C3 (`:5613`): "The launch surface stays nine panes". It holds 15.
- A3 (`:5594`): staging (item 9). D2 (`:5622`): gtd (item 17).
- **Resolution.** Close the rows or restate them.
- **Who decides.** Agent. The owner confirms C1.

### 28. "Email and calendar out of preview" against "both live"
- **A.** §2.0 item 11, `work_plan.md:142`: "Email and calendar out of preview (brief task 3)".
- **B.** `launch_surface.md:101,103`: Calendar has been live since D54, and My Email since 2026-10-02.
- **Resolution.** Restate item 11 in terms of the features that are still dark: Gmail, two-way sync and E-05 to E-11.
- **Who decides.** Agent.

### 29. Decision numbering drift
- Gaps on main: D44, D45, D151, D152, D154 to D159 and D182 to D184. D152, D183 and D184 exist only on branches, so D152 governs pricing from a branch (item 1).
- `INDEX.md:19`: "Decisions (D1–D93, current 2026-10-07 ...)". §3 runs to D185.
- Date ids with no §3 record: "D-2026-10-16" and "D-10-16" (`work_plan.md:173`, `ci_velocity.md:64`, D176).
- §6.0 uses the row ids D1 to D4 (`:5621-5624`), which collide with the decisions D1 to D4.
- The specs hold 297 local ids in 20 families (D-EM 63, D-PM 40, D-PZ 38 ...), and `work_plan.md` never names 195 of them. INDEX:19-20 says the decisions "live in `work_plan.md` §3".
- D118.2 (`:4606`) names "the rules R1 to R8", but R9 exists (`:96`).
- WS-WH "next WA-1" (`:180`) reuses the WhatsApp WA- prefix, which R2 (`:51`) forbids.
- **Resolution.** Fix the INDEX range. Rename the §6.0 row ids. Record the date-id decisions as D-numbers.
- **Who decides.** Agent.

### 30. Three ways to partition the work, plus two id schemes in the commits
- The 31 rows of §2.1, the lanes L1 to L8 (owner-approved 2026-11-04, memory `lane-plan-2026-11.md`) and the app projects of the 2026-11-06 board reorganisation all partition the work. Memory `multi-person-delivery-plan.md` says: "Unit of ownership: an app project ... not the L1-L8 lanes".
- 76 commits since 2026-10-28 use the old §2.2 ids (WS-67 24, WS-29 10, WS-66 7, WS-63 7, WS-55 5, WS-53 5 and more) and not the §2.1 ids.
- **Resolution.** Choose one unit of ownership, and map the others to it in §2.
- **Who decides.** Owner.

---

## Direction as the documents state it today

Read together, the documents point in two directions, and nothing reconciles them.

**The written direction (repo).** D119 and §2.0 set a safety-first path. Customer zero (Fracktal and DEWiN) works daily by about 17 November, with safe backups (M0). Then the cheap isolation fixes, the isolation spine H2 to H3 and a real second organization come before ten six-week pilots in December (M1). Money (Razorpay, the rate card, invoices) and brief tasks 8 to 15 lead to the first paid customer on 31 March 2027 (M2). Studio, workflows, the UX system and MCP parity wait until after Phase 2. Pricing is flat at ₹500 per user plus AI credits. Staging is retired, and production moves once a day at 21:30 UTC after an owner preview.

**The acted direction (owner directives since 2026-10-31, the board and the commits).** Per-company customization is the goal. AI Studio and the package host come first (lane order of 2026-11-06). Studio is live for every org, and every app becomes a package that the org reshapes. Metorite also sells orchestration know-how to any org that connects its MCP. The local launch plan targets beta on 25 November, five paying customers in December and full scope by 16 December, with four contract developers who are not yet hired. A branch (D152) replaces the ₹500 seat with $20, $100 and $200 tiers sold worldwide.

**What this means.** Thirty-one percent of the commits since 1 November went to Studio, the package host and web research. Four percent went to the M0, isolation, secrets and money items. The M0 tasks due 17 November are Backlog. Until the owner restates the roadmap, an agent that follows §2.0 builds different work from an agent that follows the lanes.

---

## Commit tally by workstream since 2026-10-28

Source: `git log origin/main --since=2026-10-28 --format='%ad|%s'`, 356 commits. The prefix
classifier is `direction/tally.py`, and it is approximate. A subject is counted once, under its
first matching stream. "Since 11-01" counts 203 commits.

| Workstream | Commits | Share | Since 11-01 |
|---|---:|---:|---:|
| WS-CHAT (Mira, tool packs, personalization, artifacts, chat UX) | 59 | 16.6% | 24 |
| WS-STU (AI Studio) | 45 | 12.6% | 44 |
| WS-CIV and WS-PL (CI, delivery loop, harness, tests) | 34 | 9.6% | 19 |
| WS-CRM (CRM, Zoho cutover, record card) | 29 | 8.1% | 7 |
| WS-PT (Projects and Tasks) | 25 | 7.0% | 10 |
| WS-MCP (EA, MCP-G, SW coordination) | 22 | 6.2% | 10 |
| WS-CAL (calendar, reminders) | 19 | 5.3% | 9 |
| WS-UX (glass, design system) | 15 | 4.2% | 7 |
| WS-TS (WS-29 tenant binding, H6a) | 14 | 3.9% | 12 |
| WS-OB (onboarding, guide, buddy) | 13 | 3.7% | 11 |
| WS-BRIEF (core brief, job queue) | 13 | 3.7% | 5 |
| WS-EM (email) | 12 | 3.4% | 6 |
| WS-WEB (web research) | 10 | 2.8% | 10 |
| WS-PKG (package host) | 9 | 2.5% | 9 |
| WS-KB and WS-KG (knowledge, graph) | 7 | 2.0% | 2 |
| WS-PJP (planning) | 7 | 2.0% | 7 |
| WS-ALN (alignment) | 6 | 1.7% | 6 |
| WS-DOC, HANDOFF and decisions | 6 | 1.7% | 1 |
| WS-AUTO (proactive agent) | 3 | 0.8% | 2 |
| WS-TO (box, sandbox, backup) | 3 | 0.8% | 0 |
| WS-CM (console, money) | 2 | 0.6% | 0 |
| WS-AP (waiting) | 1 | 0.3% | 1 |
| Not classified | 2 | 0.6% | 0 |

**Against the critical path (§2.0 items 1 to 15).** 37 commits (10%) name a critical-path id. 13
commits (4%) name an M0, isolation, secrets, branch-protection or money id, and most are WS-29
tenant-binding fixes. These ids have 0 commits: H2, H3.2, H3, AX-0, IR-0 to IR-3, HLP, WS-38a,
WS-5a, H-97, H-207, WS-24b, WS-1e, BH-3, BH-4, SBX-1, WS-40, E-05, E-09, E-11, E-07, MT-1j, E-58,
H-14, SC-5, H-292, H-287, and BR-8 to BR-15.

**Raw subject prefixes (top).** WS-STU 43, WS-67 24, WS-CRM 18, WS-BRIEF 14, WS-29 10, WS-PKG 9,
WS-OB 8, WS-66 7, WS-63 7, WS-WEB 6. Two prefixes are not in §2 at all: WS-CL and WS-BUD.
