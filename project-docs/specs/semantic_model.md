# The semantic model: one way for the chat to read any large data source

**Status: PROPOSED (2026-11-17, owner ask). Nothing is built.** The owner
must answer D-SEM-1 first (`building_blocks.md` §6.1 step 4). No code slice of
rung 3 starts before that answer. The owner chose D-SEM-3 = Option A on
2026-11-18. Verified against `origin/main` at `a4cfeedc7`
(2026-11-17).

**Names.** The workstream is **WS-SEM**, and its slice prefix is **SEM-n**.
Neither name occurred on `main` before this spec (R2). This spec takes no
D-number. The D-SEM-n questions of §12 wait for the owner.

**Inputs.** A research report on semantic layers, and the ten binding rules of
the WS-CSY planner, which §11 maps to this design. The live evidence is counts
only, with no record value. The working files stay in the coordinator's lane
folder, and no customer data enters this repo.

**Owner ask, 2026-11-17 (paraphrased by the coordinator).** The product can
hold large structured data, or a member can connect an outside system with many
tables. Then the chat must query it with the state-of-the-art method. This must work
with no internal app.

At connect time the AI learns what the source holds and writes a working
brief. As members correct the chat, the harness improves. If a better system
exists, adopt it.

---

## 0. The answer in one screen

**One block: the semantic model of a source.** One model for each org and each
source. A source is a Metorite app (the CRM first) or one connection (an MCP
server, Zoho Books, or Tally through a local link). The model says what each
entity is and what each field means. It names the valid measures and joins,
the words of the org, and the questions that have a verified query.

**It is mostly built for the CRM, and dark.** Five built parts form a semantic
model for one app:

- the catalogue (CRM-Q31) and the mapping plan
- the open questions and proposals (CRM-Q32)
- the business rules (CRM-Q33)
- the terms and value spellings (§18.16)
- the `org_facts` read seam

This design makes that shape general. It adds five things: one query grammar,
server-side linking of names, and verified queries. It also adds a store for
the model of a connection, and a counter of refusals for the one failure store.

**Each turn.** Link the member's words to entities and fields on the server,
and resolve names to values as the member. Send one typed query, or the
source's own read tool. Validate it and run it. Answer with the numbers of the
query, and say how the chat read the data. Repair a refused query once.

**Learning.** A member's correction becomes a proposal. An admin accepts it,
and the next chat uses the new version. A gap in the platform goes to the one
failure store (TOF-2a), and a pull request fixes it for every org. Nothing
learned in one org reaches another org.

**Adopt or build.** Adopt the pattern of Snowflake, Databricks, dbt and
Looker. Do not adopt a vendor engine. Keep the stored shape close to Apache
Ossie (formerly Open Semantic Interchange) for a later export.

**The ladder.** Rung 0: read the CRM flags of the live org. Rung 1: fix the
two refusals, link names on the server, match values, and repair, on the live
CRM. Rung 2: one grammar, proposals and verified queries on the CRM. Rung 3: a
connection that is profiled at
connect time, Tally through the local link as the first test. Rungs 4 to 6 in
one line each.

---

## 1. The evidence (production, 2026-11-17, counts only)

