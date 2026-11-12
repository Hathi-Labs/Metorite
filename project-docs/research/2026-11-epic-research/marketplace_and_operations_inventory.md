# App marketplace inventory (read-only, 2026-11-11, origin/main after fetch)

Legend: V = built (path), P = planned (task), G = gap (no spec, no task).
Sources: project-docs/specs/{building_blocks,package_host,ai_studio,agent_sandbox}.md, work_plan.md, Metorite task titles and states read today.
Line numbers come from `git show origin/main:<path>`.

## 0. BLUF

- A package, install, scope and uninstall-in-code layer exists, dark. Org and group install of a Studio app works behind `PACKAGE_HOST_ORGS` and `STUDIO_ORGS`.
- No catalog, listing, store, rating, publisher or cross-company sharing exists. Three specs forbid it by name today (package_host.md:46, ai_studio.md:53, agent_sandbox.md:259-263). A marketplace needs a NEW spec and an owner decision that amends those three non-goals.
- First-party templates K1 to K10 are all Backlog. None is built. K3 (inventory, procurement) and K4 (MRP-lite) are the manufacturing templates.
- ai_studio.md:57, non-goal 8: "No general ledger. Accounting stays in Tally and Zoho Books." This conflicts with the owner's request to build accounting as a module. Owner decision needed.
- Template upgrade with customizations (K2, task 1204) and clone/export/import (G14, task 2387) are Backlog. No semver, no dependency solver, no upstream lineage.

## 1. Package model (package_host.md, D147, epic WS-PKG task 1234, In progress)

| Item | Fact | State |
|---|---|---|
| Definition | "One versioned manifest: what an app is and what it adds" (package_host.md:54). Every app is a package, built-in too (§3.2). Shell JSON per built-in app: `packages/acb_common/acb_common/app_manifests/shell/<app>.json` | V (PKG-1..3, tasks 1239-1308 Done) |
| Manifest | One envelope, one validator `validate_manifest`: `packages/acb_common/acb_common/studio_manifest.py` and `studio_manifest.schema.json`. TS parity in `src/lib/studio/manifest.ts`. Top level: `studio:1`, id, name, purpose, objects, actions, views, workflows, skill, shell, `contributes`, `extends`, `needs` (lines 361-394) | V (A2 Done) |
| Stores | A Studio package is a row of `apps` (format='manifest'). Versions are `app_versions` (immutable int). Also `app_grants`, `app_audit`. Migrations 114, 322 | V |
| Registry | `package_install` and `package_contribution` (migration 324), FORCE RLS. Only writer: `gateway/package_host/store.py`. Never holds the manifest | V (PKG-4, 1242 Done) |
| Resolve | `GET /shell/manifest`, `gateway/package_host/resolve.py`, ETag, flag `PACKAGE_HOST_ORGS` (default empty) | V dark (1243, 1244 Done) |
| Scopes | `private`, `group:<slug>`, `org`, `system` (package_host.md:349-356). Org wins over private (PKG-Q6) | V |
| Install | A publish keeps a private install (PKG-8a). `POST /studio/packages/{id}/install` with `{scope: org or group:<slug>}` needs `studio:publish` (D180.3). Answers 201/200/403/404/409. One `app_audit` row `package.install` (package_host.md:327-338). Files: `routes/studio/packages.py` (`install_package`), `package_host/studio_app.py`, `src/lib/shell/installedApp.ts` | V dark (PKG-8a/8b/8c: 1246, 1963, 1964 Done) |
| Version pin / upgrade | An install holds `version int`. A publish moves only the owner's private install. An org or group install moves only when an approver calls install again with the new version (package_host.md:338). So an upgrade is a manual re-approve. No auto-update, no notice | V |
| Uninstall | Store functions `remove_install` (cascade deletes claims and field values), `pause_install`, `resume_install` (PKG-9, 1247 Done). **No route.** The org and group uninstall route is PKG-8e (2222 Backlog). No backfill of installs for apps published before the flag | store V, route P |
| Data on uninstall | Install removal cascades claims and `package_field_value` rows. Studio app records belong to the package and die with `DELETE /studio/packages/{id}` (A5c-1 Done, with preview counts). Admin delete for a departed owner is A5c-2 (1704 Backlog). **No export-before-delete spec** | partial V |
| Field contributions | Table `package_field_value` (PKG-13, 1251 Done), store `package_host/field_values.py`, claim point `fields`, key `<record kind>:<field>`. Host door that reads and writes as the viewer: PKG-14a (2319, 2320 In progress). Slots: PKG-14b (2321 Backlog), PKG-14c (2426 Backlog) | store V, door and slots P |
| Slots, sub-apps, overlays | §8. Slot accepts T1 content only. Sub-app is `extends` (PKG-15, 1253 In progress). Overlay (PKG-16, 1254 Backlog) may not change an action class or remove a check | P |
| Contribution points | nav, page, jobs, search, needs, cards, skill, actions, triggers, agent, slot, fields, settings (package_host.md:119-136). Epic HX (2757) adds consumes, setup, nudges, insights, prompts, resources, channels, surfaces (building_blocks.md §3.1; HX-0 task 2762) | part V, rest P |
| Needs / dependencies | `needs` = `{feature}` or `{package: <manifest id>}`. Unmet gives a `needs_setup` card (§5.4). Met when a visible package with that id exists. No version range, no auto-install | V basic |
| Lifetime | `ends_at`, daily expiry job, 30-day archive, restore, bell note 3 days before (§6.2). PKG-10 (1248, 2006 In progress), 10c (2007 Backlog, blocked) | P |
| Promotion | private, then people or group, then org, on the same install id. PKG-11 (1249, 2174 In progress, 2174 blocked on the J2 approval-record decision) | P |
| Trust tiers | T0 system, T1 declarative, T2 sandboxed UI, T3 server logic (§7) | spec |
| Words searched | "marketplace": non-goal texts only (package_host.md:46, ai_studio.md:53, agent_sandbox.md:259, integrations_console.md:214, skills_registry.md:25, workflows_app.md:66 and :69, saas_multitenancy.md:627). agent_coordination.md:2008 (Claude Code plugin marketplace) and work_plan.md:5992 (MIR-13, Metorite listings on third-party marketplaces) are unrelated. "listing, rating, review, publisher, store" for apps: no hit. "catalog": only the action catalog and `feature_catalog` | |

