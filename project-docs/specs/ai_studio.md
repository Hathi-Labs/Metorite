# AI Studio — skills, workflows and apps that a company builds for itself (WS-STU)

> **Dated 2026-11-11, price (D204.9).** Seat tiers are final. The flat ₹500 seat ends when PLN-0 merges. Until then the code sells the flat seat. Read each line that says the flat seat as the seat of D152 after PLN-0. Line: 783 (STU-Q7).

> **Product:** Metorite · **Feature:** AI Studio (`/studio`) · **Updated:** 2026-11-14 · **Owner:** vjvarada
> **Status:** 🔨 **In build.** §14 holds the state of each slice, and a built row starts with 🔨 **BUILT**. Every built slice is dark behind `STUDIO_ORGS`, which is empty on every box, and no org has the `studio` tool pack. The `/studio` pane (A1, E1) is live in the nav for the beta since 2026-11-05 (D179), and its routes still need `STUDIO_ORGS`. An org that the flag does not list sees a "not open yet" state. §15 holds the stages, and §20 holds the plan for apps that serve people outside the org. A slice never edits this line.
> **Verified against code on 2026-11-08 at `4439b200f`** (main). A script checked that each of the 114 paths that §16 names exists on that commit.
> **Board:** WS-STU in `work_plan.md` §2. **Decisions:** D127 (one app, three kinds) and D139 (one coding harness). **D141**: Studio starts now, and a skill creator replaces the agent creator. **D146**: the owner's answers of 2026-10-31, with layered apps, a typed object store and first-party templates. **D193**: the Studio IDE. **D202**: the full web IDE, the UI kit, the sandbox for each org and person, and the new blocks. **D219**: the app frame, with a left sidebar, tabs and record pages (§5.5). **D220**: build on a connection, with a guided connect (§8.1). D124, D130, D131 and D136 bind it too.
> **Metorite:** project "AI Studio", umbrella epic #491, this spec task #1135. Each group of §14 has its own epic under #491.
> **Gate summary:** every slice is AGENT-SAFE unless §14 says otherwise. The owner answered STU-Q1, Q2, Q3, Q5 and Q6 and the template question on 2026-10-31 (D146). The owner answered STU-Q4 on 2026-11-04 and STU-Q4b on 2026-11-05 (D180). Eight questions stay open: Q7, Q8, Q9 and Q11 to Q15 (§12).

---

## 0. Thesis

Every company has work that no general product covers. A 3D-printer maker tracks install visits. A telecom team chases tower permits. A finance lead chases late invoices on WhatsApp. Today that work lives in spreadsheets, in WhatsApp groups and in people's heads.

**AI Studio is where a company builds what Metorite does not ship.** A member describes the need in chat. Mira asks a few questions, proposes a plan, and builds it from parts that Metorite already has. The result is one of four shapes (§2): a skill for Mira, an automated workflow, a small app, or all three together.

**The bet is composition first, and code in a box (D146).** A Studio app is data. It names objects, actions, workflows, views and a skill. One renderer draws its views in the one look, from the same blocks that Projects, the Calendar and the CRM use. So a generated app looks native, works on a phone, renders inside chat, and reaches outside agents through the MCP.

**Four layers, each with its own containment (§3.7).** The manifest holds structure. Rules hold formulas, roll-ups, state machines and constraints. Logic modules hold computation, such as an MRP run. A custom UI block holds the rare screen that no block covers. Every layer reaches data only through the one action door. So a company can build a full inventory, procurement or manufacturing app with no hole in the platform.

**The goal is a full web IDE inside Metorite (D202).** A company builds the apps that extend Metorite to fit its own work. The IDE builds with blocks first: the same board, list, table, tabs and form that Projects and the CRM use. Where no block fits, a member writes code files in TypeScript, from the same UI kit and with the same look. Every read and every write goes through the one door, with the rights of the viewer. The sandbox keeps each organization apart, and an admin decides who in it writes code.

**Three layers on one door (D202.7).** Core apps serve every org: the CRM, Projects, the Calendar, Email, WhatsApp, People and Knowledge. Packages fit a core app to one org, with slots, fields, sub-apps and overlays (`package_host.md` §8). Custom apps, such as a clinic app or an HRMS, are Studio packages. All three layers use one door, one look and one channel layer: the org's own WhatsApp number and mailboxes (§20).

**The moat is the platform under the app.** Zapier, Airtable and Retool can draw a form. They cannot call Mira, read the company's tasks, CRM and mail with the viewer's own rights, ask on an approval card, or reach the same app from Claude Code over the MCP. Every Studio app gets all of that by construction.

---

## 1. Scope and non-goals

### 1.1 In scope

1. **One Studio pane** (`/studio`) with one library of apps, workflows and skills (D127). It replaces App Workshop, Agent Workshop and the separate Workflows pane.
2. **The package model** (§3): objects, actions, workflows, views and a skill, in one versioned manifest.
3. **The action catalog** (§3.2): one list of every capability an app, a workflow or a skill may use. It is built on the D136 app manifests, the workflow catalog and the member's connections.
4. **Blocks** (§5): the existing views made data-agnostic, plus new blocks (mind map, process flow, checklist, map).
5. **The skill creator** (§7): it replaces the Agent Workshop (D141).
6. **The builder** (§6): Mira builds and edits a package in chat, with a preview on sample data.
7. **Governance** (§9): private by default, an approval to share with the organization, versions, a kill switch, cost caps and audit.
8. **Rules and logic** (§3.7): formula fields, roll-ups, state machines and constraints, and logic modules as catalog actions.
9. **A typed object store** (§3.1): records with indexed fields, multi-record transactions and constraints, for ledgers, BOMs and large volumes.
10. **First-party templates** (K3, K4): inventory, procurement, MRP-lite and the production schedule, built on Studio itself.

### 1.2 Non-goals

1. **No second runtime.** Workflows compile to MAF through the built engine (ADR-028). A Studio app adds no second workflow engine, task store, chat, shell or approval path.
2. **No app-local look.** A package names no colour, font or pixel size. The renderer owns the look (`DESIGN_SYSTEM.md`).
3. **No raw credentials.** A package names a connection, never a key. The gateway resolves the credential at run time (D4).
4. **No widening.** An app never shows a viewer a record that the viewer cannot open in its owning app (D12, D128.3).
5. **No cross-company marketplace in this spec.** Templates are first-party. Sharing between organizations needs its own spec.
   A `system` install from reviewed repo code is not a marketplace (D187, `agent_sandbox.md` §6.8).
6. **No agent creator.** A member builds a skill that Mira loads, not a second assistant (D125, D141).
7. **No member-authored code on the box.** A member's logic module (B5) runs only on the off-box sandbox (D127.2, D130). A custom screen (L1) compiles and runs only in the browser of the member and of the viewer (D202.4). First-party logic (B4) is reviewed repo code, like any gateway route.
8. **No general ledger.** Accounting stays in Tally and Zoho Books. A Studio app posts to them through connectors.

---

## 2. The four shapes

The owner named three use cases on 2026-10-31. A fourth, the skill alone, is the cheapest, so this spec adds it. One package model covers all four. A shape is only which layers a package fills.

| Shape | Layers | Example | Who starts it |
|---|---|---|---|
| **S0 · Skill** | skill | "Write quotes in our format from the deal and the price list" | Any member, in chat or in Studio |
| **S1 · Workflow** | workflow (+ actions) | "Every morning, send overdue invoices to the customer on WhatsApp" | A member with `feature:studio` and `feature:workflows`. The workflow API reads `feature:workflows`, and J1-3 adds `feature:studio` to each write (§9 item 9) |
| **S2 · App** | objects, views, workflows | "A board and a calendar of site visits, with a phone check-in form" | A member with `feature:studio` |
| **S3 · App with a skill** | all five | S2, plus "Mira, log my visit at ACME" works in chat and on WhatsApp | A member with `feature:studio` |

Two more forms reuse the same model:

- **Connector app.** Views and actions over one connection, with no workflow. Example: an invoice list over Zoho Books, which Metorite has no screen for (slice I2).
- **Pinned answer.** A chart or table that Mira made in chat, kept as a page (slice M1).

### 2.1 Use-case catalog

The catalog seeds the templates (K1) and the builder eval (G6). Each row names its shape and the blocks it needs.

**Sales and customers**

| # | Use case | Shape | Blocks |
|---|---|---|---|
| U1 | Pipeline digest to the leadership each Monday | S1 | — |
| U2 | Web form lead → dedupe → CRM lead → owner → first task | S1 | — |
| U3 | Quote writer in the house format | S0 | — |
| U4 | Sales visit log: "Mira, log my visit at ACME" | S3 | form, table, map |
| U5 | AMC and contract renewals at 30, 15 and 7 days | S1 | — |
| U6 | Customer onboarding playbook, one checklist per customer | S2 | checklist, board |

**Service and operations**

| # | Use case | Shape | Blocks |
|---|---|---|---|
| U7 | Field service visits: plan, assign, check in with photos, close, feedback | S3 | board, calendar, form, map |
| U8 | Support desk: mail and WhatsApp intake, queue, SLA timers | S3 | table, board, detail |
| U9 | Spares and inventory, with a reorder workflow at a threshold | S2 | table, stat, form |
| U10 | Production schedule: print and assembly jobs per machine | S2 | timeline, board |
| U11 | Quality log (NCR and CAPA) with an investigation checklist | S2 | form, checklist, table |
| U12 | Asset register with a maintenance calendar | S2 | table, calendar |
| U13 | Tower or site permits with due dates and documents | S2 | table, timeline, detail |

**Finance and admin**

| # | Use case | Shape | Blocks |
|---|---|---|---|
| U14 | Overdue invoice chase on WhatsApp, with a finance digest | S1 | — |
| U15 | Purchase requests with an approval chain by amount | S3 | form, table, detail |
| U16 | Expense claims: receipt photo → fields → approval → export | S3 | form, table |
| U17 | Vendor onboarding and KYC checklist | S2 | checklist, form |
| U18 | GST and HSN lookup through an MCP server | S0 | — |

**People**

| # | Use case | Shape | Blocks |
|---|---|---|---|
| U19 | Hiring pipeline: candidates, interviews, scorecards, offer approval | S2 | board, calendar, form |
| U20 | Leave requests: "I am off tomorrow" → card → manager approval | S3 | form, calendar |
| U21 | HR policy answers from Knowledge, with a hand-off to a person | S0 | — |
| U22 | OKRs as a mind map, with a weekly check-in workflow | S2 | mind map, form |

**Plumbing**

| # | Use case | Shape | Blocks |
|---|---|---|---|
| U23 | Nightly Google Sheet → records sync | S1 | — |
| U24 | Stuck-task nudge: no update in five days → a note to the assignee | S1 | — |
| U25 | An invoice screen over Zoho Books, or a ledger over Tally | Connector app | table, detail |
| U26 | Any MCP server the organization adds → a generated app over its tools | Connector app | table, detail, form |

### 2.2 Patterns and industries (D202)

The use-case brainstorm of 2026-11-08 (task 2326) read 12 industries. The owner answered "looks good" in chat on 2026-11-08 (D202.5). §2.1 holds the use cases by team, and §20.7 holds the apps that serve people outside the org. This section repeats neither of them.

**Thirteen patterns cover the apps that a company asks for.** Each pattern is a template that Mira can start from. No block carries the name of an industry, so an industry gets a template and no new code.

| # | Pattern | What it does | Examples |
|---|---|---|---|
| P1 | Tracker | A thing moves through stages | Shipment, ticket, NCR, RFI, sample |
| P2 | Guided procedure | A person does steps in order | Assembly, installation, an SOP run, an R&D process |
| P3 | Register | A list of things that the org owns or watches | Assets, sites, suppliers, safety data sheets, permits |
| P4 | Request and approval | A person asks, and another person approves | Buy request, leave, change control, quote |
| P5 | Inspection or audit | Checks, scores and photos, with findings as tasks | Vehicle check, shelf audit, safety walk |
| P6 | Schedule | Slots, rounds, shifts and batches | Dock slots, maintenance rounds, classes |
| P7 | Notebook | A record of work that locks after a sign-off | Lab notebook, site diary, case notes |
| P8 | Controlled documents | Revisions with an approval | SOPs, procedures, specifications |
| P9 | Calculator or configurator | Inputs and rules give a price or a quantity | Freight quote, formulation, BOQ, cost roll-up |
| P10 | Link for outsiders | A person outside the org sees or sends one thing | Tracking link, supplier form, QR report, client review |
| P11 | Dashboard or monitor | Numbers and alerts over records | OEE, impact, cold chain, schemes |
| P12 | Screen over another system | Views and actions over a connection | Tally, Zoho, any MCP server (U25, U26) |
| P13 | Diagram or map | Shapes and places that link to records | Process chart, plant layout, routes, lot tree |

**The industries.** Each row names the first apps and the patterns that they use. §14 holds the blocks.

| Industry | First apps | Patterns |
|---|---|---|
| Logistics and transport | Shipment tracker, fleet and driver log, dock and yard slots, freight quote, exception desk | P1, P2, P6, P9, P10 |
| Manufacturing | Assembly guide, process charts, job traveller, BOM and routing, NCR and CAPA, downtime and OEE | P1, P2, P9, P11, P13 |
| Chemicals, pharma and R&D labs | Lab notebook, formulation sheet, sample and batch tracking, stage gates, process documents, safety data and risk library, deviation and change control | P1, P2, P3, P7, P8, P9 |
| Construction, real estate and EPC | Daily site report, snag list on drawings, RFI and submittal log, BOQ and progress bills, permit to work | P1, P3, P4, P5, P9 |
| Telecom, energy and field sites | Site survey, installation and commissioning, maintenance rounds, solar EPC pipeline, fault tickets (U8, U13) | P1, P2, P5, P6 |
| Retail, distribution and FMCG | Beat plan and shop visits, order capture on the phone, merchandising audit, scheme tracker | P5, P6, P9, P11, P13 |
| Food and agriculture | Farmer and supplier register, buying at collection centres, lot traceability, cold-chain log | P3, P9, P11, P13 |
| Clinics and diagnostics | Patient intake and appointments, sample to report, calibration register. Task 2327 holds the full clinic study | P1, P3, P6, P10 |
| Education and training | Batches and attendance, assessments and certificates, training matrix | P3, P5, P6 |
| Professional services and agencies | Engagement tracker, compliance calendar, time sheets and bills, client review by link | P1, P4, P6, P10 |
| Facilities and hospitality | Work orders from a QR on each asset, maintenance and housekeeping checklists, visitor check-in, event run sheet | P1, P5, P6, P10 |
| Non-profit and CSR | Beneficiary register and field surveys in local languages, grant milestones, impact dashboard | P3, P5, P11 |

**How Studio stays flexible.** Seven rules keep one set of blocks useful in every industry.

1. **Generic blocks, and the industry in a template.** A formulation sheet is an editable grid, line items and a "sums to 100 %" rule. It is not a chemistry block.
2. **One way down at each layer.** Field, then formula, then rule, then logic module. Block, then page layout, then custom screen. Built-in connector, then MCP server, then code. A member goes down a level only when the level above cannot do the job.
3. **One door for every read and write.** So each new block gets permissions, approvals, the audit row, chat and the MCP with no extra work.
4. **An app is data.** Mira can read, diff, undo and template a manifest.
5. **Link, never copy.** Tasks stay in Projects, people in People, and documents in Knowledge. An app points to them.
6. **One look, few words.** A member sees App, Record, View, Step and Run.
7. **Chat parity.** What a screen can do, Mira can do too (F10).

---

## 3. The model: one package, five layers

A **package** is the unit that a member builds, versions, shares and installs. In the UI it is an **app**. A package holds up to five layers. A shape (§2) is the set of layers it fills.

```text
            ┌──────────────── a Studio package ────────────────┐
  people →  │  Views     board · table · calendar · form · …    │ → page, chat card, phone
  Mira   →  │  Skill     when to use · instructions · cards     │ → a tool pack per turn
            │  Workflows triggers → steps → approvals           │ → the built MAF engine
            │  Actions   one catalog: read · write · send       │ → app doors, connections, MCP
            │  Objects   typed records, tenant-scoped           │ → studio_records (FORCE RLS)
            └───────────────────────────────────────────────────┘
```

**The rule that holds it together: every layer above Objects reaches data only through an action.** A view reads through an action. A form submits through an action. A workflow step is an action. A skill's tools are actions. The MCP door serves actions. So one permission check, one approval path and one audit row cover every way into an app.

### 3.1 Objects

An object is a typed record kind that the package owns: `visit`, `claim`, `asset`. It has fields of these types: text, long text, number, money, date, datetime, checkbox, select, multi-select, person, relation, file, url, email, phone and geo.

- **Storage (D146, STU-Q5).** Records live in a **typed object store**, not in `app_data`. An object registry holds each object and its fields. Records are JSONB rows on shared tables with FORCE ROW LEVEL SECURITY, and no tenant gets its own DDL. Hot fields get indexes within a per-org budget. `app_data` stays the store of `window.cc` storage for a code-format app (L2).
- **What C1 built** (migration `323_studio_objects.sql`). `studio_objects` holds an object of a package (an `apps` row). `studio_object_fields` holds its fields, and `studio_records` holds its records. `studio_record_hot` is the index side table. It holds one row for each value of a hot field, under three fixed btrees that every org shares. Each foreign key is a pair with `organization_id`, and each one cascades, so a deleted package leaves no record.
- **The rules of a value** are in one module, `acb_common/studio_fields.py`. The A2 validator and the store call it, and PKG-13 will too. A number, a money amount and a coordinate stay below 10^15, with no exponent. Money is `{"minor": <whole number>, "currency": "INR"}`. A datetime is kept in UTC. A person is a lower-case member email. A relation and a file store `{"id": ...}` only.
- **The caps.** An object holds at most 8 hot fields and 60 fields. An org holds at most 24 hot fields and 200 objects. A package holds at most 50 objects. A record holds at most 64 KiB of JSON. A field turns hot only on an object of at most 50,000 records, and the store fills its rows in the same transaction.
- **The org caps (C10a).** An org holds at most 1,000,000 records and 2 GiB of record JSON (`MAX_RECORDS_PER_ORG` and `MAX_RECORD_BYTES_PER_ORG` in `studio_fields.py`). The gauge table `studio_org_usage` holds one row for each org: `records` and `bytes`. Three statement triggers on `studio_records` keep it exact for each writer, also for a purge, a cascade and raw SQL. The store reads the row under the org lock before and after each batch. It refuses a batch only when the org is past a cap and the batch raised that count. So a delete and an update that makes a record smaller always pass. The gauge counts the JSON of a record only, and a history row does not count. The owner decides that in C10b. The gauge moves no credit and is no ledger, so D19.3 and D32.5 do not apply.
- **Multi-record writes and constraints (C1b).** `objects_store.apply_writes` writes several records in one SAVEPOINT, so a stock move writes both ledger rows or neither. Rules run on each write, for example "stock never goes below zero". Six kinds: `range`, `unique` and `group_sum_min` (C1b), `state_machine` (C6), `formula` (C7a) and `rollup` (C7b). `acb_common/studio_rules.py` holds the grammar, and §14 C1b holds the rules. The exclusive org lock serializes all record writes of one org. That is a known limit, and a finer lock is a later slice. A wait for that lock stops at 1 s with `store_busy`. That limits how long the writers of one org delay other tenants, and it does not end the delay. §14 C1b holds the numbers.
- **The aggregate (C2b).** `objects_store.aggregate_records` groups the records of one object by one hot field, and takes a count, a sum or an average of each group. It uses the grammar, the guard and the errors of C2a. §14 C2b holds the rules.
- **The query (C2a).** `objects_store.query_records` reads one page of the records of one object, with a filter and a sort. `acb_common/studio_query.py` holds its grammar. A filter or a sort on a hot field reads `studio_record_hot`. `v_text` holds the first 256 characters only, so the store checks a match of 256 characters or more against `data` too. §14 C2a holds the rules.
- **A write outside the store.** A raw SQL write that skips `objects_store.py` can leave a stale hot row. The one-writer fence reads the Python under `apps/` and `packages/` only, so it does not see a script or a manual statement.
- **Relations reach built-in records.** A field can point to a task, a project, a CRM record or a person. The relation stores the id only. A read of the related record goes through its owning app, as the viewer, so a relation never shows what the viewer cannot open.
- **A package may own no objects.** A connector app or an S1 workflow reads and writes other apps through actions only.

### 3.2 Actions — the one catalog

An action is one typed capability: an id, a title, an input schema, an output schema, a **class** and a source. The class vocabulary is the D136 manifest's, with no second one:

| Class | Meaning | How it runs |
|---|---|---|
| **A** | A read | At once, as the viewer |
| **B** | A write that the app can undo | On the member's approval card (D124), or with no card when a D131 trust level allows it |
| **C** | A write that is hard to undo, or a send to an outside person | On the member's card, every time |
| **X** | On neither door | Never from a package |

**Sources of actions** (slice B1 lists them, and no package invents one):

1. **App manifests** (`acb_common/app_manifests/`). Every app whose door is `ops` serves its door ops: Knowledge today, then CRM, Email and the rest as MCP-P2 lands. Projects has a header only (`door = "tools"`, no routes), because its rows live in `skill_projects/manifest.py`. A later slice adds them.
2. **The workflow catalog** (`routes/workflows/catalog.py`): the integration actions of `acb_skills/integrations.py` and the specialist agents. `catalog_rows` holds no agent row, and the catalog is not the seam for module actions (WFC-2 cleans it).
3. **The organization's MCP servers** (`connections/mcp.py`). Each tool becomes an action. A tool is class A when `mcp_needs_confirmation` says it needs no confirmation and its server has no `always_confirm`. Every other tool is class B, behind `McpPermissionGate` (B1b-1 holds the one rule).
4. **Published workflows.** A workflow is one action with its trigger input as the schema.
5. **The package's own objects:** list, get, create, update and delete for each object, plus each named action in the manifest.

**The class of an object action (B2a).** A list and a get are class A. A create and an update are class B, because the app can undo them. A delete is class C. `acb_common/studio_manifest.py::OBJECT_OP_CLASSES` is the one source of these classes, and a test holds its verbs equal to the `uses` grammar of A2. The B1 catalog (`catalog_rows`) gives the class of each other source. The door reads no class from anywhere else.

**Why one catalog.** Today five consumers each keep their own list: the workflow palette, the `window.cc` bridge (`tools.call`), the `app_<slug>_<action>` agent tools, the chat tool packs and the MCP door. A catalog keyed by the D136 manifest serves all five. A new app joins all five when it ships its manifest.

### 3.3 Workflows

Studio uses the built engine (`gateway/routes/workflows/`, D6, ADR-028). It compiles a graph to MAF, runs five trigger kinds and pauses on approval and wait nodes. Studio adds:

- **Studio triggers** (E3): a record is created or changes, a form is submitted, a button is pressed, or Mira runs it from a skill.
- **A human step** (E4): the step makes a task in the one task store (`pm_tasks`) for a person, then waits until the task is done. A **playbook** is a workflow whose steps are mostly human steps. It needs no second engine.
- **Catalog nodes** (E5): any action of §3.2 is a node, including an MCP tool.
- **Fan-in and loops** (E2): `workflows_app.md` §8.3b and §8.3c, unchanged.

⚠️ **An unattended run must bind its tenant first.** Today the event, schedule and webhook runs do not (`workflows_app.md` header, HANDOFF H-328, H4 slice 6c). No S1 workflow goes live on production for a second organization until that lands.

### 3.4 Views

A view is one block (§5). A page lays out views, in the optional `pages` list of the manifest (STU-D11a). The block of a view names a `source` (an action, or an object query) and a field mapping. The renderer owns loading, empty and error states, so no manifest writes them.

The same view renders in four places: a full page, a side panel, a chat card and a phone screen (§4).

### 3.5 Skill

A skill tells Mira when and how to use the package. It holds:

1. A **description**: when to use it. It works like the `description` of an Agent Skills `SKILL.md`.
2. **Instructions**, in Markdown.
3. **Actions**: the subset of the catalog that becomes Mira's tools for the turn.
4. **Cards**: which views Mira may render in chat.
5. **Knowledge**: links to Knowledge pages, read through `search_company`.
6. **Examples and tests**: prompts with the expected action and its arguments (F4).

At run time a published skill becomes a **tool pack** (WS-67): it loads only when a turn needs it, so a hundred skills cost no context until one is used. A skill can also stand alone, with no app (shape S0).

