# AI Studio — skills, workflows and apps that a company builds for itself (WS-STU)

> **Product:** Metorite · **Feature:** AI Studio (`/studio`) · **Updated:** 2026-10-31 · **Owner:** vjvarada
> **Status:** 🔨 **B1 BUILT and dark (2026-10-31)** behind `STUDIO_ORGS`, which is empty on every box. Every other slice is spec only. The pieces it reuses are built, and §16 names each one.
> **Verified against code on 2026-10-31 at `57b91f721`.** Each path in §16 was read on that commit.
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
            │  Objects   typed records, tenant-scoped           │ → app_data (FORCE RLS)
            └───────────────────────────────────────────────────┘
```

**The rule that holds it together: every layer above Objects reaches data only through an action.** A view reads through an action. A form submits through an action. A workflow step is an action. A skill's tools are actions. The MCP door serves actions. So one permission check, one approval path and one audit row cover every way into an app.

### 3.1 Objects

An object is a typed record kind that the package owns: `visit`, `claim`, `asset`. It has fields of these types: text, long text, number, money, date, datetime, checkbox, select, multi-select, person, relation, file, url, email, phone and geo.

- **Storage (D146, STU-Q5).** Records live in a **typed object store**, not in `app_data`. An object registry holds each object and its fields. Records are JSONB rows on shared tables with FORCE ROW LEVEL SECURITY, and no tenant gets its own DDL. Hot fields get indexes within a per-org budget. One action may write several records in one transaction, so a stock move writes both ledger rows or neither. Constraints run on each write, for example "stock never goes below zero". This is launch plan E10, brought into Studio. `app_data` stays the store of `window.cc` storage for a code-format app (L2).
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

**Why one catalog.** Today five consumers each keep their own list: the workflow palette, the `window.cc` bridge (`tools.call`), the `app_<slug>_<action>` agent tools, the chat tool packs and the MCP door. A catalog keyed by the D136 manifest serves all five. A new app joins all five when it ships its manifest.

### 3.3 Workflows

Studio uses the built engine (`gateway/routes/workflows/`, D6, ADR-028). It compiles a graph to MAF, runs five trigger kinds and pauses on approval and wait nodes. Studio adds:

- **Studio triggers** (E3): a record is created or changes, a form is submitted, a button is pressed, or Mira runs it from a skill.
- **A human step** (E4): the step makes a task in the one task store (`pm_tasks`) for a person, then waits until the task is done. A **playbook** is a workflow whose steps are mostly human steps. It needs no second engine.
- **Catalog nodes** (E5): any action of §3.2 is a node, including an MCP tool.
- **Fan-in and loops** (E2): `workflows_app.md` §8.3b and §8.3c, unchanged.

⚠️ **An unattended run must bind its tenant first.** Today the event, schedule and webhook runs do not (`workflows_app.md` header, HANDOFF H-328, H4 slice 6c). No S1 workflow goes live on production for a second organization until that lands.

### 3.4 Views

A view is one page of an app. It is a layout of blocks (§5). Each block names a `source` (an action, or an object query) and a field mapping. The renderer owns loading, empty and error states, so no manifest writes them.

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
| **2 · Rules** | Formula fields, roll-ups (stock on hand is the sum of the moves), state machines (a PO goes draft → approved → sent), constraints | An interpreted expression language with no I/O, no loops and a step cap | C6, C7 |
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
4. **Versions.** Publishing snapshots an immutable version (`app_versions`, `workflow_versions`). An open workflow run keeps its version. Rollback republishes an older version.
5. **Trust ladder.** An S1 workflow starts at "ask each run" and climbs by D131. Five acts always ask: money, a member or role write, a delete, a bulk change, and a send to an outside person whom the scope does not name.
6. **Cost.** Each build and each run has a credit cap. Every model call writes a `usage_event` row (the lesson of H-287).
7. **Kill switch.** An admin pauses a package, a workflow or a skill in one step. New actions stop within 10 seconds.
8. **Audit.** `app_audit` records each publish, share, install and action, with the actor and the version.
9. **Entitlements.** `feature:studio` and `studio:publish` map through the one entitlement seam (old STU-7). Pricing stays flat (`launch_surface.md` §4, STU-Q7).

---

## 10. What we keep, change and retire

| Built piece | Fate |
|---|---|
| Workflow engine, editor, Copilot, Module Studio (`routes/workflows/`, `/workflows`) | **Keep.** The editor moves into Studio (A1). The engine does not change |
| App Workshop tables (`apps`, `app_versions`, `app_grants`, `app_audit`, `app_templates`) | **Keep as the package store** (A3). No second app table |
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

---

## 13. Shell manifest (R9)

This is the manifest of the Studio pane itself, in the shape of `navigation_shell.md` §5 (D89). §4 covers the apps that Studio makes.

| Field | Value |
|---|---|
| **Team** | `ai-studio` |
| **Purpose** (blurb) | "Build skills, workflows and apps for your team." |
| **Jobs** | "Build an app" (describe bar), "Teach Mira a skill" (skill draft), "Automate a task" (workflow draft). Each opens its draft route |
| **Search provider** | Packages, workflows and skills by name and purpose |
| **Needs you** | A share waiting on your approval, a failed run of your workflow, a skill test that broke after an edit |
| **Home cards** | "Your apps" (altitude: me), "Waiting for approval" (altitude: admin) |
| **Agent** | `studio-builder` |
| **Own palette, bell, assistant rail or ⌘K** | None |

---

## 14. Slices

**Tier** is the review tier of `next-ticket.md` step 1b: LOW, STD or HIGH. Every slice ships dark or in preview. Migration numbers are the next free number at build time (R1).

### Group A — The pane and the package

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **A1** | The Studio pane at `/studio` (preview). One library lists apps (`apps`), workflows (`workflows`) and skills (F1, when it exists). `/workflows`, `/build/apps` and `/build/agents` redirect to it. `nav.ts` holds one preview pane in place of three | The three old routes answer 308 to `/studio`. `nav.test.ts` moves its preview count in the same PR. The library shows each kind with its owner and state. Visual review in four contexts | STD | AGENT-SAFE |
| **A2** | 🔨 **BUILT (2026-11-01).** Enum tokens are camelCase (`longText`, `multiSelect`, `mindMap`, `processFlow`), as the genUI node types are, and keys stay snake_case. Caps: depth 20, a string of 20,000 characters, a list of 500 items, a document of 256 KiB. The validator never raises on any JSON value. Credential shapes come from one list, `acb_common/secret_shapes.py` (J5 points the older copies at it). The package manifest schema v1 and its one validator: `packages/acb_common/acb_common/studio_manifest.py` (pydantic, pure data, no gateway import), the committed export `studio_manifest.schema.json`, and hand-written TypeScript types in `workbench/control_plane/src/lib/studio/manifest.ts` held equal by a parity test (no code generator, as the genUI lockstep does). `validate_manifest(doc, *, catalog=None)` takes the catalog as an input: a map of action id to class. With no catalog it checks structure only. **The `uses` grammar:** `object:<name>.(list|get|create|update|delete)`, `<app>.<op>`, `integration:<action>` or `workflow:<uuid>` (B1's ids). An `object:` action is checked against the package's own objects. **Not yet in v1:** `rules`, a state machine (C6), a formula or roll-up (C7) and a code block (L1) are reserved keys, and each refusal names the slice that lifts it. A logic module (B4) needs no key: it is a catalog action | Each refusal of §3.7 has a test that fails with a JSON path and one sentence: an unknown block, an action the catalog does not serve, a class X action, a colour, a pixel size, a credential-shaped string, a field that a view names and the object lacks, an undeclared submit, skill action or card, a bad relation, a duplicate id, a select with no options. The §3.6 example validates with no catalog and with a fake one. The committed JSON Schema equals the live export. `test_studio_manifest_ts_parity.py` holds the TypeScript types equal | STD | AGENT-SAFE |
| **A3** | The package store: `apps` gains a nullable `format` (`code` or `manifest`) and `manifest jsonb`. No second app table | An expand-only migration (R6). R8: org B reads none of A's packages. `test_tenant_coverage.py` passes | HIGH | AGENT-SAFE |
| **A4** | Publish, version, roll back and install, over `app_versions` | A rollback restores the older manifest in one call. An open workflow run keeps its version | STD | AGENT-SAFE |

### Group B — The action catalog

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **B1** | 🔨 **BUILT, dark (2026-10-31).** Source 2 is empty until a workflow tool registers with an integration, and a tripwire test says so. A manifest row takes its input schema from `agent_apps.args_schema` (AD-9). A workflow row has a null input schema. `GET /studio/actions`: the read-only catalog of §3.2 sources 1, 2 and 4. **Row:** `id`, `title`, `source` (`manifest`, `integration` or `workflow`), `class`, `input_schema` (where the source declares one, else null), `output_schema` (always null: no source declares one yet), `connection`, `app`. **Source 1:** each door op of `every_app()` through `door_ops()`. **Source 2:** each entry of `list_tools()` with an `integration`, read with no live probe and no availability. **Source 4:** the org's workflows with `status = 'published'`, id `workflow:<uuid>`, class B. **Class map:** a manifest row keeps `route.cls`. An integration tool with `read_only` is A, one with `destructive` or `open_world` is C, and every other is B. No row is X. **Route:** a new `gateway/routes/studio/` router, prefix `/studio`, gated by `require_feature_router("workflows")` until J1, mounted beside the workflows router in `main.py`. **Tenant:** `tenant_session` from `gateway/db.py`, the org from `UserContext`, never from input (R5). **Flag:** `STUDIO_ORGS` (`*` for all, default off) in `acb_common/settings.py`, read per call as `push/flags.py` does, and `STUDIO_` in `env_guard.py`. No invoke, no MCP door | `test_studio_catalog.py`: a not-listed org gets 404, a listed org and `*` get 200. The row count is the door ops plus the integration tools plus the published workflows, with no duplicate id. A draft or disabled workflow never appears. The class map holds row by row. No row is X. `test_studio_catalog_r8.py` (`pytestmark = _DB_GATE`): org B's published workflow never appears for org A, red first with the RLS-bound session removed | STD | AGENT-SAFE |
| **B1c** | Source 5: the package's own object actions and named actions | Each object of a stored manifest gives its five actions, and each named action appears once. Needs A2, A3 and C1 | STD | AGENT-SAFE |
| **B1b** | Source 3: the tools of the organization's MCP servers, read from a cached `tools/list` | A tool with `readOnlyHint` is class A, and every other tool is B. A server of another org never appears | HIGH | AGENT-SAFE |
| **B2** | The invoke door `POST /studio/apps/{id}/actions/{action}`. Class A runs as the viewer. Class B and C go through `request_confirmation` in chat, or `pending_actions` outside it. Class X and undeclared actions are refused | Each class has an R8 test, red first under a named mutation. An action that the package does not declare gets 403. A viewer without the right in the owning app gets that app's own refusal | HIGH | AGENT-SAFE |
| **B3** | A published app's actions join the D136 door: `list_apps` and `describe_app` show it, and `app_read` and `app_write` reach it | Needs MCP-P1b. The MCP coverage fences pass. A package that is not shared with the caller does not appear | HIGH | AGENT-SAFE. After MCP-P1b |
| **B4** | **First-party logic modules** (§3.7 layer 3): a typed function in the repository, served by the catalog as an action. It reads its input through B2, as the caller, and returns proposed writes. B2 applies them in one transaction | A module that returns writes to two objects applies both or neither. A proposed class B or C write waits for the member's card. A module that names an object the package does not hold is refused. The first module is the MRP run of K4 | HIGH | AGENT-SAFE |
| **B5** | **Member-authored logic modules** on the off-box sandbox, with the same contract as B4 | The module runs with no network, no database link and no secret. A share to the organization needs an approval of one version (D130.3) | HIGH | AGENT-SAFE. After SBX-9 (D127.2) |

### Group C — Objects

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **C1** | **The typed object store** (D146.4, §3.1): an object registry, record tables on shared JSONB rows, the field types of §3.1, validation on each write, and an index budget per org. No per-tenant DDL. Expand-only migration, next free number (R1) | Each field type refuses a bad value with its field name. R8: FORCE RLS holds on each table, and org B reads nothing. A hot field gets an index, and no org passes its budget. `test_tenant_coverage.py` passes | HIGH | AGENT-SAFE |
| **C1b** | Multi-record writes and constraints: one action writes several records in one transaction, and a constraint runs on each write | A stock move writes two ledger rows or neither, also when the second write fails. A constraint "stock is never below zero" refuses the write that breaks it, with its name. R8 under two writers at once | HIGH | AGENT-SAFE |
| **C2** | The object query: filter, sort, group, count, sum and average, paging and a cost guard | R8 on a real Postgres for each operator. A query over the guard stops with a clear error. An index plan is checked with EXPLAIN on 50,000 rows | HIGH | AGENT-SAFE |
| **C3** | Record events: create, update and delete emit `studio.record.*` through `ingestion/event_hooks.py` | A workflow with a record trigger runs once for one change. The event carries the org | STD | AGENT-SAFE |
| **C4** | Record history: each change names the actor and the old value | The history of one record lists each change in order | STD | AGENT-SAFE |
| **C5** | Records join `search_company` through the one retrieval seam (D128) | A new record is found by Mira within five minutes. A record the viewer cannot open is never returned | HIGH | AGENT-SAFE |
| **C6** | **State machines** (§3.7 layer 2): a status field names its allowed moves, and each move may have a guard rule and a required role | A move that the machine does not allow is refused. A guarded move ("a PO above the limit needs a director") is refused for any other role. Each move writes a history row (C4) | STD | AGENT-SAFE |
| **C7** | **Formula and roll-up fields** in an interpreted expression language with no I/O, no loops and a step cap | A roll-up (stock on hand is the sum of the moves) updates within one minute of a write. A formula over the step cap stops with a clear error. An expression can read only the fields of its own package | STD | AGENT-SAFE |

### Group D — Blocks and the renderer

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **D1** | Data binding, page state and typed actions in `GenerativeUINode` (§5.2) | A node with `source` renders rows from a mocked door, with the default loading, empty and error states. A button sends its action id. The lockstep test passes. **Built 2026-11-01 (STU-D1):** `table` and `dataGrid` bind, through `lib/studio/dataDoor.tsx`. **Narrowed:** page state is a pure encode and decode helper (`lib/studio/pageState.ts`), and the URL wiring waits for D11, since no Studio page mounts a bound node yet. Other blocks bind in D2 to D5 | STD | AGENT-SAFE |
| **D2** | The Table, Form and Detail blocks, from the §3.1 field types | Each field type renders and validates in the form. Inline edit in the table calls the update action. Visual review in four contexts | STD | AGENT-SAFE |
| **D3** | The Board block on `components/board/` | A drag between columns calls the update action once. A refused update puts the card back | STD | AGENT-SAFE |
| **D4** | The Chart and Stat blocks on `lib/charts/kinds.mjs`, fed by an aggregate query | Each of the 13 kinds renders from a C2 result | LOW | AGENT-SAFE |
| **D5** | Pull the Calendar grids out to `components/calendar/` with a generic event shape | The Calendar app's tests pass with no change. The block renders object records by a date field | STD | AGENT-SAFE |
| **D6** | Pull the Timeline core out of `TimelineView.tsx` to `components/timeline/` | The Projects tests pass with no change. The block renders records with a start and an end | STD | AGENT-SAFE |
| **D7** | The Mind map block, on `@xyflow/react` with a tree layout | A parent field draws the tree. A drag to a new parent calls the update action | STD | AGENT-SAFE |
| **D8** | The Process flow block: a read-only workflow graph with run state | A running workflow's nodes show their state from the run console stream | STD | AGENT-SAFE |
| **D9** | The Checklist block: playbook steps, each human step a task | Ticking a step completes its task in the one store | STD | AGENT-SAFE |
| **D10** | The Map block | Records with a geo field show as pins. Visual review at 390 px | STD | **OWNER-GATE** for a paid tile key (STU-Q8). Otherwise AGENT-SAFE |
| **D11** | The page layout: grid, tabs, side panel and the phone layout. Wire `pageState` to the URL | Each block passes visual review at four widths down to 390 px. A round-trip test shows `pageState` survives a reload through the URL | STD | AGENT-SAFE |
| **D12** | The manifest conformance fence | A manifest with a colour, a pixel size or an unknown block fails to publish | LOW | AGENT-SAFE |

### Group E — Workflows in Studio

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **E1** | The workflow editor at `/studio/workflows/{id}`, the same engine and components | Every `/workflows` test passes on the new route | LOW | AGENT-SAFE |
| **E2** | Fan-in and loops (`workflows_app.md` §8.3b and §8.3c, unchanged) | The acceptance of §8.3b and §8.3c | STD | AGENT-SAFE |
| **E3** | Studio triggers: record, form submit, button, skill | Each trigger starts one run, with the org bound | STD | AGENT-SAFE. Unattended triggers wait on H-328 |
| **E4** | The human step node: a task in `pm_tasks`, and a wait until it is done | The run resumes once when the task moves to done. A cancelled task fails the step | STD | AGENT-SAFE |
| **E5** | Catalog nodes: any B1 action is a node | A class B node pauses for approval, as the approval node does | HIGH | AGENT-SAFE |
| **E6** | The workflow template gallery (`workflows_app.md` §8.3a) | §8.3a's acceptance | LOW | AGENT-SAFE |

### Group F — Skills

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **F1** | The skill store: `studio_skills` and `studio_skill_versions` (immutable), tenant-scoped, private by default (§3.5). One expand-only migration in the shape of `315_brief_runs.sql`, with its own ENABLE, FORCE and policy, and a composite key so a version cannot point at another org's skill. Regenerate `infra/postgres/generated/` with `scripts/gen_tenant_migration.py`. A store module `gateway/studio/skills_store.py`, beside `gateway/brief/store.py`. **No route:** F2 owns the routes, and J1 owns the feature gate. The member purge (`routes/admin/members.py`) removes the member's skills | R8: org B reads none of org A's skills or versions, red first with FORCE removed. A skill is private by default. A stored version cannot be updated. Two `add_version` calls at once give two numbers or one clean refusal. `test_tenant_coverage.py` passes with a new case. `test_admin_member_purge.py` passes, and the purge removes the member's skills of this org only | HIGH | AGENT-SAFE |
| **F2** | The skill creator in Studio: describe, draft, edit, publish (§7) | A member drafts, edits and publishes a private skill. Visual review in four contexts | STD | AGENT-SAFE |
| **F3** | Published skills load as org tool packs in `tool_packs.choose` | A turn that matches a skill loads its actions and its rules. A skill of another member that is not shared never loads | HIGH | AGENT-SAFE. After WS-67 is live |
| **F4** | Skill tests on a dry-run door | A test fails when the turn calls another action. No write leaves the dry-run door. A test run stops at its credit cap | STD | AGENT-SAFE |
| **F5** | Skill cards: a skill renders a package view in chat | The card shows live rows, and its button reaches the member's approval card | STD | AGENT-SAFE |
| **F6** | SKL-1: after a multi-step task, "Save this as a skill?" or "Do this every week?" (task #818) | One accepted offer drafts a skill or a workflow from the chat's tool calls | STD | AGENT-SAFE |
| **F7** | SKL-2: history, one-step undo and archive when unused (task #819) | Each change shows who made it. Undo restores the version before | STD | AGENT-SAFE |
| **F8** | `SKILL.md` export and import, and skills over the MCP | An exported skill loads in a stock Agent Skills client. An import keeps only catalog actions | STD | AGENT-SAFE |
| **F9** | Retire the Agent Workshop and `AddAgentWizard` | The route and the wizard are gone. No test imports them | LOW | AGENT-SAFE |

### Group G — The builder

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **G1** | The builder tools for Mira: draft, patch, validate, preview and list actions. Agent `studio-builder` | Each tool refuses an invalid patch with the validator's error. No tool runs code | STD | AGENT-SAFE |
| **G2** | The interview and the plan card (§6.2 steps 1 and 2) | A request makes at most five questions and one plan card. A stop on the card writes nothing | STD | AGENT-SAFE |
| **G3** | The Studio build screen (§6.3) | Outline, preview and the shell's dock with `studio-builder`. `seams.test.ts` passes | STD | AGENT-SAFE |
| **G4** | Preview on sample data, with a preview store and simulated sends | A preview write never reaches `app_data` of the live package. A send in preview leaves no message | HIGH | AGENT-SAFE |
| **G5** | Edit by diff, with one-step undo | Each change shows its diff before it applies | STD | AGENT-SAFE |
| **G6** | The build eval: 30 prompts from §2.1 | The pass rate is reported per shape. The run cost is set before the run | STD | **OWNER-GATE** for a run over USD 0.50 |
| **G7** | The builder on the CA-14 harness profile `app_builder`, for the code block | Needs CA-14 (#1042) | HIGH | AGENT-SAFE. After CA-14 |

### Group H — The shell

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **H1** | Installed apps in the sidebar, per org | An installed app shows for a member who may open it, and for no one else. `nav.test.ts` still counts only the built-in live panes | STD | AGENT-SAFE |
| **H2** | App jobs, needs, Home cards and search from the manifest into the shell seams | A job of an installed app shows in ⌘K "Do". A need shows in the bell | STD | AGENT-SAFE |
| **H3** | Phone and WhatsApp: a skill's cards over WhatsApp | A skill's write on WhatsApp still asks once | STD | AGENT-SAFE |

### Group I — Connectors

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **I1** | A package names its connections. Install checks them, and Integrations lists the apps that use each one | An app with a missing connection installs in the "needs a connection" state and runs no action | STD | AGENT-SAFE |
| **I2** | Generate an app from a connection or an MCP server (§8.2) | A test MCP server with two reads and one write gives two lists, two details and one form with an approval | STD | AGENT-SAFE |

### Group J — Governance

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **J1** | `feature:studio` and `studio:publish` through the one entitlement seam | A member without `feature:studio` gets 403 on each Studio route | HIGH | AGENT-SAFE. Waits on STU-Q4 |
| **J2** | Share: private → people or a group → the organization, with an approval for the organization | A share to the organization without approval stays pending. Approval publishes one version only | HIGH | AGENT-SAFE |
| **J3** | The kill switch, the credit caps and a `usage_event` row for each model call | A paused package runs no new action within 10 seconds. A build stops at its cap | HIGH | AGENT-SAFE |
| **J4** | The admin view: every package, owner, version, runs, last error and use. It joins AUTO-4 (task #817) | An admin sees every package of the org. A member sees only their own | STD | AGENT-SAFE |

### Groups K, L and M — Templates, code, pinned answers

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **K1** | App templates in `app_templates`: U7, U8, U15, U16, U19 and U9 first | Each template installs in one step and passes D12 | LOW | AGENT-SAFE |
| **K2** | A template update to an installed app that a member changed | The update keeps each local change, or names the conflict | HIGH | AGENT-SAFE |
| **K3** | **First-party template: inventory and procurement** (D146.6, §3.7 example). Items with product lines and units, locations, the stock ledger, vendors, purchase requests and purchase orders with an approval chain by amount, and goods receipt with a QC step. It posts to Tally or Zoho Books through a connector | It installs in one step and passes D12. A receipt writes stock moves that keep stock on hand right. A purchase order above the limit waits for a director | STD | AGENT-SAFE |
| **K4** | **First-party template: MRP-lite and the production schedule** (D146.6). BOMs, work orders, machines, the nightly MRP run (a B4 module) and a production timeline by machine | On a fixed test set, the MRP run proposes the expected planned orders. Each planned order waits for approval. The timeline shows each work order on its machine | HIGH | AGENT-SAFE |
| **L1** | The code block (§5.4) | Code runs with the `--cc-*` tokens only, and every bridge call goes through B2 | HIGH | AGENT-SAFE. After SBX-9 and CA-14 |
| **L2** | Move existing App Workshop apps into packages with `format = code` | Each existing app opens and runs from Studio | STD | AGENT-SAFE |
| **M1** | Pin a chat card or an artifact as a draft package page, with a 30-day expiry | A pinned page opens by link after the chat ends. It expires on its date, and its owner can restore it | STD | AGENT-SAFE |

---

## 15. Stages and dependencies

| Stage | Slices | What a member gets | Depends on |
|---|---|---|---|
| **1 · Studio opens** (M1, dark) | A1, A2, B1, D1, E1, F1, F2, G1 | One Studio pane in preview, the skill creator for private skills, the catalog, and the manifest | None. D146 answered STU-Q1 and Q2 |
| **2 · First apps** (M2) | A3, A4, B1c, B2, C1, C1b, C2, C6, C7, D2, D3, D4, D11, D12, F5, G2, G3, G4, G5, H1, J1, J2, K1, K3 | Build, preview and publish an S2 app in chat. Tables, forms, boards and charts. The typed object store with rules. The inventory and procurement template | J1 waits on STU-Q4. F3 needs WS-67 live |
| **3 · Automations and rich blocks** | B1b, B4, C3, C4, D5 to D9, E2 to E6, F3, F4, F6, F7, H2, I1, I2, J3, J4, K4 | S1 and S3 in full, connector apps, calendar, timeline, mind map, playbooks, first-party logic and the MRP-lite template | H-328 for unattended runs. WS-67 for F3 |
| **4 · Code, outside agents, scale** | B3, B5, C5, D10, F8, F9, G6, G7, H3, K2, L1, L2, M1 | Member logic modules, the code block, MCP access, pinned answers | MCP-P1b, SBX-9, CA-14 (#1042), MCP-P5 (#869) |

**The first dispatchable slice is B1.** It waits on no owner question, writes nothing, and every later group reads it.

---

## 16. Current file paths

Each path was read on `57b91f721`.

**Backend**

- `apps/services/gateway/gateway/routes/workflows/` — `catalog.py` (`_integration_entries`, `_tool_entry`), `tools.py` (`WorkflowToolSpec`, `list_tools`), `core.py` (the router pattern), `search.py`, `triggers.py`, `scheduler.py`, `copilot.py`, `engine/graph.py`, `engine/runner.py`, `engine/handlers.py`, `engine/modules.py`
- `apps/services/gateway/gateway/db.py` (`tenant_session`), `gateway/main.py` (the router mounts), `gateway/push/flags.py` (an `*_ORGS` reader)
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

---

## 17. Verification commands

Each slice names its own test files. These commands bind every Studio PR:

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"   # R8 needs a database
uv run python scripts/affected_tests.py --run
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_genui_catalog_lockstep.py tests/unit/test_workflows_engine.py -q
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
| A package reaches data only through a declared catalog action | New with B2: `tests/unit/test_studio_action_door_r8.py` |
| A catalog row never carries class X | New with B1: `tests/unit/test_studio_catalog.py` |
