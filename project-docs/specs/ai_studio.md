# AI Studio — skills, workflows and apps that a company builds for itself (WS-STU)

> **Product:** Metorite · **Feature:** AI Studio (`/studio`) · **Updated:** 2026-11-02 · **Owner:** vjvarada
> **Status:** 🔨 **In build.** A1, A2, A3, A4, B1, B1c, B2a, B2b-1, C1, C1b, C2a, C6, C7a, D1, D2a, D3, D11a, E1, F1, F2a, F2b, G1, G1b and G2 are built, and §14 holds the state of each slice. B1, B1c, B2a, B2b-1, F2a and F2b are dark behind `STUDIO_ORGS`, which is empty on every box. B2a, the first cut of the action door, is the first caller of A3, the package store, and of C1, the typed object store. The door runs `object:<name>.get` (B2a) and `object:<name>.list` (B2b-1), and it refuses each other action. A4, publish and rollback, has no route and no caller yet. C2a, the object query, has one caller: the list of B2b-1. C1b (multi-record writes), C6 (state machines) and C7a (formula fields) have no route yet. B2c gives them their first caller. The `studio` tool pack of G1, G1b and G2 gives tools only where tool packs are on, and only to an org in `STUDIO_ORGS`. D2a and D3 draw the Table, Form, Detail and Board blocks on a fake door at `/studio/blocks-preview`, outside the nav. Live data comes with D2b. A1 and E1 are the `/studio` preview pane, which needs neither `STUDIO_ORGS` nor a new feature. The pieces it reuses are built, and §16 names each one.
> **Verified against code on 2026-11-01 at `82157e5fd`.** Each path in §16 was read on that commit.
> **Board:** WS-STU in `work_plan.md` §2. **Decisions:** D127 (one app, three kinds), D139 (one coding harness), **D141** (Studio starts now, and a skill creator replaces the agent creator), **D146** (the owner's answers of 2026-10-31: layered apps, a typed object store, first-party templates). D124, D130, D131 and D136 bind it too.
> **Metorite:** project "AI Studio", umbrella epic #491, this spec task #1135. Each group of §14 has its own epic under #491.
> **Gate summary:** every slice is AGENT-SAFE unless §14 says otherwise. The owner answered STU-Q1, Q2, Q3, Q5 and Q6 and the template question on 2026-10-31 (D146). Four questions stay open: Q4, Q7, Q8 and Q9 (§12).

---

## 0. Thesis

Every company has work that no general product covers. A 3D-printer maker tracks install visits. A telecom team chases tower permits. A finance lead chases late invoices on WhatsApp. Today that work lives in spreadsheets, in WhatsApp groups and in people's heads.

**AI Studio is where a company builds what Metorite does not ship.** A member describes the need in chat. Mira asks a few questions, proposes a plan, and builds it from parts that Metorite already has. The result is one of four shapes (§2): a skill for Mira, an automated workflow, a small app, or all three together.

**The bet is composition first, and code in a box (D146).** A Studio app is data. It names objects, actions, workflows, views and a skill. One renderer draws its views in the one look, from the same blocks that Projects, the Calendar and the CRM use. So a generated app looks native, works on a phone, renders inside chat, and reaches outside agents through the MCP.

**Four layers, each with its own containment (§3.7).** The manifest holds structure. Rules hold formulas, roll-ups, state machines and constraints. Logic modules hold computation, such as an MRP run. A custom UI block holds the rare screen that no block covers. Every layer reaches data only through the one action door. So a company can build a full inventory, procurement or manufacturing app with no hole in the platform.

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
6. **No agent creator.** A member builds a skill that Mira loads, not a second assistant (D125, D141).
7. **No member-authored code on the box.** A member's logic module (B5) and the code block (L1) run only on the off-box sandbox (D127.2, D130). First-party logic (B4) is reviewed repo code, like any gateway route.
8. **No general ledger.** Accounting stays in Tally and Zoho Books. A Studio app posts to them through connectors.

---

## 2. The four shapes

The owner named three use cases on 2026-10-31. A fourth, the skill alone, is the cheapest, so this spec adds it. One package model covers all four. A shape is only which layers a package fills.

| Shape | Layers | Example | Who starts it |
|---|---|---|---|
| **S0 · Skill** | skill | "Write quotes in our format from the deal and the price list" | Any member, in chat or in Studio |
| **S1 · Workflow** | workflow (+ actions) | "Every morning, send overdue invoices to the customer on WhatsApp" | A member with `feature:studio` |
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
- **Multi-record writes and constraints (C1b).** `objects_store.apply_writes` writes several records in one SAVEPOINT, so a stock move writes both ledger rows or neither. Rules run on each write, for example "stock never goes below zero". Five kinds: `range`, `unique` and `group_sum_min` (C1b), `state_machine` (C6) and `formula` (C7a). `acb_common/studio_rules.py` holds the grammar, and §14 C1b holds the rules. The exclusive org lock serializes all record writes of one org. That is a known limit, and a finer lock is a later slice. A wait for that lock stops at 1 s with `store_busy`. That limits how long the writers of one org delay other tenants, and it does not end the delay. §14 C1b holds the numbers.
- **Still to come.** C2b adds group, count, sum and average to the query of C2a.
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
2. **The workflow catalog** (`routes/workflows/catalog.py`): the integration actions of `acb_skills/integrations.py` and the agent nodes.
3. **The organization's MCP servers** (`connections/mcp.py`). Each tool becomes an action. A tool that says it is read-only is class A, and every other tool is class B or C behind `McpPermissionGate`.
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

One JSON document holds the package. Slice A2 writes its schema and its one validator. The example below shows the shape, not the final schema.

```json
{
  "studio": 1,
  "id": "field-visits",
  "name": "Field visits",
  "purpose": "Plan, run and close customer site visits.",
  "team": "service",
  "connections": ["whatsapp"],
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
    {"id": "tell_customer", "uses": "integration:whatsapp.send_template"}
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
| **4 · Custom UI block** | A shop-floor screen, a barcode scanner | A sandboxed frame with the `--cc-*` tokens only. Every call goes through the door | L1 |

**Two kinds of logic module.** A **first-party** module (B4) is reviewed code in the repository, written by Metorite engineers. The MRP run of template K4 is one. A **member-authored** module (B5) runs only on the off-box sandbox, after SBX-9, because D127.2 keeps member code off the box. The workflow Module Studio (`workflows_app.md` §5) keeps its own scope and does not grow into this layer.

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
| **Timeline (Gantt)** | `projects/TimelineView.tsx`, about 2,000 lines (coupled) | Pull its core out to `components/timeline/` (D6) |
| **Process flow** | The workflow editor on `@xyflow/react` (coupled) | A read-only flow block that also shows run state (D8) |
| **Mind map** | — | New, on `@xyflow/react` with a tree layout (D7) |
| **Checklist** | — | New: playbook steps, each human step a task (D9) |
| **Map** | — | New. Needs a map library and tiles (D10, STU-Q8) |
| **Markdown, document** | tiptap 3, the `markdown` primitive | Read-only first |

⚠️ **The Calendar and the Timeline pull-outs touch live apps.** D5 and D6 change no behaviour of the Calendar app or of Projects. Their own tests and a visual review prove that.

### 5.2 The renderer

The renderer is `components/GenerativeUINode.tsx`, grown, not a second one. Today it draws a `{type, props, children}` tree of 16 primitives and 19 templates, and its only interaction is `onAction(string)`, which becomes the member's next chat message. Slice D1 adds:

1. **Data binding.** A node may carry `source`. The renderer resolves it through `useCachedResource` and the catalog door (B2), and passes the rows to the block.
2. **Page state.** The selected record, the filters and the tab live in the URL, so a link opens the same view.
3. **Typed actions.** A button names a catalog action, not a sentence. In chat, a typed action still reaches the member's card.
4. **Lockstep.** `GENUI_NODE_TYPES` in `write_artifact.py` and the front end stay one list, under `test_genui_catalog_lockstep.py`. `source` and the typed action are props of existing nodes, so no set changes. `genui_refusal` refuses `source` in chat.

**Single owner.** `generative_ui_2.md` owns the catalog and the HITL rules of chat UI. D1 extends it and changes none of its rules. D1 closes its Phase 3 item "typed TEMPLATE_CATALOG" as test-held, not generated, and the same PR updates that spec's §3 and §6.

### 5.3 Layout

A page is a grid of blocks, with tabs and an optional side panel. At 390 px the grid becomes one column, and a block may name a phone form of itself (a board becomes a grouped list).

### 5.4 The code block

Some needs no block covers: a custom chart, a scanner, a bespoke calculator. The App Workshop path already runs code in `SandboxedHtml` and `SandboxedReact`, with the `window.cc` bridge (`build/apps/lib/ccBridge.ts`). Slice L1 makes that path **one block inside a package**. It gets the `--cc-*` tokens only, and its bridge calls go through the same catalog door. Build-time code runs on the off-box sandbox only (D127.2).

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

### 6.3 The Studio screen

```text
┌ AppTopBar: Field visits · Draft v3 · Preview ▾ · Share · Publish ───────────┐
│ Outline        │  Live preview (sample data)                │ Assistant dock │
│ ▸ Objects (1)  │  ┌ Visits ─────────────────────────────┐   │ (the shell's,  │
│ ▸ Views (3)    │  │ planned │ on site │ done            │   │  agent =       │
│ ▸ Workflows(1) │  │ ▢ ACME  │ ▢ Tata  │ ▢ Infosys       │   │  studio-       │
│ ▸ Skill        │  └──────────────────────────────────────┘   │  builder)      │
│ ▸ Connections  │  [ Board | Week | Check in ]   📱 390 px  │                │
└────────────────┴────────────────────────────────────────────┴────────────────┘
```

The builder chat is the shell's assistant dock with `pane.agent = "studio-builder"`. Studio mounts no `AgentChat` of its own (rule 10, `seams.test.ts`).

### 6.4 The engine under the builder

A manifest is data, so the builder needs **no sandbox and no code run**. It is a Mira run with the builder tools (G1). That is why S0 to S3 can ship before the off-box sandbox.

The code block (L1) needs code. It runs on the one coding harness, profile `app_builder` (CA-14, D139, task #1042). Studio adds no second harness.

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

**Runtime.** A published skill becomes one WS-67 tool pack for the organization. `tool_packs.choose` loads it on a turn that matches its description, inside the limits of 35 tools per pack and 3 packs per turn. Today packs are code-declared and switched on by environment (`TOOL_PACKS_ORGS`). F3 adds packs read from the database. F3 waits on WS-67 going live.

---

## 8. Connectors and MCP

1. **A package names its connections** (I1): `"connections": ["whatsapp", "mcp:tally"]`. Install checks that each one is connected. If one is not, the install page sends an admin to Integrations, and Integrations lists which apps use each connection. The app never asks a member for a key.
2. **An app over a connection** (I2). Studio reads an MCP server's tool list, or a connector's actions. It proposes a list view and a detail view for each read tool, and a form with an approval card for each write tool. This covers every system Metorite has no screen for.
3. **An organization's MCP tools are actions** (§3.2 source 3). The `McpPermissionGate` card and the admin's "always confirm" setting still apply.
4. **Outside agents reach Studio apps** (B3). A published app's actions join the D136 door, so Claude Code calls `app_read` and `app_write` on it with no new tool. Building apps over the MCP is MCP-P5 (task #869).

---

## 9. Governance

1. **Who builds.** A member with `feature:studio` builds private packages. STU-Q4 sets the default roles.
2. **Run as the viewer.** Every action runs with the viewer's `UserContext`. A view never shows more than the viewer can read in the owning app.
3. **Share.** Private → named people or a group (`group:<slug>`, D12) → the organization. A share to the organization needs a holder of `studio:publish`, who reads the manifest diff and approves one version (the pattern of D130.3).
4. **Versions.** Publishing snapshots an immutable version (`app_versions`, `workflow_versions`). An open workflow run keeps its version. Rollback copies an older version into a new one, and history stays append-only. The install of a version is WS-PKG (`package_host.md`).
5. **Trust ladder.** An S1 workflow starts at "ask each run" and climbs by D131. Five acts always ask: money, a member or role write, a delete, a bulk change, and a send to an outside person whom the scope does not name.
6. **Cost.** Each build and each run has a credit cap. Every model call writes a `usage_event` row (the lesson of H-287).
7. **Kill switch.** An admin pauses a package, a workflow or a skill in one step. New actions stop within 10 seconds.
8. **Audit.** `app_audit` records each publish, rollback, share and action, with the actor and the version. The install record belongs to WS-PKG (`package_host.md`).
9. **Entitlements.** `feature:studio` and `studio:publish` map through the one entitlement seam (old STU-7). Pricing stays flat (`launch_surface.md` §4, STU-Q7).

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

---

## 12. Open owner questions

Each question has one recommendation. A slice that waits on a question says so in §14. **The owner answered five questions and the template question on 2026-10-31, in chat (D146).**

| Id | Question | Recommendation | Answer |
|---|---|---|---|
| **STU-Q1** | Is an app a declarative manifest drawn by one renderer, with code only as one sandboxed block? Or is it code first, as the App Workshop RFC planned? | Manifest first | ✅ **Layered** (D146.1): manifest, rules, logic modules, custom UI block, all through one door (§3.7). The owner asked for full integrated apps such as inventory, procurement and manufacturing |
| **STU-Q2** | Does the library show three kinds, Apps, Workflows and Skills, with "skill" as the member's name for a D127 tool pack? | Yes | ✅ **Yes** (D146.2) |
| **STU-Q3** | Where do installed company apps show in the sidebar? | Under Apps, after the built-in apps, with a small "Company" label. Never a Center (D49) | ✅ **Under Apps, after the built-in apps** (D146.3) |
| **STU-Q4** | Who may build? | **Every member builds private packages.** Owners and admins hold `studio:publish` and may grant it to a role | Open |
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
| **STU-Q15** | Module Studio runs member Python on the box under `feature:workflows`, with an AST allowlist and rlimits. Its own docstring says that it is not a sandbox. Does it stay? | **Limit it to admins now.** Move it into the sandbox with B5, and retire the on-box path | Open |
| **STU-Q16** | (CRM OPEN-1.) May a person outside the org submit to an org's app with no sign-in, through a link, a QR code or a kiosk? | **Yes,** through the one public door (O1): its own host with no cookies, a shared rate limit, a bot trap, an org-scope app only, and each surface turned on by an admin | Open |
| **STU-Q17** | (CRM OPEN-2.) Is a person who submits a form or sends a message "named" for a send that the app declares, such as their own booking confirmation? | **Yes,** for a send that the manifest declares and an admin approved at publish, to that person only, with opt-out | Open |
| **STU-Q18** | How does an org take payments in its apps? | **Each org connects its own Razorpay account,** one click where Razorpay allows it. Metorite holds no money and takes no cut. Other providers later | Open |
| **STU-Q19** | How does an org get a WhatsApp number that answers outside people? | **Metorite joins Meta as a Tech Provider with Embedded Signup,** so an org connects its own number in one step and Meta bills the org | Open |
| **STU-Q20** | Which sender does a mail or SMS to a person outside the org use? | **Mail from the org's own domain** (one provider, DKIM set up in onboarding) first. **SMS later,** through one Indian provider | Open |
| **STU-Q21** | May health or other sensitive personal data go into an app, and may the AI read it? | **Sensitive fields are restricted by default (C9):** encrypted, never embedded, never sent to a model. An admin may open one field to the AI, with a notice in the consent text. Real patient data waits on WS-37 (DPDP) and a lawyer, as STU-Q11 | Open |
| **STU-Q22** | Where do outside people live? | **In `crm_contacts`, the one store,** also for an org that does not use the CRM pipeline. The CRM app shows them as contacts | Open |
| **STU-Q23** | Who pays for outside traffic? | **AI steps spend the org's AI credits through `usage_event`.** Meta and payment fees go to the org's own accounts. No fee for each app (STU-Q7) | Open |

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
| **Agent** | `app-builder` until G1 adds `studio-builder` |
| **Own palette, bell, assistant rail or ⌘K** | None |

---

## 14. Slices

**Tier** is the review tier of `next-ticket.md` step 1b: LOW, STD or HIGH. Every slice ships dark or in preview. Migration numbers are the next free number at build time (R1).

### Group A — The pane and the package

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **A1** | 🔨 **BUILT (2026-11-02).** The Studio pane at `/studio` (preview). One library lists apps (`apps`), workflows (`workflows`) and skills (F1, when it exists). A kind filter reads `?kind=`. Module Studio is a tab. The create, fork, pin and delete flows of apps and the create, duplicate and delete flows of workflows move in. `/workflows`, `/build/apps` and `/build/agents` redirect to it. `nav.ts` holds one preview pane in place of three | The old routes answer 308 to `/studio`. The run page and the Workshop under `/build/apps/{slug}` stay. A new `nav.test.ts` case holds `/studio` as the one preview pane. The mirrors move too: `SHELL_PANES` in `me.py`, `PREVIEW_BAR_HOME` and `app-title-bar.spec.ts`, and `runActivity.test.ts`. The library shows each kind with its owner and state. Apps show only to a member who holds `build.apps`. Visual review in four contexts | STD | AGENT-SAFE |
| **A2** | 🔨 **BUILT (2026-11-01).** Enum tokens are camelCase (`longText`, `multiSelect`, `mindMap`, `processFlow`), as the genUI node types are, and keys stay snake_case. Caps: depth 20, a string of 20,000 characters, a list of 500 items, a document of 256 KiB. The validator never raises on any JSON value. Credential shapes come from one list, `acb_common/secret_shapes.py` (J5 points the older copies at it). The package manifest schema v1 and its one validator: `packages/acb_common/acb_common/studio_manifest.py` (pydantic, pure data, no gateway import), the committed export `studio_manifest.schema.json`, and hand-written TypeScript types in `workbench/control_plane/src/lib/studio/manifest.ts` held equal by a parity test (no code generator, as the genUI lockstep does). `validate_manifest(doc, *, catalog=None)` takes the catalog as an input: a map of action id to class. With no catalog it checks structure only. **The `uses` grammar:** `object:<name>.(list|get|create|update|delete)`, `<app>.<op>`, `integration:<action>` or `workflow:<uuid>` (B1's ids). An `object:` action is checked against the package's own objects. **Not yet in v1:** `rules`, a state machine (C6), a formula or roll-up (C7) and a code block (L1) are reserved keys, and each refusal names the slice that lifts it. A logic module (B4) needs no key: it is a catalog action | Each refusal of §3.7 has a test that fails with a JSON path and one sentence: an unknown block, an action the catalog does not serve, a class X action, a colour, a pixel size, a credential-shaped string, a field that a view names and the object lacks, an undeclared submit, skill action or card, a bad relation, a duplicate id, a select with no options. The §3.6 example validates with no catalog and with a fake one. The committed JSON Schema equals the live export. `test_studio_manifest_ts_parity.py` holds the TypeScript types equal | STD | AGENT-SAFE |
| **A3** | 🔨 **BUILT, dark (2026-11-01).** Migration `322_apps_format.sql`. The package store: `apps` gains `format` only, because `apps.manifest` exists since 114. `format` is nullable with DEFAULT `code`, so an old row reads as `code` with no backfill. A reader takes NULL as `code` too. **Two CHECKs:** `apps_format` holds `code` and `manifest`. `apps_manifest_package` needs a package to hold an object with a number `studio`, in at most 262144 bytes of jsonb text. **A trigger**, `apps_format_fixed`, refuses each change of `format`, so a code app never becomes a package that skipped A2. **The writer** is `gateway/studio/packages_store.py`: `create_package`, `get_package`, `list_packages` and `update_manifest`, each over a session of `tenant_session(org)` and for one member. It runs `validate_manifest` before each write, then checks the JSON text itself. A new package is private, a draft, and has no files. **The slug** is `<manifest id>-<8 hex digits of the new id>`, because `apps.slug` is unique for all orgs. A taken slug writes nothing, and the store takes a new id. **Five readers skip a package:** `list_apps`, `load_app` (so each slug route is 404), `list_pinned_apps`, `orchestrator/app_tools.py` and `_app_workspace_for` of `routes/workspace.py`. `Path('')` is the working directory, so `_common.workspace_dir` refuses an empty or blank `workspace_path` too, for each reader of the column (fix round 1). `update_manifest` refuses a new manifest id, because the slug holds it. No route, no UI and no publish. **For A4:** `app_versions` needs no new column. A version writes `bundle_html = ''` and `bundle_sha256` as the SHA-256 of the manifest JSON, because both columns are NOT NULL. **Later:** a slug key for each org is a contract step of its own | An expand-only migration (R6). R8: org B reads none of A's packages. `test_tenant_coverage.py` passes. `test_studio_packages_r8.py` (`pytestmark = _DB_GATE`): org B and an unbound session read none of org A's packages, and a member reads only their own. An INSERT of the old shape reads as `code`. Each CHECK refuses its case, and a package of exactly the store's limit passes. The format never changes, also for the superuser. An invalid manifest writes nothing. A slug clash with another org leaks nothing. The five readers skip a package and still return a code app. A new manifest id is refused. A source scan finds no other writer of a package. `test_studio_packages_store.py` (no database): validate before write, the size cap and the slug shape | HIGH | AGENT-SAFE |
| **A4** | 🔨 **BUILT, dark (2026-11-01).** No route, no flag and no UI reach it, as with A3 and C1. No migration: every column exists. **Publish, version and roll back**, over `app_versions`. **The install (its scope, its end date and its removal) is WS-PKG** (`package_host.md`, D147). The organization share is J2. The review step is J2 too. **Built** in `gateway/studio/packages_store.py`, the one writer: `publish_version(db, member, package_id, notes, *, catalog)`, `rollback(db, member, package_id, version, *, catalog)`, `list_versions` and `get_version`. Each locks the package row first (`FOR UPDATE`), so two writers get N and N+1, and `UNIQUE (app_id, version)` is the backstop. **Publish** re-reads the stored draft and runs `check_manifest`. Then it runs `validate_manifest` WITH the catalog, so a colour, a px size, an unknown block, an action that the catalog does not serve or a class X action fails to publish (D12). The caller passes `catalog` as `{id: class}` from `routes/studio/actions.catalog_rows`, because a store must not import a route. The version row holds `bundle_html = ''`, `bundle_sha256` (the SHA-256 of the manifest JSON text), `scope_set_hash = ''`, `review_status = 'auto'` and `published_by`. Then `apps.status = 'live'` and `apps.live_version = N+1`. `apps.manifest` stays as it is. `live_version` is the pointer of the author only. `package_install.version` is WS-PKG. **Rollback** makes a NEW version N+1 that copies the older manifest. In the same transaction it sets `apps.manifest` to that copy (the name and the description follow) and `live_version` to N+1. ⚠️ It overwrites the draft, and the audit detail says so. A missing package or version answers None. A rollback to the live version raises. Rollback runs the same catalog checks on the copied manifest, and a refused rollback writes nothing. A `catalog` of None is refused. **Audit:** an `INSERT INTO app_audit` (kind `publish` or `rollback`) in the same transaction. The store never calls `record_app_audit`, which opens its own session. **No review:** the store never proposes `app.publish_review`, and it never updates a version row. The run-keeps-version test belongs to the first slice that runs a package workflow. | R8: a publish makes version N+1 and `live_version = N+1`, and leaves the draft. A rollback to v1 after v2 makes v3 with the manifest of v1, and the draft equals it. A publish or a rollback never edits an old row. Org B and another member see no versions, and cannot publish or roll back. An invalid draft publishes nothing. Two writers on two connections get N and N+1. The audit rolls back with the write. Verify: `uv run pytest tests/unit/test_studio_packages_store.py tests/unit/test_studio_packages_versions_r8.py tests/unit/test_studio_packages_r8.py tests/unit/test_tenant_coverage.py -q`. R8 tests: `test_r8_a_publish_makes_the_next_version_and_moves_the_pointer`, `test_r8_a_rollback_to_v1_after_v2_makes_v3_and_restores_the_draft`, `test_r8_a_publish_after_a_rollback_never_edits_an_old_row`, `test_r8_org_b_and_another_member_see_no_versions`, `test_r8_an_invalid_draft_publishes_nothing`, `test_r8_two_writers_on_two_connections_get_n_and_n_plus_1` and `test_r8_a_failure_after_a_write_rolls_back_the_version_and_the_audit` | STD | AGENT-SAFE |
| **A5** | **The package routes** (task 1412). Today the A3, A4 and C1 stores have no caller, so nobody can make a package. A new file `gateway/routes/studio/packages.py` sits on the router of B1. Each handler takes the raw request and checks `STUDIO_ORGS` first, so an org outside it gets 404 for each body. A body has a cap of 300 KiB (413). The org is the bound session and the owner is `user.email`. A key such as `owner_email` in a body changes nothing. **The routes:** `POST /studio/packages` (manifest; 201; 422 for a manifest that A2 refuses; 409 `package_exists` for the same member and manifest id, with the id of that package; 409 `package_limit` past 100 packages for each member). `GET /studio/packages` (a summary list, with no manifest). `GET /studio/packages/{id}` (the package and its object rows). `PUT /studio/packages/{id}/draft` (manifest; 422 for a new manifest id). `POST /studio/packages/{id}/versions` (notes; 201; 422 for A2 with the catalog; 409 `objects_out_of_sync`; 503 `catalog_unavailable`). `GET /studio/packages/{id}/versions`. Each miss is 404 `Not found`: a bad id, another member, another org, an App Workshop app. **Out:** rollback (A5b), version detail, delete, share (J2), install (WS-PKG), record routes (B2), a route that defines an object by hand, a field that changes or goes, and `set_constraints`. **The rule: the manifest is the only source, and publish projects it into rows.** (1) No route defines an object by hand. (2) Only publish writes rows, so a draft save never changes live data. (3) `objects_store.py` gains `list_objects` and `sync_objects`. (4) `sync_objects` takes the org lock, reads the rows and builds the whole plan before a write. A new object calls `define_object`, a new field calls `add_field`, and a change of `hot` only calls `set_field_hot`. Each other change is a problem: `object_removed`, `field_removed`, `field_changed` (type, many, options, to, required or label) and `required_over_records`. A problem writes nothing and raises `ObjectsOutOfSync` with the list. (5) The route runs `publish_version`, then `sync_objects` on the objects of the new version, in one `tenant_session`. A refusal rolls back, so no version row stays. After each publish, the rows equal the objects of the live version. (6) Records are never rewritten. (7) A5b takes rollback, and its sync passes only when the two object sections are equal. (8) **One lock order:** the `apps` row FOR UPDATE (inside `publish_version`), then the org lock (inside `sync_objects`). `define_object` takes them in the other order, so it has one caller, `sync_objects`. (9) The rules of C1b are not in the manifest yet, and sync leaves them alone. A longer option list of a select is `field_changed` here, and A5b must accept it. **The audit:** publish writes its row in the store. Create and sync write a row through `record_app_audit_bound` (kind `action`) after the session closes. A draft save and a 404 write none. No migration. | A member creates a package whose manifest names an object, publishes it, and the object rows exist. `tests/unit/test_studio_package_routes.py` (no database, at least 30 tests) and `tests/unit/test_studio_package_routes_r8.py` (`pytestmark = _DB_GATE`, at least 30 tests, in the `--must-run` list). R8 for each route: the owner succeeds, another member and org B get 404, an App Workshop app gets 404. A publish makes the rows, and a publish with no change makes none. Each of the four 409 refusals leaves no version row and no object row. Two publishes of one package give N and N+1 with rows made once. Two publishes in one org give no deadlock. Fences: each handler checks the flag first, `packages.py` runs no SQL, each call of `publish_version` or `rollback` has `sync_objects` after it, and `define_object` has one caller. Mutations: drop the flag check, drop the sync, sync before publish, ignore a removed field, swallow `ObjectsOutOfSync`, pass an empty catalog, use `record_app_audit`, read the body before the flag, take the owner from the body. Verify: `uv run pytest` on the two new files and the store, door, skills, catalog and builder suites, the R8 files on a private ladder database, ruff, mypy, `bash scripts/ci_local.sh` | HIGH | AGENT-SAFE. After C1b. Opus builder and Opus review |

### Group B — The action catalog

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **B1** | 🔨 **BUILT, dark (2026-10-31).** Source 2 is empty until a workflow tool registers with an integration, and a tripwire test says so. A manifest row takes its input schema from `agent_apps.args_schema` (AD-9). A workflow row has a null input schema. `GET /studio/actions`: the read-only catalog of §3.2 sources 1, 2 and 4. **Row:** `id`, `title`, `source` (`manifest`, `integration` or `workflow`), `class`, `input_schema` (where the source declares one, else null), `output_schema` (always null: no source declares one yet), `connection`, `app`. **Source 1:** each door op of `every_app()` through `door_ops()`. **Source 2:** each entry of `list_tools()` with an `integration`, read with no live probe and no availability. **Source 4:** the org's workflows with `status = 'published'`, id `workflow:<uuid>`, class B. **Class map:** a manifest row keeps `route.cls`. An integration tool with `read_only` is A, one with `destructive` or `open_world` is C, and every other is B. No row is X. **Route:** a new `gateway/routes/studio/` router, prefix `/studio`, gated by `require_feature_router("workflows")` until J1, mounted beside the workflows router in `main.py`. **Tenant:** `tenant_session` from `gateway/db.py`, the org from `UserContext`, never from input (R5). **Flag:** `STUDIO_ORGS` (`*` for all, default off) in `acb_common/settings.py`, read per call as `push/flags.py` does, and `STUDIO_` in `env_guard.py`. No invoke, no MCP door | `test_studio_catalog.py`: a not-listed org gets 404, a listed org and `*` get 200. The row count is the door ops plus the integration tools plus the published workflows, with no duplicate id. A draft or disabled workflow never appears. The class map holds row by row. No row is X. `test_studio_catalog_r8.py` (`pytestmark = _DB_GATE`): org B's published workflow never appears for org A, red first with the RLS-bound session removed | STD | AGENT-SAFE |
| **B1c** | 🔨 **BUILT, dark (2026-11-02).** Source 5 of `GET /studio/actions`: the object actions and the named actions of the caller's own packages. **Scope: per caller.** Rows come from the packages that the member owns in the bound org, read from the draft manifest as the door of B2a reads it. Another member and another org see none. **Not in `catalog_rows`.** That function feeds publish, skills and the door as `{id: class}`, and two packages with `object:items.list` would collide. A new function, `actions.package_rows`, feeds this route only. **The row:** source `package`, `id` the `uses` string of an object action or the A2 action id of a named action, `app` the package uuid. A named action also has `uses`. An object gives five rows, one for each verb of `OBJECT_OP_CLASSES`: list and get are A, create and update are B, delete is C. A named action takes the class of its target. A named action leaves out when its target is class X, or when the catalog does not serve it. **The speed:** `packages_store.list_package_actions` reads the object names and the action ids with jsonb path reads in one statement, and it loads no whole manifest. It also reads the `title` of each named action, because the row shows it. It reads only packages with `format = 'manifest'`, so an App Workshop app gives no row. No route holds SQL. The statement count does not grow with the packages. `?app=<uuid>` keeps only the rows of that package. A value that is not a uuid is 422. `validate_manifest` does not change. Its `object:` check returns before the catalog check, and the publish catalog stays the org one. No migration | `test_studio_catalog.py` (no database: its `db` fixture fakes the store read, and the R8 suite runs the real SQL) and `test_studio_catalog_r8.py` (`pytestmark = _DB_GATE`, now in the `--must-run` list). The owner sees five rows for each object and one for each named action. Another member and org B see none. Two packages with one object name give two rows with different `app`. A class X target and an action outside the catalog leave out. `?app=` narrows to one package. The statement count is constant. A fence holds `catalog_rows` free of source `package`. Each mutation in the suite docstring turns a named test red | STD | AGENT-SAFE |
| **B1b** | Source 3: the tools of the organization's MCP servers, read from a cached `tools/list` | A tool with `readOnlyHint` is class A, and every other tool is B. A server of another org never appears | HIGH | AGENT-SAFE |
| **B2a** | 🔨 **BUILT, dark (2026-11-01).** The first cut of the invoke door: `POST /studio/apps/{id}/actions/{action}` in `gateway/routes/studio/invoke.py`, on the router of B1. **The order of the checks:** the switch `STUDIO_ORGS` (404 for each body, because the route takes the raw request and FastAPI parses no body first), then the shape of the action (404 for a value that is not an A2 action id), then `packages_store.get_package` for the caller (404 for a package of another member or org, an App Workshop app, or an id that names nothing), then the declared `actions[].id` (403 `not_declared`), then the class (403 `class_x`, or 403 `not_in_catalog` for an id that the catalog does not serve), then 403 `approval_required` for class B and C. A catalog that fails is 503 `catalog_unavailable`. **The one executor** is `object:<name>.get`. It reads the object by its name in the package (`objects_store.get_object_by_name`, new), then the record (`get_record`), each with the package id. The record id is the key `id` of the body, under the id rule of C1. The executor is the only step that parses the body. An object that the package lacks is 404 `object_missing`, and a record id that names nothing is 404 `Not found`. Each other class A action gets 403 `source_not_open`. **The class** of an object action comes from `OBJECT_OP_CLASSES` (§3.2), and the class of each other source from `catalog_rows`. **The audit:** each served or refused call that reaches a package of the caller writes one `app_audit` row (kind `action`) through `routes/apps/_common.record_app_audit_bound`. The row holds the action, its `uses`, its class and the outcome, and no record data. Each text in it has at most 200 characters. A 404 before the package writes no row. The audit is best-effort on purpose, for a class A read only: a failed audit never fails the read. **The org** is the bound session and the member is `user.email`, never request input. **A gap of C1, closed:** `objects_store._PACKAGE_SQL` needs `format = 'manifest'`, so an App Workshop app owns no object. **Not built:** the executors of B2b and the approval of B2c | `test_studio_action_door_r8.py` (`pytestmark = _DB_GATE`, in the `--must-run` list): the owner reads a record, and one audit row lands in the bound org. Another member of the org, and org B with the same email, get 404 on each action. An undeclared action is 403. Class B and C write nothing. A published workflow of org B is not in the catalog of org A. An App Workshop app and a record of another package are 404. A body that names an org changes nothing. With the switch off, each call is 404 and writes no row. Each mutation of the suite docstring turns a named test red. `test_studio_action_door.py` (no database): the switch, the order of the checks, each refusal code, and the verb table against the A2 grammar. `test_studio_objects_r8.py::test_r8_a_code_app_owns_no_object` | HIGH | AGENT-SAFE |
| **B2b-1** | 🔨 **BUILT, dark (2026-11-02).** The list executor of the door: `object:<name>.list`, in `gateway/routes/studio/invoke.py`, beside `object:<name>.get`. B2b elsewhere in this spec means the three rows B2b-1, B2b-2 and B2b-3. **The flow:** `objects_store.get_object_by_name`, then `objects_store.query_records` (C2a), each with the package id of the path. The org is the bound session and the member is `user.email`, never request input. Only the owner of the package reaches it, as in B2a. **The class** comes from `OBJECT_OP_CLASSES` and the declaration from the manifest, so this slice does not need B1c. **The final order of the checks:** the switch, the shape of the action, the app id, the package, the declaration, the class and the approval, as in B2a. Each one runs before the door reads a byte of the body, so a non-owner and a refused action read nothing. Then the body: the door reads `request.stream()` with no session open, because a wait on a slow client must not hold a pool connection. The read stops past `MAX_BODY_BYTES` (1 MiB), and the answer is 413 `body_too_large`. B2c may raise the cap for writes. Then the second check, in a new session: `_check` runs again, so the door reads the package as this member, then checks the declaration and the class on that fresh manifest. **The reason:** a slow client can pass the first check and hold the read open. Without the second check, it reads after it lost access or after the package changed. The owner-only rule hides this today, but J2 (sharing) and C8 (row rules) build on this door. A miss gets the same 404, and a declaration that is gone gets the same 403. An action that now names another source gets 403 `source_not_open`. Then the executor, in that session. **The input:** an empty body means `{}`. The door accepts only the keys of `studio_query.list_input_schema()`: filter, sort, limit and offset. Any other key gets 422. `limit` and `offset` must be an `int` and not a `bool`. The door checks this before the store, because `json.loads` takes `Infinity` and `paging.bounds` raises `OverflowError` on it. An `int` out of range takes the rule of `paging.bounds`, and the answer holds the values that it used. **The answer** has the shape of `RecordPage`: `records`, `total`, `total_at_least`, `offset`, `limit` and `line`. Each record has the shape of the `object:*.get` answer. An object that the package lacks, and None from the store, get 404 `object_missing`. An `ObjectStoreError` gets the same 404, because the body names no id, so a stored value caused it. **The errors:** the door catches `QueryError` before `ObjectStoreError`. Five codes get 422 `{code, message, field}`. A refused body gets the same shape with `query_value_invalid` and no field, and the door reads nothing for it. `query_timeout` gets 503 `{code, message}` with `Retry-After: 2`. The session stays usable after a refusal, and the audit row is still written. The row holds the code and no record data. **The audit:** each served or refused call writes one row. A raw fault writes none, because the session is not safe to write on. ⚠️ **The known limit:** a read holds a pool connection for up to 5 seconds. This is advisory while the surface is owner only and dark. A wider surface needs a cap of reads before it opens. **Not built:** the door op (B2b-2), an integration (B2b-3) and each write (B2c) | `test_studio_action_door_r8.py`, as the app role: the owner lists a filtered, sorted page with the right total, and one audit row lands in the bound org. Another member and org B get 404. An object of another package gets 404 `object_missing`. Each of the six `QueryError` codes maps as stated. An extra key, `limit: true` and `limit: Infinity` get 422 and read nothing. A body past the cap gets 413, and the read stops near the cap. A non-owner, a refused action and an org outside the switch read no byte. No connection of the app role is in a transaction during the read. A package that goes, an owner that changes or a declaration that changes during the read gives 404 or 403, and the door reads no record. After a refusal the session still writes the audit row. Class B and C still get 403 `approval_required`. A class A door op, integration or workflow still gets 403 `source_not_open`. `test_studio_action_door.py` (no database): `OPEN_EXECUTORS` equals exactly the object get and the object list, and the accepted keys equal the properties of `list_input_schema()`. `test_one_writer_of_the_four_tables` keeps SQL out of `invoke.py`. Each mutation of the suite docstring turns a named test red | HIGH | AGENT-SAFE |
| **B2b-2** | The door op: `<app>.<op>` (task 1449). ⚠️ **First, this slice records in this spec the executor of a door op inside the gateway, the viewer rule and the refusals.** No such executor exists today. Only the MCP edge runs a door op: `agent_edge/tools.py::_door_send` builds the call with `app_manifests.build_call` and sends it over HTTP with the member's credentials. A run inside the gateway needs an ASGI loopback that forwards the caller's session, or a direct handler call. Each one is a new seam at the HIGH tier. The slice refuses class B and C, the `off_door` rows and each op whose route needs a body key outside `build_call` | This spec records the executor choice before the build. A class A door op has an R8 test that runs as the viewer. A viewer without the right in the owning app gets that app's own refusal | HIGH | AGENT-SAFE. After this spec records the executor |
| **B2b-3** | The integration: `integration:<action>`, through `routes/workflows/tools.py::execute_tool`. Nothing can run yet. Each registration in `routes/workflows/tools.py` has `integration=None`, so source 2 of the catalog is empty | A class A integration has an R8 test that runs as the viewer | HIGH | AGENT-SAFE. After I1, and after a tool registers with an integration |
| **B2c-1** | 🔨 **BUILT, dark (2026-11-03).** **Direct writes** (§20.4). A member's own act in the app's screen is the approval: a form Save, an inline edit, a board move. The door opens the create, the update and the delete of an object for a DIRECT call, with no card. **Not in this slice:** delegated writes (B2c-2), the door op, integrations, workflows, a batch route (E8), the UI calls (D2b) and the page confirm of a delete (D2b). **The proof.** The orchestrator runs inside the gateway process, so two tokens cannot tell the callers apart. The proof is a signed header, `X-Studio-Direct`, that only the workbench BFF signs. The key is `STUDIO_DIRECT_KEY`, in the gateway `.env` and in the workbench `.env.local`, with the settings field `studio_direct_key`. A key under 32 bytes, or equal to `GATEWAY_INTERNAL_TOKEN` or `LITELLM_MASTER_KEY`, counts as unset. Then no call is direct. The header is `v1.UNIX_SECONDS.HEX_HMAC_SHA256` over the time, the lowercase member email, POST and the gateway path. The window is 60 s, with 5 s of skew. `packages/acb_auth/acb_auth/direct_proof.py` verifies it with `compare_digest`. The door checks it once, before the body read (`invoke._is_direct`). A call is direct only if the proof holds AND `user.agent` is None AND the user is a member whose email has an `@` and does not start with `system:`. A missing or bad proof gives the same 403 `approval_required` as before, so the door gives no oracle. **The door.** `DIRECT_EXECUTORS` holds object create, update and delete. `OPEN_EXECUTORS` stays get and list. The bodies are `{data}` for a create, `{id, data}` for an update and `{id}` for a delete, and no other key (422). One `apply_writes` call with one `Write` runs the change. The store refuses a computed key. **One transaction.** `record_app_audit_in(db, ...)` in `routes/apps/_common.py` writes the `app_audit` row in the session of the write and raises on failure. A failed audit therefore fails the write. The row names its record in `record_id`. A create gives its new id, and an update or a delete gives the UUID of its body. A refusal writes its audit row, leaves the session, and only then answers, because an `HTTPException` inside the session would roll the row back. **Writers.** The door admits at most 3 writes in flight for each org, before it takes a session. The first writer of an org always gets a slot. A second or later writer also needs fewer than 6 in all, so two busy orgs cannot shut out a third. A call past the cap gets 503 `store_busy` with `Retry-After: 1` and holds no connection. The count lives in the process. The gateway runs one uvicorn worker, so it holds for the box. The door catches `StoreBusy` before `ObjectStoreError` and answers 503 with `Retry-After: 1`. A rule-check timeout is 503 with `Retry-After: 2`. **Refusals.** HTTP 422 with `{code, message, rule, fieldErrors}`. `FieldError` gives `field_invalid` and a `fieldErrors` entry for the key. Each `RuleViolation` (`ConstraintViolation`, `MoveViolation`, `FormulaError`) gives `rule_refused`, the `rule` and the message. A write to a formula target gives `computed_field`. `FormulaError` gained an optional `field`, so `fieldErrors` names the target. `RecordError` and any other store refusal give `record_invalid`. A body of the wrong shape gives `bad_input`. A missing record is 404. **BFF.** `/api/studio` signs the proof from `lib/gateway.ts` (`studioDirectHeaders`) for a POST to `apps/ID/actions/ACTION` only. It signs only a request from a page of its own origin (`sameOrigin`), because the session cookie is `SameSite=Lax`. A POST from another origin goes on unsigned and gets 403. It forwards `Retry-After`. It adds no other header and no identity. **Delete confirm:** the server cannot see it and does not need it. D2b holds the test that the Delete button asks first. D2b also maps the HTTP detail of a refusal into its `ok`, `message` and `data` (`readRefusal` in `lib/studio/formModel.ts`). **Deploy.** The code ships dark. With the key unset, no write is direct. `scripts/vps_apply.sh` copies `STUDIO_DIRECT_KEY` from `.env` to `.env.local` and never makes one. It reads the last line of the key, as systemd and dotenv do. `.env.example` documents it. Setting the key on a box is gate `env-write`. Generate it on the box with `openssl rand -hex 32`, then restart `acb-gateway` and `acb-workbench`. **Known limit.** Skills run in the gateway process, so in-process code could read the key. The fence `test_only_the_proof_module_and_settings_read_the_direct_key` and review stop that. | `test_studio_direct_proof.py` (no database): the proof round trip, the window, the key rules, the shared vector with TypeScript, the deploy line, and the fences. `test_studio_action_door.py` (no database): the proof cases, the body keys, the error map and the writer cap. `test_studio_action_door_r8.py` (R8, `_DB_GATE`, `--must-run`): a valid proof writes one record and one audit row in one transaction. A failed audit leaves no record. A call with the bearer and the member email but no proof writes nothing. A proof for another email, path or time writes nothing. Constraint, move and formula refusals write nothing and keep the audit row. Three busy writers get 503 and the fourth is refused at once. Vitest: `src/app/api/studio` and `src/lib/gateway.test.ts`. Each mutation in the docstring of `test_studio_direct_proof.py` turns a named test red | HIGH | AGENT-SAFE for code. The key on a box is env-write |
| **B2c-2** | **Delegated writes** (§20.4): a write that Mira, a workflow, a skill or an outside agent starts. **B2c-2 calls the approval store of MCP-P1b-1 and builds no second one.** For each class B or C call that has no `request_id`, it calls `gateway.app_requests.open_request` with source studio, app studio, the package id as subject, the action id as op, the class from `OBJECT_OP_CLASSES` or `catalog_rows`, and the exact input as the call. It answers 202 with `state: awaiting`, `request_id` and `expires_at`, and it runs nothing. The member decides on `AppRequestCard` through the decision route. The caller then sends the action again with `request_id`. The door calls `acb_auth.app_requests.spend` in the session of the member, with the binding `session:MEMBER:studio:PACKAGE_ID`. It runs the STORED call and ignores the body of the second request. The spend, the write and the `app_audit` row are one transaction, so a failed write or audit un-spends the request. Mira does not ask with `request_confirmation` for a Studio write: the door answers `approval_pending`, and the chat draws `AppRequestCard`. B2c-2 does not touch branch 0, `agent_apps.py` or the edge. A payload over 32 KiB cannot be approved, so it is 413 `payload_too_large`. It also opens a published workflow (`routes/workflows/service.py::run_published_workflow`) | A class B or C action of a delegated caller runs only after the member's card. A refused card writes nothing. A class B case and a class C case on R8, each red under a mutation that skips the spend and under one that spends in a separate transaction from the write | HIGH | AGENT-SAFE. After MCP-P1b-1 and B2c-1 |
| **B3** | A published app's actions join the D136 door: `list_apps` and `describe_app` show it, and `app_read` and `app_write` reach it | Needs MCP-P1b. The MCP coverage fences pass. A package that is not shared with the caller does not appear | HIGH | AGENT-SAFE. After MCP-P1b-2 |
| **B4** | **First-party logic modules** (§3.7 layer 3): a typed function in the repository, served by the catalog as an action. It reads its input through B2, as the caller, and returns proposed writes. B2 applies them in one transaction | A module that returns writes to two objects applies both or neither. A proposed class B or C write waits for the member's card. A module that names an object the package does not hold is refused. The first module is the MRP run of K4 | HIGH | AGENT-SAFE |
| **B5** | **Member-authored logic modules** on the off-box sandbox, with the same contract as B4 | The module runs with no network, no database link and no secret. A share to the organization needs an approval of one version (D130.3) | HIGH | AGENT-SAFE. After SBX-9 (D127.2) |
| **B6** | **A run with no viewer.** An install names one `run_as` member, chosen by an admin. A scheduled or event run holds the rights of that member, cut down to the `uses` of the package. Each write carries `system:package:<id>` and that name (§19.2) | A run cannot reach what `run_as` cannot read. A run stops when `run_as` loses access or leaves. The audit row names both | HIGH | AGENT-SAFE. After H-328 |
| **B7** | **The `runtime` key** (§19.3). The four kinds in the A2 model, the import scan against the image lock, the plan-card line and the rule that the builder picks the lowest kind. `hosts` is no key | No key means `none`. An import outside the lock is an error with a JSON path. A `hosts` key is refused. `studio_describe` serves the kinds. No sandbox runs in this slice | STD | AGENT-SAFE |
| **B8** | **Package layers** (§19.5). The registry proxy, the install at build time, the frozen layer with its hash, the review of the list | Each rule of §19.5 has a refusal test. A run has no network. A new package list is a new version | HIGH | AGENT-SAFE. After SBX-9 and the sandbox meter |

### Group C — Objects

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **C1** | 🔨 **BUILT, dark (2026-11-01).** No route, no flag and no UI reach it. Migration `323_studio_objects.sql` copies the two `apps` blocks of 321 (PKG-4) byte for byte, so the two files apply in either order. **The typed object store** (D146.4, §3.1): an object registry, record tables on shared JSONB rows, the field types of §3.1, validation on each write, and an index budget per org. No per-tenant DDL. Expand-only migration, next free number (R1). **Built:** `acb_common/studio_fields.py` holds the 16 types and the one check of a spec and of a value. A2 imports `FIELD_TYPES` and `validate_field_spec` from it, and A2's `FieldDef` gained `hot`. `gateway/studio/objects_store.py` is the one writer. It holds define, add a field, turn a field hot or cold, and drop for the registry. It holds create, get, update and delete for one record. A read is by id only. Each record function takes a required `package_id`, so a door of one package cannot reach a record of another package. The caller owns the transaction, so C1b can join several calls. **The rules:** a hot field has one of eight types: text, select, number, money, date, datetime, checkbox or email. A hot field takes no `many`. A number is below 10^15 in size, and a value that is not zero is at least 1e-4 in size, because the check refuses a float in exponent form. `v_text` holds the first 256 characters of a text. The options of a select must differ by more than case. A URL holds no user name and no password. A stored field reads with no authoring check, so a stricter rule binds a new spec only. **The lock order:** every write takes the advisory lock of the org first, then the row lock of the object. The org lock is exclusive, so the writes of one org run one at a time. **Hard delete:** a hard delete of the package deletes all its records by cascade (`routes/apps/lifecycle.py:566`). A confirm step that names the records belongs to A4 or J4. **Not built:** no TypeScript twin of the value check. D2 adds it, with a parity test as A2 has | Each field type refuses a bad value with its field name. R8: FORCE RLS holds on each table, and org B reads nothing. A hot field gets an index, and no org passes its budget. `test_tenant_coverage.py` passes. **The budget:** at most 8 hot fields for each object and 24 for each org. The store refuses first, and the trigger `studio_object_fields_hot_budget` refuses too, also under two writers at once. A field turns hot only on an object of at most 50,000 records. A record holds at most 64 KiB (65,536 bytes) of JSON. **Tests:** `test_studio_fields.py` (each type, the spec rules, three fences). `test_studio_objects_r8.py` (`pytestmark = _DB_GATE`): FORCE RLS on all four tables, org B reads and writes nothing, each composite key refuses a row of another org, a hot row on each write, the 9th and the 25th hot field, two writers at once, the 64 KiB cap, the 50,000 cap, the cascades. `test_admin_member_purge.py`: no purge row and no cascade reaches a record | HIGH | AGENT-SAFE |
| **C1b** | 🔨 **BUILT, dark (2026-11-02).** Store level only: no route, no flag and no UI. B2b calls it later. **Multi-record writes and constraints.** One batch writes several records in one transaction, and a constraint runs on each write. **Migration:** `327_studio_object_constraints.sql` (R1: 325 and 326 are held by open pull requests, so check the number again at merge). Expand only: `studio_objects.constraints` is JSONB, NOT NULL DEFAULT `[]`, and a CHECK holds an array. No new table. **Ships:** `acb_common/studio_rules.py` holds the grammar and `RuleViolation`, with no SQL and no table name. `objects_store.py` gains `apply_writes`, `set_constraints` and the checks. `StudioObject` gains `constraints`. All SQL stays in `objects_store.py`. **Not shipped:** a manifest key (`rules` stays reserved, and C6 lifts it), state machines, formulas, constraints across objects or references, money fields, and a check of old data when a rule is declared. **The grammar.** `set_constraints(db, object_id, rules, *, package_id)` declares the rules of an object, and each call replaces the list. A rule has a `name` (`^[a-z][a-z0-9_]{0,47}$`, unique in the object), a `kind` and an optional `message` of at most 200 characters. An object holds at most 16 rules. Three kinds, and the list is closed. `range` is `{field, min?, max?}` on one number field of one record, and it needs one bound. Non-negative is `min` 0. A record with no value passes. A `range` field may be cold, because the check reads one record in Python. `unique` is `{fields: [<one hot field>]}`. An empty value never clashes. A text of 256 characters or more compares `data` too, because `v_text` holds 256. `group_sum_min` is `{field, group_by: [<1 or 2 hot fields>], min}`. It is the derived balance: the sum of a hot number field over the records with the same group values stays at least `min`. "Stock never below zero" is this kind over a ledger object. A record with no value in a group field joins no group. A key with no rows passes at any `min`, because a group that no longer exists holds nothing to break. So the last row of a group can leave. A key with rows and no amount sums to 0, and the floor judges it. A `unique` field and a group field have the type text, email, select, number, date or datetime. A checkbox has two states, and a money value has a currency, so neither is a key. Each named field must exist. A field of `unique` and of `group_sum_min` must be hot, and `set_constraints` refuses another and does not turn it hot. A field that a rule holds cannot turn cold while the rule stands, because a cold field has no hot rows to check. **A rule that the store cannot read fails closed.** A stored rule of an unknown kind, or a malformed one, does not stop a read. Each write of its object is refused until `set_constraints` replaces the list. Only raw SQL can store such a rule. **A declaration does not check old data.** A rule binds each write after it is declared. A `unique` key that clashes already is refused only when a later write touches that key. **On a floor of 0 or less, a write that does not lower a group never reads the group.** The store adds up the net change of the batch on each key of `group_sum_min`, in exact `Decimal`, from the values that it read under its locks. With a net change of zero or more, it skips the sum. The org lock makes that safe, and a group with no rows starts at a sum of 0, which meets the floor. So a receipt on a broken key passes and a repair is possible, and a write that lowers a broken key is refused. A floor above 0 always reads its group, because a new group starts below it. It holds from the first row: with `min` 1, a new group of 0.5 is refused, and a new group of 1 passes. **The batch.** `apply_writes(db, actor, *, package_id, writes)` answers one `Record` or `True` for each `Write`. A `Write` is a frozen dataclass: `op` (`create`, `update` or `delete`), `object_id`, `record_id` and `data`. A batch holds at most 50 writes, and a longer one is refused before any statement. **The lock order:** the org lock once, then the object rows FOR KEY SHARE in ascending object id, then the record rows FOR NO KEY UPDATE in ascending `(object_id, record_id)`. The store then applies the writes in list order. `set_constraints` takes the org lock, then the object row FOR UPDATE. **The wait.** `_lock_org` bounds each wait for the org lock at 1 s, for every caller, with a LOCAL `lock_timeout` in its own SAVEPOINT. A waiter past 1 s can try again, and the pool matters more. The old `lock_timeout` comes back as soon as the lock is held, so the bound covers the wait only. Past the bound the store raises `StoreBusy` (code `store_busy`): "This app is busy. Try again in a moment." It is retryable, the caller's transaction stays usable, and a route maps it to 503 with `Retry-After`. A deadlock that Postgres settles at this wait (SQLSTATE 40P01) raises the same `StoreBusy`. A deadlock on a later statement stays a database error. A caller that holds `apps` FOR UPDATE takes the org lock first. Each waiter holds a pool connection for the whole wait, so the bound limits the delay of other tenants and does not end it. Measured on one PC, on a pool of 9 connections with one holder of the lock: a read of another org waits 1.1 s behind 12 writers, 3.2 s behind 30, 5.4 s behind 45 and 7.5 s behind 60. Behind 90 writers it fails with the pool `TimeoutError` at 10 s. **The check order:** apply all writes, then `range` on the last value of each written record, then `unique` and `group_sum_min` once for each touched key. So a stock move is judged on the net, and not on each row. A `group_sum_min` key is touched by the old and the new key of an update, by the key of a create and by the key of a delete. A `unique` key is touched by the new key only. A delete or an old key cannot add a clash, so a check there would only stop the repair of old duplicates. **Failure.** The whole batch runs in one SAVEPOINT. Any error rolls back the SAVEPOINT and raises `BatchError(index, cause)`, so the caller's transaction stays usable with its earlier work. A check reads hot rows only, through the query builders of C2a, in `_time_limit` (5 s). The one exception is a key of 256 characters or more. C2a has no hot candidates for it, so the check reads the records of the object, under the same deadline. A timeout refuses the batch with `query_timeout`. `create_record`, `update_record` and `delete_record` are a batch of one, with the same signatures and answers, so no single call skips a rule. **The errors:** `ConstraintViolation(RuleViolation)` holds `.rule` and `.object`, and its message names the rule. It never echoes a value. `ConstraintError` refuses a declaration. `RecordNotFound` is a write that names no such object or record in the package. `StoreBusy` is the wait above, and a batch gives it as the `cause` of its `BatchError`. C6 and C7 reuse `RuleViolation` and `touched_keys`. **Concurrency.** Under READ COMMITTED, a check before a write is wrong unless the writers run one at a time. Every store write takes the exclusive org lock first, and each later statement gets a new snapshot. So all three kinds are correct against each other store writer. A database unique index is impossible, because no tenant gets DDL. ⚠️ **Known limit.** The org lock serializes all record writes of one org, and a finer lock is a later slice. A writer that skips the store is out of scope: raw SQL, and the cascade of a package hard delete. A write that lowers a group past about 500,000 rows can reach `query_timeout` (measured on one PC: 200,000 rows 1.5 s, 500,000 rows 4.2 s, 1,000,000 rows refused). The same limit binds each write on a floor above 0, because that floor always reads its group. A running balance table is a later slice. | `test_studio_rules.py` (pure) and `test_studio_objects_batch_r8.py` (R8, on the `--must-run` list). A move of 7 (stock 10) writes exactly 2 rows, and the reversed order passes too. The second write fails with a database error: the batch leaves 0 rows, an earlier row of the caller stays, and the session is usable. Stock 10, move -11: refused with `stock_not_negative` in the message, 0 rows written. Move -10 passes. Two real connections, stock 10, each takes 7, 10 rounds: exactly 1 success, 1 refusal, final sum 3. The same for `unique` on `sku`: exactly 1 row. Two writers with objects in opposite order, 20 rounds: no 40P01, with a timeout of 10 s. An update that moves a row to another group checks both groups. A delete of a positive row that breaks the balance is refused. 50 writes pass. 51 are refused with 0 statements run. A 17th rule is refused. FORCE RLS: org B reads no rule and no sum of org A, also with the same field keys. On 50,000 ledger rows the plan of a sum shows no Seq Scan and a bounded read. A timeout in a check refuses the batch and leaves the session usable. A waiter past 1 s gets `store_busy`, the session stays usable, and `lock_timeout` is back. A deadlock at the lock gives `store_busy` too. On the real pool, one holder and a full pool of waiters of org A leave a read of org B a connection. On a broken key, `-3` is refused and `+3` passes. A positive write on a group of 200,000 rows reads no sum. `9999-12-31` is a legal key. With `min` 1, a delete and a move of the last row pass, a new group of 0.5 is refused, and a new group of 1 passes. With `min` 1 and rows 5 and blank, a delete of the 5 is refused. A malformed stored rule refuses each write and breaks no read. **Verify:** `uv run pytest tests/unit/test_studio_rules.py tests/unit/test_studio_fields.py tests/unit/test_studio_query.py tests/unit/test_studio_manifest.py tests/unit/test_tenant_coverage.py`. With a private ladder database: `uv run pytest tests/unit/test_studio_objects_r8.py tests/unit/test_studio_query_r8.py tests/unit/test_studio_objects_batch_r8.py`. Then `ruff check`, mypy on the changed files, and `bash scripts/ci_local.sh`. | HIGH | AGENT-SAFE. After C2a |
| **C2a** | 🔨 **BUILT, dark (2026-11-01).** No route, no flag, no UI and no migration. **The object query:** filter, sort, paging, the cost guard and its errors. **Built:** `acb_common/studio_query.py` holds the grammar: the shape, the op table of each type and the caps. It holds no SQL and no gateway import. The list of B2b-1 and the chat tool pass their input through it, and `list_input_schema()` is the JSON Schema of that input. `objects_store.query_records(db, *, package_id, object_id, filter, sort, limit, offset)` is the one reader, beside the reads of C1. It answers None for an object of another package or org. `build_query` and `build_count` are pure. **The filter** is a list of at most 8 conditions `{field, op, value}`, and a record must meet each one. **The ops of each type:** text and email take `eq`, `ne`, `in`, `contains` and `is_empty`. Select takes `eq`, `ne`, `in` and `is_empty`. Number and money take `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in` and `is_empty`. Date and datetime take `eq`, `lt`, `lte`, `gt`, `gte` and `is_empty`. Checkbox takes `eq`, `ne` and `is_empty`. Another type and a field with `many` take no filter. The grammar refuses a range op on text. **The values** go through `studio_fields.check_value`, so an email compares in lower case and a select value must be an option. `in` takes at most 100 values. A money filter compares the minor amount, and it does not check the currency. A date value means the whole day in UTC. So `lte` a day is up to the end of the day, and `gt` a day is after its end. Apart from the checkbox rule, `ne` matches only a record that holds a value, and only `is_empty` (true or false) matches a record with no value. An empty text is a value. **The checkbox rule (the one exception).** The build loop decided this rule on 2026-11-02, and the owner can change it. A checkbox has two states for a member. `eq true` and `ne false` match a ticked record. `eq false` and `ne true` match an unticked record and a record with no value, because a form that does not send an unticked box writes no value. `is_empty` matches only a record with no value. **The 256-character rule:** `v_text` holds the first 256 characters. So `eq`, `ne` and `in` with a value of 256 characters or more check `data` too. `contains` checks `data` when `v_text` holds 256 characters. **The sort:** one hot field, then the record id. A text sorts by its first 256 characters. A record with no value of the sort field comes after the others, in the ascending order of its id. A checkbox field takes no sort, because `v_bool` has no index. With no sort, the order is the record id. **Paging:** `paging.bounds` gives a page of 25 records by default and 100 at most. The offset is at most 10,000. The total is exact below 10,001. At 10,001 the page gives `total_at_least` in its place. `page_line` gives the line, and it names no offset past 10,000. **One statement for a page (fix round 1).** One statement reads the page and its total, so one snapshot serves both. A write between two reads cannot put a record on a page twice. For a sort, the head reads the hot index of the sort field in order. The CTE `hc` counts the head up to the end of the page. So the tail reads only the rows that the page needs, and no rows when the head fills the page. A Sort node takes at most the rows of one page. **The candidates.** Each part of the statement reads its candidates from the hot rows of one hot condition (a time or a text match first). With no such condition, it reads the records of the object by the records index, in the order of the id. Each other condition tests the candidate id. A condition that reads `data` runs after a lookup of the record by its primary key. **The cost guard**, in three layers. (a) The caps of the shape. (b) `hot_or_small`: the grammar refuses a sort on a field that is not hot. A filter on a field that is not hot reads `data`, so it runs only when the hot filters leave at most 5,000 records. The page statement holds this count as the CTE `g`, so a write between two statements cannot pass the guard. Then the MATERIALIZED CTE `m` holds those candidates once, and `data` is read only for them. The count and the page read `m`. (c) One call has one deadline of 5 seconds. Each statement of the call, from the read of the object on, runs in a SAVEPOINT. Before each statement, the store sets the LOCAL `statement_timeout` to the time that is left. With no time left, it refuses with `query_timeout` and runs no more statements. **A field that turns cold.** The page statement counts the fields that it reads as hot and that are still hot (the CTE `v`). A field that turned cold after the store read the fields has lost its hot rows. So the store reads the fields again and runs the query once more. A second change gives `query_timeout` with the sentence "The fields of this object changed during the query. Try again." A timeout rolls back the SAVEPOINT only. The caller's transaction stays usable, and the old timeout comes back. **The six errors:** `QueryError` holds `query_field_unknown`, `query_op_not_allowed`, `query_value_invalid`, `query_not_indexed`, `query_too_big` or `query_timeout`, and one sentence. The sentence never echoes a value. A route answers 503 for `query_timeout` and 422 for each other code. **Injection:** a field key and a value are always binds. The SQL text takes a column name and an operator from a constant map only. ⚠️ **The known gap:** no `numeric` comparison is LEAKPROOF in Postgres 16. So the app role gets no index condition on `v_num`, and a number or money filter reads the hot rows of its one field (`docs/rls_and_indexes.md`). A sort on a number still reads the index in order. A checkbox filter reads the hot rows of its field too. The verifier measured a number range filter at 2.3 seconds on 600,000 records. No cap limits the records of an object after a field turns hot. A later slice fixes the gap with a migration. **Not built:** group, count, sum and average (C2b). B2b-1 gives the query its route | `test_studio_query_r8.py` (`pytestmark = _DB_GATE`, in the `--must-run` list). Each op of each type gives the same records on a hot field and on a field that is not hot. `ne` skips a record with no value, and `lt` and `lte` differ at the boundary. A long text matches against `data`. A sort pages each record once, with the ties in id order and the empty records last. A query of another package, object or org reads nothing. The store refuses a filter on a field that is not hot over 5,000 records. At a 1 ms timeout the query gives `query_timeout`. Then `get_record` runs in the same session, and the old timeout is back. On 50,000 records, EXPLAIN of the SQL of `build_query` shows the hot index, no Seq Scan, and no Sort node for a hot sort. A one-day filter (1%) reads at most 2,500 hot rows. A cold filter after a hot filter reads at most 5,000 records, and no count or page scans a table. A second session that clears a value between two reads puts no record on the page twice. A slow statement leaves less time for the next one. Each checkbox op gives the same records on a hot field and on its cold twin. A field that turns cold during a call gives the right page. A bulk insert during a call cannot pass the guard. `test_studio_query.py` (no database): the op table, each refusal, the caps and the injection fences. Verify: `uv run pytest tests/unit/test_studio_query.py tests/unit/test_studio_query_r8.py -q` | HIGH | AGENT-SAFE |
| **C2b** | Group and aggregate: `group_by`, count, sum and average, on hot fields only. At most 50 groups, and the rest fold into `other`. Sum and average take a number only. C2b refuses a money sum unless a check of `data` proves one currency, as CRM H-333 does. A date groups by day or by month in UTC. It uses the grammar, the guard and the errors of C2a | R8 for each aggregate. A group on a field that is not hot gives `query_not_indexed`. C2b refuses a money sum of two currencies. EXPLAIN on 50,000 rows shows the hot index | HIGH | AGENT-SAFE. After C2a |
| **C3** | Record events: create, update and delete emit `studio.record.*` through `ingestion/event_hooks.py` | A workflow with a record trigger runs once for one change. The event carries the org | STD | AGENT-SAFE |
| **C4** | Record history: each change names the actor and the old value | The history of one record lists each change in order | STD | AGENT-SAFE |
| **C5** | Records join `search_company` through the one retrieval seam (D128) | A new record is found by Mira within five minutes. A record the viewer cannot open is never returned | HIGH | AGENT-SAFE |
| **C6** | 🔨 **BUILT, dark (2026-11-02).** No route, no flag and no UI reach it, and it needs no migration. **State machines** (§3.7 layer 2): a select field names its allowed moves, and each move may have a guard on the record. **Scope (narrowed, 2026-11-02).** Store level only: no route, no flag and no UI. No migration. **Not in C6:** a role guard waits for C8 (a declaration with a role is refused, and the message says so). A history row waits for C4. An expression, a money guard and a read of another record wait for C7. **Where it is declared.** The manifest is the one source. An object gets a `rules` list beside `fields`, and C6 lifts `rules` from `RESERVED_KEYS`. `state_machine` and `states` stay refused. The store keeps the list in `studio_objects.constraints` as a fourth kind, `state_machine`, so A5 can pass the manifest list unchanged to `set_constraints`. The manifest check calls `parse_rules` and `check_rules_fit` of `studio_rules.py`, the one parser. **The grammar.** `{name, kind: "state_machine", field, initial, moves: [{from, to, guard?}]}`. `field` is one select, not `many`, and it may be cold. `initial`, each `from` and each `to` are options of the select. A machine has 1 to 64 moves and no move twice. An object holds one machine for each field, and at most 16 rules (the C1b cap). A guard is a list of 1 to 4 atoms, and all must hold. An atom is `{field, op, value?}` with the ops eq, ne, lt, lte, gt, gte, in, is_empty and not_empty. A guard reads the merged record only. It never reads another record, another object or the clock. The ops of a type are a subset of the C2a table (`OPS_BY_TYPE`), plus not_empty. A money field takes no guard yet, and a datetime field takes no order op. **Enforcement.** `_check_records` of `objects_store.py` judges each written record from `mark.before` and `mark.after`, under the same org lock and SAVEPOINT, with no new SQL. A create takes `initial` when the field is absent, and another value is refused. An update that changes the value needs a matching move and a guard that holds. An unchanged value passes. A cleared field is refused. A stored state that is not an option is refused as the start of a move. An unchanged one passes, so an edit of another field can still save a record after an option goes. A guard value must equal the stored form of its field. A batch judges the net move of each record, so draft, then approved, then sent counts as draft to sent. A delete is not a move and has no check. A stored machine that the parser cannot read fails closed, as a stored rule of an unknown kind does. `touched_keys` answers no key and `hot_keys` answers none for a state machine, so the batch queues no SQL key check for it. **The error.** `MoveViolation(RuleViolation)` holds `rule`, `object`, `from_state` and `to_state`. It never echoes a guard value. | A move that the machine does not allow is refused. A guarded move whose guard fails is refused. A create with a state other than `initial` is refused. A batch of two moves counts as one net move. A refused batch writes nothing. At least 30 new pure cases in `test_studio_rules.py`. R8: `test_studio_state_machine_r8.py` (`_DB_GATE`, on the `--must-run` list), at least 12 cases, with org B isolation and a stored state that is not an option. `test_studio_manifest.py` and `test_studio_manifest_ts_parity.py` pass with `rules` lifted. **Verify:** `uv run pytest tests/unit/test_studio_rules.py tests/unit/test_studio_objects_batch_r8.py tests/unit/test_studio_state_machine_r8.py tests/unit/test_studio_manifest.py tests/unit/test_studio_manifest_ts_parity.py -q`, then `ruff check` and mypy on the changed files. | STD | AGENT-SAFE |
| **C7a** | 🔨 **BUILT, dark (2026-11-02).** No route, no flag and no UI reach it, and it needs no migration. **Formula fields** (§3.7 layer 2), stored and computed on write. **Ships:** `acb_common/studio_expr.py` (a pure closed JSON tree, no SQL) and the rule kind `formula`, `{name, kind, target, expr, scale?, rounding?, currency?}`, in the `rules` list of C6. `rounding` is required for a number target, and `scale` defaults to 0. The store computes each formula inside `_apply_write`: it validates the record without its targets, then runs each formula in dependency order, then builds the hot rows from the result. A read never computes. **Grammar:** literals (`num` as decimal text, `money`, `text`, `bool`, `date`), `ref` to a field of the same record, and the functions by type. Null propagates, and `coalesce` and `is_empty` take it. `if` is lazy. Caps: depth 8, 64 nodes, 4 args, 256 steps, 8 formula rules for each object. **Money:** whole minor units, no float, a named `rounding` (`half_up`, `half_even`, `down`, `up`) on each `div` and on each `mul` with money. Division by zero is `division_by_zero`. **Number precision:** a number target has a `scale` (0 to 4) and a `rounding`. The stored result holds at most 15 significant digits at its scale: `abs(result) < 10 ** (15 - scale)`, or the write is refused with `result_too_large`. So a JSON float holds it exactly. Money stays a whole number of minor units below 10^15. **Targets:** number, money, text, checkbox, date. A target is not `required`, and a caller cannot write it. **The description.** `StudioObject.computed` and the `computed` part of `studio_describe("fields")` name each formula target as computed, so a Form shows it read-only and sends no key for it. A write that holds a key for a target is refused with `computed_field`, also with the same value. **Declare first.** A new formula, or a formula with a changed `target`, `expr`, `scale`, `rounding` or `currency`, on an object that holds records is refused (`ConstraintError`, "declare before the first record"), because there is no backfill. A change of only `name` or `message` passes. ⚠️ A stored formula that is malformed counts as a new one. The author removes it first, and can add it again only with C7c. A removed formula is allowed, and its stored values stay as plain values. **The other rules.** `range`, `unique`, `group_sum_min` and a state-machine guard that name a formula target read the stored result, because the checks run on the record after the formulas. `rule_reads(rule)` answers every field key that a rule reads, and C9 uses it. **Errors:** `FormulaError(RuleViolation)` holds the rule, the object and a code (`division_by_zero`, `currency_mismatch`, `result_too_large`, `step_cap`, `type_error`, `bad_argument`, `bad_result`, `computed_field`, `declaration`). It never echoes a value. A tree over a cap is refused at declaration with the JSON path and one sentence, and a forged stored tree refuses each write. A forged stored list of more than 8 formulas refuses each create and update with `declaration`, and a delete computes nothing, so it passes. A `lower` or `upper` of a `longText` ref into a `text` target gives `result_too_large` when the value passes the text limit, and that blocks the writes of the record. **Not shipped:** roll-ups (C7b), a clock, a backfill, a restricted field (C9 adds the refusal, using `rule_reads`, and it is advisory in C7a), a text syntax, a UI, a route. The manifest key `formula` is no longer reserved (a rule uses `expr`), and the field type `formula` stays refused with a message that names the `rules` list. | `uv run pytest tests/unit/test_studio_expr.py tests/unit/test_studio_rules.py tests/unit/test_studio_formula_r8.py tests/unit/test_studio_objects_batch_r8.py tests/unit/test_studio_manifest.py tests/unit/test_studio_manifest_ts_parity.py tests/unit/test_studio_query.py tests/unit/test_studio_fields.py tests/unit/test_studio_describe.py -q` passes. A create stores the result and an update recomputes it from the merged record. A write of a target is refused, also with the same value. A tie rounds as the named mode, positive and negative. A zero divisor refuses the batch with 0 rows written and the session usable. A tree over a cap is refused at declaration with a JSON path. A forged stored tree refuses each write. A hot target filters and sorts. A `range` rule and a guard on a formula target read the stored result. The AST scans find no eval, clock, float or SQL. R8: `test_studio_formula_r8.py` (`_DB_GATE`, on the `--must-run` list) | HIGH | AGENT-SAFE. After C6 |
| **C7b** | **Roll-up fields.** The rule kind `rollup`, `{name, kind, target, source, via, agg, field?, where?, currency?}`: sum, count, min or max of a hot field of the child records of the same package that point to the parent through a hot relation field. **Exact and synchronous:** the store recomputes each touched parent after the writes of the batch, in the same SAVEPOINT under the org lock, then runs the formulas and the rules of the parent. A move recomputes the old and the new parent. At most 100 aggregate statements per batch, inside the 5 s deadline of C1b. **Prerequisite in the same PR:** `relation` becomes a hot type (`HOT_TYPES`, `hot_value`, the C2a ops `eq`, `ne`, `in`, `is_empty`, no sort), with a migration that widens the CHECK `studio_object_fields_hot_type` (expand only, R6, number taken at build time, R1). **Not shipped:** two hops, another package, a `many` relation, an average (C2b), a backfill. A money sum needs `currency`, and a child of another currency is refused. | Sum, count, min and max are exact after each write, with `where`. A move, a delete and a create each leave both parents exact. Two real connections that each take 7 from 10 give exactly one success and a sum of 3. 50 child writes to one parent run one aggregate statement. A parent `range` that breaks rolls back the child write. The plan of a sum over 50,000 children shows no Seq Scan. A source in another package is refused | HIGH | AGENT-SAFE. After C7a. The deploy applies the migration, not the builder |
| **C8** | **App roles and row rules** (§19.7). `roles` in the manifest, `access` on an object, `<person field> = $me`, `hide` for a field, a required `viewer` in the one reader and the one writer, query ops for `person` | R8: an employee reads only their records through the door, chat, search and a workflow. A manager reads their team only. A fence lists each reader path. A `where` on a field that is not hot is refused | HIGH | AGENT-SAFE |
| **C9** | **Field sensitivity** (§19.7). `normal`, `personal`, `restricted`, encryption with a per-org key, an audit row for each read, `[restricted]` in each tool result, and the card path to the viewer | The ciphertext is in `data`. A restricted field cannot turn hot or take a filter or an embed. A test runs each chat path and finds no restricted value in what reaches the model. A package with a restricted field and no `access` is refused. A formula or a guard that reads a personal or restricted field makes its target the same level (`rule_reads` of `studio_rules.py`, built in C7a), and a restricted source needs a restricted target | HIGH | AGENT-SAFE |
| **C10** | **Retention and the org caps.** `retention_days`, the sweep, 1,000,000 records and 2 GiB for an org, more hot fields after a measure of the write cost | A record past its date goes with its hot rows and vectors. A write past a cap is refused in one plain sentence. R8 | HIGH | AGENT-SAFE |
| **C11** | **Ingest and import.** A CSV into an object with a field map, and a signed endpoint for a device or a vendor push, with a token for each install, a rate limit and a key for each row | A second delivery of one punch writes no second row. A wrong signature writes nothing. The endpoint binds the tenant. Rows go in chunks of 50 | HIGH | AGENT-SAFE. After H-328 |
| **C12** | **Exports between packages.** Off by default. A package names what it exports, and a reader needs its install and the viewer's rights | With no export, package B reads nothing of A. C8 still binds | HIGH | AGENT-SAFE |

### Group D — Blocks and the renderer

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **D1** | Data binding, page state and typed actions in `GenerativeUINode` (§5.2) | A node with `source` renders rows from a mocked door, with the default loading, empty and error states. A button sends its action id. The lockstep test passes. **Built 2026-11-01 (STU-D1):** `table` and `dataGrid` bind, through `lib/studio/dataDoor.tsx`. **Narrowed:** page state is a pure encode and decode helper (`lib/studio/pageState.ts`), and the URL wiring waits for D11, since no Studio page mounts a bound node yet. Other blocks bind in D2 to D5 | STD | AGENT-SAFE |
| **D2a** | 🔨 **BUILT, preview only (2026-11-02).** The blocks run on the fake door at `/studio/blocks-preview`, which is not in the nav. The Table, Form and Detail blocks, from the §3.1 field types, on a mocked door. **Table:** `DataGrid` in `genUITemplates.tsx` grows a filter box and takes its cell kinds from the field types, on the D1 bind path. **Form:** all 16 types, a check on blur and on submit, a submit through `door.run`, and a 422 shown under its field. **Detail:** a read-only record sheet. **Field check:** `src/lib/studio/fieldCheck.ts` is the TypeScript twin of `check_value` and `validate_record`. Python is the one source. `tests/unit/test_studio_field_cases_parity.py` writes the golden file `packages/acb_common/acb_common/studio_field_cases.json` and fails when the committed file differs. **Preview:** `/studio/blocks-preview`, with a fake door and sample records, labelled "Sample data". It is not in `nav.ts`, and the longest-prefix match of `/studio` gates it by `workflows` | `fieldCheck.test.ts` reads the golden file and holds at least 48 cases (16 types, 3 each). `test_studio_field_cases_parity.py` holds the file and the six limits equal to the Python module. `blocks.test.ts`: each type renders in the Form and the Detail. The Table shows its loading, empty, error and row states. A bad value blocks the submit, sets `aria-invalid` and `aria-describedby`, and focuses the first bad field. A double submit calls `door.run` once. No `createContext` stands outside a "use client" file under `lib/studio/`. `dataDoor.test.ts` and the lockstep test stay green. Visual review in four contexts | STD | AGENT-SAFE |
| **D2b** | Live lists and writes for the three blocks. The Table reads `object:<name>.list`. The Form calls create and update through the door. Inline edit in the Table calls the update action. **A page source is a read.** A2 refuses a source that resolves to a write, and D2b's `door.read` must send only reads | A Table reads rows of a stored object. A page reads each source with no click, so A2 (`_check_view_source`) passes only an object `list` or `get`, or an action of class A (`test_studio_manifest.py`). A Form creates a record and an edit updates it. A refused write shows its reason under the field. The door answers a refusal as `{fieldErrors, rule, message}` in `data`, and D2b maps `FieldError.field`, `RuleViolation.rule` and `ConstraintViolation.rule` to it (the contract is a comment block in `lib/studio/formModel.ts`). Visual review in four contexts | STD | AGENT-SAFE. After B2b-1 (task 1335) for the live lists, and after B2c-1 (task 1336) for create and update |
| **D3** | 🔨 **BUILT, preview only (2026-11-02).** The Board block on `components/board/`. **Built narrowed: the fake door only.** The block reuses `Board`, `BoardColumn`, `StageChip` and `TaskCardShell` as they are. The drag is native HTML5, as on the other three boards. No Projects code changes, and `src/lib/boards.test.ts` lists the new file. The pure model is `lib/studio/boardModel.ts` (no React import). The view is `components/studio/StudioBoard.tsx`. `bindsSource` in `dataDoor.tsx` gains `board`. **Shape:** `{name: "board", source, data: {fields, groupBy, titleField, cardFields, update, maxCards}}`. `groupBy` must name a select field, or the block shows an error state. A lane holds one option, in the option order. A record with no value or an unknown value goes to a last lane, "No value". `cardFields` holds at most 3 fields, worded by `displayValue` and drawn by `ValueElement`, so no new formatter or chip exists. Each lane shows at most `maxCards` cards (default 50) and a "Show more (n)" line. The count chip shows the true count. **Drag contract:** the card moves at the drop, and exactly one `door.run(update, {id, data: {[groupBy]: option}})` runs. A drop on the same lane calls nothing. A card with a call in flight is locked, so two drops make one call. A refusal puts the card back at its lane and index, and the board shows the door message in a `role="alert"` line through `readRefusal`. A rule refusal names the rule. The keyboard path: Space picks a card up, the arrows choose a lane, Space drops it, Escape cancels, and an `aria-live` line says the result. A "Move to" menu serves touch and 390 px. **Fake door:** `sampleDoor.run` gains a partial update (a patch that lacks a required field), and it refuses a move into "Closed" of a visit that is not done, with the rule `close_needs_done`. **Preview:** a Board section on `/studio/blocks-preview`, on the sample records of the Table. **Out:** order inside a lane, add card, swimlanes, WIP limits, URL state (D11), and the greying of forbidden lanes. The greying waits for the C6 state machine to publish a move table. **Live half:** a drag on a stored object needs B2c (the write door, after MCP-P1b). Until then the update runs on the fake door only | `boardModel.test.ts`: 3 options give 3 lanes, plus "No value" only when a record has none. The option order holds. 60 records at cap 50 show 50 and "Show more (10)", and the count chip shows 60. `StudioBoard.test.ts` (the vitest config includes `*.test.ts` only): a drop on a new lane makes exactly 1 `run` call with `{id, data: {status}}`. A same-lane drop makes 0. Two drops while one call is pending make 1. A refused update puts the card back in its lane, and the alert text equals the door message. The keyboard path makes 1 call. A `groupBy` that is not a select gives the error state. `dataDoor.test.ts` covers `board`. `boards.test.ts` lists the new file. A source check holds the block to `displayValue`. Visual review in four contexts, a drag ring, a refusal and an empty lane | STD | AGENT-SAFE. The live write waits for B2c (task 1336) |
| **D4** | The Chart and Stat blocks on `lib/charts/kinds.mjs`, fed by an aggregate query | Each of the 13 kinds renders from a C2b result | LOW | AGENT-SAFE |
| **D5** | Pull the Calendar grids out to `components/calendar/` with a generic event shape | The Calendar app's tests pass with no change. The block renders object records by a date field | STD | AGENT-SAFE |
| **D6** | Pull the Timeline core out of `TimelineView.tsx` to `components/timeline/` | The Projects tests pass with no change. The block renders records with a start and an end | STD | AGENT-SAFE |
| **D7** | The Mind map block, on `@xyflow/react` with a tree layout | A parent field draws the tree. A drag to a new parent calls the update action | STD | AGENT-SAFE |
| **D8** | The Process flow block: a read-only workflow graph with run state | A running workflow's nodes show their state from the run console stream | STD | AGENT-SAFE |
| **D9** | The Checklist block: playbook steps, each human step a task | Ticking a step completes its task in the one store | STD | AGENT-SAFE |
| **D10** | The Map block | Records with a geo field show as pins. Visual review at 390 px | STD | **OWNER-GATE** for a paid tile key (STU-Q8). Otherwise AGENT-SAFE |
| **D11a** | 🔨 **BUILT (2026-11-02).** The page shape in A2. An optional top-level `pages` list: `{id, title?, cols?, tabs?: [{id, title, cells}], cells?, side?: {view}}`, with `cells: [{view, span?}]`. Caps: 8 pages, 8 tabs, 12 cells. Each cell and the side view name a declared view. A page has tabs or cells, not both. The pydantic model of A2 is the one validator. `studio_manifest.schema.json` is regenerated, and `PageDef`, `TabDef`, `CellDef` and `SideDef` join `lib/studio/manifest.ts`. The pure `lib/studio/pageModel.ts` builds the page, and `boundPageState` in `pageState.ts` bounds the URL state (`sel` 64 characters, `tab` a declared id, 8 filters, 200 characters a value, 2,000 characters in all, no prototype key). **Non-goals:** a view-to-node mapper, a new GenUI node type, live data, any block not built, a page editor. `studio_describe` and the G1 skeleton do not mention `pages` yet, so the builder writes none until G3 | `test_studio_manifest.py` holds the `pages` cases. `test_studio_manifest_ts_parity.py` and the stale-schema test pass. `pageModel.test.ts` and `pageState.test.ts` pass, with a 10,000-filter query, `__proto__` and an unknown tab. The caps in `pageModel.ts` equal the `maxItems` of the schema. A manifest with no `pages` stays valid | STD | AGENT-SAFE |
| **D11b** | The page layout component and the URL wiring (task 1497). `components/studio/StudioPage.tsx` lays out cells in a grid under `AppTopBar`, with `Tabs` (underline) and a side column (`w-[380px]` at `sm` and up, a `Modal` sheet below). It takes a `renderView` prop. `lib/studio/usePageState.ts` reads and writes `pageState` through `useSearchParams` (`push` for a tab or a pick, a debounced `replace` for a filter). Preview on `/studio/page-preview`: a fake door, "Sample data", `lib/studio/samplePage.json`, in no nav pane. **Phone:** the grid is one column below `sm`. The Board is placed and not changed (D3 stacks its lanes). **Non-goals:** a view-to-node mapper, a Board change, a new GenUI node, any block not built (it shows a "not built yet" card), live data, a sidebar entry. Needs D3 merged. The page reader of D11a assumes a validated manifest, and it drops each part that A2 refuses | `usePageState.test.ts` shows a tab, a filter and a pick survive a reload through the URL, back restores the earlier tab, a filter edit adds no history entry, and a hostile URL gives a safe state. `StudioPage.test.ts` holds the grid, tabs, side and not-built cards, and no Cmd-K, bell or palette. The lockstep, `nav`, `seams`, `conformance` and `AppTopBar` tests pass with no edit. `next build` passes. Visual review of the preview in light mode, at compact density, under a changed accent and at 1440, 1024, 768 and 390 px, with four captures and a `Visual review:` line. Owner preview (D145) before merge | STD | AGENT-SAFE, with the UI gate. After D11a and D3 |
| **D12** | The manifest conformance fence. A2 refuses no CSS class name in a title today (for example `bg-red-500` passes), and D12 adds that check | A manifest with a colour, a pixel size or an unknown block fails to publish | LOW | AGENT-SAFE |
| **D13** | **Documents.** A template block with fields from a record, a `document.render` first-party action over `render_pdf`, images in `render_pdf`, and the result stored in `attachments` | One record gives one PDF with a logo and a table of lines. A `file` field opens it for a viewer with access. A restricted value stays out of each log | STD | AGENT-SAFE |

### Group E — Workflows in Studio

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **E1** | The workflow editor at `/studio/workflows/{id}`, the same engine and components | `/workflows/{id}` answers 308 to the new route. `nav.test.ts`, `entities.test.ts`, `studioRedirects.test.ts` and the ratchet suites pass. The editor keeps its own toolbar until it gets an `AppTopBar` | LOW | AGENT-SAFE |
| **E2** | Fan-in and loops (`workflows_app.md` §8.3b and §8.3c, unchanged) | The acceptance of §8.3b and §8.3c | STD | AGENT-SAFE |
| **E3** | Studio triggers: record, form submit, button, skill. Each trigger registers as a contribution of its install (§11.1) | Each trigger starts one run, with the org bound. A trigger stops when WS-PKG removes its install | STD | AGENT-SAFE. Unattended triggers wait on H-328 |
| **E4** | The human step node: a task in `pm_tasks`, and a wait until it is done | The run resumes once when the task moves to done. A cancelled task fails the step | STD | AGENT-SAFE |
| **E5** | Catalog nodes: any B1 action is a node | A class B node pauses for approval, as the approval node does | HIGH | AGENT-SAFE |
| **E6** | The workflow template gallery (`workflows_app.md` §8.3a) | §8.3a's acceptance | LOW | AGENT-SAFE |
| **E8** | **The batch run.** One approval for one run, then chunks of 50 through `apply_writes`, a key for each item, a status for each item, and a resume after a stop | A run of 300 items stopped at item 120 resumes and repeats none. The card shows the exact list (D131.2) | HIGH | AGENT-SAFE. After C1b and E2. STU-Q14 |

### Group F — Skills

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **F1** | 🔨 **BUILT (2026-11-01).** Merged with migration `320_studio_skills.sql`. The skill store: `studio_skills` and `studio_skill_versions` (immutable), tenant-scoped, private by default (§3.5). One expand-only migration in the shape of `315_brief_runs.sql`, with its own ENABLE, FORCE and policy, and a composite key so a version cannot point at another org's skill. Regenerate `infra/postgres/generated/` with `scripts/gen_tenant_migration.py`. A store module `gateway/studio/skills_store.py`, beside `gateway/brief/store.py`. **No route:** F2 owns the routes, and J1 owns the feature gate. The member purge (`routes/admin/members.py`) removes the member's skills | R8: org B reads none of org A's skills or versions, red first with FORCE removed. A skill is private by default. A stored version cannot be updated. Two `add_version` calls at once give two numbers or one clean refusal. `test_tenant_coverage.py` passes with a new case. `test_admin_member_purge.py` passes, and the purge removes the member's skills of this org only | HIGH | AGENT-SAFE |
| **F2a** | 🔨 **BUILT, dark (2026-11-01).** The skill routes over the F1 store: `gateway/routes/studio/skills.py`, on the router of B1, so `main.py` mounts no second router. `GET` and `POST /studio/skills`, `GET /studio/skills/{id}`, `GET /studio/skills/{id}/versions/{n}`, `POST /studio/skills/{id}/versions` and `PATCH /studio/skills/{id}/state`. **Switch:** the first line of each route reads `STUDIO_ORGS`, and an org outside it gets 404. **Tenant and member:** the org is the bound session and the member is `user.email`, never request input (R5). A skill of another member or org is 404 `Not found`, as a missing skill is. **Body checks**, before the store session opens: a depth cap of 20 (A2's cap), then `check_body`, then the catalog check. Each action is a catalog id of `actions.catalog_rows()`, once, and a skill holds at most 35 (the WS-67 pack cap). Class B and C actions are legal in a draft. **Slug:** with no slug, the route makes one from the name. A slug that the member holds is 409, and the text names no slug. **States:** the route owns the table of §7. No UI, no BFF, no migration | `test_studio_skills_routes.py`: a not-listed org gets 404 on each of the six routes, and a listed org and `*` get 200 or 201. An `organization_id` or `owner_email` in a body or a query changes nothing. A body 1500 levels deep is 422, never 500. An unknown or duplicate action is 422 with its path. An oversized body is 422. A slug conflict is 409 with no slug in the text. Each legal and each illegal move holds, and `test_state_machine_table` pins the table. `test_studio_skills_routes_r8.py` (`pytestmark = _DB_GATE`): org B and member 2 get 404 on the skill by id on every route, and do not list it. Two new versions at once give 1 and 2. A workflow of org B is not an action of org A. The unknown-action tests go red with the catalog check removed. Two moves at once give one 200 and one 409, and the test goes red without the row lock. A slug, a name or a description that Postgres refuses is 422, never 500, and writes no row | HIGH | AGENT-SAFE |
| **F2b** | 🔨 **BUILT, dark (2026-11-02).** **The skill creator UI.** Scope (narrowed, 2026-11-02): one private skill, made, versioned, published, paused and resumed by its owner, over the F2a routes. **Files:** a BFF `workbench/control_plane/src/app/api/studio/[[...path]]/route.ts`, a copy of the workflows proxy for the gateway prefix `/studio`, with PATCH added. It keeps `requireIdentity`, `gatewayHeaders`, `refuseUnsafePath` and `gatewayFetch`, and it adds no header, no identity and no path rule. The Skills section of `app/studio/page.tsx` becomes `components/SkillsGallery.tsx`: a list (name, state badge, version) from `GET /api/studio/skills`, a New skill button, and the present empty state when the list is empty. A 404, a 401 or a network miss keeps the present empty state with no button and no error, because every production org answers 404 until `STUDIO_ORGS` lists it. The routes `app/studio/skills/new/page.tsx` and `app/studio/skills/[id]/page.tsx` share one `SkillForm`. Each page opens with `AppTopBar` and a back link to `/studio`. **The form:** a name (the route makes the slug), one description (it fills `skill.description` and `body.description`), instructions, an action picker, and the buttons Save version, Publish, Pause and Resume. A "describe it" box fills a **no-model starter** (a pure function in `lib/studio/skillStarter.ts` that imports no fetch): `name` from the first 6 words, `description` as "Use this when ...", and `instructions` as a Markdown skeleton with "When to use", "Steps" and "Rules". It fills only empty fields, picks no action, and shows the label "Starting point". **The picker** reads `GET /api/studio/actions` and drops each row with `source == "package"`, because F2a refuses it. It allows class A, B and C, at most 35, no repeat. **Calls:** New skill is `POST /studio/skills` with a `body`. Save version is `POST .../versions`. Publish and Resume send `PATCH .../state {state:"published"}`. Pause sends `{state:"paused"}`. After a Save version on a published skill, the page shows the draft state that the answer returns, and the Publish button returns. **Errors:** a 409 on create shows the text of the store, "You already have a skill with this name. Choose another name.", at the name field. A 422 detail maps by its prefix: `$.actions[i]` to action row i, `$.actions` to the picker, `body.description` and `body.instructions` to their fields, and `name`, `slug` and `description` to theirs. A text with no known prefix shows whole under the form. The page never invents a path. A 409 on a move shows its text under the buttons and reloads the skill. **Non-goals:** a rename or a slug change (F2a has no route), a version list or a diff (F7), archive, share (J2), a test chat or test cases (F4), "Mira drafts" (F2c), skill cards (F5), `SKILL.md` (F8), a chat tool for a skill (F2d), a `gates` entry (the longest prefix `/studio` already gates both routes), and any edit of `studio.json`, `nav.ts` or `gates.generated.ts`. **Preview:** `scripts/dev_up.sh` gives the gateway `STUDIO_ORGS=*` as a fixed local line, with a case in `tests/unit/test_dev_up.py`. No box flips. **Chat parity (rule 15 of the control plane):** the six requests of `lib/studio/skillsApi.ts` are rows of `DEBT` in `chat_parity.py` under wave P-9, and the ceiling of `studio` is 6. F2d pays them and lowers the ceiling. | A member of the preview org creates a private skill with the starter, saves a version, publishes it, saves a second version (it returns to draft), publishes again, pauses it and resumes it, all in the UI on this PC. A second member (a gateway call as `X-User-Email: rohan@...`) lists none and gets 404 by id. A repeated name shows the text of the store at the name field. A bad action id shows its 422 text at that action row. A skill with no version shows "Add a version first" on Publish. A 404 from the list shows the present empty state with no button. `src/app/api/studio/[[...path]]/route.test.ts` passes: the PATCH, GET and POST paths, a 401 with no identity, a 400 for an unsafe path, the body and the query forwarded, a 409 and a 422 body passed through unchanged, a 204 passed, a gateway failure giving 502, and a source check that the file holds no `x-user-email`, no `organization_id` and no header beyond `gatewayHeaders`. `src/lib/studio/skillStarter.test.ts` and `skillErrors.test.ts` pass (the starter fill, the empty-field rule, the 422 prefix map for each spelling). `gatewayPath.test.ts` finds the new proxy and passes. `test_dev_up.py` passes. The tsc check runs in WSL. **Visual review:** four captures of `/studio` (the list) and `/studio/skills/[id]`: light mode, compact density, a changed accent, and 390 px, with the `Visual review:` line in the PR body (the UI gate blocks in build). The owner previews it before the merge (D145). | STD | AGENT-SAFE. After A1 and F2a. Metorite task 1306. The switch-on of `STUDIO_ORGS` in production is owner-only (J1). Chat parity is F2d |
| **F2c** | "Mira drafts" (§7 step 2): Mira writes the description, the instructions and the action list through a tool of a Studio pack, and she picks actions from the catalog only | A draft names only catalog actions, and it lands as a new version through F2a. A drafting turn stops at its credit cap. The pack stays off until the owner turns it on | HIGH | AGENT-SAFE. After G1. The owner decides the credit cap and the pack switch-on. Metorite task 1307 |
| **F2d** | **Chat tools for a skill** (Metorite task 1498). Today a UI act on a skill has no chat path (owner rule 2026-11-01). Three tools join the `studio` pack: `studio_skill_save` (create, or add a version), `studio_skill_state` (publish, pause, resume) and `studio_skill_list`. They call the F2a routes by HTTP as the member, as `_read_catalog` reads B1. A publish asks on a card first. The tool text shows each 409 and 422 text unchanged | A member makes, versions, publishes and pauses a skill in chat, and the same skill shows in `/studio`. A publish with no card is refused. A tool never takes an org or a member from its input. Tool parity holds for each F2b UI act | HIGH | AGENT-SAFE. After F2b. The production `TOOL_PACKS` line and the pack switch-on are owner-only |
| **F3** | Published skills load as org tool packs in `tool_packs.choose`. Each skill pack states its token cost and whether it changes the prompt prefix, and packs load in a fixed order (§11.1). Each loaded skill is a session event | A turn that matches a skill loads its actions and its rules. A skill of another member that is not shared never loads. A skill whose instructions hold an unresolved variable fails to load | HIGH | AGENT-SAFE. After WS-67 is live |
| **F4** | Skill tests on a dry-run door | A test fails when the turn calls another action. No write leaves the dry-run door. A test run stops at its credit cap | STD | AGENT-SAFE |
| **F5** | Skill cards: a skill renders a package view in chat | The card shows live rows, and its button reaches the member's approval card | STD | AGENT-SAFE |
| **F6** | SKL-1: after a multi-step task, "Save this as a skill?" or "Do this every week?" (task #818) | One accepted offer drafts a skill or a workflow from the chat's tool calls | STD | AGENT-SAFE |
| **F7** | SKL-2: history, one-step undo and archive when unused (task #819) | Each change shows who made it. Undo restores the version before | STD | AGENT-SAFE |
| **F8** | `SKILL.md` export and import, and skills over the MCP | An exported skill loads in a stock Agent Skills client. An import keeps only catalog actions | STD | AGENT-SAFE |
| **F9** | Retire the Agent Workshop and `AddAgentWizard` | The route and the wizard are gone. No test imports them | LOW | AGENT-SAFE |
| **F10** | **Each published action is a chat tool.** A package gets a pack with no hand-written skill. Tool text comes from the manifest. A record value carries the untrusted mark | A view button with no tool fails a fence. A class B action asks on a card in chat. A member of a package sees its tools, and another member does not | HIGH | AGENT-SAFE. After B1c, B2b and F3 |

### Group G — The builder

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **G1** | 🔨 **BUILT, dark (2026-11-01).** The builder tools for Mira: draft, patch, validate, outline and list actions. The tools are stateless, because the chat holds the manifest and A3 is Stage 2. A serialized manifest has a cap of 24,000 characters, and each tool result stays under that cap too. Agent `studio-builder` owns the `studio` pack. It is not a second assistant, and Mira alone talks to the member. It never runs as an agent: it is in no agent list (`_PACK_OWNER_AGENTS` in `gateway/routes/agent.py`), and every run refuses a hand-off to it or a run of it (`tool_packs.pack_only_refusal`). Preview waits for G4 | Each tool refuses an invalid patch with the validator's error, and the manifest stays as it was. No tool runs code. Verify: `uv run pytest tests/unit/test_studio_builder_tools.py tests/unit/test_studio_patch.py tests/unit/test_tool_packs_registry.py -q` | STD | AGENT-SAFE |
| **G2** | 🔨 **BUILT, dark (2026-11-01).** The interview and the plan card (§6.2 steps 1 and 2). Two tools join the `studio` pack. `studio_interview` checks at most five questions, one topic each (`who`, `records`, `next`, `approver`, `systems`). It refuses a repeated topic and a topic that the brief states. Its result is the exact input of `ask_questions`, which stays the one ask seam. `studio_plan` checks the manifest and shows one card with `request_confirmation`. The card draws each name from the manifest inside « », and the member rejects it to build nothing. The model cannot call that function, so the tool calls it and returns a `decision`: `approved`, `revised`, `stopped` or `no_card`. `stopped` means the card closed with no approval: a Reject, a timeout or an answer by another member. `no_card` means the chat shows no card. The plan text stays in `context`, and the member answers in words. A child agent never holds `studio_plan` (it is in `DISPLAY_TOOLS`). The card shows objects, views, workflows, skill, connections and actions. "Ask only what the brief does not say" is model judgement, and it is **advisory**. The build cost is **not priced yet**: no credit estimator exists, and J3 sets the caps. The card shows the count of parts and the line "Build cost: not priced yet. Credit limits come with a later release." It shows no price and no turn count | **Enforced:** one `studio_interview` call holds at most five questions, one topic each (`test_studio_interview_caps_at_five_questions`). **Advisory:** the limit across a whole request (one interview, one plan card, no direct `ask_questions` call) is a rule for Mira and no code counts it. A counter per run is a follow-up. An invalid manifest shows no card. A stop on the card writes nothing: `studio_plan` makes no write call and G1 keeps no store. After a stop, the rules forbid `studio_patch` (advisory). Fences: `test_studio_interview_caps_at_five_questions`, `test_studio_interview_refuses_repeat_topic`, `test_a_stop_writes_nothing_so_any_non_get_call_fails`. Verify: `uv run pytest tests/unit/test_studio_interview_plan.py tests/unit/test_studio_builder_tools.py tests/unit/test_tool_packs_registry.py -q` | STD | AGENT-SAFE |
| **G1b** | 🔨 **BUILT, dark (2026-11-01).** `studio_describe(part)` in the `studio` pack: the live JSON Schema of the manifest, the field types of `studio_fields` and the block catalog, so Mira reads them before she drafts (§11.1). Stateless and read-only, like G1 | **Built:** `part` is `manifest`, `fields` or `blocks`. A bad part gives `ok: false` and the legal parts. The tool reads `Manifest.model_json_schema()`, `studio_fields` and `BLOCKS` live and copies none. It adds no per-block prop docs. The pack now holds eight tools. The schema it returns is the one A2 checks against, from the same source. A test fails when the two differ. Each result stays under G1's cap | STD | AGENT-SAFE. Stage 2 |
| **G1c** | **Chat saves and publishes** (task 1445). No tool of G1, G1b or G2 writes a store, so a chat build ends with nothing saved. Two tools join the `studio` pack: `studio_save` and `studio_publish`. They call the A5 routes by HTTP as the member, the way `_read_catalog` reads B1. `studio_publish` runs only after the G2 plan card returns `approved` for the same draft. The chat creates once, then saves by id. `studio_list_actions` passes `?app=` for the package that the member builds, so the B1c rows of other packages do not bury the door ops | A member asks for a visit log in chat, approves the plan, and a real package exists with its object rows. A refused publish returns its problem list in a form that Mira can act on. A publish with no approved plan is refused. Tool parity holds (`test_chat_parity.py`) | HIGH | AGENT-SAFE. After A5 |
| **G3** | The Studio build screen (§6.3). A build session keeps one goal record of WEB-10 (§11.1) | Outline, preview and the shell's dock with `studio-builder`. `seams.test.ts` passes. A build that reaches its round cap stops with a block code that the member can read | STD | AGENT-SAFE |
| **G4** | Preview on sample data, with a preview store and simulated sends | A preview write never reaches `app_data` of the live package. A send in preview leaves no message | HIGH | AGENT-SAFE |
| **G5** | Edit by diff, with one-step undo | Each change shows its diff before it applies | STD | AGENT-SAFE |
| **G6** | The build eval: 30 prompts from §2.1. The regression cases replay recorded builder turns on the WEB-12 harness, with no model and no network (§11.1). The live run is the quality gate only | The replay cases run in the unit suite at no cost. The eval reports the pass rate per shape. The owner sets the run cost before the live run | STD | **OWNER-GATE** for a live run over USD 0.50. The replay half waits on WEB-12 |
| **G7** | The builder on the CA-14 harness profile `app_builder`, for the code block | Needs CA-14 (#1042) | HIGH | AGENT-SAFE. After CA-14 |

### Group H — The shell

**Since D147, H1 and H2 run on the package-host registry.** `package_host.md` owns the registry, the scopes, the lifetimes and the slots. PKG-8 moves H1 and H2 onto it, and J2, J3 and M1 share its install path (PKG-9 to PKG-12).

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **H1** | **Carried by WS-PKG PKG-8** (`package_host.md`, D147). Installed apps in the sidebar, per org, through the one run-time registry. Studio builds no list of its own | An installed app shows for a member who may open it, and for no one else. `nav.test.ts` still counts only the built-in live panes | STD | AGENT-SAFE |
| **H2** | **Carried by WS-PKG PKG-8** (`package_host.md`, D147). App jobs, needs, Home cards and search from the manifest into the shell seams | A job of an installed app shows in ⌘K "Do". A need shows in the bell | STD | AGENT-SAFE |
| **H3** | Phone and WhatsApp: a skill's cards over WhatsApp | A skill's write on WhatsApp still asks once | STD | AGENT-SAFE |

### Group I — Connectors

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **I1** | A package names its connections. Install checks them, and Integrations lists the apps that use each one | An app with a missing connection installs in the "needs a connection" state and runs no action | STD | AGENT-SAFE |
| **I2** | Generate an app from a connection or an MCP server (§8.2) | A test MCP server with two reads and one write gives two lists, two details and one form with an approval | STD | AGENT-SAFE |
| **I3** | **Mail merge.** An `email.merge_send` action: one template, one attachment for each person, one approval, a throttle, a status for each person. It copies the pattern of `whatsapp.broadcast` | A run of 300 stops at the provider limit and resumes. A resend sends none twice. A send to a person outside the scope asks | HIGH | AGENT-SAFE. After E8. STU-Q13 |
| **I4** | **Doors for the rest.** Ask WS-MCP to plan the doors of attachments, documents and notes, and a write op for `people_absences` | Each built-in app that §19.6 names has a row in `external_agent_access.md` | LOW | AGENT-SAFE |

### Group J — Governance

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **J1** | `feature:studio` and `studio:publish` through the one entitlement seam | A member without `feature:studio` gets 403 on each Studio route | HIGH | AGENT-SAFE. Waits on STU-Q4 |
| **J2** | Share: private → people or a group → the organization, with an approval for the organization | A share to the organization without approval stays pending. Approval publishes one version only | HIGH | AGENT-SAFE |
| **J3** | The kill switch, the credit caps and a `usage_event` row for each model call | A paused package runs no new action within 10 seconds. A build stops at its cap | HIGH | AGENT-SAFE |
| **J4** | The admin view: every package, owner, version, runs, last error and use. It joins AUTO-4 (task #817) | An admin sees every package of the org. A member sees only their own | STD | AGENT-SAFE |
| **J6** | **The install consent screen.** What the app reads, writes and sends, who sees it, its runtime kind, its sensitive fields and its `run_as` | The screen draws from the manifest, with no hand-written list. A new version that widens any of these asks again | STD | AGENT-SAFE. After PKG-8 |

### Groups K, L and M — Templates, code, pinned answers

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **K1** | App templates in `app_templates`: U7, U8, U15, U16, U19 and U9 first | Each template installs in one step and passes D12 | LOW | AGENT-SAFE |
| **K2** | A template update to an installed app that a member changed | The update keeps each local change, or names the conflict | HIGH | AGENT-SAFE |
| **K3** | **First-party template: inventory and procurement** (D146.6, §3.7 example). Items with product lines and units, locations, the stock ledger, vendors, purchase requests and purchase orders with an approval chain by amount, and goods receipt with a QC step. It posts to Tally or Zoho Books through a connector | It installs in one step and passes D12. A receipt writes stock moves that keep stock on hand right. A purchase order above the limit waits for a director | STD | AGENT-SAFE |
| **K4** | **First-party template: MRP-lite and the production schedule** (D146.6). BOMs, work orders, machines, the nightly MRP run (a B4 module) and a production timeline by machine | On a fixed test set, the MRP run proposes the expected planned orders. Each planned order waits for approval. The timeline shows each work order on its machine | HIGH | AGENT-SAFE |
| **K5** | **First-party template: HR and payroll.** The objects, rules, workflows and views of §19.8, at kind `none`, with sample data | An HR team installs it and runs one month on sample data: punches, leave, a pay run, slips, mail. No sandbox runs. Real data waits on STU-Q11 | STD | AGENT-SAFE. After the slices of §19.9 |
| **L1** | The code block (§5.4) | Code runs with the `--cc-*` tokens only, and every bridge call goes through B2 | HIGH | AGENT-SAFE. After SBX-9 and CA-14 |
| **L2** | Move existing App Workshop apps into packages with `format = code` | Each existing app opens and runs from Studio | STD | AGENT-SAFE |
| **M1** | **Grown into WS-PKG PKG-12** (`package_host.md`, D147): Mira makes a private package from chat, with an end date. Pin a chat card or an artifact as a draft package page, with a 30-day expiry | A pinned page opens by link after the chat ends. It expires on its date, and its owner can restore it | STD | AGENT-SAFE |

### Group O — Apps for the outside world, and apps over any system (§20)

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **O1** | **One public door** for every public surface, as CRM-M10 specifies it: an opaque token, a SECURITY DEFINER lookup, then `tenant_session(org)`. Its own host with no cookies. A shared rate limiter in Redis through the prefix wrapper, a bot trap, an optional captcha, size caps, a flat 404. CRM forms and Studio surfaces are its two users. CRM-M10 narrows to "a CRM form on the public door" | A post with a valid token reaches one org, and a forged token or another org's token gives a flat 404 and writes nothing. The 11th post in a minute from one address is refused across two gateway processes. The route sets and reads no cookie | HIGH | **OWNER-GATE** until STU-Q16 (CRM OPEN-1). Build nothing past this spec before the answer |
| **O2** | **Surfaces in the manifest:** `surfaces: [{id, kind, view, audience, actions, closes_at?}]`, and the WS-PKG point `surfaces` (one owner for each id). Only an org-scope install holds one, and an admin turns each one on. Each act runs as the install's service identity (B6), limited to the declared actions, and the audit names the surface | A guest submits a form and one record lands in state `new`. A guest cannot call an undeclared action. A private or group install with a surface is refused at install. A paused surface answers within 10 s | HIGH | AGENT-SAFE. After O1, A5 and B6 |
| **O3** | **Kiosk devices.** An admin pairs a tablet with a QR code. A revocable device token for one surface. Full screen, no shell, a clear after each visitor and after an idle time. No seat | A paired tablet shows the surface and clears after a submit. A revoked token gets 404 at once. Visual review at tablet and phone widths | HIGH | AGENT-SAFE. After O2. D145 preview |
| **O4** | **Signed record links**: view, pay or cancel one record, or the records of one contact. One purpose, an expiry, and no org name or plain record id in the link | An expired or altered link gives a flat 404. A link for one record never shows another. A cancel link runs only the declared action | HIGH | AGENT-SAFE. After O1 |
| **O5** | **Outside people.** `crm_contacts` is the one store for a person outside the org. A Studio `relation` field can point at a CRM contact. A contact found by phone or email is reused. Consent is the CRM-M11 record. Writes need Native mode | A form and a WhatsApp message from the same phone give one contact. A Mirror org refuses with a clear reason. A contact never gets a session or a seat | HIGH | AGENT-SAFE. With CRM-M11. Real personal data waits on STU-Q21 and STU-Q22 |
| **O6** | **Org channel numbers.** An org owns a WhatsApp number that answers outside people. A package claims a number and an entry keyword through the WS-PKG point `channels`. It reuses the webhook split and the SECURITY DEFINER lookup of migration 230 | A message to an org number reaches the package that claims it, in the right org. Two packages cannot claim the same pair. A member number keeps working as today | HIGH | AGENT-SAFE build. **OWNER-GATE** for a live number (STU-Q19) |
| **O7** | **Channel events and conversation runs.** `channel.message` on the event bus, with the contact found or created (O5). A conversation run keeps its state for each contact and flow: the step, the values, a timeout | Two messages from one phone continue one run. A run that times out ends and says so. Another org's message never reaches the run | HIGH | AGENT-SAFE. After O6, H-328 and B6 |
| **O8** | **Conversation steps** as workflow nodes: ask (a `studio_fields` type and its checks), choose (lists and buttons), a WhatsApp Flow form, an AI step (Mira with only the flow's skill and actions), call an action, wait, hand off to a member's inbox, end. Free text inside the 24-hour window, a template outside it | A flow books a slot from free text. The AI step cannot call an action outside the flow. A reply outside the window uses a template | HIGH | AGENT-SAFE. After O7 |
| **O9** | **Sends to outside people** through the Action Broker: a WhatsApp template from an org number, mail from an org sender, SMS. A send that the manifest declares is approved once by an admin at publish (D131). Each other send asks. Every path checks the CRM-M11 suppression list, opt-out and quiet hours | A declared confirmation goes to the booking's contact only. A send to anyone else asks. A suppressed contact gets nothing | HIGH | AGENT-SAFE build. **OWNER-GATE** for a live send to a real person (CLAUDE.md §3a rule 3), STU-Q17 and STU-Q20 |
| **O10** | **Payments.** The org's own account (Razorpay first) as a row of `integration_connections`. `payments.create_link` (B), `payments.get` (A), `payments.refund` (C, always a card). A signed provider webhook emits `payment.paid` and `payment.failed`. Metorite holds no money and never reuses the Customer Console seam | A test-mode link pays and the event moves the record. A forged webhook gives 404. A refund always asks | HIGH | AGENT-SAFE in test mode. **OWNER-GATE** for a live account (STU-Q18) |
| **O11** | **Bookings**: resources, schedules, slots computed on read, a hold with an expiry (C6 and an E3 schedule), no double booking (a C1b `unique` rule), the Calendar door, reminders to the member (WS-66) and to the contact (O9). A `slots` block, and a clinic template | Two guests who take the same slot at once give one booking. An unpaid hold frees its slot at its expiry. The booking shows on the member's calendar | STD | AGENT-SAFE. After O5, C6, CE-4 and MCP-P4. D145 preview |
| **O12** | **Apps over any outside system.** An OpenAPI import grows `custom_api_definitions` into a connection with actions (GET A, other verbs B, DELETE C, an admin may raise a class). Calls go through `outbound_guard.py` with the credentials of `connections/store.py`, a size cap and the untrusted mark (#1444). A remote object (`source: {connection, list, get, create?, update?}`). A mirror into the store, with a cursor and a cap | An imported test API gives a list, a detail and a form. A remote object reads through the door as the viewer. A mirror run is idempotent. No call reaches a private address | HIGH | AGENT-SAFE. After I1, I2 and B1b |
| **O13** | **Inbound events for each connection.** `/workflows/hooks` grows into one inbound route for each connection, which binds the tenant and checks the provider's signature | A signed Razorpay or Odoo event starts one run in the right org. A replay starts none | HIGH | AGENT-SAFE. After H-328 |
| **O14** | **Insights over app data.** A scheduled insight run for each package (B6) reads aggregates (C2b) and searches (C5), and writes a Home card or a bell item through the WS-PKG points `cards` and `needs`. No restricted field (C9). A credit cap (J3) | A weekly run writes one card with its numbers and their source. A restricted field never reaches the model. A run over its cap stops and says so | STD | AGENT-SAFE. After C5, C2b, B6 and J3 |

---

## 15. Stages and dependencies

| Stage | Slices | What a member gets | Depends on |
|---|---|---|---|
| **1 · Studio opens** (M1, dark) | A1, A2, B1, D1, E1, F1, F2a, F2b, F2c, G1 | One Studio pane in preview, the skill creator for private skills, the catalog, and the manifest | None. D146 answered STU-Q1 and Q2 |
| **2 · First apps** (M2) | A3, A4, A5, B1c, B2a, B2b, B2c-1, B2c-2, C1, C1b, C2a, C2b, C6, C7, C8, D2, D3, D4, D11, D12, F3, F5, F10, G1c, G2, G3, G4, G5, H1, J1, J2, K1, K3 | Build, preview and publish an S2 app in chat. Tables, forms, boards and charts. The typed object store with rules. The inventory and procurement template | J1 waits on STU-Q4. F3 needs WS-67 live |
| **3 · Automations and rich blocks** | B1b, B4, B6, B7, C3, C4, C5, C9, C10, C11, D5 to D9, D13, E2 to E6, E8, F4, F6, F7, H2, I1, I2, I3, I4, J3, J4, J6, K4, K5 | S1 and S3 in full, connector apps, calendar, timeline, mind map, playbooks, first-party logic and the MRP-lite template | H-328 for unattended runs. WS-67 for F3 |
| **4 · Code, outside agents, scale** | B3, B5, B8, C12, D10, F8, F9, G6, G7, H3, K2, L1, L2, M1 | Member logic modules, the code block, MCP access, pinned answers | MCP-P1b, SBX-9, CA-14 (#1042), MCP-P5 (#869) |
| **5 · Outside world** | O1 to O14, with CRM-M8 and CRM-M11 | Public forms and kiosks, WhatsApp flows, payments and bookings, apps over any outside system, and insights (§20) | Stage 2 apps live. STU-Q16 to STU-Q23. H-328 and B6 for unattended runs |

**The first dispatchable slice is B1.** It waits on no owner question, writes nothing, and every later group reads it.

---

## 16. Current file paths

Each path was read on `82157e5fd`. A3 and C1 added their paths after that commit.

**Backend**

- `apps/services/gateway/gateway/routes/workflows/` — `catalog.py` (`_integration_entries`, `_tool_entry`), `tools.py` (`WorkflowToolSpec`, `list_tools`), `core.py` (the router pattern), `search.py`, `triggers.py`, `scheduler.py`, `copilot.py`, `engine/graph.py`, `engine/runner.py`, `engine/handlers.py`, `engine/modules.py`
- `apps/services/gateway/gateway/db.py` (`tenant_session`), `gateway/main.py` (the router mounts), `gateway/push/flags.py` (an `*_ORGS` reader)
- `apps/services/gateway/gateway/studio/skills_store.py` — the F1 skill store, the one writer of `studio_skills` and `studio_skill_versions` (migration `infra/postgres/320_studio_skills.sql`)
- `apps/services/gateway/gateway/studio/packages_store.py` — the A3 package store, the one writer of a Studio package in `apps` (migration `infra/postgres/322_apps_format.sql`). A3 added it after `82157e5fd`. A4 added `publish_version`, `rollback`, `list_versions` and `get_version`, over `app_versions` and `app_audit` (`infra/postgres/114_custom_apps.sql`)
- `apps/services/gateway/gateway/routes/apps/_common.py` (`load_app`, `workspace_dir`, and `record_app_audit_bound`, the audit writer of B2a), `lifecycle.py` (`list_apps`), `pins.py` (`list_pinned_apps`), and `routes/workspace.py` (`_app_workspace_for`) — the App Workshop readers that skip a package
- `apps/services/gateway/gateway/studio/objects_store.py` — the C1 object store, the one writer of `studio_objects`, `studio_object_fields`, `studio_records` and `studio_record_hot` (migration `infra/postgres/323_studio_objects.sql`)
- `packages/acb_common/acb_common/studio_rules.py` — the C1b grammar of a rule and its pure checks. It holds no SQL
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
- `infra/postgres/114_custom_apps.sql` (`apps`, `app_versions`, `app_grants`, `app_audit`, `app_data`), `123_app_templates.sql`

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

**Tooling**

- `scripts/dev_up.sh` and `tests/unit/test_dev_up.py` (the local stack on one PC, which F2b gives `STUDIO_ORGS=*`)
- `packages/acb_skills/acb_skills/chat_parity.py` (the `DEBT` rows of the skill creator, until F2d)

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
| The renderer's node list matches the backend's | `tests/unit/test_genui_catalog_lockstep.py` |
| No app mounts its own palette, bell, assistant rail or ⌘K | `src/lib/shell/seams.test.ts` |
| The live pane count changes only on purpose | `src/lib/nav.test.ts` |
| No colour literal or px font in UI code | `src/lib/theme/conformance.test.ts` |
| A manifest names no colour, px size, unknown block or class X action | New with A2 and D12: `tests/unit/test_studio_manifest.py` |
| A package reaches data only through a declared catalog action | New with B2a: `tests/unit/test_studio_action_door_r8.py` (R8) and `tests/unit/test_studio_action_door.py` |
| The class of an object action has one source, and its verbs are the verbs of the A2 grammar | New with B2a: `OBJECT_OP_CLASSES`, pinned by `test_the_object_verb_table_holds_the_five_verbs_of_the_grammar` in `tests/unit/test_studio_action_door.py` |
| The door opens only the object get and the object list | New with B2b-1: `test_the_door_opens_the_object_get_and_the_object_list_only` in `tests/unit/test_studio_action_door.py` |
| A list body takes only the keys of the one input schema | New with B2b-1: `test_the_accepted_keys_are_the_properties_of_the_input_schema` in `tests/unit/test_studio_action_door.py` |
| Only a Studio package owns an object | New with B2a: `test_r8_a_code_app_owns_no_object` in `tests/unit/test_studio_objects_r8.py` |
| A catalog row never carries class X | New with B1: `tests/unit/test_studio_catalog.py` |
| A package is private to its org and its owner, and its manifest passes A2 before each write | New with A3: `tests/unit/test_studio_packages_r8.py` (R8) and `tests/unit/test_studio_packages_store.py` |
| The format of an app never changes | New with A3: the trigger `apps_format_fixed`, pinned by `tests/unit/test_studio_packages_r8.py` |
| No App Workshop list, slug route, pin, agent tool or session workspace shows a package | New with A3: `tests/unit/test_studio_packages_r8.py` and `tests/unit/test_studio_packages_store.py` |
| An empty `workspace_path` never means the working directory | New with A3: `tests/unit/test_studio_packages_store.py` |
| Only `packages_store.py` writes a package | New with A3: `test_only_the_package_store_writes_a_package` in `tests/unit/test_studio_packages_store.py` |
| A publish validates the draft against the catalog, and writes a version, the pointer and the audit row in one transaction | New with A4: `tests/unit/test_studio_packages_store.py` (group h) and `tests/unit/test_studio_packages_versions_r8.py` |
| A publish or a rollback never edits a version row. A package never meets the App Workshop review step, and the store never calls `record_app_audit` | New with A4: `test_a_version_row_is_never_updated_or_deleted_and_no_review_is_proposed` in `tests/unit/test_studio_packages_store.py`, and `test_r8_a_publish_after_a_rollback_never_edits_an_old_row` |
| Two publishes of one package get N and N+1 | New with A4: `test_r8_two_writers_on_two_connections_get_n_and_n_plus_1` |
| One field-type module. No other module holds a tuple, a list or a `Literal` of the type names | New with C1: `tests/unit/test_studio_fields.py::test_one_field_type_module` |
| One writer. Only `gateway/studio/objects_store.py` runs SQL against the four object tables | New with C1: `tests/unit/test_studio_fields.py::test_one_writer_of_the_four_tables` |
| The object query binds each field key and each value, and its SQL text takes a column and an operator from a constant map only | New with C2a: `test_no_fstring_in_the_query_sql_takes_a_value` and `test_a_hostile_key_or_value_never_reaches_the_sql` in `tests/unit/test_studio_query.py` |
| A query reads one object of one package of one org. A filter on a field that is not hot runs on at most 5,000 records, and a timeout leaves the session usable | New with C2a: `tests/unit/test_studio_query_r8.py` (R8) |
| A hot sort reads its index in order, and a Sort node takes only the rows of one page, on 50,000 records | New with C2a: `test_r8_a_hot_sort_reads_its_index_in_order_and_sorts_only_the_page` in `tests/unit/test_studio_query_r8.py` |
| One statement reads a page and its total, so a write between two reads never puts a record on a page twice | New with C2a fix round 1: `test_r8_a_write_between_two_reads_never_puts_a_record_twice` in `tests/unit/test_studio_query_r8.py` |
| A filter on a field that is not hot reads `data` only for the candidates of the hot filters, and no count or page scans a table | New with C2a fix round 1: `test_r8_a_cold_filter_after_a_hot_filter_reads_at_most_5000_records` and `test_r8_a_count_reads_the_candidates_of_the_page_and_no_table_scan` |
| A checkbox has two states, and `is_empty` alone tests for no value | New with C2a fix round 2: `test_r8_a_checkbox_has_two_states` in `tests/unit/test_studio_query_r8.py` |
| The guard and the check of the hot fields read the snapshot of the page | New with C2a fix round 2: `test_r8_a_bulk_insert_during_a_query_cannot_pass_the_guard` and `test_r8_a_field_that_turns_cold_during_a_query_gives_the_right_page` |
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
| Reads another app | `<app>.<op>` through the door, as the viewer | Knowledge only (§19.6) |
| Gets a task or a reminder | A human step is a task in `pm_tasks` (E4). A need goes to the bell | Not built |
| Starts other work when a record changes | `studio.record.*` events (C3) start a workflow | Not built |

**Chat parity by construction.** A view can only call an action that the package declares in `uses`. F10 turns each declared action into a tool of the package's pack. So a UI action with no chat path cannot exist, and no second list holds them. This meets the owner's chat-first rule with one mechanism.

**A record value is untrusted text.** A leave reason can hold an instruction to Mira. Each record value that reaches a model carries the untrusted mark of WEB-1. A class B or C action still needs the member's card, so such a text cannot start a write.

### 19.2 The guardrails, stage by stage

The same gates bind a skill (a tool set), a workflow and an app.

| Stage | The gate | State |
|---|---|---|
| **Build** | The A2 validator, catalog actions only, no class X, the secret scan, the caps, the plan card (G2) | Built |
| **Build** | Preview on sample data (G4), dry-run tests with no real write (F4) | Not built |
| **Publish** | One immutable version (A4). A review of the manifest diff by a holder of `studio:publish` (J2) | Version built, review not built |
| **Install** | The consent screen (J6): what the app reads, what it writes, what it sends, who sees it, its runtime kind and its sensitive fields | Not built |
| **Run** | One door, the viewer's rights, approval by action class, the trust ladder (D131), a credit cap, the kill switch (J3), the audit row | Door and audit built for class A. The rest are not built |
| **Data** | Row rules, field sensitivity, retention and org caps (§19.7, C8 to C10) | Not built |

**A package with no chat run has one named human.** A scheduled run has no viewer. B6 makes an admin name a `run_as` member at install. The run holds the rights of that member, cut down to the `uses` of the package. Each write carries `system:package:<id>` and the name of that member.

### 19.3 How an app says what it needs to run

A new `runtime` key names one of four kinds. No key means `none`. The builder picks the lowest kind that works.

| Kind | Runs where | For | Review |
|---|---|---|---|
| **`none`** | The platform | Manifest, rules, workflows, first-party actions and modules (B4). No member code | The usual review |
| **`browser`** | The viewer's browser, in the sandboxed frame: `allow-scripts`, `connect-src 'none'` | A custom screen, a scanner, a bespoke chart (L1). Each call goes through the door | `studio:publish` reads the code |
| **`sandbox`** | A sandbox with no network, on the standard image | Member Python or Node (B5) | A holder of `tools:approve` reads the code and approves one version (D130.3). The author may run a private version on sample data first |
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
| **Who** | The builder proposes the list. A holder of `tools:approve` approves it |

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

- **The store.** `studio_record_vectors`, a new table with one row for each chunk, `vector(1536)` and an HNSW index, under FORCE RLS. `kb_chunks` refuses a new `source_kind`, and `knowledge_base.md` already puts vectors in the app's own table.
- **The pipeline is the Knowledge one.** A NULL vector on write, an embed after the session, a sweep each 120 s, `text-embedding-3-small` on `tier-embed`, chunks of about 600 tokens. The vector row cascades from the record.
- **The seam.** One `Source` in `retrieval/sources.py` calls `search_records` as the member, so `search_company` finds records with no second search path. It applies C8, so a search never shows a record that the viewer cannot open.
- **The caps.** At most 4 searchable fields on an object and 200,000 chunks for an org. An embed costs 0 credits today, because the card is absorbed, so this cap is the only brake.
- **A restricted field is never embedded**, because the embed sends its text to a vendor.

**Use the right tool for the question.** "How many leaves did Priya take?" is a structured query (C2a, C2b). "Who asked about relocation?" is a meaning search (C5). Mira gets both, and the tool text says which is which. A vector search never answers a number.

### 19.7 Access inside an app, and sensitive data

Today each viewer of a shared app would see every record, and the store holds no sensitivity. An HRMS cannot ship on that.

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
- **`person` needs query ops.** C2a gives `person` no op today.

**Field sensitivity (C9).** A field takes a `sensitivity`.

| Level | Example | Effect |
|---|---|---|
| `normal` | Leave type | None |
| `personal` | Phone, address, punch logs | Left out of logs and search snippets. Not embedded unless the object says so. It stays hot and filterable, because attendance queries it |
| `restricted` | Salary, bank account, PAN, Aadhaar | Encrypted in `data` with a per-org key. Never hot, never filtered, never embedded. Each read writes an audit row with no value. Left out of exports. A package with such a field must declare `access` |

**A formula over a restricted field.** Its result is restricted too. It runs in the gateway, and a total for a pay review is added in Python, because SQL cannot read the ciphertext. Only a role with `read: all` gets a total.

**A model never sees a restricted value.** A tool result holds `[restricted]`. The card takes the value from the door straight to the viewer's browser. So Mira can show a member their pay and never read it. The fence: a test runs every chat path over a restricted field and greps what reaches the model.

**Biometric data.** Metorite stores a punch: employee code, device, time and direction. It never stores a fingerprint or a face template. The template of K5 holds no such field, and review refuses one.

**Retention (C10).** An object takes `retention_days`, and a sweep deletes older records. The caps of C10 are new: 1,000,000 records and 2 GiB of JSON for an org, and more hot fields. 200 people with 4 punches a day make about 250,000 records a year, and an HRMS plus an inventory app pass today's 24 hot fields.

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
| 8. Slips | Each line gets a PDF slip from one template | `render_pdf`, a template block | **D13**: no action makes a PDF, `render_pdf` takes no image, and a `file` field holds only an id |
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
| 7 | Unattended runs that bind the tenant, and a run with no viewer | H-328, E3, **B6** (new) | Every schedule |
| 8 | Direct writes from the app's screen, and delegated writes with approval (§20.4) | **B2c-1**, B2c-2 after MCP-P1b-1 | Every write |
| 9 | Ingest, batch, documents and mail merge | **C11**, **E8**, **D13**, **I3** (new) | HR, finance, operations |
| 10 | Doors for People and Calendar, and a plan for the rest | MCP-P4, **I4** (new) | Reach into built-in apps |
| 11 | Retention, caps, consent and search | **C10**, **J6** (new), C5 | Scale and trust |
| 12 | Code: the runtime key, the sandbox, its meter, layers | **B7** (new), SBX-9, B5, L1, **B8** (new) | Member code only |
| 13 | Data shared between packages | **C12** (new) | App suites |
| 14 | The template that proves all of it | **K5** (new) | The owner's example |

**Fences for this section.** Each slice names its own when it lands. The first four are: a UI action with no chat tool fails (F10), a member reads no record of another member past a row rule (C8), a restricted value reaches no model (C9), and the validator refuses a `hosts` key in `runtime` (B7).

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
| **AI over data** | One search seam (`retrieval/sources.py`). A new source is one `Source` row. The knowledge graph of D128 | Studio records are not indexed (C5 is planned). No "ask my app data" path, and no scheduled insight |
| **Identity** | Members, roles, groups and the grant grammar (`org`, `group:<slug>`, an email). The one entitlement intersect | No identity for a person outside the org. Outside people exist only as rows of each app: `crm_contacts`, `wa_contacts` and `email_contacts` |
| **Outbound** | The Action Broker runs each outward write, with approval where needed | Mail leaves only from a member's own mailbox. No SMS provider. No workflow step sends to a person outside the org |

### 20.2 How an app joins the rest of Metorite

An app is a package (WS-PKG, D147). It joins each system through the seam that the system already owns. It never gets a second user list, a second permission model or a second search.

| Concern | The seam | Slice |
|---|---|---|
| Sign-in and seats | The one member session. A Studio app has no login of its own | None |
| Who may build and publish | `feature:studio` and `studio:publish` through the one entitlement seam | J1 (STU-Q4) |
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
| 2 | An admin turns the surface on and pairs a tablet | Surfaces (O2), kiosk devices (O3) | New, owner-gated (STU-Q16) |
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