## 2. AI Studio (ai_studio.md; epic task 491 In progress; groups 1136-1146)

### 2.1 Four layers (ai_studio.md:319-346, D146)
1. Manifest: objects, relations, lines, views, workflows, permissions. V (A2, C1, C13).
2. Rules: formulas, roll-ups, state machines, constraints. A closed JSON tree, no I/O. C1b multi-record writes with `range`, `unique`, `group_sum` rules are V (ai_studio.md:207). Formulas C7a and state machines C6 are P.
3. Logic modules. B4 first-party reviewed repo code (1216 Backlog). B5 member-authored, off-box sandbox only, after SBX-9 (1217 Backlog, OWNER-GATE). B6 `run_as` (1413). B8 package layers (1415).
4. Custom UI block. L1 (1205), L3 browser compile spike (2339), L4 UI kit (2415), L5 three.js (2416), L7 warm runtimes (2418). All Backlog.

### 2.2 First-party templates (K group, ai_studio.md:1057-1075). All Backlog.
- K1 (1203) templates in `app_templates` for U7, U8, U15, U16, U19, U9.
- K2 (1204) template update to an installed app a member changed.
- K3 (1221) inventory and procurement. K4 (1222) MRP-lite and production schedule (B4 nightly MRP).
- K5 (1427) HR and payroll. K6 (2409) assembly guide (Fracktal). K7 (2410) shipment tracker. K8 (2411) field site survey. K9 (2412) clinic. K10 India statutory pack (task 2413 per spec).
- Built template code (App Workshop, V): `apps.is_template` (migration `123_app_templates.sql`), `workbench/control_plane/src/app/studio/components/AppsGallery.tsx` ("Mark as template"), fork route `POST /api/apps/{slug}/fork` (`routes/apps/lifecycle.py:512`). The gallery is "apps where is_template AND visible to me". In-org only, code-format apps, no moderation tier (comment in 123_app_templates.sql). This is the ONLY built template and gallery code.
- E6 (1178 Backlog) workflow template gallery. Templates are repo JSON fixtures (workflows_app.md:257).