One org with a Zoho mirror of about 17 custom modules. One member, one
thread of CRM questions, 33 runs (the coordinator's evidence file). The request log
(`crm_requests.txt`) gives these counts:

| What the chat called | Count |
|---|---|
| `GET /crm/aggregate` | 53 |
| `GET /crm/fields/...` | 14 |
| `GET /crm/modules...` | 14 |
| `GET /crm/catalogue` | 2 |
| An aggregate with `term=` (the built path of §18.16.4) | 0 |
| `GET /crm/terms/resolve`, `GET /crm/questions...` (also 0 in the gateway log of this folder) | 0 |
| The `org_facts` tool (`tool=org_facts` in the gateway log) | 2 |
| An aggregate that used the key word `@amount` | 3 |
| An aggregate that split a total over named fields (`fields=`) | 1 |

**The refusal classes** (the coordinator's evidence file, "The 10 refused CRM calls"):

1. A name as `q` on a module total: 422. A module aggregate takes no `q`
   (`routes/crm/aggregate.py` `MODULE_PARAMS`).
2. A number range (`field.<number>.from`): 422. A range takes a date field
   only (`routes/crm/field_sql.py` `_filter_value`).
3. A filter on a field of another module: 422.
4. A guessed field name: 422.

**One more pattern.** For one customer, the chat summed 6 different money
fields, one call each, to find the one that the member meant.

**What it shows.**

- The knowledge exists, but the model did not take the path. The term
  resolver got 0 calls, and the `org_facts` tool got 2 in 33 runs. A design
  that waits for the model to pick the right tool repeats the fault
  (`chat_connected_systems.md` §8.2, "If the model still does not route").
- The grammar refuses common needs: a name on a module total, a number range.
- The model guesses field names, because nothing on the server links a word to
  a field before it refuses.

**The SEM-R0 read of production flags (2026-11-17 18:22 UTC).**

`CRM_ANSWER_FIRST_ORGS`, `CRM_CATALOGUE_ORGS`, `CRM_DECIDE_ORGS`,
`CRM_QUESTIONS_ORGS`, `CRM_FILL_RANK_ORGS` and `CRM_MIRROR_ORGS` are all `*`.
`CRM_ANSWER_CHECK` is `shadow`. `CRM_LONG_READ_ORGS` holds one org.

So the meaning layer is on, and the model does not use it.

---

## 2. What we have, against the state of the art

V = built on `main`. Dark = built behind a flag that is off. P = planned.

| # | Capability | State of the art | Metorite today | Gap |
|---|---|---|---|---|
| 1 | A semantic model per source | Semantic views, Genie spaces, LookML, MetricFlow | CRM: the catalogue (`routes/crm/catalogue.py`, dark `CRM_CATALOGUE_ORGS`) and the mapping plan (`crm_mapping_plans`). A connection: the server brief (P, CSY-1) | No model for a connection. No general shape |
| 2 | Meaning of each field | "Descriptions are the single most important element" | Purpose and four key parts for the money modules (`crm_platform.md` §18.13.4). Field labels and types | No meaning, unit or tax mark for a non-key field. The tax mark exists for money options only (§18.15.5) |
| 3 | Named measures | Metrics in every vendor | The catalogue amount, a sum of 2 to 5 fields (CRM-Q32-9), the org meaning of revenue (`@org`) | A measure with fixed filters ("billed, not draft") is not stored. The model finds fields by trial |
| 4 | Joins | Declared relationships only | Each lookup names its target (`options.lookup_module`). The overlay of a core slot (§18.13.5) | A total reads one module. No filter on a property of a linked record |
| 5 | Synonyms | Few, by hand or by evidence | The term resolver with the confidence rule (§18.16.4). Value aliases (§18.16.6) | Used only when the model sends `term`: 0 of 53 calls. No synonym for an entity |
| 6 | Value lookup for names | Cortex Search, Genie entity matching, CHESS value retrieval | `field_sql.text_matches` and `terms.value_matches` (§18.16.6). `resolve_name` for core kinds (FIND-5, dark `FIND_ORGS`) | A module total takes no name (`q`). `resolve_name` reads no module field |
| 7 | Sample values | For low-cardinality dimensions | `crm_list_fields` shows every picklist option (V). Masked samples for an admin (CRM-Q32) | Small. The options are one tool call away, not in a brief. A connection has none |
| 8 | Verified queries | Snowflake VQR, Genie trusted assets, Power BI verified answers | None. The digest runs fixed queries in code (CRM-Q28) | All of it |
| 9 | Instructions | Few and focused | `crm_business_rules` (migration 283), the CRM facts block, the period and filter lines (§18.16) | None for a connection |
| 10 | One query grammar | One typed query | Two: the CRM list and aggregate parameters (`records.ListParams`, `field.<key>`), and `acb_common/studio_query.py` for Studio objects. A third is proposed (the CRM UI research draft (§4.1, not on `main`)) | Three spellings of a filter |
| 11 | Validate and repair | Run, read the error, retry a fixed number of times | Typed 422s (`field_sql.py`). The refusal seam `acb_common/tool_refusals.py`. A read budget (§18.16.3) | A refusal does not list the legal fields. No count of refusals. No learning from them |
| 12 | Numbers from a query | The server computes each figure | `acb_common/number_check.py` and `acb_skills/answer_check.py` arm on a CRM tool (V) | An MCP result does not arm the check (P 3141) |
| 13 | Connect-time profiling | Snowflake Autopilot | `crm_sync/map_pipeline.py`: shape at 0 tokens, then the decide tier, with caps (V for Zoho) | None for an MCP server or a ledger |
| 14 | A review queue | Snowflake suggestions, Genie feedback, Spotter coaching levels | CRM-Q32 questions and member proposals (dark). One "Needs you" (`routes/shell/waiting.py`) | No kind for a measure, a join or a verified query. None for a connection |
| 15 | Benchmarks | Genie benchmarks, about 10 questions in Snowflake | The CRM replay (§18.16.8). The OA-8e harness for MCP (P, CSY-3) | No fixed question set per source |
| 16 | Suggestions from use | Snowflake verified query suggestions | Number memory (P, NUM-7, `answer_figures`) | All of it |
| 17 | What the chat knows of a server | Server instructions, tool texts | The admin's description (CSY-7, built). The brief and the server's instructions (P, CSY-1 and CSY-6) | No learned shape. MCP 2026-07-28 moves `instructions` to `server/discover` |
| 18 | Books of account | Accounting connectors | No spec. `ai_studio.md` non-goal 8: no general ledger. D122.1: Zoho Books, then Tally Prime, then QuickBooks. LNK-0 (PR 2550, open) plans a local link to Tally | All of it |

**The headline.** The CRM has most of a semantic layer, but it is dark and
the model does not use it. A connection has none. The query grammar has three
spellings. Verified queries and learning from use do not exist.

---

## 3. The block

### 3.1 The name, and where it sits

- **The name is "semantic model".** It is the industry's Technical Name.
  "Source profile" is taken: `crm_source_profiles` holds the raw Zoho
  discovery (CRM-Q1, `connections/store.py`). "Mapping plan" is the CRM's
  store, and stays its name.
- **The data brief** is the short, capped text that the chat reads from a
  model. For a connection it is one input of `server_brief` (rule 1).
- **The block.** This design proposes one new row of `building_blocks.md` §2,
  "Data meaning". Its one seam is the `org_facts` read seam
  (`gateway/org_facts.py`) with one provider for each source. The owner
  decides it (D-SEM-1), because `building_blocks.md` §6.1 step 4 says to ask
  before a new block.

### 3.2 The parts of a model

| Part | What it holds | CRM store (exists) | Connection store (new) | Who writes |
|---|---|---|---|---|
| Entity | A record kind: purpose, record count, freshness | Catalogue entry | Model body | Code, then the decide tier, then the admin |
| Field | Label, type, meaning, unit, tax mark, fill share | `crm_field_defs`, `crm_field_fill`, catalogue parts | Model body | Code, then the decide tier |
| Tax mark | Includes tax, excludes tax, after TDS, or the tax alone | `gst_check` evidence (§18.15.5) | Model body | Code evidence, then the admin |
| Measure | A name, an aggregate over fields, fixed filters, a unit | Plan item, new kind `measure` | Model body | The admin, from a proposal |
| Dimension | A field to group by, its kind and its default time grain | Catalogue `date`, `status`, field types | Model body | Code |
| Join | A field that points to another entity | `options.lookup_module` (code). Plan item, new kind `join`, for an admin's join | Model body | Code, or the admin. Never inferred in silence |
| Synonym | The org's word for an entity, a field or a measure | `zoho:questions` terms (§18.16.4) | Model body | The admin, or a member's pick as a proposal |
| Options | The allowed values of a picklist | `crm_field_defs.options` | Model body | Code. These are schema, not data |
| Value lookup | Which fields hold names, and how to match them | Code: lookup, text and select fields | Model body: the source's own search tool | Code. No value is stored (§3.3) |
| Sample question | A question that a member can start from | New, plan item kind `query` with an onboarding mark | Model body | The admin |
| Verified query | A question with each literal replaced by a named parameter, a query with the same parameters, the verifier's role, the time, the model version. No total | Plan item, new kind `query` | Model body | The admin |
| Instruction | A short rule, for the org or for one member | `crm_business_rules` | Model body (org scope only) | The admin. A member for themselves |
| Source notes | The admin's description and the server's own instructions | n/a | `integration_connections` (CSY-7) and the brief inputs | The admin, and the server. Never the AI (rule 4) |

### 3.3 Shape only (rule 6), and the value question

The owner's ask names value indexes, sample values and verified queries. Rule 6
allows shape only: tables, fields, units, counts and date ranges, and no
balance, personal data or free-text value. This design meets both:

1. **Store schema, never data.** A picklist option is schema, so the model
   keeps it. A ledger name, a customer name or a record title is data, so the
   model never keeps it. In Tally a ledger name is often a customer name.
2. **Resolve a name at query time, as the member.** The server matches the
   member's words against the values that the member can see. It reads in the
   bound tenant session. It uses `field_sql.text_matches`,
   `terms.value_matches` and the floor of `acb_common/name_match.py`. For a
   connection, the source's own search tool does it. Nothing is cached.
3. **A verified query holds parameters, never literals.** "Billed amount for
   `{customer}` in `{month}`" is a verified query. "Billed amount for <a
   name>" is not.
4. **A stored value index is an owner decision (D-SEM-5).** Genie refuses
   entity matching on a table with row filters, for the same reason. A stored
   list of values can show a value to a member who cannot see its record.
5. **No figure is stored.** A total is a balance. A verified query and a
   measure keep no total. When an admin compares two choices or two versions,
   the server computes both totals at that time and shows them. It stores
   neither.
6. **Two kinds of part, and one validator.** A pure `shape_only(model)` runs
   before each write, with one rule for each kind:

   | Kind of part | Examples | What `shape_only` allows |
   |---|---|---|
   | Derived from the source's data | Entity, field, tax mark, dimension, join, options, counts, the date range | Keys, labels, options, fixed words, counts, shares and dates. Never other free text, and never an amount |
   | Written by a person and accepted by an admin | An instruction, a synonym, the words of a verified question, a measure name | Short text (the cap of `crm_business_rules`), with no parameter value. A member's words become this kind only after an admin accepts them |
   | Written by the AI | The description of an entity | Text that code builds from closed choices (§4.2 step 4). Never model free text |

### 3.4 Where a model is stored

- **The CRM: no new store.** The plan gains three item kinds: `measure`,
  `join` and `query`. A snapshot already keeps an item of an unknown kind
  (`mapping_plans.split_rules`), and `check_kinds` and `merge_kinds` learn the
  three kinds. Terms and aliases stay in `zoho:questions`, and rules stay in
  `crm_business_rules`. So a model version is a plan version, with its lock and
  its Undo.
- **A connection: one new table** (D-SEM-2). The working name is
  `connection_semantic_models`:
  - `organization_id`, FORCE row level security, the tenant default.
  - `connection_id`, a foreign key to `integration_connections(id)` with
    `ON DELETE CASCADE`. Rule 7 keys a model by the connection id, never by
    its name.
  - `version`, `state` (`draft`, `approved`, `archived`), `body` (jsonb, the
    parts of §3.2), `shape_hash` (the hash of the tool list and of the
    discovered shape), `made_by` (`code`, `ai` or `admin`), `approved_role`,
    `approved_at`, and `untrusted_origin` (true for a local link, rule 10).
  - Unique on `(organization_id, connection_id, version)`. Expand only (R6).
    The migration number is taken at build time and checked at merge (R1).
  - One module, `connections/semantic_model.py`, holds every statement on
    the table (a one-writer fence, as `connections/attention.py` does).
- **Why not `provider_meta`.** It has no version, no draft and no approval,
  and many paths read it.
- **Why not a Studio skill.** A skill belongs to a member, starts private, and
  a holder of `studio:publish` approves its share. A model is the org's
  meaning of a connection, and the admin of the connection approves it
  (rule 5).
- **One read seam.** `org_facts` gets one more provider module for
  connections (`PROVIDER_MODULES`). The CRM provider adds the subjects
  `measure:`, `join:` and `query:`. Every reader, the chat included, reads a
  model through `org_facts.read`.

### 3.5 The format

The body keeps the names of Apache Ossie 0.1.x where they fit: datasets,
fields, relationships, metrics and AI context. An export to that format is a
later slice (D-SEM-10). No code depends on it now.

---

## 4. How a model is built

### 4.1 The CRM (built, extended)

The sync runs `crm_sync/map_pipeline.py`. It reads the shape at 0 tokens and
applies the code rules. Then it asks `decide_features.ask` once for each open
part, and once more with masked samples when unsure. The admin approves the
draft. Its caps
(`TOKEN_CAP`, `RUN_REQUESTS`, `DAILY_REQUESTS`) and its schema hash stay.

This design adds two code steps, at 0 tokens:

- **Joins from lookups.** Each lookup becomes a `join` fact with the source
  `code`.
- **Measure proposals.** Each money entry with an amount and a status gives
  one proposed measure, for example "the amount of each record that is not a
  draft". It waits for the admin.

### 4.2 A connection (new)

| Step | What happens | Model calls |
|---|---|---|
| 0. Start | A connect, a changed tool list (B1b-3, task 3280, or a list change notice), a weekly refresh, or the admin's "Read it again" | none |
| 1. Discover | Read the tool names, input and output schemas, and annotations. Read the server's instructions only if the owner says yes to D-CSY-3. In MCP 2026-07-28 they come from `server/discover`, not from `initialize` | none |
| 2. Shape reads | Call only the tools that the catalog classes as A (`ai_studio.md` §3.2 source 3, B1b-1). Call a tool with no required argument, or fill an argument only with a key that an earlier shape read returned, never with a value. A known connector (Tally, SEM-17) uses a fixed list of reads instead. At most 20 calls, 30 seconds and 64 KB per result. Keep only keys, types, counts, and the first and last date. Each result is untrusted data | none |
| 3. Decide | One `decide_features.ask` for each entity with an open part: its purpose, its amount, date, status and customer fields, the unit and the tax mark. Choices come from closed lists. The state names fields as objects, never values | decide tier only |
| 4. Write the draft | Code writes each description from the chosen answers, never from model free text. An example: "Holds invoices. The amount is <label>." `shape_only` checks the draft | none |
| 5. Approve | The admin sees the draft on the connection's panel as a diff, and accepts or rejects each part. A "Needs you" item points there | none |

**The rules of the profiler.**

- **No member, so reads only (rule 8).** The profiler runs with no member. A
  run with no member refuses a write (MCP-NC1). So it never calls a tool of
  class B or C. A server can have one data tool only, which says it is
  destructive. `chat_connected_systems.md` §1.1 shows one. Such a server gets
  a model of its tool shapes only.
- **Whose account.** The profile reads through the connection's own
  credential. An OAuth connection acts as the admin who connected it (D98).
- **Who pays.** The `connected_by` member of the connection, as
  `map_pipeline` does for Zoho.
- **The same caps** as `map_pipeline`, under the feature
  `connection.map_model`, with its own day counter.
- **A changed shape.** A new `shape_hash` starts a new draft. The approved
  version stays in use until the admin approves the new one.

### 4.3 The first connector test: Tally (the WS-CSY planner's suggestion)

Tally reaches Metorite through a local link (LNK-0, PR 2550, open, task 3653).
This design cites that plan and does not depend on its details.

- **What the profiler keeps.** The groups, because Tally's fixed groups are
  schema. The count of ledgers under each group, and the voucher types. The
  first and the last voucher date. The count of tax ledgers under "Duties &
  Taxes".
- **What it never keeps.** A ledger name, a party name, a balance or an
  amount.
- **The mark.** A model built through a local link has `untrusted_origin`
  true, and each read through it marks the chat session (rule 10).

---

## 5. How the chat uses a model in each turn

| Step | What happens | Where it lives (the seam it extends) | Cost |
|---|---|---|---|
| 1. A short brief | A fixed, capped text per stored version: the approved measures by name, the key parts, the rules. Same bytes for the same version (rule 3) | CRM: the CRM facts block (`crm_platform.md` §18.14.4 point 2). A connection: one input of `server_brief`, with its own header and cap (rule 1) | CRM: at most 3 more lines in a block of 13 lines today. A connection: at most 300 characters. The admin's 400 and the server's 600 already fill the 1,000 of `chat_connected_systems.md` §5. The WS-CSY planner decides whether the cap rises or the parts share it |
| 2. The full model on demand | The model asks `org_facts` for an entity, a measure or a verified query | The `org_facts` tool. It loads with the CRM family today. It also loads when a server with an approved model attaches. It is not a core tool, so the core ceiling (`test_tool_schema_diet.py`, 7,040) does not move | One tool call |
| 3. Link on the server | A field, a measure or an entity named by a word, not a key, resolves on the server by label, synonym and term, before any 422. Two close candidates follow the confidence rule of §18.16.4: ask first when the totals differ by more than 5% | `field_sql.FieldScope.ref`, `crm_sync/terms.py` | 0 tokens |
| 4. Resolve values | A filter by name finds the value that the member means, as the member. In rung 1 it is the old `field.@parent=<words>`. After SEM-1b it is the clause `{"field": "@parent", "op": "matches", "value": "<words>"}`. One match binds. Several follow the confidence rule. None gives an honest zero with the near spellings | `field_sql.text_matches`, `terms.value_matches`, `name_match` | 0 tokens |
| 5. Verified query first | When the member's words match a verified question, the server fills its parameters and runs it. The answer says "a verified question" | New, §6.2 | 0 tokens |
| 6. One typed query | Else the model sends one query in the one grammar (§5.1), or the source's own read tool for a connection | `routes/crm/aggregate.py`, MCP tools | One tool call |
| 7. Validate | The parser refuses an unknown field, an operator that the type does not take, or a bad value. The refusal lists the legal fields and operators. An MCP call checks its arguments against the tool's input schema first | `acb_common/studio_query.py`, `tool_refusals.py` | 0 tokens |
| 8. Run | Bound values, a 10-second statement limit, the member's session. An MCP read logs one `mcp.call` line (CSY-2) | `field_sql.py`, `McpPermissionGate` | One read |
| 9. Answer | Each figure comes from the result, and the number check holds the answer. A line "How I read this" names the source, the entity, the measure, each filter with its counted values, and the model version | `answer_check.py`, `crm_tools.filters_line` | none |
| 10. Repair | A refused query gets one more try inside the read budget (§18.16.3). A second refusal tells the member what failed, in plain words | `tool_refusals.py`, `RunReads` | One more call at most |

### 5.1 One query grammar (rung 2, after a narrow fix in rung 1)

**Rung 1 fixes only the two refusals of §1 on the old parameters (SEM-1a).**
A number field takes `field.<key>.from` and `.to`. A module total takes `q`,
which matches the record name, as the module list does. No grammar changes.

**Rung 2 unifies the grammar (SEM-1b).** Three spellings exist: the CRM
parameters, `acb_common/studio_query.py`, and the filter set of
the CRM UI research draft (§4.1, not on `main`). Two options:

| | Option A: `studio_query` is the one shape | Option B: a CRM `filter_grammar.py` (the CRM UI research draft, S1) |
|---|---|---|
| What it is | The JSON shape, the operator names and the error codes of `studio_query` serve every store. Each store keeps its own compiler and its own value rules | A new CRM module with its own operator words (`is`, `between`, `any_of`, `empty`) |
| Operators today | `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `contains`, `is_empty`. No `between`, no `starts_with`, no `any` group | The full set of the CRM UI research draft (§4.1, not on `main`) |
| What must change | Add one `any` group and `matches`. Make the value check a hook of each store | Nothing in Studio |
| The conflicts to solve | A Studio date is a day in UTC, and a CRM month uses the CRM zone (`field_sql.crm_zone`). Studio money is minor units, and Zoho sends JSON numbers, some as strings. A Studio select must be one option, and the CRM counts each spelling (§18.16.6). Studio filters hot fields only (`query_not_indexed`), and the CRM filters any jsonb key. Studio caps a statement at 5 s, the CRM at 10 s | None now. The CRM and Studio keep two spellings |
| The type map | `crm_field_defs` types map to Studio types: currency to money, picklist to select, boolean to checkbox, a lookup (`{id, name}`) to a relation by id or name | Not needed |
| Cost | Larger, HIGH. One shape for the chat, the sidebar, saved views and Studio | Smaller, HIGH. A third spelling until a later merge |

**Recommendation: Option A for the shape, with a value hook for each store.**
The chat then learns one query form for every app. This is D-SEM-3.

**The owner chose Option A on 2026-11-18.** The shape of
`acb_common/studio_query.py` serves every store. It gets one `any` group,
`matches`, and a value hook for each store. Each store keeps its own compiler
and its own value rules. The CRM UI research draft S1 (Option B) does not
start.

**The six conflicts, answered for the CRM store in SEM-1b-1.**

| Conflict | The CRM store | Studio |
|---|---|---|
| ZONE | The CRM keeps `crm_zone`. A day on a datetime field is the day in the CRM zone, through `_calendar_day` | Keeps UTC |
| MONEY | CRM number, decimal, currency and percent compare the guarded `_amount` with `_decimal(str(v))`. A JSON number and a numeric string both pass. A bool is refused. No minor units and no currency check | Keeps minor units |
| SPELLING | CRM `eq` on text and select keeps today's variant count `text_matches`, so lowering changes no total. `matches` waits for SEM-3 and CRM-Q34-7 | Unchanged |
| HOT FIELDS | The CRM has no hot rule and never raises `query_not_indexed` | Unchanged |
| TIMEOUT | Each store keeps its own: 10 s plus the long read for the CRM | 5 s |
| KEY SPELLING | CRM keys are mixed-case API names and `@` key words. The CRM resolves a key through `FieldScope.ref`, with the key words and the near-field hint. The shared shape check does not apply the Studio `NAME_PATTERN` | `NAME_PATTERN` is `[a-z][a-z0-9_]{0,47}` in `studio_fields.py` |

**Never model-written SQL on our Postgres.** A typed query fails loudly, and
free SQL fails with a plausible wrong answer (dbt, 2026-04-07).

### 5.2 What the chat says

- The line "How I read this" ends each answer with a figure. An example:
  "From the CRM, Invoices: the sum of the billed amount, status not Draft,
  October 2026, model version 7." It names no member. It copies no value that
  the member did not name.
- An answer from a verified query says so.
- One follow-up question at most (CRM-Q33).

---

## 6. How the model learns ("anneals")

### 6.1 Three scopes, and the owner of each

| Scope | Signal | What happens | Seam (no second loop) |
|---|---|---|---|
| In the turn | A refusal | One repair (§5 step 10). Nothing is kept | `tool_refusals.py` |
| The org | A member's correction, an admin's thumbs-up, a pick on a card | A proposal. An admin accepts it, and the model gets a new version | The source's store, and one "Needs you" (`routes/shell/waiting.py`) |
| The platform | Refusal codes counted across orgs | A gap goes to the one failure store, then a PR, then the next release | TOF-2a (task 3416) and task 1793, with IR-4 (`agent_sandbox.md` §0b, D225 point 2) |

### 6.2 The kinds of proposal

| Kind | From what | Evidence shown to the admin | Store (CRM / connection) | Who accepts |
|---|---|---|---|---|
| Synonym | "By X we mean Y", or a pick on a term card | The total of each choice | `zoho:questions` terms / model body | An admin. A member's pick holds for that member (CRM-Q32-8) |
| Instruction | A rule that the member states | The member's own words | `crm_business_rules` / model body | The member for themselves. An admin for the org |
| Field meaning, unit or tax | "That amount includes GST" | The `gst_check` evidence | Plan catalogue item / model body | An admin |
| Measure | A sum that a member picks, or an admin's thumbs-up | The query, and its total computed when the admin opens the item. Not stored | Plan item `measure` / model body | An admin |
| Verified query | An admin's "Save as a verified question", or an accepted suggestion | The query with its parameters, and its total computed at review on values that the admin enters. Not stored | Plan item `query` / model body | An admin |
| Join | A correction about which record links to which | The count of linked records | Plan item `join` / model body | An admin |

**The rules of a proposal.**

1. **A member never changes what every member reads** (rule 5). A member's
   correction is a proposal until an admin accepts it. This is the defence
   against memory poisoning by queries (MINJA, research §4).
2. **Data never becomes a proposal.** Text from a tool result, a record or a
   server is never held as a rule (CRM-Q33 part 3). At least 3 in 4 of a
   rule's words are the member's own words of the run.
3. **No value in a proposal.** A verified query keeps parameter names only
   (§3.3).
4. **Each accept is a new version.** Undo restores the version before.
5. **A member holds at most 10 open proposals in one org**, as an ask of I8b
   does.

### 6.3 Verified queries

- **Save.** An admin presses "Save as a verified question" under an answer.
  The server keeps the question words and the query, with each literal
  turned into a named parameter. It also keeps the model version. It keeps no
  total and no parameter value (§3.3 points 3 and 5).
- **Match.** A later question matches on its words and its parameters, in
  code. The TOF-1c matcher (task 3412) is the one matcher once it is built.
  Until then the term matcher of §18.16.4 serves.
- **Stale.** A new `shape_hash` that drops a field of a verified query marks
  it stale. The system proposes one repaired version, runs it read-only, and
  keeps the proposal only if it runs. The repair keeps the parameters and the
  output. At most one repair for each stale query. This is the contract of
  `agent_sandbox.md` §6.7.
- **A benchmark for free.** The verified queries of a source are its benchmark.
  Before an admin accepts a new model version, the server runs each verified
  query read-only under the old and the new version. It uses parameter values
  that the admin enters at review, or no value where the query allows it. A
  moved total shows to the admin before the accept. Nothing of the run is
  stored.
- **The test benchmark.** The eval of SEM-6 holds a fixed question set with
  synthetic data, for each change of code.

### 6.4 Suggestions from use

Once a week, each source lists the measure keys that members often asked
about, and that no verified query covers. The key is the measure key of NUM-7
(`answer_figures`), once it is built. The admin sees counts and the query
shape. The admin sees a member's question text only if the owner says yes to
D-SEM-7.

### 6.5 The platform loop and the global prompt

- Each refusal writes one log line `semantic.refusal`. It holds the org, the
  source, the code and the entity key, and no value. It also adds one to a
  daily counter in tenant Redis, with a TTL of 8 days, as CSY-8 counts calls.
- A weekly job sends the counts of each refusal code to the failure store of
  TOF-2a. Only counts cross tenants (D187 point 4). A coding agent fixes a
  real gap (for example "a number range") in a PR, and the next release ships
  it to every org.
- **The profiler's own prompt and rules are global harness** (rule 9). They
  change only through the guarded loop of task 3649. That loop has a tune set,
  a sealed held-out set, a win margin, and acceptance on the end state. A per-org
  correction never edits an eval case or a check. The coordinator stated task
  3649. This draft could not read it on the board, because the Metorite MCP did
  not connect.

### 6.6 How this ties to TOF and the sandbox

- A verified query can be the first step of a saved skill (TOF rung 1, tasks
  3404 and 3411 to 3413). The skill names the verified query and does not copy
  it.
- TOF-5a (task 3423) puts an approved MCP tool in a skill. The model of that
  connection fills the tool's arguments.
- A built-in read that fails is level (c) of `agent_sandbox.md` §6.7. The
  chat changes no platform code at run time. It files a report to the failure
  log.
- F6a (task 3160) and TOF-1d (task 3413) offer to save a job. After an answer
  with a figure, an admin gets "Save as a verified question" in the same
  offer, not in a second one.

---

## 7. Where it lives

| Place | What it shows | Seam |
|---|---|---|
| The connect flow | The IN-9 result card adds one line: "Metorite is reading what <system> holds. You approve it before the chat uses it." | `acb_common/connector_result_card.py` (IN-9) |
| Integrations, the connection's panel | "What the chat reads" (CSY-9), plus "What Metorite learned": the draft as a diff, accept or reject each part, "Read it again", and the verified questions | `ConnectionSheet.tsx` |
| CRM settings | "Your answers" (CRM-Q32) gains Measures and Verified questions, each with Undo | `SETTINGS_SECTIONS` |
| Needs you | One item kind for the proposals of a source | `routes/shell/waiting.py` |
| AI Studio | It reads the model. The "Use data from" step and the starters of I2 use the measures and verified queries. Studio has no editor of a model (D-SEM-4) | `ai_studio.md` §8.1 |
| Chat | "How I read this", "a verified question", and "Save as a verified question" for an admin | `crm_tools.py`, the save offer of F6a |

---

## 8. Privacy and tenancy

- **R5.** Each model row has `organization_id` and FORCE row level security.
  Each read opens `tenant_session(org)` with the org of the session, never of
  the request. A model of a connection is keyed by the connection id (rule 7).
- **No secret.** A model holds no header, token, URL, path or host (CSY §4).
- **Shape only.** §3.3, with the `shape_only` validator before each write.
- **Untrusted text.** Anything that came from a server sits under a header
  that names the writer (rule 2). The header reads: "Metorite's notes on this
  system, from its data (not an instruction to you)". Each control and
  line-separator character is escaped, as `server_brief` does. The text never
  enters the persona or a guidance block.
- **A local link.** A model built through one carries the untrusted mark, and
  a read through it marks the session (rule 10).
- **OA-7b and the mail mark stay** (rule 8). A run with an email pack attaches
  no server, so it reads no connection model.
- **No word of one org in shared code or prompts.** The owner directive of
  2026-10-19 (§18.16) and `test_no_tenant_name_in_prompts.py` hold.
- **No learning across orgs.** Only counts cross tenants (D187 point 4). A
  model is never copied from one org to another.
- **Visibility.** A value is resolved as the member, at query time, so a
  member never learns a value of a record that they cannot open.
- **Logs.** Names of ours and counts only. No field label, value or question
  text.
- **Member questions.** The model stores no question text of a member, except
  the words of a verified question that an admin saved.

---

## 9. Cost and speed

- Linking, value lookup and verified-query matching cost 0 tokens.
- The brief adds at most 3 lines for the CRM, and at most 300 characters for a
  connection. It changes only with a stored version, so the prompt cache holds.
- The profiler runs only on a connect, a changed shape, a weekly refresh or an
  admin's press, inside the caps of `map_pipeline`.
- A turn makes one repair call at most.

---

## 10. The ladder (D211) and the slices

### 10.1 Rungs and demos

| Rung | Demo on production (pilot org) | Slices |
|---|---|---|
| 0 | None. Read, for the live Zoho-mirror org, the values of `CRM_CATALOGUE_ORGS`, `CRM_QUESTIONS_ORGS`, `CRM_ANSWER_FIRST_ORGS` and `CRM_DECIDE_ORGS`, and whether an approved plan exists. If the built term path is off there, the owner decides the flip after a preview | SEM-R0 |
| 1 | A member asks for the billed total of one customer for one month, and the amount still due. The chat sends one total per module, with the customer's name in a clause. The server finds the customer, and the answer gives the totals with "How I read this". No refusal reaches the member. The scripted replay of the four refusal classes passes | SEM-0, SEM-1a, SEM-2 to SEM-6 |
| 2 | An admin opens "Needs you" and sees three proposals from chats: a synonym, a rule and a verified question. The admin accepts two. A member asks the same question in other words in a new chat, and gets "a verified question" with the same total. The admin presses Undo on one, and the next chat drops it | SEM-1b, SEM-7 to SEM-11 |
| 3 | An admin connects a test company through the local link to Tally, or a test MCP server. Within 5 minutes the panel shows what Metorite learned: the groups, the ledger count of each group, the voucher types, and the first and last voucher date. The admin accepts. A member asks for last quarter's sales. The chat calls the system's read tool, names the system, and says how it read the data | SEM-12 to SEM-17 |
| 4 | One line: books of account. Zoho Books after IN-3 (task 3176), and totals that respect the tax ledgers. Needs D-SEM-8 | later |
| 5 | One line: AI Studio builds starters and skills from a model (I2, TOF-5a) | later |
| 6 | One line: suggestions from use (after NUM-7), and the guarded tune of the profiler prompt (task 3649) | later |

Rung 1 fits one week of two lanes. A demo task for each rung is made with the
slices (`orchestration_playbook.md` §6d).

### 10.2 The slices

A test file marked "new" does not exist yet. Its slice writes it, red first.

| Slice | Rung | Outcome | Files | Tier · gate | Flag | Fence (R7) | Depends on |
|---|---|---|---|---|---|---|---|
| **SEM-R0** | 0 | The flag values and the plan state of the live org are known, and the owner decides | none: a read | OWNER-GATE for any flip | n/a | none: an act | none |
| **SEM-0** | 1 | This spec, its INDEX row and its work_plan §2 row | `project-docs/specs/semantic_model.md` (new), `INDEX.md`, `work_plan.md` | LOW · AGENT-SAFE | none | `test_index_completeness.py`, `test_doc_names_resolve.py`, ste-lint | none |
| **SEM-1a** | 1 | On the old parameters: a number field takes `.from` and `.to`, and a module total takes `q` on the record name. No grammar change | `routes/crm/field_sql.py`, `aggregate.py` | HIGH (SQL) · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` | `test_crm_aggregate.py` (extended), `test_crm_number_range_r8.py` (new) | none |
| **SEM-2** | 1 | Server-side linking: a word for a field or a measure resolves before a 422. A close pair follows the confidence rule. The answer names what it linked | `field_sql.py`, `crm_sync/terms.py` | HIGH · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS`, and the catalogue and questions flags of SEM-R0 | `test_crm_query_link.py` and `_r8` (new), `test_crm_answer_consistency_r8.py` | SEM-R0 |
| **SEM-3** | 1 | A lookup or text filter by name counts each spelling, as the member, with the `name_match` floor | `field_sql.py`, `terms.py` | HIGH (values, visibility) · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` | `test_crm_value_resolve_r8.py` (new): a member who cannot open a record gets no match from it | none |
| **SEM-4** | 1 | A refusal lists the legal fields (the 8 most filled) and the legal operators. One repair. One `semantic.refusal` line and a daily counter | `tool_refusals.py`, `crm_tools.py`, `aggregate.py` | STANDARD · AGENT-SAFE | none: text only | `test_crm_refusal_next.py`, `test_semantic_refusal_counter.py` (new) | none |
| **SEM-5** | 1 | The text of `crm_aggregate` says that a word is fine and the server finds the field, and that a module takes `q` | `crm_tools.py` | STANDARD · AGENT-SAFE | none: text only | `test_tool_schema_diet.py` | SEM-1a, SEM-2 |
| **SEM-6** | 1 | A scripted replay of the four refusal classes, and a fixed set of 20 questions with synthetic data | `evals/semantic/` (new) | LOW · AGENT-SAFE | none | `test_semantic_eval_scripted.py` (new) | SEM-1a to SEM-5 |
| **SEM-1b-1** | 2 | `GET /crm/aggregate` on a MODULE entity takes `query`, in the grammar of §5.1 Option A. The old parameters lower into it. Leaf: `{"field": <CRM key or key word>, "op": <op>, "value": <v>}`. Group: `{"any": [leaf, ...]}`, 2 to 8 leaves, one level, no nesting. See the rules below this table | `packages/acb_common/acb_common/studio_query.py` (a pure shape check that takes the `any` node and a set of ops for each store, and Studio callers do not change), `apps/services/gateway/gateway/routes/crm/field_sql.py` (a new CRM compiler on the one filter builder of CRM-Q34-4, PR 2584, that reuses `_filter` and `_filter_value`), `routes/crm/aggregate.py` (`compute` gets one keyword, default None), `routes/crm/flags.py`, `packages/acb_common/acb_common/settings.py`, `docs/flag_reload.md`, `tests/unit/test_flag_reload.py` (add `SEMANTIC_MODEL_ORGS` beside `CRM_MODULE_FIND_ORGS` of PR 2584) | HIGH (SQL) · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` (new) | `test_crm_query_grammar.py` and `test_crm_query_grammar_r8.py` (new). Acceptance (a) to (j) below | CRM-Q34-4 (PR 2584) merged first. SEM-6 (task 3723, PR 2576) is the replay fence. SEM-1b-1 may start from its branch if it is not merged |
| **SEM-1b-2** | 2 | `crm_aggregate` takes a `query` argument (about 32 to 49 tokens), the lowering of `custom.<key>`, and the ops `ne`, `is_empty`, `contains` and `matches` | `crm_tools.py`, `field_sql.py`, `aggregate.py`, `records.py`, a shared fixture | HIGH (SQL) · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` | `test_tool_schema_diet.py`, `test_crm_query_grammar.py` (extended) | SEM-1b-1, and the owner's ruling on the token budget (D-SEM-12) |
| **SEM-7** | 2 | The CRM model reader. The plan learns `measure`, `join` and `query`. `org_facts` serves them | `crm_sync/catalogue.py`, `routes/crm/mapping_plans.py`, `routes/crm/facts.py` | HIGH (plan) · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` | `test_crm_plan_kinds_semantic.py` (new), `test_org_facts_r8.py` | SEM-R0 |
| **SEM-8** | 2 | Verified queries: save with parameters, match, run, the "verified" mark, stale on a shape change, one repair | `routes/crm/` (a new module), `crm_tools.py` | HIGH · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` | `test_crm_verified_queries.py` and `_r8` (new) | SEM-7 |
| **SEM-9** | 2 | Proposals from chat: synonym, measure and verified query join the CRM-Q32 store and one "Needs you" kind | `routes/crm/questions.py`, `crm_sync/questions.py`, `routes/shell/waiting.py` | HIGH (fact writes) · AGENT-SAFE dark | `CRM_QUESTIONS_ORGS` and `SEMANTIC_MODEL_ORGS` | `test_crm_questions_chat_r8.py`, `test_waiting_seam_r8.py` | SEM-7 |
| **SEM-10** | 2 | Measures and Verified questions in "Your answers", with Undo | CRM settings UI | STANDARD UI · captures before merge | `SEMANTIC_MODEL_ORGS` | Vitest of the section (new) | SEM-8, SEM-9 |
| **SEM-11** | 2 | The weekly counts of refusal codes go to the failure store | a job on the queue | STANDARD · AGENT-SAFE | none | `test_semantic_refusal_feed.py` (new) | SEM-4, TOF-2a (task 3416) |
| **SEM-12** | 3 | The connection model table, its one writer and `shape_only` | a migration (number at build time), `connections/semantic_model.py` | HIGH (migration, tenancy) · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_connection_semantic_model_r8.py` (new: org B cannot read org A, cascade on delete), `test_semantic_shape_only.py` (new), `test_tenancy_boundary.py` | D-SEM-2 |
| **SEM-13** | 3 | The profiler of §4.2: discover, shape reads with no member, decide, draft | `connections/`, `decide_features.py` | HIGH (third-party data) · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_connection_profiler.py` (new, with a fake MCP server): no class B or C tool is called, the caps hold, no value is stored | SEM-12 |
| **SEM-14** | 3 | The model's summary is one input of `server_brief`, with its own header and cap, stable for a version | `acb_skills/server_brief.py` | HIGH (untrusted text) · AGENT-SAFE dark | `CONNECTED_SYSTEMS_ORGS` | `test_server_brief.py` (extended) | CSY-1 (task 3400), D-CSY-1 |
| **SEM-15** | 3 | An `org_facts` provider for connections. The `org_facts` tool loads with an approved model | `gateway/org_facts.py`, a provider module, `_tool_injection.py` | STANDARD · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_org_facts.py`, `test_org_facts_r8.py` (extended) | SEM-12 |
| **SEM-16** | 3 | The panel: the draft as a diff, accept per part, "Read it again" | `ConnectionSheet.tsx` | STANDARD UI · captures before merge | `CONNECTION_MODEL_ORGS` | Vitest of `ConnectionSheet.tsx` (extended) | SEM-13, CSY-9 (task 3409) |
| **SEM-17** | 3 | The Tally reads of §4.3 through the local link, with the untrusted mark | the LNK adapter | HIGH · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_connection_profiler_tally.py` (new, synthetic) | SEM-13, the LNK build slices (PR 2550) |

**The rules of SEM-1b-1.**

- `query` is a JSON text parameter of at most 4000 characters. Bad JSON gives 422 `query_value_invalid`.
- A query is a list of nodes joined by AND, with at most 8 leaf conditions.
- The ops now are `eq`, `in`, `gte`, `lte`, `gt` and `lt`. An `in` expands to an `any` group of `eq` leaves.
- The deferred ops `ne`, `is_empty`, `contains` and `matches` give 422 `query_op_not_allowed`. The refusal lists the legal ops.
- The lowering: `field.<key>=v` becomes `eq`, `field.<key>.from=v` becomes `gte`, and `field.<key>.to=v` becomes `lte`.
- The lowered pairs and `query` join by AND. The cap of 8 applies to `query` only, so an old request never starts to fail.
- `q` stays a separate parameter, outside the grammar.
- `custom.<key>` and `custom.<key>.in` (core entities) wait for SEM-1b-2.
- `query` on a core entity gives a 422 that names a working step: "A query takes a module entity. For leads, deals, contacts or organizations, give the filters of crm_list_records."
- `query` from an org off the flag gives a 422 that names `field_filters`. The server never drops it with no message. Off the flag, every other answer stays byte for byte the same.
- The `crm_aggregate` tool does not change (0 schema tokens). No new op enters the shared `OPS` tuple of `studio_query.py`, and `_LIST_INPUT_SCHEMA` does not change. The Studio tool schema stays byte for byte the same. No migration.

**Acceptance of SEM-1b-1.**

- (a) Each R8 seed gives the same total and count three ways. The ways are the old parameters off the flag, the old parameters on the flag, and the equal `query`.
- (b) An `any` group ORs its leaves on a real database.
- (c) A datetime day filter in a CRM zone other than UTC counts across midnight as the old range does.
- (d) A currency stored as a numeric string sums and filters like a number. "NA" counts as no value.
- (e) A lookup by name and by Metorite id matches as `field.<key>` does.
- (f) The records of org B, of the same module and key, never count for org A.
- (g) An unknown key gives a 422 that names the legal fields.
- (h) A `query` on a core entity, and a `query` from an org off the flag, each give a 422 that names a working step.
- (i) The Studio tests and the Studio schema bytes do not change.
- (j) The `crm_aggregate` schema token count does not change.

**The token budget of SEM-1b-2.** The per-tool cap test `test_crm_aggregate_costs_no_more_tokens_than_when_main_was_green` allows 687, and `main` is 675. The Studio chat budget `SCHEMA_TOKEN_BUDGET = 30_000` in `evals/one_assistant_measure/checks.py` has about 2 tokens of room. One CRM tool token costs about 3 tokens of that budget.

**Flags.** `SEMANTIC_MODEL_ORGS` (the CRM rungs) and `CONNECTION_MODEL_ORGS`
(the connection rung). Both are `*_ORGS` flags, default empty, and both reload
with no restart (`docs/flag_reload.md`). A flip on a live org needs the
`enforcement-flip` grant and the owner's preview.

**The order.** SEM-R0 first, because it costs no code. Then SEM-0. SEM-1a,
SEM-3 and SEM-4 can start at once and run in parallel. SEM-2 starts after
SEM-R0. SEM-5 and SEM-6 close rung 1.

**Later rungs.** SEM-1b-1 opens rung 2. The owner chose D-SEM-3 on 2026-11-18. Rung 3
waits for CSY-1 and for the owner's answers to D-SEM-2 and D-CSY-1.

### 10.3 The map to the CRM-Q34 slices (epic task 3689)

The CRM fields and filters epic already holds slices that overlap rung 1. This
spec adds no second slice for the same work. The map:

| Here | CRM slice | Task | Rule |
|---|---|---|---|
| SEM-1a | CRM-Q34-1: a number range on a module field, and a lookup by the Metorite id of a core record | 3693 | Same work. Merged (PR 2573) |
| SEM-4 | CRM-Q34-2 (the refusals name a working field, module or filter) and CRM-Q34-3 (the tool text) | 3694, 3695 | SEM-4 includes both. Build them once |
| SEM-3 | CRM-Q34-7: `q` finds across a module | 3700 | The two overlap. The one that starts first owns the code, and the other cites it |
| SEM-1b-1 | CRM-Q34-4 and CRM-Q34-4b: one filter builder | 3697, 3711 | Q34-4 (PR 2584) is the builder that SEM-1b-1 lowers into |
| SEM-6 | The scripted replay of the four refusal classes | 3723 | PR 2576 is open |
| none | CRM-Q34-5: the filter words a member needs | 3698 | D-SEM-3 is decided (Option A). The words are part of the one grammar |

The tasks of the rows SEM-1a to SEM-6 are made when the owner approves the
spec, under the epic of WS-SEM. No row of SEM-12 to SEM-17 gets a task before
the owner answers D-SEM-1.

---

## 11. The WS-CSY planner's ten rules, mapped

| Rule | What it says | Where this design meets it |
|---|---|---|
| 1 | One brief. The profile is a new input of `server_brief` | §3.1, §5 step 1, SEM-14. No second block |
| 2 | The profile is untrusted data, under a header that names the writer | §8 "Untrusted text", §4.2 step 4 (code writes the text) |
| 3 | A short capped summary per turn, the full brief on demand | §5 steps 1 and 2, §9 |
| 4 | The admin's description stays the admin's | §3.2 "Source notes", §4.2. AI text sits in the model body only |
| 5 | A member's correction is a proposal that an admin accepts | §6.1, §6.2 rule 1, §4.2 step 5 |
| 6 | Shape only: no balance, personal data or free-text value | §3.3, `shape_only`, §4.3 |
| 7 | Per org and per connection id, FORCE RLS, `tenant_session` | §3.4, §8 "R5", SEM-12 |
| 8 | OA-7b and the mail mark stay. Profiling reads only | §4.2 "No member, so reads only", §8 |
| 9 | The profiler's global prompt changes only through task 3649 | §6.5 |
| 10 | A local-link profile carries the untrusted mark | §3.4 `untrusted_origin`, §4.3, §8 |

---

## 12. Decisions for the owner

1. **D-SEM-1. A new block "Data meaning" in `building_blocks.md` §2**, whose
   seam is `org_facts` with one provider per source. Recommendation: yes. It
   makes the CRM's model general and adds no parallel read path.
2. **D-SEM-2. One new table for the model of a connection**, keyed by the
   connection id, with versions. Recommendation: yes. The CRM keeps its plan
   and needs no table.
3. **D-SEM-3. Which one query grammar** (§5.1). Option A: the shape of
   `studio_query` for every store. It gets one `any` group, `matches`, and a
   value hook for each store. Option B: a CRM `filter_grammar.py`, as
   the CRM UI research draft S1 proposes. **DECIDED, 2026-11-18: the owner chose
   Option A.** §5.1 answers its six conflicts for the CRM store.
4. **D-SEM-4. Who approves.** The CRM admin for the CRM (as CRM-Q32). A holder
   of `integrations:manage` for a connection. AI Studio reads a model and does
   not edit it. Recommendation: yes. Three permissions approve different
   things today, and this keeps each one where it is.
5. **D-SEM-5. No stored value index.** Values resolve at query time as the
   member. Only picklist options are kept. Recommendation: yes. A stored list
   needs its own owner decision. It can show a value to a member who cannot
   open its record.
6. **D-SEM-6. Verified queries live in the model of the source**, with
   parameters only, and a skill may name one. Recommendation: yes. The
   alternative stores them as saved skills, which a member owns.
7. **D-SEM-7. Suggestions from use show counts and query shapes only.** A
   member's question text reaches an admin only if the member consents.
   Recommendation: counts and shapes only.
8. **D-SEM-8. Books of account.** Keep `ai_studio.md` non-goal 8 (no general
   ledger). Books arrive as connections: Tally through the local link, then
   Zoho Books after IN-3. Name the spec that owns the books connectors.
   Recommendation: a books section in `crm_connectors.md` (WS-MIR) or a new
   connector spec. The owner picks.
9. **D-SEM-9. No learning across orgs.** Only counts cross tenants. The
   profiler's global prompt changes only through task 3649. Recommendation:
   yes, as D187 point 4.
10. **D-SEM-10. Keep the body close to Apache Ossie**, and take no dependency.
    Recommendation: yes. It is pre-1.0.
11. **D-SEM-11. Before an admin approves a connection's draft, the chat gets
    nothing new from it.** Recommendation: yes (rule 5). The CRM's "answer
    first" (CRM-Q33) stays for the CRM.
12. **The held WS-CSY decisions.** This design does not assume an answer.
    - **D-CSY-1** (the per-turn block reads the description). SEM-14 needs a
      yes. With a no, a connection's model reaches the chat only through
      `org_facts` on demand (SEM-15).
    - **D-CSY-3** (the server's own instructions). With a no, the profiler
      does not read them either.
    - **D-CSY-4** (match words for a server). A model's synonyms could feed the
      words, but only after a yes.
13. **D-SEM-12 (open). The token budget of SEM-1b-2.** The owner of WS-67
    raises the Studio 30,000 schema budget, or cuts CRM tool text.
14. **D-SEM-13 (open). The meaning of CRM `ne` and `is_empty`**, beside the
    checkbox exception of Studio.
15. **The rung 0 flip.** If SEM-R0 finds the built CRM layers off for the live
    org, the owner decides each flip after a preview.

---

## 13. What this design does not own

- The MCP client, the card and the server brief's frame: WS-8c and WS-CSY
  (`chat_connected_systems.md`).
- The search ladder and `resolve_name`: `find_engine.md`.
- The CRM catalogue, the plan, the questions and the rules: `crm_platform.md`
  §18.13 to §18.16. This design adds kinds and readers to them.
- The local link: LNK-0 (PR 2550, open).
- The failure store: TOF-2a (task 3416) and task 1793.
- The plausibility checks of a total: `number_sense.md`.
- Studio objects and their query store: `ai_studio.md` C2a and C2b.

## 14. Verification commands (for the slices)

```bash
node .claude/hooks/ste-lint.mjs project-docs/specs/semantic_model.md
uv run pytest tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py -q

# Each code slice keeps these green (hermetic)
uv run pytest tests/unit/test_crm_aggregate.py tests/unit/test_tool_schema_diet.py \
  tests/unit/test_org_facts.py tests/unit/test_crm_answer_consistency.py -q

# The R8 suites need a database. A skip is not a pass
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_studio_query_r8.py tests/unit/test_org_facts_r8.py \
  tests/unit/test_crm_answer_consistency_r8.py -q
```

**SEM-1b-1.** Run these on the merge result with PR 2584. An R8 skip is not a
pass. `test_crm_module_list_filters.py` and its `_r8` file come with PR 2584.

```bash
uv run pytest tests/unit/test_crm_query_grammar.py tests/unit/test_studio_query.py \
  tests/unit/test_crm_aggregate.py tests/unit/test_crm_module_list_filters.py \
  tests/unit/test_tool_schema_diet.py tests/unit/test_crm_email_tool_refusals.py \
  tests/unit/test_one_assistant_measure_search.py tests/unit/test_flag_reload.py -q

bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_crm_query_grammar_r8.py tests/unit/test_studio_query_r8.py \
  tests/unit/test_crm_aggregate_r8.py tests/unit/test_crm_aggregate_fields_r8.py \
  tests/unit/test_crm_module_list_filters_r8.py tests/unit/test_tenancy_boundary.py -q
uv run ruff check <changed files>
uv run mypy <changed files>
```