### 3.6 The package manifest

One JSON document holds the package. Slice A2 writes its schema and its one validator. The example below shows the shape, not the final schema. It names only a registry connection and no `integration:` action, because §8.1 rule 10 holds until IN-3.

```json
{
  "studio": 1,
  "id": "field-visits",
  "name": "Field visits",
  "purpose": "Plan, run and close customer site visits.",
  "team": "service",
  "connections": ["mcp:tally"],
  "objects": [{
    "name": "visit",
    "fields": [
      {"key": "customer", "type": "relation", "to": "crm.account", "required": true},
      {"key": "engineer", "type": "person"},
      {"key": "when", "type": "datetime"},
      {"key": "status", "type": "select", "options": ["planned", "on site", "done"]},
      {"key": "site", "type": "geo"},
      {"key": "photos", "type": "file", "many": true}
    ]
  }],
  "actions": [
    {"id": "close_visit", "uses": "object:visit.update", "set": {"status": "done"}},
    {"id": "log_visit", "uses": "crm.log_activity"}
  ],
  "views": [
    {"id": "board", "block": "board", "source": {"object": "visit"},
     "group_by": "status", "card": {"title": "customer", "meta": ["engineer", "when"]}},
    {"id": "week", "block": "calendar", "source": {"object": "visit"}, "start": "when"},
    {"id": "check_in", "block": "form", "object": "visit",
     "fields": ["status", "photos"], "submit": "close_visit"}
  ],
  "workflows": [
    {"id": "after_visit", "trigger": {"record": "visit", "when": {"status": "done"}},
     "graph": "workflows/after_visit.json"}
  ],
  "skill": {
    "description": "Use for site visits: plan one, find one, check in, close one.",
    "instructions": "Ask which customer first. Show the board card. Close a visit only on the member's card.",
    "actions": ["object:visit.create", "close_visit"],
    "cards": ["board", "check_in"]
  },
  "shell": {
    "jobs": [{"verb": "Check in to a visit", "view": "check_in"}],
    "needs": [{"object": "visit", "where": {"engineer": "$me", "status": "planned"}}],
    "search": ["visit"]
  }
}
```

### 3.7 The four layers (D146)

The owner chose the layered model on 2026-10-31 (STU-Q1). The layers grow in power, and each one is contained more tightly than the one before it.

| Layer | What it covers | Containment | Slices |
|---|---|---|---|
| **1 · Manifest** | Objects, relations, product lines, views, workflows, permissions | Validated data. No code | A2, C1, D, E |
| **2 · Rules** | Formula fields, roll-ups (stock on hand is the sum of the moves), state machines (a PO goes draft → approved → sent), constraints | A closed JSON tree with no I/O, no loops and a step cap | C6, C7a, C7b |
| **3 · Logic modules** | Computation: an MRP run, a cost roll-up, reorder suggestions, a schedule | A typed function that the catalog serves as an action. It gets its input through the door and returns **proposed** writes. The platform applies them in one transaction, with an audit row and the member's card for class B or C | B4 first-party, B5 member-authored |
| **4 · Custom UI block** | A shop-floor screen, a barcode scanner, a 3D scene. D202 calls it a custom screen | Code from the UI kit, in a sandboxed frame in the viewer's browser, with the `--cc-*` tokens only. Every call goes through the door (§5.4) | L1, L4 |

**Two kinds of logic module.** A **first-party** module (B4) is reviewed code in the repository, written by Metorite engineers. The MRP run of template K4 is one. A **member-authored** module (B5) runs only on the off-box sandbox, after SBX-9, because D127.2 keeps member code off the box. The workflow Module Studio (`workflows_app.md` §5) keeps its own scope and does not grow into this layer.

**A member tool is a B5 module (D187).** A tool that the sandbox builds from the chat is a B5 module in a package (`agent_sandbox.md` §7). Where §19 names `tools:approve`, read `studio:publish`.

**A worked example: inventory, procurement and MRP (templates K3 and K4).**

- **Objects:** item (product line, unit, reorder point), location, stock move (the ledger), BOM and BOM line, vendor, purchase request, purchase order, work order, machine.
- **Rules:** stock on hand as a roll-up, a purchase order above a set amount goes to a director, no negative stock.
- **Logic:** the nightly MRP run explodes the BOMs, nets demand against stock and open orders, and proposes planned purchase orders and work orders.
- **Workflows:** planned orders go for approval. An approved purchase order goes to the vendor by mail and to Tally. A goods receipt writes stock moves and then a QC human step.
- **Views:** a stock dashboard, a purchase order board, a production timeline by machine, a BOM tree, a phone receipt form.
- **Skill:** "What is short for next week's production?" and "Raise a PO for 200 nozzles from X."

**The validator is the safety line.** It refuses an unknown block, an action that the catalog does not serve, a class X action, a colour, a pixel size, a credential-shaped string and a field that a view names but the object does not hold. Each error carries a JSON path and one sentence that a model can act on, as `lib/charts/kinds.mjs` does for charts today.

---

## 4. Where a Studio app shows

A package joins the one shell. It builds no shell of its own (`navigation_shell.md`, D87 to D89, `AGENTS.md` rule 10).

| Surface | What the package gives | Seam |
|---|---|---|
| **Sidebar** | An entry for an installed app (H1) | `NavPane`, per-org entries. STU-Q3 sets where |
| **Page** | Its views, under `AppTopBar` | The one renderer (D1) |
| **The app's own rail** | Its pages and views, in a left rail under its app bar (§5.5) | `RailRow` and the `rail` prop of `AppTopBar` (AF-2) |
| **Chat** | Its skill, and its views as cards | WS-67 tool packs, `emit_generative_ui` |
| **Assistant dock** | The skill loads when the app page is open | `pane.agent` in `lib/shell/assistant.ts` |
| **⌘K** | Its jobs (verbs) and its records in Find | `lib/shell/registry.ts` `JOBS`, `/api/shell/search` |
| **Bell** | Its "needs you" items | `/api/shell/needs` |
| **Phone** | Each view at 390 px | The renderer's phone layout (D11) |
| **WhatsApp** | The skill works there too. Only delivery changes | The channel rule: a channel strips delivery tools only |
| **MCP** | Its actions, through the four fixed tools | D136, slice B3 |

---

## 5. Blocks

A block is one data-agnostic view component. It takes rows from a `source`, a field mapping, and a list of actions for its buttons. It never fetches by itself: the renderer resolves the source through the one read cache (`useCachedResource`).

### 5.1 What exists, and what it needs

| Block | Built today | Work to make it a block |
|---|---|---|
| **Table** | `DataGrid` in `genUITemplates.tsx` is generic. `projects/TableView.tsx` adds inline edit | Bind a source, add sort, filter and inline edit (D2) |
| **Form** | `formCard` template (8 field types), CRM `FieldInput.tsx` (field-typed) | One form from the §3.1 field types (D2) |
| **Detail** | `TaskPanel`, CRM `RecordSheet` (both coupled) | A generic record sheet (D2) |
| **Board** | `components/board/Board.tsx` and `BoardColumn.tsx` are generic frames | Group by a select field, drag → an update action (D3) |
| **Chart, Stat** | `lib/charts/kinds.mjs`: 13 kinds with a validator. `statDashboard`, `sparkTrend` | Feed it an aggregate query (D4) |
| **Calendar** | `calendar/TimeGrid.tsx`, `MonthGrid.tsx` (coupled to My Tasks) | Pull out to `components/calendar/` with a generic event shape (D5) |
| **Timeline (Gantt)** | `components/timeline/` holds the scale, the geometry, the arrow paths, the drag date maths, the grid chrome and `rowsToTimelineRows` (D6, built in part 2026-11-13). `projects/TimelineView.tsx` keeps the rows, the bars, the drag handlers and the menus | Wire the block to a Studio source, and move the bar and rail markup when a second app needs them (D6 follow-up) |
| **Process flow** | The workflow editor on `@xyflow/react` (coupled) | A read-only flow block that also shows run state (D8) |
| **Mind map** | — | New, on `@xyflow/react` with a tree layout (D7) |
| **Checklist** | — | New: playbook steps, each human step a task (D9) |
| **Map** | — | New. Needs a map library and tiles (D10, STU-Q8) |
| **Markdown, document** | tiptap 3, the `markdown` primitive | Read-only first |

**New blocks of D202.** The brainstorm of task 2326 and the clinic study of task 2327 add these rows. Each one reuses what exists before it adds code.

| Block | What exists to reuse | Work to make it a block |
|---|---|---|
| **Guided step player** | The Form block (`components/studio/StudioForm.tsx`), the checklist plan (D9) | One step on each screen, with media, checks and a sign-off, over a run of C16 (D14) |
| **Editable diagram canvas** | The workflow editor on `@xyflow/react`, the read-only flow (D8) | An editable canvas with swimlanes and a set of symbols. Each shape links to a record (D15) |
| **Image annotation** | `components/MarkdownImage.tsx` draws an image. Nothing places a pin | Pins on an image or a body outline, each pin a record (D16) |
| **Editable grid** | `DataGrid` in `genUITemplates.tsx`, the inline edit of `projects/TableView.tsx`, the formulas of C7a | Line items (C13) edited as a sheet, with a paste of rows (D17) |
| **Tree** | `components/AccessTree.tsx` draws the project tree of a grant | A generic tree over a hierarchy (C18), with lazy load (D18) |
| **Gallery, matrix, compare** | The Board block (D3) for cards, a group query (C2b) for cells | Cards with a cover image (D19). Rows by columns (D20). Records side by side, and a photo overlay (D21) |
| **Activity feed** | `projects/components/TaskPanel.tsx` shows the activity timeline and the comments of a task | One feed of comments, changes (C4) and the threads of the linked contact (D22, O19) |
| **Queue board** | The Board block (D3), auto-number (C15) | Tokens by counter, with a view for a TV and a view for the phone (D30) |
| **Capture fields** | The `geo` field (C1), `components/VoiceInputButton.tsx` (VOX-2), the `attachments` table | Photo, video, signature, scan and GPS inputs in a form and a step (D24, D25) |
| **3D model viewer, scene and figure** | Nothing. The app has no three.js today | A GLB viewer on three.js and react-three-fiber (D27), then records in a scene (D28) and a figure that reads records (D29) |
| **DICOM viewer** | Nothing | OHIF over an outside archive, health only (D31) |
| **Custom screen** | `SandboxedHtml`, `SandboxedReact`, and the kit `@cc/ui` (`lib/artifactUi.tsx`) | Code from the UI kit (L4), compiled in the browser (§5.4, L1) |

⚠️ **The Calendar and the Timeline pull-outs touch live apps.** D5 and D6 change no behaviour of the Calendar app or of Projects. Their own tests and a visual review prove that.

### 5.2 The renderer

The renderer is `components/GenerativeUINode.tsx`, grown, not a second one. Today it draws a `{type, props, children}` tree of 16 primitives and 19 templates, and its only interaction is `onAction(string)`, which becomes the member's next chat message. Slice D1 adds:

1. **Data binding.** A node may carry `source`. The renderer resolves it through `useCachedResource` and the catalog door (B2), and passes the rows to the block.
2. **Page state.** The selected record, the filters and the tab live in the URL, so a link opens the same view.
3. **Typed actions.** A button names a catalog action, not a sentence. In chat, a typed action still reaches the member's card.
4. **Lockstep.** `GENUI_NODE_TYPES` in `write_artifact.py` and the front end stay one list, under `test_genui_catalog_lockstep.py`. `source` and the typed action are props of existing nodes, so no set changes. `genui_refusal` refuses `source` in chat.

**Single owner.** `generative_ui_2.md` owns the catalog and the HITL rules of chat UI. D1 extends it and changes none of its rules. D1 closes its Phase 3 item "typed TEMPLATE_CATALOG" as test-held, not generated, and the same PR updates that spec's §3 and §6.

### 5.3 Layout

A page is a grid of blocks, with tabs and an optional side panel. At 390 px the grid becomes one column, and a block may name a phone form of itself (a board becomes a grouped list). §5.5 puts the pages in the app frame: a left rail of pages and views, tabs, record pages and dialogs.

### 5.4 The code block: custom screens from the UI kit (D202)

Some needs no block covers: a custom chart, a scanner, a bespoke calculator, a 3D scene. **A custom screen** covers them. It is TypeScript or TSX code that a member writes in the IDE (G8), from the Metorite UI kit (L4). The owner chose this on 2026-11-08 (D202.2 and D202.4).

- **The kit.** The code frame gets the same Board, Table, List, Tabs, Form, Detail and chart components that the app uses. They come from the same source files, with the same tokens and with TypeScript types. L4 grows the kit that exists, `@cc/ui` (`lib/artifactUi.tsx`), and adds no second kit.
- **The standard set.** React, the UI kit, the icons (`lucide-react`) and three.js (L5). A screen imports nothing else. A screen gets no package list. B8 packages serve server code only, after SBX-9.
- **Where it runs.** The viewer's browser runs it in the sandboxed frame of `SandboxedHtml`: `allow-scripts`, `connect-src 'none'` (§19.3, kind `browser`). The frame reaches data only through the `window.cc` bridge (`build/apps/lib/ccBridge.ts`). Each call goes through the catalog door as the viewer.
- **Where it compiles.** In the browser, never on the production box (D127.2, D202.4). Today chat artifacts compile on the server (`lib/compileArtifact.ts`, `app/api/artifacts/compile/route.ts`). The Studio path does not use that route for member code. L3 must prove the browser compile first. If it fails, custom screens wait for SBX-9.
- **Source only.** The server stores the source of a version, never a bundle that a browser sent. Each viewer's browser compiles the approved source, and keeps the output by the hash of the source. So the code that runs is the code that the reviewer read (§19.10 rule 15).
- **One React.** Today the app runs React 19.2.4. The artifact frame runs `preact/compat`, and the T2 vendor cache holds React 18.3.1 (§19.4). L4 picks one runtime for the kit and the frame, and a test holds them equal.

Slice L1 makes this path **one block inside a package**. The App Workshop path (`SandboxedHtml`, `SandboxedReact`) is its base, not a second frame. Server code is a logic module (B5). It runs on the off-box sandbox only, after SBX-9 (D127.2).

### 5.5 The app frame: the left sidebar, tabs, record pages and dialogs (D219)

The owner asked for this on 2026-11-14 (task 3146). A Studio app opens in the same frame as a built-in app, such as the CRM or Knowledge. It has an app bar, its own left sidebar, pages, tabs, record pages and dialogs. The manifest declares the frame, and the app writes no frame code. The frame adds no second component of any kind.

**The left sidebar is the app's own rail, not the shell sidebar.** The shell sidebar holds one entry for the installed app (H1, PKG-8). The rail of the app sits under its app bar, as `CrmRail` does in the CRM.

| Part | What a member sees | What the manifest says | What it reuses |
|---|---|---|---|
| **App bar** | The icon and the name of the app, the scope line, the actions of the app ("New visit") and the rail toggle | `title`, `icon`, and the actions of the app or of a page | `AppTopBar` and its `rail` prop (`components/AppTopBar.tsx`) |
| **Rail** | Sections of rows. A row opens a page or a saved view, with an icon and a muted count | `nav`: sections, each with a title and its rows. A row names a page, or a view with a filter | `RailRow` (`components/ui/RailRow.tsx`) and `useMobileDrawer` (`components/AppShell.tsx`). `app/crm/components/CrmRail.tsx` is the model |
| **Page** | A grid of blocks, with an optional side panel on the right | `pages` (D11a) | `StudioPage` (D11b) |
| **Tabs** | Underline tabs over the cells of a page | The `tabs` of a page (D11a) | `Tabs` (`components/Tabs.tsx`) |
| **Filter bar and layout switch** | Search, filters, sort and group over a table or a board. A switch between the layouts that a view allows | The `filters`, `sort`, `group` and `layouts` of a view | The pure model of the CRM filter bar (`app/crm/lib/filterBar.ts`), which AF-4 lifts to a shared module. `ViewTabs` of Projects is the model of the switch |
| **Record page** | A record opens in the side panel or as a full page, with its own tabs: Details, Related, Files, and later Activity | The `record` of an object: its tabs and its related lists | The Detail block (D2). `app/crm/components/RecordShell.tsx` and `RelatedLists.tsx` are the model. The activity feed (D22) when it is built |
| **Dialogs** | "New visit" opens the form in a dialog. A delete asks once | A typed action (D1) that opens a form | `Modal` and `ConfirmDialog` (`components/ui/`), and the Form block (D2) |
| **Sections** | A page header with one sentence and a count. Groups of cells that fold | The `header` and the `sections` of a page | `PageHeader` and `Collapsible` (`components/ui/Collapsible.tsx`) |
| **Views that a member saves** | A member saves a filter as a view, and the view shows in the rail | Nothing. The member makes it | The shared seam `lib/savedViews` and `components/ViewChips.tsx`. The Studio store is new (AF-10a), and the front end is AF-10b |
| **Phone** | The rail opens in the drawer. The tabs scroll in one row. The side panel is a bottom sheet | Nothing more | `useMobileDrawer`, `ScrollStrip`, and the `Modal` sheet of D11b |

**The rules.** Each rule binds when its slice merges, and until then it is advisory (R7).

1. **One frame, from the parts that exist.** `components/studio/StudioAppFrame.tsx` composes `AppTopBar`, `RailRow`, `StudioPage` and `useMobileDrawer`. It adds no second rail, tab bar, dialog or filter bar. Fences: `src/lib/railRows.test.ts` (the new rail joins `RAILS`), `src/lib/shell/appBar.test.ts` and the conformance suite (AF-2).
2. **The manifest declares, and the app draws nothing of its own.** `nav`, `record`, `header`, `sections` and the view parts join the A2 model. The validator refuses a colour, a pixel size and an icon outside the icon list (D12). Fence: `tests/unit/test_studio_manifest.py` (AF-2).
3. **Each part has a default.** A manifest with no `pages` gets one page for each object, and a dashboard page when it holds a chart or a stat view. A manifest with no `nav` gets one rail row for each page. An object with no `record` gets Details, a Related tab for each `relation` field that points to it, and Files when it has a `file` field. So an app that was built before D219 gets the frame with no edit. Fences: `pageModel.test.ts` (AF-1) and the rail model test (AF-2).
4. **The URL holds the state.** The page, the tab, the record, the filters and the layout live in the URL (`lib/studio/pageState.ts`). A link opens the same place, and Back works. Fence: `usePageState.test.ts` (AF-1).
5. **The rail shows only what the viewer can open.** A row stays hidden when its page reads no object that the viewer can read (C8). The door still checks each read (B2), so the rail is display only. Fence: the rail model test (AF-3).
6. **A count costs one bounded read.** A rail row may show a count, with `count: true`. The count does not come from the C2b aggregate, because the door serves `aggregate` to the owner only and ignores row rules. The client reads the page view through the door `list` op with `limit: 1` and takes its `total`. That read applies the row rules of the viewer. It keeps the cost guard of C2b (5 s, and the small-object rule). `RailRow` draws the count in muted text. A count of items that wait for the member takes the attention tone of `RailRow`. The filter and the sort stay on the view of the page (`source.args`), never on the row. A rail has at most 8 counted rows (`MAX_NAV_COUNTS`), and a counted row needs a page with a table or board view. Fence: the validator caps the counts and checks the page (AF-3a). The owner may still change the count source (open question on task 3151).
7. **A custom screen sits inside the frame.** A code screen (L1) fills a cell of a page. It never draws its own rail or app bar. Fence: the L1 tests.
8. **Mira writes the frame.** `studio_describe` knows `nav`, `pages` and the view parts. The G1 skeleton leaves `nav` and `pages` absent, so the defaults apply. `record` is not yet a manifest key. The IDE edits them in a Pages branch of the Explorer (AF-8).
9. **It looks like the CRM.** The visual review of each frame slice puts the app beside the CRM. The rail width, the toggle, the app bar and the drawer match at 1440, 1024, 768 and 390 px. The review uses light mode, compact density and a changed accent.

**The caps.** 8 pages (D11a), 4 rail sections, 12 rows in a section, 8 tabs on a page, 6 tabs on a record, 4 related lists on a record, and 8 counts on a rail. `pageModel.ts` and the `maxItems` of the schema hold the same numbers.

**Standard page layouts.** `studio_layouts.md` (draft, task 3312) owns the layout presets of a page, the manifest keys `layout` and `regions`, and the shared states. This section keeps the frame.

**Where the blocks are.** The frame holds the blocks of §5.1. The rich blocks (calendar, timeline, gallery, tree, editable grid, diagram, 3D and the rest) stay rows D5 to D31. The frame adds none.

---

## 6. The builder

### 6.1 Ways in

1. **From any chat.** "Build me a tracker for site visits." Mira starts the interview there and opens Studio when the plan is approved (chat-first).
2. **From Studio.** A describe bar on the empty library.
3. **From a template** (K1).
4. **From a finished chat task** (SKL-1): "Save this as a skill?" or "Do this every week?"
5. **From a connection** (I2): "Make an app over Zoho Books."

### 6.2 The build loop

1. **Interview.** Mira asks at most five questions: who uses it, what they record, what happens next, who approves, which systems it touches. She asks only what the request does not say.
2. **Plan card.** One card shows the objects, views, workflows, skill and connections, and the cost of the build. The member approves, edits or stops.
3. **Generate.** Mira writes the manifest through the builder tools (G1). The validator runs after each change.
4. **Preview.** The app runs on sample records. Writes go to a preview store, and sends are simulated (G4).
5. **Refine.** Each request shows a diff of the manifest ("adds the field `site`, adds the view `map`") before it applies. One step undoes it (G5).
6. **Test.** The skill's tests run (F4). The workflow runs once on sample data.
7. **Publish.** The member publishes a private version, or asks to share it (§9).

### 6.3 The Studio IDE

**Owner decision, 2026-11-06, in chat (D193).** The owner tried Studio, then "Build it". The owner expected a contained IDE like VS Code, as in the old App Workshop. The owner chose "New Studio IDE".

"Build it" opens a full-page IDE. The Explorer tree of the parts of the app is on the left. The Preview, Data, Views and Code tabs are in the centre, with the problems below. Mira is on the right and works on THIS app only.

A simple app uses Data and Views. A complex app adds the other branches of the same tree. The old Workshop agent no longer runs, so the IDE uses the Studio engine: manifest packages, D146.1.

```text
┌ Studio › Metorite Train ─────────── Draft · Open app ────────┐
│ EXPLORER         │ [Preview][Data][Views][Code] │ Mira         │
│ ▾ Metorite Train │                              │──────────────│
│  ▾ Data          │   (live preview of the app)  │ the shell's  │
│     Milestones   │                              │ dock, with   │
│     Stations     │  Table · Board               │ the facts of │
│  ▾ Views         │                              │ THIS app     │
│     Train track  │──────────────────────────────│              │
│  ▸ Actions       │ PROBLEMS  0 errors 1 warning │              │
│  ▸ Rules         │                              │              │
│  ▸ Workflows     │                              │              │
│  ▸ Code blocks   │                              │              │
└──────────────────┴──────────────────────────────┴──────────────┘
```

**The route** is `/studio/build?pkg=<id>`. It is under `/studio`, so the access gate of `/studio` covers it. It is no nav pane. An org outside `STUDIO_ORGS` sees an empty state. The page reads the DRAFT manifest from `GET /studio/packages/{id}`, so it needs no new route.

"Build it" and "New app" make a draft package first. They then open the IDE, and open the dock with the brief in the box. A library card and the app page have an "Open in Studio IDE" link.

**The Explorer** is a tree that `lib/studio/buildOutline.ts` makes from the draft. It holds the app, then Data (each object, with its fields), Views, Actions, Rules, Workflows, Skill and Code blocks. A branch with nothing shows "None yet". So a member sees what a complex app can hold. The manifest has no code-block part yet, so that branch lists the entries of `contributes`. A click on an object selects it in the tabs.

**The tabs** are Preview, Data, Views and Code. Preview draws each object as a Table, and as a Board when it has a select field to group by. The rows are SAMPLE rows from the `sampleRows` module, and a door refuses every write. The page says "Sample data. Nothing is saved."

A toggle sets the frame to 390 px. Data, Views and Code are read only in G3a and editable since G3c (below). The Code tab shows the draft JSON in the Monaco editor of the Workshop.