### 2.3 Copy, customize, upgrade
- Copy: G14 (2387 Backlog, HIGH) clones in one org, exports a version as one file, imports it into another org as a new PRIVATE DRAFT. No records, secrets or connection ids. A clone names its source. This is the only cross-org movement of an app in any spec, and it is not called publishing.
- Customize: the copy is an ordinary private package. Edit in the IDE. Overlay (PKG-16) changes another package within its change points (§8.4).
- Template gets a new version: K2 says only "keeps each local change, or names the conflict". No merge model, no diff format. C20 (2351) covers field rename and type change that keep data (a record migration, not a template merge). PKG-16 text: "A system package update keeps every overlay, or names the conflict (K2)".
- D202 IDE rows, all Backlog: G8 Files and Monaco 2381, G9 preview 2382, G10 tests 2383, G11 package panel 2384, G12 version history and diff 2385, G13 2386, G15 co-edit 2388, G16 Mira writes code 2389, G17 save history 2390, G18 named drafts 2391, G19 GitHub sync 2392, G20 contract version 2393, G21 scaffold 2394, G22 SDK 2395, G23 CLI login 2396, G24 CLI 2397, G25 source only 2398, G26 harness 2399, G27 VS Code 2400, L6 limits 2417, J7 code right 2405.
- Sharing between orgs: none. ai_studio.md:53 and package_host.md:46: "needs its own spec". agent_sandbox.md:259-263 (D187): a running tool never leaves its org. It reaches other orgs only as reviewed first-party code, with the source org's consent (§6.8, SBX-17), as a `system` install.
- Sharing inside an org: J2 (1200 Backlog), PKG-11. The owner asks. A `studio:publish` holder reads the manifest diff and approves one version (ai_studio.md §9.3). Seeding `studio:publish` on admin: J1-2 (1707 Backlog, OWNER-GATE). J6 install consent screen (1426): what the app reads, writes and sends, runtime kind, `run_as`. A new version that widens any of these asks again. J4 admin view (1202). J3 kill switch and caps (1201).

## 3. Manufacturing and operations content already specified

| Topic | Where | State |
|---|---|---|
| Manufacturing row: assembly guide, process charts, job traveller, BOM and routing, NCR and CAPA, downtime and OEE. Patterns P1, P2, P9, P11, P13 | ai_studio.md:159 | Spec only. No template task for traveller, BOM/routing, NCR/CAPA, OEE. Only K3, K4, K6 exist |
| U-catalog: U9 spares and inventory with reorder, U10 production schedule per machine, U11 NCR/CAPA, U12 asset register with maintenance calendar, U13 permits | ai_studio.md:98-102 | K1 lists only U7, U8, U15, U16, U19, U9. U10, U11, U12 have no slice |
| Worked example inventory, procurement, MRP. Objects: item, location, stock move (ledger), BOM, BOM line, vendor, PR, PO, work order, machine. Rules: stock roll-up, PO over a limit goes to a director, no negative stock. Nightly MRP logic module. Views: stock dashboard, PO board, timeline by machine, BOM tree, phone receipt form | ai_studio.md:334-343 | = K3 (1221), K4 (1222). Backlog |
| Fracktal chain (D202, customer zero; brainstorm 2326, clinic 2327). Fields and records: C14 types 2344 (**In progress**), C15 auto-number 2345, C16 template and run 2346, C17 version lock 2347, C18 hierarchy 2349, C19 lookup tables 2350, C20 upgrades keep data 2351, C21 suggest and confirm 2352, C22 field library 2353, C23 coded value 2354, C24 measurements 2355, C25 retention lock 2356, C26 save rules on core records 2357. Blocks: D14 step player 2358, D15 diagram canvas 2359, D16 annotation 2360, D17 editable grid 2361, D18 tree block 2362, D19 gallery 2363, D20 matrix 2364, D21 compare 2365, D22 activity feed 2366, D23 floor mode 2367, D24 capture 2368, D25 scan and GPS 2369, D26 offline 2370, D27 3D viewer 2371. AI: B9 2331, B10 2332, B11 2333, B12 2334, B13 2335, B14 2336, B15 2337, B16 2340. Also J8 e-signature 2406, E9 SLA 2376, E10 approval chains 2377 | ai_studio.md:861-939, 1052-1075 | All Backlog except C14. None built |
| Facilities, work orders, asset | Facilities row "work orders from a QR on each asset, maintenance and housekeeping checklists" (ai_studio.md:168). P3 Register "assets" (:143). U12 | One-line rows. No object model, no PM schedule, no spares link, no task |
| Machine / asset objects | Only as items in the K4 example and U12 | G: no asset-management spec |
| Costing / cost roll-up | P9 examples "cost roll-up" (ai_studio.md:148). Layer-3 example "a cost roll-up" (:327) | G: no standard cost, WIP, variance, landed cost |
| Accounting / ledger | **Non-goal 8 (ai_studio.md:57): no general ledger.** U25 invoice screen over Zoho Books or ledger over Tally (:129). P12 screen over another system. I1 (1197) connections, I2 (1198) app from a connection or MCP server, O12 apps over any outside system (OpenAPI import, remote objects, mirror). K3 "posts to Tally or Zoho Books through a connector". The stock ledger is inventory only (C1b `group_sum`) | Spec. Connectors P |
| ERP connectors | D122.1 (work_plan.md:4652-4656): ask the ten pilots first, default order Zoho Books, then Tally Prime, then QuickBooks Online. BR-9a (649) "Build the first accounting/ERP connector" Backlog. Connectors epic 481 not re-read. `integration_connections` V (migration 245). CRM Zoho mirror V for Fracktal only (`CRM_MIRROR_ORGS`) | P |
| CRM products, quotes, invoices | CRX-3a 2736 products and price book, "the one source of price" (D205.7). CRX-3b 2737 quote builder with GST, versions, PDF, accept link. CRX-3c 2738 GST invoice and payment link via the org's own Razorpay. CRX-8c 2747 cash forecast. CRX-13 2755 deal to delivery (won deal makes project, home article, invoice). CRX-30 2810 tier packaging of CRM features. Epic WS-CX 2732 | All Backlog. Owning spec `crm_complete.md` (CRX-0, 2733) does not exist on main yet |
| Manufacturer preset | building_blocks.md §4.2: inventory, procurement, MRP-lite apps, a deliveries project for won deals, AMC renewals. "A preset never adds a table. It installs packages and fills settings" | P. I found no task that builds the preset mechanism |
| Department Centers | department_centers.md:174, :602 mention "Operations (production tracker, inventory/BOM, dispatch, service and AMC)" as a sketch. Centers are withdrawn (D49) | Historic |
| Documents | D13 (1421) template block and `document.render` | P |
| HR and payroll example | ai_studio.md §19.8, K5, K10 | P |
| Shop floor | D23 floor mode 2367, D24 2368, D25 2369, D26 offline 2370, O3 kiosk | P |

## 4. Payments, billing, credits

- Seat price. Code sells flat 500 INR per user per month plus AI credits (CLAUDE.md §2, launch_surface.md §4). The D152 seat tiers ($20, $100, $200) with base pricing are FINAL (D190.3, work_plan.md:5649; D204.9, :5953). They land with PLN-0 (1274 In progress) and PLN-1 catalog (1280 In progress). Epic WS-PLN, project Billing and Accounts.
- Studio pricing: STU-Q7 (ai_studio.md:785) "Inside the seat. Builds and runs spend AI credits through `usage_event`. No fee per app." Status Open. Governance §9.9: pricing stays flat.
- Credits: each model call writes `usage_event` (J3, 1201 Backlog). The Router meters all AI (D90, CP-11). Sandbox time meters as `usage_event` rows with a `sandbox` row in `task_catalog`; the price is an owner call (SBX-Q4, agent_sandbox.md §10). B9-B15 each write `usage_event`. First-party B4 runs (the MRP run) have no stated meter.
- CRX-30 (2810) decides which CRM features and credits go in which tier. Nothing says an app can be a tier feature.
- Paid apps, per-app price, vendor payout, revenue share, GST on an app sale, per-app entitlement: **G, no spec, no task.** STU-O10 payments (1515) is the org's OWN Razorpay for its customers. "Metorite holds no money" and it "never reuses the Customer Console seam". Customer Console payments (`customer_console/payments.py`) bill seats only, dark.
- Entitlement seam: `feature:studio` and `studio:publish` (J1-1 built dark). PKG-7 (1245 In progress) lets a feature that only an install declares pass the seam. So an installed app could carry a `feature` slug and be gated by a tier. The hook exists, the policy does not.

## 5. Security of installable apps