**The Problems panel** shows errors and warnings with a count. No gateway route runs the validator on a draft. So G3a runs structural checks in the browser (`lib/studio/draftProblems.ts`). The panel says that the full check runs at a save and at a publish.

**The chat** is the dock of the shell. The dock agent is `orchestrator` on every page (D99). `studio-builder` only owns the `studio` pack. So the IDE mounts no `AgentChat` (rule 10, `seams.test.ts`).

It binds the dock to the package with `useAssistantFacts`. The facts hold the package id, the `based_on` hash of the draft (`manifest_hash` of `GET /studio/packages/{id}`) and the draft manifest. The manifest sits between "Begin member data" and "End member data" marks, and says that it is data and never instructions.

This page puts a stored body in the facts on purpose. `studio_package_get` returns no manifest, and Mira needs the draft to edit it. The page pastes the draft up to 24,000 characters, the same cap as a save. So any draft that a chat may save is also in the facts, and Mira can edit it. The cost is about 6,000 tokens a turn at the cap, while the IDE is open. A larger draft shows a line that says Mira cannot edit it and to start a new, smaller app.

A save with a stale `based_on` is refused and nothing is written, so a stale fact fails closed.

The page gives the facts only while the app exists. After a 404 it gives none, and the polling stops.

"Build it" holds its brief in a one-shot holder keyed by the package id, then opens the IDE. The IDE sends the brief as an ask of the shell after the first draft loaded and the facts are published. This order stops a second package from being made.

A rule of the `studio` pack tells Mira to edit the package that the facts name. The page reads the draft again every 5 s while it shows, and when a chat run ends. It redraws only when the draft changed. The app nav folds while the IDE is open, only when the member allows the auto fold. The page marks its fold in `sessionStorage`, so a reload keeps it. On leave it opens the nav again, except when the dock rule holds it folded.

**Direct edits (G3c).** One bar above the tabs holds the mark "Unsaved changes", Save and Revert. The tabs draw the edit. The facts of the dock keep the stored draft.

The Data tab adds, labels and deletes an object. It adds, labels, retypes and deletes a field. It changes the required flag and the options of a select field. It offers only the types that need no more than a label.

The Views tab adds a Table or a Board, changes its title and its object, and deletes it. The Code tab edits the JSON. A text that does not parse is refused in the page, with its line.

**One save.** Every save is `PUT /studio/packages/{id}/draft?based_on=<hash>`, the route of `studio_save` (`lib/studio/draftSave.ts`). The hash is the `manifest_hash` that the edit began from. A 409 says that Mira or another tab saved the draft. It says that nothing was saved and that the edit stays, and it offers Reload. A 422 puts each error in the Problems panel at its path.

The poll never replaces an unsaved edit. It shows "A newer draft exists" instead. Leaving with an edit asks first (`useLeaveGuard`).

After a save the page adopts the answer. So the facts carry the new hash, and the next save of Mira uses it. The route adds the record actions of each object, by the rule of `studio_save`. It names them in `record_actions`.

**What a delete does.** A delete changes the draft only. It also removes what names the item. These are the actions and views of an object, the page cells of a view, and the name of a field in the views. A relation field that links to an object blocks the delete of that object.

At publish, `sync_objects` refuses a published object or field that the manifest no longer holds. The answer is 409 `objects_out_of_sync` with "A published object cannot go yet". It refuses a change of type, required flag, options or label of a published field too. The live app and its records stay as they are. The dialog says this for a live item. For an item never published, it says that the item only leaves the draft.

**Reuse of the Workshop (G3c).** The Monaco editor, its theme and its save key are now `components/CodeEditor.tsx`. The Workshop page uses it too. The mark of an unsaved buffer is `components/UnsavedMark.tsx`, in both. The leave guard is the one of the skill form.

The Workshop checkpoints (`/api/apps/{slug}/checkpoints`) are git commits of files. They do not fit the manifest draft. G5 undo can reuse the 409 hash check and the `adopt` step of the draft watch.

**Publish** is in the top bar of the IDE since G5. Its dialog lists what changes in the live app, in plain words, and then sends the stored draft with its hash. Mira still publishes from chat after the member approves the plan card. Both use the same route. A change bar above the tabs shows the newest change in the draft, with a one-step Undo through the G3c save. Undo is off when it would drop a live record type or field.

**Code in the IDE (D202.1).** The owner's goal of 2026-11-08 makes the IDE a full web IDE. Blocks stay first, and code files become a first-class part. The D193 route, layout and dock stay as they are.

| Part | What it does | Slice |
|---|---|---|
| **Files** | A branch of the Explorer beside Data, Views and Rules. Each file is TypeScript or TSX, in Monaco, with type hints for the UI kit and the door client | G8 |
| **Preview on save** | A save compiles the screen in the member's browser, and the preview frame reloads. After an error, the last good preview stays | G9 |
| **Console** | The logs and the errors of the frame, with the file and the line | G9 |
| **Tests** | Skill tests, workflow dry runs and a render test of each screen | G10 |
| **Packages** | What a module may import: the standard set for a screen, and the reviewed list for server code | G11 |
| **Version history** | The versions, a diff of the manifest and the files, and a rollback | G12 |
| **Mira** | She uses blocks first, and writes a file only where no block fits. The plan card names the gap that each file fills. She checks her own screens in captures | G16, G13 |

**Git in the IDE (D202.9).** The owner asked for git on 2026-11-08. Three rows give it:

- **Save history (G17).** Each save of a draft is a revision, with its author, a message and a diff. A member reverts to any revision, and a publish tags a version (A4).
- **Named drafts (G18).** A branch. A member tries a change away from the main draft, then merges it with a diff and a conflict view.
- **GitHub sync (G19).** An optional two-way sync with a folder in the repository of the org. A push makes a draft revision and never publishes. The token stays in `integration_connections`, and no git process runs on a sandbox dir.

**Who writes code.** Every Studio member builds with blocks. Only a member that the code right names (J7) writes files, and each other member sees the files read only. A holder of `studio:publish` approves each version with code before it reaches anybody but its author (§19.10).

### 6.4 The engine under the builder

A manifest is data, so the builder needs **no sandbox and no code run**. It is a Mira run with the builder tools (G1). That is why S0 to S3 can ship before the off-box sandbox.

Server code (B5) runs on the one coding harness, profile `app_builder` (CA-14, D139, task #1042). Studio adds no second harness. A custom screen (L1) needs no harness: Mira writes its files with builder tools (G16), and the browser compiles them (D202.4).

### 6.5 Building a Studio app outside Metorite (D202.10)

The owner asked on 2026-11-08 for a scaffold and a harness. They keep an app that a member builds in VS Code compatible with Studio.

**The rule: an app built in VS Code is the same package as one built in Studio.** It has one manifest format, one validator, one build and one review. The kit adds no second implementation of any seam. The CLI calls the validator, the rule evaluator and the dry run (F4) of the server, and never ports them.

**The scaffold (G21).** `npx @metorite/create-studio-app`, or `metorite studio init`, makes one folder:

| Path | What it holds |
|---|---|
| `metorite.json` | The package id, the org, the version of the Studio API contract, and the link |
| `manifest/` | The app, objects, views, actions, rules and workflows as files, and the skill as `SKILL.md` (the format of F8) |
| `screens/*.tsx` | Custom screens on the UI kit (L4) |
| `modules/` | Logic modules (B5) |
| `samples/` | Sample data for the preview |
| `tests/` | The tests of the app |
| `AGENTS.md`, `CLAUDE.md` | The rules for coding agents |
| `package.json` | Pinned versions of `@metorite/studio-sdk` and `@metorite/ui` |

**The sync.** The CLI joins the files into one manifest and splits it again, so each git diff stays small. The server reads only the joined manifest. A manifest from files may grow to the A2 cap of 256 KiB (`studio_manifest.py` `MAX_DOC_BYTES`).

**The chat cap.** The chat cap is 24,000 characters (`studio_tools.py` `MAX_MANIFEST_CHARS`), for a save from chat and for the facts of the IDE (§6.3). Above that cap, the IDE says that Mira in the dock cannot edit the whole draft. Mira then edits it file by file through the harness (G26). This is agent design.

**The SDK (G22).** One package, `@metorite/studio-sdk`:

- TypeScript types for the manifest, made from the live JSON Schema that `studio_describe` serves (G1b).
- A `json.schemas` mapping for VS Code, for IntelliSense.
- A typed door client (`metorite.records.list`, `metorite.actions.run`), made from the manifest of the app.
- The UI kit with its types (L4, D202.2). The SDK takes the kit, and copies no part of it.
- A mock door with the sample rows, for the unit tests of custom screens.

**The CLI (G23, G24).**

| Command | What it does |
|---|---|
| `login` | A device code. It reuses the WS-55 connection for outside agents and its scopes (D136), so no second token type exists |
| `check` | The validator of the server, with each error at its file and line |
| `test` | The rule tests and the workflow dry runs on the server, and local vitest for screens on the mock door |
| `dev` | It watches the folder and pushes to a dev draft. The preview of the Studio IDE reloads on sample data (G4), so one renderer stays |
| `push` | A new draft revision (G17) |
| `diff`, `pull` | The diff with the draft, and the draft into the folder |

**The CLI has no `publish`.** A publish stays in Studio, with the review of `studio:publish`.

**Source only (G25).** Metorite accepts source, and never a bundle that a laptop built. Server code builds in a throwaway sandbox of Metorite (L7, §19.10 rule 12). A screen compiles from the approved source in the browser (L1, §19.10 rule 15). The local build serves only the preview and the tests.

**Compatibility (G20).**

- `metorite.json` pins the version of the Studio API contract. The server refuses an unknown or a retired version with the code `contract_unsupported` (§19.10).
- The SDK and the server publish a support window, and `check` warns before a version retires.
- A golden-file test locks the manifest schema, so a server change that breaks the kit fails CI.

**The AI harness (G26, G27).**

- A Claude Code skill or plugin, `metorite-studio`. It teaches the manifest, the blocks, the door and the guardrails. It runs `check` after each edit, and takes the visual captures of each custom screen (§11.2).
- The same tools work over the Metorite MCP (MCP-P5, task 869), so Claude Code in VS Code can build with no CLI.
- A VS Code extension comes later, with the schema, a preview pane and the state of the sync (G27).

**Git.** The GitHub sync (G19) uses the same folder. A push from the CLI and a push to GitHub both make draft revisions (G17).

---

## 7. The skill creator

The skill creator replaces the Agent Workshop (D141). A member does not build a second assistant. The member teaches Mira a new skill.

1. **Describe.** "When someone asks for a quote, use our price list and this template."
2. **Draft.** Mira writes the description, the instructions and the action list. She picks actions from the catalog only.
3. **Try.** A test chat beside the draft runs the skill on a dry-run door: reads are real, writes and sends are simulated.
4. **Test cases.** Each test is a prompt and the action it should call. The member saves a good try as a test in one click.
5. **Publish.** Private first. Sharing with the organization needs an approval (§9).
6. **Improve.** Mira proposes an edit from failed turns, with the evidence. The member approves the diff.

**States (F2a).** A skill is in one of four states. The route `PATCH /studio/skills/{id}/state` owns the moves, and `skills.TRANSITIONS` holds them:

| From | To |
|---|---|
| `draft` | `published`, `archived` |
| `published` | `paused`, `archived` |
| `paused` | `published`, `archived` |
| `archived` | `draft` |

- A move to the same state is a no-op (200). The route refuses each other move with 409.
- A publish needs a version. A skill with no version gets 409 "Add a version first".
- A publish runs the catalog check again on the newest version. An action that the catalog no longer serves is 422.
- **A new version of a published skill returns it to `draft`**, in the same transaction. So a published skill always runs a version that its owner published.
- **Two moves at once.** A move reads the skill row `FOR UPDATE`. So a second move waits for the first one, reads the new state, and the table decides on that state. A move that the new state does not allow is 409.
- No move changes the visibility. J2 adds the share, and until then a skill is private.

**Format.** A skill exports as an Agent Skills folder (`SKILL.md`, its resources). So an outside agent such as Claude Code can load a company's skill over the MCP (F8). A `SKILL.md` can also be imported, and the importer keeps only actions that the catalog serves.

**Runtime.** A published skill becomes one WS-67 tool pack for the organization. `tool_packs.choose` loads it on a turn that matches its description, inside the limits of 35 tools per pack and 3 packs per turn. The code declares the code packs, and the environment switches them on (`TOOL_PACKS_ORGS`). F3 adds packs read from the database, dark behind `TOOL_PACKS_ORGS` and `STUDIO_ORGS` (§14 F3).

---

## 8. Connectors and MCP

1. **A package names its connections** (I1): `"connections": ["zoho", "mcp:tally"]`. Install checks for a live connection of each one. If one has none, the install opens Connect in place (§8.1, I8). Integrations lists which apps use each connection (IN-10). The app never asks a member for a key.
2. **An app over a connection** (I2). Studio reads an MCP server's tool list, or a connector's actions. It proposes a list view and a detail view for each read tool, and a form with an approval card for each write tool. This covers every system Metorite has no screen for.
3. **An organization's MCP tools are actions** (§3.2 source 3). The `McpPermissionGate` card and the admin's "always confirm" setting still apply.
4. **Outside agents reach Studio apps** (B3). A published app's actions join the D136 door, so Claude Code calls `app_read` and `app_write` on it with no new tool. Building apps over the MCP is MCP-P5 (task #869).

### 8.1 Build on a connection: the whole flow (D220)

The owner asked on 2026-11-14 (task 3164). Studio apps, workflows and skills build on the systems that a company connected. Studio guides a member through a connect when an app needs one. A connect offers a build where a build fits. Items 1 to 4 above hold the base. This section closes the gaps that the review of task 3164 found.

| Where the member is | What happens | Slices |
|---|---|---|
| Building, in Studio or in chat | Mira asks what the app is for, and proposes starters from the actions of a connection (I2). A step "Use data from" lists the connected systems and the ones the member can connect (IN-11). A missing one opens Connect in place (I8). The preview reads live data and sends nothing (I11) | I2, I8, I11, IN-11 |
| Installing or publishing | A missing connection puts the install in `needs_setup` with one Connect card (I1). The card opens the same Connect step (I8). The install screen names whose account each connection uses (I9) | I1, I8, I9 |
| After a connect, in any app or in Integrations | One result card says what works now, then names the templates, then offers a build only where it fits (IN-9) | IN-9 |
| When a connection breaks | Each app, workflow and skill that uses it shows one Reconnect card, its triggers pause, and the owners get a Needs-you item (I10). A disconnect names what stops first (IN-10) | I10, IN-10 |

**The rules.** Each rule binds when its slice merges, and until then it is advisory (R7).

1. **One registry, and one connect flow for each provider (D96).** Studio adds no connection store and no connect flow. Connect in place opens the connect flow of the owning app, through the `setup_href` of its registry row (`integrations_console.md` §3.2). It adds a path back to the step that asked. A provider with no owning app connects through the flow of the Integrations console: an MCP server, or a key. A missing connection has no row, so its link comes from the provider-level map `setup_href_for` (I8a). Fence: `src/components/studio/ConnectSheet.test.ts` (I8a).
2. **One click where the provider allows it.** An OAuth provider connects in one step, through a provider app that Metorite owns. An MCP server connects with its address, and with the OAuth of IN-8 when it asks for it. A key goes in a secure field, with one sentence that says where to find it. A key never goes in chat text. In chat, Mira shows the same Connect card. A member who may not connect an org connection gets "Ask an admin", which puts a Needs-you item in front of the admins (I8).
3. **Whose account, stated (I9).** A connection need says `as: "org"` or `as: "member"`. With `member`, each viewer uses their own connection, and a viewer with none sees "Connect your <provider>" and reads nothing. With `org`, every viewer reads through one account. So the install screen (J6) names that account, and an admin approves it at install. A provider that holds the data of a person, such as mail or a calendar, defaults to `member`. The roles of the app (C8) still limit the rows that each viewer sees. Fence: the I9 R8 test.
4. **Least access.** A need names the scopes that it uses. The connect step asks only for them. A new version that widens them asks again (J6).
5. **A build starts from a job, not from the API (I2).** Mira first asks what the app is for. She proposes up to three starters from the actions of the connection, such as "Overdue invoices" or "Stock below reorder". She then makes the views. A list and a form for each tool is the fallback, not the default.
6. **Live first, and a copy by choice.** A remote object (O12) reads live. A copy (the O12 mirror) is a stated choice: "Keep a copy, so that Mira can search it and the app works when <provider> is down." The choice shows the refresh and how long the copy stays.
7. **A preview sends nothing (I11).** The preview of the IDE reads the class A actions of a connection live, at most 50 rows, kept for 60 s. Each other action of the connection goes to the preview store of G4. One line says "Live data from <provider>. Nothing is sent." Fence: the I11 test that counts the calls of the connection client.
8. **A break is visible (I10).** An expired, revoked or removed connection puts each install that needs it in `needs_setup`, with one Reconnect card. It pauses each trigger that needs it, with a reason. A skill that needs it tells the chat "Reconnect <provider>". The owner of the connection and each app owner get a Needs-you item.
9. **Every shape of package.** An app, a workflow and a skill name their connections in the same way. A workflow whose trigger needs a missing connection cannot publish.
10. **Registry connections only, until IN-3.** The integration actions of `acb_skills/integrations.py` still read the process environment (`integrations_console.md` IN-D7). Until IN-3 (task 3176) lands, a package names only registry connections: an MCP server, Zoho, GitHub, a payment account or a mailbox.

**The offer to build after a connect, reviewed.** The owner asked for an offer to build a custom app after a connect. The review keeps the offer and changes its shape (D220.4).

- Most connections feed a built-in app: a mailbox feeds Email, a calendar feeds Calendar, and Zoho feeds the CRM. An offer to build a mail app after a mailbox connect makes a second place for the same data.
- Right after a connect, the member wants to see that it worked (the rule that a setup screen shows the result). So the card shows what works now first.
- The offer of CE-10d (task 3069) works because it is the one next step that fits: "Also sync its calendar". IN-9 copies that pattern.
- So "Build an app on <provider>" shows only for a provider whose data no built-in app shows. The connector list (IN-11) says which. It is never a modal, and never an automatic build. A member can always build from Studio.

---

## 9. Governance

1. **Who builds.** A member with `feature:studio` builds private packages and skills. A member, a manager and an admin hold it through `feature:*`, and the owner holds it through `*`. A guest does not hold it. An admin can deny it to one person with an override (STU-Q4, D180).
2. **Run as the viewer.** Every action runs with the viewer's `UserContext`. A view never shows more than the viewer can read in the owning app.
3. **Share.** Private → named people or a group (`group:<slug>`, D12) → the organization. A share to the organization needs a holder of `studio:publish`. That holder reads the manifest diff and approves one version (the pattern of D130.3). J2b-1 shows the full manifest of the pending version first, and a later slice adds the diff. The org install of PKG-8b waits for that approval (path A, owner 2026-11-13). The owner holds `studio:publish` through `*`. J1-2 (task 1707, migration 413) seeds it on `admin` in each org and in `provision_org_roles`. So owners and admins can grant it (D180). A grant goes to one person (an override) or to a custom role, because a group carries no permission grant. A share of an app to a group is the object grant `group:<slug>` of J2.
4. **Versions.** Version publish by the owner of a private package or skill needs `feature:studio` and ownership only. `studio:publish` gates the share to other people and the review of a manifest diff (STU-Q4b, J2). Publishing snapshots an immutable version (`app_versions`, `workflow_versions`). An open workflow run keeps its version. Rollback copies an older version into a new one, and history stays append-only. The install of a version is WS-PKG (`package_host.md`).
5. **Trust ladder.** An S1 workflow starts at "ask each run" and climbs by D131. Five acts always ask: money, a member or role write, a delete, a bulk change, and a send to an outside person whom the scope does not name.
6. **Cost.** Each build and each run has a credit cap. Every model call writes a `usage_event` row (the lesson of H-287).
7. **Kill switch.** An admin pauses a package, a workflow or a skill in one step. New actions stop within 10 seconds.
8. **Audit.** `app_audit` records each publish, rollback, share and action, with the actor and the version. The install record belongs to WS-PKG (`package_host.md`).
9. **Entitlements.** `feature:studio` and `studio:publish` map through the one entitlement seam (old STU-7). Pricing stays flat (`launch_surface.md` §4, STU-Q7). The workflow API keeps `feature:workflows` on every route. J1-3 adds `feature:studio` to each workflow write and to a test run of the draft, so a write needs both features. A read, a run of the published version, a module route and the four live routes add no Studio right. J1-3 ships dark behind `WORKFLOW_STUDIO_GATE_ORGS`. Until the flag lists an org, a deny of `feature:studio` there hides the Studio pane and the workflow editor (`/studio/workflows/:id`), but it does not stop a write through `/api/workflows`. The Studio page shows its Workflows parts only to a holder of `workflows` (§14 J1-3).

---

## 10. What we keep, change and retire

| Built piece | Fate |
|---|---|
| Workflow engine, editor, Copilot, Module Studio (`routes/workflows/`, `/workflows`) | **Keep.** The editor moves into Studio (A1). The engine does not change |
| App Workshop tables (`apps`, `app_versions`, `app_grants`, `app_audit`, `app_templates`) | **Keep as the package store** (A3, built). A package is a row of `apps` with `format = 'manifest'`, and its manifest lives in `apps.manifest`. No second app table |
| `app_data` (the `window.cc` storage) | **Keep for code-format apps** (L2). Studio objects live in the typed object store of C1 (D146) |
| App Workshop builder (Monaco, `SandboxedHtml`, `window.cc`) | **Becomes the code block** (L1). The code-first builder stops being the default |
| `GenerativeUINode` and its templates | **Grows into the one renderer** (D1) |
| Agent Workshop (`/build/agents`, a 404 today) and `AddAgentWizard` | **Retire** (F9). The skill creator replaces it |
| Skill families (WS-23) and tool packs (WS-67) | **Keep.** A published skill becomes an org tool pack (F3) |
| Member skills in the workspace (`agent-data/skills/`) | **Keep for sandbox runs.** A Studio skill lives in the database. F8 bridges the two formats |
| `app_<slug>_<action>` agent tools (`orchestrator/app_tools.py`) | **Replace** with catalog actions in a skill (F3), then remove |

**The old checklist.** Task #778 listed STU-1 to STU-8 before this spec. Their meanings map to the new ids: STU-1 → F1 and F2, STU-2 → E2, STU-3 → D8, STU-4 → the groups C, D and G, STU-5 → A1, STU-6 → F9, STU-7 → J1, STU-8 → E6. The old ids are retired, so no id carries two meanings (R2).

---

## 11. What other apps do

Research of 2026-10-31. External sources are named, and the App Workshop RFC (`docs/app-workshop/README.md` §2, July 2026) holds the earlier survey of chat-to-app builders and internal-tool platforms.

| Product | What it does | We take | We do not take |
|---|---|---|---|
| **Zapier** (vendor) | Tables (data), Interfaces (forms and pages), Zaps (automation), Agents, Copilot that builds Zaps | Data, views and automation as one package | Three separate products and three separate builders |
| **Airtable Interfaces** (vendor) | Pages of blocks bound to a base, automations | Field-typed blocks, grouping a board by a select field | A per-app look |
| **Notion** (vendor) | Databases with board, calendar, timeline and gallery views, automations, AI agents | One record set, many views | A document-first model |
| **Retool** (vendor) | Components bound to queries, code in every field | Components bound to a source | JavaScript in every field |
| **Salesforce** (vendor) | Objects, Lightning App Builder, Flow, Agentforce topics and actions | One action used by the UI and the agent | Per-tenant schema work |
| **Copilot Studio** (vendor) | Agents with topics and actions, Power Automate flows | A skill as instructions plus actions plus triggers | A separate agent per use |
| **MCP Apps** (open standard, `io.modelcontextprotocol/ui`) | A tool links a sandboxed `ui://` HTML resource that a host renders | Export an app view as an MCP App later | HTML as the default app format |
| **A2UI** (open protocol, Google) | Agents send declarative JSON that the client renders from a trusted component catalog | **The catalog model**: data, not code, from an approved list. It backs STU-Q1 | A second protocol inside our own client |
| **Agent Skills** (open standard, from Anthropic) | A folder with `SKILL.md`, loaded in three stages: discovery, activation, execution | The `SKILL.md` format and loading on demand (WS-67 packs) | — |

**Not verified.** The MCP Apps version and its SEP number differ between secondary sources. The A2UI renderer list and the Agent Skills adoption counts come from secondary sources. Zapier Interfaces limits come from a third-party listing. Copilot Studio got no search result in this round, so its row is from prior knowledge.

**Sources:** modelcontextprotocol/ext-apps; github.com/a2ui-project/a2ui; agentskills.io; help.zapier.com; the App Workshop RFC §2.

### 11.1 Lessons from DeepSeek Harness (D147, PKG-Q5)

The WS-PKG research of 2026-10-31 read `github.com/deepseek-ai/deepseek-harness` (dsh) and its Cordis kernel. `package_host.md` §5 and `web_research.md` §8 own the lessons. Studio reuses them, and builds no second harness, goal store or registry.

| Lesson (dsh) | Owner | What Studio does with it |
|---|---|---|
| Each registration returns a disposer, so a removed plugin leaves nothing | WS-PKG PKG-9 | Each trigger (E3), schedule, skill pack (F3) and job (H2) of a package registers as a contribution of its install. The removal of the install stops each one |
| A plugin with an unmet need waits in `PENDING` | WS-PKG `needs_setup`, Studio I1 | A package with a missing connection installs and runs no action |
| The model reads the live config schema before it writes (`tool-cordis`) | Studio G1b | Mira reads the manifest schema, the field types and the block catalog before she drafts |
| Replay tests: a recorded turn replays with a stubbed model | WS-WEB WEB-12 | G6 runs its regression cases on the WEB-12 harness, with no model and no cost. The live eval is the quality gate only |
| A goal record: a round cap, a block code, and a stale update refused | WS-WEB WEB-10 | A build session (G3, G4) and a long workflow run each keep one goal record |
| Model-visible means logged | WS-CHAT | Each skill that F3 loads, and each builder result that Mira reads, is a session event |
| Each plugin states its token effect and its prompt-cache effect | Studio F3 | Each skill pack states its token cost and whether it changes the prompt prefix. Packs load in a fixed order |
| An unresolved prompt variable fails assembly | Studio F3 | A skill's instructions with an unresolved variable fail to load, and Mira gets no broken prompt |
| An approval with no answerer fails closed | Studio B2c | With no member to answer, a class B or C action does not run. An AI approver never decides a class C or X action |

**Not copied:** dsh runs plugins in process with full trust, keeps config in files, and loads React slot code into the page. Studio runs member code off the box only (D127.2), keeps installs and config in Postgres, and draws UI from the block catalog.

### 11.2 Anatomy and Dynamic Apps (research, 2026-11-08, D202)

The owner sent two repositories as references for the full IDE goal (task 2326). The research read each README and one more file. It ran no code. The tag **[unverified]** marks a fact that no read file confirmed. The full notes are on epic 491, in the comment of 2026-11-08.

**Rule: Metorite takes the patterns below and copies no code.**

#### 11.2.1 Anatomy

`github.com/wheresryan22/anatomy`. A Claude skill from a third party, under MIT. Read: the README and `SKILL.md`. The kit code was not read.

| Fact | What it means for Studio |
|---|---|
| It explains an idea with a working, interactive drawing of a machine: SVG, SVG with WebGL effects, and 3D in beta | The live explainer figure (D29) |
| One small model drives every number and every moving part of the figure | A Studio figure binds to the records of the app, so the drawing stays true |
| It captures each output at desktop and 390 px, in both themes, and reads each capture | Mira checks her own screens (G13), in the member's browser |
| It builds from a kit of parts, never from nothing | The Metorite UI kit (L4) |
| It proposes the smaller figure first | Mira's plan card proposes the smaller app first |
| Its output is standalone HTML or React, with no npm dependencies, both themes and reduced motion [unverified for the 3D mode] | Nothing new. The renderer already owns themes |

**We do not take** its own 3D engine. The owner asked for three.js, which loads GLB models (D27, L5). We do not take its build time of minutes to hours as a default.

#### 11.2.2 Dynamic Apps

`github.com/rivet-dev/dynamic-apps`. Open source from the vendor Rivet, in TypeScript. Read: the README and the API contract file. Nothing was run. The research of 2026-11-03 on epic 491 found no licence file [unverified now], so it is no dependency.

| Fact | What it means for Studio |
|---|---|
| A throwaway build VM builds the source into an immutable release | Build once in a throwaway sandbox (§19.10 rule 12, L7) |
| The live pointer moves only after a complete write. A failed build leaves the old release live | The atomic switch (§19.10 rule 13). A4 already publishes this way |
| A cached VM serves each release, with limits on memory, time and queue | Warm runtimes for each org and each version (L7) |
| The host injects host functions. Management credentials never enter the sandbox | The door and the `window.cc` bridge are our host functions (§19.10 rule 14) |
| Limits as numbers: 64 MB of heap, a 30 s run, a 1 MiB request, a 4 MiB response, 4 MiB of source, 2,000 files and 256 dependencies | The limits table of §19.10 |
| Golden tests lock the error codes | The fixed error codes of §19.10 (L6) |
| Its contract states no network rule at run time [unverified] | §19.3 keeps no network at run time |

**We do not take** Rivet actors or its cloud. They would be a second runtime beside the sandbox and MAF, and our data stays in Postgres under RLS (D15, D146.4). We do not take a backend for each user, because our unit is the app of an org. We do not take npm installs that run scripts (B8 forbids them).

#### 11.2.3 What both teach

1. **Compose from a kit.** Both build from parts. Studio uses blocks first, and the UI kit second.
2. **Check what you built.** Both check their own output before they show it, with a capture or a smoke test.
3. **Limits are data.** Both write their limits down. Studio keeps one table for each version (§19.10).

---

## 12. Open owner questions

Each question has one recommendation. A slice that waits on a question says so in §14. **The owner answered five questions and the template question on 2026-10-31, in chat (D146).**

| Id | Question | Recommendation | Answer |
|---|---|---|---|
| **STU-Q1** | Is an app a declarative manifest drawn by one renderer, with code only as one sandboxed block? Or is it code first, as the App Workshop RFC planned? | Manifest first | ✅ **Layered** (D146.1): manifest, rules, logic modules, custom UI block, all through one door (§3.7). The owner asked for full integrated apps such as inventory, procurement and manufacturing |
| **STU-Q2** | Does the library show three kinds, Apps, Workflows and Skills, with "skill" as the member's name for a D127 tool pack? | Yes | ✅ **Yes** (D146.2) |
| **STU-Q3** | Where do installed company apps show in the sidebar? | Under Apps, after the built-in apps, with a small "Company" label. Never a Center (D49) | ✅ **Under Apps, after the built-in apps** (D146.3) |
| **STU-Q4** | Who may build? | **Every member builds private packages.** Owners and admins hold `studio:publish` and may grant it to a person or to a custom role. A group carries no permission grant | ✅ **Answered 2026-11-04** (D180): "Every member builds private apps. Owners and admins publish and can grant publish." Source: the owner, in a Metorite run on task 1199 |
| **STU-Q4b** | May a member without `studio:publish` publish a version of their own private app or skill, for themselves only? | **Yes.** `studio:publish` gates the share to other people, and the review of a manifest diff (J2) | ✅ **Yes** (D180, 2026-11-05): "A member publishes their own private app for themselves. `studio:publish` gates sharing to others." Source: the owner, in the same run on task 1199 |
| **STU-Q5** | Where do app records live? | `app_data`, typed by C1, with a cap per app | ✅ **A typed object store now** (D146.4): launch plan E10 inside Studio (§3.1) |
| **STU-Q6** | Which milestone? | Stage 1 dark in M1, Stage 2 to the pilots in M2 | ✅ **As recommended** (D146.5). Neither moves an M0 item |
| **STU-Q7** | How is Studio priced? | **Inside the ₹500 seat.** Builds and runs spend AI credits through `usage_event`. No fee per app | Open |
| **STU-Q8** | Which map tiles? | **MapLibre with an OpenStreetMap-based tile host**, and a paid key only at scale | Open |
| **STU-Q9** | Can Mira start a build from any chat? | **Yes.** Chat starts it, and Studio finishes it | Open |
| **STU-Q10** | Does Metorite ship first-party templates for inventory, procurement, MRP-lite and the production schedule, built on Studio? | Yes, in Stages 2 and 3 | ✅ **Yes** (D146.6): K3 and K4 |
| **STU-Q11** | May real HR data (pay, bank details, ID numbers, punches) go into a Studio app? | **Only after C8, C9 and C10, and after a lawyer reads the design for WS-37 (DPDP).** Sample data until then | Open |
| **STU-Q12** | May an employee with no seat use self-service? | **No, at first.** The slip goes by mail. A free self-service seat is a pricing call | Open |
| **STU-Q13** | Which sender does a bulk mail use? | **An HR shared mailbox with a throttle first.** An org system sender needs a mail provider and domain setup, so it comes later | Open |
| **STU-Q14** | Does one card that approves a whole pay run fit D131.7? | **Yes.** "Ask each run" means one ask for one run, and the card shows the exact list (D131.2) | Open |
| **STU-Q15** | Module Studio runs member Python on the box under `feature:workflows`, with an AST allowlist and rlimits. Its own docstring says that it is not a sandbox. Does it stay? | **Limit it to admins now.** Move it into the sandbox with B5, and retire the on-box path | **Closed 2026-11-12.** The owner chose "limit Module Studio to admins now" on task 2953. The code change that does it is not built. It is a slice of its own, and the module routes keep `feature:workflows` until it lands |
| **STU-Q16** | (CRM OPEN-1.) May a person outside the org submit to an org's app with no sign-in, through a link, a QR code or a kiosk? | **Yes,** through the one public door (O1): its own host with no cookies, a shared rate limit, a bot trap, an org-scope app only, and each surface turned on by an admin | ✅ **As recommended** (D174.1) |
| **STU-Q17** | (CRM OPEN-2.) Is a person who submits a form or sends a message "named" for a send that the app declares, such as their own booking confirmation? | **Yes,** for a send that the manifest declares and an admin approved at publish, to that person only, with opt-out | ✅ **As recommended** (D174.2) |
| **STU-Q18** | How does an org take payments in its apps? | **Each org connects its own Razorpay account,** one click where Razorpay allows it. Metorite holds no money and takes no cut. Other providers later | ✅ **As recommended** (D174.3) |
| **STU-Q19** | How does an org get a WhatsApp number that answers outside people? | **Metorite joins Meta as a Tech Provider with Embedded Signup,** so an org connects its own number in one step and Meta bills the org | ✅ **As recommended** (D174.4) |
| **STU-Q20** | Which sender does a mail or SMS to a person outside the org use? | **Mail from the org's own domain** (one provider, DKIM set up in onboarding) first. **SMS later,** through one Indian provider | ✅ **As recommended** (D174.5) |
| **STU-Q21** | May health or other sensitive personal data go into an app, and may the AI read it? | **Sensitive fields are restricted by default (C9):** encrypted, never embedded, never sent to a model. An admin may open one field to the AI, with a notice in the consent text. Real patient data waits on WS-37 (DPDP) and a lawyer, as STU-Q11 | ✅ **As recommended** (D174.6) |
| **STU-Q22** | Where do outside people live? | **In `crm_contacts`, the one store,** also for an org that does not use the CRM pipeline. The CRM app shows them as contacts | ✅ **As recommended** (D174.7) |
| **STU-Q23** | Who pays for outside traffic? | **AI steps spend the org's AI credits through `usage_event`.** Meta and payment fees go to the org's own accounts. No fee for each app (STU-Q7) | ✅ **As recommended** (D174.8) |

---

## 13. Shell manifest (R9)

This is the manifest of the Studio pane itself, in the shape of `navigation_shell.md` §5 (D89). §4 covers the apps that Studio makes.

| Field | Value |
|---|---|
| **Team** | `studio` |
| **Purpose** (blurb) | "Build skills, workflows and apps for your team." |
| **Jobs** | "Build an app" (describe bar), "Teach Mira a skill" (skill draft), "Automate a task" (workflow draft). Each opens its draft route |
| **Search provider** | Packages, workflows and skills by name and purpose |
| **Needs you** | A share waiting on your approval, a failed run of your workflow, a skill test that broke after an edit |
| **Home cards** | "Your apps" (altitude: me), "Waiting for approval" (altitude: admin) |
| **Agent** | `orchestrator`. G1 is built, but `studio-builder` only owns the `studio` pack and never runs as an agent |
| **Own palette, bell, assistant rail or ⌘K** | None |

---

## 14. Slices

**Tier** is the review tier of `next-ticket.md` step 1b: LOW, STD or HIGH. Every slice ships dark or in preview. Migration numbers are the next free number at build time (R1).

**Where the rows live (CIV-33, task 3337).** This section holds one INDEX line for each slice: id, status, tier, gate and a link. The row text (What, Done when, Tier, Gate) lives in `project-docs/specs/ai_studio_rows/<id>.md`. To change a row, edit its file and, when the status changes, the index line. To add a row, write its file and add one index line in its group. **A conflict in this section: take main's section 14, then put your row text in its row file and your index line back.** The fence is `tests/unit/test_ai_studio_rows.py`. `python scripts/ai_studio_rows.py check` names each bad line.

### Group A — The pane and the package

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **A1** | 🔨 BUILT (2026-11-02) | STD | AGENT-SAFE | [row](ai_studio_rows/A1.md) |
| **A2** | 🔨 BUILT (2026-11-01) | STD | AGENT-SAFE | [row](ai_studio_rows/A2.md) |
| **A3** | 🔨 BUILT, dark (2026-11-01) | HIGH | AGENT-SAFE | [row](ai_studio_rows/A3.md) |
| **A3b-1** | 🔨 BUILT, expand (2026-11-11) | HIGH | AGENT-SAFE | [row](ai_studio_rows/A3b-1.md) |
| **A3b-2** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/A3b-2.md) |
| **A4** | 🔨 BUILT, dark (2026-11-01) | STD | AGENT-SAFE | [row](ai_studio_rows/A4.md) |
| **A5** | 🔨 BUILT, dark (2026-11-03) | HIGH | AGENT-SAFE | [row](ai_studio_rows/A5.md) |
| **A5c-1** | 🔨 BUILT, dark (2026-11-05) | HIGH | AGENT-SAFE | [row](ai_studio_rows/A5c-1.md) |
| **A5c-2** | 🔨 BUILT (2026-11-11) | HIGH | AGENT-SAFE | [row](ai_studio_rows/A5c-2.md) |
| **BETA-1** | 🔨 BUILT, dark (2026-11-06) | STANDARD | AGENT-SAFE | [row](ai_studio_rows/BETA-1.md) |