- Every action runs as the VIEWER. An app never widens D12. No raw credential in a manifest (ai_studio.md §1.2 items 3-4, §9.2). An install or agent acts as a `run_as` member cut to its scopes (D205.6; B6, 1413 Backlog).
- The validator is the safety line (ai_studio.md:344). It refuses an unknown block, an uncatalogued action, class X, a colour, a pixel size, a credential-shaped string and a dangling field reference. Caps: 256 KiB document, depth 20. V.
- Door classes: A read, B undoable write, C hard to undo, X on neither door (building_blocks.md §3.2, D136). Four MCP tools serve all apps (D205.9). Five acts always ask: money, member or role write, delete, bulk change, send to an outside person (ai_studio.md §9.5, D131.7).
- Code tiers (package_host.md §7). T1 declarative, any `feature:studio` member. T2 UI in an iframe with `connect-src 'none'`, compiled in the browser only (L3 spike decides). T3 server logic: B4 first-party only until SBX-9; B5 for members after SBX-9. "No member-authored code on the box" (ai_studio.md:56). Never package code in the gateway.
- Sandbox: agent_sandbox.md SBX-1 to SBX-17. SBX-9 (turn on in production, dedicated box) is OWNER-GATE (:1343). CBT-13 (1674) waits on it. B8 package layer rules (age at least 14 days, hashes, at most 20 direct dependencies, no git URL; ai_studio.md §19.5), task 1415 Backlog.
- Review of member code: agent_sandbox.md §8.1. A code package needs a `studio:publish` approval at each scope past private. The approver reads the files, scopes, package list and last 5 runs. Pins never move on their own. Kill switches pause and revoke.
- Review of a FOREIGN publisher's app: none. All review rules assume the approver is a member of the same org reading code written in that org.
- Data isolation (R5): `package_install`, `package_contribution`, `package_field_value` are FORCE RLS (migration 324, PKG-13). Fences: `test_package_registry_r8.py`, `test_package_host_store_r8.py`, `test_package_field_values_r8.py`, `test_studio_package_install_r8.py`. Studio objects are tenant-scoped (`323_studio_objects.sql`). G14 import carries no records, secrets or connection ids. Only anonymous counts cross tenants (agent_sandbox.md §6.8).
- Permissions an app asks for: J6 consent screen (1426, Backlog), drawn from the manifest. On paper only.
- Prompt injection: text from documents, customers and connectors is data (building_blocks.md §6.4). F1b hidden-character refusal in skill text is V.

## 6. Gaps for a marketplace (no spec, no task covers these)

1. Catalog and listing model: no table, route or page for a listing (name, publisher, notes, screenshots, category, price, compatibility). `apps.is_template` is an in-org boolean.
2. Owner decision and spec to lift the three non-goals (package_host.md:46, ai_studio.md:53, agent_sandbox.md:259-263) and to settle D187 "a running tool never leaves its org".
3. Distribution artifact: a signed, versioned, immutable bundle (manifest, files, sample data, docs). G14 export is one unsigned file with no registry.
4. Publisher identity and trust levels: first-party, verified third party, org-private. Takedown and abuse handling. Only `system` (repo-reviewed) and member exist.
5. Review of a foreign app before it is listed. Sandbox review is org-internal. Only the A2 validator runs as an automatic scan.
6. Semver and compatibility: `app_versions.version` is an int. No `requires_metorite`, no yank, no changelog.
7. Upgrade of a customized install: K2 is one line. Needs a diff and merge model (base = template version, theirs = new version, ours = customized), data migration (C20), and an approver (J2).
8. Dependencies between apps and bundles: only `needs: {package: id}` and `extends`. No version range, no install order. Presets (building_blocks §4.2) have no mechanism task.
9. Install preview and sample data: J6 only. G4 (1189) is a builder preview. Nothing for "try with sample data, then remove it".
10. Uninstall and data export: PKG-8e (2222) missing. No export before delete. No "remove app, keep data" mode.
11. Search, browse, ratings, reviews, usage counts. Cross-tenant counts are allowed only as anonymous category counts, so ratings need a privacy decision.
12. Commerce: paid apps, trials, price per app or tier, vendor payout, revenue share, GST, refunds, per-app entitlement. Metorite holds no money.
13. Private org catalog: an org-approved list for its own groups, distinct from J2 sharing. `is_template` is the nearest thing.
14. Install-time configuration and secrets: a `settings` contribution point exists; a setup item exists (HX, P 1341, D196). No install wizard.
15. Metering per installed app: `usage_event` has org and member. J4 shows "use". No per-app credit attribution.
16. Template lineage: G14 names the source. No upstream tracking for update notices.
17. Terms, liability, data-processing for third-party code: owner and lawyer items.

## 7. Gaps for the operations modules