### Group B — The action catalog

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **B1** | 🔨 BUILT, dark (2026-10-31) | STD | AGENT-SAFE | [row](ai_studio_rows/B1.md) |
| **B1c** | 🔨 BUILT, dark (2026-11-02) | STD | AGENT-SAFE | [row](ai_studio_rows/B1c.md) |
| **B1b-1** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B1b-1.md) |
| **B1b-2** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B1b-2.md) |
| **B1b-3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B1b-3.md) |
| **B2a** | 🔨 BUILT, dark (2026-11-01) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2a.md) |
| **B2b-1** | 🔨 BUILT, dark (2026-11-02) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2b-1.md) |
| **B2b-2** | 🔨 BUILT, dark (2026-11-11) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2b-2.md) |
| **B2b-2c** | 🔨 BUILT, dark (2026-11-13, task 2968) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2b-2c.md) |
| **B2b-3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2b-3.md) |
| **B2c-1** | 🔨 BUILT, dark (2026-11-03) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2c-1.md) |
| **B2c-2a** | 🔨 BUILT, dark (2026-11-06) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2c-2a.md) |
| **B2c-2b** | 🔨 BUILT, dark (2026-11-06) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2c-2b.md) |
| **B2c-2c** | 🔨 BUILT, dark (2026-11-08, task 1866) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2c-2c.md) |
| **B2c-2d** | 🔨 BUILT, dark (2026-11-11, task 1867) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B2c-2d.md) |
| **B3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B3.md) |
| **B4a** | 🔨 BUILT, dark (2026-11-13, task 1216) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B4a.md) |
| **B4b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B4b.md) |
| **B4c** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B4c.md) |
| **B5** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B5.md) |
| **STU-Q15a** | 🔨 BUILT, dark (2026-11-06) | HIGH | AGENT-SAFE | [row](ai_studio_rows/STU-Q15a.md) |
| **B6** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B6.md) |
| **B6a** | 🔨 BUILT, dark (2026-11-13, task 1413) | HIGH | AGENT-SAFE | [row](ai_studio_rows/B6a.md) |
| **B7** | 🔨 BUILT, dark (2026-11-13, task 1414) | STD | AGENT-SAFE | [row](ai_studio_rows/B7.md) |
| **B8** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B8.md) |
| **B9** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B9.md) |
| **B10** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B10.md) |
| **B11** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B11.md) |
| **B12** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B12.md) |
| **B13** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B13.md) |
| **B14** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B14.md) |
| **B15** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B15.md) |
| **B16** | open | HIGH | OWNER-GATE | [row](ai_studio_rows/B16.md) |
| **B17** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B17.md) |
| **B18** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/B18.md) |

### Group C — Objects

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **C1** | 🔨 BUILT, dark (2026-11-01) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C1.md) |
| **C1b** | 🔨 BUILT, dark (2026-11-02) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C1b.md) |
| **C2a** | 🔨 BUILT, dark (2026-11-01) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C2a.md) |
| **C2b** | 🔨 BUILT, dark (2026-11-11) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C2b.md) |
| **C2c** | 🔨 BUILT, dark (2026-11-13, task 1349) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C2c.md) |
| **C3** | 🔨 BUILT, dark (2026-11-11) | STD | AGENT-SAFE | [row](ai_studio_rows/C3.md) |
| **C4** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C4.md) |
| **C4a-1** | 🔨 BUILT, dark (2026-11-13, task 3084) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C4a-1.md) |
| **C4a-2** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C4a-2.md) |
| **C4b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/C4b.md) |
| **C5a** | 🔨 BUILT, dark (2026-11-13, task 1159) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C5a.md) |
| **C5b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C5b.md) |
| **C6** | 🔨 BUILT, dark (2026-11-02) | STD | AGENT-SAFE | [row](ai_studio_rows/C6.md) |
| **C7a** | 🔨 BUILT, dark (2026-11-02) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C7a.md) |
| **C7b** | 🔨 BUILT, dark (2026-11-03) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C7b.md) |
| **C8a** | 🔨 BUILT, dark (2026-11-11, task 1416) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C8a.md) |
| **C8b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C8b.md) |
| **C8c** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C8c.md) |
| **C8d** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C8d.md) |
| **C8e** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C8e.md) |
| **C9** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C9.md) |
| **C10a** | 🔨 BUILT, dark (2026-11-13, task 3092) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C10a.md) |
| **C10b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C10b.md) |
| **C10c** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C10c.md) |
| **C11** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C11.md) |
| **C12** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C12.md) |
| **C13** | 🔨 BUILT (2026-11-09) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C13.md) |
| **C14** | 🔨 BUILT (2026-11-09) | STD | AGENT-SAFE | [row](ai_studio_rows/C14.md) |
| **C15** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C15.md) |
| **C16** | open | STD | AGENT-SAFE | [row](ai_studio_rows/C16.md) |
| **C17** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C17.md) |
| **C18** | 🔨 BUILT, dark (2026-11-11) | HIGH | AGENT-SAFE | [row](ai_studio_rows/C18.md) |
| **C19** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C19.md) |
| **C20** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C20.md) |
| **C21** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C21.md) |
| **C22** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C22.md) |
| **C23** | open | STD | AGENT-SAFE | [row](ai_studio_rows/C23.md) |
| **C24a** | 🔨 BUILT, dark (2026-11-14) | STD | AGENT-SAFE | [row](ai_studio_rows/C24a.md) |
| **C24b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/C24b.md) |
| **C25** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C25.md) |
| **C26** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/C26.md) |

### Group D — Blocks and the renderer

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **D1** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D1.md) |
| **D2a** | 🔨 BUILT, preview only (2026-11-02) | STD | AGENT-SAFE | [row](ai_studio_rows/D2a.md) |
| **D2b-1** | 🔨 BUILT, dark (2026-11-04) | STD | AGENT-SAFE | [row](ai_studio_rows/D2b-1.md) |
| **D3** | 🔨 BUILT, preview only (2026-11-02) | STD | AGENT-SAFE | [row](ai_studio_rows/D3.md) |
| **D3b** | 🔨 BUILT, preview only (2026-11-05) | STD | AGENT-SAFE | [row](ai_studio_rows/D3b.md) |
| **D4** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/D4.md) |
| **D5** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D5.md) |
| **D6** | 🔨 BUILT, part (2026-11-13, STU-D6, task 1166) | STD | AGENT-SAFE | [row](ai_studio_rows/D6.md) |
| **D7** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D7.md) |
| **D8** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D8.md) |
| **D9** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D9.md) |
| **D10** | open | STD | OWNER-GATE | [row](ai_studio_rows/D10.md) |
| **D11a** | 🔨 BUILT (2026-11-02) | STD | AGENT-SAFE | [row](ai_studio_rows/D11a.md) |
| **D11b** | 🔨 BUILT, preview only (2026-11-03) | STD | AGENT-SAFE | [row](ai_studio_rows/D11b.md) |
| **D12** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/D12.md) |
| **D13a** | 🔨 BUILT, dark (2026-11-14, task 1421) | STD | AGENT-SAFE | [row](ai_studio_rows/D13a.md) |
| **D13b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/D13b.md) |
| **D13b-2** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/D13b-2.md) |
| **D13c** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/D13c.md) |
| **D13d** | open | STD | see row | [row](ai_studio_rows/D13d.md) |
| **D14** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D14.md) |
| **D15** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D15.md) |
| **D16** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D16.md) |
| **D17a** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D17a.md) |
| **D17b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D17b.md) |
| **D17c** | open | HIGH | see row | [row](ai_studio_rows/D17c.md) |
| **D18** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D18.md) |
| **D18-1** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D18-1.md) |
| **D18-2a** | 🔨 BUILT, dark (2026-11-14, task 3296) | HIGH | AGENT-SAFE | [row](ai_studio_rows/D18-2a.md) |
| **D18-2b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D18-2b.md) |
| **D19a** | 🔨 BUILT, dark, preview only (2026-11-14, task 2363) | STD | AGENT-SAFE | [row](ai_studio_rows/D19a.md) |
| **D19b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/D19b.md) |
| **D19c** | open | STD | see row | [row](ai_studio_rows/D19c.md) |
| **D20** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D20.md) |
| **D21** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D21.md) |
| **D22** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D22.md) |
| **D23a** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D23a.md) |
| **D23a-2** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D23a-2.md) |
| **D23b** | open | HIGH | OWNER-GATE | [row](ai_studio_rows/D23b.md) |
| **D24** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/D24.md) |
| **D25** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D25.md) |
| **D26** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/D26.md) |
| **D27** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D27.md) |
| **D28** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D28.md) |
| **D29** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D29.md) |
| **D30** | open | STD | AGENT-SAFE | [row](ai_studio_rows/D30.md) |
| **D31** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/D31.md) |

### Group AF — The app frame (D219, §5.5)

Epic: task 3147. Rung 4 of the ladder (demo task 3148) holds AF-1 to AF-6, AF-8 and AF-9. AF-7, AF-10a and AF-10b join rung 6 (more blocks), and AF-11 joins rung 8 (the IDE grows). Each frame slice carries the UI gate: four captures, a `Visual review:` line, and the CRM beside the app (§5.5 rule 9).

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **AF-1** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-1.md) |
| **AF-2a** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-2a.md) |
| **AF-2b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-2b.md) |
| **AF-3a** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-3a.md) |
| **AF-3b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-3b.md) |
| **AF-4** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-4.md) |
| **AF-5** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-5.md) |
| **AF-6** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-6.md) |
| **AF-7** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/AF-7.md) |
| **AF-8a** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-8a.md) |
| **AF-8d** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-8d.md) |
| **AF-8e** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-8e.md) |
| **AF-8b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-8b.md) |
| **AF-9** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/AF-9.md) |
| **AF-10a** | 🔨 BUILT, dark (2026-11-13, task 3158) | HIGH | AGENT-SAFE | [row](ai_studio_rows/AF-10a.md) |
| **AF-10b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-10b.md) |
| **AF-11a** | open | STD | see row | [row](ai_studio_rows/AF-11a.md) |
| **AF-11b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/AF-11b.md) |