1. Accounting: the request contradicts ai_studio.md:57. No chart of accounts, journal, GST ledger, bank reconciliation, AR/AP. Only connectors are planned (BR-9a 649, U25, O12). Owner decision: build or connect.
2. Costing: no standard cost, BOM cost roll-up, WIP, variance, landed cost. C7a formulas and roll-ups are the base. No template.
3. Asset management: only U12 and facilities one-liners. No register model, depreciation, PM schedule or work order from QR.
4. BOM, routing, production: K4 covers BOM, work orders, machines, MRP. Routing and operations, work centers, capacity, job traveller, shop-floor confirmation, backflush, scrap and yield, lot and serial traceability have no slice. Prerequisites C18 (2349) and D18 (2362) are Backlog.
5. Quality: NCR/CAPA (U11), inspection (P5), SPC have no slice. Bases C17 (2347) and J8 (2406) are Backlog.
6. Maintenance, OEE, downtime: P11 names OEE. No machine event capture, no machine connector.
7. Sales-to-fulfilment link: CRX-13 (2755) makes a project. No order, delivery or dispatch object. Duplicate-store risk: K3 has its own items and product lines, while D205.7 makes the CRM catalog (2736) the one source of product and price.
8. India statutory: e-invoice, e-way bill, TDS, HSN (C23 2354). K10 is HR only. Money in whole paise (C7).
9. Scale: ledgers and BOMs at volume. Caps 1,000,000 records and 2 GiB per org (C10, 1418 Backlog). Sum reads on hot fields (C2b 1447 Backlog; C1b V).
10. The common stack is unbuilt: wave-1 Fracktal blocks (C14 to C21, D14, D19, D22, D24, D25) are Backlog (C14 In progress). B4 (1216) Backlog.

## 8. Open tasks that touch this (state read 2026-11-11)

Package Host (project caa36ee7, epic 1234 In progress):
- In progress: 1248 PKG-10, 2006 PKG-10b, 1249 PKG-11, 2174 PKG-11b, 1252 PKG-14, 2319 PKG-14a-2, 2320 PKG-14a-3, 1253 PKG-15, 1245 PKG-7, 2195 PKG-6c, 1255 PKG-17, 1256 PKG-18, 1258 PKG-20.
- Backlog: 2222 PKG-8e uninstall route, 1965 PKG-8d, 2007 PKG-10c, 1250 PKG-12, 1254 PKG-16, 2321 PKG-14b, 2426 PKG-14c, 2311 PKG-6c-2, 1257 PKG-19.
- Done: 1236 to 1244, 1246 (PKG-8), 1247 (PKG-9), 1251 (PKG-13), 1308, 1312, 1725, 1963, 1964.

AI Studio (project 55f9826e, epic 491 In progress, 221 non-done tasks):
- Templates (Backlog): 1146 K/L/M group, 1203 K1, 1204 K2, 1221 K3, 1222 K4, 1427 K5, 2409 K6, 2410 K7, 2411 K8, 2412 K9, 1178 E6.
- Share and govern: 1200 J2, 1201 J3, 1202 J4, 1426 J6, 1707 J1-2, 1818 J1-3, 2405 J7; group 1145 In progress.
- Clone, export, history: 2387 G14, 2385 G12, 2390 G17, 2391 G18, 2392 G19, 2351 C20, 2346 C16.
- Code and sandbox: 1216 B4, 1217 B5, 1413 B6, 1414 B7, 1415 B8, 1205 L1, 1206 L2, 2415 L4, 2418 L7, 1674 CBT-13.
- Connectors: 1144 group, 1197 I1, 1198 I2, 1424 I3, 649 BR-9a (Proactive Intelligence project).
- Fracktal blocks: §3 row (2344 In progress, rest Backlog).

Other: CRX-3a/b/c 2736/2737/2738, CRX-13 2755, CRX-30 2810, WS-CX 2732 (Backlog). PLN-0 1274 and PLN-1 1280 (In progress). DECISIONS pack 2816 (Backlog). HX epic 2757 and HX-0 2762 (not re-read).

## 9. What I did not read or verify

- Task bodies. I read titles and states only (no `get_task`), so acceptance text comes from the specs.
- Not read: `docs/app-workshop/README.md`, `credit_pricing.md`, `ai_metering_and_analytics.md`, `launch_surface.md` §4 in depth, `external_agent_access.md` §19 (door classes come from building_blocks.md and ai_studio.md), `saas_multitenancy.md` §2.4, `integrations_console.md` beyond line 214, `project-docs/research/` (blocks_inventory.md, 2026-11-plan-audit), `department_centers.md` beyond grep.
- Not found on main: `crm_complete.md`, a WS-PLN seat-plans spec file.
- Task 2413 (K10) and epic 481 (connectors): states unknown (not in my filtered list).
- Code was not opened beyond grep hits and file listings. "Built" rests on spec status text plus file existence on origin/main. No tests were run.