### Group E — Workflows in Studio

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **E1** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/E1.md) |
| **E2** | open | STD | AGENT-SAFE | [row](ai_studio_rows/E2.md) |
| **E3** | open | STD | OWNER-GATE | [row](ai_studio_rows/E3.md) |
| **E4** | open | STD | AGENT-SAFE | [row](ai_studio_rows/E4.md) |
| **E5** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E5.md) |
| **E6** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/E6.md) |
| **E7** | open | STD | AGENT-SAFE | [row](ai_studio_rows/E7.md) |
| **E8a** | 🔨 BUILT, dark (2026-11-13, task 3095) | HIGH | AGENT-SAFE | [row](ai_studio_rows/E8a.md) |
| **E8b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E8b.md) |
| **E8c** | open | STD | AGENT-SAFE | [row](ai_studio_rows/E8c.md) |
| **E9a** | 🔨 BUILT, dark (2026-11-13, task 2376) | HIGH | OWNER-GATE | [row](ai_studio_rows/E9a.md) |
| **E9b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E9b.md) |
| **E9c** | open | STD | AGENT-SAFE | [row](ai_studio_rows/E9c.md) |
| **E9d** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E9d.md) |
| **E10** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E10.md) |
| **E11** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E11.md) |
| **E12** | 🔨 E12a BUILT, dark (2026-11-13, task 2379), the rest open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E12.md) |
| **E12a** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E12a.md) |
| **WFC-0** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/WFC-0.md) |
| **WFC-1** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/WFC-1.md) |
| **WFC-2** | open | STD | AGENT-SAFE | [row](ai_studio_rows/WFC-2.md) |
| **WFC-3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/WFC-3.md) |
| **WFC-4a-1** | 🔨 BUILT (2026-11-14, task 3287), owner preview pending | STD | AGENT-SAFE | [row](ai_studio_rows/WFC-4a-1.md) |
| **WFC-4a-2a** | 🔨 BUILT (2026-11-15, task 3305), owner preview pending | STD | AGENT-SAFE | [row](ai_studio_rows/WFC-4a-2a.md) |
| **WFC-4a-2b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/WFC-4a-2b.md) |
| **WFC-4a-2s** | 🔨 BUILT (2026-11-15, task 3309) | HIGH | AGENT-SAFE | [row](ai_studio_rows/WFC-4a-2s.md) |
| **WFC-4a-3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/WFC-4a-3.md) |
| **WFC-4b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/WFC-4b.md) |
| **WFC-5** | open | STD | AGENT-SAFE | [row](ai_studio_rows/WFC-5.md) |
| **WFC-6** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/WFC-6.md) |
| **E12** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/E12-dup2.md) |

**The owner questions of the WFC rows (2026-11-14, task 3287).** Both are open.

- **Q-a.** May a Test run of a draft, or any author of a workflow, put a task in the personal list of another member? WFC-4a-1 lets an author pick any active member. When the owner lists the org in `WORKFLOW_UNATTENDED_ORGS`, a run of that step makes a private task in that member's list.
- **Q-d.** Which member does a run of a `pm_task` or `pm_lifecycle` step check before it writes a task? The candidates are the author of the workflow and the member who starts the run. WFC-4a-3 waits on this answer.

### Group F — Skills

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **F1** | 🔨 BUILT (2026-11-01) | HIGH | AGENT-SAFE | [row](ai_studio_rows/F1.md) |
| **F1b** | 🔨 BUILT, dark (2026-11-05): F1b-1 | HIGH | AGENT-SAFE | [row](ai_studio_rows/F1b.md) |
| **F2a** | 🔨 BUILT, dark (2026-11-01) | HIGH | AGENT-SAFE | [row](ai_studio_rows/F2a.md) |
| **F2b** | 🔨 BUILT, dark (2026-11-02) | STD | AGENT-SAFE | [row](ai_studio_rows/F2b.md) |
| **F2c** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/F2c.md) |
| **F2d** | 🔨 BUILT, dark (2026-11-03) | HIGH | AGENT-SAFE | [row](ai_studio_rows/F2d.md) |
| **F3** | 🔨 BUILT, dark (2026-11-11, Metorite task 1181) | HIGH | AGENT-SAFE | [row](ai_studio_rows/F3.md) |
| **F4** | open | STD | AGENT-SAFE | [row](ai_studio_rows/F4.md) |
| **F5a** | 🔨 BUILT, dark (2026-11-13, task 1183) | STD | AGENT-SAFE | [row](ai_studio_rows/F5a.md) |
| **F5b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/F5b.md) |
| **F5c** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/F5c.md) |
| **F6** | open | STD | AGENT-SAFE | [row](ai_studio_rows/F6.md) |
| **F6a** | 🔨 BUILT, dark (2026-11-14, task 3160) | STD | AGENT-SAFE | [row](ai_studio_rows/F6a.md) |
| **F6b** | open | HIGH | OWNER-GATE | [row](ai_studio_rows/F6b.md) |
| **F6c** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/F6c.md) |
| **F7** | 🔨 819a BUILT, dark (2026-11-13). 819b OPEN | STD | AGENT-SAFE | [row](ai_studio_rows/F7.md) |
| **F8** | open | STD | AGENT-SAFE | [row](ai_studio_rows/F8.md) |
| **F9** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/F9.md) |
| **F10** | 🔨 F10a BUILT (2026-11-12, Metorite task 1423) | HIGH | AGENT-SAFE | [row](ai_studio_rows/F10.md) |
| **F11** | open | STD | AGENT-SAFE | [row](ai_studio_rows/F11.md) |

### Group G — The builder

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **G1** | 🔨 BUILT, dark (2026-11-01) | STD | AGENT-SAFE | [row](ai_studio_rows/G1.md) |
| **G2** | 🔨 BUILT, dark (2026-11-01) | STD | AGENT-SAFE | [row](ai_studio_rows/G2.md) |
| **G1b** | 🔨 BUILT, dark (2026-11-01) | STD | AGENT-SAFE | [row](ai_studio_rows/G1b.md) |
| **G1c** | 🔨 BUILT, dark (2026-11-04) | HIGH | AGENT-SAFE | [row](ai_studio_rows/G1c.md) |
| **BETA-2** | 🔨 BUILT, dark (2026-11-06) | STANDARD | AGENT-SAFE | [row](ai_studio_rows/BETA-2.md) |
| **STU-FIX** | open | STANDARD | AGENT-SAFE | [row](ai_studio_rows/STU-FIX.md) |
| **G1d** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G1d.md) |
| **G3a** | 🔨 BUILT, dark (2026-11-07, task 1188) | STD | AGENT-SAFE | [row](ai_studio_rows/G3a.md) |
| **G3b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G3b.md) |
| **G3c** | 🔨 BUILT, dark (2026-11-07, task 2208) | STD | AGENT-SAFE | [row](ai_studio_rows/G3c.md) |
| **G4** | 🔨 BUILT, dark (2026-11-11, task 1189), the server half | HIGH | AGENT-SAFE | [row](ai_studio_rows/G4.md) |
| **G4b** | 🔨 BUILT, dark (2026-11-12, task 1189) | STD | AGENT-SAFE | [row](ai_studio_rows/G4b.md) |
| **G5** | 🔨 BUILT, dark (2026-11-07, task 1190) | STD | AGENT-SAFE | [row](ai_studio_rows/G5.md) |
| **G6** | open | STD | OWNER-GATE | [row](ai_studio_rows/G6.md) |
| **G7** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/G7.md) |
| **G8** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/G8.md) |
| **G9** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G9.md) |
| **G10** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G10.md) |
| **G11** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G11.md) |
| **G12** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G12.md) |
| **G13** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G13.md) |
| **G14** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/G14.md) |
| **G15** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G15.md) |
| **G16** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G16.md) |
| **G17** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/G17.md) |
| **G18** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/G18.md) |
| **G19** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/G19.md) |
| **G20** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G20.md) |
| **G21** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G21.md) |
| **G22** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G22.md) |
| **G23** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/G23.md) |
| **G24** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G24.md) |
| **G25** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/G25.md) |
| **G26** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G26.md) |
| **G27** | open | STD | AGENT-SAFE | [row](ai_studio_rows/G27.md) |

### Group H — The shell

**Since D147, H1 and H2 run on the package-host registry.** `package_host.md` owns the registry, the scopes, the lifetimes and the slots. PKG-8 moves H1 and H2 onto it, and J2, J3 and M1 share its install path (PKG-9 to PKG-12).

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **H1** | open | STD | AGENT-SAFE | [row](ai_studio_rows/H1.md) |
| **H2** | open | STD | AGENT-SAFE | [row](ai_studio_rows/H2.md) |
| **H3** | open | STD | AGENT-SAFE | [row](ai_studio_rows/H3.md) |
| **H4** | open | STD | AGENT-SAFE | [row](ai_studio_rows/H4.md) |

### Group I — Connectors

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **I1** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I1.md) |
| **I1a** | 🔨 BUILT, dark (2026-11-14), task 1197 | STD | AGENT-SAFE | [row](ai_studio_rows/I1a.md) |
| **I1b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I1b.md) |
| **I1c** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I1c.md) |
| **I2** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I2.md) |
| **I3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/I3.md) |
| **I4** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/I4.md) |
| **I5** | 🔨 BUILT, dark: the backend seam only (2026-11-11) | STD | AGENT-SAFE | [row](ai_studio_rows/I5.md) |
| **I6** | open | HIGH | OWNER-GATE | [row](ai_studio_rows/I6.md) |
| **I7** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/I7.md) |
| **I8** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I8.md) |
| **I8a** | 🔨 BUILT, dark (2026-11-14), task 3168 | STD | AGENT-SAFE | [row](ai_studio_rows/I8a.md) |
| **I8b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/I8b.md) |
| **I8c** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I8c.md) |
| **I8d** | 🔨 BUILT, dark (2026-11-14), task 3217 | STD | AGENT-SAFE | [row](ai_studio_rows/I8d.md) |
| **I8e** | open | HIGH | see row | [row](ai_studio_rows/I8e.md) |
| **I8f** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I8f.md) |
| **I9** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/I9.md) |
| **I10** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I10.md) |
| **I11-0** | 🔨 BUILT, test only (2026-11-14, task 3235) | STD | AGENT-SAFE | [row](ai_studio_rows/I11-0.md) |
| **I11a** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/I11a.md) |
| **I11b** | open | STD | AGENT-SAFE | [row](ai_studio_rows/I11b.md) |

### Group J — Governance

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **J1** | 🔨 J1-1 BUILT, dark (2026-11-05). J1-2 BUILT (2026-11-11) | HIGH | OWNER-GATE | [row](ai_studio_rows/J1.md) |
| **J1-3** | 🔨 BUILT, dark (2026-11-11), task 1818 | HIGH | AGENT-SAFE | [row](ai_studio_rows/J1-3.md) |
| **J2** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J2.md) |
| **J2a** | 🔨 BUILT, dark (2026-11-11, task 1200) | HIGH | AGENT-SAFE | [row](ai_studio_rows/J2a.md) |
| **J2b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J2b.md) |
| **J2b-1** | 🔨 BUILT, dark (2026-11-13, task 3088) | HIGH | AGENT-SAFE | [row](ai_studio_rows/J2b-1.md) |
| **J2b-2** | 🔨 BUILT, dark (2026-11-13, task 3089) | HIGH | AGENT-SAFE | [row](ai_studio_rows/J2b-2.md) |
| **J2b-3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J2b-3.md) |
| **J2b-4** | open | STD | AGENT-SAFE | [row](ai_studio_rows/J2b-4.md) |
| **J2b-5** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J2b-5.md) |
| **J3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J3.md) |
| **J3a** | 🔨 BUILT, dark (2026-11-11, task 1201) | HIGH | AGENT-SAFE | [row](ai_studio_rows/J3a.md) |
| **J3b** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J3b.md) |
| **J3c** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J3c.md) |
| **J4** | 🔨 BUILT, dark (2026-11-11, task 1202) | STD | AGENT-SAFE | [row](ai_studio_rows/J4.md) |
| **J6** | open | STD | AGENT-SAFE | [row](ai_studio_rows/J6.md) |
| **J7** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J7.md) |
| **J8** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J8.md) |
| **J9** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/J9.md) |
| **J10** | 🔨 FLAG AND REGISTRY BUILT (2026-11-11) | HIGH | OWNER-GATE | [row](ai_studio_rows/J10.md) |

#### J1-3 — the route table of `/workflows`

`EXPECTED_ROUTES` in `tests/unit/test_workflows_author_gate.py` holds this table route by route. A new route fails that test until a person gives it a class.

| Class | Routes | Gate |
|---|---|---|
| Read | `GET /workflows`, `GET /workflows/{id}`, `GET /workflows/catalog`, `GET /workflows/catalog/search`, `GET /workflows/{id}/runs`, `GET /workflows/runs/{run_id}`, `GET /workflows/runs/{run_id}/stream`, `POST /workflows/{id}/validate` | `feature:workflows` |
| Run | `POST /workflows/{id}/run` | `feature:workflows`. With `draft: true`, the handler adds `feature:studio` |
| Write | `POST /workflows`, `POST /workflows/{id}/duplicate`, `PUT /workflows/{id}`, `DELETE /workflows/{id}`, `POST /workflows/{id}/copilot` | `feature:workflows` and `feature:studio` (`AUTHOR_GATE`) |
| Live | `POST /workflows/{id}/publish`, `POST /workflows/{id}/versions/{version}/rollback`, `POST /workflows/{id}/disable`, `POST /workflows/{id}/enable` | `feature:workflows` and `workflows:publish`, with no Studio right (rule 2) |
| Module, out of scope | `GET /workflows/modules`, `POST /workflows/modules`, `GET`, `PUT` and `DELETE /workflows/modules/{module_id}`, `POST /workflows/modules/test`, `POST /workflows/modules/{module_id}/test`, `POST /workflows/modules/generate` | `feature:workflows`, as before. J1-3 changes nothing here. STU-Q15 has a decision (2026-11-12): limit Module Studio to admins. That change is not built yet. **Effect, after the flip:** a member without `feature:studio` can still edit or delete a module that a live workflow uses, and the next run reads the new code. The owner kept the module routes out (2026-11-10). Close this before the flip of `WORKFLOW_STUDIO_GATE_ORGS` in an org with live workflows |
| Hook | `POST /workflows/hooks/{hook_token}` | No feature gate. The token is the credential (`core.EXEMPT_ROUTES`) |

- **Validate is a read.** It writes no row and spends no credit. It reads the draft and the module list, and it answers the issues of the graph.
- **The Copilot is a write.** It builds a graph for the editor and spends credits. It also writes a `workflow_modules` row (`copilot._save_module`). The route keeps the router gate, which is the gate of the module routes. So the Copilot needs both features, and J1-3 opens no module write to a person without `feature:workflows`.
- **Paths with no member stay as they were.** The hook, the schedule scan, the event triggers and the service runs check no feature. The F13 chat tool `run_workflow` (`orchestrator/workflow_tools.py`) calls `service.run_published_workflow`. That is use, so J1-3 does not touch it.
- **Who the flip changes.** Migrations 130 and 207 give `feature:*` to the member, manager and admin roles, and the owner holds `*`. So each system role but the guest holds both features, and the flip changes none of them. It changes a person with a deny override on one feature, a guest with a `feature:workflows` allow, and a custom role that lists `feature:workflows` and not `feature:studio`. ⚠️ A custom role that lists its features by name, and that is older than migration 347, has no `feature:studio`. Before the flip in an org, list its custom roles that grant `feature:workflows` and not `feature:studio`.
- **The page.** The Workflows pill, the Workflows section and the Module Studio tab of `/studio` keep reading `workflows` (`lib/studio/library.ts`). The page gate of `/studio` needs `feature:studio`, so each viewer of the pill holds it already. The editor `/studio/workflows/:id` has the same page gate. So J1-3 changes no page, and a write needs only the `workflows` that the pill checks.
- **Finding, kept as it is: the hook token on a read.** `GET /workflows/{id}` (`crud.get_workflow`) returns `hook_token`, `hook_url` and `hook_path` to each holder of `feature:workflows`. The token starts the published workflow from outside, with no session. By rule 1 that is use, not authoring. But a reader who cannot write still holds a credential. A later slice can show the token only to a writer or a publisher. J1-3 records the finding and changes nothing.

### Groups K, L and M — Templates, code, pinned answers

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **K1** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/K1.md) |
| **K2** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/K2.md) |
| **K3** | open | STD | AGENT-SAFE | [row](ai_studio_rows/K3.md) |
| **K4** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/K4.md) |
| **K5** | open | STD | AGENT-SAFE | [row](ai_studio_rows/K5.md) |
| **K6** | open | STD | AGENT-SAFE | [row](ai_studio_rows/K6.md) |
| **K7** | open | STD | AGENT-SAFE | [row](ai_studio_rows/K7.md) |
| **K8** | open | STD | AGENT-SAFE | [row](ai_studio_rows/K8.md) |
| **K9** | open | STD | OWNER-GATE | [row](ai_studio_rows/K9.md) |
| **K10** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/K10.md) |
| **L1** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/L1.md) |
| **L2** | open | STD | AGENT-SAFE | [row](ai_studio_rows/L2.md) |
| **L3** | open | LOW | AGENT-SAFE | [row](ai_studio_rows/L3.md) |
| **L4** | open | STD | AGENT-SAFE | [row](ai_studio_rows/L4.md) |
| **L5** | open | STD | AGENT-SAFE | [row](ai_studio_rows/L5.md) |
| **L6** | open | STD | AGENT-SAFE | [row](ai_studio_rows/L6.md) |
| **L7** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/L7.md) |
| **M1** | open | STD | AGENT-SAFE | [row](ai_studio_rows/M1.md) |

### Group O — Apps for the outside world, and apps over any system (§20)

| Id | Status | Tier | Gate | Row |
|---|---|---|---|---|
| **O1** | open | HIGH | see row | [row](ai_studio_rows/O1.md) |
| **O2** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O2.md) |
| **O3** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O3.md) |
| **O4** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O4.md) |
| **O5** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O5.md) |
| **O6** | open | HIGH | OWNER-GATE | [row](ai_studio_rows/O6.md) |
| **O7** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O7.md) |
| **O8** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O8.md) |
| **O9** | open | HIGH | OWNER-GATE | [row](ai_studio_rows/O9.md) |
| **O10** | open | HIGH | OWNER-GATE | [row](ai_studio_rows/O10.md) |
| **O11** | open | STD | AGENT-SAFE | [row](ai_studio_rows/O11.md) |
| **O12** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O12.md) |
| **O13** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O13.md) |
| **O14** | open | STD | AGENT-SAFE | [row](ai_studio_rows/O14.md) |
| **O15** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O15.md) |
| **O16** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O16.md) |
| **O17** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O17.md) |
| **O18** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O18.md) |
| **O19** | open | STD | AGENT-SAFE | [row](ai_studio_rows/O19.md) |
| **O20** | open | HIGH | AGENT-SAFE | [row](ai_studio_rows/O20.md) |
| **O21** | open | HIGH | OWNER-GATE | [row](ai_studio_rows/O21.md) |

---

## 15. Stages and dependencies

| Stage | Slices | What a member gets | Depends on |
|---|---|---|---|
| **1 · Studio opens** (M1, dark) | A1, A2, B1, D1, E1, F1, F2a, F2b, F2c, G1 | One Studio pane in preview, the skill creator for private skills, the catalog, and the manifest | None. D146 answered STU-Q1 and Q2 |
| **2 · First apps** (M2) | A3, A4, A5, B1c, B2a, B2b, B2c-1, B2c-2a to B2c-2d, C1, C1b, C2a, C2b, C6, C7, C8, D2, D3, D4, D11, D12, F3, F5, F10, G1c, G2, G3, G4, G5, H1, J1, J2, K1, K3 | Build, preview and publish an S2 app in chat. Tables, forms, boards and charts. The typed object store with rules. The inventory and procurement template | J1 waits on STU-Q4. F3 needs WS-67 live |
| **3 · Automations and rich blocks** | B1b, B4, B6, B7, C3, C4, C5, C9, C10, C11, D5 to D9, D13, E2 to E6, E8, F4, F6, F7, H2, I1, I2, I3, I4, J3, J4, J6, K4, K5 | S1 and S3 in full, connector apps, calendar, timeline, mind map, playbooks, first-party logic and the MRP-lite template | H-328 for unattended runs. WS-67 for F3 |
| **4 · Code, outside agents, scale** | B3, B5, B8, C12, D10, F8, F9, G6, G7, H3, K2, L1, L2, M1 | Member logic modules, the code block, MCP access, pinned answers | MCP-P1b, SBX-9, CA-14 (#1042), MCP-P5 (#869) |
| **5 · Outside world** | O1 to O14, with CRM-M8 and CRM-M11 | Public forms and kiosks, WhatsApp flows, payments and bookings, apps over any outside system, and insights (§20) | Stage 2 apps live. STU-Q16 to STU-Q23. H-328 and B6 for unattended runs |

**The three waves of D202.** The use-case brainstorm of task 2326 ordered the new rows in three waves, and the owner answered "looks good" (D202.5). The waves map onto the stages as this table shows. Each row of §14 names its wave.

| Wave | What a member gets | Stage | New rows | Starts after |
|---|---|---|---|---|
| **1 · Trackers, registers and procedures, with no code** | Line items, template and run, the guided step player, photo, signature and scan fields, auto-number, SLA timers, the Knowledge link, record history and the version lock, Mira inside each app, and AI fills that a person confirms | Stage 3, first | C13, C14, C15, C16, C17 (with C4), C21, D14, D19, D22, D24, D25, E9, F11, I5, B9, B10, B11, then the templates K6, K7 and K8 | Stage 2 delivers its first apps (A5, B2c-1, D2b, D11b, PKG-8, F10). D4 to D11 finish first |
| **2 · Documents and reach** | Print layouts (D13), versioned documents with e-signatures, offline capture, translation, video to steps, the 3D viewer, generated images, the channel rows, git save history, and the blocks and the template of the clinic study | Stage 3, second. O15 to O21 join Stage 5 | C18, C19, C20, C22, C23, C24, C25, C26, D16, D17, D18, D20, D21, D23b, D26, D27, D30, E10, E11, E12, G14, G17, H4, B12, B13, B14, B15, J8, J9, J10, O15 to O21, and the clinic rows B17, D31, I6 and K9 (D202.8) | Wave 1. Group O keeps its own order (§20.8) |
| **3 · Code and extension** | The full code IDE with the UI kit and custom screens, the diagram canvas, 3D scenes, then server code after SBX-9 | Stage 4 | L3 first, then J7, L4, G8, G16, L1, L5, L6, G9, G10, G11, G12, G13, G15, G18, G19, G20 to G26, D15, D28, D29. After SBX-9: B5, B8, L7, B16, G7 | L3 passes. If L3 fails, every code row waits for SBX-9 (D202.4) |

**Rows outside the waves.** B18, I7 and K10 serve HR, and the owner put them later on 2026-11-08 (D202.7). G27, the VS Code extension, comes later too (D202.10). `package_host.md` PKG-14c is the slot row of D202.7, in Phase P3 of that spec.

**Build on a connection (D220).** The ladder puts I1, I2, I8 to I11, B1b, IN-9 to IN-11 (`integrations_console.md`) and the CSV import of C11 in rung 7. The demo is task 3167. I3, mail merge, moves to rung 9, the apps for the outside world. IN-12 comes later. IN-3 is an owner gate (task 3176), and §8.1 rule 10 holds until it lands.

**The app frame (D219).** Group AF sits outside the three waves. The ladder puts AF-1 to AF-6, AF-8 and AF-9 in rung 4, after the approval workflow of rung 3. AF-7, AF-10a and AF-10b join rung 6, and AF-11 joins rung 8. AF-1 waits on D4c (task 2955).

**The floor view (D23a, task 3328).** The ladder puts D23a in rung 6, with AF-7 and AF-10b. It changes only how the app frame draws, so it waits on no O3. Its part of the demo of rung 6 is an app that opens with `?floor=1` on a tablet. D23b, the member session on a paired tablet, stays in wave 2, after O3.

**The first dispatchable slice is B1.** It waits on no owner question, writes nothing, and every later group reads it.

---

## 16. Current file paths

Each path was read on `82157e5fd`. A3 and C1 added their paths after that commit. On 2026-11-08 a script checked that each path still exists at `4439b200f`.

**Backend**

- `apps/services/gateway/gateway/routes/workflows/` — `catalog.py` (`_integration_entries`, `_tool_entry`), `tools.py` (`WorkflowToolSpec`, `list_tools`), `core.py` (the router pattern), `search.py`, `triggers.py`, `scheduler.py`, `copilot.py`, `engine/graph.py`, `engine/runner.py`, `engine/handlers.py`, `engine/modules.py`
- `apps/services/gateway/gateway/db.py` (`tenant_session`), `gateway/main.py` (the router mounts), `gateway/push/flags.py` (an `*_ORGS` reader)
- `apps/services/gateway/gateway/studio/skills_store.py` — the F1 skill store, the one writer of `studio_skills` and `studio_skill_versions` (migration `infra/postgres/320_studio_skills.sql`)
- `apps/services/gateway/gateway/studio/packages_store.py` — the A3 package store, the one writer of a Studio package in `apps` (migration `infra/postgres/322_apps_format.sql`). A3 added it after `82157e5fd`. A4 added `publish_version`, `rollback`, `list_versions` and `get_version`, over `app_versions` and `app_audit` (`infra/postgres/114_custom_apps.sql`)
- `apps/services/gateway/gateway/routes/apps/_common.py` (`load_app`, `workspace_dir`, and `record_app_audit_bound`, the audit writer of B2a), `lifecycle.py` (`list_apps`), `pins.py` (`list_pinned_apps`), and `routes/workspace.py` (`_app_workspace_for`) — the App Workshop readers that skip a package
- `apps/services/gateway/gateway/studio/objects_store.py` — the C1 object store, the one writer of `studio_objects`, `studio_object_fields`, `studio_records` and `studio_record_hot` (migration `infra/postgres/323_studio_objects.sql`)
- `packages/acb_skills/acb_skills/hidden_text.py` — the F1b rule of a hidden character in owner text: `find_hidden`, `strip_hidden`, `hidden_problem` and `body_hidden_problem`. The store, the skill routes and the skill tools call it. F1b added it after `82157e5fd`
- `packages/acb_common/acb_common/studio_rules.py` — the C1b grammar of a rule and its pure checks. It holds no SQL
- `packages/acb_common/acb_common/studio_access.py` — the C8a grammar of roles and row rules, the viewer and its scope. A2 and the store call it, and it holds no SQL
- `packages/acb_common/acb_common/studio_fields.py` — the C1 field types and the one check of a spec and of a value. `studio_manifest.py` (A2) imports it
- `apps/services/gateway/gateway/routes/studio/` — `actions.py` (B1, `GET /studio/actions` and `catalog_rows`), `flags.py` (the `STUDIO_ORGS` reader), `skills.py` (F2a, the skill routes on the same router) and `invoke.py` (B2a and B2b-1, the action door on the same router). B2a added `invoke.py` after `82157e5fd`
- `packages/acb_common/acb_common/settings.py`, `env_guard.py` — where an `*_ORGS` flag is declared and guarded
- `packages/acb_common/acb_common/app_manifests/__init__.py` (`every_app`, `door_ops`)
- `infra/postgres/132_workflows.sql` (`workflows`, `workflow_versions`)
- `apps/services/gateway/gateway/routes/apps/runtime.py` — the `window.cc` bridge routes
- `apps/services/gateway/gateway/connections/mcp.py`, `connections/store.py` — org connections and MCP servers
- `apps/services/orchestrator/orchestrator/workflow_tools.py`, `app_tools.py`, `_tool_injection.py`, `_maf_mcp.py`
- `apps/services/ingestion/ingestion/event_hooks.py` — `register_event_sink`, `emit_event`
- `packages/acb_common/acb_common/app_manifests/base.py`, `knowledge.py` — the D136 manifest classes A, B, C and X
- `packages/acb_skills/acb_skills/tool_packs.py`, `skill_families.py`, `integrations.py`, `ask_tools.py`, `write_artifact.py`
- `packages/acb_skills/acb_skills/studio_tools.py` — the chat tools of the `studio` pack. G1c added `studio_save`, `studio_publish`, `manifest_hash` and the approval record of a plan (`PLAN_NAMESPACE`)
- `infra/postgres/114_custom_apps.sql` (`apps`, `app_versions`, `app_grants`, `app_audit`, `app_data`), `123_app_templates.sql`

**J2, the share (read on `7eade1dd0`)**

- `apps/services/gateway/gateway/package_host/viewer.py` — J2a added it: the read of an app by a viewer, `installed_app` and `InstalledApp`
- `apps/services/gateway/gateway/package_host/resolve.py` — `visible_installs`, the one install read of the sidebar and of J2a, over `INSTALLS_SQL`, `read_installs` and `widest_per_app`. J2a added the column `version` of `InstallRow`
- `apps/services/gateway/gateway/routes/studio/packages.py` (`get_package`, the read of a viewer) and `routes/studio/invoke.py` (`_check`, `refusal_for`, `_reader_slot` and `READ_ONLY`)
- `apps/services/gateway/gateway/package_host/sharing.py` (`SHARE_ROW`) and `package_host/flags.py` (`package_host_allowed`)
- `tests/unit/test_studio_package_viewer.py` and `tests/unit/test_studio_package_viewer_r8.py` — the J2a fences
- `workbench/control_plane/src/app/studio/package-preview/page.tsx`, `src/lib/studio/packagePreview.ts` (`previewControls`) and `src/lib/studio/realDoor.ts` (`packageAccess`, `readActions` and `READ_ONLY_SENTENCE`) — the page of a viewer (J2a fix round 1)
- J2b-1 calls the approval functions of `studio/packages_store.py` (PR 1919, on `main`). J2b-2 reads `package_host/promotion.py` (PKG-11a, on `main` in 14cfbddf7)

**I8a, Connect in place (read on `db3a95eb9`)**

- `apps/services/gateway/gateway/routes/studio/connections.py` — I8a added it: `GET /studio/packages/{id}/connections` and `connection_rows`
- `apps/services/gateway/gateway/routes/integrations_connections.py` — `_SETUP_HREF`, `_app_of`, and the provider-level map `setup_href_for` with `may_connect` (I8a)
- `apps/services/gateway/gateway/package_host/resolve.py` (`connection_names`, `met_connections`) and `gateway/connections/store.py` (`met_providers`) — the one read of I1a
- `packages/acb_common/acb_common/connector_list.py` and `connector_list.json` — the connector list of IN-11, the source of `label` and of the scope of a provider
- `workbench/control_plane/src/lib/studio/connect.ts` (the lines and the link) and `src/lib/studio/returnPath.ts` (`safeStudioReturn`, the one guard of `return`)
- `workbench/control_plane/src/components/studio/ConnectSheet.tsx` (`ConnectLines` and the sheet) and `src/app/studio/build/components/ProblemsPanel.tsx`
- `workbench/control_plane/src/app/integrations/page.tsx`, `components/AddMcpModal.tsx` and `lib/connections.ts` (`addMcpAsk`, `prefilledMcpForm`) — the console half of the return

**Frontend** (`workbench/control_plane/src/`)

- `components/GenerativeUINode.tsx`, `components/genUITemplates.tsx`, `components/SandboxedHtml.tsx`, `components/SandboxedReact.tsx`
- `components/board/Board.tsx`, `lib/charts/kinds.mjs`
- `app/calendar/components/TimeGrid.tsx`, `MonthGrid.tsx`, `app/projects/components/TimelineView.tsx`, `TableView.tsx`
- `app/build/apps/lib/ccBridge.ts`, `app/workflows/`
- `lib/nav.ts`, `lib/nav.test.ts`, `lib/shell/AssistantDock.tsx`, `lib/shell/registry.ts`, `lib/shell/seams.test.ts`
- `lib/useCachedResource.ts`, `lib/dataCache.ts`
- `app/api/workflows/[[...path]]/route.ts` (the proxy that F2b copied), `app/api/projects/[...path]/route.ts` (the PATCH precedent), `lib/gatewayPath.ts` and `lib/gatewayPath.test.ts` (the sweep of every catch-all proxy)
- `app/studio/page.tsx`, `app/studio/components/WorkflowsGallery.tsx` (the model of `SkillsGallery`), `components/AppTopBar.tsx`
- F2b added: `app/api/studio/[[...path]]/route.ts`, `app/studio/components/SkillsGallery.tsx`, `SkillForm.tsx` and `SkillStateBadge.tsx`, the pages `app/studio/skills/new/page.tsx` and `app/studio/skills/[id]/page.tsx`, and `lib/studio/skillStarter.ts`, `skillErrors.ts`, `skillForm.ts` and `skillsApi.ts`

**Read for D202, on `9cbfea6a2`**

- `workbench/control_plane/src/lib/compileArtifact.ts` and `app/api/artifacts/compile/route.ts` — chat artifacts compile on the server with esbuild, and alias React to `preact/compat`. Member code does not use this route (§5.4)
- `workbench/control_plane/src/lib/artifactUi.tsx` — the kit `@cc/ui`, which L4 grows
- `workbench/control_plane/src/components/studio/` — `StudioBoard.tsx`, `StudioTable.tsx`, `StudioForm.tsx`, `StudioDetail.tsx` and `StudioPage.tsx`, the blocks that the kit exports
- `workbench/control_plane/src/components/Tabs.tsx`, `components/VoiceInputButton.tsx`, `components/AccessTree.tsx` and `components/MarkdownImage.tsx`
- `workbench/control_plane/src/app/studio/build/page.tsx` — the IDE of G3a
- `apps/services/gateway/gateway/package_host/field_values.py` — `HOST_RECORD_KINDS`, which PKG-14c grows
- `workbench/control_plane/package.json` — React 19.2.4, `@xyflow/react`, `@monaco-editor/react` and `lucide-react`. No three.js

**Tooling**

- `scripts/dev_up.sh` and `tests/unit/test_dev_up.py` (the local stack on one PC, which F2b gives `STUDIO_ORGS=*`)
- `packages/acb_skills/acb_skills/chat_parity.py` (F2d moved the six `DEBT` rows of the skill creator to `COVERED`)

---

## 17. Verification commands

Each slice names its own test files. These commands bind every Studio PR:

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"   # R8 needs a database
uv run python scripts/affected_tests.py --run
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_genui_catalog_lockstep.py tests/unit/test_workflows_engine.py -q
uv run pytest tests/unit/test_studio_packages_store.py tests/unit/test_studio_packages_versions_r8.py tests/unit/test_studio_packages_r8.py -q   # A3 and A4
uv run ruff check apps packages
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/nav.test.ts src/lib/shell/seams.test.ts src/lib/theme/conformance.test.ts
bash scripts/ci_local.sh
```

A slice that changes UI also runs the `visual-review` skill in light mode, at compact density, under a changed accent and at four widths.

---

## 18. Fences (R7)

| Rule | Fence |
|---|---|
| A new table is tenant-scoped | `tests/unit/test_tenant_coverage.py` |
| An admin of each org holds `studio:publish`, and no other role holds a row of it | New with J1-2: `tests/unit/test_studio_publish_seed_r8.py` (R8) |
| The renderer's node list matches the backend's | `tests/unit/test_genui_catalog_lockstep.py` |
| No app mounts its own palette, bell, assistant rail or ⌘K | `src/lib/shell/seams.test.ts` |
| The live pane count changes only on purpose | `src/lib/nav.test.ts` |
| No colour literal or px font in UI code | `src/lib/theme/conformance.test.ts` |
| A manifest names no colour, px size, unknown block or class X action | New with A2 and D12: `tests/unit/test_studio_manifest.py` |
| A connection name has the grammar of I1a, and a field key names no secret. In an org that `STUDIO_CONNECTION_GATE_ORGS` lists, an install with a missing connection is `needs_setup`, and the door refuses each action of it. In any other org, nothing changes | New with I1a: `tests/unit/test_studio_manifest.py`, `tests/unit/test_package_host_resolve.py`, `tests/unit/test_studio_action_door.py`, and the R8 suites `test_package_host_resolve_r8.py`, `test_studio_action_door_r8.py` and `test_studio_run_as_r8.py` |
| Studio copies no connect flow. It sends the browser to the server's `setup_href`, and a return goes only to a path of this site under `/studio/` | New with I8a: `src/components/studio/ConnectSheet.test.ts`, `src/lib/studio/connect.test.ts`, `src/app/integrations/components/mcpConnect.test.ts` and `tests/unit/test_studio_connect_in_place.py`. R8: `tests/unit/test_studio_connect_in_place_r8.py` |
| A package reaches data only through a declared catalog action | New with B2a: `tests/unit/test_studio_action_door_r8.py` (R8) and `tests/unit/test_studio_action_door.py` |
| The class of an object action has one source, and its verbs are the verbs of the A2 grammar | New with B2a: `OBJECT_OP_CLASSES`, pinned by `test_the_object_verb_table_holds_the_five_verbs_of_the_grammar` in `tests/unit/test_studio_action_door.py` |
| The door opens only the object get, the object list and, behind `STUDIO_DOOR_OPS_ORGS`, a class A door op | New with B2b-1, and B2b-2b adds the door op: `test_the_door_opens_the_object_get_the_object_list_and_the_door_op` in `tests/unit/test_studio_action_door.py` |
| A list body takes only the keys of the one input schema | New with B2b-1: `test_the_accepted_keys_are_the_properties_of_the_input_schema` in `tests/unit/test_studio_action_door.py` |
| Only a Studio package owns an object | New with B2a: `test_r8_a_code_app_owns_no_object` in `tests/unit/test_studio_objects_r8.py` |
| A catalog row never carries class X | New with B1: `tests/unit/test_studio_catalog.py` |
| A package is private to its org and its owner, and its manifest passes A2 before each write | New with A3: `tests/unit/test_studio_packages_r8.py` (R8) and `tests/unit/test_studio_packages_store.py` |
| A template is a valid manifest, names only declared blocks and fields, holds no colour, px size or credential, and installs in one step for its member only | New with K1a: `tests/unit/test_studio_templates.py` and `tests/unit/test_studio_templates_r8.py` (R8) |
| The format of an app never changes | New with A3: the trigger `apps_format_fixed`, pinned by `tests/unit/test_studio_packages_r8.py` |
| No App Workshop list, slug route, pin, agent tool or session workspace shows a package | New with A3: `tests/unit/test_studio_packages_r8.py` and `tests/unit/test_studio_packages_store.py` |
| An empty `workspace_path` never means the working directory | New with A3: `tests/unit/test_studio_packages_store.py` |
| Only `packages_store.py` writes a package | New with A3: `test_only_the_package_store_writes_a_package` in `tests/unit/test_studio_packages_store.py` |
| A publish validates the draft against the catalog, and writes a version, the pointer and the audit row in one transaction | New with A4: `tests/unit/test_studio_packages_store.py` (group h) and `tests/unit/test_studio_packages_versions_r8.py` |
| A publish or a rollback never edits a version row. A package never meets the App Workshop review step, and the store never calls `record_app_audit` | New with A4: `test_a_version_row_is_never_updated_or_deleted_and_no_review_is_proposed` in `tests/unit/test_studio_packages_store.py`, and `test_r8_a_publish_after_a_rollback_never_edits_an_old_row` |
| Two publishes of one package get N and N+1 | New with A4: `test_r8_two_writers_on_two_connections_get_n_and_n_plus_1` |
| One field-type module. No other module holds a tuple, a list or a `Literal` of the type names | New with C1: `tests/unit/test_studio_fields.py::test_one_field_type_module` |
| One writer. Only `gateway/studio/objects_store.py` runs SQL against the four object tables | New with C1: `tests/unit/test_studio_fields.py::test_one_writer_of_the_four_tables` |
| Only the triggers on `studio_records` write the gauge `studio_org_usage`, and only `objects_store.py` reads it, bound to the org | New with C10a: `test_one_reader_of_the_org_gauge` in `tests/unit/test_studio_fields.py`, and `test_every_statement_on_the_gauge_is_bound_to_the_org` in `tests/unit/test_studio_org_caps.py` |
| A write past an org cap is refused, a delete always passes, and the gauge stays exact for each writer | New with C10a: `tests/unit/test_studio_org_caps_r8.py` (R8) |
| The object query binds each field key and each value, and its SQL text takes a column and an operator from a constant map only | New with C2a: `test_no_fstring_in_the_query_sql_takes_a_value` and `test_a_hostile_key_or_value_never_reaches_the_sql` in `tests/unit/test_studio_query.py` |
| A query reads one object of one package of one org. A filter on a field that is not hot runs on at most 5,000 records, and a timeout leaves the session usable | New with C2a: `tests/unit/test_studio_query_r8.py` (R8) |
| A hot sort reads its index in order, and a Sort node takes only the rows of one page, on 50,000 records | New with C2a: `test_r8_a_hot_sort_reads_its_index_in_order_and_sorts_only_the_page` in `tests/unit/test_studio_query_r8.py` |
| One statement reads a page and its total, so a write between two reads never puts a record on a page twice | New with C2a fix round 1: `test_r8_a_write_between_two_reads_never_puts_a_record_twice` in `tests/unit/test_studio_query_r8.py` |
| A filter on a field that is not hot reads `data` only for the candidates of the hot filters, and no count or page scans a table | New with C2a fix round 1: `test_r8_a_cold_filter_after_a_hot_filter_reads_at_most_5000_records` and `test_r8_a_count_reads_the_candidates_of_the_page_and_no_table_scan` |
| A checkbox has two states, and `is_empty` alone tests for no value | New with C2a fix round 2: `test_r8_a_checkbox_has_two_states` in `tests/unit/test_studio_query_r8.py` |
| The guard and the check of the hot fields read the snapshot of the page | New with C2a fix round 2: `test_r8_a_bulk_insert_during_a_query_cannot_pass_the_guard` and `test_r8_a_field_that_turns_cold_during_a_query_gives_the_right_page` |
| An aggregate reads hot fields only, and one statement reads its groups, its total and the currency of a money sum. A sum never adds two currencies | New with C2b: `test_r8_a_money_sum_of_two_currencies_is_refused` and `test_r8_a_group_or_a_measure_on_a_field_that_is_not_hot_is_refused` in `tests/unit/test_studio_query_r8.py`, and `test_one_statement_reads_the_groups_the_total_and_the_currency` in `tests/unit/test_studio_query.py` |
| An aggregate keeps at most 50 groups, and the rest fold into `other` | New with C2b: `test_r8_fifty_groups_stay_and_the_rest_fold_into_other` in `tests/unit/test_studio_query_r8.py` |
| An aggregate of 50,000 records reads the hot index, and a hot filter reads no table | New with C2b: `test_r8_an_aggregate_of_50000_records_reads_the_hot_index_and_no_table_scan` and `test_r8_an_aggregate_with_no_filter_reads_each_record_of_its_object_once` in `tests/unit/test_studio_query_r8.py` |
| A constraint name is in the refusal, a batch holds at most 50 writes and an object at most 16 rules. The rules module holds no SQL | New with C1b: `test_rules_module_holds_no_sql` and `test_batch_cap_is_50` in `tests/unit/test_studio_rules.py` |
| No single record call skips a rule. The three record functions call the batch core, and only it runs their SQL | New with C1b: `test_every_record_write_path_runs_the_checks` in `tests/unit/test_studio_rules.py` |
| The store never commits or rolls back. A batch rolls back only its own SAVEPOINT | New with C1b: `test_store_never_commits_or_rolls_back` in `tests/unit/test_studio_rules.py` |
| A batch writes all its rows or none, two writers cannot both pass a rule, and no write crosses an org or a package. The checks need READ COMMITTED | New with C1b: `tests/unit/test_studio_objects_batch_r8.py` (R8), with `test_isolation_is_read_committed` |
| The SQL of a rule check binds each key and each value | New with C1b: `build_group_sum` falls under `test_no_fstring_in_the_query_sql_takes_a_value` in `tests/unit/test_studio_query.py` |
| A sum reads hot rows only: no Seq Scan and a bounded read on 50,000 ledger rows | New with C1b: `test_r8_a_sum_reads_the_hot_rows_of_its_group_on_50000_rows` in `tests/unit/test_studio_objects_batch_r8.py` |
| A wait for the org lock stops at 1 s with `store_busy` for every caller, also on a deadlock at the lock, and a full pool of waiters leaves another org a connection | New with C1b fix rounds 1 and 2: `test_r8_each_caller_of_the_org_lock_gets_the_bound`, `test_r8_a_deadlock_at_the_lock_gives_store_busy` and `test_r8_a_full_pool_of_waiters_leaves_another_org_a_connection` in `tests/unit/test_studio_objects_batch_r8.py`, and `test_every_lock_of_the_org_goes_through_the_bound` in `tests/unit/test_studio_rules.py` |
| On a floor of 0 or less, a write that does not lower a group never reads the group. A floor above 0 holds from the first row. A key with rows and no amount sums to 0 | New with C1b fix rounds 1 and 2: `test_r8_a_write_that_does_not_lower_a_group_reads_no_sum`, `test_r8_a_floor_above_zero_holds_from_the_first_row` and `test_r8_a_group_with_rows_and_no_amount_sums_to_zero` in `tests/unit/test_studio_objects_batch_r8.py` |
| A rule that the store cannot read fails closed | New with C1b fix round 1: `test_r8_a_stored_rule_that_the_checks_cannot_read_refuses_each_write` in `tests/unit/test_studio_objects_batch_r8.py` |
| The hot budget: 8 for each object and 24 for each org, also under two writers at once | New with C1: the trigger `studio_object_fields_hot_budget` and `tests/unit/test_studio_objects_r8.py` |
| A record is company data, so the member purge deletes none | New with C1: `tests/unit/test_admin_member_purge.py::test_the_records_of_studio_objects_stay` |
| `studio_describe` output equals the live manifest schema, the committed schema, the field module and the block list | New with G1b: `tests/unit/test_studio_describe.py` |
| A skill text holds no hidden character. The store refuses it, a publish refuses an old row, and a chat read leaves it out. | New with F1b-1: `tests/unit/test_hidden_text.py`, `tests/unit/test_studio_skills_r8.py` (R8), `tests/unit/test_studio_skills_routes.py`, `tests/unit/test_studio_skills_routes_r8.py` (R8) and `tests/unit/test_studio_skill_tools.py` |
| Only `hidden_text.py` holds the rule. `skills_store.py` and `studio_tools.py` hold no code point list of their own, but the names that F1b-3 removes. | New with F1b-1: `test_the_store_and_the_tools_hold_no_code_point_list_of_their_own` in `tests/unit/test_hidden_text.py` |
| A chat publish needs the approval record of the plan of the exact stored draft, for the member of the run. A fault, another member and a changed draft refuse with no publish | New with G1c: `tests/unit/test_studio_package_tools.py` (fences 1 to 5) and `tests/unit/test_studio_chat_build_r8.py` (R8) |
| A chat save with an id needs the `based_on` of the stored draft, and no result holds stored manifest text | New with G1c: `test_a_save_with_an_id_needs_the_based_on_of_the_stored_draft` and `test_no_result_holds_the_text_of_a_stored_manifest_of_100_kib` in `tests/unit/test_studio_package_tools.py` |
| A refused publish gives the names inside « » and a fix line that the tool owns, never the text of the server | New with G1c: `test_the_problems_are_framed_with_one_fix_line_for_each_kind` in `tests/unit/test_studio_package_tools.py` |
| Only `delete_package` deletes a package from `apps`, and the versions go by the cascade | New with A5c-1: `test_only_delete_package_deletes_a_package` and `test_the_versions_go_by_the_cascade_and_no_statement_names_them` in `tests/unit/test_studio_package_delete.py` |
| The sandbox rules for each org, each person and each version (D202) | §19.10 names one fence for each rule |
| A skill loads only in the state `published`, and only when the member owns it or the org shares it. Org B's skill never loads for org A | New with F3: `tests/unit/test_skill_packs_r8.py` (R8) |
| A skill pack takes a place of the 3 packs, loads in a fixed order, and fails to load with an unresolved variable, an action with no chat tool or a tool that the run does not hold | New with F3: `tests/unit/test_skill_packs.py` |
| A record and its lines are one batch of at most 50 writes, and a line names a parent of its own package and org only | New with C13: `tests/unit/test_studio_lines.py` and `tests/unit/test_studio_lines_r8.py` (R8) |
| Each call of the six record functions and of `read_tree` (D18-2a) passes a `viewer`. Each takes it as a required keyword, and the test lists each reader path. Only the owner's door calls `aggregate_records`, which takes no viewer | New with C8a: `test_each_call_of_the_six_passes_a_viewer`, `test_the_six_take_viewer_as_a_required_keyword` and `test_no_code_calls_a_reader_that_takes_no_viewer` in `tests/unit/test_studio_row_rules.py` |
| A viewer reads only the rows of its grants, and a hidden key leaves each answer. A change or a delete of a record that the viewer cannot read answers not found | New with C8a: `tests/unit/test_studio_row_rules_r8.py` (R8) |
| Each part of a page statement holds the row rule, and a check of a rule reads every record | New with C8a: `test_a_page_with_a_row_rule_holds_it_in_each_part` and `test_a_check_of_a_rule_reads_every_record` in `tests/unit/test_studio_row_rules.py` |
| The door builds the viewer from the session's member only | New with C8a: `test_the_door_builds_the_viewer_from_the_session_member_only` in `tests/unit/test_studio_row_rules.py`, and `test_the_door_passes_the_owner_of_the_session_as_the_viewer` in `tests/unit/test_studio_action_door.py` |
| The second list of hot types in `draftEdit.ts` equals `studio_fields.HOT_TYPES` | New with C8a: `test_the_second_list_of_hot_types_in_draft_edit_equals_the_first` in `tests/unit/test_studio_row_rules.py` |
| A hierarchy refuses a cycle, a parent of another object or org, and more than 20 levels. A tree reads in one statement | New with C18: `tests/unit/test_studio_hierarchy.py` and `tests/unit/test_studio_hierarchy_r8.py` (R8) |
| A delete locks the `apps` row and then the org lock, holds no lock between two steps, and writes its audit row in the transaction of the delete | New with A5c-1: `tests/unit/test_studio_package_delete_r8.py` (R8), and `test_the_lock_order_is_in_the_source` in `tests/unit/test_studio_package_delete.py` |
| A viewer reads only the installed version, through the one install read of the sidebar. A draft never reaches a member who is not the owner. A viewer writes nothing, and the answers to the owner do not change | New with J2a: `tests/unit/test_studio_package_viewer_r8.py` (R8), and `test_the_sidebar_and_the_viewer_read_share_one_install_read` and `test_each_caller_reads_the_owner_first` in `tests/unit/test_studio_package_viewer.py` |
| A read of a viewer past the cap gets 503 `store_busy` and holds no connection. A read of the owner takes no slot | New with J2a: `test_a_viewer_read_past_the_cap_is_503_and_opens_no_second_session` and `test_the_owner_read_takes_no_slot` in `tests/unit/test_studio_package_viewer.py` |
| A field, a rule or a value that a newer version adds never reaches a viewer of the older version, and a filter or a sort on it is refused | New with J2a fix round 1: `test_r8_a_field_of_a_newer_version_never_reaches_a_pinned_viewer` in `tests/unit/test_studio_package_viewer_r8.py` (R8), and section 6b of `tests/unit/test_studio_package_viewer.py` |
| The app page offers a viewer no write, no delete and no IDE | New with J2a fix round 1: `src/app/studio/package-preview/page.test.ts` and `src/lib/studio/realDoor.test.ts` |

---

## 19. Native apps: the contract, the runtime, the data and a full HRMS

**The rule of this section: a Studio app gets no private power.** It gets what a built-in app gets, through the same seams. So a valid app is a native app.

**An HRMS needs no sandbox, no Node and no package.** Rules and first-party actions cover it (§19.8). Sandbox code is the last resort, because no off-box sandbox exists today.

Owner ask, 2026-11-02, in chat. The design is agent-proposed, and it changes no decision. The facts were read at `c793ff0a0`. §12 holds the questions that the owner decides.

### 19.1 The native contract

| What a member does | The seam | State |
|---|---|---|
| Opens the app from the sidebar, ⌘K, the bell, Home or search | `contributes` of the package: `nav`, `jobs`, `needs`, `cards`, `search` | Spec. No point works for an installed package until PKG-8 |
| Types in chat: "apply for leave on Friday" | Each published action is a chat tool (F10) | Not built |
| Enters data in chat | The Form block draws as a chat card and asks for each missing field (F5). A file goes to `attachments`, then a draft record | Not built. `genui_refusal` refuses a bound action in chat today |
| Sees one look | One renderer. The A2 validator refuses a colour, a pixel size and an unknown block | Built |
| Reads another app | `<app>.<op>` through the door, as the viewer | Knowledge only (§19.6). Built dark (B2b-2b), behind `STUDIO_DOOR_OPS_ORGS` |
| Gets a task or a reminder | A human step is a task in `pm_tasks` (E4). A need goes to the bell | Not built |
| Starts other work when a record changes | `studio.record.*` events (C3) start a workflow | Not built |

**Chat parity by construction.** A view can only call an action that the package declares in `uses`. F10 turns each declared action into a tool of the package's pack. So a UI action with no chat path cannot exist, and no second list holds them. This meets the owner's chat-first rule with one mechanism.

**A record value is untrusted text.** A leave reason can hold an instruction to Mira. Each record value that reaches a model carries the untrusted mark of WEB-1. A class B or C action still needs the member's card, so such a text cannot start a write.

### 19.2 The guardrails, stage by stage

The same gates bind a skill (a tool set), a workflow and an app.

| Stage | The gate | State |
|---|---|---|
| **Build** | The A2 validator, catalog actions only, no class X, the secret scan, the caps, the plan card (G2) | Built |
| **Build** | Preview on sample data (G4), dry-run tests with no real write (F4) | G4: server built, client built (G4b). F4: not built |
| **Publish** | One immutable version (A4). A review of the manifest diff by a holder of `studio:publish` (J2) | Version built, review not built |
| **Install** | The consent screen (J6): what the app reads, what it writes, what it sends, who sees it, its runtime kind and its sensitive fields | Not built |
| **Run** | One door, the viewer's rights, approval by action class, the trust ladder (D131), a credit cap, the kill switch (J3), the audit row | Door and audit built for class A. The stop of a package is built dark (J3a). The reads of a run with no viewer are built dark (B6a). The rest are not built |
| **Data** | Row rules, field sensitivity, retention and org caps (§19.7, C8 to C10) | The org caps are built dark (C10a). The rest are not built |

**A package with no chat run has one named human.** A scheduled run has no viewer. B6 makes an admin name a `run_as` member at install. The run holds the rights of that member, cut down to the `uses` of the package. Each write carries `system:package:<id>` and the name of that member. In B6a, a holder of `studio:publish` names the member, because that right installs the app (D180.3).

### 19.3 How an app says what it needs to run

A new `runtime` key names one of four kinds. No key means `none`. The builder picks the lowest kind that works.

| Kind | Runs where | For | Review |
|---|---|---|---|
| **`none`** | The platform | Manifest, rules, workflows, first-party actions and modules (B4). No member code | The usual review |
| **`browser`** | The viewer's browser, in the sandboxed frame: `allow-scripts`, `connect-src 'none'` | A custom screen, a scanner, a bespoke chart (L1). Each call goes through the door | `studio:publish` reads the code |
| **`sandbox`** | A sandbox with no network, on the standard image | Member Python or Node (B5) | A holder of `studio:publish` reads the code and approves one version (D130.3, D187). The author may run a private version on sample data first |
| **`packages`** | The same sandbox, plus one frozen package layer | Code that needs a package outside the image (B8) | The same, and the reviewer reads the package list. Approval comes before the first real run |

```json
"logic": [{
  "id": "late_mark_report",
  "runtime": {
    "kind": "packages",
    "language": "python",
    "packages": {"num2words": "0.5.13"},
    "limits": {"seconds": 120, "memory_mb": 512}
  }
}]
```

**How the kind gets chosen.** Mira asks four questions in order, and the first yes wins.

1. Can a rule do it (a formula, a roll-up, a state machine, a constraint)? Then `none`.
2. Can a workflow or a first-party action do it? Then `none`.
3. Is it only a screen, with no server work? Then `browser`.
4. Does the standard image hold what the code imports? Then `sandbox`. If not, `packages`.

**How the build proves it.** The validator scans the imports of the code against the lock of the image. An import outside the lock and outside `packages` is an error with a JSON path and one sentence that a model can act on. A dry run on sample data then runs at the declared kind. The plan card names the kind and the cost.

**Run-time rules that do not bend.**

- **No network at run time.** D130.2 says that a step that can read Metorite data never gets a host. So `runtime` has no `hosts` key. A call to an outside API goes through `integration:<action>` or an MCP tool, and the platform holds the credential.
- **Input in, proposed writes out.** The code gets one JSON input through the door. It holds no database credential and no token. It returns proposed writes, and the platform applies them (§3.7).
- **A restricted field does not enter a sandbox** unless the logic names it and the reviewer approves that name.
- **The limits are ceilings, and they are the broker's** (`settings.py:1418-1436`): 1 CPU, 1 GiB of memory, 300 s, 256 processes, 2 GiB of disk. New: at most 5 MiB of input, 5 MiB of output and 5,000 proposed writes.
- **Cost.** A step costs at least 1 credit (D130.4). No sandbox meter exists yet, so sandbox runs stay off until one does.

**⚠️ Where it runs today: nowhere.** BH-3 and SBX-9 are on the same box, and SBX-9 is an owner gate. The true off-box step is SBX-10, on a hosted microVM service. It needs an account (SBX-G3) and a data processing agreement before tenant data goes there (SBX-Q7). Each box has 2 vCPU, about 7 GB and no `/dev/kvm`. So plan each Stage 2 and Stage 3 template at kind `none`.

### 19.4 The standard image

A member installs nothing to use these. A change to this list is a Metorite PR with a measured size. The image stays at or below 1.2 GB on disk. It measures 1.05 GB to 1.13 GB today (`maf_coding_engine.md`).

| Layer | Contents | State |
|---|---|---|
| **Runtimes** | Python 3.12, Node 22 and npm, git, bash, procps | In `Dockerfile.coding-sandbox` |
| **Python data** | pandas, numpy, scipy, duckdb, networkx | In the lock |
| **Python files** | openpyxl, xlsxwriter, python-docx, python-pptx, pypdf, pdfplumber, reportlab, fpdf2, pillow | In the lock |
| **Python charts and text** | matplotlib, seaborn, jinja2, markdown, tabulate, pyyaml, python-dateutil, beautifulsoup4, lxml, requests | In the lock. `requests` has no network at run time |
| **Python, to add** | pydantic, babel, num2words, qrcode | Proposed |
| **Node, to add** | date-fns, decimal.js, zod, csv-parse, exceljs, pdf-lib | Proposed. The image holds no npm package today |
| **Browser** | react 18.3.1, react-dom 18.3.1, lucide-react 1.17.0 | In the T2 vendor cache |
| **Left out** | LibreOffice, Pandoc, Tesseract | Left out on purpose, for size. OCR and documents go through first-party actions |

### 19.5 What a member may install, and how much

**An install happens once, at build time, never at run time.** The build step has a network that reaches the registry proxy only. It writes one layer, which the store freezes by hash and ties to one package version. A change of a package is a new version and a new review.

| Rule | The limit |
|---|---|
| **Sources** | PyPI and npm through one proxy (`pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`). No git URL, tarball URL, private index or local path |
| **Pinning** | An exact version and a hash for each package, also for each indirect one |
| **No build code** | Python takes wheels only (`--only-binary :all:`). npm runs with `--ignore-scripts` |
| **No system packages** | No `apt`. A need for one goes to the standard image or to a first-party module |
| **Age** | A version is at least 14 days old, so that the community has time to find a poisoned release |
| **Known risk** | No version with a high or critical advisory. A licence outside MIT, BSD, Apache, ISC and MPL goes to the reviewer |
| **Count** | At most 20 direct packages and 150 with their dependencies |
| **Size** | One download at most 50 MiB. One layer at most 300 MiB unpacked, the cap of T2 today (`install_t2_deps.mjs`). An org holds at most 2 GiB of layers. A layer that two orgs share by hash counts once for each |
| **Time** | An install takes at most 180 s, the T2 limit |
| **Who** | The builder proposes the list. A holder of `studio:publish` approves it (D187) |

### 19.6 Data: existing, new, outside and searchable

**No app runs SQL.** Every route below goes through the door or the store.

| Data | The route | State |
|---|---|---|
| **Existing Metorite data** | `<app>.<op>`, as the viewer. A relation field stores an id, and the read goes through the owning app | Knowledge has a door. CRM and Email wait on MCP-P2a and P2b, People and Calendar on MCP-P4. Attachments, documents and notes have no plan (I4). `people_absences` has no write op |
| **New data** | A typed object (D146.4). A member sees a new table. Each record is a row of shared tables under FORCE RLS. No tenant gets DDL, now or later | C1 built, no route (A5) |
| **Another package** | Off by default. A package names what it `exports`, and a reader needs its install and the viewer's rights (C12) | Not built |
| **An outside database** | An HTTP MCP server in front of it, after B1b, read-only by default. Metorite ships no direct Postgres or MySQL connector, and stdio MCP stays refused | Not built |
| **A file** | A `file` field holds an `attachments` id. A member upload needs a Studio path | Not built |

**Search by meaning.** An object opts in:

```json
"search": {"fields": ["title", "notes"], "semantic": true}
```

- **The store (C5b).** `studio_record_vectors`, a new table with one row for each chunk, `vector(1536)` and an HNSW index, under FORCE RLS. `kb_chunks` refuses a new `source_kind`, and `knowledge_base.md` already puts vectors in the app's own table.
- **The pipeline is the Knowledge one.** A NULL vector on write, an embed after the session, a sweep each 120 s, `text-embedding-3-small` on `tier-embed`, chunks of about 600 tokens. The vector row cascades from the record.
- **The seam.** One `Source` in `retrieval/sources.py` calls `query_records` as the member, so `search_company` finds records with no second search path. It applies C8, so a search never shows a record that the viewer cannot open. C5a built the lexical half. The vectors and `studio_record_vectors` are C5b.
- **The caps.** At most 4 searchable fields on an object and 200,000 chunks for an org. An embed costs 0 credits today, because the card is absorbed, so this cap is the only brake.
- **A restricted field is never embedded**, because the embed sends its text to a vendor.

**Use the right tool for the question.** "How many leaves did Priya take?" is a structured query (C2a, C2b). "Who asked about relocation?" is a meaning search (C5). Mira gets both, and the tool text says which is which. A vector search never answers a number.

### 19.7 Access inside an app, and sensitive data

Today each viewer of a shared app would see every record, and the store holds no sensitivity. An HRMS cannot ship on that. J2a (§14) ships this state, dark: a viewer reads every record of an app until C8 is built.

**A second gap of J2a (fix round 2).** A stored value of a formula or a roll-up follows the rule of the newest version. A viewer of an older version sees the older rule. It needs a changed formula or roll-up while the object holds no records. The J2a row of §14 names the R8 case that closes it.

**App roles and row rules (C8).** A package names roles. An install maps each role to a `group:<slug>` or to named people, so D12 stays the one grant grammar.

```json
"roles": ["hr", "manager", "employee"],
"objects": [{"name": "payslip", "access": [
  {"role": "hr", "read": "all", "write": "all"},
  {"role": "manager", "read": {"where": "manager = $me"}},
  {"role": "employee", "read": {"where": "employee = $me"}, "hide": ["bank_account"]}
]}]
```

- **A closed grammar.** A `where` is `<person field> = $me`, and that field must be hot. Nothing else in v1.
- **One place.** The store applies the rule in its one reader and its one writer, and `viewer` becomes a required input. So the door, chat, search and workflows inherit it, and none can skip it.
- **A residual risk.** RLS stays org-only, so a row rule is an app-level check. The one-reader fence and an R8 suite hold it.
- **`person` has query ops (C8a).** A person field may be hot, and it takes eq, ne, in and is_empty. It takes no sort and no group.
- **The state (2026-11-11).** C8a built the grammar and the one reader and writer, dark. C8b adds the role map of an install. C8c, C8d and C8e bind search, workflows and the MCP door (§14).

**Field sensitivity (C9).** A field takes a `sensitivity`.

| Level | Example | Effect |
|---|---|---|
| `normal` | Leave type | None |
| `personal` | Phone, address, punch logs | Left out of logs and search snippets. Not embedded unless the object says so. It stays hot and filterable, because attendance queries it |
| `restricted` | Salary, bank account, PAN, Aadhaar | Encrypted in `data` with a per-org key. Never hot, never filtered, never embedded. Each read writes an audit row with no value. Left out of exports. A package with such a field must declare `access` |

**A formula over a restricted field.** Its result is restricted too. It runs in the gateway, and a total for a pay review is added in Python, because SQL cannot read the ciphertext. Only a role with `read: all` gets a total.

**A model never sees a restricted value.** A tool result holds `[restricted]`. The card takes the value from the door straight to the viewer's browser. So Mira can show a member their pay and never read it. The fence: a test runs every chat path over a restricted field and greps what reaches the model.

**Biometric data.** Metorite stores a punch: employee code, device, time and direction. It never stores a fingerprint or a face template. The template of K5 holds no such field, and review refuses one.

**Retention (C10).** An object takes `retention_days`, and a sweep deletes older records (C10b). The org caps are built (C10a): 1,000,000 records and 2 GiB of JSON for an org. More hot fields wait for a measure (C10c). 200 people with 4 punches a day make about 250,000 records a year, and an HRMS plus an inventory app pass today's 24 hot fields.

**⚠️ The law.** `people_center_app.md` keeps pay out of the directory, and says that it belongs "in a payroll module with its own access model". C8 and C9 are that model. WS-37 (DPDP) has no spec yet, and no purge reaches a record. A lawyer reads this design before real HR data goes in (STU-Q11).

### 19.8 The worked example: an HRMS that an HR team builds

Kind `none` for the whole app. K5 ships it as a first-party template, so the HR team starts from it and changes it.

**Objects:** employee, shift, punch, attendance day, leave type, leave balance, leave request, salary structure, statutory rate (with its dates), pay run, pay line, payslip.

| Step | The member's view | Built on | Missing |
|---|---|---|---|
| 1. Build | HR describes it in chat, and Mira plans it. HR previews it on sample data | G1, G2, G3, G4, K5 | **A5**: no route creates a package, an object or a publish. Without it, nobody builds anything |
| 2. Attendance | The device pushes punches, or HR uploads a CSV | An object with `retention_days`, the org token | **C11** (a signed ingest and a CSV import). **H-328**: webhooks read `workflows` unbound under FORCE RLS |
| 3. Leave request | "Apply for leave on Friday" in chat, or the form | Form card (F5), state machine (C6), balance as a roll-up (C7), a rule against a negative balance (C1b) | F10, F5, C6, C7 |
| 4. Leave approval | The manager gets a card and approves | Human step (E4) | E4. A named approver: the approval node files an inbox proposal today |
| 5. It shows everywhere | Approved leave appears in Calendar and in Out today | A People door that writes `people_absences` | **I4** and MCP-P4 |
| 6. Pay run | HR opens the month, and each person gets a pay line | Schedule trigger (E3), formulas and roll-ups (C7), statutory rates as data | E3 and H-328, C7 with exact money rounding, **E8** (a batch), **B6** (no viewer) |
| 7. Pay review | HR sees totals and each line, and approves once | State machine (C6), one card for the run | STU-Q14 |
| 8. Slips | Each line gets a PDF slip from one template | `render_pdf`, a template block | **D13**: no action makes a PDF. The `document` kind has no table, no header logo and no JPEG. A `file` field holds only an id |
| 9. Email | Each person gets one mail with one attachment | The Email door, the throttle of `whatsapp.broadcast` | **I3**: no bulk send, no merge, no org sender, no send limit. STU-Q13 |
| 10. Self-service | An employee asks "show my last slip", and only their slip shows | Row rule, a chat card | **C8**, **C9**. STU-Q12 for a person with no seat |
| 11. Native | Sidebar, ⌘K, the bell ("3 leaves wait"), a Home card | PKG-8, H1, H2 | PKG-5b part 2, PKG-6, PKG-7, PKG-8 |

**A pay run is a batch.** C1b caps one batch at 50 writes, and a lock serves one writer for each org. So 300 lines cannot be one transaction. E8 gives one approval, then chunks of 50, with a key for each item, so a stopped run resumes and pays no one twice. The same primitive serves the slips and the mail.

**Money.** C7 holds money as whole minor units, and it names the rounding of each step. It has no float. A pay line keeps the rates that it used. A correction is a new adjustment line, because an approved run never changes. Metorite calculates what HR configures, and it does not certify statutory compliance. The template says so.

**This calculates pay and sends slips. It does not move money.** A bank file is a later act, and D131.7 keeps it on Ask each run.

### 19.9 What is missing, in build order

| # | Missing | Slice | Blocks |
|---|---|---|---|
| 1 | Routes that create a package, define objects and publish | **A5** (new) | Every app |
| 2 | The door runs an op of an app, and the catalog lists the actions of a package | B2b-2, B1c | Every app |
| 3 | Each action is a chat tool, and a form works in chat | **F10** (new), **G1c** (new), F3, F5 | Native chat |
| 4 | Install and the shell points | PKG-5b part 2 to PKG-8 | Native shell |
| 5 | Rules | C6, C7 | Business logic |
| 6 | Roles, row rules and sensitivity | **C8**, **C9** (new) | Any data on people |
| 7 | Unattended runs that bind the tenant, and a run with no viewer | H-328, E3, **B6** (new, B6a built dark) | Every schedule |
| 8 | Direct writes from the app's screen, and delegated writes with approval (§20.4) | **B2c-1**, B2c-2 after MCP-P1b-1 | Every write |
| 9 | Ingest, batch, documents and mail merge | **C11**, **E8**, **D13**, **I3** (new) | HR, finance, operations |
| 10 | Doors for People and Calendar, and a plan for the rest | MCP-P4, **I4** (new) | Reach into built-in apps |
| 11 | Retention, caps, consent and search | **C10**, **J6** (new), C5 | Scale and trust |
| 12 | Code: the runtime key and custom screens in the browser, then the sandbox, its meter, layers and warm runtimes | **B7**, L3, J7, L4, L1, then SBX-9, B5, **B8**, L7 (D202) | Member code only |
| 13 | Data shared between packages | **C12** (new) | App suites |
| 14 | The template that proves all of it | **K5** (new) | The owner's example |

**Fences for this section.** Each slice names its own when it lands. The first four are: a UI action with no chat tool fails (F10), a member reads no record of another member past a row rule (C8), a restricted value reaches no model (C9), and the validator refuses a `hosts` key in `runtime` (B7).

### 19.10 The sandbox, per organization and per person (D202.3)

The owner accepted choice 3 of task 2326 on 2026-11-08. Before it, the rules sat in §19.3, §19.5, §19.7, D130, D187 and `agent_sandbox.md`. This table holds them in one place, and the owning text of each rule stays where the table names it. Each rule names its fence (R7). "New with X" means that slice X builds the fence, so the rule is advisory until X lands.

| # | Rule | Owner | Fence |
|---|---|---|---|
| | **Each organization** | | |
| 1 | No org shares a run with another org. A warm runtime, a build and a container each serve one org | L7, `agent_sandbox.md` §4.6 | New with L7: `test_a_runtime_of_one_org_never_runs_a_call_of_another` |
| 2 | Metorite stores a package layer once only when the hash is the same. The layer counts against the cap of each org that uses it | §19.5 | New with B8: `test_a_shared_layer_counts_for_each_org` |
| 3 | Each org owns its secrets, connectors, credits and caps | D4, `connections/store.py`, D19.3 | `tests/unit/test_tenant_coverage.py` for each table. The rest is advisory |
| 4 | One admin switch stops all code of the org: each screen within 10 s, and each server run before it starts | J7 | New with J7: `test_the_org_code_switch_stops_each_code_path` |
| | **Each person** | | |
| 5 | Writing code is off by default. An admin turns it on for a group (`group:<slug>`, D12) in Studio settings. The code right is a Studio setting, not a permission: the access model gives a permission to a role or a person only (D180.4), and Studio adds no permission past `feature:studio` and `studio:publish` (D180.5). The owner confirmed the label on task 2326 (D202) | J7 | New with J7: `test_the_code_right_is_off_by_default` |
| 6 | Every Studio member still builds with blocks | D180.1 | `tests/unit/test_studio_route_gate.py`. New with J7: a member without the code right saves a manifest |
| 7 | A holder of `studio:publish` approves each version with code, and each package list, before it reaches anybody but its author | D180.3, D187.2, B8 | New with SBX-6 and B8: `tests/unit/test_studio_code_approval_r8.py` |
| 8 | Code reads and writes with the rights of the viewer, never of the author | §9 item 2, D187.2 | `tests/unit/test_studio_action_door_r8.py` for the door. New with L1: `test_a_screen_call_runs_as_the_viewer` |
| 9 | A restricted field never enters a sandbox, unless the logic names it and a reviewer approves that name | §19.3, C9 | New with C9 and B5: `test_a_restricted_field_never_enters_a_sandbox` |
| 10 | A run with no viewer runs as one named member, cut down to the `uses` of the package | B6, §19.2 | `tests/unit/test_studio_run_as_r8.py` for the reads (B6a). The writes stay advisory until a later slice of B6 |
| 11 | **AI output on a restricted field is always a suggestion that a person accepts (C21).** It never writes to a record by itself, and it never prescribes. The AI reads such a field only after an admin opened it to the AI (D174.6) | C21, C9, task 2327 | New with C21: `test_ai_output_on_a_restricted_field_is_a_suggestion`. New with K9: `test_no_delegated_write_reaches_a_prescription` |
| | **Each version** (Dynamic Apps, §11.2) | | |
| 12 | Build once in a throwaway sandbox. Serve the version from warm runtimes with limits, kept for each org and each version. A call never starts a build | L7 | New with L7: `test_a_second_call_of_one_version_starts_no_build` |
| 13 | **An atomic switch.** The live pointer moves only after a complete build and a smoke test that passes. A failed build never replaces the live version | A4, L7 | `test_r8_a_failure_after_a_write_rolls_back_the_version_and_the_audit` (A4). New with L7: `test_a_failed_build_never_moves_the_live_version` |
| 14 | **Host functions are the only way out.** A screen calls the `window.cc` bridge, and server code calls the door client (SBX-13). No token, key or database link enters the code | §19.3, SBX-2, SBX-13 | New with L1: `customScreenFrame.test.ts`, the policy of the frame. New with SBX-2: `SANDBOX_ROUTES` and its coverage fence (`agent_sandbox.md` SBX-2). B7 refuses a `hosts` key |
| 15 | The server stores the source of a screen, and never a bundle that a browser sent. Each viewer compiles the approved source | L1, §5.4 | New with L1: `test_a_version_stores_source_and_no_bundle` |
| 16 | Each version obeys the limits table below | L6 | New with L6: one refusal test for each limit |
| 17 | Each failure gives one code from a fixed set, with one sentence | L6 | New with L6: `tests/unit/test_studio_code_error_codes.py` and `src/lib/studio/codeErrors.test.ts`, both golden |
| 18 | Member code runs only in the off-box sandbox (server code) or in the viewer's browser (screens). A capture of a screen (G13) and a compile (L1, L3) run in the member's browser | D127.2, D146.1 as D202.4 amends it | Advisory. The review of each code slice checks it |

**The limits for each version (proposed).** Dynamic Apps sets 64 MB of heap, a 30 s run, a 1 MiB request and a 4 MiB response. It also sets 4 MiB of source, 2,000 files and 256 dependencies. The numbers below stay inside the ceilings that the spec already holds. They stay proposals until L6 measures them.

| Limit | Screen (kind `browser`) | Server code (kinds `sandbox` and `packages`) | Ceiling today |
|---|---|---|---|
| Source of one version | 256 KiB in at most 100 files | 256 KiB in at most 100 files | `app_versions.files` holds 262,144 bytes (`agent_sandbox.md` §7.2) |
| Compile or build time | 10 s in the browser | 180 s for a build | 180 s for an install (§19.5) |
| Memory | The limit of the browser | 256 MiB for each runtime | 1 GiB (§19.3) |
| One call | No limit of its own. Each door call keeps the limits of the door | 30 s | 300 s (§19.3) |
| Input of one call | 1 MiB for each door call | 1 MiB | 5 MiB (§19.3) |
| Output of one call | 4 MiB for each door answer | 4 MiB | 5 MiB (§19.3) |
| Proposed writes of one call | The class rules of the door (§3.2) | 5,000 | 5,000 (§19.3) |
| Queue | 20 door calls a second for each frame | 16 calls for each runtime | None yet |
| Warm runtimes | None | 1 for each org, and an idle runtime stops after 10 minutes | 1 container of this target for each org (`agent_sandbox.md` §5.6) |
| Packages | The standard set only (§5.4) | 20 direct and 150 in all | §19.5 |

**The error codes (proposed).** L6 fixes the set. Each code has one sentence that says what happened and what to do next.

| Code | When |
|---|---|
| `compile_failed` | A screen does not compile. The console names the file and the line |
| `build_failed` | A server build fails. The live version stays |
| `limit_exceeded` | A version or a call passes a limit of the table above |
| `timeout` | A call runs past its time |
| `door_refused` | The door refuses a call, for the reason that the door gives |
| `not_approved` | The version waits for a holder of `studio:publish` |
| `code_off` | The member has no code right (J7) |
| `code_paused` | An admin stopped the code of the org (J7) |
| `runtime_busy` | The queue of a runtime is full. Try again in a few seconds |
| `bad_output` | The output does not match the output schema |
| `contract_unsupported` | A folder pins a version of the Studio API contract that the server does not serve (G20) |

**Health data (task 2327).** D174.6 (STU-Q21) binds each clinical field. C9 restricts it by default, and real patient data waits on WS-37 and a lawyer (STU-Q11). Rule 11 applies to each clinical field. This section does not reopen D174.6.

---

## 20. Apps for the outside world, and apps over any system

The owner asked on 2026-11-03 for full apps that extend Metorite from inside Metorite. Such an app joins user and access management and the other apps, and serves people outside the org. Three examples set the bar:

1. **A hospital.** A patient messages a WhatsApp number. The app collects the details, books a slot, sends a Razorpay link, and confirms after the payment.
2. **An event.** A visitor fills a form on a tablet at a stall. The lead goes into a campaign of the CRM app.
3. **Any outside system.** An org connects Salesforce, Odoo or any API. It gets a full app around that system, with no screen made in advance.

All data that these apps collect must also reach the wider AI of Metorite, so that Mira can answer questions and find insights on it. This section maps each need onto what exists on `origin/main` (read at `12d930cac`), names each gap, and adds the slices of Group O (§14).

### 20.1 What exists, and what does not

The facts below came from a full read of the code on 2026-11-03.

| Area | What exists | What is missing for these apps |
|---|---|---|
| **WhatsApp** | Meta posts to `POST /whatsapp/webhook`. The route splits the batch by `phone_number_id`, finds the org with the SECURITY DEFINER function of migration 230, and binds the tenant. Sends go through the `cloud_api` provider, and a template is used outside the 24-hour window | Each number belongs to **one member** (`wa_accounts.user_id`). The bot answers only a phone linked to a member, and an unknown phone gets a fixed reply. No workflow event for a message, no conversation state, and no unattended send to a person outside the org |
| **Public routes** | `PUBLIC_ROUTES` (`gateway/main.py`) holds only machine endpoints, and each one carries its own auth. CRM-M10 already specifies a public form door | No public form, kiosk, signed link or captcha. Every rate limiter runs in one process. Three specs decline anonymous links |
| **Payments** | Razorpay code serves Metorite's own seat billing only (`customer_console/payments.py`), and it is dark | No payment account of an org, no payment link, no payment event |
| **Inbound events** | `/workflows/hooks/{token}` and the event bus (`emit_event`, `dispatch_event`) with the `pm.*`, Zoho and Gmail topics | The hook reads FORCE RLS tables with no tenant, so it fails for a tenant (H-328). No event for WhatsApp, CRM, Calendar, forms or Studio records |
| **Connections** | `integration_connections` (FORCE RLS, encrypted secrets, one reader in `connections/store.py`). MCP servers over HTTP. A custom API definition table | No OpenAPI import, and no native Salesforce, Odoo or HubSpot connector. Nothing reads the stored custom API keys |
| **CRM** | Leads, contacts, companies, deals and generic modules, with full writes in Native mode | No campaigns (CRM-M8), no public forms (CRM-M10, owner-gated by OPEN-1), and no consent record (CRM-M11). A Mirror org refuses a create |
| **Calendar** | The CE-4 event store, dark behind `CALENDAR_EVENTS` | No bookable slots, no resources, no availability, and no Google or Outlook sync yet |
| **AI over data** | One search seam (`retrieval/sources.py`). A new source is one `Source` row. The knowledge graph of D128 | C5a searches records lexically at query time. C5b (vectors) is open. No "ask my app data" path, and no scheduled insight |
| **Identity** | Members, roles, groups and the grant grammar (`org`, `group:<slug>`, an email). The one entitlement intersect | No identity for a person outside the org. Outside people exist only as rows of each app: `crm_contacts`, `wa_contacts` and `email_contacts` |
| **Outbound** | The Action Broker runs each outward write, with approval where needed | Mail leaves only from a member's own mailbox. No SMS provider. No workflow step sends to a person outside the org |

**An app builds no inbox, calendar or payment system of its own (D202, the clinic study of task 2327).** A clinic app uses the WhatsApp number of the org (O6) and its shared mailbox (task 733). It also uses the Calendar door (O11) and its own Razorpay account (O10). The study found three gaps, and O17, O18 and O19 close them. They are email as a channel, a policy for automatic replies, and conversation threads on the record timeline. O20 adds one team inbox over the same seams.

### 20.2 How an app joins the rest of Metorite

An app is a package (WS-PKG, D147). It joins each system through the seam that the system already owns. It never gets a second user list, a second permission model or a second search.

| Concern | The seam | Slice |
|---|---|---|
| Sign-in and seats | The one member session. A Studio app has no login of its own | None |
| Who may build and publish | `feature:studio` and `studio:publish` through the one entitlement seam. Answered (STU-Q4 and STU-Q4b, D180): each member builds and publishes for themselves, and `studio:publish` gates the share to others | J1 (J1-1 built, J1-2 built, J1-3 built dark behind `WORKFLOW_STUDIO_GATE_ORGS`) |
| Who may use an app, and which records | App roles bind to members and groups through the one grant grammar (`org`, `group:<slug>`, an email). Admins manage people and groups in Settings, as today. Row rules such as `owner = $me` | C8, J2 |
| Sensitive fields | Restricted fields are encrypted, never embedded, and never sent to a model | C9 |
| The sidebar, ⌘K, the bell, Home and settings | The WS-PKG contribution points `nav`, `jobs`, `needs`, `cards` and `settings`, read from one run-time registry | PKG-6, PKG-8 |
| Chat | Each action is a chat tool, and a form works in chat | F10, F3, F5 |
| Search and the wider AI | One `Source` for Studio records, vectors in `studio_record_vectors`, aggregates for numbers | C5, C2b, O14 |
| Other apps' data | A relation field stores an id. Reads and writes go through each app's door, as the viewer | MCP-P2a, MCP-P2b, MCP-P4, I4 |
| Outside agents | A published app's actions join the D136 door | B3 |
| Outside systems | A package names its connections. Credentials stay in `integration_connections` | I1, I2, B1b, O12 |
| Audit, kill switch and cost | `app_audit`, a pause that stops actions within 10 s, credit caps and `usage_event` | J3, PKG-9 |

So an app looks and acts like a built-in app. It uses the one renderer and the one look. It shows in the same sidebar, palette, bell and Home. Mira can use it from chat, and outside agents can reach it over the MCP. Each action runs as the viewer.

### 20.3 Three audiences

| Audience | Who | Identity | What they reach |
|---|---|---|---|
| **Members** | The org's own people, in the shell | The member session | The app's views and actions, as the viewer (C8) |
| **Guests** | A person on a public link, a QR code or a kiosk | None, or the token of a signed link | Only the actions that the surface declares (O2) |
| **Contacts** | A person on a channel: WhatsApp, mail or SMS | A CRM contact, found by phone or email (O5) | Only the steps of the flow (O8), and their own records through a signed link (O4) |

**A guest or a contact is never a member.** They get no session and take no seat. They never see the shell, and they never read a record that the surface or the flow does not give them. Each act of a guest or a contact runs as the install's service identity (B6), limited to the declared actions. The audit names the surface or the flow as the actor.

**Only an org-scope install may face the outside.** A private app or a group app cannot hold a surface or a channel, because WS-PKG §6.1 limits who can see it. An admin turns on each surface and each channel, and can pause it in one step.

### 20.4 Direct acts and delegated acts (a correction to B2c)

The B2c row said that every class B or C action runs only after the member's card. Read as written, a member's own Save in an app's own form would raise a card. Built-in apps do not work that way. In Projects, a member's own edit saves at once, and only a write that Mira proposes waits on a card.

- **A direct act** is a member's own click in the app's screen: Save, a board move, Delete. The click is the approval. A delete asks in the page first. **B2c-1** opens these writes, and it does not wait on MCP-P1b.
- **A delegated act** is a write that Mira, a workflow, a skill or an outside agent starts. It waits on the member's card, through the one approval store of MCP-P1b-1. **B2c-2** opens these writes.

The door must know which kind of caller it serves. A header that any caller can set does not prove it. Two internal tokens cannot do it either. The orchestrator runs inside the gateway process, and every caller holds the same `GATEWAY_INTERNAL_TOKEN`.

B2c-1 uses a signed proof instead. The workbench BFF signs the header `X-Studio-Direct` under a second key, `STUDIO_DIRECT_KEY`, that only the workbench env file and the gateway env file hold. The gateway checks it before it reads the body, and it treats the call as direct only for a member session with no agent scope. A missing or bad proof gives the same 403 `approval_required` as a delegated call. A fence holds that only the proof module and the settings read the key, and that no other code names the header.

### 20.5 The new building blocks (Group O)

Each block reuses the seam that exists, and §14 Group O holds the rows.

1. **One public door (O1).** CRM-M10 already specifies it, and this section adopts that design for every public surface. An opaque token leads to a SECURITY DEFINER lookup, then `tenant_session(org)`. The door sits on its own host with no cookies, and the browser never names the org. It uses a shared rate limiter in Redis, through the prefix wrapper. It also uses a bot trap, an optional captcha, size caps and a flat 404 for any bad token. **CRM forms and Studio surfaces are two users of one door,** and neither one builds a second door.
2. **Surfaces in the manifest (O2).** `surfaces: [{id, kind, view, audience, actions, closes_at?}]`. The kind is a form, a page or a lookup. The audience is `public`, `link` or `kiosk`. It is a new WS-PKG point, `surfaces`, with one owner for each surface id. A submission can land in a state such as `new`, so that a member reviews it.
3. **Kiosk devices (O3).** An admin pairs a tablet with a QR code. The device holds a revocable token for one surface. It shows the surface full screen with no shell, and it clears after each visitor and after an idle time. It takes no seat.
4. **Signed record links (O4).** A link to one record, or to the records of one contact, for "see your booking", "pay" or "cancel". It has one purpose and an expiry. The link holds no org name and no record id in plain text.
5. **Outside people (O5).** `crm_contacts` is the one store for each person outside the org: a patient, a visitor, a customer. A Studio `relation` field can point at a CRM contact. Consent is the record of CRM-M11. A contact found by phone or email is reused, never copied.
6. **Org channel numbers (O6).** An org owns a WhatsApp number that answers outside people. The member numbers of today stay. A package claims a number and an entry keyword through a new WS-PKG point, `channels`. Each pair has one owner. It reuses the webhook split and the SECURITY DEFINER lookup of migration 230.
7. **Channel events and conversation runs (O7).** An inbound message on an org number emits `channel.message`, with the contact found or created (O5). A conversation run on the workflow engine keeps its state for each contact and each flow. The state is the step, the values so far and a timeout. It needs H-328.
8. **Conversation steps (O8).** These are workflow nodes:
   - **Ask:** a field type from `studio_fields`, with the same checks.
   - **Choose:** WhatsApp lists and buttons.
   - **Form:** a WhatsApp Flow.
   - **AI step:** Mira with only the flow's skill and actions. She reads free text such as "a skin doctor on Friday evening" and fills fields. She never acts outside the flow.
   - **Call an action, wait, hand off and end.** A run can wait for a payment or a person, and a hand-off goes to a member's WhatsApp inbox.
   - Inside the 24-hour window a reply is free text. Outside it, a reply is an approved template.
9. **Sends to outside people (O9).** The Action Broker gets three handlers: a WhatsApp template, a mail from an org sender, and an SMS. STU-Q13 and STU-Q20 decide the senders.
   - A send that the manifest declares is approved once, by an admin at publish (D131, STU-Q17). An example is the booking confirmation to that booking's contact.
   - Each other send asks.
   - Each send path checks the suppression list of CRM-M11, and honours opt-out and quiet hours.
10. **Payments (O10).** An org connects its own payment account (Razorpay first) as a row of `integration_connections`.
    - The actions are `payments.create_link` (class B), `payments.get` (class A) and `payments.refund` (class C, always a card).
    - A signed webhook of the provider emits `payment.paid` or `payment.failed`.
    - Metorite never holds money. It never reuses the Customer Console seam, because that seam writes Metorite entitlements.
11. **Bookings (O11).** A pattern, a `slots` block and a template:
    - Resources and their schedules, with the slots computed on read.
    - A hold with an expiry (C6 and an E3 schedule), and no double booking (a C1b `unique` rule on the resource and the start).
    - The booking goes on the member's calendar through the Calendar door (CE-4, MCP-P4).
    - A reminder goes to the member (WS-66) and to the contact (O9).
12. **Apps over any outside system (O12):**
    - **An OpenAPI import.** It grows `custom_api_definitions` into a connection with actions. A GET is class A, another verb is class B, and a DELETE is class C, and an admin may raise a class.
    - **Each call** goes through `outbound_guard.py` with the stored credentials of `connections/store.py`. It has a size cap, and every value carries the untrusted mark (#1444).
    - **A remote object.** An object with `source: {connection, list, get, create?, update?}`. Blocks, the door and F10 treat it as an object, with no local rows.
    - **A mirror.** A scheduled sync of a remote object into the store, with a cursor and a cap, so that search and the AI see it (C5).
    - **The generator.** I2 builds the app from the tool list, and B1b covers an MCP server.
13. **Inbound events for each connection (O13).** `/workflows/hooks` grows into one inbound route for each connection, which binds the tenant (after H-328). Provider events reach a trigger through it, for example Razorpay, Salesforce, Odoo and Shopify.
14. **Insights over app data (O14).** A scheduled insight run for each package (B6) reads aggregates (C2b) and searches (C5). It writes a Home card or a bell item through the WS-PKG points `cards` and `needs`.
    - It never reads a restricted field (C9), and a credit cap binds it (J3).
    - In chat, Mira answers "how did no-shows change this month" with the same tools (F10).

### 20.6 The three examples, mapped

**The hospital: booking by WhatsApp, with payment.**

| Step | What happens | Blocks | State |
|---|---|---|---|
| 1 | A patient messages the hospital number | An org number (O6), a channel event (O7) | New |
| 2 | The flow finds or makes the patient, and asks for consent | A CRM contact (O5), consent (CRM-M11) | New, owner-gated |
| 3 | "Which doctor and which day?" The AI step reads the answer | Conversation steps (O8), objects (C1) | New |
| 4 | The flow offers free slots as a list, and holds one | Bookings (O11), state machine (C6), `unique` (C1b) | Partly built |
| 5 | The flow sends a Razorpay link, and waits | Payments (O10), a wait step (O8) | New, owner-gated |
| 6 | The payment event confirms the booking, and a template confirms to the patient | The provider webhook (O13), sends (O9) | New |
| 7 | The booking is on the doctor's calendar, with a reminder the day before | The Calendar door (CE-4, MCP-P4), WS-66 | Partly built |
| 8 | The front desk sees the day's board, and Mira answers "who did not show this week" | Board (D3), table (D2), F10, aggregates (C2b), insights (O14) | Partly built |
| 9 | Clinical notes stay restricted, and no model reads them | C9, STU-Q21 | Planned |

**The event: a kiosk form into a CRM campaign.**

| Step | What happens | Blocks | State |
|---|---|---|---|
| 1 | A member makes the form in chat, with a live preview | The builder (G1, G2), the Form block (D2a) | Built |
| 2 | An admin turns the surface on and pairs a tablet | Surfaces (O2), kiosk devices (O3) | New (STU-Q16 decided, D174.1) |
| 3 | A visitor fills the form and the screen clears | The public door (O1) | New |
| 4 | The lead joins the campaign, and a duplicate links to the existing person | CRM campaigns (CRM-M8), the CRM door (MCP-P2b), contacts (O5) | Planned |
| 5 | A thank-you mail goes out, with an unsubscribe link | Sends (O9), CRM-M11, CRM-M12 | New, owner-gated |
| 6 | Sales sees the leads in the CRM app, and Mira summarises the day | The CRM app as it is, C5, O14 | Partly built |

**Any outside system: an app around Odoo or Salesforce.**

| Step | What happens | Blocks | State |
|---|---|---|---|
| 1 | An admin connects the system: an MCP server, or an OpenAPI document with a key | Connections, B1b, OpenAPI import (O12) | Partly built |
| 2 | Studio reads the tools and proposes a list, a detail and a form for each object | The generator (I2), remote objects (O12) | Planned |
| 3 | Members open "Odoo stock" in the sidebar and approve stock orders on a card | PKG-8, B2c-2, the approval store (MCP-P1b-1) | Planned |
| 4 | A mirror keeps a copy in the store, so search and Mira see it | The mirror (O12), C5 | New |
| 5 | Odoo events start workflows | Inbound events for each connection (O13) | New |

### 20.7 Use cases by kind of organisation

These are the apps an org will ask for first. Each row names its audiences and the Group O blocks it needs beyond Stages 2 and 3.

| Org | App | Audiences | Group O blocks |
|---|---|---|---|
| Hospital or clinic | WhatsApp booking with payment, intake form, report link | Contacts, guests, members | O1 to O11 |
| Diagnostic lab | Home sample pickup booking, report by signed link | Contacts, guests | O4 to O11 |
| Event or expo | Kiosk lead capture into a campaign, check-in by QR, session feedback | Guests, members | O1 to O5, O9 |
| Real estate | Lead qualification on WhatsApp, site-visit booking, token payment | Contacts | O5 to O11 |
| School or coaching | Admission enquiry form, fee payment links, parent updates | Guests, contacts | O1, O2, O4 to O10 |
| Hardware maker | Public warranty registration, service tickets by WhatsApp, install scheduling, spare-part orders with payment | Guests, contacts, members | O1 to O11 |
| D2C retail | Order status on WhatsApp over Shopify, returns form, COD confirmation | Contacts, guests | O1, O2, O5 to O9, O12, O13 |
| Restaurant or hotel | Table booking, feedback kiosk, banquet enquiry | Guests, contacts | O1 to O3, O5 to O11 |
| Salon or gym | Bookings, memberships, renewal reminders with payment links | Contacts | O5 to O11 |
| Field service | Technician jobs on the phone, customer sign-off by signed link | Members, guests | O4, O9 |
| NGO | Donations with receipts, volunteer sign-up | Guests | O1, O2, O9, O10 |
| Hiring | Public job application, interview booking (beside the HRMS of §19.8) | Guests, contacts | O1, O2, O5, O9, O11 |
| Finance team | Invoice reminders with payment links on WhatsApp | Contacts | O5, O6, O9, O10 |
| Logistics | Delivery proof by drivers, a tracking link for the customer | Members, guests | O4, O9 |
| CA or law firm | Client intake, document collection by signed link, a compliance calendar | Guests, contacts | O1, O2, O4, O9 |
| Any org on Odoo, Salesforce, Tally or HubSpot | A screen and a chat over the system of record | Members | O12, O13 |

### 20.8 Order

Group O starts after Stage 2 delivers its first apps (A5, B2c-1, D2b, D11b, PKG-8, F10). The waves follow the examples:

1. **Public forms and kiosks:** O1, O2, O3, O4 and O5, with CRM-M8 and CRM-M11. This is the event example, after STU-Q16.
2. **WhatsApp flows:** O6, O7, O8 and O9, after H-328 and B6.
3. **Money and bookings:** O10, O11 and O13. This is the hospital example, after STU-Q18.
4. **Any outside system:** O12, with I1, I2 and B1b.
5. **Insights:** O14, with C5 and C2b.

Each wave ships dark and needs an admin to turn on each surface or channel.
